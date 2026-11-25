"""Oberfläche der Akquise: Übersicht, Firma im Detail (Akte) mit Chat, Regeln, Gehirn, Web-App und Push."""
import json
import re
import time
from datetime import datetime

from flask import Blueprint, Response, abort, g, jsonify, redirect, render_template, request, send_from_directory, url_for

from . import agent, ai, assistent, aufgaben, kalender, kpis, projekte, crm, emailbuild, firma, leaddb, pixel, team, webpush
from .main import DB_PATH, ROOT, SETTINGS, login_required

bp = Blueprint("leads", __name__)


def db():
    if "leads_db" not in g:
        g.leads_db = leaddb.connect(DB_PATH)
    return g.leads_db


@bp.teardown_app_request
def close_db(_exc):
    conn = g.pop("leads_db", None)
    if conn:
        conn.close()


def who():
    return g.account.get("name") or g.account.get("address") or g.account.get("user") or "Team"


def me():
    """Schlüssel der angemeldeten Person im Team (finn, hamza, oskar) oder None."""
    return g.account.get("team")


def lead_or_404(lead_id):
    lead = leaddb.get_lead(db(), lead_id)
    if not lead:
        abort(404)
    return lead


def fmt_date(value):
    if not value:
        return ""
    dt = datetime.fromisoformat(value)
    return dt.strftime("%H:%M") if dt.date() == datetime.now().date() else dt.strftime("%d.%m.%Y")


def ago(ts):
    secs = int(time.time() - ts)
    return "gerade eben" if secs < 60 else f"seit {secs // 60} Min." if secs < 3600 else f"seit {secs // 3600} Std."


@bp.app_template_filter("tage_seit")
def _tage_seit(value):
    return (datetime.now() - datetime.fromisoformat(value)).days if value else 0


@bp.app_template_filter("kurzdatum")
def _kurzdatum(value):
    return fmt_date(value)


@bp.app_context_processor
def _leads_globals():
    return {"lead_status_labels": leaddb.STATUS_LABELS, "interest_labels": leaddb.INTEREST_LABELS,
            "tab_of": leaddb.tab_of, "is_due": leaddb.is_due, "followup_days": leaddb.followup_days(),
            "stages": crm.STAGES, "stage_labels": crm.STAGE_LABELS, "fmt_day": crm.fmt_day,
            "leads_todo": lambda: _todo_count(leaddb.tab_counts(db()))}


def _todo_count(counts):
    """Was in den Leads auf euch wartet: Freigaben, Nachfassen, Interessenten."""
    return counts["entwurf"] + counts["nachfassen"] + counts["qualified"]


def money(usd):
    return round(ai.eur(usd), 2)


def status_payload():
    st = agent.status()
    c = db()
    costs = leaddb.cost_summary(c)
    run = c.execute("SELECT * FROM runs WHERE arbeitsbereich=ab() AND status IN ('wartet','laeuft') ORDER BY id DESC LIMIT 1").fetchone()
    run_cost = leaddb._sum(c, "run_id = ?", (run["id"],)) if run else None
    cap = SETTINGS["LEADS_MONTHLY_BUDGET_EUR"]
    return {
        "activity": st["activity"], "since": ago(st["since"]), "error": st["error"],
        "warnings": st["warnings"], "email_enabled": st["email_enabled"], "ai": ai.available(),
        "counts": leaddb.tab_counts(c),
        "unread": c.execute("SELECT COUNT(*) FROM leads WHERE arbeitsbereich=ab() AND unread=1").fetchone()[0],
        "due_tasks": crm.due_count(c),
        "stages": {k: n for k, n in c.execute("SELECT stage, COUNT(*) FROM leads WHERE arbeitsbereich=ab() AND stage IS NOT NULL GROUP BY stage")}, "today": crm.today(),
        "last_search": leaddb.kv_get(c, "last_search"),
        "run": dict(run, params=json.loads(run["params"])) if run else None,
        "run_eur": money(run_cost["usd"]) if run_cost else 0, "run_tokens": (run_cost["tin"] + run_cost["tout"]) if run_cost else 0,
        "today_eur": money(costs["today"]["usd"]), "month_eur": money(costs["month"]["usd"]),
        "month_tokens": costs["month"]["tin"] + costs["month"]["tout"], "cap_eur": cap,
        "last_run": costs["last_run"], "last_run_eur": money(costs["last_run_cost"]["usd"]) if costs["last_run_cost"] else None,
    }


