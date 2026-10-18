"""Bewertung der Betriebe nach festen Regeln, ganz ohne KI.

Die Webseite wird gelesen (kostenlos), dann vergibt jede Regel Punkte mit einem kurzen Grund. Die Summe (0–100)
entscheidet, ob ein Betrieb in die Pipeline kommt. Alles ist nachvollziehbar: In der Akte steht jede Regel, die
gegriffen hat.

Dazu lernt der Algorithmus aus euren Ergebnissen, auch ohne KI: Branchen mit Antworten und Interesse bekommen einen
Zuschlag, Branchen mit vielen Absagen einen Abzug. Das wird nur gezählt.

Außerdem hier: Antworten von Firmen nach Stichworten einordnen (Interesse, später, Absage, Abmeldung, Abwesenheit).
"""
import re
from datetime import datetime
from urllib.parse import urlparse

from .regions import BAU_CATEGORIES

BASE = 10  # Startwert, bevor eine Regel greift
MIN_DEFAULT = 50  # Mindestpunktzahl (LEADS_RULE_MIN); ins CRM kommen pro Lauf nur die besten (LEADS_RUN_TOP)

# Wen wir suchen (siehe Gehirn, „Wen wir suchen“): Stufe A ist der Kern, Stufe B passt oft, alles andere selten.
TIER_A = {"handwerk", "pflege", "landwirtschaft"}
TIER_B = {"dienstleistung", "industrie"}
BRANCHE_LABELS = {"handwerk": "Handwerk", "pflege": "Pflege", "landwirtschaft": "Landwirtschaft",
                  "industrie": "Werft und Bootsbau", "dienstleistung": "Dienstleistung (Verwaltung, Planung, Logistik)",
                  "handel": "Handel", "gastgewerbe": "Gastgewerbe"}
# Typische Zeitfresser je Branche (aus „Beispiele, was wir bauen können“ im Gehirn)
BRANCHE_ZEITFRESSER = {"handwerk": "Angebote, Aufmaß und Nachkalkulation",
                       "pflege": "Dokumentation nach jedem Einsatz",
                       "landwirtschaft": "Dokumentationspflichten und Förderanträge"}

# (Schlüssel, Punkte, Grund, Muster, Zeitfresser für den Entwurf). Muster werden im kleingeschriebenen Text gesucht.
TEXT_RULES = [
    ("fax", 5, "Faxnummer auf der Webseite", r"\b(?:tele)?fax\b\s*(?:nr\.?|nummer)?\s*[:.]?\s*[+(0-9]",
     "Anfragen und Bestellungen per Fax und Telefon"),
    ("telefon", 5, "Anfragen laufen vor allem übers Telefon",
     r"rufen sie (?:uns )?(?:gerne |einfach )?an|telefonisch (?:erreichbar|vereinbaren|unter)|termin(?:e)? nach telefonischer",
     "Anfragen, die alle übers Telefon laufen"),
    ("notdienst", 5, "Notdienst oder Bereitschaft, also viele spontane Anfragen",
     r"notdienst|rund um die uhr|24\s*/\s*7|24[ -]?h\b|24[ -]stunden|bereitschaftsdienst", "viele spontane Kundenanfragen"),
    ("touren", 5, "Arbeitet vor Ort beim Kunden (Touren, Montage, Hausbesuche)",
     r"\btouren\b|tourenplan|ambulant|hausbesuch|beim kunden vor ort|montage|einsatzplanung|fuhrpark",
     "Einsatz- und Tourenplanung"),
    ("doku", 5, "Pflichtdokumentation, Nachweise oder Zertifizierung",
     r"dokumentation|nachweis|prüfprotokoll|wartungsprotokoll|zertifiziert|iso 9001|qualitätsmanagement|"
     r"düngeverordnung|hygieneplan|gefährdungsbeurteilung|bautagebuch", "Dokumentation und Nachweise"),
    ("inhaber", 3, "Inhabergeführt (Familien- oder Meisterbetrieb)", r"familienbetrieb|familienunternehmen|meisterbetrieb|"
     r"inhabergeführt|in (?:zweiter|dritter|vierter|\d\.) generation", None),
]
OFFICE_JOB = re.compile(r"bürokauf|kauffrau für büro|kaufmann für büro|büromanagement|verwaltungsangestellt|"
                        r"sachbearbeit|bürokraft|büroassist|assistenz der geschäftsführung|disponent|disposition|"
                        r"buchhalt|bürohilfe|mitarbeiter(?:in)? (?:im |für das |fürs )?büro|kaufmännische")
