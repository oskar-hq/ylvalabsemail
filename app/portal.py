"""Kunden-Bereich (Firmen-OS, Stufe 7, docs/FIRMEN-OS.md Abschnitt 4.7).

- **Zugang per Einmal-Link:** Die verantwortliche Person klickt am Projekt „Zugang schicken“ (der Klick ist die
  Freigabe). Der Kunde bekommt eine Mail mit einem Link, der einmal funktioniert und `link_tage` gilt. Danach bleibt
  er auf seinem Gerät angemeldet (`sitzung_tage`, eigenes Cookie, getrennt von den Team-Sitzungen). Einen neuen Link
  kann der Kunde mit seiner Adresse selbst anfordern. In der Datenbank liegen nur Hashes.
- **Portal-Seiten:** Projektstand in Kundensprache, Termine, Anforderung bestätigen, Abnahme-Checkliste,
  Änderungswünsche stellen und ihren Stand sehen, Dokumente, Wochenberichte.
- **Dokumente:** Das Team lädt am Projekt hoch (Angebot, AV-Vertrag …) und entscheidet, ob der Kunde sie sieht.
- **Wochenbericht** (Freigabe-Stufe „wochenbericht“, Standard B mit Einspruchsfrist) und **Termin-Erinnerung an
  Kunden** 24 Stunden vorher (Stufe „erinnerung_kunde“, Standard A) laufen im Takt des Hintergrund-Arbeiters.
- Alle Regeln (Gültigkeit, Texte, Berichtstag, was der Kunde sieht) stehen in Einstellungen → Kunden-Bereich.
Mails an Kunden gehen über das Akquise-Postfach (`agent.send_new`), landen in der Akte und nie an gesperrte Betriebe.
"""
import hashlib
import json
import re
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS kunden_zugaenge (
    id INTEGER PRIMARY KEY AUTOINCREMENT, projekt_id INTEGER NOT NULL, email TEXT NOT NULL, name TEXT,
    link_hash TEXT, link_bis TEXT, link_angefordert TEXT, status TEXT NOT NULL DEFAULT 'aktiv',
    erstellt_von TEXT, erstellt_am TEXT NOT NULL, letzter_login TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS kunden_zugaenge_projekt ON kunden_zugaenge(projekt_id, email);
CREATE TABLE IF NOT EXISTS kunden_sitzungen (
    token_hash TEXT PRIMARY KEY, zugang_id INTEGER NOT NULL, erstellt_am TEXT NOT NULL, bis TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dokumente (
    id INTEGER PRIMARY KEY AUTOINCREMENT, projekt_id INTEGER NOT NULL, titel TEXT NOT NULL, art TEXT NOT NULL,
    datei TEXT NOT NULL, dateiname TEXT, groesse INTEGER, sichtbar INTEGER NOT NULL DEFAULT 1,
    hochgeladen_von TEXT, hochgeladen_am TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wochenberichte (
    id INTEGER PRIMARY KEY AUTOINCREMENT, projekt_id INTEGER NOT NULL, woche TEXT NOT NULL, betreff TEXT NOT NULL,
    text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'wartet', erstellt_am TEXT NOT NULL, verschickt_am TEXT,
    UNIQUE (projekt_id, woche)
);
"""

DOK_ARTEN = [("angebot", "Angebot"), ("av_vertrag", "AV-Vertrag"), ("vertrag", "Vertrag"),
             ("anforderung", "Anforderung"), ("sonstiges", "Sonstiges")]
DOK_LABEL = dict(DOK_ARTEN)
ENDUNGEN = {".pdf", ".png", ".jpg", ".jpeg", ".docx", ".xlsx", ".txt", ".csv", ".zip"}
MAX_MB = 8  # wie MAX_CONTENT_LENGTH der App

# Was der Kunde zu jedem Schritt der Journey liest (statt der internen Bezeichnungen)
SCHRITTE = {
    "beauftragt": ("Auftrag erteilt", "Wir bereiten alles vor: Vertrag zur Datenverarbeitung und ein Termin, um "
                                      "Ihre Anforderungen aufzunehmen."),
    "anforderung": ("Anforderung", "Wir schreiben auf, was gebaut wird. Bitte lesen und bestätigen Sie es."),
    "bau": ("Im Bau", "Wir bauen. Zur Hälfte zeigen wir Ihnen einen Zwischenstand."),
    "abnahme": ("Abnahme", "Wir gehen gemeinsam die Checkliste durch, bevor es live geht."),
    "live": ("Live", "Ihr System läuft. Anpassungen sind in den ersten Wochen inklusive."),
    "betrieb": ("Im Betrieb", "Wir kümmern uns um den Betrieb und melden uns regelmäßig."),
    "beendet": ("Beendet", "Die Zusammenarbeit ist beendet."),
}
AENDERUNG_STATUS = {"neu": "Eingegangen", "eingeordnet": "Eingeplant", "geschaetzt": "Eingeplant",
                    "erledigt": "Erledigt", "abgelehnt": "Nicht umgesetzt"}

REGELN = {
    "link_tage": (7, "Login-Link gilt (Tage)"),
    "sitzung_tage": (90, "Angemeldet bleiben (Tage)"),
    "betreff_zugang": ("Ihr Zugang zum Kunden-Bereich von Ylva Labs", "Betreff der Zugangs-Mail"),
    "text_zugang": ("Guten Tag {name},\n\nüber diesen Link kommen Sie in Ihren Kunden-Bereich. Dort sehen Sie den "
                    "Stand Ihres Projekts, Termine und Dokumente und können Änderungswünsche schicken.\n\n{link}\n\n"
                    "Der Link funktioniert einmal und gilt {tage} Tage. Danach bleiben Sie auf diesem Gerät angemeldet.",
                    "Text der Zugangs-Mail ({name}, {link}, {tage})"),
    "bericht_tag": (4, "Wochenbericht am (0 = Montag … 4 = Freitag)"),
    "bericht_stunde": (12, "Wochenbericht ab (Uhr)"),
    "betreff_bericht": ("Wochenbericht {kunde}, KW {kw}", "Betreff des Wochenberichts ({kunde}, {kw})"),
    "text_erinnerung": ("Guten Tag {name},\n\nkurz zur Erinnerung: morgen, {zeit}, haben wir einen Termin "
                        "„{titel}“.{meet}\n\nBis morgen!", "Text der Termin-Erinnerung ({name}, {zeit}, {titel}, {meet})"),
    "zeige_abnahme": (True, "Kunde sieht die Abnahme-Checkliste"),
    "zeige_termine": (True, "Kunde sieht seine Termine"),
}
WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


class PortalError(ValueError):
    pass


def now():
    return datetime.now().isoformat(timespec="seconds")


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _json(raw, default):
    try:
        v = json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default
    return v if isinstance(v, type(default)) else default

# ---------------------------------------------------------------- Regeln (Einstellungen → Kunden-Bereich)


def regeln(c):
    row = c.execute("SELECT value FROM kv WHERE key='regel:portal'").fetchone()
    stored = _json(row["value"], {}) if row else {}
    out = {}
    for k, (standard, _) in REGELN.items():
        v = stored.get(k, standard)
        out[k] = v if isinstance(v, type(standard)) else standard
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
            grenzen = {"link_tage": (1, 30), "sitzung_tage": (1, 365), "bericht_tag": (0, 6), "bericht_stunde": (0, 23)}[k]
            if not raw.lstrip("-").isdigit() or not grenzen[0] <= int(raw) <= grenzen[1]:
                raise PortalError(f"{label}: {grenzen[0]} bis {grenzen[1]}.")
            neu[k] = int(raw)
        else:
            v = str(form.get(k, alt[k])).replace("\r\n", "\n").strip()
            if not v:
                raise PortalError(f"{label}: bitte ausfüllen.")
            if k == "text_zugang" and "{link}" not in v:
                raise PortalError("Der Text der Zugangs-Mail braucht den Platzhalter {link}.")
            neu[k] = v[:3000]
    geaendert = [REGELN[k][1] for k in neu if neu[k] != alt[k]]
    c.execute("INSERT INTO kv (key, value) VALUES ('regel:portal', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (json.dumps(neu),))
    firma.log(c, wer, "einstellung", "regel", "portal", "Kunden-Bereich: " + (", ".join(geaendert) or "nichts")
              + " geändert.", {k: neu[k] for k in neu if neu[k] != alt[k]} or None)
    return geaendert


def _fuellen(text, **werte):
    """Platzhalter {name} usw. ersetzen; unbekannte bleiben stehen (kein KeyError durch Tippfehler)."""
    return re.sub(r"\{(\w+)\}", lambda m: str(werte.get(m.group(1), m.group(0))), text)

# ---------------------------------------------------------------- Mail an Kunden


def basis_url(fallback=""):
    from . import agent
    try:
        basis = agent.S("BASE_URL")
    except KeyError:  # außerhalb der App (Konsole, Tests ohne Einstellungen)
        basis = ""
    return (basis or fallback or "").rstrip("/")


def mail_an_kunde(c, p, to, betreff, text, wer):
    """Über das Akquise-Postfach, im Namen der verantwortlichen Person, in der Akte vermerkt. Nie an gesperrte
    Betriebe. Das ist kein Erstkontakt (bestehender Kunde); ob es raus darf, entscheidet die Freigabe-Stufe."""
    from . import agent, leaddb
    lead = leaddb.get_lead(c, p["lead_id"])
    if not lead:
        raise PortalError("Zu diesem Projekt gibt es keinen Kunden in der Akte.")
    domain = (to or "").rsplit("@", 1)[-1].lower()
    if lead["status"] == "gesperrt" or domain in set(leaddb.kv_get(c, "blocklist", [])):
        raise PortalError("Dieser Kunde möchte keine Nachrichten mehr bekommen.")
    if not agent.outreach_configured():
        raise PortalError("Das Akquise-Postfach ist nicht eingerichtet (Einstellungen → Postfach und Versand).")
    try:
        agent.send_new(c, lead, to, betreff, text, wer, sender=p.get("verantwortlich") or None)
    except RuntimeError as exc:
        raise PortalError(str(exc)) from exc

# ---------------------------------------------------------------- Zugang


def zugaenge(c, pid):
    rows = c.execute("SELECT * FROM kunden_zugaenge WHERE projekt_id=? ORDER BY id", (pid,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["link_offen"] = bool(d["link_hash"] and d["link_bis"] and d["link_bis"] > now())
        out.append(d)
    return out


def darf_zugang(c, wer, p):
    from . import firma
    return wer == p["verantwortlich"] or firma.darf(c, wer, "kunden", firma.VERWALTEN)


def _neuer_link(c, zid):
    token = secrets.token_urlsafe(32)
    bis = (datetime.now() + timedelta(days=regeln(c)["link_tage"])).isoformat(timespec="seconds")
    c.execute("UPDATE kunden_zugaenge SET link_hash=?, link_bis=?, link_angefordert=? WHERE id=?",
              (_hash(token), bis, now(), zid))
    return token


def zugang_schicken(c, wer, pid, email="", name="", senden=True, basis=""):
    """Zugang anlegen (oder erneuern) und den Einmal-Link schicken. Gibt den Link zurück (zum Kopieren, z. B. im
    Demo-Modus oder wenn die Mail nicht ankommt)."""
    from . import firma, leaddb, projekte
    p = projekte.get(c, pid)
    if not p:
        raise PortalError("Dieses Projekt gibt es nicht.")
    if not darf_zugang(c, wer, p):
        raise PortalError("Den Zugang schickt die verantwortliche Person (oder wer Kunden verwaltet).")
    email = (email or p["kunde_email"] or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", email):
        raise PortalError("Bitte eine gültige Mailadresse des Kunden.")
    name = (name or p["contact_name"] or "").strip()[:120] or None
    row = c.execute("SELECT * FROM kunden_zugaenge WHERE projekt_id=? AND email=?", (pid, email)).fetchone()
    if row:
        zid = row["id"]
        c.execute("UPDATE kunden_zugaenge SET status='aktiv', name=COALESCE(?, name) WHERE id=?", (name, zid))
    else:
        zid = c.execute("INSERT INTO kunden_zugaenge (projekt_id, email, name, erstellt_von, erstellt_am) "
                        "VALUES (?,?,?,?,?)", (pid, email, name, wer, now())).lastrowid
    token = _neuer_link(c, zid)
    link = f"{basis_url(basis)}/portal/login/{token}"
    firma.log(c, wer, "projekt", "projekt", pid, f"Zugang zum Kunden-Bereich für {email} "
              f"{'geschickt' if senden else 'angelegt (Link zum Kopieren)'}.", {"zugang": zid}, commit=False)
    c.commit()
    if senden:
        r = regeln(c)
        text = _fuellen(r["text_zugang"], name=name or "", link=link, tage=r["link_tage"])
        mail_an_kunde(c, p, email, r["betreff_zugang"], text.replace("Guten Tag ,", "Guten Tag,"), wer)
    else:
        leaddb.add_event(c, p["lead_id"], "note", f"Zugang zum Kunden-Bereich für {email} angelegt.",
                         author=firma.name_von(c, wer))
    return link


def zugang_sperren(c, wer, zid, gesperrt=True):
    from . import firma, projekte
    z = c.execute("SELECT * FROM kunden_zugaenge WHERE id=?", (zid,)).fetchone()
    if not z:
        raise PortalError("Diesen Zugang gibt es nicht.")
    if not darf_zugang(c, wer, projekte.get(c, z["projekt_id"])):
        raise PortalError("Sperren darf die verantwortliche Person (oder wer Kunden verwaltet).")
    c.execute("UPDATE kunden_zugaenge SET status=?, link_hash=NULL, link_bis=NULL WHERE id=?",
              ("gesperrt" if gesperrt else "aktiv", zid))
    if gesperrt:
        c.execute("DELETE FROM kunden_sitzungen WHERE zugang_id=?", (zid,))
    firma.log(c, wer, "projekt", "projekt", z["projekt_id"],
              f"Zugang zum Kunden-Bereich für {z['email']} {'gesperrt' if gesperrt else 'wieder frei'}.")


def link_pruefen(c, token):
    """Ist der Link gültig? Gibt den Zugang zurück (ohne ihn zu verbrauchen; die Seite fragt erst nach einem Klick,
    damit Virenscanner in Mailprogrammen den Link nicht verbrauchen)."""
    if not token:
        return None
    row = c.execute("SELECT * FROM kunden_zugaenge WHERE link_hash=? AND status='aktiv'", (_hash(token),)).fetchone()
    if not row or not row["link_bis"] or row["link_bis"] <= now():
        return None
    return dict(row)


def anmelden(c, token):
    """Link einlösen: einmal gültig. Gibt (Sitzungs-Token, Tage) zurück."""
    from . import firma
    z = link_pruefen(c, token)
    if not z:
        raise PortalError("Dieser Link ist abgelaufen oder wurde schon benutzt. Fordern Sie einfach einen neuen an.")
    sitzung = secrets.token_urlsafe(32)
    tage = regeln(c)["sitzung_tage"]
    c.execute("UPDATE kunden_zugaenge SET link_hash=NULL, link_bis=NULL, letzter_login=? WHERE id=?", (now(), z["id"]))
    c.execute("DELETE FROM kunden_sitzungen WHERE bis < ?", (now(),))
    c.execute("INSERT INTO kunden_sitzungen (token_hash, zugang_id, erstellt_am, bis) VALUES (?,?,?,?)",
              (_hash(sitzung), z["id"], now(), (datetime.now() + timedelta(days=tage)).isoformat(timespec="seconds")))
    firma.log(c, "Kunde", "portal", "projekt", z["projekt_id"], f"{z['email']} hat sich im Kunden-Bereich angemeldet.",
              commit=False)
    c.commit()
    return sitzung, tage


def sitzung(c, token):
    """Zugang zur Sitzung (oder None)."""
    if not token:
        return None
    row = c.execute("SELECT z.* FROM kunden_sitzungen s JOIN kunden_zugaenge z ON z.id = s.zugang_id "
                    "WHERE s.token_hash=? AND s.bis > ? AND z.status='aktiv'", (_hash(token), now())).fetchone()
    return dict(row) if row else None


def abmelden(c, token):
    if token:
        c.execute("DELETE FROM kunden_sitzungen WHERE token_hash=?", (_hash(token),))
        c.commit()


def link_anfordern(c, email, basis=""):
    """Der Kunde gibt seine Adresse ein und bekommt einen neuen Link, wenn es einen aktiven Zugang gibt. Höchstens
    einmal in zehn Minuten. Antwortet immer gleich, damit niemand ausprobieren kann, welche Adressen Kunden sind."""
    from . import projekte
    email = (email or "").strip().lower()
    vor10 = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
    for z in c.execute("SELECT * FROM kunden_zugaenge WHERE email=? AND status='aktiv'", (email,)).fetchall():
        if z["link_angefordert"] and z["link_angefordert"] > vor10:
            continue
        p = projekte.get(c, z["projekt_id"])
        if not p or p["status"] == "beendet":
            continue
        token = _neuer_link(c, z["id"])
        c.commit()
        r = regeln(c)
        text = _fuellen(r["text_zugang"], name=z["name"] or "", link=f"{basis_url(basis)}/portal/login/{token}",
                        tage=r["link_tage"])
        try:
            mail_an_kunde(c, p, email, r["betreff_zugang"], text.replace("Guten Tag ,", "Guten Tag,"), "Kunde")
        except PortalError:
            pass  # das Team sieht den Fehler im Protokoll des Postfachs; der Kunde bekommt dieselbe Antwort
    return "Wenn wir die Adresse kennen, ist ein neuer Link unterwegs. Schauen Sie in Ihr Postfach."

# ---------------------------------------------------------------- Was der Kunde sieht


def ansicht(c, z):
    """Alles für die Portal-Seite eines Zugangs, ohne interne Daten (keine Zeiten, Kosten, Notizen, Namen intern)."""
    from . import projekte
    p = projekte.get(c, z["projekt_id"])
    if not p:
        return None
    r = regeln(c)
    schritte = [(k, *SCHRITTE[k]) for k, _, _ in projekte.STATUS if k != "beendet"]
    termine = []
    if r["zeige_termine"]:
        from . import kalender
        jetzt = datetime.now().strftime("%Y-%m-%dT%H:%M")
        for t in c.execute("SELECT id FROM termine WHERE lead_id=? AND status='geplant' AND ende >= ? ORDER BY start "
                           "LIMIT 10", (p["lead_id"], jetzt)).fetchall():
            t = kalender.get(c, t["id"])
            termine.append({"titel": t["titel"], "zeit": t["zeit"], "ort": t["ort"], "meet_link": t["meet_link"]})
    aenderungen = [{"id": a["id"], "text": a["text"].split("\n\n(aus dem Kunden-Bereich")[0], "am": a["erstellt_am"],
                    "status": AENDERUNG_STATUS.get(a["status"], "In Arbeit"),
                    "erledigt": a["status"] == "erledigt"}
                   for a in projekte.aenderungen(c, p["id"])]
    return {
        "p": {"id": p["id"], "titel": p["titel"], "kunde": p["kunde"], "status": p["status"],
              "schritt": SCHRITTE.get(p["status"], (p["status_label"], ""))[0],
              "erklaerung": SCHRITTE.get(p["status"], ("", ""))[1], "index": p["status_index"],
              "anforderung": p["anforderung"], "bestaetigt_am": p["kunde_bestaetigt_am"],
              "kann_bestaetigen": p["status"] == "anforderung" and bool(p["anforderung"]) and not p["kunde_bestaetigt_am"],
              "bau_ende_plan": p["bau_ende_plan"], "live_am": p["live_am"], "kostenlos_bis": p["kostenlos_bis"],
              "verantwortlich_name": p["verantwortlich_name"]},
        "schritte": schritte, "termine": termine, "aenderungen": aenderungen,
        "punkte": [{"text": x["text"], "erledigt": bool(x["erledigt"])} for x in p["punkte"]]
        if r["zeige_abnahme"] and p["status"] in ("abnahme", "live", "betrieb") else [],
        "dokumente": dokumente(c, p["id"], nur_sichtbar=True),
        "berichte": [b for b in berichte(c, p["id"]) if b["status"] == "verschickt"],
        "z": {"email": z["email"], "name": z["name"]},
    }


def bestaetigen(c, z):
    """Der Kunde bestätigt die Anforderung selbst (statt dass es jemand von Hand vermerkt)."""
    from . import aufgaben, firma, projekte
    p = projekte.get(c, z["projekt_id"])
    if not (p and p["status"] == "anforderung" and p["anforderung"]):
        raise PortalError("Hier gibt es gerade nichts zu bestätigen.")
    if p["kunde_bestaetigt_am"]:
        return
    c.execute("UPDATE projekte SET kunde_bestaetigt_am=? WHERE id=?", (now(), p["id"]))
    firma.log(c, "Kunde", "projekt", "projekt", p["id"], f"Kunde hat die Anforderung im Kunden-Bereich bestätigt "
              f"({z['email']}).")
    for person in {p["verantwortlich"], p["technik"]} - {None}:
        aufgaben.melden(c, person, f"Anforderung bestätigt: {p['kunde']}", "Der Kunde hat im Kunden-Bereich "
                        "bestätigt. Jetzt freigeben.", f"/kunden/{p['id']}")


def wunsch(c, z, text):
    from . import projekte
    text = (text or "").strip()
    if len(text) < 5:
        raise PortalError("Bitte beschreiben Sie kurz, was Sie sich wünschen.")
    try:
        return projekte.aenderung_anlegen(c, "Kunde", z["projekt_id"], f"{text}\n\n(aus dem Kunden-Bereich, {z['email']})",
                                          quelle="portal")
    except projekte.ProjektError as exc:
        raise PortalError(str(exc)) from exc

# ---------------------------------------------------------------- Dokumente


def dokumente(c, pid, nur_sichtbar=False):
    rows = c.execute("SELECT d.*, t.name AS von_name FROM dokumente d LEFT JOIN team t ON t.key = d.hochgeladen_von "
                     f"WHERE d.projekt_id=? {'AND d.sichtbar=1' if nur_sichtbar else ''} ORDER BY d.id DESC", (pid,))
    return [dict(r, art_label=DOK_LABEL.get(r["art"], r["art"])) for r in rows]


def dokument(c, did):
    row = c.execute("SELECT * FROM dokumente WHERE id=?", (did,)).fetchone()
    return dict(row) if row else None


def dok_ordner(data_dir):
    d = Path(data_dir) / "dokumente"
    d.mkdir(parents=True, exist_ok=True)
    return d


def dokument_speichern(c, wer, pid, datei, titel, art, sichtbar, data_dir):
    """`datei`: werkzeug FileStorage. Gespeichert unter zufälligem Namen (nie der Name vom Upload)."""
    from . import firma, projekte
    p = projekte.get(c, pid)
    if not p:
        raise PortalError("Dieses Projekt gibt es nicht.")
    if not datei or not datei.filename:
        raise PortalError("Bitte eine Datei wählen.")
    endung = Path(datei.filename).suffix.lower()
    if endung not in ENDUNGEN:
        raise PortalError(f"Erlaubt sind {', '.join(sorted(ENDUNGEN))}.")
    if art not in DOK_LABEL:
        art = "sonstiges"
    titel = re.sub(r"\s+", " ", titel or "").strip()[:120] or Path(datei.filename).stem[:120]
    name = secrets.token_hex(16) + endung
    ziel = dok_ordner(data_dir) / name
    datei.save(ziel)
    groesse = ziel.stat().st_size
    if groesse > MAX_MB * 1024 * 1024:
        ziel.unlink(missing_ok=True)
        raise PortalError(f"Die Datei ist größer als {MAX_MB} MB.")
    did = c.execute("INSERT INTO dokumente (projekt_id, titel, art, datei, dateiname, groesse, sichtbar, hochgeladen_von, "
                    "hochgeladen_am) VALUES (?,?,?,?,?,?,?,?,?)",
                    (pid, titel, art, name, Path(datei.filename).name[:200], groesse, 1 if sichtbar else 0, wer,
                     now())).lastrowid
    firma.log(c, wer, "projekt", "projekt", pid, f"Dokument hochgeladen: {titel} ({DOK_LABEL[art]})"
              + ("" if sichtbar else ", nur intern"), {"dokument": did})
    return did


def dokument_aendern(c, wer, did, sichtbar=None, loeschen=False, data_dir=None):
    from . import firma
    d = dokument(c, did)
    if not d:
        raise PortalError("Dieses Dokument gibt es nicht.")
    if loeschen:
        c.execute("DELETE FROM dokumente WHERE id=?", (did,))
        if data_dir:
            (dok_ordner(data_dir) / d["datei"]).unlink(missing_ok=True)
        firma.log(c, wer, "projekt", "projekt", d["projekt_id"], f"Dokument gelöscht: {d['titel']}")
        return
    c.execute("UPDATE dokumente SET sichtbar=? WHERE id=?", (1 if sichtbar else 0, did))
    firma.log(c, wer, "projekt", "projekt", d["projekt_id"],
              f"Dokument {d['titel']}: {'für den Kunden sichtbar' if sichtbar else 'nur intern'}.")

# ---------------------------------------------------------------- Wochenbericht


def berichte(c, pid):
    return [dict(r) for r in c.execute("SELECT * FROM wochenberichte WHERE projekt_id=? ORDER BY id DESC LIMIT 20",
                                       (pid,))]


def bericht_get(c, bid):
    row = c.execute("SELECT * FROM wochenberichte WHERE id=?", (bid,)).fetchone()
    return dict(row) if row else None


def bericht_text(c, p, woche):
    """Aus den Daten: wo das Projekt steht, was diese Woche erledigt wurde, was offen ist, was als Nächstes kommt."""
    from . import kpis, projekte
    von, bis = kpis.woche_grenzen(woche)
    schritt, erklaerung = SCHRITTE.get(p["status"], (p["status_label"], ""))
    name = p["contact_name"] or ""
    zeilen = [f"Guten Tag{' ' + name if name else ''},", "", f"hier der Stand Ihres Projekts „{p['titel']}“ diese Woche.",
              "", f"Stand: {schritt}. {erklaerung}"]
    if p["status"] == "bau" and p["bau_ende_plan"]:
        zeilen.append(f"Geplant fertig: {date.fromisoformat(p['bau_ende_plan'][:10]).strftime('%d.%m.%Y')}.")
    alle = projekte.aenderungen(c, p["id"])
    erledigt = [a for a in alle if a["status"] == "erledigt" and (a.get("erledigt_am") or "") >= von
                and (a.get("erledigt_am") or "") < bis]
    offen = [a for a in alle if a["status"] not in ("erledigt", "abgelehnt")]
    if erledigt:
        zeilen += ["", "Diese Woche umgesetzt:"] + [f"- {a['text'].splitlines()[0][:120]}" for a in erledigt]
    if offen:
        zeilen += ["", "In Arbeit oder eingeplant:"] + [
            f"- {a['text'].splitlines()[0][:120]} ({AENDERUNG_STATUS.get(a['status'], 'In Arbeit')})" for a in offen[:8]]
    naechste = c.execute("SELECT titel, start FROM termine WHERE lead_id=? AND status='geplant' AND start >= ? "
                         "ORDER BY start LIMIT 3", (p["lead_id"], bis)).fetchall()
    if naechste:
        zeilen += ["", "Nächste Termine:"] + [
            f"- {datetime.fromisoformat(t['start']).strftime('%d.%m. %H:%M')} Uhr: {t['titel']}" for t in naechste]
    link = basis_url()
    if link:
        zeilen += ["", f"Alles im Detail in Ihrem Kunden-Bereich: {link}/portal"]
    zeilen += ["", "Bei Fragen antworten Sie einfach auf diese Mail."]
    return "\n".join(zeilen)


def bericht_anlegen(c, pid, woche, wer="System"):
    """Bericht für die Woche anlegen und nach der Freigabe-Stufe behandeln (B: geht nach der Frist raus, C: wartet).
    Gibt die Bericht-ID zurück oder None (schon da, kein Zugang, keine Adresse)."""
    from . import aufgaben, firma, freigaben, projekte
    p = projekte.get(c, pid)
    if not p or not p["verantwortlich"]:
        return None
    if not empfaenger(c, p):
        return None
    if c.execute("SELECT 1 FROM wochenberichte WHERE projekt_id=? AND woche=?", (pid, woche)).fetchone():
        return None
    kw = woche.split("-W")[1].lstrip("0")
    betreff = _fuellen(regeln(c)["betreff_bericht"], kunde=p["kunde"] or p["titel"], kw=kw)
    bid = c.execute("INSERT INTO wochenberichte (projekt_id, woche, betreff, text, erstellt_am) VALUES (?,?,?,?,?)",
                    (pid, woche, betreff[:200], bericht_text(c, p, woche), now())).lastrowid
    c.commit()
    stufe = freigaben.stufe(c, "wochenbericht")
    if stufe == "B":
        _, frist = freigaben.b_anlegen(c, wer, "wochenbericht", "wochenbericht", bid, p["verantwortlich"])
        aufgaben.melden(c, p["verantwortlich"], f"Wochenbericht geht raus: {p['kunde']}",
                        f"automatisch am {frist[8:10]}.{frist[5:7]}. um {frist[11:16]}, wenn niemand stoppt",
                        "/freigaben")
    else:
        c.execute("INSERT INTO freigaben (art, objekt_typ, objekt_id, stufe, angefragt_von, angefragt_am, pruefer) "
                  "VALUES ('wochenbericht', 'wochenbericht', ?, 'C', ?, ?, ?)", (bid, wer, now(), p["verantwortlich"]))
        c.commit()
        aufgaben.melden(c, p["verantwortlich"], f"Wochenbericht freigeben: {p['kunde']}", betreff, "/freigaben")
    firma.log(c, wer, "projekt", "projekt", pid, f"Wochenbericht KW {kw} vorbereitet (Stufe {stufe}).", {"bericht": bid})
    return bid


def empfaenger(c, p):
    """An wen der Bericht geht: aktive Zugänge des Projekts, sonst die Adresse aus der Akte."""
    mails = [r["email"] for r in c.execute("SELECT email FROM kunden_zugaenge WHERE projekt_id=? AND status='aktiv'",
                                           (p["id"],))]
    return mails or ([p["kunde_email"]] if p.get("kunde_email") else [])


def bericht_bearbeiten(c, wer, bid, betreff, text):
    from . import firma, projekte
    b = bericht_get(c, bid)
    if not b or b["status"] != "wartet":
        raise PortalError("Dieser Bericht ist schon verschickt oder gestoppt.")
    if not darf_zugang(c, wer, projekte.get(c, b["projekt_id"])):
        raise PortalError("Bearbeiten darf die verantwortliche Person (oder wer Kunden verwaltet).")
    if not (betreff or "").strip() or len((text or "").strip()) < 10:
        raise PortalError("Bitte Betreff und Text.")
    c.execute("UPDATE wochenberichte SET betreff=?, text=? WHERE id=?", (betreff.strip()[:200], text.strip()[:8000], bid))
    firma.log(c, wer, "projekt", "projekt", b["projekt_id"], "Wochenbericht vor dem Versand bearbeitet.")


def bericht_entscheiden(c, wer, bid, ok, grund=""):
    """Aus der Freigabe-Zentrale (oder vom Takt nach der Frist): verschicken oder nicht."""
    from . import aufgaben, firma, projekte
    b = bericht_get(c, bid)
    f = c.execute("SELECT * FROM freigaben WHERE art='wochenbericht' AND objekt_id=? AND status='offen' ORDER BY id "
                  "DESC LIMIT 1", (str(bid),)).fetchone()
    if not b or not f or b["status"] != "wartet":
        raise PortalError("Dieser Bericht wartet nicht auf Freigabe.")
    p = projekte.get(c, b["projekt_id"])
    system = wer == "System" and f["stufe"] == "B"
    if not system and not (wer == f["pruefer"] or darf_zugang(c, wer, p)):
        raise PortalError("Freigeben darf die verantwortliche Person (oder wer Kunden verwaltet).")
    grund = (grund or "").strip()
    if not ok and len(grund) < 3:
        raise PortalError("Bitte einen Grund angeben.")
    if ok:
        for to in empfaenger(c, p):
            mail_an_kunde(c, p, to, b["betreff"], b["text"], p["verantwortlich"] if system else wer)
    c.execute("UPDATE freigaben SET status=?, grund=?, entschieden_von=?, entschieden_am=? WHERE id=?",
              ("angenommen" if ok else "abgelehnt", grund or None, wer, now(), f["id"]))
    c.execute("UPDATE wochenberichte SET status=?, verschickt_am=? WHERE id=?",
              ("verschickt" if ok else "gestoppt", now() if ok else None, bid))
    firma.log(c, wer, "projekt", "projekt", p["id"], f"Wochenbericht {'verschickt' if ok else 'nicht verschickt'}"
              + (" (Stufe B, Frist ohne Einspruch abgelaufen)" if system else "") + (f": {grund}" if grund else "."),
              {"bericht": bid}, commit=False)
    c.commit()
    if system:
        aufgaben.melden(c, p["verantwortlich"], f"Wochenbericht verschickt: {p['kunde']}", b["betreff"],
                        f"/kunden/{p['id']}")

# ---------------------------------------------------------------- Termin-Erinnerung an Kunden


def erinnerung_entscheiden(c, wer, tid, ok, grund=""):
    from . import firma, kalender, leaddb, projekte
    f = c.execute("SELECT * FROM freigaben WHERE art='erinnerung_kunde' AND objekt_id=? AND status='offen' ORDER BY id "
                  "DESC LIMIT 1", (str(tid),)).fetchone()
    t = kalender.get(c, tid)
    if not f or not t:
        raise PortalError("Diese Erinnerung wartet nicht auf Freigabe.")
    system = wer == "System" and f["stufe"] == "B"
    if not system and wer != f["pruefer"] and not firma.darf(c, wer, "kalender", firma.VERWALTEN):
        raise PortalError("Freigeben darf die verantwortliche Person des Termins.")
    grund = (grund or "").strip()
    if not ok and len(grund) < 3:
        raise PortalError("Bitte einen Grund angeben.")
    if ok:
        if t["status"] != "geplant" or t["start"] <= datetime.now().strftime("%Y-%m-%dT%H:%M"):
            raise PortalError("Der Termin ist abgesagt oder schon vorbei.")
        _erinnerung_senden(c, t, wer if not system else t["verantwortlich"], projekte, leaddb)
    c.execute("UPDATE freigaben SET status=?, grund=?, entschieden_von=?, entschieden_am=? WHERE id=?",
              ("angenommen" if ok else "abgelehnt", grund or None, wer, now(), f["id"]))
    c.commit()


def _erinnerung_senden(c, t, wer, projekte, leaddb):
    p = projekte.zum_lead(c, t["lead_id"]) or {"lead_id": t["lead_id"], "verantwortlich": t["verantwortlich"]}
    lead = leaddb.get_lead(c, t["lead_id"])
    r = regeln(c)
    for to in t["gaeste"]:
        text = _fuellen(r["text_erinnerung"], name=(lead or {}).get("contact_name") or "", zeit=t["zeit"],
                        titel=t["titel"], meet=f"\n\nZum Termin: {t['meet_link']}" if t["meet_link"] else "")
        mail_an_kunde(c, p, to, f"Erinnerung: {t['titel']}", text.replace("Guten Tag ,", "Guten Tag,"), wer)


def takt(c, jetzt=None):
    """Hintergrund-Arbeiter: Wochenberichte am eingestellten Tag, Termin-Erinnerungen an Kunden 24 h vorher."""
    from . import firma, freigaben, kalender, kpis, leaddb, projekte
    jetzt = jetzt or datetime.now()
    r = regeln(c)
    n = {"berichte": 0, "erinnerungen": 0}
    if jetzt.weekday() == r["bericht_tag"] and jetzt.hour >= r["bericht_stunde"]:
        woche = kpis.woche_von(jetzt.date())
        for row in c.execute("SELECT id FROM projekte WHERE status IN ('bau','abnahme','live','betrieb')").fetchall():
            if bericht_anlegen(c, row["id"], woche):
                n["berichte"] += 1
    stufe = freigaben.stufe(c, "erinnerung_kunde")
    rows = c.execute("SELECT id FROM termine WHERE status='geplant' AND lead_id IS NOT NULL AND start > ? AND start <= ? "
                     "AND instr(erinnert, 'kunde') = 0",
                     (jetzt.strftime("%Y-%m-%dT%H:%M"), (jetzt + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M"))
                     ).fetchall()
    for row in rows:
        t = kalender.get(c, row["id"])
        c.execute("UPDATE termine SET erinnert = erinnert || 'kunde,' WHERE id=?", (t["id"],))
        c.commit()
        if not t["gaeste"]:
            continue
        if stufe == "A":
            try:
                _erinnerung_senden(c, t, t["verantwortlich"], projekte, leaddb)
                firma.log(c, "System", "termin", "termin", t["id"], f"Erinnerung an den Kunden verschickt: {t['titel']}")
                n["erinnerungen"] += 1
            except PortalError as exc:
                firma.log(c, "System", "termin", "termin", t["id"], f"Erinnerung an den Kunden nicht verschickt: {exc}")
        else:
            c.execute("INSERT INTO freigaben (art, objekt_typ, objekt_id, stufe, angefragt_von, angefragt_am, pruefer) "
                      "VALUES ('erinnerung_kunde', 'termin', ?, 'C', 'System', ?, ?)", (t["id"], now(), t["verantwortlich"]))
            c.commit()
            from . import aufgaben
            aufgaben.melden(c, t["verantwortlich"], f"Erinnerung an Kunden freigeben: {t['titel']}", t["zeit"],
                            "/freigaben")
    return n
