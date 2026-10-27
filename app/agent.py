"""Der Hintergrund-Arbeiter der Akquise.

Läuft als Thread im Mailer-Prozess (Gunicorn mit genau einem Worker).
Von sich aus liest er nur das Postfach und ordnet Antworten nach Regeln ein (kostenlos). Ein Lauf, den ihr von
Hand startet, macht:

1. Suchen (OpenStreetMap) und Grobfilter nach Regeln: kostenlos.
2. Bewertung nach Regeln (app/scoring.py): Webseite lesen, Punkte vergeben, ohne KI, kostenlos.
   Wer genug Punkte hat, kommt als „Neu“ in die Pipeline.
3. Nur wenn ihr es wollt: Entwürfe für die Besten, von der KI geschrieben, mit Budgetgrenze.
Versendet wird nie automatisch.
"""
import email
import email.policy
import imaplib
import json
import logging
import random
import re
import smtplib
import socket
import ssl
import threading
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr, parsedate_to_datetime
from urllib.parse import urlparse

from . import ai, crm, emailbuild, finder, known, leaddb, mailbox, niches, regions, schedule, scoring, team, webpush

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
    if not settings["LEADS_WORKER"]:
        set_activity("Wartet")
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
    if not outreach_configured():
        warnings.append("Kein Akquise-Postfach: OUTREACH_USER und OUTREACH_PASSWORD eintragen, "
                        "sonst werden keine Antworten gelesen und keine Mails verschickt.")
    elif not S("IMAP_HOST"):
        warnings.append("IMAP_HOST fehlt: Antworten der Firmen können nicht gelesen werden.")
    if not S("LEADS_WORKER"):
        warnings.append("Der Hintergrund-Arbeiter ist aus (LEADS_WORKER=false).")
    return {**STATE, "run_id": RUN["id"], "progress": RUN["progress"], "warnings": warnings,
            "email_enabled": S("OUTREACH_EMAIL") and outreach_configured()}

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


REGION_LABELS = {"DE-SH": "in ganz Schleswig-Holstein", "DE-SL": "im Saarland", "DE-HH": "in Hamburg"}


class SearchError(Exception):
    pass


def run_search(c):
    lat, lon = S("LEADS_CENTER")
    keys = niches.search_keys(c)
    where = REGION_LABELS.get(S("LEADS_REGION"), S("LEADS_REGION")) if S("LEADS_REGION") \
        else f"im Umkreis von {S('LEADS_RADIUS_KM'):g} km"
    set_activity(f"Schritt 1/3 · Sucht Betriebe {where} (kostenlos, dauert ein paar Minuten) …")
    try:
        found = finder.search_osm(lat, lon, S("LEADS_RADIUS_KM"), S("OVERPASS_URL"), S("LEADS_REGION"), keys)
    except Exception as exc:
        raise SearchError(f"Die Kartensuche (OpenStreetMap) ist gerade nicht erreichbar ({exc}). "
                          "Bitte später erneut starten; es sind keine Kosten entstanden.") from exc
    new = filtered = duplicates = 0
    for f in found:
        if known.find(c, f["name"], f["city"], f["website"], f["email"], f["source_id"]):
            duplicates += 1  # schon einmal gefunden (von wem auch immer, auch wenn er inzwischen weg ist)
            continue
        reason = f.get("filter_reason")
        cur = c.execute(
            "INSERT OR IGNORE INTO leads (source_id, created_at, updated_at, name, branche, category, category_label,"
            " niche, address, city, lat, lon, distance_km, website, email, phone, status, rejected_by, fit_reason)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f["source_id"], leaddb.now(), leaddb.now(), f["name"], f["branche"], f["category"], f["category_label"],
             f.get("niche"),
             f["address"], f["city"], f["lat"], f["lon"], f["distance_km"], f["website"], f["email"], f["phone"],
             "aussortiert" if reason else "kandidat", "filter" if reason else None,
             f"Grobfilter: {reason}" if reason else None))
        new += cur.rowcount
        filtered += cur.rowcount if reason else 0
        if cur.rowcount:
            known.remember(c, f["name"], f["city"], f["website"], f["email"], f["source_id"], "osm",
                           RUN.get("by") or "Suche", cur.lastrowid, commit=False)
    c.commit()
    niches.backfill(c)  # schon bekannte Betriebe, die jetzt in eine gewählte Nische fallen
    leaddb.kv_set(c, "last_search", {"at": leaddb.now(), "found": len(found), "new": new, "filtered": filtered,
                                     "known": duplicates, "niches": keys})
    return new, filtered


def learned_filter(c):
    """Auch kostenlos: Branchen, die schon mindestens fünfmal nicht gepasst haben und noch nie, fallen raus.
    Gesperrte Adressen ebenso."""
    removed = 0
    for r in c.execute("""
            SELECT category, category_label FROM leads
            WHERE status NOT IN ('kandidat','recherche') AND IFNULL(rejected_by,'') != 'filter' AND category != ''
            GROUP BY category
            HAVING IFNULL(SUM(interest IN ('qualified','mittel') OR stage IS NOT NULL
                              OR status IN ('passt','entwurf','versendet')), 0) = 0
               AND IFNULL(SUM(rejected_by IN ('ki','regel','team') OR interest='kein_interesse'), 0) >= 5""").fetchall():
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


def next_candidate(c, plan=None):
    """Welche Firma als Nächstes? Branchen, die bisher gut liefen, kommen öfter dran (Thompson-Sampling),
    und ab und zu wird ganz bewusst etwas anderes ausprobiert, damit die Suche nicht festfährt.
    `plan` = {"weights": {nische: prozent}, "taken": {nische: anzahl}}: nur die gewählten Nischen, im Verhältnis
    ihrer Prozente. Die gezählte Nische trägt der Aufrufer in taken ein (siehe execute_run)."""
    stats = {}
    for r in c.execute("""
            SELECT branche, category,
                   SUM(interest='qualified') q, SUM(interest='mittel') m,
                   SUM(interest='kein_interesse' OR status='gesperrt') k,
                   SUM(rejected_by='team') rt, SUM(rejected_by IN ('ki','regel')) rk,
                   SUM(status IN ('passt','entwurf','versendet','beantwortet') AND rejected_by IS NULL) ok
            FROM leads WHERE status NOT IN ('kandidat','recherche') AND IFNULL(rejected_by,'') != 'filter'
            GROUP BY branche, category""").fetchall():
        for key in (("b", r["branche"]), ("c", r["category"])):
            s = stats.setdefault(key, [0.0, 0.0])
            s[0] += 3 * (r["q"] or 0) + 1.5 * (r["m"] or 0) + 0.5 * (r["ok"] or 0)
            s[1] += (r["k"] or 0) + (r["rt"] or 0) + 0.5 * (r["rk"] or 0)
    rows = c.execute("SELECT id, source_id, branche, category, niche, distance_km FROM leads "
                     "WHERE status='kandidat'").fetchall()
    imported = [r for r in rows if (r["source_id"] or "").startswith("claude:")]
    if imported:  # Betriebe aus der Claude-Recherche zuerst, die habt ihr gezielt suchen lassen
        rows = imported
    elif plan:
        rows = [r for r in rows if r["niche"] in plan["weights"]]
        niche = niches.pick(plan["weights"], plan["taken"], {r["niche"] for r in rows})
        rows = [r for r in rows if r["niche"] == niche]
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

