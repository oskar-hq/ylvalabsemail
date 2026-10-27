"""Oberfläche des CRM: Pipeline-Board, Wiedervorlagen, Kontaktdaten bearbeiten, Firmen anlegen, CSV."""
import re
from datetime import date

from flask import Blueprint, Response, abort, g, jsonify, render_template, request

from . import agent, crm, leaddb, mailbox, regions, team
from .leads_views import db, lead_or_404, me, status_payload, who
from .main import SETTINGS, login_required

bp = Blueprint("crm", __name__)


@bp.app_context_processor
def _crm_globals():
    return {"crm_due": lambda: crm.due_count(db(), me()) + mailbox.unread_count(db(), me()),
            "inbox_unread": lambda: mailbox.unread_count(db(), me())}


@bp.get("/crm")
@login_required
def board():
    today = date.today().isoformat()
    wer = _wer()
    columns = crm.board(db(), wer, me())
    view = "tabelle" if request.args.get("ansicht") == "tabelle" else "board"
    return render_template("crm.html", account=g.account, columns=columns, today=today, view=view,
                           total_cards=sum(len(col[3]) for col in columns), wer=wer, people=team.names(db()),
                           due=crm.open_tasks(db(), until=today, who=wer, me=me()),
                           upcoming=crm.open_tasks(db(), who=wer, me=me())[:40],
                           st=status_payload(), demo=SETTINGS["DEMO_MODE"])


def _wer():
    """Welches CRM: das der angemeldeten Person (Standard), das einer anderen Person oder „alle“ (Gesamt)."""
    wer = request.args.get("wer") or me() or "alle"
    if wer == "meine":
        wer = me() or "alle"
    return wer if wer == "alle" or wer in team.names(db()) else "alle"


@bp.post("/crm/<int:lead_id>/stufe")
@login_required
def set_stage(lead_id):
    lead_or_404(lead_id)
    stage = request.form.get("stage", "")
    if stage not in crm.STAGE_KEYS:
        abort(400)
    crm.set_stage(db(), lead_id, stage, author=who(), note=request.form.get("note", "").strip())
    crm.claim(db(), lead_id, me())
    return jsonify(ok=True, label=crm.STAGE_LABELS[stage])


@bp.post("/crm/<int:lead_id>/zustaendig")
@login_required
def set_owner(lead_id):
    lead_or_404(lead_id)
    key = request.form.get("owner", "")
    if key and key not in team.names(db()):
        abort(400)
    crm.set_owner(db(), lead_id, key or None, who())
    return jsonify(ok=True, name=team.names(db()).get(key, "niemand"))


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
    crm.claim(db(), lead_id, me())
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
    crm.claim(db(), lead_id, me())
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


# ---------------------------------------------------------------- Postfach: Antworten lesen und direkt antworten


@bp.get("/crm/postfach")
@login_required
def inbox():
    wer = "alle" if request.args.get("wer") == "alle" or not me() else "meine"
    return render_template("inbox.html", account=g.account, groups=mailbox.grouped(db(), me() if wer == "meine" else None),
                           moods=mailbox.MOODS, wer=wer, people=team.names(db()),
                           inbox_status=leaddb.kv_get(db(), "inbox_status"), ready=_mail_ready(),
                           st=status_payload(), demo=SETTINGS["DEMO_MODE"])


def _mail_ready():
    """Kann die App Mails schicken und abrufen? None = ja, sonst was fehlt."""
    if not agent.outreach_configured():
        return "Das Akquise-Postfach ist noch nicht eingetragen."
    if not SETTINGS["IMAP_HOST"]:
        return "Der Posteingang (IMAP) fehlt, Antworten kommen dann nicht in die App."
    return None


@bp.get("/crm/postfach/neu.json")
@login_required
def inbox_news():
    """Für die Glocke oben: ungelesene Gespräche und ob das Abrufen klappt."""
    c = db()
    items = [{"name": cv["name"], "n": cv["unread_msgs"], "at": cv["last_at"],
              "text": (cv["last_text"] or "").replace("\n", " ")[:120],
              "url": f"/crm/postfach/{cv['id']}"} for cv in mailbox.conversations(c, me()) if cv["unread_msgs"]][:8]
    status = leaddb.kv_get(c, "inbox_status") or {}
    return jsonify(count=mailbox.unread_count(c, me()), items=items,
                   error=None if status.get("ok", True) else status.get("error"), checked=status.get("at"))


@bp.post("/crm/postfach/abrufen")
@login_required
def inbox_fetch():
    """„Jetzt abrufen“: Postfach sofort lesen statt auf die nächsten Minuten zu warten."""
    if g.account.get("demo"):
        return jsonify(ok=True, neu=0, message="Demo: Es wird kein echtes Postfach gelesen.")
    missing = _mail_ready()
    if missing:
        return jsonify(error=missing), 400
    n, error = agent.fetch_inbox(db())
    if error:
        return jsonify(error=error), 502
    return jsonify(ok=True, neu=n, message=f"{n} neue Mails." if n != 1 else "1 neue Mail.")


