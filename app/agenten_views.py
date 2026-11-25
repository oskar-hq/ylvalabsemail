"""Seiten der Agenten-Schnittstelle (Stufe 12): OAuth für Grok und Claude, der MCP-Endpunkt, Einstellungen →
Agenten mit Einrichtung (Onboarding) und die Freigabe-Karten der Agents."""
import base64
import json
import urllib.error
import urllib.request

from flask import Blueprint, Response, flash, g, jsonify, redirect, render_template, request, url_for

from . import agenten, firma, freigaben, mcp_server
from .leads_views import db, me
from .main import SETTINGS, darf, login_required, safe_next

bp = Blueprint("agenten", __name__)


def _basis():
    """Öffentliche Adresse der App (Einstellungen → Adresse der App), sonst die der Anfrage."""
    from . import agent
    base = (agent.S("BASE_URL") if agent.CFG else "") or SETTINGS.get("BASE_URL") or ""
    return (base or request.host_url).rstrip("/")


def _ajax():
    return request.headers.get("X-Requested-With") == "fetch" or request.accept_mimetypes.best == "application/json"


def _fertig(message, url, ok=True):
    if _ajax():
        return (jsonify(ok=True, message=message, url=url), 200) if ok else (jsonify(error=message), 400)
    flash(message if ok else f"Nicht gespeichert: {message}")
    return redirect(url)


def _json_antwort(daten, status=200, headers=None):
    resp = Response(json.dumps(daten, ensure_ascii=False), status=status, mimetype="application/json")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    for k, v in (headers or {}).items():
        resp.headers[k] = v
    return resp

# ---------------------------------------------------------------- Discovery (RFC 9728, RFC 8414)


@bp.get("/.well-known/oauth-protected-resource")
@bp.get("/.well-known/oauth-protected-resource/<path:sub>")
def resource_metadata(sub=""):
    base = _basis()
    pfad = "/" + sub.strip("/") if sub else "/mcp"
    if not pfad.startswith("/mcp"):
        pfad = "/mcp"
    return _json_antwort({"resource": base + pfad, "authorization_servers": [base], "scopes_supported": ["os"],
                          "bearer_methods_supported": ["header"], "resource_name": "Ylva Labs Firmen-OS"})


@bp.get("/.well-known/oauth-authorization-server")
@bp.get("/.well-known/oauth-authorization-server/<path:sub>")
@bp.get("/.well-known/openid-configuration")
@bp.get("/.well-known/openid-configuration/<path:sub>")
def server_metadata(sub=""):
    base = _basis()
    return _json_antwort({
        "issuer": base, "authorization_endpoint": base + "/oauth/authorize", "token_endpoint": base + "/oauth/token",
        "registration_endpoint": base + "/oauth/register", "revocation_endpoint": base + "/oauth/revoke",
        "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"], "scopes_supported": ["os"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
        "revocation_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
        "service_documentation": base + "/einstellungen/agenten/einrichten"})


def _oauth_fehler(code, text, status=400):
    return _json_antwort({"error": code, "error_description": text}, status)


@bp.route("/oauth/register", methods=["POST", "OPTIONS"])
def register():
    if request.method == "OPTIONS":
        return _json_antwort({}, 204, {"Access-Control-Allow-Headers": "Content-Type, Authorization"})
    daten = request.get_json(silent=True)
    if not isinstance(daten, dict):
        return _oauth_fehler("invalid_client_metadata", "JSON erwartet.")
    try:
        out = agenten.client_registrieren(db(), daten)
    except agenten.AgentError as exc:
        return _oauth_fehler("invalid_redirect_uri" if "Rücksprung" in str(exc) else "invalid_client_metadata", str(exc))
    return _json_antwort(out, 201)

# ---------------------------------------------------------------- Anmelden und Verbinden (für Menschen im Browser)


def _angemeldet():
    from . import main
    g.account = main.current_account() or main._resume_remembered()
    return g.account


def _agent_aus_resource(resource):
    """/mcp/<key> in der Ressource wählt den Agent vor (je Agent eine eigene Verbindungsadresse)."""
    teil = (resource or "").rstrip("/").rsplit("/mcp", 1)
    if len(teil) == 2 and teil[1].startswith("/"):
        return teil[1].strip("/")
    return None


