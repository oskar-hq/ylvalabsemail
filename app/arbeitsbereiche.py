"""Arbeitsbereiche: Outreach für uns und für Kunden auf einem Server (Firmen-OS, Stufe 11, docs/FIRMEN-OS.md).

Ein Arbeitsbereich ist „Ylva Labs“ (Schlüssel `ylva`, unsere eigene Akquise) oder ein Kunde, für den wir Outreach
machen. Leads, CRM, Postfach, Gehirn, Suchgebiet, Nischen, Sperrliste, Läufe und KI-Kosten gehören immer zu genau
einem Arbeitsbereich und mischen sich nie. Personen, Aufgaben, Kalender, Finanzen und Freigaben bleiben gemeinsam.

So funktioniert die Trennung:

- **Aktiver Arbeitsbereich:** `aktiv()` gibt den Arbeitsbereich der laufenden Anfrage (Auswahl oben in der App,
  gespeichert in der Sitzung) oder des Hintergrund-Arbeiters (Schleife über alle aktiven) zurück. Gesetzt wird er mit
  `with im("kunde-a"):` (ContextVar, je Thread und Anfrage getrennt). Ohne Angabe gilt `ylva`.
- **In SQL** steht er als Funktion `ab()` bereit (in `leaddb.connect` registriert): `WHERE arbeitsbereich = ab()`.
  Neue Zeilen in den Akquise-Tabellen bekommen ihn automatisch (temporäre Trigger in `leaddb.connect`).
- **Gespeicherte Werte** (Sperrliste, Nischen, Suchgebiet …) liegen in `kv` mit Präfix `ab:<key>:` (siehe
  `leaddb.AB_KV`), **Einstellungen** wie Absender und Suchgebiet je Arbeitsbereich in `einstellungen` (siehe
  `FELDER`), das **Akquise-Postfach** eines Kunden ist ein Postfach aus Einstellungen → Postfächer (`postfach_id`).
- **Wer darf:** `personen` (Liste der Personen-Schlüssel) – leer heißt: alle, die Leads oder CRM sehen dürfen. Wer
  Personen und Rechte verwaltet, sieht immer alle Arbeitsbereiche.
"""
import contextvars
import json
import re
from datetime import datetime

YLVA = "ylva"

SCHEMA = """
CREATE TABLE IF NOT EXISTS arbeitsbereiche (
    key TEXT PRIMARY KEY, name TEXT NOT NULL, projekt_id INTEGER, postfach_id INTEGER, personen TEXT,
    einstellungen TEXT NOT NULL DEFAULT '{}', aktiv INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 0,
    erstellt_am TEXT NOT NULL, erstellt_von TEXT
);
"""
SEED = [
    "INSERT OR IGNORE INTO arbeitsbereiche (key, name, aktiv, sort, erstellt_am) "
    "VALUES ('ylva', 'Ylva Labs', 1, 0, datetime('now', 'localtime'))",
]

# Einstellungen je Kunden-Arbeitsbereich: (Schlüssel, Bezeichnung, Typ, Hinweis, Rückfall auf den Wert von Ylva Labs).
# Ohne Rückfall gilt der leere Wert bzw. der Standard hier, damit nie etwas im Namen von Ylva Labs an Kunden geht.
FELDER = [
    ("ORG_NAME", "Firmenname des Kunden (Absender)", "text", "So steht der Kunde als Absender in den Mails", False),
    ("LEADS_SENDER_NAME", "Unterschrift", "text", "Name unter den Nachrichten, z. B. „Maria Muster, Vertrieb“", False),
    ("DEFAULT_FOOTER", "Fußzeile (Impressum)", "text", "Firmenname und Anschrift des Kunden wie in seinem Impressum", False),
    ("OUTREACH_EMAIL", "Erstkontakt per Mail erlauben", "bool",
     "Erst einschalten, wenn der Kunde schriftlich bestätigt hat, dass er Erstkontakt per Mail will (§ 7 UWG)", False),
    ("LEADS_FOLLOWUP_DAYS", "Nachfassen nach Tagen ohne Antwort", "int", "0 = nie", True),
    ("LEADS_MONTHLY_BUDGET_EUR", "KI-Budget pro Monat (€)", "float", "Eigene Grenze für diesen Kunden, 0 = keine", True),
    ("PAUSCHALE_EUR", "Pauschale pro Monat (€, netto)", "float", "Steht als Position „Outreach“ auf der Monatsrechnung", False),
    ("KI_WEITERBERECHNEN", "KI-Kosten mit abrechnen", "bool", "Die KI-Kosten des Monats als eigene Position", False),
]
FELD = {k: dict(key=k, label=lab, type=t, hint=h, fallback=fb) for k, lab, t, h, fb in FELDER}
STANDARD = {"OUTREACH_EMAIL": False, "KI_WEITERBERECHNEN": False, "PAUSCHALE_EUR": 0.0}
# Werte, die aus dem Akquise-Postfach des Kunden kommen (Einstellungen → Postfächer)
POSTFACH_FELDER = ("SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "IMAP_HOST", "IMAP_PORT", "OUTREACH_USER",
                   "OUTREACH_PASSWORD", "FROM_ADDRESS", "IMAP_SENT_FOLDER")

