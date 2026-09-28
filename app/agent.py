"""Der Hintergrund-Arbeiter der KI-Akquise.

Läuft als Thread im Mailer-Prozess (Gunicorn mit genau einem Worker).
Von sich aus liest er nur das Postfach. Alles, was Tokens kostet, passiert in einem Lauf, den ihr von Hand
startet, mit Budgetgrenze. Ein Lauf arbeitet sich von billig nach teuer vor, damit nur die besten Betriebe
die teure Analyse bekommen:

1. Suchen (OpenStreetMap) und Grobfilter nach Regeln: kostenlos.
2. Vorprüfung der Startseite mit dem günstigsten Modell.
3. Gründliche Analyse mit Unterseiten mit dem mittleren Modell, nur für die Besten aus Stufe 2.
4. Entwurf mit dem stärksten Modell, nur für die Besten aus Stufe 3.
Zum Schluss lernt er aus den Rückmeldungen. Versendet wird nie automatisch.
"""
import email
import email.policy
import imaplib
import json
import logging
import random
import re
import ssl
import threading
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate, parseaddr
from urllib.parse import urlparse

from . import ai, emailbuild, finder, leaddb, webpush

log = logging.getLogger("ylva.leads")

CFG = {}
STATE = {"activity": "Startet …", "since": time.time(), "error": None, "error_at": None, "running": False}
_wake = threading.Event()
_job_lock = threading.Lock()
_vapid = None

CORE_BRANCHEN = ("handwerk", "pflege", "landwirtschaft")
EXPLORE = 0.15  # Anteil zufällig gewählter Betriebe, damit die Suche nicht festfährt
FREEMAIL = {"gmail.com", "googlemail.com", "web.de", "gmx.de", "gmx.net", "t-online.de", "outlook.com",
            "outlook.de", "hotmail.com", "hotmail.de", "yahoo.com", "yahoo.de", "icloud.com", "me.com",
            "aol.com", "freenet.de", "posteo.de", "mailbox.org", "live.de", "online.de", "arcor.de"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def init(settings, smtp_connect, sent_folder, db_path, data_dir):
    CFG.update(settings=settings, smtp_connect=smtp_connect, sent_folder=sent_folder, db_path=db_path,
               data_dir=data_dir)
    ai.on_usage = _record_usage
    leaddb.FOLLOWUP_DAYS = settings["LEADS_FOLLOWUP_DAYS"]
    if settings["LEADS_WORKER"] and not STATE["running"]:
        STATE["running"] = True
        threading.Thread(target=_loop, name="leads-worker", daemon=True).start()


def conn():
    return leaddb.connect(CFG["db_path"])


def S(key):
    return CFG["settings"][key]


def set_activity(text):
    STATE["activity"], STATE["since"] = text, time.time()


def outreach_configured():
    return bool(S("OUTREACH_USER") and S("OUTREACH_PASSWORD"))


def outreach_address():
    user = S("OUTREACH_USER")
    return user if "@" in user else S("FROM_ADDRESS")


def status():
    """Für die Anzeige oben auf der Leads-Seite."""
    warnings = []
    if not ai.available():
        warnings.append("Kein KI-Schlüssel: ANTHROPIC_API_KEY in der .env eintragen, sonst wird nichts recherchiert.")
    if not outreach_configured():
        warnings.append("Kein Akquise-Postfach: OUTREACH_USER und OUTREACH_PASSWORD eintragen, "
                        "sonst werden keine Antworten gelesen und keine Mails verschickt.")
    elif not S("IMAP_HOST"):
        warnings.append("IMAP_HOST fehlt: Antworten der Firmen können nicht gelesen werden.")
    if not S("LEADS_WORKER"):
        warnings.append("Der Hintergrund-Arbeiter ist aus (LEADS_WORKER=false).")
    return {**STATE, "run_id": RUN["id"], "progress": RUN["progress"], "warnings": warnings, "email_enabled": S("OUTREACH_EMAIL") and outreach_configured()}

# ---------------------------------------------------------------- Benachrichtigungen


def vapid():
    global _vapid
    if _vapid is None:
        _vapid = webpush.Vapid(str(CFG["data_dir"] / "vapid_private.pem"))
    return _vapid


def notify(c, title, body, url="/leads", mail=True):
    subject = "mailto:" + (S("NOTIFY_EMAILS")[0] if S("NOTIFY_EMAILS") else "info@ylvalabs.de")
    for row in c.execute("SELECT endpoint, data FROM push_subs").fetchall():
        try:
            if not webpush.send(vapid(), json.loads(row["data"]), {"title": title, "body": body, "url": url}, subject):
                c.execute("DELETE FROM push_subs WHERE endpoint=?", (row["endpoint"],))
                c.commit()
        except Exception as exc:
            log.warning("Push an %s fehlgeschlagen: %s", row["endpoint"][:60], exc)
    if mail and S("NOTIFY_EMAILS") and outreach_configured():
        try:
            msg = EmailMessage()
            msg["Subject"] = f"[Leads] {title}"
            msg["From"] = formataddr(("Ylva Labs · Leads", outreach_address()))
            msg["To"] = ", ".join(S("NOTIFY_EMAILS"))
            msg["Date"] = formatdate(localtime=True)
            link = (S("BASE_URL").rstrip("/") + url) if S("BASE_URL") else url
            msg.set_content(f"{body}\n\nAnsehen: {link}\n")
            smtp = CFG["smtp_connect"](S("OUTREACH_USER"), S("OUTREACH_PASSWORD"))
            try:
                smtp.send_message(msg)
            finally:
                smtp.quit()
        except Exception as exc:
            log.warning("Benachrichtigung per Mail fehlgeschlagen: %s", exc)

# ---------------------------------------------------------------- Stufe 1: Firmen suchen und grob filtern (kostenlos)


class SearchError(Exception):
    pass


def run_search(c):
    lat, lon = S("LEADS_CENTER")
    set_activity(f"Stufe 1/4 · Sucht Betriebe im Umkreis von {S('LEADS_RADIUS_KM'):g} km (kostenlos) …")
    try:
        found = finder.search_osm(lat, lon, S("LEADS_RADIUS_KM"), S("OVERPASS_URL"))
    except Exception as exc:
        raise SearchError(f"Die Kartensuche (OpenStreetMap) ist gerade nicht erreichbar ({exc}). "
                          "Bitte später erneut starten; es sind keine Kosten entstanden.") from exc
    new = filtered = 0
    for f in found:
        reason = f.get("filter_reason")
        cur = c.execute(
            "INSERT OR IGNORE INTO leads (source_id, created_at, updated_at, name, branche, category, category_label,"
            " address, city, lat, lon, distance_km, website, email, phone, status, rejected_by, fit_reason)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f["source_id"], leaddb.now(), leaddb.now(), f["name"], f["branche"], f["category"], f["category_label"],
             f["address"], f["city"], f["lat"], f["lon"], f["distance_km"], f["website"], f["email"], f["phone"],
             "aussortiert" if reason else "kandidat", "filter" if reason else None,
             f"Grobfilter: {reason}" if reason else None))
        new += cur.rowcount
        filtered += cur.rowcount if reason else 0
    c.commit()
    leaddb.kv_set(c, "last_search", {"at": leaddb.now(), "found": len(found), "new": new, "filtered": filtered})
    return new, filtered


