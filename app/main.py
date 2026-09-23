"""Ylva Labs Mailer – Web-Oberfläche zum Versenden gestalteter HTML-E-Mails."""
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
from functools import wraps
from pathlib import Path

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)

from . import emailbuild, pixel

ROOT = Path(__file__).resolve().parent.parent


def env(key, default=""):
    return os.environ.get(key, default).strip()


SETTINGS = {
    "SMTP_HOST": env("SMTP_HOST"),
    "SMTP_PORT": int(env("SMTP_PORT", "587")),
    "SMTP_SECURITY": env("SMTP_SECURITY", "starttls").lower(),  # starttls | ssl | none
    "IMAP_HOST": env("IMAP_HOST"),
    "IMAP_PORT": int(env("IMAP_PORT", "993")),
    "IMAP_SENT_FOLDER": env("IMAP_SENT_FOLDER"),
    "FROM_ADDRESS": env("FROM_ADDRESS"),
    "ALLOWED_USERS": [u.lower() for u in re.split(r"[,\s]+", env("ALLOWED_USERS")) if u],
    "ORG_NAME": env("ORG_NAME", "Ylva Labs"),
    "DEFAULT_SENDER_NAME": env("DEFAULT_SENDER_NAME"),
    "DEFAULT_FOOTER": env("DEFAULT_FOOTER", "Ylva Labs · Schleswig-Holstein"),
    "DEFAULT_CLOSING": env("DEFAULT_CLOSING", "Ihr habt ein Problem, das nervt, oder eine Idee, die raus will? Antwortet einfach auf diese Mail."),
    "SEND_DELAY": float(env("SEND_DELAY", "1")),
    "MAX_RECIPIENTS": int(env("MAX_RECIPIENTS", "200")),
    "SESSION_HOURS": float(env("SESSION_HOURS", "12")),
    "DATA_DIR": Path(env("DATA_DIR", str(ROOT / "data"))),
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
_failed_logins = {}


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


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        g.account = current_account()
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


@app.before_request
def check_csrf():
    if request.method == "POST":
        token = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
        if not token or token != session.get("csrf"):
            abort(400, "Ungültiges Formular-Token. Bitte Seite neu laden.")


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if resp.mimetype == "text/html" and not request.path.startswith(("/preview", "/history/")):
        resp.headers.setdefault("X-Frame-Options", "DENY")
    return resp


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
        fails = [t for t in _failed_logins.get(ip, []) if time.time() - t < 600]
        if len(fails) >= 5:
            flash("Zu viele Fehlversuche. Bitte in ein paar Minuten erneut versuchen.")
            return render_template("login.html"), 429
        user = request.form.get("user", "").strip()
        password = request.form.get("password", "")
        if SETTINGS["ALLOWED_USERS"] and user.lower() not in SETTINGS["ALLOWED_USERS"]:
            error = "Dieses Konto ist für den Mailer nicht freigegeben."
        else:
            try:
                smtp_connect(user, password).quit()
                error = None
            except smtplib.SMTPAuthenticationError:
                error = "Anmeldung fehlgeschlagen: Benutzername oder Passwort falsch."
            except Exception as exc:  # Netzwerk, TLS, falscher Host …
                error = f"Mailserver nicht erreichbar ({exc.__class__.__name__}: {exc})."
        if error:
            _failed_logins[ip] = fails + [time.time()]
            flash(error)
            return render_template("login.html", user=user), 401
        _failed_logins.pop(ip, None)
        sid = secrets.token_urlsafe(32)
        with _sessions_lock:
            _sessions[sid] = {"user": user, "password": password, "since": time.time(),
                              "address": user if "@" in user else SETTINGS["FROM_ADDRESS"]}
        session.clear()
        session["sid"] = sid
        nxt = request.args.get("next", "")
        return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("compose"))
    if current_account():
        return redirect(url_for("compose"))
    return render_template("login.html")


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
    return redirect(url_for("compose"))


@app.route("/compose")
@login_required
def compose():
    fields, from_history = defaults(), False
    if request.args.get("from"):
        row = db().execute("SELECT fields FROM mails WHERE id=? AND user=?",
                           (request.args["from"], g.account["user"])).fetchone()
        if row:
            fields.update(json.loads(row["fields"]))
            from_history = True
    return render_template(
        "compose.html", fields=fields, from_history=from_history, templates=emailbuild.TEMPLATES,
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
    account = g.account
    form = form_data()
    test = form.get("mode") == "test"
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
    sent, failed, sent_msgs = [], [], []
    try:
        smtp = smtp_connect(account["user"], account["password"])
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
                smtp = smtp_connect(account["user"], account["password"])
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
    if sent_msgs and not test and SETTINGS["IMAP_HOST"] and form.get("save_sent"):
        try:
            save_to_sent(account, sent_msgs)
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
