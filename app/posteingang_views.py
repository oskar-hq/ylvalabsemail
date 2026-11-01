"""Einstellungen → Postfächer: weitere Postfächer (IMAP/SMTP) anlegen, zuordnen, testen und abrufen."""
from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import firma, posteingang
from .leads_views import db, me
from .main import darf, login_required

bp = Blueprint("posteingang", __name__)


def _fertig(message, ok=True, anker=""):
    url = url_for("posteingang.einstellungen") + anker
    if request.headers.get("X-Requested-With") == "fetch" or request.accept_mimetypes.best == "application/json":
        return (jsonify(ok=True, message=message), 200) if ok else (jsonify(error=message), 400)
    flash(message if ok else f"Nicht gespeichert: {message}")
    return redirect(url)


def _verwalten():
    if g.account.get("demo") or not darf("einstellungen", firma.VERWALTEN):
        abort(403, "Dafür brauchst du „Einstellungen: Verwalten“.")


@bp.get("/einstellungen/postfaecher")
@login_required
def einstellungen():
    c = db()
    return render_template("postfaecher.html", account=g.account, liste=posteingang.postfaecher(c),
                           personen=firma.aktive_personen(c), verwalten=darf("einstellungen", firma.VERWALTEN))


@bp.post("/einstellungen/postfaecher")
@login_required
def speichern():
    _verwalten()
    try:
        pid = posteingang.postfach_speichern(db(), me(), int(request.form.get("id") or 0) or None, request.form)
    except posteingang.PosteingangError as exc:
        return _fertig(str(exc), ok=False)
    return _fertig("Postfach gespeichert. Mit „Jetzt abrufen“ prüfen, ob der Zugang stimmt.", anker=f"#p{pid}")


@bp.post("/einstellungen/postfaecher/<int:pid>/loeschen")
@login_required
def loeschen(pid):
    _verwalten()
    try:
        posteingang.postfach_loeschen(db(), me(), pid)
    except posteingang.PosteingangError as exc:
        return _fertig(str(exc), ok=False)
    return _fertig("Postfach entfernt.")


@bp.post("/einstellungen/postfaecher/<int:pid>/abrufen")
@login_required
def abrufen(pid):
    _verwalten()
    try:
        n = posteingang.abrufen(db(), pid)
    except posteingang.PosteingangError as exc:
        return _fertig(f"Abrufen fehlgeschlagen: {exc}", ok=False, anker=f"#p{pid}")
    return _fertig(f"Zugang klappt. {n} neue Mail{'s' if n != 1 else ''}.", anker=f"#p{pid}")
