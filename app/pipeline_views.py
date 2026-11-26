"""Pipeline (Stufe 13): ein Bildschirm, auf dem man sieht, was gerade läuft. Oben die Agents (arbeitet, wartet auf
euch, ruhig) mit ihren Aktionen von heute als Balken und Punkten, darunter die Projekte als Board nach Phase, dazu
ein paar Kennzahlen. Gedacht zum schnellen Erfassen, nicht zum Lesen."""
from datetime import date, datetime, timedelta

from flask import Blueprint, g, render_template

from . import agenten, firma, posteingang, projekte
from .leads_views import db, me
from .main import login_required

bp = Blueprint("pipeline", __name__)

STUNDEN = list(range(7, 22))  # Balken je Stunde von 7 bis 21 Uhr
# Art einer Aktion (aus dem Firmen-Protokoll) → Kurzname und Farbe der Punkte
ARTEN = {"aufgabe": ("Aufgabe", "blau"), "anfrage": ("Aufgabe", "blau"), "kommentar": ("Notiz", "grau"),
         "lead": ("Lead", "gruen"), "freigabe": ("Freigabe", "orange"), "agent": ("Entwurf/Info", "grau"),
         "projekt": ("Projekt", "lila"), "entscheidung": ("Entscheidung", "orange")}


def _agent_status(offen, zuletzt, heute_n, spaeter=0):
    jetzt = datetime.now()
    if offen:
        return "wartet", "wartet auf euch"
    if spaeter:
        return "heute", f"{spaeter} auf später gelegt"
    if zuletzt and jetzt - datetime.fromisoformat(zuletzt) < timedelta(minutes=20):
        return "arbeitet", "arbeitet gerade"
    if heute_n:
        return "heute", "heute aktiv"
    return "ruhig", "ruhig"


def agents_ueberblick(c):
    heute = date.today().isoformat()
    verbunden = {v["agent"] for v in agenten.verbindungen(c) if v["aktiv"]}
    out = []
    for a in agenten.liste(c):
        ev = [dict(r) for r in c.execute("SELECT at, art, text FROM ereignisse WHERE wer=? AND at >= ? ORDER BY id",
                                         (a["wer"], heute))]
        je_stunde = {h: 0 for h in STUNDEN}
        for e in ev:
            h = int(e["at"][11:13])
            if h in je_stunde:
                je_stunde[h] += 1
        hoch = max(je_stunde.values()) or 1
        jetzt = datetime.now().isoformat(timespec="seconds")
        offen = c.execute("SELECT COUNT(*) FROM freigaben WHERE angefragt_von=? AND status='offen' AND "
                          "IFNULL(spaeter_bis, '') <= ?", (a["wer"], jetzt)).fetchone()[0]
        spaeter = c.execute("SELECT COUNT(*) FROM freigaben WHERE angefragt_von=? AND status='offen' AND "
                            "spaeter_bis > ?", (a["wer"], jetzt)).fetchone()[0]
        bei = [r[0] for r in c.execute("SELECT DISTINCT pruefer FROM freigaben WHERE angefragt_von=? AND status='offen' "
                                       "AND IFNULL(spaeter_bis, '') <= ?", (a["wer"], jetzt))]
        status, label = _agent_status(offen, a["zuletzt_aktiv"], len(ev), spaeter)
        if a["status"] != "aktiv":
            status, label = "gesperrt", "gesperrt"
        elif a["key"] not in verbunden and not ev:
            status, label = "aus", "nicht verbunden"
        zaehl = {}
        for e in ev:
            name = ARTEN.get(e["art"], ("Sonstiges", "grau"))[0]
            zaehl[name] = zaehl.get(name, 0) + 1
        out.append({**a, "status_key": status, "status_text": label, "offen": offen,
                    "bei": [firma.name_von(c, p).split(" ")[0] for p in bei],
                    "heute": len(ev), "balken": [(h, je_stunde[h], je_stunde[h] / hoch) for h in STUNDEN],
                    "punkte": [{"farbe": ARTEN.get(e["art"], ("", "grau"))[1], "text": e["text"], "zeit": e["at"][11:16]}
                               for e in ev[-24:]],
                    "zaehl": sorted(zaehl.items(), key=lambda x: -x[1])[:4],
                    "letzte": ev[-1] if ev else None})
    return out


def projekt_board(c):
    offen_je = {}
    for r in c.execute("SELECT objekt_id, COUNT(*) n FROM freigaben WHERE status='offen' AND objekt_typ='projekt' "
                       "GROUP BY objekt_id"):
        offen_je[str(r["objekt_id"])] = r["n"]
    aufg_je = {}
    for r in c.execute("SELECT objekt_id, COUNT(*) n FROM aufgaben WHERE status IN ('offen','angefragt') AND "
                       "objekt_typ='projekt' GROUP BY objekt_id"):
        aufg_je[r["objekt_id"]] = r["n"]
    phasen = [s for s in projekte.STATUS if s[0] != "beendet"]
    alle = projekte.liste(c)
    spalten = []
    for i, (key, label, hint) in enumerate(phasen):
        karten = []
        for p in alle:
            if p["status"] != key:
                continue
            karten.append({**p, "fortschritt": (i + 1) / len(phasen), "freigaben": offen_je.get(str(p["id"]), 0),
                           "aufgaben": aufg_je.get(p["id"], 0),
                           "aenderungen": len(projekte.aenderungen(c, p["id"], True))})
        spalten.append({"key": key, "label": label, "hint": hint, "karten": karten})
    return spalten


@bp.get("/pipeline")
@login_required
def seite():
    c = db()
    woche = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    kacheln = [
        ("Freigaben offen", c.execute("SELECT COUNT(*) FROM freigaben WHERE status='offen' AND IFNULL(spaeter_bis, '') <= ?",
                                      (datetime.now().isoformat(timespec="seconds"),)).fetchone()[0], "/freigaben"),
        ("Antworten warten", len(posteingang.wartend(c)), "/chat"),
        ("Aufgaben überfällig", c.execute("SELECT COUNT(*) FROM aufgaben WHERE status='offen' AND faellig < ?",
                                          (date.today().isoformat(),)).fetchone()[0], "/aufgaben/alle?ansicht=alle"),
        ("Neue Leads diese Woche", c.execute("SELECT COUNT(*) FROM leads WHERE arbeitsbereich=ab() AND created_at >= ? "
                                             "AND stage IS NOT NULL", (woche,)).fetchone()[0], "/crm"),
        ("Projekte live", c.execute("SELECT COUNT(*) FROM projekte WHERE status IN ('live','betrieb')").fetchone()[0],
         "/kunden"),
    ]
    zeige_projekte = firma.darf(c, me(), "kunden")
    return render_template("pipeline.html", account=g.account, agents=agents_ueberblick(c), kacheln=kacheln,
                           spalten=projekt_board(c) if zeige_projekte else [], zeige_projekte=zeige_projekte,
                           stunden=STUNDEN, jetzt_h=datetime.now().hour, arten=ARTEN)
