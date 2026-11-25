"""Agenten-Schnittstelle (Firmen-OS, Stufe 12): KI-Agents arbeiten über MCP im OS, Menschen geben frei.

- **Agenten-Konten** (`agenten`): Jeder verbundene Agent (z. B. die Grok-Bots Ops, Delivery, Outreach, Content) hat
  ein eigenes Konto mit Name, Bereich, Rechten je Modul (Rechte-Modell aus `firma`) und Tageslimits. Im Protokoll
  erscheint er als `agent:<key>`. Agents haben höchstens „Bearbeiten“: **Freigeben kann ein Agent nie** (hart im
  Code, nicht per Einstellung abschaltbar; geprüft in `firma.rechte`, `freigaben.entscheiden` und hier).
- **Anmeldung** per OAuth 2.1 mit dynamischer Client-Registrierung und PKCE (`oauth_clients`, `oauth_codes`,
  `agent_tokens`). Tokens stehen nur als Hash in der Datenbank. Sperren wirkt sofort, weil jeder Aufruf das Token
  und den Status des Agents prüft.
- **Entwürfe** (`agent_entwuerfe`): Mail, Nachricht, Content-Post. Der Inhalt hat einen Hash; ausgeführt wird nur,
  was genau so freigegeben wurde. Ändert ein Agent den Entwurf danach, ist die Freigabe hinfällig.
- **Freigaben** (Zeilen in `freigaben` mit art='agent'): Das OS bestimmt aus Art und Entscheidungsrechten selbst,
  wer zuständig ist. Der Agent kann höchstens jemanden vorschlagen. Nach der Freigabe führt das OS aus, was es kann
  (Mail über das Postfach, Lead-Stufe, Projekt-Status); sonst meldet es dem Agent „bitte ausführen“ zurück.
- **Infos** (`agent_infos`): Reine Infos ohne Entscheidung landen im Tagesüberblick auf Mein Tag.

Alle Regeln (Redirect-Adressen, Token-Laufzeiten, Tageslimits, Bündeln, wer welche Art entscheidet) stehen in den
Einstellungen (kv „regel:agenten“), nicht im Code (G9).
"""
import hashlib
import json
import re
import secrets
import uuid
from datetime import date, datetime, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS agenten (
    key TEXT PRIMARY KEY, name TEXT NOT NULL, beschreibung TEXT, bereich TEXT, rechte TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'aktiv', max_karten_tag INTEGER, max_schreiben_tag INTEGER, erstellt_am TEXT NOT NULL,
    erstellt_von TEXT, zuletzt_aktiv TEXT, zuletzt_was TEXT, sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id TEXT PRIMARY KEY, secret_hash TEXT, name TEXT, redirect_uris TEXT NOT NULL, erstellt_am TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_codes (
    code_hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, agent TEXT NOT NULL, redirect_uri TEXT NOT NULL,
    challenge TEXT NOT NULL, resource TEXT, ablauf TEXT NOT NULL, person TEXT, benutzt INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS agent_tokens (
    token_hash TEXT PRIMARY KEY, art TEXT NOT NULL, familie TEXT NOT NULL, client_id TEXT, agent TEXT NOT NULL,
    ablauf TEXT NOT NULL, erstellt_am TEXT NOT NULL, erstellt_von TEXT, gesperrt INTEGER NOT NULL DEFAULT 0,
    zuletzt TEXT
);
CREATE INDEX IF NOT EXISTS agent_tokens_familie ON agent_tokens(familie);
CREATE TABLE IF NOT EXISTS agent_entwuerfe (
    id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT NOT NULL, art TEXT NOT NULL, lead_id INTEGER, projekt_id INTEGER,
    an TEXT, betreff TEXT, text TEXT NOT NULL, kanal TEXT, antwort INTEGER NOT NULL DEFAULT 0, hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'entwurf', erstellt_am TEXT NOT NULL, geaendert_am TEXT, geaendert_von TEXT
);
CREATE TABLE IF NOT EXISTS agent_infos (
    id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT NOT NULL, at TEXT NOT NULL, titel TEXT NOT NULL, text TEXT,
    fuer TEXT, objekt_typ TEXT, objekt_id INTEGER
);
CREATE INDEX IF NOT EXISTS agent_infos_at ON agent_infos(at);
"""

PREFIX = "agent:"
STATUS_LABEL = {"aktiv": "Aktiv", "gesperrt": "Gesperrt"}
DRINGLICHKEIT = {"dringend": ("🟥", "Dringend"), "normal": ("🟧", "Normal"), "niedrig": ("⬜", "Kann warten")}
OPTION_KEYS = ("A", "B", "C")
MAX_STUFE = 2  # Agents höchstens „Bearbeiten“: nie „Freigeben“ oder „Verwalten“

# Startwerte der vier Grok-Bots (einmalig über leaddb.DATA_MIGRATIONS, danach in der App gepflegt)
_R = {"aufgaben": 2, "kalender": 1, "cockpit": 1, "entscheidungen": 1}
STARTAGENTEN = [
    ("ops", "Ops", "Meetings → Aufgaben, Rechnungs-Checks, Termin-Briefings, Aufgaben verteilen und nachhalten.",
     "markt", {**_R, "kunden": 1, "leads": 1, "crm": 1, "finanzen": 1}, 1),
    ("delivery", "Delivery", "Kunden-Delivery: Status-Updates, Antworten an Kunden vorbereiten, Projekt-Notizen.",
     "markt", {**_R, "kunden": 2, "leads": 1, "crm": 2}, 2),
    ("outreach", "Outreach", "Vertrieb: Leads recherchieren und anlegen, Erstnachrichten und Follow-ups vorbereiten.",
     "akquise", {**_R, "leads": 2, "crm": 2}, 3),
    ("content", "Content", "Markt-Scan und Content-Entwürfe (LinkedIn, Website, Case Studys) zur Freigabe.",
     "markt", {**_R, "kunden": 1, "leads": 1}, 4),
]
SEED = [
    "INSERT OR IGNORE INTO agenten (key, name, beschreibung, bereich, rechte, erstellt_am, erstellt_von, sort) "
    "VALUES ('%s', '%s', '%s', '%s', '%s', datetime('now', 'localtime'), 'System', %d)"
    % (k, n, b.replace("'", "''"), ber, json.dumps(r), s) for k, n, b, ber, r, s in STARTAGENTEN
]

# Was ein Agent zur Freigabe vorlegen kann. Je Art: welches Entscheidungsrecht gilt (einstellbar), wer freigeben
# darf (Recht „Freigeben“ im Modul), was das OS danach selbst tut.
ARTEN = {
    "mail": {"label": "Mail an Lead oder Kunden", "recht": "eigener_bereich", "modul": "crm", "ausfuehren": "mail",
             "hinweis": "Das OS verschickt genau den freigegebenen Text über das Postfach."},
    "nachricht": {"label": "Nachricht (LinkedIn, WhatsApp, Brief, Anruf)", "recht": "eigener_bereich", "modul": "crm",
                  "ausfuehren": "manuell", "hinweis": "Kein Kanal im OS: Der Agent oder ihr führt es aus."},
    "content": {"label": "Content-Post (LinkedIn, Website, Case Study)", "recht": "eigener_bereich",
                "modul": "entscheidungen", "ausfuehren": "manuell", "hinweis": "Veröffentlichen außerhalb des OS."},
    "status": {"label": "Status ändern (Lead-Stufe, Projekt weiterschalten)", "recht": "eigener_bereich",
               "modul": "leads", "ausfuehren": "status", "hinweis": "Das OS setzt den Status selbst."},
    "entscheidung": {"label": "Entscheidung im Bereich", "recht": "eigener_bereich", "modul": "entscheidungen",
                     "ausfuehren": "entscheidung", "hinweis": "Kommt ins Entscheidungs-Log."},
    "ausgabe": {"label": "Ausgabe oder Kauf", "recht": "eigener_bereich", "modul": "entscheidungen",
                "ausfuehren": "entscheidung", "hinweis": "Ab der Betragsgrenze entscheidet das Gründerteam."},
    "preis": {"label": "Preis oder Garantie", "recht": "preis", "modul": "entscheidungen",
              "ausfuehren": "entscheidung", "hinweis": "Kommt ins Entscheidungs-Log."},
    "kundenversprechen": {"label": "Kundenversprechen mit Technikzeit", "recht": "kundenversprechen",
                          "modul": "entscheidungen", "ausfuehren": "entscheidung",
                          "hinweis": "Markt und Technik entscheiden gemeinsam."},
    "grundsatz": {"label": "Grundsatz (Zielgruppe, Verträge, Anteile, Einstellungen)", "recht": "gruenderteam",
                  "modul": "entscheidungen", "ausfuehren": "entscheidung", "hinweis": "Gründerteam gemeinsam."},
    "sonstiges": {"label": "Sonstige Freigabe", "recht": "eigener_bereich", "modul": "entscheidungen",
                  "ausfuehren": "manuell", "hinweis": "Der Agent macht nach dem Ja selbst weiter."},
}
ENTWURF_ARTEN = {"mail": "Mail", "nachricht": "Nachricht", "content": "Content-Post"}
ENTWURF_STATUS = {"entwurf": "Entwurf", "wartet": "Wartet auf Freigabe", "freigegeben": "Freigegeben",
                  "ausgefuehrt": "Ausgeführt", "abgelehnt": "Abgelehnt", "ersetzt": "Ersetzt"}
AUSFUEHRUNG_LABEL = {"ausgefuehrt": "Vom OS ausgeführt", "bitte_ausfuehren": "Freigegeben, bitte ausführen",
                     "erledigt": "Vom Agent erledigt", "entschieden": "Entschieden", "fehler": "Fehler beim Ausführen",
                     "wartet_auf_weitere": "Wartet auf weitere Freigaben"}

REDIRECTS_STANDARD = [
    "https://grok.com/connectors-oauth-exchange-code/",
    "https://grok.com/connectors-oauth-exchange-code",
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
]
REGELN = {
    "redirects": REDIRECTS_STANDARD,
    "localhost": False,          # http://localhost:… und 127.0.0.1 (z. B. Claude Code am eigenen Rechner)
    "access_h": 24,              # Gültigkeit Zugangs-Token in Stunden
    "refresh_tage": 180,         # Gültigkeit Erneuerungs-Token in Tagen (wird bei jedem Erneuern ersetzt)
    "max_karten_tag": 5,         # Freigabe-Karten je Agent und Tag (ein Bündel zählt einmal)
    "max_schreiben_tag": 300,    # schreibende Aufrufe je Agent und Tag
    "buendeln": True,            # gleichartige Karten eines Agents als eine Karte zeigen
    "push_dringend": True,       # Push aufs Handy bei 🟥
    "arten": {},                 # Art → Entscheidungsrecht (leer = Standard aus ARTEN)
}
REGEL_LABEL = {
    "localhost": "Lokale Adressen erlauben (localhost, z. B. Claude Code am eigenen Rechner)",
    "access_h": "Zugangs-Token gilt (Stunden)",
    "refresh_tage": "Verbindung bleibt ohne Nutzung bestehen (Tage)",
    "max_karten_tag": "Freigabe-Karten je Agent und Tag (Standard)",
    "max_schreiben_tag": "Schreibende Aufrufe je Agent und Tag (Standard)",
    "buendeln": "Gleichartige Karten eines Agents bündeln",
    "push_dringend": "Push aufs Handy bei dringenden Freigaben",
}


class AgentError(ValueError):
    """Fehler, den der Agent (oder die Person in der App) als klare Meldung bekommt."""


def now():
    return datetime.now().isoformat(timespec="seconds")


def _json(raw, default):
    try:
        v = json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default
    return v if isinstance(v, type(default)) else default


def _hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def ist_agent(wer):
    return isinstance(wer, str) and wer.startswith(PREFIX)


def schluessel(wer):
    return wer[len(PREFIX):] if ist_agent(wer) else wer


def wer_von(key):
    return f"{PREFIX}{key}"

# ---------------------------------------------------------------- Regeln (Einstellungen → Agenten)


def regeln(c):
    from . import leaddb
    stored = leaddb.kv_get(c, "regel:agenten", {}) or {}
    out = {**REGELN, **{k: v for k, v in stored.items() if k in REGELN}}
    if not isinstance(out["redirects"], list):
        out["redirects"] = list(REDIRECTS_STANDARD)
    for k, lo, hi in (("access_h", 1, 720), ("refresh_tage", 1, 730), ("max_karten_tag", 1, 100),
                      ("max_schreiben_tag", 10, 5000)):
        try:
            out[k] = max(lo, min(hi, int(out[k])))
        except (TypeError, ValueError):
            out[k] = REGELN[k]
    out["arten"] = {a: (out["arten"] or {}).get(a) or v["recht"] for a, v in ARTEN.items()} \
        if isinstance(out["arten"], dict) else {a: v["recht"] for a, v in ARTEN.items()}
    return out


def _redirect_ok(uri):
    return bool(re.fullmatch(r"https://[^\s/?#]+(/[^\s#]*)?|http://(localhost|127\.0\.0\.1)(:\d+)?(/[^\s#]*)?", uri))


def regeln_speichern(c, wer, form):
    from . import firma, freigaben, leaddb
    alt = regeln(c)
    neu = dict(alt)
    uris = [u.strip() for u in re.split(r"[\s,]+", form.get("redirects", "")) if u.strip()]
    falsch = [u for u in uris if not _redirect_ok(u)]
    if falsch:
        raise AgentError(f"Diese Adresse geht nicht: {falsch[0]} (nur https://… oder lokal http://localhost).")
    neu["redirects"] = list(dict.fromkeys(uris))
    for k in ("localhost", "buendeln", "push_dringend"):
        neu[k] = bool(form.get(k))
    for k in ("access_h", "refresh_tage", "max_karten_tag", "max_schreiben_tag"):
        if str(form.get(k, "")).strip():
            try:
                neu[k] = int(form.get(k))
            except ValueError as exc:
                raise AgentError(f"„{REGEL_LABEL[k]}“ bitte als ganze Zahl.") from exc
    keys = {r["key"] for r in freigaben.rechte(c)}
    arten = {}
    for a in ARTEN:
        v = form.get(f"art_{a}") or alt["arten"][a]
        if v not in keys:
            raise AgentError("Bitte für jede Art ein vorhandenes Entscheidungsrecht wählen.")
        arten[a] = v
    neu["arten"] = arten
    leaddb.kv_set(c, "regel:agenten", neu)
    neu = regeln(c)
    geaendert = [k for k in REGELN if neu[k] != alt[k]]
    if geaendert:
        firma.log(c, wer, "einstellung", "regel", "agenten", "Regeln für Agents geändert: "
                  + ", ".join(REGEL_LABEL.get(k, {"redirects": "erlaubte Rücksprung-Adressen",
                                                  "arten": "wer welche Art entscheidet"}.get(k, k))
                              for k in geaendert))
    return neu

# ---------------------------------------------------------------- Agenten-Konten


def _zeile(row):
    from . import firma
    if not row:
        return None
    d = dict(row)
    d["wer"] = wer_von(d["key"])
    roh = _json(d.get("rechte"), {})
    d["rechte"] = {k: max(0, min(MAX_STUFE, int(roh.get(k, 0)) if str(roh.get(k, 0)).isdigit() else 0))
                   for k in firma.MODUL_KEYS}
    d["status_label"] = STATUS_LABEL.get(d["status"], d["status"])
    return d


def liste(c):
    return [_zeile(r) for r in c.execute("SELECT * FROM agenten ORDER BY sort, name")]


def get(c, key):
    return _zeile(c.execute("SELECT * FROM agenten WHERE key=?", (schluessel(key or ""),)).fetchone())


def rechte(c, wer):
    """Rechte eines Agents je Modul, nie über „Bearbeiten“. Gesperrt oder unbekannt = keine Rechte."""
    from . import firma
    a = get(c, wer)
    if not a or a["status"] != "aktiv":
        return {k: 0 for k in firma.MODUL_KEYS}
    return a["rechte"]


def name_von(c, wer):
    a = c.execute("SELECT name FROM agenten WHERE key=?", (schluessel(wer),)).fetchone()
    return f"{a['name']} (Agent)" if a else wer


def _rechte_aus_form(form):
    from . import firma
    out = {}
    for k in firma.MODUL_KEYS:
        try:
            out[k] = max(0, min(MAX_STUFE, int(form.get(f"r_{k}", 0))))
        except (TypeError, ValueError):
            out[k] = 0
    return out


def _limit(v):
    v = str(v or "").strip()
    if not v:
        return None
    if not v.isdigit() or not 0 < int(v) <= 5000:
        raise AgentError("Tageslimits bitte als ganze Zahl (leer = Standard aus den Regeln).")
    return int(v)


def anlegen(c, wer, name, beschreibung="", bereich=None, rechte_neu=None):
    from . import firma
    name = re.sub(r"\s+", " ", name or "").strip()
    if not name or len(name) > 40:
        raise AgentError("Bitte einen Namen (höchstens 40 Zeichen), z. B. „Ops“.")
    if bereich and not firma.bereich(c, bereich):
        raise AgentError("Diesen Bereich gibt es nicht.")
    taken = {r["key"] for r in c.execute("SELECT key FROM agenten")}
    key = firma.slug(name, taken)
    rechte_neu = rechte_neu or {"aufgaben": 1}
    sort = (c.execute("SELECT MAX(sort) FROM agenten").fetchone()[0] or 0) + 1
    c.execute("INSERT INTO agenten (key, name, beschreibung, bereich, rechte, erstellt_am, erstellt_von, sort) "
              "VALUES (?,?,?,?,?,?,?,?)", (key, name, (beschreibung or "").strip()[:300] or None, bereich or None,
                                           json.dumps({k: min(MAX_STUFE, v) for k, v in rechte_neu.items()}), now(),
                                           wer, sort))
    firma.log(c, wer, "agent", "agent", key, f"Agent angelegt: {name}")
    return key


def speichern(c, wer, key, form):
    from . import firma
    a = get(c, key)
    if not a:
        raise AgentError("Diesen Agent gibt es nicht.")
    name = re.sub(r"\s+", " ", form.get("name", a["name"]) or "").strip()
    if not name or len(name) > 40:
        raise AgentError("Bitte einen Namen (höchstens 40 Zeichen).")
    bereich = form.get("bereich") or None
    if bereich and not firma.bereich(c, bereich):
        raise AgentError("Diesen Bereich gibt es nicht.")
    neu = _rechte_aus_form(form)
    c.execute("UPDATE agenten SET name=?, beschreibung=?, bereich=?, rechte=?, max_karten_tag=?, max_schreiben_tag=? "
              "WHERE key=?", (name, (form.get("beschreibung") or "").strip()[:300] or None, bereich,
                              json.dumps(neu), _limit(form.get("max_karten_tag")),
                              _limit(form.get("max_schreiben_tag")), a["key"]))
    geaendert = {k: neu[k] for k in neu if neu[k] != a["rechte"][k]}
    firma.log(c, wer, "agent", "agent", a["key"], f"Agent geändert: {name}" +
              (" (Rechte: " + ", ".join(f"{k} {firma.STUFE_LABEL[v]}" for k, v in geaendert.items()) + ")"
               if geaendert else ""), {"rechte": geaendert} if geaendert else None)


def status_setzen(c, wer, key, status):
    from . import firma
    a = get(c, key)
    if not a or status not in STATUS_LABEL:
        raise AgentError("Das geht nicht.")
    c.execute("UPDATE agenten SET status=? WHERE key=?", (status, a["key"]))
    firma.log(c, wer, "agent", "agent", a["key"], f"Agent {a['name']}: {STATUS_LABEL[status]}"
              + (" (alle Zugänge gelten sofort nicht mehr)" if status == "gesperrt" else ""))


def loeschen(c, wer, key):
    """Agent entfernen: Zugänge sperren, Konto löschen. Was er angelegt hat, bleibt (mit Name im Protokoll)."""
    from . import firma
    a = get(c, key)
    if not a:
        raise AgentError("Diesen Agent gibt es nicht.")
    c.execute("UPDATE agent_tokens SET gesperrt=1 WHERE agent=?", (a["key"],))
    c.execute("DELETE FROM agenten WHERE key=?", (a["key"],))
    firma.log(c, wer, "agent", "agent", a["key"], f"Agent gelöscht: {a['name']}")


def aktiv_merken(c, agent, was):
    c.execute("UPDATE agenten SET zuletzt_aktiv=?, zuletzt_was=? WHERE key=?", (now(), was[:80], schluessel(agent)))
    c.commit()

# ---------------------------------------------------------------- OAuth 2.1 (Clients, Codes, Tokens)


def redirect_erlaubt(c, uri):
    r = regeln(c)
    if uri in r["redirects"]:
        return True
    return bool(r["localhost"] and re.fullmatch(r"http://(localhost|127\.0\.0\.1)(:\d+)?(/[^\s#]*)?", uri or ""))


def client_registrieren(c, daten):
    """Dynamische Client-Registrierung (RFC 7591). Nur erlaubte Rücksprung-Adressen (Einstellungen → Agenten)."""
    uris = daten.get("redirect_uris")
    if not isinstance(uris, list) or not uris or not all(isinstance(u, str) for u in uris):
        raise AgentError("redirect_uris fehlt.")
    falsch = [u for u in uris if not redirect_erlaubt(c, u)]
    if falsch:
        raise AgentError(f"Rücksprung-Adresse nicht erlaubt: {falsch[0]}. Erlauben unter Einstellungen → Agenten.")
    client_id = "ylva-" + secrets.token_urlsafe(18)
    methode = daten.get("token_endpoint_auth_method") or "none"
    secret = None
    if methode in ("client_secret_post", "client_secret_basic"):
        secret = secrets.token_urlsafe(32)
    elif methode != "none":
        methode = "none"
    name = str(daten.get("client_name") or "")[:80] or None
    c.execute("INSERT INTO oauth_clients (client_id, secret_hash, name, redirect_uris, erstellt_am) VALUES (?,?,?,?,?)",
              (client_id, _hash(secret) if secret else None, name, json.dumps(uris), now()))
    c.commit()
    out = {"client_id": client_id, "client_id_issued_at": int(datetime.now().timestamp()), "redirect_uris": uris,
           "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
           "token_endpoint_auth_method": methode}
    if name:
        out["client_name"] = name
    if secret:
        out["client_secret"], out["client_secret_expires_at"] = secret, 0
    return out


def client(c, client_id):
    row = c.execute("SELECT * FROM oauth_clients WHERE client_id=?", (client_id or "",)).fetchone()
    return dict(row, redirect_uris=_json(row["redirect_uris"], [])) if row else None


def client_pruefen(c, client_id, secret=None):
    k = client(c, client_id)
    if not k:
        raise AgentError("invalid_client")
    if k["secret_hash"] and (not secret or _hash(secret) != k["secret_hash"]):
        raise AgentError("invalid_client")
    return k


def code_ausstellen(c, person, client_id, agent_key, redirect_uri, challenge, resource=None):
    from . import firma
    a = get(c, agent_key)
    if not a or a["status"] != "aktiv":
        raise AgentError("Diesen Agent gibt es nicht oder er ist gesperrt.")
    code = secrets.token_urlsafe(32)
    c.execute("INSERT INTO oauth_codes (code_hash, client_id, agent, redirect_uri, challenge, resource, ablauf, person) "
              "VALUES (?,?,?,?,?,?,?,?)", (_hash(code), client_id, a["key"], redirect_uri, challenge, resource,
                                         (datetime.now() + timedelta(minutes=10)).isoformat(timespec="seconds"),
                                         person))
    firma.log(c, person, "agent", "agent", a["key"], f"Verbindung für Agent {a['name']} erlaubt "
              f"({(client(c, client_id) or {}).get('name') or 'Client'})")
    return code


def _pkce_ok(verifier, challenge):
    import base64
    if not verifier or not 43 <= len(verifier) <= 128:
        return False
    digest = hashlib.sha256(verifier.encode()).digest()
    return secrets.compare_digest(base64.urlsafe_b64encode(digest).rstrip(b"=").decode(), challenge or "")


def _tokens(c, agent_key, client_id, familie, person):
    r = regeln(c)
    access, refresh = "ya_" + secrets.token_urlsafe(32), "yr_" + secrets.token_urlsafe(32)
    jetzt = datetime.now()
    c.execute("INSERT INTO agent_tokens (token_hash, art, familie, client_id, agent, ablauf, erstellt_am, erstellt_von) "
              "VALUES (?,?,?,?,?,?,?,?)", (_hash(access), "access", familie, client_id, agent_key,
                                           (jetzt + timedelta(hours=r["access_h"])).isoformat(timespec="seconds"),
                                           now(), person))
    c.execute("INSERT INTO agent_tokens (token_hash, art, familie, client_id, agent, ablauf, erstellt_am, erstellt_von) "
              "VALUES (?,?,?,?,?,?,?,?)", (_hash(refresh), "refresh", familie, client_id, agent_key,
                                           (jetzt + timedelta(days=r["refresh_tage"])).isoformat(timespec="seconds"),
                                           now(), person))
    c.commit()
    return {"access_token": access, "token_type": "Bearer", "expires_in": r["access_h"] * 3600,
            "refresh_token": refresh, "scope": "os"}


def code_einloesen(c, code, client_id, redirect_uri, verifier):
    row = c.execute("SELECT * FROM oauth_codes WHERE code_hash=?", (_hash(code or ""),)).fetchone()
    if not row or row["benutzt"] or row["ablauf"] < now() or row["client_id"] != client_id \
            or row["redirect_uri"] != redirect_uri or not _pkce_ok(verifier, row["challenge"]):
        if row and row["benutzt"]:  # Code zweimal benutzt: alles aus dieser Anmeldung sperren (OAuth 2.1)
            c.execute("UPDATE agent_tokens SET gesperrt=1 WHERE familie=?", (row["code_hash"][:32],))
            c.commit()
        raise AgentError("invalid_grant")
    c.execute("UPDATE oauth_codes SET benutzt=1 WHERE code_hash=?", (row["code_hash"],))
    a = get(c, row["agent"])
    if not a or a["status"] != "aktiv":
        raise AgentError("invalid_grant")
    return _tokens(c, a["key"], client_id, row["code_hash"][:32], row["person"])


def erneuern(c, refresh, client_id):
    row = c.execute("SELECT * FROM agent_tokens WHERE token_hash=? AND art='refresh'", (_hash(refresh or ""),)).fetchone()
    if not row or row["client_id"] != client_id or row["ablauf"] < now():
        raise AgentError("invalid_grant")
    if row["gesperrt"]:
        raise AgentError("invalid_grant")
    a = get(c, row["agent"])
    if not a or a["status"] != "aktiv":
        raise AgentError("invalid_grant")
    # Rotation: altes Erneuerungs-Token gilt nicht mehr, alte Zugangs-Tokens laufen aus
    c.execute("UPDATE agent_tokens SET gesperrt=1 WHERE token_hash=?", (row["token_hash"],))
    return _tokens(c, a["key"], client_id, row["familie"], row["erstellt_von"])


def token_agent(c, token):
    """Zum Zugangs-Token den Agent (oder None). Gesperrte Tokens und gesperrte Agents gelten sofort nicht mehr."""
    if not token or len(token) > 200:
        return None
    row = c.execute("SELECT * FROM agent_tokens WHERE token_hash=? AND art='access'", (_hash(token),)).fetchone()
    if not row or row["gesperrt"] or row["ablauf"] < now():
        return None
    a = get(c, row["agent"])
    if not a or a["status"] != "aktiv":
        return None
    jetzt = now()
    if (row["zuletzt"] or "")[:16] != jetzt[:16]:
        c.execute("UPDATE agent_tokens SET zuletzt=? WHERE token_hash=?", (jetzt, row["token_hash"]))
        c.commit()
    return dict(a, familie=row["familie"])


def verbindungen(c, agent_key=None):
    """Verbundene Clients je Agent (eine Zeile je Anmeldung), mit zuletzt genutzt."""
    where, args = ("WHERE t.agent=?", [schluessel(agent_key)]) if agent_key else ("", [])
    rows = c.execute(f"""SELECT t.familie, t.agent, MIN(t.erstellt_am) AS seit, MAX(t.zuletzt) AS zuletzt,
                                MAX(t.ablauf) AS ablauf, MIN(t.gesperrt) AS gesperrt, t.client_id, k.name AS client,
                                MIN(t.erstellt_von) AS von
                         FROM agent_tokens t LEFT JOIN oauth_clients k ON k.client_id = t.client_id {where}
                         GROUP BY t.familie ORDER BY seit DESC""", args).fetchall()
    return [dict(r, aktiv=not r["gesperrt"] and (r["ablauf"] or "") > now()) for r in rows]


def verbindung_trennen(c, wer, familie):
    from . import firma
    row = c.execute("SELECT agent FROM agent_tokens WHERE familie=? LIMIT 1", (familie,)).fetchone()
    if not row:
        raise AgentError("Diese Verbindung gibt es nicht.")
    c.execute("UPDATE agent_tokens SET gesperrt=1 WHERE familie=?", (familie,))
    firma.log(c, wer, "agent", "agent", row["agent"], f"Verbindung von {name_von(c, row['agent'])} getrennt "
              "(Token gesperrt, gilt sofort)")

# ---------------------------------------------------------------- Limits


def _heute_von():
    return date.today().isoformat() + "T00:00:00"


def schreiben_pruefen(c, agent):
    """Schreibende Aufrufe je Tag begrenzen (Schutz vor einer Endlosschleife eines Bots)."""
    a = get(c, agent)
    grenze = a["max_schreiben_tag"] or regeln(c)["max_schreiben_tag"]
    n = c.execute("SELECT COUNT(*) FROM ereignisse WHERE wer=? AND at >= ?", (wer_von(a["key"]), _heute_von())).fetchone()[0]
    if n >= grenze:
        raise AgentError(f"Tageslimit erreicht: {grenze} schreibende Aufrufe. Morgen geht es weiter; das Limit "
                         "steht unter Einstellungen → Agenten.")


def karten_heute(c, agent):
    rows = c.execute("SELECT id, vorschlag FROM freigaben WHERE art='agent' AND angefragt_von=? AND angefragt_am >= ?",
                     (wer_von(schluessel(agent)), _heute_von())).fetchall()
    gesehen = set()
    for r in rows:
        v = _json(r["vorschlag"], {})
        gesehen.add(("b", v["buendel"]) if v.get("buendel") else ("g", v.get("gruppe") or r["id"]))
    return len(gesehen)

# ---------------------------------------------------------------- Entwürfe


def _entwurf_hash(an, betreff, text):
    return _hash(json.dumps([an or "", betreff or "", text or ""], ensure_ascii=False))


def _inhalt_hash(e):
    """Hash aus dem, was wirklich in der Datenbank steht (nicht aus der gespeicherten Spalte)."""
    return _entwurf_hash(e["an"], e["betreff"], e["text"])


def entwurf_get(c, eid):
    row = c.execute("SELECT * FROM agent_entwuerfe WHERE id=?", (eid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["art_label"] = ENTWURF_ARTEN.get(d["art"], d["art"])
    d["status_label"] = ENTWURF_STATUS.get(d["status"], d["status"])
    return d


def _bezug_pruefen(c, lead_id=None, projekt_id=None):
    from . import leaddb, projekte
    lead = leaddb.get_lead(c, lead_id) if lead_id else None
    if lead_id and not lead:
        raise AgentError(f"Lead {lead_id} gibt es nicht. Erst mit leads_suchen die ID holen.")
    p = projekte.get(c, projekt_id) if projekt_id else None
    if projekt_id and not p:
        raise AgentError(f"Projekt {projekt_id} gibt es nicht.")
    if p and not lead:
        lead = leaddb.get_lead(c, p["lead_id"])
    return lead, p


def entwurf_anlegen(c, agent, art, text, lead_id=None, projekt_id=None, an="", betreff="", kanal="", antwort=False):
    from . import firma
    if art not in ENTWURF_ARTEN:
        raise AgentError("Art des Entwurfs: mail, nachricht oder content.")
    text = (text or "").strip()
    if len(text) < 3 or len(text) > 20000:
        raise AgentError("Der Text fehlt oder ist zu lang (höchstens 20.000 Zeichen).")
    lead, p = _bezug_pruefen(c, lead_id, projekt_id)
    an, betreff = (an or "").strip()[:300], re.sub(r"\s+", " ", betreff or "").strip()[:200]
    if art == "mail":
        if not lead:
            raise AgentError("Eine Mail braucht einen Lead oder Kunden (lead_id oder projekt_id).")
        an = an or lead.get("email") or ""
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", an):
            raise AgentError("Für diese Mail fehlt eine gültige Adresse (an).")
        if not betreff and not antwort:
            raise AgentError("Eine neue Mail braucht einen Betreff.")
    cur = c.execute("INSERT INTO agent_entwuerfe (agent, art, lead_id, projekt_id, an, betreff, text, kanal, antwort, "
                    "hash, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (schluessel(agent), art, (lead or {}).get("id"), (p or {}).get("id"), an or None, betreff or None,
                     text, (kanal or "").strip()[:40] or None, 1 if antwort else 0, _entwurf_hash(an, betreff, text),
                     now()))
    eid = cur.lastrowid
    firma.log(c, wer_von(schluessel(agent)), "agent", "entwurf", eid,
              f"Entwurf angelegt ({ENTWURF_ARTEN[art]}){': ' + (lead or {}).get('name', '') if lead else ''}",
              {"lead": (lead or {}).get("id"), "projekt": (p or {}).get("id")})
    return eid


def entwurf_aendern(c, wer, eid, betreff=None, text=None, an=None):
    """Entwurf ändern. Liegt er schon zur Freigabe, ist diese Freigabe hinfällig, wenn ein Agent ändert; ändert die
    prüfende Person beim Freigeben, gilt ihre Fassung. Nach der Freigabe ändert niemand mehr (neuer Entwurf nötig)."""
    from . import firma
    e = entwurf_get(c, eid)
    if not e:
        raise AgentError("Diesen Entwurf gibt es nicht.")
    if ist_agent(wer) and e["agent"] != schluessel(wer):
        raise AgentError("Das ist nicht dein Entwurf.")
    if e["status"] not in ("entwurf", "wartet"):
        raise AgentError(f"Der Entwurf ist {e['status_label'].lower()} und lässt sich nicht mehr ändern. "
                         "Leg einen neuen an und frag neu an.")
    neu_text = e["text"] if text is None else (text or "").strip()
    neu_betreff = e["betreff"] if betreff is None else re.sub(r"\s+", " ", betreff or "").strip()[:200]
    neu_an = e["an"] if an is None else (an or "").strip()[:300]
    if len(neu_text) < 3:
        raise AgentError("Der Text fehlt.")
    if e["art"] == "mail" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", neu_an or ""):
        raise AgentError("Die Adresse sieht nicht richtig aus.")
    h = _entwurf_hash(neu_an, neu_betreff, neu_text)
    if h == e["hash"]:
        return False
    c.execute("UPDATE agent_entwuerfe SET an=?, betreff=?, text=?, hash=?, geaendert_am=?, geaendert_von=? WHERE id=?",
              (neu_an or None, neu_betreff or None, neu_text, h, now(), wer, eid))
    if ist_agent(wer):  # offene Freigabe passt nicht mehr zum Inhalt
        for f in c.execute("SELECT id FROM freigaben WHERE art='agent' AND status='offen' AND "
                           "json_extract(vorschlag, '$.entwurf_id')=?", (eid,)).fetchall():
            c.execute("UPDATE freigaben SET status='hinfaellig', grund=?, entschieden_am=? WHERE id=?",
                      ("Entwurf danach vom Agent geändert", now(), f["id"]))
        c.execute("UPDATE agent_entwuerfe SET status='entwurf' WHERE id=?", (eid,))
    firma.log(c, wer, "agent", "entwurf", eid, f"Entwurf geändert ({e['art_label']})", commit=False)
    c.commit()
    return True

# ---------------------------------------------------------------- Zuständigkeit


def _kontext_person(c, agent, lead=None, projekt=None):
    from . import firma
    for k in ((projekt or {}).get("verantwortlich"), (lead or {}).get("owner")):
        if k and firma.aktive_person(c, k):
            return k
    a = get(c, agent) or {}
    b = firma.bereich(c, a.get("bereich")) if a.get("bereich") else None
    if b and b["verantwortlich"] and firma.aktive_person(c, b["verantwortlich"]):
        return b["verantwortlich"]
    gruender = [p["key"] for p in firma.aktive_personen(c) if firma.darf(c, p["key"], "entscheidungen", firma.VERWALTEN)]
    return gruender[0] if gruender else None


def _darf_freigeben(c, person, modul):
    from . import firma
    return firma.darf(c, person, modul, firma.FREIGEBEN)


def zustaendig(c, agent, art, lead=None, projekt=None, betrag=None):
    """Wer entscheidet? Aus Art → Entscheidungsrecht (Einstellung) und dem Bezug (Lead, Projekt, Bereich des Agents).
    Nur Personen mit „Freigeben“ im passenden Modul. Fehlt so jemand, geht es den Bereich hinauf, dann an die
    Gründer. Gibt (Recht, [Personen], Modus) zurück; die erste Person ist die naheliegendste."""
    from . import firma, freigaben
    info = ARTEN[art]
    modul = "kunden" if art == "status" and projekt else info["modul"]
    kontext = _kontext_person(c, agent, lead, projekt)
    recht_key = regeln(c)["arten"].get(art) or info["recht"]
    try:
        r, noetig, _ = freigaben.wer_entscheidet(c, recht_key, kontext, betrag)
    except freigaben.FreigabeError:
        r, noetig, _ = freigaben.wer_entscheidet(c, "eigener_bereich", kontext, betrag)
    noetig = [p for p in noetig if _darf_freigeben(c, p, modul)]
    if not noetig:
        a = get(c, agent) or {}
        key, gesehen = a.get("bereich"), set()
        while key and key not in gesehen and not noetig:
            gesehen.add(key)
            b = firma.bereich(c, key)
            if not b:
                break
            if b["verantwortlich"] and firma.aktive_person(c, b["verantwortlich"]) \
                    and _darf_freigeben(c, b["verantwortlich"], modul):
                noetig = [b["verantwortlich"]]
            key = b["eltern"]
    if not noetig:
        noetig = [p["key"] for p in firma.aktive_personen(c)
                  if firma.darf(c, p["key"], "entscheidungen", firma.VERWALTEN) and _darf_freigeben(c, p["key"], modul)]
    if not noetig:
        raise AgentError("Gerade gibt es niemanden, der das freigeben darf (Einstellungen → Entscheidungsrechte).")
    if kontext in noetig:
        noetig = [kontext] + [p for p in noetig if p != kontext]
    return r, noetig, r.get("modus", "eine")

# ---------------------------------------------------------------- Freigaben anfragen


def _frist(v):
    v = (v or "").strip()
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v if "T" in v or " " in v else v + "T18:00")
    except ValueError as exc:
        raise AgentError("Frist bitte als Datum (JJJJ-MM-TT) oder Datum und Uhrzeit (JJJJ-MM-TTTHH:MM).") from exc
    return d.isoformat(timespec="minutes")


def _woerter(text):
    return len([w for w in re.split(r"\s+", text.strip()) if w])


def anfragen(c, agent, art, titel, was, optionen=None, empfehlung=None, frist=None, dringlichkeit="normal",
             lead_id=None, projekt_id=None, details="", entwurf_id=None, aktion=None, buendel=None,
             vorschlag_person=None, betrag=None):
    """Freigabe anfragen. Das OS bestimmt die zuständige Person selbst; `vorschlag_person` wird nur angezeigt."""
    from . import aufgaben, firma, leaddb
    a = get(c, agent)
    if not a or a["status"] != "aktiv":
        raise AgentError("Dieser Agent ist gesperrt.")
    if art not in ARTEN:
        raise AgentError("Unbekannte Art. Möglich: " + ", ".join(ARTEN) + ".")
    titel = re.sub(r"\s+", " ", titel or "").strip()
    if not titel or _woerter(titel) > 5 or len(titel) > 60:
        raise AgentError("Titel: höchstens 5 Wörter (Kurzformat), z. B. „Antwort an Tischlerei Hansen“.")
    was = re.sub(r"\s+", " ", was or "").strip()
    if not was or len(was) > 240:
        raise AgentError("„was“: ein Satz, worum es geht (höchstens 240 Zeichen). Mehr gehört in „details“.")
    if dringlichkeit not in DRINGLICHKEIT:
        raise AgentError("Dringlichkeit: dringend, normal oder niedrig.")
    lead, projekt = _bezug_pruefen(c, lead_id, projekt_id)
    opts = []
    for o in optionen or []:
        key = str((o or {}).get("key", "")).strip().upper()
        text = re.sub(r"\s+", " ", str((o or {}).get("text", ""))).strip()
        if key not in OPTION_KEYS or not text or len(text) > 160 or key in [x["key"] for x in opts]:
            raise AgentError("Optionen: bis zu drei, key A, B oder C, text höchstens 160 Zeichen.")
        opts.append({"key": key, "text": text})
    if len(opts) == 1:
        raise AgentError("Eine einzelne Option ist keine Wahl. Lass optionen weg (dann Freigeben/Ablehnen).")
    empfehlung = (empfehlung or "").strip().upper() or None
    if opts and empfehlung not in [o["key"] for o in opts]:
        raise AgentError("Empfehlung: einer der Options-Keys (A, B oder C).")
    info = ARTEN[art]
    entwurf = None
    if entwurf_id:
        entwurf = entwurf_get(c, entwurf_id)
        if not entwurf or entwurf["agent"] != a["key"]:
            raise AgentError("Diesen Entwurf gibt es nicht (oder er ist nicht von dir).")
        if entwurf["status"] != "entwurf":
            raise AgentError(f"Der Entwurf ist {entwurf['status_label'].lower()}. Nur offene Entwürfe zur Freigabe.")
        if entwurf["lead_id"] and not lead:
            lead = leaddb.get_lead(c, entwurf["lead_id"])
    if art == "mail" and (not entwurf or entwurf["art"] != "mail"):
        raise AgentError("Eine Mail braucht einen Mail-Entwurf (erst entwurf_anlegen mit art=mail).")
    if art == "status":
        if not isinstance(aktion, dict) or aktion.get("typ") not in ("lead_stufe", "projekt_status"):
            raise AgentError("Status ändern geht über lead_stufe_setzen oder projekt_status_vorschlagen.")
    elif aktion:
        raise AgentError("„aktion“ gibt es nur bei Art status.")
    if opts and (entwurf or aktion):
        raise AgentError("Entweder Optionen (Entscheidung) oder ein Entwurf/eine Aktion (Freigabe), nicht beides.")
    if opts and info["ausfuehren"] not in ("entscheidung", "manuell"):
        raise AgentError("Optionen passen nur zu Entscheidungen. Für Mails: Entwurf zur Freigabe.")
    if betrag is not None:
        try:
            betrag = round(float(str(betrag).replace(",", ".")), 2)
        except ValueError as exc:
            raise AgentError("Betrag bitte als Zahl in Euro.") from exc
    buendel = re.sub(r"\s+", " ", buendel or "").strip()[:60] or None
    r = regeln(c)
    if not r["buendeln"]:
        buendel = None
    grenze = a["max_karten_tag"] or r["max_karten_tag"]
    in_buendel = buendel and c.execute(
        "SELECT 1 FROM freigaben WHERE art='agent' AND angefragt_von=? AND angefragt_am >= ? AND "
        "json_extract(vorschlag, '$.buendel')=?", (a["wer"], _heute_von(), buendel)).fetchone()
    if not in_buendel and karten_heute(c, a["key"]) >= grenze:
        raise AgentError(f"Tageslimit erreicht: {grenze} Freigabe-Karten heute. Bündle gleichartige Anfragen "
                         "(gleiches „buendel“) oder schick reine Infos mit info_melden. Morgen geht es weiter.")
    recht, personen, modus = zustaendig(c, a["key"], art, lead, projekt, betrag)
    if entwurf:
        h = entwurf["hash"]
    elif aktion:
        h = _hash(json.dumps(aktion, sort_keys=True))
    else:
        h = _hash(json.dumps([titel, was, opts], ensure_ascii=False))
    typ = "entscheidung" if opts else "freigabe"
    vorschlag_person = (vorschlag_person or "").strip().lower() or None
    v = {"typ": typ, "art_key": art, "titel": titel, "was": was, "optionen": opts, "empfehlung": empfehlung,
         "frist": _frist(frist), "dringlichkeit": dringlichkeit, "details": (details or "").strip()[:4000],
         "entwurf_id": (entwurf or {}).get("id"), "aktion": aktion, "hash": h, "buendel": buendel,
         "vorschlag_person": vorschlag_person, "recht": recht["key"], "recht_titel": recht["titel"],
         "betrag": betrag, "lead_id": (lead or {}).get("id"), "projekt_id": (projekt or {}).get("id")}
    # „alle“: bei Freigaben jede nötige Person; Entscheidungen startet die erste, das Log holt die übrigen dazu
    pruefer = personen if (modus == "alle" and typ == "freigabe") else personen[:1]
    gruppe = uuid.uuid4().hex[:12] if len(pruefer) > 1 else None
    v["gruppe"] = gruppe
    objekt = ("projekt", projekt["id"]) if projekt else ("lead", lead["id"]) if lead else (None, None)
    ids = []
    for p in pruefer:
        cur = c.execute("INSERT INTO freigaben (art, objekt_typ, objekt_id, stufe, vorschlag, angefragt_von, "
                        "angefragt_am, pruefer) VALUES ('agent', ?, ?, 'C', ?, ?, ?, ?)",
                        (objekt[0], objekt[1], json.dumps(v, ensure_ascii=False), a["wer"], now(), p))
        ids.append(cur.lastrowid)
    if entwurf:
        c.execute("UPDATE agent_entwuerfe SET status='wartet' WHERE id=?", (entwurf["id"],))
    firma.log(c, a["wer"], "freigabe", objekt[0], objekt[1],
              f"Freigabe angefragt: {titel} → {', '.join(firma.name_von(c, p) for p in pruefer)}",
              {"freigaben": ids, "art": art, "recht": recht["key"]}, commit=False)
    c.commit()
    symbol = DRINGLICHKEIT[dringlichkeit][0]
    for p in pruefer:
        aufgaben.melden(c, p, f"{symbol} {titel}", f"{a['name']}: {was}", f"/freigaben#a{ids[pruefer.index(p)]}",
                        push=dringlichkeit == "dringend" and r["push_dringend"])
    return {"freigabe_ids": ids, "zustaendig": [firma.name_von(c, p) for p in pruefer], "recht": recht["titel"],
            "hinweis": "Das OS hat die zuständige Person aus den Entscheidungsrechten bestimmt. Antworten holst du "
                       "beim nächsten Lauf mit freigaben_status."}


def info(c, agent, titel, text="", fuer=None, lead_id=None, projekt_id=None):
    """Reine Info ohne Entscheidung: landet im Tagesüberblick auf Mein Tag, nicht als Freigabe."""
    from . import firma
    titel = re.sub(r"\s+", " ", titel or "").strip()
    if not titel or len(titel) > 120:
        raise AgentError("Titel der Info: höchstens 120 Zeichen.")
    if fuer and not firma.aktive_person(c, fuer):
        raise AgentError(f"„{fuer}“ ist keine aktive Person (personen_und_bereiche zeigt die Schlüssel).")
    _bezug_pruefen(c, lead_id, projekt_id)
    objekt = ("projekt", projekt_id) if projekt_id else ("lead", lead_id) if lead_id else (None, None)
    cur = c.execute("INSERT INTO agent_infos (agent, at, titel, text, fuer, objekt_typ, objekt_id) VALUES (?,?,?,?,?,?,?)",
                    (schluessel(agent), now(), titel, (text or "").strip()[:2000] or None, fuer or None, *objekt))
    firma.log(c, wer_von(schluessel(agent)), "agent", "info", cur.lastrowid, f"Info: {titel}")
    return cur.lastrowid


def infos_fuer(c, person, tage=1):
    seit = (date.today() - timedelta(days=tage)).isoformat()
    rows = c.execute("SELECT i.*, a.name AS agent_name FROM agent_infos i LEFT JOIN agenten a ON a.key = i.agent "
                     "WHERE i.at >= ? AND (i.fuer IS NULL OR i.fuer = ?) ORDER BY i.id DESC LIMIT 30",
                     (seit, person)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["url"] = (f"/kunden/{d['objekt_id']}" if d["objekt_typ"] == "projekt"
                    else f"/leads/{d['objekt_id']}" if d["objekt_typ"] == "lead" else None)
        out.append(d)
    return out

# ---------------------------------------------------------------- Freigaben für Menschen (Karten)


def _karte(c, f):
    from . import firma
    v = _json(f["vorschlag"], {})
    entwurf = entwurf_get(c, v["entwurf_id"]) if v.get("entwurf_id") else None
    a = get(c, f["angefragt_von"]) or {"name": schluessel(f["angefragt_von"] or "")}
    bezug = None
    if v.get("projekt_id"):
        row = c.execute("SELECT p.titel, l.name FROM projekte p LEFT JOIN leads l ON l.id=p.lead_id WHERE p.id=?",
                        (v["projekt_id"],)).fetchone()
        bezug = {"name": (row["name"] or row["titel"]) if row else "Projekt", "url": f"/kunden/{v['projekt_id']}"}
    elif v.get("lead_id"):
        row = c.execute("SELECT name FROM leads WHERE id=?", (v["lead_id"],)).fetchone()
        bezug = {"name": row["name"] if row else "Lead", "url": f"/leads/{v['lead_id']}"}
    jetzt = now()
    return {"id": f["id"], "typ": v.get("typ"), "art": v.get("art_key"), "art_label": ARTEN.get(v.get("art_key"), {}).get("label"),
            "titel": v.get("titel"), "was": v.get("was"), "optionen": v.get("optionen") or [],
            "empfehlung": v.get("empfehlung"), "frist": v.get("frist"),
            "frist_um": bool(v.get("frist") and v["frist"] < jetzt[:16]),
            "dringlichkeit": v.get("dringlichkeit", "normal"),
            "symbol": DRINGLICHKEIT.get(v.get("dringlichkeit"), DRINGLICHKEIT["normal"])[0],
            "details": v.get("details"), "entwurf": entwurf, "aktion": v.get("aktion"), "buendel": v.get("buendel"),
            "agent": a["name"], "agent_key": schluessel(f["angefragt_von"] or ""), "seit": f["angefragt_am"],
            "pruefer": f["pruefer"], "pruefer_name": firma.name_von(c, f["pruefer"]),
            "vorschlag_person": firma.name_von(c, v["vorschlag_person"]) if v.get("vorschlag_person") else None,
            "recht_titel": v.get("recht_titel"), "betrag": v.get("betrag"), "bezug": bezug,
            "spaeter_bis": f["spaeter_bis"] if "spaeter_bis" in f.keys() else None,
            "gruppe": v.get("gruppe")}


_RANG = {"dringend": 0, "normal": 1, "niedrig": 2}


def karten(c, person, alle=False, buendeln=None):
    """Offene Agent-Freigaben für die Person, dringend und mit naher Frist zuerst. „Später“ zurückgestellte erst
    wieder ab dem Zeitpunkt. Mit Bündeln: gleiches Bündel eines Agents → eine Karte mit mehreren Einträgen."""
    from . import firma
    jetzt = now()
    if alle and firma.darf(c, person, "entscheidungen", firma.VERWALTEN):
        rows = c.execute("SELECT * FROM freigaben WHERE art='agent' AND status='offen' ORDER BY id").fetchall()
    else:
        rows = c.execute("SELECT * FROM freigaben WHERE art='agent' AND status='offen' AND pruefer=? ORDER BY id",
                         (person,)).fetchall()
    einzeln, spaeter = [], 0
    for f in rows:
        if f["spaeter_bis"] and f["spaeter_bis"] > jetzt:
            spaeter += 1
            continue
        einzeln.append(_karte(c, f))
    buendel = regeln(c)["buendeln"] if buendeln is None else buendeln
    out, index = [], {}
    for k in einzeln:
        schl = (k["agent_key"], k["art"], k["buendel"], k["pruefer"]) if buendel and k["buendel"] else None
        if schl and schl in index:
            g = index[schl]
            g["eintraege"].append(k)
            g["rang"] = min(g["rang"], _RANG.get(k["dringlichkeit"], 1))
            if k["frist"] and (not g["frist"] or k["frist"] < g["frist"]):
                g["frist"] = k["frist"]
            continue
        g = {"buendel": k["buendel"] if schl else None, "eintraege": [k], "rang": _RANG.get(k["dringlichkeit"], 1),
             "frist": k["frist"], "erste": k}
        if schl:
            index[schl] = g
        out.append(g)
    for g in out:
        g["symbol"] = ("🟥", "🟧", "⬜")[g["rang"]]
    out.sort(key=lambda g: (g["rang"], g["frist"] or "9999", g["erste"]["seit"]))
    return out, spaeter


def offen_zaehlen(c, person):
    jetzt = now()
    return c.execute("SELECT COUNT(*) FROM freigaben WHERE art='agent' AND status='offen' AND pruefer=? AND "
                     "(spaeter_bis IS NULL OR spaeter_bis <= ?)", (person, jetzt)).fetchone()[0]


def _darf_entscheiden(c, wer, f):
    from . import firma
    if ist_agent(wer) or not firma.aktive_person(c, wer):
        return False
    return wer == f["pruefer"] or firma.darf(c, wer, "entscheidungen", firma.VERWALTEN)


def _freigabe(c, fid):
    f = c.execute("SELECT * FROM freigaben WHERE id=? AND art='agent'", (fid,)).fetchone()
    if not f:
        raise AgentError("Diese Freigabe gibt es nicht.")
    return f


def spaeter(c, wer, fid, bis=None):
    from . import firma
    f = _freigabe(c, fid)
    if f["status"] != "offen" or not _darf_entscheiden(c, wer, f):
        raise AgentError("Das geht hier nicht.")
    bis = bis or (datetime.combine(date.today() + timedelta(days=1), datetime.min.time()) + timedelta(hours=7))
    c.execute("UPDATE freigaben SET spaeter_bis=? WHERE id=?", (bis.isoformat(timespec="seconds"), fid))
    firma.log(c, wer, "freigabe", f["objekt_typ"], f["objekt_id"], f"Auf später gelegt: {_json(f['vorschlag'], {}).get('titel')}")


def entscheiden(c, wer, fid, ok, option=None, grund="", text=None, betreff=None, an=None, data_dir=None):
    """Eine Agent-Freigabe entscheiden. Nur Menschen, nie ein Agent (hart im Code). Bei Freigaben mit Entwurf gilt
    genau der Inhalt, den die Person sieht (oder hier ändert); ausgeführt wird nur, wenn er noch dazu passt."""
    from . import firma
    if ist_agent(wer):
        raise AgentError("Ein Agent kann nie selbst freigeben.")
    f = _freigabe(c, fid)
    if f["status"] != "offen":
        raise AgentError("Diese Freigabe ist schon entschieden.")
    if not _darf_entscheiden(c, wer, f):
        raise AgentError("Entscheiden darf die zuständige Person (oder wer Entscheidungen verwaltet).")
    v = _json(f["vorschlag"], {})
    grund = (grund or "").strip()[:500]
    titel = v.get("titel") or "Freigabe"
    if not ok:
        c.execute("UPDATE freigaben SET status='abgelehnt', grund=?, entschieden_von=?, entschieden_am=? WHERE id=?",
                  (grund or None, wer, now(), fid))
        if v.get("gruppe"):
            c.execute("UPDATE freigaben SET status='hinfaellig', entschieden_am=? WHERE art='agent' AND status='offen' "
                      "AND json_extract(vorschlag, '$.gruppe')=?", (now(), v["gruppe"]))
        if v.get("entwurf_id"):
            c.execute("UPDATE agent_entwuerfe SET status='abgelehnt' WHERE id=?", (v["entwurf_id"],))
        firma.log(c, wer, "freigabe", f["objekt_typ"], f["objekt_id"], f"Abgelehnt: {titel}" +
                  (f" ({grund})" if grund else ""), {"freigabe": fid, "agent": f["angefragt_von"]})
        return {"status": "abgelehnt"}
    geaendert = {}
    if v.get("typ") == "entscheidung":
        keys = [o["key"] for o in v.get("optionen") or []]
        option = (option or "").strip().upper()
        if option not in keys:
            raise AgentError("Bitte eine der Optionen wählen.")
        geaendert["option"] = option
    elif v.get("entwurf_id"):
        e = entwurf_get(c, v["entwurf_id"])
        if not e:
            raise AgentError("Der Entwurf fehlt.")
        if _inhalt_hash(e) != v.get("hash"):  # geändert, nachdem der Agent angefragt hat
            c.execute("UPDATE freigaben SET status='hinfaellig', grund=?, entschieden_am=? WHERE id=?",
                      ("Inhalt passt nicht mehr zur Anfrage", now(), fid))
            c.commit()
            raise AgentError("Der Entwurf wurde nach der Anfrage geändert. Die Freigabe ist hinfällig; der Agent muss "
                             "neu anfragen.")
        if any(x is not None for x in (text, betreff, an)):
            if entwurf_aendern(c, wer, e["id"], betreff=betreff, text=text, an=an):
                e = entwurf_get(c, e["id"])
                v["hash"] = e["hash"]
                geaendert["text_geaendert"] = True
    geaendert["grund"] = grund or None
    c.execute("UPDATE freigaben SET status='angenommen', grund=?, entschieden_von=?, entschieden_am=?, geaendert=?, "
              "vorschlag=? WHERE id=?", (grund or None, wer, now(), json.dumps(geaendert, ensure_ascii=False),
                                         json.dumps(v, ensure_ascii=False), fid))
    firma.log(c, wer, "freigabe", f["objekt_typ"], f["objekt_id"],
              f"Freigegeben: {titel}" + (f" (Option {geaendert['option']})" if geaendert.get("option") else "")
              + (" mit Änderung" if geaendert.get("text_geaendert") else ""),
              {"freigabe": fid, "agent": f["angefragt_von"]}, commit=False)
    c.commit()
    if v.get("gruppe"):
        offen = c.execute("SELECT COUNT(*) FROM freigaben WHERE art='agent' AND status='offen' AND "
                          "json_extract(vorschlag, '$.gruppe')=?", (v["gruppe"],)).fetchone()[0]
        if offen:
            _ausfuehrung_setzen(c, fid, "wartet_auf_weitere", f"Noch {offen} Freigabe(n) offen.")
            return {"status": "wartet_auf_weitere"}
    return ausfuehren(c, wer, fid, data_dir=data_dir)


def _ausfuehrung_setzen(c, fid, status, text="", extra=None):
    d = {"status": status, "text": text, "am": now(), **(extra or {})}
    c.execute("UPDATE freigaben SET ausfuehrung=? WHERE id=?", (json.dumps(d, ensure_ascii=False), fid))
    c.commit()
    return d


def ausfuehren(c, wer, fid, data_dir=None):
    """Nach der Freigabe: das OS führt aus, was es kann, mit genau dem freigegebenen Inhalt. Läuft im Arbeitsbereich
    des Leads (Stufe 11), egal in welchem die freigebende Person gerade arbeitet: Postfach und Sperrliste passen."""
    from . import arbeitsbereiche
    v = _json(_freigabe(c, fid)["vorschlag"], {})
    row = c.execute("SELECT arbeitsbereich FROM leads WHERE id=?", (v.get("lead_id") or 0,)).fetchone()
    with arbeitsbereiche.im((row["arbeitsbereich"] if row else None) or arbeitsbereiche.YLVA):
        return _ausfuehren(c, wer, fid, data_dir)


def _ausfuehren(c, wer, fid, data_dir=None):
    from . import crm, firma, freigaben, leaddb, projekte
    f = _freigabe(c, fid)
    if f["status"] != "angenommen":
        raise AgentError("Ausgeführt wird nur, was freigegeben ist.")
    if ist_agent(wer):
        raise AgentError("Ein Agent führt Freigaben nicht selbst aus.")
    v = _json(f["vorschlag"], {})
    g = _json(f["geaendert"], {})
    art = ARTEN.get(v.get("art_key"), {})
    a_name = name_von(c, f["angefragt_von"])
    try:
        if v.get("typ") == "entscheidung" or art.get("ausfuehren") == "entscheidung":
            opt = next((o for o in v.get("optionen") or [] if o["key"] == g.get("option")), None)
            titel = f"{v['titel']}: {opt['text']}" if opt else v["titel"]
            eid = freigaben.entscheiden_festhalten(
                c, f["entschieden_von"], titel[:200], f"Vorschlag von {a_name}: {v['was']}"
                + (f" Grund: {g['grund']}" if g.get("grund") else ""), (v.get("details") or "")[:1000],
                v.get("recht") or "eigener_bereich", v.get("betrag"))
            e = freigaben.entscheidung_get(c, eid)
            return _ausfuehrung_setzen(c, fid, "entschieden", f"Im Entscheidungs-Log: {e['status_label']}",
                                       {"entscheidung_id": eid, "entscheidung_status": e["status"]})
        if art.get("ausfuehren") == "mail":
            e = entwurf_get(c, v["entwurf_id"])
            if not e or _inhalt_hash(e) != v.get("hash"):
                raise AgentError("Der Inhalt passt nicht mehr zur Freigabe. Nichts verschickt.")
            if e["status"] == "ausgefuehrt":
                return _ausfuehrung_setzen(c, fid, "ausgefuehrt", "Schon verschickt.")
            lead = leaddb.get_lead(c, e["lead_id"])
            _mail_senden(c, f["entschieden_von"], lead, e)
            c.execute("UPDATE agent_entwuerfe SET status='ausgefuehrt' WHERE id=?", (e["id"],))
            firma.log(c, f["entschieden_von"], "freigabe", "lead", lead["id"],
                      f"Mail verschickt (freigegeben, vorbereitet von {a_name}): {e['betreff'] or 'Antwort'}",
                      {"freigabe": fid, "entwurf": e["id"]}, commit=False)
            return _ausfuehrung_setzen(c, fid, "ausgefuehrt", f"Mail an {e['an']} verschickt.")
        if art.get("ausfuehren") == "status":
            akt = v.get("aktion") or {}
            if _hash(json.dumps(akt, sort_keys=True)) != v.get("hash"):
                raise AgentError("Die Aktion passt nicht mehr zur Freigabe.")
            if akt["typ"] == "lead_stufe":
                crm.set_stage(c, int(akt["lead_id"]), akt["ziel"], author=firma.name_von(c, f["entschieden_von"]),
                              note=f"(vorgeschlagen von {a_name})")
                return _ausfuehrung_setzen(c, fid, "ausgefuehrt", f"Stufe: {crm.STAGE_LABELS[akt['ziel']]}.")
            p = projekte.get(c, int(akt["projekt_id"]))
            idx = projekte.STATUS_KEYS.index(p["status"]) if p and p["status"] in projekte.STATUS_KEYS else -1
            if p and idx + 1 < len(projekte.STATUS_KEYS) and projekte.STATUS_KEYS[idx + 1] == akt["ziel"]:
                projekte.weiter(c, f["entschieden_von"], p["id"], f"vorgeschlagen von {a_name}")
                return _ausfuehrung_setzen(c, fid, "ausgefuehrt", f"Projekt: {projekte.STATUS_LABEL[akt['ziel']]}.")
            return _ausfuehrung_setzen(c, fid, "bitte_ausfuehren", "Nicht der nächste Schritt: bitte im Projekt von "
                                       "Hand weiterschalten.")
        if v.get("entwurf_id"):
            c.execute("UPDATE agent_entwuerfe SET status='freigegeben' WHERE id=?", (v["entwurf_id"],))
            c.commit()
        return _ausfuehrung_setzen(c, fid, "bitte_ausfuehren", "Freigegeben. Der Agent führt es mit genau diesem "
                                   "Inhalt aus und meldet es mit freigabe_erledigt_melden.")
    except Exception as exc:  # Postfach, Rechte, Regeln: als Fehler zurück an Person und Agent
        msg = str(exc) or exc.__class__.__name__
        firma.log(c, f["entschieden_von"], "freigabe", f["objekt_typ"], f["objekt_id"],
                  f"Ausführen fehlgeschlagen: {v.get('titel')} ({msg})")
        return _ausfuehrung_setzen(c, fid, "fehler", msg)


def _mail_senden(c, person, lead, e):
    from . import agent, crm, leaddb
    if not lead:
        raise AgentError("Den Lead gibt es nicht mehr.")
    gesperrt = {x.lower() for x in leaddb.kv_get(c, "blocklist", []) or []}
    if (e["an"] or "").lower() in gesperrt:
        raise AgentError("Diese Adresse steht auf der Sperrliste. Nichts verschickt.")
    ok, warum = crm.may_email(dict(lead, email=e["an"]), agent.S("OUTREACH_EMAIL"))
    if not ok:
        raise AgentError(warum)
    if e["antwort"]:
        agent.send_reply(c, lead, e["text"], person, subject=e["betreff"] or None, sender=person)
    else:
        agent.send_new(c, lead, e["an"], e["betreff"], e["text"], person, sender=person)
    crm.advance(c, lead["id"], "kontaktiert")


def erledigt_melden(c, agent, fid, notiz=""):
    """Agent meldet: Freigegebenes, das das OS nicht selbst ausführen kann, ist erledigt (z. B. Post veröffentlicht)."""
    from . import firma
    f = _freigabe(c, fid)
    if f["angefragt_von"] != wer_von(schluessel(agent)):
        raise AgentError("Das ist nicht deine Freigabe.")
    aus = _json(f["ausfuehrung"], {})
    if f["status"] != "angenommen" or aus.get("status") != "bitte_ausfuehren":
        raise AgentError("Erledigt melden geht nur bei Freigaben mit Status „bitte ausführen“.")
    v = _json(f["vorschlag"], {})
    if v.get("entwurf_id"):
        c.execute("UPDATE agent_entwuerfe SET status='ausgefuehrt' WHERE id=?", (v["entwurf_id"],))
    _ausfuehrung_setzen(c, fid, "erledigt", (notiz or "").strip()[:500])
    firma.log(c, wer_von(schluessel(agent)), "freigabe", f["objekt_typ"], f["objekt_id"],
              f"Erledigt: {v.get('titel')}" + (f" ({notiz.strip()[:120]})" if (notiz or "").strip() else ""))


def status_fuer_agent(c, agent, nur_neue=True, freigabe_id=None):
    """Antworten auf die eigenen Freigaben: entschieden (mit Option, Grund, geänderter Fassung, Ergebnis), offen."""
    from . import firma, leaddb
    wer = wer_von(schluessel(agent))
    if freigabe_id:
        rows = c.execute("SELECT * FROM freigaben WHERE id=? AND angefragt_von=?", (freigabe_id, wer)).fetchall()
    else:
        seit = leaddb.kv_get(c, f"agent_status_seit:{schluessel(agent)}", "") if nur_neue else ""
        rows = c.execute("SELECT * FROM freigaben WHERE angefragt_von=? AND status != 'offen' AND IFNULL(entschieden_am,'') "
                         "> ? AND angefragt_am >= ? ORDER BY entschieden_am", (wer, seit or "",
                                                                                (date.today() - timedelta(days=30)).isoformat())).fetchall()
    out = []
    for f in rows:
        v = _json(f["vorschlag"], {})
        g = _json(f["geaendert"], {})
        aus = _json(f["ausfuehrung"], {})
        d = {"freigabe_id": f["id"], "art": v.get("art_key") or f["art"], "titel": v.get("titel") or f["art"],
             "status": {"angenommen": "freigegeben"}.get(f["status"], f["status"]),
             "entschieden_von": firma.name_von(c, f["entschieden_von"]) if f["entschieden_von"] else None,
             "entschieden_am": f["entschieden_am"], "option": g.get("option"), "grund": f["grund"],
             "ausfuehrung": aus.get("status"), "ausfuehrung_text": aus.get("text"),
             "bezug": {"lead_id": v.get("lead_id"), "projekt_id": v.get("projekt_id")}}
        if v.get("entwurf_id"):
            e = entwurf_get(c, v["entwurf_id"])
            d["entwurf_id"] = v["entwurf_id"]
            if g.get("text_geaendert") and e:
                d["freigegebene_fassung"] = {"an": e["an"], "betreff": e["betreff"], "text": e["text"]}
                d["hinweis"] = "Die Person hat den Text vor der Freigabe geändert. Lern daraus für die nächsten Entwürfe."
        if f["art"] == "angebot":
            d["hinweis"] = "Angebote verschickt das OS nach der Freigabe selbst."
        out.append(d)
    if not freigabe_id and nur_neue and rows:
        leaddb.kv_set(c, f"agent_status_seit:{schluessel(agent)}", max(r["entschieden_am"] or "" for r in rows))
    offen = c.execute("SELECT id, vorschlag, pruefer, angefragt_am, spaeter_bis FROM freigaben WHERE angefragt_von=? "
                      "AND status='offen' ORDER BY id", (wer,)).fetchall()
    return {"entschieden": out,
            "offen": [{"freigabe_id": r["id"], "titel": _json(r["vorschlag"], {}).get("titel"),
                       "bei": firma.name_von(c, r["pruefer"]), "seit": r["angefragt_am"],
                       "spaeter_bis": r["spaeter_bis"]} for r in offen],
            "bitte_ausfuehren": [d["freigabe_id"] for d in out if d["ausfuehrung"] == "bitte_ausfuehren"]}

# ---------------------------------------------------------------- Einrichtung (Onboarding)


def einrichtung(c):
    """Stand der Einrichtung für den Assistenten unter Einstellungen → Agenten → Einrichten."""
    from . import leaddb
    alle = liste(c)
    verbunden = {v["agent"] for v in verbindungen(c) if v["aktiv"]}
    return {"agents": alle, "verbunden": verbunden, "n_verbunden": len([a for a in alle if a["key"] in verbunden]),
            "fertig": bool(leaddb.kv_get(c, "agenten:eingerichtet", False))}
