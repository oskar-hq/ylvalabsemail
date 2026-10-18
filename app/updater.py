"""Updates aus der Web-App: Die App legt nur einen Auftrag in einen Austauschordner, der Update-Dienst auf dem Server
(deploy/update.sh, eingerichtet von deploy/install-updater.sh) führt ihn aus und schreibt den Stand zurück.

Die App selbst bekommt dadurch keinen Zugriff auf den Server oder auf Docker. Sie kann nur zwei feste Aufträge geben:
„nach Updates suchen“ und „Update einspielen“. Was eingespielt wird, kommt immer aus dem GitHub-Repository.

Dateien im Austauschordner:
    check-requested / update-requested   Auftrag der App (Inhalt: wer, wann)
    version.json                          Stand: aktuelle Version, verfügbare Änderungen, Zeitpunkt der Prüfung
    status.json                           letztes Update: läuft / fertig / fehlgeschlagen / zurückgerollt, mit Protokoll
"""
import json
import time
from datetime import datetime

KINDS = ("check", "update")
STALE_HOURS = 30  # so lange darf die letzte Prüfung her sein, sonst läuft der Dienst vermutlich nicht


def _read(control, name):
    try:
        return json.loads((control / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def available(control):
    return bool(control) and control.is_dir()


def request(control, kind, who):
    if kind not in KINDS:
        raise ValueError(kind)
    path = control / f"{kind}-requested"
    tmp = control / f".{kind}-requested.tmp"
    tmp.write_text(json.dumps({"by": who, "at": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    tmp.replace(path)


def state(control):
    """Alles, was die Seite „Software“ anzeigt."""
    if not available(control):
        return {"available": False}
    version = _read(control, "version.json") or {}
    status = _read(control, "status.json") or {}
    checked = version.get("checked_at")
    age_h = None
    if checked:
        try:
            age_h = (time.time() - datetime.fromisoformat(checked).timestamp()) / 3600
        except ValueError:
            age_h = None
    return {
        "available": True,
        "version": version,
        "status": status,
        "pending": {k: (control / f"{k}-requested").exists() for k in KINDS},
        "service_ok": age_h is not None and age_h < STALE_HOURS,
        "checked_hours_ago": round(age_h, 1) if age_h is not None else None,
    }
