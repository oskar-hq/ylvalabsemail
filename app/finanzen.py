"""Finanzen (Firmen-OS, Stufe 8, docs/FIRMEN-OS.md Abschnitt 4.10).

- **Rechnungen** aus den Projekten: Einmalbetrag, monatliche Pauschale, kleine Änderungen (Abrechnung „Pauschale“).
  Ein Entwurf hat noch keine Nummer. Die Nummer (fortlaufend, Format einstellbar) bekommt die Rechnung erst, wenn sie
  freigegeben ist (Freigabe-Stufe „rechnung“, Standard C). Dann entsteht das PDF, es liegt im Kunden-Bereich und geht
  per Mail an den Kunden. Eine ausgestellte Rechnung ändert man nicht, man storniert sie (Stornorechnung mit eigener
  Nummer). Monatsrechnungen bereitet der Takt am Monatsersten vor.
- **Laufende Kosten** als Liste mit Monatsbetrag; KI-Kosten kommen automatisch aus `ai_usage`.
- **Kassenstand** von Hand mit Datum, Verlauf.
- **Kennzahlen:** wiederkehrender Umsatz, Fixkosten, Gewinnschwelle, offene Forderungen und die Geld-Regel für
  Einstellungen aus docs/ROLLEN-UND-ABLAEUFE.md als Ampel.
- **Export** für den Steuerberater: ZIP mit CSV (Rechnungen, Kosten, Kasse) und den Rechnungs-PDFs eines Zeitraums.
Umsatzsteuer: Kleinunternehmer (§ 19 UStG) oder Regelbesteuerung, in Einstellungen → Finanzen. E-Rechnung
(XRechnung/ZUGFeRD) kommt vor 2028 als eigene Stufe; bis Ende 2027 reicht für kleine Firmen das PDF.
"""
import csv
import io
import json
import re
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS rechnungen (
    id INTEGER PRIMARY KEY AUTOINCREMENT, nummer TEXT UNIQUE, projekt_id INTEGER, lead_id INTEGER,
    art TEXT NOT NULL DEFAULT 'sonstig', zeitraum TEXT, status TEXT NOT NULL DEFAULT 'entwurf',
    datum TEXT, faellig TEXT, leistung TEXT, empfaenger TEXT NOT NULL DEFAULT '', email TEXT,
    netto REAL NOT NULL DEFAULT 0, ust_satz REAL NOT NULL DEFAULT 0, ust REAL NOT NULL DEFAULT 0,
    brutto REAL NOT NULL DEFAULT 0, hinweis TEXT, pdf TEXT, storno_von INTEGER, grund TEXT,
    erstellt_von TEXT, erstellt_am TEXT NOT NULL, ausgestellt_am TEXT, verschickt_am TEXT, bezahlt_am TEXT
);
CREATE INDEX IF NOT EXISTS rechnungen_projekt ON rechnungen(projekt_id, art, zeitraum);
CREATE TABLE IF NOT EXISTS rechnung_positionen (
    id INTEGER PRIMARY KEY AUTOINCREMENT, rechnung_id INTEGER NOT NULL, text TEXT NOT NULL,
    menge REAL NOT NULL DEFAULT 1, einzel REAL NOT NULL DEFAULT 0, quelle_typ TEXT, quelle_id INTEGER,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kosten (
    id INTEGER PRIMARY KEY AUTOINCREMENT, titel TEXT NOT NULL, kategorie TEXT NOT NULL DEFAULT 'sonstiges',
    betrag_monat REAL NOT NULL, seit TEXT NOT NULL, bis TEXT, notiz TEXT, erstellt_von TEXT, erstellt_am TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kasse (
    id INTEGER PRIMARY KEY AUTOINCREMENT, datum TEXT NOT NULL, stand REAL NOT NULL, notiz TEXT, wer TEXT,
    erstellt_am TEXT NOT NULL
);
"""

ARTEN = {"einmal": "Einmalbetrag", "monat": "Monatsrechnung", "sonstig": "Sonstige Rechnung", "storno": "Storno"}
STATUS = {"entwurf": "Entwurf", "wartet": "Wartet auf Freigabe", "offen": "Offen", "bezahlt": "Bezahlt",
          "storniert": "Storniert"}
KATEGORIEN = [("server", "Server und Hosting"), ("lizenz", "Lizenzen und Software"), ("ki", "KI"),
              ("buero", "Büro und Verwaltung"), ("beratung", "Steuerberater und Recht"), ("sonstiges", "Sonstiges")]
KATEGORIE_LABEL = dict(KATEGORIEN)

REGELN = {
    "firma_name": ("Ylva Labs", "Name auf der Rechnung"),
    "firma_anschrift": ("", "Anschrift (eine Zeile je Zeile)"),
    "firma_email": ("", "Mail für Rückfragen"),
    "steuernummer": ("", "Steuernummer"),
    "ust_id": ("", "USt-IdNr. (falls vorhanden)"),
    "bank": ("", "Bank"),
    "iban": ("", "IBAN"),
    "bic": ("", "BIC"),
    "steuer": ("klein", "Umsatzsteuer"),
    "ust_satz": (19, "Steuersatz in % (Regelbesteuerung)"),
    "zahlungsziel": (14, "Zahlungsziel (Tage)"),
    "nummer_format": ("RE-{jahr}-{n:04}", "Rechnungsnummer ({jahr}, {n} fortlaufend je Jahr)"),
    "versand": (True, "Rechnung nach der Freigabe per Mail an den Kunden schicken"),
    "betreff": ("Rechnung {nummer} von {firma}", "Betreff der Rechnungs-Mail ({nummer}, {firma})"),
    "text": ("Guten Tag {name},\n\nanbei die Rechnung {nummer} über {betrag}, zahlbar bis {faellig}.\n\n"
             "Sie finden sie auch in Ihrem Kunden-Bereich.\n\nVielen Dank!", "Text der Mail ({name}, {nummer}, {betrag}, {faellig})"),
    "monat_tag": (1, "Monatsrechnungen vorbereiten am (Tag des Monats)"),
    "personalkosten_neu": (0, "Geld-Regel: Personalkosten der nächsten Stelle (€/Monat)"),
}
STEUER = {"klein": "Kleinunternehmer (§ 19 UStG, keine Umsatzsteuer)", "regel": "Regelbesteuerung (mit Umsatzsteuer)"}
HINWEIS_KLEIN = "Gemäß § 19 UStG wird keine Umsatzsteuer berechnet."


class FinanzError(ValueError):
    pass


def now():
    return datetime.now().isoformat(timespec="seconds")


def _json(raw, default):
    try:
        v = json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default
    return v if isinstance(v, type(default)) else default


def euro(v):
    return f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _zahl(v, feld="Betrag", leer=None, negativ=False):
    s = str(v if v is not None else "").strip().replace(" ", "").replace("€", "")
    if not s:
        if leer is not None:
            return leer
        raise FinanzError(f"{feld}: bitte eine Zahl.")
    if "," in s or re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):  # deutsch: 1.200,50 oder 12.000
        s = s.replace(".", "").replace(",", ".")
    try:
        z = round(float(s), 2)
    except ValueError as exc:
        raise FinanzError(f"{feld}: bitte eine Zahl.") from exc
    if z < 0 and not negativ:
        raise FinanzError(f"{feld}: bitte eine positive Zahl.")
    return z


def _datum(v, feld="Datum"):
    try:
        return date.fromisoformat(str(v)[:10]).isoformat()
    except ValueError as exc:
        raise FinanzError(f"{feld}: bitte ein Datum.") from exc

# ---------------------------------------------------------------- Regeln (Einstellungen → Finanzen)


def regeln(c):
    row = c.execute("SELECT value FROM kv WHERE key='regel:finanzen'").fetchone()
    stored = _json(row["value"], {}) if row else {}
    out = {}
    for k, (standard, _) in REGELN.items():
        v = stored.get(k, standard)
        out[k] = v if isinstance(v, type(standard)) else standard
    if out["steuer"] not in STEUER:
        out["steuer"] = "klein"
    return out


def regeln_speichern(c, wer, form):
    from . import firma
    alt = regeln(c)
    neu = {}
    for k, (standard, label) in REGELN.items():
        if isinstance(standard, bool):
            neu[k] = form.get(k) in ("1", "on", "true", True)
        elif isinstance(standard, int):
            raw = str(form.get(k, alt[k])).strip()
            grenzen = {"ust_satz": (0, 30), "zahlungsziel": (0, 90), "monat_tag": (1, 28),
                       "personalkosten_neu": (0, 100000)}[k]
            if not raw.isdigit() or not grenzen[0] <= int(raw) <= grenzen[1]:
                raise FinanzError(f"{label}: {grenzen[0]} bis {grenzen[1]}.")
            neu[k] = int(raw)
        else:
            neu[k] = str(form.get(k, alt[k])).replace("\r\n", "\n").strip()[:2000]
    if neu["steuer"] not in STEUER:
        raise FinanzError("Bitte wählen, wie die Umsatzsteuer abgerechnet wird.")
    if "{n" not in neu["nummer_format"]:
        raise FinanzError("Die Rechnungsnummer braucht den Platzhalter {n} (fortlaufende Nummer).")
    try:
        _nummer_text(neu["nummer_format"], 2026, 1)
    except (KeyError, ValueError, IndexError) as exc:
        raise FinanzError("Rechnungsnummer: nur {jahr} und {n} (z. B. {n:04} für vier Stellen).") from exc
    if neu["iban"]:
        neu["iban"] = re.sub(r"\s+", "", neu["iban"]).upper()
    geaendert = [REGELN[k][1] for k in neu if neu[k] != alt[k]]
    c.execute("INSERT INTO kv (key, value) VALUES ('regel:finanzen', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (json.dumps(neu),))
    firma.log(c, wer, "einstellung", "regel", "finanzen", "Finanzen: " + (", ".join(geaendert) or "nichts")
              + " geändert.", {k: neu[k] for k in neu if neu[k] != alt[k] and k not in ("iban", "bic")} or None)
    return geaendert


def fehlende_angaben(r):
    """Pflichtangaben auf der Rechnung (§ 14 UStG), die in den Einstellungen noch fehlen."""
    out = []
    if not r["firma_name"]:
        out.append("Name")
    if not r["firma_anschrift"]:
        out.append("Anschrift")
    if not (r["steuernummer"] or r["ust_id"]):
        out.append("Steuernummer oder USt-IdNr.")
    return out


def _fuellen(text, **werte):
    return re.sub(r"\{(\w+)\}", lambda m: str(werte.get(m.group(1), m.group(0))), text)


def _nummer_text(fmt, jahr, n):
    return fmt.format(jahr=jahr, n=n)

# ---------------------------------------------------------------- Rechnungen


def get(c, rid):
    row = c.execute("SELECT r.*, l.name AS kunde, p.titel AS projekt_titel, p.verantwortlich FROM rechnungen r "
                    "LEFT JOIN leads l ON l.id = r.lead_id LEFT JOIN projekte p ON p.id = r.projekt_id WHERE r.id=?",
                    (rid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["positionen"] = [dict(p, betrag=round(p["menge"] * p["einzel"], 2)) for p in c.execute(
        "SELECT * FROM rechnung_positionen WHERE rechnung_id=? ORDER BY sort, id", (rid,))]
    d["art_label"] = ARTEN.get(d["art"], d["art"])
    d["status_label"] = STATUS.get(d["status"], d["status"])
    d["ueberfaellig"] = d["status"] == "offen" and bool(d["faellig"]) and d["faellig"] < date.today().isoformat()
    return d


def liste(c, status=None, projekt_id=None, limit=300):
    where, args = ["1"], []
    if status == "ueberfaellig":
        where.append("r.status='offen' AND r.faellig < ?")
        args.append(date.today().isoformat())
    elif status:
        where.append("r.status=?")
        args.append(status)
    if projekt_id:
        where.append("r.projekt_id=?")
        args.append(projekt_id)
    rows = c.execute(f"SELECT r.id FROM rechnungen r WHERE {' AND '.join(where)} "
                     "ORDER BY r.status='entwurf' DESC, r.status='wartet' DESC, COALESCE(r.datum, r.erstellt_am) DESC, r.id DESC "
                     "LIMIT ?", [*args, limit]).fetchall()
    return [get(c, r["id"]) for r in rows]


def _summen(c, rid, wie=None):
    """Summen aus den Positionen. Steuer nach den Einstellungen, bei einem Storno wie in der stornierten Rechnung
    (`wie`)."""
    r = regeln(c)
    netto = round(sum(p["menge"] * p["einzel"] for p in c.execute(
        "SELECT menge, einzel FROM rechnung_positionen WHERE rechnung_id=?", (rid,))), 2)
    if wie:
        satz, hinweis = wie["ust_satz"], wie["hinweis"]
    else:
        satz = 0 if r["steuer"] == "klein" else r["ust_satz"]
        hinweis = HINWEIS_KLEIN if r["steuer"] == "klein" else None
    ust = round(netto * satz / 100, 2)
    c.execute("UPDATE rechnungen SET netto=?, ust_satz=?, ust=?, brutto=?, hinweis=? WHERE id=?",
              (netto, satz, ust, round(netto + ust, 2), hinweis, rid))


def _empfaenger(c, lead_id):
    l = c.execute("SELECT name, contact_name, address, city, email FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not l:
        return "", None
    zeilen = [l["name"]]
    if l["contact_name"]:
        zeilen.append(l["contact_name"])
    if l["address"]:
        zeilen += [x.strip() for x in l["address"].split(",") if x.strip()]
    elif l["city"]:
        zeilen.append(l["city"])
    return "\n".join(dict.fromkeys(zeilen)), l["email"]


def darf(c, wer, modul_stufe=None):
    from . import firma
    return firma.darf(c, wer, "finanzen", modul_stufe or firma.BEARBEITEN)


def anlegen(c, wer, projekt_id=None, art="sonstig", positionen=(), zeitraum=None, leistung="", lead_id=None):
    """Entwurf anlegen (ohne Nummer). `positionen`: [(text, menge, einzel, quelle_typ, quelle_id), …]."""
    from . import firma, projekte
    if art not in ("einmal", "monat", "sonstig"):
        raise FinanzError("Unbekannte Art der Rechnung.")
    p = projekte.get(c, projekt_id) if projekt_id else None
    if projekt_id and not p:
        raise FinanzError("Dieses Projekt gibt es nicht.")
    lead_id = p["lead_id"] if p else lead_id
    if not lead_id:
        raise FinanzError("Eine Rechnung braucht einen Kunden.")
    empfaenger, email = _empfaenger(c, lead_id)
    rid = c.execute("INSERT INTO rechnungen (projekt_id, lead_id, art, zeitraum, leistung, empfaenger, email, erstellt_von, "
                    "erstellt_am) VALUES (?,?,?,?,?,?,?,?,?)",
                    (projekt_id, lead_id, art, zeitraum, leistung or None, empfaenger, email, wer, now())).lastrowid
    for i, (text, menge, einzel, qt, qid) in enumerate(positionen):
        c.execute("INSERT INTO rechnung_positionen (rechnung_id, text, menge, einzel, quelle_typ, quelle_id, sort) "
                  "VALUES (?,?,?,?,?,?,?)", (rid, text, menge, einzel, qt, qid, i))
    _summen(c, rid)
    firma.log(c, wer, "rechnung", "rechnung", rid, f"Rechnung angelegt (Entwurf): {ARTEN[art]}"
              + (f", {p['kunde']}" if p else ""), {"projekt": projekt_id}, commit=False)
    if p:
        firma.log(c, wer, "projekt", "projekt", p["id"], f"Rechnungsentwurf angelegt: {ARTEN[art]}.", {"rechnung": rid},
                  commit=False)
    c.commit()
    return rid


def einmal_anlegen(c, wer, pid):
    from . import projekte
    p = projekte.get(c, pid)
    if not p:
        raise FinanzError("Dieses Projekt gibt es nicht.")
    if not p["preis_einmal"]:
        raise FinanzError("Am Projekt fehlt der Einmalbetrag.")
    if c.execute("SELECT 1 FROM rechnungen WHERE projekt_id=? AND art='einmal' AND status != 'storniert'", (pid,)).fetchone():
        raise FinanzError("Für den Einmalbetrag gibt es schon eine Rechnung.")
    return anlegen(c, wer, pid, "einmal", [(f"{p['titel']}: Einrichtung (Einmalbetrag)", 1, p["preis_einmal"], None, None)],
                   leistung=date.today().strftime("%m/%Y"))


def _monat_positionen(c, p, monat):
    """Pauschale für `monat` (JJJJ-MM) und kleine Änderungen mit Abrechnung „Pauschale“, die im Vormonat erledigt und
    noch nicht abgerechnet wurden."""
    from . import projekte
    j, m = (int(x) for x in monat.split("-"))
    pos = []
    if p["pauschale_monat"]:
        pos.append((f"Betrieb und Betreuung {m:02d}/{j} (monatliche Pauschale)", 1, p["pauschale_monat"], "pauschale", None))
    vor = date(j, m, 1) - timedelta(days=1)
    von, bis = vor.replace(day=1).isoformat(), date(j, m, 1).isoformat()
    preis = projekte.regeln(c)["pauschale_klein"]
    for a in c.execute("SELECT a.id, a.text FROM aenderungen a WHERE a.projekt_id=? AND a.status='erledigt' "
                       "AND a.abrechnung='pauschale' AND a.erledigt_am >= ? AND a.erledigt_am < ? AND NOT EXISTS "
                       "(SELECT 1 FROM rechnung_positionen rp JOIN rechnungen r ON r.id = rp.rechnung_id WHERE "
                       "rp.quelle_typ='aenderung' AND rp.quelle_id=a.id AND r.status != 'storniert')",
                       (p["id"], von, bis)).fetchall():
        pos.append((f"Kleine Änderung: {a['text'].splitlines()[0][:90]}", 1, preis, "aenderung", a["id"]))
    return pos


def monat_anlegen(c, wer, pid, monat=None):
    """Monatsrechnung (Pauschale + kleine Änderungen). Gibt die ID zurück oder None (schon da, nichts abzurechnen)."""
    from . import projekte
    monat = monat or date.today().strftime("%Y-%m")
    p = projekte.get(c, pid)
    if not p:
        raise FinanzError("Dieses Projekt gibt es nicht.")
    if c.execute("SELECT 1 FROM rechnungen WHERE projekt_id=? AND art='monat' AND zeitraum=? AND status != 'storniert'",
                 (pid, monat)).fetchone():
        return None
    pos = _monat_positionen(c, p, monat)
    if not pos:
        return None
    j, m = monat.split("-")
    return anlegen(c, wer, pid, "monat", pos, zeitraum=monat, leistung=f"{m}/{j}")


def bearbeiten(c, wer, rid, empfaenger=None, email=None, leistung=None, positionen=None):
    """Nur Entwürfe. `positionen`: [(text, menge, einzel), …] ersetzt alle (Herkunft bleibt, wo der Text gleich ist)."""
    from . import firma
    r = get(c, rid)
    if not r:
        raise FinanzError("Diese Rechnung gibt es nicht.")
    if r["status"] != "entwurf":
        raise FinanzError("Nur Entwürfe lassen sich ändern. Ausgestellte Rechnungen storniert man.")
    if empfaenger is not None:
        if not empfaenger.strip():
            raise FinanzError("Bitte den Empfänger (Name und Anschrift).")
        c.execute("UPDATE rechnungen SET empfaenger=? WHERE id=?", (empfaenger.strip()[:600], rid))
    if email is not None:
        email = email.strip().lower()
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", email):
            raise FinanzError("Bitte eine gültige Mailadresse (oder leer lassen).")
        c.execute("UPDATE rechnungen SET email=? WHERE id=?", (email or None, rid))
    if leistung is not None:
        c.execute("UPDATE rechnungen SET leistung=? WHERE id=?", (leistung.strip()[:60] or None, rid))
    if positionen is not None:
        alt = {p["text"]: (p["quelle_typ"], p["quelle_id"]) for p in r["positionen"]}
        neu = []
        for text, menge, einzel in positionen:
            text = re.sub(r"\s+", " ", text or "").strip()
            if not text and not str(einzel or "").strip():
                continue
            if not text:
                raise FinanzError("Jede Position braucht einen Text.")
            neu.append((text[:200], _zahl(menge, "Menge", 1), _zahl(einzel, "Einzelpreis", negativ=True)))
        if not neu:
            raise FinanzError("Eine Rechnung braucht mindestens eine Position.")
        c.execute("DELETE FROM rechnung_positionen WHERE rechnung_id=?", (rid,))
        for i, (text, menge, einzel) in enumerate(neu):
            qt, qid = alt.get(text, (None, None))
            c.execute("INSERT INTO rechnung_positionen (rechnung_id, text, menge, einzel, quelle_typ, quelle_id, sort) "
                      "VALUES (?,?,?,?,?,?,?)", (rid, text, menge, einzel, qt, qid, i))
    _summen(c, rid)
    firma.log(c, wer, "rechnung", "rechnung", rid, "Rechnungsentwurf bearbeitet.")


def loeschen(c, wer, rid):
    from . import firma
    r = get(c, rid)
    if not r or r["status"] != "entwurf":
        raise FinanzError("Löschen geht nur bei Entwürfen.")
    c.execute("DELETE FROM rechnung_positionen WHERE rechnung_id=?", (rid,))
    c.execute("DELETE FROM rechnungen WHERE id=?", (rid,))
    firma.log(c, wer, "rechnung", "rechnung", rid, f"Rechnungsentwurf gelöscht ({r['kunde']}).")


def pruefer(c, r):
    """Wer gibt frei: die verantwortliche Person des Projekts, sonst jemand, der Finanzen verwaltet."""
    from . import firma
    if r.get("verantwortlich") and darf(c, r["verantwortlich"], firma.FREIGEBEN):
        return r["verantwortlich"]
    for p in firma.aktive_personen(c):
        if darf(c, p["key"], firma.VERWALTEN):
            return p["key"]
    return None


def zur_freigabe(c, wer, rid):
    """Entwurf fertig: in die Freigabe-Zentrale (Stufe C) oder mit Einspruchsfrist (Stufe B)."""
    from . import aufgaben, firma, freigaben
    r = get(c, rid)
    if not r or r["status"] != "entwurf":
        raise FinanzError("Diese Rechnung ist kein Entwurf (mehr).")
    if not r["positionen"] or r["brutto"] <= 0:
        raise FinanzError("Die Rechnung braucht Positionen und einen Betrag über 0 €.")
    fehlt = fehlende_angaben(regeln(c))
    if fehlt:
        raise FinanzError("In Einstellungen → Finanzen fehlen noch: " + ", ".join(fehlt) + ".")
    wer_prueft = pruefer(c, r)
    if not wer_prueft:
        raise FinanzError("Niemand darf Rechnungen freigeben (Recht „Finanzen: Freigeben“).")
    c.execute("UPDATE rechnungen SET status='wartet', grund=NULL WHERE id=?", (rid,))
    stufe = freigaben.stufe(c, "rechnung")
    titel = f"{r['kunde']}, {euro(r['brutto'])}"
    if stufe == "B":
        c.commit()
        _, frist = freigaben.b_anlegen(c, wer, "rechnung", "rechnung", rid, wer_prueft)
        aufgaben.melden(c, wer_prueft, f"Rechnung geht raus: {titel}",
                        f"automatisch am {frist[8:10]}.{frist[5:7]}. um {frist[11:16]}, wenn niemand stoppt", "/freigaben")
    else:
        c.execute("INSERT INTO freigaben (art, objekt_typ, objekt_id, stufe, angefragt_von, angefragt_am, pruefer) "
                  "VALUES ('rechnung', 'rechnung', ?, 'C', ?, ?, ?)", (rid, wer, now(), wer_prueft))
        c.commit()
        if wer_prueft != wer:
            aufgaben.melden(c, wer_prueft, f"Rechnung freigeben: {titel}", r["art_label"], "/freigaben", push=True)
    firma.log(c, wer, "rechnung", "rechnung", rid, f"Rechnung zur Freigabe (Stufe {stufe}): {titel}")


def _naechste_nummer(c):
    r = regeln(c)
    jahr = date.today().year
    key = f"rechnung_nr:{jahr}"
    row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    n = int(row["value"]) + 1 if row else 1
    while True:
        nummer = _nummer_text(r["nummer_format"], jahr, n)
        if not c.execute("SELECT 1 FROM rechnungen WHERE nummer=?", (nummer,)).fetchone():
            break
        n += 1
    c.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (key, json.dumps(n)))
    return nummer


def entscheiden(c, wer, rid, ok, grund="", data_dir=None):
    """Aus der Freigabe-Zentrale (oder vom Takt nach der Frist): ausstellen (Nummer, PDF, Kunden-Bereich, Mail)
    oder zurück in den Entwurf (mit Grund)."""
    from . import aufgaben, firma
    r = get(c, rid)
    f = c.execute("SELECT * FROM freigaben WHERE art='rechnung' AND objekt_id=? AND status='offen' ORDER BY id DESC "
                  "LIMIT 1", (str(rid),)).fetchone()
    if not r or not f or r["status"] != "wartet":
        raise FinanzError("Diese Rechnung wartet nicht auf Freigabe.")
    system = wer == "System" and f["stufe"] == "B"
    if not system and wer != f["pruefer"] and not darf(c, wer, firma.VERWALTEN):
        raise FinanzError("Freigeben darf die eingetragene Person (oder wer Finanzen verwaltet).")
    if not system and not darf(c, wer, firma.FREIGEBEN):
        raise FinanzError("Dafür braucht es „Finanzen: Freigeben“.")
    grund = (grund or "").strip()
    if not ok and len(grund) < 3:
        raise FinanzError("Bitte einen Grund angeben.")
    c.execute("UPDATE freigaben SET status=?, grund=?, entschieden_von=?, entschieden_am=? WHERE id=?",
              ("angenommen" if ok else "abgelehnt", grund or None, wer, now(), f["id"]))
    if not ok:
        c.execute("UPDATE rechnungen SET status='entwurf', grund=? WHERE id=?", (grund, rid))
        firma.log(c, wer, "rechnung", "rechnung", rid, f"Rechnung zurück in den Entwurf: {grund}", commit=False)
        c.commit()
        if f["angefragt_von"] and f["angefragt_von"] not in (wer, "System"):
            aufgaben.melden(c, f["angefragt_von"], f"Rechnung zurück: {r['kunde']}", grund, f"/finanzen/rechnung/{rid}")
        return get(c, rid)
    data_dir = _data_dir(data_dir)
    _ausstellen(c, wer, rid, data_dir)
    r = get(c, rid)
    if regeln(c)["versand"] and r["email"]:
        try:
            versenden(c, wer if not system else (r["verantwortlich"] or "System"), rid, data_dir)
        except FinanzError as exc:  # ausgestellt ist sie trotzdem; Versand von Hand nachholen
            firma.log(c, wer, "rechnung", "rechnung", rid, f"Rechnung ausgestellt, aber nicht verschickt: {exc}")
            if r["verantwortlich"]:
                aufgaben.melden(c, r["verantwortlich"], f"Rechnung {r['nummer']} nicht verschickt", str(exc),
                                f"/finanzen/rechnung/{rid}")
    return get(c, rid)


def _ausstellen(c, wer, rid, data_dir):
    from . import firma
    r = get(c, rid)
    heute = date.today()
    nummer = _naechste_nummer(c)
    faellig = (heute + timedelta(days=regeln(c)["zahlungsziel"])).isoformat()
    c.execute("UPDATE rechnungen SET nummer=?, status='offen', datum=?, faellig=?, ausgestellt_am=? WHERE id=?",
              (nummer, heute.isoformat(), faellig, now(), rid))
    _summen(c, rid, get(c, r["storno_von"]) if r["storno_von"] else None)
    if data_dir:
        pdf_speichern(c, rid, data_dir)
    firma.log(c, wer, "rechnung", "rechnung", rid, f"Rechnung {nummer} ausgestellt: {r['kunde']}, {euro(r['brutto'])}",
              {"nummer": nummer}, commit=False)
    if r["projekt_id"]:
        firma.log(c, wer, "projekt", "projekt", r["projekt_id"], f"Rechnung {nummer} ausgestellt ({euro(r['brutto'])}).",
                  commit=False)
    c.commit()


def _data_dir(data_dir=None):
    if data_dir:
        return data_dir
    from . import agent
    return agent.CFG.get("data_dir")


def ordner(data_dir):
    d = Path(_data_dir(data_dir)) / "rechnungen"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pdf_bytes(c, rid):
    from .pdf import BREITE, Dokument, umbrechen
    r = get(c, rid)
    e = regeln(c)
    doc = Dokument()
    L, R = 56, BREITE - 56
    doc.text(L, 60, e["firma_name"], 16, fett=True)
    y = 78
    for z in [x for x in e["firma_anschrift"].split("\n") if x.strip()] + ([e["firma_email"]] if e["firma_email"] else []):
        doc.text(L, y, z.strip(), 9, grau=True)
        y += 12
    doc.text(L, 150, f"{e['firma_name']} · " + " · ".join(x.strip() for x in e["firma_anschrift"].split("\n") if x.strip()),
             7, grau=True)
    y = 166
    for z in r["empfaenger"].split("\n"):
        doc.text(L, y, z, 10)
        y += 13
    titel = "Stornorechnung" if r["art"] == "storno" else "Rechnung"
    doc.text(L, 270, titel, 18, fett=True)
    meta = [("Rechnungsnummer", r["nummer"] or "ENTWURF"), ("Rechnungsdatum", _de(r["datum"]) or "–"),
            ("Leistungszeitraum", r["leistung"] or _de(r["datum"]) or "–")]
    if r["storno_von"]:
        orig = get(c, r["storno_von"])
        meta.append(("Storno zu", orig["nummer"] if orig else "–"))
    y = 300
    for k, v in meta:
        doc.text(L, y, k, 9, grau=True)
        doc.text(L + 120, y, v, 9)
        y += 14
    y += 18
    doc.text(L, y, "Leistung", 9, fett=True)
    doc.text(R - 170, y, "Menge", 9, fett=True, rechts=True)
    doc.text(R - 90, y, "Einzelpreis", 9, fett=True, rechts=True)
    doc.text(R, y, "Betrag", 9, fett=True, rechts=True)
    y += 8
    doc.linie(L, y, R)
    y += 16
    for p in r["positionen"]:
        zeilen = umbrechen(p["text"], 10, R - 200 - L)
        if y + 14 * len(zeilen) > 740:
            doc.neue_seite()
            y = 60
        for i, z in enumerate(zeilen):
            doc.text(L, y + i * 13, z, 10)
        doc.text(R - 170, y, f"{p['menge']:g}".replace(".", ","), 10, rechts=True)
        doc.text(R - 90, y, euro(p["einzel"]), 10, rechts=True)
        doc.text(R, y, euro(p["betrag"]), 10, rechts=True)
        y += 13 * len(zeilen) + 8
    doc.linie(L, y, R)
    y += 18
    summen = [("Summe netto", euro(r["netto"]))] if r["ust_satz"] else []
    if r["ust_satz"]:
        summen.append((f"Umsatzsteuer {r['ust_satz']:g} %", euro(r["ust"])))
    summen.append(("Gesamtbetrag", euro(r["brutto"])))
    for i, (k, v) in enumerate(summen):
        fett = i == len(summen) - 1
        doc.text(R - 120, y, k, 10, fett=fett, rechts=True)
        doc.text(R, y, v, 10, fett=fett, rechts=True)
        y += 15
    y += 14
    if r["hinweis"]:
        doc.text(L, y, r["hinweis"], 9)
        y += 14
    if r["art"] != "storno" and r["faellig"]:
        doc.text(L, y, f"Bitte überweisen Sie den Betrag bis zum {_de(r['faellig'])} unter Angabe der Rechnungsnummer.", 9)
        y += 14
    fuss = [e["firma_name"]]
    if e["steuernummer"]:
        fuss.append(f"Steuernummer {e['steuernummer']}")
    if e["ust_id"]:
        fuss.append(f"USt-IdNr. {e['ust_id']}")
    bank = [x for x in (e["bank"], "IBAN " + " ".join(e["iban"][i:i + 4] for i in range(0, len(e["iban"]), 4)) if e["iban"] else "", f"BIC {e['bic']}" if e["bic"] else "") if x]
    doc.linie(L, 780, R)
    doc.text(L, 794, " · ".join(fuss), 7, grau=True)
    if bank:
        doc.text(L, 805, " · ".join(bank), 7, grau=True)
    return doc.bytes()


def _de(v):
    return f"{v[8:10]}.{v[5:7]}.{v[:4]}" if v else ""


def pdf_speichern(c, rid, data_dir):
    r = get(c, rid)
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", r["nummer"] or f"entwurf-{rid}") + ".pdf"
    (ordner(data_dir) / name).write_bytes(pdf_bytes(c, rid))
    c.execute("UPDATE rechnungen SET pdf=? WHERE id=?", (name, rid))
    if r["projekt_id"] and r["nummer"]:  # im Kunden-Bereich zeigen
        from . import portal
        ziel = portal.dok_ordner(data_dir) / name
        ziel.write_bytes((ordner(data_dir) / name).read_bytes())
        c.execute("INSERT INTO dokumente (projekt_id, titel, art, datei, dateiname, groesse, sichtbar, hochgeladen_von, "
                  "hochgeladen_am) VALUES (?,?, 'rechnung', ?, ?, ?, 1, 'System', ?)",
                  (r["projekt_id"], f"{'Stornorechnung' if r['art'] == 'storno' else 'Rechnung'} {r['nummer']}", name,
                   name, ziel.stat().st_size, now()))
    c.commit()
    return name


def versenden(c, wer, rid, data_dir):
    """Rechnung als PDF-Anhang an den Kunden (über das Akquise-Postfach, in der Akte)."""
    from email.message import EmailMessage
    from email.utils import formataddr, formatdate, make_msgid
    from . import agent, firma, leaddb, team
    r = get(c, rid)
    if not r or r["status"] not in ("offen", "bezahlt") or not r["nummer"]:
        raise FinanzError("Verschickt wird nur eine ausgestellte Rechnung.")
    if not r["email"]:
        raise FinanzError("An der Rechnung fehlt die Mailadresse.")
    lead = leaddb.get_lead(c, r["lead_id"])
    domain = r["email"].rsplit("@", 1)[-1]
    if not lead or lead["status"] == "gesperrt" or domain in set(leaddb.kv_get(c, "blocklist", [])):
        raise FinanzError("Dieser Kunde möchte keine Nachrichten mehr bekommen.")
    if not agent.outreach_configured():
        raise FinanzError("Das Akquise-Postfach ist nicht eingerichtet (Einstellungen → Postfach und Versand).")
    if not r["pdf"] or not (ordner(data_dir) / r["pdf"]).exists():
        pdf_speichern(c, rid, data_dir)
        r = get(c, rid)
    e = regeln(c)
    snd = agent._sender(c, r["verantwortlich"] if wer == "System" else wer)
    werte = {"name": lead.get("contact_name") or "", "nummer": r["nummer"], "betrag": euro(r["brutto"]),
             "faellig": _de(r["faellig"]), "firma": e["firma_name"]}
    msg = EmailMessage()
    msg["From"] = formataddr((snd["display"], agent.outreach_address()))
    msg["To"] = formataddr((lead.get("contact_name") or "", r["email"]))
    msg["Subject"] = _fuellen(e["betreff"], **werte)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=agent._domain(agent.outreach_address()) or None)
    text = _fuellen(e["text"], **werte).replace("Guten Tag ,", "Guten Tag,")
    msg.set_content(f"{text.strip()}\n\n{team.signature(snd)}")
    msg.add_attachment((ordner(data_dir) / r["pdf"]).read_bytes(), maintype="application", subtype="pdf",
                       filename=r["pdf"])
    try:
        agent._deliver(msg)
    except RuntimeError as exc:
        raise FinanzError(str(exc)) from exc
    agent.record_reply(c, lead, text, snd["name"] or wer, str(msg["Subject"]), r["email"], msg["Message-ID"],
                       what=f"Rechnung {r['nummer']} verschickt")
    c.execute("UPDATE rechnungen SET verschickt_am=? WHERE id=?", (now(), rid))
    firma.log(c, wer, "rechnung", "rechnung", rid, f"Rechnung {r['nummer']} an {r['email']} verschickt.")


def bezahlt(c, wer, rid, am=None, ja=True):
    from . import firma
    r = get(c, rid)
    if not r or r["status"] not in ("offen", "bezahlt"):
        raise FinanzError("Nur ausgestellte Rechnungen können bezahlt sein.")
    am = _datum(am or date.today().isoformat(), "Bezahlt am") if ja else None
    c.execute("UPDATE rechnungen SET status=?, bezahlt_am=? WHERE id=?", ("bezahlt" if ja else "offen", am, rid))
    firma.log(c, wer, "rechnung", "rechnung", rid, f"Rechnung {r['nummer']} " + (f"bezahlt am {_de(am)}." if ja else
                                                                                 "wieder offen."))


def stornieren(c, wer, rid, grund, data_dir=None):
    """Ausgestellte Rechnung stornieren: Stornorechnung mit eigener Nummer und negativen Beträgen."""
    from . import firma
    r = get(c, rid)
    if not r or r["status"] not in ("offen", "bezahlt") or r["art"] == "storno":
        raise FinanzError("Stornieren geht nur bei ausgestellten Rechnungen.")
    grund = (grund or "").strip()
    if len(grund) < 3:
        raise FinanzError("Bitte einen Grund für das Storno.")
    sid = c.execute("INSERT INTO rechnungen (projekt_id, lead_id, art, zeitraum, leistung, empfaenger, email, storno_von, "
                    "grund, erstellt_von, erstellt_am) VALUES (?,?, 'storno', ?,?,?,?,?,?,?,?)",
                    (r["projekt_id"], r["lead_id"], r["zeitraum"], r["leistung"], r["empfaenger"], r["email"], rid, grund,
                     wer, now())).lastrowid
    for i, p in enumerate(r["positionen"]):
        c.execute("INSERT INTO rechnung_positionen (rechnung_id, text, menge, einzel, sort) VALUES (?,?,?,?,?)",
                  (sid, p["text"], p["menge"], -p["einzel"], i))
    c.execute("UPDATE rechnungen SET status='storniert', grund=? WHERE id=?", (grund, rid))
    _ausstellen(c, wer, sid, _data_dir(data_dir))
    c.execute("UPDATE rechnungen SET status='storniert' WHERE id=?", (sid,))  # Storno gleicht aus, ist nie „offen“
    firma.log(c, wer, "rechnung", "rechnung", rid, f"Rechnung {r['nummer']} storniert: {grund}", {"storno": sid},
              commit=False)
    c.commit()
    return sid

# ---------------------------------------------------------------- Kosten und Kasse


def kosten_liste(c, mit_beendeten=False):
    heute = date.today().isoformat()
    rows = c.execute("SELECT * FROM kosten ORDER BY (bis IS NOT NULL AND bis < ?), kategorie, titel", (heute,))
    out = []
    for r in rows:
        d = dict(r, kategorie_label=KATEGORIE_LABEL.get(r["kategorie"], r["kategorie"]))
        d["aktiv"] = d["seit"] <= heute and (not d["bis"] or d["bis"] >= heute)
        if d["aktiv"] or mit_beendeten or d["seit"] > heute:
            out.append(d)
    return out


def kosten_speichern(c, wer, kid, titel, betrag, kategorie="sonstiges", seit=None, bis=None, notiz=""):
    from . import firma
    titel = re.sub(r"\s+", " ", titel or "").strip()[:120]
    if not titel:
        raise FinanzError("Wofür sind die Kosten?")
    betrag = _zahl(betrag, "Betrag pro Monat")
    kategorie = kategorie if kategorie in KATEGORIE_LABEL else "sonstiges"
    seit = _datum(seit or date.today().isoformat(), "Seit")
    bis = _datum(bis, "Bis") if bis else None
    if bis and bis < seit:
        raise FinanzError("„Bis“ liegt vor „Seit“.")
    if kid:
        if not c.execute("SELECT 1 FROM kosten WHERE id=?", (kid,)).fetchone():
            raise FinanzError("Diesen Posten gibt es nicht.")
        c.execute("UPDATE kosten SET titel=?, betrag_monat=?, kategorie=?, seit=?, bis=?, notiz=? WHERE id=?",
                  (titel, betrag, kategorie, seit, bis, (notiz or "").strip()[:500] or None, kid))
    else:
        kid = c.execute("INSERT INTO kosten (titel, kategorie, betrag_monat, seit, bis, notiz, erstellt_von, erstellt_am) "
                        "VALUES (?,?,?,?,?,?,?,?)", (titel, kategorie, betrag, seit, bis,
                                                     (notiz or "").strip()[:500] or None, wer, now())).lastrowid
    firma.log(c, wer, "finanzen", "kosten", kid, f"Laufende Kosten: {titel}, {euro(betrag)} im Monat"
              + (f" bis {_de(bis)}" if bis else "") + ".")
    return kid


def kasse_eintragen(c, wer, stand, datum=None, notiz=""):
    from . import firma
    stand = _zahl(stand, "Kontostand", negativ=True)
    datum = _datum(datum or date.today().isoformat())
    kid = c.execute("INSERT INTO kasse (datum, stand, notiz, wer, erstellt_am) VALUES (?,?,?,?,?)",
                    (datum, stand, (notiz or "").strip()[:300] or None, wer, now())).lastrowid
    firma.log(c, wer, "finanzen", "kasse", kid, f"Kassenstand {euro(stand)} am {_de(datum)}.")
    return kid


def kasse_liste(c, limit=24):
    return [dict(r) for r in c.execute("SELECT k.*, t.name AS wer_name FROM kasse k LEFT JOIN team t ON t.key = k.wer "
                                       "ORDER BY datum DESC, id DESC LIMIT ?", (limit,))]

# ---------------------------------------------------------------- Kennzahlen und Geld-Regel


def _monate_zurueck(n, bis=None):
    """Die letzten n abgeschlossenen Monate als (JJJJ-MM, von, bis), ältester zuerst."""
    d = (bis or date.today()).replace(day=1)
    out = []
    for _ in range(n):
        ende = d
        d = (d - timedelta(days=1)).replace(day=1)
        out.append((d.strftime("%Y-%m"), d.isoformat(), ende.isoformat()))
    return list(reversed(out))


def fixkosten(c, stichtag=None):
    tag = (stichtag or date.today()).isoformat()
    return round(c.execute("SELECT IFNULL(SUM(betrag_monat),0) FROM kosten WHERE seit <= ? AND (bis IS NULL OR bis >= ?)",
                           (tag, tag)).fetchone()[0], 2)


def ki_im_zeitraum(c, von, bis):
    from . import kpis
    return kpis.ki_kosten(c, von + "T00:00:00", bis + "T00:00:00")


def umsatz(c, von, bis):
    """Netto der ausgestellten Rechnungen (Stornos ziehen ab) mit Rechnungsdatum im Zeitraum."""
    return round(c.execute("SELECT IFNULL(SUM(netto),0) FROM rechnungen WHERE nummer IS NOT NULL AND datum >= ? "
                           "AND datum < ?", (von, bis)).fetchone()[0], 2)


def kennzahlen(c):
    from . import projekte
    r = regeln(c)
    heute = date.today()
    mrr = round(sum(p["pauschale_monat"] or 0 for p in projekte.liste(c) if p["status"] in ("live", "betrieb")), 2)
    monate = []
    for monat, von, bis in _monate_zurueck(3):
        k = round(fixkosten(c, date.fromisoformat(bis) - timedelta(days=1)) + ki_im_zeitraum(c, von, bis), 2)
        u = umsatz(c, von, bis)
        monate.append({"monat": monat, "umsatz": u, "kosten": k, "uebrig": round(u - k, 2)})
    ki_monat = round(sum(ki_im_zeitraum(c, von, bis) for _, von, bis in _monate_zurueck(3)) / 3, 2)
    fix = fixkosten(c)
    monatskosten = round(fix + ki_monat, 2)
    kasse = c.execute("SELECT stand, datum FROM kasse ORDER BY datum DESC, id DESC LIMIT 1").fetchone()
    offen = c.execute("SELECT IFNULL(SUM(brutto),0), COUNT(*) FROM rechnungen WHERE status='offen'").fetchone()
    ueber = c.execute("SELECT IFNULL(SUM(brutto),0), COUNT(*) FROM rechnungen WHERE status='offen' AND faellig < ?",
                      (heute.isoformat(),)).fetchone()
    # Geld-Regel: drei Monate in Folge mindestens 1,5 × neue Personalkosten übrig und 6 Monatskosten auf dem Konto
    personal = r["personalkosten_neu"]
    bed1 = bool(personal) and all(m["uebrig"] >= 1.5 * personal for m in monate)
    ziel_kasse = round(6 * (monatskosten + personal), 2)
    bed2 = bool(kasse) and kasse["stand"] >= ziel_kasse
    ampel = "grau" if not personal else ("gruen" if bed1 and bed2 else ("gelb" if bed1 or bed2 else "rot"))
    return {"mrr": mrr, "fixkosten": fix, "ki_monat": ki_monat, "monatskosten": monatskosten,
            "gewinnschwelle": mrr >= monatskosten, "luecke": round(monatskosten - mrr, 2),
            "kasse": kasse["stand"] if kasse else None, "kasse_datum": kasse["datum"] if kasse else None,
            "reichweite": round(kasse["stand"] / monatskosten, 1) if kasse and monatskosten else None,
            "offen": round(offen[0], 2), "offen_n": offen[1], "ueberfaellig": round(ueber[0], 2), "ueberfaellig_n": ueber[1],
            "monate": monate, "personal": personal, "bed_uebrig": bed1, "bed_kasse": bed2, "ziel_kasse": ziel_kasse,
            "ampel": ampel, "fehlt": fehlende_angaben(r), "steuer": r["steuer"]}


def kpi_umsatz(c, von, bis, person=None):
    return None if person else umsatz(c, von[:10], bis[:10])

# ---------------------------------------------------------------- Export für den Steuerberater


def export_zip(c, von, bis, data_dir):
    """ZIP mit rechnungen.csv, kosten.csv, kasse.csv und den PDFs der Rechnungen mit Datum in [von, bis)."""
    von, bis = _datum(von, "Von"), _datum(bis, "Bis")
    if bis <= von:
        raise FinanzError("„Bis“ muss nach „Von“ liegen.")
    buf = io.BytesIO()

    def csv_text(kopf, zeilen):
        s = io.StringIO()
        w = csv.writer(s, delimiter=";")
        w.writerow(kopf)
        w.writerows(zeilen)
        return "﻿" + s.getvalue()  # mit BOM, damit Excel die Umlaute erkennt

    def zahl(v):
        return f"{v:.2f}".replace(".", ",")
    rechnungen = c.execute("SELECT r.*, l.name AS kunde FROM rechnungen r LEFT JOIN leads l ON l.id = r.lead_id "
                           "WHERE r.nummer IS NOT NULL AND r.datum >= ? AND r.datum < ? ORDER BY r.datum, r.nummer",
                           (von, bis)).fetchall()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("rechnungen.csv", csv_text(
            ["Nummer", "Datum", "Kunde", "Art", "Leistungszeitraum", "Netto", "USt-Satz", "USt", "Brutto", "Status",
             "Fällig", "Bezahlt am", "Storno zu"],
            [[r["nummer"], _de(r["datum"]), r["kunde"], ARTEN.get(r["art"], r["art"]), r["leistung"] or "", zahl(r["netto"]),
              f"{r['ust_satz']:g}", zahl(r["ust"]), zahl(r["brutto"]), STATUS.get(r["status"], r["status"]),
              _de(r["faellig"]), _de(r["bezahlt_am"]),
              (get(c, r["storno_von"]) or {}).get("nummer", "") if r["storno_von"] else ""] for r in rechnungen]))
        z.writestr("kosten.csv", csv_text(
            ["Posten", "Kategorie", "Betrag pro Monat", "Seit", "Bis", "Notiz"],
            [[k["titel"], k["kategorie_label"], zahl(k["betrag_monat"]), _de(k["seit"]), _de(k["bis"]), k["notiz"] or ""]
             for k in kosten_liste(c, mit_beendeten=True) if k["seit"] < bis and (not k["bis"] or k["bis"] >= von)]
            + [["KI (automatisch)", "KI", zahl(ki_im_zeitraum(c, von, bis)), _de(von), _de(bis), "Summe im Zeitraum"]]))
        z.writestr("kasse.csv", csv_text(
            ["Datum", "Kontostand", "Notiz"],
            [[_de(k["datum"]), zahl(k["stand"]), k["notiz"] or ""] for k in c.execute(
                "SELECT * FROM kasse WHERE datum >= ? AND datum < ? ORDER BY datum", (von, bis))]))
        for r in rechnungen:
            datei = ordner(data_dir) / (r["pdf"] or "")
            if r["pdf"] and datei.exists():
                z.write(datei, f"rechnungen/{r['pdf']}")
    return buf.getvalue(), f"ylva-labs-finanzen-{von}-bis-{bis}.zip"

# ---------------------------------------------------------------- Takt


def takt(c, jetzt=None, data_dir=None):
    """Am eingestellten Tag des Monats: Monatsrechnungen für Projekte mit Pauschale vorbereiten und zur Freigabe
    geben (einmal je Projekt und Monat). Ohne vollständige Firmendaten bleibt es beim Entwurf."""
    from . import firma
    jetzt = jetzt or datetime.now()
    if jetzt.day < regeln(c)["monat_tag"] or jetzt.hour < 6:
        return 0
    monat = jetzt.strftime("%Y-%m")
    n = 0
    for row in c.execute("SELECT id FROM projekte WHERE status IN ('live','betrieb') AND IFNULL(pauschale_monat,0) > 0"
                         ).fetchall():
        rid = monat_anlegen(c, "System", row["id"], monat)
        if not rid:
            continue
        n += 1
        try:
            zur_freigabe(c, "System", rid)
        except FinanzError as exc:
            firma.log(c, "System", "rechnung", "rechnung", rid, f"Monatsrechnung bleibt Entwurf: {exc}")
    return n
