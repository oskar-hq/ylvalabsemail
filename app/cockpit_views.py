"""Seiten „Cockpit“ (KPIs je Bereich mit Verlauf und Ampel), „Wochenziele“ und „Wochengespräch“ (Agenda), dazu
die Regeln und Freigaben der Lernstufen."""
from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import firma, kpis
from .leads_views import db, me
from .main import SETTINGS, darf, login_required, safe_next

bp = Blueprint("cockpit", __name__)


def _ajax():
    return request.headers.get("X-Requested-With") == "fetch" or request.accept_mimetypes.best == "application/json"


def _fertig(message, url, ok=True):
    if _ajax():
        return (jsonify(ok=True, message=message), 200) if ok else (jsonify(error=message), 400)
    flash(message if ok else f"Nicht gespeichert: {message}")
    return redirect(url)


def _woche():
    w = request.values.get("woche") or kpis.woche_von()
    try:
        kpis.woche_grenzen(w)
    except kpis.KpiError:
        w = kpis.woche_von()
    return w


@bp.app_template_filter("kpi")
def _kpi_format(value, einheit="anzahl"):
    if value is None:
        return "–"
    if einheit == "prozent":
        return f"{value:.0f} %"
    if einheit == "stunden":
        return f"{value:.1f} h".replace(".", ",").replace(",0 h", " h")
    if einheit == "tage":
        return f"{value:.0f} Tage"
    if einheit == "euro":
        return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.1f}".replace(".", ",")
    return f"{int(value)}"


@bp.app_template_filter("kw")
def _kw(value):
    from datetime import date
    return kpis.woche_von(date.fromisoformat(str(value)[:10]))


@bp.app_template_filter("sparkline")
def _sparkline(werte, breite=96, hoehe=28):
    """Verlauf als Punktliste für eine SVG-Linie (Werte ohne Zahl werden ausgelassen)."""
    punkte = [(i, v) for i, v in enumerate(werte) if v is not None]
    if len(punkte) < 2:
        return ""
    lo, hi = min(v for _, v in punkte), max(v for _, v in punkte)
    span = (hi - lo) or 1
    n = max(len(werte) - 1, 1)
    return " ".join(f"{i * breite / n:.1f},{hoehe - 2 - (v - lo) * (hoehe - 4) / span:.1f}" for i, v in punkte)

# ---------------------------------------------------------------- Cockpit


@bp.get("/cockpit")
@login_required
def cockpit():
    c = db()
    woche = _woche()
    sichtbar = kpis.sichtbare_personen(c, me())
    person = request.args.get("person") or None
    if person and not kpis.darf_sehen(c, me(), person):
        abort(403, "Persönliche Zahlen sieht die Person selbst und wer sie führt.")
    bereiche = []
    for b in ("akquise", "markt", "technik", "alle"):
        zeilen = [kpis.zeile(c, k["key"], woche, person) for k in kpis.KATALOG
                  if k["bereich"] == b and (not person or k["je_person"])]
        if zeilen:
            bereiche.append((kpis.BEREICH_LABEL[b], zeilen))
    lernstaende = [dict(kpis.lernstand(c, p["key"]), name=p["name"]) for p in sichtbar if p.get("lernstufe")]
    return render_template("cockpit.html", account=g.account, woche=woche, woche_text=kpis.woche_text(woche),
                           vor=kpis.woche_plus(woche, -1), nach=kpis.woche_plus(woche, 1),
                           aktuell=woche == kpis.woche_von(), bereiche=bereiche, sichtbar=sichtbar, person=person,
                           person_name=firma.name_von(c, person) if person else None, lernstaende=lernstaende,
                           verwalten=darf("cockpit", firma.VERWALTEN), regeln=kpis.lernregeln(c),
                           regel_labels=kpis.REGELN, demo=SETTINGS["DEMO_MODE"])


@bp.post("/cockpit/ziel")
@login_required
def zielwert():
    if not darf("cockpit", firma.VERWALTEN):
        abort(403, "Zielwerte setzt, wer „Zahlen, Ziele und Wochengespräch: Verwalten“ hat.")
    try:
        kpis.ziel_setzen(db(), me(), request.form.get("kpi", ""), request.form.get("wert", "").strip(),
                         request.form.get("person", ""))
    except kpis.KpiError as exc:
        return _fertig(str(exc), url_for("cockpit.cockpit"), ok=False)
    return _fertig("Zielwert gespeichert.", url_for("cockpit.cockpit"))


@bp.post("/cockpit/lernstufen")
@login_required
def lernregeln():
    if not darf("cockpit", firma.VERWALTEN):
        abort(403)
    try:
        kpis.lernregeln_speichern(db(), me(), request.form)
    except kpis.KpiError as exc:
        return _fertig(str(exc), url_for("cockpit.cockpit") + "#lernstufen", ok=False)
    return _fertig("Regeln für die Lernstufen gespeichert.", url_for("cockpit.cockpit") + "#lernstufen")

# ---------------------------------------------------------------- Wochenziele


@bp.post("/ziele/neu")
@login_required
def ziel_neu():
    c = db()
    f = request.form
    person = f.get("person") or me()
    if not kpis.darf_ziele(c, me(), person):
        abort(403, "Ziele setzt die Person selbst und wer sie führt.")
    nxt = safe_next(f.get("next", "")) or url_for("aufgaben.mein_tag")
    try:
        kpis.ziel_anlegen(c, me(), person, f.get("woche") or kpis.woche_von(), f.get("text", ""),
                          f.get("kpi") or None, f.get("zielwert") or None)
    except kpis.KpiError as exc:
        return _fertig(str(exc), nxt, ok=False)
    return _fertig("Ziel gespeichert.", nxt)


@bp.post("/ziele/<int:zid>")
@login_required
def ziel_stand(zid):
    nxt = safe_next(request.form.get("next", "")) or url_for("aufgaben.mein_tag")
    try:
        if request.form.get("loeschen") == "1":
            kpis.ziel_loeschen(db(), me(), zid)
        else:
            kpis.ziel_bewerten(db(), me(), zid, request.form.get("status", ""), request.form.get("notiz", ""))
    except kpis.KpiError as exc:
        return _fertig(str(exc), nxt, ok=False)
    return _fertig("Gespeichert.", nxt)

# ---------------------------------------------------------------- Wochengespräch


@bp.get("/wochengespraech")
@login_required
def wochengespraech():
    c = db()
    woche = _woche()
    a = kpis.agenda(c, me(), woche)
    from . import aufgaben
    return render_template("wochengespraech.html", account=g.account, a=a, vor=kpis.woche_plus(woche, -1),
                           nach=kpis.woche_plus(woche, 1), naechste=kpis.woche_plus(woche, 1),
                           katalog=[k for k in kpis.KATALOG if k["fn"] and k["je_person"]],
                           bereiche=firma.bereiche(c), personen=firma.aktive_personen(c), prios=aufgaben.PRIOS,
                           heute=aufgaben.heute(), demo=SETTINGS["DEMO_MODE"])


@bp.post("/lernstufe/<int:fid>")
@login_required
def aufstieg(fid):
    try:
        kpis.aufstieg_entscheiden(db(), me(), fid, request.form.get("ok") == "1", request.form.get("grund", ""))
    except kpis.KpiError as exc:
        return _fertig(str(exc), url_for("cockpit.wochengespraech"), ok=False)
    return _fertig("Entschieden.", safe_next(request.form.get("next", "")) or url_for("cockpit.wochengespraech"))
