"""Das Team: Finn Adams, Hamza Al-Shebli und Oskar Jacobsen.

Jede Person meldet sich mit ihrem Namen und einem eigenen Passwort an (gespeichert nur als scrypt-Hash) und hat
eigene Einstellungen: Gruß, Unterschrift, Vorlage und Design der Mails. Verschickt wird für alle über das
gemeinsame Akquise-Postfach, mit dem Namen der Person als Absender.
"""
import json

from werkzeug.security import check_password_hash, generate_password_hash

from . import leaddb

MIN_PASSWORD = 8
DEFAULTS = {"gruss": "Viele Grüße", "signatur": "", "vorlage": "", "design": None, "einstellungen": {}}
# Was jede Person für sich anders einstellen kann (leer = Team-Standard aus den Einstellungen).
# (Schlüssel, Bezeichnung, Typ, kleinster Wert, größter Wert)
PERSONAL = [
    ("LEADS_RULE_MIN", "Mindestpunktzahl, ab der ein Betrieb passt", int, 0, 100),
    ("LEADS_RUN_CHECK", "Betriebe pro Lauf bewerten", int, 0, 1000),
    ("LEADS_RUN_TOP", "Davon die besten ins CRM", int, 0, 200),
    ("LEADS_RUN_DRAFTS", "KI-Entwürfe pro Lauf", int, 0, 50),
    ("LEADS_RUN_BUDGET_EUR", "KI-Budget pro Lauf (€)", float, 0, 100),
    ("MY_MONTHLY_BUDGET_EUR", "Mein KI-Budget pro Monat (€)", float, 0, 1000),
]
PERSONAL_KEYS = {k for k, *_ in PERSONAL}


class TeamError(ValueError):
    pass


def members(c):
    return [dict(r) | {"has_password": bool(r["pw_hash"])} for r in
            c.execute("SELECT key, name, pw_hash, pw_set_at FROM team ORDER BY sort, name")]


def get(c, key):
    row = c.execute("SELECT * FROM team WHERE key=?", (key or "",)).fetchone()
    return dict(row) if row else None


def names(c):
    return {m["key"]: m["name"] for m in members(c)}


def check_password(c, key, password):
    member = get(c, key)
    if not member or not member["pw_hash"]:
        return False
    return check_password_hash(member["pw_hash"], password or "")


def set_password(c, key, password):
    if not get(c, key):
        raise TeamError("Diese Person gibt es nicht.")
    if len(password or "") < MIN_PASSWORD:
        raise TeamError(f"Das Passwort braucht mindestens {MIN_PASSWORD} Zeichen.")
    if password.strip() != password:
        raise TeamError("Bitte ohne Leerzeichen am Anfang oder Ende.")
    c.execute("UPDATE team SET pw_hash=?, pw_set_at=? WHERE key=?", (generate_password_hash(password), leaddb.now(), key))
    c.commit()


def settings(c, key, org_name="Ylva Labs"):
    """Einstellungen der Person mit Standardwerten. Unterschrift ohne eigene Angabe: Name und Firma."""
    member = get(c, key)
    if not member:
        return dict(DEFAULTS)
    try:
        stored = json.loads(member["settings"] or "{}")
    except ValueError:
        stored = {}
    out = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
    if not (out["signatur"] or "").strip():
        out["signatur"] = f"{member['name']}\n{org_name}"
    return out


def save_settings(c, key, **values):
    member = get(c, key)
    if not member:
        raise TeamError("Diese Person gibt es nicht.")
    stored = json.loads(member["settings"] or "{}")
    stored.update({k: v for k, v in values.items() if k in DEFAULTS})
    c.execute("UPDATE team SET settings=? WHERE key=?", (json.dumps(stored, ensure_ascii=False), key))
    c.commit()


def prefs(c, key):
    """Eigene Werte der Person (nur die gesetzten), passend getypt."""
    raw = settings(c, key).get("einstellungen") or {} if key else {}
    out = {}
    for k, _label, typ, lo, hi in PERSONAL:
        if k in raw:
            try:
                v = typ(raw[k])
            except (TypeError, ValueError):
                continue
            if lo <= v <= hi:
                out[k] = v
    return out


def parse_prefs(form):
    """Formular → eigene Werte. Leeres Feld = Team-Standard."""
    out = {}
    for k, label, typ, lo, hi in PERSONAL:
        value = (form.get(k) or "").strip().replace(",", ".")
        if not value:
            continue
        try:
            v = typ(float(value)) if typ is int and float(value).is_integer() else typ(value)
        except ValueError as exc:
            raise TeamError(f"{label}: bitte eine Zahl.") from exc
        if not lo <= v <= hi:
            raise TeamError(f"{label}: zwischen {lo} und {hi}.")
        out[k] = v
    return out


def sender(c, key, org_name="Ylva Labs"):
    """Absender einer Mail: Anzeigename (z. B. „Finn Adams | Ylva Labs“), Name, Gruß und Unterschrift."""
    member = get(c, key)
    if not member:
        return {"key": None, "name": "", "display": org_name, "gruss": DEFAULTS["gruss"], "signatur": org_name}
    s = settings(c, key, org_name)
    return {"key": key, "name": member["name"], "display": f"{member['name']} | {org_name}",
            "gruss": s["gruss"], "signatur": s["signatur"]}


def signature(snd):
    return "\n".join(x for x in (snd["gruss"], snd["signatur"]) if x and x.strip())


def stats(c):
    """Zahlen je Person und gesamt: im CRM, angeschrieben, Antworten, Interesse, Gespräche, Kunden, Umsatz, Mails."""
    people = members(c)
    rows = {r["owner"]: dict(r) for r in c.execute("""
        SELECT owner,
               SUM(stage IS NOT NULL) AS im_crm,
               SUM(sent_at IS NOT NULL) AS angeschrieben,
               SUM(replied_at IS NOT NULL OR stage IN ('antwort','gespraech','angebot','kunde')) AS antworten,
               SUM(interest='qualified') AS interesse,
               SUM(stage IN ('gespraech','angebot')) AS gespraeche,
               SUM(stage='kunde') AS kunden,
               SUM(CASE WHEN stage='kunde' THEN IFNULL(deal_eur,0) ELSE 0 END) AS umsatz
        FROM leads GROUP BY owner""")}
    by_name = {m["name"]: m["key"] for m in people}
    mails = {}
    for r in c.execute("SELECT sent_by, COUNT(*) n FROM messages WHERE direction='out' GROUP BY sent_by"):
        mails[by_name.get(r["sent_by"], r["sent_by"])] = r["n"]
    keys = ("im_crm", "angeschrieben", "antworten", "interesse", "gespraeche", "kunden", "umsatz")

    def card(key, name):
        r = rows.get(key) or {}
        d = {k: (r.get(k) or 0) for k in keys}
        d.update(key=key, name=name, mails=mails.get(key, 0))
        d["quote"] = round(100 * d["antworten"] / d["angeschrieben"]) if d["angeschrieben"] else None
        return d
    out = [card(m["key"], m["name"]) for m in people]
    total = {k: sum((r.get(k) or 0) for r in rows.values()) for k in keys}
    total.update(key=None, name="Gesamt", mails=sum(mails.values()))
    total["quote"] = round(100 * total["antworten"] / total["angeschrieben"]) if total["angeschrieben"] else None
    total["ohne"] = (rows.get(None) or {}).get("im_crm") or 0
    return out, total
