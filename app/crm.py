"""CRM: Pipeline (Stufen), Wiedervorlagen, Firmen von Hand anlegen, CSV-Import und -Export.

Die Stufe (`leads.stage`) ist eure Sicht auf den Verkauf und unabhängig vom Arbeitsstand der Akquise (`status`).
Ereignisse schieben einen Betrieb automatisch weiter (gesendet → Kontaktiert, Antwort → Antwort, Absage → Verloren),
aber nie zurück. Von Hand dürft ihr jede Stufe setzen.
"""
import csv
import hashlib
import io
import json
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

from . import finder, known, leaddb, regions

STAGES = [
    ("neu", "Neu", "Passt, noch nicht kontaktiert."),
    ("kontaktiert", "Kontaktiert", "Erste Nachricht ist raus."),
    ("antwort", "Antwort", "Hat geantwortet."),
    ("gespraech", "Gespräch", "Erstgespräch vereinbart oder geführt."),
    ("angebot", "Angebot", "Pilot oder Angebot liegt vor."),
    ("kunde", "Kunde", "Gewonnen."),
    ("verloren", "Verloren", "Absage oder kein Bedarf."),
]
STAGE_KEYS = [k for k, _, _ in STAGES]
STAGE_LABELS = {k: label for k, label, _ in STAGES}
OPEN_STAGES = STAGE_KEYS[:5]


def set_stage(conn, lead_id, stage, author="Team", note=""):
    """Stufe von Hand setzen (jede Richtung)."""
    if stage not in STAGE_KEYS:
        raise ValueError(stage)
    lead = leaddb.get_lead(conn, lead_id)
    if lead["stage"] == stage:
        return False
    leaddb.update_lead(conn, lead_id, stage=stage, stage_at=leaddb.now())
    text = f"Stufe: {STAGE_LABELS.get(lead['stage'], 'außerhalb der Pipeline')} → {STAGE_LABELS[stage]}."
    leaddb.add_event(conn, lead_id, "stage", f"{text} {note}".strip(), author=author)
    return True


def advance(conn, lead_id, stage, author="Ablauf"):
    """Automatisch weiterschieben, aber nur nach vorn. „Verloren“ gilt nur, solange noch nichts vereinbart ist."""
    lead = leaddb.get_lead(conn, lead_id)
    current = lead["stage"]
    if current == stage:
        return False
    if stage == "verloren":
        if current not in (None, "neu", "kontaktiert", "antwort"):
            return False
    elif current is not None and (current == "verloren" or STAGE_KEYS.index(current) >= STAGE_KEYS.index(stage)):
        return False
    return set_stage(conn, lead_id, stage, author)


def _first_sentences(text, limit=180):
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "))
    return cut[:end + 1] if end > 60 else cut.rsplit(" ", 1)[0] + " …"


def profile(lead):
    """Alles, was wir über einen Betrieb wissen, für Karte, Tabelle und Bewerten. Was die kostenlose Suche nicht
    herausfinden konnte, steht in `fehlt` (dafür braucht es die KI- oder Claude-Recherche)."""
    research = lead.get("research") or {}
    if isinstance(research, str):
        try:
            research = json.loads(research)
        except ValueError:
            research = {}
    imp = research.get("import") or {}
    desc, desc_src = imp.get("taetigkeit") or "", "Claude-Recherche"
    if not desc and research.get("beschreibung"):
        desc, desc_src = research["beschreibung"], "Webseite"
    if not desc and research.get("auszug"):
        desc, desc_src = _first_sentences(research["auszug"]), "Startseite"
    size = research.get("groesse") if research.get("groesse") not in (None, "", "unklar") else ""
    if not size and imp.get("groesse"):
        size = f"ca. {imp['groesse']} Mitarbeitende"
    kreis = regions.KREISE.get(lead.get("kreis") or "", ("",))[0]
    ort = lead.get("city") or (lead.get("address") or "").split(",")[-1].strip()
    host = urlparse(lead.get("website") or "").netloc.removeprefix("www.")
    fehlt = [label for label, value in (("Was sie machen", desc), ("Größe", size),
                                         ("Ansprechperson", lead.get("contact_name")), ("E-Mail", lead.get("email")))
             if not value]
    return {"beschreibung": _first_sentences(desc, 220), "quelle": desc_src if desc else "", "groesse": size,
            "ort": ort, "adresse": lead.get("address") or "", "kreis": kreis, "km": lead.get("distance_km"),
            "kontakt": lead.get("contact_name") or "", "email": lead.get("email") or "", "telefon": lead.get("phone") or "",
            "web": host, "website": lead.get("website") or "",
            "zeitfresser": (research.get("zeitfresser") or imp.get("zeitfresser") or [])[:2],
            "aufhaenger": imp.get("aufhaenger") or "", "punkte": lead.get("fit_score"),
            "grund": lead.get("fit_reason") or "", "kategorie": lead.get("category_label") or "Betrieb",
            "branche": lead.get("branche") or "", "fehlt": fehlt}


