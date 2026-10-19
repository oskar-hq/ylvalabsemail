"""Oberfläche der Akquise: Übersicht, Firma im Detail (Akte) mit Chat, Regeln, Gehirn, Web-App und Push."""
import json
import re
import time
from datetime import datetime

from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, send_from_directory, url_for

from . import agent, ai, claude_research, crm, emailbuild, leaddb, schedule, scoring
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
    return g.account.get("address") or g.account.get("user") or "Team"


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
            "tab_of": leaddb.tab_of, "is_due": leaddb.is_due, "followup_days": leaddb.FOLLOWUP_DAYS,
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
    run = c.execute("SELECT * FROM runs WHERE status IN ('wartet','laeuft') ORDER BY id DESC LIMIT 1").fetchone()
    run_cost = leaddb._sum(c, "run_id = ?", (run["id"],)) if run else None
    cap = SETTINGS["LEADS_MONTHLY_BUDGET_EUR"]
    return {
        "activity": st["activity"], "since": ago(st["since"]), "error": st["error"],
        "warnings": st["warnings"], "email_enabled": st["email_enabled"], "ai": ai.available(),
        "counts": leaddb.tab_counts(c),
        "unread": c.execute("SELECT COUNT(*) FROM leads WHERE unread=1").fetchone()[0],
        "due_tasks": crm.due_count(c),
        "stages": {k: n for k, n in c.execute("SELECT stage, COUNT(*) FROM leads WHERE stage IS NOT NULL GROUP BY stage")}, "rule_min": SETTINGS["LEADS_RULE_MIN"], "today": crm.today(),
        "last_search": leaddb.kv_get(c, "last_search"),
        "run": dict(run, params=json.loads(run["params"])) if run else None,
        "run_eur": money(run_cost["usd"]) if run_cost else 0, "run_tokens": (run_cost["tin"] + run_cost["tout"]) if run_cost else 0,
        "today_eur": money(costs["today"]["usd"]), "month_eur": money(costs["month"]["usd"]),
        "month_tokens": costs["month"]["tin"] + costs["month"]["tout"], "cap_eur": cap,
        "last_run": costs["last_run"], "last_run_eur": money(costs["last_run_cost"]["usd"]) if costs["last_run_cost"] else None,
    }


def run_defaults():
    est = agent.estimate(db(), SETTINGS["LEADS_RUN_DRAFTS"] if ai.available() else 0, followups=False)
    return {"check": SETTINGS["LEADS_RUN_CHECK"], "top": SETTINGS["LEADS_RUN_TOP"], "drafts": SETTINGS["LEADS_RUN_DRAFTS"],
            "budget": SETTINGS["LEADS_RUN_BUDGET_EUR"], "estimate": est, "models": ai.MODELS}

# ---------------------------------------------------------------- Heute (Startseite)


def _first_name(account):
    if account.get("demo"):
        return "Team"
    local = (account.get("address") or account.get("user") or "").split("@")[0]
    return re.split(r"[._\-]", local)[0].capitalize() or "Team"


@bp.get("/heute")
@login_required
def home():
    st = status_payload()
    c = st["counts"]
    parts = [(st["due_tasks"], "Wiedervorlagen" if st["due_tasks"] != 1 else "Wiedervorlage", url_for("crm.board")),
             (c["entwurf"], "Freigaben" if c["entwurf"] != 1 else "Freigabe", url_for("leads.overview", tab="entwurf")),
             (c["nachfassen"], "Nachfassen", url_for("leads.overview", tab="nachfassen")),
             (c["qualified"], "Interessenten" if c["qualified"] != 1 else "Interessent",
              url_for("leads.overview", tab="qualified"))]
    to_review = review_count()
    parts.append((to_review, "zu prüfen", url_for("leads.review")))
    total = sum(n for n, _, _ in parts)
    # „Starten“ führt direkt zur ersten Aufgabe: Wiedervorlagen, dann Freigaben, Nachfassen, Interessenten
    start = None
    if st["due_tasks"]:
        start = url_for("crm.board")
    else:
        for key in ("entwurf", "nachfassen", "qualified"):
            if c[key]:
                first = leaddb.tab_leads(db(), key, limit=1)
                start = url_for("leads.detail", lead_id=first[0]["id"]) if first else None
                break
        if not start and to_review:
            start = url_for("leads.review")
    hour = datetime.now().hour
    greeting = "Guten Morgen" if hour < 11 else ("Guten Tag" if hour < 18 else "Guten Abend")
    return render_template("home.html", account=g.account, sched=schedule_view(), queue=review_queue(10),
                           to_review=to_review, st=st, parts=[p for p in parts if p[0]], total=total,
                           start=start, greeting=greeting, name=_first_name(g.account), demo=SETTINGS["DEMO_MODE"])

