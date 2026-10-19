"""Oberfläche des CRM: Pipeline-Board, Wiedervorlagen, Kontaktdaten bearbeiten, Firmen anlegen, CSV."""
import re
from datetime import date

from flask import Blueprint, Response, abort, g, jsonify, render_template, request

from . import agent, crm, leaddb, regions
from .leads_views import db, lead_or_404, status_payload, who
from .main import SETTINGS, login_required

bp = Blueprint("crm", __name__)


@bp.app_context_processor
def _crm_globals():
    return {"crm_due": lambda: crm.due_count(db())}


@bp.get("/crm")
@login_required
def board():
    today = date.today().isoformat()
    columns = crm.board(db())
    view = "tabelle" if request.args.get("ansicht") == "tabelle" else "board"
    return render_template("crm.html", account=g.account, columns=columns, today=today, view=view,
                           total_cards=sum(len(col[3]) for col in columns),
                           due=crm.open_tasks(db(), until=today), upcoming=crm.open_tasks(db())[:40],
                           st=status_payload(), demo=SETTINGS["DEMO_MODE"])


@bp.post("/crm/<int:lead_id>/stufe")
@login_required
def set_stage(lead_id):
    lead_or_404(lead_id)
    stage = request.form.get("stage", "")
    if stage not in crm.STAGE_KEYS:
        abort(400)
    crm.set_stage(db(), lead_id, stage, author=who(), note=request.form.get("note", "").strip())
    return jsonify(ok=True, label=crm.STAGE_LABELS[stage])


@bp.post("/crm/<int:lead_id>/kontakt")
@login_required
def contact(lead_id):
    """Kontaktdaten in der Akte bearbeiten."""
    lead = lead_or_404(lead_id)
    fields = {k: request.form.get(k, "").strip() or None
              for k in ("contact_name", "email", "phone", "website", "address") if k in request.form}
    if fields.get("email") and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", fields["email"]):
        return jsonify(error="Bitte eine gültige E-Mail-Adresse eingeben."), 400
    if fields.get("email"):
        fields["email"] = fields["email"].lower()
    if fields.get("website"):
        fields["website"] = crm.website_url(fields["website"]) or None
    if "deal_eur" in request.form:
        raw = request.form["deal_eur"].strip().replace(".", "").replace(",", ".")
        try:
            fields["deal_eur"] = float(raw) if raw else None
        except ValueError:
            return jsonify(error="Auftragswert bitte als Zahl, z. B. 4.500."), 400
    changed = {k: v for k, v in fields.items() if lead.get(k) != v}
    if changed:
        leaddb.update_lead(db(), lead_id, **changed)
        leaddb.add_event(db(), lead_id, "edit", "Kontaktdaten geändert: " + ", ".join(
            {"contact_name": "Person", "email": "E-Mail", "phone": "Telefon", "website": "Webseite",
             "address": "Adresse", "deal_eur": "Auftragswert"}[k] for k in changed) + ".", author=who())
    return jsonify(ok=True, changed=bool(changed))


@bp.post("/crm/<int:lead_id>/aufgabe")
@login_required
def add_task(lead_id):
    lead_or_404(lead_id)
    text = request.form.get("text", "").strip()
    due = crm.quick_due(request.form.get("quick", "")) or request.form.get("due", "").strip()
    if not text:
        return jsonify(error="Was soll erledigt werden?"), 400
    try:
        crm.add_task(db(), lead_id, due, text, who())
    except ValueError:
        return jsonify(error="Bitte ein Datum wählen."), 400
    return jsonify(ok=True)


@bp.post("/crm/aufgabe/<int:task_id>/erledigt")
@login_required
def done_task(task_id):
    if not crm.complete_task(db(), task_id, who()):
        return jsonify(error="Schon erledigt oder nicht gefunden."), 400
    return jsonify(ok=True)