def owner_sql(who, me=None):
    """Filter nach Zuständigkeit: "alle" (Gesamt, auch noch niemandem zugeordnete) oder der Schlüssel einer Person
    (nur ihre). „meine“ heißt: die angemeldete Person."""
    if who == "meine":
        who = me
    if who and who != "alle":
        return "l.owner = ?", [who]
    return "1", []


def claim(conn, lead_id, key):
    """Zuständig wird, wer zuerst etwas tut. Schon zugeordnete Betriebe bleiben, wo sie sind."""
    if key and conn.execute("UPDATE leads SET owner=? WHERE id=? AND owner IS NULL", (key, lead_id)).rowcount:
        conn.commit()


def set_owner(conn, lead_id, key, author):
    conn.execute("UPDATE leads SET owner=?, updated_at=? WHERE id=?", (key or None, leaddb.now(), lead_id))
    conn.commit()
    leaddb.add_event(conn, lead_id, "status", f"Zuständig: {key or 'niemand'}.", author=author)


def board(conn, who="alle", me=None):
    """Betriebe in der Pipeline, gruppiert nach Stufe, mit nächster Wiedervorlage. `who`: "alle" oder eine Person."""
    where, args = owner_sql(who, me)
    rows = conn.execute(f"""
        SELECT l.*, (SELECT MIN(due) FROM tasks t WHERE t.lead_id = l.id AND t.done_at IS NULL) AS next_due,
               (SELECT COUNT(*) FROM tasks t WHERE t.lead_id = l.id AND t.done_at IS NULL) AS open_tasks
        FROM leads l WHERE l.stage IS NOT NULL AND {where}
        ORDER BY l.unread DESC, IFNULL(l.stage_at, l.updated_at) DESC""", args).fetchall()
    columns = {k: [] for k in STAGE_KEYS}
    for r in rows:
        d = dict(r)
        d["p"] = profile(d)
        columns.setdefault(r["stage"], []).append(d)
    return [(k, label, hint, columns[k], sum(c["deal_eur"] or 0 for c in columns[k])) for k, label, hint in STAGES]

# ---------------------------------------------------------------- Wiedervorlagen


def add_task(conn, lead_id, due, text, author):
    due = due if isinstance(due, str) else due.isoformat()
    date.fromisoformat(due)  # prüft das Format
    cur = conn.execute("INSERT INTO tasks (lead_id, due, text, created_at, created_by) VALUES (?,?,?,?,?)",
                       (lead_id, due, text.strip(), leaddb.now(), author))
    conn.commit()
    leaddb.add_event(conn, lead_id, "task", f"Wiedervorlage am {fmt_day(due)}: {text.strip()}", author=author)
    return cur.lastrowid


def complete_task(conn, task_id, author):
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task or task["done_at"]:
        return None
    conn.execute("UPDATE tasks SET done_at=?, done_by=? WHERE id=?", (leaddb.now(), author, task_id))
    conn.commit()
    leaddb.add_event(conn, task["lead_id"], "task", f"Erledigt: {task['text']}", author=author)
    return task["lead_id"]


def open_tasks(conn, until=None, lead_id=None, who="alle", me=None):
    owner_where, args = owner_sql(who, me)
    where = ["t.done_at IS NULL", owner_where]
    if until:
        where.append("t.due <= ?")
        args.append(until)
    if lead_id:
        where.append("t.lead_id = ?")
        args.append(lead_id)
    return [dict(r) for r in conn.execute(
        f"SELECT t.*, l.name, l.stage FROM tasks t JOIN leads l ON l.id = t.lead_id WHERE {' AND '.join(where)} "
        "ORDER BY t.due, t.id", args)]


