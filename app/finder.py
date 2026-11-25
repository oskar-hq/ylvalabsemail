"""Webseiten sicher lesen (nur öffentliche Adressen, robots.txt) und Links auf soziale Netzwerke erkennen.
Die Suche über OpenStreetMap gibt es seit Stufe 13 nicht mehr (Leads kommen von den Agents)."""
import http.client
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser


USER_AGENT = "YlvaLabsLeadBot/1.0 (+https://ylvalabs.de; info@ylvalabs.de)"


def normalize_url(url):
    url = (url or "").strip().split(";")[0].strip()
    if not url:
        return ""
    if not re.match(r"(?i)^https?://", url):
        url = "https://" + url
    return url


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


# ---------------------------------------------------------------- Social Media
# Viele Betriebe, vor allem Agenturen, zeigen ihre Arbeit eher auf Instagram oder LinkedIn als auf der Webseite.
# Gelesen werden diese Seiten nicht (sie verlangen eine Anmeldung); die App merkt sich nur die Links zum Öffnen.
SOCIAL = [
    ("instagram", "Instagram", r"^https?://(?:www\.)?instagram\.com/(?!p/|reel/|reels/|explore/|stories/|accounts/|share)"
                               r"([A-Za-z0-9_.]{2,30})/?$"),
    ("linkedin", "LinkedIn", r"^https?://(?:[a-z]{2,3}\.)?linkedin\.com/(company|in|school)/([^/?#]{2,100})/?$"),
    ("facebook", "Facebook", r"^https?://(?:www\.|m\.|de-de\.)?facebook\.com/(?!sharer|share|plugins|tr|dialog|"
                             r"login|groups/?$)([^?#]{2,100}?)/?$"),
    ("xing", "Xing", r"^https?://(?:www\.)?xing\.com/(pages|companies|profile)/([^/?#]{2,100})/?$"),
    ("tiktok", "TikTok", r"^https?://(?:www\.)?tiktok\.com/@([A-Za-z0-9_.]{2,30})/?$"),
    ("youtube", "YouTube", r"^https?://(?:www\.)?youtube\.com/(@[^/?#]{2,100}|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+)/?$"),
]
SOCIAL_LABELS = {k: label for k, label, _ in SOCIAL}


def social_url(network, value):
    """Ein Link als aufgeräumter Profil-Link des Netzwerks, sonst ''."""
    value = (value or "").strip()
    if not value.startswith("http"):
        return ""
    url = value.split("?")[0].split("#")[0]
    pattern = dict((k, p) for k, _, p in SOCIAL)[network]
    return url if re.match(pattern, url, re.I) else ""


def social_links(urls):
    """Aus allen Links einer Webseite: je Netzwerk der erste Profil-Link (keine einzelnen Beiträge, kein Teilen)."""
    found = {}
    for raw in urls or []:
        url = (raw or "").split("?")[0].split("#")[0]
        for key, _, pattern in SOCIAL:
            if key not in found and re.match(pattern, url, re.I):
                found[key] = url
    return found