# ---------------------------------------------------------------- Übersicht


@bp.get("/leads")
@login_required
def overview():
    tab = request.args.get("tab", "alle")
    keys = [k for k, _, _ in leaddb.TABS]
    if tab not in keys:
        tab = "alle"
    sections = [(k, label, hint, leaddb.tab_leads(db(), k, limit=300 if tab == k else 8))
                for k, label, hint in leaddb.TABS if tab in ("alle", k)]
    by_key = {k: (k, label, hint, items) for k, label, hint, items in sections}
    groups = [(title, hint, [by_key[k] for k in keys if k in by_key]) for title, hint, keys in leaddb.TAB_GROUPS]
    return render_template("leads.html", account=g.account, tab=tab, tabs=leaddb.TABS, sections=sections,
                           sched=schedule_view(),
                           groups=[grp for grp in groups if grp[2]], tab_groups=leaddb.TAB_GROUPS,
                           st=status_payload(), rd=run_defaults(), demo=SETTINGS["DEMO_MODE"])


@bp.get("/leads/suche")
@login_required
def search():
    """Neue Betriebe finden: Lauf starten (Kartensuche und Bewertung) oder mit Claude recherchieren."""
    return render_template("search.html", account=g.account, st=status_payload(), rd=run_defaults(),
                           sched=schedule_view(), demo=SETTINGS["DEMO_MODE"])


def schedule_view():
    """Für die Anzeige: je Art die Einstellung, Kurzfassung, letzter und nächster Start."""
    c = db()
    state = leaddb.kv_get(c, "schedule_state", {}) or {}
    now = datetime.now()
    out = {}
    for kind, cfg in schedule.load(c).items():
        st = state.get(kind) or {}
        nxt = schedule.next_start(cfg, now, st.get("last"))
        out[kind] = {"cfg": cfg, "label": schedule.KINDS[kind], "summary": schedule.describe(cfg),
                     "last": st.get("last"), "note": st.get("note"), "next": nxt.isoformat(timespec="minutes") if nxt else None}
    return out


@bp.get("/leads/automatik")
@login_required
def automatic():
    from .settings_views import is_admin
    return render_template("automatik.html", account=g.account, sched=schedule_view(), days=schedule.DAYS,
                           min_every=schedule.MIN_EVERY, st=status_payload(), admin=is_admin(g.account),
                           today_eur=money(leaddb._sum(db(), "at >= ?", (datetime.now().strftime("%Y-%m-%d"),))["usd"]),
                           demo=SETTINGS["DEMO_MODE"])


@bp.post("/leads/automatik/<kind>")
@login_required
def automatic_save(kind):
    from .settings_views import is_admin
    if kind not in schedule.KINDS:
        abort(404)
    if not is_admin(g.account):
        abort(403, "Nur Admins können den Zeitplan ändern.")
    try:
        cfg = schedule.parse(kind, request.form)
    except schedule.ScheduleError as exc:
        flash(f"Nicht gespeichert: {exc}")
        return redirect(url_for("leads.automatic"))
    if kind == "ki" and cfg["enabled"] and not ai.available():
        flash("Nicht gespeichert: Für die Suche mit KI fehlt der KI-Schlüssel (Einstellungen → KI und Budget).")
        return redirect(url_for("leads.automatic"))
    schedule.save(db(), kind, cfg)
    agent.log.info("Zeitplan „%s“ geändert von %s: %s", kind, who(), schedule.describe(cfg))
    flash(f"{schedule.KINDS[kind]}: {schedule.describe(cfg)}. Gespeichert.")
    agent.wake()
    return redirect(url_for("leads.automatic"))


