"""Ylva Labs Mailer – Web-Oberfläche zum Versenden gestalteter HTML-E-Mails."""
import hashlib
import imaplib
import json
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import threading
import time
import uuid
from datetime import datetime
from email.utils import parseaddr
from functools import wraps
from pathlib import Path

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)

from . import emailbuild, pixel

ROOT = Path(__file__).resolve().parent.parent


def env(key, default=""):
    return os.environ.get(key, default).strip()


# Einstellungen, die in der App geändert wurden, gelten vor der .env (siehe app/config_store.py)
from . import config_store  # noqa: E402

_DATA_DIR = Path(env("DATA_DIR", str(ROOT / "data")))
_DATA_DIR.mkdir(parents=True, exist_ok=True)
# Eine in der App hochgeladene Sicherung wird hier eingesetzt, bevor irgendetwas die Datenbank öffnet
from . import backups  # noqa: E402

backups.apply_pending(_DATA_DIR)
config_store.apply_overrides(_DATA_DIR)


SETTINGS = {
    "SMTP_HOST": env("SMTP_HOST"),
    "SMTP_PORT": int(env("SMTP_PORT", "587")),
    "SMTP_SECURITY": env("SMTP_SECURITY", "starttls").lower(),  # starttls | ssl | none
    "IMAP_HOST": env("IMAP_HOST"),
    "IMAP_PORT": int(env("IMAP_PORT", "993")),
    "IMAP_SENT_FOLDER": env("IMAP_SENT_FOLDER"),
    "FROM_ADDRESS": env("FROM_ADDRESS"),
    "ALLOWED_USERS": [u.lower() for u in re.split(r"[,\s]+", env("ALLOWED_USERS")) if u],
    "ALLOWED_DOMAINS": [d.lower().lstrip("@") for d in re.split(r"[,\s]+", env("ALLOWED_DOMAINS")) if d],
    "TRUST_PROXY": env("TRUST_PROXY", "none").lower(),  # none | cloudflare | proxy
    "ORG_NAME": env("ORG_NAME", "Ylva Labs"),
    "DEFAULT_SENDER_NAME": env("DEFAULT_SENDER_NAME"),
    "DEFAULT_FOOTER": env("DEFAULT_FOOTER", "Ylva Labs · Schleswig-Holstein"),
    "DEFAULT_CLOSING": env("DEFAULT_CLOSING", "Ihr habt ein Problem, das nervt, oder eine Idee, die raus will? Antwortet einfach auf diese Mail."),
    "SEND_DELAY": float(env("SEND_DELAY", "1")),
    "MAX_RECIPIENTS": int(env("MAX_RECIPIENTS", "200")),
    "SESSION_HOURS": float(env("SESSION_HOURS", "12")),
    "DEMO_MODE": env("DEMO_MODE", "false").lower() in ("1", "true", "yes"),
    "DATA_DIR": _DATA_DIR,
    # Wer Einstellungen ändern und Updates einspielen darf (leer = alle, die sich anmelden dürfen)
    "ADMIN_USERS": [u.lower() for u in re.split(r"[,\s]+", env("ADMIN_USERS")) if u],
    # Austauschordner mit dem Update-Dienst auf dem Server (deploy/install-updater.sh), leer = keine Updates aus der App
    "CONTROL_DIR": Path(env("CONTROL_DIR")) if env("CONTROL_DIR") else None,
    # Akquise (Bereich „Leads“) und CRM
    "LEADS_WORKER": env("LEADS_WORKER", "true").lower() in ("1", "true", "yes"),
    # Suchgebiet: Bundesland als ISO-Code (DE-SH = Schleswig-Holstein). Leer = Umkreis um LEADS_CENTER.
    "LEADS_REGION": env("LEADS_REGION", "DE-SH"),
    "LEADS_CENTER": tuple(float(x) for x in env("LEADS_CENTER", "54.6614,9.9311").split(",")),  # Kappeln, für die Entfernung
    "LEADS_RADIUS_KM": float(env("LEADS_RADIUS_KM", "50")),
    # Vorschläge für den Start-Dialog eines Laufs
    "LEADS_RUN_CHECK": int(env("LEADS_RUN_CHECK", "150")),
    "LEADS_RUN_TOP": int(env("LEADS_RUN_TOP", "25")),  # so viele der besten pro Lauf ins CRM
    "LEADS_RUN_DRAFTS": int(env("LEADS_RUN_DRAFTS", "0")),
    "LEADS_RUN_BUDGET_EUR": float(env("LEADS_RUN_BUDGET_EUR", "2")),
    "LEADS_MONTHLY_BUDGET_EUR": float(env("LEADS_MONTHLY_BUDGET_EUR", "20")),  # 0 = keine Grenze
    "LEADS_RULE_MIN": int(env("LEADS_RULE_MIN", "50")),  # ab dieser Punktzahl der Regel-Bewertung kommt er in die Pipeline
    "LEADS_FOLLOWUP_DAYS": int(env("LEADS_FOLLOWUP_DAYS", "7")),  # nach so vielen Tagen ohne Antwort nachfassen, 0 = nie
    "LEADS_SENDER_NAME": env("LEADS_SENDER_NAME", "Oskar Jacobsen"),
    "OUTREACH_USER": env("OUTREACH_USER"),
    "OUTREACH_PASSWORD": env("OUTREACH_PASSWORD"),
    "OUTREACH_EMAIL": env("OUTREACH_EMAIL", "false").lower() in ("1", "true", "yes"),
    "INBOX_MINUTES": float(env("INBOX_MINUTES", "10")),
    "NOTIFY_EMAILS": [a for a in re.split(r"[,\s]+", env("NOTIFY_EMAILS")) if a],
    "BASE_URL": env("BASE_URL"),
    "OVERPASS_URL": env("OVERPASS_URL", "https://overpass-api.de/api/interpreter"),
}
UPLOAD_DIR = SETTINGS["DATA_DIR"] / "uploads"
DB_PATH = SETTINGS["DATA_DIR"] / "mailer.db"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _secret_key():
    if env("SECRET_KEY"):
        return env("SECRET_KEY")
    path = SETTINGS["DATA_DIR"] / ".secret_key"
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip()