ANY_JOB = re.compile(r"wir suchen|stellenangebot|stellenanzeige|karriere|offene stellen|jobs bei|verstärkung|"
                     r"bewirb dich|bewerben sie sich|ausbildungsplatz|wir stellen ein")
GROWTH = re.compile(r"neuer? (?:standort|niederlassung|filiale|halle|betriebshof)|(?:wir )?(?:wachsen|expandieren)|"
                    r"erweitern unser team|eröffne(?:n|t) (?:im|in|unsere)|neubau unserer")
JOB_TAG = re.compile(r"\((?:m|w|d)\s*/\s*(?:m|w|d)\s*/\s*(?:m|w|d)\)")
DIGITAL = re.compile(r"kundenportal|online-portal|zur app|unsere app|app herunterladen|online-terminbuchung|"
                     r"termin online buchen|online buchen|jetzt online termin")
IT_FIRM = re.compile(r"webdesign|softwareentwicklung|it-dienstleist|it-service|digitalagentur|werbeagentur|"
                     r"online-marketing|app-entwicklung|ki-beratung|ki-lösungen|seo-agentur|internetagentur")
AUTHORITY = re.compile(r"\b(?:amt|ämter|gemeinde|stadtverwaltung|kreisverwaltung|landesamt|behörde|bundesagentur)\b")
FRANCHISE = re.compile(r"franchise|ein unternehmen der .{0,40}gruppe|teil der .{0,40}gruppe|konzern")
BOOKING_HOSTS = ("doctolib", "calendly", "terminland", "etermin", "shore.com", "treatwell", "planity", "samedi",
                 "simplybook", "timify", "jameda")
PDF_FORM = re.compile(r"formular|antrag|auftrag|anmeld|bestell|vordruck|fragebogen")
SIZE_RE = re.compile(r"(?:über |rund |ca\.? |circa |mehr als |knapp )?(\d{1,4})\s*(?:\+\s*)?(?:festangestellten? |engagierten? |"
                     r"qualifizierten? |motivierten? )?(?:mitarbeiter(?:innen|n)?|mitarbeitenden?|beschäftigten?|angestellten?|"
                     r"kolleg(?:inn)?en|fachkräften?|pflegekräften?|monteuren?|gesellen|leuten?)\b")
TEAM_RE = re.compile(r"team (?:aus|von|mit) (?:über |rund |ca\.? |mehr als )?(\d{1,4})\b")
COPYRIGHT_RE = re.compile(r"(?:©|\(c\)|copyright)\s*(?:\d{4}\s*[-–]\s*)?((?:19|20)\d{2})")
GENERIC_LOCAL = {"info", "kontakt", "contact", "office", "mail", "post", "service", "buero", "büro", "hallo", "hello",
                 "team", "anfrage", "anfragen", "verwaltung", "zentrale", "empfang", "termine", "praxis", "webmaster",
                 "datenschutz", "bewerbung", "jobs", "karriere", "rechnung", "buchhaltung", "noreply", "no-reply"}
CONTACT_RE = re.compile(r"(?i:inhaber(?:in)?|geschäftsführer(?:in)?|geschäftsführung|vertreten durch|vertretungsberechtigt"
                        r"(?:e|er)?(?: geschäftsführer(?:in)?)?|betriebsleiter(?:in)?|praxisinhaber(?:in)?|"
                        r"pflegedienstleitung)\s*[:\-]?\s*((?:dr\.\s*|dipl\.-\w+\.?\s*)?[A-ZÄÖÜ][a-zäöüß]+"
                        r"(?:[ -][A-ZÄÖÜ][a-zäöüß]+){1,2})")
