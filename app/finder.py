"""Firmen finden (OpenStreetMap) und ihre Webseite lesen."""
import ipaddress
import json
import math
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser

USER_AGENT = "YlvaLabsLeadBot/1.0 (+https://ylvalabs.de; info@ylvalabs.de)"

# Welche OpenStreetMap-Einträge als Betrieb in Frage kommen, und in welche Branche sie fallen.
# Nur Einträge mit Webseite oder E-Mail werden übernommen, sonst kann die KI nichts recherchieren.
QUERIES = [
    ('["craft"]', "handwerk"),
    ('["office"~"^(company|accountant|tax_advisor|lawyer|notary|estate_agent|insurance|architect|engineer|'
     'logistics|consulting|surveyor|property_management|employment_agency)$"]', "dienstleistung"),
    ('["amenity"~"^(nursing_home|social_facility|dentist|doctors|clinic|veterinary)$"]', "pflege"),
    ('["healthcare"~"^(nursing_home|home_care|physiotherapist|doctor|dentist|clinic|rehabilitation|'
     'occupational_therapist|speech_therapist|care)$"]', "pflege"),
    ('["social_facility"]', "pflege"),
    ('["place"="farm"]', "landwirtschaft"),
    ('["landuse"="farmyard"]["name"]', "landwirtschaft"),
    ('["shop"~"^(farm|agrarian)$"]', "landwirtschaft"),
    ('["shop"~"^(car_repair|car|furniture|kitchen|bathroom_furnishing|doityourself|trade|hardware|'
     'electronics|bicycle|optician|hearing_aids|boat|tyres|motorcycle)$"]', "handel"),
    ('["industrial"]', "industrie"),
    ('["man_made"="works"]', "industrie"),
    ('["tourism"~"^(hotel|camp_site|guest_house|caravan_site)$"]', "gastgewerbe"),
    ('["amenity"="driving_school"]', "dienstleistung"),
]

LABELS = {
    "carpenter": "Tischlerei", "joiner": "Tischlerei", "electrician": "Elektro", "plumber": "Sanitär",
    "hvac": "Heizung/Lüftung", "heating_engineer": "Heizungsbau", "roofer": "Dachdeckerei",
    "painter": "Malerbetrieb", "metal_construction": "Metallbau", "builder": "Bauunternehmen",
    "stonemason": "Steinmetz", "tiler": "Fliesenleger", "glaziery": "Glaserei", "plasterer": "Stuckateur",
    "gardener": "Garten- und Landschaftsbau", "landscaper": "Garten- und Landschaftsbau",
    "boatbuilder": "Bootsbau", "shipbuilder": "Werft", "car_repair": "Kfz-Werkstatt", "baker": "Bäckerei",
    "butcher": "Fleischerei", "brewery": "Brauerei", "hairdresser": "Friseur", "optician": "Optiker",
    "agricultural_engines": "Landmaschinen", "sawmill": "Sägewerk", "scaffolder": "Gerüstbau",
    "floorer": "Bodenleger", "insulation": "Dämmung", "window_construction": "Fensterbau",
    "chimney_sweeper": "Schornsteinfeger", "locksmith": "Schlüsseldienst", "upholsterer": "Polsterei",
    "tax_advisor": "Steuerberatung", "accountant": "Buchhaltung", "lawyer": "Kanzlei", "notary": "Notariat",
    "estate_agent": "Immobilien", "insurance": "Versicherung", "architect": "Architekturbüro",
    "engineer": "Ingenieurbüro", "logistics": "Logistik", "consulting": "Beratung", "company": "Unternehmen",
    "surveyor": "Vermessung", "property_management": "Hausverwaltung", "employment_agency": "Personalvermittlung",
    "nursing_home": "Pflegeheim", "home_care": "Ambulante Pflege", "social_facility": "Soziale Einrichtung",
    "dentist": "Zahnarztpraxis", "doctors": "Arztpraxis", "doctor": "Arztpraxis", "clinic": "Klinik",
    "veterinary": "Tierarztpraxis", "physiotherapist": "Physiotherapie", "rehabilitation": "Reha",
    "occupational_therapist": "Ergotherapie", "speech_therapist": "Logopädie", "care": "Pflege",
    "farm": "Hof", "farmyard": "Hof", "agrarian": "Agrarhandel", "car": "Autohaus",
    "furniture": "Möbel", "kitchen": "Küchenstudio", "bathroom_furnishing": "Bäder",
    "doityourself": "Baumarkt", "trade": "Fachhandel", "hardware": "Eisenwaren", "electronics": "Elektronik",
    "bicycle": "Fahrräder", "hearing_aids": "Hörakustik", "boat": "Bootshandel", "tyres": "Reifenhandel",
    "motorcycle": "Motorräder", "works": "Produktion", "hotel": "Hotel", "camp_site": "Campingplatz",
    "guest_house": "Pension", "caravan_site": "Wohnmobilplatz", "driving_school": "Fahrschule",
}


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def overpass_query(lat, lon, radius_km):
    around = f"(around:{int(radius_km * 1000)},{lat},{lon})"
    parts = "".join(f"nwr{around}{sel}[\"name\"];" for sel, _ in QUERIES)
    return f"[out:json][timeout:180];({parts});out center tags;"


