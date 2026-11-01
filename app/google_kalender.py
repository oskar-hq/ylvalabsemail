"""Google Kalender: Verbinden per OAuth (jede Person ihr eigenes Konto), frei/belegt lesen, Termine mit Meet-Link
anlegen, ändern, absagen. Ohne Zusatzbibliothek (urllib), damit nichts Neues installiert werden muss.

Berechtigungen (genau die zwei aus docs/FIRMEN-OS.md, 4.3): frei/belegt und Termine. Client-ID und Clientschlüssel
stehen in den Einstellungen (Anbindungen → Google), nicht in der .env. Die Rücksprung-Adresse ist fest
/kalender/google/zurueck, so ist sie im Google-Projekt eingetragen.
"""
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/calendar/v3"
SCOPES = ["https://www.googleapis.com/auth/calendar.events.freebusy",
          "https://www.googleapis.com/auth/calendar.events"]
RUECKSPRUNG = "/kalender/google/zurueck"
TIMEOUT = 20


class GoogleError(Exception):
    """Fehler von Google. `abgelaufen` heißt: Die Verbindung gilt nicht mehr, die Person muss neu verbinden."""

    def __init__(self, text, abgelaufen=False):
        super().__init__(text)
        self.abgelaufen = abgelaufen


def _http(method, url, form=None, body=None, token=None):
    """Eine Anfrage an Google, Antwort als dict. In Tests wird diese Funktion ersetzt."""
    headers = {"Accept": "application/json"}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        try:
            info = json.loads(exc.read() or b"{}")
        except ValueError:
            info = {}
        err = info.get("error")
        text = (err.get("message") if isinstance(err, dict) else info.get("error_description") or err) or exc.reason
        raise GoogleError(f"Google: {text} ({exc.code})", abgelaufen=err == "invalid_grant" or exc.code == 401) \
            from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GoogleError(f"Google nicht erreichbar: {exc}") from exc


def neuer_state():
    return secrets.token_urlsafe(24)


def auth_url(client_id, redirect_uri, state):
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(SCOPES),
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state})


def code_tauschen(client_id, secret, code, redirect_uri):
    data = _http("POST", TOKEN_URL, form={"code": code, "client_id": client_id, "client_secret": secret,
                                          "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
    if not data.get("refresh_token"):
        raise GoogleError("Google hat keinen dauerhaften Zugang geschickt. Bitte unter myaccount.google.com → "
                          "Sicherheit die App entfernen und neu verbinden.")
    fehlend = [s for s in SCOPES if s not in (data.get("scope") or "").split()]
    if fehlend:
        raise GoogleError("Beim Verbinden fehlen Häkchen. Bitte neu verbinden und beide Kalender-Rechte erlauben.")
    return data


def access_token(client_id, secret, refresh_token):
    data = _http("POST", TOKEN_URL, form={"client_id": client_id, "client_secret": secret,
                                          "refresh_token": refresh_token, "grant_type": "refresh_token"})
    return data["access_token"]


def widerrufen(refresh_token):
    try:
        _http("POST", REVOKE_URL, form={"token": refresh_token})
    except GoogleError:
        pass  # schon ungültig: auch gut


def freebusy(token, von_utc, bis_utc):
    """Belegte Zeiten im Hauptkalender als Liste von (start, ende) in RFC3339 (UTC)."""
    data = _http("POST", f"{API}/freeBusy", token=token,
                 body={"timeMin": von_utc, "timeMax": bis_utc, "items": [{"id": "primary"}]})
    cal = (data.get("calendars") or {}).get("primary") or {}
    if cal.get("errors"):
        raise GoogleError(f"Google: Kalender nicht lesbar ({cal['errors'][0].get('reason')})")
    return [(b["start"], b["end"]) for b in cal.get("busy", [])]


def _event(termin, gaeste, zeitzone, meet):
    body = {
        "summary": termin["titel"],
        "description": termin.get("beschreibung_google") or "",
        "start": {"dateTime": termin["start"] + ":00" if len(termin["start"]) == 16 else termin["start"],
                  "timeZone": zeitzone},
        "end": {"dateTime": termin["ende"] + ":00" if len(termin["ende"]) == 16 else termin["ende"],
                "timeZone": zeitzone},
        "reminders": {"useDefault": True},
        "extendedProperties": {"private": {"ylva_termin": str(termin["id"])}},
    }
    if termin.get("ort"):
        body["location"] = termin["ort"]
    if gaeste:
        body["attendees"] = [{"email": e} for e in gaeste]
    if meet:
        body["conferenceData"] = {"createRequest": {"requestId": f"ylva-{termin['id']}-{secrets.token_hex(4)}",
                                                    "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
    return body


def _meet_link(data):
    if data.get("hangoutLink"):
        return data["hangoutLink"]
    for ep in (data.get("conferenceData") or {}).get("entryPoints", []):
        if ep.get("entryPointType") == "video":
            return ep.get("uri")
    return None


def event_anlegen(token, termin, gaeste=(), zeitzone="Europe/Berlin", meet=False, einladen=False):
    """Termin im eigenen Kalender anlegen. Mit Gästen und einladen=True schickt Google die Einladung."""
    q = urllib.parse.urlencode({"conferenceDataVersion": 1 if meet else 0,
                                "sendUpdates": "all" if einladen and gaeste else "none"})
    data = _http("POST", f"{API}/calendars/primary/events?{q}", token=token,
                 body=_event(termin, list(gaeste), zeitzone, meet))
    return {"id": data["id"], "meet_link": _meet_link(data), "link": data.get("htmlLink")}


def event_aendern(token, event_id, termin, gaeste=(), zeitzone="Europe/Berlin", einladen=False):
    q = urllib.parse.urlencode({"sendUpdates": "all" if einladen and gaeste else "none"})
    body = _event(termin, list(gaeste), zeitzone, meet=False)
    body.pop("extendedProperties", None)
    data = _http("PATCH", f"{API}/calendars/primary/events/{urllib.parse.quote(event_id)}?{q}", token=token,
                 body=body)
    return {"id": data.get("id", event_id), "meet_link": _meet_link(data)}


def event_loeschen(token, event_id, einladen=False):
    q = urllib.parse.urlencode({"sendUpdates": "all" if einladen else "none"})
    try:
        _http("DELETE", f"{API}/calendars/primary/events/{urllib.parse.quote(event_id)}?{q}", token=token)
    except GoogleError as exc:
        if "(404)" not in str(exc) and "(410)" not in str(exc):
            raise