NAME_STOP = {"Telefon", "Tel", "Fax", "Mail", "Email", "Registergericht", "Amtsgericht", "Handelsregister", "Sitz",
             "Ust", "Steuernummer", "Straße", "Str", "Anschrift", "Kontakt", "Inhaltlich", "Verantwortlich", "Und",
             "Gmb", "Geschäftsführer", "Registernummer"}


def _compact(text):
    return re.sub(r"\s+", " ", text or "").strip()


def extract_size(text):
    """Größte plausible Mitarbeiterzahl im Text, sonst None."""
    numbers = []
    for rx in (SIZE_RE, TEAM_RE):
        for m in rx.finditer(text):
            n = int(m.group(1))
            if 1 <= n <= 5000 and not 1950 <= n <= 2100:
                numbers.append(n)
    return max(numbers) if numbers else None


def extract_contact(text):
    """Name der Inhaberin / des Geschäftsführers aus dem Impressum, sonst ''."""
    for m in CONTACT_RE.finditer(text):
        words = []
        for w in m.group(1).split():
            if w.rstrip(".,:") in NAME_STOP:
                break
            words.append(w)
        if len(words) >= 2:
            return " ".join(words)
    return ""


def _host(url):
    return urlparse(url or "").netloc.lower().removeprefix("www.")


def pick_email(emails, website, contact_name="", known=""):
    """Beste Kontaktadresse: persönlich vor info@, eigene Domain vor fremden (z. B. der Webagentur)."""
    candidates = [e for e in dict.fromkeys([*(emails or []), known]) if e and "@" in e]
    if not candidates:
        return ""
    host = _host(website)
    surname = contact_name.split()[-1].lower() if contact_name else ""

    def rank(addr):
        local, domain = addr.lower().split("@", 1)
        own = bool(host) and (domain == host or host.endswith("." + domain) or domain.endswith("." + host))
        personal = local not in GENERIC_LOCAL and not local.startswith(("info", "kontakt", "mail"))
        return (own, bool(surname) and surname in local, personal, addr == known)
    return max(candidates, key=rank)


def is_personal(addr):
    local = addr.lower().split("@", 1)[0] if addr and "@" in addr else ""
    return bool(local) and local not in GENERIC_LOCAL and not local.startswith(("info", "kontakt", "mail"))