@bp.route("/crm/postfach/schreiben", methods=["GET", "POST"])
@login_required
def compose_mail():
    """Neue Mail an irgendwen, unter dem Namen von Ylva Labs aus dem Akquise-Postfach."""
    c = db()
    if request.method == "GET":
        lead = None
        if request.args.get("lead", "").isdigit():
            lead = lead_or_404(int(request.args["lead"]))
        return render_template("mail_new.html", account=g.account, lead=lead, ready=_mail_ready(),
                               senders=team.members(db()), sender_key=_default_sender(lead), org=SETTINGS["ORG_NAME"],
                               sender=agent.outreach_address(),
                               st=status_payload(), demo=SETTINGS["DEMO_MODE"])
    to = request.form.get("to", "").strip().lower()
    subject = request.form.get("subject", "").strip()
    text = request.form.get("text", "").strip()
    if not re.fullmatch(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+", to):
        return jsonify(error="Bitte eine gültige E-Mail-Adresse eintragen (eine Adresse)."), 400
    if not subject or not text:
        return jsonify(error="Bitte Betreff und Text schreiben."), 400
    row = c.execute("SELECT id FROM leads WHERE lower(email)=? ORDER BY id DESC LIMIT 1", (to,)).fetchone()
    if row:
        lead = leaddb.get_lead(c, row["id"])
        if lead["status"] != "kontakt":
            ok, why = crm.may_email(lead, SETTINGS["OUTREACH_EMAIL"])
            if not ok:
                return jsonify(error=why), 400
    else:
        now = leaddb.now()
        cur = c.execute("INSERT OR IGNORE INTO leads (source_id, created_at, updated_at, name, branche, category, "
                        "category_label, email, status, fit_reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (f"mail:{to}", now, now, request.form.get("name", "").strip() or to, "sonstige", "", "Kontakt",
                         to, "kontakt", "Von euch angeschrieben."))
        c.commit()
        lead_id = cur.lastrowid or c.execute("SELECT id FROM leads WHERE source_id=?", (f"mail:{to}",)).fetchone()["id"]
        lead = leaddb.get_lead(c, lead_id)
    if lead["status"] == "gesperrt":
        return jsonify(error="Diese Adresse möchte keine Nachrichten mehr bekommen."), 400
    sender = _sender_from_form()
    if g.account.get("demo"):
        agent.record_reply(c, lead, text, team.names(c).get(sender) or who(), subject, to, note=" Demo: nicht verschickt.",
                           what="Neue Mail aus dem Postfach")
        crm.claim(c, lead["id"], sender)
        return jsonify(ok=True, url=f"/crm/postfach/{lead['id']}")
    try:
        agent.send_new(c, lead, to, subject, text, who(), sender=sender)
    except Exception as exc:
        return jsonify(error=str(exc)), 502
    return jsonify(ok=True, url=f"/crm/postfach/{lead['id']}")


@bp.get("/crm/postfach/<int:lead_id>")
@login_required
def conversation(lead_id):
    lead = lead_or_404(lead_id)
    items = mailbox.thread(db(), lead)
    mailbox.mark_read(db(), lead_id)
    if lead["unread"]:
        leaddb.update_lead(db(), lead_id, unread=0)
    last_in = mailbox.last_incoming(db(), lead_id)
    mood, why = mailbox.mood(lead, last_in)
    can_send = agent.outreach_configured() or g.account.get("demo")
    return render_template("conversation.html", account=g.account, lead=lead, items=items, mood=mood, mood_why=why,
                           senders=team.members(db()), sender=_default_sender(lead), people=team.names(db()),
                           moods=mailbox.MOODS, last_in=last_in, can_send=can_send,
                           subject=mailbox.reply_subject((last_in or {}).get("subject") or lead["subject"] or lead["name"]),
                           st=status_payload(), demo=SETTINGS["DEMO_MODE"])


def _default_sender(lead=None):
    return me() or (lead or {}).get("owner") or "oskar"


def _sender_from_form():
    key = request.form.get("von", "")
    return key if key in team.names(db()) else me()


@bp.post("/crm/postfach/<int:lead_id>/antwort")
@login_required
def reply(lead_id):
    lead = lead_or_404(lead_id)
    text = request.form.get("text", "").strip()
    if not text:
        return jsonify(error="Bitte eine Antwort schreiben."), 400
    if lead["status"] == "gesperrt":
        return jsonify(error="Dieser Betrieb möchte keine Nachrichten mehr bekommen."), 400
    subject = request.form.get("subject", "").strip() or None
    if not mailbox.last_incoming(db(), lead_id) and lead["status"] != "kontakt":
        ok, why = crm.may_email(lead, SETTINGS["OUTREACH_EMAIL"])  # noch keine Antwort: Erstkontakt-Regel gilt
        if not ok:
            return jsonify(error=why), 400
    sender = _sender_from_form()
    if g.account.get("demo"):
        last = mailbox.last_incoming(db(), lead_id)
        agent.record_reply(db(), lead, text, team.names(db()).get(sender) or who(), subject or "Re: …",
                           (last or {}).get("from_addr") or lead["email"] or "", note=" Demo: nicht verschickt.")
        crm.claim(db(), lead_id, sender)
        return jsonify(ok=True)
    try:
        agent.send_reply(db(), lead, text, who(), subject, sender=sender)
    except Exception as exc:
        return jsonify(error=str(exc)), 502
    return jsonify(ok=True)


@bp.post("/crm/postfach/<int:lead_id>/stimmung")
@login_required
def set_mood(lead_id):
    lead_or_404(lead_id)
    value = request.form.get("mood", "")
    if value and value not in mailbox.MOODS:
        abort(400)
    leaddb.update_lead(db(), lead_id, mood=value or None)
    return jsonify(ok=True)
