"""Seiten des Branchentests: Übersicht mit Auswertung je Branche, eine Branche im Detail, Lead-Prüfung (Freigabe),
Regeln. Dazu die Knöpfe in der Lead-Akte: Testbranche und warm/kalt, Kontakt protokollieren, Preisgespräch."""
from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import branchentest as bt
from . import crm, firma, leaddb, niches
from .leads_views import db, lead_or_404, me
from .main import SETTINGS, darf, login_required, safe_next

bp = Blueprint("branchentest", __name__)


def _ajax():
    return request.headers.get("X-Requested-With") == "fetch" or request.accept_mimetypes.best == "application/json"


def _fertig(message, url, ok=True):
    if _ajax():
        return (jsonify(ok=True, message=message), 200) if ok else (jsonify(error=message), 400)
    flash(message if ok else f"Nicht gespeichert: {message}")
    return redirect(url)


def _verwalten():
    if g.account.get("demo"):
        abort(403, "Im Demo-Modus lassen sich die Regeln nicht ändern.")
    if not darf("leads", firma.VERWALTEN):
        abort(403, "Testbranchen und Regeln ändert, wer „Leads und Akquise: Verwalten“ hat.")


def _basis():
    return {"account": g.account, "demo": SETTINGS["DEMO_MODE"], "verwalten": darf("leads", firma.VERWALTEN),
            "kennzahlen": bt.KENNZAHLEN}


@bp.app_context_processor
def _globals():
    return {"pruefungen_offen": lambda: len(bt.pruefungen(db(), me())) if g.get("account") and me() else 0}

# ---------------------------------------------------------------- Übersicht und Branche


@bp.get("/branchentest")
@login_required
def uebersicht():
    c = db()
    zeilen = bt.auswertung(c)
    aktiv = [z for z in zeilen if z["aktiv"]]
    gesamt = {k: sum(z["kalt"][k] for z in aktiv) for k in ("betriebe", *dict(bt.KENNZAHLEN))}
    regeln = bt.regeln(c)
    return render_template("branchentest.html", zeilen=zeilen, gesamt=gesamt, ziel=sum(z["ziel"] for z in aktiv),
                           regeln=regeln, regel_labels=bt.REGELN, offen=len(bt.pruefungen(c, me())),
                           alle_offen=len(bt.pruefungen(c)), personen=firma.aktive_personen(c),
                           nischen=niches.NICHES, gruppen=niches.GROUPS, **_basis())


@bp.get("/branchentest/<key>")
@login_required
def branche(key):
    c = db()
    zeile = next((z for z in bt.auswertung(c) if z["key"] == key), None)
    if not zeile:
        abort(404)
    leads = bt.leads_der_branche(c, key)
    return render_template("testbranche.html", b=zeile, leads=leads, kandidaten=bt.kandidaten(c, key),
                           personen=firma.aktive_personen(c), nischen=niches.NICHES, gruppen=niches.GROUPS,
                           stufen=crm.STAGE_LABELS,
                           **_basis())


@bp.post("/branchentest/speichern")
@login_required
def speichern():
    _verwalten()
    f = request.form
    try:
        key = bt.branche_speichern(db(), me(), f.get("key") or None, f.get("name", ""), f.getlist("nischen"),
                                   f.get("person") or None, f.get("ziel", 50), f.get("beschreibung", ""),
                                   f.get("aktiv") == "1")
    except bt.BranchentestError as exc:
        return _fertig(str(exc), url_for("branchentest.uebersicht"), ok=False)
    return _fertig("Testbranche gespeichert.", url_for("branchentest.branche", key=key))


@bp.post("/branchentest/regeln")
@login_required
def regeln():
    _verwalten()
    try:
        bt.regeln_speichern(db(), me(), request.form)
    except bt.BranchentestError as exc:
        return _fertig(str(exc), url_for("branchentest.uebersicht") + "#regeln", ok=False)
    return _fertig("Regeln gespeichert.", url_for("branchentest.uebersicht") + "#regeln")


