"""Datenbank für die KI-Akquise: Firmen (Leads), Verlauf, Gehirn, Push-Abos."""
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRAIN_SEED = Path(__file__).resolve().parent / "brain_seed.md"

# Reihenfolge = Reihenfolge in der Übersicht
TABS = [
    ("qualified", "Qualified", "Hat Interesse. Jetzt selbst weiterschreiben."),
    ("mittel", "Mittel", "Vielleicht später, Rückfrage oder unklar."),
    ("kein_interesse", "Kein Interesse", "Abgesagt oder möchte nicht mehr kontaktiert werden."),
    ("entwurf", "Zur Prüfung", "Nachricht ist fertig und wartet auf eure Freigabe."),
    ("versendet", "Versendet", "Raus, wir warten auf Antwort."),
    ("arbeit", "In Bearbeitung", "Gefunden, noch nicht fertig recherchiert."),
    ("aussortiert", "Aussortiert", "Passt nicht, von der KI oder von euch aussortiert."),
]

STATUS_LABELS = {
    "kandidat": "Gefunden", "recherche": "Wird recherchiert", "vorgeprueft": "Vorgeprüft",
    "analysiert": "Analysiert", "entwurf": "Zur Prüfung",
    "versendet": "Versendet", "beantwortet": "Beantwortet", "aussortiert": "Aussortiert",
    "gesperrt": "Gesperrt", "fehler": "Fehler",
}
INTEREST_LABELS = {"qualified": "Qualified", "mittel": "Mittel", "kein_interesse": "Kein Interesse"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT UNIQUE,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    name TEXT NOT NULL, branche TEXT, category TEXT, category_label TEXT,
    address TEXT, city TEXT, lat REAL, lon REAL, distance_km REAL,
    website TEXT, email TEXT, phone TEXT,
    status TEXT NOT NULL DEFAULT 'kandidat',
    interest TEXT, interest_reason TEXT,
    fit_score INTEGER, fit_reason TEXT, research TEXT, rejected_by TEXT,
    contact_name TEXT, subject TEXT, greeting TEXT, body TEXT,
    channel TEXT, sent_at TEXT, message_id TEXT, replied_at TEXT,
    unread INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS leads_status ON leads(status);
CREATE TABLE IF NOT EXISTS lead_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL, at TEXT NOT NULL,
    kind TEXT NOT NULL, author TEXT, text TEXT, meta TEXT
);
CREATE INDEX IF NOT EXISTS lead_events_lead ON lead_events(lead_id);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL, lead_id INTEGER, kind TEXT NOT NULL, text TEXT NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS brain_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL, author TEXT NOT NULL, reason TEXT, content TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS push_subs (
    endpoint TEXT PRIMARY KEY, data TEXT NOT NULL, user TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inbox_seen (message_id TEXT PRIMARY KEY, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL, ended_at TEXT, started_by TEXT,
    params TEXT, status TEXT NOT NULL DEFAULT 'laeuft', message TEXT, counts TEXT
);
CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL, run_id INTEGER, lead_id INTEGER, stage TEXT NOT NULL, model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0, cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ai_usage_at ON ai_usage(at);
"""

# Spalten, die nach der ersten Version dazugekommen sind
MIGRATIONS = [
    "ALTER TABLE leads ADD COLUMN quick_score INTEGER",
    "ALTER TABLE leads ADD COLUMN quick_reason TEXT",
]


def now():
    return datetime.now().isoformat(timespec="seconds")


def connect(path):
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    for stmt in MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # gibt es schon
    return conn

# ---------------------------------------------------------------- Kleinkram


def kv_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def kv_set(conn, key, value):
    conn.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, json.dumps(value, ensure_ascii=False)))
    conn.commit()


def add_event(conn, lead_id, kind, text, author="KI", meta=None):
    conn.execute("INSERT INTO lead_events (lead_id, at, kind, author, text, meta) VALUES (?,?,?,?,?,?)",
                 (lead_id, now(), kind, author, text, json.dumps(meta, ensure_ascii=False) if meta else None))
    conn.commit()


def add_signal(conn, kind, text, lead_id=None):
    """Etwas, woraus die KI lernen soll (Feedback, Änderung, Antwort einer Firma …)."""
    conn.execute("INSERT INTO signals (at, lead_id, kind, text) VALUES (?,?,?,?)", (now(), lead_id, kind, text))
    conn.commit()


def update_lead(conn, lead_id, **fields):
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE leads SET {cols} WHERE id=?", (*fields.values(), lead_id))
    conn.commit()


def get_lead(conn, lead_id):
    row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not row:
        return None
    lead = dict(row)
    lead["research"] = json.loads(lead["research"]) if lead["research"] else {}
    return lead


def tab_of(lead):
    if lead["status"] == "beantwortet" or lead.get("interest"):
        return lead.get("interest") or "mittel"
    if lead["status"] == "gesperrt":
        return "kein_interesse"
    if lead["status"] in ("kandidat", "recherche", "fehler", "vorgeprueft", "analysiert"):
        return "arbeit"
    return lead["status"]


TAB_SQL = {
    "qualified": "interest='qualified'",
    "mittel": "interest='mittel' OR (status='beantwortet' AND interest IS NULL)",
    "kein_interesse": "interest='kein_interesse' OR status='gesperrt'",
    "entwurf": "status='entwurf' AND interest IS NULL",
    "versendet": "status='versendet' AND interest IS NULL",
    "arbeit": "status IN ('recherche','fehler','vorgeprueft','analysiert')",
    # Was schon der kostenlose Grobfilter aussortiert hat, bleibt aus der Liste raus (nur gezählt)
    "aussortiert": "status='aussortiert' AND interest IS NULL AND IFNULL(rejected_by,'') != 'filter'",
}


def tab_counts(conn):
    counts = {key: conn.execute(f"SELECT COUNT(*) FROM leads WHERE {TAB_SQL[key]}").fetchone()[0]
              for key, _, _ in TABS}
    counts["kandidaten"] = conn.execute("SELECT COUNT(*) FROM leads WHERE status='kandidat'").fetchone()[0]
    counts["grobfilter"] = conn.execute("SELECT COUNT(*) FROM leads WHERE rejected_by='filter'").fetchone()[0]
    return counts


def tab_leads(conn, key, limit=300):
    order = {"entwurf": "fit_score DESC, updated_at DESC",
             "arbeit": "IFNULL(fit_score, quick_score) DESC, updated_at DESC"}.get(key, "updated_at DESC")
    rows = conn.execute(f"SELECT * FROM leads WHERE {TAB_SQL[key]} ORDER BY {order} LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]

# ---------------------------------------------------------------- Gehirn


def brain(conn):
    row = conn.execute("SELECT content FROM brain_versions ORDER BY id DESC LIMIT 1").fetchone()
    return row["content"] if row else BRAIN_SEED.read_text(encoding="utf-8")


def save_brain(conn, content, author, reason):
    content = content.replace("\r\n", "\n").strip() + "\n"
    if content == brain(conn):
        return False
    conn.execute("INSERT INTO brain_versions (at, author, reason, content) VALUES (?,?,?,?)",
                 (now(), author, reason, content))
    conn.commit()
    return True


LEARNED_HEADING = "## Gelernt"


def split_learned(text):
    """Teilt das Gehirn in (euer Teil, Abschnitt „Gelernt“)."""
    idx = text.find(LEARNED_HEADING)
    if idx < 0:
        return text.rstrip() + "\n", ""
    rest = text[idx + len(LEARNED_HEADING):]
    nxt = rest.find("\n## ")
    learned = rest if nxt < 0 else rest[:nxt]
    after = "" if nxt < 0 else rest[nxt:]
    return (text[:idx].rstrip() + "\n" + after).rstrip() + "\n", learned.strip()


def with_learned(text, learned):
    base, _ = split_learned(text)
    return f"{base.rstrip()}\n\n{LEARNED_HEADING}\n\n{learned.strip()}\n"


def branche_stats(conn):
    """Was hat bisher funktioniert? Pro Branche und Kategorie."""
    rows = conn.execute("""
        SELECT branche, category_label,
               IFNULL(SUM(status IN ('versendet','beantwortet') OR interest IS NOT NULL), 0) AS sent,
               IFNULL(SUM(interest='qualified'), 0) AS qualified,
               IFNULL(SUM(interest='mittel'), 0) AS mittel,
               IFNULL(SUM(interest='kein_interesse' OR status='gesperrt'), 0) AS kein,
               IFNULL(SUM(status='aussortiert'), 0) AS aussortiert,
               IFNULL(SUM(status='entwurf'), 0) AS entwurf
        FROM leads WHERE status NOT IN ('kandidat','recherche') AND IFNULL(rejected_by,'') != 'filter'
        GROUP BY branche, category_label ORDER BY sent DESC, qualified DESC""").fetchall()
    return [dict(r) for r in rows]


def brain_sections(text, names):
    """Nur bestimmte Abschnitte des Gehirns (für die günstige Vorprüfung, spart Tokens)."""
    parts = re.split(r"(?m)^(?=## )", text)
    return "\n".join(p.strip() for p in parts if any(p.startswith(f"## {n}") for n in names)) + "\n"

# ---------------------------------------------------------------- Kosten


def record_usage(conn, stage, model, tokens, cost_usd, run_id=None, lead_id=None):
    conn.execute("INSERT INTO ai_usage (at, run_id, lead_id, stage, model, input_tokens, output_tokens,"
                 " cache_write_tokens, cache_read_tokens, cost_usd) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (now(), run_id, lead_id, stage, model, tokens["input"], tokens["output"],
                  tokens["cache_write"], tokens["cache_read"], cost_usd))
    conn.commit()


def _sum(conn, where="1", args=()):
    row = conn.execute(f"SELECT COUNT(*) n, IFNULL(SUM(cost_usd),0) usd, IFNULL(SUM(input_tokens+cache_write_tokens"
                       f"+cache_read_tokens),0) tin, IFNULL(SUM(output_tokens),0) tout FROM ai_usage WHERE {where}",
                       args).fetchone()
    return dict(row)


def month_cost_usd(conn):
    return _sum(conn, "at >= ?", (datetime.now().strftime("%Y-%m-01"),))["usd"]


def cost_summary(conn):
    today = datetime.now().strftime("%Y-%m-%d")
    month = datetime.now().strftime("%Y-%m-01")
    last_run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    stages = [dict(r) for r in conn.execute(
        "SELECT stage, model, COUNT(*) n, SUM(input_tokens+cache_write_tokens+cache_read_tokens) tin,"
        " SUM(output_tokens) tout, SUM(cost_usd) usd FROM ai_usage WHERE at >= ? GROUP BY stage, model"
        " ORDER BY usd DESC", (month,))]
    return {
        "today": _sum(conn, "at >= ?", (today,)), "month": _sum(conn, "at >= ?", (month,)), "total": _sum(conn),
        "last_run": dict(last_run) if last_run else None,
        "last_run_cost": _sum(conn, "run_id = ?", (last_run["id"],)) if last_run else None,
        "stages": stages,
    }


def stage_averages(conn):
    """Durchschnittliche Kosten pro Aufruf und Stufe (für die Schätzung vor einem Lauf)."""
    return {r["stage"]: (r["usd"], r["n"]) for r in conn.execute(
        "SELECT stage, AVG(cost_usd) usd, COUNT(*) n FROM ai_usage GROUP BY stage")}


def pass_rates(conn):
    """Wie viele kommen durch die Vorprüfung bzw. Analyse? Für die Schätzung vor einem Lauf."""
    row = conn.execute("""SELECT SUM(quick_score IS NOT NULL) q_all,
        SUM(quick_score IS NOT NULL AND NOT (status='aussortiert' AND fit_score IS NULL)) q_pass,
        SUM(fit_score IS NOT NULL) a_all,
        SUM(fit_score IS NOT NULL AND status != 'aussortiert') a_pass FROM leads""").fetchone()
    q = (row["q_pass"] / row["q_all"]) if (row["q_all"] or 0) >= 10 else 0.4
    a = (row["a_pass"] / row["a_all"]) if (row["a_all"] or 0) >= 10 else 0.5
    return q, a
