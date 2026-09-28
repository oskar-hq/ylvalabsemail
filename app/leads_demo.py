"""Beispieldaten für den Demo-Modus: so sieht die Akquise aus, bevor die KI echte Betriebe gefunden hat.
Alle Betriebe sind erfunden."""
import json
from datetime import datetime, timedelta

from . import leaddb

DEMO = [
    dict(name="Tischlerei Hansen (Beispiel)", branche="handwerk", category="craft=carpenter", category_label="Tischlerei",
         city="Kappeln", address="Beispielweg 1, 24376 Kappeln", distance_km=1.2, status="beantwortet", interest="qualified",
         fit_score=86, email="info@tischlerei-hansen.example", contact_name="Jens Hansen",
         fit_reason="Familienbetrieb mit 14 Leuten, baut Küchen und Treppen auf Maß. Anfragen laufen nur über ein PDF-Formular "
                    "und Telefon, im Karriere-Bereich wird eine Bürokraft gesucht.",
         zeitfresser=["Angebote für Maßanfertigungen", "Anfragen per Telefon und PDF-Formular", "Aufmaß-Notizen"],
         subject="Angebote für Maßküchen schneller schreiben?", greeting="Moin Herr Hansen,",
         body="auf Ihrer Webseite habe ich gesehen, dass Sie Küchen und Treppen komplett auf Maß bauen und gerade eine Bürokraft suchen.\n\n"
              "Bei Betrieben wie Ihrem kosten Angebote oft ganze Abende. Wir bauen gerade mit Handwerksbetrieben aus der Region ein Werkzeug, "
              "mit dem Aufmaß, Fotos und eine kurze Sprachnotiz reichen und der Angebotsentwurf fast fertig ist.\n\n"
              "Hätten Sie Lust auf ein kostenloses Gespräch von 30 Minuten, am Telefon oder bei Ihnen in der Werkstatt?",
         reply="Moin Herr Jacobsen,\n\ndas klingt tatsächlich spannend, die Angebote sind bei uns echt ein Problem. "
               "Rufen Sie gern nächste Woche an, am besten vormittags.\n\nGruß\nJens Hansen",
         reply_summary="Interesse! Hat ein konkretes Problem mit Angeboten und bittet um einen Anruf nächste Woche vormittags.",
         grund="Angebote schreiben ist ein spürbares Problem.", schritt="Nächste Woche vormittags anrufen und Vor-Ort-Tag vorschlagen."),
    dict(name="Pflegedienst Schleiufer (Beispiel)", branche="pflege", category="healthcare=home_care", category_label="Ambulante Pflege",
         city="Arnis", address="Musterstraße 4, 24399 Arnis", distance_km=4.8, status="beantwortet", interest="mittel", fit_score=78,
         email="kontakt@pflege-schleiufer.example", fit_reason="Ambulanter Dienst mit rund 30 Mitarbeitenden und Touren durch Angeln. "
         "Dokumentation und Tourenplanung sind typische Zeitfresser.",
         zeitfresser=["Doku nach jedem Einsatz", "Tourenplanung"], subject="Pflegedoku per Sprache statt nach Feierabend",
         greeting="Guten Tag,", body="Sie versorgen mit Ihrem Team Patientinnen und Patienten zwischen Arnis und Kappeln.\n\n"
         "Wir entwickeln eine Doku per Sprache: nach dem Einsatz kurz einsprechen, der Eintrag wird strukturiert erstellt.\n\n"
         "Darf ich Ihnen das in 30 Minuten zeigen?", reply="Hallo, aktuell stecken wir mitten in der Umstellung auf eine neue "
         "Pflegesoftware. Melden Sie sich gern im Frühjahr wieder.", reply_summary="Vielleicht später: stellen gerade die Software um, im Frühjahr wieder melden.",
         grund="Gerade Umstellung auf neue Pflegesoftware.", schritt="Im März erneut melden."),
    dict(name="Autohaus Beispiel & Söhne", branche="handel", category="shop=car", category_label="Autohaus", city="Süderbrarup",
         address="Hauptstraße 9, 24392 Süderbrarup", distance_km=14.2, status="beantwortet", interest="kein_interesse", fit_score=58,
         email="info@autohaus-beispiel.example", fit_reason="Autohaus mit Werkstatt, Terminvergabe schon online.",
         zeitfresser=["Werkstatttermine"], subject="Werkstatt-Anfragen automatisch sortieren", greeting="Sehr geehrte Damen und Herren,",
         body="…", reply="Danke, kein Bedarf. Wir sind über den Hersteller gut versorgt.",
         reply_summary="Absage: Software kommt vom Hersteller.", grund="Herstellervorgaben, eigene Systeme.", schritt=""),
    dict(name="Hof Ostertoft (Beispiel)", branche="landwirtschaft", category="place=farm", category_label="Hof", city="Rabenkirchen-Faulück",
         address="Ostertoft 2, 24407 Rabenkirchen-Faulück", distance_km=7.9, status="entwurf", fit_score=81,
         email="hof@ostertoft.example", contact_name="Maren Petersen",
         fit_reason="Milchviehbetrieb mit Hofladen und Online-Bestellungen. Dokumentationspflichten und Direktvermarktung erkennbar.",
         zeitfresser=["Dokumentation Düngung und Tiere", "Bestellungen im Hofladen"], subject="Weniger Papier auf dem Hof",
         greeting="Moin Frau Petersen,", body="Ihr Hofladen und die Bestellung per Mail sind toll gemacht.\n\nWir bauen mit Höfen aus Angeln "
         "einen Assistenten, der Nachweise und Anträge aus Daten erstellt, die auf dem Hof ohnehin schon da sind.\n\n"
         "Hätten Sie 30 Minuten für ein Gespräch?"),
    dict(name="Elektro Nordlicht (Beispiel)", branche="handwerk", category="craft=electrician", category_label="Elektro",
         city="Eckernförde", address="Am Hafen 3, 24340 Eckernförde", distance_km=27.5, status="entwurf", fit_score=74,
         email="info@elektro-nordlicht.example", fit_reason="Elektrobetrieb mit rund 20 Leuten, Notdienst und viele Kundenanfragen.",
         zeitfresser=["Kundenanfragen", "Einsatzplanung"], subject="Kundenanfragen, die nicht untergehen", greeting="Guten Tag,",
         body="Sie bieten einen Notdienst und Photovoltaik-Beratung an, da kommt sicher einiges an Anfragen zusammen.\n\n"
              "Wir entwickeln mit Handwerksbetrieben ein Werkzeug, das Anfragen sortiert und Antworten vorbereitet.\n\n"
              "Wollen wir 30 Minuten telefonieren?"),
    dict(name="Steuerbüro Angeln (Beispiel)", branche="dienstleistung", category="office=tax_advisor", category_label="Steuerberatung",
         city="Kappeln", address="Schmiedestraße 5, 24376 Kappeln", distance_km=0.6, status="versendet", fit_score=69,
         email="kanzlei@steuer-angeln.example", fit_reason="Kanzlei mit 12 Mitarbeitenden, Belege kommen noch viel auf Papier.",
         zeitfresser=["Belege abtippen"], subject="Belege nicht mehr abtippen", greeting="Sehr geehrte Frau Möller,",
         body="…", channel="brief"),
    dict(name="Friseur Einzelhaar (Beispiel)", branche="handwerk", category="craft=hairdresser", category_label="Friseur",
         city="Kappeln", address="", distance_km=0.9, status="aussortiert", fit_score=22, email="",
         fit_reason="Ein-Personen-Salon mit Online-Terminbuchung, kaum Verwaltung.", zeitfresser=[]),
    dict(name="Bäckerei Kette Filiale Kappeln (Beispiel)", branche="handwerk", category="craft=baker", category_label="Bäckerei",
         city="Kappeln", address="", distance_km=0.4, status="aussortiert", fit_score=18, email="",
         fit_reason="Filiale einer großen Kette, Entscheidungen fallen in der Zentrale.", zeitfresser=[]),
    dict(name="Zimmerei Schwansen (Beispiel)", branche="handwerk", category="craft=carpenter", category_label="Tischlerei",
         city="Damp", address="", distance_km=11.3, status="recherche", fit_score=None, email="", fit_reason=None, zeitfresser=[]),
]


