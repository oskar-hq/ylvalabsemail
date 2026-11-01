"""Aufgaben, Anfragen an andere Bereiche und Kommentare mit @-Erwähnung (Firmen-OS, Stufe 1).

Jede Aufgabe hat genau einen Bereich, eine verantwortliche Person und einen Termin (G4). Ohne Person landet sie bei
der verantwortlichen Person des Bereichs. Eine Anfrage ist eine Aufgabe an einen anderen Bereich: Sie landet bei
dessen verantwortlicher Person, die sie annimmt (mit Termin) oder mit Grund ablehnt. Die bisherigen Wiedervorlagen
an Leads sind normale Aufgaben mit Bezug auf den Lead (objekt_typ='lead').
"""
import json
import re
import threading
from datetime import date, datetime, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS aufgaben (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    art TEXT NOT NULL DEFAULT 'aufgabe', titel TEXT NOT NULL, notiz TEXT,
    bereich TEXT NOT NULL, person TEXT, faellig TEXT NOT NULL, prio INTEGER NOT NULL DEFAULT 2,
    status TEXT NOT NULL DEFAULT 'offen', schaetzung_min INTEGER,
    objekt_typ TEXT, objekt_id INTEGER,
    erstellt_von TEXT, erstellt_am TEXT NOT NULL, erledigt_am TEXT, erledigt_von TEXT,
    quelle TEXT, anfrage_von TEXT, antwort TEXT, alt_task_id INTEGER UNIQUE
);
CREATE INDEX IF NOT EXISTS aufgaben_person ON aufgaben(person, status, faellig);
CREATE INDEX IF NOT EXISTS aufgaben_objekt ON aufgaben(objekt_typ, objekt_id, status);
CREATE TABLE IF NOT EXISTS kommentare (
    id INTEGER PRIMARY KEY AUTOINCREMENT, objekt_typ TEXT NOT NULL, objekt_id INTEGER NOT NULL,
    wer TEXT NOT NULL, at TEXT NOT NULL, text TEXT NOT NULL, erwaehnt TEXT
);
CREATE INDEX IF NOT EXISTS kommentare_objekt ON kommentare(objekt_typ, objekt_id, id);
"""

# Bisherige Wiedervorlagen (Tabelle tasks) einmalig übernehmen; die alte Tabelle bleibt unangetastet liegen
UEBERNAHME = """
INSERT OR IGNORE INTO aufgaben (art, titel, bereich, person, faellig, prio, status, objekt_typ, objekt_id,
                                erstellt_von, erstellt_am, erledigt_am, erledigt_von, quelle, alt_task_id)
SELECT 'aufgabe', t.text, 'akquise',
       COALESCE((SELECT key FROM team WHERE key = l.owner),
                (SELECT verantwortlich FROM bereiche WHERE key = 'akquise')),
       substr(t.due, 1, 10), 2, CASE WHEN t.done_at IS NULL THEN 'offen' ELSE 'erledigt' END, 'lead', t.lead_id,
       t.created_by, t.created_at, t.done_at, t.done_by, 'wiedervorlage', t.id