def _own_budget_gone():
    left = agent.person_budget_left_eur(db(), me())
    return left is not None and left <= 0


def my(key):
    """Einstellung für die angemeldete Person: ihr eigener Wert (Mein Profil), sonst der Team-Standard."""
    return team.prefs(db(), me()).get(key, SETTINGS[key])


# ---------------------------------------------------------------- Heute (Startseite)


def _first_name(account):
    if account.get("name"):
        return account["name"].split(" ")[0]
    local = (account.get("address") or account.get("user") or "").split("@")[0]
    return re.split(r"[._\-]", local)[0].capitalize() or "Team"


# ---------------------------------------------------------------- Übersicht


OPEN_STATUSES = ("kandidat", "recherche", "vorgeprueft", "analysiert", "passt", "entwurf", "aussortiert", "fehler")


@bp.get("/leads/kosten")
@login_required
def costs():
    c = db()
    runs = []
    for r in c.execute("SELECT * FROM runs WHERE arbeitsbereich=ab() ORDER BY id DESC LIMIT 30").fetchall():
        runs.append({**dict(r), "counts": json.loads(r["counts"]) if r["counts"] else {},
                     "params": json.loads(r["params"]) if r["params"] else {},
                     "eur": money(leaddb._sum(c, "run_id = ?", (r["id"],))["usd"])})
    summary = leaddb.cost_summary(c)
    stages = [{**s, "label": ai.STAGE_LABELS.get(s["stage"], s["stage"]), "eur": money(s["usd"]),
               "per_call_ct": ai.eur(s["usd"] / s["n"]) * 100 if s["n"] else 0} for s in summary["stages"]]
    drafts_month = c.execute("SELECT COUNT(*) FROM lead_events WHERE kind='draft' AND at >= ?",
                             (datetime.now().strftime("%Y-%m-01"),)).fetchone()[0]
    return render_template("costs.html", account=g.account, st=status_payload(), runs=runs, stages=stages,
                           summary=summary, money=money, drafts_month=drafts_month, prices=ai.PRICES,
                           eur_rate=ai.EUR_PER_USD, models=ai.MODELS, stage_labels=ai.STAGE_LABELS)

# ---------------------------------------------------------------- Einzelne Firma


@bp.get("/leads/<int:lead_id>")
@login_required
def detail(lead_id):
    lead = lead_or_404(lead_id)
    if lead["unread"]:
        leaddb.update_lead(db(), lead_id, unread=0)
    events = [dict(e, meta=json.loads(e["meta"]) if e["meta"] else {}) for e in
              db().execute("SELECT * FROM lead_events WHERE lead_id=? ORDER BY id", (lead_id,))]
    can_mail, mail_hint = crm.may_email(lead, SETTINGS["OUTREACH_EMAIL"])
    return render_template("lead.html", account=g.account, lead=lead, events=events, st=status_payload(),
                           reminder_ct=ai.eur(agent.avg_cost_usd(db(), "erinnerung")) * 100,
                           draft_ct=ai.eur(agent.avg_cost_usd(db(), "entwurf")) * 100,
                           tasks=crm.open_tasks(db(), lead_id=lead_id), can_mail=can_mail, mail_hint=mail_hint,
                           tab=leaddb.tab_of(lead), tabs=leaddb.TABS, people=team.names(db()),
                           komm=aufgaben.kommentare(db(), "lead", lead_id), os_personen=firma.aktive_personen(db()),
                           testbranchen=[], kanaele=[], pruefung=None,
                           lead_termine=kalender.zum_lead(db(), lead_id), projekt=projekte.zum_lead(db(), lead_id),
                           lead_angebote=assistent.angebote(db(), lead_id=lead_id))


