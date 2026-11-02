"""Seiten „Kalender“ (Firmenkalender, Termin, neuer Termin mit Vorschlägen), „Mein Kalender“ (Google verbinden,
Abo-Adresse) und Einstellungen → Kalender (Google-Zugang, Regeln, Serien). Dazu die Rücksprung-Adresse für Google
und das ICS-Abo ohne Anmeldung."""
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, session, url_for

from . import firma, google_kalender, kalender, leaddb
from .leads_views import db, me
from .main import SETTINGS, darf, login_required

bp = Blueprint("kalender", __name__)


def _ajax():
    return request.headers.get("X-Requested-With") == "fetch" or request.accept_mimetypes.best == "application/json"


def _fertig(message, url, ok=True):
    if _ajax():
        return (jsonify(ok=True, message=message), 200) if ok else (jsonify(error=message), 400)
    flash(message if ok else f"Nicht gespeichert: {message}")
    return redirect(url)


def _verwalten():
    if not darf("kalender", firma.VERWALTEN):
        abort(403, "Dafür brauchst du „Kalender und Termine: Verwalten“.")


def _rueckweg():
    """Rücksprung-Adresse für Google: genau so muss sie im Google-Projekt stehen."""
    base = (SETTINGS["BASE_URL"] or request.host_url).rstrip("/")
    return base + google_kalender.RUECKSPRUNG


@bp.app_template_filter("datumzeit")
def _datumzeit(value):
    return datetime.fromisoformat(str(value)[:16])


@bp.app_template_filter("datum")
def _datum(value):
    return date.fromisoformat(str(value)[:10])


@bp.app_context_processor
def _globals():
    def heute():
        if not g.get("account") or not me():
            return 0
        jetzt = datetime.now()
        return len(kalender.liste(db(), jetzt.strftime("%Y-%m-%dT%H:%M"), date.today().isoformat() + "T23:59", me()))
    return {"termine_heute": heute}


def _form_kontext(lead=None):
    c = db()
    return {"personen": firma.aktive_personen(c), "regeln": kalender.regeln(c), "lead": lead,
            "verbunden": kalender.verbunden(c, me()), "google_da": bool(kalender.google_cfg())}

# ---------------------------------------------------------------- Firmenkalender


@bp.get("/kalender")
@login_required
def uebersicht():
    c = db()
    ansicht = request.args.get("ansicht", "meine")
    ansicht = ansicht if ansicht in ("meine", "alle") else "meine"
    person = request.args.get("person") or None
    try:
        ab = date.fromisoformat(request.args.get("ab", ""))
    except ValueError:
        ab = date.today()
    bis = ab + timedelta(days=14)
    wer = person or (me() if ansicht == "meine" else None)
    termine = kalender.liste(c, ab.isoformat() + "T00:00", bis.isoformat() + "T00:00", wer)
    tage = {}
    for t in termine:
        tage.setdefault(t["start"][:10], []).append(t)
    lead = leaddb.get_lead(c, request.args.get("lead", type=int)) if request.args.get("lead") else None
    return render_template("kalender.html", account=g.account, tage=sorted(tage.items()), ansicht=ansicht,
                           person=person, ab=ab, zurueck=(ab - timedelta(days=14)).isoformat(),
                           vor=bis.isoformat(), heute=date.today(), freigaben=kalender.freigaben_fuer(c, me()),
                           ergebnisse=kalender.offene_ergebnisse(c, me()), neu=request.args.get("neu") == "1", **_form_kontext(lead))


@bp.get("/kalender/vorschlaege.json")
@login_required
def vorschlaege():
    c = db()
    personen = [p for p in request.args.getlist("personen") if firma.aktive_person(c, p)] or [me()]
    try:
        ab = datetime.fromisoformat(request.args.get("ab", "")) if request.args.get("ab") else None
    except ValueError:
        ab = None
    dauer = request.args.get("dauer", type=int)
    nicht_verbunden = [firma.name_von(c, p) for p in personen if not kalender.verbunden(c, p)]
    return jsonify(vorschlaege=kalender.vorschlaege(c, personen, dauer, ab), ohne_google=nicht_verbunden)


