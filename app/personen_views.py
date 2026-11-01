"""Einstellungen des Firmen-OS: Personen (anlegen, einladen, sperren, archivieren), Rollen und Rechte, Bereiche,
Firmen-Protokoll. Dazu die öffentliche Seite des Einladungslinks, auf der eine Person ihr Passwort setzt."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

from . import aufgaben, firma, leaddb
from .leads_views import db, me
from .main import SETTINGS, _login_page, _start_session, darf, login_required, remember_member

bp = Blueprint("personen", __name__)


def _verwalten():
    if g.account.get("demo"):
        abort(403, "Im Demo-Modus lassen sich Personen und Rechte nicht ändern.")
    if not darf("personen", firma.VERWALTEN):
        abort(403, "Dafür brauchst du das Recht „Personen, Rollen und Bereiche: Verwalten“.")


def _fehler(exc, url):
    flash(f"Nicht gespeichert: {exc}")
    return redirect(url)


def _basis():
    return {"account": g.account, "demo": SETTINGS["DEMO_MODE"], "verwalten": darf("personen", firma.VERWALTEN),
            "module": firma.MODULE, "stufen": firma.STUFEN, "lernstufen": firma.LERNSTUFEN}

# ---------------------------------------------------------------- Personen


@bp.get("/einstellungen/personen")
@login_required
def liste():
    c = db()
    archiv = request.args.get("archiv") == "1"
    leute = [p for p in firma.personen(c, mit_archiv=True) if (p["status"] == "archiviert") == archiv]
    bnamen = {b["key"]: b["name"] for b in firma.bereiche(c, mit_archiv=True)}
    n_archiv = sum(1 for p in firma.personen(c, mit_archiv=True) if p["status"] == "archiviert")
    return render_template("personen.html", leute=leute, archiv=archiv, n_archiv=n_archiv, bnamen=bnamen,
                           rollen=firma.rollen(c), bereiche=firma.bereiche(c), einladung_tage=firma.einladung_tage(c),
                           **_basis())


@bp.post("/einstellungen/personen/regeln")
@login_required
def personen_regeln():
    _verwalten()
    try:
        firma.einladung_tage_setzen(db(), me(), request.form.get("tage", ""))
    except firma.FirmaError as exc:
        return _fehler(exc, url_for("personen.liste"))
    flash("Gespeichert.")
    return redirect(url_for("personen.liste"))


@bp.post("/einstellungen/personen/neu")
@login_required
def neu():
    _verwalten()
    c = db()
    f = request.form
    try:
        key = firma.person_anlegen(c, me(), f.get("name", ""), f.get("rolle", ""), f.get("email", ""),
                                   f.getlist("bereiche"), f.get("lernstufe") or None)
        token = firma.einladen(c, me(), key)
    except firma.FirmaError as exc:
        return _fehler(exc, url_for("personen.liste"))
    flash("Angelegt. Schick den Einladungslink an die Person, damit sie ihr Passwort setzt.")
    session["einladung"] = {"key": key, "token": token}  # nur einmal gezeigt, nicht in der Adresse
    return redirect(url_for("personen.detail", key=key))


@bp.get("/einstellungen/personen/<key>")
@login_required
def detail(key):
    c = db()
    p = firma.person(c, key)
    if not p:
        abort(404)
    neu = session.pop("einladung", None) or {}
    link = None
    if neu.get("key") == key and darf("personen", firma.VERWALTEN) and firma.einladung(c, neu.get("token")):
        link = (SETTINGS["BASE_URL"] or request.host_url.rstrip("/")) + url_for("personen.einladung",
                                                                                 token=neu["token"])
    rolle = firma.rolle(c, p["rolle"])
    return render_template("person.html", p=p, rolle=rolle, link=link, rollen=firma.rollen(c),
                           bereiche=firma.bereiche(c), verantwortet=firma.verantwortet(c, key),
                           rechte=firma.rechte(c, key), ist_ich=key == me(),
                           verlauf=firma.protokoll(c, objekt=("person", key), limit=20),
                           einladung_tage=firma.einladung_tage(c), **_basis())


@bp.post("/einstellungen/personen/<key>")
@login_required
def speichern(key):
    _verwalten()
    c = db()
    f = request.form
    try:
        eigene = firma.parse_rechte(f, prefix="r_", erlaube_leer=True)
        firma.person_aendern(c, me(), key, f.get("name", ""), f.get("rolle", ""), f.get("email", ""),
                             f.getlist("bereiche"), f.get("lernstufe") or None, eigene)
    except firma.FirmaError as exc:
        return _fehler(exc, url_for("personen.detail", key=key))
    if key == me():
        from .main import update_session
        update_session(name=firma.person(c, key)["name"])
    flash("Gespeichert.")
    return redirect(url_for("personen.detail", key=key))


@bp.post("/einstellungen/personen/<key>/status")
@login_required
def status(key):
    _verwalten()
    neu = request.form.get("status", "")
    try:
        firma.status_setzen(db(), me(), key, neu)
    except firma.FirmaError as exc:
        return _fehler(exc, url_for("personen.detail", key=key))
    flash({"aktiv": "Wieder aktiv.", "gesperrt": "Gesperrt. Die Person kann sich nicht mehr anmelden.",
           "archiviert": "Archiviert. Ihre Daten bleiben erhalten."}[neu])
    return redirect(url_for("personen.detail", key=key))


@bp.post("/einstellungen/personen/<key>/einladen")
@login_required
def einladen(key):
    _verwalten()
    try:
        token = firma.einladen(db(), me(), key)
    except firma.FirmaError as exc:
        return _fehler(exc, url_for("personen.detail", key=key))
    flash("Neuer Einladungslink erzeugt. Ein älterer Link gilt nicht mehr.")
    session["einladung"] = {"key": key, "token": token}
    return redirect(url_for("personen.detail", key=key))

# ---------------------------------------------------------------- Einladung (ohne Anmeldung)


@bp.route("/einladung/<token>", methods=["GET", "POST"])
def einladung(token):
    c = leaddb.connect(SETTINGS["DATA_DIR"] / "mailer.db")
    try:
        p = firma.einladung(c, token)
        if not p:
            flash("Dieser Einladungslink ist abgelaufen oder wurde schon benutzt. Bitte um einen neuen.")
            return _login_page(410)
        if request.method == "POST":
            new, again = request.form.get("new", ""), request.form.get("again", "")
            if new != again:
                flash("Die beiden Passwörter sind nicht gleich.")
            else:
                try:
                    firma.einladung_einloesen(c, token, new)
                except (firma.FirmaError, ValueError) as exc:
                    flash(str(exc))
                else:
                    _start_session({"user": p["key"], "team": p["key"], "name": p["name"], "password": "",
                                    "address": ""})
                    flash(f"Willkommen, {p['name'].split(' ')[0]}. Ab jetzt meldest du dich mit deinem Namen an.")
                    return remember_member(redirect(url_for("aufgaben.mein_tag")), p["key"])
        from . import team
        return render_template("einladung.html", p=p, account=None, min_pw=team.MIN_PASSWORD)
    finally:
        c.close()

# ---------------------------------------------------------------- Rollen und Rechte


@bp.get("/einstellungen/rollen")
@login_required
def rollen():
    return render_template("rollen.html", rollen=firma.rollen(db()), **_basis())


@bp.get("/einstellungen/rollen/<key>")
@login_required
def rolle(key):
    r = firma.rolle(db(), key) if key != "neu" else {"key": "", "name": "", "rechte": {k: 0 for k in firma.MODUL_KEYS}}
    if not r:
        abort(404)
    leute = [p for p in firma.personen(db()) if p["rolle"] == key]
    return render_template("rolle.html", r=r, leute=leute, **_basis())


@bp.post("/einstellungen/rollen/<key>")
@login_required
def rolle_speichern(key):
    _verwalten()
    c = db()
    try:
        if request.form.get("loeschen") == "1":
            firma.rolle_loeschen(c, me(), key)
            flash("Rolle gelöscht.")
            return redirect(url_for("personen.rollen"))
        neu_key = firma.rolle_speichern(c, me(), "" if key == "neu" else key, request.form.get("name", ""),
                                        firma.parse_rechte(request.form))
    except firma.FirmaError as exc:
        return _fehler(exc, url_for("personen.rolle", key=key))
    flash("Gespeichert. Die Rechte gelten sofort für alle mit dieser Rolle.")
    return redirect(url_for("personen.rolle", key=neu_key))

# ---------------------------------------------------------------- Bereiche


@bp.get("/einstellungen/bereiche")
@login_required
def bereiche():
    c = db()
    return render_template("bereiche.html", bereiche=firma.bereiche(c, mit_archiv=True),
                           personen=firma.aktive_personen(c), wiedervorlage=aufgaben.wiedervorlage_bereich(c),
                           offen={r[0]: r[1] for r in c.execute(
                               "SELECT bereich, COUNT(*) FROM aufgaben WHERE status IN ('offen','angefragt') "
                               "GROUP BY bereich")}, **_basis())


@bp.post("/einstellungen/bereiche/speichern")
@login_required
def bereich_speichern():
    _verwalten()
    f = request.form
    try:
        firma.bereich_speichern(db(), me(), f.get("key") or None, f.get("name", ""), f.get("verantwortlich", ""),
                                f.get("eltern") or None, f.get("beschreibung", ""))
    except firma.FirmaError as exc:
        return _fehler(exc, url_for("personen.bereiche"))
    flash("Bereich gespeichert.")
    return redirect(url_for("personen.bereiche"))


@bp.post("/einstellungen/bereiche/<key>/archiv")
@login_required
def bereich_archiv(key):
    _verwalten()
    try:
        firma.bereich_archivieren(db(), me(), key, request.form.get("archiv") == "1")
    except firma.FirmaError as exc:
        return _fehler(exc, url_for("personen.bereiche"))
    flash("Gespeichert.")
    return redirect(url_for("personen.bereiche"))


@bp.post("/einstellungen/bereiche/regeln")
@login_required
def bereich_regeln():
    _verwalten()
    c = db()
    key = request.form.get("wiedervorlage", "")
    b = firma.bereich(c, key)
    if not b or b["archiviert"]:
        return _fehler("Bitte einen Bereich wählen.", url_for("personen.bereiche"))
    leaddb.kv_set(c, "regel:wiedervorlage_bereich", key)
    firma.log(c, me(), "einstellung", "regel", "wiedervorlage_bereich", f"Wiedervorlagen gehen an „{b['name']}“.")
    flash("Gespeichert.")
    return redirect(url_for("personen.bereiche"))

# ---------------------------------------------------------------- Firmen-Protokoll


@bp.get("/einstellungen/protokoll")
@login_required
def protokoll():
    c = db()
    art = request.args.get("art") or None
    wer = request.args.get("wer") or None
    vor = request.args.get("vor", type=int)
    rows = firma.protokoll(c, art=art, wer=wer, limit=100, vor=vor)
    namen = {p["key"]: p["name"] for p in firma.personen(c, mit_archiv=True)}
    arten = [r[0] for r in c.execute("SELECT DISTINCT art FROM ereignisse ORDER BY art")]
    return render_template("protokoll.html", rows=rows, namen=namen, art=art, wer=wer, arten=arten,
                           art_labels=firma.ART_LABELS, personen=firma.personen(c, mit_archiv=True),
                           weiter=rows[-1]["id"] if len(rows) == 100 else None, **_basis())
