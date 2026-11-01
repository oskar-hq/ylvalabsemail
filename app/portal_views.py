"""Kunden-Bereich: die Seiten, die Kunden sehen (ohne Team-Anmeldung, mit eigenem Cookie nur für /portal)."""
from functools import wraps

from flask import Blueprint, abort, flash, g, redirect, render_template, request, send_from_directory, url_for

from . import portal
from .leads_views import db
from .main import SETTINGS, app

bp = Blueprint("portal", __name__)
COOKIE = "ylva_kunde"


def _kunde():
    return portal.sitzung(db(), request.cookies.get(COOKIE, ""))


def kunde_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        g.kunde = _kunde()
        if not g.kunde:
            if request.method == "POST":
                abort(401)
            return redirect(url_for("portal.start"))
        return view(*args, **kwargs)
    return wrapper


@bp.after_request
def _kein_cache(resp):
    resp.headers["Cache-Control"] = "no-store"
    resp.headers.setdefault("Referrer-Policy", "no-referrer")  # der Einmal-Link soll nirgends mitgeschickt werden
    return resp


@bp.get("/portal")
def start():
    z = _kunde()
    if not z:
        return render_template("portal_login.html", account=None)
    a = portal.ansicht(db(), z)
    if not a:
        abort(404)
    return render_template("portal.html", account=None, a=a)


@bp.post("/portal/link")
def link_anfordern():
    flash(portal.link_anfordern(db(), request.form.get("email", ""), basis=request.host_url))
    return redirect(url_for("portal.start"))


@bp.get("/portal/login/<token>")
def login(token):
    z = portal.link_pruefen(db(), token)
    return render_template("portal_login.html", account=None, token=token if z else None, abgelaufen=not z,
                           zugang=z)


@bp.post("/portal/login/<token>")
def login_einloesen(token):
    try:
        sitzung, tage = portal.anmelden(db(), token)
    except portal.PortalError as exc:
        flash(str(exc))
        return redirect(url_for("portal.start"))
    resp = redirect(url_for("portal.start"))
    resp.set_cookie(COOKIE, sitzung, max_age=tage * 86400, httponly=True, samesite="Lax", path="/portal",
                    secure=app.config["SESSION_COOKIE_SECURE"])
    return resp


@bp.post("/portal/abmelden")
def abmelden():
    portal.abmelden(db(), request.cookies.get(COOKIE, ""))
    resp = redirect(url_for("portal.start"))
    resp.delete_cookie(COOKIE, path="/portal", samesite="Lax", secure=app.config["SESSION_COOKIE_SECURE"])
    flash("Abgemeldet.")
    return resp


@bp.post("/portal/bestaetigen")
@kunde_required
def bestaetigen():
    try:
        portal.bestaetigen(db(), g.kunde)
        flash("Danke, die Anforderung ist bestätigt. Wir melden uns, wenn es losgeht.")
    except portal.PortalError as exc:
        flash(str(exc))
    return redirect(url_for("portal.start") + "#anforderung")


@bp.post("/portal/wunsch")
@kunde_required
def wunsch():
    try:
        portal.wunsch(db(), g.kunde, request.form.get("text", ""))
        flash("Danke, Ihr Wunsch ist angekommen. Sie sehen hier, wie es weitergeht.")
    except portal.PortalError as exc:
        flash(str(exc))
    return redirect(url_for("portal.start") + "#wuensche")


@bp.get("/portal/dokument/<int:did>")
@kunde_required
def dokument(did):
    d = portal.dokument(db(), did)
    if not d or d["projekt_id"] != g.kunde["projekt_id"] or not d["sichtbar"]:
        abort(404)
    return send_from_directory(portal.dok_ordner(SETTINGS["DATA_DIR"]), d["datei"], as_attachment=True,
                               download_name=d["dateiname"] or d["datei"])
