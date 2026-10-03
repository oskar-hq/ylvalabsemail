"""Systemcheck: Ist alles eingerichtet? Prüft Einstellungen, Postfach, KI, Kartensuche, Webseiten und Push.

In der App unter „Leads → Systemcheck“, oder im Terminal auf dem Server:
    docker compose exec mailer python -m app.systemcheck      (mit Docker)
    /opt/ylva-mailer/.venv/bin/python -m app.systemcheck       (ohne Docker, im Ordner /opt/ylva-mailer)

Kostet nichts: Es wird keine KI-Anfrage gestellt, nur geprüft, ob Schlüssel und Modelle freigeschaltet sind.
"""
import imaplib
import os
import shutil
import ssl
import time
import urllib.request

OK, WARN, FAIL = "ok", "warn", "fail"


FAIL_HINTS = {
    "Postausgang (SMTP)": "Adresse, Passwort, SMTP_HOST und SMTP_PORT prüfen (Strato: smtp.strato.de, 465 oder 587). "
                          "Die Firewall des Servers muss ausgehend Port 465 bzw. 587 erlauben.",
    "Posteingang (IMAP)": "IMAP_HOST prüfen (Strato: imap.strato.de) und ausgehend Port 993 erlauben.",
    "KI-Schlüssel und Modelle": "Schlüssel in console.anthropic.com prüfen; der Server muss api.anthropic.com erreichen.",
    "Kartensuche (OpenStreetMap)": "Der Server muss overpass-api.de erreichen. Ist der Dienst überlastet, später erneut prüfen.",
    "Webseiten lesen": "Der Server muss ausgehend beliebige Webseiten über HTTPS (Port 443) erreichen.",
    "Datenordner": "Rechte des Datenordners prüfen (Docker: Volume mailer-data).",
}


def _check(name, func):
    start = time.time()
    try:
        status, detail, hint = func()
    except Exception as exc:  # jeder Check darf scheitern, ohne die anderen mitzureißen
        status, detail, hint = FAIL, f"{exc.__class__.__name__}: {exc}", FAIL_HINTS.get(name)
    return {"name": name, "status": status, "detail": detail, "hint": hint, "ms": int((time.time() - start) * 1000)}