@bp.get("/leads/status.json")
@login_required
def status_json():
    return jsonify(status_payload())


@bp.post("/leads/run")
@login_required
def run():
    try:
        params = {"bewerten": max(0, min(1000, int(request.form.get("bewerten", 0)))),
                  "top": max(0, min(200, int(request.form.get("top", SETTINGS["LEADS_RUN_TOP"])))),
                  "entwuerfe": max(0, min(50, int(request.form.get("entwuerfe", 0)))),
                  "budget_eur": max(0.0, min(100.0, float(request.form.get("budget_eur", "0").replace(",", ".") or 0))),
                  "suchen": request.form.get("suchen") == "1",
                  "erinnerungen": request.form.get("erinnerungen") == "1"}
    except ValueError:
        return jsonify(error="Bitte Zahlen eingeben."), 400
    run_id, error = agent.start_run(params, who())
    if error:
        return jsonify(error=error), 400
    return jsonify(ok=True, run_id=run_id, message="Lauf gestartet. Du bekommst Bescheid, wenn er fertig ist.")


@bp.post("/leads/stop")
@login_required
def stop():
    agent.stop_run()
    return jsonify(ok=True, message="Wird nach dem aktuellen Schritt gestoppt.")


@bp.get("/leads/schaetzung")
@login_required
def estimate():
    try:
        drafts = int(request.args.get("entwuerfe", 0))
    except ValueError:
        abort(400)
    return jsonify(agent.estimate(db(), max(0, drafts), request.args.get("erinnerungen") == "1"))


@bp.get("/leads/kosten")
@login_required
def costs():
    c = db()
    runs = []
    for r in c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 30").fetchall():
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
                           eur_rate=ai.EUR_PER_USD, models=ai.MODELS, stage_labels=ai.STAGE_LABELS,
                           demo=SETTINGS["DEMO_MODE"])

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
                           tab=leaddb.tab_of(lead), tabs=leaddb.TABS, demo=SETTINGS["DEMO_MODE"])


@bp.get("/leads/<int:lead_id>/preview")
@login_required
def preview(lead_id):
    lead = lead_or_404(lead_id)
    rcpt = {"name": lead["contact_name"] or "", "first": "", "email": lead["email"] or ""}
    html, _, _ = emailbuild.render(agent.lead_form(lead), rcpt, SETTINGS)
    resp = Response(html, mimetype="text/html")
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    return resp


@bp.get("/leads/<int:lead_id>/brief")
@login_required
def letter(lead_id):
    """Druckansicht als Brief (DIN 5008 light), falls der Erstkontakt per Post läuft."""
    lead = lead_or_404(lead_id)
    return render_template("letter.html", lead=lead, paragraphs=emailbuild.paragraphs(lead["body"]),
                           sender_name=SETTINGS["LEADS_SENDER_NAME"], footer=SETTINGS["DEFAULT_FOOTER"],
                           org=SETTINGS["ORG_NAME"], today=datetime.now().strftime("%d.%m.%Y"))


def _draft_from_form():
    return {k: request.form.get(k, "").replace("\r\n", "\n").strip() for k in ("subject", "greeting", "body")}


@bp.post("/leads/<int:lead_id>/save")
@login_required
def save(lead_id):
    return jsonify(ok=True, changed=_save_draft(lead_or_404(lead_id)))


def _save_draft(lead):
    """Übernimmt den mitgeschickten Entwurf. Gibt True zurück, wenn sich etwas geändert hat."""
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
    before = leaddb._sum(db(), "lead_id = ? AND stage = 'chat'", (lead_id,))["usd"]
    try:
        result = ai.revise(leaddb.brain(db()), lead,
                           {"subject": lead["subject"] or "", "greeting": lead["greeting"] or "", "body": lead["body"] or ""},
                           chat_events[:-1], text)
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


