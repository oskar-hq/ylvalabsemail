"""Seiten „Finanzen“: Übersicht mit Kennzahlen und Geld-Regel, Rechnungen, laufende Kosten, Kassenstand, Export und
Einstellungen → Finanzen."""
from datetime import date

from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import finanzen, firma
from .leads_views import db, me
from .main import SETTINGS, darf, login_required

bp = Blueprint("finanzen", __name__)


def _ajax():
    return request.headers.get("X-Requested-With") == "fetch" or request.accept_mimetypes.best == "application/json"


def _fertig(message, url, ok=True):
    if _ajax():
        return (jsonify(ok=True, message=message), 200) if ok else (jsonify(error=message), 400)
    flash(message if ok else f"Nicht gespeichert: {message}")
    return redirect(url)


def _tun(fn, ziel, ok_text):
    try:
        text = fn()
    except finanzen.FinanzError as exc:
        return _fertig(str(exc), ziel, ok=False)
    return _fertig(text if isinstance(text, str) else ok_text, ziel)


def _rechnung_or_404(rid):
    r = finanzen.get(db(), rid)
    if not r:
        abort(404)
    return r


@bp.get("/finanzen")
@login_required
def uebersicht():
    c = db()
    status = request.args.get("status") or None
    if status not in (None, "ueberfaellig", *finanzen.STATUS):
        status = None
    heute = date.today()
    return render_template("finanzen.html", account=g.account, k=finanzen.kennzahlen(c),
                           rechnungen=finanzen.liste(c, status), status=status, status_label=finanzen.STATUS,
                           kasse=finanzen.kasse_liste(c, 6), heute=heute.isoformat(),
                           export_von=heute.replace(day=1, month=((heute.month - 1) // 3) * 3 + 1).isoformat(),
                           projekte=[dict(r) for r in c.execute(
                               "SELECT p.id, IFNULL(l.name, p.titel) AS name FROM projekte p LEFT JOIN leads l "
                               "ON l.id = p.lead_id WHERE p.status != 'beendet' ORDER BY name")])


@bp.post("/finanzen/rechnung/neu")
@login_required
def neu():
    f = request.form
    pid = int(f.get("projekt_id") or 0) or None
    art = f.get("art", "sonstig")
    ziel = url_for("finanzen.uebersicht")
    try:
        if art == "einmal":
            rid = finanzen.einmal_anlegen(db(), me(), pid)
        elif art == "monat":
            rid = finanzen.monat_anlegen(db(), me(), pid, f.get("monat") or None)
            if not rid:
                raise finanzen.FinanzError("Für diesen Monat gibt es schon eine Rechnung, oder es ist nichts abzurechnen.")
        else:
            rid = finanzen.anlegen(db(), me(), pid, "sonstig", [("Leistung", 1, 0, None, None)])
    except finanzen.FinanzError as exc:
        return _fertig(str(exc), safe_ziel(f.get("next")) or ziel, ok=False)
    return redirect(url_for("finanzen.rechnung", rid=rid))


def safe_ziel(nxt):
    from .main import safe_next
    return safe_next(nxt or "")


@bp.get("/finanzen/rechnung/<int:rid>")
@login_required
def rechnung(rid):
    c = db()
    r = _rechnung_or_404(rid)
    return render_template("rechnung.html", account=g.account, r=r, regeln=finanzen.regeln(c),
                           fehlt=finanzen.fehlende_angaben(finanzen.regeln(c)), euro=finanzen.euro,
                           storno=finanzen.get(c, r["storno_von"]) if r["storno_von"] else None,
                           storniert_durch=c.execute("SELECT id, nummer FROM rechnungen WHERE storno_von=?",
                                                     (rid,)).fetchone(),
                           verlauf=firma.protokoll(c, objekt=("rechnung", rid), limit=20),
                           freigeben=darf("finanzen", firma.FREIGEBEN))


@bp.post("/finanzen/rechnung/<int:rid>")
@login_required
def rechnung_aktion(rid):
    _rechnung_or_404(rid)
    f = request.form
    was = f.get("was", "")
    ziel = url_for("finanzen.rechnung", rid=rid)
    c, wer = db(), me()
    if was == "speichern":
        texte, mengen, preise = f.getlist("pos_text"), f.getlist("pos_menge"), f.getlist("pos_einzel")
        return _tun(lambda: finanzen.bearbeiten(c, wer, rid, f.get("empfaenger"), f.get("email"), f.get("leistung"),
                                                list(zip(texte, mengen, preise))), ziel, "Gespeichert.")
    if was == "freigabe":
        return _tun(lambda: finanzen.zur_freigabe(c, wer, rid), ziel, "Zur Freigabe gegeben.")
    if was == "loeschen":
        try:
            finanzen.loeschen(c, wer, rid)
        except finanzen.FinanzError as exc:
            return _fertig(str(exc), ziel, ok=False)
        return _fertig("Entwurf gelöscht.", url_for("finanzen.uebersicht"))
    if was in ("bezahlt", "offen"):
        if not darf("finanzen", firma.FREIGEBEN):
            abort(403)
        return _tun(lambda: finanzen.bezahlt(c, wer, rid, f.get("am"), was == "bezahlt"), ziel, "Gespeichert.")
    if was == "versenden":
        if not darf("finanzen", firma.FREIGEBEN) or g.account.get("demo"):
            return _fertig("Im Demo-Modus wird nichts verschickt." if g.account.get("demo") else
                           "Dafür braucht es „Finanzen: Freigeben“.", ziel, ok=False)
        return _tun(lambda: finanzen.versenden(c, wer, rid, SETTINGS["DATA_DIR"]), ziel, "Verschickt.")
    if was == "stornieren":
        if not darf("finanzen", firma.VERWALTEN):
            abort(403)
        try:
            sid = finanzen.stornieren(c, wer, rid, f.get("grund", ""), SETTINGS["DATA_DIR"])
        except finanzen.FinanzError as exc:
            return _fertig(str(exc), ziel, ok=False)
        return _fertig("Storniert. Die Stornorechnung ist ausgestellt.", url_for("finanzen.rechnung", rid=sid))
    abort(400)


@bp.get("/finanzen/rechnung/<int:rid>.pdf")
@login_required
def pdf(rid):
    r = _rechnung_or_404(rid)
    daten = finanzen.pdf_bytes(db(), rid)
    name = (r["nummer"] or f"Entwurf-{rid}") + ".pdf"
    return Response(daten, mimetype="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})


@bp.get("/finanzen/kosten")
@login_required
def kosten():
    c = db()
    return render_template("kosten.html", account=g.account, liste=finanzen.kosten_liste(c, mit_beendeten=True),
                           kategorien=finanzen.KATEGORIEN, k=finanzen.kennzahlen(c), heute=date.today().isoformat(),
                           euro=finanzen.euro)


@bp.post("/finanzen/kosten")
@login_required
def kosten_speichern():
    f = request.form
    return _tun(lambda: finanzen.kosten_speichern(db(), me(), int(f.get("id") or 0) or None, f.get("titel"),
                                                  f.get("betrag"), f.get("kategorie"), f.get("seit"), f.get("bis"),
                                                  f.get("notiz")) and None,
                url_for("finanzen.kosten"), "Gespeichert.")


@bp.post("/finanzen/kasse")
@login_required
def kasse():
    f = request.form
    return _tun(lambda: finanzen.kasse_eintragen(db(), me(), f.get("stand"), f.get("datum"), f.get("notiz")) and None,
                url_for("finanzen.uebersicht") + "#kasse", "Kassenstand eingetragen.")


@bp.get("/finanzen/export")
@login_required
def export():
    try:
        daten, name = finanzen.export_zip(db(), request.args.get("von", ""), request.args.get("bis", ""),
                                          SETTINGS["DATA_DIR"])
    except finanzen.FinanzError as exc:
        flash(f"Nicht exportiert: {exc}")
        return redirect(url_for("finanzen.uebersicht") + "#export")
    firma.log(db(), me(), "finanzen", "export", None, f"Export für den Steuerberater: {name}")
    return Response(daten, mimetype="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@bp.get("/einstellungen/finanzen")
@login_required
def einstellungen():
    c = db()
    return render_template("finanzen_einstellungen.html", account=g.account, regeln=finanzen.regeln(c),
                           labels=finanzen.REGELN, steuer=finanzen.STEUER, verwalten=darf("finanzen", firma.VERWALTEN),
                           fehlt=finanzen.fehlende_angaben(finanzen.regeln(c)))


@bp.post("/einstellungen/finanzen")
@login_required
def einstellungen_speichern():
    if g.account.get("demo") or not darf("finanzen", firma.VERWALTEN):
        abort(403, "Dafür brauchst du „Finanzen: Verwalten“.")
    try:
        geaendert = finanzen.regeln_speichern(db(), me(), request.form)
    except finanzen.FinanzError as exc:
        return _fertig(str(exc), url_for("finanzen.einstellungen"), ok=False)
    return _fertig("Gespeichert." if geaendert else "Nichts geändert.", url_for("finanzen.einstellungen"))