def seed(conn):
    t0 = datetime.now() - timedelta(days=6)
    for i, d in enumerate(DEMO):
        at = (t0 + timedelta(hours=9 * i)).isoformat(timespec="seconds")
        cur = conn.execute(
            "INSERT OR IGNORE INTO leads (source_id, created_at, updated_at, name, branche, category, category_label, address, city,"
            " lat, lon, distance_km, website, email, phone, status, interest, interest_reason, fit_score, fit_reason, research,"
            " contact_name, subject, greeting, body, channel, sent_at, replied_at, unread, rejected_by)"
            " VALUES (?,?,?,?,?,?,?,?,?,0,0,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"demo:{i}", at, at, d["name"], d["branche"], d["category"], d["category_label"], d["address"], d["city"],
             d["distance_km"], "https://ylvalabs.de", d.get("email") or None, "04642 000000", d["status"], d.get("interest"),
             d.get("grund"), d.get("fit_score"), d.get("fit_reason"),
             json.dumps({"groesse": "Beispieldaten", "zeitfresser": d.get("zeitfresser", []), "pages": [], "emails": []}),
             d.get("contact_name"), d.get("subject"), d.get("greeting"), d.get("body"),
             d.get("channel", "email") if d["status"] in ("versendet", "beantwortet") else None,
             at if d["status"] in ("versendet", "beantwortet") else None,
             at if d.get("reply") else None, 1 if d["status"] in ("entwurf",) or d.get("interest") == "qualified" else 0,
             "ki" if d["status"] == "aussortiert" else None))
        if not cur.rowcount:
            continue
        lead_id = cur.lastrowid
        leaddb.add_event(conn, lead_id, "draft" if d.get("body") else "status",
                         f"Passt ({d['fit_score']}/100). {d['fit_reason']}" if d.get("body") else (d.get("fit_reason") or "Gefunden."))
        if d["status"] in ("versendet", "beantwortet"):
            leaddb.add_event(conn, lead_id, "sent", "Kontakt aufgenommen (Beispiel).", author="oskar@ylvalabs.de")
        if d.get("reply"):
            leaddb.add_event(conn, lead_id, "reply", d["reply"], author=d.get("contact_name") or d["name"],
                             meta={"subject": "Re: " + d["subject"]})
            leaddb.add_event(conn, lead_id, "classify", d["reply_summary"],
                             meta={"grund": d["grund"], "naechster_schritt": d["schritt"]})
    # Ein Beispiel-Lauf mit Kosten, damit die Kostenanzeige etwas zeigt
    if not conn.execute("SELECT 1 FROM runs").fetchone():
        at = (t0 + timedelta(days=5)).isoformat(timespec="seconds")
        cur = conn.execute(
            "INSERT INTO runs (started_at, ended_at, started_by, params, status, message, counts) VALUES (?,?,?,?,?,?,?)",
            (at, at, "oskar@ylvalabs.de", json.dumps({"vorpruefen": 30, "entwuerfe": 5, "budget_eur": 2.0, "suchen": True}),
             "fertig", "2 neue Entwürfe, 30 vorgeprüft, 6 analysiert. Kosten: 0,42 € (Beispiel).",
             json.dumps({"neu_gefunden": 412, "grobfilter": 131, "vorgeprueft": 30, "vorpruefung_durch": 11,
                         "analysiert": 6, "analyse_durch": 4, "entwuerfe": 2})))
        run_id = cur.lastrowid
        rows = [("vorpruefung", "claude-haiku-4-5", 30, 3100, 160, 0.0039),
                ("analyse", "claude-sonnet-5", 6, 9800, 2100, 0.0406),
                ("entwurf", "claude-opus-5", 2, 3300, 1500, 0.054),
                ("lernen", "claude-sonnet-5", 1, 4200, 1600, 0.0244)]
        for stage, model, n, tin, tout, usd in rows:
            for _ in range(n):
                conn.execute("INSERT INTO ai_usage (at, run_id, stage, model, input_tokens, output_tokens, cost_usd)"
                             " VALUES (?,?,?,?,?,?,?)", (at, run_id, stage, model, tin, tout, usd))
    conn.commit()
