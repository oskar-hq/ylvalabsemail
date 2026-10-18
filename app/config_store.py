"""Einstellungen aus der Web-App statt aus der .env.

Was in der App gespeichert wird, liegt im Datenordner (settings.json) und gilt vor der .env. Beim Start werden die
Werte in die Umgebung übernommen, bevor irgendein Modul seine Einstellungen liest; nach dem Speichern startet die App
deshalb kurz neu. Passwörter und Schlüssel werden verschlüsselt abgelegt (Schlüsseldatei .settings_key, nur für die App
lesbar) und in der Oberfläche nie wieder angezeigt.

Bewusst nicht in der App änderbar, weil man sich damit aussperren oder die Sicherheit aushebeln könnte:
ALLOWED_DOMAINS, ALLOWED_USERS, ADMIN_USERS, SECRET_KEY, COOKIE_SECURE, TRUST_PROXY, DEMO_MODE, DATA_DIR.
"""
import json
import os
import re
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

STORE = "settings.json"
KEYFILE = ".settings_key"

# (Schlüssel, Bezeichnung, Typ, Vorgabe, Hinweis). Typen: text, int, float, bool, secret, emails, url, select:a|b|c
GROUPS = [
    ("postfach", "Postfach und Versand", [
        ("SMTP_HOST", "Postausgang (SMTP)", "text", "", "z. B. smtp.strato.de"),
        ("SMTP_PORT", "SMTP-Port", "int", "587", "465 (SSL) oder 587 (STARTTLS)"),
        ("SMTP_SECURITY", "Verschlüsselung", "select:starttls|ssl|none", "starttls", "starttls bei 587, ssl bei 465"),
        ("IMAP_HOST", "Posteingang (IMAP)", "text", "", "z. B. imap.strato.de. Nötig, um Antworten zu lesen"),
        ("IMAP_PORT", "IMAP-Port", "int", "993", ""),
        ("OUTREACH_USER", "Akquise-Postfach", "text", "", "z. B. kontakt@ylvalabs.de"),
        ("OUTREACH_PASSWORD", "Passwort des Akquise-Postfachs", "secret", "", ""),
        ("OUTREACH_EMAIL", "Erstkontakt per Mail erlauben", "bool", "false",
         "Erst einschalten, wenn § 7 UWG geklärt ist. Sonst nur Brief und Telefon"),
        ("SEND_DELAY", "Pause zwischen zwei Mails (Sekunden)", "float", "1", ""),
        ("MAX_RECIPIENTS", "Höchstens Empfänger pro Versand", "int", "200", ""),
        ("FROM_ADDRESS", "Absenderadresse", "emails", "", "Nur nötig, wenn der Login-Name keine E-Mail-Adresse ist"),
        ("IMAP_SENT_FOLDER", "Ordner „Gesendet“", "text", "", "Leer = automatisch erkennen"),
    ]),
    ("absender", "Absender und Texte", [
        ("ORG_NAME", "Firmenname", "text", "Ylva Labs", ""),
        ("LEADS_SENDER_NAME", "Unterschrift unter Akquise-Nachrichten", "text", "Oskar Jacobsen", ""),
        ("DEFAULT_SENDER_NAME", "Vorbelegter Absendername im Mailer", "text", "", ""),
        ("DEFAULT_FOOTER", "Fußzeile der Mails", "text", "Ylva Labs · Schleswig-Holstein", "Firmenname und Anschrift wie im Impressum"),
        ("DEFAULT_CLOSING", "Vorbelegter Schlusssatz im Mailer", "text",
         "Ihr habt ein Problem, das nervt, oder eine Idee, die raus will? Antwortet einfach auf diese Mail.", ""),
    ]),
    ("suche", "Suche und Bewertung", [
        ("LEADS_REGION", "Suchgebiet", "select:DE-SH|", "DE-SH", "DE-SH = ganz Schleswig-Holstein, leer = Umkreis"),
        ("LEADS_CENTER", "Mittelpunkt (Breite,Länge)", "text", "54.6614,9.9311", "Kappeln. Für die Entfernung und den Umkreis"),
        ("LEADS_RADIUS_KM", "Umkreis in km", "float", "50", "Nur, wenn das Suchgebiet leer ist"),
        ("LEADS_RULE_MIN", "Mindestpunktzahl", "int", "50", "Ab dieser Punktzahl passt ein Betrieb"),
        ("LEADS_RUN_CHECK", "Vorschlag: Betriebe pro Lauf bewerten", "int", "150", ""),
        ("LEADS_RUN_TOP", "Vorschlag: die besten ins CRM", "int", "25", ""),
        ("LEADS_FOLLOWUP_DAYS", "Nachfassen nach Tagen ohne Antwort", "int", "7", "0 = nie"),
        ("OVERPASS_URL", "Kartensuche (Overpass-Adresse)", "url", "https://overpass-api.de/api/interpreter", ""),
    ]),
    ("ki", "KI und Budget", [
        ("ANTHROPIC_API_KEY", "KI-Schlüssel (Anthropic)", "secret", "", "Optional. Nur für KI-Entwürfe. In der Anthropic Console ein Ausgabenlimit setzen"),
        ("AI_MODEL", "Modell für Entwürfe und Chat", "text", "claude-opus-5", ""),
        ("AI_MODEL_ANALYSE", "Modell für Erinnerungen und Lernen", "text", "claude-sonnet-5", ""),
        ("AI_EFFORT", "Denk-Aufwand für Entwürfe", "select:low|medium|high", "high", ""),
        ("AI_EFFORT_ANALYSE", "Denk-Aufwand für Erinnerungen", "select:low|medium|high", "medium", ""),
        ("LEADS_RUN_DRAFTS", "Vorschlag: KI-Entwürfe pro Lauf", "int", "0", ""),
        ("LEADS_RUN_BUDGET_EUR", "Vorschlag: Budget pro Lauf (€)", "float", "2", ""),
        ("LEADS_MONTHLY_BUDGET_EUR", "Monatsbudget für die KI (€)", "float", "20", "Harte Grenze. 0 = keine Grenze"),
        ("EUR_PER_USD", "Euro pro US-Dollar", "float", "0.86", "Zum Umrechnen der Preise"),
    ]),
    ("hinweise", "Benachrichtigungen", [
        ("NOTIFY_EMAILS", "Benachrichtigungen per Mail an", "emails", "", "Mehrere mit Komma trennen"),
        ("BASE_URL", "Adresse der App", "url", "", "z. B. https://mail.ylvalabs.de (für Links in Benachrichtigungen)"),
        ("INBOX_MINUTES", "Postfach prüfen alle (Minuten)", "float", "10", ""),
    ]),
]
# Einstellungen wie in einer App: Bereich → Unterseite mit höchstens vier Werten (siehe DESIGN.md im Scraper)
PAGES = {
    "postfach": [("ausgang", "Postausgang", ["SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY"]),
                 ("eingang", "Posteingang", ["IMAP_HOST", "IMAP_PORT", "IMAP_SENT_FOLDER"]),
                 ("zugang", "Akquise-Postfach", ["OUTREACH_USER", "OUTREACH_PASSWORD", "FROM_ADDRESS"]),
                 ("versand", "Versand", ["OUTREACH_EMAIL", "SEND_DELAY", "MAX_RECIPIENTS"])],
    "absender": [("name", "Name und Unterschrift", ["ORG_NAME", "LEADS_SENDER_NAME", "DEFAULT_SENDER_NAME"]),
                 ("texte", "Fußzeile und Schlusssatz", ["DEFAULT_FOOTER", "DEFAULT_CLOSING"])],
    "suche": [("gebiet", "Suchgebiet", ["LEADS_REGION", "LEADS_CENTER", "LEADS_RADIUS_KM"]),
              ("bewertung", "Bewertung und Lauf", ["LEADS_RULE_MIN", "LEADS_RUN_CHECK", "LEADS_RUN_TOP",
                                                   "LEADS_FOLLOWUP_DAYS"]),
              ("karte", "Kartensuche", ["OVERPASS_URL"])],
    "ki": [("schluessel", "KI-Schlüssel", ["ANTHROPIC_API_KEY"]),
           ("modelle", "Modelle", ["AI_MODEL", "AI_EFFORT", "AI_MODEL_ANALYSE", "AI_EFFORT_ANALYSE"]),
           ("budget", "Budget", ["LEADS_MONTHLY_BUDGET_EUR", "LEADS_RUN_BUDGET_EUR", "LEADS_RUN_DRAFTS", "EUR_PER_USD"])],
    "hinweise": [("hinweise", "Benachrichtigungen", ["NOTIFY_EMAILS", "BASE_URL", "INBOX_MINUTES"])],
}
# Fehlt einer dieser Werte, steht beim Bereich „Fehlt“
NEEDED = {"SMTP_HOST", "OUTREACH_USER", "OUTREACH_PASSWORD", "IMAP_HOST"}
GROUP_HINTS = {"postfach": "Strato, Akquise-Postfach, Versand", "absender": "Firmenname, Unterschrift, Fußzeile",
               "suche": "Gebiet, Punkte, wie viele pro Lauf", "ki": "Schlüssel, Modelle, Budget",
               "hinweise": "Mail-Benachrichtigungen, Adresse der App"}
