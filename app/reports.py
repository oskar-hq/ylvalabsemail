"""Berichte über die Läufe: nach jedem Lauf von Hand eine Zusammenfassung, für die automatische Suche ein Bericht
pro Tag. Beides lässt sich eine Woche zurück ansehen.

Was ein Lauf bewertet hat, steht in run_results (Betrieb, Punkte, passt ja/nein, Begründung). Der Tagesbericht der
automatischen Suche ist fertig, sobald das letzte Zeitfenster des Tages vorbei ist (z. B. um 18 Uhr). Läuft die
Suche über Mitternacht oder rund um die Uhr, kommt der Bericht um 0 Uhr.
"""
import json
from datetime import date, datetime, time, timedelta

from . import crm, leaddb, schedule

SCHEMA = """
CREATE TABLE IF NOT EXISTS run_results (
    run_id INTEGER NOT NULL, lead_id INTEGER NOT NULL, passed INTEGER NOT NULL, score INTEGER, reason TEXT,
    at TEXT NOT NULL, PRIMARY KEY (run_id, lead_id)
);
CREATE INDEX IF NOT EXISTS run_results_lead ON run_results(lead_id);
"""
DAYS_BACK = 7
AUTO = "Automatik%"
WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def record(c, run_id, lead_id, passed, score, reason):
    """Im Lauf bewertet: für den Bericht merken."""
    if not run_id:
        return
    c.execute("INSERT OR REPLACE INTO run_results (run_id, lead_id, passed, score, reason, at) VALUES (?,?,?,?,?,?)",
              (run_id, lead_id, 1 if passed else 0, score, reason, leaddb.now()))
    c.commit()


def cleanup(c):
    cutoff = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    c.execute("DELETE FROM run_results WHERE at < ?", (cutoff,))


def _state(row):
    """Wo der Betrieb jetzt steht: zu prüfen, im CRM (Stufe) oder aussortiert."""
    if row["stage"]:
        return "crm", f"CRM · {crm.STAGE_LABELS.get(row['stage'], row['stage'])}"
    if row["status"] == "aussortiert":
        return "raus", "aussortiert"
    if row["status"] == "passt" and not row["rejected_by"]:
        return "pruefen", "zu prüfen"
    return "sonst", leaddb.STATUS_LABELS.get(row["status"], row["status"])


def _picked(c, run_ids):
    if not run_ids:
        return []
    marks = ",".join("?" * len(run_ids))
    rows = c.execute(f"""
        SELECT l.id, l.name, l.category_label, l.city, l.stage, l.status, l.rejected_by, l.owner,
               MAX(r.score) AS score, r.reason
        FROM run_results r JOIN leads l ON l.id = r.lead_id
        WHERE r.run_id IN ({marks}) AND r.passed = 1
        GROUP BY l.id ORDER BY score DESC, l.name""", list(run_ids)).fetchall()
    out = []
    for r in rows:
        key, label = _state(r)
        out.append({**dict(r), "state": key, "state_label": label})
    return out


def _rated(c, run_ids):
    if not run_ids:
        return 0, 0
    marks = ",".join("?" * len(run_ids))
    row = c.execute(f"SELECT COUNT(DISTINCT lead_id) n, COUNT(DISTINCT CASE WHEN passed THEN lead_id END) p "
                    f"FROM run_results WHERE run_id IN ({marks})", list(run_ids)).fetchone()
    return row["n"] or 0, row["p"] or 0


def _counts(row):
    try:
        return json.loads(row["counts"] or "{}") or {}
    except ValueError:
        return {}


def run_report(c, run):
    """Bericht zu einem Lauf: gefunden, bewertet, ausgewählt (welche und warum)."""
    run = dict(run)
    counts = _counts(run)
    picked = _picked(c, [run["id"]])
    rated, passed = _rated(c, [run["id"]])
    return {"id": run["id"], "started_at": run["started_at"], "ended_at": run["ended_at"], "by": run["started_by"],
            "status": run["status"], "message": run["message"] or "", "auto": (run["started_by"] or "").startswith("Automatik"),
            "gefunden": counts.get("neu_gefunden", 0), "schon_bekannt": counts.get("schon_bekannt", 0),
            "grobfilter": counts.get("grobfilter", 0), "bewertet": rated or counts.get("bewertet", 0),
            "ausgewaehlt": passed if rated else counts.get("passen", 0),
            "aussortiert": (rated - passed) if rated else max(0, counts.get("bewertet", 0) - counts.get("passen", 0)),
            "entwuerfe": counts.get("entwuerfe", 0), "picked": picked, "detail": bool(rated)}


def last_manual(c):
    row = c.execute("SELECT * FROM runs WHERE IFNULL(started_by,'') NOT LIKE ? AND status NOT IN ('wartet','laeuft') "
                    "ORDER BY id DESC LIMIT 1", (AUTO,)).fetchone()
    return run_report(c, row) if row else None