@bp.post("/crm/neu")
@login_required
def create():
    data = {k: request.form.get(k, "") for k in ("name", "email", "phone", "website", "contact_name", "city",
                                                  "branche", "note")}
    lead_id, reason = crm.create_lead(db(), data, who())
    if not lead_id:
        return jsonify(error=f"Nicht angelegt: {reason}."), 400
    return jsonify(ok=True, lead_id=lead_id)


@bp.post("/crm/import")
@login_required
def import_csv():
    file = request.files.get("file")
    if not file:
        return jsonify(error="Keine Datei erhalten."), 400
    try:
        result = crm.import_csv(db(), file.read(), who())
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    skipped = [f"Zeile {line}: {reason}" for line, reason in result["uebersprungen"][:20]]
    return jsonify(ok=True, created=result["neu"], skipped=skipped, skipped_count=len(result["uebersprungen"]))


@bp.get("/crm/export.csv")
@login_required
def export_csv():
    everything = request.args.get("alle") == "1"
    name = f"ylva-{'alle-betriebe' if everything else 'pipeline'}-{date.today().isoformat()}.csv"
    return Response(crm.export_csv(db(), everything), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@bp.get("/crm/karte")
@login_required
def heatmap():
    points = crm.map_points(db())
    store = leaddb.kv_get(db(), "regionen", {}) or {}
    kreise = regions.summary(store)
    per_kreis = {}
    for p in points:
        if p["passend"] and p.get("kreis"):
            per_kreis[p["kreis"]] = per_kreis.get(p["kreis"], 0) + 1
    for k in kreise:
        k["passend"] = per_kreis.get(k["key"], 0)
    geo = leaddb.kv_get(db(), "kreise_geo") or {}
    return render_template("karte.html", account=g.account, points=points, hotspots=crm.hotspots(points),
                           kreise=kreise, geo=geo, stand=store.get("stand") or {}, kinds=regions.KINDS,
                           has_stats=bool(store.get("gewerbe") or store.get("bau")), top=regions.TOP,
                           stage_labels=crm.STAGE_LABELS, st=status_payload(), demo=SETTINGS["DEMO_MODE"])


@bp.post("/crm/regionen/import")
@login_required
def import_region_stats():
    """CSV mit Werten je Kreis (Gewerbeanmeldungen, Baugenehmigungen oder Einwohner) übernehmen."""
    kind = request.form.get("kind", "")
    file = request.files.get("file")
    if kind not in regions.KINDS or not file:
        return jsonify(error="Bitte Art der Zahlen wählen und eine CSV-Datei anhängen."), 400
    values = regions.parse_stats_csv(file.read())
    if len(values) < 5:
        return jsonify(error=f"Nur {len(values)} Kreise erkannt. Erwartet wird eine Zeile je Kreis mit Kreisschlüssel "
                             "(z. B. 01058) oder Kreisname und dem Wert dahinter."), 400
    store = leaddb.kv_get(db(), "regionen", {}) or {}
    store[kind] = values
    label = request.form.get("label", "").strip() or "ohne Zeitangabe"
    store.setdefault("stand", {})[kind] = f"{label} · importiert {crm.fmt_day(crm.today())} von {who()}"
    leaddb.kv_set(db(), "regionen", store)
    return jsonify(ok=True, count=len(values), missing=[regions.KREISE[k][0] for k in regions.KREISE if k not in values])


@bp.post("/crm/regionen/grenzen")
@login_required
def load_borders():
    """Kreisgrenzen (neu) laden lassen; das macht der Hintergrund-Arbeiter."""
    if not SETTINGS["LEADS_WORKER"]:
        return jsonify(error="Der Hintergrund-Arbeiter ist aus (LEADS_WORKER=false)."), 400
    leaddb.kv_set(db(), "request:kreise", True)
    agent.wake()
    return jsonify(ok=True, message="Die Kreisgrenzen werden geladen. Das dauert etwa eine Minute.")