def learned_filter(c):
    """Auch kostenlos: Branchen, die schon mindestens fünfmal nicht gepasst haben und noch nie, fallen raus.
    Gesperrte Adressen ebenso."""
    removed = 0
    for r in c.execute("""
            SELECT category, category_label FROM leads
            WHERE status NOT IN ('kandidat','recherche') AND IFNULL(rejected_by,'') != 'filter' AND category != ''
            GROUP BY category
            HAVING IFNULL(SUM(interest IN ('qualified','mittel') OR status IN ('entwurf','versendet','analysiert')), 0) = 0
               AND IFNULL(SUM(rejected_by IN ('ki','team') OR interest='kein_interesse'), 0) >= 5""").fetchall():
        removed += c.execute(
            "UPDATE leads SET status='aussortiert', rejected_by='filter', updated_at=?, fit_reason=? "
            "WHERE status='kandidat' AND category=?",
            (leaddb.now(), f"Grobfilter (gelernt): {r['category_label']} hat bisher fünfmal nicht gepasst.",
             r["category"])).rowcount
    blocked = set(leaddb.kv_get(c, "blocklist", []))
    for r in c.execute("SELECT id, email, website FROM leads WHERE status='kandidat'").fetchall():
        if (_domain(r["email"]) or _host(r["website"])) in blocked:
            leaddb.update_lead(c, r["id"], status="gesperrt", rejected_by="filter",
                               fit_reason="Adresse steht auf der Sperrliste.")
            removed += 1
    c.commit()
    return removed


def _beta(pos, neg):
    return random.betavariate(1 + pos, 1 + neg)


def next_candidate(c):
    """Welche Firma als Nächstes? Branchen, die bisher gut liefen, kommen öfter dran (Thompson-Sampling),
    und ab und zu wird ganz bewusst etwas anderes ausprobiert, damit die Suche nicht festfährt."""
    stats = {}
    for r in c.execute("""
            SELECT branche, category,
                   SUM(interest='qualified') q, SUM(interest='mittel') m,
                   SUM(interest='kein_interesse' OR status='gesperrt') k,
                   SUM(rejected_by='team') rt, SUM(rejected_by='ki') rk,
                   SUM(status IN ('entwurf','versendet','beantwortet','analysiert') AND rejected_by IS NULL) ok
            FROM leads WHERE status NOT IN ('kandidat','recherche') AND IFNULL(rejected_by,'') != 'filter'
            GROUP BY branche, category""").fetchall():
        for key in (("b", r["branche"]), ("c", r["category"])):
            s = stats.setdefault(key, [0.0, 0.0])
            s[0] += 3 * (r["q"] or 0) + 1.5 * (r["m"] or 0) + 0.5 * (r["ok"] or 0)
            s[1] += (r["k"] or 0) + (r["rt"] or 0) + 0.5 * (r["rk"] or 0)
    rows = c.execute("SELECT id, branche, category, distance_km FROM leads WHERE status='kandidat'").fetchall()
    if rows and random.random() < EXPLORE:
        return random.choice(rows)["id"]
    best, best_score = None, -1e9
    for r in rows:
        pb, nb = stats.get(("b", r["branche"]), (0, 0))
        pc, nc = stats.get(("c", r["category"]), (0, 0))
        score = 0.6 * _beta(pb, nb) + 0.4 * _beta(pc, nc)
        score += 0.08 if r["branche"] in CORE_BRANCHEN else 0
        score -= (r["distance_km"] or 0) / 1000
        if score > best_score:
            best, best_score = r["id"], score
    return best