# ---------------------------------------------------------------- Bewertung nach Regeln (kostenlos) und Entwurf (KI)


def stage_score(c, lead_id, learned, promote=True):
    """Webseite lesen und nach Regeln bewerten. Gibt True zurück, wenn der Betrieb passt.
    promote=False (im Lauf): Passende bleiben erst im Reiter „Passt“; ins CRM kommen am Ende nur die besten."""
    lead = leaddb.get_lead(c, lead_id)
    in_pipeline = lead["stage"] is not None  # von Hand angelegt oder importiert: bleibt drin
    leaddb.update_lead(c, lead_id, status="recherche")
    research = finder.research_website(lead["website"]) if lead["website"] else \
        {"pages": [], "emails": [], "links": [], "errors": ["keine Webseite"]}
    imported = lead["research"].get("import")  # aus der Claude-Recherche (CSV), bleibt erhalten
    if imported and imported.get("groesse"):
        research["size_hint"] = imported["groesse"]
    result = scoring.score(lead, research, learned, regions.bonus_info(leaddb.kv_get(c, "regionen")))
    if imported:
        result["zeitfresser"] = list(dict.fromkeys([*imported.get("zeitfresser", []), *result["zeitfresser"]]))
    threshold = S("LEADS_RULE_MIN")
    email_addr = result["email"] or lead["email"] or None
    passes = result["score"] >= threshold and not result["ko"]
    info = {"signale": result["signals"], "ko": result["ko"], "groesse": result["groesse"], "zeitfresser": result["zeitfresser"],
            "auszug": result["auszug"], "pages": [p["url"] for p in research["pages"]], "emails": research["emails"],
            "errors": research["errors"],
            "beschreibung": next((pg.get("description") for pg in research["pages"] if pg.get("description")), ""),
            "titel": research["pages"][0]["title"] if research["pages"] else ""}
    if imported:
        info["import"] = imported
    common = dict(fit_score=result["score"], fit_reason=scoring.summary(result, threshold), research=_json(info),
                  contact_name=lead["contact_name"] or result["contact_name"] or None, email=email_addr)

    blocked = set(leaddb.kv_get(c, "blocklist", []))
    if email_addr and (_domain(email_addr) in blocked or email_addr in blocked):
        leaddb.update_lead(c, lead_id, status="gesperrt", **common)
        leaddb.add_event(c, lead_id, "status", "Gesperrt: Diese Adresse möchte nicht kontaktiert werden.")
        return False
    duplicate = email_addr and c.execute(
        "SELECT name FROM leads WHERE id != ? AND email=? AND status IN ('passt','entwurf','versendet',"
        "'beantwortet','gesperrt')", (lead_id, email_addr)).fetchone()
    if duplicate and not in_pipeline:
        leaddb.update_lead(c, lead_id, status="aussortiert", rejected_by="regel", **{
            **common, "fit_reason": f"Doppelt: dieselbe Adresse wie „{duplicate['name']}“."})
        leaddb.add_event(c, lead_id, "status", f"Aussortiert: gleiche Adresse wie „{duplicate['name']}“.")
        return False
    if passes or in_pipeline:
        leaddb.update_lead(c, lead_id, status="passt", rejected_by=None, **common)
        note = "" if passes else " Laut Regeln eher nicht, bleibt aber in der Pipeline, weil ihr den Betrieb angelegt habt."
        leaddb.add_event(c, lead_id, "status", common["fit_reason"] + note, author="Regeln")
        if promote or in_pipeline:
            crm.advance(c, lead_id, "neu")
        return passes
    leaddb.update_lead(c, lead_id, status="aussortiert", rejected_by="regel", **common)
    leaddb.add_event(c, lead_id, "status", common["fit_reason"], author="Regeln")
    return False


def promote_best(c, top):
    """Nur die besten passenden Betriebe kommen als „Neu“ ins CRM, der Rest bleibt im Reiter „Passt“ als Reserve."""
    rows = c.execute("SELECT id FROM leads WHERE status='passt' AND stage IS NULL AND interest IS NULL "
                     "ORDER BY fit_score DESC, distance_km LIMIT ?", (max(0, top),)).fetchall()
    for r in rows:
        crm.advance(c, r["id"], "neu", author="Regeln")
    return len(rows)


def _analysis(lead):
    info = lead["research"]
    return {"begruendung": lead["fit_reason"] or "", "groesse": info.get("groesse"),
            "zeitfresser": info.get("zeitfresser", []), "auszug": info.get("auszug", ""),
            "ansprechpartner": lead["contact_name"], "recherche": info.get("import")}


def stage_write(c, lead_id, brain):
    """Den Entwurf von der KI schreiben lassen (stärkstes Modell). Nur auf Wunsch."""
    lead = leaddb.get_lead(c, lead_id)
    result = ai.write_draft(brain, lead, _analysis(lead))
    leaddb.update_lead(c, lead_id, status="entwurf", unread=1, subject=result["betreff"], greeting=result["anrede"],
                       body=result["text"])
    leaddb.add_event(c, lead_id, "draft", f"Entwurf geschrieben ({ai.MODELS['entwurf']}).")
    return True