@bp.post("/leads/<int:lead_id>/send")
@login_required
def send(lead_id):
    _save_draft(lead_or_404(lead_id))
    lead = lead_or_404(lead_id)
    if lead["status"] not in ("entwurf", "passt", "aussortiert"):
        return jsonify(error="Diese Nachricht ist schon raus."), 400
    if not (lead["body"] or "").strip():
        return jsonify(error="Es gibt noch keinen Text."), 400
    if g.account.get("demo"):
        leaddb.update_lead(db(), lead_id, status="versendet", channel="email", sent_at=leaddb.now())
        leaddb.add_event(db(), lead_id, "sent", "Demo: als versendet markiert (nichts verschickt).", author=who())
        crm.advance(db(), lead_id, "kontaktiert")
        return jsonify(ok=True)
    try:
        agent.send_outreach(db(), lead, who())
    except Exception as exc:
        return jsonify(error=str(exc)), 502
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
    channel = request.form.get("channel")
    labels = {"brief": "per Brief", "telefon": "am Telefon", "email": "per Mail (selbst verschickt)"}
    if channel not in labels:
        abort(400)
    leaddb.update_lead(db(), lead_id, status="versendet", channel=channel, sent_at=leaddb.now(), unread=0)
    leaddb.add_event(db(), lead_id, "sent", f"Kontakt {labels[channel]} aufgenommen.", author=who())
    crm.advance(db(), lead_id, "kontaktiert")
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

REVIEW_SQL = ("SELECT * FROM leads WHERE status='passt' AND stage IS NULL AND interest IS NULL "
              "AND IFNULL(rejected_by,'')='' ORDER BY fit_score DESC, distance_km LIMIT ?")


def review_queue(limit=30):
    out = []
    for r in db().execute(REVIEW_SQL, (limit,)).fetchall():
        lead = dict(r)
        lead["p"] = crm.profile(lead)
        research = leaddb.get_lead(db(), lead["id"])["research"]
        lead["signale"] = sorted(research.get("signale") or [], key=lambda x: -x[0])[:5]
        out.append(lead)
    return out


def review_count():
    return db().execute("SELECT COUNT(*) FROM leads WHERE status='passt' AND stage IS NULL AND interest IS NULL "
                        "AND IFNULL(rejected_by,'')=''").fetchone()[0]


@bp.get("/leads/pruefen")
@login_required
def review():
    return render_template("review.html", account=g.account, queue=review_queue(), total=review_count(),
                           st=status_payload(), demo=SETTINGS["DEMO_MODE"])


@bp.post("/leads/<int:lead_id>/urteil")
@login_required
def verdict(lead_id):
    """Gut: als „Neu“ ins CRM. Nicht gut: aussortieren (der Algorithmus lernt aus beidem)."""
    lead = lead_or_404(lead_id)
    good = request.form.get("gut") == "1"
    if good:
        crm.advance(db(), lead_id, "neu", author=who())
        leaddb.update_lead(db(), lead_id, unread=0)
        leaddb.add_event(db(), lead_id, "status", "Beim Prüfen als gut markiert, ins CRM.", author=who())
        leaddb.add_signal(db(), "gut", f"Das Team findet {lead['name']} ({lead['branche']} / {lead['category_label']}, "
                          f"{lead['fit_score']} Punkte) passend.", lead_id)
    else:
        _reject(lead, request.form.get("reason", "").strip() or "Beim Prüfen als nicht gut markiert.")
    return jsonify(ok=True, left=review_count())