def _host(url):
    return urlparse(url or "").netloc.lower().removeprefix("www.")


def _domain(addr):
    return addr.rsplit("@", 1)[-1].lower() if addr and "@" in addr else ""


def _json(data):
    return json.dumps(data, ensure_ascii=False)

# ---------------------------------------------------------------- Stufe 2–4: KI, von günstig nach stark


def stage_quick(c, lead_id, brain_short):
    """Stufe 2: nur die Startseite, günstigstes Modell."""
    lead = leaddb.get_lead(c, lead_id)
    leaddb.update_lead(c, lead_id, status="recherche")
    research = finder.research_website(lead["website"], max_pages=1, chars_per_page=2500)
    if not research["pages"]:
        leaddb.update_lead(c, lead_id, status="aussortiert", rejected_by="filter",
                           fit_reason="Grobfilter: Webseite nicht lesbar. " + "; ".join(research["errors"])[:200])
        return False
    try:
        result = ai.quick_check(leaddb.brain_sections(leaddb.brain(c), ["Wen wir suchen", "Gelernt"])
                                if brain_short is None else brain_short, lead, research)
    except ai.AIError:
        leaddb.update_lead(c, lead_id, status="kandidat")  # beim nächsten Lauf noch einmal
        raise
    fields = dict(quick_score=result["score"], quick_reason=result["grund"])
    if result["score"] < S("LEADS_QUICK_MIN"):
        leaddb.update_lead(c, lead_id, status="aussortiert", rejected_by="ki", fit_reason=f"Vorprüfung: {result['grund']}",
                           **fields)
        leaddb.add_event(c, lead_id, "status", f"Vorprüfung ({ai.MODELS['vorpruefung']}): {result['score']}/100, "
                         f"aussortiert. {result['grund']}")
        return False
    leaddb.update_lead(c, lead_id, status="vorgeprueft", **fields)
    leaddb.add_event(c, lead_id, "status", f"Vorprüfung ({ai.MODELS['vorpruefung']}): {result['score']}/100. {result['grund']}")
    return True


def stage_analyse(c, lead_id, brain):
    """Stufe 3: Startseite plus Unterseiten, mittleres Modell."""
    lead = leaddb.get_lead(c, lead_id)
    leaddb.update_lead(c, lead_id, status="recherche")
    research = finder.research_website(lead["website"])
    try:
        result = ai.analyse(brain, lead, research, lead["quick_reason"] or "")
    except ai.AIError:
        leaddb.update_lead(c, lead_id, status="vorgeprueft")
        raise
    known = set(research["emails"]) | ({lead["email"].lower()} if lead["email"] else set())
    chosen = (result.get("email") or "").strip().lower()
    email_addr = chosen if chosen in known else (lead["email"] or (research["emails"][0] if research["emails"] else ""))
    info = {"groesse": result.get("groesse"), "zeitfresser": result.get("zeitfresser", []),
            "aufhaenger": result.get("aufhaenger"), "pages": [p["url"] for p in research["pages"]],
            "emails": research["emails"], "errors": research["errors"]}
    common = dict(fit_score=result["score"], fit_reason=result["begruendung"], research=_json(info),
                  contact_name=result.get("ansprechpartner") or None, email=email_addr or None)

    blocked = set(leaddb.kv_get(c, "blocklist", []))
    if email_addr and (_domain(email_addr) in blocked or email_addr in blocked):
        leaddb.update_lead(c, lead_id, status="gesperrt", **common)
        leaddb.add_event(c, lead_id, "status", "Gesperrt: Diese Adresse möchte nicht kontaktiert werden.")
        return False
    duplicate = email_addr and c.execute(
        "SELECT name FROM leads WHERE id != ? AND email=? AND status IN ('analysiert','entwurf','versendet',"
        "'beantwortet','gesperrt')", (lead_id, email_addr)).fetchone()
    if duplicate:
        leaddb.update_lead(c, lead_id, status="aussortiert", rejected_by="ki", **{
            **common, "fit_reason": f"Doppelt: dieselbe Adresse wie „{duplicate['name']}“."})
        leaddb.add_event(c, lead_id, "status", f"Aussortiert: gleiche Adresse wie „{duplicate['name']}“.")
        return False
    label = f"Analyse ({ai.MODELS['analyse']}): {result['score']}/100."
    if result["passt"] and result["score"] >= S("LEADS_ANALYSE_MIN"):
        leaddb.update_lead(c, lead_id, status="analysiert", **common)
        leaddb.add_event(c, lead_id, "status", f"{label} Passt. {result['begruendung']}")
        return True
    leaddb.update_lead(c, lead_id, status="aussortiert", rejected_by="ki", **common)
    leaddb.add_event(c, lead_id, "status", f"{label} Aussortiert. {result['begruendung']}")
    return False