TEMPLATE_HEADING = "Vorlage (ohne KI)"
# Für die Vorlage: ein Satz, der zeigt, dass wir den Alltag der Branche verstehen, und ein konkretes Beispiel.
VERSTAENDNIS = {
    "handwerk": "Ich kann mir gut vorstellen, wie es bei Ihnen läuft: Tagsüber sind Sie und Ihr Team bei den Kunden, "
                "und Angebote, Aufmaß und Rechnungen warten danach noch im Büro. Gerade wenn die Auftragsbücher voll "
                "sind, kostet genau das die Zeit, die eigentlich für das Handwerk da sein sollte.",
    "pflege": "Ich weiß, wie viel Ihr Team jeden Tag leistet, und dass nach jedem Einsatz noch die Dokumentation wartet. "
              "Das ist Zeit, die am Ende bei den Menschen fehlt, um die es eigentlich geht.",
    "landwirtschaft": "Zwischen Stall, Feld und Hofladen bleibt wenig Luft, und trotzdem wollen Düngung, Pflanzenschutz "
                      "und Förderanträge sauber dokumentiert sein. Das ist viel Schreibarbeit für einen Betrieb, der "
                      "eigentlich draußen gebraucht wird.",
    "dienstleistung": "In einem Büro wie Ihrem landet vieles auf demselben Tisch: Anfragen, Termine und Unterlagen, die "
                      "erst zusammengesucht werden müssen. Vieles davon wiederholt sich jeden Tag und kostet trotzdem "
                      "jedes Mal wieder Zeit.",
    "industrie": "Auf einer Werft hängt viel an Absprachen, Prüfprotokollen und Unterlagen, die zur richtigen Zeit am "
                 "richtigen Ort sein müssen. Wenn das von Hand läuft, kostet es gute Leute jeden Tag Zeit.",
    "kreativ": "In einem Studio wie Ihrem soll die Zeit in die Gestaltung fließen. Stattdessen gehen oft Stunden in "
               "Briefings, Abstimmungsschleifen, Angebote und Rechnungen, die sich bei jedem Projekt wiederholen.",
    "gesundheit": "In einer Praxis wie Ihrer klingelt das Telefon den ganzen Tag, dazu kommen Termine, Rezepte und "
                  "Unterlagen. Das ist Zeit, die am Empfang und am Ende bei den Patienten fehlt.",
    "gastgewerbe": "Bei Ihnen dreht sich alles um die Gäste, und trotzdem laufen nebenher Anfragen, Buchungen, "
                   "Dienstpläne und Bestellungen, die jeden Tag wieder Zeit kosten.",
    "handel": "Neben dem Verkauf landen bei Ihnen vermutlich Anfragen, Bestellungen und Lieferscheine auf dem Tisch, "
              "die alle von Hand nachgehalten werden wollen.",
}
VERSTAENDNIS_SONST = ("Ich kann mir gut vorstellen, dass bei Ihnen vieles an wenigen Köpfen hängt und Routinearbeit im "
                      "Büro Zeit kostet, die an anderer Stelle fehlt.")
BEISPIEL = {
    "handwerk": "Genau da setzen wir an, zum Beispiel mit Angeboten, die Sie per Sprachnotiz direkt beim Kunden "
                "diktieren und die im Büro fertig formatiert ankommen.",
    "pflege": "Genau da setzen wir an, zum Beispiel mit Einsatzberichten, die Ihr Team direkt nach dem Besuch "
              "einspricht und die sauber im richtigen Format landen, ohne Abtippen am Abend.",
    "landwirtschaft": "Genau da setzen wir an, zum Beispiel mit Nachweisen, die sich aus den Daten Ihrer Maschinen und "
                      "Apps fast von selbst schreiben.",
    "dienstleistung": "Genau da setzen wir an, zum Beispiel mit Anfragen, die vorsortiert und mit einem "
                      "Antwortvorschlag in Ihrem Postfach liegen.",
    "industrie": "Genau da setzen wir an, zum Beispiel mit Prüfprotokollen und Berichten, die aus Fotos und kurzen "
                 "Notizen von selbst entstehen.",
    "kreativ": "Genau da setzen wir an, zum Beispiel mit Angeboten und Projektplänen, die aus einem kurzen Briefing "
               "fast von selbst entstehen, damit mehr Zeit für die eigentliche Arbeit bleibt.",
    "gesundheit": "Genau da setzen wir an, zum Beispiel mit Anfragen und Terminwünschen, die vorsortiert und mit einem "
                  "Antwortvorschlag bei Ihnen ankommen.",
    "gastgewerbe": "Genau da setzen wir an, zum Beispiel mit Buchungsanfragen, die automatisch vorbereitet und mit "
                   "einer passenden Antwort bei Ihnen liegen.",
    "handel": "Genau da setzen wir an, zum Beispiel mit Bestellungen und Anfragen, die automatisch erfasst und "
              "sortiert bei Ihnen ankommen.",
}
BEISPIEL_SONST = "Genau da setzen wir an, zum Beispiel mit einem kleinen Werkzeug, das wiederkehrende Schreibarbeit übernimmt."
# Alte Vorlage (bis Oktober 2026), wird beim Start einmal durch die neue ersetzt, wenn ihr sie nicht geändert habt
OLD_TEMPLATE_MARK = "Bei Betrieben wie {firma} kostet oft das hier viel Zeit"


def upgrade_template(c):
    """Einmalig: die alte, eher allgemeine Vorlage („Bei Betrieben wie …“) durch die persönlichere ersetzen.
    Nur, wenn ihr sie nicht selbst geändert habt; der Rest des Gehirns bleibt, wie er ist."""
    if leaddb.kv_get(c, "template_v2"):
        return False
    leaddb.kv_set(c, "template_v2", True)
    current = leaddb.brain(c)
    if OLD_TEMPLATE_MARK not in current:
        return False
    seed = leaddb.BRAIN_SEED.read_text(encoding="utf-8")
    new_section = leaddb.brain_sections(seed, [TEMPLATE_HEADING]).strip()
    parts = re.split(r"(?m)^(?=## )", current)
    replaced = "".join(new_section + "\n\n" if p.startswith(f"## {TEMPLATE_HEADING}") else p for p in parts)
    return leaddb.save_brain(c, replaced, "Update", "Neue, persönlichere Vorlage (ohne KI)")


def template_text(c, member=None):
    """Die Vorlage als Text („Betreff: …“, „Anrede: …“, Leerzeile, Text): die eigene der Person, sonst die aus
    dem Gehirn (Abschnitt „Vorlage (ohne KI)“)."""
    upgrade_template(c)
    own = (team.settings(c, member)["vorlage"] if member else "") or ""
    if own.strip():
        return own.strip()
    section = leaddb.brain_sections(leaddb.brain(c), [TEMPLATE_HEADING]).strip() \
        or leaddb.brain_sections(leaddb.BRAIN_SEED.read_text(encoding="utf-8"), [TEMPLATE_HEADING]).strip()
    text = "\n".join(section.splitlines()[1:]).strip()  # Überschrift weg
    # Die gemeinsame Vorlage stellt die Person vor, die schreibt (früher fest „Oskar Jacobsen“)
    return text.replace("Ich bin Oskar Jacobsen von", "Ich bin {absender} von")


