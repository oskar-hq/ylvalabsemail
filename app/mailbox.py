"""Postfach im CRM: Gespräche mit Betrieben, die geantwortet haben, und direkt antworten.

Jede Nachricht (unsere und ihre) liegt in `messages`. Die erste Nachricht an einen Betrieb steht weiter in der Akte
(`leads.subject/body`), die Erinnerung in `followup_*`; der Verlauf setzt beides davor.
"""
import logging
import re
from email.utils import parseaddr

from . import leaddb, scoring

log = logging.getLogger("ylva.postfach")


def add(c, lead_id, direction, text, subject="", from_addr="", from_name="", to_addr="", message_id=None,
        in_reply_to=None, sent_by=None, read=True, at=None, kanal="mail", postfach_id=None, erfasst_von=None, fehler=0):
    """Eine Nachricht im Posteingang. `kanal`: mail, telefon, brief, whatsapp, linkedin, portal; `postfach_id`: weiteres
    Postfach (None = Akquise-Postfach). Gibt die ID zurück."""
    cur = c.execute("INSERT INTO messages (lead_id, at, direction, from_addr, from_name, to_addr, subject, text, "
                    "message_id, in_reply_to, sent_by, read, kanal, postfach_id, erfasst_von, fehler) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (lead_id, at or leaddb.now(), direction, from_addr, from_name, to_addr, subject, text, message_id,
                     in_reply_to, sent_by, 1 if read else 0, kanal or "mail", postfach_id, erfasst_von, 1 if fehler else 0))
    c.commit()
    return cur.lastrowid


def thread(c, lead):
    """Der ganze Verlauf, älteste zuerst: erste Nachricht, Erinnerung, dann alles aus dem Postfach."""
    items = []
    if lead.get("sent_at") and (lead.get("body") or "").strip():
        text = "\n\n".join(x for x in ((lead.get("greeting") or "").strip(), lead["body"].strip()) if x)
        items.append({"direction": "out", "at": lead["sent_at"], "subject": lead.get("subject") or "", "text": text,
                      "who": "Ihr", "channel": lead.get("channel") or "email"})
    if lead.get("followup_sent_at") and (lead.get("followup_body") or "").strip():
        text = "\n\n".join(x for x in ((lead.get("followup_greeting") or "").strip(), lead["followup_body"].strip()) if x)
        items.append({"direction": "out", "at": lead["followup_sent_at"], "subject": lead.get("followup_subject") or "",
                      "text": text, "who": "Ihr", "channel": "email"})
    for m in c.execute("SELECT m.*, p.name AS postfach_name FROM messages m LEFT JOIN postfaecher p ON p.id = m.postfach_id "
                       "WHERE m.lead_id=? ORDER BY m.at, m.id", (lead["id"],)):
        kanal = m["kanal"] or "mail"
        items.append({"direction": m["direction"], "at": m["at"], "subject": m["subject"] or "", "text": m["text"] or "",
                      "who": (m["sent_by"] or "Ihr") if m["direction"] == "out" else (m["from_name"] or m["from_addr"] or lead["name"]),
                      "channel": "email" if kanal == "mail" else kanal, "kanal": kanal, "id": m["id"],
                      "message_id": m["message_id"], "from_addr": m["from_addr"], "read": m["read"],
                      "wunsch": m["wunsch"], "aenderung_id": m["aenderung_id"], "postfach_name": m["postfach_name"],
                      "postfach_id": m["postfach_id"], "versand": m["versand"] if "versand" in m.keys() else None})
    return sorted(items, key=lambda i: i["at"] or "")


