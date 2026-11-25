"""Daten aufräumen (Stufe 15): alte Leads, Projekte und Aufgaben aus der Anfangszeit entfernen, damit das OS mit den
echten Kunden und Leads neu durchstarten kann.

Nur über eine Freigabe: Ein Agent schlägt mit dem Werkzeug `aufraeumen_vorschlagen` vor, welche Leads bleiben;
das Gründerteam sagt Ja; erst dann löscht das OS. Immer erhalten bleiben (auch ohne Nennung) Leads mit Rechnungen,
erfasster Zeit oder einem Arbeitsbereich, weil Buchhaltung und Abrechnung sie brauchen. Das Firmen-Protokoll, das
Entscheidungs-Log, KI-Kosten und die „schon kontaktiert“-Liste (known_firms, ohne Verweis) bleiben ebenfalls.
"""
from . import firma

# Tabellen mit Spalte lead_id, die mit dem Lead verschwinden
_JE_LEAD = ("lead_events", "signals", "tasks", "messages", "termine", "notfaelle", "agent_entwuerfe")
# Tabellen mit Spalte projekt_id, die mit dem Projekt verschwinden
_JE_PROJEKT = ("abnahme_punkte", "abnahmen", "aenderungen", "dokumente", "wochenberichte", "kunden_zugaenge",
               "agent_entwuerfe")


def _tabellen(c):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _ids(c, sql, args=()):
    return {r[0] for r in c.execute(sql, args)}


def plan(c, behalten_lead_ids, aufgaben_ids=()):
    """Was würde entfernt? Gibt ein dict mit Zahlen und Listen zurück (ändert nichts)."""
    t = _tabellen(c)
    behalten = {int(x) for x in behalten_lead_ids or []}
    alle = _ids(c, "SELECT id FROM leads WHERE arbeitsbereich=ab()")
    geschuetzt = set()
    if "rechnungen" in t:
        geschuetzt |= _ids(c, "SELECT lead_id FROM rechnungen WHERE lead_id IS NOT NULL")
        geschuetzt |= _ids(c, "SELECT p.lead_id FROM projekte p JOIN rechnungen r ON r.projekt_id = p.id")
    if "zeiten" in t:
        geschuetzt |= _ids(c, "SELECT p.lead_id FROM projekte p JOIN zeiten z ON z.projekt_id = p.id")
    if "arbeitsbereiche" in t:
        geschuetzt |= _ids(c, "SELECT p.lead_id FROM projekte p JOIN arbeitsbereiche a ON a.projekt_id = p.id")
    weg = sorted((alle - behalten) - geschuetzt)
    projekte = sorted(_ids(c, f"SELECT id FROM projekte WHERE lead_id IN ({','.join('?' * len(weg))})", weg)) \
        if weg else []
    aufgaben_ids = sorted({int(x) for x in aufgaben_ids or []})
    return {"leads_weg": weg, "leads_bleiben": sorted(alle - set(weg)),
            "geschuetzt": sorted((alle - behalten) & geschuetzt), "projekte_weg": projekte,
            "aufgaben_weg": aufgaben_ids,
            "namen_bleiben": [r[0] for r in c.execute(
                f"SELECT name FROM leads WHERE id IN ({','.join('?' * len(alle - set(weg)))}) ORDER BY name",
                sorted(alle - set(weg)))] if alle - set(weg) else []}


def ausfuehren(c, wer, behalten_lead_ids, aufgaben_ids=()):
    """Löscht laut plan(); nur nach Freigabe aufrufen (agenten._ausfuehren). Gibt die Zahlen zurück."""
    p = plan(c, behalten_lead_ids, aufgaben_ids)
    t = _tabellen(c)
    weg, proj = p["leads_weg"], p["projekte_weg"]

    def _in(ids):
        return ",".join("?" * len(ids))

    n_aufg = 0
    for start in range(0, len(proj), 500):
        teil = proj[start:start + 500]
        for tab in _JE_PROJEKT:
            if tab in t:
                c.execute(f"DELETE FROM {tab} WHERE projekt_id IN ({_in(teil)})", teil)
        n_aufg += c.execute(f"DELETE FROM aufgaben WHERE objekt_typ='projekt' AND objekt_id IN ({_in(teil)})",
                            teil).rowcount
        if "angebote" in t:
            c.execute(f"DELETE FROM angebote WHERE projekt_id IN ({_in(teil)})", teil)
        c.execute(f"DELETE FROM projekte WHERE id IN ({_in(teil)})", teil)
    for start in range(0, len(weg), 500):
        teil = weg[start:start + 500]
        for tab in _JE_LEAD:
            if tab in t:
                c.execute(f"DELETE FROM {tab} WHERE lead_id IN ({_in(teil)})", teil)
        if "angebote" in t:
            c.execute(f"DELETE FROM angebote WHERE lead_id IN ({_in(teil)})", teil)
        n_aufg += c.execute(f"DELETE FROM aufgaben WHERE objekt_typ='lead' AND objekt_id IN ({_in(teil)})",
                            teil).rowcount
        c.execute(f"DELETE FROM kommentare WHERE objekt_typ='lead' AND objekt_id IN ({_in(teil)})", teil)
        c.execute(f"DELETE FROM freigaben WHERE status='offen' AND objekt_typ='lead' AND objekt_id IN ({_in(teil)})",
                  teil)
        if "agent_infos" in t:
            c.execute(f"DELETE FROM agent_infos WHERE objekt_typ='lead' AND objekt_id IN ({_in(teil)})", teil)
        if "known_firms" in t:  # Liste „schon kontaktiert“ bleibt, nur ohne Verweis auf den gelöschten Lead
            c.execute(f"UPDATE known_firms SET lead_id=NULL WHERE lead_id IN ({_in(teil)})", teil)
        c.execute(f"DELETE FROM leads WHERE id IN ({_in(teil)})", teil)
    if p["aufgaben_weg"]:
        ids = p["aufgaben_weg"]
        n_aufg += c.execute(f"DELETE FROM aufgaben WHERE id IN ({_in(ids)})", ids).rowcount
        c.execute(f"DELETE FROM kommentare WHERE objekt_typ='aufgabe' AND objekt_id IN ({_in(ids)})", ids)
    erg = {"leads": len(weg), "projekte": len(proj), "aufgaben": n_aufg, "geschuetzt": len(p["geschuetzt"]),
           "bleiben": len(p["leads_bleiben"])}
    firma.log(c, wer, "einstellung", "daten", "aufraeumen",
              f"Aufgeräumt: {erg['leads']} Leads, {erg['projekte']} Projekte, {erg['aufgaben']} Aufgaben entfernt; "
              f"{erg['bleiben']} Leads bleiben ({erg['geschuetzt']} wegen Rechnungen/Zeiten geschützt).", erg,
              commit=False)
    c.commit()
    return erg