def fill_template(lead, text, absender):
    """Vorlage für einen Betrieb ausfüllen. Platzhalter: {firma}, {anrede}, {zeitfresser}, {kategorie}, {ort},
    {absender}, {aufhaenger}, {verstaendnis}, {beispiel}. Ein Absatz, dessen Platzhalter leer bleibt (z. B.
    {aufhaenger} ohne Recherche), fällt weg."""
    lines = (text or "").splitlines()
    subject = greeting = ""
    while lines and (not lines[0].strip() or re.match(r"(?i)^(betreff|anrede):", lines[0])):
        m = re.match(r"(?i)^(betreff|anrede):\s*(.*)$", lines.pop(0))
        if m and m.group(1).lower() == "betreff":
            subject = m.group(2)
        elif m:
            greeting = m.group(2)
    research = lead.get("research") or {}
    imported = research.get("import") or {}
    fresser = (research.get("zeitfresser") or ["Papierkram und wiederkehrende Anfragen"])[:3]
    if lead.get("branche") in VERSTAENDNIS:  # was der Branchen-Satz schon sagt, nicht noch einmal aufzählen
        fresser = [f for f in fresser if f != scoring.BRANCHE_ZEITFRESSER.get(lead["branche"])]
    fresser = fresser[:2]
    values = {"firma": lead["name"], "kategorie": lead.get("category_label") or "Betrieb",
              "anrede": imported.get("anrede") or (f"Guten Tag {lead['contact_name']},"
                                                   if lead.get("contact_name") else "Guten Tag,"),
              "zeitfresser": " und ".join(fresser), "ort": lead.get("city") or "Ihrer Region",
              "absender": absender, "aufhaenger": imported.get("aufhaenger", ""),
              "verstaendnis": VERSTAENDNIS.get(lead.get("branche"), VERSTAENDNIS_SONST),
              "beispiel": BEISPIEL.get(lead.get("branche"), BEISPIEL_SONST)}

    def fill(t):
        return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), t).strip()

    def fill_body(t):
        keep = [p for p in re.split(r"\n\s*\n", t)
                if not any(values.get(k) == "" for k in re.findall(r"\{(\w+)\}", p))]
        return fill("\n\n".join(keep))
    return {"subject": fill(subject), "greeting": fill(greeting) or values["anrede"],
            "body": fill_body("\n".join(lines))}


def template_draft(c, lead_id, member=None):
    """Entwurf aus der Vorlage (der eigenen der Person, sonst der aus dem Gehirn), kostenlos."""
    lead = leaddb.get_lead(c, lead_id)
    member = member or lead.get("owner")
    absender = (team.get(c, member) or {}).get("name") or S("LEADS_SENDER_NAME")
    fields = fill_template(lead, template_text(c, member), absender)
    leaddb.update_lead(c, lead_id, status="entwurf", **fields)
    leaddb.add_event(c, lead_id, "draft", "Entwurf aus der Vorlage erstellt (ohne KI).", author="Vorlage")
    return fields

# ---------------------------------------------------------------- Läufe (von Hand oder nach Zeitplan, siehe schedule.py)

# Grobe Kosten pro Aufruf in US-Dollar, bis genug eigene Messwerte da sind
DEFAULT_COST_USD = {"entwurf": 0.06, "chat": 0.06, "lernen": 0.04, "erinnerung": 0.02}
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


def estimate(c, drafts, followups=True):
    """Was kostet ein Lauf ungefähr? Die Bewertung ist kostenlos, nur Entwürfe und Erinnerungen kosten."""
    waiting = c.execute("SELECT COUNT(*) FROM leads WHERE status='passt' AND interest IS NULL "
                        "AND IFNULL(body,'')=''").fetchone()[0]
    reminders = len(due_followups(c, without_text=True)) if followups else 0
    usd = drafts * avg_cost_usd(c, "entwurf") + reminders * avg_cost_usd(c, "erinnerung")
    if drafts or reminders:
        usd += avg_cost_usd(c, "lernen")
    return {"eur": ai.eur(usd), "entwuerfe": drafts, "erinnerungen": reminders, "ohne_entwurf": waiting,
            "per_stage_eur": {s: ai.eur(avg_cost_usd(c, s)) for s in DEFAULT_COST_USD}}


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
    if not S("LEADS_WORKER"):
        return None, "Der Hintergrund-Arbeiter ist aus (LEADS_WORKER=false)."
    if params.get("entwuerfe") and not ai.available():
        return None, ("Ohne KI-Schlüssel (ANTHROPIC_API_KEY) schreibt niemand Entwürfe. Setzt „Entwürfe“ auf 0: "
                      "Die Bewertung läuft ohne KI.")
    c = conn()
    try:
        if RUN["id"] or c.execute("SELECT 1 FROM runs WHERE status='wartet'").fetchone():
            return None, "Es läuft schon ein Lauf."
        left = month_budget_left_eur(c)
        if params.get("entwuerfe") and left is not None and left <= 0:
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


def _eur(value):
    return f"{value:.2f}".replace(".", ",") + " €"