@bp.route("/leads/<int:lead_id>/preview", methods=["GET", "POST"])
@login_required
def preview(lead_id):
    lead = lead_or_404(lead_id)
    if request.method == "POST":  # Live-Vorschau aus dem Mail-Fenster, ohne zu speichern
        lead = {**lead, **_draft_from_form()}
        design = _design_from_form(lead)
        if design:
            lead["mail_design"] = json.dumps(design)
    rcpt = {"name": lead["contact_name"] or "", "first": "", "email": lead["email"] or ""}
    key = _sender_key(lead)
    snd = team.sender(db(), key, SETTINGS["ORG_NAME"]) if key else None
    html, _, _ = emailbuild.render(agent.lead_form(lead, snd), rcpt, SETTINGS)
    resp = Response(html, mimetype="text/html")
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    return resp


@bp.route("/leads/<int:lead_id>/mail.json", methods=["GET", "POST"])
@login_required
def mail_json(lead_id):
    """Für das Mail-Fenster auf Heute und im CRM: fertiger Text aus der Vorlage (Firmenname, Anrede … eingesetzt).
    Gibt es noch keinen Text, wird die Vorlage eingesetzt; POST mit neu=1 setzt sie neu ein."""
    lead = lead_or_404(lead_id)
    open_status = lead["status"] in ("passt", "entwurf", "aussortiert", "fehler") and not lead["interest"]
    sender = _sender_key(lead)
    if open_status and (not (lead["body"] or "").strip() or request.form.get("neu") == "1"):
        agent.template_draft(db(), lead_id, sender)
        lead = lead_or_404(lead_id)
    ok, why = crm.may_email(lead, SETTINGS["OUTREACH_EMAIL"])
    sent = lead["status"] in ("versendet", "beantwortet")
    own_design = team.settings(db(), sender)["design"] if sender else None
    if not lead.get("mail_design") and (own_design or leaddb.kv_get(db(), "mail_design_last")):
        lead = {**lead, "mail_design": json.dumps(own_design or leaddb.kv_get(db(), "mail_design_last"))}
    return jsonify(id=lead_id, name=lead["name"], email=lead["email"] or "", contact=lead["contact_name"] or "",
                   design=agent.design_of(lead),
                   templates={k: v["name"] for k, v in emailbuild.TEMPLATES.items()}, graphics=agent.GRAPHICS,
                   icons={k: v[0] for k, v in pixel.ICONS.items()},
                   subject=lead["subject"] or "", greeting=lead["greeting"] or "", body=lead["body"] or "",
                   can_email=ok and not sent, why="Diese Nachricht ist schon raus." if sent else why,
                   sent=sent, editable=open_status, letter=url_for("leads.letter", lead_id=lead_id),
                   detail=url_for("leads.detail", lead_id=lead_id), preview=url_for("leads.preview", lead_id=lead_id),
                   senders=team.names(db()), sender=sender, template_url=url_for("team.template_editor"))


def _sender_key(lead=None):
    """Von wem die Mail kommt: aus dem Formular („von“), sonst die angemeldete Person, sonst die zuständige."""
    names = team.names(db())
    key = request.values.get("von", "")
    if key in names:
        return key
    return me() or (lead or {}).get("owner") or None


@bp.get("/leads/<int:lead_id>/brief")
@login_required
def letter(lead_id):
    """Druckansicht als Brief (DIN 5008 light), falls der Erstkontakt per Post läuft."""
    lead = lead_or_404(lead_id)
    key = _sender_key(lead)
    return render_template("letter.html", lead=lead, paragraphs=emailbuild.paragraphs(lead["body"]),
                           sender_name=team.names(db()).get(key) or SETTINGS["LEADS_SENDER_NAME"],
                           footer=SETTINGS["DEFAULT_FOOTER"],
                           org=SETTINGS["ORG_NAME"], today=datetime.now().strftime("%d.%m.%Y"))