_AKTIV = contextvars.ContextVar("arbeitsbereich", default=YLVA)
_CACHE = {}  # key → Zeile mit geladenen Einstellungen (wird beim Speichern geleert)


class ArbeitsbereichError(ValueError):
    pass


def aktiv():
    return _AKTIV.get()


def setzen(key):
    """Für eine ganze Anfrage (before_request): gibt das Token zum Zurücksetzen zurück."""
    return _AKTIV.set(key or YLVA)


class im:
    """`with im("kunde-a"):` – alles darin gehört zu diesem Arbeitsbereich (auch im Hintergrund-Arbeiter)."""

    def __init__(self, key):
        self.key = key or YLVA

    def __enter__(self):
        self._token = _AKTIV.set(self.key)
        return self.key

    def __exit__(self, *exc):
        _AKTIV.reset(self._token)


def ist_kunde(key=None):
    return (key or aktiv()) != YLVA


def now():
    return datetime.now().isoformat(timespec="seconds")

# ---------------------------------------------------------------- Lesen


def _zeile(r):
    d = dict(r)
    try:
        d["einstellungen"] = json.loads(d.get("einstellungen") or "{}")
    except ValueError:
        d["einstellungen"] = {}
    try:
        d["personen"] = json.loads(d.get("personen") or "[]")
    except ValueError:
        d["personen"] = []
    return d


def alle(c, nur_aktive=False):
    rows = c.execute(f"SELECT * FROM arbeitsbereiche {'WHERE aktiv=1' if nur_aktive else ''} "
                     "ORDER BY key != 'ylva', sort, name").fetchall()
    return [_zeile(r) for r in rows]


def get(c, key):
    row = c.execute("SELECT * FROM arbeitsbereiche WHERE key=?", (key or "",)).fetchone()
    return _zeile(row) if row else None


def name(c, key=None):
    a = get(c, key or aktiv())
    return a["name"] if a else (key or aktiv())


def namen(c):
    return {a["key"]: a["name"] for a in alle(c)}


def _verwaltet_alle(c, person):
    from . import firma
    return firma.darf(c, person, "personen", firma.VERWALTEN)


def darf(c, person, key):
    """Darf die Person in diesem Arbeitsbereich arbeiten? Leere Liste = alle mit Leads- oder CRM-Recht."""
    from . import firma
    a = get(c, key)
    if not a or not person:
        return False
    if _verwaltet_alle(c, person):
        return True
    if not (firma.darf(c, person, "leads") or firma.darf(c, person, "crm")):
        return False
    if not a["aktiv"] and key != YLVA:
        return False
    return not a["personen"] or person in a["personen"]


def fuer_person(c, person):
    """Arbeitsbereiche, zwischen denen die Person oben wählen kann."""
    return [a for a in alle(c) if (a["aktiv"] or a["key"] == YLVA) and darf(c, person, a["key"])]


def standard_fuer(c, person):
    """Ohne Auswahl in der Sitzung: Ylva Labs, wer dort nicht arbeitet, der erste erlaubte Kunde."""
    if darf(c, person, YLVA):
        return YLVA
    erlaubt = fuer_person(c, person)
    return erlaubt[0]["key"] if erlaubt else YLVA

# ---------------------------------------------------------------- Einstellungen je Arbeitsbereich


def _laden(c, key):
    a = get(c, key)
    if a and a.get("postfach_id"):
        from . import posteingang
        a["postfach"] = posteingang.postfach(c, a["postfach_id"])
    return a