def execute_run(c, run_id):
    run = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    p = json.loads(run["params"])
    check, drafts, budget = int(p.get("bewerten", 0)), int(p.get("entwuerfe", 0)), float(p.get("budget_eur", 0))
    use_ai = ai.available()
    top = int(p.get("top", S("LEADS_RUN_TOP")))
    counts = {"neu_gefunden": 0, "grobfilter": 0, "bewertet": 0, "passen": 0, "ins_crm": 0, "entwuerfe": 0,
              "erinnerungen": 0}
    RUN.update(id=run_id, cancel=False, by=run["started_by"])
    _local.run_id = run_id
    c.execute("UPDATE runs SET status='laeuft' WHERE id=?", (run_id,))
    c.commit()
    status, message = "fertig", None
    try:
        # Schritt 1: suchen und grob filtern (kostenlos). Neu suchen auch, wenn eine gewählte Nische noch nie
        # gesucht wurde. Welche Auswahl gilt: die der manuellen oder der automatischen Suche (params „nischen“)
        niches.backfill(c)
        weights = niches.load(c, p.get("nischen", "manuell"))
        searched = (leaddb.kv_get(c, "last_search") or {}).get("niches")
        marks = ",".join("?" * len(weights))
        waiting = c.execute(f"SELECT 1 FROM leads WHERE status='kandidat' AND (niche IN ({marks}) "
                            "OR source_id LIKE 'claude:%')", list(weights)).fetchone()
        if p.get("suchen") or (check and (not waiting or (searched is not None and not set(weights) <= set(searched)))):
            counts["neu_gefunden"], counts["grobfilter"] = run_search(c)
        counts["grobfilter"] += learned_filter(c)
        ensure_boundaries(c)
        assign_kreise(c)

        # Schritt 2: nach Regeln bewerten (kostenlos, nur die Webseite wird gelesen)
        learned = scoring.learned_adjustments(leaddb.category_outcomes(c))
        plan = {"weights": weights, "taken": {}}
        for i in range(check):
            if RUN["cancel"]:
                raise BudgetStop("Von Hand gestoppt.")
            lead_id = next_candidate(c, plan)
            if not lead_id:
                break
            niche = leaddb.get_lead(c, lead_id)["niche"]
            plan["taken"][niche] = plan["taken"].get(niche, 0) + 1
            _progress(f"Schritt 2/3 · Bewertet {i + 1}/{check}: {leaddb.get_lead(c, lead_id)['name']} (kostenlos)")
            counts["bewertet"] += 1
            counts["passen"] += stage_score(c, lead_id, learned, promote=False)
        counts["ins_crm"] = promote_best(c, top)

        # Schritt 3 (nur auf Wunsch): Entwürfe für die besten passenden Betriebe ohne Nachricht (KI)
        if drafts and use_ai:
            brain = leaddb.brain(c)
            best = c.execute("SELECT id, name FROM leads WHERE status='passt' AND interest IS NULL AND IFNULL(body,'')='' "
                             "ORDER BY stage IS NOT NULL DESC, fit_score DESC, distance_km LIMIT ?", (drafts,)).fetchall()
            for i, row in enumerate(best):
                check_budget(c, run_id, budget, "entwurf")
                _progress(f"Schritt 3/3 · Schreibt Entwurf {i + 1}/{len(best)}: {row['name']}")
                counts["entwuerfe"] += stage_write(c, row["id"], brain)

        # Erinnerungen für Betriebe, die seit einer Woche nicht geantwortet haben (KI, kurz)
        if p.get("erinnerungen") and use_ai:
            due = due_followups(c, without_text=True)
            for i, lead in enumerate(due):
                check_budget(c, run_id, budget, "erinnerung")
                _progress(f"Erinnerung {i + 1}/{len(due)}: {lead['name']}")
                write_followup(c, lead["id"])
                counts["erinnerungen"] += 1

        # Wenn die KI geschrieben hat: aus euren Rückmeldungen den Stil lernen
        if (counts["entwuerfe"] or counts["erinnerungen"]) \
                and c.execute("SELECT 1 FROM signals WHERE processed=0").fetchone():
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
    finally:
        c.execute("UPDATE leads SET status='fehler', updated_at=? WHERE status='recherche'", (leaddb.now(),))
        _local.run_id = None
        cost = ai.eur(leaddb._sum(c, "run_id = ?", (run_id,))["usd"])
        summary = (f"{counts['bewertet']} bewertet, {counts['passen']} passen, {counts['ins_crm']} davon ins CRM"
                   + (f", {counts['entwuerfe']} Entwürfe" if counts["entwuerfe"] else "")
                   + (f", {counts['erinnerungen']} Erinnerungen" if counts["erinnerungen"] else "")
                   + (f". KI-Kosten: {_eur(cost)}." if cost else ". Ohne KI-Kosten."))
        c.execute("UPDATE runs SET ended_at=?, status=?, message=?, counts=? WHERE id=?",
                  (leaddb.now(), status, " ".join(x for x in (message, summary) if x), _json(counts), run_id))
        c.commit()
        RUN.update(id=None, cancel=False, progress="")
        title = {"fertig": "Lauf fertig", "gestoppt": "Lauf gestoppt", "fehler": "Lauf mit Fehler beendet"}[status]
        quiet = (run["started_by"] or "").startswith("Automatik") and status == "fertig" \
            and not (counts["ins_crm"] or counts["entwuerfe"] or counts["erinnerungen"])
        if not quiet:  # automatische Läufe melden sich nur, wenn etwas Neues da ist
            notify(c, title, " ".join(x for x in (message, summary) if x),
                   "/leads?tab=entwurf" if counts["entwuerfe"] else "/crm")

# ---------------------------------------------------------------- Versand


DESIGN_DEFAULT = {"template": "brief", "graphic": "none", "icon": "werkstatt", "band_seed": 3}
GRAPHICS = {"none": "Keine Grafik", "band_original": "Pixel-Band Ylva", "band": "Pixel-Band, Variante", "icon": "Symbol"}


def design_of(lead):
    """Gewähltes Design der Mail (im Mail-Fenster änderbar), sonst ruhige Kurz-Mail ohne Grafik."""
    try:
        stored = json.loads(lead.get("mail_design") or "{}")
    except ValueError:
        stored = {}
    return {**DESIGN_DEFAULT, **{k: v for k, v in stored.items() if k in DESIGN_DEFAULT}}


def lead_form(lead, snd=None):
    """Die Felder für den Mail-Baukasten des Mailers, im gewählten Design. `snd`: Absender aus team.sender."""
    d = design_of(lead)
    snd = snd or {}
    return {
        "template": d["template"], "graphic": d["graphic"], "icon": d["icon"], "band_seed": str(d["band_seed"]),
        "subject": lead["subject"] or "", "preheader": "", "headline": "",
        "greeting": lead["greeting"] or "", "body": lead["body"] or "", "body2": "",
        "closing": snd.get("gruss") or "Viele Grüße", "sender_name": snd.get("name") or S("LEADS_SENDER_NAME"),
        "from_name": snd.get("display") or "",
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


def send_outreach(c, lead, sent_by, followup=False, sender=None):
    if not (S("OUTREACH_EMAIL") and outreach_configured()):
        raise RuntimeError("Der Versand per Mail ist ausgeschaltet (OUTREACH_EMAIL).")
    if not lead["email"]:
        raise RuntimeError("Für diesen Betrieb ist keine E-Mail-Adresse bekannt.")
    if followup and not lead["followup_body"]:
        raise RuntimeError("Es gibt noch keinen Text für die Erinnerung.")
    rcpt = {"name": lead["contact_name"] or "", "first": "", "email": lead["email"]}
    snd = team.sender(c, sender or lead.get("owner"), S("ORG_NAME")) if (sender or lead.get("owner")) else None
    form = {**(followup_form(lead) if followup else lead_form(lead)), **({} if not snd else {
        "closing": snd["gruss"], "sender_name": snd["name"], "from_name": snd["display"]})}
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
    crm.advance(c, lead["id"], "kontaktiert")

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
        row = c.execute("SELECT id FROM leads WHERE message_id=? OR followup_message_id=?", (mid, mid)).fetchone() \
            or c.execute("SELECT lead_id AS id FROM messages WHERE message_id=?", (mid,)).fetchone()
        if row:
            return row["id"]
    sender = parseaddr(msg.get("From", ""))[1].lower()
    if not sender:
        return None
    row = c.execute("SELECT id FROM leads WHERE lower(email)=? ORDER BY status IN ('versendet','beantwortet') DESC, "
                    "sent_at DESC, id DESC LIMIT 1", (sender,)).fetchone()
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


INBOX_START_DAYS = 3  # beim ersten Abruf: Mails der letzten drei Tage übernehmen, ältere nicht
_inbox_lock = threading.Lock()
BULK_SENDER = re.compile(r"^(?:no-?reply|do-?not-?reply|mailer-daemon|postmaster|bounces?|newsletter|news|"
                         r"notifications?|info-?mail)\b", re.I)


def _is_bulk(msg):
    """Newsletter, Rundmails, Zustellfehler und automatische Mails von Unbekannten gehören nicht ins Postfach."""
    if msg.get("List-Unsubscribe") or msg.get("List-Id"):
        return True
    if (msg.get("Precedence") or "").strip().lower() in ("bulk", "list", "junk"):
        return True
    if (msg.get("Auto-Submitted") or "no").strip().lower() != "no":
        return True
    local = parseaddr(msg.get("From", ""))[1].split("@")[0]
    return bool(BULK_SENDER.match(local))


def _contact_for(c, msg):
    """Unbekannter Absender: als Kontakt anlegen (nicht in der Pipeline, nicht in den Leads-Reitern)."""
    name, addr = parseaddr(msg.get("From", ""))
    addr = addr.lower()
    row = c.execute("SELECT id FROM leads WHERE lower(email)=? ORDER BY id DESC LIMIT 1", (addr,)).fetchone()
    if row:
        return row["id"]
    now = leaddb.now()
    cur = c.execute(
        "INSERT OR IGNORE INTO leads (source_id, created_at, updated_at, name, branche, category, category_label, "
        "email, contact_name, status, fit_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (f"mail:{addr}", now, now, name or addr, "sonstige", "", "Kontakt", addr, name or None, "kontakt",
         "Hat uns geschrieben."))
    c.commit()
    if cur.rowcount:
        leaddb.add_event(c, cur.lastrowid, "status", "Hat uns eine Mail geschrieben, als Kontakt angelegt.", author="Postfach")
        return cur.lastrowid
    return c.execute("SELECT id FROM leads WHERE source_id=?", (f"mail:{addr}",)).fetchone()["id"]