app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
app.config.update(
    SECRET_KEY=_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=env("COOKIE_SECURE", "false").lower() in ("1", "true", "yes"),
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
)

# ---------------------------------------------------------------- Anmeldung
# Das Postfach-Passwort bleibt nur im Arbeitsspeicher des Servers, das Cookie
# enthält lediglich eine zufällige Sitzungs-ID. Nach einem Neustart meldet man
# sich einfach neu an.
_sessions = {}
_sessions_lock = threading.Lock()
_failed_logins = {}  # Schlüssel: "ip:<adresse>" oder "user:<login>"
LOCK_RULES = {"ip": (5, 600), "user": (10, 900)}  # max. Fehlversuche, Zeitfenster in Sekunden


def client_ip():
    """Echte Besucher-IP. Weitergeleitete Header zählen nur hinter einem bekannten Proxy,
    sonst könnte man die Sperre mit einem gefälschten Header umgehen."""
    if SETTINGS["TRUST_PROXY"] == "cloudflare" and request.headers.get("CF-Connecting-IP"):
        return request.headers["CF-Connecting-IP"].strip()
    if SETTINGS["TRUST_PROXY"] in ("cloudflare", "proxy") and request.headers.get("X-Forwarded-For"):
        return request.headers["X-Forwarded-For"].split(",")[-1].strip()
    return request.remote_addr or ""


def recent_fails(key):
    window = LOCK_RULES[key.split(":", 1)[0]][1]
    with _sessions_lock:
        fails = [t for t in _failed_logins.get(key, []) if time.time() - t < window]
        _failed_logins[key] = fails
    return fails


def login_allowed(user):
    user = user.lower()
    if SETTINGS["ALLOWED_USERS"] and user in SETTINGS["ALLOWED_USERS"]:
        return True
    if SETTINGS["ALLOWED_DOMAINS"] and user.rsplit("@", 1)[-1] in SETTINGS["ALLOWED_DOMAINS"] and "@" in user:
        return True
    return not SETTINGS["ALLOWED_USERS"] and not SETTINGS["ALLOWED_DOMAINS"]


def smtp_connect(user, password):
    host, port, mode = SETTINGS["SMTP_HOST"], SETTINGS["SMTP_PORT"], SETTINGS["SMTP_SECURITY"]
    ctx = ssl.create_default_context()
    if mode == "ssl":
        smtp = smtplib.SMTP_SSL(host, port, timeout=30, context=ctx)
    else:
        smtp = smtplib.SMTP(host, port, timeout=30)
        smtp.ehlo()
        if mode == "starttls":
            smtp.starttls(context=ctx)
            smtp.ehlo()
    try:
        smtp.login(user, password)
    except Exception:
        smtp.close()
        raise
    return smtp