def _cache(key):
    """Einstellungen eines Arbeitsbereichs (agent.S fragt ohne Datenbankverbindung). Nach dem Speichern neu."""
    if key not in _CACHE:
        from . import agent, leaddb
        if not agent.CFG.get("db_path"):
            return None
        c = leaddb.connect(agent.CFG["db_path"])
        try:
            _CACHE[key] = _laden(c, key)
        finally:
            c.close()
    return _CACHE[key]


def cache_leeren():
    _CACHE.clear()


def _typ(key, value):
    t = FELD[key]["type"] if key in FELD else "text"
    if t == "bool":
        return value in (True, 1, "1", "true", "on", "yes")
    if t == "int":
        return int(float(value))
    if t == "float":
        return float(str(value).replace(",", "."))
    if key == "LEADS_CENTER":
        return tuple(float(x) for x in str(value).split(","))
    return value


def postfach_wert(p, key):
    """Werte des Akquise-Postfachs eines Kunden in der Form der globalen Einstellungen."""
    if not p:
        return "" if key not in ("SMTP_PORT", "IMAP_PORT") else 0
    if key == "OUTREACH_PASSWORD":
        from . import posteingang
        try:
            return posteingang._passwort(p)
        except posteingang.PosteingangError:
            return ""
    return {"SMTP_HOST": p["smtp_host"], "SMTP_PORT": p["smtp_port"], "SMTP_SECURITY": p["smtp_sicherheit"],
            "IMAP_HOST": p["imap_host"], "IMAP_PORT": p["imap_port"], "OUTREACH_USER": p["benutzer"],
            "FROM_ADDRESS": p["adresse"], "IMAP_SENT_FOLDER": ""}[key]


_NICHT_GESETZT = object()


def wert(key, ab=None):
    """Einstellung im Arbeitsbereich eines Kunden. Gibt `_NICHT_GESETZT` zurück, wenn der globale Wert gilt
    (Ylva Labs oder ein Feld mit Rückfall, das beim Kunden leer ist)."""
    ab = ab or aktiv()
    if ab == YLVA:
        return _NICHT_GESETZT
    if key in POSTFACH_FELDER:
        a = _cache(ab)
        return postfach_wert((a or {}).get("postfach"), key)
    if key not in FELD:
        return _NICHT_GESETZT
    a = _cache(ab)
    raw = (a or {}).get("einstellungen", {}).get(key)
    if raw in (None, ""):
        if FELD[key]["fallback"]:
            return _NICHT_GESETZT
        if key == "ORG_NAME":
            return (a or {}).get("name") or ""
        return STANDARD.get(key, "")
    try:
        return _typ(key, raw)
    except (TypeError, ValueError):
        return _NICHT_GESETZT if FELD[key]["fallback"] else STANDARD.get(key, "")


def gesetzt(value):
    return value is not _NICHT_GESETZT


def postfach_ids(c):
    """Postfächer, die einem Kunden als Akquise-Postfach gehören (der Posteingang ruft sie nicht selbst ab)."""
    return {r[0] for r in c.execute("SELECT postfach_id FROM arbeitsbereiche WHERE postfach_id IS NOT NULL "
                                    "AND key != 'ylva'")}

# ---------------------------------------------------------------- Verwalten


def _slug(text):
    s = text.lower().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:30]


def anlegen(c, wer, name_, projekt_id=None):
    from . import firma
    name_ = re.sub(r"\s+", " ", name_ or "").strip()[:80]
    if len(name_) < 2:
        raise ArbeitsbereichError("Bitte einen Namen, z. B. den Namen des Kunden.")
    key = _slug(name_) or "kunde"
    if key == YLVA:
        key = "kunde-ylva"
    basis, n = key, 2
    while get(c, key):
        key, n = f"{basis}-{n}", n + 1
    sort = (c.execute("SELECT MAX(sort) FROM arbeitsbereiche").fetchone()[0] or 0) + 1
    c.execute("INSERT INTO arbeitsbereiche (key, name, projekt_id, personen, einstellungen, aktiv, sort, erstellt_am,"
              " erstellt_von) VALUES (?,?,?,?,?,?,?,?,?)",
              (key, name_, projekt_id or None, "[]", json.dumps({"ORG_NAME": name_}), 1, sort, now(), wer))
    firma.log(c, wer, "einstellung", "arbeitsbereich", key, f"Arbeitsbereich „{name_}“ angelegt.")
    cache_leeren()
    _CACHE[key] = _laden(c, key)
    return key


