"""Baut aus den Formularfeldern die HTML-E-Mail (und eine Textversion)."""
import mimetypes
import re
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, getaddresses
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from . import pixel

ROOT = Path(__file__).resolve().parent.parent

TEMPLATES = {
    "newsletter": {
        "name": "Newsletter",
        "description": "Große Headline, Pixel-Band oder Titelbild über die volle Breite.",
        "file": "newsletter.html",
    },
    "brief": {
        "name": "Kurz-Mail",
        "description": "Ruhiger Brief mit kleiner Headline, Grafik im Textfluss.",
        "file": "brief.html",
    },
}

env = Environment(
    loader=FileSystemLoader(ROOT / "email_templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

LINK_STYLE = "color:#1d2126;text-decoration:underline"
_MD_LINK = re.compile(r"\[([^\]\n]+)\]\(((?:https?://|mailto:)[^)\s]+)\)")
_BARE_URL = re.compile(r"(?<![\w/\"'=])((?:https?://)[^\s<]+[^\s<.,;:!?)\]])")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_EMAIL = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


def personalize(text, rcpt):
    """Ersetzt {vorname}, {name}, {email}. Fehlt der Name, verschwindet der Platzhalter sauber."""
    if not text:
        return ""
    first = rcpt.get("first", "")
    if not first:
        text = re.sub(r"[  ]\{vorname\}", "", text)
    if not rcpt.get("name"):
        text = re.sub(r"[  ]\{name\}", "", text)
    return (text.replace("{vorname}", first)
                .replace("{name}", rcpt.get("name", ""))
                .replace("{email}", rcpt.get("email", "")))


def inline_html(text):
    """Eine Textzeile/-absatz -> sicheres HTML mit **fett**, [Link](url) und nackten URLs."""
    links = []

    def stash(label, url):
        links.append(f'<a href="{escape(url)}" target="_blank" style="{LINK_STYLE}">{label}</a>')
        return f"\x00{len(links) - 1}\x00"

    text = _MD_LINK.sub(lambda m: stash(escape(m.group(1)), m.group(2)), text)
    text = _BARE_URL.sub(lambda m: stash(escape(m.group(1)), m.group(1)), text)
    html = str(escape(text))
    html = _BOLD.sub(r'<strong style="color:#1d2126">\1</strong>', html)
    html = html.replace("\n", "<br>")
    html = re.sub(r"\x00(\d+)\x00", lambda m: links[int(m.group(1))], html)
    return Markup(html)


def paragraphs(text):
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    return [inline_html(p.strip()) for p in re.split(r"\n\s*\n", text) if p.strip()]


def plain(text):
    text = _MD_LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", text or "")
    return _BOLD.sub(r"\1", text).strip()


def parse_recipients(raw):
    """'Anna Muster <anna@x.de>, bob@y.de' (Komma, Semikolon oder Zeilen) -> Liste + Fehler."""
    raw = (raw or "").replace(";", ",").replace("\n", ",")
    result, errors, seen = [], [], set()
    for name, addr in getaddresses([raw]):
        addr = addr.strip()
        name = name.strip().strip('"')
        if not addr and not name:
            continue
        if not _EMAIL.match(addr):
            errors.append(name or addr)
            continue
        if addr.lower() in seen:
            continue
        seen.add(addr.lower())
        result.append({"name": name, "first": name.split()[0] if name else "", "email": addr})
    return result, errors


def build_graphic(form, image_src=None):
    kind = form.get("graphic", "band_original")
    if kind == "band_original":
        return {"kind": kind, "html": Markup(pixel.original_band())}
    if kind == "band":
        seed = max(1, min(50, int(form.get("band_seed") or 3)))
        return {"kind": kind, "html": Markup(pixel.band_table(seed))}
    if kind == "icon" and form.get("icon") in pixel.ICONS:
        return {"kind": kind, "html": Markup(pixel.icon_table(form["icon"]))}
    if kind == "image" and image_src:
        return {"kind": kind, "src": image_src, "alt": form.get("image_alt", "")}
    return {"kind": "none"}


def info_rows(form):
    labels = form.get("info_label") or []
    values = form.get("info_value") or []
    return [{"label": l.strip(), "value": inline_html(v.strip())}
            for l, v in zip(labels, values) if l.strip() or v.strip()]


def render(form, rcpt, settings, image_src=None):
    """Rendert HTML + Text für einen Empfänger."""
    tpl = TEMPLATES.get(form.get("template"), TEMPLATES["newsletter"])
    p = lambda key: personalize(form.get(key, ""), rcpt)
    cta_url = form.get("cta_url", "").strip()
    ctx = {
        "subject": p("subject"),
        "preheader": p("preheader"),
        "headline": p("headline"),
        "greeting": inline_html(p("greeting")) if form.get("greeting", "").strip() else "",
        "intro": paragraphs(p("body")),
        "more": paragraphs(p("body2")),
        "graphic": build_graphic(form, image_src),
        "info_rows": info_rows({"info_label": form.get("info_label"),
                                "info_value": [personalize(v, rcpt) for v in form.get("info_value") or []]}),
        "cta_text": p("cta_text"),
        "cta_url": cta_url if re.match(r"(?i)^(https?://|mailto:)", cta_url) else "",
        "closing": inline_html(p("closing")) if form.get("closing", "").strip() else "",
        "sender_name": form.get("sender_name", "").strip(),
        "org_name": settings["ORG_NAME"],
        "footer": form.get("footer", "").strip(),
        "logo_mark": Markup(pixel.logo_mark()),
    }
    html = env.get_template(tpl["file"]).render(**ctx)

    parts = [ctx["headline"], plain(p("greeting")), plain(p("body")), plain(p("body2"))]
    for label, value in zip(form.get("info_label") or [], form.get("info_value") or []):
        if label.strip() or value.strip():
            parts.append(f"{label.strip().upper()}: {plain(personalize(value, rcpt))}")
    if ctx["cta_text"] and ctx["cta_url"]:
        parts.append(f"{ctx['cta_text']}: {ctx['cta_url']}")
    parts.append(plain(p("closing")))
    parts.append("\n".join(x for x in (ctx["sender_name"], settings["ORG_NAME"]) if x))
    if ctx["footer"]:
        parts.append("--\n" + ctx["footer"])
    text = "\n\n".join(x for x in parts if x) + "\n"
    return html, text, ctx["subject"]


def build_message(form, rcpt, settings, sender, image_path=None):
    cid = None
    if form.get("graphic") == "image" and image_path:
        cid = make_msgid(domain="ylvalabs.mail")[1:-1]
    html, text, subject = render(form, rcpt, settings, image_src=f"cid:{cid}" if cid else None)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((form.get("sender_name", "").strip() or settings["ORG_NAME"], sender))
    msg["To"] = formataddr((rcpt["name"], rcpt["email"]))
    if form.get("reply_to", "").strip():
        msg["Reply-To"] = form["reply_to"].strip()
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1] if "@" in sender else None)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    if cid:
        ctype = mimetypes.guess_type(str(image_path))[0] or "image/png"
        maintype, subtype = ctype.split("/", 1)
        html_part = msg.get_payload()[1]
        html_part.add_related(Path(image_path).read_bytes(), maintype, subtype, cid=f"<{cid}>",
                              filename=Path(image_path).name, disposition="inline")
    return msg