def stage_write(c, lead_id, brain):
    """Stufe 4: den Entwurf schreiben, stärkstes Modell, nur für die besten."""
    lead = leaddb.get_lead(c, lead_id)
    info = lead["research"]
    analysis = {"begruendung": lead["fit_reason"] or "", "groesse": info.get("groesse"),
                "zeitfresser": info.get("zeitfresser", []), "aufhaenger": info.get("aufhaenger"),
                "ansprechpartner": lead["contact_name"]}
    result = ai.write_draft(brain, lead, analysis)
    leaddb.update_lead(c, lead_id, status="entwurf", unread=1, subject=result["betreff"], greeting=result["anrede"],
                       body=result["text"])
    leaddb.add_event(c, lead_id, "draft", f"Entwurf geschrieben ({ai.MODELS['entwurf']}).")
    return True

# ---------------------------------------------------------------- Läufe (nur von Hand gestartet)

# Grobe Kosten pro Aufruf in US-Dollar, bis genug eigene Messwerte da sind
DEFAULT_COST_USD = {"vorpruefung": 0.004, "analyse": 0.04, "entwurf": 0.06, "chat": 0.06, "antwort": 0.02,
                    "lernen": 0.04, "erinnerung": 0.02}
ANALYSE_FACTOR = 3  # höchstens so viele Analysen pro gewünschtem Entwurf
RUN = {"id": None, "cancel": False, "progress": ""}
_local = threading.local()


class BudgetStop(Exception):
    pass


def avg_cost_usd(c, stage):
    avg, n = leaddb.stage_averages(c).get(stage, (None, 0))
    return avg if n >= 5 and avg else DEFAULT_COST_USD[stage]


def due_followups(c, without_text=False):
    rows = c.execute("SELECT * FROM leads WHERE status='versendet' AND interest IS NULL AND IFNULL(followup_count,0)=0 "
                     "AND ? > 0 AND sent_at <= ? ORDER BY sent_at", (leaddb.FOLLOWUP_DAYS, leaddb.followup_cutoff()))
    return [dict(r) for r in rows if not (without_text and r["followup_body"])]


def estimate(c, check, drafts, followups=True):
    """Was kostet ein Lauf ungefähr? Aus den bisherigen Durchschnittswerten."""
    q_rate, a_rate = leaddb.pass_rates(c)
    # Analysiert wird nur, bis genug Entwürfe beisammen sind
    analyses = min(check * q_rate, drafts / max(a_rate, 0.1), drafts * ANALYSE_FACTOR)
    written = min(analyses * a_rate, drafts)
    usd = (check * avg_cost_usd(c, "vorpruefung") + analyses * avg_cost_usd(c, "analyse")
           + written * avg_cost_usd(c, "entwurf") + avg_cost_usd(c, "lernen"))
    reminders = len(due_followups(c, without_text=True)) if followups else 0
    usd += reminders * avg_cost_usd(c, "erinnerung")
    return {"eur": ai.eur(usd), "analysen": round(analyses), "entwuerfe": round(written), "erinnerungen": reminders,
            "per_stage_eur": {s: ai.eur(avg_cost_usd(c, s)) for s in DEFAULT_COST_USD},
            "q_rate": q_rate, "a_rate": a_rate}


def month_budget_left_eur(c):
    cap = S("LEADS_MONTHLY_BUDGET_EUR")
    return None if not cap else cap - ai.eur(leaddb.month_cost_usd(c))


def check_budget(c, run_id, budget_eur, stage):
    if RUN["cancel"]:
        raise BudgetStop("Von Hand gestoppt.")
    nxt = ai.eur(avg_cost_usd(c, stage))
    spent = ai.eur(leaddb._sum(c, "run_id = ?", (run_id,))["usd"])
    if spent + nxt > budget_eur:
        raise BudgetStop(f"Budget des Laufs erreicht ({spent:.2f} € von {budget_eur:.2f} €).")
    left = month_budget_left_eur(c)
    if left is not None and nxt > left:
        raise BudgetStop(f"Monatsbudget von {S('LEADS_MONTHLY_BUDGET_EUR'):.2f} € erreicht.")


def _record_usage(stage, model, tokens, cost, lead_id):
    c = conn()
    try:
        leaddb.record_usage(c, stage, model, tokens, cost, getattr(_local, "run_id", None), lead_id)
    finally:
        c.close()