@bp.post("/leads/<int:lead_id>/urteil/zurueck")
@login_required
def verdict_undo(lead_id):
    lead = lead_or_404(lead_id)
    leaddb.update_lead(db(), lead_id, status="passt", rejected_by=None, stage=None, stage_at=leaddb.now())
    leaddb.add_event(db(), lead_id, "status", "Entscheidung beim Prüfen rückgängig gemacht.", author=who())
    db().execute("DELETE FROM signals WHERE lead_id=? AND processed=0 AND kind IN ('gut','aussortiert')", (lead_id,))
    db().commit()
    return jsonify(ok=True, name=lead["name"], left=review_count())


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


@bp.post("/leads/<int:lead_id>/retry")
@login_required
def retry(lead_id):
    """Neu nach Regeln bewerten (kostenlos). Geht nur, solange noch nichts verschickt ist."""
    lead = lead_or_404(lead_id)
    if lead["status"] not in ("kandidat", "passt", "aussortiert", "fehler"):
        return jsonify(error="Der Betrieb ist schon kontaktiert, eine neue Bewertung ändert nichts mehr."), 400
    if not lead["website"]:
        return jsonify(error="Ohne Webseite gibt es nichts zu bewerten."), 400
    if not SETTINGS["LEADS_WORKER"]:
        return jsonify(error="Der Hintergrund-Arbeiter ist aus (LEADS_WORKER=false)."), 400
    leaddb.kv_set(db(), "request:lead", lead_id)
    agent.wake()
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
    before = leaddb._sum(db(), "lead_id = ? AND stage = 'entwurf'", (lead_id,))["usd"]
    try:
        agent.stage_write(db(), lead_id, leaddb.brain(db()))
    except ai.AIError as exc:
        return jsonify(error=str(exc)), 502
    spent = leaddb._sum(db(), "lead_id = ? AND stage = 'entwurf'", (lead_id,))["usd"] - before
    return jsonify(ok=True, cost_eur=money(spent))


@bp.post("/leads/<int:lead_id>/entwurf/vorlage")
@login_required
def draft_template(lead_id):
    """Entwurf aus der Vorlage im Gehirn, ohne KI."""
    lead = lead_or_404(lead_id)
    if lead["status"] not in ("passt", "entwurf", "aussortiert", "fehler") or lead["interest"]:
        return jsonify(error="Für diesen Betrieb wird kein neuer Erstkontakt geschrieben."), 400
    try:
        agent.template_draft(db(), lead_id)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True)


@bp.get("/leads/regeln")
@login_required
def rules():
    return render_template("rules.html", account=g.account, rules=scoring.rules_table(), ko_rules=scoring.KO_RULES,
                           base=scoring.BASE, top=SETTINGS["LEADS_RUN_TOP"],
                           threshold=SETTINGS["LEADS_RULE_MIN"], st=status_payload(), demo=SETTINGS["DEMO_MODE"])

# ---------------------------------------------------------------- Recherche mit Claude (ohne API-Tokens)


@bp.get("/leads/claude")
@login_required
def claude():
    try:
        count = max(5, min(200, int(request.args.get("anzahl", 40))))
    except ValueError:
        count = 40
    branche = request.args.get("branche", "")
    if branche not in claude_research.BRANCHEN:
        branche = ""
    brain = leaddb.brain(db())
    template = leaddb.brain_sections(brain, [agent.TEMPLATE_HEADING])
    prompt = claude_research.build_prompt(brain, SETTINGS, count, branche, claude_research.known_hosts(db()),
                                          template)
    waiting = db().execute("SELECT COUNT(*) FROM leads WHERE status='kandidat' AND source_id LIKE 'claude:%'"
                           ).fetchone()[0]
    return render_template("claude.html", account=g.account, prompt=prompt, count=count, branche=branche,
                           branchen=claude_research.BRANCHEN, columns=claude_research.COLUMNS,
                           used=claude_research.placeholders(template), own=claude_research.OWN_PLACEHOLDERS,
                           waiting=waiting, filename=claude_research.FILENAME, st=status_payload(),
                           demo=SETTINGS["DEMO_MODE"])


