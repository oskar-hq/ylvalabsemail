"""Einstellungen → Arbeitsbereiche: Kunden anlegen, für die wir Outreach machen (Stufe 11)."""
from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, session, url_for

from . import arbeitsbereiche, firma, posteingang
from .leads_views import db, me
from .main import darf, login_required

bp = Blueprint("arbeitsbereiche", __name__)


def _fertig(message, ok=True, ziel=None):
    if request.headers.get("X-Requested-With") == "fetch" or request.accept_mimetypes.best == "application/json":
        return (jsonify(ok=True, message=message, redirect=ziel), 200) if ok else (jsonify(error=message), 400)
    flash(message if ok else f"Nicht gespeichert: {message}")
    return redirect(ziel or url_for("arbeitsbereiche.einstellungen"))


def _verwalten():
    if not darf("einstellungen", firma.VERWALTEN):
        abort(403, "Dafür brauchst du „Einstellungen: Verwalten“.")


@bp.get("/einstellungen/arbeitsbereiche")
@login_required
def einstellungen():
    c = db()
    liste = arbeitsbereiche.alle(c)
    zahlen = {r["arbeitsbereich"]: dict(r) for r in c.execute(
        "SELECT arbeitsbereich, COUNT(*) AS leads, SUM(sent_at IS NOT NULL) AS angeschrieben FROM leads "
        "GROUP BY arbeitsbereich")}
    return render_template("arbeitsbereiche.html", account=g.account, liste=liste, zahlen=zahlen,
                           verwalten=darf("einstellungen", firma.VERWALTEN))


@bp.get("/einstellungen/arbeitsbereiche/<key>")
@login_required
def bearbeiten(key):
    c = db()
    a = arbeitsbereiche.get(c, key)
    if not a:
        abort(404)
    projekte = [dict(r) for r in c.execute(
        "SELECT p.id, p.titel, IFNULL(l.name, '') AS kunde FROM projekte p LEFT JOIN leads l ON l.id = p.lead_id "
        "ORDER BY p.id DESC LIMIT 200")]
    belegt = {r[0]: r[1] for r in c.execute("SELECT postfach_id, name FROM arbeitsbereiche "
                                            "WHERE postfach_id IS NOT NULL AND key != ?", (key,))}
    return render_template("arbeitsbereich.html", account=g.account, a=a, felder=arbeitsbereiche.FELDER,
                           projekte=projekte, postfaecher=posteingang.postfaecher(c), belegt=belegt,
                           personen=firma.aktive_personen(c), verwalten=darf("einstellungen", firma.VERWALTEN))


@bp.post("/einstellungen/arbeitsbereiche")
@login_required
def anlegen():
    _verwalten()
    try:
        key = arbeitsbereiche.anlegen(db(), me(), request.form.get("name"), request.form.get("projekt_id") or None)
    except arbeitsbereiche.ArbeitsbereichError as exc:
        return _fertig(str(exc), ok=False)
    return _fertig("Arbeitsbereich angelegt. Jetzt Akquise-Postfach, Absender und Suchgebiet eintragen.",
                   ziel=url_for("arbeitsbereiche.bearbeiten", key=key))


@bp.post("/einstellungen/arbeitsbereiche/<key>")
@login_required
def speichern(key):
    _verwalten()
    try:
        arbeitsbereiche.speichern(db(), me(), key, request.form)
    except arbeitsbereiche.ArbeitsbereichError as exc:
        return _fertig(str(exc), ok=False)
    if session.get("ab") == key and not arbeitsbereiche.darf(db(), me(), key):
        session.pop("ab", None)
    return _fertig("Arbeitsbereich gespeichert.", ziel=url_for("arbeitsbereiche.bearbeiten", key=key))