def check_inbox(c):
    """Neue Mails aus dem Akquise-Postfach holen. Antworten von Betrieben landen in ihrem Gespräch, Mails von
    Unbekannten als neuer Kontakt im Postfach. Newsletter und automatische Mails bleiben draußen."""
    if not _inbox_lock.acquire(timeout=60):
        return 0
    try:
        return _check_inbox(c)
    finally:
        _inbox_lock.release()


def _check_inbox(c):
    state = leaddb.kv_get(c, "inbox_state") or {}
    if not state.get("start"):
        state["start"] = (datetime.now() - timedelta(days=INBOX_START_DAYS)).isoformat(timespec="seconds")
        leaddb.kv_set(c, "inbox_state", state)
    start = datetime.fromisoformat(state["start"])
    since = max(start, datetime.now() - timedelta(days=30)) - timedelta(days=1)
    own = (outreach_address() or "").lower()
    set_activity("Liest das Postfach …")
    imap = _imap()
    handled = 0
    try:
        imap.select("INBOX", readonly=True)
        _, data = imap.search(None, f"(SINCE {since.day:02d}-{MONTHS[since.month - 1]}-{since.year})")
        for num in (data[0] or b"").split()[-300:]:
            _, header = imap.fetch(num, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
            mid_raw = next((p[1] for p in header if isinstance(p, tuple)), b"")
            mid = email.message_from_bytes(mid_raw).get("Message-ID", "").strip() or f"num:{num.decode()}:{state['start']}"
            if c.execute("SELECT 1 FROM inbox_seen WHERE message_id=?", (mid,)).fetchone():
                continue
            _, parts = imap.fetch(num, "(BODY.PEEK[])")
            raw = next((p[1] for p in parts if isinstance(p, tuple)), b"")
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            if _take(c, msg, mid, start, own):
                handled += 1
            c.execute("INSERT OR IGNORE INTO inbox_seen (message_id, at) VALUES (?,?)", (mid, leaddb.now()))
            c.commit()
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return handled


def _take(c, msg, mid, start, own):
    """Eine neue Mail einsortieren. Gibt True zurück, wenn sie im Postfach gelandet ist."""
    sender = parseaddr(msg.get("From", ""))[1].lower()
    if not sender or sender == own or str(msg.get("Subject", "")).startswith("[Leads]"):
        return False  # eigene Mails und unsere Benachrichtigungen, sonst schreibt sich die App selbst
    try:
        sent = parsedate_to_datetime(msg.get("Date")).astimezone().replace(tzinfo=None)
    except (TypeError, ValueError):
        sent = None
    if sent and sent < start:
        return False  # älter als der erste Abruf: steht schon in eurem Mailprogramm
    lead_id = _match_lead(c, msg)
    if not lead_id:
        if _is_bulk(msg):
            return False
        lead_id = _contact_for(c, msg)
    return bool(handle_reply(c, lead_id, msg, mid))


def handle_reply(c, lead_id, msg, message_id=None):
    """Antwort einer Firma: ins Postfach und in die Akte, nach Stichworten einordnen (ohne KI), Pipeline und
    Wiedervorlage setzen. Weitere Antworten im laufenden Gespräch landen nur im Postfach (mit Hinweis)."""
    lead = leaddb.get_lead(c, lead_id)
    name, addr = parseaddr(msg.get("From", ""))
    text = _message_text(msg)
    subject = str(msg.get("Subject", ""))
    mailbox.add(c, lead_id, "in", text or "(leere Nachricht)", subject=subject, from_addr=addr, from_name=name,
                message_id=(message_id or msg.get("Message-ID") or "").strip() or None,
                in_reply_to=(msg.get("In-Reply-To") or "").strip() or None, read=False)
    if lead["status"] in ("beantwortet", "gesperrt", "kontakt") or lead.get("interest"):  # Gespräch läuft schon
        leaddb.update_lead(c, lead_id, unread=1)
        leaddb.add_event(c, lead_id, "reply", text or "(leere Nachricht)", author=name or addr,
                         meta={"from": addr, "subject": subject, "date": str(msg.get("Date", ""))})
        notify(c, f"Neue Mail von {lead['name']}" if lead["status"] == "kontakt" else f"Neue Antwort von {lead['name']}",
               (text or subject)[:220], f"/crm/postfach/{lead_id}")
        return True
    leaddb.add_event(c, lead_id, "reply", text or "(leere Nachricht)", author=name or addr,
                     meta={"from": addr, "subject": subject, "date": str(msg.get("Date", ""))})
    result = scoring.classify_reply(subject, text, dict(msg.items()))
    kind = result["interesse"]
    leaddb.add_event(c, lead_id, "classify", f"{INTEREST_TITLES[kind]} {result['zusammenfassung']}", author="Regeln",
                     meta={"interesse": kind, "grund": result["grund"], "naechster_schritt": result["naechster_schritt"]})
    if kind == "automatisch":
        return True
    fields = {"status": "beantwortet", "interest": INTEREST_MAP[kind], "interest_reason": result["grund"] or None,
              "replied_at": leaddb.now(), "unread": 1}
    if kind == "abmeldung":
        fields["status"] = "gesperrt"
        block = set(leaddb.kv_get(c, "blocklist", []))
        block.add(addr.lower())
        leaddb.kv_set(c, "blocklist", sorted(block))
    leaddb.update_lead(c, lead_id, **fields)
    if kind in ("qualified", "mittel"):
        crm.advance(c, lead_id, "antwort")
        due, todo = (crm.today(), "Auf die Antwort reagieren: anrufen oder Termin vorschlagen") if kind == "qualified" \
            else (crm.quick_due("1m"), "Antwort lesen und wieder melden")
        crm.add_task(c, lead_id, due, todo, "Regeln")
    else:
        crm.advance(c, lead_id, "verloren")
    leaddb.add_signal(c, "antwort", f"{lead['name']} ({lead['branche']} / {lead['category_label']}, "
                      f"{lead['distance_km']} km, Größe: {lead['research'].get('groesse') or '?'}): {kind}. "
                      f"Antwort: {result['zusammenfassung']}", lead_id)
    notify(c, f"{INTEREST_TITLES[kind]} {lead['name']}", result["zusammenfassung"][:220], f"/crm/postfach/{lead_id}")
    return True


def send_reply(c, lead, text, sent_by, subject=None, sender=None):
    """Im Postfach antworten: schlichte, persönliche Mail im selben Gesprächsverlauf (In-Reply-To), mit Zitat.
    Erlaubt, weil der Betrieb uns zuerst geschrieben hat; braucht nur das Akquise-Postfach."""
    if not outreach_configured():
        raise RuntimeError("Das Akquise-Postfach ist nicht eingerichtet (Einstellungen → Postfach und Versand).")
    last = mailbox.last_incoming(c, lead["id"])
    to = (last or {}).get("from_addr") or lead["email"]
    if not to:
        raise RuntimeError("Keine E-Mail-Adresse bekannt.")
    snd = _sender(c, sender)
    msg = EmailMessage()
    msg["From"] = formataddr((snd["display"], outreach_address()))
    msg["To"] = formataddr(((last or {}).get("from_name") or lead["contact_name"] or "", to))
    msg["Subject"] = subject or mailbox.reply_subject((last or {}).get("subject") or lead["subject"] or lead["name"])
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=_domain(outreach_address()) or None)
    refs = [m for m in ((last or {}).get("message_id"), lead.get("message_id")) if m]
    if refs:
        msg["In-Reply-To"] = refs[0]
        msg["References"] = " ".join(dict.fromkeys(reversed(refs)))
    msg.set_content(f"{text.strip()}\n\n{team.signature(snd)}{mailbox.quote(last)}")
    _deliver(msg)
    record_reply(c, lead, text, snd["name"] or sent_by, msg["Subject"], to, msg["Message-ID"], refs[0] if refs else None)
    _claim(c, lead, snd["key"])
    return msg