def _design_from_form(lead):
    """Design aus dem Mail-Fenster (Vorlage, Grafik, Symbol, Band-Variante), geprüft. None, wenn nichts mitkam."""
    if "template" not in request.form:
        return None
    d = agent.design_of(lead)
    if request.form.get("template") in emailbuild.TEMPLATES:
        d["template"] = request.form["template"]
    if request.form.get("graphic") in agent.GRAPHICS:
        d["graphic"] = request.form["graphic"]
    if request.form.get("icon") in pixel.ICONS:
        d["icon"] = request.form["icon"]
    try:
        d["band_seed"] = max(1, min(50, int(request.form.get("band_seed") or d["band_seed"])))
    except ValueError:
        pass
    return d


def _draft_from_form():
    return {k: request.form.get(k, "").replace("\r\n", "\n").strip() for k in ("subject", "greeting", "body")}


@bp.post("/leads/<int:lead_id>/save")
@login_required
def save(lead_id):
    return jsonify(ok=True, changed=_save_draft(lead_or_404(lead_id)))


def _save_draft(lead):
    """Übernimmt den mitgeschickten Entwurf. Gibt True zurück, wenn sich etwas geändert hat."""
    design = _design_from_form(lead)
    if design and design != agent.design_of(lead):
        leaddb.update_lead(db(), lead["id"], mail_design=json.dumps(design))
        leaddb.kv_set(db(), "mail_design_last", design)  # das nächste Mail-Fenster startet mit diesem Design
    if "body" not in request.form:
        return False
    lead_id = lead["id"]
    draft = _draft_from_form()
    before = {"subject": lead["subject"] or "", "greeting": lead["greeting"] or "", "body": lead["body"] or ""}
    if draft == before:
        return False
    if lead["status"] == "passt" and draft["body"]:
        draft["status"] = "entwurf"  # selbst geschrieben
    leaddb.update_lead(db(), lead_id, **draft)
    kpis.entwurf_merken(db(), lead_id, me(), draft["body"])
    draft.pop("status", None)
    leaddb.add_event(db(), lead_id, "edit", "Entwurf von Hand geändert.", author=who(),
                     meta={"vorher": before, "nachher": draft})
    leaddb.add_signal(db(), "handänderung",
                      f"Das Team hat den Entwurf für {lead['name']} ({lead['category_label']}) selbst geändert.\n"
                      f"Vorher:\nBetreff: {before['subject']}\n{before['greeting']}\n{before['body']}\n\n"
                      f"Nachher:\nBetreff: {draft['subject']}\n{draft['greeting']}\n{draft['body']}", lead_id)
    return True


@bp.post("/leads/<int:lead_id>/chat")
@login_required
def chat(lead_id):
    lead = lead_or_404(lead_id)
    text = request.form.get("text", "").strip()
    if not text:
        return jsonify(error="Bitte eine Nachricht eingeben."), 400
    if _save_draft(lead):  # ungespeicherte Handänderungen zuerst übernehmen
        lead = lead_or_404(lead_id)
    leaddb.add_event(db(), lead_id, "feedback", text, author=who())
    if not ai.available():
        reply = "Ich habe noch keinen KI-Schlüssel und kann den Entwurf deshalb nicht überarbeiten. Deine " \
                "Rückmeldung ist gespeichert und fließt ins Gehirn ein, sobald die KI läuft."
        leaddb.add_event(db(), lead_id, "ai", reply)
        leaddb.add_signal(db(), "feedback", f"Zu {lead['name']} ({lead['category_label']}): {text}", lead_id)
        return jsonify(reply=reply, subject=lead["subject"], greeting=lead["greeting"], body=lead["body"])
    chat_events = [dict(e) for e in db().execute(
        "SELECT author, text FROM lead_events WHERE lead_id=? AND kind IN ('feedback','ai') ORDER BY id", (lead_id,))]
    if _own_budget_gone():
        return jsonify(error="Dein eigenes KI-Budget für diesen Monat ist aufgebraucht (Mein Profil)."), 400
    before = leaddb._sum(db(), "lead_id = ? AND stage = 'chat'", (lead_id,))["usd"]
    try:
        with agent.acting_as(db(), me()):
            result = ai.revise(leaddb.brain(db()), lead,
                               {"subject": lead["subject"] or "", "greeting": lead["greeting"] or "", "body": lead["body"] or ""},
                               chat_events[:-1], text, agent.writer_name(db(), lead))
    except ai.AIError as exc:
        return jsonify(error=str(exc)), 502
    leaddb.update_lead(db(), lead_id, subject=result["betreff"], greeting=result["anrede"], body=result["text"])
    leaddb.add_event(db(), lead_id, "ai", result["antwort"])
    signal = f"Rückmeldung zu {lead['name']} ({lead['category_label']}): „{text}“."
    if result["lernpunkt"]:
        signal += f" Allgemein: {result['lernpunkt']}"
    leaddb.add_signal(db(), "feedback", signal, lead_id)
    spent = leaddb._sum(db(), "lead_id = ? AND stage = 'chat'", (lead_id,))["usd"] - before
    return jsonify(reply=result["antwort"], subject=result["betreff"], greeting=result["anrede"], body=result["text"],
                   cost_eur=money(spent))


