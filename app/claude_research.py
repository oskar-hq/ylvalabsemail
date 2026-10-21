"""Recherche mit dem eigenen Claude-Konto statt mit der Kartensuche oder API-Tokens.

Die App baut einen Prompt (aus dem Gehirn, dem Suchgebiet und den Platzhaltern der Vorlage). Den gebt ihr in Claude
Code oder claude.ai mit Websuche ein. Claude recherchiert die Betriebe und liefert eine CSV-Datei mit genau den
Spalten unten. Die ladet ihr in der App hoch: Die Betriebe landen als Kandidaten in der Liste und werden im nächsten
Lauf wie gefundene Betriebe nach Regeln bewertet (kostenlos). Was Claude herausgesucht hat (Ansprechperson, Anrede,
Zeitfresser, Aufhänger), wird für die Vorlage und die KI-Entwürfe verwendet.
"""
import csv
import hashlib
import io
import json
import re
from urllib.parse import urlparse

from . import crm, finder, leaddb, niches

FILENAME = "ylva-recherche.csv"
BRANCHEN = tuple(k for k, _ in niches.GROUPS)

# (Spaltenüberschrift, Feld, Pflicht, Beschreibung für Claude, Platzhalter in der Vorlage)
COLUMNS = [
    ("Firma", "name", True, "Name des Betriebs wie im Impressum", "{firma}"),
    ("Webseite", "website", True, "Startseite, mit https://", ""),
    ("E-Mail", "email", False, "Geschäftliche Adresse aus Impressum oder Kontaktseite. Persönliche Adresse der "
     "Inhaberin/des Geschäftsführers vor info@, wenn sie dort öffentlich steht", ""),
    ("Telefon", "phone", False, "Geschäftliche Nummer aus dem Impressum", ""),
    ("Ansprechperson", "contact_name", False, "Inhaber/in oder Geschäftsführer/in aus dem Impressum, nur Vor- und "
     "Nachname", ""),
    ("Anrede", "anrede", False, "Fertige Anrede für die Nachricht, z. B. „Guten Tag Frau Petersen,“ oder „Guten Tag "
     "Herr Hansen,“. Nur, wenn das Geschlecht eindeutig ist (z. B. „Inhaberin“), sonst „Guten Tag Vorname "
     "Nachname,“. Ohne Ansprechperson leer lassen", "{anrede}"),
    ("Straße", "street", False, "Straße und Hausnummer", ""),
    ("PLZ", "postcode", False, "Postleitzahl", ""),
    ("Ort", "city", True, "Ort des Betriebs", "{ort}"),
    ("Branche", "branche", True, "Genau eines von: " + ", ".join(BRANCHEN), ""),
    ("Nische", "niche", False, "Genau einer der Namen aus der Verteilung oben (z. B. „Werbeagentur“)", ""),
    ("Kategorie", "category_label", True, "Kurz und deutsch, z. B. „Tischlerei“, „Ambulante Pflege“, "
     "„Hausverwaltung“", "{kategorie}"),
    ("Tätigkeit", "taetigkeit", False, "Ein Satz, was der Betrieb macht und für wen (z. B. „Bäder und Heizungen für "
     "Privatkunden im Raum Schleswig“)", ""),
    ("Mitarbeitende", "groesse", False, "Nur eine Zahl, wenn sie irgendwo genannt wird (Webseite, Stellenanzeige, "
     "Presse). Sonst leer lassen, nicht schätzen", ""),
    ("Zeitfresser", "zeitfresser", False, "Ein bis zwei konkrete Abläufe, die bei genau diesem Betrieb vermutlich "
     "viel Zeit kosten, kurz und passend zum Satz „Bei Betrieben wie … kostet oft das hier viel Zeit: …“. Mehrere "
     "mit | trennen, z. B. „Angebote und Aufmaß|Tourenplanung“", "{zeitfresser}"),
    ("Aufhänger", "aufhaenger", False, "Ein ganzer Satz über etwas Konkretes, das ihr bei genau diesem Betrieb "
     "gesehen habt, in Sie-Form, z. B. „Auf Ihrer Webseite habe ich gesehen, dass Sie gerade eine Bürokraft für die "
     "Disposition suchen.“ Nur Belegtes, nichts erfinden", "{aufhaenger}"),
    ("Begründung", "begruendung", False, "Ein Satz, warum der Betrieb passt (Größe, Druck, Abläufe)", ""),
    ("Quelle", "quelle", False, "Adresse der Seite, auf der die wichtigsten Angaben stehen (z. B. Impressum)", ""),
    ("Breite", "lat", False, "Geografische Breite, z. B. 54.6614, wenn bekannt", ""),
    ("Länge", "lon", False, "Geografische Länge, z. B. 9.9311, wenn bekannt", ""),
]
HEADERS = [c[0] for c in COLUMNS]
# Platzhalter, die die App ohne Recherche selbst füllt
OWN_PLACEHOLDERS = {"{absender}": "Name aus den Einstellungen (Unterschrift unter Akquise-Nachrichten)",
                    "{verstaendnis}": "Satz zum Alltag der Branche (setzt die App ein)",
                    "{beispiel}": "Konkretes Beispiel, was wir bauen könnten (setzt die App ein)"}


