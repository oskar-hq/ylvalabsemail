"""Oberfläche der KI-Akquise: Übersicht, Firma im Detail mit Chat, Gehirn, Web-App und Push."""
import json
import time
from datetime import datetime

from flask import Blueprint, Response, abort, g, jsonify, redirect, render_template, request, send_from_directory, url_for

from . import agent, ai, emailbuild, leaddb
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


@bp.app_template_filter("kurzdatum")
def _kurzdatum(value):
    return fmt_date(value)


@bp.app_context_processor
def _leads_globals():
    return {"lead_status_labels": leaddb.STATUS_LABELS, "interest_labels": leaddb.INTEREST_LABELS,
            "tab_of": leaddb.tab_of}


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
        "last_search": leaddb.kv_get(c, "last_search"),
        "run": dict(run, params=json.loads(run["params"])) if run else None,
        "run_eur": money(run_cost["usd"]) if run_cost else 0, "run_tokens": (run_cost["tin"] + run_cost["tout"]) if run_cost else 0,
        "today_eur": money(costs["today"]["usd"]), "month_eur": money(costs["month"]["usd"]),
        "month_tokens": costs["month"]["tin"] + costs["month"]["tout"], "cap_eur": cap,
        "last_run": costs["last_run"], "last_run_eur": money(costs["last_run_cost"]["usd"]) if costs["last_run_cost"] else None,
    }


def run_defaults():
    est = agent.estimate(db(), SETTINGS["LEADS_RUN_CHECK"], SETTINGS["LEADS_RUN_DRAFTS"])
    return {"check": SETTINGS["LEADS_RUN_CHECK"], "drafts": SETTINGS["LEADS_RUN_DRAFTS"],
            "budget": SETTINGS["LEADS_RUN_BUDGET_EUR"], "estimate": est, "models": ai.MODELS,
            "factor": agent.ANALYSE_FACTOR}

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
    return render_template("leads.html", account=g.account, tab=tab, tabs=leaddb.TABS, sections=sections,
                           st=status_payload(), rd=run_defaults(), demo=SETTINGS["DEMO_MODE"])


@bp.get("/leads/status.json")
@login_required
def status_json():
    return jsonify(status_payload())


@bp.post("/leads/run")
@login_required
def run():
    try:
        params = {"vorpruefen": max(1, min(500, int(request.form.get("vorpruefen", 0)))),
                  "entwuerfe": max(0, min(50, int(request.form.get("entwuerfe", 0)))),
                  "budget_eur": max(0.05, min(100.0, float(request.form.get("budget_eur", "0").replace(",", ".")))),
                  "suchen": request.form.get("suchen") == "1"}
    except ValueError:
        return jsonify(error="Bitte Zahlen eingeben."), 400
    run_id, error = agent.start_run(params, who())
    if error:
        return jsonify(error=error), 400
    return jsonify(ok=True, run_id=run_id, message="Lauf gestartet. Ihr bekommt Bescheid, wenn er fertig ist.")


@bp.post("/leads/stop")
@login_required
def stop():
    agent.stop_run()
    return jsonify(ok=True, message="Wird nach dem aktuellen Schritt gestoppt.")


@bp.get("/leads/schaetzung")
@login_required
def estimate():
    try:
        check, drafts = int(request.args.get("vorpruefen", 0)), int(request.args.get("entwuerfe", 0))
    except ValueError:
        abort(400)
    return jsonify(agent.estimate(db(), max(0, check), max(0, drafts)))


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
    return render_template("lead.html", account=g.account, lead=lead, events=events, st=status_payload(),
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
    return {k: request.form.get(k, "").strip() for k in ("subject", "greeting", "body")}


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
    leaddb.update_lead(db(), lead_id, **draft)
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
    if lead["status"] not in ("entwurf", "aussortiert"):
        return jsonify(error="Diese Nachricht ist schon raus."), 400
    if g.account.get("demo"):
        leaddb.update_lead(db(), lead_id, status="versendet", channel="email", sent_at=leaddb.now())
        leaddb.add_event(db(), lead_id, "sent", "Demo: als versendet markiert (nichts verschickt).", author=who())
        return jsonify(ok=True)
    try:
        agent.send_outreach(db(), lead, who())
    except Exception as exc:
        return jsonify(error=str(exc)), 502
    _approved_signal(lead, "per Mail")
    return jsonify(ok=True)


def _approved_signal(lead, channel):
    leaddb.add_signal(db(), "freigabe", f"Das Team hat die Nachricht an {lead['name']} ({lead['branche']} / "
                      f"{lead['category_label']}, Score {lead['fit_score']}) {channel} freigegeben.", lead["id"])


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
    _approved_signal(lead, labels[channel])
    return jsonify(ok=True)


@bp.post("/leads/<int:lead_id>/reject")
@login_required
def reject(lead_id):
    lead = lead_or_404(lead_id)
    reason = request.form.get("reason", "").strip()
    leaddb.update_lead(db(), lead_id, status="aussortiert", rejected_by="team", unread=0)
    leaddb.add_event(db(), lead_id, "status", f"Vom Team aussortiert. {reason}".strip(), author=who())
    leaddb.add_signal(db(), "aussortiert", f"Das Team hält {lead['name']} ({lead['branche']} / {lead['category_label']}, "
                      f"{lead['distance_km']} km, KI-Score {lead['fit_score']}) für ungeeignet. Grund: {reason or 'nicht genannt'}. "
                      f"Einschätzung der KI war: {lead['fit_reason']}", lead_id)
    return jsonify(ok=True)


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
    lead_or_404(lead_id)
    leaddb.update_lead(db(), lead_id, status="kandidat", rejected_by=None)
    if not ai.available():
        return jsonify(error="Ohne KI-Schlüssel kann nichts geprüft werden."), 400
    leaddb.kv_set(db(), "request:lead", lead_id)
    agent.wake()
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