def _sender(c, key):
    """Absender aus dem Team; ohne Person der Name aus den Einstellungen (wie bisher)."""
    if key and team.get(c, key):
        return team.sender(c, key, S("ORG_NAME"))
    name = S("LEADS_SENDER_NAME") or S("ORG_NAME")
    return {"key": None, "name": name, "display": name, "gruss": "Viele Grüße",
            "signatur": "\n".join(x for x in (S("LEADS_SENDER_NAME"), S("DEFAULT_FOOTER")) if x)}


def _claim(c, lead, key):
    """Wer zuerst etwas mit einem Betrieb macht, ist zuständig (solange noch niemand zuständig ist)."""
    if key:
        crm.claim(c, lead["id"], key)


def _deliver(msg):
    """Über das Akquise-Postfach verschicken und eine Kopie in „Gesendet“ ablegen. Fehler mit klarer Meldung."""
    try:
        smtp = CFG["smtp_connect"](S("OUTREACH_USER"), S("OUTREACH_PASSWORD"))
        try:
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except Exception:
                pass
    except Exception as exc:
        raise RuntimeError(mail_error(exc)) from exc
    if S("IMAP_HOST"):
        try:
            imap = _imap()
            try:
                imap.append(CFG["sent_folder"](imap), "\\Seen", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            finally:
                imap.logout()
        except Exception as exc:
            log.warning("Ablage im Ordner „Gesendet“ fehlgeschlagen: %s", exc)


def send_new(c, lead, to, subject, text, sent_by, sender=None):
    """Neue Mail aus dem Postfach (nicht als Antwort): schlicht, mit Unterschrift, unter dem Namen von Ylva Labs."""
    if not outreach_configured():
        raise RuntimeError("Das Akquise-Postfach ist nicht eingerichtet (Einstellungen → Postfach und Versand).")
    snd = _sender(c, sender)
    msg = EmailMessage()
    msg["From"] = formataddr((snd["display"], outreach_address()))
    msg["To"] = formataddr((lead.get("contact_name") or "", to))
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=_domain(outreach_address()) or None)
    msg.set_content(f"{text.strip()}\n\n{team.signature(snd)}")
    _deliver(msg)
    record_reply(c, lead, text, snd["name"] or sent_by, subject, to, msg["Message-ID"], what="Neue Mail aus dem Postfach")
    _claim(c, lead, snd["key"])
    return msg


def record_reply(c, lead, text, sent_by, subject, to, message_id=None, in_reply_to=None, note="",
                 what="Im Postfach geantwortet"):
    mailbox.add(c, lead["id"], "out", text.strip(), subject=subject, from_addr=outreach_address(), to_addr=to,
                message_id=message_id, in_reply_to=in_reply_to, sent_by=sent_by)
    mailbox.mark_read(c, lead["id"])
    leaddb.update_lead(c, lead["id"], unread=0)
    leaddb.add_event(c, lead["id"], "sent", f"{what} an {to}.{note}", author=sent_by)

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