def classify(tags):
    """OSM-Tags -> (branche, kategorie, lesbares Label)."""
    for key in ("craft", "healthcare", "office", "social_facility", "amenity", "shop", "industrial",
                "man_made", "tourism", "place", "landuse"):
        value = tags.get(key)
        if not value:
            continue
        for sel, branche in QUERIES:
            if f'["{key}"' not in sel:
                continue
            m = re.search(r'"~"\^\(([^)]*)\)\$"', sel) or re.search(r'"="([^"]+)"', sel)
            if m is None or value in m.group(1).split("|"):
                label = LABELS.get(value, value.replace("_", " ").capitalize())
                if key == "industrial" and value not in LABELS:
                    label = "Industrie: " + value.replace("_", " ")
                return branche, f"{key}={value}", label
    return "sonstige", "", "Betrieb"


def normalize_url(url):
    url = (url or "").strip().split(";")[0].strip()
    if not url:
        return ""
    if not re.match(r"(?i)^https?://", url):
        url = "https://" + url
    return url


SOCIAL_HOSTS = ("facebook.com", "instagram.com", "linkedin.com", "xing.com", "google.com", "business.site",
                "wixsite.com", "jimdosite.com", "tiktok.com", "youtube.com")
# Offensichtlich zu klein oder nicht vor Ort entscheidend. Ohne KI, also kostenlos.
SMALL_CATEGORIES = {"craft=hairdresser", "craft=beautician", "craft=tailor", "craft=key_cutter", "craft=shoemaker",
                    "craft=photographer", "amenity=driving_school"}


def rough_filter(tags, website, host):
    """Kostenloser Grobfilter vor jeder KI. Gibt einen Grund zurück, wenn der Betrieb rausfällt."""
    if tags.get("brand") or tags.get("brand:wikidata") or tags.get("operator:wikidata"):
        return "Filiale einer Kette (laut Kartendaten)"
    if not website:
        return "Keine Webseite, die KI hätte nichts zu lesen"
    if any(host == h or host.endswith("." + h) for h in SOCIAL_HOSTS):
        return "Nur eine Social-Media- oder Baukasten-Seite, vermutlich sehr klein"
    for key, value in tags.items():
        if f"{key}={value}" in SMALL_CATEGORIES:
            return f"Branche meist ohne nennenswerte Verwaltung ({LABELS.get(value, value)})"
    return None


def parse_elements(elements, lat, lon, radius_km):
    """Overpass-Antwort -> Liste von Firmen (nur mit Webseite oder E-Mail)."""
    found, seen = [], set()
    for el in elements:
        tags = el.get("tags") or {}
        name = (tags.get("name") or "").strip()
        website = normalize_url(tags.get("website") or tags.get("contact:website") or tags.get("url"))
        email = (tags.get("email") or tags.get("contact:email") or "").split(";")[0].strip()
        if not name or not (website or email):
            continue
        plat = el.get("lat") or (el.get("center") or {}).get("lat")
        plon = el.get("lon") or (el.get("center") or {}).get("lon")
        if plat is None or plon is None:
            continue
        dist = haversine_km(lat, lon, plat, plon)
        if dist > radius_km:
            continue
        host = urllib.parse.urlparse(website).netloc.lower().removeprefix("www.") if website else ""
        dedupe = host or name.lower()
        if dedupe in seen:  # Filialen/mehrere Einträge derselben Firma nur einmal
            continue
        seen.add(dedupe)
        branche, category, label = classify(tags)
        filter_reason = rough_filter(tags, website, host)
        street = " ".join(x for x in (tags.get("addr:street"), tags.get("addr:housenumber")) if x)
        city = " ".join(x for x in (tags.get("addr:postcode"), tags.get("addr:city")) if x)
        found.append({
            "source_id": f"osm:{el.get('type')}/{el.get('id')}", "name": name, "branche": branche,
            "category": category, "category_label": label,
            "address": ", ".join(x for x in (street, city) if x), "city": tags.get("addr:city", ""),
            "lat": plat, "lon": plon, "distance_km": round(dist, 1), "website": website, "email": email,
            "phone": (tags.get("phone") or tags.get("contact:phone") or "").split(";")[0].strip(),
            "filter_reason": filter_reason,
        })
    return found