def current_account():
    sid = session.get("sid")
    if not sid:
        return None
    with _sessions_lock:
        acc = _sessions.get(sid)
        if acc and time.time() - acc["since"] > SETTINGS["SESSION_HOURS"] * 3600:
            _sessions.pop(sid, None)
            acc = None
    return acc


# ---------------------------------------------------------------- Gerät merken
# Wer sich mit Name und Passwort anmeldet, bleibt auf diesem Gerät angemeldet: Das Cookie enthält ein zufälliges
# Token, in der Datenbank steht nur sein Hash. Pro Gerät gilt das für die zuletzt angemeldete Person; wer zu jemand
# anderem wechselt, braucht dessen Passwort, und danach ist die bisherige Person auf dem Gerät vergessen.
REMEMBER_COOKIE = "ylva_geraet"
REMEMBER_DAYS = 90


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def remembered_member():
    """Die Person, die auf diesem Gerät zuletzt angemeldet war (oder None)."""
    token = request.cookies.get(REMEMBER_COOKIE, "")
    if not token or len(token) > 100:
        return None
    c = leaddb.connect(DB_PATH)
    try:
        row = c.execute("SELECT member, last_used FROM remember_tokens WHERE token_hash=?",
                        (_token_hash(token),)).fetchone()
        if not row or time.time() - row["last_used"] > REMEMBER_DAYS * 86400:
            return None
        member = c.execute("SELECT key, name FROM team WHERE key=?", (row["member"],)).fetchone()
        if not member:
            return None
        c.execute("UPDATE remember_tokens SET last_used=? WHERE token_hash=?", (time.time(), _token_hash(token)))
        c.commit()
        return dict(member)
    finally:
        c.close()


def remember_member(resp, key):
    """Dieses Gerät merkt sich die Person (ersetzt, was es sich vorher gemerkt hat)."""
    old = request.cookies.get(REMEMBER_COOKIE, "")
    token = secrets.token_urlsafe(32)
    c = leaddb.connect(DB_PATH)
    try:
        if old:
            c.execute("DELETE FROM remember_tokens WHERE token_hash=?", (_token_hash(old),))
        c.execute("DELETE FROM remember_tokens WHERE last_used < ?", (time.time() - REMEMBER_DAYS * 86400,))
        c.execute("INSERT INTO remember_tokens (token_hash, member, created_at, last_used) VALUES (?, ?, ?, ?)",
                  (_token_hash(token), key, leaddb.now(), time.time()))
        c.commit()
    finally:
        c.close()
    resp.set_cookie(REMEMBER_COOKIE, token, max_age=REMEMBER_DAYS * 86400, httponly=True, samesite="Lax",
                    secure=app.config["SESSION_COOKIE_SECURE"])
    return resp


def _resume_remembered():
    """Keine Sitzung (abgelaufen, Neustart), aber das Gerät kennt die Person: einfach weiter angemeldet."""
    member = remembered_member()
    if not member:
        return None
    sid = secrets.token_urlsafe(32)
    acc = {"user": member["key"], "team": member["key"], "name": member["name"], "password": "", "address": "",
           "since": time.time()}
    with _sessions_lock:
        _sessions[sid] = acc
    session["sid"] = sid  # CSRF-Token in der Sitzung bleibt, damit ein gerade abgeschicktes Formular noch durchgeht
    return acc


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        g.account = current_account() or _resume_remembered()
        if not g.account:
            if request.method == "POST" or request.path.startswith(("/preview", "/send", "/upload")):
                return jsonify(error="Sitzung abgelaufen. Bitte neu anmelden."), 401
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token
app.jinja_env.globals["demo_enabled"] = SETTINGS["DEMO_MODE"]


@app.before_request
def check_csrf():
    if request.method == "POST":
        token = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
        if not token or token != session.get("csrf"):
            abort(400, "Ungültiges Formular-Token. Bitte Seite neu laden.")


@app.url_defaults
def _static_version(endpoint, values):
    """Hängt an CSS/JS-Adressen den Stand der Datei an (?v=…). So holt der Browser nach einem Update sofort die neue
    Fassung, statt die alte aus dem Zwischenspeicher zu nehmen."""
    if endpoint == "static" and "filename" in values and "v" not in values:
        try:
            values["v"] = int((ROOT / "static" / values["filename"]).stat().st_mtime)
        except OSError:
            pass