def due_count(conn, me=None):
    """Fällige Wiedervorlagen: ohne Person alle, sonst nur die eigenen."""
    where, args = owner_sql(me or "alle")
    return conn.execute(f"SELECT COUNT(*) FROM tasks t JOIN leads l ON l.id = t.lead_id "
                        f"WHERE t.done_at IS NULL AND t.due <= ? AND {where}",
                        [date.today().isoformat(), *args]).fetchone()[0]


def fmt_day(value):
    d = date.fromisoformat(value[:10])
    delta = (d - date.today()).days
    return {0: "heute", 1: "morgen", -1: "gestern"}.get(delta, d.strftime("%d.%m.%Y"))


def quick_due(key):
    """Kurzwahl im Formular: morgen, 3 Tage, 1 Woche, 2 Wochen, 1 Monat."""
    days = {"morgen": 1, "3t": 3, "1w": 7, "2w": 14, "1m": 30}.get(key)
    return (date.today() + timedelta(days=days)).isoformat() if days else None

# ---------------------------------------------------------------- Firmen anlegen und importieren


def website_url(url):
    url = finder.normalize_url(url)
    return url if urlparse(url).netloc else ""


def find_duplicate(conn, name, email, website):
    if email:
        row = conn.execute("SELECT id, name FROM leads WHERE lower(email)=?", (email.lower(),)).fetchone()
        if row:
            return row
    host = urlparse(website or "").netloc.lower().removeprefix("www.")
    if host:
        for row in conn.execute("SELECT id, name, website FROM leads WHERE website LIKE ?", (f"%{host}%",)):
            if urlparse(row["website"]).netloc.lower().removeprefix("www.") == host:
                return row
    if name:
        return conn.execute("SELECT id, name FROM leads WHERE lower(name)=?", (name.lower(),)).fetchone()
    return None


def create_lead(conn, data, author, source="hand"):
    """Firma von Hand oder per Import anlegen. Gibt (lead_id, None) oder (None, Grund) zurück.
    Solche Firmen habt ihr selbst ausgesucht: Sie kommen direkt als „Neu“ in die Pipeline."""
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    website = website_url(data.get("website") or "")
    if not name:
        return None, "Name fehlt"
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return None, f"ungültige E-Mail-Adresse {email}"
    dup = find_duplicate(conn, name, email, website)
    if dup:
        return None, f"gibt es schon („{dup['name']}“)"
    seen = known.find(conn, name, data.get("city", ""), website, email)
    if seen:
        return None, (f"wurde schon am {seen['first_seen_at'][:10]} gefunden"
                      f"{' von ' + seen['found_by'] if seen.get('found_by') else ''} (steht auf der Merkliste)")
    now = leaddb.now()
    source_id = f"{source}:" + hashlib.sha1(f"{name}|{email}|{website}".lower().encode()).hexdigest()[:16]
    city = (data.get("city") or "").strip()
    address = (data.get("address") or "").strip() or city
    cur = conn.execute(
        "INSERT INTO leads (source_id, created_at, updated_at, name, branche, category, category_label, address, city,"
        " website, email, phone, contact_name, status, stage, stage_at, fit_reason)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (source_id, now, now, name, (data.get("branche") or "").strip().lower() or "sonstige", "",
         (data.get("category_label") or data.get("branche") or "").strip() or "Betrieb", address, city,
         website or None, email or None, (data.get("phone") or "").strip() or None,
         (data.get("contact_name") or "").strip() or None, "passt", "neu", now,
         "Von Hand angelegt." if source == "hand" else "Importiert."))
    lead_id = cur.lastrowid
    known.remember(conn, name, city, website, email, source_id, source, author, lead_id, commit=False)
    conn.commit()
    leaddb.add_event(conn, lead_id, "status", "Von Hand angelegt." if source == "hand" else "Aus CSV importiert.",
                     author=author)
    if (data.get("note") or "").strip():
        leaddb.add_event(conn, lead_id, "note", data["note"].strip(), author=author)
    return lead_id, None


# Spaltennamen, die beim Import erkannt werden (klein geschrieben, ohne Leer- und Sonderzeichen)
IMPORT_ALIASES = {
    "name": ("name", "firma", "firmenname", "unternehmen", "betrieb", "company", "organisation"),
    "email": ("email", "mail", "emailadresse", "e-mail", "kontaktmail"),
    "phone": ("telefon", "tel", "phone", "telefonnummer", "festnetz", "mobil"),
    "website": ("webseite", "website", "web", "url", "homepage", "internet"),
    "contact_name": ("ansprechpartner", "ansprechperson", "kontakt", "kontaktperson", "person", "inhaber",
                     "geschaeftsfuehrer", "geschäftsführer"),
    "city": ("ort", "stadt", "city", "plzort"),
    "address": ("adresse", "anschrift", "strasse", "straße", "address"),
    "branche": ("branche", "industry", "kategorie", "category"),
    "note": ("notiz", "notizen", "bemerkung", "kommentar", "note", "notes"),
}


