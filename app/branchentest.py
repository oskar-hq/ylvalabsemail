"""Branchentest (Firmen-OS, Stufe 2): Testbranchen, warme und kalte Leads, Lead-Prüfung als Freigabe, Auswertung.

Regeln aus docs/ROLLEN-UND-ABLAEUFE.md, alle in der App änderbar (Branchentest → Regeln):
- Vier Testbranchen mit je 50 Betrieben, jede mit einer Person aus der Recherche. Warme Kontakte (Oskars
  Bekannte) werden markiert und zählen nicht mit.
- Wer einen Lead in den Test bringt, ist die recherchierende Person. Je nach Lernstufe wird der Lead geprüft:
  Stufe 1 jeder Lead, Stufe 2 jeder 5., Stufe 3 jeder 10., Stufe 4 keiner (Quoten einstellbar).
- Prüfen darf die verantwortliche Person der Akquise; ist das die recherchierende Person selbst, die verantwortliche
  Person des übergeordneten Bereichs (Omid → Hamza, Hamza → Oskar). Von den Leads, die die Erstprüfung bestehen,
  geht jeder 5. zusätzlich als Stichprobe an die nächste Ebene (Omid → Hamza → Stichprobe Oskar).
- Ein abgelehnter Lead wird aussortiert, die recherchierende Person bekommt den Grund. Der Kontakt wartet, bis die
  Prüfung durch ist.
- Ausgewertet wird je Branche nur kalt: Kontakte, Antworten, Gespräche, ernsthafte Preisgespräche, Abschlüsse.
  Ampel ab 3 ernsthaften Preisgesprächen (einstellbar).
"""
import json
import re
from datetime import date, datetime, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS testbranchen (
    key TEXT PRIMARY KEY, name TEXT NOT NULL, beschreibung TEXT, nischen TEXT NOT NULL DEFAULT '[]',
    person TEXT, ziel INTEGER NOT NULL DEFAULT 50, aktiv INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS freigaben (
    id INTEGER PRIMARY KEY AUTOINCREMENT, art TEXT NOT NULL, objekt_typ TEXT, objekt_id INTEGER, stufe TEXT,
    vorschlag TEXT, angefragt_von TEXT, angefragt_am TEXT NOT NULL, pruefer TEXT,
    status TEXT NOT NULL DEFAULT 'offen', grund TEXT, entschieden_von TEXT, entschieden_am TEXT, geaendert TEXT
);
CREATE INDEX IF NOT EXISTS freigaben_offen ON freigaben(status, pruefer);
CREATE INDEX IF NOT EXISTS freigaben_objekt ON freigaben(objekt_typ, objekt_id);
"""

# Startwerte aus ROLLEN-UND-ABLAEUFE.md (einmalig, danach in der App gepflegt). Handwerk und Verwaltung gehören
# Omid, den es beim Start noch nicht gibt: dort steht noch niemand, Oskar trägt Omid in der App ein.
SEED = [
    """INSERT OR IGNORE INTO testbranchen (key, name, beschreibung, nischen, person, ziel, sort) VALUES
       ('video', 'Video & Content', 'Nur Fremde: prüft, ob die Piloten mehr waren als Kontakte.', '["video", "foto"]', 'hamza', 50, 1),
       ('design', 'Design & Werbung', 'Ähnlich wie Video, aber ohne Bezug zu Oskars Kontakten.', '["werbeagentur", "marketing", "grafik", "design"]', 'hamza', 50, 2),
       ('handwerk', 'Handwerk (Heizung/Sanitär, Elektro)', 'Viel Büroarbeit.', '["sanitaer", "elektro"]', NULL, 50, 3),
       ('verwaltung', 'Hausverwaltungen und Ingenieurbüros', 'Viel wiederkehrende Büroarbeit.', '["hausverwaltung", "ingenieur"]', NULL, 50, 4)""",
]

REGELN = {  # Schlüssel: (Bezeichnung, Standard, kleinster, größter Wert)
    "stufe2": ("Lernstufe 2: jeder wievielte Lead wird geprüft", 5, 1, 50),
    "stufe3": ("Lernstufe 3: jeder wievielte Lead wird geprüft", 10, 1, 100),
    "stichprobe": ("Stichprobe der nächsten Ebene: jeder wievielte bestandene Lead", 5, 1, 50),
    "preisgespraeche": ("Branche weiterverfolgen ab so vielen ernsthaften Preisgesprächen", 3, 1, 50),
}
KANAELE = [("telefon", "Telefon"), ("brief", "Brief"), ("linkedin", "LinkedIn"), ("vor_ort", "Vor Ort"),
           ("email", "Mail (Antwort auf ihre Mail)")]
KANAL_LABEL = dict(KANAELE)
KONTAKT_STUFEN = ("kontaktiert", "antwort", "gespraech", "angebot", "kunde")


class BranchentestError(ValueError):
    pass


def now():
    return datetime.now().isoformat(timespec="seconds")

# ---------------------------------------------------------------- Regeln


def regeln(c):
    row = c.execute("SELECT value FROM kv WHERE key='regel:branchentest'").fetchone()
    try:
        stored = json.loads(row["value"]) if row else {}
    except ValueError:
        stored = {}
    out = {}
    for k, (_label, default, lo, hi) in REGELN.items():
        try:
            out[k] = max(lo, min(hi, int(stored.get(k, default))))
        except (TypeError, ValueError):
            out[k] = default
    return out


def regeln_speichern(c, wer, form):
    from . import firma
    neu = {}
    for k, (label, _default, lo, hi) in REGELN.items():
        raw = str(form.get(k, "")).strip()
        if not raw.isdigit() or not lo <= int(raw) <= hi:
            raise BranchentestError(f"{label}: bitte eine Zahl von {lo} bis {hi}.")
        neu[k] = int(raw)
    c.execute("INSERT INTO kv (key, value) VALUES ('regel:branchentest', ?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(neu),))
    firma.log(c, wer, "einstellung", "regel", "branchentest", "Regeln für Branchentest und Lead-Prüfung geändert.", neu)

# ---------------------------------------------------------------- Testbranchen


def branchen(c, nur_aktiv=False):
    rows = c.execute("SELECT b.*, t.name AS person_name FROM testbranchen b LEFT JOIN team t ON t.key = b.person "
                     f"{'WHERE b.aktiv = 1' if nur_aktiv else ''} ORDER BY b.aktiv DESC, b.sort, b.name").fetchall()
    return [dict(r, nischen=json.loads(r["nischen"] or "[]")) for r in rows]


def branche(c, key):
    row = c.execute("SELECT b.*, t.name AS person_name FROM testbranchen b LEFT JOIN team t ON t.key = b.person "
                    "WHERE b.key=?", (key or "",)).fetchone()
    return dict(row, nischen=json.loads(row["nischen"] or "[]")) if row else None


def branche_speichern(c, wer, key, name, nischen, person=None, ziel=50, beschreibung="", aktiv=True):
    from . import firma, niches
    name = re.sub(r"\s+", " ", name or "").strip()
    if not name or len(name) > 80:
        raise BranchentestError("Bitte einen Namen (höchstens 80 Zeichen).")
    nischen = [n for n in nischen if n in niches.NICHES]
    if person and not firma.aktive_person(c, person):
        raise BranchentestError("Diese Person ist nicht aktiv.")
    try:
        ziel = int(ziel)
    except (TypeError, ValueError) as exc:
        raise BranchentestError("Ziel bitte als Zahl.") from exc
    if not 1 <= ziel <= 1000:
        raise BranchentestError("Ziel bitte zwischen 1 und 1000 Betrieben.")
    neu = not key
    if neu:
        key = firma.slug(name, {r["key"] for r in c.execute("SELECT key FROM testbranchen")})
        sort = (c.execute("SELECT MAX(sort) FROM testbranchen").fetchone()[0] or 0) + 1
        c.execute("INSERT INTO testbranchen (key, name, beschreibung, nischen, person, ziel, aktiv, sort) "
                  "VALUES (?,?,?,?,?,?,?,?)", (key, name, (beschreibung or "").strip()[:500], json.dumps(nischen),
                                               person or None, ziel, 1 if aktiv else 0, sort))
    else:
        if not branche(c, key):
            raise BranchentestError("Diese Testbranche gibt es nicht.")
        c.execute("UPDATE testbranchen SET name=?, beschreibung=?, nischen=?, person=?, ziel=?, aktiv=? WHERE key=?",
                  (name, (beschreibung or "").strip()[:500], json.dumps(nischen), person or None, ziel,
                   1 if aktiv else 0, key))
    firma.log(c, wer, "branchentest", "testbranche", key, f"Testbranche „{name}“ {'angelegt' if neu else 'geändert'}.",
              {"nischen": nischen, "person": person, "ziel": ziel, "aktiv": bool(aktiv)})
    return key


def branche_fuer_lead(c, lead):
    """Passende aktive Testbranche nach der Nische des Leads (oder seiner Kategorie)."""
    from . import niches
    nische = lead.get("niche") or niches.of_category(lead.get("category") or "")
    if not nische:
        return None
    for b in branchen(c, nur_aktiv=True):
        if nische in b["nischen"]:
            return b["key"]
    return None

# ---------------------------------------------------------------- Lead in den Test, Prüfung als Freigabe


def _bereich_kette(c, bereich_key="akquise"):
    """Verantwortliche Personen von unten nach oben (Akquise → Markt …), ohne Doppelte."""
    from . import firma
    kette, key, gesehen = [], bereich_key, set()
    while key and key not in gesehen:
        gesehen.add(key)
        b = firma.bereich(c, key)
        if not b:
            break
        if b["verantwortlich"] and b["verantwortlich"] not in kette:
            kette.append(b["verantwortlich"])
        key = b["eltern"]
    return kette


def pruefer_fuer(c, person):
    """Wer prüft die Leads dieser Person, und wer nimmt danach Stichproben? Kette der Verantwortlichen vom Bereich
    der Akquise (Einstellungen → Bereiche, Wiedervorlagen) nach oben, ab der Ebene über der Person."""
    from . import aufgaben
    kette = _bereich_kette(c, aufgaben.wiedervorlage_bereich(c))
    if person in kette:
        kette = kette[kette.index(person) + 1:]
    return (kette[0] if kette else None), (kette[1] if len(kette) > 1 else None)


def _muss_geprueft(c, person):
    """Nach Lernstufe der recherchierenden Person: jeder, jeder n-te oder kein Lead."""
    from . import firma
    p = firma.person(c, person)
    stufe = (p or {}).get("lernstufe")
    if not stufe or stufe >= 4:
        return False
    if stufe == 1:
        return True
    n = regeln(c)["stufe2" if stufe == 2 else "stufe3"]
    bisher = c.execute("SELECT COUNT(*) FROM leads WHERE recherche_von=?", (person,)).fetchone()[0]
    return bisher % n == 0  # der 1., (n+1)., … Lead dieser Person


def offene_pruefung(c, lead_id):
    row = c.execute("SELECT * FROM freigaben WHERE art='lead_pruefung' AND objekt_typ='lead' AND objekt_id=? "
                    "AND status='offen' ORDER BY id LIMIT 1", (lead_id,)).fetchone()
    return dict(row) if row else None


def eingang(c, lead_id, wer, testbranche=None, herkunft=None):
    """Ein Lead kommt in die Recherche-Pipeline (beim Prüfen als gut markiert, von Hand angelegt, in den Test
    übernommen). Merkt die recherchierende Person, ordnet die Testbranche zu und legt, falls die Lernstufe es
    verlangt, eine Prüfung an. Mehrfacher Aufruf schadet nicht."""
    from . import firma, leaddb
    lead = leaddb.get_lead(c, lead_id)
    if not lead:
        raise BranchentestError("Diesen Lead gibt es nicht.")
    felder = {}
    neu_recherchiert = not lead.get("recherche_von") and wer and firma.aktive_person(c, wer)
    if neu_recherchiert:
        felder["recherche_von"] = wer
    tb = testbranche or lead.get("testbranche") or branche_fuer_lead(c, lead)
    if tb and tb != lead.get("testbranche"):
        felder["testbranche"] = tb
    if herkunft in ("warm", "kalt"):
        felder["herkunft"] = herkunft
    pruefung = None
    if neu_recherchiert and not lead.get("pruef_status") and _muss_geprueft(c, wer):
        erst, _stich = pruefer_fuer(c, wer)
        if erst:
            felder["pruef_status"] = "wartet"
            pruefung = (erst, "erst")
    if felder:
        leaddb.update_lead(c, lead_id, **felder)
    if pruefung:
        _pruefung_anlegen(c, lead, wer, *pruefung)
    if felder.get("testbranche"):
        name = (branche(c, felder["testbranche"]) or {}).get("name", felder["testbranche"])
        leaddb.add_event(c, lead_id, "status", f"Im Branchentest: {name}.", author=firma.name_von(c, wer) or "Ablauf")
    return felder


def _pruefung_anlegen(c, lead, von, pruefer, stufe):
    from . import aufgaben, firma, leaddb
    c.execute("INSERT INTO freigaben (art, objekt_typ, objekt_id, stufe, angefragt_von, angefragt_am, pruefer) "
              "VALUES ('lead_pruefung', 'lead', ?, ?, ?, ?, ?)", (lead["id"], stufe, von, now(), pruefer))
    text = ("Stichprobe" if stufe == "stichprobe" else "Prüfung") + f" angefragt bei {firma.name_von(c, pruefer)}."
    leaddb.add_event(c, lead["id"], "status", text, author=firma.name_von(c, von) or "Ablauf")
    if stufe == "erst":
        aufgaben.melden(c, pruefer, f"Lead prüfen: {lead['name']}", f"Von {firma.name_von(c, von)}.", "/branchentest/pruefen")


def pruefungen(c, pruefer=None, status="offen", limit=200):
    where, args = ["f.art='lead_pruefung'"], []
    if status:
        where.append("f.status=?")
        args.append(status)
    if pruefer:
        where.append("f.pruefer=?")
        args.append(pruefer)
    rows = c.execute(f"""SELECT f.*, l.name, l.category_label, l.city, l.website, l.phone, l.fit_score, l.fit_reason,
                                l.testbranche, l.herkunft, b.name AS branche_name, t.name AS von_name,
                                p.name AS pruefer_name
                         FROM freigaben f JOIN leads l ON l.id = f.objekt_id
                         LEFT JOIN testbranchen b ON b.key = l.testbranche LEFT JOIN team t ON t.key = f.angefragt_von
                         LEFT JOIN team p ON p.key = f.pruefer
                         WHERE {' AND '.join(where)} ORDER BY f.stufe = 'stichprobe', f.id LIMIT ?""", [*args, limit])
    return [dict(r) for r in rows]


def darf_entscheiden(c, wer, freigabe):
    from . import firma
    return wer == freigabe["pruefer"] or firma.darf(c, wer, "leads", firma.VERWALTEN)


def entscheiden(c, wer, freigabe_id, angenommen, grund=""):
    """Prüfung annehmen oder ablehnen (mit Grund). Abgelehnt: Lead wird aussortiert, Bescheid an die
    recherchierende Person (und bei einer Stichprobe auch an die Erstprüfung). Angenommen: Kontakt frei; jeder
    n-te bestandene Lead geht als Stichprobe an die nächste Ebene."""
    from . import aufgaben, crm, firma, leaddb
    row = c.execute("SELECT * FROM freigaben WHERE id=? AND art='lead_pruefung'", (freigabe_id,)).fetchone()
    if not row or row["status"] != "offen":
        raise BranchentestError("Diese Prüfung ist schon entschieden oder gibt es nicht.")
    f = dict(row)
    grund = (grund or "").strip()
    if not angenommen and len(grund) < 3:
        raise BranchentestError("Bitte einen Grund angeben. Daraus lernt die recherchierende Person.")
    lead = leaddb.get_lead(c, f["objekt_id"])
    status = "angenommen" if angenommen else "abgelehnt"
    c.execute("UPDATE freigaben SET status=?, grund=?, entschieden_von=?, entschieden_am=? WHERE id=?",
              (status, grund[:1000] or None, wer, now(), freigabe_id))
    firma.log(c, wer, "pruefung", "lead", lead["id"], f"Lead {status}: {lead['name']}",
              {"stufe": f["stufe"], "grund": grund[:1000] or None, "recherche_von": f["angefragt_von"]}, commit=False)
    c.commit()
    name = firma.name_von(c, wer)
    if angenommen:
        if f["stufe"] == "erst":
            leaddb.update_lead(c, lead["id"], pruef_status="angenommen")
        leaddb.add_event(c, lead["id"], "status", ("Stichprobe" if f["stufe"] == "stichprobe" else "Prüfung")
                         + f" bestanden ({name}).", author=name)
        if f["stufe"] == "erst":
            _vielleicht_stichprobe(c, lead, f)
    else:
        leaddb.update_lead(c, lead["id"], pruef_status="abgelehnt", status="aussortiert", rejected_by="pruefung",
                           unread=0)
        if lead["stage"] in (None, "neu"):
            leaddb.update_lead(c, lead["id"], stage=None, stage_at=leaddb.now())
        else:
            crm.advance(c, lead["id"], "verloren", author=name)
        leaddb.add_event(c, lead["id"], "status", f"Bei der {'Stichprobe' if f['stufe'] == 'stichprobe' else 'Prüfung'}"
                         f" abgelehnt ({name}): {grund}", author=name)
        leaddb.add_signal(c, "aussortiert", f"Bei der Lead-Prüfung abgelehnt: {lead['name']} ({lead['branche']} / "
                          f"{lead['category_label']}). Grund: {grund}", lead["id"])
        empfaenger = {lead.get("recherche_von") or f["angefragt_von"]}
        if f["stufe"] == "stichprobe":
            erst = c.execute("SELECT entschieden_von FROM freigaben WHERE art='lead_pruefung' AND objekt_id=? "
                             "AND stufe='erst' AND status='angenommen' ORDER BY id DESC LIMIT 1",
                             (lead["id"],)).fetchone()
            if erst:
                empfaenger.add(erst["entschieden_von"])
        for p in empfaenger - {wer, None}:
            aufgaben.melden(c, p, f"Lead abgelehnt: {lead['name']}", f"{name}: {grund[:140]}", f"/leads/{lead['id']}")
    return status


def _vielleicht_stichprobe(c, lead, erst):
    """Jeder n-te Lead, den die Erstprüfung durchlässt, geht als Stichprobe an die nächste Ebene."""
    _erst, stich = pruefer_fuer(c, erst["angefragt_von"])
    if not stich or stich == erst["pruefer"]:
        return
    n = regeln(c)["stichprobe"]
    bestanden = c.execute("SELECT COUNT(*) FROM freigaben WHERE art='lead_pruefung' AND stufe='erst' "
                          "AND status='angenommen' AND pruefer=?", (erst["pruefer"],)).fetchone()[0]
    if bestanden % n == 0:
        _pruefung_anlegen(c, lead, erst["pruefer"], stich, "stichprobe")


def kontakt_frei(lead):
    """Darf der Betrieb kontaktiert werden? Nicht, solange die Prüfung aussteht oder sie abgelehnt wurde."""
    return lead.get("pruef_status") not in ("wartet", "abgelehnt")

# ---------------------------------------------------------------- Kontakte und Preisgespräche


def kontakt(c, wer, lead_id, kanal, notiz="", mail_erlaubt=False):
    """Einen Kontakt mit einem Klick protokollieren (Telefon, Brief, LinkedIn, vor Ort …). Schiebt auf
    „Kontaktiert“. Mail als Erstkontakt nur, wenn er eingeschaltet ist (Einstellungen → Postfach → Versand);
    sonst gilt „Mail“ nur als Antwort auf eine Mail des Betriebs."""
    from . import crm, firma, leaddb
    if kanal not in KANAL_LABEL:
        raise BranchentestError("Bitte einen Kontaktweg wählen.")
    lead = leaddb.get_lead(c, lead_id)
    if not lead:
        raise BranchentestError("Diesen Lead gibt es nicht.")
    if not kontakt_frei(lead):
        raise BranchentestError("Erst die Lead-Prüfung abwarten." if lead.get("pruef_status") == "wartet" else
                                "Dieser Lead wurde bei der Prüfung abgelehnt.")
    if kanal == "email" and not mail_erlaubt and not lead.get("replied_at") and lead.get("stage") in (None, "neu", "kontaktiert"):
        raise BranchentestError("Erstkontakt per Mail ist ausgeschaltet (Einstellungen → Postfach → Versand). "
                                "Mail nur als Antwort auf eine Mail des Betriebs.")
    notiz = (notiz or "").strip()[:500]
    felder = {"unread": 0, "channel": lead.get("channel") or kanal}
    if not lead.get("sent_at"):
        felder["sent_at"] = leaddb.now()
    if lead["status"] in ("kandidat", "passt", "entwurf"):
        felder["status"] = "versendet"
    leaddb.update_lead(c, lead_id, **felder)
    leaddb.add_event(c, lead_id, "kontakt", f"Kontakt: {KANAL_LABEL[kanal]}." + (f" {notiz}" if notiz else ""),
                     author=firma.name_von(c, wer) or wer, meta={"kanal": kanal})
    crm.advance(c, lead_id, "kontaktiert", author=firma.name_von(c, wer) or wer)
    crm.claim(c, lead_id, wer)


def preisgespraech(c, wer, lead_id, an=True):
    """Ernsthaftes Preisgespräch markieren: der Betrieb hat über einen konkreten Preis gesprochen."""
    from . import firma, leaddb
    lead = leaddb.get_lead(c, lead_id)
    if not lead:
        raise BranchentestError("Diesen Lead gibt es nicht.")
    if bool(lead.get("preisgespraech_am")) == an:
        return False
    leaddb.update_lead(c, lead_id, preisgespraech_am=leaddb.now() if an else None)
    leaddb.add_event(c, lead_id, "status", "Ernsthaftes Preisgespräch." if an else "Kein ernsthaftes Preisgespräch "
                     "(zurückgenommen).", author=firma.name_von(c, wer) or wer)
    return True


def herkunft_setzen(c, wer, lead_id, herkunft, testbranche=None):
    from . import firma, leaddb
    lead = leaddb.get_lead(c, lead_id)
    if not lead:
        raise BranchentestError("Diesen Lead gibt es nicht.")
    if herkunft not in ("warm", "kalt"):
        raise BranchentestError("Herkunft ist warm oder kalt.")
    if testbranche and not branche(c, testbranche):
        raise BranchentestError("Diese Testbranche gibt es nicht.")
    felder = {k: v for k, v in {"herkunft": herkunft, "testbranche": testbranche or None}.items()
              if lead.get(k) != v}
    if not felder:
        return False
    leaddb.update_lead(c, lead_id, **felder)
    tb = branche(c, testbranche) if testbranche else None
    leaddb.add_event(c, lead_id, "status", f"Branchentest: {tb['name'] if tb else 'keine Testbranche'}, "
                     f"{'warm (zählt nicht mit)' if herkunft == 'warm' else 'kalt'}.", author=firma.name_von(c, wer) or wer)
    return True

# ---------------------------------------------------------------- Auswertung


ZAHLEN_SQL = """
SELECT l.testbranche AS key, IFNULL(l.herkunft, 'kalt') AS herkunft,
       COUNT(*) AS betriebe,
       SUM(l.pruef_status = 'wartet') AS in_pruefung,
       SUM(l.sent_at IS NOT NULL OR l.stage IN ('kontaktiert','antwort','gespraech','angebot','kunde')) AS kontakte,
       SUM(l.replied_at IS NOT NULL OR l.stage IN ('antwort','gespraech','angebot','kunde')) AS antworten,
       SUM(l.stage IN ('gespraech','angebot','kunde') OR l.preisgespraech_am IS NOT NULL) AS gespraeche,
       SUM(l.preisgespraech_am IS NOT NULL) AS preisgespraeche,
       SUM(l.stage = 'kunde') AS abschluesse
FROM leads l
WHERE l.testbranche IS NOT NULL AND IFNULL(l.pruef_status, '') != 'abgelehnt'
  AND NOT (l.status = 'aussortiert' AND l.stage IS NULL)
GROUP BY l.testbranche, IFNULL(l.herkunft, 'kalt')
"""
KENNZAHLEN = [("kontakte", "Kontakte"), ("antworten", "Antworten"), ("gespraeche", "Gespräche"),
              ("preisgespraeche", "Preisgespräche"), ("abschluesse", "Abschlüsse")]


def auswertung(c):
    """Je Testbranche die Zahlen (nur kalt) plus warm getrennt, Fortschritt zum Ziel und Ampel."""
    leer = {k: 0 for k in ("betriebe", "in_pruefung", *dict(KENNZAHLEN))}
    zahlen = {}
    for r in c.execute(ZAHLEN_SQL):
        zahlen.setdefault(r["key"], {"kalt": dict(leer), "warm": dict(leer)})[r["herkunft"]] = \
            {k: r[k] or 0 for k in leer}
    schwelle = regeln(c)["preisgespraeche"]
    out = []
    for b in branchen(c):
        z = zahlen.get(b["key"], {"kalt": dict(leer), "warm": dict(leer)})
        kalt = z["kalt"]
        quote = round(100 * kalt["antworten"] / kalt["kontakte"]) if kalt["kontakte"] else None
        p = kalt["preisgespraeche"]
        ampel = "gruen" if p >= schwelle else ("gelb" if p else "grau")
        out.append(dict(b, kalt=kalt, warm=z["warm"], antwortquote=quote, ampel=ampel, schwelle=schwelle,
                        fortschritt=min(100, round(100 * kalt["betriebe"] / b["ziel"])) if b["ziel"] else 0))
    return out


def leads_der_branche(c, key):
    rows = c.execute("""SELECT l.id, l.name, l.city, l.stage, l.status, l.herkunft, l.pruef_status, l.preisgespraech_am,
                               l.sent_at, l.replied_at, l.recherche_von, t.name AS recherche_name, l.owner
                        FROM leads l LEFT JOIN team t ON t.key = l.recherche_von
                        WHERE l.testbranche = ? ORDER BY IFNULL(l.herkunft,'kalt') = 'warm', l.pruef_status = 'abgelehnt',
                              l.stage IS NULL, l.name""", (key,)).fetchall()
    return [dict(r) for r in rows]


def kandidaten(c, key, limit=50):
    """Passende Betriebe, die noch keiner Testbranche gehören (nach Nische), die besten zuerst."""
    from . import niches
    b = branche(c, key)
    if not b or not b["nischen"]:
        return []
    rows = c.execute(f"""SELECT id, name, city, fit_score, niche, category FROM leads
                         WHERE testbranche IS NULL AND status IN ('passt','entwurf','versendet','beantwortet')
                           AND IFNULL(interest,'') != 'kein_interesse'
                         ORDER BY fit_score DESC NULLS LAST, id LIMIT 2000""").fetchall()
    out = [dict(r) for r in rows if (r["niche"] or niches.of_category(r["category"] or "")) in b["nischen"]]
    return out[:limit]


def recherche_kpis(c, person, tage=7):
    """Für die Person: angenommene Leads der letzten Woche und Anteil angenommener Leads (alle Erstprüfungen)."""
    seit = (date.today() - timedelta(days=tage)).isoformat()
    r = c.execute("""SELECT SUM(status='angenommen') AS an, SUM(status IN ('angenommen','abgelehnt')) AS alle,
                            SUM(status='angenommen' AND entschieden_am >= ?) AS woche, SUM(status='offen') AS offen
                     FROM freigaben WHERE art='lead_pruefung' AND stufe='erst' AND angefragt_von=?""",
                  (seit, person)).fetchone()
    alle = r["alle"] or 0
    return {"angenommen_woche": r["woche"] or 0, "offen": r["offen"] or 0, "entschieden": alle,
            "anteil": round(100 * (r["an"] or 0) / alle) if alle else None}