def _gesperrt_bis_pruefung(lead):
    """Kontakt erst nach bestandener Lead-Prüfung (Branchentest, Lernstufe)."""
    return None  # Lead-Prüfung gibt es seit Stufe 13 nicht mehr


@bp.post("/leads/<int:lead_id>/send")
@login_required
def send(lead_id):
    _save_draft(lead_or_404(lead_id))
    lead = lead_or_404(lead_id)
    if _gesperrt_bis_pruefung(lead):
        return _gesperrt_bis_pruefung(lead)
    if lead["status"] not in ("entwurf", "passt", "aussortiert"):
        return jsonify(error="Diese Nachricht ist schon raus."), 400
    if not (lead["body"] or "").strip():
        return jsonify(error="Es gibt noch keinen Text."), 400
    kpis.entwurf_freigegeben(db(), lead_id, me(), lead["body"])  # Lernstufen: wie stark wurde geändert?
    sender = _sender_key(lead)
    try:
        agent.send_outreach(db(), lead, who(), sender=sender)
    except Exception as exc:
        return jsonify(error=str(exc)), 502
    crm.claim(db(), lead_id, sender)
    _approved_signal(lead, "per Mail")
    return jsonify(ok=True)


def _approved_signal(lead, channel):
    leaddb.add_signal(db(), "freigabe", f"Das Team hat die Nachricht an {lead['name']} ({lead['branche']} / "
                      f"{lead['category_label']}, Punkte {lead['fit_score']}) {channel} freigegeben.", lead["id"])


@bp.post("/leads/<int:lead_id>/mark-sent")
@login_required
def mark_sent(lead_id):
    _save_draft(lead_or_404(lead_id))
    lead = lead_or_404(lead_id)
    if _gesperrt_bis_pruefung(lead):
        return _gesperrt_bis_pruefung(lead)
    channel = request.form.get("channel")
    labels = {"brief": "per Brief", "telefon": "am Telefon", "email": "per Mail (selbst verschickt)"}
    if channel not in labels:
        abort(400)
    kpis.entwurf_freigegeben(db(), lead_id, me(), lead["body"])
    leaddb.update_lead(db(), lead_id, status="versendet", channel=channel, sent_at=leaddb.now(), unread=0)
    leaddb.add_event(db(), lead_id, "sent", f"Kontakt {labels[channel]} aufgenommen.", author=who())
    crm.advance(db(), lead_id, "kontaktiert")
    crm.claim(db(), lead_id, _sender_key(lead))
    _approved_signal(lead, labels[channel])
    return jsonify(ok=True)


@bp.post("/leads/<int:lead_id>/reject")
@login_required
def reject(lead_id):
    _reject(lead_or_404(lead_id), request.form.get("reason", "").strip())
    return jsonify(ok=True)


