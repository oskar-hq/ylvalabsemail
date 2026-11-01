"""Seiten der Stufe 6: Freigabe-Zentrale, Entscheidungs-Log, Notfall-Knopf und die Einstellungen dazu
(Freigabe-Stufen, Entscheidungsrechte, Zuständigkeit bei Notfällen)."""
from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import firma, freigaben, notfall
from .leads_views import db, me
from .main import darf, login_required, safe_next

bp = Blueprint("freigaben", __name__)
FEHLER = (freigaben.FreigabeError, notfall.NotfallError, ValueError)


def _ajax():
    return request.headers.get("X-Requested-With") == "fetch" or request.accept_mimetypes.best == "application/json"


def _fertig(message, url, ok=True):
    if _ajax():
        return (jsonify(ok=True, message=message), 200) if ok else (jsonify(error=message), 400)
    flash(message if ok else f"Nicht gespeichert: {message}")
    return redirect(url)


def _zurueck(default):
    return safe_next(request.form.get("next", "")) or default


@bp.app_context_processor
def _freigaben_globals():
    def _offen():
        try:
            return freigaben.zaehlen(db(), me()) if g.get("account") else 0
        except Exception:
            return 0

    def _notfaelle():
        try:
            return notfall.aktive(db()) if g.get("account") else []
        except Exception:
            return []
    return {"freigaben_offen": _offen, "notfaelle_aktiv": _notfaelle}

# ---------------------------------------------------------------- Freigabe-Zentrale


@bp.get("/freigaben")
@login_required
def zentrale():
    c = db()
    key = me()
    alle = request.args.get("alle") == "1"
    liste = freigaben.offen(c, key, alle=alle)
    gruppen = {}
    for f in liste:
        gruppen.setdefault(f["art_label"], []).append(f)
    darf_alle = any(darf(m, firma.VERWALTEN) for m in ("leads", "kalender", "personen", "kunden"))
    return render_template("freigaben.html", account=g.account, gruppen=gruppen, n=len(liste), alle=alle,
                           darf_alle=darf_alle, stufen=freigaben.STUFEN)


@bp.post("/freigaben/<art>/<int:oid>")
@login_required
def entscheiden(art, oid):
    ok = request.form.get("ok") == "1"
    ziel = _zurueck(url_for("freigaben.zentrale"))
    try:
        freigaben.entscheiden(db(), me(), art, oid, ok, request.form.get("grund", ""))
    except FEHLER as exc:
        return _fertig(str(exc), ziel, ok=False)
    return _fertig("Freigegeben." if ok else "Abgelehnt, der Grund ist vermerkt.", ziel)


@bp.post("/freigaben/<int:fid>/stoppen")
@login_required
def stoppen(fid):
    try:
        freigaben.stoppen(db(), me(), fid)
    except FEHLER as exc:
        return _fertig(str(exc), url_for("freigaben.zentrale"), ok=False)
    return _fertig("Gestoppt. Es passiert erst, wenn jemand freigibt.", url_for("freigaben.zentrale"))

# ---------------------------------------------------------------- Entscheidungs-Log


@bp.get("/entscheidungen")
@login_required
def log():
    c = db()
    status = request.args.get("status") or None
    if status not in (None, *freigaben.STATUS_LABEL):
        status = None
    return render_template("entscheidungen.html", account=g.account, liste=freigaben.entscheidungen(c, status=status),
                           status=status, status_label=freigaben.STATUS_LABEL, rechte=freigaben.rechte(c),
                           wer_text=lambda t: freigaben.wer_text(c, t))


@bp.post("/entscheidungen")
@login_required
def festhalten():
    ziel = _zurueck(url_for("freigaben.log"))
    f = request.form
    quelle = "wochengespraech" if f.get("quelle") == "wochengespraech" else "hand"
    try:
        eid = freigaben.entscheiden_festhalten(db(), me(), f.get("titel", ""), f.get("begruendung", ""),
                                               f.get("grundlage", ""), f.get("recht", ""), f.get("betrag") or None,
                                               quelle)
    except FEHLER as exc:
        return _fertig(str(exc), ziel, ok=False)
    e = freigaben.entscheidung_get(db(), eid)
    if e["status"] == "gilt":
        return _fertig("Entscheidung festgehalten.", ziel)
    return _fertig(f"Festgehalten. Wartet noch auf: {', '.join(e['fehlt'])}.", ziel)

# ---------------------------------------------------------------- Notfall