FROM tasks t LEFT JOIN leads l ON l.id = t.lead_id
"""

# Erst nach SCHEMA und UEBERNAHME: leaddb liest beide schon beim Import (Startwerte)
from . import firma, leaddb  # noqa: E402

PRIOS = [(1, "Hoch"), (2, "Normal"), (3, "Niedrig")]
PRIO_LABEL = dict(PRIOS)
STATUS_LABEL = {"offen": "Offen", "angefragt": "Angefragt", "abgelehnt": "Abgelehnt", "erledigt": "Erledigt"}
OBJEKT_LABEL = {"lead": "Lead", "aufgabe": "Aufgabe"}
OFFEN = ("offen", "angefragt")


class AufgabeError(ValueError):
    pass


def heute():
    return date.today().isoformat()


def _datum(value, feld="Termin"):
    value = (value or "").strip()[:10]
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise AufgabeError(f"Bitte einen {feld} wählen.") from exc


def schnell_termin(key):
    """Kurzwahl für den Termin: heute, morgen, woche (nächster Montag), 2w."""
    d = date.today()
    return {"heute": d, "morgen": d + timedelta(days=1), "woche": d + timedelta(days=7 - d.weekday()),
            "2w": d + timedelta(days=14)}.get(key, None)


def wiedervorlage_bereich(c):
    """In welchen Bereich Wiedervorlagen an Leads gehen (Einstellungen → Bereiche), Standard Akquise."""
    key = leaddb.kv_get(c, "regel:wiedervorlage_bereich") or "akquise"
    if firma.bereich(c, key):
        return key
    row = c.execute("SELECT key FROM bereiche ORDER BY archiviert, sort LIMIT 1").fetchone()
    return row["key"] if row else key

# ---------------------------------------------------------------- Anlegen und ändern


def anlegen(c, wer, titel, bereich, faellig, person=None, prio=2, notiz="", objekt_typ=None, objekt_id=None,
            quelle="hand", schaetzung_min=None, anfrage=False, streng=True):
    """Neue Aufgabe oder Anfrage. `streng=False` (Abläufe, Wiedervorlagen): eine nicht mehr aktive Person wird
    still durch die verantwortliche Person des Bereichs ersetzt."""
    titel = re.sub(r"\s+", " ", titel or "").strip()
    if not titel:
        raise AufgabeError("Was ist zu tun? Bitte einen Titel.")
    if len(titel) > 200:
        raise AufgabeError("Der Titel ist zu lang (höchstens 200 Zeichen). Details gehören in die Notiz.")
    b = firma.bereich(c, bereich)
    if not b or (b["archiviert"] and streng):
        raise AufgabeError("Bitte einen Bereich wählen.")
    faellig = _datum(faellig)
    try:
        prio = int(prio)
    except (TypeError, ValueError):
        prio = 2
    prio = prio if prio in PRIO_LABEL else 2
    if person and not firma.aktive_person(c, person):
        if streng:
            raise AufgabeError("Diese Person ist nicht (mehr) aktiv. Bitte jemand anderen wählen.")
        person = None
    status = "offen"
    if anfrage and wer != b["verantwortlich"]:
        person, status = b["verantwortlich"], "angefragt"
    else:
        anfrage = False
    person = person or b["verantwortlich"]
    cur = c.execute(
        "INSERT INTO aufgaben (art, titel, notiz, bereich, person, faellig, prio, status, schaetzung_min, objekt_typ,"
        " objekt_id, erstellt_von, erstellt_am, quelle, anfrage_von) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("anfrage" if anfrage else "aufgabe", titel, (notiz or "").strip()[:4000] or None, b["key"], person, faellig,
         prio, status, schaetzung_min, objekt_typ, objekt_id, wer, leaddb.now(), quelle, wer if anfrage else None))
    aid = cur.lastrowid
    firma.log(c, wer, "anfrage" if anfrage else "aufgabe", "aufgabe", aid,
              f"{'Anfrage an' if anfrage else 'Aufgabe für'} {firma.name_von(c, person)}: {titel}",
              {"bereich": b["key"], "person": person, "faellig": faellig, "quelle": quelle}, commit=False)
    c.commit()
    if person and person != wer:
        von = firma.name_von(c, wer) or "Das OS"
        if anfrage:
            melden(c, person, f"Anfrage von {von}", f"{titel} (an {b['name']}, Wunschtermin {tag(faellig)})",
                   f"/aufgaben/{aid}", push=True)
        elif quelle == "hand":
            melden(c, person, f"Neue Aufgabe von {von}", f"{titel} · fällig {tag(faellig)}", f"/aufgaben/{aid}")
    return aid


def get(c, aid):
    row = c.execute("""SELECT a.*, b.name AS bereich_name, b.verantwortlich AS bereich_verantwortlich,
                              t.name AS person_name, e.name AS erstellt_von_name, q.name AS anfrage_von_name,
                              l.name AS lead_name
                       FROM aufgaben a LEFT JOIN bereiche b ON b.key = a.bereich
                       LEFT JOIN team t ON t.key = a.person LEFT JOIN team e ON e.key = a.erstellt_von
                       LEFT JOIN team q ON q.key = a.anfrage_von
                       LEFT JOIN leads l ON a.objekt_typ = 'lead' AND l.id = a.objekt_id
                       WHERE a.id = ?""", (aid,)).fetchone()
    return _zeile(row) if row else None


def _zeile(row):
    d = dict(row)
    d["ueberfaellig"] = d["status"] in OFFEN and d["faellig"] < heute()
    d["tag"] = tag(d["faellig"])
    d["prio_label"] = PRIO_LABEL.get(d["prio"], "Normal")
    d["status_label"] = STATUS_LABEL.get(d["status"], d["status"])
    d["erstellt_von_name"] = d.get("erstellt_von_name") or d.get("erstellt_von") or ""
    if d.get("objekt_typ") == "lead":
        d["objekt_url"] = f"/leads/{d['objekt_id']}"
        d["objekt_name"] = d.get("lead_name") or f"Lead {d['objekt_id']}"
    elif d.get("objekt_typ") == "projekt":
        d["objekt_url"], d["objekt_name"] = f"/kunden/{d['objekt_id']}", "Projekt"
    elif d.get("objekt_typ") == "aenderung":
        d["objekt_url"], d["objekt_name"] = f"/kunden/aenderung/{d['objekt_id']}", "Änderungswunsch"
    return d


def darf_aendern(c, wer, a):
    """Termin, Person, Titel ändern: die Person selbst, wer sie angelegt hat, die verantwortliche Person des Bereichs
    und wer Aufgaben verwalten darf."""
    return wer in (a["person"], a["erstellt_von"], a.get("bereich_verantwortlich"), a.get("anfrage_von")) \
        or firma.darf(c, wer, "aufgaben", firma.VERWALTEN)


def darf_entscheiden(c, wer, a):
    """Eine Anfrage annehmen oder ablehnen: die verantwortliche Person des Bereichs (oder wer Aufgaben verwaltet)."""
    return wer == a.get("bereich_verantwortlich") or firma.darf(c, wer, "aufgaben", firma.VERWALTEN)


def aendern(c, wer, aid, **felder):
    a = get(c, aid)
    if not a:
        raise AufgabeError("Diese Aufgabe gibt es nicht.")
    neu = {}
    if "titel" in felder:
        titel = re.sub(r"\s+", " ", felder["titel"] or "").strip()
        if not titel or len(titel) > 200:
            raise AufgabeError("Bitte einen Titel (höchstens 200 Zeichen).")
        neu["titel"] = titel
    if "notiz" in felder:
        neu["notiz"] = (felder["notiz"] or "").strip()[:4000] or None
    if "faellig" in felder:
        neu["faellig"] = _datum(felder["faellig"])
    if "prio" in felder:
        p = int(felder["prio"]) if str(felder["prio"]).isdigit() else 2
        neu["prio"] = p if p in PRIO_LABEL else 2
    if felder.get("bereich"):
        b = firma.bereich(c, felder["bereich"])
        if not b or b["archiviert"]:
            raise AufgabeError("Bitte einen Bereich wählen.")
        neu["bereich"] = b["key"]
    if "person" in felder:
        person = felder["person"] or firma.bereich(c, neu.get("bereich", a["bereich"]))["verantwortlich"]
        if not firma.aktive_person(c, person):
            raise AufgabeError("Diese Person ist nicht (mehr) aktiv.")
        neu["person"] = person
    neu = {k: v for k, v in neu.items() if a.get(k) != v}
    if not neu:
        return False
    c.execute(f"UPDATE aufgaben SET {', '.join(k + '=?' for k in neu)} WHERE id=?", [*neu.values(), aid])
    firma.log(c, wer, "aufgabe", "aufgabe", aid, f"Geändert: {a['titel']}",
              {k: v for k, v in neu.items() if k != "notiz"}, commit=False)
    c.commit()
    if neu.get("person") and neu["person"] != wer:
        melden(c, neu["person"], f"{firma.name_von(c, wer)} hat dir eine Aufgabe gegeben",
               f"{neu.get('titel', a['titel'])} · fällig {tag(neu.get('faellig', a['faellig']))}", f"/aufgaben/{aid}")
    return True


def erledigen(c, wer, aid, erledigt=True):
    a = get(c, aid)
    if not a:
        raise AufgabeError("Diese Aufgabe gibt es nicht.")
    if erledigt and a["status"] == "erledigt" or not erledigt and a["status"] != "erledigt":
        return None
    if erledigt and a["status"] == "angefragt":
        raise AufgabeError("Die Anfrage ist noch nicht angenommen.")
    if erledigt:
        c.execute("UPDATE aufgaben SET status='erledigt', erledigt_am=?, erledigt_von=? WHERE id=?",
                  (leaddb.now(), wer, aid))
    else:
        c.execute("UPDATE aufgaben SET status='offen', erledigt_am=NULL, erledigt_von=NULL WHERE id=?", (aid,))
    firma.log(c, wer, "aufgabe", "aufgabe", aid, f"{'Erledigt' if erledigt else 'Wieder offen'}: {a['titel']}",
              {"status": "erledigt" if erledigt else "offen"}, commit=False)
    c.commit()
    if erledigt and a["anfrage_von"] and a["anfrage_von"] != wer:
        melden(c, a["anfrage_von"], f"Erledigt: {a['titel']}", f"{firma.name_von(c, wer)} hat deine Anfrage erledigt.",
               f"/aufgaben/{aid}")
    return a


def annehmen(c, wer, aid, faellig, person=None):
    a = get(c, aid)
    if not a or a["art"] != "anfrage" or a["status"] != "angefragt":
        raise AufgabeError("Diese Anfrage wartet nicht (mehr) auf eine Entscheidung.")
    faellig = _datum(faellig)
    person = person or a["person"]
    if not firma.aktive_person(c, person):
        raise AufgabeError("Diese Person ist nicht (mehr) aktiv.")
    c.execute("UPDATE aufgaben SET status='offen', faellig=?, person=? WHERE id=?", (faellig, person, aid))
    firma.log(c, wer, "anfrage", "aufgabe", aid, f"Angenommen: {a['titel']} (bis {tag(faellig)})",
              {"faellig": faellig, "person": person}, commit=False)
    c.commit()
    if a["anfrage_von"] and a["anfrage_von"] != wer:
        melden(c, a["anfrage_von"], f"Angenommen: {a['titel']}",
               f"{firma.name_von(c, person)} erledigt das bis {tag(faellig)}.", f"/aufgaben/{aid}")
    if person != wer:
        melden(c, person, f"Neue Aufgabe von {firma.name_von(c, wer)}", f"{a['titel']} · fällig {tag(faellig)}",
               f"/aufgaben/{aid}")


def ablehnen(c, wer, aid, grund):
    a = get(c, aid)
    if not a or a["art"] != "anfrage" or a["status"] != "angefragt":
        raise AufgabeError("Diese Anfrage wartet nicht (mehr) auf eine Entscheidung.")
    grund = (grund or "").strip()
    if len(grund) < 3:
        raise AufgabeError("Bitte einen Grund angeben, damit die anfragende Person weiß, woran es liegt.")
    c.execute("UPDATE aufgaben SET status='abgelehnt', antwort=?, erledigt_am=?, erledigt_von=? WHERE id=?",
              (grund[:1000], leaddb.now(), wer, aid))
    firma.log(c, wer, "anfrage", "aufgabe", aid, f"Abgelehnt: {a['titel']}", {"grund": grund[:1000]}, commit=False)
    c.commit()
    if a["anfrage_von"] and a["anfrage_von"] != wer:
        melden(c, a["anfrage_von"], f"Abgelehnt: {a['titel']}", f"{firma.name_von(c, wer)}: {grund[:140]}",
               f"/aufgaben/{aid}", push=True)

# ---------------------------------------------------------------- Listen


SELECT = """SELECT a.*, b.name AS bereich_name, b.verantwortlich AS bereich_verantwortlich, t.name AS person_name,
                   e.name AS erstellt_von_name, q.name AS anfrage_von_name, l.name AS lead_name
            FROM aufgaben a LEFT JOIN bereiche b ON b.key = a.bereich LEFT JOIN team t ON t.key = a.person
            LEFT JOIN team e ON e.key = a.erstellt_von LEFT JOIN team q ON q.key = a.anfrage_von
            LEFT JOIN leads l ON a.objekt_typ = 'lead' AND l.id = a.objekt_id"""
ORDER = "ORDER BY a.faellig, a.prio, a.id"


def liste(c, me, ansicht="meine", bereich=None, erledigt=False, limit=300):
    """Ansichten: meine, bereich (meine Bereiche oder ein gewählter), vergeben (von mir an andere), alle."""
    where, args = [], []
    if erledigt:
        where.append("a.status IN ('erledigt','abgelehnt') AND a.erledigt_am >= ?")
        args.append((date.today() - timedelta(days=30)).isoformat())
    else:
        where.append("a.status IN ('offen','angefragt')")
    if bereich:
        where.append("a.bereich = ?")
        args.append(bereich)
    elif ansicht == "bereich":
        keys = sorted(set(firma.bereiche_von(c, me)) | set(firma.verantwortet(c, me))) or ["-"]
        where.append(f"a.bereich IN ({','.join('?' * len(keys))})")
        args += keys
    if ansicht == "meine":
        where.append("a.person = ?")
        args.append(me)
    elif ansicht == "vergeben":
        where.append("(a.erstellt_von = ? OR a.anfrage_von = ?) AND IFNULL(a.person,'') != ?")
        args += [me, me, me]
    order = "ORDER BY a.erledigt_am DESC" if erledigt else ORDER
    return [_zeile(r) for r in c.execute(f"{SELECT} WHERE {' AND '.join(where)} {order} LIMIT ?", [*args, limit])]


def zum_objekt(c, typ, oid, offen=True):
    nur_offen = "AND a.status IN ('offen','angefragt')" if offen else ""
    rows = c.execute(f"{SELECT} WHERE a.objekt_typ = ? AND a.objekt_id = ? {nur_offen} {ORDER}", (typ, oid))
    return [_zeile(r) for r in rows]


def anfragen_an(c, me):
    keys = firma.verantwortet(c, me)
    if not keys:
        return []
    return [_zeile(r) for r in c.execute(
        f"{SELECT} WHERE a.status = 'angefragt' AND a.bereich IN ({','.join('?' * len(keys))}) {ORDER}", keys)]


def offen_zaehlen(c, me):
    """Für die Navigation: meine überfälligen und heutigen Aufgaben plus Anfragen, die auf mich warten."""
    if not me:
        return 0
    n = c.execute("SELECT COUNT(*) FROM aufgaben WHERE person=? AND status='offen' AND faellig <= ?",
                  (me, heute())).fetchone()[0]
    return n + len(anfragen_an(c, me))


def mein_tag(c, me):
    t = heute()
    woche = (date.today() + timedelta(days=7)).isoformat()
    offen = [_zeile(r) for r in c.execute(f"{SELECT} WHERE a.person = ? AND a.status = 'offen' AND a.faellig <= ? "
                                         f"{ORDER}", (me, woche))]
    meine_anfragen = [_zeile(r) for r in c.execute(
        f"{SELECT} WHERE a.anfrage_von = ? AND (a.status = 'angefragt' OR (a.status = 'abgelehnt' AND "
        f"a.erledigt_am >= ?)) {ORDER}", (me, (date.today() - timedelta(days=7)).isoformat()))]
    return {
        "anfragen": anfragen_an(c, me),
        "ueberfaellig": [a for a in offen if a["faellig"] < t],
        "heute": [a for a in offen if a["faellig"] == t],
        "woche": [a for a in offen if a["faellig"] > t],
        "meine_anfragen": meine_anfragen,
        "erwaehnungen": erwaehnungen(c, me),
    }

# ---------------------------------------------------------------- Kommentare und @-Erwähnungen


def _fold(text):
    return text.lower().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))


def erwaehnte(c, text):
    """@oskar, @Oskar oder @Finn → Schlüssel aktiver Personen (über Schlüssel oder Vorname)."""
    namen = {}
    for p in firma.aktive_personen(c):
        namen[_fold(p["key"])] = p["key"]
        namen.setdefault(_fold(p["name"].split(" ")[0]), p["key"])
    out = []
    for m in re.finditer(r"(?<![\w.])@([\wäöüÄÖÜß-]{2,30})", text or ""):
        key = namen.get(_fold(m.group(1)))
        if key and key not in out:
            out.append(key)
    return out


def objekt_info(c, typ, oid):
    """Titel und Adresse des Dings, an dem ein Kommentar hängt (oder None, wenn es das nicht gibt)."""
    if typ == "aufgabe":
        a = get(c, oid)
        return {"titel": a["titel"], "url": f"/aufgaben/{oid}", "person": a["person"]} if a else None
    if typ == "lead":
        row = c.execute("SELECT name, owner FROM leads WHERE id=?", (oid,)).fetchone()
        return {"titel": row["name"], "url": f"/leads/{oid}", "person": row["owner"]} if row else None
    return None


def kommentieren(c, wer, typ, oid, text):
    text = (text or "").strip()
    if not text:
        raise AufgabeError("Der Kommentar ist leer.")
    if len(text) > 4000:
        raise AufgabeError("Der Kommentar ist zu lang (höchstens 4.000 Zeichen).")
    info = objekt_info(c, typ, oid)
    if not info:
        raise AufgabeError("Das gibt es nicht (mehr).")
    genannt = erwaehnte(c, text)
    cur = c.execute("INSERT INTO kommentare (objekt_typ, objekt_id, wer, at, text, erwaehnt) VALUES (?,?,?,?,?,?)",
                    (typ, oid, wer, leaddb.now(), text, json.dumps(genannt) if genannt else None))
    firma.log(c, wer, "kommentar", typ, oid, f"Kommentar an {info['titel']}", {"erwaehnt": genannt}, commit=False)
    c.commit()
    von = firma.name_von(c, wer)
    for key in genannt:
        if key != wer:
            melden(c, key, f"{von} hat dich erwähnt", f"{info['titel']}: {text[:140]}", info["url"], push=True)
    if info["person"] and info["person"] != wer and info["person"] not in genannt and typ == "aufgabe":
        melden(c, info["person"], f"Neuer Kommentar von {von}", f"{info['titel']}: {text[:140]}", info["url"])
    return cur.lastrowid


def kommentare(c, typ, oid):
    rows = c.execute("SELECT k.*, t.name AS wer_name FROM kommentare k LEFT JOIN team t ON t.key = k.wer "
                     "WHERE k.objekt_typ=? AND k.objekt_id=? ORDER BY k.id", (typ, oid)).fetchall()
    return [dict(r, wer_name=r["wer_name"] or r["wer"], erwaehnt=json.loads(r["erwaehnt"] or "[]")) for r in rows]


def erwaehnungen(c, me, tage=14):
    """Wo die Person in den letzten Tagen erwähnt wurde (neueste zuerst), mit „neu“ seit dem letzten Ansehen."""
    seit = (datetime.now() - timedelta(days=tage)).isoformat(timespec="seconds")
    seen = leaddb.kv_get(c, f"erwaehnung_seen:{me}", 0) or 0
    out = []
    for r in c.execute("SELECT k.*, t.name AS wer_name FROM kommentare k LEFT JOIN team t ON t.key = k.wer "
                       "WHERE k.at >= ? AND k.erwaehnt LIKE ? AND k.wer != ? ORDER BY k.id DESC LIMIT 20",
                       (seit, f'%"{me}"%', me)):
        if me not in json.loads(r["erwaehnt"] or "[]"):
            continue
        info = objekt_info(c, r["objekt_typ"], r["objekt_id"]) or {"titel": "(gelöscht)", "url": "/"}
        out.append(dict(r, wer_name=r["wer_name"] or r["wer"], titel=info["titel"], url=info["url"],
                        neu=r["id"] > seen))
    return out


def erwaehnungen_gesehen(c, me):
    top = c.execute("SELECT MAX(id) FROM kommentare").fetchone()[0] or 0
    leaddb.kv_set(c, f"erwaehnung_seen:{me}", top)

# ---------------------------------------------------------------- Meldungen an eine Person


def melden(c, fuer, titel, text, url, push=False):
    """Meldung in der Glocke nur für diese Person, auf Wunsch auch aufs Handy (nur ihre Geräte, im Hintergrund)."""
    leaddb.add_notice(c, titel, text, url, fuer=fuer)
    if not push:
        return
    from . import agent
    if not agent.CFG.get("db_path"):
        return

    def _send():
        conn = leaddb.connect(agent.CFG["db_path"])
        try:
            agent.push_to(conn, fuer, titel, text, url)
        except Exception as exc:  # Push ist nur ein Zusatz, die Meldung in der Glocke ist schon da
            agent.log.warning("Push an %s fehlgeschlagen: %s", fuer, exc)
        finally:
            conn.close()
    threading.Thread(target=_send, daemon=True).start()


def tag(value):
    """Deutsches Kurzdatum: heute, morgen, gestern, sonst „Mo., 3. Nov.“."""
    if not value:
        return ""
    d = date.fromisoformat(value[:10])
    delta = (d - date.today()).days
    if delta in (-1, 0, 1):
        return {0: "heute", 1: "morgen", -1: "gestern"}[delta]
    monate = ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sept.", "Okt.", "Nov.", "Dez."]
    wt = ["Mo.", "Di.", "Mi.", "Do.", "Fr.", "Sa.", "So."][d.weekday()]
    jahr = "" if d.year == date.today().year else f" {d.year}"
    return f"{wt}, {d.day}. {monate[d.month - 1]}{jahr}"