def _reject(lead, reason):
    lead_id = lead["id"]
    leaddb.update_lead(db(), lead_id, status="aussortiert", rejected_by="team", unread=0)
    leaddb.add_event(db(), lead_id, "status", f"Vom Team aussortiert. {reason}".strip(), author=who())
    if lead["stage"] in (None, "neu"):  # noch nicht kontaktiert: nicht (mehr) in der Pipeline
        leaddb.update_lead(db(), lead_id, stage=None, stage_at=leaddb.now())
    else:
        crm.advance(db(), lead_id, "verloren", author=who())
    leaddb.add_signal(db(), "aussortiert", f"Das Team hält {lead['name']} ({lead['branche']} / {lead['category_label']}, "
                      f"{lead['distance_km']} km, {lead['fit_score']} Punkte) für ungeeignet. Grund: {reason or 'nicht genannt'}. "
                      f"Bewertung war: {lead['fit_reason']}", lead_id)


# ---------------------------------------------------------------- Prüfen per Wischen (gut / nicht gut)

REVIEW_SQL = ("SELECT * FROM leads WHERE arbeitsbereich=ab() AND status='passt' AND stage IS NULL AND interest IS NULL "
              "AND IFNULL(rejected_by,'')='' ORDER BY fit_score DESC, distance_km LIMIT ?")


@bp.post("/leads/<int:lead_id>/interest")
@login_required
def set_interest(lead_id):
    lead = lead_or_404(lead_id)
    interest = request.form.get("interest")
    if interest not in leaddb.INTEREST_LABELS:
        abort(400)
    reason = request.form.get("reason", "").strip()
    leaddb.update_lead(db(), lead_id, interest=interest, status="beantwortet", interest_reason=reason or lead["interest_reason"])
    leaddb.add_event(db(), lead_id, "status", f"Vom Team eingeordnet: {leaddb.INTEREST_LABELS[interest]}. {reason}".strip(),
                     author=who())
    crm.advance(db(), lead_id, "verloren" if interest == "kein_interesse" else "antwort", author=who())
    leaddb.add_signal(db(), "einordnung", f"{lead['name']} ({lead['branche']} / {lead['category_label']}) wurde vom Team als "
                      f"„{leaddb.INTEREST_LABELS[interest]}“ eingeordnet. {reason}", lead_id)
    return jsonify(ok=True)


@bp.post("/leads/<int:lead_id>/note")
@login_required
def note(lead_id):
    lead_or_404(lead_id)
    text = request.form.get("text", "").strip()
    if not text:
        return jsonify(error="Leere Notiz."), 400
    leaddb.add_event(db(), lead_id, "note", text, author=who())
    return jsonify(ok=True)


@bp.post("/leads/<int:lead_id>/entwurf/ki")
@login_required
def draft_ai(lead_id):
    """Entwurf von der KI schreiben lassen, nur auf Knopfdruck."""
    lead = lead_or_404(lead_id)
    if lead["status"] not in ("passt", "entwurf", "aussortiert", "fehler") or lead["interest"]:
        return jsonify(error="Für diesen Betrieb wird kein neuer Erstkontakt geschrieben."), 400
    if not ai.available():
        return jsonify(error="Ohne KI-Schlüssel (ANTHROPIC_API_KEY) geht das nicht. Nimm die Vorlage oder schreib selbst."), 400
    left = agent.month_budget_left_eur(db())
    if left is not None and left <= 0:
        return jsonify(error="Das Monatsbudget ist aufgebraucht."), 400
    if _own_budget_gone():
        return jsonify(error="Dein eigenes KI-Budget für diesen Monat ist aufgebraucht (Mein Profil)."), 400
    before = leaddb._sum(db(), "lead_id = ? AND stage = 'entwurf'", (lead_id,))["usd"]
    try:
        with agent.acting_as(db(), me()):
            agent.stage_write(db(), lead_id, leaddb.brain(db()))
    except ai.AIError as exc:
        return jsonify(error=str(exc)), 502
    spent = leaddb._sum(db(), "lead_id = ? AND stage = 'entwurf'", (lead_id,))["usd"] - before
    kpis.entwurf_merken(db(), lead_id, me(), leaddb.get_lead(db(), lead_id)["body"])
    return jsonify(ok=True, cost_eur=money(spent))


