"""Der Hintergrund-Arbeiter der KI-Akquise.

Läuft als Thread im Mailer-Prozess (Gunicorn mit genau einem Worker) und macht reihum:
1. Postfach lesen: Antworten auf versendete Nachrichten erkennen und einordnen.
2. Firmen suchen (OpenStreetMap), eine nach der anderen recherchieren und bei Eignung einen Entwurf schreiben.
3. Aus Feedback und Antworten lernen und den Abschnitt „Gelernt“ im Gehirn fortschreiben.
Versendet wird nie automatisch. Jede Nachricht gibt ein Mensch frei.
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
from datetime import date, datetime, timedelta
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
    return {**STATE, "warnings": warnings, "email_enabled": S("OUTREACH_EMAIL") and outreach_configured()}

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

# ---------------------------------------------------------------- Firmen suchen


def run_search(c):
    lat, lon = S("LEADS_CENTER")
    set_activity(f"Sucht Betriebe im Umkreis von {S('LEADS_RADIUS_KM'):g} km …")
    found = finder.search_osm(lat, lon, S("LEADS_RADIUS_KM"), S("OVERPASS_URL"))
    new = 0
    for f in found:
        cur = c.execute(
            "INSERT OR IGNORE INTO leads (source_id, created_at, updated_at, name, branche, category, category_label,"
            " address, city, lat, lon, distance_km, website, email, phone, status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'kandidat')",
            (f["source_id"], leaddb.now(), leaddb.now(), f["name"], f["branche"], f["category"], f["category_label"],
             f["address"], f["city"], f["lat"], f["lon"], f["distance_km"], f["website"], f["email"], f["phone"]))
        new += cur.rowcount
    c.commit()
    leaddb.kv_set(c, "last_search", {"at": leaddb.now(), "found": len(found), "new": new})
    return new


def _beta(pos, neg):
    return random.betavariate(1 + pos, 1 + neg)


def next_candidate(c):
    """Welche Firma als Nächstes? Branchen, die bisher gut liefen, kommen öfter dran (Thompson-Sampling),
    andere aber weiterhin ab und zu, damit die Suche nicht festfährt."""
    stats = {}
    for r in c.execute("""
            SELECT branche, category,
                   SUM(interest='qualified') q, SUM(interest='mittel') m,
                   SUM(interest='kein_interesse' OR status='gesperrt') k,
                   SUM(rejected_by='team') rt, SUM(rejected_by='ki') rk,
                   SUM(status IN ('entwurf','versendet','beantwortet') AND rejected_by IS NULL) ok
            FROM leads WHERE status NOT IN ('kandidat','recherche') GROUP BY branche, category""").fetchall():
        for key in (("b", r["branche"]), ("c", r["category"])):
            s = stats.setdefault(key, [0.0, 0.0])
            s[0] += 3 * (r["q"] or 0) + 1.5 * (r["m"] or 0) + 0.5 * (r["ok"] or 0)
            s[1] += (r["k"] or 0) + (r["rt"] or 0) + 0.5 * (r["rk"] or 0)
    blocked = set(leaddb.kv_get(c, "blocklist", []))
    rows = [r for r in c.execute("SELECT id, branche, category, distance_km, website, email FROM leads "
                                 "WHERE status='kandidat'")
            if not ((_domain(r["email"]) or _host(r["website"])) in blocked)]
    if rows and random.random() < EXPLORE:
        return random.choice(rows)["id"]  # ab und zu ganz bewusst etwas Neues ausprobieren
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


def process_lead(c, lead_id):
    lead = leaddb.get_lead(c, lead_id)
    leaddb.update_lead(c, lead_id, status="recherche")
    set_activity(f"Recherchiert: {lead['name']}")
    if lead["website"]:
        research = finder.research_website(lead["website"])
    else:
        research = {"pages": [], "emails": [], "errors": ["Keine Webseite bekannt"]}
    if not research["pages"] and not lead["email"]:
        leaddb.update_lead(c, lead_id, status="aussortiert", rejected_by="ki", fit_score=0,
                           fit_reason="Webseite nicht lesbar und keine E-Mail bekannt.",
                           research=_json({"errors": research["errors"]}))
        leaddb.add_event(c, lead_id, "status", "Aussortiert: Webseite nicht lesbar, keine Kontaktadresse.")
        return False

    set_activity(f"Bewertet und schreibt: {lead['name']}")
    try:
        result = ai.qualify(leaddb.brain(c), lead, research)
    except ai.AIError:
        leaddb.update_lead(c, lead_id, status="kandidat")  # später noch einmal versuchen
        raise
    known = set(research["emails"]) | ({lead["email"].lower()} if lead["email"] else set())
    chosen = (result.get("email") or "").strip().lower()
    email_addr = chosen if chosen in known else (lead["email"] or (research["emails"][0] if research["emails"] else ""))
    info = {"groesse": result.get("groesse"), "zeitfresser": result.get("zeitfresser", []),
            "pages": [p["url"] for p in research["pages"]], "emails": research["emails"], "errors": research["errors"]}

    duplicate = email_addr and c.execute(
        "SELECT name FROM leads WHERE id != ? AND email=? AND status IN ('entwurf','versendet','beantwortet','gesperrt')",
        (lead_id, email_addr)).fetchone()
    blocked = set(leaddb.kv_get(c, "blocklist", []))
    if _domain(email_addr) in blocked or email_addr in blocked:
        leaddb.update_lead(c, lead_id, status="gesperrt", email=email_addr, research=_json(info))
        leaddb.add_event(c, lead_id, "status", "Gesperrt: Diese Adresse möchte nicht kontaktiert werden.")
        return False
    if duplicate:
        leaddb.update_lead(c, lead_id, status="aussortiert", rejected_by="ki", email=email_addr, research=_json(info),
                           fit_reason=f"Doppelt: dieselbe Adresse wie „{duplicate['name']}“.")
        leaddb.add_event(c, lead_id, "status", f"Aussortiert: gleiche Adresse wie „{duplicate['name']}“.")
        return False

    common = dict(fit_score=result["score"], fit_reason=result["begruendung"], research=_json(info),
                  contact_name=result.get("ansprechpartner") or None, email=email_addr or None)
    if result["passt"]:
        leaddb.update_lead(c, lead_id, status="entwurf", unread=1, subject=result["betreff"],
                           greeting=result["anrede"], body=result["text"], **common)
        leaddb.add_event(c, lead_id, "draft", f"Passt ({result['score']}/100). {result['begruendung']}")
        _count_today(c)
        notify(c, f"Neuer Entwurf: {lead['name']}",
               f"{lead['category_label']} · {lead['distance_km']} km. {result['begruendung'][:180]}", f"/leads/{lead_id}")
        return True
    leaddb.update_lead(c, lead_id, status="aussortiert", rejected_by="ki", **common)
    leaddb.add_event(c, lead_id, "status", f"Aussortiert ({result['score']}/100). {result['begruendung']}")
    return False


def _json(data):
    return json.dumps(data, ensure_ascii=False)


def _today_key():
    return f"drafts:{date.today().isoformat()}"


def _count_today(c):
    leaddb.kv_set(c, _today_key(), leaddb.kv_get(c, _today_key(), 0) + 1)

# ---------------------------------------------------------------- Versand


def lead_form(lead):
    """Die Felder für den Mail-Baukasten des Mailers (Vorlage „Kurz-Mail“)."""
    return {
        "template": "brief", "graphic": "none", "subject": lead["subject"] or "", "preheader": "", "headline": "",
        "greeting": lead["greeting"] or "", "body": lead["body"] or "", "body2": "",
        "closing": "Viele Grüße", "sender_name": S("LEADS_SENDER_NAME"),
        "footer": S("DEFAULT_FOOTER"), "info_label": [], "info_value": [], "cta_text": "", "cta_url": "",
    }


def send_outreach(c, lead, sent_by):
    if not (S("OUTREACH_EMAIL") and outreach_configured()):
        raise RuntimeError("Der Versand per Mail ist ausgeschaltet (OUTREACH_EMAIL).")
    if not lead["email"]:
        raise RuntimeError("Für diesen Betrieb ist keine E-Mail-Adresse bekannt.")
    rcpt = {"name": lead["contact_name"] or "", "first": "", "email": lead["email"]}
    msg = emailbuild.build_message(lead_form(lead), rcpt, CFG["settings"], outreach_address())
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
        row = c.execute("SELECT id FROM leads WHERE message_id=?", (mid,)).fetchone()
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
    if not ai.available():
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


def request(action):
    """Vom Knopf in der Oberfläche: 'search' oder 'next' beim nächsten Durchlauf erledigen."""
    c = conn()
    try:
        leaddb.kv_set(c, f"request:{action}", True)
    finally:
        c.close()
    _wake.set()


def tick():
    """Ein Durchlauf. Gibt True zurück, wenn gleich weitergemacht werden soll."""
    more = False
    with _job_lock:
        c = conn()
        try:
            if outreach_configured() and S("IMAP_HOST"):
                last = leaddb.kv_get(c, "last_inbox", 0)
                if time.time() - last > S("INBOX_MINUTES") * 60:
                    check_inbox(c)
                    leaddb.kv_set(c, "last_inbox", time.time())
            paused = time.time() < leaddb.kv_get(c, "pause_until", 0)
            if ai.available() and not paused:
                force_search = leaddb.kv_get(c, "request:search", False)
                force_next = leaddb.kv_get(c, "request:next", False)
                auto = leaddb.kv_get(c, "auto", True)
                open_drafts = c.execute("SELECT COUNT(*) FROM leads WHERE status='entwurf'").fetchone()[0]
                room = open_drafts < S("LEADS_MAX_OPEN_DRAFTS") and leaddb.kv_get(c, _today_key(), 0) < S("LEADS_PER_DAY")
                last_search = leaddb.kv_get(c, "last_search")
                stale = not last_search or datetime.now() - datetime.fromisoformat(last_search["at"]) > timedelta(days=7)
                retry_ok = time.time() - leaddb.kv_get(c, "search_attempt", 0) > 3600
                if force_search or (auto and room and stale and retry_ok):
                    leaddb.kv_set(c, "request:search", False)
                    leaddb.kv_set(c, "search_attempt", time.time())
                    run_search(c)
                wanted = leaddb.kv_get(c, "request:lead")
                if wanted:
                    leaddb.kv_set(c, "request:lead", None)
                    process_lead(c, wanted)
                elif force_next or (auto and room):
                    leaddb.kv_set(c, "request:next", False)
                    lead_id = next_candidate(c)
                    if lead_id:
                        process_lead(c, lead_id)
                        more = auto
                maybe_reflect(c)
            STATE["error"] = None
        except ai.AIError as exc:
            log.warning("KI-Fehler: %s", exc)
            STATE["error"], STATE["error_at"] = str(exc), time.time()
            leaddb.kv_set(c, "pause_until", time.time() + 30 * 60)  # KI-Probleme: eine halbe Stunde Pause
        except Exception as exc:
            log.exception("Fehler im Leads-Arbeiter")
            STATE["error"], STATE["error_at"] = f"{exc.__class__.__name__}: {exc}", time.time()
            # Eine Firma, bei der es knallt, nicht endlos wiederholen
            c.execute("UPDATE leads SET status='fehler', updated_at=? WHERE status='recherche'", (leaddb.now(),))
            c.commit()
        finally:
            c.close()
            set_activity("Wartet")
    return more


def _loop():
    time.sleep(3)
    c = conn()
    c.execute("UPDATE leads SET status='kandidat' WHERE status='recherche'")  # nach Neustart
    c.commit()
    c.close()
    while True:
        more = tick()
        _wake.wait(timeout=5 if more else 60)
        _wake.clear()