class CsvError(ValueError):
    pass


def _region_text(settings):
    region = settings.get("LEADS_REGION")
    if region == "DE-SH":
        return "in ganz Schleswig-Holstein (Sitz von Ylva Labs: Kappeln)"
    if region:
        return f"im Gebiet {region}"
    lat, lon = settings["LEADS_CENTER"]
    return f"im Umkreis von {settings['LEADS_RADIUS_KM']:g} km um den Punkt {lat},{lon}"


def placeholders(template_text):
    return sorted(set(re.findall(r"\{\w+\}", template_text or "")))


def niche_plan(weights, count):
    """Wie viele Betriebe je Nische? Nach Prozent, gerundet, zusammen genau count."""
    raw = {k: w * count / 100 for k, w in weights.items()}
    out = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[:count - sum(out.values())]:
        out[k] += 1
    return out


def build_prompt(brain_text, settings, count=40, branche="", known_hosts=(), template_text="", weights=None):
    """Der Prompt für Claude. `known_hosts`: Webseiten, die schon in der App sind und nicht nochmal kommen sollen.
    `weights`: die gewählten Nischen mit Prozent; ohne Branchen-Schwerpunkt verteilt Claude die Betriebe danach."""
    sections = leaddb.brain_sections(brain_text, ["Über uns", "Wen wir suchen"]).strip()
    used = placeholders(template_text)
    lines = []
    for header, _field, required, desc, ph in COLUMNS:
        note = f" Wird in der Nachricht als {ph} eingesetzt." if ph and (not used or ph in used) else ""
        lines.append(f"- {header}{' (Pflicht)' if required else ''}: {desc}.{note}")
    focus = f" Schwerpunkt dieses Mal: Branche „{branche}“." if branche else ""
    chosen = {k: w for k, w in (weights or {}).items() if not branche or niches.NICHES[k]["branche"] == branche}
    split = ""
    if chosen:
        total = sum(chosen.values())
        plan = niche_plan({k: round(w * 100 / total) for k, w in chosen.items()}, count)
        split = "\nVerteilung nach Nischen (ungefähr so viele Betriebe je Nische):\n" + "\n".join(
            f"- {niches.NICHES[k]['label']}: {n} ({round(chosen[k] * 100 / total)} %)"
            for k, n in plan.items() if n) + "\n"
    agencies = any(niches.NICHES[k]["branche"] in ("kreativ", "it") for k in chosen)
    no_agencies = "" if agencies else ", keine IT-Firmen oder Agenturen"
    known = ""
    if known_hosts:
        known = ("\nDiese Webseiten kennen wir schon, bitte nicht noch einmal aufnehmen:\n"
                 + ", ".join(sorted(known_hosts)[:400]) + "\n")
    return f"""Du recherchierst für Ylva Labs passende Betriebe für eine Erstansprache. Nutze die Websuche und lies \
die Webseiten der Betriebe (Startseite, Impressum, Kontakt, Über uns, Karriere).

Aufgabe: Finde {count} Betriebe {_region_text(settings)}, die nach den Kriterien unten gut passen.{focus}
{split}Lieber weniger, aber passende Betriebe als eine lange Liste. Jeder Betrieb nur einmal.

{sections}

Regeln:
- Nur öffentliche, geschäftliche Angaben aus Impressum, Kontaktseite oder Stellenanzeigen. Keine privaten Adressen \
oder Nummern, keine Angaben aus sozialen Netzwerken.
- Nichts erfinden. Was du nicht belegen kannst, bleibt leer.
- Keine Filialen von Ketten, keine Franchise, keine Behörden{no_agencies}.
- Nur Betriebe mit eigener Webseite.
{known}
Ergebnis: eine CSV-Datei „{FILENAME}“ (UTF-8, Trennzeichen Semikolon, Text mit Semikolon in Anführungszeichen, \
eine Zeile pro Betrieb) mit genau dieser Kopfzeile:

{';'.join(HEADERS)}

Die Spalten:
{chr(10).join(lines)}

Wenn du Dateien anlegen kannst (Claude Code), speichere die Datei als „{FILENAME}“. Sonst gib den kompletten Inhalt \
in einem einzigen Codeblock aus, ohne weiteren Text darin. Danach kurz: wie viele Betriebe, und wo du unsicher warst.
"""