def start_run(params, started_by):
    """Vom Knopf „Lauf starten“. Gibt (run_id, None) oder (None, Fehlertext) zurück."""
    if not ai.available():
        return None, "Ohne KI-Schlüssel (ANTHROPIC_API_KEY) kann nichts geprüft werden."
    if not S("LEADS_WORKER"):
        return None, "Der Hintergrund-Arbeiter ist aus (LEADS_WORKER=false)."
    c = conn()
    try:
        if RUN["id"] or c.execute("SELECT 1 FROM runs WHERE status='wartet'").fetchone():
            return None, "Es läuft schon ein Lauf."
        left = month_budget_left_eur(c)
        if left is not None and left <= 0:
            return None, f"Das Monatsbudget von {S('LEADS_MONTHLY_BUDGET_EUR'):.2f} € ist aufgebraucht."
        cur = c.execute("INSERT INTO runs (started_at, started_by, params, status) VALUES (?,?,?, 'wartet')",
                        (leaddb.now(), started_by, _json(params)))
        c.commit()
        run_id = cur.lastrowid
    finally:
        c.close()
    _wake.set()
    return run_id, None


def stop_run():
    RUN["cancel"] = True
    _wake.set()


def _progress(text):
    RUN["progress"] = text
    set_activity(text)


def execute_run(c, run_id):
    run = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    p = json.loads(run["params"])
    check, drafts, budget = int(p["vorpruefen"]), int(p["entwuerfe"]), float(p["budget_eur"])
    counts = {"neu_gefunden": 0, "grobfilter": 0, "vorgeprueft": 0, "vorpruefung_durch": 0, "analysiert": 0,
              "analyse_durch": 0, "entwuerfe": 0, "erinnerungen": 0}
    RUN.update(id=run_id, cancel=False)
    _local.run_id = run_id
    c.execute("UPDATE runs SET status='laeuft' WHERE id=?", (run_id,))
    c.commit()
    status, message = "fertig", None
    try:
        # Stufe 1: suchen und grob filtern (kostenlos)
        if p.get("suchen") or not c.execute("SELECT 1 FROM leads WHERE status='kandidat'").fetchone():
            counts["neu_gefunden"], counts["grobfilter"] = run_search(c)
        counts["grobfilter"] += learned_filter(c)
        brain = leaddb.brain(c)
        brain_short = leaddb.brain_sections(brain, ["Wen wir suchen", "Gelernt"])

        # Stufe 2: Vorprüfung der Startseite (günstigstes Modell)
        for i in range(check):
            check_budget(c, run_id, budget, "vorpruefung")
            lead_id = next_candidate(c)
            if not lead_id:
                break
            _progress(f"Stufe 2/4 · Vorprüfung {i + 1}/{check}: {leaddb.get_lead(c, lead_id)['name']}")
            counts["vorgeprueft"] += 1
            counts["vorpruefung_durch"] += stage_quick(c, lead_id, brain_short)

        # Stufe 3: gründliche Analyse, nur die besten aus der Vorprüfung (mittleres Modell)
        for n in range(drafts * ANALYSE_FACTOR):
            ready = c.execute("SELECT COUNT(*) FROM leads WHERE status='analysiert'").fetchone()[0]
            if ready >= drafts:
                break
            row = c.execute("SELECT id, name FROM leads WHERE status='vorgeprueft' "
                            "ORDER BY quick_score DESC, distance_km LIMIT 1").fetchone()
            if not row:
                break
            check_budget(c, run_id, budget, "analyse")
            _progress(f"Stufe 3/4 · Analyse {n + 1}: {row['name']}")
            counts["analysiert"] += 1
            counts["analyse_durch"] += stage_analyse(c, row["id"], brain)

        # Stufe 4: Entwürfe, nur für die allerbesten (stärkstes Modell)
        best = c.execute("SELECT id, name FROM leads WHERE status='analysiert' ORDER BY fit_score DESC LIMIT ?",
                         (drafts,)).fetchall()
        for i, row in enumerate(best):
            check_budget(c, run_id, budget, "entwurf")
            _progress(f"Stufe 4/4 · Schreibt Entwurf {i + 1}/{len(best)}: {row['name']}")
            counts["entwuerfe"] += stage_write(c, row["id"], brain)

        # Erinnerungen für Betriebe, die seit einer Woche nicht geantwortet haben (mittleres Modell, kurz)
        if p.get("erinnerungen", True):
            due = due_followups(c, without_text=True)
            for i, lead in enumerate(due):
                check_budget(c, run_id, budget, "erinnerung")
                _progress(f"Erinnerung {i + 1}/{len(due)}: {lead['name']}")
                write_followup(c, lead["id"])
                counts["erinnerungen"] += 1

        # Zum Schluss aus den Rückmeldungen seit dem letzten Lauf lernen
        if c.execute("SELECT 1 FROM signals WHERE processed=0").fetchone():
            check_budget(c, run_id, budget, "lernen")
            maybe_reflect(c, force=True)
    except BudgetStop as exc:
        status, message = "gestoppt", str(exc)
    except (ai.AIError, SearchError) as exc:
        status, message = "fehler", str(exc)
        STATE["error"], STATE["error_at"] = str(exc), time.time()
    except Exception as exc:
        log.exception("Fehler im Lauf %s", run_id)
        status, message = "fehler", f"{exc.__class__.__name__}: {exc}"
        STATE["error"], STATE["error_at"] = message, time.time()
        c.execute("UPDATE leads SET status='fehler', updated_at=? WHERE status='recherche'", (leaddb.now(),))
    finally:
        _local.run_id = None
        cost = ai.eur(leaddb._sum(c, "run_id = ?", (run_id,))["usd"])
        summary = (f"{counts['entwuerfe']} neue Entwürfe, "
                   + (f"{counts['erinnerungen']} Erinnerungen, " if counts["erinnerungen"] else "")
                   + f"{counts['vorgeprueft']} vorgeprüft, "
                   f"{counts['analysiert']} analysiert. Kosten: {cost:.2f} €.".replace(f"{cost:.2f}", f"{cost:.2f}".replace(".", ",")))
        c.execute("UPDATE runs SET ended_at=?, status=?, message=?, counts=? WHERE id=?",
                  (leaddb.now(), status, " ".join(x for x in (message, summary) if x), _json(counts), run_id))
        c.commit()
        RUN.update(id=None, cancel=False, progress="")
        title = {"fertig": "Lauf fertig", "gestoppt": "Lauf gestoppt", "fehler": "Lauf mit Fehler beendet"}[status]
        notify(c, title, " ".join(x for x in (message, summary) if x), "/leads?tab=entwurf")

