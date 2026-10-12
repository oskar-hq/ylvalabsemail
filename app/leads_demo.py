"""Beispieldaten für den Demo-Modus: so sehen Akquise und CRM aus, bevor echte Betriebe gefunden sind.
Alle Betriebe sind erfunden."""
import json
from datetime import date, datetime, timedelta

from . import crm, leaddb

# Ungefähre Ortsmitten (Breite, Länge) für die Karte
ORTE = {"Kappeln": (54.661, 9.931), "Arnis": (54.630, 9.932), "Süderbrarup": (54.636, 9.773),
        "Rabenkirchen-Faulück": (54.627, 9.866), "Eckernförde": (54.469, 9.838), "Damp": (54.585, 10.020),
        "Kiel": (54.323, 10.123), "Flensburg": (54.784, 9.437), "Schleswig": (54.521, 9.566), "Rendsburg": (54.305, 9.664),
        "Neumünster": (54.073, 9.984), "Husum": (54.477, 9.052), "Heide": (54.196, 9.093), "Itzehoe": (53.925, 9.516),
        "Lübeck": (53.866, 10.686), "Bad Segeberg": (53.936, 10.309), "Eutin": (54.137, 10.618), "Plön": (54.162, 10.421),
        "Elmshorn": (53.754, 9.652), "Niebüll": (54.789, 8.829), "Bad Oldesloe": (53.811, 10.374), "Ratzeburg": (53.700, 10.757)}
# Reserve: passende Betriebe ohne Nachricht, verteilt übers Land (Anzahl je Ort), damit die Karte etwas zeigt
RESERVE = {"Kiel": 7, "Flensburg": 6, "Schleswig": 4, "Rendsburg": 4, "Eckernförde": 3, "Neumünster": 4, "Husum": 2,
           "Heide": 2, "Lübeck": 5, "Bad Segeberg": 2, "Itzehoe": 1, "Elmshorn": 2, "Kappeln": 3}
GEWERKE = [("handwerk", "craft=electrician", "Elektro"), ("handwerk", "craft=hvac", "Heizung/Lüftung"),
           ("handwerk", "craft=carpenter", "Tischlerei"), ("pflege", "healthcare=home_care", "Ambulante Pflege"),
           ("handwerk", "craft=roofer", "Dachdeckerei"), ("handwerk", "craft=gardener", "Garten- und Landschaftsbau"),
           ("dienstleistung", "office=property_management", "Hausverwaltung")]

# Beispielhafte Regel-Treffer für die Akte
SIGNALE = {
    "handwerk": [(20, "Kernbranche Handwerk"), (15, "Etwa 14 Mitarbeitende, ideale Größe"),
                 (25, "Sucht Personal fürs Büro, die Verwaltung oder die Disposition"), (5, "Formulare als PDF zum Ausdrucken"),
                 (5, "Inhaber oder Geschäftsführung im Impressum: Jens Hansen")],
    "pflege": [(20, "Kernbranche Pflege"), (15, "Etwa 30 Mitarbeitende, ideale Größe"),
               (5, "Pflichtdokumentation, Nachweise oder Zertifizierung"),
               (5, "Arbeitet vor Ort beim Kunden (Touren, Montage, Hausbesuche)"), (10, "Wächst (3 offene Stellen)")],
    "landwirtschaft": [(20, "Kernbranche Landwirtschaft"), (5, "Pflichtdokumentation, Nachweise oder Zertifizierung"),
                       (5, "Persönliche E-Mail-Adresse statt info@"), (5, "Anfragen laufen vor allem übers Telefon")],
}

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
         grund="Stichwort „spannend“", schritt="Nächste Woche vormittags anrufen und Vor-Ort-Tag vorschlagen."),
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
    dict(name="Metallbau Schlei (Beispiel)", branche="handwerk", category="craft=metal_construction", category_label="Metallbau",
         city="Kappeln", address="Werftstraße 7, 24376 Kappeln", distance_km=2.1, status="passt", fit_score=71,
         email="post@metallbau-schlei.example", contact_name="Birte Lorenzen",
         fit_reason="Regeln: 71/100, passt. Kernbranche Handwerk; Etwa 25 Mitarbeitende, ideale Größe; Faxnummer auf der Webseite.",
         zeitfresser=["Angebote, Aufmaß und Nachkalkulation", "Anfragen und Bestellungen per Fax und Telefon"]),
    dict(name="Gärtnerei Grünholz (Beispiel)", branche="handwerk", category="craft=gardener", category_label="Garten- und Landschaftsbau",
         city="Süderbrarup", address="Am Moor 1, 24392 Süderbrarup", distance_km=13.0, status="beantwortet", interest="qualified",
         fit_score=77, email="info@gruenholz.example", contact_name="Hauke Thomsen", stage="kunde", deal_eur=6500,
         fit_reason="Garten- und Landschaftsbau mit 18 Leuten und vielen Baustellen gleichzeitig.",
         zeitfresser=["Einsatz- und Tourenplanung"], subject="Baustellen planen ohne Zettelwirtschaft", greeting="Moin Herr Thomsen,",
         body="…", reply="Moin, lassen Sie uns gerne sprechen. Dienstag passt.", reply_summary="Interesse! Termin am Dienstag.",
         grund="Stichwort „gerne“", schritt="Termin bestätigen."),
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