@app.context_processor
def _ui_theme():
    """Darstellung (Hell, Dunkel, Automatisch) aus dem Cookie, damit die Seite gleich richtig gezeichnet wird."""
    theme = request.cookies.get("theme", "")
    return {"ui_theme": theme if theme in ("light", "dark") else "auto"}


# Skripte nur aus der App selbst: Selbst wenn irgendwo fremder Text als HTML durchrutscht, läuft kein Skript.
# Kartenkacheln kommen von OpenStreetMap, Styles dürfen inline sein (Mails und Leaflet brauchen das).
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob: https://tile.openstreetmap.org; connect-src 'self'; frame-src 'self'; "
       "frame-ancestors 'self'; object-src 'none'; base-uri 'self'; form-action 'self'")


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if resp.mimetype == "text/html" and not request.path.startswith(("/preview", "/history/")):
        resp.headers.setdefault("X-Frame-Options", "DENY")
    if resp.mimetype == "text/html":
        resp.headers.setdefault("Content-Security-Policy", CSP)
    if app.config["SESSION_COOKIE_SECURE"]:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return resp


def safe_next(nxt):
    """Nur Pfade innerhalb der App. „//evil.de“ und „/\\evil.de“ behandeln Browser als fremde Adresse."""
    if not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt or any(ord(ch) < 32 for ch in nxt):
        return ""
    return nxt


def _login_page(status=200, **kw):
    from . import team
    c = leaddb.connect(DB_PATH)
    try:
        people = team.members(c)
    finally:
        c.close()
    known = remembered_member()
    mode = kw.pop("mode", None) or ("team" if known or any(m["has_password"] for m in people) else "postfach")
    kw.setdefault("member", known["key"] if known else None)
    return render_template("login.html", people=people, mode=mode, known=known, **kw), status


def _team_login():
    """Anmelden mit Name und eigenem Passwort (Finn, Hamza, Oskar). Gleiche Sperre nach Fehlversuchen."""
    from . import team
    key = request.form.get("member", "")
    password = request.form.get("password", "")
    keys = [f"ip:{client_ip()}", f"user:team:{key}"]
    if any(len(recent_fails(k)) >= LOCK_RULES[k.split(":", 1)[0]][0] for k in keys):
        flash("Zu viele Fehlversuche. Bitte in einigen Minuten erneut versuchen.")
        return _login_page(429, member=key, mode="team")
    known = remembered_member()
    if known and known["key"] == key and not password:  # zuletzt auf diesem Gerät: ohne Passwort weiter
        _start_session({"user": key, "team": key, "name": known["name"], "password": "", "address": ""})
        return remember_member(redirect(safe_next(request.args.get("next", "")) or url_for("index")), key)
    c = leaddb.connect(DB_PATH)
    try:
        member = team.get(c, key)
        ok = bool(member) and team.check_password(c, key, password)
        no_password = bool(member) and not member["pw_hash"]
    finally:
        c.close()
    if not ok:
        with _sessions_lock:
            for k in keys:
                _failed_logins.setdefault(k, []).append(time.time())
        app.logger.warning("Fehlgeschlagene Anmeldung für %r von %s", key, client_ip())
        flash("Für diese Person ist noch kein Passwort festgelegt. Bitte einmal mit dem Postfach anmelden und unter "
              "„Mein Profil“ ein Passwort festlegen." if no_password else "Anmeldung fehlgeschlagen: Passwort falsch.")
        return _login_page(401, member=key, mode="team")
    with _sessions_lock:
        for k in keys:
            _failed_logins.pop(k, None)
    _start_session({"user": key, "team": key, "name": member["name"], "password": "", "address": ""})
    return remember_member(redirect(safe_next(request.args.get("next", "")) or url_for("index")), key)


def _start_session(acc):
    sid = secrets.token_urlsafe(32)
    with _sessions_lock:
        _sessions[sid] = {**acc, "since": time.time()}
    session.clear()
    session["sid"] = sid


def update_session(**values):
    """Angaben der laufenden Anmeldung ändern (z. B. wer man im Team ist)."""
    with _sessions_lock:
        acc = _sessions.get(session.get("sid"))
        if acc:
            acc.update(values)
            g.account = acc


