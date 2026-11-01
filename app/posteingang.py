"""Ein Posteingang (Firmen-OS, Stufe 9, docs/FIRMEN-OS.md Abschnitt 4.8).

Alle Nachrichten mit Kunden und Leads liegen in `messages` (siehe mailbox.py) – mit Kanal (Mail, Telefon, Brief,
WhatsApp, LinkedIn, Kunden-Bereich), Postfach und Akte. Dazu:

- **Weitere Postfächer** (beliebig viele, z. B. kontakt@ und persönliche Adressen) mit IMAP/SMTP-Zugang in den
  Einstellungen; je Postfach, wem es gehört (eine Person oder das Team). Das Passwort liegt verschlüsselt in der
  Datenbank. Der Takt holt neue Mails, Antworten gehen aus demselben Postfach raus.
- **Von Hand festhalten:** Telefonat, Brief, WhatsApp- oder LinkedIn-Nachricht als Nachricht an der Akte.
  WhatsApp und LinkedIn über Unipile sind vorbereitet (Zugang in den Einstellungen), aber noch nicht angebunden.
- **Wünsche per Mail:** Schreibt ein Kunde mit laufendem Projekt, markiert das OS die Nachricht als möglichen
  Änderungswunsch. Die verantwortliche Person übernimmt ihn mit einem Klick (oder nicht).
- **Wartet auf Antwort seit** und **Antwortzeit** (KPI) aus den Nachrichten.
"""
import email
import email.policy
import imaplib
import re
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr

SCHEMA = """
CREATE TABLE IF NOT EXISTS postfaecher (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, adresse TEXT NOT NULL, benutzer TEXT NOT NULL,
    passwort TEXT, imap_host TEXT NOT NULL, imap_port INTEGER NOT NULL DEFAULT 993, smtp_host TEXT NOT NULL,
    smtp_port INTEGER NOT NULL DEFAULT 587, smtp_sicherheit TEXT NOT NULL DEFAULT 'starttls', besitzer TEXT,
    aktiv INTEGER NOT NULL DEFAULT 1, start TEXT, abgerufen_am TEXT, fehler TEXT, erstellt_von TEXT,
    erstellt_am TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS postfach_gesehen (
    postfach_id INTEGER NOT NULL, message_id TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (postfach_id, message_id)
);
"""

KANAELE = {"mail": "Mail", "telefon": "Telefon", "brief": "Brief", "whatsapp": "WhatsApp", "linkedin": "LinkedIn",
           "portal": "Kunden-Bereich"}
VON_HAND = ["telefon", "brief", "whatsapp", "linkedin"]
START_TAGE = 3  # beim ersten Abruf eines Postfachs: Mails der letzten drei Tage übernehmen
ABRUF_MINUTEN = 10


class PosteingangError(ValueError):
    pass


def now():
    return datetime.now().isoformat(timespec="seconds")


def _fernet():
    from . import agent, config_store
    return config_store._fernet(agent.CFG.get("data_dir"))

# ---------------------------------------------------------------- Postfächer


def postfaecher(c, nur_aktive=False):
    rows = c.execute("SELECT p.*, t.name AS besitzer_name FROM postfaecher p LEFT JOIN team t ON t.key = p.besitzer "
                     f"{'WHERE p.aktiv=1' if nur_aktive else ''} ORDER BY p.name").fetchall()
    return [dict(r, passwort=None, hat_passwort=bool(r["passwort"])) for r in rows]


def postfach(c, pid):
    row = c.execute("SELECT * FROM postfaecher WHERE id=?", (pid,)).fetchone()
    return dict(row) if row else None


def fuer_person(c, person):
    """Postfächer, aus denen `person` senden darf: eigene und die des Teams."""
    return [p for p in postfaecher(c, nur_aktive=True) if not p["besitzer"] or p["besitzer"] == person]