@bp.route("/oauth/authorize", methods=["GET", "POST"])
def authorize():
    c = db()
    p = request.values
    client_id, redirect_uri = p.get("client_id", ""), p.get("redirect_uri", "")
    k = agenten.client(c, client_id)
    if not k:
        return render_template("agent_verbinden.html", fehler="Dieser Zugang ist unbekannt. Bitte im Agent die "
                               "Verbindung neu einrichten.", account=None), 400
    if redirect_uri not in k["redirect_uris"] or not agenten.redirect_erlaubt(c, redirect_uri):
        return render_template("agent_verbinden.html", fehler="Diese Rücksprung-Adresse ist nicht erlaubt "
                               "(Einstellungen → Agenten → Regeln).", account=None), 400
    if p.get("response_type") != "code" or p.get("code_challenge_method") != "S256" or not p.get("code_challenge"):
        return _zurueck_mit_fehler(redirect_uri, "invalid_request", p.get("state"))
    if not _angemeldet():
        return redirect(url_for("login", next=request.full_path))
    key = g.account.get("team")
    if not key or not firma.darf(c, key, "personen", firma.VERWALTEN):
        return render_template("agent_verbinden.html", account=None, fehler="Agents verbinden darf nur, wer "
                               "Personen und Rechte verwalten darf (z. B. Oskar oder Finn)."), 403
    vorwahl = _agent_aus_resource(p.get("resource"))
    if request.method == "POST":
        from .main import csrf_token
        if request.form.get("_csrf") != csrf_token():
            return render_template("agent_verbinden.html", account=None, fehler="Formular abgelaufen. Bitte "
                                   "die Verbindung im Agent neu starten."), 400
        if request.form.get("abbrechen"):
            return _zurueck_mit_fehler(redirect_uri, "access_denied", p.get("state"))
        agent_key = request.form.get("agent", "")
        if vorwahl and agent_key != vorwahl:
            return render_template("agent_verbinden.html", account=None, fehler="Diese Adresse gehört zu einem "
                                   "anderen Agent."), 400
        try:
            code = agenten.code_ausstellen(c, key, client_id, agent_key, redirect_uri, p.get("code_challenge"),
                                           p.get("resource"))
        except agenten.AgentError as exc:
            return render_template("agent_verbinden.html", account=None, fehler=str(exc)), 400
        return redirect(_mit_query(redirect_uri, {"code": code, "state": p.get("state"), "iss": _basis()}))
    agents = [a for a in agenten.liste(c) if a["status"] == "aktiv" and (not vorwahl or a["key"] == vorwahl)]
    html = render_template("agent_verbinden.html", account=None, client=k, agents=agents, vorwahl=vorwahl,
                           werte={n: p.get(n, "") for n in ("client_id", "redirect_uri", "response_type", "state",
                                                            "code_challenge", "code_challenge_method", "resource",
                                                            "scope")}, fehler=None)
    return _mit_ruecksprung_csp(html, redirect_uri)


def _mit_ruecksprung_csp(html, redirect_uri):
    """Nach „Verbinden“ leitet das OS zu Grok oder Claude weiter. Browser prüfen auch diese Weiterleitung gegen
    „form-action“ der Seite mit dem Formular; ohne die Adresse dort passiert beim Tippen einfach nichts. Erlaubt wird
    nur der Ursprung der (schon geprüften, in den Einstellungen erlaubten) Rücksprung-Adresse."""
    from urllib.parse import urlsplit
    from .main import CSP
    teile = urlsplit(redirect_uri)
    ursprung = f"{teile.scheme}://{teile.netloc}"
    resp = Response(html, mimetype="text/html")
    resp.headers["Content-Security-Policy"] = CSP.replace("form-action 'self'", f"form-action 'self' {ursprung}")
    return resp


def _mit_query(url, werte):
    from urllib.parse import urlencode
    werte = {k: v for k, v in werte.items() if v}
    return url + ("&" if "?" in url else "?") + urlencode(werte)


def _zurueck_mit_fehler(redirect_uri, code, state):
    return redirect(_mit_query(redirect_uri, {"error": code, "state": state}))


