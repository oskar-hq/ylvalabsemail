"""Automatische Läufe nach Zeitplan: kostenlos (suchen und nach Regeln bewerten) und mit KI (Entwürfe, Erinnerungen).

Für jede Art: an welchen Tagen, von wann bis wann und in welchem Abstand (mindestens 10 Minuten) ein Lauf startet.
Der Hintergrund-Arbeiter schaut jede Minute nach. Läuft schon ein Lauf, wartet der nächste. Die KI-Läufe haben
zusätzlich eine Tagesgrenze in Euro; das Monatsbudget gilt wie immer. Verschickt wird auch hier nichts ohne Freigabe.
"""
import re
from datetime import datetime, timedelta

from . import leaddb

MIN_EVERY = 10
DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
KINDS = {"frei": "Kostenlose Suche", "ki": "Suche mit KI"}
DEFAULTS = {
    "frei": {"enabled": False, "days": [0, 1, 2, 3, 4], "start": "08:00", "end": "18:00", "every": 60,
             "bewerten": 50, "top": 10, "neu_suchen_h": 24},
    "ki": {"enabled": False, "days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00", "every": 120,
           "bewerten": 0, "top": 10, "entwuerfe": 3, "budget_eur": 1.0, "erinnerungen": True, "tag_max_eur": 3.0,
           "web_anzahl": 0, "web_kanaele": ["webseiten", "instagram", "linkedin"]},
}
STARTED_BY = {"frei": "Automatik · kostenlos", "ki": "Automatik · KI"}


class ScheduleError(ValueError):
    pass


def load(c):
    stored = leaddb.kv_get(c, "schedule", {}) or {}
    return {k: {**DEFAULTS[k], **(stored.get(k) or {})} for k in KINDS}


def _time(value, label):
    if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", value or ""):
        raise ScheduleError(f"{label}: bitte als Uhrzeit, z. B. 08:00.")
    h, m = value.split(":")
    return f"{int(h):02d}:{m}"


def _int(value, label, lo, hi):
    try:
        n = int(str(value).strip())
    except ValueError as exc:
        raise ScheduleError(f"{label}: bitte eine ganze Zahl.") from exc
    if not lo <= n <= hi:
        raise ScheduleError(f"{label}: zwischen {lo} und {hi}.")
    return n


def _eur(value, label):
    try:
        x = float(str(value).replace(",", ".").strip())
    except ValueError as exc:
        raise ScheduleError(f"{label}: bitte einen Betrag.") from exc
    if not 0 <= x <= 100:
        raise ScheduleError(f"{label}: zwischen 0 und 100 €.")
    return round(x, 2)


def parse(kind, form):
    """Formular → gespeicherte Form. form ist ein dict-artiges Objekt mit getlist (Flask) oder Listen."""
    getlist = form.getlist if hasattr(form, "getlist") else (lambda k: form.get(k) or [])
    cfg = {"enabled": form.get("enabled") == "1",
           "days": sorted({int(d) for d in getlist("days") if str(d).isdigit() and int(d) < 7}),
           "start": _time(form.get("start"), "Von"), "end": _time(form.get("end"), "Bis"),
           "every": _int(form.get("every"), "Abstand in Minuten", MIN_EVERY, 24 * 60),
           "bewerten": _int(form.get("bewerten", 0), "Betriebe bewerten", 0, 1000),
           "top": _int(form.get("top", 10), "Davon ins CRM", 0, 200)}
    if cfg["start"] == cfg["end"]:
        raise ScheduleError("Von und Bis dürfen nicht gleich sein.")
    if cfg["enabled"] and not cfg["days"]:
        raise ScheduleError("Bitte mindestens einen Tag wählen.")
    if kind == "frei":
        cfg["neu_suchen_h"] = _int(form.get("neu_suchen_h", 24), "Neu suchen alle … Stunden", 1, 24 * 14)
        if cfg["enabled"] and not cfg["bewerten"]:
            raise ScheduleError("Betriebe bewerten: mindestens 1, sonst tut der Lauf nichts.")
    else:
        cfg.update(entwuerfe=_int(form.get("entwuerfe", 0), "Entwürfe pro Lauf", 0, 50),
                   budget_eur=_eur(form.get("budget_eur", 0), "Budget pro Lauf"),
                   erinnerungen=form.get("erinnerungen") == "1",
                   tag_max_eur=_eur(form.get("tag_max_eur", 0), "Höchstens pro Tag"),
                   web_anzahl=_int(form.get("web_anzahl", 0) or 0, "Websuche: Betriebe pro Lauf", 0, 30),
                   web_kanaele=[k for k in ("webseiten", "instagram", "linkedin") if k in set(getlist("web_kanaele"))])
        if cfg["web_anzahl"] and not cfg["web_kanaele"]:
            raise ScheduleError("Websuche: bitte Webseiten, Instagram oder LinkedIn wählen.")
        if cfg["enabled"] and not (cfg["entwuerfe"] or cfg["erinnerungen"] or cfg["web_anzahl"]):
            raise ScheduleError("Websuche, Entwürfe oder Erinnerungen einschalten, sonst braucht es keinen KI-Lauf.")
    return cfg