@bp.post("/kalender/neu")
@login_required
def neu():
    c = db()
    f = request.form
    start = f.get("start") or (f.get("datum", "") + "T" + f.get("uhrzeit", ""))
    try:
        tid = kalender.anlegen(c, me(), f.get("titel", ""), start, dauer=f.get("dauer") or None,
                               verantwortlich=f.get("verantwortlich") or me(), personen=f.getlist("personen"),
                               gaeste=f.get("gaeste", ""), lead_id=f.get("lead_id", type=int) or None,
                               ort=f.get("ort", ""), beschreibung=f.get("beschreibung", ""), meet=f.get("meet") == "1",
                               einladung_freigeben=f.get("freigeben") == "1")
    except kalender.KalenderError as exc:
        return _fertig(str(exc), url_for("kalender.uebersicht"), ok=False)
    t = kalender.get(c, tid)
    if t["status"] == "vorschlag":
        text = f"Termin angelegt. Die Einladung wartet auf die Freigabe von {t['verantwortlich_name']}."
    elif t["sync_fehler"]:
        text = f"Termin angelegt. Hinweis: {t['sync_fehler']}"
    else:
        text = "Termin angelegt." + (" Google verschickt die Einladung." if t["gaeste"] else "")
    if _ajax():
        return jsonify(ok=True, message=text, url=url_for("kalender.detail", tid=tid))
    flash(text)
    return redirect(url_for("kalender.detail", tid=tid))


def _termin_or_404(tid):
    t = kalender.get(db(), tid)
    if not t:
        abort(404)
    return t


@bp.get("/kalender/<int:tid>")
@login_required
def detail(tid):
    c = db()
    t = _termin_or_404(tid)
    f = kalender.offene_freigabe(c, tid)
    darf_aendern = me() in (t["verantwortlich"], t["erstellt_von"]) or darf("kalender", firma.VERWALTEN)
    return render_template("termin.html", account=g.account, t=t, freigabe=f, briefing=kalender.briefing(c, t),
                           darf_aendern=darf_aendern, ergebnisse=kalender.ERGEBNISSE,
                           ergebnis_label=kalender.ERGEBNIS_LABEL, verlauf=firma.protokoll(c, objekt=("termin", tid),
                                                                                          limit=20),
                           namen={p["key"]: p["name"] for p in firma.personen(c, mit_archiv=True)}, **_form_kontext())


def _darf_aendern(t):
    if not (me() in (t["verantwortlich"], t["erstellt_von"]) or darf("kalender", firma.VERWALTEN)):
        abort(403, "Ändern darf die verantwortliche Person und wer den Termin angelegt hat.")


@bp.post("/kalender/<int:tid>/aendern")
@login_required
def aendern(tid):
    t = _termin_or_404(tid)
    _darf_aendern(t)
    f = request.form
    start = f.get("start") or ((f.get("datum", "") + "T" + f.get("uhrzeit", "")) if f.get("datum") else None)
    ende = None
    if start and f.get("dauer"):
        ende = (datetime.fromisoformat(start[:16]) + timedelta(minutes=int(f["dauer"]))).strftime("%Y-%m-%dT%H:%M")
    try:
        geaendert = kalender.aendern(db(), me(), tid, titel=f.get("titel"), start=start, ende=ende, ort=f.get("ort"),
                                     beschreibung=f.get("beschreibung"))
    except (kalender.KalenderError, ValueError) as exc:
        return _fertig(str(exc), url_for("kalender.detail", tid=tid), ok=False)
    return _fertig("Gespeichert." if geaendert else "Nichts geändert.", url_for("kalender.detail", tid=tid))