def postfach_speichern(c, wer, pid, form):
    from . import firma
    name = re.sub(r"\s+", " ", form.get("name") or "").strip()[:80]
    adresse = (form.get("adresse") or "").strip().lower()
    benutzer = (form.get("benutzer") or adresse).strip()
    if not name:
        raise PosteingangError("Bitte einen Namen für das Postfach (z. B. „Kontakt“ oder „Oskar“).")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", adresse):
        raise PosteingangError("Bitte eine gültige Mailadresse.")
    imap_host, smtp_host = (form.get("imap_host") or "").strip(), (form.get("smtp_host") or "").strip()
    if not imap_host or not smtp_host:
        raise PosteingangError("Bitte Posteingang (IMAP) und Postausgang (SMTP) eintragen.")
    try:
        imap_port, smtp_port = int(form.get("imap_port") or 993), int(form.get("smtp_port") or 587)
    except ValueError as exc:
        raise PosteingangError("Ports bitte als Zahl.") from exc
    sicherheit = form.get("smtp_sicherheit") if form.get("smtp_sicherheit") in ("starttls", "ssl") else "starttls"
    besitzer = form.get("besitzer") or None
    if besitzer and not firma.aktive_person(c, besitzer):
        raise PosteingangError("Diese Person gibt es nicht (mehr).")
    passwort = form.get("passwort") or ""
    enc = _fernet().encrypt(passwort.encode()).decode() if passwort else None
    aktiv = 1 if form.get("aktiv") in ("1", "on", True) else 0
    if pid:
        alt = postfach(c, pid)
        if not alt:
            raise PosteingangError("Dieses Postfach gibt es nicht.")
        c.execute("UPDATE postfaecher SET name=?, adresse=?, benutzer=?, passwort=COALESCE(?, passwort), imap_host=?, "
                  "imap_port=?, smtp_host=?, smtp_port=?, smtp_sicherheit=?, besitzer=?, aktiv=?, fehler=NULL WHERE id=?",
                  (name, adresse, benutzer, enc, imap_host, imap_port, smtp_host, smtp_port, sicherheit, besitzer, aktiv,
                   pid))
    else:
        if not enc:
            raise PosteingangError("Bitte das Passwort des Postfachs.")
        pid = c.execute("INSERT INTO postfaecher (name, adresse, benutzer, passwort, imap_host, imap_port, smtp_host, "
                        "smtp_port, smtp_sicherheit, besitzer, aktiv, start, erstellt_von, erstellt_am) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (name, adresse, benutzer, enc, imap_host, imap_port, smtp_host, smtp_port, sicherheit, besitzer,
                         aktiv, (datetime.now() - timedelta(days=START_TAGE)).isoformat(timespec="seconds"), wer,
                         now())).lastrowid
    firma.log(c, wer, "einstellung", "postfach", pid, f"Postfach „{name}“ ({adresse}) gespeichert"
              + (f", gehört {firma.name_von(c, besitzer)}" if besitzer else ", fürs Team") + ".")
    return pid


def postfach_loeschen(c, wer, pid):
    from . import firma
    p = postfach(c, pid)
    if not p:
        raise PosteingangError("Dieses Postfach gibt es nicht.")
    c.execute("DELETE FROM postfaecher WHERE id=?", (pid,))
    c.execute("DELETE FROM postfach_gesehen WHERE postfach_id=?", (pid,))
    firma.log(c, wer, "einstellung", "postfach", pid, f"Postfach „{p['name']}“ entfernt (Nachrichten bleiben in den Akten).")


def _passwort(p):
    try:
        return _fernet().decrypt((p["passwort"] or "").encode()).decode()
    except Exception as exc:
        raise PosteingangError("Das Passwort des Postfachs lässt sich nicht lesen. Bitte neu eintragen.") from exc


def _imap_verbinden(p):
    imap = imaplib.IMAP4_SSL(p["imap_host"], p["imap_port"], ssl_context=ssl.create_default_context())
    imap.login(p["benutzer"], _passwort(p))
    return imap


