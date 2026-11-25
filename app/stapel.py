"""Freigabe-Stapel (Stufe 13, getrennt in 13b): alles, was eine Person entscheiden muss, in einer Reihenfolge.
Vorbild ist der Tagesstapel im Outreach-Cockpit: eine Zahl, „Starten“, dann eine Karte pro Bildschirm.

Im Stapel stehen nur Freigaben: die Karten der Agents (Kurzformat, gebündelt) und alle übrigen Freigaben aus der
Zentrale (Termin-Einladungen, Erstkontakte, Rechnungen …). Aufgaben, die Menschen erledigen müssen, stehen auf der
Seite „Aufgaben“ (Erinnerungsliste, `aufgaben.erinnerungen`), Antworten von Kunden im Chat.

Reihenfolge (ohne KI): dringende Agent-Freigaben, Freigaben mit Frist (Stufe B), übrige Freigaben, übrige
Agent-Freigaben. „Jetzt nicht“ überspringt eine Karte bis zum Ende der Sitzung.
"""

TYP_LABEL = {"agent": "Freigabe", "freigabe": "Freigabe"}
GRUPPEN = [("agents", "von den Agents", ("agent",)), ("weitere", "weitere", ("freigabe",))]


def _rang(item):
    return item["rang"]


def stapel(c, person, skip=()):
    """Liste der Karten (dicts mit key, typ, rang, titel, text, url, …), gefiltert um `skip` (Schlüssel)."""
    from . import agenten, freigaben
    out = []
    for g in agenten.karten(c, person)[0]:
        k = g["erste"]
        mehrere = bool(g["buendel"]) and len(g["eintraege"]) > 1
        out.append({"key": f"agent:{k['id']}", "typ": "agent", "rang": (10, 30, 80)[g["rang"]], "gruppe": g,
                    "karte": k, "mehrere": mehrere,
                    "titel": f"{len(g['eintraege'])} × {g['buendel']}" if mehrere else k["titel"],
                    "text": k["was"], "eyebrow": k["agent"], "url": f"/freigaben/alle#{'b' if mehrere else 'a'}{k['id']}"})
    for f in freigaben.offen(c, person):
        out.append({"key": f"freigabe:{f['art']}:{f['oid']}", "typ": "freigabe", "rang": 20 if f.get("stufe") == "B" else 32,
                    "f": f, "titel": f.get("titel") or f["art_label"], "text": f.get("text") or "",
                    "eyebrow": f.get("eyebrow") or f["art_label"], "url": f.get("url") or "/freigaben/alle"})
    skip = set(skip or ())
    out = [x for x in out if x["key"] not in skip]
    out.sort(key=_rang)
    return out


def zusammenfassung(items):
    """Für die Startseite: Anzahl je Gruppe (von den Agents, weitere)."""
    zahlen = []
    for key, label, typen in GRUPPEN:
        n = sum(len(x["gruppe"]["eintraege"]) if x["typ"] == "agent" else 1 for x in items if x["typ"] in typen)
        if n:
            zahlen.append({"key": key, "label": label, "n": n})
    return zahlen


def anzahl(c, person):
    """Für die Navigation: wie viele Karten warten (Bündel zählen je Eintrag)."""
    return sum(len(x["gruppe"]["eintraege"]) if x["typ"] == "agent" else 1 for x in stapel(c, person))