def search_osm(lat, lon, radius_km, overpass_url):
    data = urllib.parse.urlencode({"data": overpass_query(lat, lon, radius_km)}).encode()
    req = urllib.request.Request(overpass_url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=240) as resp:
        payload = json.load(resp)
    return parse_elements(payload.get("elements", []), lat, lon, radius_km)

# ---------------------------------------------------------------- Webseite lesen


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
             "header", "footer", "td", "th", "nav", "ul", "ol", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.title, self._skip, self._in_title = [], [], "", 0, False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)

    def text(self):
        text = re.sub(r"[ \t\r\f\v]+", " ", "".join(self.parts))
        return re.sub(r"\n\s*\n+", "\n", text).strip()


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+(?:@|\s?\[at\]\s?|\s?\(at\)\s?)[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
SUBPAGE_HINTS = ("impressum", "kontakt", "contact", "ueber-uns", "uber-uns", "about", "team",
                 "leistungen", "unternehmen", "karriere", "jobs", "stellen")


def extract_emails(html):
    emails = set()
    for m in re.finditer(r"mailto:([^\"'?>\s]+)", html, re.I):
        emails.add(urllib.parse.unquote(m.group(1)))
    for m in _EMAIL_RE.finditer(html):
        emails.add(re.sub(r"\s?[\[(]at[\])]\s?", "@", m.group(0)))
    return sorted(e.lower().strip(".") for e in emails
                  if not re.search(r"\.(png|jpe?g|gif|webp|svg)$", e, re.I) and "example" not in e.lower())


def _robots_ok(url, cache):
    parts = urllib.parse.urlparse(url)
    base = f"{parts.scheme}://{parts.netloc}"
    if base not in cache:
        rp = urllib.robotparser.RobotFileParser()
        try:
            req = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": USER_AGENT})
            with _opener.open(req, timeout=10) as resp:
                rp.parse(resp.read(200_000).decode("utf-8", "ignore").splitlines())
        except Exception:
            rp.parse([])  # keine robots.txt: alles erlaubt
        cache[base] = rp
    return cache[base].can_fetch(USER_AGENT, url)


ALLOW_PRIVATE = False  # nur für Tests


def check_public(url):
    """Webseiten-Adressen kommen aus öffentlichen Kartendaten. Damit darüber niemand Geräte im
    Büronetz (Router, Proxmox …) abfragen lässt, sind nur öffentliche Adressen erlaubt."""
    parts = urllib.parse.urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"ungültige Adresse: {url}")
    if ALLOW_PRIVATE:
        return
    for info in socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)):
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise PermissionError(f"{parts.hostname} zeigt auf eine interne Adresse")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SafeRedirect)


def fetch_page(url, robots_cache):
    check_public(url)
    if not _robots_ok(url, robots_cache):
        raise PermissionError("robots.txt verbietet das Lesen")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.5"})
    with _opener.open(req, timeout=20) as resp:
        ctype = resp.headers.get("Content-Type", "")
        if "html" not in ctype and "text" not in ctype:
            raise ValueError(f"keine Webseite ({ctype})")
        raw = resp.read(2_000_000)
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.geturl(), raw.decode(charset, "ignore")


def research_website(url, max_pages=4, chars_per_page=6000):
    """Startseite plus Impressum/Kontakt/Über uns lesen. Gibt Texte, E-Mails und Fehler zurück."""
    robots, pages, emails, errors = {}, [], set(), []
    try:
        final_url, html = fetch_page(url, robots)
    except Exception as exc:
        return {"pages": [], "emails": [], "errors": [f"{url}: {exc}"]}
    queue, done = [(final_url, html)], {final_url}
    host = urllib.parse.urlparse(final_url).netloc
    while queue and len(pages) < max_pages:
        page_url, html = queue.pop(0)
        parser = _TextExtractor()
        try:
            parser.feed(html)
        except Exception:
            pass
        pages.append({"url": page_url, "title": parser.title.strip()[:200], "text": parser.text()[:chars_per_page]})
        emails.update(extract_emails(html))
        if len(pages) == 1:  # Unterseiten nur von der Startseite aus
            candidates = []
            for href in parser.links:
                link = urllib.parse.urljoin(page_url, href).split("#")[0]
                low = link.lower()
                if urllib.parse.urlparse(link).netloc == host and link not in done \
                        and any(h in low for h in SUBPAGE_HINTS):
                    rank = next(i for i, h in enumerate(SUBPAGE_HINTS) if h in low)
                    candidates.append((rank, link))
                    done.add(link)
            for _, link in sorted(candidates)[:max_pages - 1]:
                try:
                    queue.append(fetch_page(link, robots))
                except Exception as exc:
                    errors.append(f"{link}: {exc}")
    return {"pages": pages, "emails": sorted(emails), "errors": errors}