def mail_error(exc):
    """Verständliche Meldung, wenn Senden oder Abrufen am Postfach scheitert."""
    text = str(exc)
    if isinstance(exc, smtplib.SMTPAuthenticationError) or "AUTHENTICATIONFAILED" in text or \
            "Password required" in text or "authentication failed" in text.lower():
        return ("Strato lehnt die Anmeldung ab: Benutzer oder Passwort des Akquise-Postfachs stimmen nicht. "
                "Bitte unter Einstellungen → Postfach und Versand → Akquise-Postfach neu eintragen.")
    if isinstance(exc, (socket.timeout, TimeoutError, ConnectionError, socket.gaierror, ssl.SSLError)):
        return f"Der Mailserver ist nicht erreichbar ({exc.__class__.__name__}). Server und Port in den Einstellungen prüfen."
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "Die Empfängeradresse wurde abgelehnt. Bitte prüfen."
    return f"{exc.__class__.__name__}: {text}"


def fetch_inbox(c):
    """Postfach abrufen und das Ergebnis merken (für den Hinweis im Postfach und an der Glocke)."""
    try:
        n = check_inbox(c)
        leaddb.kv_set(c, "inbox_status", {"at": leaddb.now(), "ok": True, "neu": n})
        return n, None
    except Exception as exc:
        log.warning("Postfach abrufen fehlgeschlagen: %s", exc)
        msg = mail_error(exc)
        leaddb.kv_set(c, "inbox_status", {"at": leaddb.now(), "ok": False, "error": msg})
        return 0, msg


def tick():
    with _job_lock:
        c = conn()
        try:
            # Antworten lesen ist kostenlos, nur das Einordnen einer echten Antwort kostet ein paar Cent
            if outreach_configured() and S("IMAP_HOST"):
                last = leaddb.kv_get(c, "last_inbox", 0)
                if time.time() - last > S("INBOX_MINUTES") * 60 or leaddb.kv_get(c, "request:inbox"):
                    leaddb.kv_set(c, "last_inbox", time.time())
                    leaddb.kv_set(c, "request:inbox", False)
                    fetch_inbox(c)
            notify_due(c)
            notify_tasks(c)
            start_scheduled(c)
            run = c.execute("SELECT id FROM runs WHERE status='wartet' ORDER BY id LIMIT 1").fetchone()
            if run:
                execute_run(c, run["id"])
            if leaddb.kv_get(c, "request:reflect") and ai.available():
                leaddb.kv_set(c, "request:reflect", False)
                maybe_reflect(c, force=True)
            if leaddb.kv_get(c, "request:kreise"):
                leaddb.kv_set(c, "request:kreise", False)
                ensure_boundaries(c, force=True)
                assign_kreise(c, everything=True)
            wanted = leaddb.kv_get(c, "request:lead")
            if wanted:
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


def start_scheduled(c, now=None):
    """Zeitplan (Leads → Neue Betriebe finden → Automatisch): fälligen Lauf anlegen. Höchstens einer pro Minute."""
    now = now or datetime.now()
    state = leaddb.kv_get(c, "schedule_state", {}) or {}
    started = None
    for kind, cfg in schedule.load(c).items():
        last = (state.get(kind) or {}).get("last")
        if not schedule.due(cfg, now, last):
            continue
        stamp = now.isoformat(timespec="seconds")
        if kind == "ki" and not ai.available():
            state[kind] = {"last": stamp, "note": "Kein KI-Schlüssel eingetragen, KI-Lauf übersprungen."}
            continue
        params, why = schedule.params_for(kind, cfg, c, now, ai.eur)
        if not params:
            state[kind] = {"last": stamp, "note": why}
            continue
        run_id, error = start_run(params, schedule.STARTED_BY[kind])
        if error and "läuft schon" in error:
            continue  # nächste Minute noch einmal
        state[kind] = {"last": stamp, "note": error or "", "run_id": run_id}
        if run_id:
            started = run_id
            break
    leaddb.kv_set(c, "schedule_state", state)
    return started


def ensure_boundaries(c, force=False):
    """Kreisgrenzen einmal über OpenStreetMap laden (nur für Schleswig-Holstein). Fehler halten nichts auf."""
    if S("LEADS_REGION") != "DE-SH" or (leaddb.kv_get(c, "kreise_geo") and not force):
        return False
    set_activity("Lädt die Kreisgrenzen von Schleswig-Holstein (einmalig) …")
    try:
        geo = regions.load_boundaries(S("OVERPASS_URL"), S("LEADS_REGION"))
    except Exception as exc:
        log.warning("Kreisgrenzen nicht geladen: %s", exc)
        STATE["error"], STATE["error_at"] = f"Kreisgrenzen konnten nicht geladen werden ({exc}).", time.time()
        return False
    if len(geo) < 10:  # unvollständig: lieber nicht speichern
        log.warning("Nur %s Kreise in der Antwort, Grenzen nicht gespeichert", len(geo))
        return False
    leaddb.kv_set(c, "kreise_geo", geo)
    return True


def assign_kreise(c, everything=False):
    """Jedem Betrieb mit Koordinaten seinen Kreis zuordnen (Punkt in Kreisgrenze)."""
    geo = leaddb.kv_get(c, "kreise_geo")
    if not geo:
        return 0
    where = "lat IS NOT NULL" + ("" if everything else " AND kreis IS NULL")
    rows = c.execute(f"SELECT id, lat, lon FROM leads WHERE {where}").fetchall()
    for r in rows:
        c.execute("UPDATE leads SET kreis=? WHERE id=?", (regions.kreis_of(geo, r["lat"], r["lon"]), r["id"]))
    c.commit()
    return len(rows)


def recheck_lead(c, lead_id):
    """Einen einzelnen Betrieb von Hand neu bewerten lassen (kostenlos)."""
    set_activity(f"Bewertet neu: {leaddb.get_lead(c, lead_id)['name']}")
    stage_score(c, lead_id, scoring.learned_adjustments(leaddb.category_outcomes(c)))


def notify_tasks(c):
    """Kostenlos: einmal Bescheid geben, wenn Wiedervorlagen fällig werden."""
    told = set(leaddb.kv_get(c, "tasks_notified", []))
    new = [t for t in crm.open_tasks(c, until=crm.today()) if t["id"] not in told]
    if not new:
        return 0
    leaddb.kv_set(c, "tasks_notified", sorted(told | {t["id"] for t in new})[-500:])
    names = ", ".join(t["name"] for t in new[:3]) + (" …" if len(new) > 3 else "")
    notify(c, f"Wiedervorlage: {len(new)} heute dran", names, "/crm")
    return len(new)


def _loop():
    time.sleep(3)
    c = conn()
    upgrade_template(c)
    c.execute("UPDATE leads SET status='kandidat' WHERE status='recherche'")  # nach Neustart
    c.execute("UPDATE runs SET status='abgebrochen', ended_at=?, message='Server wurde neu gestartet.' "
              "WHERE status='laeuft'", (leaddb.now(),))
    c.commit()
    c.close()
    while True:
        tick()
        _wake.wait(timeout=60)
        _wake.clear()
