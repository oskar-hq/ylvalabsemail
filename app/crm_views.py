"""Oberfläche des CRM: Pipeline-Board, Wiedervorlagen, Kontaktdaten bearbeiten, Firmen anlegen, CSV."""
import re
from datetime import date

from flask import Blueprint, Response, abort, g, jsonify, redirect, render_template, request, url_for

from . import agent, arbeitsbereiche, crm, leaddb, mailbox, team
from .leads_views import db, lead_or_404, me, status_payload, who
from .main import SETTINGS, darf, login_required

bp = Blueprint("crm", __name__)


@bp.app_context_processor
def _crm_globals():
    return {"crm_due": lambda: crm.due_count(db(), me()) + mailbox.unread_count(db(), me()),
            "inbox_unread": lambda: mailbox.unread_count(db(), me()),
            "notices_unseen": lambda: leaddb.notices(db(), g.account["user"], 1, me())[1] if g.get("account") else 0}


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
                           st=status_payload())


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
    crm.claim(db(), lead_id, me())  # zuerst: dann ist die Wiedervorlage bei der zuständigen Person
    try:
        crm.add_task(db(), lead_id, due, text, who(), wer=me())
    except ValueError:
        return jsonify(error="Bitte ein Datum wählen."), 400
    return jsonify(ok=True)


@bp.post("/crm/aufgabe/<int:task_id>/erledigt")
@login_required
def done_task(task_id):
    if not crm.complete_task(db(), task_id, who(), wer=me()):
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


# ---------------------------------------------------------------- Postfach: Antworten lesen und direkt antworten


# ---------------------------------------------------------------- Chat (Stufe 13): alle Kanäle als Gespräch


def _chat_liste(c, wer):
    from . import posteingang
    person = me() if wer == "meine" else None
    gespraeche = mailbox.conversations(c, person)
    wartend = {w["id"] for w in posteingang.wartend(c, person)}
    letzte_kanal = {r["lead_id"]: r["kanal"] for r in c.execute(
        "SELECT lead_id, kanal FROM messages m WHERE fehler=0 AND id = (SELECT MAX(id) FROM messages x WHERE "
        "x.lead_id = m.lead_id AND x.fehler = 0)")}
    for g_ in gespraeche:
        g_["wartet"] = g_["id"] in wartend
        g_["kanal"] = letzte_kanal.get(g_["id"]) or "mail"
    gespraeche.sort(key=lambda x: (not x["wartet"], x["last_at"] or ""), reverse=False)
    wartet = [x for x in gespraeche if x["wartet"]]
    rest = sorted([x for x in gespraeche if not x["wartet"]], key=lambda x: x["last_at"] or "", reverse=True)
    return wartet, rest


@bp.get("/chat")
@bp.get("/chat/<int:lead_id>")
@login_required
def chat(lead_id=None):
    """Chat: links die Gespräche (wartet auf euch zuerst), rechts der Verlauf als Sprechblasen. Mails, Slack,
    WhatsApp, LinkedIn, Telefonate: alles eines Kontakts in einem Verlauf."""
    from . import posteingang
    c = db()
    wer = "alle" if request.args.get("wer") == "alle" or not me() else "meine"
    wartet, rest = _chat_liste(c, wer)
    lead = items = None
    kontext = {}
    if lead_id:
        lead = lead_or_404(lead_id)
        items = mailbox.thread(c, lead)
        mailbox.mark_read(c, lead_id)
        if lead["unread"]:
            leaddb.update_lead(c, lead_id, unread=0)
        letzter = next((i for i in reversed(items) if i["direction"] == "in"), None)
        kanal = (letzter or {}).get("kanal") or ("mail" if lead.get("email") else "slack")
        last_in = mailbox.last_incoming(c, lead_id)
        kontext = {"kanal": kanal, "kanaele": posteingang.KANAELE, "ueber_agents": posteingang.UEBER_AGENTS,
                   "can_mail": agent.outreach_configured() and bool(lead.get("email")),
                   "subject": mailbox.reply_subject((last_in or {}).get("subject") or lead["subject"] or lead["name"]),
                   "wartet_seit": posteingang.wartet_seit(c, lead_id), "von_hand": posteingang.VON_HAND,
                   "senders": team.members(c), "sender": _default_sender(lead)}
        projekt = c.execute("SELECT verantwortlich FROM projekte WHERE lead_id=? AND status != 'beendet' ORDER BY id "
                            "DESC LIMIT 1", (lead_id,)).fetchone()
        kontext["wunsch_darf"] = bool(projekt) and (projekt["verantwortlich"] == me() or darf("kunden", 4))
    return render_template("chat.html", account=g.account, wartet=wartet, rest=rest, wer=wer, lead=lead, items=items,
                           **kontext)