def _day_label(d, today):
    if d == today:
        return "Heute"
    if d == today - timedelta(days=1):
        return "Gestern"
    return f"{WEEKDAYS[d.weekday()]}, {d.day:02d}.{d.month:02d}."


def manual_days(c, now=None):
    """Läufe von Hand der letzten sieben Tage, nach Tag."""
    now = now or datetime.now()
    today = now.date()
    since = (today - timedelta(days=DAYS_BACK - 1)).isoformat()
    rows = c.execute("SELECT * FROM runs WHERE IFNULL(started_by,'') NOT LIKE ? AND started_at >= ? "
                     "AND status NOT IN ('wartet','laeuft') ORDER BY id DESC", (AUTO, since)).fetchall()
    days = {}
    for r in rows:
        d = date.fromisoformat(r["started_at"][:10])
        days.setdefault(d, []).append(run_report(c, r))
    return [{"date": d.isoformat(), "label": _day_label(d, today), "runs": runs} for d, runs in sorted(days.items(), reverse=True)]


def release_at(cfgs, d):
    """Wann der Tagesbericht für den Tag d fertig ist: am Ende des letzten Zeitfensters an diesem Tag; geht ein
    Fenster über Mitternacht (oder rund um die Uhr), um 0 Uhr danach."""
    midnight = datetime.combine(d + timedelta(days=1), time(0, 0))
    ends = []
    for cfg in cfgs:
        if not cfg.get("enabled") or d.weekday() not in cfg.get("days", []):
            continue
        start, end = cfg["start"], cfg["end"]
        if start >= end or end == "00:00":
            return midnight
        ends.append(datetime.combine(d, time(*map(int, end.split(":")))))
    return max(ends) if ends else midnight


def auto_day(c, d, cfgs, now=None):
    now = now or datetime.now()
    rows = c.execute("SELECT * FROM runs WHERE started_by LIKE ? AND substr(started_at, 1, 10) = ? "
                     "AND status NOT IN ('wartet','laeuft') ORDER BY id", (AUTO, d.isoformat())).fetchall()
    ready = release_at(cfgs, d)
    ids = [r["id"] for r in rows]
    rated, passed = _rated(c, ids)
    counts = [_counts(r) for r in rows]
    total = lambda k: sum(x.get(k, 0) or 0 for x in counts)  # noqa: E731
    return {"date": d.isoformat(), "label": _day_label(d, now.date()), "released": now >= ready,
            "ready_at": ready.isoformat(timespec="minutes"), "laeufe": len(rows),
            "fehler": sum(1 for r in rows if r["status"] == "fehler"),
            "gefunden": total("neu_gefunden"), "schon_bekannt": total("schon_bekannt"), "grobfilter": total("grobfilter"),
            "bewertet": rated or total("bewertet"), "ausgewaehlt": passed if rated else total("passen"),
            "aussortiert": (rated - passed) if rated else max(0, total("bewertet") - total("passen")),
            "entwuerfe": total("entwuerfe"), "picked": _picked(c, ids), "detail": bool(rated)}


def auto_days(c, now=None):
    """Tagesberichte der automatischen Suche, sieben Tage zurück (Tage ohne Lauf fallen weg, außer heute)."""
    now = now or datetime.now()
    cfgs = list(schedule.load(c).values())
    out = []
    for i in range(DAYS_BACK):
        d = now.date() - timedelta(days=i)
        day = auto_day(c, d, cfgs, now)
        if day["laeufe"] or (i == 0 and any(cfg["enabled"] for cfg in cfgs)):
            out.append(day)
    return out


def summary(day):
    return (f"{day['laeufe']} Läufe, {day['gefunden']} neu gefunden, {day['bewertet']} bewertet, "
            f"{day['ausgewaehlt']} ausgewählt")


def notify_released(c, notify, now=None):
    """Einmal Bescheid geben, wenn ein Tagesbericht fertig ist (nur, wenn an dem Tag etwas lief)."""
    now = now or datetime.now()
    cfgs = list(schedule.load(c).values())
    told = leaddb.kv_get(c, "report_notified")
    for i in (1, 0):  # gestern zuerst (Bericht um 0 Uhr), dann heute
        d = now.date() - timedelta(days=i)
        if told and told >= d.isoformat():
            continue
        day = auto_day(c, d, cfgs, now)
        if not day["released"]:
            continue
        leaddb.kv_set(c, "report_notified", d.isoformat())
        told = d.isoformat()
        if day["laeufe"]:
            notify(c, f"Tagesbericht der automatischen Suche ({day['label']})", summary(day) + ".",
                   "/leads/berichte?art=auto")