def _client_daten():
    """Client-ID und -Secret aus Formular oder Basic-Auth."""
    cid, secret = request.form.get("client_id", ""), request.form.get("client_secret")
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("basic "):
        try:
            from urllib.parse import unquote
            roh = base64.b64decode(auth[6:]).decode()
            cid, secret = (unquote(x) for x in roh.split(":", 1))
        except (ValueError, UnicodeDecodeError):
            pass
    return cid, secret


@bp.route("/oauth/token", methods=["POST", "OPTIONS"])
def token():
    if request.method == "OPTIONS":
        return _json_antwort({}, 204, {"Access-Control-Allow-Headers": "Content-Type, Authorization"})
    c = db()
    cid, secret = _client_daten()
    try:
        agenten.client_pruefen(c, cid, secret)
    except agenten.AgentError:
        return _oauth_fehler("invalid_client", "Unbekannter Zugang.", 401)
    art = request.form.get("grant_type")
    try:
        if art == "authorization_code":
            out = agenten.code_einloesen(c, request.form.get("code"), cid, request.form.get("redirect_uri", ""),
                                         request.form.get("code_verifier"))
        elif art == "refresh_token":
            out = agenten.erneuern(c, request.form.get("refresh_token"), cid)
        else:
            return _oauth_fehler("unsupported_grant_type", "Nur authorization_code und refresh_token.")
    except agenten.AgentError:
        return _oauth_fehler("invalid_grant", "Code oder Token ungültig, abgelaufen oder gesperrt.")
    return _json_antwort(out)


@bp.route("/oauth/revoke", methods=["POST"])
def revoke():
    c = db()
    t = request.form.get("token", "")
    row = c.execute("SELECT familie FROM agent_tokens WHERE token_hash=?", (agenten._hash(t),)).fetchone()
    if row:
        c.execute("UPDATE agent_tokens SET gesperrt=1 WHERE familie=?", (row["familie"],))
        c.commit()
    return _json_antwort({})

# ---------------------------------------------------------------- MCP-Endpunkt


