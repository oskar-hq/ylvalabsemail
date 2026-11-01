"""KPIs, Wochenziele, Wochengespräch und Lernstufen-Messung (Firmen-OS, Stufe 4).

Alle Zahlen entstehen aus dem, was ohnehin passiert (G3): Prüfungen, Termine, Kontakte, Aufgaben, Stufenwechsel,
KI-Kosten. Niemand pflegt Zahlen von Hand. KPIs, die erst spätere Stufen liefern (Zeiterfassung, Abnahmen,
Finanzen), stehen im Katalog, sind aber als „kommt mit Stufe …“ markiert.

Persönliche Zahlen sieht die Person selbst und wer sie führt (verantwortlich für einen ihrer Bereiche oder einen
Bereich darüber), außerdem wer „Zahlen und Ziele: Verwalten“ hat. Teamzahlen sehen alle mit Zugriff aufs Cockpit.
"""
import difflib
import json
from datetime import date, datetime, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS kpi_ziele (
    kpi_key TEXT NOT NULL, person TEXT NOT NULL DEFAULT '', wert REAL NOT NULL, geaendert_von TEXT, geaendert_am TEXT,
    PRIMARY KEY (kpi_key, person)
);
CREATE TABLE IF NOT EXISTS ziele (
    id INTEGER PRIMARY KEY AUTOINCREMENT, person TEXT NOT NULL, woche TEXT NOT NULL, text TEXT NOT NULL,
    kpi_key TEXT, zielwert REAL, status TEXT NOT NULL DEFAULT 'offen', notiz TEXT,
    erstellt_von TEXT, erstellt_am TEXT NOT NULL, bewertet_von TEXT, bewertet_am TEXT
);
CREATE INDEX IF NOT EXISTS ziele_woche ON ziele(woche, person);
"""

# Startwerte für Zielwerte aus den Rollen (einmalig, danach im Cockpit änderbar)
SEED = [
    "INSERT OR IGNORE INTO kpi_ziele (kpi_key, person, wert) VALUES ('anteil_angenommen', '', 80)",
    "INSERT OR IGNORE INTO kpi_ziele (kpi_key, person, wert) VALUES ('entwuerfe_ohne_aenderung', '', 80)",
]

REGELN = {  # Lernstufen-Messung, alles einstellbar (Cockpit → Lernstufen)
    "s1_quote": ("Stufe 1 → 2: Anteil angenommener Leads mindestens (%)", 80),
    "s1_mindest": ("Stufe 1 → 2: dafür mindestens geprüfte Leads je Woche", 5),
    "s2_quote": ("Stufe 2 → 3: Entwürfe ohne große Änderung mindestens (%)", 80),
    "s2_mindest": ("Stufe 2 → 3: dafür mindestens freigegebene Entwürfe je Woche", 3),
    "s2_aehnlich": ("„Ohne große Änderung“ heißt: Text zu mindestens so viel % gleich", 80),
    "wochen": ("So viele Wochen am Stück muss die Bedingung erfüllt sein", 4),
    "s3_termine": ("Stufe 3 → 4: gebuchte Kundentermine (insgesamt)", 10),
    "s3_gespraeche": ("Stufe 3 → 4: begleitete Gespräche (insgesamt)", 5),
}


class KpiError(ValueError):
    pass


def now():
    return datetime.now().isoformat(timespec="seconds")

# ---------------------------------------------------------------- Wochen


def woche_von(d=None):
    d = d or date.today()
    jahr, kw, _ = d.isocalendar()
    return f"{jahr}-W{kw:02d}"


def woche_grenzen(woche):
    """'2026-W45' → (Montag 00:00, nächster Montag 00:00) als ISO-Text."""
    try:
        montag = datetime.strptime(woche + "-1", "%G-W%V-%u").date()
    except ValueError as exc:
        raise KpiError("Unbekannte Woche.") from exc
    return montag.isoformat() + "T00:00:00", (montag + timedelta(days=7)).isoformat() + "T00:00:00"


def woche_plus(woche, n):
    von, _ = woche_grenzen(woche)
    return woche_von(date.fromisoformat(von[:10]) + timedelta(weeks=n))


def woche_text(woche):
    von, _ = woche_grenzen(woche)
    m = date.fromisoformat(von[:10])
    s = m + timedelta(days=6)
    return f"KW {int(woche[-2:])} · {m.day}.{m.month}. bis {s.day}.{s.month}."

# ---------------------------------------------------------------- Rohzahlen (je Zeitraum, optional je Person)


def _n(c, sql, args):
    return c.execute(sql, args).fetchone()[0] or 0


def _person_sql(feld, person):
    return (f" AND {feld} = ?", [person]) if person else ("", [])


def qualifizierte_leads(c, von, bis, person=None):
    extra, args = _person_sql("angefragt_von", person)
    return _n(c, "SELECT COUNT(*) FROM freigaben WHERE art='lead_pruefung' AND stufe='erst' AND status='angenommen' "
                 f"AND entschieden_am >= ? AND entschieden_am < ?{extra}", [von, bis, *args])


def anteil_angenommen(c, von, bis, person=None):
    extra, args = _person_sql("angefragt_von", person)
    r = c.execute("SELECT SUM(status='angenommen'), SUM(status IN ('angenommen','abgelehnt')) FROM freigaben "
                  "WHERE art='lead_pruefung' AND stufe='erst' AND entschieden_am >= ? AND entschieden_am < ?"
                  + extra, [von, bis, *args]).fetchone()
    return round(100 * (r[0] or 0) / r[1]) if r[1] else None


def gepruefte_leads(c, von, bis, person=None):
    extra, args = _person_sql("angefragt_von", person)
    return _n(c, "SELECT COUNT(*) FROM freigaben WHERE art='lead_pruefung' AND stufe='erst' "
                 f"AND status IN ('angenommen','abgelehnt') AND entschieden_am >= ? AND entschieden_am < ?{extra}",
              [von, bis, *args])


def termine_gebucht(c, von, bis, person=None):
    extra, args = _person_sql("erstellt_von", person)
    return _n(c, "SELECT COUNT(*) FROM termine WHERE art='kunde' AND erstellt_am >= ? AND erstellt_am < ?" + extra,
              [von, bis, *args])


def kontakte(c, von, bis, person=None):
    """Kontakte mit Betrieben (Brief, Telefon, Mail, LinkedIn …), aus dem Verlauf der Leads."""
    args = [von, bis]
    extra = ""
    if person:
        from . import firma
        extra, args = " AND author IN (?, ?)", [*args, person, firma.name_von(c, person)]
    return _n(c, f"SELECT COUNT(*) FROM lead_events WHERE kind IN ('sent','kontakt') AND at >= ? AND at < ?{extra}",
              args)


def erstgespraeche(c, von, bis, person=None):
    extra, args = _person_sql("verantwortlich", person)
    return _n(c, "SELECT COUNT(*) FROM termine WHERE lead_id IS NOT NULL AND ergebnis='stattgefunden' "
                 f"AND start >= ? AND start < ?{extra}", [von, bis, *args])


def preisgespraeche(c, von, bis, person=None):
    extra, args = _person_sql("owner", person)
    return _n(c, f"SELECT COUNT(*) FROM leads WHERE preisgespraech_am >= ? AND preisgespraech_am < ?{extra}",
              [von, bis, *args])


def neue_kunden(c, von, bis, person=None):
    extra, args = _person_sql("owner", person)
    return _n(c, f"SELECT COUNT(*) FROM leads WHERE stage='kunde' AND stage_at >= ? AND stage_at < ?{extra}",
              [von, bis, *args])


def abschlussquote(c, von, bis, person=None):
    g = erstgespraeche(c, von, bis, person)
    return round(100 * neue_kunden(c, von, bis, person) / g) if g else None


def ki_kosten(c, von, bis, person=None):
    import os
    extra, args = _person_sql("person", person)
    usd = c.execute(f"SELECT SUM(cost_usd) FROM ai_usage WHERE at >= ? AND at < ?{extra}", [von, bis, *args]
                    ).fetchone()[0] or 0
    try:
        kurs = float(os.environ.get("EUR_PER_USD") or 0.86)
    except ValueError:
        kurs = 0.86
    return round(usd * kurs, 2)


def aufgaben_erledigt(c, von, bis, person=None):
    extra, args = _person_sql("person", person)
    return _n(c, "SELECT COUNT(*) FROM aufgaben WHERE status='erledigt' AND erledigt_am >= ? AND erledigt_am < ?"
              + extra, [von, bis, *args])


def aufgaben_ueberfaellig(c, von, bis, person=None):
    """Stand jetzt (nur für die laufende Woche sinnvoll): offene Aufgaben mit Termin in der Vergangenheit."""
    if bis < datetime.now().isoformat():
        return None
    extra, args = _person_sql("person", person)
    return _n(c, f"SELECT COUNT(*) FROM aufgaben WHERE status='offen' AND faellig < ?{extra}",
              [date.today().isoformat(), *args])


def entwuerfe(c, von, bis, person=None):
    """(freigegeben, davon ohne große Änderung) für Entwürfe dieser Person, die jemand anderes freigegeben hat."""
    schwelle = lernregeln(c)["s2_aehnlich"] / 100
    gesamt = gleich = 0
    for r in c.execute("SELECT daten FROM ereignisse WHERE art='entwurf_freigabe' AND at >= ? AND at < ?", (von, bis)):
        d = json.loads(r["daten"] or "{}")
        if person and d.get("von") != person:
            continue
        gesamt += 1
        gleich += 1 if (d.get("aehnlich") or 0) >= schwelle else 0
    return gesamt, gleich


def entwuerfe_ohne_aenderung(c, von, bis, person=None):
    gesamt, gleich = entwuerfe(c, von, bis, person)
    return round(100 * gleich / gesamt) if gesamt else None

# ---------------------------------------------------------------- KPI-Katalog (docs/FIRMEN-OS.md, 4.9)


def _projekte():
    from . import projekte
    return projekte


def _k(key, label, bereich, fn, einheit="anzahl", richtung="hoch", je_person=True, quelle="", stufe=None):
    return {"key": key, "label": label, "bereich": bereich, "fn": fn, "einheit": einheit, "richtung": richtung,
            "je_person": je_person, "quelle": quelle, "kommt_mit": stufe}


KATALOG = [
    _k("qualifizierte_leads", "Qualifizierte Leads", "akquise", qualifizierte_leads,
       quelle="Leads mit Prüfung „angenommen“"),
    _k("anteil_angenommen", "Anteil angenommener Leads", "akquise", anteil_angenommen, "prozent",
       quelle="Prüfungen angenommen / alle"),
    _k("entwuerfe_ohne_aenderung", "Entwürfe ohne große Änderung", "akquise", entwuerfe_ohne_aenderung, "prozent",
       quelle="Text bei der Freigabe kaum geändert"),
    _k("termine_gebucht", "Termine gebucht", "akquise", termine_gebucht, quelle="Kundentermine, die die Person anlegt"),
    _k("kontakte", "Kontakte", "akquise", kontakte, quelle="Brief, Telefon, Mail, LinkedIn aus dem Verlauf"),
    _k("erstgespraeche", "Geführte Erstgespräche", "markt", erstgespraeche, quelle="Termine mit Lead, stattgefunden"),
    _k("preisgespraeche", "Ernsthafte Preisgespräche", "markt", preisgespraeche, quelle="Markierung in Akte oder Termin"),
    _k("neue_kunden", "Neue Kunden", "markt", neue_kunden, quelle="Stufe „Kunde“ erreicht"),
    _k("abschlussquote", "Abschlussquote", "markt", abschlussquote, "prozent",
       quelle="Neue Kunden / geführte Erstgespräche"),
    _k("tage_bis_live", "Tage bis live", "markt", lambda *a: _projekte().kpi_tage_bis_live(*a), "tage", "runter",
       quelle="Beauftragt bis live, Projekte live in der Woche"),
    _k("kunden_bleiben", "Kunden, die bleiben", "markt", lambda *a: _projekte().kpi_kunden_bleiben(*a), "prozent",
       je_person=False, quelle="Vor 3+ Monaten live, noch aktiv"),
    _k("kassenstand", "Kassenstand", "markt", None, "euro", stufe=8),
    _k("ki_kosten", "KI-Kosten", "technik", ki_kosten, "euro", "runter", quelle="Abrechnung der KI je Aufruf"),
    _k("stunden_pro_build", "Stunden pro Build", "technik", lambda *a: _projekte().kpi_stunden_pro_build(*a), "stunden",
       "runter", quelle="Zeiterfassung an Projekten, live in der Woche"),
    _k("freigaben_ohne_aenderung", "Abnahmen beim ersten Mal", "technik",
       lambda *a: _projekte().kpi_abnahmen_erstes_mal(*a), "prozent", quelle="Erste Abnahme bestanden"),
    _k("ausfallzeit", "Ausfallzeit", "technik", None, "stunden", "runter", stufe=6),
    _k("aufgaben_erledigt", "Aufgaben erledigt", "alle", aufgaben_erledigt, quelle="Aufgaben im OS"),
    _k("aufgaben_ueberfaellig", "Aufgaben überfällig", "alle", aufgaben_ueberfaellig, richtung="runter",
       quelle="Stand jetzt"),
]
KPI = {k["key"]: k for k in KATALOG}
BEREICH_LABEL = {"akquise": "Akquise & Recherche", "markt": "Markt, Kunden & Betrieb", "technik": "Produkt & Technik",
                 "alle": "Alle"}


def wert(c, key, woche, person=None):
    k = KPI.get(key)
    if not k or not k["fn"]:
        return None
    von, bis = woche_grenzen(woche)
    return k["fn"](c, von, bis, person)


def ziel(c, key, person=None):
    row = c.execute("SELECT wert FROM kpi_ziele WHERE kpi_key=? AND person IN (?, '') ORDER BY person = '' LIMIT 1",
                    (key, person or "")).fetchone()
    return row["wert"] if row else None


def ziel_setzen(c, wer, key, wert_, person=""):
    from . import firma
    if key not in KPI or not KPI[key]["fn"]:
        raise KpiError("Diese Kennzahl gibt es (noch) nicht.")
    if wert_ in ("", None):
        c.execute("DELETE FROM kpi_ziele WHERE kpi_key=? AND person=?", (key, person or ""))
    else:
        try:
            wert_ = float(str(wert_).replace(",", "."))
        except ValueError as exc:
            raise KpiError("Zielwert bitte als Zahl.") from exc
        c.execute("INSERT INTO kpi_ziele (kpi_key, person, wert, geaendert_von, geaendert_am) VALUES (?,?,?,?,?) "
                  "ON CONFLICT(kpi_key, person) DO UPDATE SET wert=excluded.wert, geaendert_von=excluded.geaendert_von,"
                  " geaendert_am=excluded.geaendert_am", (key, person or "", wert_, wer, now()))
    firma.log(c, wer, "einstellung", "kpi", key, f"Zielwert {KPI[key]['label']}: {wert_ if wert_ not in ('', None) else 'keiner'}"
              + (f" ({firma.name_von(c, person)})" if person else ""))


def ampel(key, wert_, ziel_):
    if wert_ is None or ziel_ is None:
        return "grau"
    if KPI[key]["richtung"] == "runter":
        return "gruen" if wert_ <= ziel_ else ("gelb" if wert_ <= ziel_ * 1.3 else "rot")
    return "gruen" if wert_ >= ziel_ else ("gelb" if wert_ >= ziel_ * 0.7 else "rot")


def zeile(c, key, woche, person=None, wochen=8):
    """Eine KPI für das Cockpit: Wert dieser Woche, Verlauf, Ziel, Ampel."""
    k = KPI[key]
    if not k["fn"]:
        return dict(k, wert=None, verlauf=[], ziel=None, ampel="grau", fn=None)
    verlauf = [wert(c, key, woche_plus(woche, -n), person) for n in range(wochen - 1, -1, -1)]
    z = ziel(c, key, person)
    return dict(k, fn=None, wert=verlauf[-1], verlauf=verlauf, ziel=z, ampel=ampel(key, verlauf[-1], z),
                vorwoche=verlauf[-2] if len(verlauf) > 1 else None)

# ---------------------------------------------------------------- Wer sieht wessen Zahlen


def fuehrt(c, wer, person):
    """Führt `wer` diese Person? (Verantwortlich für einen ihrer Bereiche oder einen Bereich darüber.)"""
    from . import firma
    if wer == person:
        return False
    for b in firma.bereiche_von(c, person):
        key, gesehen = b, set()
        while key and key not in gesehen:
            gesehen.add(key)
            row = firma.bereich(c, key)
            if not row:
                break
            if row["verantwortlich"] == wer and row["verantwortlich"] != person:
                return True
            key = row["eltern"]
    return False


def darf_sehen(c, wer, person):
    from . import firma
    return wer == person or fuehrt(c, wer, person) or firma.darf(c, wer, "cockpit", firma.VERWALTEN)


def sichtbare_personen(c, wer):
    from . import firma
    return [p for p in firma.aktive_personen(c) if darf_sehen(c, wer, p["key"])]

# ---------------------------------------------------------------- Wochenziele


def ziele_der_woche(c, woche, person=None):
    where, args = ["z.woche = ?"], [woche]
    if person:
        where.append("z.person = ?")
        args.append(person)
    rows = c.execute(f"SELECT z.*, t.name AS person_name FROM ziele z LEFT JOIN team t ON t.key = z.person "
                     f"WHERE {' AND '.join(where)} ORDER BY t.sort, z.id", args).fetchall()
    out = []
    for r in rows:
        z = dict(r)
        z["ist"] = wert(c, z["kpi_key"], woche, z["person"]) if z["kpi_key"] else None
        z["kpi_label"] = KPI[z["kpi_key"]]["label"] if z["kpi_key"] in KPI else None
        z["erreicht_live"] = (z["ist"] is not None and z["zielwert"] is not None and z["ist"] >= z["zielwert"])
        z["fortschritt"] = (min(100, round(100 * z["ist"] / z["zielwert"])) if z["ist"] is not None and z["zielwert"]
                            else None)
        out.append(z)
    return out


def darf_ziele(c, wer, person):
    """Eigene Ziele setzt jede Person selbst; wer sie führt, auch (im Gespräch, z. B. Oskar mit Hamza)."""
    from . import firma
    return wer == person or fuehrt(c, wer, person) or firma.darf(c, wer, "cockpit", firma.VERWALTEN)


def ziel_anlegen(c, wer, person, woche, text, kpi_key=None, zielwert=None, max_ziele=3):
    from . import firma
    text = " ".join((text or "").split())
    if not text or len(text) > 200:
        raise KpiError("Bitte das Ziel in einem Satz (höchstens 200 Zeichen).")
    if not firma.aktive_person(c, person):
        raise KpiError("Diese Person ist nicht aktiv.")
    woche_grenzen(woche)
    if c.execute("SELECT COUNT(*) FROM ziele WHERE person=? AND woche=?", (person, woche)).fetchone()[0] >= max_ziele:
        raise KpiError(f"Höchstens {max_ziele} Ziele pro Woche. Weniger ist mehr.")
    if kpi_key:
        if kpi_key not in KPI or not KPI[kpi_key]["fn"]:
            raise KpiError("Diese Kennzahl gibt es (noch) nicht.")
        try:
            zielwert = float(str(zielwert).replace(",", "."))
        except (TypeError, ValueError) as exc:
            raise KpiError("Mit Kennzahl bitte auch einen Zielwert.") from exc
    else:
        kpi_key, zielwert = None, None
    cur = c.execute("INSERT INTO ziele (person, woche, text, kpi_key, zielwert, erstellt_von, erstellt_am) "
                    "VALUES (?,?,?,?,?,?,?)", (person, woche, text, kpi_key, zielwert, wer, now()))
    firma.log(c, wer, "ziel", "ziel", cur.lastrowid, f"Wochenziel für {firma.name_von(c, person)}: {text}",
              {"woche": woche, "kpi": kpi_key, "zielwert": zielwert})
    return cur.lastrowid


def ziel_bewerten(c, wer, zid, status, notiz=""):
    from . import firma
    row = c.execute("SELECT * FROM ziele WHERE id=?", (zid,)).fetchone()
    if not row:
        raise KpiError("Dieses Ziel gibt es nicht.")
    if status not in ("offen", "erreicht", "verfehlt"):
        raise KpiError("Unbekannter Stand.")
    if not darf_ziele(c, wer, row["person"]):
        raise KpiError("Das darf die Person selbst und wer sie führt.")
    c.execute("UPDATE ziele SET status=?, notiz=?, bewertet_von=?, bewertet_am=? WHERE id=?",
              (status, (notiz or "").strip()[:500] or None, wer, now(), zid))
    firma.log(c, wer, "ziel", "ziel", zid, f"Wochenziel {status}: {row['text']}")


def ziel_loeschen(c, wer, zid):
    from . import firma
    row = c.execute("SELECT * FROM ziele WHERE id=?", (zid,)).fetchone()
    if not row or not darf_ziele(c, wer, row["person"]):
        raise KpiError("Das darf die Person selbst und wer sie führt.")
    c.execute("DELETE FROM ziele WHERE id=?", (zid,))
    firma.log(c, wer, "ziel", "ziel", zid, f"Wochenziel gelöscht: {row['text']}")

# ---------------------------------------------------------------- Entwürfe: wie stark wurde bei der Freigabe geändert?


def entwurf_merken(c, lead_id, person, text):
    """Der Entwurf, wie ihn die schreibende Person hinterlassen hat. Ändert jemand anderes (die freigebende Person),
    bleibt dieser Stand stehen, damit die Änderung bei der Freigabe messbar ist."""
    row = c.execute("SELECT entwurf_von FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not person or not row or (row["entwurf_von"] and row["entwurf_von"] != person):
        return
    c.execute("UPDATE leads SET entwurf_original=?, entwurf_von=? WHERE id=?", (text or "", person, lead_id))
    c.commit()


def entwurf_freigegeben(c, lead_id, wer, text):
    """Bei Versand oder „ist raus“: Ähnlichkeit zwischen Entwurf und freigegebenem Text ins Protokoll. Zählt nur,
    wenn jemand anderes freigibt als die Person, die geschrieben hat."""
    from . import firma
    row = c.execute("SELECT entwurf_original, entwurf_von FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not row or not row["entwurf_von"] or row["entwurf_von"] == wer or row["entwurf_original"] is None:
        return None
    aehnlich = difflib.SequenceMatcher(None, row["entwurf_original"].strip(), (text or "").strip()).ratio()
    firma.log(c, wer, "entwurf_freigabe", "lead", lead_id,
              f"Entwurf von {firma.name_von(c, row['entwurf_von'])} freigegeben ({round(aehnlich * 100)} % gleich).",
              {"von": row["entwurf_von"], "aehnlich": round(aehnlich, 3)})
    c.execute("UPDATE leads SET entwurf_von=NULL, entwurf_original=NULL WHERE id=?", (lead_id,))
    c.commit()
    return aehnlich

# ---------------------------------------------------------------- Lernstufen-Messung


def lernregeln(c):
    row = c.execute("SELECT value FROM kv WHERE key='regel:lernstufen'").fetchone()
    try:
        stored = json.loads(row["value"]) if row else {}
    except ValueError:
        stored = {}
    out = {}
    for k, (_label, default) in REGELN.items():
        try:
            out[k] = int(stored.get(k, default))
        except (TypeError, ValueError):
            out[k] = default
    return out


def lernregeln_speichern(c, wer, form):
    from . import firma
    neu = {}
    for k, (label, _default) in REGELN.items():
        raw = str(form.get(k, "")).strip()
        if not raw.isdigit() or not 0 <= int(raw) <= 1000:
            raise KpiError(f"{label}: bitte eine Zahl.")
        neu[k] = int(raw)
    for k in ("s1_quote", "s2_quote", "s2_aehnlich"):
        if neu[k] > 100:
            raise KpiError("Prozentwerte höchstens 100.")
    if not 1 <= neu["wochen"] <= 26:
        raise KpiError("Wochen am Stück: 1 bis 26.")
    c.execute("INSERT INTO kv (key, value) VALUES ('regel:lernstufen', ?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(neu),))
    firma.log(c, wer, "einstellung", "regel", "lernstufen", "Regeln für die Lernstufen geändert.", neu)


def lernstand(c, person, woche=None):
    """Erfüllt die Person die Aufstiegsbedingung ihrer Lernstufe? Mit Begründung je Woche."""
    from . import firma
    p = firma.person(c, person)
    stufe = (p or {}).get("lernstufe")
    r = lernregeln(c)
    woche = woche or woche_plus(woche_von(), -1)  # die letzte abgeschlossene Woche zählt
    out = {"person": person, "stufe": stufe, "erfuellt": False, "wochen": [], "text": ""}
    if not stufe or stufe >= 4:
        out["text"] = "Keine Aufstiegsmessung." if not stufe else "Höchste Lernstufe erreicht."
        return out
    if stufe in (1, 2):
        for n in range(r["wochen"] - 1, -1, -1):
            w = woche_plus(woche, -n)
            von, bis = woche_grenzen(w)
            if stufe == 1:
                anzahl, quote = gepruefte_leads(c, von, bis, person), anteil_angenommen(c, von, bis, person)
                ok = anzahl >= r["s1_mindest"] and (quote or 0) >= r["s1_quote"]
            else:
                anzahl, gleich = entwuerfe(c, von, bis, person)
                quote = round(100 * gleich / anzahl) if anzahl else None
                ok = anzahl >= r["s2_mindest"] and (quote or 0) >= r["s2_quote"]
            out["wochen"].append({"woche": w, "anzahl": anzahl, "quote": quote, "ok": ok})
        out["erfuellt"] = all(x["ok"] for x in out["wochen"])
        was = "angenommene Leads" if stufe == 1 else "Entwürfe ohne große Änderung"
        mind = r["s1_mindest"] if stufe == 1 else r["s2_mindest"]
        out["text"] = (f"{sum(x['ok'] for x in out['wochen'])} von {r['wochen']} Wochen mit mindestens "
                       f"{r['s1_quote'] if stufe == 1 else r['s2_quote']} % {was} (je mindestens {mind}).")
    else:
        termine = _n(c, "SELECT COUNT(*) FROM termine WHERE art='kunde' AND erstellt_von=? AND status != 'abgesagt'",
                     [person])
        begleitet = _n(c, "SELECT COUNT(DISTINCT t.id) FROM termine t JOIN termin_teilnehmer tt ON tt.termin_id=t.id "
                          "WHERE tt.person=? AND t.verantwortlich != ? AND t.ergebnis='stattgefunden' "
                          "AND t.lead_id IS NOT NULL", [person, person])
        out["erfuellt"] = termine >= r["s3_termine"] and begleitet >= r["s3_gespraeche"]
        out["text"] = f"{termine} von {r['s3_termine']} Terminen gebucht, {begleitet} von {r['s3_gespraeche']} Gesprächen begleitet."
    return out


def _aufstieg_pruefer(c, person):
    """Wer gibt den Aufstieg frei: die Person, die sie führt (oberste Ebene), sonst eine Person, die Personen
    verwaltet."""
    from . import firma
    fuehrende = [p["key"] for p in firma.aktive_personen(c) if fuehrt(c, p["key"], person)]
    verwalter = [v for v in firma.verwalter(c) if v != person]
    for v in verwalter:
        if v in fuehrende:
            return v
    return (fuehrende or verwalter or [None])[0]


def aufstieg_pruefen(c, person, wer="System"):
    """Bedingung erfüllt und noch kein offener Vorschlag: Freigabe anlegen (Stufe C). Gibt die Freigabe-ID zurück."""
    from . import aufgaben, firma
    stand = lernstand(c, person)
    if not stand["erfuellt"]:
        return None
    if c.execute("SELECT 1 FROM freigaben WHERE art='lernstufe' AND objekt_typ='person' AND vorschlag=? AND status IN "
                 "('offen','abgelehnt') AND angefragt_am >= ?",
                 (json.dumps({"person": person, "nach": stand["stufe"] + 1}),
                  (datetime.now() - timedelta(days=28)).isoformat())).fetchone():
        return None  # schon vorgeschlagen (oder vor kurzem abgelehnt)
    pruefer = _aufstieg_pruefer(c, person)
    if not pruefer:
        return None
    cur = c.execute("INSERT INTO freigaben (art, objekt_typ, objekt_id, stufe, vorschlag, angefragt_von, angefragt_am, "
                    "pruefer) VALUES ('lernstufe', 'person', NULL, 'C', ?, ?, ?, ?)",
                    (json.dumps({"person": person, "nach": stand["stufe"] + 1}), wer, now(), pruefer))
    c.commit()
    name = firma.name_von(c, person)
    firma.log(c, wer, "lernstufe", "person", person, f"Aufstieg vorgeschlagen: {name} auf Lernstufe {stand['stufe'] + 1}.",
              {"begruendung": stand["text"]})
    aufgaben.melden(c, pruefer, f"Aufstieg freigeben: {name}", f"Lernstufe {stand['stufe'] + 1}. {stand['text']}",
                    "/wochengespraech", push=True)
    return cur.lastrowid


def aufstiege(c, pruefer=None):
    where, args = ["art='lernstufe'", "status='offen'"], []
    if pruefer:
        where.append("pruefer=?")
        args.append(pruefer)
    out = []
    for r in c.execute(f"SELECT * FROM freigaben WHERE {' AND '.join(where)} ORDER BY id", args):
        d = dict(r)
        v = json.loads(d["vorschlag"] or "{}")
        from . import firma
        d.update(person=v.get("person"), nach=v.get("nach"), person_name=firma.name_von(c, v.get("person")),
                 stand=lernstand(c, v.get("person")))
        out.append(d)
    return out


def aufstieg_entscheiden(c, wer, fid, ok, grund=""):
    from . import aufgaben, firma
    row = c.execute("SELECT * FROM freigaben WHERE id=? AND art='lernstufe'", (fid,)).fetchone()
    if not row or row["status"] != "offen":
        raise KpiError("Dieser Vorschlag ist schon entschieden.")
    if wer != row["pruefer"] and not firma.darf(c, wer, "personen", firma.VERWALTEN):
        raise KpiError("Freigeben darf die Person, die führt.")
    grund = (grund or "").strip()
    if not ok and len(grund) < 3:
        raise KpiError("Bitte einen Grund angeben.")
    v = json.loads(row["vorschlag"])
    c.execute("UPDATE freigaben SET status=?, grund=?, entschieden_von=?, entschieden_am=? WHERE id=?",
              ("angenommen" if ok else "abgelehnt", grund or None, wer, now(), fid))
    if ok:
        c.execute("UPDATE team SET lernstufe=? WHERE key=?", (v["nach"], v["person"]))
    name = firma.name_von(c, v["person"])
    firma.log(c, wer, "lernstufe", "person", v["person"],
              f"{name}: Lernstufe {v['nach']} {'freigegeben' if ok else 'noch nicht'}." + (f" {grund}" if grund else ""))
    if v["person"] != wer:
        aufgaben.melden(c, v["person"], f"Lernstufe {v['nach']}" + (" erreicht" if ok else ": noch nicht"),
                        grund or "Glückwunsch, ab jetzt gilt die nächste Stufe.", "/cockpit")


def takt(c):
    """Einmal am Tag: Lernstufen prüfen und Aufstiege vorschlagen (Hintergrund-Arbeiter)."""
    from . import firma, leaddb
    heute = date.today().isoformat()
    if leaddb.kv_get(c, "lernstufen_geprueft") == heute:
        return 0
    leaddb.kv_set(c, "lernstufen_geprueft", heute)
    return sum(1 for p in firma.aktive_personen(c) if p.get("lernstufe") and aufstieg_pruefen(c, p["key"]))

# ---------------------------------------------------------------- Agenda fürs Wochengespräch


def agenda(c, wer, woche):
    """Pro Person (die `wer` sehen darf): Zahlen der Woche, Ziele, wo es hängt. Dazu Teamzahlen und offene
    Entscheidungen. Ergebnisse werden im Gespräch direkt als Aufgaben und Ziele erfasst."""
    from . import aufgaben, kalender
    von, bis = woche_grenzen(woche)
    personen = []
    for p in sichtbare_personen(c, wer):
        key = p["key"]
        tag = aufgaben.mein_tag(c, key)
        zahlen = [(KPI[k]["label"], wert(c, k, woche, key), KPI[k]["einheit"])
                  for k in ("qualifizierte_leads", "anteil_angenommen", "termine_gebucht", "kontakte",
                            "erstgespraeche", "aufgaben_erledigt")]
        personen.append({
            "person": p, "zahlen": [z for z in zahlen if z[1] not in (None, 0)],
            "ziele": ziele_der_woche(c, woche, key), "naechste_ziele": ziele_der_woche(c, woche_plus(woche, 1), key),
            "ueberfaellig": tag["ueberfaellig"], "anfragen": tag["anfragen"],
            "ergebnisse": kalender.offene_ergebnisse(c, key),
            "pruefungen": c.execute("SELECT COUNT(*) FROM freigaben WHERE status='offen' AND pruefer=?", (key,)
                                    ).fetchone()[0],
            "lernstand": lernstand(c, key) if p.get("lernstufe") else None,
        })
    team = [zeile(c, k, woche) for k in ("qualifizierte_leads", "kontakte", "termine_gebucht", "erstgespraeche",
                                         "preisgespraeche", "neue_kunden", "ki_kosten")]
    offen = {r["art"]: r["n"] for r in c.execute("SELECT art, COUNT(*) n FROM freigaben WHERE status='offen' GROUP BY art")}
    return {"woche": woche, "text": woche_text(woche), "personen": personen, "team": team, "offen": offen,
            "aufstiege": aufstiege(c), "von": von, "bis": bis}
