"""Postfach im CRM: Gespräche mit Betrieben, die geantwortet haben, und direkt antworten.

Jede Nachricht (unsere und ihre) liegt in `messages`. Die erste Nachricht an einen Betrieb steht weiter in der Akte
(`leads.subject/body`), die Erinnerung in `followup_*`; der Verlauf setzt beides davor.
"""
from email.utils import parseaddr

from . import leaddb, scoring


def add(c, lead_id, direction, text, subject="", from_addr="", from_name="", to_addr="", message_id=None,
        in_reply_to=None, sent_by=None, read=True, at=None):
    c.execute("INSERT INTO messages (lead_id, at, direction, from_addr, from_name, to_addr, subject, text, message_id,"
              " in_reply_to, sent_by, read) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
              (lead_id, at or leaddb.now(), direction, from_addr, from_name, to_addr, subject, text, message_id,
               in_reply_to, sent_by, 1 if read else 0))
    c.commit()


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
    for m in c.execute("SELECT * FROM messages WHERE lead_id=? ORDER BY at, id", (lead["id"],)):
        items.append({"direction": m["direction"], "at": m["at"], "subject": m["subject"] or "", "text": m["text"] or "",
                      "who": (m["sent_by"] or "Ihr") if m["direction"] == "out" else (m["from_name"] or m["from_addr"] or lead["name"]),
                      "channel": "email", "message_id": m["message_id"], "from_addr": m["from_addr"], "read": m["read"]})
    return sorted(items, key=lambda i: i["at"] or "")


def last_incoming(c, lead_id):
    row = c.execute("SELECT * FROM messages WHERE lead_id=? AND direction='in' ORDER BY at DESC, id DESC LIMIT 1",
                    (lead_id,)).fetchone()
    return dict(row) if row else None


def _owner(me):
    return ("AND (l.owner = ? OR l.owner IS NULL)", [me]) if me else ("", [])


def conversations(c, me=None):
    """Alle Gespräche mit mindestens einer Nachricht von außen, neueste zuerst, mit Anzahl ungelesener Nachrichten.
    `me`: nur die eigenen und die noch niemandem zugeordneten."""
    where, args = _owner(me)
    rows = c.execute(f"""
        SELECT l.*, MAX(m.at) AS last_at, SUM(m.direction='in' AND m.read=0) AS unread_msgs,
               (SELECT text FROM messages x WHERE x.lead_id=l.id ORDER BY x.at DESC, x.id DESC LIMIT 1) AS last_text,
               (SELECT direction FROM messages x WHERE x.lead_id=l.id ORDER BY x.at DESC, x.id DESC LIMIT 1) AS last_dir
        FROM leads l JOIN messages m ON m.lead_id = l.id WHERE 1 {where}
        GROUP BY l.id HAVING SUM(m.direction='in') > 0
        ORDER BY unread_msgs > 0 DESC, last_at DESC""", args).fetchall()
    return [dict(r) for r in rows]


def unread_count(c, me=None):
    where, args = _owner(me)
    return c.execute(f"SELECT COUNT(DISTINCT m.lead_id) FROM messages m JOIN leads l ON l.id = m.lead_id "
                     f"WHERE m.direction='in' AND m.read=0 {where}", args).fetchone()[0]


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


def grouped(c, me=None):
    """Nach Kunden: offen zum Antworten (letzte Nachricht kam von ihnen) und gesendet (wartet auf ihre Antwort)."""
    groups = {"offen": [], "gesendet": []}
    for conv in conversations(c, me):
        last_in = last_incoming(c, conv["id"])
        conv["mood"], conv["mood_why"] = mood(conv, last_in)
        conv["last_in"] = last_in
        groups["offen" if conv["last_dir"] == "in" else "gesendet"].append(conv)
    return groups