@bp.post("/leads/<int:lead_id>/entwurf/vorlage")
@login_required
def draft_template(lead_id):
    """Entwurf aus der Vorlage im Gehirn, ohne KI."""
    lead = lead_or_404(lead_id)
    if lead["status"] not in ("passt", "entwurf", "aussortiert", "fehler") or lead["interest"]:
        return jsonify(error="Für diesen Betrieb wird kein neuer Erstkontakt geschrieben."), 400
    try:
        agent.template_draft(db(), lead_id, _sender_key(lead))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    kpis.entwurf_merken(db(), lead_id, me(), leaddb.get_lead(db(), lead_id)["body"])
    return jsonify(ok=True)


# ---------------------------------------------------------------- Recherche mit Claude (ohne API-Tokens)


# ---------------------------------------------------------------- Erinnerung (Nachfassen)


def _followup_fields():
    return {f"followup_{k}": request.form.get(k, "").replace("\r\n", "\n").strip() for k in ("subject", "greeting", "body")}


def _save_followup(lead):
    if "body" not in request.form:
        return False
    fields = _followup_fields()
    if fields == {k: lead[k] or "" for k in fields}:
        return False
    leaddb.update_lead(db(), lead["id"], **fields)
    leaddb.add_event(db(), lead["id"], "edit", "Erinnerung von Hand geändert.", author=who())
    return True


@bp.post("/leads/<int:lead_id>/erinnerung/schreiben")
@login_required
def followup_write(lead_id):
    lead = lead_or_404(lead_id)
    if not ai.available():
        return jsonify(error="Ohne KI-Schlüssel kann die Erinnerung nicht geschrieben werden. Ihr könnt sie aber selbst tippen."), 400
    left = agent.month_budget_left_eur(db())
    if left is not None and left <= 0:
        return jsonify(error="Das Monatsbudget ist aufgebraucht."), 400
    if _own_budget_gone():
        return jsonify(error="Dein eigenes KI-Budget für diesen Monat ist aufgebraucht (Mein Profil)."), 400
    before = leaddb._sum(db(), "lead_id = ? AND stage = 'erinnerung'", (lead_id,))["usd"]
    try:
        with agent.acting_as(db(), me()):
            agent.write_followup(db(), lead["id"])
    except ai.AIError as exc:
        return jsonify(error=str(exc)), 502
    spent = leaddb._sum(db(), "lead_id = ? AND stage = 'erinnerung'", (lead_id,))["usd"] - before
    return jsonify(ok=True, cost_eur=money(spent))


@bp.post("/leads/<int:lead_id>/erinnerung/speichern")
@login_required
def followup_save(lead_id):
    return jsonify(ok=True, changed=_save_followup(lead_or_404(lead_id)))


@bp.post("/leads/<int:lead_id>/erinnerung/senden")
@login_required
def followup_send(lead_id):
    _save_followup(lead_or_404(lead_id))
    lead = lead_or_404(lead_id)
    if (lead["followup_count"] or 0) != 0:
        return jsonify(error="Die Erinnerung ist schon raus."), 400
    try:
        agent.send_outreach(db(), lead, who(), followup=True)
    except Exception as exc:
        return jsonify(error=str(exc)), 502
    return jsonify(ok=True)


@bp.post("/leads/<int:lead_id>/erinnerung/erledigt")
@login_required
def followup_mark(lead_id):
    _save_followup(lead_or_404(lead_id))
    channel = request.form.get("channel")
    labels = {"brief": "per Brief", "telefon": "am Telefon", "email": "per Mail (selbst verschickt)"}
    if channel not in labels:
        abort(400)
    leaddb.update_lead(db(), lead_id, followup_count=1, followup_sent_at=leaddb.now(), unread=0)
    leaddb.add_event(db(), lead_id, "sent", f"Nachgefasst {labels[channel]}.", author=who())
    return jsonify(ok=True)


@bp.post("/leads/<int:lead_id>/erinnerung/auslassen")
@login_required
def followup_skip(lead_id):
    lead = lead_or_404(lead_id)
    leaddb.update_lead(db(), lead_id, followup_count=-1, unread=0)
    leaddb.add_event(db(), lead_id, "status", "Nicht nachfassen.", author=who())
    leaddb.add_signal(db(), "nachfassen", f"Das Team möchte bei {lead['name']} ({lead['category_label']}) nicht nachfassen.",
                      lead_id)
    return jsonify(ok=True)

