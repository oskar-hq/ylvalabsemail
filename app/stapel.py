"""Aufgabenstapel (Stufe 13): alles, was heute auf eine Person wartet, in einer Reihenfolge. Vorbild ist der
Tagesstapel im Outreach-Cockpit: eine Zahl, „Starten“, dann eine Karte pro Bildschirm.

Was in den Stapel kommt (nur, was diese Person entscheiden oder tun muss):
- Freigaben der Agents (Kurzformat, gebündelt) und alle übrigen Freigaben aus der Zentrale
- Antworten, die auf uns warten (Chat)
- Anfragen an die eigenen Bereiche
- Aufgaben, die überfällig oder heute fällig sind
- Termine, die vorbei sind und noch ein Ergebnis brauchen

Die Reihenfolge ist fest (ohne KI): dringende Agent-Freigaben, Freigaben, Antworten, Anfragen, Überfälliges,
Termin-Ergebnisse, Heute, übrige Agent-Freigaben. „Jetzt nicht“ überspringt eine Karte bis zum Ende der Sitzung.
"""
from datetime import date

TYP_LABEL = {"agent": "Freigabe", "freigabe": "Freigabe", "antwort": "Antwort", "anfrage": "Anfrage",
             "aufgabe": "Aufgabe", "ergebnis": "Termin"}
GRUPPEN = [("freigabe", "Freigaben", ("agent", "freigabe")), ("antwort", "Antworten", ("antwort",)),
           ("aufgabe", "Aufgaben", ("anfrage", "aufgabe", "ergebnis"))]


def _rang(item):
    return item["rang"]


def stapel(c, person, skip=()):
    """Liste der Karten (dicts mit key, typ, rang, titel, text, url, …), gefiltert um `skip` (Schlüssel)."""
    from . import agenten, aufgaben, firma, freigaben, kalender, posteingang
    out = []
    for g in agenten.karten(c, person)[0]:
        k = g["erste"]
        mehrere = bool(g["buendel"]) and len(g["eintraege"]) > 1
        out.append({"key": f"agent:{k['id']}", "typ": "agent", "rang": (10, 30, 80)[g["rang"]], "gruppe": g,
                    "karte": k, "mehrere": mehrere,
                    "titel": f"{len(g['eintraege'])} × {g['buendel']}" if mehrere else k["titel"],
                    "text": k["was"], "eyebrow": k["agent"], "url": f"/freigaben#{'b' if mehrere else 'a'}{k['id']}"})
    for f in freigaben.offen(c, person):
        out.append({"key": f"freigabe:{f['art']}:{f['oid']}", "typ": "freigabe", "rang": 20 if f.get("stufe") == "B" else 32,
                    "f": f, "titel": f.get("titel") or f["art_label"], "text": f.get("text") or "",
                    "eyebrow": f.get("eyebrow") or f["art_label"], "url": f.get("url") or "/freigaben"})
    if firma.darf(c, person, "crm"):
        for w in posteingang.wartend(c, person)[:30]:
            out.append({"key": f"antwort:{w['id']}", "typ": "antwort", "rang": 40, "lead_id": w["id"],
                        "titel": w["name"], "text": "Wartet auf eine Antwort von uns", "seit": w.get("seit"),
                        "eyebrow": "Antwort", "url": f"/chat/{w['id']}"})
    t = aufgaben.mein_tag(c, person)
    for a in t["anfragen"]:
        out.append({"key": f"anfrage:{a['id']}", "typ": "anfrage", "rang": 45, "a": a, "titel": a["titel"],
                    "text": f"Anfrage von {a.get('anfrage_von_name') or 'jemandem'} an {a.get('bereich_name')}",
                    "eyebrow": "Anfrage", "url": f"/aufgaben/{a['id']}"})
    heute = date.today()
    for a in t["ueberfaellig"]:
        tage = (heute - date.fromisoformat(a["faellig"][:10])).days
        out.append({"key": f"aufgabe:{a['id']}", "typ": "aufgabe", "rang": 50 - min(tage, 9) - (5 if a["prio"] == 1 else 0),
                    "a": a, "titel": a["titel"], "text": f"seit {tage} {'Tag' if tage == 1 else 'Tagen'} überfällig",
                    "eyebrow": a.get("objekt_name") or a.get("bereich_name") or "Aufgabe", "url": f"/aufgaben/{a['id']}"})
    if firma.darf(c, person, "kalender"):
        for k in kalender.offene_ergebnisse(c, person)[:5]:
            out.append({"key": f"ergebnis:{k['id']}", "typ": "ergebnis", "rang": 60, "termin": k,
                        "titel": f"Wie war es? {k['titel']}", "text": k.get("zeit") or "", "eyebrow": "Termin",
                        "url": f"/kalender/{k['id']}#ergebnis"})
    for a in t["heute"]:
        out.append({"key": f"aufgabe:{a['id']}", "typ": "aufgabe", "rang": 65 - (5 if a["prio"] == 1 else 0), "a": a,
                    "titel": a["titel"], "text": "heute fällig", "eyebrow": a.get("objekt_name") or a.get("bereich_name")
                    or "Aufgabe", "url": f"/aufgaben/{a['id']}"})
    skip = set(skip or ())
    out = [x for x in out if x["key"] not in skip]
    out.sort(key=_rang)
    return out


def zusammenfassung(items):
    """Für die Startseite: Anzahl je Gruppe (Freigaben, Antworten, Aufgaben)."""
    zahlen = []
    for key, label, typen in GRUPPEN:
        n = sum(len(x["gruppe"]["eintraege"]) if x["typ"] == "agent" else 1 for x in items if x["typ"] in typen)
        if n:
            zahlen.append({"key": key, "label": label, "n": n})
    return zahlen


def anzahl(c, person):
    """Für die Navigation: wie viele Karten warten (Bündel zählen je Eintrag)."""
    return sum(len(x["gruppe"]["eintraege"]) if x["typ"] == "agent" else 1 for x in stapel(c, person))
