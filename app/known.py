"""Merkliste aller jemals gefundenen Betriebe (für alle im Team gemeinsam).

Jeder Betrieb, der irgendwie in die App kommt (Kartensuche, Claude-Recherche, von Hand, CSV-Import), wird hier
vermerkt und bleibt es, auch wenn er später aussortiert oder gelöscht wird. Neue Funde werden dagegen geprüft:
gleiche Webseite, gleiche E-Mail-Adresse, gleicher Kartenein­trag oder gleicher Name im gleichen Ort zählen als
doppelt und kommen nicht noch einmal.
"""
import re
from urllib.parse import urlparse

from . import leaddb

SCHEMA = """
CREATE TABLE IF NOT EXISTS known_firms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, city TEXT, host TEXT, email TEXT, name_key TEXT, source_id TEXT,
    first_seen_at TEXT NOT NULL, source TEXT, found_by TEXT, lead_id INTEGER
);
CREATE INDEX IF NOT EXISTS known_host ON known_firms(host);
CREATE INDEX IF NOT EXISTS known_email ON known_firms(email);
CREATE INDEX IF NOT EXISTS known_name ON known_firms(name_key);
CREATE INDEX IF NOT EXISTS known_source ON known_firms(source_id);
CREATE INDEX IF NOT EXISTS known_lead ON known_firms(lead_id);
"""
LEGAL = re.compile(r"\b(gmbh|mbh|ug|haftungsbeschränkt|ag|kg|ohg|gbr|ek|ev|co|inh|inhaber|und|partner|"
                   r"partnerschaft)\b")
SOURCES = {"osm": "Kartensuche", "claude": "Claude-Recherche", "hand": "Von Hand", "import": "CSV-Import"}


def host_of(website):
    return urlparse(website or "").netloc.lower().removeprefix("www.").split(":")[0]


def name_key(name, city=""):
    """Name ohne Rechtsform und Satzzeichen, dazu der Ort: „Tischlerei Hansen GmbH“, Kappeln → „tischlerei hansen|kappeln“."""
    n = re.sub(r"[^\wäöüß]+", " ", (name or "").lower())
    n = re.sub(r"\s+", " ", LEGAL.sub(" ", n)).strip()
    c = re.sub(r"[^\wäöüß]+", " ", (city or "").lower()).strip()
    return f"{n}|{c}" if n else ""


def find(c, name="", city="", website="", email="", source_id=""):
    """Schon einmal gefunden? Gibt den Eintrag der Merkliste zurück oder None."""
    checks = [("source_id", source_id), ("host", host_of(website)), ("email", (email or "").strip().lower()),
              ("name_key", name_key(name, city))]
    for col, value in checks:
        if value:
            row = c.execute(f"SELECT * FROM known_firms WHERE {col}=? ORDER BY id LIMIT 1", (value,)).fetchone()
            if row:
                return dict(row)
    return None


def remember(c, name, city="", website="", email="", source_id="", source="", found_by="", lead_id=None,
             at=None, commit=True):
    c.execute("INSERT INTO known_firms (name, city, host, email, name_key, source_id, first_seen_at, source, found_by,"
              " lead_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
              (name, city or None, host_of(website) or None, (email or "").strip().lower() or None,
               name_key(name, city) or None, source_id or None, at or leaddb.now(), source or None,
               found_by or None, lead_id))
    if commit:
        c.commit()


def source_of(source_id):
    prefix = (source_id or "").split(":", 1)[0]
    return {"osm": "osm", "claude": "claude", "hand": "hand", "import": "import"}.get(prefix, prefix or "")


def backfill(c):
    """Einmalig und danach bei jedem Start: Betriebe, die noch nicht auf der Merkliste stehen, nachtragen
    (z. B. aus der Zeit vor der Merkliste oder aus einer zurückgespielten Sicherung). Kontakte aus dem Postfach
    zählen nicht, die haben uns selbst geschrieben."""
    rows = c.execute("""SELECT l.id, l.name, l.city, l.website, l.email, l.source_id, l.created_at FROM leads l
                        WHERE l.status != 'kontakt'
                          AND NOT EXISTS (SELECT 1 FROM known_firms k WHERE k.lead_id = l.id)""").fetchall()
    for r in rows:
        remember(c, r["name"], r["city"], r["website"], r["email"], r["source_id"], source_of(r["source_id"]), "",
                 r["id"], at=r["created_at"], commit=False)
    c.commit()
    return len(rows)


def count(c):
    return c.execute("SELECT COUNT(*) FROM known_firms").fetchone()[0]


def hosts(c, limit=3000):
    return {r["host"] for r in c.execute("SELECT host FROM known_firms WHERE host IS NOT NULL ORDER BY id DESC LIMIT ?",
                                         (limit,))}


def listing(c, q="", limit=300):
    """Für die Seite „Schon gefunden“: neueste zuerst, mit dem heutigen Stand des Betriebs (falls es ihn noch gibt)."""
    args, where = [], ""
    if q:
        like = f"%{q.lower()}%"
        where = "WHERE lower(k.name) LIKE ? OR lower(IFNULL(k.city,'')) LIKE ? OR IFNULL(k.host,'') LIKE ? OR IFNULL(k.email,'') LIKE ?"
        args = [like] * 4
    rows = c.execute(f"""SELECT k.*, l.status, l.stage, l.owner FROM known_firms k LEFT JOIN leads l ON l.id = k.lead_id
                         {where} ORDER BY k.id DESC LIMIT ?""", [*args, limit]).fetchall()
    return [dict(r) for r in rows]
