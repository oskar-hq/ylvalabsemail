"""Seiten „Einstellungen“ und „Software“ (Updates aus der Web-App)."""
import os
import signal
import threading
import time

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import agent, config_store, updater
from .main import SETTINGS, login_required

bp = Blueprint("settings", __name__)


def who():
    return g.account.get("address") or g.account.get("user") or "Team"


def is_admin(account):
    if account.get("demo"):
        return False
    admins = SETTINGS["ADMIN_USERS"]
    return not admins or (account.get("address") or account.get("user", "")).lower() in admins


def require_admin():
    if not is_admin(g.account):
        abort(403, "Nur für Admins (ADMIN_USERS in der .env).")


def restart_soon(environ):
    """Unter Gunicorn: Arbeitsprozess neu starten, damit alle Module die neuen Werte lesen."""
    if not environ.get("SERVER_SOFTWARE", "").startswith("gunicorn"):
        return False

    def _go():
        time.sleep(1.5)
        os.kill(os.getppid(), signal.SIGHUP)  # Gunicorn startet die Arbeitsprozesse neu
    threading.Thread(target=_go, daemon=True).start()
    return True


@bp.route("/einstellungen", methods=["GET", "POST"])
@login_required
def settings():
    data_dir = SETTINGS["DATA_DIR"]
    if request.method == "POST":
        require_admin()
        if agent.RUN.get("id"):
            flash("Gerade läuft ein Lauf. Bitte warten, bis er fertig ist, oder ihn stoppen.")
            return redirect(url_for("settings.settings"))
        stored = config_store.load(data_dir)
        changes, removals, errors = {}, [], []
        for key, field in config_store.FIELDS.items():
            if request.form.get(f"reset_{key}") == "1":
                if key in stored:
                    removals.append(key)
                continue
            if field["type"] == "secret":
                value = request.form.get(key, "")
                if value.strip():  # leer = unverändert lassen
                    changes[key] = value.strip()
                continue
            if field["type"] == "bool":
                raw = "true" if request.form.get(key) else "false"
            elif key not in request.form:
                continue
            else:
                raw = request.form.get(key, "")
            try:
                value = config_store.normalize(key, raw)
            except config_store.ValidationError as exc:
                errors.append(str(exc))
                continue
            current = stored.get(key, os.environ.get(key, ""))
            shown = current if current != "" or key in stored else field["default"]
            if value != shown:
                changes[key] = value
        if errors:
            for e in errors:
                flash(e)
            return redirect(url_for("settings.settings"))
        if not changes and not removals:
            flash("Keine Änderung.")
            return redirect(url_for("settings.settings"))
        config_store.save(data_dir, changes, removals)
        names = ", ".join(config_store.FIELDS[k]["label"] for k in [*changes, *removals])
        agent.log.info("Einstellungen geändert von %s: %s", who(), ", ".join([*changes, *removals]))
        if restart_soon(request.environ):
            return render_template("restarting.html", account=g.account, what=f"Gespeichert: {names}.")
        flash(f"Gespeichert: {names}. Wirksam nach einem Neustart der App.")
        return redirect(url_for("settings.settings"))
    return render_template("settings.html", account=g.account, groups=config_store.view(data_dir),
                           read_only={k: os.environ.get(k, "") for k in config_store.READ_ONLY},
                           admin=is_admin(g.account), admins_set=bool(SETTINGS["ADMIN_USERS"]),
                           demo=SETTINGS["DEMO_MODE"])


@bp.get("/einstellungen/software")
@login_required
def software():
    return render_template("software.html", account=g.account, up=updater.state(SETTINGS["CONTROL_DIR"]),
                           admin=is_admin(g.account), demo=SETTINGS["DEMO_MODE"])


@bp.get("/einstellungen/software/status.json")
@login_required
def software_status():
    return jsonify(updater.state(SETTINGS["CONTROL_DIR"]))


@bp.post("/einstellungen/software/<kind>")
@login_required
def software_request(kind):
    require_admin()
    control = SETTINGS["CONTROL_DIR"]
    if kind not in updater.KINDS:
        abort(404)
    if not updater.available(control):
        return jsonify(error="Der Update-Dienst ist auf diesem Server nicht eingerichtet."), 400
    if kind == "update" and agent.RUN.get("id"):
        return jsonify(error="Gerade läuft ein Lauf. Bitte warten, bis er fertig ist, oder ihn stoppen."), 409
    try:
        updater.request(control, kind, who())
    except OSError as exc:
        return jsonify(error=f"Der Austauschordner ist nicht beschreibbar ({exc}). Auf dem Server "
                             "„sudo bash deploy/install-updater.sh“ ausführen."), 500
    agent.log.info("Software-Auftrag „%s“ von %s", kind, who())
    return jsonify(ok=True, message="Wird nach Updates gesucht …" if kind == "check" else
                   "Update startet. Die App ist gleich kurz weg und kommt von selbst wieder.")