def run(settings, smtp_connect, sent_folder, data_dir):
    from . import agent, ai, finder, leaddb, webpush

    S = settings
    out_user, out_pw = S["OUTREACH_USER"], S["OUTREACH_PASSWORD"]

    def config():
        problems = []
        if not S["SMTP_HOST"] or "example" in S["SMTP_HOST"]:
            problems.append("SMTP_HOST fehlt")
        if not S["ALLOWED_DOMAINS"] and not S["ALLOWED_USERS"]:
            problems.append("ALLOWED_DOMAINS ist leer (jedes Postfach könnte sich anmelden)")
        if S["DEMO_MODE"]:
            problems.append("DEMO_MODE ist an")
        if problems:
            return FAIL, "; ".join(problems), "In der .env korrigieren und neu starten."
        return OK, f"Anmeldung nur für {', '.join(S['ALLOWED_DOMAINS'] + S['ALLOWED_USERS'])}", None

    def https():
        base = S["BASE_URL"]
        if not base:
            return WARN, "BASE_URL ist leer", "Ohne HTTPS-Adresse gehen keine Push-Nachrichten aufs Handy, und Links in Mails fehlen."
        if not base.startswith("https://"):
            return WARN, f"BASE_URL ist {base}", "Für die Handy-App ist HTTPS nötig."
        cookie = os.environ.get("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")
        if not cookie:
            return WARN, f"{base}, aber COOKIE_SECURE=false", "Bei HTTPS COOKIE_SECURE=true setzen."
        return OK, base, None

    def storage():
        test = data_dir / ".schreibtest"
        test.write_text("ok")
        test.unlink()
        free_gb = shutil.disk_usage(data_dir).free / 1e9
        if free_gb < 1:
            return WARN, f"{data_dir}, nur noch {free_gb:.1f} GB frei", "Speicher freigeben oder vergrößern."
        return OK, f"{data_dir} beschreibbar, {free_gb:.0f} GB frei", None

    def smtp_check():
        if not out_user or not out_pw:
            return WARN, "OUTREACH_USER / OUTREACH_PASSWORD fehlen", \
                "Ohne Akquise-Postfach keine Mails und keine Benachrichtigungen per Mail."
        smtp_connect(out_user, out_pw).quit()
        return OK, f"Anmeldung als {out_user} bei {S['SMTP_HOST']}:{S['SMTP_PORT']} klappt", None

    def imap_check():
        if not out_user or not out_pw:
            return WARN, "Kein Akquise-Postfach eingetragen", None
        if not S["IMAP_HOST"]:
            return WARN, "IMAP_HOST fehlt", "Ohne IMAP werden Antworten der Firmen nicht gelesen."
        imap = imaplib.IMAP4_SSL(S["IMAP_HOST"], S["IMAP_PORT"], ssl_context=ssl.create_default_context(), timeout=20)
        try:
            imap.login(out_user, out_pw)
            typ, data = imap.select("INBOX", readonly=True)
            folder = sent_folder(imap)
        finally:
            try:
                imap.logout()
            except Exception:
                pass
        return OK, f"Posteingang lesbar ({data[0].decode()} Mails), Gesendet-Ordner: {folder}", None

    def ai_check():
        if not ai.available():
            return FAIL, "ANTHROPIC_API_KEY fehlt", "Schlüssel in der Anthropic Console erzeugen und in die .env eintragen."
        client = ai.client()
        found = []
        for stage in ("vorpruefung", "analyse", "entwurf"):
            model = ai.MODELS[stage]
            try:
                client.models.retrieve(model)
                found.append(model)
            except Exception as exc:
                return FAIL, f"Modell {model} nicht verfügbar: {exc}", "AI_MODEL_QUICK / AI_MODEL_ANALYSE / AI_MODEL prüfen."
        return OK, "Schlüssel gültig, freigeschaltet: " + ", ".join(dict.fromkeys(found)), None

    def overpass_check():
        url = S["OVERPASS_URL"].rsplit("/", 1)[0] + "/status"
        req = urllib.request.Request(url, headers={"User-Agent": finder.USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            first = resp.read(300).decode("utf-8", "ignore").splitlines()[:1]
        return OK, f"Kartensuche erreichbar ({first[0] if first else 'ok'})", None

    def web_check():
        url, html = finder.fetch_page("https://ylvalabs.de/", {})
        return OK, f"Webseiten lesbar (Test: {url}, {len(html) // 1000} kB)", None

    def push_check():
        v = webpush.Vapid(str(data_dir / "vapid_private.pem"))
        c = leaddb.connect(data_dir / "mailer.db")
        try:
            subs = c.execute("SELECT COUNT(*) FROM push_subs").fetchone()[0]
        finally:
            c.close()
        if not subs:
            return WARN, f"Schlüssel bereit ({v.public_key[:12]}…), aber noch kein Gerät angemeldet", \
                "Auf dem Handy die Web-App installieren und „Benachrichtigungen an“ tippen."
        return OK, f"{subs} Gerät(e) angemeldet", None

    def notify_check():
        if not S["NOTIFY_EMAILS"]:
            return WARN, "NOTIFY_EMAILS ist leer", "Sonst kommen Benachrichtigungen nur per Push."
        return OK, "Benachrichtigung per Mail an " + ", ".join(S["NOTIFY_EMAILS"]), None

    def budget_check():
        cap = S["LEADS_MONTHLY_BUDGET_EUR"]
        mail = "Erstkontakt per Mail ist AN" if S["OUTREACH_EMAIL"] else "Erstkontakt per Mail ist aus (nur Brief/Telefon)"
        if not cap:
            return WARN, f"Kein Monatsbudget. {mail}", "LEADS_MONTHLY_BUDGET_EUR setzen, z. B. 20."
        return OK, f"Monatsbudget {cap:.0f} €. {mail}", None

    def worker_check():
        if not S["LEADS_WORKER"]:
            return WARN, "LEADS_WORKER=false", "Ohne Arbeiter laufen keine Läufe und das Postfach wird nicht gelesen."
        if not agent.STATE["running"]:
            return WARN, "Arbeiter läuft in diesem Prozess nicht (normal beim Aufruf im Terminal)", None
        return OK, f"Läuft, zuletzt: {agent.STATE['activity']}", None

    checks = [
        ("Grundeinstellungen", config), ("HTTPS-Adresse", https), ("Datenordner", storage),
        ("Postausgang (SMTP)", smtp_check), ("Posteingang (IMAP)", imap_check), ("KI-Schlüssel und Modelle", ai_check),
        ("Kartensuche (OpenStreetMap)", overpass_check), ("Webseiten lesen", web_check), ("Push-Benachrichtigungen", push_check),
        ("Benachrichtigung per Mail", notify_check), ("Budget und Versand", budget_check), ("Hintergrund-Arbeiter", worker_check),
    ]
    return [_check(name, func) for name, func in checks]


def main():
    worker_wanted = os.environ.get("LEADS_WORKER", "true").lower() in ("1", "true", "yes")
    os.environ["LEADS_WORKER"] = "false"  # im Terminal keinen zweiten Arbeiter starten
    from . import main as web

    settings = dict(web.SETTINGS, LEADS_WORKER=worker_wanted)
    results = run(settings, web.smtp_connect, web.imap_sent_folder, web.SETTINGS["DATA_DIR"])
    marks = {OK: "OK   ", WARN: "HINW.", FAIL: "FEHLT"}
    for r in results:
        print(f"[{marks[r['status']]}] {r['name']}: {r['detail']}")
        if r["hint"] and r["status"] != OK:
            print(f"         → {r['hint']}")
    fails = sum(r["status"] == FAIL for r in results)
    warns = sum(r["status"] == WARN for r in results)
    print(f"\n{len(results) - fails - warns} in Ordnung, {warns} Hinweise, {fails} Fehler.")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
