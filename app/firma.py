"""Firmen-OS, Fundament: Firmen-Protokoll, Personen, Rollen und Rechte, Bereiche.

Grundsätze (docs/FIRMEN-OS.md): Jede schreibende Funktion erzeugt ein Ereignis (`log`). Personen, Rollen, Rechte
und Bereiche sind Daten und werden in der App gepflegt, nicht im Code. Mindestens eine aktive Person darf immer
Personen und Rechte verwalten, und niemand kann sich dieses Recht selbst entziehen.

Notfall, wenn sich doch niemand mehr anmelden kann (auf dem Server):
    cd /opt/ylva-mailer && sudo -u mailer bash -c 'set -a; . ./.env; .venv/bin/python -m app.firma notfall oskar'
Das macht die Person wieder aktiv, gibt ihr die Rolle „Gründer“ und druckt einen Einladungslink zum neuen Passwort.
"""
import hashlib
import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS ereignisse (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, wer TEXT, art TEXT NOT NULL,
    objekt_typ TEXT, objekt_id TEXT, text TEXT, daten TEXT
);
CREATE INDEX IF NOT EXISTS ereignisse_objekt ON ereignisse(objekt_typ, objekt_id, id);
CREATE INDEX IF NOT EXISTS ereignisse_at ON ereignisse(at);
CREATE TABLE IF NOT EXISTS rollen (
    key TEXT PRIMARY KEY, name TEXT NOT NULL, rechte TEXT NOT NULL DEFAULT '{}', sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS bereiche (
    key TEXT PRIMARY KEY, name TEXT NOT NULL, verantwortlich TEXT, eltern TEXT, beschreibung TEXT,
    sort INTEGER NOT NULL DEFAULT 0, archiviert INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS person_bereiche (
    person TEXT NOT NULL, bereich TEXT NOT NULL, PRIMARY KEY (person, bereich)
);
"""

# Module, für die es Rechte gibt, und die Stufen (jede höhere Stufe schließt die niedrigeren ein)
MODULE = [
    ("aufgaben", "Aufgaben und Anfragen"),
    ("kalender", "Kalender und Termine"),
    ("cockpit", "Zahlen, Ziele und Wochengespräch"),
    ("leads", "Leads und Akquise"),
    ("crm", "CRM und Postfach"),
    ("mailer", "Mailer"),
    ("kosten", "KI-Kosten des Teams"),
    ("protokoll", "Firmen-Protokoll"),
    ("einstellungen", "Einstellungen, Updates, Sicherung"),
    ("personen", "Personen, Rollen und Bereiche"),
]
MODUL_KEYS = [k for k, _ in MODULE]
STUFEN = [(0, "Kein Zugriff"), (1, "Sehen"), (2, "Bearbeiten"), (3, "Freigeben"), (4, "Verwalten")]
STUFE_LABEL = dict(STUFEN)
SEHEN, BEARBEITEN, FREIGEBEN, VERWALTEN = 1, 2, 3, 4

STATUS = [("aktiv", "Aktiv"), ("gesperrt", "Gesperrt"), ("archiviert", "Archiviert")]
STATUS_LABEL = dict(STATUS)
LERNSTUFEN = [(1, "1 Recherche"), (2, "2 Vorbereitung"), (3, "3 Akquise"), (4, "4 Vertrieb")]
KEY_RE = re.compile(r"[a-z][a-z0-9_-]{1,29}")
EINLADUNG_TAGE = 7  # Standard; einstellbar unter Einstellungen → Personen (kv „regel:einladung_tage“)

GRUENDER = {k: VERWALTEN for k in MODUL_KEYS}
RECHERCHE = {"aufgaben": BEARBEITEN, "kalender": BEARBEITEN, "cockpit": BEARBEITEN, "leads": BEARBEITEN,
             "crm": BEARBEITEN, "mailer": BEARBEITEN, "kosten": 0, "protokoll": 0, "einstellungen": 0, "personen": 0}

# Startwerte, laufen genau einmal (leaddb.DATA_MIGRATIONS). Danach wird alles in der App gepflegt.
SEED = [
    "INSERT OR IGNORE INTO rollen (key, name, rechte, sort) VALUES ('gruender', 'Gründer', '%s', 1)"
    % json.dumps(GRUENDER),
    "INSERT OR IGNORE INTO rollen (key, name, rechte, sort) VALUES ('recherche', 'Recherche', '%s', 2)"
    % json.dumps(RECHERCHE),
    "UPDATE team SET rolle='gruender' WHERE key IN ('oskar', 'finn') AND rolle IS NULL",
    "UPDATE team SET rolle='recherche', lernstufe=1 WHERE key='hamza' AND rolle IS NULL",
    """INSERT OR IGNORE INTO bereiche (key, name, verantwortlich, eltern, beschreibung, sort) VALUES
       ('technik', 'Produkt & Technik', 'finn', NULL, 'Codebasis, Firmen-OS, Kundenanpassungen, Hosting, Sicherheit, KI-Qualität', 1),
       ('markt', 'Markt, Kunden & Betrieb', 'oskar', NULL, 'Erstgespräche, Abschlüsse, Preis, Abnahme, Onboarding, Finanzen, Verträge', 2),
       ('akquise', 'Akquise & Recherche', 'hamza', 'markt', 'Lead-Recherche, Qualität der Lead-Liste, Branchentest, CRM-Pflege, Nachfassen', 3)""",
    """INSERT OR IGNORE INTO person_bereiche (person, bereich)
       SELECT key, CASE key WHEN 'finn' THEN 'technik' WHEN 'oskar' THEN 'markt' ELSE 'akquise' END
       FROM team WHERE key IN ('finn', 'oskar', 'hamza')""",
]


class FirmaError(ValueError):
    pass


def now():
    return datetime.now().isoformat(timespec="seconds")


def _json(value, default):
    try:
        out = json.loads(value) if value else default
    except ValueError:
        return default
    return out if isinstance(out, type(default)) else default

# ---------------------------------------------------------------- Firmen-Protokoll


def log(c, wer, art, objekt_typ=None, objekt_id=None, text="", daten=None, commit=True):
    """Ein Eintrag im Firmen-Protokoll: wer (Schlüssel der Person oder „System“), was, woran, mit welchen Daten."""
    c.execute("INSERT INTO ereignisse (at, wer, art, objekt_typ, objekt_id, text, daten) VALUES (?,?,?,?,?,?,?)",
              (now(), wer or "System", art, objekt_typ, None if objekt_id is None else str(objekt_id), text or "",
               json.dumps(daten, ensure_ascii=False) if daten else None))
    if commit:
        c.commit()


ART_LABELS = {
    "person": "Person", "rolle": "Rolle", "bereich": "Bereich", "einladung": "Einladung", "anmeldung": "Anmeldung",
    "einstellung": "Einstellung", "aufgabe": "Aufgabe", "anfrage": "Anfrage", "kommentar": "Kommentar",
    "lead": "Lead", "software": "Software", "sicherung": "Sicherung",
}


def protokoll(c, art=None, wer=None, objekt=None, limit=200, vor=None):
    where, args = ["1"], []
    if art:
        where.append("art = ?")
        args.append(art)
    if wer:
        where.append("wer = ?")
        args.append(wer)
    if objekt:
        where.append("objekt_typ = ? AND objekt_id = ?")
        args += [objekt[0], str(objekt[1])]
    if vor:
        where.append("id < ?")
        args.append(vor)
    rows = c.execute(f"SELECT * FROM ereignisse WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",
                     [*args, limit]).fetchall()
    return [dict(r, daten=_json(r["daten"], {})) for r in rows]

# ---------------------------------------------------------------- Rollen und Rechte


def rollen(c):
    return [dict(r, rechte=_rechte(r["rechte"]), personen=r["n"]) for r in c.execute(
        "SELECT r.*, (SELECT COUNT(*) FROM team t WHERE t.rolle = r.key AND IFNULL(t.status,'aktiv') != 'archiviert')"
        " AS n FROM rollen r ORDER BY sort, name")]


def rolle(c, key):
    row = c.execute("SELECT * FROM rollen WHERE key=?", (key or "",)).fetchone()
    return dict(row, rechte=_rechte(row["rechte"])) if row else None


def _rechte(raw):
    data = _json(raw, {}) if isinstance(raw, (str, type(None))) else raw
    out = {}
    for k in MODUL_KEYS:
        try:
            v = int(data.get(k, 0))
        except (TypeError, ValueError):
            v = 0
        out[k] = max(0, min(VERWALTEN, v))
    return out


def rechte(c, key):
    """Rechte einer Person: aus ihrer Rolle, einzelne Module können je Person abweichen. Nicht aktiv = keine."""
    p = c.execute("SELECT rolle, rechte, status FROM team WHERE key=?", (key or "",)).fetchone()
    if not p or (p["status"] or "aktiv") != "aktiv":
        return {k: 0 for k in MODUL_KEYS}
    r = rolle(c, p["rolle"])
    base = r["rechte"] if r else {k: 0 for k in MODUL_KEYS}
    own = _json(p["rechte"], {})
    return {k: max(0, min(VERWALTEN, int(own[k]))) if str(own.get(k, "")).lstrip("-").isdigit() else base[k]
            for k in MODUL_KEYS}


def darf(c, key, modul, stufe=SEHEN):
    return rechte(c, key).get(modul, 0) >= stufe


def verwalter(c):
    """Aktive Personen, die Personen und Rechte verwalten dürfen."""
    return [r["key"] for r in c.execute("SELECT key FROM team WHERE IFNULL(status,'aktiv')='aktiv'")
            if darf(c, r["key"], "personen", VERWALTEN)]


def _pruefe_aussperren(c, wer):
    """Nach einer Änderung (noch nicht gespeichert): Gibt es noch Verwalter, und behält die handelnde Person ihr
    Verwaltungsrecht? Sonst alles zurück."""
    if not verwalter(c):
        c.rollback()
        raise FirmaError("Das geht nicht: Danach dürfte niemand mehr Personen und Rechte verwalten.")
    if wer and wer != "System" and c.execute("SELECT 1 FROM team WHERE key=?", (wer,)).fetchone() \
            and not darf(c, wer, "personen", VERWALTEN):
        c.rollback()
        raise FirmaError("Das geht nicht: Du würdest dir selbst das Recht nehmen, Personen und Rechte zu verwalten.")


def parse_rechte(form, prefix="r_", erlaube_leer=False):
    """Formular → Rechte je Modul. Mit erlaube_leer: leeres Feld = wie die Rolle (für Rechte je Person)."""
    out = {}
    for k in MODUL_KEYS:
        raw = (form.get(prefix + k) or "").strip()
        if raw == "" and erlaube_leer:
            continue
        if raw not in {str(s) for s, _ in STUFEN}:
            raise FirmaError(f"Unbekannte Stufe für {dict(MODULE)[k]}.")
        out[k] = int(raw)
    return out


def rolle_speichern(c, wer, key, name, rechte_neu):
    name = (name or "").strip()
    if not name or len(name) > 60:
        raise FirmaError("Bitte einen Namen für die Rolle (höchstens 60 Zeichen).")
    neu = not key
    if neu:
        key = slug(name, {r["key"] for r in c.execute("SELECT key FROM rollen")})
        sort = (c.execute("SELECT MAX(sort) FROM rollen").fetchone()[0] or 0) + 1
        c.execute("INSERT INTO rollen (key, name, rechte, sort) VALUES (?,?,?,?)",
                  (key, name, json.dumps(_rechte(rechte_neu)), sort))
    else:
        alt = rolle(c, key)
        if not alt:
            raise FirmaError("Diese Rolle gibt es nicht.")
        c.execute("UPDATE rollen SET name=?, rechte=? WHERE key=?", (name, json.dumps(_rechte(rechte_neu)), key))
    _pruefe_aussperren(c, wer)
    log(c, wer, "rolle", "rolle", key, f"Rolle „{name}“ {'angelegt' if neu else 'geändert'}.",
        {"rechte": _rechte(rechte_neu)})
    return key


def rolle_loeschen(c, wer, key):
    r = rolle(c, key)
    if not r:
        raise FirmaError("Diese Rolle gibt es nicht.")
    if c.execute("SELECT 1 FROM team WHERE rolle=?", (key,)).fetchone():
        raise FirmaError("Die Rolle ist noch Personen zugeordnet (auch archivierten). Erst dort ändern.")
    c.execute("DELETE FROM rollen WHERE key=?", (key,))
    log(c, wer, "rolle", "rolle", key, f"Rolle „{r['name']}“ gelöscht.")

# ---------------------------------------------------------------- Bereiche


def bereiche(c, mit_archiv=False):
    rows = [dict(r) for r in c.execute(
        "SELECT b.*, t.name AS verantwortlich_name FROM bereiche b LEFT JOIN team t ON t.key = b.verantwortlich "
        f"{'' if mit_archiv else 'WHERE b.archiviert = 0'} ORDER BY b.sort, b.name")]
    names = {r["key"]: r["name"] for r in rows}
    for r in rows:
        r["eltern_name"] = names.get(r["eltern"])
        r["voll"] = f"{names[r['eltern']]} › {r['name']}" if r["eltern"] in names else r["name"]
    return rows


def bereich(c, key):
    row = c.execute("SELECT * FROM bereiche WHERE key=?", (key or "",)).fetchone()
    return dict(row) if row else None


def bereich_speichern(c, wer, key, name, verantwortlich, eltern=None, beschreibung=""):
    name = (name or "").strip()
    if not name or len(name) > 80:
        raise FirmaError("Bitte einen Namen für den Bereich (höchstens 80 Zeichen).")
    if not verantwortlich or not aktive_person(c, verantwortlich):
        raise FirmaError("Jeder Bereich braucht eine verantwortliche, aktive Person.")
    eltern = eltern or None
    if eltern and (eltern == key or not bereich(c, eltern)):
        raise FirmaError("Der übergeordnete Bereich passt nicht.")
    if eltern and key and bereich(c, eltern).get("eltern") == key:
        raise FirmaError("Zwei Bereiche können nicht gegenseitig übergeordnet sein.")
    neu = not key
    if neu:
        key = slug(name, {r["key"] for r in c.execute("SELECT key FROM bereiche")})
        sort = (c.execute("SELECT MAX(sort) FROM bereiche").fetchone()[0] or 0) + 1
        c.execute("INSERT INTO bereiche (key, name, verantwortlich, eltern, beschreibung, sort) VALUES (?,?,?,?,?,?)",
                  (key, name, verantwortlich, eltern, (beschreibung or "").strip(), sort))
    else:
        if not bereich(c, key):
            raise FirmaError("Diesen Bereich gibt es nicht.")
        c.execute("UPDATE bereiche SET name=?, verantwortlich=?, eltern=?, beschreibung=? WHERE key=?",
                  (name, verantwortlich, eltern, (beschreibung or "").strip(), key))
    c.execute("INSERT OR IGNORE INTO person_bereiche (person, bereich) VALUES (?,?)", (verantwortlich, key))
    log(c, wer, "bereich", "bereich", key, f"Bereich „{name}“ {'angelegt' if neu else 'geändert'}.",
        {"verantwortlich": verantwortlich, "eltern": eltern})
    return key


def bereich_archivieren(c, wer, key, archiv=True):
    b = bereich(c, key)
    if not b:
        raise FirmaError("Diesen Bereich gibt es nicht.")
    if archiv and c.execute("SELECT 1 FROM bereiche WHERE eltern=? AND archiviert=0", (key,)).fetchone():
        raise FirmaError("Der Bereich hat noch Unterbereiche. Erst diese archivieren oder umhängen.")
    c.execute("UPDATE bereiche SET archiviert=? WHERE key=?", (1 if archiv else 0, key))
    log(c, wer, "bereich", "bereich", key, f"Bereich „{b['name']}“ {'archiviert' if archiv else 'zurückgeholt'}.")


def bereiche_von(c, person):
    return [r["bereich"] for r in c.execute(
        "SELECT pb.bereich FROM person_bereiche pb JOIN bereiche b ON b.key = pb.bereich "
        "WHERE pb.person=? AND b.archiviert=0 ORDER BY b.sort", (person,))]


def verantwortet(c, person):
    """Bereiche, für die die Person verantwortlich ist (dort landen Anfragen)."""
    return [r["key"] for r in c.execute("SELECT key FROM bereiche WHERE verantwortlich=? AND archiviert=0",
                                        (person,))]

# ---------------------------------------------------------------- Personen


def slug(text, taken):
    base = text.strip().split(" ")[0].lower()
    base = base.translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "é": "e", "è": "e"}))
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:24] or "x"
    if not base[0].isalpha():
        base = "p-" + base
    key, n = base if len(base) > 1 else base + "1", 2
    while key in taken:
        key, n = f"{base}-{n}", n + 1
    return key


def personen(c, mit_archiv=False):
    where = "1" if mit_archiv else "IFNULL(t.status,'aktiv') != 'archiviert'"
    rows = [dict(r) for r in c.execute(
        "SELECT t.key, t.name, t.email, t.rolle, t.lernstufe, IFNULL(t.status,'aktiv') AS status, t.pw_hash, "
        "t.einladung_bis, r.name AS rolle_name FROM team t LEFT JOIN rollen r ON r.key = t.rolle "
        f"WHERE {where} ORDER BY t.sort, t.name")]
    for r in rows:
        r["has_password"] = bool(r.pop("pw_hash"))
        r["bereiche"] = bereiche_von(c, r["key"])
        r["einladung_offen"] = bool(r["einladung_bis"] and r["einladung_bis"] > now())
    return rows


def person(c, key):
    row = c.execute("SELECT * FROM team WHERE key=?", (key or "",)).fetchone()
    if not row:
        return None
    p = dict(row)
    p["status"] = p.get("status") or "aktiv"
    p["eigene_rechte"] = _json(p.get("rechte"), {})
    p["bereiche"] = bereiche_von(c, key)
    p["has_password"] = bool(p.get("pw_hash"))
    return p


def aktive_person(c, key):
    p = person(c, key)
    return p if p and p["status"] == "aktiv" else None


def aktive_personen(c):
    return [p for p in personen(c) if p["status"] == "aktiv"]


def name_von(c, key):
    row = c.execute("SELECT name FROM team WHERE key=?", (key or "",)).fetchone()
    return row["name"] if row else (key or "")


def person_anlegen(c, wer, name, rolle_key, email="", bereiche_neu=(), lernstufe=None):
    name = re.sub(r"\s+", " ", name or "").strip()
    if not 2 <= len(name) <= 80:
        raise FirmaError("Bitte den vollen Namen eintragen.")
    if c.execute("SELECT 1 FROM team WHERE lower(name)=lower(?)", (name,)).fetchone():
        raise FirmaError("Eine Person mit diesem Namen gibt es schon.")
    if not rolle(c, rolle_key):
        raise FirmaError("Bitte eine Rolle wählen.")
    email = _email(email)
    key = slug(name, {r["key"] for r in c.execute("SELECT key FROM team")})
    sort = (c.execute("SELECT MAX(sort) FROM team").fetchone()[0] or 0) + 1
    c.execute("INSERT INTO team (key, name, sort, rolle, email, lernstufe, status, erstellt_am) "
              "VALUES (?,?,?,?,?,?,'aktiv',?)", (key, name, sort, rolle_key, email, _lernstufe(lernstufe), now()))
    _bereiche_setzen(c, key, bereiche_neu)
    log(c, wer, "person", "person", key, f"{name} angelegt.", {"rolle": rolle_key, "bereiche": list(bereiche_neu)})
    return key


def _email(email):
    email = (email or "").strip().lower()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise FirmaError("Die E-Mail-Adresse sieht nicht richtig aus.")
    return email or None


def _lernstufe(value):
    if value in (None, ""):
        return None
    try:
        v = int(value)
    except (TypeError, ValueError) as exc:
        raise FirmaError("Lernstufe bitte 1 bis 4.") from exc
    if not 1 <= v <= 4:
        raise FirmaError("Lernstufe bitte 1 bis 4.")
    return v


def _bereiche_setzen(c, key, keys):
    gueltig = {b["key"] for b in bereiche(c)}
    if any(k not in gueltig for k in keys):
        raise FirmaError("Unbekannter Bereich.")
    c.execute("DELETE FROM person_bereiche WHERE person=?", (key,))
    for k in keys:
        c.execute("INSERT INTO person_bereiche (person, bereich) VALUES (?,?)", (key, k))


def person_aendern(c, wer, key, name, rolle_key, email="", bereiche_neu=(), lernstufe=None, eigene_rechte=None):
    alt = person(c, key)
    if not alt:
        raise FirmaError("Diese Person gibt es nicht.")
    name = re.sub(r"\s+", " ", name or "").strip()
    if not 2 <= len(name) <= 80:
        raise FirmaError("Bitte den vollen Namen eintragen.")
    if c.execute("SELECT 1 FROM team WHERE lower(name)=lower(?) AND key != ?", (name, key)).fetchone():
        raise FirmaError("Eine Person mit diesem Namen gibt es schon.")
    if not rolle(c, rolle_key):
        raise FirmaError("Bitte eine Rolle wählen.")
    eigene = {k: int(v) for k, v in (eigene_rechte or {}).items() if k in MODUL_KEYS}
    verantwortlich = set(verantwortet(c, key))
    if verantwortlich - set(bereiche_neu):
        raise FirmaError("Die Person ist für einen Bereich verantwortlich, den du ihr gerade wegnimmst. "
                         "Erst dort eine andere verantwortliche Person eintragen.")
    c.execute("UPDATE team SET name=?, rolle=?, email=?, lernstufe=?, rechte=? WHERE key=?",
              (name, rolle_key, _email(email), _lernstufe(lernstufe), json.dumps(eigene) if eigene else None, key))
    _bereiche_setzen(c, key, bereiche_neu)
    _pruefe_aussperren(c, wer)
    diff = {k: v for k, v in {"name": name, "rolle": rolle_key, "lernstufe": _lernstufe(lernstufe),
                              "bereiche": list(bereiche_neu), "rechte": eigene}.items()
            if (alt.get(k) if k not in ("bereiche", "rechte") else
                (alt["bereiche"] if k == "bereiche" else alt["eigene_rechte"])) != v}
    log(c, wer, "person", "person", key, f"{name} geändert.", diff or None)


def status_setzen(c, wer, key, status):
    """Sperren (keine Anmeldung, bleibt sichtbar), archivieren (weg aus allen Listen, Daten bleiben), aktivieren."""
    if status not in ("aktiv", "gesperrt", "archiviert"):
        raise FirmaError("Unbekannter Status.")
    p = person(c, key)
    if not p:
        raise FirmaError("Diese Person gibt es nicht.")
    if key == wer and status != "aktiv":
        raise FirmaError("Dich selbst kannst du nicht sperren oder archivieren.")
    if status == "archiviert" and verantwortet(c, key):
        raise FirmaError("Die Person ist noch für einen Bereich verantwortlich. Erst dort jemand anderen eintragen.")
    c.execute("UPDATE team SET status=? WHERE key=?", (status, key))
    if status != "aktiv":
        c.execute("DELETE FROM remember_tokens WHERE member=?", (key,))
        c.execute("UPDATE team SET einladung_hash=NULL, einladung_bis=NULL WHERE key=?", (key,))
    _pruefe_aussperren(c, wer)
    log(c, wer, "person", "person", key, f"{p['name']}: {STATUS_LABEL[status]}.", {"status": status})


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def einladen(c, wer, key):
    """Neuer Einladungslink (der alte gilt dann nicht mehr). Gibt das Token zurück, gespeichert ist nur sein Hash."""
    p = person(c, key)
    if not p or p["status"] != "aktiv":
        raise FirmaError("Einladen geht nur für aktive Personen.")
    token = secrets.token_urlsafe(32)
    bis = (datetime.now() + timedelta(days=einladung_tage(c))).isoformat(timespec="seconds")
    c.execute("UPDATE team SET einladung_hash=?, einladung_bis=? WHERE key=?", (_hash(token), bis, key))
    log(c, wer, "einladung", "person", key, f"Einladungslink für {p['name']} erzeugt (gilt bis {bis[:10]}).")
    return token


def einladung_tage(c):
    row = c.execute("SELECT value FROM kv WHERE key='regel:einladung_tage'").fetchone()
    try:
        tage = int(json.loads(row["value"])) if row else EINLADUNG_TAGE
    except (TypeError, ValueError):
        tage = EINLADUNG_TAGE
    return max(1, min(30, tage))


def einladung_tage_setzen(c, wer, tage):
    try:
        tage = int(tage)
    except (TypeError, ValueError) as exc:
        raise FirmaError("Bitte eine Zahl von 1 bis 30.") from exc
    if not 1 <= tage <= 30:
        raise FirmaError("Bitte eine Zahl von 1 bis 30.")
    c.execute("INSERT INTO kv (key, value) VALUES ('regel:einladung_tage', ?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(tage),))
    log(c, wer, "einstellung", "regel", "einladung_tage", f"Einladungslinks gelten {tage} Tage.")


def einladung(c, token):
    """Person zu einem gültigen Einladungslink oder None."""
    if not token or len(token) > 100:
        return None
    row = c.execute("SELECT key FROM team WHERE einladung_hash=? AND einladung_bis > ? "
                    "AND IFNULL(status,'aktiv')='aktiv'", (_hash(token), now())).fetchone()
    return person(c, row["key"]) if row else None


def einladung_einloesen(c, token, password):
    from . import team
    p = einladung(c, token)
    if not p:
        raise FirmaError("Der Link ist abgelaufen oder wurde schon benutzt. Bitte um einen neuen.")
    team.set_password(c, p["key"], password)  # prüft Länge, löscht gemerkte Geräte
    c.execute("UPDATE team SET einladung_hash=NULL, einladung_bis=NULL WHERE key=?", (p["key"],))
    log(c, p["key"], "einladung", "person", p["key"], f"{p['name']} hat das Passwort über den Einladungslink gesetzt.")
    return p


def notfall(c, key):
    """Wiederherstellung über die Konsole (siehe Kopf der Datei)."""
    p = person(c, key)
    if not p:
        raise FirmaError(f"Keine Person mit dem Schlüssel {key!r}. Vorhanden: "
                         + ", ".join(r["key"] for r in c.execute("SELECT key FROM team")))
    if not rolle(c, "gruender"):
        c.execute("INSERT INTO rollen (key, name, rechte, sort) VALUES ('gruender', 'Gründer', ?, 0)",
                  (json.dumps(GRUENDER),))
    else:
        c.execute("UPDATE rollen SET rechte=? WHERE key='gruender'", (json.dumps(GRUENDER),))
    c.execute("UPDATE team SET status='aktiv', rolle='gruender', rechte=NULL WHERE key=?", (key,))
    token = einladen(c, "Konsole", key)
    log(c, "Konsole", "person", "person", key, f"Notfall: {p['name']} wieder als Gründer freigeschaltet.")
    return token


def _main(argv):
    import os
    from pathlib import Path
    from . import leaddb
    if len(argv) != 3 or argv[1] != "notfall":
        print(__doc__)
        return 2
    data = Path(os.environ.get("DATA_DIR") or Path(__file__).resolve().parent.parent / "data")
    c = leaddb.connect(data / "mailer.db")
    try:
        token = notfall(c, argv[2])
    except (FirmaError, sqlite3.Error) as exc:
        print(f"Nicht geklappt: {exc}")
        return 1
    base = os.environ.get("BASE_URL", "").rstrip("/") or "https://<deine-adresse>"
    print(f"{argv[2]} ist wieder aktiv und Gründer. Passwort setzen über:\n{base}/einladung/{token}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv))