def speichern(c, wer, key, form):
    """Name, Projekt, Akquise-Postfach, Personen, Aktiv und die Einstellungen aus FELDER."""
    from . import firma, posteingang
    a = get(c, key)
    if not a:
        raise ArbeitsbereichError("Diesen Arbeitsbereich gibt es nicht.")
    name_ = re.sub(r"\s+", " ", form.get("name") or a["name"]).strip()[:80]
    if len(name_) < 2:
        raise ArbeitsbereichError("Bitte einen Namen.")
    projekt_id = form.get("projekt_id") or None
    if projekt_id:
        try:
            projekt_id = int(projekt_id)
        except ValueError as exc:
            raise ArbeitsbereichError("Dieses Projekt gibt es nicht.") from exc
        if not c.execute("SELECT 1 FROM projekte WHERE id=?", (projekt_id,)).fetchone():
            raise ArbeitsbereichError("Dieses Projekt gibt es nicht.")
    postfach_id = form.get("postfach_id") or None
    if key == YLVA:
        postfach_id = None  # Ylva Labs nutzt das Akquise-Postfach aus Einstellungen → Postfach und Versand
    if postfach_id:
        try:
            postfach_id = int(postfach_id)
        except ValueError as exc:
            raise ArbeitsbereichError("Dieses Postfach gibt es nicht.") from exc
        if not posteingang.postfach(c, postfach_id):
            raise ArbeitsbereichError("Dieses Postfach gibt es nicht.")
        andere = c.execute("SELECT name FROM arbeitsbereiche WHERE postfach_id=? AND key != ?",
                           (postfach_id, key)).fetchone()
        if andere:
            raise ArbeitsbereichError(f"Dieses Postfach ist schon das Akquise-Postfach von „{andere['name']}“.")
    personen = [p for p in (form.getlist("personen") if hasattr(form, "getlist") else form.get("personen") or [])
                if firma.person(c, p)]
    werte = dict(a["einstellungen"])
    if key != YLVA:
        for k, label, typ, _hint, _fb in FELDER:
            raw = form.get(k)
            if typ == "bool":
                werte[k] = raw in ("1", "on", "true", True)
                continue
            raw = (raw or "").strip()
            if not raw:
                werte.pop(k, None)
                continue
            if typ.startswith("select:") and raw not in typ.split(":", 1)[1].split("|"):
                raise ArbeitsbereichError(f"{label}: ungültige Auswahl.")
            if k == "LEADS_CENTER" and not re.fullmatch(r"-?\d+(\.\d+)?,\s*-?\d+(\.\d+)?", raw):
                raise ArbeitsbereichError(f"{label}: Format „53.5511,9.9937“.")
            try:
                _typ(k, raw)
            except (TypeError, ValueError) as exc:
                raise ArbeitsbereichError(f"{label}: bitte eine Zahl.") from exc
            werte[k] = raw.replace(" ", "") if k == "LEADS_CENTER" else raw[:300]
    aktiv_ = 1 if key == YLVA or form.get("aktiv") in ("1", "on", True) else 0
    c.execute("UPDATE arbeitsbereiche SET name=?, projekt_id=?, postfach_id=?, personen=?, einstellungen=?, aktiv=? "
              "WHERE key=?", (name_, projekt_id, postfach_id, json.dumps(personen), json.dumps(werte, ensure_ascii=False),
                             aktiv_, key))
    vorher = bool(a["einstellungen"].get("OUTREACH_EMAIL"))
    text = f"Arbeitsbereich „{name_}“ gespeichert"
    if werte.get("OUTREACH_EMAIL") != vorher and key != YLVA:
        text += ": Erstkontakt per Mail " + ("erlaubt" if werte.get("OUTREACH_EMAIL") else "ausgeschaltet")
    # Als Regeländerung im Entscheidungs-Log (Erstkontakt, Absender, Budget sind Entscheidungen)
    firma.log(c, wer, "einstellung", "regel", f"arbeitsbereich:{key}", text + ".",
              {"einstellungen": werte, "personen": personen, "postfach_id": postfach_id, "aktiv": aktiv_})
    cache_leeren()
    _CACHE[key] = _laden(c, key)