def known_hosts(conn):
    hosts = set()
    for r in conn.execute("SELECT website FROM leads WHERE IFNULL(website,'') != '' ORDER BY id DESC LIMIT 2000"):
        host = urlparse(r["website"]).netloc.lower().removeprefix("www.")
        if host:
            hosts.add(host)
    return hosts


def _num(value):
    try:
        return float(str(value).replace(",", ".").strip())
    except ValueError:
        return None


def parse(raw):
    """CSV-Text (auch aus einem Codeblock kopiert) -> Liste von (Zeile, Daten)."""
    text = raw.decode("utf-8-sig", "replace") if isinstance(raw, bytes) else raw
    text = re.sub(r"^\s*```[a-z]*\s*\n|\n\s*```\s*$", "", text.strip("﻿ \n"))
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
    except csv.Error:
        dialect = crm._Semicolon
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    by_key = {crm._key(header): field for header, field, *_ in COLUMNS}
    by_key.update({crm._key(a): field for field, names in crm.IMPORT_ALIASES.items() for a in names
                   if crm._key(a) not in by_key and field != "note"})
    mapping = {}
    for header in reader.fieldnames or []:
        field = by_key.get(crm._key(header))
        if field and field not in mapping.values():
            mapping[header] = field
    if "name" not in mapping.values():
        raise CsvError("Keine Spalte „Firma“ gefunden. Bitte die Kopfzeile aus dem Prompt verwenden.")
    rows = []
    for line, row in enumerate(reader, start=2):
        data = {field: (row.get(header) or "").strip() for header, field in mapping.items()}
        if any(data.values()):
            rows.append((line, data))
    return rows


def import_csv(conn, raw, author, center):
    """Betriebe als Kandidaten anlegen. Gibt {neu, uebersprungen: [(zeile, grund)]} zurück."""
    created, skipped = 0, []
    for line, d in parse(raw):
        name = d.get("name", "")
        email = d.get("email", "").lower()
        website = crm.website_url(d.get("website", ""))
        if not name:
            skipped.append((line, "Firma fehlt"))
            continue
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            email = ""
        if not website and not email:
            skipped.append((line, f"{name}: weder Webseite noch E-Mail"))
            continue
        dup = crm.find_duplicate(conn, name, email, website)
        if dup:
            skipped.append((line, f"{name}: gibt es schon („{dup['name']}“)"))
            continue
        niche = niches.by_label(d.get("niche", ""))
        branche = d.get("branche", "").lower().strip()
        if branche not in BRANCHEN:
            branche = niches.NICHES[niche]["branche"] if niche else "sonstige"
        city = d.get("city", "")
        street = d.get("street", "")
        plz_ort = " ".join(x for x in (d.get("postcode", ""), city) if x)
        lat, lon = _num(d.get("lat", "")), _num(d.get("lon", ""))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            lat = lon = None
        distance = round(finder.haversine_km(center[0], center[1], lat, lon), 1) if lat is not None else None
        size = _num(d.get("groesse", ""))
        info = {"import": {
            "quelle": "Claude-Recherche",
            "anrede": d.get("anrede", ""),
            "zeitfresser": [z.strip() for z in re.split(r"\|", d.get("zeitfresser", "")) if z.strip()][:3],
            "aufhaenger": d.get("aufhaenger", ""),
            "groesse": int(size) if size and 0 < size < 100000 else None,
            "begruendung": d.get("begruendung", ""),
            "taetigkeit": d.get("taetigkeit", ""),
            "url": d.get("quelle", ""),
        }}
        now = leaddb.now()
        source_id = "claude:" + hashlib.sha1(f"{name}|{email}|{website}".lower().encode()).hexdigest()[:16]
        cur = conn.execute(
            "INSERT OR IGNORE INTO leads (source_id, created_at, updated_at, name, branche, category, category_label,"
            " niche, address, city, lat, lon, distance_km, website, email, phone, contact_name, status, research)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (source_id, now, now, name, branche, "",
             d.get("category_label") or (niches.NICHES[niche]["label"] if niche else "Betrieb"), niche,
             ", ".join(x for x in (street, plz_ort) if x), city, lat, lon, distance, website or None,
             email or None, d.get("phone") or None, d.get("contact_name") or None, "kandidat",
             json.dumps(info, ensure_ascii=False)))
        if not cur.rowcount:
            skipped.append((line, f"{name}: gibt es schon"))
            continue
        conn.commit()
        leaddb.add_event(conn, cur.lastrowid, "status", "Aus der Claude-Recherche übernommen (CSV).", author=author)
        created += 1
    return {"neu": created, "uebersprungen": skipped}
