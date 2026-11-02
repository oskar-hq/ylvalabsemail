"""Kalender und Termine (Firmen-OS, Stufe 3).

- Termine im OS: intern, mit Kunden (Lead) oder als Serie (Wochengespräch). Jeder Termin hat eine verantwortliche
  Person und Teilnehmende aus dem Team, Kundentermine dazu Gäste (E-Mail).
- Jede Person verbindet ihren eigenen Google-Kalender. Das OS liest dort nur frei/belegt und trägt Firmentermine
  direkt ein. Kundentermine legt es im Kalender der verantwortlichen Person an; Google erzeugt den Meet-Link und
  verschickt die Einladung.
- Freigabe-Stufe C: Eine Einladung an einen Kunden geht erst raus, wenn die verantwortliche Person sie freigibt.
  Legt sie den Termin selbst an und bestätigt „Einladung senden“, ist das die Freigabe.
- Der Hintergrund-Arbeiter (takt) gleicht frei/belegt ab, erinnert 24 h und 1 h vorher, fragt nach dem Termin
  nach dem Ergebnis und füllt Serien auf.
- ICS-Abo als Rückfall: geheime Adresse je Person, nur lesend, in der App erneuerbar.
"""
import hashlib
import json
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS kalender_konten (
    person TEXT PRIMARY KEY, anbieter TEXT NOT NULL DEFAULT 'google', refresh_token TEXT NOT NULL,
    verbunden_am TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ok', fehler TEXT, abgeglichen_am TEXT
);
CREATE TABLE IF NOT EXISTS termine (
    id INTEGER PRIMARY KEY AUTOINCREMENT, titel TEXT NOT NULL, art TEXT NOT NULL DEFAULT 'intern',
    start TEXT NOT NULL, ende TEXT NOT NULL, ort TEXT, beschreibung TEXT, verantwortlich TEXT NOT NULL,
    lead_id INTEGER, status TEXT NOT NULL DEFAULT 'geplant', meet INTEGER NOT NULL DEFAULT 0, meet_link TEXT,
    serie_id INTEGER, ergebnis TEXT, ergebnis_notiz TEXT, ergebnis_von TEXT, ergebnis_am TEXT,
    briefing_notiz TEXT, erinnert TEXT NOT NULL DEFAULT '', sync_fehler TEXT,
    erstellt_von TEXT, erstellt_am TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS termine_start ON termine(start);
CREATE INDEX IF NOT EXISTS termine_lead ON termine(lead_id);
CREATE TABLE IF NOT EXISTS termin_teilnehmer (
    id INTEGER PRIMARY KEY AUTOINCREMENT, termin_id INTEGER NOT NULL, person TEXT, email TEXT, name TEXT,
    google_event_id TEXT
);
CREATE INDEX IF NOT EXISTS teilnehmer_termin ON termin_teilnehmer(termin_id);
CREATE INDEX IF NOT EXISTS teilnehmer_person ON termin_teilnehmer(person);
CREATE TABLE IF NOT EXISTS belegt (person TEXT NOT NULL, start TEXT NOT NULL, ende TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS belegt_person ON belegt(person, start);
CREATE TABLE IF NOT EXISTS serien (
    id INTEGER PRIMARY KEY AUTOINCREMENT, titel TEXT NOT NULL, wochentag INTEGER NOT NULL, uhrzeit TEXT NOT NULL,
    dauer_min INTEGER NOT NULL, verantwortlich TEXT NOT NULL, teilnehmer TEXT NOT NULL DEFAULT '[]',
    meet INTEGER NOT NULL DEFAULT 0, aktiv INTEGER NOT NULL DEFAULT 1, erstellt_von TEXT, erstellt_am TEXT NOT NULL
);
"""

# Startwert: das Wochengespräch zu viert ist ein Serientermin, angelegt wird er in der App (Kalender → Serien).
REGELN = {  # Schlüssel: (Bezeichnung, Standard, Typ)
    "von": ("Termine frühestens ab (Uhrzeit)", "09:00", "zeit"),
    "bis": ("Termine spätestens bis (Uhrzeit)", "18:00", "zeit"),
    "tage": ("Wochentage für Terminvorschläge", "0,1,2,3,4", "tage"),
    "dauer": ("Standarddauer eines Termins (Minuten)", 45, "int"),
    "puffer": ("Puffer zwischen zwei Terminen (Minuten)", 15, "int"),
    "abgleich": ("Frei/belegt abgleichen alle (Minuten)", 10, "int"),
    "erinnerung_24h": ("Erinnerung 24 Stunden vorher", True, "bool"),
    "erinnerung_1h": ("Erinnerung 1 Stunde vorher", True, "bool"),
    "zeitzone": ("Zeitzone", "Europe/Berlin", "text"),
}
RHYTHMEN = [("taeglich", "Täglich"), ("werktags", "Werktags (Montag bis Freitag)"), ("woche", "Wöchentlich"),
            ("zweiwochen", "Alle zwei Wochen"), ("monat", "Monatlich")]
RHYTHMUS_LABEL = dict(RHYTHMEN)
VORLAUF_TAGE = {"taeglich": 14, "werktags": 14, "woche": 42, "zweiwochen": 56, "monat": 93}  # so weit im Voraus
SPIELRAEUME = [30, 60, 120, 180, 240]  # so weit darf eine flexible Serie von der Wunschzeit abweichen (Minuten)
FRUEHESTENS, SPAETESTENS = "07:00", "22:00"  # Ausweichzeiten nur innerhalb dieses Rahmens
NICHT_MEHR_VERSCHIEBEN = timedelta(hours=2)  # so kurz vorher wird nicht mehr automatisch verschoben
WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
ARTEN = {"intern": "Intern", "kunde": "Kundentermin", "serie": "Serie"}
ERGEBNISSE = [("stattgefunden", "Hat stattgefunden"), ("abgesagt", "Abgesagt"), ("nicht_erschienen", "Nicht erschienen")]
ERGEBNIS_LABEL = dict(ERGEBNISSE)
_EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


class KalenderError(ValueError):
    pass


def now():
    return datetime.now().isoformat(timespec="seconds")

# ---------------------------------------------------------------- Regeln (Einstellungen → Kalender)


def regeln(c):
    row = c.execute("SELECT value FROM kv WHERE key='regel:kalender'").fetchone()
    try:
        stored = json.loads(row["value"]) if row else {}
    except ValueError:
        stored = {}
    out = {}
    for k, (_label, default, typ) in REGELN.items():
        v = stored.get(k, default)
        if typ == "int":
            try:
                v = int(v)
            except (TypeError, ValueError):
                v = default
        out[k] = v
    out["tage_liste"] = [int(t) for t in str(out["tage"]).split(",") if t.strip().isdigit()]
    return out


def regeln_speichern(c, wer, form):
    from . import firma
    neu = {}
    for k, (label, _default, typ) in REGELN.items():
        if typ == "tage":
            raw = ",".join(form.getlist("tage")) if hasattr(form, "getlist") else ",".join(form.get("tage", []))
        else:
            raw = str(form.get(k) or "").strip()
        if typ == "zeit":
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", raw):
                raise KalenderError(f"{label}: bitte als Uhrzeit, z. B. 09:00.")
        elif typ == "int":
            if not raw.isdigit() or not 0 <= int(raw) <= 600:
                raise KalenderError(f"{label}: bitte eine Zahl von 0 bis 600.")
            raw = int(raw)
        elif typ == "bool":
            raw = raw in ("1", "on", "true")
        elif typ == "tage":
            if not raw or any(t not in "0123456" for t in raw.split(",")):
                raise KalenderError("Bitte mindestens einen Wochentag wählen.")
        elif k == "zeitzone":
            try:
                _zone(raw)
            except Exception as exc:
                raise KalenderError("Unbekannte Zeitzone, z. B. Europe/Berlin.") from exc
        neu[k] = raw
    if neu["von"] >= neu["bis"]:
        raise KalenderError("„Ab“ muss vor „bis“ liegen.")
    c.execute("INSERT INTO kv (key, value) VALUES ('regel:kalender', ?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(neu),))
    firma.log(c, wer, "einstellung", "regel", "kalender", "Regeln für Kalender und Termine geändert.", neu)


def _zone(name):
    from zoneinfo import ZoneInfo
    return ZoneInfo(name or "Europe/Berlin")

# ---------------------------------------------------------------- Zeiten


def _dt(value):
    """'2026-11-03T10:00' (lokal) → datetime."""
    try:
        return datetime.fromisoformat((value or "").strip()[:16])
    except ValueError as exc:
        raise KalenderError("Bitte Datum und Uhrzeit angeben.") from exc


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M")


def _utc(dt, zone):
    return dt.replace(tzinfo=_zone(zone)).astimezone(_zone("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lokal(rfc, zone):
    dt = datetime.fromisoformat(rfc.replace("Z", "+00:00"))
    return dt.astimezone(_zone(zone)).replace(tzinfo=None)


def zeitraum_text(t):
    s, e = _dt(t["start"]), _dt(t["ende"])
    from .aufgaben import tag
    return f"{tag(t['start'])}, {s:%H:%M}–{e:%H:%M}"

# ---------------------------------------------------------------- Google-Konten der Personen


def _data_dir():
    return Path(os.environ.get("DATA_DIR") or Path(__file__).resolve().parent.parent / "data")


def _fernet():
    from . import config_store
    return config_store._fernet(_data_dir())


def google_cfg():
    """Client-ID und Clientschlüssel aus den Einstellungen (Anbindungen → Google)."""
    cid, sec = os.environ.get("GOOGLE_CLIENT_ID", "").strip(), os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    return (cid, sec) if cid and sec else None


def konto(c, person):
    row = c.execute("SELECT person, anbieter, verbunden_am, status, fehler, abgeglichen_am FROM kalender_konten "
                    "WHERE person=?", (person or "",)).fetchone()
    return dict(row) if row else None


def konto_speichern(c, person, refresh_token):
    from . import firma
    enc = _fernet().encrypt(refresh_token.encode()).decode()
    c.execute("INSERT INTO kalender_konten (person, refresh_token, verbunden_am, status) VALUES (?,?,?, 'ok') "
              "ON CONFLICT(person) DO UPDATE SET refresh_token=excluded.refresh_token, verbunden_am=excluded.verbunden_am,"
              " status='ok', fehler=NULL", (person, enc, now()))
    firma.log(c, person, "kalender", "person", person, f"{firma.name_von(c, person)} hat den Google-Kalender verbunden.")


def konto_trennen(c, person):
    from . import firma, google_kalender
    row = c.execute("SELECT refresh_token FROM kalender_konten WHERE person=?", (person,)).fetchone()
    if not row:
        return False
    try:
        google_kalender.widerrufen(_fernet().decrypt(row["refresh_token"].encode()).decode())
    except Exception:
        pass
    c.execute("DELETE FROM kalender_konten WHERE person=?", (person,))
    c.execute("DELETE FROM belegt WHERE person=?", (person,))
    firma.log(c, person, "kalender", "person", person, f"{firma.name_von(c, person)} hat den Google-Kalender getrennt.")
    return True


def _token(c, person):
    """Gültiger Zugang für die Person oder None (nicht verbunden, Google nicht eingerichtet, Verbindung abgelaufen)."""
    from . import google_kalender
    cfg = google_cfg()
    row = c.execute("SELECT refresh_token, status FROM kalender_konten WHERE person=?", (person or "",)).fetchone()
    if not cfg or not row or row["status"] == "abgelaufen":
        return None
    try:
        refresh = _fernet().decrypt(row["refresh_token"].encode()).decode()
        return google_kalender.access_token(cfg[0], cfg[1], refresh)
    except google_kalender.GoogleError as exc:
        c.execute("UPDATE kalender_konten SET status=?, fehler=? WHERE person=?",
                  ("abgelaufen" if exc.abgelaufen else "fehler", str(exc)[:300], person))
        c.commit()
        if exc.abgelaufen:
            from . import aufgaben
            aufgaben.melden(c, person, "Google-Kalender neu verbinden", "Die Verbindung gilt nicht mehr.", "/kalender/mein")
        return None
    except Exception:
        return None


def verbunden(c, person):
    k = konto(c, person)
    return bool(k and k["status"] != "abgelaufen" and google_cfg())

# ---------------------------------------------------------------- Termine anlegen, ändern, absagen


def get(c, tid):
    row = c.execute("""SELECT t.*, p.name AS verantwortlich_name, l.name AS lead_name, e.name AS erstellt_von_name
                       FROM termine t LEFT JOIN team p ON p.key = t.verantwortlich
                       LEFT JOIN leads l ON l.id = t.lead_id LEFT JOIN team e ON e.key = t.erstellt_von
                       WHERE t.id=?""", (tid,)).fetchone()
    if not row:
        return None
    t = dict(row)
    t["teilnehmer"] = [dict(r) for r in c.execute(
        "SELECT tt.*, p.name AS person_name FROM termin_teilnehmer tt LEFT JOIN team p ON p.key = tt.person "
        "WHERE termin_id=? ORDER BY tt.person IS NULL, tt.id", (tid,))]
    t["personen"] = [x["person"] for x in t["teilnehmer"] if x["person"]]
    t["gaeste"] = [x["email"] for x in t["teilnehmer"] if not x["person"] and x["email"]]
    t["zeit"] = zeitraum_text(t)
    t["vorbei"] = t["ende"] <= _iso(datetime.now())
    t["laeuft"] = t["start"] <= _iso(datetime.now()) < t["ende"]
    return t


def _gaeste(text):
    out = []
    for e in re.split(r"[,;\s]+", text or ""):
        e = e.strip().lower()
        if not e:
            continue
        if not _EMAIL.match(e):
            raise KalenderError(f"„{e}“ ist keine gültige E-Mail-Adresse.")
        if e not in out:
            out.append(e)
    return out


def anlegen(c, wer, titel, start, ende=None, dauer=None, verantwortlich=None, personen=(), gaeste="", lead_id=None,
            ort="", beschreibung="", meet=False, einladung_freigeben=False, serie_id=None, wunsch_start=None,
            melden=True):
    """Neuer Termin. Mit Gästen ist es ein Kundentermin (Freigabe C): Gibt die verantwortliche Person ihn selbst
    frei, geht er gleich raus; sonst wartet er als Vorschlag auf ihre Freigabe."""
    from . import firma, leaddb
    titel = re.sub(r"\s+", " ", titel or "").strip()
    if not titel or len(titel) > 200:
        raise KalenderError("Bitte einen Titel (höchstens 200 Zeichen).")
    s = _dt(start)
    if ende:
        e = _dt(ende)
    else:
        e = s + timedelta(minutes=int(dauer or regeln(c)["dauer"]))
    if e <= s:
        raise KalenderError("Das Ende muss nach dem Anfang liegen.")
    if e - s > timedelta(hours=12):
        raise KalenderError("Ein Termin dauert höchstens 12 Stunden.")
    verantwortlich = verantwortlich or wer
    if not firma.aktive_person(c, verantwortlich):
        raise KalenderError("Bitte eine aktive verantwortliche Person wählen.")
    personen = [p for p in dict.fromkeys([verantwortlich, *personen]) if p]
    for p in personen:
        if not firma.aktive_person(c, p):
            raise KalenderError("Eine der Personen ist nicht (mehr) aktiv.")
    gaeste = _gaeste(gaeste) if isinstance(gaeste, str) else list(gaeste)
    lead = leaddb.get_lead(c, lead_id) if lead_id else None
    if lead_id and not lead:
        raise KalenderError("Diesen Lead gibt es nicht.")
    from . import branchentest
    if lead and not branchentest.kontakt_frei(lead):
        raise KalenderError("Erst die Lead-Prüfung abwarten, dann einen Termin vereinbaren.")
    art = "serie" if serie_id else ("kunde" if gaeste or lead else "intern")
    status = "geplant"
    from . import freigaben
    stufe = freigaben.stufe(c, "termin_einladung") if gaeste else "A"  # Einstellungen → Freigabe-Stufen
    if gaeste and stufe != "A" and not (einladung_freigeben and wer == verantwortlich):
        status = "vorschlag"
    cur = c.execute("""INSERT INTO termine (titel, art, start, ende, ort, beschreibung, verantwortlich, lead_id, status,
                       meet, serie_id, wunsch_start, erstellt_von, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (titel, art, _iso(s), _iso(e), (ort or "").strip()[:200] or None,
                     (beschreibung or "").strip()[:4000] or None, verantwortlich, lead_id, status, 1 if meet else 0,
                     serie_id, wunsch_start, wer, now()))
    tid = cur.lastrowid
    for p in personen:
        c.execute("INSERT INTO termin_teilnehmer (termin_id, person) VALUES (?,?)", (tid, p))
    for g in gaeste:
        c.execute("INSERT INTO termin_teilnehmer (termin_id, email, name) VALUES (?,?,?)",
                  (tid, g, (lead or {}).get("contact_name")))
    firma.log(c, wer, "termin", "termin", tid, f"Termin angelegt: {titel} ({_iso(s).replace('T', ' ')})",
              {"art": art, "status": status, "personen": personen, "gaeste": len(gaeste), "lead": lead_id},
              commit=False)
    c.commit()
    if lead:
        leaddb.add_event(c, lead_id, "termin", f"Termin {'vorgeschlagen' if status == 'vorschlag' else 'geplant'}: "
                         f"{titel}, {zeitraum_text(get(c, tid))}.", author=firma.name_von(c, wer) or wer)
    if status == "vorschlag":
        _freigabe_anfragen(c, wer, tid, stufe)
    else:
        sync(c, tid, einladen=bool(gaeste))
        if melden:
            _melden_neu(c, wer, tid)
    return tid


def _melden_neu(c, wer, tid):
    from . import aufgaben, firma
    t = get(c, tid)
    for p in t["personen"]:
        if p != wer:
            aufgaben.melden(c, p, f"Neuer Termin: {t['titel']}", f"{t['zeit']} · von {firma.name_von(c, wer)}",
                            f"/kalender/{tid}")


def _freigabe_anfragen(c, wer, tid, stufe="C"):
    """Stufe C: wartet auf die verantwortliche Person. Stufe B: geht nach der Einspruchsfrist von selbst raus."""
    from . import aufgaben, firma, freigaben
    t = get(c, tid)
    if stufe == "B":
        _, frist = freigaben.b_anlegen(c, wer, "termin_einladung", "termin", tid, t["verantwortlich"],
                                       {"gaeste": t["gaeste"]})
        aufgaben.melden(c, t["verantwortlich"], f"Einladung geht raus: {t['titel']}",
                        f"{t['zeit']} · an {', '.join(t['gaeste'])} · automatisch am {frist[:16].replace('T', ' um ')}"
                        ", wenn niemand stoppt", "/freigaben", push=True)
        return
    c.execute("INSERT INTO freigaben (art, objekt_typ, objekt_id, stufe, angefragt_von, angefragt_am, pruefer, vorschlag)"
              " VALUES ('termin_einladung', 'termin', ?, 'C', ?, ?, ?, ?)",
              (tid, wer, now(), t["verantwortlich"], json.dumps({"gaeste": t["gaeste"]})))
    c.commit()
    aufgaben.melden(c, t["verantwortlich"], f"Einladung freigeben: {t['titel']}",
                    f"{t['zeit']} · an {', '.join(t['gaeste'])} · von {firma.name_von(c, wer)}", f"/kalender/{tid}",
                    push=True)


def offene_freigabe(c, tid):
    row = c.execute("SELECT * FROM freigaben WHERE art='termin_einladung' AND objekt_id=? AND status='offen' "
                    "ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
    return dict(row) if row else None


def freigaben_fuer(c, person):
    rows = c.execute("SELECT objekt_id FROM freigaben WHERE art='termin_einladung' AND status='offen' AND pruefer=? "
                     "ORDER BY id", (person,)).fetchall()
    return [get(c, r["objekt_id"]) for r in rows if get(c, r["objekt_id"])]


def freigeben(c, wer, tid, ok, grund=""):
    """Einladung an den Kunden freigeben (geht über Google raus) oder verwerfen (mit Grund)."""
    from . import aufgaben, firma, leaddb
    t = get(c, tid)
    f = offene_freigabe(c, tid)
    if not t or not f or t["status"] == "abgesagt":
        raise KalenderError("Hier wartet keine Einladung auf Freigabe.")
    system = wer == "System" and f["stufe"] == "B"  # Stufe B: Frist abgelaufen, niemand hat gestoppt
    if not system and wer != f["pruefer"] and not firma.darf(c, wer, "kalender", firma.VERWALTEN):
        raise KalenderError("Freigeben darf nur die verantwortliche Person.")
    grund = (grund or "").strip()
    if not ok and len(grund) < 3:
        raise KalenderError("Bitte einen Grund angeben.")
    c.execute("UPDATE freigaben SET status=?, grund=?, entschieden_von=?, entschieden_am=? WHERE id=?",
              ("angenommen" if ok else "abgelehnt", grund or None, wer, now(), f["id"]))
    c.execute("UPDATE termine SET status=? WHERE id=?", ("geplant" if ok else "abgesagt", tid))
    firma.log(c, wer, "freigabe", "termin", tid, f"Einladung {'freigegeben' if ok else 'verworfen'}: {t['titel']}"
              + (" (Stufe B, Frist ohne Einspruch abgelaufen)" if system else ""),
              {"grund": grund or None}, commit=False)
    c.commit()
    if t["lead_id"]:
        leaddb.add_event(c, t["lead_id"], "termin", f"Einladung {'freigegeben und verschickt' if ok else 'verworfen'}: "
                         f"{t['titel']}." + (f" {grund}" if grund else ""), author=firma.name_von(c, wer))
    if ok:
        sync(c, tid, einladen=True)
        _melden_neu(c, wer, tid)
    if f["angefragt_von"] and f["angefragt_von"] != wer:
        aufgaben.melden(c, f["angefragt_von"], f"Einladung {'raus' if ok else 'verworfen'}: {t['titel']}",
                        grund or t["zeit"], f"/kalender/{tid}")
    return get(c, tid)


def aendern(c, wer, tid, titel=None, start=None, ende=None, ort=None, beschreibung=None, meldung=None):
    """Termin ändern. `meldung` (z. B. beim automatischen Verschieben einer Serie) geht als Push an alle."""
    from . import firma
    t = get(c, tid)
    if not t or t["status"] == "abgesagt":
        raise KalenderError("Diesen Termin gibt es nicht (mehr).")
    neu = {}
    if titel is not None:
        titel = re.sub(r"\s+", " ", titel).strip()
        if not titel:
            raise KalenderError("Bitte einen Titel.")
        neu["titel"] = titel[:200]
    if start:
        s = _dt(start)
        e = _dt(ende) if ende else s + (_dt(t["ende"]) - _dt(t["start"]))
        if e <= s:
            raise KalenderError("Das Ende muss nach dem Anfang liegen.")
        neu.update(start=_iso(s), ende=_iso(e), erinnert="")
    if ort is not None:
        neu["ort"] = ort.strip()[:200] or None
    if beschreibung is not None:
        neu["beschreibung"] = beschreibung.strip()[:4000] or None
    neu = {k: v for k, v in neu.items() if t.get(k) != v}
    if not neu:
        return False
    c.execute(f"UPDATE termine SET {', '.join(k + '=?' for k in neu)} WHERE id=?", [*neu.values(), tid])
    firma.log(c, wer, "termin", "termin", tid, f"Termin geändert: {neu.get('titel', t['titel'])}",
              {k: v for k, v in neu.items() if k != "beschreibung"}, commit=False)
    c.commit()
    if t["status"] == "geplant":
        sync(c, tid, einladen=bool(t["gaeste"]))
    from . import aufgaben
    for p in t["personen"]:
        if meldung:
            aufgaben.melden(c, p, f"Verschoben: {neu.get('titel', t['titel'])}", meldung, f"/kalender/{tid}", push=True)
        elif p != wer:
            aufgaben.melden(c, p, f"Termin geändert: {neu.get('titel', t['titel'])}", get(c, tid)["zeit"],
                            f"/kalender/{tid}")
    return True


def absagen(c, wer, tid, grund=""):
    from . import aufgaben, firma, google_kalender, leaddb
    t = get(c, tid)
    if not t or t["status"] == "abgesagt":
        raise KalenderError("Dieser Termin ist schon abgesagt.")
    for x in t["teilnehmer"]:
        if x["person"] and x["google_event_id"]:
            token = _token(c, x["person"])
            if token:
                try:
                    google_kalender.event_loeschen(token, x["google_event_id"],
                                                   einladen=x["person"] == t["verantwortlich"] and bool(t["gaeste"]),
                                                   kalender_id=x.get("google_kalender_id") or "primary")
                except google_kalender.GoogleError as exc:
                    c.execute("UPDATE termine SET sync_fehler=? WHERE id=?", (str(exc)[:300], tid))
    c.execute("UPDATE termine SET status='abgesagt' WHERE id=?", (tid,))
    c.execute("UPDATE freigaben SET status='hinfaellig' WHERE art='termin_einladung' AND objekt_id=? AND status='offen'",
              (tid,))
    firma.log(c, wer, "termin", "termin", tid, f"Termin abgesagt: {t['titel']}", {"grund": grund or None}, commit=False)
    c.commit()
    if t["lead_id"]:
        leaddb.add_event(c, t["lead_id"], "termin", f"Termin abgesagt: {t['titel']}." + (f" {grund}" if grund else ""),
                         author=firma.name_von(c, wer))
    for p in t["personen"]:
        if p != wer:
            aufgaben.melden(c, p, f"Abgesagt: {t['titel']}", grund or t["zeit"], f"/kalender/{tid}")

# ---------------------------------------------------------------- Abgleich mit Google


def _google_text(c, t):
    """Beschreibung im Google-Termin: Notiz, Kunde und der Link zurück ins OS."""
    teile = [t.get("beschreibung") or ""]
    if t.get("lead_name") and not t["gaeste"]:
        teile.append(f"Kunde: {t['lead_name']}")
    base = os.environ.get("BASE_URL", "").rstrip("/")
    if base:
        teile.append(f"Im OS: {base}/kalender/{t['id']}")
    return "\n\n".join(x for x in teile if x)


def sync(c, tid, einladen=False):
    """Termin in die Google-Kalender der Teilnehmenden eintragen bzw. ändern. Kunden-Gäste und Meet nur im Kalender
    der verantwortlichen Person (Google schickt dort die Einladung). Fehler werden am Termin vermerkt."""
    from . import google_kalender
    t = get(c, tid)
    if not t or t["status"] != "geplant":
        return
    zone = regeln(c)["zeitzone"]
    fehler = []
    reihenfolge = sorted([x for x in t["teilnehmer"] if x["person"]], key=lambda x: x["person"] != t["verantwortlich"])
    for x in reihenfolge:
        haupt = x["person"] == t["verantwortlich"]
        token = _token(c, x["person"])
        if not token:
            if haupt and (t["gaeste"] or t["meet"]):
                fehler.append("Die verantwortliche Person hat ihren Google-Kalender nicht verbunden: keine Einladung "
                              "und kein Meet-Link.")
            continue
        t = get(c, tid)  # Meet-Link nach dem ersten Eintrag übernehmen
        daten = dict(t, beschreibung_google=_google_text(c, t))
        if not haupt and t["meet_link"]:
            daten["ort"] = t["ort"] or t["meet_link"]
        try:
            if x["google_event_id"]:
                res = google_kalender.event_aendern(token, x["google_event_id"], daten, t["gaeste"] if haupt else (),
                                                    zone, einladen=haupt and einladen,
                                                    kalender_id=x.get("google_kalender_id") or "primary")
            else:
                ziel = ziel_kalender(c, x["person"])
                res = google_kalender.event_anlegen(token, daten, t["gaeste"] if haupt else (), zone,
                                                    meet=haupt and bool(t["meet"]), einladen=haupt and einladen,
                                                    kalender_id=ziel)
                c.execute("UPDATE termin_teilnehmer SET google_event_id=?, google_kalender_id=? WHERE id=?",
                          (res["id"], ziel, x["id"]))
            if haupt and res.get("meet_link"):
                c.execute("UPDATE termine SET meet_link=? WHERE id=?", (res["meet_link"], tid))
            c.commit()
        except google_kalender.GoogleError as exc:
            fehler.append(f"{x['person_name']}: {exc}")
    c.execute("UPDATE termine SET sync_fehler=? WHERE id=?", ("; ".join(fehler)[:500] or None, tid))
    c.commit()


# Kalender, die standardmäßig nicht als „belegt“ zählen (Feiertage, Geburtstage, Kalenderwochen)
NICHT_BELEGT = ("#holiday@group.v.calendar.google.com", "#contacts@group.v.calendar.google.com",
                "#weeknum@group.v.calendar.google.com", "addressbook#")


def kalender_von(c, person):
    """Die Google-Kalender der Person mit ihrer Wahl: [{id, name, primary, schreiben, lesen}] oder None (noch nicht
    geladen oder Recht fehlt, dann gilt nur der Hauptkalender)."""
    row = c.execute("SELECT kalender FROM kalender_konten WHERE person=?", (person or "",)).fetchone()
    try:
        return json.loads(row["kalender"]) if row and row["kalender"] else None
    except ValueError:
        return None


def ziel_kalender(c, person):
    """In diesen Kalender trägt das OS neue Termine der Person ein (Standard: Hauptkalender)."""
    row = c.execute("SELECT ziel_kalender FROM kalender_konten WHERE person=?", (person or "",)).fetchone()
    return (row["ziel_kalender"] if row else None) or "primary"


def kalender_laden(c, person):
    """Liste der Google-Kalender holen und mit der bisherigen Wahl zusammenführen. Neue Kalender zählen als belegt,
    außer Feiertage und Geburtstage. Gibt die Liste zurück oder None, wenn das Recht fehlt (dann neu verbinden)."""
    from . import google_kalender
    token = _token(c, person)
    if not token:
        return None
    try:
        neu = google_kalender.kalender_liste(token)
    except google_kalender.GoogleError as exc:
        if "(403)" in str(exc) or "(401)" in str(exc):
            c.execute("UPDATE kalender_konten SET kalender=NULL WHERE person=?", (person,))
            c.commit()
        return None
    alt = {k["id"]: k for k in kalender_von(c, person) or []}
    for k in neu:
        k["lesen"] = alt[k["id"]]["lesen"] if k["id"] in alt else not any(x in k["id"] for x in NICHT_BELEGT)
    ziel = ziel_kalender(c, person)
    if ziel != "primary" and not any(k["id"] == ziel and k["schreiben"] for k in neu):
        ziel = None  # Kalender gibt es nicht mehr oder nur lesend: zurück zum Hauptkalender
    c.execute("UPDATE kalender_konten SET kalender=?, ziel_kalender=? WHERE person=?",
              (json.dumps(neu, ensure_ascii=False), None if ziel == "primary" else ziel, person))
    c.commit()
    return neu


def kalender_wahl_speichern(c, person, ziel, lesen_ids):
    """Wahl auf „Mein Kalender“: wohin neue Termine kommen, welche Kalender als belegt zählen."""
    from . import firma
    liste = kalender_von(c, person)
    if liste is None:
        raise KalenderError("Erst die Liste deiner Kalender laden (neu verbinden).")
    ids = {k["id"] for k in liste}
    if ziel != "primary" and not any(k["id"] == ziel and k["schreiben"] for k in liste):
        raise KalenderError("In diesen Kalender darf das OS nicht schreiben. Bitte einen eigenen Kalender wählen.")
    lesen_ids = set(lesen_ids) & ids
    haupt = next((k["id"] for k in liste if k["primary"]), None)
    for k in liste:
        k["lesen"] = k["id"] in lesen_ids
    if haupt == ziel:
        ziel = "primary"
    c.execute("UPDATE kalender_konten SET kalender=?, ziel_kalender=? WHERE person=?",
              (json.dumps(liste, ensure_ascii=False), None if ziel == "primary" else ziel, person))
    name = next((k["name"] for k in liste if k["id"] == ziel or (ziel == "primary" and k["primary"])), "Hauptkalender")
    firma.log(c, person, "kalender", "person", person, f"Kalender-Wahl gespeichert: Termine in „{name}“, "
              f"{len(lesen_ids)} Kalender zählen als belegt.")
    return liste


def belegt_abgleichen(c, person, tage=21):
    """Belegte Zeiten der Person aus allen gewählten Google-Kalendern holen (Schule, Sport, Arbeit …; nur Zeiten,
    keine Titel). Termine, die das OS selbst eingetragen hat, zählen nicht doppelt: die stehen schon in `termine`."""
    from . import google_kalender
    token = _token(c, person)
    if not token:
        return False
    zone = regeln(c)["zeitzone"]
    von = datetime.now().replace(minute=0, second=0, microsecond=0)
    liste = kalender_von(c, person)
    if liste is None:
        liste = kalender_laden(c, person)
    ids = [k["id"] for k in liste if k.get("lesen")] if liste is not None else ["primary"]
    busy, fehler = [], []
    for kid in ids:
        try:
            busy += google_kalender.belegte_termine(token, _utc(von, zone), _utc(von + timedelta(days=tage), zone),
                                                    kalender_id=kid)
        except google_kalender.GoogleError as exc:
            if kid == "primary" or any(k["id"] == kid and k["primary"] for k in liste or []):
                c.execute("UPDATE kalender_konten SET status='fehler', fehler=? WHERE person=?", (str(exc)[:300], person))
                c.commit()
                return False
            fehler.append(next((k["name"] for k in liste if k["id"] == kid), kid))
    eigene = {r[0] for r in c.execute("SELECT google_event_id FROM termin_teilnehmer WHERE person=? AND "
                                      "google_event_id IS NOT NULL", (person,))}
    c.execute("DELETE FROM belegt WHERE person=?", (person,))
    for eid, s, e in busy:
        if eid in eigene:
            continue
        start = f"{s}T00:00" if len(s) == 10 else _iso(_lokal(s, zone))  # ganztägig: Datum ohne Uhrzeit
        ende = f"{e}T00:00" if len(e) == 10 else _iso(_lokal(e, zone))
        c.execute("INSERT INTO belegt (person, start, ende) VALUES (?,?,?)", (person, start, ende))
    hinweis = f"Nicht lesbar: {', '.join(fehler)}"[:300] if fehler else None
    c.execute("UPDATE kalender_konten SET status='ok', fehler=?, abgeglichen_am=? WHERE person=?",
              (hinweis, now(), person))
    c.commit()
    return True

# ---------------------------------------------------------------- Listen und freie Zeiten


def liste(c, von, bis, person=None, mit_abgesagt=False):
    where, args = ["t.start < ?", "t.ende > ?"], [bis, von]
    if not mit_abgesagt:
        where.append("t.status != 'abgesagt'")
    if person:
        where.append("t.id IN (SELECT termin_id FROM termin_teilnehmer WHERE person = ?)")
        args.append(person)
    rows = c.execute(f"SELECT t.id FROM termine t WHERE {' AND '.join(where)} ORDER BY t.start", args).fetchall()
    return [get(c, r["id"]) for r in rows]


def zum_lead(c, lead_id):
    return [get(c, r["id"]) for r in c.execute(
        "SELECT id FROM termine WHERE lead_id=? AND status != 'abgesagt' ORDER BY start DESC LIMIT 10", (lead_id,))]


def offene_ergebnisse(c, person):
    """Vorbei, aber noch ohne Ergebnis: Das OS fragt die verantwortliche Person."""
    rows = c.execute("SELECT id FROM termine WHERE verantwortlich=? AND status='geplant' AND ergebnis IS NULL "
                     "AND ende <= ? AND ende >= ? ORDER BY ende", (person, _iso(datetime.now()),
                                                                  _iso(datetime.now() - timedelta(days=30)))).fetchall()
    return [get(c, r["id"]) for r in rows]


def _belegungen(c, personen, von, bis, ohne_termin=None):
    """Belegte Zeiten aller Personen: aus Google (frei/belegt) und aus den Terminen im OS."""
    zeiten = []
    if not personen:
        return zeiten
    marks = ",".join("?" * len(personen))
    for r in c.execute(f"SELECT start, ende FROM belegt WHERE person IN ({marks}) AND start < ? AND ende > ?",
                       [*personen, bis, von]):
        zeiten.append((r["start"], r["ende"]))
    for r in c.execute(f"""SELECT DISTINCT t.start, t.ende FROM termine t JOIN termin_teilnehmer tt ON tt.termin_id = t.id
                           WHERE tt.person IN ({marks}) AND t.status != 'abgesagt' AND t.start < ? AND t.ende > ?
                           AND t.id != ?""", [*personen, bis, von, ohne_termin or 0]):
        zeiten.append((r["start"], r["ende"]))
    return zeiten


def vorschlaege(c, personen, dauer=None, ab=None, tage=14, anzahl=6):
    """Freie Zeiten, in denen alle können: innerhalb der Arbeitszeit, an den eingestellten Wochentagen, mit Puffer.
    Je Tag höchstens zwei Vorschläge, damit die Auswahl über mehrere Tage reicht."""
    r = regeln(c)
    dauer = timedelta(minutes=int(dauer or r["dauer"]))
    puffer = timedelta(minutes=r["puffer"])
    jetzt = datetime.now()
    ab = max(ab or jetzt, jetzt + timedelta(minutes=30)).replace(second=0, microsecond=0)
    if ab.minute % 30:
        ab += timedelta(minutes=30 - ab.minute % 30)
    belegt = [(_dt(s) - puffer, _dt(e) + puffer) for s, e in
              _belegungen(c, list(personen), _iso(ab), _iso(ab + timedelta(days=tage)))]
    out = []
    for tagnr in range(tage + 1):
        d = (ab + timedelta(days=tagnr)).date()
        if d.weekday() not in r["tage_liste"]:
            continue
        beginn = datetime.combine(d, datetime.strptime(r["von"], "%H:%M").time())
        schluss = datetime.combine(d, datetime.strptime(r["bis"], "%H:%M").time())
        slot, am_tag = beginn, 0
        while slot + dauer <= schluss and am_tag < 2:
            if slot >= ab and not any(s < slot + dauer and e > slot for s, e in belegt):
                out.append({"start": _iso(slot), "ende": _iso(slot + dauer),
                            "text": zeitraum_text({"start": _iso(slot), "ende": _iso(slot + dauer)})})
                am_tag += 1
                slot += timedelta(hours=2)  # zweiter Vorschlag später am Tag
                continue
            slot += timedelta(minutes=30)
        if len(out) >= anzahl:
            break
    return out[:anzahl]


def konflikte(c, personen, start, ende, ohne_termin=None):
    """Wer ist in dieser Zeit schon belegt? (Namen, für einen Hinweis beim Anlegen)."""
    from . import firma
    out = []
    for p in personen:
        if _belegungen(c, [p], start, ende, ohne_termin):
            out.append(firma.name_von(c, p))
    return out

# ---------------------------------------------------------------- Nach dem Termin, Briefing


def ergebnis(c, wer, tid, art, notiz="", preisgespraech=False, folge_titel="", folge_faellig=""):
    """Ergebnis eintragen. Bei Leads: stattgefunden schiebt auf „Gespräch“, ernsthaftes Preisgespräch zählt im
    Branchentest; eine Folgeaufgabe wird gleich angelegt."""
    from . import aufgaben, branchentest, crm, firma, leaddb
    t = get(c, tid)
    if not t:
        raise KalenderError("Diesen Termin gibt es nicht.")
    if art not in ERGEBNIS_LABEL:
        raise KalenderError("Bitte ein Ergebnis wählen.")
    notiz = (notiz or "").strip()[:2000]
    c.execute("UPDATE termine SET ergebnis=?, ergebnis_notiz=?, ergebnis_von=?, ergebnis_am=? WHERE id=?",
              (art, notiz or None, wer, now(), tid))
    firma.log(c, wer, "termin_ergebnis", "termin", tid, f"{ERGEBNIS_LABEL[art]}: {t['titel']}",
              {"ergebnis": art, "lead": t["lead_id"], "art": t["art"], "preisgespraech": bool(preisgespraech)},
              commit=False)
    c.commit()
    name = firma.name_von(c, wer)
    if t["lead_id"]:
        leaddb.add_event(c, t["lead_id"], "termin", f"Termin „{t['titel']}“: {ERGEBNIS_LABEL[art]}."
                         + (f" {notiz}" if notiz else ""), author=name)
        if art == "stattgefunden":
            crm.advance(c, t["lead_id"], "gespraech", author=name)
            if preisgespraech:
                branchentest.preisgespraech(c, wer, t["lead_id"], True)
    if folge_titel.strip():
        b = aufgaben.wiedervorlage_bereich(c) if t["lead_id"] else (firma.bereiche_von(c, wer) or ["markt"])[0]
        aufgaben.anlegen(c, wer, folge_titel, b, folge_faellig or (date.today() + timedelta(days=1)).isoformat(),
                         person=wer, objekt_typ="lead" if t["lead_id"] else None, objekt_id=t["lead_id"],
                         quelle="termin")


def briefing(c, t):
    """Briefing aus der Akte (ohne KI): wer, was, Bewertung, Verlauf, offene Aufgaben, Branchentest."""
    from . import aufgaben, crm, leaddb
    if not t.get("lead_id"):
        return None
    lead = leaddb.get_lead(c, t["lead_id"])
    if not lead:
        return None
    research = lead.get("research") or {}
    events = [dict(r) for r in c.execute("SELECT at, kind, author, text FROM lead_events WHERE lead_id=? AND kind NOT IN "
                                         "('feedback','ai') ORDER BY id DESC LIMIT 6", (lead["id"],))]
    tb = c.execute("SELECT name FROM testbranchen WHERE key=?", (lead.get("testbranche") or "",)).fetchone()
    return {
        "lead": lead,
        "fakten": [(k, v) for k, v in [
            ("Betrieb", lead["name"]), ("Art", lead.get("category_label")), ("Ort", lead.get("city") or lead.get("address")),
            ("Ansprechperson", lead.get("contact_name")), ("Telefon", lead.get("phone")),
            ("Webseite", lead.get("website")), ("Stufe", crm.STAGE_LABELS.get(lead.get("stage"), "")),
            ("Branchentest", (tb["name"] if tb else "") + (" · warm" if lead.get("herkunft") == "warm" else "")),
            ("Bewertung", f"{lead['fit_score']} Punkte" if lead.get("fit_score") is not None else None)] if v],
        "warum": lead.get("fit_reason"),
        "signale": sorted(research.get("signale") or [], key=lambda x: -x[0])[:5],
        "zeitfresser": research.get("zeitfresser") or [],
        "verlauf": events,
        "aufgaben": aufgaben.zum_objekt(c, "lead", lead["id"]),
    }

# ---------------------------------------------------------------- Serien (Wochengespräch)


def _zahl(wert, feld, lo, hi):
    try:
        n = int(wert)
    except (TypeError, ValueError) as exc:
        raise KalenderError(f"{feld} bitte als Zahl.") from exc
    if not lo <= n <= hi:
        raise KalenderError(f"{feld} zwischen {lo} und {hi}.")
    return n


def serie_anlegen(c, wer, titel, wochentag, uhrzeit, dauer, verantwortlich, personen, meet=False, rhythmus="woche",
                  flexibel=False, spielraum=120):
    """Neue Serie. Bei `flexibel` sucht das OS für jeden Termin die Zeit am selben Tag, die der Wunschzeit am nächsten
    liegt und in der alle können (höchstens `spielraum` Minuten früher oder später)."""
    from . import aufgaben, firma
    titel = re.sub(r"\s+", " ", titel or "").strip()
    if not titel or len(titel) > 200:
        raise KalenderError("Bitte einen Titel (höchstens 200 Zeichen).")
    if rhythmus not in RHYTHMUS_LABEL:
        raise KalenderError("Bitte einen Rhythmus wählen.")
    if rhythmus in ("taeglich", "werktags"):
        wochentag = 0
    elif str(wochentag) not in "0123456" or not str(wochentag):
        raise KalenderError("Bitte einen Wochentag.")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", uhrzeit or ""):
        raise KalenderError("Uhrzeit bitte wie 17:00.")
    dauer = _zahl(dauer, "Dauer (Minuten)", 5, 480)
    spielraum = _zahl(spielraum, "Spielraum (Minuten)", 0, 600) if flexibel else 0
    if not firma.aktive_person(c, verantwortlich):
        raise KalenderError("Bitte eine aktive verantwortliche Person.")
    personen = [p for p in dict.fromkeys([verantwortlich, *personen]) if firma.aktive_person(c, p)]
    heute = date.today()
    start_datum = heute if rhythmus in ("taeglich", "werktags") else \
        heute + timedelta(days=(int(wochentag) - heute.weekday()) % 7)
    cur = c.execute("INSERT INTO serien (titel, wochentag, uhrzeit, dauer_min, verantwortlich, teilnehmer, meet, "
                    "rhythmus, flexibel, spielraum_min, start_datum, erstellt_von, erstellt_am) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (titel, int(wochentag), uhrzeit, dauer, verantwortlich, json.dumps(personen), 1 if meet else 0,
                     rhythmus, 1 if flexibel else 0, spielraum, start_datum.isoformat(), wer, now()))
    sid = cur.lastrowid
    s = serie(c, sid)
    firma.log(c, wer, "termin", "serie", sid, f"Serie angelegt: {titel}, {s['rhythmus_text']} {uhrzeit}"
              + (f" (verschiebt sich bis {spielraum} Minuten)" if flexibel else ""))
    for p in personen:
        if p != wer:
            aufgaben.melden(c, p, f"Neue Serie: {titel}", f"{s['rhythmus_text']}, {uhrzeit} Uhr · von "
                            f"{firma.name_von(c, wer)}", "/kalender")
    serien_auffuellen(c)
    return sid


def _rhythmus_text(s):
    tag = WOCHENTAGE[s["wochentag"]]
    r = s.get("rhythmus") or "woche"
    if r == "taeglich":
        return "Täglich"
    if r == "werktags":
        return "Werktags"
    if r == "zweiwochen":
        return f"Alle zwei Wochen {tag}s"
    if r == "monat":
        n = min((date.fromisoformat(s["start_datum"]).day - 1) // 7, 3) if s.get("start_datum") else 0
        return f"Jeden {n + 1}. {tag} im Monat"
    return f"{tag}s"


def _serie_dict(r):
    s = dict(r, teilnehmer=json.loads(r["teilnehmer"] or "[]"), tag=WOCHENTAGE[r["wochentag"]])
    s["rhythmus"] = s.get("rhythmus") or "woche"
    s["rhythmus_text"] = _rhythmus_text(s)
    return s


def serien(c):
    rows = c.execute("SELECT s.*, p.name AS verantwortlich_name FROM serien s LEFT JOIN team p ON p.key=s.verantwortlich "
                     "ORDER BY s.aktiv DESC, s.wochentag, s.uhrzeit").fetchall()
    return [_serie_dict(r) for r in rows]


def serie(c, sid):
    row = c.execute("SELECT s.*, p.name AS verantwortlich_name FROM serien s LEFT JOIN team p ON p.key=s.verantwortlich "
                    "WHERE s.id=?", (sid,)).fetchone()
    return _serie_dict(row) if row else None


def serie_beenden(c, wer, sid):
    """Serie beenden: künftige Termine werden abgesagt, vergangene bleiben."""
    from . import firma
    row = c.execute("SELECT * FROM serien WHERE id=?", (sid,)).fetchone()
    if not row:
        raise KalenderError("Diese Serie gibt es nicht.")
    c.execute("UPDATE serien SET aktiv=0 WHERE id=?", (sid,))
    c.commit()
    for r in c.execute("SELECT id FROM termine WHERE serie_id=? AND status != 'abgesagt' AND start > ?",
                       (sid, _iso(datetime.now()))).fetchall():
        absagen(c, wer, r["id"], "Serie beendet.")
    firma.log(c, wer, "termin", "serie", sid, f"Serie beendet: {row['titel']}")


def serien_tage(s, von, bis):
    """Tage zwischen `von` und `bis` (je einschließlich), an denen die Serie stattfindet."""
    r = s.get("rhythmus") or "woche"
    anker = date.fromisoformat(s["start_datum"]) if s.get("start_datum") else von
    n_monat = min((anker.day - 1) // 7, 3)
    out, d = [], max(von, anker)
    while d <= bis:
        if r == "taeglich":
            ok = True
        elif r == "werktags":
            ok = d.weekday() < 5
        elif d.weekday() != s["wochentag"]:
            ok = False
        elif r == "zweiwochen":
            ok = (d - anker).days // 7 % 2 == 0
        elif r == "monat":
            ok = (d.day - 1) // 7 == n_monat
        else:
            ok = True
        if ok:
            out.append(d)
        d += timedelta(days=1)
    return out

# ---------------------------------------------------------------- Flexible Serien: Zeit finden, in der alle können


def besetzt(c, personen, start, ende, termin=None):
    """Wer hat in [start, ende) keine Zeit? Google (ohne die eigenen OS-Termine, siehe belegt_abgleichen) und
    Termine im OS; der Termin `termin` selbst zählt nicht mit."""
    out = []
    for p in personen:
        if c.execute("SELECT 1 FROM belegt WHERE person=? AND start < ? AND ende > ?", (p, ende, start)).fetchone() or \
                c.execute("""SELECT 1 FROM termine t JOIN termin_teilnehmer tt ON tt.termin_id = t.id WHERE tt.person=?
                             AND t.status != 'abgesagt' AND t.start < ? AND t.ende > ? AND t.id != ?""",
                          (p, ende, start, (termin or {}).get("id") or 0)).fetchone():
            out.append(p)
    return out


def platz_finden(c, personen, wunsch, dauer_min, spielraum_min, termin=None):
    """Die Zeit am selben Tag, die `wunsch` am nächsten liegt und in der alle können (erst später, dann früher, in
    Viertelstunden). None, wenn es im Spielraum keine gibt."""
    dauer = timedelta(minutes=dauer_min)
    tag_ = wunsch.date()
    frueh = datetime.combine(tag_, datetime.strptime(FRUEHESTENS, "%H:%M").time())
    spaet = datetime.combine(tag_, datetime.strptime(SPAETESTENS, "%H:%M").time())
    grenze = datetime.now() + timedelta(minutes=15)
    schritte = [0] + [v * k for k in range(15, spielraum_min + 1, 15) for v in (1, -1)]
    for minuten in schritte:
        slot = wunsch + timedelta(minutes=minuten)
        if slot.date() != tag_ or slot < frueh or slot + dauer > spaet or slot < grenze:
            continue
        if not besetzt(c, personen, _iso(slot), _iso(slot + dauer), termin):
            return slot
    return None


def _gross(text):
    return text[:1].upper() + text[1:]


def _namen(c, keys):
    from . import firma
    namen = [firma.name_von(c, k).split(" ")[0] for k in keys]
    return namen[0] if len(namen) == 1 else ", ".join(namen[:-1]) + " und " + namen[-1]


def serien_auffuellen(c, wochen=None):
    """Hält für jede aktive Serie die nächsten Termine bereit (täglich zwei Wochen, wöchentlich sechs Wochen …).
    Flexible Serien landen gleich auf einer Zeit, in der alle können."""
    from . import aufgaben
    from .aufgaben import tag
    neu = 0
    heute = date.today()
    jetzt = _iso(datetime.now())
    for s in serien(c):
        if not s["aktiv"]:
            continue
        tage = wochen * 7 if wochen else VORLAUF_TAGE.get(s["rhythmus"], 42)
        for tag_ in serien_tage(s, heute, heute + timedelta(days=tage - 1)):
            wunsch = datetime.fromisoformat(f"{tag_.isoformat()}T{s['uhrzeit']}")
            if _iso(wunsch) <= jetzt:
                continue
            if c.execute("SELECT 1 FROM termine WHERE serie_id=? AND substr(start,1,10)=?",
                         (s["id"], tag_.isoformat())).fetchone():
                continue
            start, hinweis = wunsch, None
            if s["flexibel"]:
                wer_nicht = besetzt(c, s["teilnehmer"], _iso(wunsch), _iso(wunsch + timedelta(minutes=s["dauer_min"])))
                if wer_nicht:
                    platz = platz_finden(c, s["teilnehmer"], wunsch, s["dauer_min"], s["spielraum_min"])
                    if platz:
                        start = platz
                        hinweis = (f"{_gross(tag(_iso(platz)))} um {platz:%H:%M} statt {s['uhrzeit']} Uhr, weil "
                                   f"{_namen(c, wer_nicht)} um {s['uhrzeit']} keine Zeit "
                                   f"{'hat' if len(wer_nicht) == 1 else 'haben'}.")
                    else:
                        hinweis = (f"Für {tag(_iso(wunsch))} findet sich keine Zeit, in der alle können "
                                   f"({_namen(c, wer_nicht)} ist belegt). Der Termin steht zur gewohnten Zeit.")
            tid = anlegen(c, s["erstellt_von"] or s["verantwortlich"], s["titel"], _iso(start), dauer=s["dauer_min"],
                          verantwortlich=s["verantwortlich"], personen=s["teilnehmer"], meet=bool(s["meet"]),
                          serie_id=s["id"], wunsch_start=_iso(wunsch), melden=False)
            if hinweis and start != wunsch:
                for p in s["teilnehmer"]:
                    aufgaben.melden(c, p, f"Verschoben: {s['titel']}", hinweis, f"/kalender/{tid}", push=True)
            elif hinweis:
                aufgaben.melden(c, s["verantwortlich"], f"Keine gemeinsame Zeit: {s['titel']}", hinweis,
                                f"/kalender/{tid}", push=True)
                c.execute("UPDATE termine SET erinnert = erinnert || 'konflikt,' WHERE id=?", (tid,))
                c.commit()
            neu += 1
    return neu


def serien_pruefen(c):
    """Flexible Serien neu prüfen (neue Termine, Google frei/belegt): Hat jemand zur geplanten Zeit keine Zeit mehr,
    rückt der Termin auf die nächste Zeit, in der alle können; ist die Wunschzeit wieder frei, kehrt er dorthin zurück.
    Jede Verschiebung bekommen alle als Meldung. Kurz vor dem Termin wird nicht mehr verschoben."""
    from . import aufgaben
    from .aufgaben import tag
    jetzt = datetime.now()
    bewegt = 0
    for row in c.execute("""SELECT t.id, s.spielraum_min, s.uhrzeit FROM termine t JOIN serien s ON s.id = t.serie_id
                            WHERE s.aktiv=1 AND s.flexibel=1 AND t.status='geplant' AND t.start > ? AND t.start < ?
                            ORDER BY t.start""",
                         (_iso(jetzt + NICHT_MEHR_VERSCHIEBEN), _iso(jetzt + timedelta(days=21)))).fetchall():
        t = get(c, row["id"])
        dauer = int((_dt(t["ende"]) - _dt(t["start"])).total_seconds() // 60)
        wunsch = _dt(t.get("wunsch_start") or f"{t['start'][:10]}T{row['uhrzeit']}")
        wer_nicht = besetzt(c, t["personen"], t["start"], t["ende"], t)
        if not wer_nicht and t["start"] == _iso(wunsch):
            continue
        platz = platz_finden(c, t["personen"], wunsch, dauer, row["spielraum_min"], t)
        if platz is None:
            if wer_nicht and "konflikt" not in (t["erinnert"] or ""):
                aufgaben.melden(c, t["verantwortlich"], f"Keine gemeinsame Zeit: {t['titel']}",
                                f"{t['zeit']}: {_namen(c, wer_nicht)} ist belegt, und im Spielraum findet sich keine "
                                "Zeit, in der alle können. Bitte von Hand verschieben oder absagen.",
                                f"/kalender/{t['id']}", push=True)
                c.execute("UPDATE termine SET erinnert = erinnert || 'konflikt,' WHERE id=?", (t["id"],))
                c.commit()
            continue
        if _iso(platz) == t["start"] or (not wer_nicht and platz != wunsch):
            continue  # schon die beste Zeit, oder nur eine andere Ausweichzeit: nicht hin und her schieben
        if platz == wunsch:
            meldung = f"{_gross(tag(_iso(platz)))} wieder zur gewohnten Zeit: {platz:%H:%M} Uhr (statt {t['start'][11:16]})."
        else:
            meldung = (f"{_gross(tag(_iso(platz)))} um {platz:%H:%M} statt {t['start'][11:16]} Uhr, weil "
                       f"{_namen(c, wer_nicht)} dann keine Zeit {'hat' if len(wer_nicht) == 1 else 'haben'}.")
        aendern(c, None, t["id"], start=_iso(platz), meldung=meldung)
        bewegt += 1
    return bewegt

# ---------------------------------------------------------------- Taktgeber (Hintergrund-Arbeiter, jede Minute)


def takt(c):
    """Erinnerungen, Frage nach dem Ergebnis, frei/belegt abgleichen, Serien auffüllen. Gibt Zähler zurück."""
    from . import aufgaben, leaddb
    r = regeln(c)
    jetzt = datetime.now()
    zaehler = {"erinnert": 0, "abgeglichen": 0, "serien": 0, "ergebnis": 0}
    stufen = [("24h", timedelta(hours=24), r["erinnerung_24h"]), ("1h", timedelta(hours=1), r["erinnerung_1h"])]
    for key, vorlauf, an in stufen:
        if not an:
            continue
        rows = c.execute("SELECT id FROM termine WHERE status='geplant' AND start > ? AND start <= ? "
                         "AND instr(erinnert, ?) = 0", (_iso(jetzt), _iso(jetzt + vorlauf), key)).fetchall()
        for row in rows:
            t = get(c, row["id"])
            for p in t["personen"]:
                aufgaben.melden(c, p, f"{'Morgen' if key == '24h' else 'In einer Stunde'}: {t['titel']}",
                                t["zeit"] + (f" · {t['meet_link']}" if t["meet_link"] else ""), f"/kalender/{t['id']}",
                                push=key == "1h")
            c.execute("UPDATE termine SET erinnert = erinnert || ? WHERE id=?", (key + ",", t["id"]))
            c.commit()
            zaehler["erinnert"] += 1
    rows = c.execute("SELECT id FROM termine WHERE status='geplant' AND ergebnis IS NULL AND ende <= ? AND ende > ? "
                     "AND instr(erinnert, 'ergebnis') = 0", (_iso(jetzt - timedelta(minutes=15)),
                                                            _iso(jetzt - timedelta(days=2)))).fetchall()
    for row in rows:
        t = get(c, row["id"])
        aufgaben.melden(c, t["verantwortlich"], f"Wie war der Termin? {t['titel']}", "Ergebnis eintragen",
                        f"/kalender/{t['id']}")
        c.execute("UPDATE termine SET erinnert = erinnert || 'ergebnis,' WHERE id=?", (t["id"],))
        c.commit()
        zaehler["ergebnis"] += 1
    if google_cfg():
        for k in c.execute("SELECT person, abgeglichen_am FROM kalender_konten WHERE status != 'abgelaufen'").fetchall():
            if not k["abgeglichen_am"] or k["abgeglichen_am"] < (jetzt - timedelta(minutes=r["abgleich"])).isoformat():
                zaehler["abgeglichen"] += 1 if belegt_abgleichen(c, k["person"]) else 0
    if leaddb.kv_get(c, "serien_aufgefuellt") != date.today().isoformat():
        zaehler["serien"] = serien_auffuellen(c)
        leaddb.kv_set(c, "serien_aufgefuellt", date.today().isoformat())
    geprueft = leaddb.kv_get(c, "serien_geprueft") or ""
    if geprueft < (jetzt - timedelta(minutes=r["abgleich"])).isoformat(timespec="seconds"):
        zaehler["verschoben"] = serien_pruefen(c)
        leaddb.kv_set(c, "serien_geprueft", jetzt.isoformat(timespec="seconds"))
    return zaehler

# ---------------------------------------------------------------- ICS-Abo (Rückfall ohne Verbindung)


def ics_token_erneuern(c, person):
    """Neue geheime Abo-Adresse (die alte gilt sofort nicht mehr). Gespeichert ist nur der Hash."""
    from . import firma
    token = secrets.token_urlsafe(24)
    c.execute("UPDATE team SET ics_hash=? WHERE key=?", (hashlib.sha256(token.encode()).hexdigest(), person))
    firma.log(c, person, "kalender", "person", person, "Neue Adresse für das Kalender-Abo erzeugt.")
    return token


def ics_person(c, token):
    if not token or len(token) > 80:
        return None
    row = c.execute("SELECT key FROM team WHERE ics_hash=? AND IFNULL(status,'aktiv')='aktiv'",
                    (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    return row["key"] if row else None


def _ics_text(text):
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics(c, person, host="ylvalabs"):
    """Kalender-Datei (iCalendar) mit den Terminen der Person, 30 Tage zurück bis 180 Tage voraus."""
    zone = regeln(c)["zeitzone"]
    von = _iso(datetime.now() - timedelta(days=30))
    bis = _iso(datetime.now() + timedelta(days=180))
    zeilen = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Ylva Labs//Firmen-OS//DE", "CALSCALE:GREGORIAN",
              "X-WR-CALNAME:Ylva Labs", f"X-WR-TIMEZONE:{zone}"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for t in liste(c, von, bis, person, mit_abgesagt=True):
        fmt = lambda v: _utc(_dt(v), zone).replace("-", "").replace(":", "")  # noqa: E731
        beschreibung = "\n".join(x for x in [t.get("beschreibung") or "", t.get("meet_link") or ""] if x)
        zeilen += ["BEGIN:VEVENT", f"UID:termin-{t['id']}@{host}", f"DTSTAMP:{stamp}", f"DTSTART:{fmt(t['start'])}",
                   f"DTEND:{fmt(t['ende'])}", f"SUMMARY:{_ics_text(t['titel'])}",
                   f"STATUS:{'CANCELLED' if t['status'] == 'abgesagt' else ('TENTATIVE' if t['status'] == 'vorschlag' else 'CONFIRMED')}"]
        if beschreibung:
            zeilen.append(f"DESCRIPTION:{_ics_text(beschreibung)}")
        if t.get("ort") or t.get("meet_link"):
            zeilen.append(f"LOCATION:{_ics_text(t.get('ort') or t.get('meet_link'))}")
        zeilen.append("END:VEVENT")
    zeilen.append("END:VCALENDAR")
    return "\r\n".join(zeilen) + "\r\n"