def score(lead, research, learned=None, region=None, now=None):
    """Bewertet einen Betrieb. `research` kommt von finder.research_website, `learned` von learned_adjustments.

    `region` kommt von regions.bonus_info: Wachstumsregionen nach Gewerbeanmeldungen und Baugenehmigungen.
    Gibt {score, signals: [(punkte, grund)], ko: [gründe], email, contact_name, groesse, zeitfresser, auszug} zurück.
    Ein Ausschlussgrund (ko) heißt: kommt nie ins CRM, egal wie viele Punkte."""
    now = now or datetime.now()
    pages = research.get("pages") or []
    raw = "\n".join(p.get("text", "") for p in pages)
    text = raw.lower()
    title = " ".join((p.get("title") or "") for p in pages[:1]).lower()
    links = [link.lower() for link in research.get("links") or []]
    signals, ko = [], []

    def add(points, reason):
        signals.append((points, reason))

    # Branche: Stufe A ist der Kern
    branche = (lead.get("branche") or "sonstige").lower()
    label = BRANCHE_LABELS.get(branche, branche.capitalize())
    if branche in TIER_A:
        add(20, f"Kernbranche {label}")
    elif branche in TIER_B:
        add(8, f"Passende Branche: {label}")

    # Größe: 10–100 ist ideal für einen Piloten
    size = extract_size(text) or research.get("size_hint")  # sonst die Zahl aus der Claude-Recherche
    if size is not None:
        if 10 <= size <= 100:
            add(15, f"Etwa {size} Mitarbeitende, ideale Größe")
        elif 5 <= size < 10:
            add(5, f"Etwa {size} Mitarbeitende, eher klein")
        elif 100 < size <= 250:
            add(8, f"Etwa {size} Mitarbeitende, eher groß")
        elif size > 250:
            add(-10, f"Groß ({size} Mitarbeitende), entscheidet vermutlich nicht vor Ort")
        else:
            ko.append(f"Sehr klein ({size} Mitarbeitende), kaum Verwaltung")

    # Akuter Druck: sucht Leute fürs Büro, wächst
    jobs = len(JOB_TAG.findall(text))
    if OFFICE_JOB.search(text) and ANY_JOB.search(text):
        add(25, "Sucht Personal fürs Büro, die Verwaltung oder die Disposition")
    elif ANY_JOB.search(text):
        add(5, "Sucht Personal")
    if jobs >= 2 or GROWTH.search(text):
        add(10, f"Wächst ({jobs} offene Stellen)" if jobs >= 2 else "Wächst (neuer Standort oder Ausbau)")

    # Anzeichen für Papierkram und analoge Abläufe
    zeitfresser = []
    if branche in BRANCHE_ZEITFRESSER:
        zeitfresser.append(BRANCHE_ZEITFRESSER[branche])
    if OFFICE_JOB.search(text) and ANY_JOB.search(text):
        zeitfresser.append("ein knapp besetztes Büro")
    for key, points, reason, pattern, fresser in TEXT_RULES:
        if re.search(pattern, text):
            add(points, reason)
            if fresser:
                zeitfresser.append(fresser)
    pdfs = [link for link in links if link.split("?")[0].endswith(".pdf") and PDF_FORM.search(link)]
    if pdfs:
        add(5, "Formulare als PDF zum Ausdrucken")
        zeitfresser.append("Formulare, die ausgedruckt und abgetippt werden")
    years = [int(y) for y in COPYRIGHT_RE.findall(text)]
    if years and max(years) <= now.year - 5:
        add(3, f"Webseite lange nicht aktualisiert (© {max(years)})")

    # Schon digital: weniger Potenzial
    if any(h in link for link in links for h in BOOKING_HOSTS) or DIGITAL.search(text):
        add(-10, "Schon digital (Online-Buchung, Kundenportal oder App)")

    # Ausschlussgründe
    if IT_FIRM.search(text) or IT_FIRM.search(title):
        ko.append("IT-Firma oder Agentur, baut so etwas selbst")
    if AUTHORITY.search(title) or AUTHORITY.search((lead.get("name") or "").lower()):
        ko.append("Behörde oder Verwaltung")
    if FRANCHISE.search(text):
        ko.append("Teil einer Gruppe, Kette oder Franchise, entscheidet nicht vor Ort")

    # Erreichbarkeit: die Person, die entscheidet, direkt per Mail
    contact = extract_contact(raw)
    email = pick_email(research.get("emails"), lead.get("website"), contact, lead.get("email") or "")
    if contact:
        add(5, f"Inhaber oder Geschäftsführung im Impressum: {contact}")
    if email and is_personal(email):
        add(5, "Persönliche E-Mail-Adresse statt info@")
    if not email:
        ko.append("Keine E-Mail-Adresse gefunden")
    if not pages:
        ko.append("Webseite nicht lesbar")

    # Wachstumsregion (Statistik je Kreis): kleiner Zuschlag, ersetzt nicht die Prüfung des Betriebs
    info = (region or {}).get(lead.get("kreis"))
    if info:
        if info.get("gewerbe_top"):
            add(5, f"Wachstumsregion: viele Gewerbeanmeldungen im Kreis {info['name']}")
        bau_gewerk = lead.get("category") in BAU_CATEGORIES or (not lead.get("category") and branche == "handwerk")
        if info.get("bau_top") and bau_gewerk:
            add(8, f"Viele Baugenehmigungen im Kreis {info['name']}, volle Auftragsbücher absehbar")

    # Gelernt aus euren Ergebnissen (gezählt, keine KI)
    adj = (learned or {}).get(lead.get("category")) or (learned or {}).get(lead.get("branche"))
    if adj and adj[0]:
        add(adj[0], adj[1])

    total = max(0, min(100, BASE + sum(p for p, _ in signals)))
    excerpt = _compact(pages[0]["text"])[:2500] if pages else ""
    return {"score": total, "signals": signals, "ko": ko, "email": email, "contact_name": contact,
            "groesse": f"ca. {size} Mitarbeitende" if size else "unklar",
            "zeitfresser": list(dict.fromkeys(zeitfresser)), "auszug": excerpt}