@bp.post("/leads/claude/import")
@login_required
def claude_import():
    file = request.files.get("file")
    raw = file.read() if file and file.filename else request.form.get("text", "").encode()
    if not raw.strip():
        flash("Bitte eine CSV-Datei auswählen oder den Inhalt einfügen.")
        return redirect(url_for("leads.claude"))
    try:
        result = claude_research.import_csv(db(), raw, who(), SETTINGS["LEADS_CENTER"])
    except claude_research.CsvError as exc:
        flash(f"Nicht übernommen: {exc}")
        return redirect(url_for("leads.claude"))
    msg = f"{result['neu']} Betriebe übernommen."
    if result["uebersprungen"]:
        msg += f" {len(result['uebersprungen'])} übersprungen: " + "; ".join(
            f"Zeile {line}: {reason}" for line, reason in result["uebersprungen"][:8])
    if result["neu"]:
        msg += " Sie werden im nächsten Lauf zuerst bewertet (kostenlos, ohne neue Suche)."
    flash(msg)
    return redirect(url_for("leads.claude"))

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
    before = leaddb._sum(db(), "lead_id = ? AND stage = 'erinnerung'", (lead_id,))["usd"]
    try:
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
    if g.account.get("demo"):
        leaddb.update_lead(db(), lead_id, followup_count=1, followup_sent_at=leaddb.now())
        leaddb.add_event(db(), lead_id, "sent", "Demo: Erinnerung als versendet markiert (nichts verschickt).", author=who())
        return jsonify(ok=True)
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
        "SELECT id, at, author, reason FROM brain_versions ORDER BY id DESC LIMIT 30")]
    pending = db().execute("SELECT COUNT(*) FROM signals WHERE processed=0").fetchone()[0]
    return render_template("brain.html", account=g.account, content=leaddb.brain(db()), versions=versions,
                           stats=leaddb.branche_stats(db()), pending=pending, saved=request.args.get("saved"),
                           st=status_payload(), demo=SETTINGS["DEMO_MODE"])


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
    row = db().execute("SELECT content FROM brain_versions WHERE id=?", (version_id,)).fetchone()
    if not row:
        abort(404)
    return Response(row["content"], mimetype="text/plain; charset=utf-8")

# ---------------------------------------------------------------- Systemcheck


@bp.get("/leads/system")
@login_required
def system():
    from . import main, systemcheck
    results = systemcheck.run(SETTINGS, main.smtp_connect, main.imap_sent_folder, SETTINGS["DATA_DIR"])
    return render_template("system.html", account=g.account, results=results, st=status_payload(),
                           demo=SETTINGS["DEMO_MODE"])

# ---------------------------------------------------------------- Demo


@bp.post("/leads/demo")
@login_required
def demo_seed():
    if not SETTINGS["DEMO_MODE"]:
        abort(404)
    from . import leads_demo
    leads_demo.seed(db())
    return redirect(url_for("leads.overview"))

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
        assert sub["endpoint"].startswith("https://") and sub["keys"]["p256dh"] and sub["keys"]["auth"]
    except Exception:
        return jsonify(error="Ungültiges Abo."), 400
    db().execute("INSERT INTO push_subs (endpoint, data, user, created_at) VALUES (?,?,?,?) "
                 "ON CONFLICT(endpoint) DO UPDATE SET data=excluded.data, user=excluded.user",
                 (sub["endpoint"], json.dumps(sub), who(), leaddb.now()))
    db().commit()
    return jsonify(ok=True)


@bp.post("/push/unsubscribe")
@login_required
def push_unsubscribe():
    db().execute("DELETE FROM push_subs WHERE endpoint=?", (request.form.get("endpoint", ""),))
    db().commit()
    return jsonify(ok=True)


@bp.post("/push/test")
@login_required
def push_test():
    count = db().execute("SELECT COUNT(*) FROM push_subs").fetchone()[0]
    if not count:
        return jsonify(error="Noch kein Gerät angemeldet."), 400
    agent.notify(db(), "Test", "Benachrichtigungen funktionieren.", "/leads", mail=False)
    return jsonify(ok=True, devices=count)