def mail_creds(account):
    """Womit der Mailer verschickt: mit dem eigenen Postfach (Anmeldung per Postfach) oder, bei Anmeldung mit
    Name und Passwort, über das gemeinsame Akquise-Postfach."""
    if account.get("password") or account.get("demo"):
        return account["user"], account["password"], account["address"]
    user = SETTINGS["OUTREACH_USER"]
    return user, SETTINGS["OUTREACH_PASSWORD"], user if "@" in user else SETTINGS["FROM_ADDRESS"]


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("member"):
            return _team_login()
        user = request.form.get("user", "").strip()
        password = request.form.get("password", "")
        keys = [f"ip:{client_ip()}", f"user:{user.lower()}"]
        if any(len(recent_fails(k)) >= LOCK_RULES[k.split(":", 1)[0]][0] for k in keys):
            flash("Zu viele Fehlversuche. Bitte in einigen Minuten erneut versuchen.")
            return _login_page(429, user=user, mode="postfach")
        count_fail = True
        if not login_allowed(user):
            error = "Dieses Konto ist für den Mailer nicht freigegeben."
        else:
            try:
                smtp_connect(user, password).quit()
                error = None
            except smtplib.SMTPAuthenticationError:
                error = "Anmeldung fehlgeschlagen: Benutzername oder Passwort falsch."
            except Exception as exc:  # Netzwerk, TLS, falscher Host …
                error = f"Mailserver nicht erreichbar ({exc.__class__.__name__}: {exc})."
                count_fail = False  # Serverprobleme sollen niemanden aussperren
        if error and count_fail:
            with _sessions_lock:
                for k in keys:
                    _failed_logins.setdefault(k, []).append(time.time())
            app.logger.warning("Fehlgeschlagene Anmeldung für %r von %s", user, client_ip())
        if error:
            flash(error)
            return _login_page(401, user=user, mode="postfach")
        with _sessions_lock:
            for k in keys:
                _failed_logins.pop(k, None)
        _start_session({"user": user, "password": password, "team": None,
                        "address": user if "@" in user else SETTINGS["FROM_ADDRESS"]})
        nxt = safe_next(request.args.get("next", "")) or url_for("index")
        return redirect(url_for("team.who_am_i", next=nxt))
    if current_account():
        return redirect(url_for("index"))
    return _login_page()


@app.post("/demo")
def demo_login():
    """Demo-Modus: ohne Postfach alles ausprobieren, verschickt wird nichts."""
    if not SETTINGS["DEMO_MODE"]:
        abort(404)
    sid = secrets.token_urlsafe(32)
    with _sessions_lock:
        _sessions[sid] = {"user": "demo", "password": "", "since": time.time(), "team": "oskar",
                          "name": "Oskar Jacobsen", "address": "demo@ylvalabs.de", "demo": True}
    session.clear()
    session["sid"] = sid
    return redirect(url_for("index"))


@app.post("/login/vergessen")
def forget_device():
    """Dieses Gerät soll sich niemanden mehr merken."""
    token = request.cookies.get(REMEMBER_COOKIE, "")
    if token:
        c = leaddb.connect(DB_PATH)
        try:
            c.execute("DELETE FROM remember_tokens WHERE token_hash=?", (_token_hash(token),))
            c.commit()
        finally:
            c.close()
    with _sessions_lock:
        _sessions.pop(session.get("sid"), None)
    session.clear()
    resp = redirect(url_for("login"))
    resp.delete_cookie(REMEMBER_COOKIE, samesite="Lax", secure=app.config["SESSION_COOKIE_SECURE"])
    return resp


@app.post("/logout")
def logout():
    with _sessions_lock:
        _sessions.pop(session.get("sid"), None)
    session.clear()
    return redirect(url_for("login"))

# ---------------------------------------------------------------- Datenbank


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("""CREATE TABLE IF NOT EXISTS mails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL, user TEXT NOT NULL, template TEXT, subject TEXT,
            recipients TEXT, sent INTEGER, failed TEXT, fields TEXT, html TEXT)""")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn:
        conn.close()

# ---------------------------------------------------------------- Formular


LIST_FIELDS = ("info_label", "info_value")


def form_data():
    data = {k: v for k, v in request.form.items() if k not in LIST_FIELDS and k != "_csrf"}
    for key in LIST_FIELDS:
        data[key] = request.form.getlist(key)
    return data


def image_path(form):
    image_id = form.get("image_id", "")
    if form.get("graphic") != "image" or not re.fullmatch(r"[0-9a-f]{32}\.(png|jpg|gif)", image_id):
        return None
    path = UPLOAD_DIR / image_id
    return path if path.exists() else None