def summary(result, threshold):
    """Ein Satz für die Übersicht, z. B. „72/100, passt: Kernbranche Handwerk; Sucht Personal fürs Büro …“."""
    top = [r for p, r in sorted(result["signals"], key=lambda s: -abs(s[0])) if p][:4]
    if result.get("ko"):
        return f"Regeln: {result['score']}/100, ausgeschlossen: " + "; ".join(result["ko"]) + "."
    verdict = "passt" if result["score"] >= threshold else "zu wenig Punkte"
    return f"Regeln: {result['score']}/100, {verdict}. " + "; ".join(top) + "."


def learned_adjustments(rows):
    """Aus euren Ergebnissen je Kategorie (und Branche) einen Zuschlag oder Abzug von höchstens 10 Punkten.

    rows: Dicts mit key, label, pos (Interesse, Gespräch, Kunde …) und neg (Absage, von euch aussortiert)."""
    result = {}
    for r in rows:
        pos, neg = r["pos"] or 0, r["neg"] or 0
        if pos + neg < 3:
            continue  # zu wenig Erfahrung
        points = round(10 * (pos - neg) / (pos + neg + 2))
        if points:
            result[r["key"]] = (points, f"Erfahrung mit {r['label']}: {pos:g}× gut gelaufen, {neg:g}× nicht")
    return result


def rules_table():
    """Für die Seite „So wird bewertet“: (Punkte oder None für Ausschluss, Regel)."""
    rows = [(20, "Kernbranche: Handwerk (Heizung/Sanitär, Elektro, Tischlerei, Metallbau, Dach, Bau, GaLaBau), "
                 "Pflege, Landwirtschaft"),
            (8, "Passende Branche: Hausverwaltung, Ingenieur- und Vermessungsbüro, Spedition, Werft und Bootsbau"),
            (25, "Sucht Personal fürs Büro, die Verwaltung oder die Disposition"), (5, "Sucht Personal (andere Stellen)"),
            (10, "Wächst: mindestens 2 offene Stellen oder neuer Standort"),
            (15, "10 bis 100 Mitarbeitende laut Webseite"), (8, "101 bis 250 Mitarbeitende"),
            (5, "5 bis 9 Mitarbeitende"), (-10, "Mehr als 250 Mitarbeitende")]
    rows += [(p, r) for _, p, r, _, _ in TEXT_RULES]
    rows += [(5, "Formulare als PDF zum Ausdrucken"), (3, "Webseite seit mindestens 5 Jahren nicht aktualisiert (©)"),
             (5, "Inhaber oder Geschäftsführung im Impressum gefunden"), (5, "Persönliche E-Mail-Adresse statt info@"),
             (-10, "Schon digital: Online-Terminbuchung, Kundenportal oder App"),
             (5, "Wachstumsregion: Kreis unter den 5 mit den meisten Gewerbeanmeldungen je 1.000 Einwohner"),
             (8, "Bau-Gewerk in einem der 5 Kreise mit den meisten Baugenehmigungen je 1.000 Einwohner"),
             (10, "Bis zu ±10: Erfahrung mit der Branche aus euren Ergebnissen")]
    return rows


KO_RULES = ["Keine E-Mail-Adresse (der Kontakt läuft per Mail)", "Weniger als 5 Mitarbeitende",
            "Kette, Gruppe oder Franchise (auch laut Kartendaten)", "IT-Firma oder Agentur", "Behörde",
            "Webseite fehlt oder ist nicht lesbar", "Außerhalb von Schleswig-Holstein (wird gar nicht gesucht)"]


# ---------------------------------------------------------------- Antworten einordnen (ohne KI)


AUTO_SUBJECT = re.compile(r"abwesen|out of office|automatische antwort|auto(?:matic)?[- ]?reply|autoreply|"
                          r"nicht im büro|urlaub|unzustellbar|undeliverable|delivery status|mail delivery|"
                          r"returned mail|zustellung fehlgeschlagen")