# ---------------------------------------------------------------- Versand


def lead_form(lead):
    """Die Felder für den Mail-Baukasten des Mailers (Vorlage „Kurz-Mail“)."""
    return {
        "template": "brief", "graphic": "none", "subject": lead["subject"] or "", "preheader": "", "headline": "",
        "greeting": lead["greeting"] or "", "body": lead["body"] or "", "body2": "",
        "closing": "Viele Grüße", "sender_name": S("LEADS_SENDER_NAME"),
        "footer": S("DEFAULT_FOOTER"), "info_label": [], "info_value": [], "cta_text": "", "cta_url": "",
    }


def write_followup(c, lead_id):
    """Erinnerung schreiben (kostet eine kleine KI-Anfrage). Verschickt wird sie erst nach Freigabe."""
    lead = leaddb.get_lead(c, lead_id)
    result = ai.write_followup(leaddb.brain(c), lead, leaddb.FOLLOWUP_DAYS)
    leaddb.update_lead(c, lead_id, followup_subject=result["betreff"], followup_greeting=result["anrede"],
                       followup_body=result["text"], unread=1)
    leaddb.add_event(c, lead_id, "draft", f"Erinnerung geschrieben ({ai.MODELS['erinnerung']}).")
    return result


def notify_due(c):
    """Kostenlos: einmal Bescheid geben, wenn Betriebe zum Nachfassen fällig werden."""
    told = set(leaddb.kv_get(c, "followup_notified", []))
    new = [lead for lead in due_followups(c) if lead["id"] not in told]
    if not new:
        return 0
    leaddb.kv_set(c, "followup_notified", sorted(told | {lead["id"] for lead in new}))
    names = ", ".join(lead["name"] for lead in new[:3]) + (" …" if len(new) > 3 else "")
    notify(c, f"Nachfassen: {len(new)} Betrieb{'e' if len(new) != 1 else ''}",
           f"Seit {leaddb.FOLLOWUP_DAYS} Tagen keine Antwort: {names}", "/leads?tab=nachfassen")
    return len(new)


def followup_form(lead):
    subject = lead["followup_subject"] or ""
    original = lead["subject"] or ""
    if not subject.lower().startswith("re:"):
        subject = f"Re: {original}" if original else subject
    return {**lead_form(lead), "subject": subject, "greeting": lead["followup_greeting"] or "",
            "body": lead["followup_body"] or ""}