def _401(pfad):
    base = _basis()
    meta = base + "/.well-known/oauth-protected-resource" + (pfad if pfad != "/mcp" else "")
    return _json_antwort({"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "Anmeldung nötig."}},
                         401, {"WWW-Authenticate": f'Bearer resource_metadata="{meta}", scope="os"'})


@bp.route("/mcp", methods=["GET", "POST", "DELETE", "OPTIONS"])
@bp.route("/mcp/<agent_key>", methods=["GET", "POST", "DELETE", "OPTIONS"])
def mcp(agent_key=None):
    if request.method == "OPTIONS":
        return _json_antwort({}, 204, {"Access-Control-Allow-Headers": "Content-Type, Authorization, "
                                       "Mcp-Session-Id, MCP-Protocol-Version", "Access-Control-Allow-Methods": "POST"})
    c = db()
    auth = request.headers.get("Authorization", "")
    a = agenten.token_agent(c, auth[7:].strip()) if auth.lower().startswith("bearer ") else None
    if not a:
        return _401(request.path.rstrip("/"))
    if agent_key and agent_key != a["key"]:
        return _json_antwort({"jsonrpc": "2.0", "id": None, "error": {
            "code": -32001, "message": "Dieses Token gehört zu einem anderen Agent."}}, 403)
    if request.method != "POST":  # kein Server-Stream: alles läuft über einzelne POSTs
        return Response("Nur POST.", 405, {"Allow": "POST"})
    nachricht = request.get_json(silent=True)
    if nachricht is None:
        return _json_antwort({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Kein JSON."}}, 400)
    header = request.headers.get("MCP-Protocol-Version")
    if isinstance(nachricht, list):
        antworten = [x for x in (mcp_server.behandeln(c, a["wer"], m, header) for m in nachricht) if x]
        return _json_antwort(antworten) if antworten else Response(status=202)
    antwort = mcp_server.behandeln(c, a["wer"], nachricht, header)
    if antwort is None:
        return Response(status=202)
    return _json_antwort(antwort)

# ---------------------------------------------------------------- Einstellungen → Agenten


def _verwalten():
    return darf("personen", firma.VERWALTEN)


@bp.get("/einstellungen/agenten")
@login_required
def einstellungen():
    c = db()
    alle = agenten.liste(c)
    verb = agenten.verbindungen(c)
    je = {}
    for v in verb:
        je.setdefault(v["agent"], []).append(v)
    return render_template("agenten.html", account=g.account, agents=alle, verbindungen=je,
                           regeln=agenten.regeln(c), labels=agenten.REGEL_LABEL, arten=agenten.ARTEN,
                           rechte=freigaben.rechte(c), bereiche=firma.bereiche(c), verwalten=_verwalten(),
                           basis=_basis(), einrichtung=agenten.einrichtung(c),
                           karten={a["key"]: agenten.karten_heute(c, a["key"]) for a in alle})


@bp.post("/einstellungen/agenten/regeln")
@login_required
def regeln_speichern():
    if not _verwalten():
        return _fertig("Ändern darf, wer Personen und Rechte verwaltet.", url_for("agenten.einstellungen"), ok=False)
    try:
        agenten.regeln_speichern(db(), me(), request.form)
    except agenten.AgentError as exc:
        return _fertig(str(exc), url_for("agenten.einstellungen"), ok=False)
    return _fertig("Regeln gespeichert.", url_for("agenten.einstellungen"))


@bp.post("/einstellungen/agenten/neu")
@login_required
def neu():
    if not _verwalten():
        return _fertig("Anlegen darf, wer Personen und Rechte verwaltet.", url_for("agenten.einstellungen"), ok=False)
    try:
        from . import firma
        alles = request.form.get("zugriff") == "alles"
        key = agenten.anlegen(db(), me(), request.form.get("name"), request.form.get("beschreibung"),
                              request.form.get("bereich") or None,
                              rechte_neu={k: agenten.MAX_STUFE for k in firma.MODUL_KEYS} if alles else None)
    except agenten.AgentError as exc:
        return _fertig(str(exc), url_for("agenten.einstellungen"), ok=False)
    return _fertig("Agent angelegt. Jetzt Rechte setzen und verbinden.", url_for("agenten.detail", key=key))


@bp.get("/einstellungen/agenten/<key>")
@login_required
def detail(key):
    c = db()
    a = agenten.get(c, key)
    if not a:
        return _fertig("Diesen Agent gibt es nicht.", url_for("agenten.einstellungen"), ok=False)
    aktivitaet = firma.protokoll(c, wer=a["wer"], limit=30)
    return render_template("agent.html", account=g.account, a=a, module=firma.MODULE,
                           stufen=[s for s in firma.STUFEN if s[0] <= agenten.MAX_STUFE], bereiche=firma.bereiche(c),
                           verbindungen=agenten.verbindungen(c, a["key"]), regeln=agenten.regeln(c),
                           verwalten=_verwalten(), basis=_basis(), aktivitaet=aktivitaet,
                           werkzeuge=[(n, w[0], mcp_server.sichtbar(c, a["wer"], n)) for n, w in
                                      mcp_server.WERKZEUGE.items()],
                           karten_heute=agenten.karten_heute(c, a["key"]), art_labels=firma.ART_LABELS)


@bp.post("/einstellungen/agenten/<key>")
@login_required
def speichern(key):
    ziel = url_for("agenten.detail", key=key)
    if not _verwalten():
        return _fertig("Ändern darf, wer Personen und Rechte verwaltet.", ziel, ok=False)
    try:
        agenten.speichern(db(), me(), key, request.form)
    except agenten.AgentError as exc:
        return _fertig(str(exc), ziel, ok=False)
    return _fertig("Gespeichert. Gilt ab dem nächsten Aufruf des Agents.", ziel)


@bp.post("/einstellungen/agenten/<key>/alles")
@login_required
def alles(key):
    ziel = url_for("agenten.detail", key=key)
    if not _verwalten():
        return _fertig("Ändern darf, wer Personen und Rechte verwaltet.", ziel, ok=False)
    try:
        agenten.alles_erlauben(db(), me(), key)
    except agenten.AgentError as exc:
        return _fertig(str(exc), ziel, ok=False)
    return _fertig("Darf jetzt alles lesen und bearbeiten. Freigeben bleibt bei euch.", ziel)


@bp.post("/einstellungen/agenten/<key>/status")
@login_required
def status(key):
    ziel = url_for("agenten.detail", key=key)
    if not _verwalten():
        return _fertig("Ändern darf, wer Personen und Rechte verwaltet.", ziel, ok=False)
    try:
        agenten.status_setzen(db(), me(), key, request.form.get("status"))
    except agenten.AgentError as exc:
        return _fertig(str(exc), ziel, ok=False)
    return _fertig("Gesperrt. Der Agent kommt ab sofort nicht mehr rein." if request.form.get("status") == "gesperrt"
                   else "Wieder aktiv.", ziel)


@bp.post("/einstellungen/agenten/<key>/loeschen")
@login_required
def loeschen(key):
    if not _verwalten():
        return _fertig("Löschen darf, wer Personen und Rechte verwaltet.", url_for("agenten.einstellungen"), ok=False)
    try:
        agenten.loeschen(db(), me(), key)
    except agenten.AgentError as exc:
        return _fertig(str(exc), url_for("agenten.einstellungen"), ok=False)
    return _fertig("Agent gelöscht. Was er angelegt hat, bleibt.", url_for("agenten.einstellungen"))


@bp.post("/einstellungen/agenten/verbindung/<familie>/trennen")
@login_required
def trennen(familie):
    ziel = safe_next(request.form.get("next", "")) or url_for("agenten.einstellungen")
    if not _verwalten():
        return _fertig("Trennen darf, wer Personen und Rechte verwaltet.", ziel, ok=False)
    try:
        agenten.verbindung_trennen(db(), me(), familie)
    except agenten.AgentError as exc:
        return _fertig(str(exc), ziel, ok=False)
    return _fertig("Getrennt. Das Token gilt ab sofort nicht mehr.", ziel)

# ---------------------------------------------------------------- Einrichten (Onboarding)


def _durchlass_pruefen(base):
    """Kommen Grok und Claude von außen an die Anmeldung? Hinter Cloudflare Access kommt statt JSON eine Login-Seite."""
    url = base + "/.well-known/oauth-authorization-server"
    if not base.startswith("https://"):
        return False, "Die Adresse der App beginnt nicht mit https://. Grok und Claude brauchen HTTPS."
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "ylva-os-check"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            roh = resp.read(20000)
            endziel = resp.geturl()
        daten = json.loads(roh)
        if daten.get("token_endpoint"):
            return True, "Erreichbar. Grok und Claude kommen an Anmeldung und Schnittstelle."
        return False, "Antwort ohne die erwarteten Angaben."
    except urllib.error.HTTPError as exc:
        if exc.code in (302, 401, 403):
            return False, "Gesperrt (vermutlich Cloudflare Access). Bitte den Durchlass anlegen (Schritt unten)."
        return False, f"Fehler {exc.code} beim Abruf von {url}."
    except (ValueError, json.JSONDecodeError):
        return False, ("Statt der Anmelde-Angaben kam eine Webseite zurück, vermutlich die Anmeldung von Cloudflare "
                       f"Access ({locals().get('endziel', url)}). Bitte den Durchlass anlegen.")
    except Exception as exc:  # Netz, DNS, Zertifikat
        return False, f"Nicht erreichbar: {exc}"


@bp.get("/einstellungen/agenten/einrichten")
@login_required
def einrichten():
    from . import leaddb
    c = db()
    stand = agenten.einrichtung(c)
    check = leaddb.kv_get(c, "agenten:durchlass", None)
    base = _basis()
    return render_template("agenten_einrichten.html", account=g.account, stand=stand, basis=base, check=check,
                           verwalten=_verwalten(), base_gesetzt=bool(base.startswith("https://")),
                           verbindungen={v["agent"]: v for v in agenten.verbindungen(c) if v["aktiv"]},
                           prompts=agenten_prompts(base), redirects=agenten.regeln(c)["redirects"])


@bp.post("/einstellungen/agenten/einrichten/pruefen")
@login_required
def einrichten_pruefen():
    from . import leaddb
    ok, text = _durchlass_pruefen(_basis())
    leaddb.kv_set(db(), "agenten:durchlass", {"ok": ok, "text": text, "am": agenten.now()})
    return _fertig(text, url_for("agenten.einrichten") + "#durchlass", ok=True)


@bp.post("/einstellungen/agenten/einrichten/fertig")
@login_required
def einrichten_fertig():
    from . import leaddb
    if not _verwalten():
        return _fertig("Das darf, wer Personen und Rechte verwaltet.", url_for("agenten.einrichten"), ok=False)
    leaddb.kv_set(db(), "agenten:eingerichtet", request.form.get("wieder") != "1")
    firma.log(db(), me(), "einstellung", "system", "agenten", "Einrichtung der Agents abgeschlossen"
              if request.form.get("wieder") != "1" else "Einrichtung der Agents wieder geöffnet")
    return _fertig("Fertig. Die Agents arbeiten ab jetzt über das OS.", url_for("agenten.einstellungen"))


def agenten_prompts(base):
    from . import agenten_prompts as ap
    return ap.alle(base)

# ---------------------------------------------------------------- Freigabe-Karten (Menschen entscheiden)


def _ziel():
    return safe_next(request.form.get("next", "")) or url_for("freigaben.zentrale")


@bp.post("/freigaben/agent/<int:fid>")
@login_required
def entscheiden(fid):
    c = db()
    f = request.form
    aktion = f.get("aktion", "")
    try:
        if aktion == "spaeter":
            agenten.spaeter(c, me(), fid)
            return _fertig("Auf morgen früh gelegt.", _ziel())
        if aktion == "ablehnen":
            agenten.entscheiden(c, me(), fid, False, grund=f.get("grund", ""))
            return _fertig("Abgelehnt. Der Agent sieht es beim nächsten Lauf.", _ziel())
        if aktion in ("freigeben", "option"):
            text = f.get("text") if "text" in f else None
            betreff = f.get("betreff") if "betreff" in f else None
            an = f.get("an") if "an" in f else None
            erg = agenten.entscheiden(c, me(), fid, True, option=f.get("option"), grund=f.get("grund", ""),
                                      text=text, betreff=betreff, an=an)
        elif aktion == "erneut":
            fr = agenten._freigabe(c, fid)
            if fr["entschieden_von"] != me() and not darf("entscheidungen", firma.VERWALTEN):
                raise agenten.AgentError("Erneut ausführen darf, wer freigegeben hat.")
            erg = agenten.ausfuehren(c, me(), fid)
        else:
            raise agenten.AgentError("Unbekannte Aktion.")
    except agenten.AgentError as exc:
        return _fertig(str(exc), _ziel(), ok=False)
    st = erg.get("status")
    if st == "fehler":
        return _fertig(f"Freigegeben, aber nicht ausgeführt: {erg.get('text')}", _ziel(), ok=False)
    return _fertig({"ausgefuehrt": erg.get("text") or "Erledigt.", "bitte_ausfuehren": "Freigegeben. Der Agent führt "
                    "es jetzt aus.", "entschieden": "Entschieden und im Entscheidungs-Log.",
                    "wartet_auf_weitere": "Freigegeben. Es fehlt noch die Freigabe der anderen."}.get(st, "Freigegeben."),
                   _ziel())


@bp.post("/freigaben/agent/buendel")
@login_required
def buendel():
    c = db()
    ids = [int(x) for x in request.form.getlist("ids") if x.isdigit()]
    aktion = request.form.get("aktion")
    if not ids:
        return _fertig("Bitte mindestens einen Eintrag auswählen.", _ziel(), ok=False)
    ok, fehler = 0, []
    for fid in ids:
        try:
            if aktion == "ablehnen":
                agenten.entscheiden(c, me(), fid, False, grund=request.form.get("grund", ""))
            elif aktion == "spaeter":
                agenten.spaeter(c, me(), fid)
            else:
                erg = agenten.entscheiden(c, me(), fid, True)
                if erg.get("status") == "fehler":
                    fehler.append(erg.get("text"))
                    continue
            ok += 1
        except agenten.AgentError as exc:
            fehler.append(str(exc))
    text = f"{ok} von {len(ids)} {'abgelehnt' if aktion == 'ablehnen' else 'auf später' if aktion == 'spaeter' else 'freigegeben'}."
    if fehler:
        text += " Nicht geklappt: " + fehler[0]
    return _fertig(text, _ziel(), ok=not fehler or ok > 0)