AUTO_BODY = re.compile(r"ich bin (?:bis|vom|ab|derzeit|zurzeit|zur zeit) .{0,60}(?:nicht im büro|abwesend|im urlaub|"
                       r"nicht erreichbar)|bin ich wieder (?:im büro|erreichbar)|diese nachricht wurde automatisch")
REPLY_RULES = [
    ("abmeldung", re.compile(r"keine (?:weiteren )?(?:werbe-?)?(?:e-?mails|mails|nachrichten|werbung|anfragen)|"
                             r"nicht mehr (?:kontaktieren|anschreiben|anrufen|schreiben)|abmelden|austragen|"
                             r"unsubscribe|aus (?:ihrem|dem) verteiler|löschen sie (?:meine|unsere) daten|"
                             r"unerwünscht|unaufgefordert|spam\b|unterlassen")),
    ("kein_interesse", re.compile(r"kein(?:en)? (?:interesse|bedarf)|nicht interessiert|brauchen (?:wir )?(?:so etwas |"
                                  r"das |sowas )?nicht|haben (?:bereits|schon) (?:eine lösung|einen anbieter|"
                                  r"einen dienstleister|ein system)|kommt für uns nicht in frage|leider nein|"
                                  r"nein,? danke|kein thema für uns|passt (?:für uns )?nicht")),
    ("mittel", re.compile(r"\bspäter\b|im (?:frühjahr|sommer|herbst|winter|neuen jahr|januar|februar|märz|april|mai|"
                          r"juni|juli|august|september|oktober|november|dezember)|(?:derzeit|aktuell|momentan|zurzeit|"
                          r"zur zeit|gerade) (?:nicht|keine|kein)|weitergeleitet|weiterleiten|zuständig ist|"
                          r"nach der saison|nächstes jahr|melde mich|melden uns")),
    ("qualified", re.compile(r"\bgerne?\b|klingt (?:gut|spannend|interessant)|interessant|interesse|rufen sie (?:mich |"
                             r"uns )?(?:gern |gerne )?an|melden sie sich|termin|telefonieren|gespräch|vorbeikommen|"
                             r"wann passt|zeit für|ja,? (?:bitte|gern)|erzählen sie|mehr erfahren")),
]
REPLY_STEPS = {"qualified": "Schnell antworten oder anrufen und einen Termin vorschlagen.",
               "mittel": "Wiedervorlage setzen und zum genannten Zeitpunkt wieder melden.",
               "kein_interesse": "Nicht weiter nachfassen.",
               "abmeldung": "Adresse ist gesperrt. Keine weiteren Nachrichten.",
               "automatisch": ""}


def classify_reply(subject, text, headers=None):
    """Ordnet eine Antwort nach Stichworten ein. Gibt {interesse, zusammenfassung, grund, naechster_schritt} zurück.
    Reihenfolge: Abwesenheit → Abmeldung → Absage → später → Interesse → sonst „mittel“."""
    headers = {k.lower(): (v or "") for k, v in (headers or {}).items()}
    subj, body = (subject or "").lower(), (text or "").lower()
    kind, grund = None, ""
    auto_header = headers.get("auto-submitted", "no").lower() not in ("", "no") or "x-autoreply" in headers \
        or "x-autorespond" in headers or headers.get("precedence", "").lower() in ("auto_reply", "bulk", "junk")
    if auto_header or AUTO_SUBJECT.search(subj) or AUTO_BODY.search(body):
        kind = "automatisch"
    else:
        for key, rx in REPLY_RULES:
            m = rx.search(body)
            if m:
                kind, grund = key, f"Stichwort „{m.group(0).strip()}“"
                break
    kind = kind or "mittel"
    if kind == "mittel" and not grund:
        grund = "Keine eindeutigen Stichworte, bitte selbst lesen."
    short = _compact(text)
    return {"interesse": kind, "zusammenfassung": short[:220] + ("…" if len(short) > 220 else ""), "grund": grund,
            "naechster_schritt": REPLY_STEPS[kind]}
