"""Seiten „Mein Tag“ und „Aufgaben“ (mit Anfragen an andere Bereiche) und Kommentare mit @-Erwähnung."""
import json
import re
from datetime import datetime, timedelta

from markupsafe import Markup, escape
from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import aufgaben, branchentest, crm, firma, freigaben, kalender, kpis, leaddb, mailbox
from .leads_views import db, me
from .main import SETTINGS, darf, login_required, safe_next

bp = Blueprint("aufgaben", __name__)


@bp.app_context_processor
def _aufgaben_globals():
    return {"aufgaben_offen": lambda: aufgaben.offen_zaehlen(db(), me()) if g.get("account") else 0,
            "tag": aufgaben.tag}


@bp.app_template_filter("erwaehnung")
def _erwaehnung(text):
    """Kommentartext sicher ausgeben, @Namen hervorgehoben."""
    return Markup(re.sub(r"(?<![\w.])(@[\wäöüÄÖÜß-]{2,30})", r"<mark>\1</mark>", str(escape(text or ""))))


@bp.app_template_filter("mention_json")
def _mention_json(personen):
    return json.dumps([{"key": p["key"], "name": p["name"], "first": p["name"].split(" ")[0]} for p in personen],
                      ensure_ascii=False)


def _ajax():
    return request.headers.get("X-Requested-With") == "fetch" or request.accept_mimetypes.best == "application/json"


def _fertig(message, url, ok=True, status=200):
    """Antwort nach einer Aktion: für fetch als JSON (Toast im Browser), sonst Weiterleitung mit Meldung."""
    if _ajax():
        return (jsonify(ok=True, message=message) if ok else jsonify(error=message)), (status if not ok else 200)
    flash(message)
    return redirect(url)


def _zurueck(default):
    return safe_next(request.form.get("next", "") or request.args.get("next", "")) or default


def _form_kontext():
    """Was die Formulare zum Anlegen brauchen: Bereiche, aktive Personen, Kurzwahl für den Termin."""
    c = db()
    return {"bereiche": firma.bereiche(c), "personen": firma.aktive_personen(c), "prios": aufgaben.PRIOS,
            "meine_bereiche": firma.bereiche_von(c, me()), "heute": aufgaben.heute()}

# ---------------------------------------------------------------- Mein Tag


@bp.get("/mein-tag")
@login_required
def mein_tag():
    c = db()
    key = me()
    tag = aufgaben.mein_tag(c, key)
    hour = datetime.now().hour
    gruss = "Guten Morgen" if hour < 11 else ("Guten Tag" if hour < 18 else "Guten Abend")
    akquise = None
    if darf("leads") or darf("crm"):
        akquise = {"wiedervorlagen": crm.due_count(c, key) if darf("crm") else 0,
                   "antworten": mailbox.unread_count(c, key) if darf("crm") else 0,
                   "pruefen": leaddb.tab_counts(c).get("entwurf", 0) if darf("leads") else 0}
    termine = ergebnisse = []
    if darf("kalender"):
        jetzt = datetime.now()
        termine = kalender.liste(c, (jetzt - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
                                 jetzt.date().isoformat() + "T23:59", key)
        ergebnisse = kalender.offene_ergebnisse(c, key)
    woche = kpis.woche_von()
    ziele = kpis.ziele_der_woche(c, woche, key) if darf("cockpit") else []
    offene_freigaben = freigaben.offen(c, key)
    meine_leads = branchentest.recherche_kpis(c, key) if darf("leads") else None
    person = firma.person(c, key)
    wochentag = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    monat = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
             "November", "Dezember"]
    jetzt = datetime.now()
    html = render_template("mein_tag.html", account=g.account, t=tag, gruss=gruss, akquise=akquise,
                           freigaben=offene_freigaben, meine_leads=meine_leads, termine=termine,
                           ergebnisse=ergebnisse, kalender_verbunden=kalender.verbunden(c, key), ziele=ziele, woche=woche,
                           katalog=[k for k in kpis.KATALOG if k["fn"] and k["je_person"]],
                           vorname=(person["name"] if person else "Team").split(" ")[0],
                           datum=f"{wochentag[jetzt.weekday()]}, {jetzt.day}. {monat[jetzt.month - 1]}",
                           **_form_kontext(), demo=SETTINGS["DEMO_MODE"])
    aufgaben.erwaehnungen_gesehen(c, key)  # einmal gezeigt, danach nicht mehr „neu“
    return html

# ---------------------------------------------------------------- Aufgaben


ANSICHTEN = [("meine", "Meine"), ("bereich", "Bereich"), ("vergeben", "Vergeben"), ("alle", "Alle")]


@bp.get("/aufgaben")
@login_required
def liste():
    c = db()
    ansicht = request.args.get("ansicht", "meine")
    ansicht = ansicht if ansicht in dict(ANSICHTEN) else "meine"
    bereich = request.args.get("bereich") or None
    if bereich and not firma.bereich(c, bereich):
        bereich = None
    erledigt = request.args.get("erledigt") == "1"
    rows = aufgaben.liste(c, me(), ansicht, bereich=bereich, erledigt=erledigt)
    return render_template("aufgaben.html", account=g.account, rows=rows, ansicht=ansicht, ansichten=ANSICHTEN,
                           bereich=bereich, erledigt=erledigt, **_form_kontext(), demo=SETTINGS["DEMO_MODE"])