@bp.post("/kalender/<int:tid>/absagen")
@login_required
def absagen(tid):
    t = _termin_or_404(tid)
    _darf_aendern(t)
    try:
        kalender.absagen(db(), me(), tid, request.form.get("grund", ""))
    except kalender.KalenderError as exc:
        return _fertig(str(exc), url_for("kalender.detail", tid=tid), ok=False)
    return _fertig("Abgesagt." + (" Google sagt den Gästen ab." if t["gaeste"] else ""),
                   url_for("kalender.detail", tid=tid))


@bp.post("/kalender/<int:tid>/freigabe")
@login_required
def freigabe(tid):
    _termin_or_404(tid)
    ok = request.form.get("ok") == "1"
    try:
        t = kalender.freigeben(db(), me(), tid, ok, request.form.get("grund", ""))
    except kalender.KalenderError as exc:
        return _fertig(str(exc), url_for("kalender.detail", tid=tid), ok=False)
    if not ok:
        return _fertig("Verworfen. Die anfragende Person bekommt Bescheid.", url_for("kalender.detail", tid=tid))
    return _fertig("Freigegeben." + (f" Hinweis: {t['sync_fehler']}" if t["sync_fehler"] else
                                     " Google verschickt die Einladung."), url_for("kalender.detail", tid=tid))


@bp.post("/kalender/<int:tid>/ergebnis")
@login_required
def ergebnis(tid):
    t = _termin_or_404(tid)
    if me() not in t["personen"] and not darf("kalender", firma.VERWALTEN):
        abort(403, "Das Ergebnis tragen die Teilnehmenden ein.")
    f = request.form
    try:
        kalender.ergebnis(db(), me(), tid, f.get("ergebnis", ""), f.get("notiz", ""), f.get("preisgespraech") == "1",
                          f.get("folge_titel", ""), f.get("folge_faellig", ""))
    except Exception as exc:  # KalenderError oder AufgabeError (Folgeaufgabe)
        if not isinstance(exc, ValueError):
            raise
        return _fertig(str(exc), url_for("kalender.detail", tid=tid), ok=False)
    return _fertig("Ergebnis gespeichert.", url_for("kalender.detail", tid=tid))


@bp.post("/kalender/<int:tid>/briefing")
@login_required
def briefing(tid):
    _termin_or_404(tid)
    notiz = request.form.get("briefing_notiz", "").strip()[:4000]
    db().execute("UPDATE termine SET briefing_notiz=? WHERE id=?", (notiz or None, tid))
    firma.log(db(), me(), "termin", "termin", tid, "Briefing ergänzt.")
    return _fertig("Briefing gespeichert.", url_for("kalender.detail", tid=tid) + "#briefing")

# ---------------------------------------------------------------- Mein Kalender: Google verbinden, Abo


@bp.get("/kalender/mein")
@login_required
def mein():
    c = db()
    neu_link = session.pop("ics_link", None)
    konto = kalender.konto(c, me())
    liste = kalender.kalender_von(c, me()) if konto else None
    if konto and liste is None and konto["status"] == "ok" and kalender.google_cfg():
        liste = kalender.kalender_laden(c, me())  # einmal versuchen; fehlt das Recht, steht unten „neu verbinden“
    return render_template("kalender_mein.html", account=g.account, konto=konto, kalender_liste=liste,
                           ziel=kalender.ziel_kalender(c, me()),
                           google_da=bool(kalender.google_cfg()), ics_link=neu_link,
                           hat_abo=bool(c.execute("SELECT ics_hash FROM team WHERE key=?", (me(),)).fetchone()["ics_hash"]))


@bp.get("/kalender/google/verbinden")
@login_required
def google_verbinden():
    cfg = kalender.google_cfg()
    if not cfg:
        flash("Nicht verbunden: Google ist noch nicht eingerichtet (Einstellungen → Kalender).")
        return redirect(url_for("kalender.mein"))
    state = google_kalender.neuer_state()
    session["google_state"] = {"state": state, "person": me()}
    return redirect(google_kalender.auth_url(cfg[0], _rueckweg(), state))