@bp.post("/branchentest/<key>/uebernehmen")
@login_required
def uebernehmen(key):
    """Passende Betriebe in diese Testbranche übernehmen (kalt). Wer übernimmt, gilt als recherchierende Person."""
    c = db()
    if not bt.branche(c, key):
        abort(404)
    ids = [int(x) for x in request.form.getlist("id") if x.isdigit()][:200]
    if not ids:
        return _fertig("Bitte mindestens einen Betrieb auswählen.", url_for("branchentest.branche", key=key), ok=False)
    for lead_id in ids:
        lead = leaddb.get_lead(c, lead_id)
        if not lead:
            continue
        if not lead["stage"]:
            crm.advance(c, lead_id, "neu", author=g.account.get("name") or me())
        crm.claim(c, lead_id, me())
        bt.eingang(c, lead_id, me(), testbranche=key, herkunft=lead.get("herkunft") or "kalt")
    return _fertig(f"{len(ids)} {'Betrieb' if len(ids) == 1 else 'Betriebe'} übernommen.",
                   url_for("branchentest.branche", key=key))

# ---------------------------------------------------------------- Lead-Prüfung


@bp.get("/branchentest/pruefen")
@login_required
def pruefen():
    c = db()
    alle = request.args.get("alle") == "1" and darf("leads", firma.VERWALTEN)
    liste = bt.pruefungen(c, None if alle else me())
    erledigt = bt.pruefungen(c, None if alle else me(), status=None, limit=60)
    erledigt = [p for p in erledigt if p["status"] != "offen"][:20]
    return render_template("lead_pruefung.html", liste=liste, erledigt=erledigt, alle=alle,
                           kpis=bt.recherche_kpis(c, me()), **_basis())


@bp.post("/branchentest/pruefung/<int:fid>")
@login_required
def entscheiden(fid):
    c = db()
    row = c.execute("SELECT * FROM freigaben WHERE id=? AND art='lead_pruefung'", (fid,)).fetchone()
    if not row:
        abort(404)
    if not bt.darf_entscheiden(c, me(), dict(row)):
        abort(403, "Diese Prüfung gehört einer anderen Person.")
    nxt = safe_next(request.form.get("next", "")) or url_for("branchentest.pruefen")
    try:
        status = bt.entscheiden(c, me(), fid, request.form.get("angenommen") == "1", request.form.get("grund", ""))
    except bt.BranchentestError as exc:
        return _fertig(str(exc), nxt, ok=False)
    return _fertig("Angenommen." if status == "angenommen" else "Abgelehnt. Die Person bekommt den Grund.", nxt)

# ---------------------------------------------------------------- In der Lead-Akte


@bp.post("/leads/<int:lead_id>/branchentest")
@login_required
def lead_test(lead_id):
    lead_or_404(lead_id)
    try:
        bt.herkunft_setzen(db(), me(), lead_id, request.form.get("herkunft", "kalt"),
                           request.form.get("testbranche") or None)
        if request.form.get("testbranche"):
            bt.eingang(db(), lead_id, me())
    except bt.BranchentestError as exc:
        return _fertig(str(exc), url_for("leads.detail", lead_id=lead_id), ok=False)
    return _fertig("Gespeichert.", url_for("leads.detail", lead_id=lead_id))


@bp.post("/leads/<int:lead_id>/kontakt")
@login_required
def kontakt(lead_id):
    lead_or_404(lead_id)
    try:
        bt.kontakt(db(), me(), lead_id, request.form.get("kanal", ""), request.form.get("notiz", ""))
    except bt.BranchentestError as exc:
        return _fertig(str(exc), url_for("leads.detail", lead_id=lead_id), ok=False)
    return _fertig("Kontakt protokolliert.", url_for("leads.detail", lead_id=lead_id))


@bp.post("/leads/<int:lead_id>/preisgespraech")
@login_required
def preisgespraech(lead_id):
    lead_or_404(lead_id)
    an = request.form.get("an") == "1"
    try:
        bt.preisgespraech(db(), me(), lead_id, an)
    except bt.BranchentestError as exc:
        return _fertig(str(exc), url_for("leads.detail", lead_id=lead_id), ok=False)
    return _fertig("Ernsthaftes Preisgespräch vermerkt." if an else "Zurückgenommen.",
                   url_for("leads.detail", lead_id=lead_id))