def _smtp_verbinden(p):
    ctx = ssl.create_default_context()
    if p["smtp_sicherheit"] == "ssl":
        smtp = smtplib.SMTP_SSL(p["smtp_host"], p["smtp_port"], timeout=30, context=ctx)
    else:
        smtp = smtplib.SMTP(p["smtp_host"], p["smtp_port"], timeout=30)
        smtp.ehlo()
        smtp.starttls(context=ctx)
        smtp.ehlo()
    smtp.login(p["benutzer"], _passwort(p))
    return smtp


# In Tests ersetzbar
IMAP = _imap_verbinden
SMTP = _smtp_verbinden

# ---------------------------------------------------------------- Abrufen


def abrufen(c, pid):
    """Neue Mails eines Postfachs holen. Zuordnung zur Akte wie beim Akquise-Postfach (Antwort auf unsere Mail,
    Absender, Domain), sonst neuer Kontakt. Newsletter und automatische Mails bleiben draußen."""
    from . import agent
    p = postfach(c, pid)
    if not p or not p["aktiv"]:
        return 0
    start = datetime.fromisoformat(p["start"] or now())
    since = max(start, datetime.now() - timedelta(days=30)) - timedelta(days=1)
    neu = 0
    try:
        imap = IMAP(p)
        try:
            imap.select("INBOX", readonly=True)
            _, data = imap.search(None, f"(SINCE {since.day:02d}-{agent.MONTHS[since.month - 1]}-{since.year})")
            for num in (data[0] or b"").split()[-300:]:
                _, parts = imap.fetch(num, "(BODY.PEEK[])")
                raw = next((x[1] for x in parts if isinstance(x, tuple)), b"")
                msg = email.message_from_bytes(raw, policy=email.policy.default)
                mid = (msg.get("Message-ID") or "").strip() or f"num:{num.decode()}:{p['start']}"
                if c.execute("SELECT 1 FROM postfach_gesehen WHERE postfach_id=? AND message_id=?", (pid, mid)).fetchone():
                    continue
                if _einsortieren(c, p, msg, mid, start):
                    neu += 1
                c.execute("INSERT OR IGNORE INTO postfach_gesehen (postfach_id, message_id, at) VALUES (?,?,?)",
                          (pid, mid, now()))
                c.commit()
        finally:
            try:
                imap.logout()
            except Exception:
                pass
    except PosteingangError as exc:
        _fehler(c, pid, str(exc))
        raise
    except Exception as exc:
        _fehler(c, pid, agent.mail_error(exc))
        raise PosteingangError(agent.mail_error(exc)) from exc
    c.execute("UPDATE postfaecher SET abgerufen_am=?, fehler=NULL WHERE id=?", (now(), pid))
    c.commit()
    return neu


def _fehler(c, pid, text):
    c.execute("UPDATE postfaecher SET abgerufen_am=?, fehler=? WHERE id=?", (now(), text[:500], pid))
    c.commit()


def _einsortieren(c, p, msg, mid, start):
    from email.utils import parsedate_to_datetime
    from . import agent, aufgaben, leaddb, mailbox
    name, sender = parseaddr(msg.get("From", ""))
    sender = sender.lower()
    if not sender or sender == p["adresse"] or str(msg.get("Subject", "")).startswith("[Leads]"):
        return False
    try:
        gesendet = parsedate_to_datetime(msg.get("Date")).astimezone().replace(tzinfo=None)
    except (TypeError, ValueError):
        gesendet = None
    if gesendet and gesendet < start:
        return False
    if c.execute("SELECT 1 FROM messages WHERE message_id=?", (mid,)).fetchone():
        return False  # schon über ein anderes Postfach da (z. B. an zwei Adressen geschickt)
    lead_id = agent._match_lead(c, msg)
    if not lead_id:
        if agent._is_bulk(msg):
            return False
        lead_id = agent._contact_for(c, msg)
    text = agent._message_text(msg) or "(leere Nachricht)"
    subject = str(msg.get("Subject", ""))
    mid_db = mailbox.add(c, lead_id, "in", text, subject=subject, from_addr=sender, from_name=name, to_addr=p["adresse"],
                         message_id=mid, in_reply_to=(msg.get("In-Reply-To") or "").strip() or None, read=False,
                         kanal="mail", postfach_id=p["id"])
    lead = leaddb.get_lead(c, lead_id)
    leaddb.update_lead(c, lead_id, unread=1)
    leaddb.add_event(c, lead_id, "reply", text, author=name or sender,
                     meta={"from": sender, "subject": subject, "postfach": p["name"]})
    fuer = p["besitzer"] or lead.get("owner")
    titel = f"Neue Mail von {lead['name']}"
    if fuer:
        aufgaben.melden(c, fuer, titel, (text or subject)[:200], f"/crm/postfach/{lead_id}")
    else:
        leaddb.add_notice(c, titel, (text or subject)[:200], f"/crm/postfach/{lead_id}")
    nach_eingang(c, lead_id, mid_db)
    return True