def last_incoming(c, lead_id):
    """Letzte eingehende Mail (für Antwort-Adresse, Betreff, Zitat). Telefonate und andere Kanäle zählen hier nicht."""
    row = c.execute("SELECT * FROM messages WHERE lead_id=? AND direction='in' AND IFNULL(kanal, 'mail') = 'mail' "
                    "AND fehler=0 ORDER BY at DESC, id DESC LIMIT 1", (lead_id,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------- Ordner: Gespräche, Sonstige, Fehler
# „Gespräche“: alle, die wir angeschrieben haben (Leads, Kunden) – nur diese zählen für Glocke und Zähler.
# „Sonstige“: Unbekannte, die uns geschrieben haben und denen wir (noch) nicht geantwortet haben.
# „Fehler“: Zustellfehler und automatische Antworten (Abwesenheit). Im Mailprogramm im Ordner „Fehler“.
ORDNER = {"haupt": "Gespräche", "sonstige": "Sonstige", "fehler": "Fehler"}
_GESCHRIEBEN = "EXISTS (SELECT 1 FROM messages o WHERE o.lead_id = l.id AND o.direction = 'out')"
ORDNER_SQL = {"haupt": f"(l.status != 'kontakt' OR {_GESCHRIEBEN})",
              "sonstige": f"(l.status = 'kontakt' AND NOT {_GESCHRIEBEN})"}
FEHLER_ABSENDER = re.compile(r"^(?:mailer-?daemon|postmaster|mail-?daemon|mailerdaemon)\b", re.I)
FEHLER_BETREFF = re.compile(r"undeliver|unzustellbar|nicht zugestellt|nicht zustellbar|zustellung fehlgeschlagen|"
                            r"delivery status|delivery (?:failure|failed|has failed)|failure notice|returned mail|"
                            r"mail delivery (?:failed|system|subsystem)|message not delivered|zustellfehler", re.I)


def fehler_art(msg, text=""):
    """Zustellfehler („zustellfehler“) oder automatische Antwort („automatisch“), sonst None."""
    from . import scoring
    local = parseaddr(msg.get("From", ""))[1].split("@")[0]
    subject = str(msg.get("Subject", ""))
    if FEHLER_ABSENDER.match(local) or msg.get_content_type() == "multipart/report" or FEHLER_BETREFF.search(subject):
        return "zustellfehler"
    if scoring.classify_reply(subject, text, dict(msg.items()))["interesse"] == "automatisch":
        return "automatisch"
    return None


FEHLER_LABEL = {"zustellfehler": "Zustellfehler", "automatisch": "Automatische Antwort"}


def fehler_ablegen(c, lead_id, msg, text, art, message_id=None, to_addr=None, postfach_id=None):
    """Fehler-Mail im Ordner „Fehler“ an der Akte: gelesen, ohne Meldung, ohne Einordnung."""
    from . import leaddb
    name, addr = parseaddr(msg.get("From", ""))
    subject = str(msg.get("Subject", ""))
    add(c, lead_id, "in", text or "(leere Nachricht)", subject=subject, from_addr=addr, from_name=name, to_addr=to_addr,
        message_id=message_id, read=True, postfach_id=postfach_id, fehler=1)
    hinweis = " Die Adresse stimmt vermutlich nicht mehr: bitte prüfen." if art == "zustellfehler" else ""
    leaddb.add_event(c, lead_id, "note", f"{FEHLER_LABEL[art]}: {subject[:120]}.{hinweis}", author="Postfach")


def fehler_liste(c, limit=200):
    rows = c.execute("""SELECT m.*, l.name, l.email AS lead_email FROM messages m JOIN leads l ON l.id = m.lead_id
                        WHERE l.arbeitsbereich=ab() AND m.fehler=1 ORDER BY m.at DESC, m.id DESC LIMIT ?""",
                     (limit,)).fetchall()
    return [dict(r) for r in rows]


def im_ordner_verschieben(imap, nums, ordner):
    """Fehler-Mails im Mailprogramm aus dem Posteingang in den Ordner (z. B. „Fehler“) verschieben, damit dort nur
    echte Antworten stehen. Fehler beim Verschieben halten nichts auf. Gibt die Anzahl zurück."""
    if not ordner or not nums:
        return 0
    name = '"' + ordner.replace('"', "") + '"'
    try:
        imap.create(name)  # gibt es den Ordner schon, meldet der Server nur einen Fehler
    except Exception:
        pass
    n = 0
    for num in nums:
        try:
            typ, _ = imap.copy(num, name)
            if typ == "OK":
                imap.store(num, "+FLAGS", "\\Deleted")
                n += 1
        except Exception as exc:
            log.warning("Fehler-Mail nicht verschoben: %s", exc)
    if n:
        try:
            imap.expunge()
        except Exception as exc:
            log.warning("Posteingang nicht aufgeräumt: %s", exc)
    return n


def _owner(me):
    return ("AND (l.owner = ? OR l.owner IS NULL)", [me]) if me else ("", [])


def conversations(c, me=None, ordner="haupt"):
    """Alle Gespräche mit mindestens einer Nachricht von außen, neueste zuerst, mit Anzahl ungelesener Nachrichten.
    `me`: nur die eigenen und die noch niemandem zugeordneten. `ordner`: „haupt“ oder „sonstige“ (siehe ORDNER)."""
    where, args = _owner(me)
    where = f"AND {ORDNER_SQL[ordner]} {where}"
    rows = c.execute(f"""
        SELECT l.*, MAX(m.at) AS last_at, SUM(m.direction='in' AND m.read=0) AS unread_msgs,
               (SELECT text FROM messages x WHERE x.lead_id=l.id AND x.fehler=0 ORDER BY x.at DESC, x.id DESC LIMIT 1) AS last_text,
               (SELECT direction FROM messages x WHERE x.lead_id=l.id AND x.fehler=0 ORDER BY x.at DESC, x.id DESC LIMIT 1) AS last_dir
        FROM leads l JOIN messages m ON m.lead_id = l.id AND m.fehler = 0 WHERE l.arbeitsbereich=ab() {where}
        GROUP BY l.id HAVING SUM(m.direction='in') > 0
        ORDER BY unread_msgs > 0 DESC, last_at DESC""", args).fetchall()
    return [dict(r) for r in rows]


def unread_count(c, me=None):
    where, args = _owner(me)
    return c.execute(f"SELECT COUNT(DISTINCT m.lead_id) FROM messages m JOIN leads l ON l.id = m.lead_id "
                     f"WHERE l.arbeitsbereich=ab() AND m.direction='in' AND m.read=0 AND m.fehler=0 "
                     f"AND {ORDNER_SQL['haupt']} {where}", args).fetchone()[0]


def ordner_zahlen(c, me=None):
    """Für die Reiter im Postfach: Gespräche in „Sonstige“ und Mails in „Fehler“."""
    where, args = _owner(me)
    sonstige = c.execute(f"SELECT COUNT(DISTINCT l.id) FROM leads l JOIN messages m ON m.lead_id = l.id AND "
                         f"m.fehler = 0 AND m.direction = 'in' WHERE l.arbeitsbereich=ab() AND {ORDNER_SQL['sonstige']} "
                         f"{where}", args).fetchone()[0]
    fehler = c.execute("SELECT COUNT(*) FROM messages m JOIN leads l ON l.id = m.lead_id WHERE l.arbeitsbereich=ab() "
                       "AND m.fehler=1").fetchone()[0]
    return {"sonstige": sonstige, "fehler": fehler}


def ist_sonstige(c, lead_id):
    """Unbekannte, die wir nicht angeschrieben haben: keine Meldung, Ordner „Sonstige“."""
    return bool(c.execute(f"SELECT 1 FROM leads l WHERE l.id=? AND {ORDNER_SQL['sonstige']}", (lead_id,)).fetchone())


def mark_read(c, lead_id):
    c.execute("UPDATE messages SET read=1 WHERE lead_id=? AND direction='in' AND read=0", (lead_id,))
    c.commit()


def reply_subject(subject):
    subject = (subject or "").strip()
    return subject if subject.lower().startswith(("re:", "aw:")) else f"Re: {subject}".strip()


def quote(msg_row):
    if not msg_row:
        return ""
    who = msg_row.get("from_name") or parseaddr(msg_row.get("from_addr") or "")[1] or "Sie"
    lines = "\n".join("> " + line for line in (msg_row.get("text") or "").splitlines())
    return f"\n\n{who} schrieb:\n{lines}"


MOODS = {"gut": "läuft gut", "mittel": "mittel", "schlecht": "eher schlecht", "neutral": "neutral"}


def mood(lead, last_in):
    """Stimmung im Gespräch (grüner, gelber, roter oder weißer Kreis). Von euch gesetzt geht vor, sonst kostenlos
    nach Stichworten in der letzten Antwort (dieselben Regeln wie beim Einordnen, ohne KI)."""
    if lead.get("mood") in MOODS:
        return lead["mood"], "von euch gesetzt"
    if not last_in:
        return "neutral", "noch keine Antwort"
    r = scoring.classify_reply(last_in.get("subject"), last_in.get("text"))
    kind = r["interesse"]
    if kind == "qualified":
        return "gut", f"automatisch: {r['grund']}"
    if kind in ("kein_interesse", "abmeldung"):
        return "schlecht", f"automatisch: {r['grund']}"
    if kind == "mittel" and r["grund"].startswith("Stichwort"):
        return "mittel", f"automatisch: {r['grund']}"
    return "neutral", "automatisch: keine eindeutigen Stichworte"


def grouped(c, me=None, ordner="haupt"):
    """Nach Kunden: offen zum Antworten (letzte Nachricht kam von ihnen) und gesendet (wartet auf ihre Antwort)."""
    groups = {"offen": [], "gesendet": []}  # „offen“: älteste wartende zuerst (siehe unten)
    for conv in conversations(c, me, ordner):
        last_in = last_incoming(c, conv["id"])
        conv["mood"], conv["mood_why"] = mood(conv, last_in)
        conv["last_in"] = last_in
        if conv["last_dir"] == "in":
            from . import posteingang
            conv["wartet_seit"] = posteingang.wartet_seit(c, conv["id"])
        conv["wunsch"] = bool(c.execute("SELECT 1 FROM messages WHERE lead_id=? AND wunsch='vorschlag'",
                                        (conv["id"],)).fetchone())
        groups["offen" if conv["last_dir"] == "in" else "gesendet"].append(conv)
    groups["offen"].sort(key=lambda x: (not x["unread_msgs"], x.get("wartet_seit") or ""))
    return groups