@bp.post("/aufgaben/neu")
@login_required
def neu():
    c = db()
    f = request.form
    termin = aufgaben.schnell_termin(f.get("schnell", ""))
    try:
        aid = aufgaben.anlegen(c, me(), f.get("titel", ""), f.get("bereich", ""),
                               termin.isoformat() if termin else f.get("faellig", ""),
                               person=f.get("person") or None, prio=f.get("prio", 2), notiz=f.get("notiz", ""),
                               anfrage=f.get("art") == "anfrage")
    except (aufgaben.AufgabeError, firma.FirmaError) as exc:
        return _fertig(str(exc), _zurueck(url_for("aufgaben.liste")), ok=False, status=400)
    a = aufgaben.get(c, aid)
    text = (f"Anfrage an {a['bereich_name']} geschickt." if a["art"] == "anfrage" else
            "Aufgabe angelegt." if a["person"] == me() else f"Aufgabe an {a['person_name']} gegeben.")
    return _fertig(text, _zurueck(url_for("aufgaben.liste")))


def _aufgabe_or_404(aid):
    a = aufgaben.get(db(), aid)
    if not a:
        abort(404)
    return a


@bp.get("/aufgaben/<int:aid>")
@login_required
def detail(aid):
    c = db()
    a = _aufgabe_or_404(aid)
    return render_template("aufgabe.html", account=g.account, a=a, komm=aufgaben.kommentare(c, "aufgabe", aid),
                           verlauf=firma.protokoll(c, objekt=("aufgabe", aid), limit=30),
                           darf_aendern=aufgaben.darf_aendern(c, me(), a),
                           darf_entscheiden=aufgaben.darf_entscheiden(c, me(), a),
                           namen={p["key"]: p["name"] for p in firma.personen(c, mit_archiv=True)},
                           **_form_kontext(), demo=SETTINGS["DEMO_MODE"])


@bp.post("/aufgaben/<int:aid>/aendern")
@login_required
def aendern(aid):
    c = db()
    a = _aufgabe_or_404(aid)
    if not aufgaben.darf_aendern(c, me(), a):
        abort(403, "Ändern darf die Person selbst, wer die Aufgabe angelegt hat, und die verantwortliche Person "
                   "des Bereichs.")
    f = request.form
    felder = {k: f.get(k) for k in ("titel", "notiz", "faellig", "prio", "bereich", "person") if k in f}
    termin = aufgaben.schnell_termin(f.get("schnell", ""))
    if termin:
        felder["faellig"] = termin.isoformat()
    try:
        changed = aufgaben.aendern(c, me(), aid, **felder)
    except (aufgaben.AufgabeError, firma.FirmaError) as exc:
        return _fertig(str(exc), url_for("aufgaben.detail", aid=aid), ok=False, status=400)
    return _fertig("Gespeichert." if changed else "Nichts geändert.", url_for("aufgaben.detail", aid=aid))


@bp.post("/aufgaben/<int:aid>/erledigt")
@login_required
def erledigt(aid):
    c = db()
    a = _aufgabe_or_404(aid)
    zurueck = request.form.get("zurueck") == "1"
    try:
        aufgaben.erledigen(c, me(), aid, erledigt=not zurueck)
    except aufgaben.AufgabeError as exc:
        return _fertig(str(exc), _zurueck(url_for("aufgaben.detail", aid=aid)), ok=False, status=400)
    if a["objekt_typ"] == "lead" and not zurueck:
        leaddb.add_event(c, a["objekt_id"], "task", f"Erledigt: {a['titel']}", author=g.account.get("name") or me())
    return _fertig("Wieder offen." if zurueck else "Erledigt.", _zurueck(url_for("aufgaben.liste")))


@bp.post("/aufgaben/<int:aid>/annehmen")
@login_required
def annehmen(aid):
    c = db()
    a = _aufgabe_or_404(aid)
    if not aufgaben.darf_entscheiden(c, me(), a):
        abort(403, "Über Anfragen entscheidet die verantwortliche Person des Bereichs.")
    termin = aufgaben.schnell_termin(request.form.get("schnell", ""))
    try:
        aufgaben.annehmen(c, me(), aid, termin.isoformat() if termin else request.form.get("faellig", ""),
                          person=request.form.get("person") or None)
    except aufgaben.AufgabeError as exc:
        return _fertig(str(exc), url_for("aufgaben.detail", aid=aid), ok=False, status=400)
    return _fertig("Angenommen.", _zurueck(url_for("aufgaben.detail", aid=aid)))


@bp.post("/aufgaben/<int:aid>/ablehnen")
@login_required
def ablehnen(aid):
    c = db()
    a = _aufgabe_or_404(aid)
    if not aufgaben.darf_entscheiden(c, me(), a):
        abort(403, "Über Anfragen entscheidet die verantwortliche Person des Bereichs.")
    try:
        aufgaben.ablehnen(c, me(), aid, request.form.get("grund", ""))
    except aufgaben.AufgabeError as exc:
        return _fertig(str(exc), url_for("aufgaben.detail", aid=aid), ok=False, status=400)
    return _fertig("Abgelehnt. Die anfragende Person bekommt Bescheid.", _zurueck(url_for("aufgaben.detail", aid=aid)))

# ---------------------------------------------------------------- Kommentare


@bp.post("/kommentar/<typ>/<int:oid>")
@login_required
def kommentar(typ, oid):
    if typ not in ("aufgabe", "lead"):
        abort(404)
    if typ == "lead" and not darf("crm") and not darf("leads"):
        abort(403)
    c = db()
    ziel = url_for("aufgaben.detail", aid=oid) if typ == "aufgabe" else url_for("leads.detail", lead_id=oid)
    try:
        aufgaben.kommentieren(c, me(), typ, oid, request.form.get("text", ""))
    except aufgaben.AufgabeError as exc:
        return _fertig(str(exc), ziel + "#kommentare", ok=False, status=400)
    return _fertig("Kommentar gespeichert.", ziel + "#kommentare")