READ_ONLY = ["ALLOWED_DOMAINS", "ALLOWED_USERS", "ADMIN_USERS", "DEMO_MODE", "COOKIE_SECURE", "TRUST_PROXY"]
FIELDS = {key: dict(key=key, label=label, type=typ, default=default, hint=hint, group=gid)
          for gid, _, items in GROUPS for key, label, typ, default, hint in items}
_EMAIL = re.compile(r"^[^@\s,]+@[^@\s,]+\.[^@\s,]+$")


class ValidationError(ValueError):
    pass


def _fernet(data_dir):
    path = Path(data_dir) / KEYFILE
    if not path.exists():
        path.write_bytes(Fernet.generate_key())
        path.chmod(0o600)
    return Fernet(path.read_bytes().strip())


def load(data_dir):
    """Gespeicherte Änderungen aus der App (Geheimnisse entschlüsselt)."""
    path = Path(data_dir) / STORE
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for key, value in raw.items():
        if key not in FIELDS:
            continue
        if FIELDS[key]["type"] == "secret":
            try:
                value = _fernet(data_dir).decrypt(value.encode()).decode()
            except (InvalidToken, ValueError):
                continue  # Schlüsseldatei fehlt oder passt nicht: Wert aus der .env verwenden
        out[key] = value
    return out