def _key(header):
    return re.sub(r"[^a-z0-9äöüß-]", "", (header or "").lower().replace("ä", "ae").replace("ö", "oe")
                  .replace("ü", "ue")).replace("-", "") or header


class _Semicolon(csv.excel):
    delimiter = ";"


def import_csv(conn, raw, author):
    """CSV (Excel-Export mit ; oder ,) einlesen. Gibt {neu, uebersprungen: [(zeile, grund)]} zurück."""
    text = raw.decode("utf-8-sig", "replace") if isinstance(raw, bytes) else raw
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
    except csv.Error:
        dialect = _Semicolon
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    mapping = {}
    aliases = {_key(a): field for field, names in IMPORT_ALIASES.items() for a in names}
    for header in reader.fieldnames or []:
        field = aliases.get(_key(header))
        if field and field not in mapping.values():
            mapping[header] = field
    if "name" not in mapping.values():
        raise ValueError("Keine Spalte für den Firmennamen gefunden (z. B. „Firma“ oder „Name“).")
    created, skipped = 0, []
    for line, row in enumerate(reader, start=2):
        data = {field: (row.get(header) or "").strip() for header, field in mapping.items()}
        if not any(data.values()):
            continue
        lead_id, reason = create_lead(conn, data, author, source="import")
        if lead_id:
            created += 1
        else:
            skipped.append((line, f"{data.get('name') or '?'}: {reason}"))
    return {"neu": created, "uebersprungen": skipped}


EXPORT_COLUMNS = [
    ("Firma", "name"), ("Stufe", "stage_label"), ("Punkte", "fit_score"), ("Branche", "branche"),
    ("Kategorie", "category_label"), ("Ansprechperson", "contact_name"), ("E-Mail", "email"), ("Telefon", "phone"),
    ("Webseite", "website"), ("Adresse", "address"), ("Entfernung km", "distance_km"), ("Kontaktiert am", "sent_at"),
    ("Antwort am", "replied_at"), ("Einordnung", "interest_label"), ("Auftragswert €", "deal_eur"),
    ("Nächste Wiedervorlage", "next_due"), ("Einschätzung", "fit_reason"),
]


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}".replace(".", ",")  # deutsches Excel erwartet Komma
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        # Namen und Texte kommen von fremden Webseiten: „=HYPERLINK(…)“ soll in Excel Text bleiben, keine Formel
        return "'" + value
    return value


def export_csv(conn, everything=False):
    """CSV für Excel (Semikolon, UTF-8 mit BOM). Standard: alle Betriebe in der Pipeline."""
    where = "1" if everything else "stage IS NOT NULL"
    rows = conn.execute(f"""SELECT l.*, (SELECT MIN(due) FROM tasks t WHERE t.lead_id=l.id AND t.done_at IS NULL)
                            AS next_due FROM leads l WHERE {where} ORDER BY name""").fetchall()
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([h for h, _ in EXPORT_COLUMNS])
    for r in rows:
        d = dict(r, stage_label=STAGE_LABELS.get(r["stage"], ""),
                 interest_label=leaddb.INTEREST_LABELS.get(r["interest"], ""))
        writer.writerow([_cell(d.get(k)) for _, k in EXPORT_COLUMNS])
    return "﻿" + out.getvalue()

# ---------------------------------------------------------------- Gestaltete Mails aus dem Mailer


def may_email(lead, email_enabled):
    """Darf an diesen Betrieb eine Mail gehen? Erstkontakt nur, wenn OUTREACH_EMAIL an ist (§ 7 UWG).
    Hat der Betrieb schon geantwortet oder ist im Gespräch, ist weiterer Mailverkehr in Ordnung."""
    if not lead.get("email"):
        return False, "Für diesen Betrieb ist keine E-Mail-Adresse bekannt."
    if lead["status"] == "gesperrt":
        return False, "Dieser Betrieb möchte keine Nachrichten mehr bekommen."
    replied = lead.get("replied_at") or lead.get("interest") or lead.get("stage") in ("antwort", "gespraech",
                                                                                   "angebot", "kunde")
    if not replied and not email_enabled:
        return False, ("Der Erstkontakt per Mail ist ausgeschaltet (OUTREACH_EMAIL=false, § 7 UWG). "
                       "Testmails an dich gehen trotzdem.")
    return True, ""