def save(c, kind, cfg):
    stored = leaddb.kv_get(c, "schedule", {}) or {}
    stored[kind] = cfg
    leaddb.kv_set(c, "schedule", stored)


def _minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_window(cfg, now):
    """Liegt now im Zeitfenster? Bis vor Von heißt: über Mitternacht (z. B. 22:00 bis 06:00)."""
    start, end, t = _minutes(cfg["start"]), _minutes(cfg["end"]), now.hour * 60 + now.minute
    if start < end:
        return now.weekday() in cfg["days"] and start <= t < end
    # über Mitternacht: der Teil nach Mitternacht gehört zum Vortag
    if t >= start:
        return now.weekday() in cfg["days"]
    return t < end and (now.weekday() - 1) % 7 in cfg["days"]


def due(cfg, now, last):
    if not cfg["enabled"] or not in_window(cfg, now):
        return False
    return not last or now - datetime.fromisoformat(last) >= timedelta(minutes=cfg["every"]) - timedelta(seconds=30)


def next_start(cfg, now, last):
    """Wann startet der nächste automatische Lauf ungefähr? Für die Anzeige, auf die Minute."""
    if not cfg["enabled"] or not cfg["days"]:
        return None
    t = now.replace(second=0, microsecond=0)
    if last:
        t = max(t, datetime.fromisoformat(last).replace(second=0, microsecond=0) + timedelta(minutes=cfg["every"]))
    for _ in range(8 * 24 * 60 // 5):  # bis zu acht Tage in 5-Minuten-Schritten
        if in_window(cfg, t):
            return t
        t += timedelta(minutes=5)
    return None


def today_cost_eur(c, eur):
    return eur(leaddb._sum(c, "at >= ?", (datetime.now().strftime("%Y-%m-%d"),))["usd"])


def params_for(kind, cfg, c, now, eur):
    """Die Parameter des Laufs oder (None, Grund), wenn er heute nicht mehr laufen soll."""
    if kind == "frei":
        last = (leaddb.kv_get(c, "last_search") or {}).get("at")
        stale = not last or now - datetime.fromisoformat(last) >= timedelta(hours=cfg["neu_suchen_h"])
        return {"bewerten": cfg["bewerten"], "top": cfg["top"], "entwuerfe": 0, "budget_eur": 0.0,
                "suchen": stale, "erinnerungen": False, "nischen": "auto"}, None
    left = cfg["tag_max_eur"] - today_cost_eur(c, eur) if cfg["tag_max_eur"] else cfg["budget_eur"]
    budget = min(cfg["budget_eur"], left)
    if budget <= 0.005:
        return None, f"Tagesgrenze von {cfg['tag_max_eur']:.2f} € erreicht."
    return {"bewerten": cfg["bewerten"] or (cfg.get("web_anzahl") or 0), "top": cfg["top"], "entwuerfe": cfg["entwuerfe"],
            "budget_eur": round(budget, 2), "suchen": False, "erinnerungen": cfg["erinnerungen"], "nischen": "auto",
            "web_anzahl": cfg.get("web_anzahl") or 0, "web_kanaele": cfg.get("web_kanaele") or []}, None


def describe(cfg):
    if not cfg["enabled"]:
        return "aus"
    days = cfg["days"]
    if days == [0, 1, 2, 3, 4]:
        d = "Mo–Fr"
    elif days == list(range(7)):
        d = "täglich"
    else:
        d = ", ".join(DAYS[i] for i in days)
    every = f"alle {cfg['every']} Min." if cfg["every"] < 120 or cfg["every"] % 60 else f"alle {cfg['every'] // 60} Std."
    return f"{d} {cfg['start']}–{cfg['end']} Uhr, {every}"