def projekt_von(c, key):
    a = get(c, key)
    return a["projekt_id"] if a else None


def fuer_projekt(c, projekt_id):
    row = c.execute("SELECT * FROM arbeitsbereiche WHERE projekt_id=? ORDER BY sort LIMIT 1", (projekt_id,)).fetchone()
    return _zeile(row) if row else None

# ---------------------------------------------------------------- Ergebnisse für den Kunden (Kunden-Bereich, Rechnung)

STUFEN_KUNDE = {"antwort": "Hat geantwortet", "gespraech": "Gespräch vereinbart", "angebot": "Angebot",
                "kunde": "Kunde geworden"}


def _woche(iso):
    d = datetime.fromisoformat(iso[:19])
    jahr, kw, _ = d.isocalendar()
    return f"{jahr}-{kw:02d}"


def outreach_zahlen(c, key, wochen=8):
    """Was der Outreach für einen Kunden gebracht hat: gesamt und je Kalenderwoche (neueste zuerst), dazu die
    interessierten Kontakte. Ohne interne Notizen, Punkte oder Begründungen."""
    from datetime import timedelta
    leads = [dict(r) for r in c.execute(
        "SELECT id, name, city, contact_name, email, phone, website, stage, interest, sent_at, replied_at, updated_at "
        "FROM leads WHERE arbeitsbereich=?", (key,))]
    ids = [l["id"] for l in leads]
    termine = []
    if ids:
        marks = ",".join("?" * len(ids))
        termine = [dict(r) for r in c.execute(
            f"SELECT lead_id, start FROM termine WHERE lead_id IN ({marks}) AND status != 'abgesagt'", ids)]
    antwort = [l for l in leads if l["replied_at"]]
    interesse = [l for l in leads if l["interest"] == "qualified" or l["stage"] in ("gespraech", "angebot", "kunde")]
    gesamt = {"angeschrieben": sum(1 for l in leads if l["sent_at"]), "antworten": len(antwort),
              "interesse": len(interesse), "termine": len(termine)}
    gesamt["quote"] = round(100 * gesamt["antworten"] / gesamt["angeschrieben"]) if gesamt["angeschrieben"] else None
    heute = datetime.now()
    kws = []
    for i in range(wochen):
        d = heute - timedelta(weeks=i)
        jahr, kw, _ = d.isocalendar()
        kws.append((f"{jahr}-{kw:02d}", kw))
    je_woche = []
    for schluessel, kw in kws:
        je_woche.append({
            "kw": kw,
            "angeschrieben": sum(1 for l in leads if l["sent_at"] and _woche(l["sent_at"]) == schluessel),
            "antworten": sum(1 for l in antwort if _woche(l["replied_at"]) == schluessel),
            "interesse": sum(1 for l in interesse if _woche(l["replied_at"] or l["updated_at"]) == schluessel),
            "termine": sum(1 for t in termine if t["start"] and _woche(t["start"]) == schluessel)})
    kontakte = [{"name": l["name"], "ort": l["city"] or "", "ansprechperson": l["contact_name"] or "",
                 "email": l["email"] or "", "telefon": l["phone"] or "", "webseite": l["website"] or "",
                 "stand": STUFEN_KUNDE.get(l["stage"], "Interessiert"), "seit": (l["replied_at"] or l["updated_at"])[:10]}
                for l in sorted(interesse, key=lambda x: x["replied_at"] or x["updated_at"], reverse=True)]
    return {"gesamt": gesamt, "wochen": je_woche, "kontakte": kontakte}


def monat_zahlen(c, key, von, bis):
    """Für die Monatsrechnung: angeschrieben im Zeitraum und KI-Kosten (USD) des Arbeitsbereichs."""
    n = c.execute("SELECT COUNT(*) FROM leads WHERE arbeitsbereich=? AND sent_at >= ? AND sent_at < ?",
                  (key, von, bis)).fetchone()[0]
    usd = c.execute("SELECT IFNULL(SUM(cost_usd), 0) FROM ai_usage WHERE arbeitsbereich=? AND at >= ? AND at < ?",
                    (key, von, bis)).fetchone()[0]
    return {"angeschrieben": n, "ki_usd": usd}