def _coords(city, i):
    """Ortsmitte plus eine kleine, feste Streuung, damit Punkte nicht aufeinanderliegen."""
    lat, lon = ORTE.get(city, (54.3, 9.9))
    return lat + ((i * 37) % 11 - 5) * 0.006, lon + ((i * 53) % 13 - 6) * 0.009


def _seed_reserve(conn, t0):
    n = 0
    for city, count in RESERVE.items():
        for k in range(count):
            branche, category, label = GEWERKE[(n * 3 + k) % len(GEWERKE)]
            score = 52 + (n * 7) % 34
            at = (t0 + timedelta(hours=n)).isoformat(timespec="seconds")
            name = f"{label} {city} {k + 1} (Beispiel)"
            lat, lon = _coords(city, n + 100)
            conn.execute(
                "INSERT OR IGNORE INTO leads (source_id, created_at, updated_at, name, branche, category, category_label,"
                " address, city, lat, lon, distance_km, website, email, status, fit_score, fit_reason, research)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"demo:reserve:{n}", at, at, name, branche, category, label, city, city, lat, lon,
                 round(((lat - 54.661) ** 2 * 111 ** 2 + (lon - 9.931) ** 2 * 65 ** 2) ** 0.5, 1),
                 "https://ylvalabs.de", f"info@beispiel-{n}.example", "passt", score,
                 f"Regeln: {score}/100, passt. Kernbranche; ideale Größe (Beispiel).",
                 json.dumps({"groesse": "Beispieldaten", "zeitfresser": [], "signale": SIGNALE.get(branche, [])})))
            n += 1


def _stage(d):
    if d.get("interest") == "kein_interesse":
        return "verloren"
    if d.get("interest"):
        return "antwort"
    return {"versendet": "kontaktiert", "entwurf": "neu", "passt": "neu"}.get(d["status"])