@bp.get("/notfall", endpoint="notfall")
@login_required
def notfall_seite():
    c = db()
    leads = [dict(r) for r in c.execute(
        "SELECT p.lead_id AS id, IFNULL(l.name, p.titel) AS name FROM projekte p LEFT JOIN leads l ON l.id = p.lead_id "
        "WHERE p.lead_id IS NOT NULL AND p.status != 'beendet' ORDER BY name")] if darf("kunden") else []
    return render_template("notfall.html", account=g.account, aktiv=notfall.aktive(c),
                           behoben=[n for n in notfall.liste(c, limit=30) if n["behoben_am"]], arten=notfall.ARTEN,
                           zustaendig={k: [firma.name_von(c, p) for p in notfall.zustaendige(c, k)]
                                       for k in notfall.ARTEN}, leads=leads)


@bp.post("/notfall")
@login_required
def notfall_melden():
    try:
        nid = notfall.melden(db(), me(), request.form.get("art", ""), request.form.get("text", ""),
                             request.form.get("lead_id"))
    except FEHLER as exc:
        return _fertig(str(exc), url_for("freigaben.notfall"), ok=False)
    n = notfall.get(db(), nid)
    return _fertig(f"Gemeldet. Benachrichtigt: {', '.join(n['zustaendig_namen']) or 'niemand'}.",
                   url_for("freigaben.notfall"))


@bp.post("/notfall/<int:nid>/behoben")
@login_required
def notfall_behoben(nid):
    try:
        notfall.beheben(db(), me(), nid, request.form.get("notiz", ""))
    except FEHLER as exc:
        return _fertig(str(exc), url_for("freigaben.notfall"), ok=False)
    return _fertig("Als behoben vermerkt.", url_for("freigaben.notfall"))

# ---------------------------------------------------------------- Einstellungen


def _verwalten():
    return darf("entscheidungen", firma.VERWALTEN)


@bp.get("/einstellungen/freigaben")
@login_required
def einstellungen():
    c = db()
    return render_template("freigaben_einstellungen.html", account=g.account, verwalten=_verwalten(),
                           aktionen=freigaben.AKTIONEN, werte=freigaben.stufen(c), stufen=freigaben.STUFEN,
                           rechte=freigaben.rechte(c), optionen=freigaben.wer_optionen(c),
                           wer_text=lambda t: freigaben.wer_text(c, t), notfall_regeln=notfall.regeln(c),
                           notfall_arten=notfall.ARTEN, bereiche=firma.bereiche(c),
                           neu_recht={"key": "", "titel": "", "beschreibung": "", "wer": [], "modus": "eine",
                                      "gefragt": [], "grenze_eur": None, "ab_grenze": None})


@bp.post("/einstellungen/freigaben/stufen")
@login_required
def stufen_speichern():
    if not _verwalten():
        abort(403)
    try:
        geaendert = freigaben.stufen_speichern(db(), me(), request.form)
    except FEHLER as exc:
        return _fertig(str(exc), url_for("freigaben.einstellungen"), ok=False)
    return _fertig("Gespeichert." if geaendert else "Nichts geändert.", url_for("freigaben.einstellungen"))


@bp.post("/einstellungen/freigaben/rechte")
@login_required
def recht_speichern():
    if not _verwalten():
        abort(403)
    f = request.form
    try:
        if f.get("loeschen") == "1":
            freigaben.recht_loeschen(db(), me(), f.get("key", ""))
            return _fertig("Entscheidungsrecht gelöscht.", url_for("freigaben.einstellungen") + "#rechte")
        freigaben.recht_speichern(db(), me(), f.get("key", ""), f.get("titel", ""), f.get("beschreibung", ""),
                                  f.getlist("wer"), f.get("modus", "eine"), f.getlist("gefragt"),
                                  f.get("grenze_eur", ""), f.get("ab_grenze", ""))
    except FEHLER as exc:
        return _fertig(str(exc), url_for("freigaben.einstellungen") + "#rechte", ok=False)
    return _fertig("Entscheidungsrecht gespeichert.", url_for("freigaben.einstellungen") + "#rechte")


@bp.post("/einstellungen/freigaben/notfall")
@login_required
def notfall_speichern():
    if not _verwalten():
        abort(403)
    try:
        notfall.regeln_speichern(db(), me(), request.form)
    except FEHLER as exc:
        return _fertig(str(exc), url_for("freigaben.einstellungen") + "#notfall", ok=False)
    return _fertig("Zuständigkeit gespeichert.", url_for("freigaben.einstellungen") + "#notfall")