@bp.post("/chat/<int:lead_id>/senden")
@login_required
def chat_senden(lead_id):
    """Antwort auf Slack, WhatsApp oder LinkedIn: wartet, bis ein Agent sie dort verschickt."""
    from . import posteingang
    lead_or_404(lead_id)
    try:
        posteingang.zum_senden(db(), me(), lead_id, request.form.get("kanal", ""), request.form.get("text", ""))
    except posteingang.PosteingangError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True, message="Liegt beim Agent zum Verschicken. Im Verlauf steht, sobald es raus ist.")


@bp.get("/crm/postfach")
@login_required
def inbox():
    """Ordner „Sonstige“ und „Fehler“ des Postfachs; die Gespräche selbst stehen seit Stufe 13 im Chat."""
    wer = "alle" if request.args.get("wer") == "alle" or not me() else "meine"
    ordner = request.args.get("ordner") if request.args.get("ordner") in mailbox.ORDNER else "haupt"
    if ordner == "haupt":
        return redirect(url_for("crm.chat", wer="alle" if wer == "alle" else None))
    from . import posteingang
    person = me() if wer == "meine" else None
    return render_template("inbox.html", account=g.account, ordner=ordner, ordner_namen=mailbox.ORDNER,
                           ordner_zahlen=mailbox.ordner_zahlen(db(), person),
                           fehler=mailbox.fehler_liste(db()) if ordner == "fehler" else [],
                           fehler_ordner=agent.S("FEHLER_ORDNER"),
                           groups=mailbox.grouped(db(), person, ordner if ordner != "fehler" else "haupt"),
                           weitere=posteingang.fuer_person(db(), me()) if me() and not arbeitsbereiche.ist_kunde() else [],
                           moods=mailbox.MOODS, wer=wer, people=team.names(db()),
                           inbox_status=leaddb.kv_get(db(), "inbox_status"), ready=_mail_ready(),
                           st=status_payload())


def _mail_ready():
    """Kann die App Mails schicken und abrufen? None = ja, sonst was fehlt."""
    if not agent.outreach_configured():
        return "Das Akquise-Postfach ist noch nicht eingetragen."
    if not agent.S("IMAP_HOST"):
        return "Der Posteingang (IMAP) fehlt, Antworten kommen dann nicht in die App."
    return None


@bp.get("/crm/postfach/neu.json")
@login_required
def inbox_news():
    """Für die Glocke oben: ungelesene Gespräche und ob das Abrufen klappt."""
    c = db()
    items = [{"name": cv["name"], "n": cv["unread_msgs"], "at": cv["last_at"],
              "text": (cv["last_text"] or "").replace("\n", " ")[:120],
              "url": f"/chat/{cv['id']}"} for cv in mailbox.conversations(c, me()) if cv["unread_msgs"]][:8]
    status = leaddb.kv_get(c, "inbox_status") or {}
    return jsonify(count=mailbox.unread_count(c, me()), items=items,
                   error=None if status.get("ok", True) else status.get("error"), checked=status.get("at"))


@bp.get("/meldungen.json")
@login_required
def notices_json():
    """Für die Glocke: Meldungen der App (Läufe, Antworten, Wiedervorlagen, Tagesberichte)."""
    rows, unseen = leaddb.notices(db(), g.account["user"], person=me())
    return jsonify(count=unseen, items=[{"title": r["title"], "text": (r["body"] or "")[:160], "url": r["url"] or "/",
                                         "at": r["at"], "new": r["new"]} for r in rows])


@bp.post("/meldungen/gelesen")
@login_required
def notices_read():
    leaddb.notices_seen(db(), g.account["user"])
    return jsonify(ok=True)


@bp.post("/crm/postfach/abrufen")
@login_required
def inbox_fetch():
    """„Jetzt abrufen“: Postfach sofort lesen statt auf die nächsten Minuten zu warten."""
    from . import posteingang
    weitere = posteingang.postfaecher(db(), nur_aktive=True)
    missing = _mail_ready()
    if missing and not weitere:
        return jsonify(error=missing), 400
    n, error = agent.fetch_inbox(db()) if not missing else (0, None)
    for p in weitere:
        try:
            n += posteingang.abrufen(db(), p["id"])
        except posteingang.PosteingangError as exc:
            error = error or f"Postfach „{p['name']}“: {exc}"
    if error and not n:
        return jsonify(error=error), 502
    return jsonify(ok=True, neu=n, message=(f"{n} neue Mails." if n != 1 else "1 neue Mail.") + (f" {error}" if error else ""))


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
                               st=status_payload())
    to = request.form.get("to", "").strip().lower()
    subject = request.form.get("subject", "").strip()
    text = request.form.get("text", "").strip()
    if not re.fullmatch(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+", to):
        return jsonify(error="Bitte eine gültige E-Mail-Adresse eintragen (eine Adresse)."), 400
    if not subject or not text:
        return jsonify(error="Bitte Betreff und Text schreiben."), 400
    row = c.execute("SELECT id FROM leads WHERE arbeitsbereich=ab() AND lower(email)=? ORDER BY id DESC LIMIT 1", (to,)).fetchone()
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
                        (leaddb.source_id(f"mail:{to}"), now, now, request.form.get("name", "").strip() or to, "sonstige", "", "Kontakt",
                         to, "kontakt", "Von euch angeschrieben."))
        c.commit()
        lead_id = cur.lastrowid or c.execute("SELECT id FROM leads WHERE source_id=?", (leaddb.source_id(f"mail:{to}"),)).fetchone()["id"]
        lead = leaddb.get_lead(c, lead_id)
    if lead["status"] == "gesperrt":
        return jsonify(error="Diese Adresse möchte keine Nachrichten mehr bekommen."), 400
    sender = _sender_from_form()
    postfach_id = int(request.form.get("postfach") or 0) or None
    if postfach_id:
        from . import posteingang
        last = mailbox.last_incoming(db(), lead_id)
        try:
            posteingang.senden(db(), me() or sender, postfach_id, lead, (last or {}).get("from_addr") or lead["email"] or "",
                               subject or mailbox.reply_subject((last or {}).get("subject") or lead["name"]),
                               text + mailbox.quote(last), (last or {}).get("message_id"))
        except posteingang.PosteingangError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(ok=True)
    try:
        agent.send_new(c, lead, to, subject, text, who(), sender=sender)
    except Exception as exc:
        return jsonify(error=str(exc)), 502
    return jsonify(ok=True, url=f"/chat/{lead['id']}")


