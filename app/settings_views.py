"""Seiten „Einstellungen“ und „Software“ (Updates aus der Web-App)."""
import os
import signal
import threading
import time

from datetime import datetime

from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import agent, backups, config_store, leaddb, updater
from .main import DB_PATH, SETTINGS, login_required

bp = Blueprint("settings", __name__)


def who():
    return g.account.get("address") or g.account.get("user") or "Team"


def is_admin(account):
    if account.get("demo"):
        return False
    admins = SETTINGS["ADMIN_USERS"]
    # Postfach-Anmeldung: die Adresse; Anmeldung mit Name: der Kurzname (finn) oder der volle Name aus ADMIN_USERS
    names = {(account.get(k) or "").lower() for k in ("address", "user", "team", "name")} - {""}
    return not admins or bool(names & set(admins))


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
        # Eine Unterseite schickt nur ihre eigenen Werte mit (sonst würden fehlende Häkchen als „aus“ gelten)
        on_page = set(request.form.getlist("keys")) & set(config_store.FIELDS) or set(config_store.FIELDS)
        back = request.form.get("back", "")
        back = back if back.startswith("/einstellungen") else url_for("settings.settings")
        for key, field in config_store.FIELDS.items():
            if key not in on_page:
                continue
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
            return redirect(back)
        if not changes and not removals:
            flash("Keine Änderung.")
            return redirect(back)
        config_store.save(data_dir, changes, removals)
        mailbox_keys = {"OUTREACH_USER", "OUTREACH_PASSWORD", "SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "IMAP_HOST",
                        "IMAP_PORT", "FROM_ADDRESS"}
        if mailbox_keys & {*changes, *removals}:
            # alte Fehlermeldung weg, nach dem Neustart gleich neu abrufen statt erst in zehn Minuten
            c = leaddb.connect(DB_PATH)
            try:
                leaddb.kv_set(c, "inbox_status", None)
                leaddb.kv_set(c, "last_inbox", 0)
                leaddb.kv_set(c, "request:inbox", True)
            finally:
                c.close()
        names = ", ".join(config_store.FIELDS[k]["label"] for k in [*changes, *removals])
        agent.log.info("Einstellungen geändert von %s: %s", who(), ", ".join([*changes, *removals]))
        if restart_soon(request.environ):
            return render_template("restarting.html", account=g.account, what=f"Gespeichert: {names}.")
        flash(f"Gespeichert: {names}. Wirksam nach einem Neustart der App.")
        return redirect(back)
    return render_template("settings.html", account=g.account, menu=config_store.menu(data_dir),
                           admin=is_admin(g.account), admins_set=bool(SETTINGS["ADMIN_USERS"]),
                           up=updater.state(SETTINGS["CONTROL_DIR"]), demo=SETTINGS["DEMO_MODE"])


def _group(gid):
    group = next((grp for grp in config_store.menu(SETTINGS["DATA_DIR"]) if grp["id"] == gid), None)
    if not group:
        abort(404)
    return group


@bp.post("/einstellungen/postfach/test")
@login_required
def mailbox_test():
    """„Verbindung testen“: meldet sich mit den gespeicherten Daten an, verschickt nichts."""
    if g.account.get("demo"):
        return jsonify(results=[{"name": "Demo", "ok": True, "text": "Im Demo-Modus wird kein Postfach geprüft."}])
    return jsonify(results=[{"name": n, "ok": ok, "text": t} for n, ok, t in agent.test_mailbox()])


@bp.get("/einstellungen/zugang")
@login_required
def access():
    return render_template("settings_access.html", account=g.account,
                           read_only={k: os.environ.get(k, "") for k in config_store.READ_ONLY},
                           demo=SETTINGS["DEMO_MODE"])


@bp.get("/einstellungen/<gid>")
@login_required
def group(gid):
    grp = _group(gid)
    if len(grp["pages"]) == 1:
        return redirect(url_for("settings.page", gid=gid, pid=grp["pages"][0]["id"]))
    return render_template("settings_group.html", account=g.account, grp=grp, demo=SETTINGS["DEMO_MODE"])


@bp.get("/einstellungen/<gid>/<pid>")
@login_required
def page(gid, pid):
    grp = _group(gid)
    pg = next((pg for pg in grp["pages"] if pg["id"] == pid), None)
    if not pg:
        abort(404)
    from . import team
    return render_template("settings_page.html", account=g.account, grp=grp, pg=pg, admin=is_admin(g.account),
                           personal_keys=team.PERSONAL_KEYS,
                           single=len(grp["pages"]) == 1, demo=SETTINGS["DEMO_MODE"])


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


# ---------------------------------------------------------------- Sicherung


@bp.get("/einstellungen/sicherung")
@login_required
def backup_page():
    data_dir = SETTINGS["DATA_DIR"]
    return render_template("backup.html", account=g.account, admin=is_admin(g.account), demo=SETTINGS["DEMO_MODE"],
                           history=backups.history(data_dir), can_undo=backups.can_undo(data_dir),
                           pending=backups.pending(data_dir), host_backups=bool(SETTINGS["CONTROL_DIR"]))


@bp.get("/einstellungen/sicherung/herunterladen")
@login_required
def backup_download():
    require_admin()
    data = backups.create(SETTINGS["DATA_DIR"])
    name = f"ylva-mailer_{datetime.now():%Y-%m-%d_%H%M}.tar.gz"
    agent.log.info("Sicherung heruntergeladen von %s", who())
    return Response(data, mimetype="application/gzip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


def _restart_or_flash(what):
    if restart_soon(request.environ):
        return render_template("restarting.html", account=g.account, what=what)
    flash(f"{what} Wird beim nächsten Neustart der App eingesetzt.")
    return redirect(url_for("settings.backup_page"))


@bp.post("/einstellungen/sicherung/zurueckspielen")
@login_required
def backup_restore():
    require_admin()
    request.max_content_length = backups.MAX_BYTES
    if agent.RUN.get("id"):
        flash("Gerade läuft ein Lauf. Bitte warten, bis er fertig ist, oder ihn stoppen.")
        return redirect(url_for("settings.backup_page"))
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Bitte eine Sicherungsdatei auswählen.")
        return redirect(url_for("settings.backup_page"))
    if request.form.get("confirm") != "1":
        flash("Bitte bestätigen, dass die aktuellen Daten ersetzt werden.")
        return redirect(url_for("settings.backup_page"))
    try:
        info = backups.stage(SETTINGS["DATA_DIR"], file.stream)
    except backups.BackupError as exc:
        flash(f"Nicht zurückgespielt: {exc}")
        return redirect(url_for("settings.backup_page"))
    backups.mark(SETTINGS["DATA_DIR"], who(), file.filename[:120])
    agent.log.info("Sicherung %s wird zurückgespielt (von %s)", file.filename, who())
    return _restart_or_flash(f"Sicherung „{file.filename}“ geprüft ({info['leads']} Betriebe)."
                             + (" Enthält auch die Einstellungen aus der App." if info["settings"] else ""))


@bp.post("/einstellungen/sicherung/rueckgaengig")
@login_required
def backup_undo():
    require_admin()
    try:
        backups.stage_undo(SETTINGS["DATA_DIR"])
    except backups.BackupError as exc:
        flash(str(exc))
        return redirect(url_for("settings.backup_page"))
    backups.mark(SETTINGS["DATA_DIR"], who(), "Stand vor dem letzten Zurückspielen", kind="undo")
    return _restart_or_flash("Der Stand vor dem letzten Zurückspielen wird wiederhergestellt.")
