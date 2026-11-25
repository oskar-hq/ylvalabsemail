"""Seiten des Aufgabenstapels (Stufe 13): Start (eine Zahl, „Starten“) und Fokus (eine Karte pro Bildschirm)."""
from datetime import date, datetime, timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from . import aufgaben, firma, mailbox, stapel
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


@bp.get("/aufgaben")
@login_required
def start():
    if request.args:  # alte Links wie /aufgaben?ansicht=alle: das ist die Liste
        return redirect(url_for("aufgaben.liste", **request.args))
    c = db()
    key = me()
    if not key:
        return redirect(url_for("team.who_am_i", next=request.path))
    items = stapel.stapel(c, key)
    from . import agenten, kalender
    from .aufgaben_views import _form_kontext, _startklar_offen
    jetzt = datetime.now()
    termine = kalender.liste(c, jetzt.strftime("%Y-%m-%dT%H:%M"), jetzt.date().isoformat() + "T23:59", key) \
        if firma.darf(c, key, "kalender") else []
    _, spaeter = agenten.karten(c, key)
    person = firma.person(c, key) or {}
    from . import kpis
    woche = kpis.woche_von()
    cockpit = firma.darf(c, key, "cockpit")
    return render_template("stapel.html", account=g.account, items=items, zahlen=stapel.zusammenfassung(items),
                           n=sum(z["n"] for z in stapel.zusammenfassung(items)), gruss=_gruss(person.get("name")),
                           termine=termine, infos=agenten.infos_fuer(c, key), spaeter=spaeter,
                           einrichten=firma.darf(c, key, "personen", firma.VERWALTEN)
                           and not agenten.einrichtung(c)["fertig"], startklar_offen=_startklar_offen(c, key),
                           ziele=kpis.ziele_der_woche(c, woche, key) if cockpit else None, woche=woche,
                           katalog=[k for k in kpis.KATALOG if k["fn"] and k["je_person"]], **_form_kontext())


@bp.get("/aufgaben/fokus")
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
    item = items[0]
    extra = {}
    if item["typ"] == "antwort":
        lead = c.execute("SELECT * FROM leads WHERE id=?", (item["lead_id"],)).fetchone()
        verlauf = mailbox.thread(c, dict(lead)) if lead else []
        extra = {"verlauf": verlauf[-3:], "lead": dict(lead) if lead else None}
    return render_template("stapel_fokus.html", account=g.account, item=item, items=items, gesamt=gesamt,
                           erledigt=max(0, gesamt - len(items)), hier=url_for("stapel.fokus"),
                           morgen=(date.today() + timedelta(days=1)).isoformat(), **extra)


@bp.post("/aufgaben/fokus/weiter")
@login_required
def weiter():
    """„Jetzt nicht“: Karte bis zum Ende dieser Sitzung überspringen."""
    key = request.form.get("key", "")
    s = _skip()
    if key and key not in s:
        s.append(key)
    session["stapel_skip"] = s[-200:]
    return redirect(safe_next(request.form.get("next", "")) or url_for("stapel.fokus"))


@bp.post("/aufgaben/fokus/spaeter")
@login_required
def spaeter():
    """„Später“: Aufgabe auf morgen, Agent-Freigabe auf morgen früh, alles andere nur überspringen."""
    from . import agenten
    c = db()
    key = request.form.get("key", "")
    typ, _, rest = key.partition(":")
    try:
        if typ == "aufgabe":
            aufgaben.aendern(c, me(), int(rest), faellig=(date.today() + timedelta(days=1)).isoformat())
            flash("Auf morgen verschoben.")
        elif typ == "agent":
            for fid in request.form.getlist("ids") or [rest]:
                agenten.spaeter(c, me(), int(fid))
            flash("Auf morgen früh gelegt.")
        else:
            return weiter()
    except (aufgaben.AufgabeError, agenten.AgentError, ValueError) as exc:
        flash(f"Nicht verschoben: {exc}")
    return redirect(url_for("stapel.fokus"))