@bp.get("/kalender/google/zurueck")
@login_required
def google_zurueck():
    """Google schickt die Person nach der Zustimmung hierher zurück (Pfad steht so im Google-Projekt)."""
    erwartet = session.pop("google_state", None) or {}
    if not erwartet or request.args.get("state") != erwartet.get("state") or erwartet.get("person") != me():
        flash("Nicht verbunden: Die Anfrage passt nicht zu deiner Anmeldung. Bitte noch einmal verbinden.")
        return redirect(url_for("kalender.mein"))
    if request.args.get("error"):
        flash("Nicht verbunden: Bei Google abgebrochen." if request.args["error"] == "access_denied" else
              f"Nicht verbunden: Google meldet „{request.args['error']}“.")
        return redirect(url_for("kalender.mein"))
    cfg = kalender.google_cfg()
    try:
        data = google_kalender.code_tauschen(cfg[0], cfg[1], request.args.get("code", ""), _rueckweg())
    except (google_kalender.GoogleError, TypeError) as exc:
        flash(f"Nicht verbunden: {exc}")
        return redirect(url_for("kalender.mein"))
    kalender.konto_speichern(db(), me(), data["refresh_token"])
    kalender.kalender_laden(db(), me())
    kalender.belegt_abgleichen(db(), me())
    flash("Google-Kalender verbunden. Das OS sieht nur frei/belegt und trägt Firmentermine ein.")
    return redirect(url_for("kalender.mein"))


@bp.post("/kalender/mein/kalender")
@login_required
def kalender_wahl():
    """Welche eigenen Google-Kalender zählen als belegt, in welchen trägt das OS Termine ein."""
    c = db()
    try:
        kalender.kalender_wahl_speichern(c, me(), request.form.get("ziel", "primary"), request.form.getlist("lesen"))
    except kalender.KalenderError as exc:
        return _fertig(str(exc), url_for("kalender.mein"), ok=False)
    kalender.belegt_abgleichen(c, me())
    return _fertig("Gespeichert. Deine freien Zeiten sind neu abgeglichen.", url_for("kalender.mein"))


@bp.post("/kalender/mein/kalender/laden")
@login_required
def kalender_neu_laden():
    c = db()
    if kalender.kalender_laden(c, me()) is None:
        return _fertig("Die Liste deiner Kalender ließ sich nicht laden. Bitte Google neu verbinden.",
                       url_for("kalender.mein"), ok=False)
    kalender.belegt_abgleichen(c, me())
    return _fertig("Liste deiner Kalender neu geladen.", url_for("kalender.mein"))


@bp.post("/kalender/google/trennen")
@login_required
def google_trennen():
    kalender.konto_trennen(db(), me())
    flash("Getrennt. Termine im OS bleiben, in Google eingetragene bleiben dort stehen.")
    return redirect(url_for("kalender.mein"))


@bp.post("/kalender/abo/neu")
@login_required
def abo_neu():
    token = kalender.ics_token_erneuern(db(), me())
    base = (SETTINGS["BASE_URL"] or request.host_url).rstrip("/")
    session["ics_link"] = f"{base}/kalender/{token}.ics"
    flash("Neue Abo-Adresse erzeugt. Die alte gilt nicht mehr.")
    return redirect(url_for("kalender.mein"))


@bp.get("/kalender/<token>.ics")
def abo(token):
    """Kalender-Abo ohne Anmeldung: die geheime Adresse ist der Zugang (nur lesen)."""
    c = leaddb.connect(SETTINGS["DATA_DIR"] / "mailer.db")
    try:
        person = kalender.ics_person(c, token)
        if not person:
            abort(404)
        text = kalender.ics(c, person, host=request.host.split(":")[0])
    finally:
        c.close()
    return Response(text, mimetype="text/calendar", headers={"Cache-Control": "no-store",
                                                             "Content-Disposition": 'inline; filename="ylva-labs.ics"'})

# ---------------------------------------------------------------- Einstellungen → Kalender