def seed(conn):
    t0 = datetime.now() - timedelta(days=6)
    for i, d in enumerate(DEMO):
        at = (t0 + timedelta(hours=9 * i)).isoformat(timespec="seconds")
        cur = conn.execute(
            "INSERT OR IGNORE INTO leads (source_id, created_at, updated_at, name, branche, category, category_label, address, city,"
            " lat, lon, distance_km, website, email, phone, status, interest, interest_reason, fit_score, fit_reason, research,"
            " contact_name, subject, greeting, body, channel, sent_at, replied_at, unread, rejected_by, stage, stage_at, deal_eur)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"demo:{i}", at, at, d["name"], d["branche"], d["category"], d["category_label"], d["address"], d["city"],
             *_coords(d["city"], i),
             d["distance_km"], "https://ylvalabs.de", d.get("email") or None, "04642 000000", d["status"], d.get("interest"),
             d.get("grund"), d.get("fit_score"), d.get("fit_reason"),
             json.dumps({"groesse": "Beispieldaten", "zeitfresser": d.get("zeitfresser", []), "pages": [], "emails": [],
                         "signale": SIGNALE.get(d["branche"], []) if (d.get("fit_score") or 0) >= 60 else []}),
             d.get("contact_name"), d.get("subject"), d.get("greeting"), d.get("body"),
             d.get("channel", "email") if d["status"] in ("versendet", "beantwortet") else None,
             ((datetime.now() - timedelta(days=9)).isoformat(timespec="seconds") if d["status"] == "versendet"
              else at if d["status"] == "beantwortet" else None),  # „versendet“ ist schon zum Nachfassen fällig
             at if d.get("reply") else None, 1 if d["status"] in ("entwurf",) or d.get("interest") == "qualified" else 0,
             "regel" if d["status"] == "aussortiert" else None, d.get("stage") or _stage(d), at, d.get("deal_eur")))
        if not cur.rowcount:
            continue
        lead_id = cur.lastrowid
        leaddb.add_event(conn, lead_id, "draft" if d.get("body") else "status",
                         f"Passt ({d['fit_score']}/100). {d['fit_reason']}" if d.get("body") else (d.get("fit_reason") or "Gefunden."),
                         author="Regeln")
        if d["status"] in ("versendet", "beantwortet"):
            leaddb.add_event(conn, lead_id, "sent", "Kontakt aufgenommen (Beispiel).", author="oskar@ylvalabs.de")
        if d.get("reply"):
            leaddb.add_event(conn, lead_id, "reply", d["reply"], author=d.get("contact_name") or d["name"],
                             meta={"subject": "Re: " + d["subject"]})
            leaddb.add_event(conn, lead_id, "classify", d["reply_summary"], author="Regeln",
                             meta={"grund": d["grund"], "naechster_schritt": d["schritt"]})
    _seed_reserve(conn, t0)
    # Wiedervorlagen, damit „Heute dran“ etwas zeigt
    if not conn.execute("SELECT 1 FROM tasks").fetchone():
        names = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM leads WHERE source_id LIKE 'demo:%'")}
        for name, days, text in [("Tischlerei Hansen (Beispiel)", 0, "Anrufen, vormittags (hat um Rückruf gebeten)"),
                                 ("Pflegedienst Schleiufer (Beispiel)", 150, "Im Frühjahr wieder melden"),
                                 ("Gärtnerei Grünholz (Beispiel)", 7, "Zwischenstand Prototyp zeigen")]:
            if name in names:
                crm.add_task(conn, names[name], (date.today() + timedelta(days=days)).isoformat(), text, "oskar@ylvalabs.de")
    # Ein Beispiel-Lauf mit Kosten, damit die Kostenanzeige etwas zeigt
    if not conn.execute("SELECT 1 FROM runs").fetchone():
        at = (t0 + timedelta(days=5)).isoformat(timespec="seconds")
        cur = conn.execute(
            "INSERT INTO runs (started_at, ended_at, started_by, params, status, message, counts) VALUES (?,?,?,?,?,?,?)",
            (at, at, "oskar@ylvalabs.de", json.dumps({"bewerten": 50, "entwuerfe": 2, "budget_eur": 2.0, "suchen": True}),
             "fertig", "50 bewertet, 9 passen, 2 Entwürfe. KI-Kosten: 0,13 € (Beispiel).",
             json.dumps({"neu_gefunden": 412, "grobfilter": 131, "bewertet": 50, "passen": 9, "entwuerfe": 2})))
        run_id = cur.lastrowid
        rows = [("entwurf", "claude-opus-5", 2, 3300, 1500, 0.054),
                ("lernen", "claude-sonnet-5", 1, 4200, 1600, 0.0244)]
        for stage, model, n, tin, tout, usd in rows:
            for _ in range(n):
                conn.execute("INSERT INTO ai_usage (at, run_id, stage, model, input_tokens, output_tokens, cost_usd)"
                             " VALUES (?,?,?,?,?,?,?)", (at, run_id, stage, model, tin, tout, usd))
    conn.commit()