def apply_overrides(data_dir, environ=os.environ):
    """Beim Start: gespeicherte Werte vor die .env setzen. Merkt sich die ursprünglichen .env-Werte."""
    overrides = load(data_dir)
    for key in FIELDS:
        environ.setdefault(f"_ENV_ORIGINAL_{key}", environ.get(key, ""))
    environ.update(overrides)
    return overrides


def env_value(key, environ=os.environ):
    """Der Wert aus der .env (ohne Änderung aus der App)."""
    return environ.get(f"_ENV_ORIGINAL_{key}", environ.get(key, ""))


def normalize(key, raw):
    """Prüft eine Eingabe und gibt sie als Text zurück, so wie sie in der Umgebung steht."""
    field = FIELDS[key]
    typ, value = field["type"], (raw or "").strip()
    if typ == "bool":
        return "true" if value in ("1", "true", "on", "yes") else "false"
    if typ == "int":
        if not re.fullmatch(r"-?\d+", value):
            raise ValidationError(f"{field['label']}: bitte eine ganze Zahl.")
        return value
    if typ == "float":
        value = value.replace(",", ".")
        try:
            float(value)
        except ValueError as exc:
            raise ValidationError(f"{field['label']}: bitte eine Zahl.") from exc
        return value
    if typ.startswith("select:"):
        allowed = typ.split(":", 1)[1].split("|")
        if value not in allowed:
            raise ValidationError(f"{field['label']}: ungültige Auswahl.")
        return value
    if typ == "emails":
        parts = [p for p in re.split(r"[,\s;]+", value) if p]
        bad = [p for p in parts if not _EMAIL.match(p)]
        if bad:
            raise ValidationError(f"{field['label']}: ungültige Adresse {', '.join(bad)}.")
        return ", ".join(parts)
    if typ == "url":
        if value and not re.match(r"^https?://[^\s]+$", value):
            raise ValidationError(f"{field['label']}: bitte mit http:// oder https:// beginnen.")
        return value.rstrip("/") if key == "BASE_URL" else value
    if key == "LEADS_CENTER" and value and not re.fullmatch(r"-?\d+(\.\d+)?,\s*-?\d+(\.\d+)?", value):
        raise ValidationError(f"{field['label']}: Format „54.6614,9.9311“.")
    if "\n" in value:
        raise ValidationError(f"{field['label']}: bitte einzeilig.")
    return value


