"""Firmen finden (OpenStreetMap) und ihre Webseite lesen."""
import http.client
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

from . import niches

USER_AGENT = "YlvaLabsLeadBot/1.0 (+https://ylvalabs.de; info@ylvalabs.de)"

# Welche OpenStreetMap-Einträge gesucht werden, steht in niches.py (ihr wählt die Nischen in der App aus).

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
    "cabinet_maker": "Tischlerei", "shipyard": "Werft", "moving_company": "Spedition/Umzüge",
    "assisted_living": "Betreutes Wohnen", "day_care": "Tagespflege",
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


def overpass_query(lat, lon, radius_km, region="", keys=None):
    """Suche in einem Bundesland (ISO-Code, z. B. DE-SH) oder, ohne Region, im Umkreis um einen Punkt.
    `keys`: die gewählten Nischen (ohne Angabe die, die zu Ylva Labs passen)."""
    if region:
        head, where = f'area["ISO3166-2"="{region}"]->.gebiet;', "(area.gebiet)"
    else:
        head, where = "", f"(around:{int(radius_km * 1000)},{lat},{lon})"
    parts = "".join(f"nwr{where}{sel}[\"name\"];" for sel in niches.selectors(keys or niches.DEFAULT_KEYS))
    return f"[out:json][timeout:300];{head}({parts});out center tags;"


def classify(tags, keys=None):
    """OSM-Tags -> (branche, kategorie, lesbares Label, nische)."""
    niche, category = niches.match(tags, keys or niches.DEFAULT_KEYS)
    if niche is None:
        return "sonstige", category, "Betrieb", None
    value = category.split("=", 1)[1] if category else ""
    label = LABELS.get(value) or niches.NICHES[niche]["label"]
    if category.startswith("industrial=") and value not in LABELS:
        label = "Industrie: " + value.replace("_", " ")
    return niches.NICHES[niche]["branche"], category, label, niche


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


def rough_filter(tags, website, host, category=""):
    """Kostenloser Grobfilter vor jeder KI. Gibt einen Grund zurück, wenn der Betrieb rausfällt.
    `category`: die gewählte Nische, nach der gesucht wurde; die gilt nie als „zu klein“."""
    if tags.get("brand") or tags.get("brand:wikidata") or tags.get("operator:wikidata"):
        return "Filiale einer Kette (laut Kartendaten)"
    if not website:
        return "Keine Webseite, die KI hätte nichts zu lesen"
    if any(host == h or host.endswith("." + h) for h in SOCIAL_HOSTS):
        return "Nur eine Social-Media- oder Baukasten-Seite, vermutlich sehr klein"
    for key, value in tags.items():
        if f"{key}={value}" in SMALL_CATEGORIES and f"{key}={value}" != category:
            return f"Branche meist ohne nennenswerte Verwaltung ({LABELS.get(value, value)})"
    return None


def parse_elements(elements, lat, lon, radius_km=None, keys=None):
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
        if radius_km and dist > radius_km:
            continue
        host = urllib.parse.urlparse(website).netloc.lower().removeprefix("www.") if website else ""
        dedupe = host or name.lower()
        if dedupe in seen:  # Filialen/mehrere Einträge derselben Firma nur einmal
            continue
        seen.add(dedupe)
        branche, category, label, niche = classify(tags, keys)
        filter_reason = rough_filter(tags, website, host, category)
        street = " ".join(x for x in (tags.get("addr:street"), tags.get("addr:housenumber")) if x)
        city = " ".join(x for x in (tags.get("addr:postcode"), tags.get("addr:city")) if x)
        found.append({
            "source_id": f"osm:{el.get('type')}/{el.get('id')}", "name": name, "branche": branche,
            "category": category, "category_label": label, "niche": niche,
            "address": ", ".join(x for x in (street, city) if x), "city": tags.get("addr:city", ""),
            "lat": plat, "lon": plon, "distance_km": round(dist, 1), "website": website, "email": email,
            "phone": (tags.get("phone") or tags.get("contact:phone") or "").split(";")[0].strip(),
            "filter_reason": filter_reason,
        })
    return found


def search_osm(lat, lon, radius_km, overpass_url, region="", keys=None):
    """Mit Region (z. B. DE-SH) zählt nur das Bundesland, Entfernung dient dann nur der Anzeige."""
    data = urllib.parse.urlencode({"data": overpass_query(lat, lon, radius_km, region, keys)}).encode()
    req = urllib.request.Request(overpass_url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=330) as resp:
        payload = json.load(resp)
    return parse_elements(payload.get("elements", []), lat, lon, None if region else radius_km, keys)

# ---------------------------------------------------------------- Webseite lesen


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
             "header", "footer", "td", "th", "nav", "ul", "ol", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.title, self._skip, self._in_title = [], [], "", 0, False
        self.description = ""

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "meta" and not self.description:  # Kurzbeschreibung der Seite (kostenlos, steht oft im Kopf)
            a = dict(attrs)
            if (a.get("name") or a.get("property") or "").lower() in ("description", "og:description"):
                self.description = re.sub(r"\s+", " ", a.get("content") or "").strip()[:400]
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


def _check_peer(sock):
    """Nach dem Verbinden: Ist die Gegenstelle wirklich öffentlich? check_public allein reicht nicht, weil ein
    Name beim zweiten Nachschlagen (DNS-Rebinding) plötzlich auf ein Gerät im eigenen Netz zeigen kann."""
    if ALLOW_PRIVATE:
        return
    ip = sock.getpeername()[0]
    if not ipaddress.ip_address(ip).is_global:
        sock.close()
        raise PermissionError(f"Verbindung zu interner Adresse {ip} abgelehnt")


class _PublicHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        super().connect()
        if not self._tunnel_host:  # über einen Proxy ist die Gegenstelle der Proxy selbst
            _check_peer(self.sock)


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        super().connect()
        if not self._tunnel_host:  # über einen Proxy ist die Gegenstelle der Proxy selbst
            _check_peer(self.sock)


class _PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PublicHTTPConnection, req)


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PublicHTTPSConnection, req, context=self._context)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SafeRedirect, _PublicHTTPHandler, _PublicHTTPSHandler)


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
    """Startseite plus Impressum/Kontakt/Über uns lesen. Gibt Texte, Links, E-Mails und Fehler zurück."""
    robots, pages, emails, errors, links = {}, [], set(), [], []
    try:
        final_url, html = fetch_page(url, robots)
    except Exception as exc:
        return {"pages": [], "emails": [], "links": [], "errors": [f"{url}: {exc}"]}
    queue, done = [(final_url, html)], {final_url}
    host = urllib.parse.urlparse(final_url).netloc
    while queue and len(pages) < max_pages:
        page_url, html = queue.pop(0)
        parser = _TextExtractor()
        try:
            parser.feed(html)
        except Exception:
            pass
        pages.append({"url": page_url, "title": parser.title.strip()[:200], "text": parser.text()[:chars_per_page],
                      "description": parser.description})
        emails.update(extract_emails(html))
        links.extend(urllib.parse.urljoin(page_url, h) for h in parser.links[:300])
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
    return {"pages": pages, "emails": sorted(emails), "links": list(dict.fromkeys(links))[:600], "errors": errors}