def send_outreach(c, lead, sent_by, followup=False):
    if not (S("OUTREACH_EMAIL") and outreach_configured()):
        raise RuntimeError("Der Versand per Mail ist ausgeschaltet (OUTREACH_EMAIL).")
    if not lead["email"]:
        raise RuntimeError("Für diesen Betrieb ist keine E-Mail-Adresse bekannt.")
    if followup and not lead["followup_body"]:
        raise RuntimeError("Es gibt noch keinen Text für die Erinnerung.")
    rcpt = {"name": lead["contact_name"] or "", "first": "", "email": lead["email"]}
    form = followup_form(lead) if followup else lead_form(lead)
    msg = emailbuild.build_message(form, rcpt, CFG["settings"], outreach_address())
    if followup and lead["message_id"]:  # im selben Gesprächsverlauf wie die erste Mail
        msg["In-Reply-To"] = lead["message_id"]
        msg["References"] = lead["message_id"]
    smtp = CFG["smtp_connect"](S("OUTREACH_USER"), S("OUTREACH_PASSWORD"))
    try:
        smtp.send_message(msg)
    finally:
        try:
            smtp.quit()
        except Exception:
            pass
    if S("IMAP_HOST"):
        try:
            imap = _imap()
            try:
                imap.append(CFG["sent_folder"](imap), "\\Seen", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            finally:
                imap.logout()
        except Exception as exc:
            log.warning("Ablage im Ordner „Gesendet“ fehlgeschlagen: %s", exc)
    if followup:
        leaddb.update_lead(c, lead["id"], followup_count=1, followup_sent_at=leaddb.now(),
                           followup_message_id=msg["Message-ID"], unread=0)
        leaddb.add_event(c, lead["id"], "sent", f"Erinnerung per Mail an {lead['email']} versendet.", author=sent_by)
        return
    leaddb.update_lead(c, lead["id"], status="versendet", channel="email", sent_at=leaddb.now(),
                       message_id=msg["Message-ID"], unread=0)
    leaddb.add_event(c, lead["id"], "sent", f"Per Mail an {lead['email']} versendet.", author=sent_by)

# ---------------------------------------------------------------- Antworten lesen


def _imap():
    imap = imaplib.IMAP4_SSL(S("IMAP_HOST"), S("IMAP_PORT"), ssl_context=ssl.create_default_context())
    imap.login(S("OUTREACH_USER"), S("OUTREACH_PASSWORD"))
    return imap


def _message_text(msg):
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    text = part.get_content()
    if part.get_content_type() == "text/html":
        parser = finder._TextExtractor()
        parser.feed(text)
        text = parser.text()
    # Zitierte Original-Nachricht abschneiden
    cut = re.search(r"(?im)^(am .{5,120} schrieb .{0,120}:|on .{5,120} wrote:|-{2,} ?(ursprüngliche|original) "
                    r"(nachricht|message)|von: .{3,}\n.*gesendet:)", text)
    if cut:
        text = text[:cut.start()]
    return "\n".join(line for line in text.splitlines() if not line.startswith(">")).strip()


def _match_lead(c, msg):
    refs = " ".join(filter(None, (msg.get("In-Reply-To"), msg.get("References"))))
    for mid in re.findall(r"<[^>]+>", refs):
        row = c.execute("SELECT id FROM leads WHERE message_id=? OR followup_message_id=?", (mid, mid)).fetchone()
        if row:
            return row["id"]
    sender = parseaddr(msg.get("From", ""))[1].lower()
    if not sender:
        return None
    row = c.execute("SELECT id FROM leads WHERE lower(email)=? AND status IN ('versendet','beantwortet') "
                    "ORDER BY sent_at DESC LIMIT 1", (sender,)).fetchone()
    if row:
        return row["id"]
    domain = _domain(sender)
    if domain and domain not in FREEMAIL:
        for r in c.execute("SELECT id, email, website FROM leads WHERE status IN ('versendet','beantwortet') "
                           "ORDER BY sent_at DESC"):
            if domain in (_domain(r["email"]), _host(r["website"])):
                return r["id"]
    return None


INTEREST_MAP = {"qualified": "qualified", "mittel": "mittel", "kein_interesse": "kein_interesse",
                "abmeldung": "kein_interesse"}
INTEREST_TITLES = {"qualified": "Interesse!", "mittel": "Antwort (mittel)", "kein_interesse": "Absage",
                   "abmeldung": "Möchte keine Nachrichten", "automatisch": "Automatische Antwort"}


def check_inbox(c):
    first = c.execute("SELECT MIN(sent_at) FROM leads WHERE sent_at IS NOT NULL AND channel='email'").fetchone()[0]
    if not first:
        return 0
    set_activity("Liest das Postfach …")
    since = datetime.fromisoformat(first) - timedelta(days=1)
    imap = _imap()
    handled = 0
    try:
        imap.select("INBOX", readonly=True)
        _, data = imap.search(None, f"(SINCE {since.day:02d}-{MONTHS[since.month - 1]}-{since.year})")
        for num in (data[0] or b"").split()[-300:]:
            _, header = imap.fetch(num, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
            mid_raw = next((p[1] for p in header if isinstance(p, tuple)), b"")
            mid = email.message_from_bytes(mid_raw).get("Message-ID", "").strip() or f"num:{num.decode()}:{first}"
            if c.execute("SELECT 1 FROM inbox_seen WHERE message_id=?", (mid,)).fetchone():
                continue
            _, parts = imap.fetch(num, "(BODY.PEEK[])")
            raw = next((p[1] for p in parts if isinstance(p, tuple)), b"")
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            lead_id = _match_lead(c, msg)
            if lead_id and handle_reply(c, lead_id, msg):
                handled += 1
            c.execute("INSERT OR IGNORE INTO inbox_seen (message_id, at) VALUES (?,?)", (mid, leaddb.now()))
            c.commit()
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return handled


def handle_reply(c, lead_id, msg):
    lead = leaddb.get_lead(c, lead_id)
    name, addr = parseaddr(msg.get("From", ""))
    text = _message_text(msg)
    leaddb.add_event(c, lead_id, "reply", text or "(leere Nachricht)", author=name or addr,
                     meta={"from": addr, "subject": str(msg.get("Subject", "")), "date": str(msg.get("Date", ""))})
    leaddb.update_lead(c, lead_id, replied_at=leaddb.now(), unread=1)
    left = month_budget_left_eur(c)
    if not ai.available() or (left is not None and left <= 0):
        leaddb.update_lead(c, lead_id, status="beantwortet")
        notify(c, f"Antwort: {lead['name']}", (text or "")[:200], f"/leads/{lead_id}")
        return True
    set_activity(f"Liest Antwort von {lead['name']}")
    sent_text = f"Betreff: {lead['subject']}\n{lead['greeting']}\n\n{lead['body']}"
    result = ai.classify_reply(leaddb.brain(c), lead, sent_text, text)
    kind = result["interesse"]
    leaddb.add_event(c, lead_id, "classify", f"{INTEREST_TITLES[kind]} {result['zusammenfassung']}",
                     meta={"interesse": kind, "grund": result["grund"], "naechster_schritt": result["naechster_schritt"]})
    if kind == "automatisch":
        return True
    fields = {"status": "beantwortet", "interest": INTEREST_MAP[kind], "interest_reason": result["grund"] or None}
    if kind == "abmeldung":
        fields["status"] = "gesperrt"
        block = set(leaddb.kv_get(c, "blocklist", []))
        block.add(addr.lower())
        leaddb.kv_set(c, "blocklist", sorted(block))
    leaddb.update_lead(c, lead_id, **fields)
    leaddb.add_signal(c, "antwort", f"{lead['name']} ({lead['branche']} / {lead['category_label']}, "
                      f"{lead['distance_km']} km, Größe: {lead['research'].get('groesse') or '?'}): {kind}. "
                      f"Grund: {result['grund'] or '–'}. {result['lernpunkt']}", lead_id)
    notify(c, f"{INTEREST_TITLES[kind]} {lead['name']}", result["zusammenfassung"][:220], f"/leads/{lead_id}")
    return True

# ---------------------------------------------------------------- Lernen


def maybe_reflect(c, force=False):
    signals = [dict(r) for r in c.execute("SELECT * FROM signals WHERE processed=0 ORDER BY id")]
    if not signals:
        return False
    oldest = datetime.fromisoformat(signals[0]["at"])
    if not force and len(signals) < 3 and datetime.now() - oldest < timedelta(minutes=20):
        return False
    set_activity("Lernt aus euren Rückmeldungen …")
    current = leaddb.brain(c)
    _, learned = leaddb.split_learned(current)
    result = ai.reflect(current, learned, leaddb.branche_stats(c), signals)
    leaddb.save_brain(c, leaddb.with_learned(current, result["gelernt"]), "KI", result["aenderung"])
    c.execute(f"UPDATE signals SET processed=1 WHERE id IN ({','.join('?' * len(signals))})",
              [s["id"] for s in signals])
    c.commit()
    return True

# ---------------------------------------------------------------- Ablauf


def wake():
    _wake.set()


def tick():
    with _job_lock:
        c = conn()
        try:
            # Antworten lesen ist kostenlos, nur das Einordnen einer echten Antwort kostet ein paar Cent
            if outreach_configured() and S("IMAP_HOST"):
                last = leaddb.kv_get(c, "last_inbox", 0)
                if time.time() - last > S("INBOX_MINUTES") * 60:
                    leaddb.kv_set(c, "last_inbox", time.time())
                    check_inbox(c)
            notify_due(c)
            run = c.execute("SELECT id FROM runs WHERE status='wartet' ORDER BY id LIMIT 1").fetchone()
            if run:
                execute_run(c, run["id"])
            if leaddb.kv_get(c, "request:reflect") and ai.available():
                leaddb.kv_set(c, "request:reflect", False)
                maybe_reflect(c, force=True)
            wanted = leaddb.kv_get(c, "request:lead")
            if wanted and ai.available():
                leaddb.kv_set(c, "request:lead", None)
                _local.run_id = None
                recheck_lead(c, wanted)
            STATE["error"] = None if not run else STATE["error"]
        except ai.AIError as exc:
            log.warning("KI-Fehler: %s", exc)
            STATE["error"], STATE["error_at"] = str(exc), time.time()
        except Exception as exc:
            log.exception("Fehler im Leads-Arbeiter")
            STATE["error"], STATE["error_at"] = f"{exc.__class__.__name__}: {exc}", time.time()
        finally:
            c.close()
            set_activity("Wartet")


def recheck_lead(c, lead_id):
    """Einen einzelnen Betrieb von Hand neu prüfen lassen (alle Stufen, ohne Lauf)."""
    brain = leaddb.brain(c)
    name = leaddb.get_lead(c, lead_id)["name"]
    set_activity(f"Prüft neu: {name}")
    if stage_quick(c, lead_id, None) and stage_analyse(c, lead_id, brain):
        stage_write(c, lead_id, brain)


def _loop():
    time.sleep(3)
    c = conn()
    c.execute("UPDATE leads SET status='kandidat' WHERE status='recherche'")  # nach Neustart
    c.execute("UPDATE runs SET status='abgebrochen', ended_at=?, message='Server wurde neu gestartet.' "
              "WHERE status='laeuft'", (leaddb.now(),))
    c.commit()
    c.close()
    while True:
        tick()
        _wake.wait(timeout=60)
        _wake.clear()