def takt(c):
    """Hintergrund-Arbeiter: aktive Postfächer alle zehn Minuten abrufen (Fehler stehen am Postfach)."""
    grenze = (datetime.now() - timedelta(minutes=ABRUF_MINUTEN)).isoformat(timespec="seconds")
    n = 0
    for p in c.execute("SELECT id FROM postfaecher WHERE aktiv=1 AND passwort IS NOT NULL AND "
                       "(abgerufen_am IS NULL OR abgerufen_am < ?)", (grenze,)).fetchall():
        try:
            n += abrufen(c, p["id"])
        except PosteingangError:
            pass
    return n

# ---------------------------------------------------------------- Senden


def senden(c, wer, pid, lead, to, betreff, text, in_reply_to=None):
    """Aus einem weiteren Postfach antworten oder schreiben (mit Unterschrift der Person), in der Akte vermerkt."""
    from . import agent, crm, firma, leaddb, mailbox, team
    p = postfach(c, pid)
    if not p or not p["aktiv"]:
        raise PosteingangError("Dieses Postfach gibt es nicht (mehr).")
    if p["besitzer"] and p["besitzer"] != wer:
        raise PosteingangError(f"Aus diesem Postfach schreibt nur {firma.name_von(c, p['besitzer'])}.")
    if lead["status"] == "gesperrt" or sperrliste(c, to):
        raise PosteingangError("Dieser Betrieb möchte keine Nachrichten mehr bekommen.")
    if not mailbox.last_incoming(c, lead["id"]) and lead["status"] != "kontakt":
        ok, why = crm.may_email(lead, agent.S("OUTREACH_EMAIL"))  # Erstkontakt-Regel gilt für jedes Postfach
        if not ok:
            raise PosteingangError(why)
    snd = agent._sender(c, wer)
    msg = EmailMessage()
    msg["From"] = formataddr((snd["display"], p["adresse"]))
    msg["To"] = formataddr((lead.get("contact_name") or "", to))
    msg["Subject"] = betreff
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=p["adresse"].rsplit("@", 1)[-1])
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(f"{text.strip()}\n\n{team.signature(snd)}")
    try:
        smtp = SMTP(p)
        try:
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except Exception:
                pass
    except PosteingangError:
        raise
    except Exception as exc:
        raise PosteingangError(agent.mail_error(exc)) from exc
    mailbox.add(c, lead["id"], "out", text.strip(), subject=betreff, from_addr=p["adresse"], to_addr=to,
                message_id=msg["Message-ID"], in_reply_to=in_reply_to, sent_by=snd["name"] or wer, kanal="mail",
                postfach_id=pid)
    mailbox.mark_read(c, lead["id"])
    leaddb.update_lead(c, lead["id"], unread=0)
    leaddb.add_event(c, lead["id"], "sent", f"Mail aus dem Postfach „{p['name']}“ an {to}.", author=snd["name"] or wer)
    crm.claim(c, lead["id"], wer)
    return msg