def defaults():
    return {
        "template": "newsletter", "graphic": "band_original", "band_seed": "3", "icon": "werkstatt",
        "greeting": "Hallo {vorname},", "closing": SETTINGS["DEFAULT_CLOSING"],
        "sender_name": SETTINGS["DEFAULT_SENDER_NAME"], "footer": SETTINGS["DEFAULT_FOOTER"],
        "info_label": [""], "info_value": [""],
    }


@app.route("/")
@login_required
def index():
    return redirect(url_for("leads.home"))


def lead_fields(lead):
    """Formular „Verfassen“ für einen Betrieb aus dem CRM vorbelegen (Text aus dem Entwurf)."""
    rcpt = emailbuild.formataddr((lead["contact_name"] or "", lead["email"])) if lead["email"] else ""
    return {"template": "brief", "graphic": "none", "recipients": rcpt, "subject": lead["subject"] or "",
            "greeting": lead["greeting"] or ("Guten Tag {name}," if lead["contact_name"] else "Guten Tag,"),
            "body": lead["body"] or "", "closing": "Viele Grüße",
            "sender_name": g.account.get("name") or SETTINGS["LEADS_SENDER_NAME"],
            "reply_to": agent.outreach_address() if agent.outreach_configured() else "", "lead_id": str(lead["id"])}


def _record_in_crm(lead_id, msgs, author):
    """Nach dem Versand aus dem Mailer: Betrieb in der Akte vermerken, auf „Kontaktiert“ schieben und ins CRM der
    angemeldeten Person (wenn noch niemand zuständig ist). Ohne Betrieb aus dem CRM: Empfänger, deren Adresse zu
    einem Betrieb gehört, zählen genauso."""
    c = leaddb.connect(DB_PATH)
    try:
        if lead_id:
            pairs = [(lead_id, msgs[0])]
        else:
            pairs = []
            for msg in msgs:
                addr = parseaddr(str(msg["To"]))[1].strip().lower()
                row = c.execute("SELECT id FROM leads WHERE lower(email)=? AND status != 'gesperrt' "
                                "ORDER BY stage IS NULL, id LIMIT 1", (addr,)).fetchone() if addr else None
                if row:
                    pairs.append((row["id"], msg))
        for lid, msg in pairs:
            crm.record_mail(c, lid, msg, author)
            crm.claim(c, lid, g.account.get("team"))
    finally:
        c.close()


@app.route("/compose")
@login_required
def compose():
    fields, from_history, lead = defaults(), False, None
    if g.account.get("name"):
        fields["sender_name"] = g.account["name"]
    if request.args.get("from"):
        row = db().execute("SELECT fields FROM mails WHERE id=? AND user=?",
                           (request.args["from"], g.account["user"])).fetchone()
        if row:
            fields.update(json.loads(row["fields"]))
            fields.pop("lead_id", None)
            from_history = True
    elif request.args.get("lead", "").isdigit():
        c = leaddb.connect(DB_PATH)
        try:
            lead = leaddb.get_lead(c, int(request.args["lead"]))
        finally:
            c.close()
        if lead:
            fields.update(lead_fields(lead))
            from_history = True  # gespeicherten Entwurf im Browser nicht darüberlegen
    return render_template(
        "compose.html", fields=fields, from_history=from_history, lead=lead, templates=emailbuild.TEMPLATES,
        icons={k: (label, pixel.icon_preview_svg(k)) for k, (label, _) in pixel.ICONS.items()},
        account=g.account, imap=bool(SETTINGS["IMAP_HOST"]))


@app.post("/preview")
@login_required
def preview():
    form = form_data()
    rcpts, _ = emailbuild.parse_recipients(form.get("recipients"))
    rcpt = rcpts[0] if rcpts else {"name": "", "first": "", "email": ""}
    img = image_path(form)
    html, _, _ = emailbuild.render(form, rcpt, SETTINGS,
                                   image_src=url_for("uploaded", name=img.name) if img else None)
    return Response(html, mimetype="text/html")