def save(data_dir, changes, removals=()):
    """changes: {Schlüssel: Text}; removals: Schlüssel, die wieder aus der .env kommen sollen."""
    path = Path(data_dir) / STORE
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for key in removals:
        raw.pop(key, None)
    for key, value in changes.items():
        if FIELDS[key]["type"] == "secret":
            value = _fernet(data_dir).encrypt(value.encode()).decode()
        raw[key] = value
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def view(data_dir, environ=os.environ):
    """Für die Oberfläche: je Gruppe die Felder mit aktuellem Wert und Herkunft. Geheimnisse nur als „gesetzt“."""
    stored = load(data_dir)
    groups = []
    for gid, title, items in GROUPS:
        fields = []
        for key, *_ in items:
            f = dict(FIELDS[key])
            current = environ.get(key, "") if key not in stored else stored[key]
            f["source"] = "app" if key in stored else ("env" if env_value(key, environ) else "standard")
            f["is_set"] = bool(current)
            f["value"] = "" if f["type"] == "secret" else (current if current != "" or key in stored else f["default"])
            f["options"] = f["type"].split(":", 1)[1].split("|") if f["type"].startswith("select:") else []
            fields.append(f)
        groups.append({"id": gid, "title": title, "fields": fields})
    return groups


def _short(f):
    if f["type"] == "secret":
        return "gesetzt" if f["is_set"] else "fehlt"
    if f["type"] == "bool":
        return "an" if f["value"] in ("true", "1", "yes") else "aus"
    v = f["value"] or ("Umkreis" if f["key"] == "LEADS_REGION" else "")
    return v if len(v) <= 28 else v[:26] + "…"


def menu(data_dir, environ=os.environ):
    """Für die Menüs: je Bereich die Unterseiten mit Kurzfassung der Werte, „geändert“ und „fehlt“."""
    groups = {g["id"]: g for g in view(data_dir, environ)}
    out = []
    for gid, title, _ in GROUPS:
        fields = {f["key"]: f for f in groups[gid]["fields"]}
        pages = []
        for pid, ptitle, keys in PAGES[gid]:
            fs = [fields[k] for k in keys]
            missing = [f["label"] for f in fs if f["key"] in NEEDED and not f["is_set"]]
            pages.append({"id": pid, "title": ptitle, "fields": fs, "missing": missing,
                          "changed": any(f["source"] == "app" for f in fs),
                          "summary": " · ".join(x for x in (_short(f) for f in fs) if x) or "nicht gesetzt"})
        out.append({"id": gid, "title": title, "hint": GROUP_HINTS.get(gid, ""), "pages": pages,
                    "missing": [m for pg in pages for m in pg["missing"]],
                    "changed": any(pg["changed"] for pg in pages)})
    return out
