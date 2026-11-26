"""Seiten des Freigabe-Stapels (Stufe 13, seit 13b nur Freigaben): Start (eine Zahl, „Starten“) und Fokus (eine
Karte pro Bildschirm). Die Aufgaben stehen auf einer eigenen Seite (`aufgaben_views.start`)."""
from datetime import datetime

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from . import firma, stapel
from .leads_views import db, me
from .main import login_required, safe_next

bp = Blueprint("stapel", __name__)


@bp.app_context_processor
def _stapel_globals():
    def _offen():
        try:
            return stapel.anzahl(db(), me()) if g.get("account") and me() else 0
        except Exception:
            return 0
    return {"stapel_offen": _offen}


def _skip():
    return list(session.get("stapel_skip") or [])


def _gruss(name):
    h = datetime.now().hour
    return ("Guten Morgen" if h < 11 else "Guten Tag" if h < 18 else "Guten Abend") + ", " + (name or "").split(" ")[0]


@bp.get("/freigaben")
@login_required
def start():
    c = db()
    key = me()
    if not key:
        return redirect(url_for("team.who_am_i", next=request.path))
    items = stapel.stapel(c, key)
    from . import agenten
    from .aufgaben_views import _startklar_offen
    _, spaeter = agenten.karten(c, key)
    person = firma.person(c, key) or {}
    zahlen = stapel.zusammenfassung(items)
    return render_template("stapel.html", account=g.account, items=items, zahlen=zahlen,
                           n=sum(z["n"] for z in zahlen), gruss=_gruss(person.get("name")),
                           infos=agenten.infos_fuer(c, key), spaeter=spaeter,
                           einrichten=firma.darf(c, key, "personen", firma.VERWALTEN)
                           and not agenten.einrichtung(c)["fertig"], startklar_offen=_startklar_offen(c, key))


@bp.get("/heute")
@bp.get("/leads")
@bp.get("/aufgaben/fokus")
def alt_start():
    """Frühere Startseiten (Web-App auf dem Handy, Lesezeichen, Stapel unter /aufgaben) führen zu den Freigaben."""
    return redirect(url_for("stapel.start"))


@bp.get("/freigaben/fokus")
@login_required
def fokus():
    c = db()
    key = me()
    if request.args.get("neu") == "1":
        session["stapel_skip"] = []
        session["stapel_start"] = len(stapel.stapel(c, key))
        return redirect(url_for("stapel.fokus"))
    items = stapel.stapel(c, key, _skip())
    gesamt = max(session.get("stapel_start") or 0, len(items))
    if not items:
        session.pop("stapel_skip", None)
        return render_template("stapel_fertig.html", account=g.account, gesamt=gesamt,
                               uebersprungen=len(stapel.stapel(c, key)))
    return render_template("stapel_fokus.html", account=g.account, item=items[0], items=items, gesamt=gesamt,
                           erledigt=max(0, gesamt - len(items)), hier=url_for("stapel.fokus"))


@bp.post("/freigaben/fokus/weiter")
@login_required
def weiter():
    """„Jetzt nicht“: Karte bis zum Ende dieser Sitzung überspringen."""
    key = request.form.get("key", "")
    s = _skip()
    if key and key not in s:
        s.append(key)
    session["stapel_skip"] = s[-200:]
    return redirect(safe_next(request.form.get("next", "")) or url_for("stapel.fokus"))


@bp.post("/freigaben/zurueckholen")
@login_required
def zurueckholen():
    """Auf „Später“ gelegte Freigaben sofort wieder zeigen."""
    from . import agenten
    n = agenten.zurueckholen(db(), me())
    flash(f"{n} Freigabe{'n' if n != 1 else ''} wieder da." if n else "Nichts auf später gelegt.")
    return redirect(url_for("stapel.start"))


@bp.post("/freigaben/fokus/spaeter")
@login_required
def spaeter():
    """„Später“: Agent-Freigabe auf morgen früh, alles andere nur überspringen."""
    from . import agenten
    c = db()
    key = request.form.get("key", "")
    typ, _, rest = key.partition(":")
    try:
        if typ == "agent":
            for fid in request.form.getlist("ids") or [rest]:
                agenten.spaeter(c, me(), int(fid))
            flash("Auf morgen früh gelegt.")
        else:
            return weiter()
    except (agenten.AgentError, ValueError) as exc:
        flash(f"Nicht verschoben: {exc}")
    return redirect(url_for("stapel.fokus"))