@app.get("/pixel/band/<int:seed>.svg")
@login_required
def band_svg(seed):
    resp = Response(pixel.band_preview_svg(max(1, min(50, seed))), mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


IMAGE_MAGIC = {b"\x89PNG": "png", b"\xff\xd8\xff": "jpg", b"GIF8": "gif"}


@app.post("/upload")
@login_required
def upload():
    file = request.files.get("image")
    if not file:
        return jsonify(error="Keine Datei erhalten."), 400
    head = file.stream.read(8)
    file.stream.seek(0)
    ext = next((e for magic, e in IMAGE_MAGIC.items() if head.startswith(magic)), None)
    if not ext:
        return jsonify(error="Bitte PNG, JPG oder GIF verwenden (WebP/HEIC zeigen viele Mailprogramme nicht an)."), 400
    name = f"{uuid.uuid4().hex}.{ext}"
    file.save(UPLOAD_DIR / name)
    return jsonify(id=name, url=url_for("uploaded", name=name), filename=file.filename)


@app.get("/uploads/<name>")
@login_required
def uploaded(name):
    if not re.fullmatch(r"[0-9a-f]{32}\.(png|jpg|gif)", name):
        abort(404)
    return send_from_directory(UPLOAD_DIR, name)

# ---------------------------------------------------------------- Versand


def imap_sent_folder(imap):
    if SETTINGS["IMAP_SENT_FOLDER"]:
        return SETTINGS["IMAP_SENT_FOLDER"]
    _, folders = imap.list()
    for raw in folders or []:
        line = raw.decode(errors="ignore")
        if "\\Sent" in line:
            return line.rsplit(' "/" ', 1)[-1].rsplit(' "." ', 1)[-1].strip()
    return "Sent"


def save_to_sent(account, messages):
    imap = imaplib.IMAP4_SSL(SETTINGS["IMAP_HOST"], SETTINGS["IMAP_PORT"], ssl_context=ssl.create_default_context())
    try:
        imap.login(account["user"], account["password"])
        folder = imap_sent_folder(imap)
        for msg in messages:
            imap.append(folder, "\\Seen", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
    finally:
        try:
            imap.logout()
        except Exception:
            pass


@app.post("/send")
@login_required
def send():
    m_user, m_password, m_address = mail_creds(g.account)
    account = {**g.account, "address": m_address}
    form = form_data()
    test = form.get("mode") == "test"
    if not m_password and not g.account.get("demo"):
        return jsonify(error="Zum Senden fehlt das Akquise-Postfach (Einstellungen → Postfach und Versand)."), 400
    if not account["address"]:
        return jsonify(error="Keine Absenderadresse bekannt. Bitte FROM_ADDRESS in der Konfiguration setzen."), 400
    if test:
        rcpts = [{"name": "", "first": "", "email": account["address"]}]
        real, _ = emailbuild.parse_recipients(form.get("recipients"))
        if real:  # Testmail mit den Daten des ersten Empfängers personalisieren
            rcpts = [dict(real[0], email=account["address"])]
    else:
        rcpts, errors = emailbuild.parse_recipients(form.get("recipients"))
        if errors:
            return jsonify(error="Ungültige Adresse(n): " + ", ".join(errors)), 400
    if not rcpts:
        return jsonify(error="Bitte mindestens einen Empfänger angeben."), 400
    if len(rcpts) > SETTINGS["MAX_RECIPIENTS"]:
        return jsonify(error=f"Maximal {SETTINGS['MAX_RECIPIENTS']} Empfänger pro Versand."), 400
    if not form.get("subject", "").strip():
        return jsonify(error="Bitte einen Betreff eingeben."), 400
    if form.get("graphic") == "image" and not image_path(form):
        return jsonify(error="Bitte ein Bild hochladen oder eine andere Grafik wählen."), 400

    img = image_path(form)
    lead_id = int(form["lead_id"]) if form.get("lead_id", "").isdigit() else None
    if lead_id and not test:
        c = leaddb.connect(DB_PATH)
        try:
            lead = leaddb.get_lead(c, lead_id)
            ok, why = crm.may_email(lead, SETTINGS["OUTREACH_EMAIL"]) if lead else (False, "Betrieb nicht gefunden.")
        finally:
            c.close()
        if not ok:
            return jsonify(error=why), 400
    if account.get("demo"):
        if not test:
            msgs = [emailbuild.build_message(form, r, SETTINGS, account["address"], img) for r in rcpts]
            _record_in_crm(lead_id, msgs, "Demo")  # Demo: in der Akte vermerken, verschickt wird nichts
            return jsonify(demo=True, would_send=len(rcpts))
        # Statt zu senden: die fertige Mail als .eml-Datei zum Öffnen im Mailprogramm
        msg = emailbuild.build_message(form, rcpts[0], SETTINGS, account["address"], img)
        msg.replace_header("To", account["address"])
        name = re.sub(r"[^\w\-]+", "-", form.get("subject", "mail")).strip("-")[:60] or "mail"
        return Response(msg.as_bytes(), mimetype="message/rfc822",
                        headers={"Content-Disposition": f'attachment; filename="{name}.eml"'})
    sent, failed, sent_msgs = [], [], []
    try:
        smtp = smtp_connect(m_user, m_password)
    except Exception as exc:
        return jsonify(error=f"Verbindung zum Mailserver fehlgeschlagen: {exc}"), 502
    try:
        for i, rcpt in enumerate(rcpts):
            if i and SETTINGS["SEND_DELAY"] > 0:
                time.sleep(SETTINGS["SEND_DELAY"])
            msg = emailbuild.build_message(form, rcpt, SETTINGS, account["address"], img)
            if test:
                msg.replace_header("Subject", "[Test] " + msg["Subject"])
                msg.replace_header("To", account["address"])
            try:
                smtp.send_message(msg)
                sent.append(rcpt["email"])
                sent_msgs.append(msg)
            except smtplib.SMTPServerDisconnected:
                smtp = smtp_connect(m_user, m_password)
                smtp.send_message(msg)
                sent.append(rcpt["email"])
                sent_msgs.append(msg)
            except Exception as exc:
                failed.append({"email": rcpt["email"], "error": str(exc)})
    except Exception as exc:
        failed.append({"email": "–", "error": f"Versand abgebrochen: {exc}"})
    finally:
        try:
            smtp.quit()
        except Exception:
            pass

    warning = None
    if sent_msgs and not test:
        _record_in_crm(lead_id, sent_msgs, g.account.get("name") or account["address"])
    if sent_msgs and not test and SETTINGS["IMAP_HOST"] and form.get("save_sent"):
        try:
            save_to_sent({"user": m_user, "password": m_password}, sent_msgs)
        except Exception as exc:
            warning = f"Gesendet, aber Ablage im Ordner „Gesendet“ fehlgeschlagen: {exc}"

    if not test and sent:
        html, _, subject = emailbuild.render(form, rcpts[0], SETTINGS,
                                             image_src=url_for("uploaded", name=img.name) if img else None)
        db().execute(
            "INSERT INTO mails (created_at, user, template, subject, recipients, sent, failed, fields, html)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), account["user"], form.get("template"),
             form.get("subject"), form.get("recipients"), len(sent), json.dumps(failed, ensure_ascii=False),
             json.dumps(form, ensure_ascii=False), html))
        db().commit()
    status = 200 if sent else 502
    return jsonify(sent=sent, failed=failed, warning=warning, test=test), status

# ---------------------------------------------------------------- Verlauf


@app.get("/history")
@login_required
def history():
    rows = db().execute("SELECT id, created_at, template, subject, recipients, sent, failed FROM mails "
                        "WHERE user=? ORDER BY id DESC LIMIT 200", (g.account["user"],)).fetchall()
    items = []
    for r in rows:
        rc, _ = emailbuild.parse_recipients(r["recipients"])
        items.append({**dict(r), "failed": json.loads(r["failed"] or "[]"), "rcpt_count": len(rc),
                      "rcpt_preview": ", ".join(x["email"] for x in rc[:3]) + (" …" if len(rc) > 3 else ""),
                      "date": datetime.fromisoformat(r["created_at"]).strftime("%d.%m.%Y · %H:%M")})
    return render_template("history.html", items=items, account=g.account)


@app.get("/history/<int:mail_id>/html")
@login_required
def history_html(mail_id):
    row = db().execute("SELECT html FROM mails WHERE id=? AND user=?", (mail_id, g.account["user"])).fetchone()
    if not row:
        abort(404)
    return Response(row["html"], mimetype="text/html")


@app.get("/healthz")
def healthz():
    return "ok"


# ---------------------------------------------------------------- Akquise und CRM
from . import agent, crm, leaddb  # noqa: E402
from .crm_views import bp as crm_bp  # noqa: E402
from .leads_views import bp as leads_bp  # noqa: E402
from .team_views import bp as team_bp  # noqa: E402
from .palette import bp as palette_bp  # noqa: E402
from .settings_views import bp as settings_bp  # noqa: E402

app.jinja_env.globals["subject_choices"] = agent.SUBJECTS
app.register_blueprint(leads_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(crm_bp)
app.register_blueprint(palette_bp)
app.register_blueprint(team_bp)
agent.init(SETTINGS, smtp_connect, imap_sent_folder, DB_PATH, SETTINGS["DATA_DIR"])