def record_mail(conn, lead_id, msg, author):
    """Nach dem Versand einer gestalteten Mail aus „Verfassen“: in der Akte vermerken und weiterschieben."""
    lead = leaddb.get_lead(conn, lead_id)
    fields = {"unread": 0}
    if lead["status"] in ("kandidat", "passt", "entwurf", "aussortiert", "fehler"):
        fields.update(status="versendet", channel="email", sent_at=leaddb.now(), message_id=msg["Message-ID"],
                      subject=lead["subject"] or str(msg["Subject"]))
    leaddb.update_lead(conn, lead_id, **fields)
    leaddb.add_event(conn, lead_id, "sent", f"Gestaltete Mail „{msg['Subject']}“ an {lead['email']} gesendet.",
                     author=author, meta={"message_id": msg["Message-ID"]})
    advance(conn, lead_id, "kontaktiert")


def today():
    return datetime.now().date().isoformat()

# ---------------------------------------------------------------- Karte: wo liegen die passenden Betriebe?

PASSEND = ("passt", "entwurf", "versendet", "beantwortet")


def map_points(conn):
    """Alle bewerteten Betriebe mit Koordinaten, für die Heatmap."""
    rows = conn.execute("""SELECT id, name, lat, lon, fit_score, status, stage, interest, city, category_label, branche, kreis
                           FROM leads WHERE lat IS NOT NULL AND lon IS NOT NULL AND NOT (lat = 0 AND lon = 0)
                             AND fit_score IS NOT NULL""").fetchall()
    points = []
    for r in rows:
        d = dict(r)
        d["passend"] = (d["status"] in PASSEND or d["stage"] is not None) and d["stage"] != "verloren" \
            and d["interest"] != "kein_interesse"
        points.append(d)
    return points


def _km(a, b):
    # In Schleswig-Holstein reicht die flache Näherung: 1° Breite ≈ 111 km, 1° Länge ≈ 65 km
    return (((a["lat"] - b["lat"]) * 111) ** 2 + ((a["lon"] - b["lon"]) * 65) ** 2) ** 0.5


def hotspots(points, radius_km=12, limit=8):
    """Gegenden mit den meisten passenden Betrieben, benannt nach dem häufigsten Ort darin. Gedacht für
    Vor-Ort-Tage: mehrere Termine an einem Tag. Immer die dichteste Stelle zuerst, dann alles im Umkreis von
    radius_km dazu, dann die nächste dichteste Stelle unter den übrigen Betrieben."""
    pts = {p["id"]: p for p in points if p["passend"]}
    cell = lambda p: (int(p["lat"] * 111 // radius_km), int(p["lon"] * 65 // radius_km))  # noqa: E731
    grid = {}
    for p in pts.values():
        grid.setdefault(cell(p), []).append(p)
    near = {}  # Nachbarn im Umkreis (inklusive sich selbst), über die umliegenden Rasterzellen gesucht
    for pid, p in pts.items():
        ci, cj = cell(p)
        near[pid] = [q["id"] for di in (-1, 0, 1) for dj in (-1, 0, 1) for q in grid.get((ci + di, cj + dj), [])
                     if _km(p, q) <= radius_km]
    count = {pid: len(ids) for pid, ids in near.items()}
    alive, result = set(pts), []
    while alive and len(result) < limit:
        center = max(alive, key=lambda pid: (count[pid], pts[pid]["fit_score"] or 0, -pid))
        items = sorted((pts[q] for q in near[center] if q in alive), key=lambda p: -(p["fit_score"] or 0))
        for q in items:
            alive.discard(q["id"])
            for w in near[q["id"]]:
                count[w] -= 1
        towns = [p["city"] for p in items if p["city"]]
        result.append({
            "name": max(set(towns), key=towns.count) if towns else "ohne Ortsangabe", "count": len(items),
            "avg": round(sum(p["fit_score"] or 0 for p in items) / len(items)),
            "lat": sum(p["lat"] for p in items) / len(items), "lon": sum(p["lon"] for p in items) / len(items),
            "in_pipeline": sum(1 for p in items if p["stage"]),
            "top": [{"id": p["id"], "name": p["name"], "score": p["fit_score"]} for p in items[:5]],
        })
    return result