def sperrliste(c, adresse):
    """Steht die Adresse oder ihre Domain auf der Sperrliste?"""
    from . import leaddb
    adresse = (adresse or "").strip().lower()
    gesperrt = set(leaddb.kv_get(c, "blocklist", []))
    return adresse in gesperrt or adresse.rsplit("@", 1)[-1] in gesperrt

# ---------------------------------------------------------------- Von Hand festhalten


def festhalten(c, wer, lead_id, kanal, richtung, text, am=None):
    """Telefonat, Brief, WhatsApp- oder LinkedIn-Nachricht an der Akte festhalten."""
    from . import firma, leaddb, mailbox
    lead = leaddb.get_lead(c, lead_id)
    if not lead:
        raise PosteingangError("Diesen Kontakt gibt es nicht.")
    if kanal not in VON_HAND:
        raise PosteingangError("Bitte den Kanal wählen (Telefon, Brief, WhatsApp, LinkedIn).")
    if richtung not in ("in", "out"):
        raise PosteingangError("Kam die Nachricht vom Kunden oder von uns?")
    text = (text or "").strip()
    if len(text) < 3:
        raise PosteingangError("Was wurde gesagt oder geschrieben?")
    am = (am or "").strip()
    if am:
        try:
            am = datetime.fromisoformat(am).isoformat(timespec="seconds")
        except ValueError as exc:
            raise PosteingangError("Bitte Datum und Uhrzeit.") from exc
    name = firma.name_von(c, wer)
    mid = mailbox.add(c, lead_id, richtung, text[:4000], subject=KANAELE[kanal],
                      from_name=(lead.get("contact_name") or lead["name"]) if richtung == "in" else name,
                      sent_by=name if richtung == "out" else None, read=True, at=am or None, kanal=kanal,
                      erfasst_von=wer)
    leaddb.add_event(c, lead_id, "note", f"{KANAELE[kanal]} {'vom Kunden' if richtung == 'in' else 'von uns'}: "
                     f"{text[:300]}", author=name)
    if richtung == "in":
        nach_eingang(c, lead_id, mid)
    return mid

# ---------------------------------------------------------------- Wünsche aus Nachrichten


def _laufendes_projekt(c, lead_id):
    row = c.execute("SELECT id FROM projekte WHERE lead_id=? AND status NOT IN ('beendet') ORDER BY id DESC LIMIT 1",
                    (lead_id,)).fetchone()
    return row["id"] if row else None


def nach_eingang(c, lead_id, message_db_id):
    """Nach jeder eingehenden Nachricht: Hat der Kontakt ein laufendes Projekt, ist es ein möglicher
    Änderungswunsch. Die verantwortliche Person entscheidet mit einem Klick (Vorschlag, nie automatisch)."""
    from . import aufgaben, projekte
    if not message_db_id:
        return
    m = c.execute("SELECT kanal, text FROM messages WHERE id=?", (message_db_id,)).fetchone()
    if not m or m["kanal"] == "portal":  # Wünsche aus dem Kunden-Bereich sind schon Änderungswünsche
        return
    pid = _laufendes_projekt(c, lead_id)
    if not pid:
        return
    c.execute("UPDATE messages SET wunsch='vorschlag' WHERE id=?", (message_db_id,))
    c.commit()
    p = projekte.get(c, pid)
    if p and p["verantwortlich"]:
        aufgaben.melden(c, p["verantwortlich"], f"Möglicher Änderungswunsch: {p['kunde']}", (m["text"] or "")[:160],
                        f"/crm/postfach/{lead_id}#m{message_db_id}")