@bp.get("/crm/postfach/<int:lead_id>")
@login_required
def conversation(lead_id):
    if request.args.get("alt") != "1":  # seit Stufe 13: Gespräche im Chat
        return redirect(url_for("crm.chat", lead_id=lead_id))
    lead = lead_or_404(lead_id)
    items = mailbox.thread(db(), lead)
    mailbox.mark_read(db(), lead_id)
    if lead["unread"]:
        leaddb.update_lead(db(), lead_id, unread=0)
    last_in = mailbox.last_incoming(db(), lead_id)
    mood, why = mailbox.mood(lead, last_in)
    from . import posteingang
    eigene = posteingang.fuer_person(db(), me()) if me() else []
    can_send = agent.outreach_configured() or bool(eigene)
    zuletzt = next((i.get("postfach_id") for i in reversed(items) if i["direction"] == "in" and i.get("kanal") == "mail"),
                   None)
    projekt = db().execute("SELECT id, verantwortlich FROM projekte WHERE lead_id=? AND status != 'beendet' ORDER BY id "
                           "DESC LIMIT 1", (lead_id,)).fetchone()
    return render_template("conversation.html", account=g.account, lead=lead, items=items, mood=mood, mood_why=why,
                           postfaecher=eigene, postfach_vorschlag=zuletzt if zuletzt in [p["id"] for p in eigene] else "",
                           akquise=agent.outreach_configured(), kanaele=posteingang.KANAELE,
                           von_hand=posteingang.VON_HAND, wartet=posteingang.wartet_seit(db(), lead_id),
                           wunsch_darf=bool(projekt) and (projekt["verantwortlich"] == me() or darf("kunden", 4)),
                           senders=team.members(db()), sender=_default_sender(lead), people=team.names(db()),
                           moods=mailbox.MOODS, last_in=last_in, can_send=can_send,
                           subject=mailbox.reply_subject((last_in or {}).get("subject") or lead["subject"] or lead["name"]),
                           st=status_payload())


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
    try:
        agent.send_reply(db(), lead, text, who(), subject, sender=sender)
    except Exception as exc:
        return jsonify(error=str(exc)), 502
    return jsonify(ok=True)


@bp.post("/crm/postfach/<int:lead_id>/festhalten")
@login_required
def festhalten(lead_id):
    """Telefonat, Brief, WhatsApp- oder LinkedIn-Nachricht von Hand an der Akte festhalten."""
    from . import posteingang
    lead_or_404(lead_id)
    f = request.form
    try:
        posteingang.festhalten(db(), me() or who(), lead_id, f.get("kanal", ""), f.get("richtung", "in"), f.get("text", ""),
                               f.get("am", ""))
    except posteingang.PosteingangError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True, message="Festgehalten.")


@bp.post("/crm/postfach/nachricht/<int:mid>/wunsch")
@login_required
def wunsch(mid):
    """Möglichen Änderungswunsch übernehmen (ja=1) oder verwerfen."""
    from . import posteingang
    try:
        aid = posteingang.wunsch_entscheiden(db(), me(), mid, request.form.get("ja") == "1")
    except posteingang.PosteingangError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True, message="Als Änderungswunsch übernommen." if aid else "Kein Änderungswunsch.")


@bp.post("/crm/postfach/<int:lead_id>/stimmung")
@login_required
def set_mood(lead_id):
    lead_or_404(lead_id)
    value = request.form.get("mood", "")
    if value and value not in mailbox.MOODS:
        abort(400)
    leaddb.update_lead(db(), lead_id, mood=value or None)
    return jsonify(ok=True)
