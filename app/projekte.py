"""Kunden und Projekte (Firmen-OS, Stufe 5): Customer Journey nach „Kunde“, Anforderung, Bau, Abnahme mit Checkliste,
Live und Betrieb, Zeiterfassung, Änderungswünsche mit Abrechnungsregeln, Kosten je Kunde.

Regeln aus docs/ROLLEN-UND-ABLAEUFE.md, alle unter Einstellungen → Projekte änderbar:
- Oskar entscheidet, was geliefert wird (verantwortlich), Finn, wie gebaut wird (Technik).
- Bau startet erst, wenn die Anforderung schriftlich steht, der Kunde sie bestätigt hat, Finns Aufwandsschätzung da
  ist und beide (verantwortlich und Technik) freigegeben haben.
- Abnahme: Checkliste muss komplett abgehakt sein, sonst zurück in den Bau. Das letzte Wort hat die verantwortliche
  Person (Stufe D).
- Änderungswünsche: Oskar ordnet ein (klein/groß, Vorrang), Finn schätzt. Abrechnung nach Regel: in den ersten
  30 Tagen nach Live inklusive, danach klein = Pauschale (einmal pro Woche umgesetzt), groß = eigenes Angebot.
- Zeiterfassung an Projekt, Änderung oder Aufgabe (Start/Stopp oder nachtragen), Pflicht für den Bau.
- Liegt die Zeit je Kunde und Monat zwei Monate am Stück über der Annahme im Preismodell, meldet das OS das an
  verantwortlich und Technik als Entscheidungsvorlage.
"""
import json
import math
import re
from datetime import date, datetime, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS projekte (
    id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL, titel TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'beauftragt', verantwortlich TEXT, technik TEXT,
    preis_einmal REAL, pauschale_monat REAL, schaetzung_h REAL,
    anforderung TEXT, kunde_bestaetigt_am TEXT, freigabe_markt_von TEXT, freigabe_technik_von TEXT,
    beauftragt_am TEXT NOT NULL, bau_start TEXT, bau_ende_plan TEXT, live_am TEXT, betrieb_am TEXT, beendet_am TEXT,
    erstellt_von TEXT, erstellt_am TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS projekte_lead ON projekte(lead_id);
CREATE TABLE IF NOT EXISTS abnahme_punkte (
    id INTEGER PRIMARY KEY AUTOINCREMENT, projekt_id INTEGER NOT NULL, text TEXT NOT NULL, sort INTEGER NOT NULL DEFAULT 0,
    erledigt_von TEXT, erledigt_am TEXT
);
CREATE TABLE IF NOT EXISTS abnahmen (
    id INTEGER PRIMARY KEY AUTOINCREMENT, projekt_id INTEGER NOT NULL, at TEXT NOT NULL, wer TEXT NOT NULL,
    bestanden INTEGER NOT NULL, notiz TEXT
);
CREATE TABLE IF NOT EXISTS aenderungen (
    id INTEGER PRIMARY KEY AUTOINCREMENT, projekt_id INTEGER NOT NULL, text TEXT NOT NULL, groesse TEXT,
    vorrang INTEGER NOT NULL DEFAULT 2, status TEXT NOT NULL DEFAULT 'neu', abrechnung TEXT, schaetzung_min INTEGER,
    quelle TEXT NOT NULL DEFAULT 'hand', erstellt_von TEXT, erstellt_am TEXT NOT NULL, erledigt_am TEXT, grund TEXT
);
CREATE INDEX IF NOT EXISTS aenderungen_projekt ON aenderungen(projekt_id, status);
CREATE TABLE IF NOT EXISTS zeiten (
    id INTEGER PRIMARY KEY AUTOINCREMENT, person TEXT NOT NULL, start TEXT NOT NULL, ende TEXT, minuten INTEGER,
    objekt_typ TEXT NOT NULL, objekt_id INTEGER NOT NULL, projekt_id INTEGER, notiz TEXT
);
CREATE INDEX IF NOT EXISTS zeiten_person ON zeiten(person, ende);
CREATE INDEX IF NOT EXISTS zeiten_projekt ON zeiten(projekt_id, start);
"""

STATUS = [
    ("beauftragt", "Beauftragt", "AV-Vertrag, Rechnung Einmalbetrag, Anforderungstermin"),
    ("anforderung", "Anforderung", "Beschreibung schreiben, Kunde bestätigt, Finn schätzt"),
    ("bau", "Bau", "Finn baut, Zwischenstand nach der Hälfte"),
    ("abnahme", "Abnahme", "Checkliste durchgehen, Oskar hat das letzte Wort"),
    ("live", "Live", "Einweisung, erste Freigabeliste, 30 Tage kostenlose Anpassungen"),
    ("betrieb", "Im Betrieb", "Wöchentlicher Check-in, Zahlen für die Case Study"),
    ("beendet", "Beendet", "Kunde ist nicht mehr aktiv"),
]
STATUS_LABEL = {k: label for k, label, _ in STATUS}
STATUS_KEYS = [k for k, _, _ in STATUS]
AENDERUNG_STATUS = {"neu": "Neu, einordnen", "eingeordnet": "Eingeordnet", "geschaetzt": "Geschätzt",
                    "erledigt": "Erledigt", "abgelehnt": "Abgelehnt"}
ABRECHNUNG = {"inklusive": "Inklusive", "pauschale": "Pauschale (kleine Änderung)", "angebot": "Eigenes Angebot"}

CHECKLISTE = ["Alle Punkte aus der Anforderung erfüllt", "Mit echten Daten des Kunden getestet",
              "Zugänge und Rechte eingerichtet", "Datenschutz und AV-Vertrag geklärt",
              "Kurzanleitung für das Team des Kunden fertig", "Sicherung und Überwachung eingerichtet"]
REGELN = {  # Schlüssel: (Bezeichnung, Standard, Typ)
    "kostenlose_tage": ("Kostenlose Anpassungen nach Live (Tage)", 30, "int"),
    "klein_min": ("Kleine Änderung: höchstens (Minuten)", 60, "int"),
    "pauschale_klein": ("Pauschale je kleiner Änderung (€)", 50, "int"),
    "umsetzungstag": ("Kleine Änderungen werden umgesetzt am", 4, "tag"),
    "stunden_pro_tag": ("Bau: Stunden pro Arbeitstag (für den geplanten Termin)", 4, "int"),
    "annahme_h_monat": ("Annahme im Preismodell: Stunden je Kunde und Monat", 1, "int"),
    "stundensatz": ("Interner Stundensatz für die Kosten je Kunde (€)", 60, "int"),
    "server_monat": ("Serverkosten pro Monat gesamt (€)", 20, "int"),
    "zeit_pflicht": ("Ohne erfasste Zeit kein Wechsel vom Bau zur Abnahme", True, "bool"),
    "bereich_kunden": ("Bereich der Kundenverantwortung", "markt", "bereich"),
    "bereich_technik": ("Bereich der Technik", "technik", "bereich"),
}
WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


class ProjektError(ValueError):
    pass


def now():
    return datetime.now().isoformat(timespec="seconds")

# ---------------------------------------------------------------- Regeln


def regeln(c):
    row = c.execute("SELECT value FROM kv WHERE key='regel:projekte'").fetchone()
    try:
        stored = json.loads(row["value"]) if row else {}
    except ValueError:
        stored = {}
    out = {}
    for k, (_l, default, typ) in REGELN.items():
        v = stored.get(k, default)
        if typ in ("int", "tag"):
            try:
                v = int(v)
            except (TypeError, ValueError):
                v = default
        out[k] = v
    out["checkliste"] = stored.get("checkliste") or CHECKLISTE
    return out


def regeln_speichern(c, wer, form):
    from . import firma
    neu = {}
    for k, (label, _d, typ) in REGELN.items():
        raw = str(form.get(k, "")).strip()
        if typ == "int":
            if not raw.isdigit() or int(raw) > 100000:
                raise ProjektError(f"{label}: bitte eine Zahl.")
            neu[k] = int(raw)
        elif typ == "tag":
            if raw not in "0123456" or not raw:
                raise ProjektError("Bitte einen Wochentag.")
            neu[k] = int(raw)
        elif typ == "bool":
            neu[k] = raw in ("1", "on", "true")
        elif typ == "bereich":
            if not firma.bereich(c, raw):
                raise ProjektError(f"{label}: Bereich wählen.")
            neu[k] = raw
    if neu["stunden_pro_tag"] < 1:
        raise ProjektError("Stunden pro Arbeitstag: mindestens 1.")
    punkte = [p.strip() for p in str(form.get("checkliste", "")).splitlines() if p.strip()]
    if not punkte:
        raise ProjektError("Die Abnahme-Checkliste braucht mindestens einen Punkt.")
    neu["checkliste"] = punkte[:40]
    c.execute("INSERT INTO kv (key, value) VALUES ('regel:projekte', ?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(neu, ensure_ascii=False),))
    firma.log(c, wer, "einstellung", "regel", "projekte", "Regeln für Projekte und Änderungswünsche geändert.",
              {k: v for k, v in neu.items() if k != "checkliste"})

# ---------------------------------------------------------------- Projekte


def get(c, pid):
    row = c.execute("""SELECT p.*, l.name AS kunde, l.contact_name, l.email AS kunde_email, l.phone AS kunde_telefon,
                              v.name AS verantwortlich_name, t.name AS technik_name
                       FROM projekte p LEFT JOIN leads l ON l.id = p.lead_id
                       LEFT JOIN team v ON v.key = p.verantwortlich LEFT JOIN team t ON t.key = p.technik
                       WHERE p.id=?""", (pid,)).fetchone()
    if not row:
        return None
    p = dict(row)
    p["status_label"] = STATUS_LABEL.get(p["status"], p["status"])
    p["status_index"] = STATUS_KEYS.index(p["status"]) if p["status"] in STATUS_KEYS else 0
    p["punkte"] = [dict(r) for r in c.execute("SELECT * FROM abnahme_punkte WHERE projekt_id=? ORDER BY sort, id", (pid,))]
    p["abnahmen"] = [dict(r) for r in c.execute("SELECT * FROM abnahmen WHERE projekt_id=? ORDER BY id", (pid,))]
    p["zeit_min"] = c.execute("SELECT IFNULL(SUM(minuten),0) FROM zeiten WHERE projekt_id=? AND minuten IS NOT NULL",
                              (pid,)).fetchone()[0]
    r = regeln(c)
    p["kostenlos_bis"] = ((date.fromisoformat(p["live_am"][:10]) + timedelta(days=r["kostenlose_tage"])).isoformat()
                          if p["live_am"] else None)
    p["kostenlos"] = bool(p["kostenlos_bis"] and date.today().isoformat() <= p["kostenlos_bis"])
    return p


def zum_lead(c, lead_id):
    row = c.execute("SELECT id FROM projekte WHERE lead_id=? ORDER BY id DESC LIMIT 1", (lead_id,)).fetchone()
    return get(c, row["id"]) if row else None


def liste(c, status=None):
    where, args = ("WHERE status=?", [status]) if status else ("WHERE status != 'beendet'", [])
    return [get(c, r["id"]) for r in c.execute(f"SELECT id FROM projekte {where} ORDER BY beauftragt_am DESC", args)]


def _verantwortliche(c):
    from . import firma
    r = regeln(c)
    markt, technik = firma.bereich(c, r["bereich_kunden"]), firma.bereich(c, r["bereich_technik"])
    return (markt or {}).get("verantwortlich"), (technik or {}).get("verantwortlich")


def _aufgabe(c, wer, titel, bereich_regel, person, faellig, pid):
    from . import aufgaben
    r = regeln(c)
    try:
        return aufgaben.anlegen(c, wer, titel, r[bereich_regel], faellig, person=person, objekt_typ="projekt",
                                objekt_id=pid, quelle="projekt", streng=False)
    except Exception:  # Bereich archiviert o. Ä.: Projekt geht trotzdem weiter, die Aufgabe fehlt nur
        return None


def anlegen(c, wer, lead_id, titel="", preis_einmal=None, pauschale_monat=None, verantwortlich=None, technik=None):
    """Projekt für einen Kunden anlegen (Status „beauftragt“) mit den ersten Aufgaben: AV-Vertrag, Rechnung,
    Anforderungstermin."""
    from . import firma, leaddb
    lead = leaddb.get_lead(c, lead_id)
    if not lead:
        raise ProjektError("Diesen Kunden gibt es nicht.")
    v_std, t_std = _verantwortliche(c)
    verantwortlich, technik = verantwortlich or v_std, technik or t_std
    for p in (verantwortlich, technik):
        if p and not firma.aktive_person(c, p):
            raise ProjektError("Verantwortlich und Technik müssen aktive Personen sein.")
    titel = re.sub(r"\s+", " ", titel or "").strip() or f"Projekt {lead['name']}"
    cur = c.execute("INSERT INTO projekte (lead_id, titel, verantwortlich, technik, preis_einmal, pauschale_monat, "
                    "beauftragt_am, erstellt_von, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?)",
                    (lead_id, titel[:200], verantwortlich, technik, _zahl(preis_einmal), _zahl(pauschale_monat),
                     now(), wer, now()))
    pid = cur.lastrowid
    firma.log(c, wer, "projekt", "projekt", pid, f"Projekt angelegt: {titel}", {"lead": lead_id, "status": "beauftragt"})
    leaddb.add_event(c, lead_id, "status", f"Projekt angelegt: {titel}.", author=firma.name_von(c, wer) or "Ablauf")
    morgen = (date.today() + timedelta(days=1)).isoformat()
    _aufgabe(c, wer, f"AV-Vertrag abschließen: {lead['name']}", "bereich_kunden", verantwortlich, morgen, pid)
    _aufgabe(c, wer, f"Rechnung Einmalbetrag vorbereiten: {lead['name']}", "bereich_kunden", verantwortlich, morgen, pid)
    _aufgabe(c, wer, f"Anforderungstermin buchen: {lead['name']}", "bereich_kunden", verantwortlich, morgen, pid)
    return pid


def aus_kunde(c, lead_id, wer):
    """Lead wurde „Kunde“: Projekt automatisch anlegen, falls es noch keins gibt (G3: erfassen nebenbei)."""
    if c.execute("SELECT 1 FROM projekte WHERE lead_id=?", (lead_id,)).fetchone():
        return None
    from . import firma
    key = wer if firma.person(c, wer) else (c.execute("SELECT key FROM team WHERE name=?", (wer,)).fetchone() or
                                             {"key": "System"})["key"]
    return anlegen(c, key, lead_id)


def _zahl(v):
    """Deutsche und englische Schreibweise: 4.500 / 4.500,50 / 4500.5 / 12,5"""
    if v in (None, ""):
        return None
    s = str(v).strip().replace(" ", "").replace("€", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return round(float(s), 2)
    except ValueError as exc:
        raise ProjektError("Beträge und Stunden bitte als Zahl.") from exc


def aendern(c, wer, pid, **felder):
    from . import firma
    p = get(c, pid)
    if not p:
        raise ProjektError("Dieses Projekt gibt es nicht.")
    neu = {}
    for k in ("preis_einmal", "pauschale_monat"):
        if k in felder:
            neu[k] = _zahl(felder[k])
    if "titel" in felder and felder["titel"].strip():
        neu["titel"] = felder["titel"].strip()[:200]
    if "anforderung" in felder:
        neu["anforderung"] = felder["anforderung"].strip()[:20000] or None
        if neu["anforderung"] != p["anforderung"]:  # geänderte Anforderung muss neu bestätigt und freigegeben werden
            neu.update(kunde_bestaetigt_am=None, freigabe_markt_von=None, freigabe_technik_von=None)
    for k in ("verantwortlich", "technik"):
        if felder.get(k):
            if not firma.aktive_person(c, felder[k]):
                raise ProjektError("Bitte eine aktive Person.")
            neu[k] = felder[k]
    neu = {k: v for k, v in neu.items() if p.get(k) != v}
    if not neu:
        return False
    c.execute(f"UPDATE projekte SET {', '.join(k + '=?' for k in neu)} WHERE id=?", [*neu.values(), pid])
    firma.log(c, wer, "projekt", "projekt", pid, f"Projekt geändert: {p['titel']}",
              {k: v for k, v in neu.items() if k != "anforderung"} | ({"anforderung": "geändert"} if "anforderung" in neu
                                                                     else {}))
    return True


def schaetzen(c, wer, pid, stunden):
    from . import firma
    p = get(c, pid)
    if wer != p["technik"]:  # Regel: kein Versprechen ohne die Schätzung der Technik, auch nicht stellvertretend
        raise ProjektError("Die Aufwandsschätzung gibt die Technik des Projekts ab.")
    h = _zahl(stunden)
    if not h or h <= 0 or h > 2000:
        raise ProjektError("Schätzung bitte in Stunden, größer als 0.")
    c.execute("UPDATE projekte SET schaetzung_h=?, freigabe_technik_von=NULL WHERE id=?", (h, pid))
    firma.log(c, wer, "projekt", "projekt", pid, f"Aufwand geschätzt: {h:g} Stunden.", {"schaetzung_h": h})


def kunde_bestaetigt(c, wer, pid, ja=True):
    from . import firma
    p = get(c, pid)
    if not p["anforderung"]:
        raise ProjektError("Erst die Anforderung schriftlich festhalten.")
    c.execute("UPDATE projekte SET kunde_bestaetigt_am=? WHERE id=?", (now() if ja else None, pid))
    firma.log(c, wer, "projekt", "projekt", pid, "Kunde hat die Anforderung bestätigt." if ja else
              "Bestätigung des Kunden zurückgenommen.")


def anforderung_freigeben(c, wer, pid):
    """Freigabe der Anforderung: verantwortlich (was) und Technik (wie, mit Schätzung). Beide nötig."""
    from . import firma
    p = get(c, pid)
    if not p["anforderung"] or not p["kunde_bestaetigt_am"]:
        raise ProjektError("Freigeben geht erst, wenn die Anforderung steht und der Kunde sie bestätigt hat.")
    if not p["schaetzung_h"]:
        raise ProjektError("Ohne Finns Aufwandsschätzung kein Versprechen an den Kunden: erst schätzen.")
    feld = "freigabe_markt_von" if wer == p["verantwortlich"] else ("freigabe_technik_von" if wer == p["technik"] else None)
    if not feld:
        raise ProjektError("Freigeben dürfen die verantwortliche Person und die Technik des Projekts.")
    c.execute(f"UPDATE projekte SET {feld}=? WHERE id=?", (wer, pid))
    firma.log(c, wer, "freigabe", "projekt", pid, f"Anforderung freigegeben ({'was' if feld == 'freigabe_markt_von' else 'wie'}).")


# Aufgaben, die das OS für einen Schritt anlegt und beim Verlassen des Schritts selbst abschließt
SCHRITT_AUFGABEN = {"anforderung": ("Anforderung schreiben", "Aufwand schätzen"),
                    "bau": ("Bau:", "Zwischenstand zeigen", "Nacharbeit nach Abnahme"),
                    "abnahme": ("Abnahme-Checkliste durchgehen",)}


def _setze_status(c, wer, p, nach, extra=None):
    from . import firma, leaddb
    for praefix in SCHRITT_AUFGABEN.get(p["status"], ()):
        c.execute("UPDATE aufgaben SET status='erledigt', erledigt_am=?, erledigt_von=? WHERE objekt_typ='projekt' "
                  "AND objekt_id=? AND quelle='projekt' AND status='offen' AND titel LIKE ?",
                  (now(), wer, p["id"], praefix + "%"))
    felder = {"status": nach, **(extra or {})}
    c.execute(f"UPDATE projekte SET {', '.join(k + '=?' for k in felder)} WHERE id=?", [*felder.values(), p["id"]])
    firma.log(c, wer, "projekt", "projekt", p["id"], f"{p['titel']}: {STATUS_LABEL[p['status']]} → {STATUS_LABEL[nach]}",
              {"von": p["status"], "nach": nach, "lead": p["lead_id"]})
    leaddb.add_event(c, p["lead_id"], "status", f"Projekt: {STATUS_LABEL[nach]}.", author=firma.name_von(c, wer))


def weiter(c, wer, pid, notiz=""):
    """Nächster Schritt der Customer Journey, mit den Regeln je Schritt."""
    from . import aufgaben, firma
    p = get(c, pid)
    if not p:
        raise ProjektError("Dieses Projekt gibt es nicht.")
    r = regeln(c)
    s = p["status"]
    if s == "beauftragt":
        if wer != p["verantwortlich"] and not firma.darf(c, wer, "kunden", firma.VERWALTEN):
            raise ProjektError("Den nächsten Schritt setzt die verantwortliche Person.")
        _setze_status(c, wer, p, "anforderung")
        _aufgabe(c, wer, f"Anforderung schreiben: {p['kunde']}", "bereich_kunden", p["verantwortlich"],
                 (date.today() + timedelta(days=3)).isoformat(), pid)
        _aufgabe(c, wer, f"Aufwand schätzen: {p['kunde']}", "bereich_technik", p["technik"],
                 (date.today() + timedelta(days=5)).isoformat(), pid)
    elif s == "anforderung":
        if not (p["freigabe_markt_von"] and p["freigabe_technik_von"]):
            raise ProjektError("Der Bau startet erst, wenn verantwortlich und Technik die Anforderung freigegeben haben.")
        tage = math.ceil((p["schaetzung_h"] or 0) / r["stunden_pro_tag"])
        ende = _arbeitstage(date.today(), max(tage, 1))
        haelfte = _arbeitstage(date.today(), max(tage // 2, 1))
        _setze_status(c, wer, p, "bau", {"bau_start": now(), "bau_ende_plan": ende.isoformat()})
        _aufgabe(c, wer, f"Bau: {p['titel']}", "bereich_technik", p["technik"], ende.isoformat(), pid)
        _aufgabe(c, wer, f"Zwischenstand zeigen: {p['kunde']}", "bereich_technik", p["technik"], haelfte.isoformat(), pid)
    elif s == "bau":
        if wer != p["technik"] and not firma.darf(c, wer, "kunden", firma.VERWALTEN):
            raise ProjektError("„Build fertig“ meldet die Technik.")
        if r["zeit_pflicht"] and not p["zeit_min"]:
            raise ProjektError("Für den Bau ist noch keine Zeit erfasst. Bitte erst die Zeit nachtragen.")
        if not p["punkte"]:
            for i, text in enumerate(r["checkliste"]):
                c.execute("INSERT INTO abnahme_punkte (projekt_id, text, sort) VALUES (?,?,?)", (pid, text, i))
        else:
            c.execute("UPDATE abnahme_punkte SET erledigt_von=NULL, erledigt_am=NULL WHERE projekt_id=?", (pid,))
        _setze_status(c, wer, p, "abnahme")
        _aufgabe(c, wer, f"Abnahme-Checkliste durchgehen: {p['kunde']}", "bereich_kunden", p["verantwortlich"],
                 (date.today() + timedelta(days=2)).isoformat(), pid)
        if p["verantwortlich"] != wer:
            aufgaben.melden(c, p["verantwortlich"], f"Build fertig: {p['titel']}", "Abnahme-Checkliste durchgehen",
                            f"/kunden/{pid}", push=True)
    elif s == "abnahme":
        abnahme(c, wer, pid, notiz)
    elif s == "live":
        if wer != p["verantwortlich"] and not firma.darf(c, wer, "kunden", firma.VERWALTEN):
            raise ProjektError("Den nächsten Schritt setzt die verantwortliche Person.")
        _setze_status(c, wer, p, "betrieb", {"betrieb_am": now()})
    elif s == "betrieb":
        raise ProjektError("Im Betrieb ist der letzte Schritt. Beenden geht über „Kunde beendet“.")
    return get(c, pid)


def _arbeitstage(start, n):
    d = start
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def punkt(c, wer, pid, punkt_id, erledigt=True):
    from . import firma
    p = get(c, pid)
    if p["status"] != "abnahme":
        raise ProjektError("Die Checkliste gilt während der Abnahme.")
    c.execute("UPDATE abnahme_punkte SET erledigt_von=?, erledigt_am=? WHERE id=? AND projekt_id=?",
              (wer if erledigt else None, now() if erledigt else None, punkt_id, pid))
    firma.log(c, wer, "abnahme", "projekt", pid, "Checkliste: Punkt " + ("abgehakt." if erledigt else "wieder offen."),
              {"punkt": punkt_id})


def abnahme(c, wer, pid, notiz=""):
    """Abnahme vor der Übergabe: nur die verantwortliche Person (Stufe D). Alles abgehakt → Live, sonst zurück in den
    Bau. Jede Abnahme zählt für „Abnahmen beim ersten Mal“."""
    from . import aufgaben, firma
    p = get(c, pid)
    if p["status"] != "abnahme":
        raise ProjektError("Das Projekt ist nicht in der Abnahme.")
    if wer != p["verantwortlich"]:
        raise ProjektError("Bei der Abnahme hat die verantwortliche Person das letzte Wort.")
    offen = [x["text"] for x in p["punkte"] if not x["erledigt_am"]]
    bestanden = not offen
    c.execute("INSERT INTO abnahmen (projekt_id, at, wer, bestanden, notiz) VALUES (?,?,?,?,?)",
              (pid, now(), wer, 1 if bestanden else 0, (notiz or "").strip()[:2000] or ("Offen: " + "; ".join(offen))))
    if bestanden:
        _setze_status(c, wer, p, "live", {"live_am": now()})
        _aufgabe(c, wer, f"Einweisung beim Kunden: {p['kunde']}", "bereich_kunden", p["verantwortlich"],
                 (date.today() + timedelta(days=3)).isoformat(), pid)
        _aufgabe(c, wer, f"Baseline-Zahlen erfassen: {p['kunde']}", "bereich_kunden", p["verantwortlich"],
                 (date.today() + timedelta(days=7)).isoformat(), pid)
    else:
        _setze_status(c, wer, p, "bau")
        _aufgabe(c, wer, f"Nacharbeit nach Abnahme: {p['titel']}", "bereich_technik", p["technik"],
                 (date.today() + timedelta(days=3)).isoformat(), pid)
        if p["technik"] and p["technik"] != wer:
            aufgaben.melden(c, p["technik"], f"Zurück in den Bau: {p['titel']}", "Offen: " + "; ".join(offen)[:140],
                            f"/kunden/{pid}", push=True)
        firma.log(c, wer, "abnahme", "projekt", pid, "Abnahme nicht bestanden, zurück in den Bau.", {"offen": offen})
    return bestanden


def beenden(c, wer, pid, grund=""):
    from . import firma
    p = get(c, pid)
    if wer != p["verantwortlich"] and not firma.darf(c, wer, "kunden", firma.VERWALTEN):
        raise ProjektError("Beenden darf die verantwortliche Person.")
    _setze_status(c, wer, p, "beendet", {"beendet_am": now()})
    if grund:
        firma.log(c, wer, "projekt", "projekt", pid, f"Beendet: {grund}")

# ---------------------------------------------------------------- Änderungswünsche


def aenderung_get(c, aid):
    row = c.execute("SELECT a.*, p.titel AS projekt_titel, p.verantwortlich, p.technik, l.name AS kunde "
                    "FROM aenderungen a JOIN projekte p ON p.id = a.projekt_id LEFT JOIN leads l ON l.id = p.lead_id "
                    "WHERE a.id=?", (aid,)).fetchone()
    if not row:
        return None
    a = dict(row)
    a["zeit_min"] = c.execute("SELECT IFNULL(SUM(minuten),0) FROM zeiten WHERE objekt_typ='aenderung' AND objekt_id=?",
                              (aid,)).fetchone()[0]
    a["status_label"] = AENDERUNG_STATUS.get(a["status"], a["status"])
    a["abrechnung_label"] = ABRECHNUNG.get(a["abrechnung"] or "", "")
    return a


def aenderungen(c, pid, offen=None):
    where = "projekt_id=?" + ({True: " AND status NOT IN ('erledigt','abgelehnt')",
                               False: " AND status IN ('erledigt','abgelehnt')"}.get(offen, ""))
    return [aenderung_get(c, r["id"]) for r in c.execute(f"SELECT id FROM aenderungen WHERE {where} ORDER BY "
                                                        "status='erledigt', vorrang, id", (pid,))]


def aenderung_anlegen(c, wer, pid, text, quelle="hand"):
    """Ein Wunsch des Kunden, immer an einer Stelle: Das OS legt ihn an, die verantwortliche Person ordnet ein. Nie
    direkt an die Technik."""
    from . import aufgaben, firma
    p = get(c, pid)
    if not p:
        raise ProjektError("Dieses Projekt gibt es nicht.")
    text = (text or "").strip()
    if len(text) < 3:
        raise ProjektError("Was wünscht sich der Kunde?")
    cur = c.execute("INSERT INTO aenderungen (projekt_id, text, quelle, erstellt_von, erstellt_am) VALUES (?,?,?,?,?)",
                    (pid, text[:4000], quelle, wer, now()))
    aid = cur.lastrowid
    firma.log(c, wer, "aenderung", "aenderung", aid, f"Änderungswunsch {p['kunde']}: {text[:80]}", {"projekt": pid})
    if p["verantwortlich"] and p["verantwortlich"] != wer:
        aufgaben.melden(c, p["verantwortlich"], f"Änderungswunsch einordnen: {p['kunde']}", text[:140], f"/kunden/{pid}")
    return aid


def abrechnung_fuer(c, p, groesse):
    """Regel: vor Live und in den ersten Tagen danach inklusive (große Änderungen vor Live: eigenes Angebot);
    danach klein = Pauschale, groß = eigenes Angebot."""
    if not p["live_am"]:
        return "inklusive" if groesse == "klein" else "angebot"
    if p["kostenlos"]:
        return "inklusive"
    return "pauschale" if groesse == "klein" else "angebot"


def _naechster_umsetzungstag(c):
    tag = regeln(c)["umsetzungstag"]
    d = date.today()
    while d.weekday() != tag or d == date.today():
        d += timedelta(days=1)
    return d


def einordnen(c, wer, aid, groesse, vorrang=2):
    from . import firma
    a = aenderung_get(c, aid)
    if not a:
        raise ProjektError("Diesen Änderungswunsch gibt es nicht.")
    if wer != a["verantwortlich"] and not firma.darf(c, wer, "kunden", firma.VERWALTEN):
        raise ProjektError("Einordnen darf die verantwortliche Person des Kunden.")
    if groesse not in ("klein", "gross"):
        raise ProjektError("Klein oder groß?")
    p = get(c, a["projekt_id"])
    abr = abrechnung_fuer(c, p, groesse)
    vorrang = int(vorrang) if str(vorrang) in ("1", "2", "3") else 2
    c.execute("UPDATE aenderungen SET groesse=?, vorrang=?, abrechnung=?, status='eingeordnet' WHERE id=?",
              (groesse, vorrang, abr, aid))
    firma.log(c, wer, "aenderung", "aenderung", aid, f"Eingeordnet: {'klein' if groesse == 'klein' else 'groß'}, "
              f"{ABRECHNUNG[abr]}.", {"groesse": groesse, "abrechnung": abr, "vorrang": vorrang})
    faellig = (_naechster_umsetzungstag(c) if groesse == "klein" else date.today() + timedelta(days=2)).isoformat()
    titel = (f"Kleine Änderung umsetzen: {p['kunde']}" if groesse == "klein" else f"Änderung schätzen: {p['kunde']}")
    from . import aufgaben
    try:
        aufgaben.anlegen(c, wer, titel, regeln(c)["bereich_technik"], faellig, person=p["technik"], prio=vorrang,
                         notiz=a["text"], objekt_typ="aenderung", objekt_id=aid, quelle="projekt", streng=False)
    except Exception:
        pass


def aenderung_schaetzen(c, wer, aid, minuten):
    from . import firma
    a = aenderung_get(c, aid)
    if wer != a["technik"]:
        raise ProjektError("Schätzen darf die Technik des Projekts.")
    try:
        m = int(minuten)
    except (TypeError, ValueError) as exc:
        raise ProjektError("Schätzung in Minuten.") from exc
    if m <= 0:
        raise ProjektError("Schätzung in Minuten, größer als 0.")
    groesse = "klein" if m <= regeln(c)["klein_min"] else "gross"
    abr = abrechnung_fuer(c, get(c, a["projekt_id"]), groesse)
    c.execute("UPDATE aenderungen SET schaetzung_min=?, groesse=?, abrechnung=?, status='geschaetzt' WHERE id=?",
              (m, groesse, abr, aid))
    firma.log(c, wer, "aenderung", "aenderung", aid, f"Geschätzt: {m} Minuten ({ABRECHNUNG[abr]}).",
              {"schaetzung_min": m, "abrechnung": abr})
    if groesse != a["groesse"] and a["verantwortlich"] and a["verantwortlich"] != wer:
        from . import aufgaben
        aufgaben.melden(c, a["verantwortlich"], f"Änderung ist {'klein' if groesse == 'klein' else 'groß'}: {a['kunde']}",
                        f"{m} Minuten geschätzt, {ABRECHNUNG[abr]}.", f"/kunden/{a['projekt_id']}")


def aenderung_abschliessen(c, wer, aid, erledigt=True, grund=""):
    from . import firma
    a = aenderung_get(c, aid)
    if not a:
        raise ProjektError("Diesen Änderungswunsch gibt es nicht.")
    if not erledigt and len((grund or "").strip()) < 3:
        raise ProjektError("Ablehnen nur mit Grund.")
    c.execute("UPDATE aenderungen SET status=?, erledigt_am=?, grund=? WHERE id=?",
              ("erledigt" if erledigt else "abgelehnt", now(), (grund or "").strip()[:500] or None, aid))
    c.execute("UPDATE aufgaben SET status='erledigt', erledigt_am=?, erledigt_von=? WHERE objekt_typ='aenderung' "
              "AND objekt_id=? AND status='offen'", (now(), wer, aid))
    firma.log(c, wer, "aenderung", "aenderung", aid, ("Erledigt: " if erledigt else "Abgelehnt: ") + a["text"][:80])

# ---------------------------------------------------------------- Zeiterfassung


def laufend(c, person):
    row = c.execute("SELECT * FROM zeiten WHERE person=? AND ende IS NULL ORDER BY id DESC LIMIT 1", (person,)).fetchone()
    if not row:
        return None
    z = dict(row)
    z["titel"] = _objekt_titel(c, z["objekt_typ"], z["objekt_id"])
    z["seit_min"] = int((datetime.now() - datetime.fromisoformat(z["start"])).total_seconds() // 60)
    return z


def _objekt_titel(c, typ, oid):
    if typ == "projekt":
        p = c.execute("SELECT titel FROM projekte WHERE id=?", (oid,)).fetchone()
        return p["titel"] if p else "Projekt"
    if typ == "aenderung":
        a = c.execute("SELECT text FROM aenderungen WHERE id=?", (oid,)).fetchone()
        return ("Änderung: " + a["text"][:40]) if a else "Änderung"
    if typ == "aufgabe":
        a = c.execute("SELECT titel FROM aufgaben WHERE id=?", (oid,)).fetchone()
        return a["titel"] if a else "Aufgabe"
    return typ


def _projekt_von(c, typ, oid):
    if typ == "projekt":
        return oid if c.execute("SELECT 1 FROM projekte WHERE id=?", (oid,)).fetchone() else None
    if typ == "aenderung":
        row = c.execute("SELECT projekt_id FROM aenderungen WHERE id=?", (oid,)).fetchone()
        return row["projekt_id"] if row else None
    if typ == "aufgabe":
        row = c.execute("SELECT objekt_typ, objekt_id FROM aufgaben WHERE id=?", (oid,)).fetchone()
        if not row:
            return None
        return _projekt_von(c, row["objekt_typ"], row["objekt_id"]) if row["objekt_typ"] in ("projekt", "aenderung") \
            else 0
    return None


def start(c, person, typ, oid, notiz=""):
    """Zeit läuft. Eine laufende Zeit der Person wird vorher gestoppt (immer nur eine)."""
    from . import firma
    if typ not in ("projekt", "aenderung", "aufgabe"):
        raise ProjektError("Zeit läuft an Projekt, Änderung oder Aufgabe.")
    pid = _projekt_von(c, typ, oid)
    if pid is None:
        raise ProjektError("Das gibt es nicht (mehr).")
    stopp(c, person)
    cur = c.execute("INSERT INTO zeiten (person, start, objekt_typ, objekt_id, projekt_id, notiz) VALUES (?,?,?,?,?,?)",
                    (person, now(), typ, oid, pid or None, (notiz or "").strip()[:300] or None))
    firma.log(c, person, "zeit", typ, oid, f"Zeit gestartet: {_objekt_titel(c, typ, oid)}", commit=True)
    return cur.lastrowid


def stopp(c, person):
    from . import firma
    z = laufend(c, person)
    if not z:
        return None
    minuten = max(1, z["seit_min"])
    c.execute("UPDATE zeiten SET ende=?, minuten=? WHERE id=?", (now(), minuten, z["id"]))
    firma.log(c, person, "zeit", z["objekt_typ"], z["objekt_id"], f"Zeit erfasst: {minuten} Min. {z['titel']}",
              {"minuten": minuten, "projekt": z["projekt_id"]})
    return minuten


def nachtragen(c, person, typ, oid, minuten, tag=None, notiz=""):
    from . import firma
    pid = _projekt_von(c, typ, oid)
    if pid is None:
        raise ProjektError("Das gibt es nicht (mehr).")
    try:
        minuten = int(minuten)
    except (TypeError, ValueError) as exc:
        raise ProjektError("Minuten bitte als Zahl.") from exc
    if not 1 <= minuten <= 16 * 60:
        raise ProjektError("Zwischen 1 Minute und 16 Stunden.")
    try:
        tag = date.fromisoformat(tag) if tag else date.today()
    except ValueError as exc:
        raise ProjektError("Bitte ein Datum.") from exc
    s = datetime.combine(tag, datetime.min.time()).replace(hour=9)
    c.execute("INSERT INTO zeiten (person, start, ende, minuten, objekt_typ, objekt_id, projekt_id, notiz) "
              "VALUES (?,?,?,?,?,?,?,?)", (person, s.isoformat(timespec="seconds"),
                                            (s + timedelta(minutes=minuten)).isoformat(timespec="seconds"), minuten,
                                            typ, oid, pid or None, (notiz or "").strip()[:300] or None))
    firma.log(c, person, "zeit", typ, oid, f"Zeit nachgetragen: {minuten} Min. {_objekt_titel(c, typ, oid)}",
              {"minuten": minuten, "projekt": pid, "tag": tag.isoformat()})


def zeiten(c, pid, limit=50):
    return [dict(r, titel=_objekt_titel(c, r["objekt_typ"], r["objekt_id"])) for r in c.execute(
        "SELECT z.*, t.name AS person_name FROM zeiten z LEFT JOIN team t ON t.key = z.person "
        "WHERE projekt_id=? ORDER BY z.start DESC LIMIT ?", (pid, limit))]

# ---------------------------------------------------------------- Kosten je Kunde


def kosten(c, pid):
    """Kosten und Umsatz je Kunde: KI-Kosten (aus der Abrechnung je Lead), Zeit × interner Stundensatz, Anteil an den
    Serverkosten. Umsatz: Einmalbetrag plus Pauschale je angefangenem Monat seit Live."""
    import os
    p = get(c, pid)
    r = regeln(c)
    try:
        kurs = float(os.environ.get("EUR_PER_USD") or 0.86)
    except ValueError:
        kurs = 0.86
    ki = (c.execute("SELECT SUM(cost_usd) FROM ai_usage WHERE lead_id=?", (p["lead_id"],)).fetchone()[0] or 0) * kurs
    zeit_h = p["zeit_min"] / 60
    aktive = c.execute("SELECT COUNT(*) FROM projekte WHERE status IN ('live','betrieb')").fetchone()[0] or 1
    monate = 0
    if p["live_am"]:
        bis = date.fromisoformat((p["beendet_am"] or now())[:10])
        start_ = date.fromisoformat(p["live_am"][:10])
        monate = max(1, (bis.year - start_.year) * 12 + bis.month - start_.month + (1 if bis.day >= start_.day else 0))
    server = r["server_monat"] / aktive * monate if p["status"] in ("live", "betrieb") else 0
    umsatz = (p["preis_einmal"] or 0) + (p["pauschale_monat"] or 0) * monate
    pauschalen = r["pauschale_klein"] * (c.execute("SELECT COUNT(*) FROM aenderungen WHERE projekt_id=? AND "
                                                  "abrechnung='pauschale' AND status='erledigt'", (pid,)).fetchone()[0])
    kosten_ = ki + zeit_h * r["stundensatz"] + server
    return {"ki": round(ki, 2), "zeit_h": round(zeit_h, 1), "zeit_eur": round(zeit_h * r["stundensatz"], 2),
            "server": round(server, 2), "summe": round(kosten_, 2), "umsatz": round(umsatz + pauschalen, 2),
            "pauschalen": round(pauschalen, 2), "monate": monate, "marge": round(umsatz + pauschalen - kosten_, 2)}


def zeit_im_monat(c, pid, jahr, monat):
    von = date(jahr, monat, 1)
    bis = date(jahr + (monat == 12), monat % 12 + 1, 1)
    return c.execute("SELECT IFNULL(SUM(minuten),0) FROM zeiten WHERE projekt_id=? AND start >= ? AND start < ?",
                     (pid, von.isoformat(), bis.isoformat())).fetchone()[0]


def takt(c):
    """Einmal im Monat: Liegt die Zeit eines Kunden im Betrieb zwei Monate am Stück über der Annahme, bekommen
    verantwortlich und Technik eine Entscheidungsvorlage (Pauschale oder Regeln anpassen)."""
    from . import aufgaben, firma, leaddb
    heute = date.today()
    monat = f"{heute.year}-{heute.month:02d}"
    if leaddb.kv_get(c, "projekte_monat") == monat:
        return 0
    leaddb.kv_set(c, "projekte_monat", monat)
    annahme = regeln(c)["annahme_h_monat"] * 60
    vor1 = (heute.replace(day=1) - timedelta(days=1))
    vor2 = (vor1.replace(day=1) - timedelta(days=1))
    n = 0
    for p in liste(c, "betrieb"):
        m1, m2 = zeit_im_monat(c, p["id"], vor1.year, vor1.month), zeit_im_monat(c, p["id"], vor2.year, vor2.month)
        if annahme and m1 > annahme and m2 > annahme:
            text = (f"{p['kunde']}: {m2 / 60:.1f} h und {m1 / 60:.1f} h in den letzten Monaten, angenommen sind "
                    f"{annahme / 60:g} h. Pauschale anpassen oder Regeln ändern?")
            for person in {p["verantwortlich"], p["technik"]} - {None}:
                aufgaben.melden(c, person, f"Mehr Zeit als angenommen: {p['kunde']}", text, f"/kunden/{p['id']}")
            firma.log(c, "System", "projekt", "projekt", p["id"], "Zeit über der Annahme im Preismodell.",
                      {"minuten": [m2, m1], "annahme": annahme})
            n += 1
    return n

# ---------------------------------------------------------------- KPIs (für app/kpis.py)


def kpi_stunden_pro_build(c, von, bis, person=None):
    rows = c.execute("SELECT id FROM projekte WHERE live_am >= ? AND live_am < ?", (von, bis)).fetchall()
    if not rows:
        return None
    summe = sum(c.execute("SELECT IFNULL(SUM(minuten),0) FROM zeiten WHERE projekt_id=? AND objekt_typ != 'aenderung'"
                          + (" AND person=?" if person else ""), (r["id"], person) if person else (r["id"],)
                          ).fetchone()[0] for r in rows)
    return round(summe / 60 / len(rows), 1)


def kpi_abnahmen_erstes_mal(c, von, bis, person=None):
    rows = c.execute("SELECT id FROM projekte WHERE live_am >= ? AND live_am < ?"
                     + (" AND technik=?" if person else ""), (von, bis, person) if person else (von, bis)).fetchall()
    if not rows:
        return None
    erst = sum(1 for r in rows if (c.execute("SELECT bestanden FROM abnahmen WHERE projekt_id=? ORDER BY id LIMIT 1",
                                             (r["id"],)).fetchone() or {"bestanden": 0})["bestanden"])
    return round(100 * erst / len(rows))


def kpi_tage_bis_live(c, von, bis, person=None):
    rows = c.execute("SELECT beauftragt_am, live_am FROM projekte WHERE live_am >= ? AND live_am < ?"
                     + (" AND verantwortlich=?" if person else ""), (von, bis, person) if person else (von, bis)
                     ).fetchall()
    if not rows:
        return None
    tage = [(date.fromisoformat(r["live_am"][:10]) - date.fromisoformat(r["beauftragt_am"][:10])).days for r in rows]
    return round(sum(tage) / len(tage), 1)


def kpi_kunden_bleiben(c, von, bis, person=None):
    """Anteil der Kunden, die vor mindestens drei Monaten live gingen und noch aktiv sind (Stand Ende der Woche)."""
    grenze = (datetime.fromisoformat(bis) - timedelta(days=90)).isoformat()
    rows = c.execute("SELECT status, beendet_am FROM projekte WHERE live_am IS NOT NULL AND live_am < ?",
                     (grenze,)).fetchall()
    if not rows:
        return None
    aktiv = sum(1 for r in rows if not r["beendet_am"] or r["beendet_am"] >= bis)
    return round(100 * aktiv / len(rows))