def wunsch_entscheiden(c, wer, message_db_id, ja):
    from . import firma, projekte
    m = c.execute("SELECT * FROM messages WHERE id=?", (message_db_id,)).fetchone()
    if not m or m["wunsch"] != "vorschlag":
        raise PosteingangError("Hier wartet kein Vorschlag (mehr).")
    pid = _laufendes_projekt(c, m["lead_id"])
    p = projekte.get(c, pid) if pid else None
    if not p:
        raise PosteingangError("Zu diesem Kontakt läuft kein Projekt mehr.")
    if wer != p["verantwortlich"] and not firma.darf(c, wer, "kunden", firma.VERWALTEN):
        raise PosteingangError("Das entscheidet die verantwortliche Person des Projekts.")
    if not ja:
        c.execute("UPDATE messages SET wunsch='nein' WHERE id=?", (message_db_id,))
        firma.log(c, wer, "projekt", "projekt", pid, "Nachricht ist kein Änderungswunsch.")
        return None
    text = (m["text"] or "").strip()
    quelle = KANAELE.get(m["kanal"] or "mail", "Mail")
    aid = projekte.aenderung_anlegen(c, wer, pid, f"{text}\n\n({quelle} vom {m['at'][8:10]}.{m['at'][5:7]}.{m['at'][:4]})",
                                     quelle="mail" if (m["kanal"] or "mail") == "mail" else m["kanal"])
    c.execute("UPDATE messages SET wunsch='uebernommen', aenderung_id=? WHERE id=?", (aid, message_db_id))
    c.commit()
    return aid

# ---------------------------------------------------------------- Wartet auf Antwort, Antwortzeit


def wartet_seit(c, lead_id):
    """Seit wann die älteste unbeantwortete Nachricht des Kunden wartet (oder None)."""
    letzte_raus = c.execute("SELECT MAX(at) FROM messages WHERE lead_id=? AND direction='out'", (lead_id,)).fetchone()[0]
    row = c.execute("SELECT MIN(at) FROM messages WHERE lead_id=? AND direction='in' AND at > ?",
                    (lead_id, letzte_raus or "")).fetchone()
    return row[0]


def wartend(c, person=None):
    """Gespräche, die auf uns warten, mit „wartet seit“ (älteste zuerst). `person`: zuständig oder niemand."""
    extra, args = ("AND (l.owner = ? OR l.owner IS NULL)", [person]) if person else ("", [])
    rows = c.execute(f"""SELECT l.id, l.name, l.owner FROM leads l WHERE EXISTS (SELECT 1 FROM messages m WHERE
                         m.lead_id = l.id AND m.direction='in' AND m.at > IFNULL((SELECT MAX(o.at) FROM messages o
                         WHERE o.lead_id = l.id AND o.direction='out'), '')) {extra}""", args).fetchall()
    out = [{"id": r["id"], "name": r["name"], "owner": r["owner"], "seit": wartet_seit(c, r["id"])} for r in rows]
    return sorted(out, key=lambda x: x["seit"] or "")


def antwortzeit(c, von, bis, person=None):
    """KPI: Stunden von einer Nachricht des Kunden bis zu unserer nächsten Nachricht (Mittel über die Woche).
    Gezählt werden eingehende Nachrichten der Woche, auf die schon geantwortet wurde."""
    extra, args = ("AND l.owner = ?", [person]) if person else ("", [])
    rows = c.execute(f"""SELECT m.at AS rein, (SELECT MIN(o.at) FROM messages o WHERE o.lead_id = m.lead_id AND
                         o.direction='out' AND o.at > m.at) AS raus FROM messages m JOIN leads l ON l.id = m.lead_id
                         WHERE m.direction='in' AND m.at >= ? AND m.at < ? AND m.at > IFNULL((SELECT MAX(p.at) FROM
                         messages p WHERE p.lead_id = m.lead_id AND p.direction='out' AND p.at < m.at), '') {extra}""",
                     [von, bis, *args]).fetchall()
    stunden = [(datetime.fromisoformat(r["raus"]) - datetime.fromisoformat(r["rein"])).total_seconds() / 3600
               for r in rows if r["raus"]]
    return round(sum(stunden) / len(stunden), 1) if stunden else None


def kpi_wartend(c, von, bis, person=None):
    return len(wartend(c, person))