@bp.get("/einstellungen/kalender")
@login_required
def einstellungen():
    c = db()
    konten = {r["person"]: dict(r) for r in c.execute("SELECT person, status, abgeglichen_am FROM kalender_konten")}
    rueckwege = leaddb.kv_get(c, "google_rueckwege")
    return render_template("kalender_einstellungen.html", account=g.account, google_da=bool(kalender.google_cfg()),
                           rueckweg_fehlt=rueckwege is not None and _rueckweg() not in rueckwege,
                           rueckweg=_rueckweg(), regeln=kalender.regeln(c), regel_labels=kalender.REGELN,
                           wochentage=kalender.WOCHENTAGE, serien=kalender.serien(c), rhythmen=kalender.RHYTHMEN,
                           spielraeume=kalender.SPIELRAEUME, personen=firma.aktive_personen(c),
                           konten=konten, verwalten=darf("kalender", firma.VERWALTEN))


@bp.post("/einstellungen/kalender/google-datei")
@login_required
def google_datei():
    """Google-Zugang aus der heruntergeladenen JSON-Datei eintragen (statt Client-ID und Schlüssel abzutippen)."""
    import os
    from . import config_store
    from .settings_views import is_admin
    _verwalten()
    ziel = url_for("kalender.einstellungen")
    if not is_admin(g.account):
        return _fertig("Den Google-Zugang ändert, wer „Einstellungen: Verwalten“ hat.", ziel, ok=False)
    datei = request.files.get("datei")
    try:
        cid, secret, uris = google_kalender.client_aus_datei(datei.read(70 * 1024) if datei else b"")
    except google_kalender.GoogleError as exc:
        return _fertig(str(exc), ziel, ok=False)
    config_store.save(SETTINGS["DATA_DIR"], {"GOOGLE_CLIENT_ID": cid, "GOOGLE_CLIENT_SECRET": secret})
    os.environ.update(GOOGLE_CLIENT_ID=cid, GOOGLE_CLIENT_SECRET=secret)  # gilt sofort, ohne Neustart
    firma.log(db(), me(), "einstellung", "system", None, "Google-Zugang aus der JSON-Datei eingetragen.",
              {"geaendert": ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]})
    leaddb.kv_set(db(), "google_rueckwege", uris)  # Hinweis auf der Seite, falls unsere Adresse fehlt
    return _fertig("Google-Zugang eingetragen." if _rueckweg() in uris
                   else "Eingetragen, siehe Hinweis.", ziel)


@bp.post("/einstellungen/kalender/regeln")
@login_required
def regeln():
    _verwalten()
    try:
        kalender.regeln_speichern(db(), me(), request.form)
    except kalender.KalenderError as exc:
        return _fertig(str(exc), url_for("kalender.einstellungen"), ok=False)
    return _fertig("Regeln gespeichert.", url_for("kalender.einstellungen"))


@bp.post("/einstellungen/kalender/serie")
@login_required
def serie():
    _verwalten()
    f = request.form
    try:
        kalender.serie_anlegen(db(), me(), f.get("titel", ""), f.get("wochentag", ""), f.get("uhrzeit", ""),
                               f.get("dauer", ""), f.get("verantwortlich") or me(), f.getlist("personen"),
                               f.get("meet") == "1", rhythmus=f.get("rhythmus", "woche"),
                               flexibel=f.get("flexibel") == "1", spielraum=f.get("spielraum", "120"))
    except kalender.KalenderError as exc:
        return _fertig(str(exc), url_for("kalender.einstellungen"), ok=False)
    return _fertig("Serie angelegt. Die nächsten Termine stehen im Kalender.", url_for("kalender.einstellungen"))


@bp.post("/einstellungen/kalender/serie/<int:sid>/beenden")
@login_required
def serie_beenden(sid):
    _verwalten()
    try:
        kalender.serie_beenden(db(), me(), sid)
    except kalender.KalenderError as exc:
        return _fertig(str(exc), url_for("kalender.einstellungen"), ok=False)
    return _fertig("Serie beendet. Künftige Termine sind abgesagt.", url_for("kalender.einstellungen"))
