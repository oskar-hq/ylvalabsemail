"""Notfall-Knopf (Firmen-OS, Stufe 6, docs/ROLLEN-UND-ABLAEUFE.md „Notfälle“).

Wer etwas bemerkt, drückt den Knopf und wählt die Art. Das OS benachrichtigt sofort die Zuständigen (Glocke und
Handy), zeigt allen ein Band oben, bis der Notfall behoben ist, und hält alles im Protokoll fest. Wer für welche Art
zuständig ist, steht in den Einstellungen (Bereiche); Startwerte wie im Dokument. Notfälle am Kundensystem ergeben
die Kennzahl „Ausfallzeit“ (kpis.py).
"""
import json
import re
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS notfaelle (
    id INTEGER PRIMARY KEY AUTOINCREMENT, art TEXT NOT NULL, text TEXT NOT NULL, lead_id INTEGER,
    gemeldet_von TEXT, gemeldet_am TEXT NOT NULL, zustaendig TEXT NOT NULL DEFAULT '[]',
    behoben_am TEXT, behoben_von TEXT, notiz TEXT
);
"""

# Art → (Bezeichnung, Erklärung, zuständige Bereiche als Startwert)
ARTEN = {
    "kundensystem": ("Kundensystem gestört", "Ein System beim Kunden läuft nicht oder falsch.", ["technik"]),
    "kundendaten": ("Kundendaten in Gefahr", "Daten könnten in falsche Hände geraten sein. DSGVO-Meldefrist 72 Stunden.",
                    ["technik", "markt"]),
    "abschluss": ("Kunde droht abzuspringen", "Ein Abschluss oder ein laufender Kunde ist in Gefahr.", ["markt"]),
}
ART_LABEL = {k: v[0] for k, v in ARTEN.items()}


class NotfallError(ValueError):
    pass


def now():
    return datetime.now().isoformat(timespec="seconds")


def _json(raw, default):
    try:
        v = json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default
    return v if isinstance(v, type(default)) else default


def regeln(c):
    """{art: [bereich, ...]} – wer bei welcher Art benachrichtigt wird (Verantwortliche dieser Bereiche)."""
    row = c.execute("SELECT value FROM kv WHERE key='regel:notfall'").fetchone()
    stored = _json(row["value"], {}) if row else {}
    return {k: [b for b in stored.get(k, v[2]) if isinstance(b, str)] or v[2] for k, v in ARTEN.items()}


def regeln_speichern(c, wer, form):
    """`form`: Mapping mit Listen je Art (z. B. werkzeug MultiDict via getlist)."""
    from . import firma
    gueltig = {b["key"] for b in firma.bereiche(c)}
    neu = {}
    for k, (label, _, _) in ARTEN.items():
        werte = form.getlist(k) if hasattr(form, "getlist") else form.get(k, [])
        werte = [b for b in dict.fromkeys(werte) if b in gueltig]
        if not werte:
            raise NotfallError(f"{label}: Bitte mindestens einen Bereich wählen.")
        neu[k] = werte
    c.execute("INSERT INTO kv (key, value) VALUES ('regel:notfall', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (json.dumps(neu),))
    namen = {b["key"]: b["name"] for b in firma.bereiche(c)}
    firma.log(c, wer, "einstellung", "regel", "notfall", "Zuständigkeit bei Notfällen geändert: " + "; ".join(
        f"{ART_LABEL[k]} → {', '.join(namen[b] for b in v)}" for k, v in neu.items()) + ".", neu)
    return neu


def zustaendige(c, art):
    from . import firma
    out = []
    for key in regeln(c).get(art, []):
        b = firma.bereich(c, key)
        if b and b["verantwortlich"] and b["verantwortlich"] not in out and firma.aktive_person(c, b["verantwortlich"]):
            out.append(b["verantwortlich"])
    if not out:  # niemand eingetragen: alle, die Personen verwalten
        out = [p["key"] for p in firma.aktive_personen(c) if firma.darf(c, p["key"], "personen", firma.VERWALTEN)]
    return out


def _zeile(c, row):
    from . import firma
    d = dict(row)
    d["zustaendig"] = _json(d["zustaendig"], [])
    d["zustaendig_namen"] = [firma.name_von(c, p) for p in d["zustaendig"]]
    d["art_label"] = ART_LABEL.get(d["art"], d["art"])
    d["von_name"] = firma.name_von(c, d["gemeldet_von"])
    d["behoben_name"] = firma.name_von(c, d["behoben_von"]) if d["behoben_von"] else ""
    d["lead_name"] = None
    if d["lead_id"]:
        lead = c.execute("SELECT name FROM leads WHERE id=?", (d["lead_id"],)).fetchone()
        d["lead_name"] = lead["name"] if lead else None
    return d


def get(c, nid):
    row = c.execute("SELECT * FROM notfaelle WHERE id=?", (nid,)).fetchone()
    return _zeile(c, row) if row else None


def aktive(c):
    return [_zeile(c, r) for r in c.execute("SELECT * FROM notfaelle WHERE behoben_am IS NULL ORDER BY id DESC")]


def liste(c, von=None, bis=None, limit=100):
    """Notfälle, die im Zeitraum offen waren (gemeldet vor `bis`, nicht vor `von` behoben)."""
    where, args = ["1"], []
    if bis:
        where.append("gemeldet_am < ?")
        args.append(bis)
    if von:
        where.append("(behoben_am IS NULL OR behoben_am >= ?)")
        args.append(von)
    rows = c.execute(f"SELECT * FROM notfaelle WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?", [*args, limit])
    return [_zeile(c, r) for r in rows]


def melden(c, wer, art, text, lead_id=None):
    from . import aufgaben, firma
    if art not in ARTEN:
        raise NotfallError("Bitte die Art des Notfalls wählen.")
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) < 3:
        raise NotfallError("Bitte kurz beschreiben, was los ist.")
    lead_id = int(lead_id) if str(lead_id or "").isdigit() else None
    if lead_id and not c.execute("SELECT 1 FROM leads WHERE id=?", (lead_id,)).fetchone():
        lead_id = None
    zust = zustaendige(c, art)
    cur = c.execute("INSERT INTO notfaelle (art, text, lead_id, gemeldet_von, gemeldet_am, zustaendig) "
                    "VALUES (?,?,?,?,?,?)", (art, text[:1000], lead_id, wer, now(), json.dumps(zust)))
    nid = cur.lastrowid
    firma.log(c, wer, "notfall", "notfall", nid, f"Notfall gemeldet: {ART_LABEL[art]} – {text[:200]}",
              {"zustaendig": zust, "lead": lead_id})
    for p in zust:
        if p != wer:
            aufgaben.melden(c, p, f"Notfall: {ART_LABEL[art]}", f"{firma.name_von(c, wer)}: {text[:160]}",
                            f"/notfall#n{nid}", push=True)
    return nid


def beheben(c, wer, nid, notiz=""):
    from . import aufgaben, firma
    n = get(c, nid)
    if not n or n["behoben_am"]:
        raise NotfallError("Dieser Notfall ist schon behoben.")
    notiz = (notiz or "").strip()
    if len(notiz) < 3:
        raise NotfallError("Bitte kurz festhalten, was getan wurde. Daraus lernt das Team.")
    c.execute("UPDATE notfaelle SET behoben_am=?, behoben_von=?, notiz=? WHERE id=?", (now(), wer, notiz[:2000], nid))
    firma.log(c, wer, "notfall", "notfall", nid, f"Notfall behoben: {n['art_label']} – {notiz[:200]}")
    for p in {*n["zustaendig"], n["gemeldet_von"]} - {wer, None}:
        aufgaben.melden(c, p, f"Notfall behoben: {n['art_label']}", f"{firma.name_von(c, wer)}: {notiz[:160]}",
                        f"/notfall#n{nid}")
    return get(c, nid)


def ausfallzeit(c, von, bis, person=None):
    """KPI: Stunden, in denen ein Kundensystem in der Woche gestört war (überlappende Zeiträume zusammengefasst)."""
    if person:
        return None  # Teamzahl
    jetzt = now()
    spannen = []
    for r in c.execute("SELECT gemeldet_am, behoben_am FROM notfaelle WHERE art='kundensystem' AND gemeldet_am < ? "
                       "AND (behoben_am IS NULL OR behoben_am >= ?) ORDER BY gemeldet_am", (bis, von)):
        a, b = max(r["gemeldet_am"], von), min(r["behoben_am"] or jetzt, bis)
        if b > a:
            spannen.append((a, b))
    sekunden, ende = 0.0, None
    for a, b in spannen:
        if ende and a < ende:
            a = ende
        if b > a:
            sekunden += (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()
            ende = b
        ende = max(ende or b, b)
    return round(sekunden / 3600, 1)