# ---------------------------------------------------------------- Gehirn


@bp.route("/leads/gehirn", methods=["GET", "POST"])
@login_required
def brain():
    if request.method == "POST":
        changed = leaddb.save_brain(db(), request.form.get("content", ""), who(),
                                    request.form.get("reason", "").strip() or "Von Hand bearbeitet")
        return redirect(url_for("leads.brain", saved=1 if changed else 0))
    versions = [dict(r) for r in db().execute(
        "SELECT id, at, author, reason FROM brain_versions WHERE arbeitsbereich=ab() ORDER BY id DESC LIMIT 30")]
    pending = db().execute("SELECT COUNT(*) FROM signals WHERE arbeitsbereich=ab() AND processed=0").fetchone()[0]
    return render_template("brain.html", account=g.account, content=leaddb.brain(db()), versions=versions,
                           stats=leaddb.branche_stats(db()), pending=pending, saved=request.args.get("saved"),
                           st=status_payload())


@bp.post("/leads/gehirn/lernen")
@login_required
def learn_now():
    if not ai.available():
        return jsonify(error="Ohne KI-Schlüssel kann nichts gelernt werden."), 400
    leaddb.kv_set(db(), "request:reflect", True)
    agent.wake()
    return jsonify(ok=True, message="Die KI wertet die Rückmeldungen jetzt aus. Das dauert etwa eine Minute.")


@bp.get("/leads/gehirn/<int:version_id>")
@login_required
def brain_version(version_id):
    row = db().execute("SELECT content FROM brain_versions WHERE arbeitsbereich=ab() AND id=?", (version_id,)).fetchone()
    if not row:
        abort(404)
    return Response(row["content"], mimetype="text/plain; charset=utf-8")

# ---------------------------------------------------------------- Systemcheck


@bp.get("/leads/system")
@login_required
def system():
    from . import main, systemcheck
    results = systemcheck.run(SETTINGS, main.smtp_connect, main.imap_sent_folder, SETTINGS["DATA_DIR"])
    return render_template("system.html", account=g.account, results=results, st=status_payload())

# ---------------------------------------------------------------- Web-App & Push


@bp.get("/sw.js")
def service_worker():
    resp = send_from_directory(ROOT / "static", "sw.js", mimetype="text/javascript", max_age=0)
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@bp.get("/manifest.webmanifest")
def manifest():
    return send_from_directory(ROOT / "static", "manifest.webmanifest", mimetype="application/manifest+json", max_age=3600)


@bp.get("/push/key")
@login_required
def push_key():
    return jsonify(key=agent.vapid().public_key)


@bp.post("/push/subscribe")
@login_required
def push_subscribe():
    try:
        sub = json.loads(request.form.get("subscription", ""))
        assert webpush.valid_endpoint(sub["endpoint"]) and sub["keys"]["p256dh"] and sub["keys"]["auth"]
    except Exception:
        return jsonify(error="Ungültiges Abo."), 400
    db().execute("INSERT INTO push_subs (endpoint, data, user, created_at, person) VALUES (?,?,?,?,?) "
                 "ON CONFLICT(endpoint) DO UPDATE SET data=excluded.data, user=excluded.user, person=excluded.person",
                 (sub["endpoint"], json.dumps(sub), who(), leaddb.now(), me()))
    db().commit()
    return jsonify(ok=True)


@bp.post("/push/unsubscribe")
@login_required
def push_unsubscribe():
    db().execute("DELETE FROM push_subs WHERE endpoint=? AND user=?", (request.form.get("endpoint", ""), who()))
    db().commit()
    return jsonify(ok=True)


@bp.post("/push/test")
@login_required
def push_test():
    count = db().execute("SELECT COUNT(*) FROM push_subs").fetchone()[0]
    if not count:
        return jsonify(error="Noch kein Gerät angemeldet."), 400
    agent.notify(db(), "Test", "Benachrichtigungen funktionieren.", "/leads", push=True)
    return jsonify(ok=True, devices=count)
