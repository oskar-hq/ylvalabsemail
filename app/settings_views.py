"""Seiten „Einstellungen“ und „Software“ (Updates aus der Web-App)."""
import os
import signal
import threading
import time

from datetime import datetime

from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import agent, backups, config_store, firma, leaddb, startklar, updater
from .main import DB_PATH, SETTINGS, login_required

bp = Blueprint("settings", __name__)


def who():
    return g.account.get("address") or g.account.get("user") or "Team"


def is_admin(account):
    """Darf Einstellungen ändern, Updates einspielen und Sicherungen zurückspielen: Recht „Einstellungen: Verwalten“
    (Einstellungen → Rollen). Nur solange in der App noch niemand Personen verwalten darf, gilt ADMIN_USERS."""
    from . import firma
    from .leads_views import db
    if firma.verwalter(db()):
        return bool(account.get("team")) and firma.darf(db(), account["team"], "einstellungen", firma.VERWALTEN)
    admins = SETTINGS["ADMIN_USERS"]
    # Postfach-Anmeldung: die Adresse; Anmeldung mit Name: der Kurzname (finn) oder der volle Name aus ADMIN_USERS
    names = {(account.get(k) or "").lower() for k in ("address", "user", "team", "name")} - {""}
    return not admins or bool(names & set(admins))


def _log(art, text, daten=None):
    """Ins Firmen-Protokoll (Einstellungen → Protokoll): wer hat wann was geändert."""
    c = leaddb.connect(DB_PATH)
    try:
        firma.log(c, g.account.get("team") or who(), art, "system", None, text, daten)
    finally:
        c.close()


def require_admin():
    if not is_admin(g.account):
        abort(403, "Dafür brauchst du das Recht „Einstellungen: Verwalten“ (Einstellungen → Rollen und Rechte).")


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
        _log("einstellung", f"Einstellungen geändert: {names}.", {"geaendert": sorted(changes),
                                                                  "zurueckgesetzt": sorted(removals)})
        if restart_soon(request.environ):
            return render_template("restarting.html", account=g.account, what=f"Gespeichert: {names}.")
        flash(f"Gespeichert: {names}. Wirksam nach einem Neustart der App.")
        return redirect(back)
    from . import agenten
    from .leads_views import db
    agenten_offen = not agenten.einrichtung(db())["fertig"]
    return render_template("settings.html", account=g.account, menu=config_store.menu(data_dir), agenten_offen=agenten_offen,
                           admin=is_admin(g.account), admins_set=bool(SETTINGS["ADMIN_USERS"]),
                           up=updater.state(SETTINGS["CONTROL_DIR"]),
                           startklar=startklar.zusammenfassung(_startklar_punkte()))


def _startklar_punkte():
    from .leads_views import db
    return startklar.punkte(db(), url_for, host_backups=bool(SETTINGS["CONTROL_DIR"]))


@bp.get("/einstellungen/startklar")
@login_required
def startklar_seite():
    punkte = _startklar_punkte()
    return render_template("startklar.html", account=g.account, punkte=punkte,
                           summe=startklar.zusammenfassung(punkte))


def _group(gid):
    group = next((grp for grp in config_store.menu(SETTINGS["DATA_DIR"]) if grp["id"] == gid), None)
    if not group:
        abort(404)
    return group


@bp.post("/einstellungen/postfach/test")
@login_required
def mailbox_test():
    """„Verbindung testen“: meldet sich mit den gespeicherten Daten an, verschickt nichts."""
    return jsonify(results=[{"name": n, "ok": ok, "text": t} for n, ok, t in agent.test_mailbox()])


@bp.get("/einstellungen/zugang")
@login_required
def access():
    return render_template("settings_access.html", account=g.account,
                           read_only={k: os.environ.get(k, "") for k in config_store.READ_ONLY})


@bp.get("/einstellungen/<gid>")
@login_required
def group(gid):
    grp = _group(gid)
    if len(grp["pages"]) == 1:
        return redirect(url_for("settings.page", gid=gid, pid=grp["pages"][0]["id"]))
    return render_template("settings_group.html", account=g.account, grp=grp)


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
                           single=len(grp["pages"]) == 1)


@bp.get("/einstellungen/software")
@login_required
def software():
    return render_template("software.html", account=g.account, up=updater.state(SETTINGS["CONTROL_DIR"]),
                           admin=is_admin(g.account))


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
    _log("software", {"update": "Update gestartet.", "check": "Nach Updates gesucht."}.get(kind, f"Auftrag {kind}."))
    return jsonify(ok=True, message="Wird nach Updates gesucht …" if kind == "check" else
                   "Update startet. Die App ist gleich kurz weg und kommt von selbst wieder.")


# ---------------------------------------------------------------- Sicherung


def _aufraeumen_behalten(c):
    """Vorauswahl „behalten“: aus dem Link (?behalten=1,2,3), sonst Kunden und von Agents angelegte Leads."""
    roh = request.values.get("behalten")
    if roh is not None:
        return {int(x) for x in roh.replace(" ", "").split(",") if x.isdigit()}
    return {r[0] for r in c.execute("SELECT id FROM leads WHERE arbeitsbereich=ab() AND (stage='kunde' OR "
                                    "source_id LIKE 'agent:%')")}


@bp.get("/einstellungen/aufraeumen")
@login_required
def aufraeumen_seite():
    """Neustart: alte Leads aus der Anfangszeit entfernen. Wer Einstellungen verwaltet, wählt, was bleibt, und löscht
    mit einem Klick (vorher wird eine Sicherung im Datenordner abgelegt)."""
    from . import aufraeumen
    from .leads_views import db
    c = db()
    behalten = _aufraeumen_behalten(c)
    rows = [dict(r) for r in c.execute("SELECT id, name, city, stage, owner, updated_at FROM leads WHERE "
                                       "arbeitsbereich=ab() AND (stage IS NOT NULL OR id IN (%s)) ORDER BY "
                                       "stage='kunde' DESC, updated_at DESC" % ",".join(str(i) for i in behalten or {0}))]
    p = aufraeumen.plan(c, behalten)
    return render_template("aufraeumen.html", account=g.account, admin=is_admin(g.account), rows=rows,
                           behalten=behalten, plan=p, ohne_stufe=c.execute(
                               "SELECT COUNT(*) FROM leads WHERE arbeitsbereich=ab() AND stage IS NULL").fetchone()[0])


@bp.post("/einstellungen/aufraeumen")
@login_required
def aufraeumen_los():
    require_admin()
    from . import aufraeumen
    from .leads_views import db
    behalten = [int(x) for x in request.form.getlist("behalten") if x.isdigit()]
    if not behalten:
        flash("Nichts zum Behalten gewählt. Zur Sicherheit wird so nicht aufgeräumt.")
        return redirect(url_for("settings.aufraeumen_seite"))
    data_dir = SETTINGS["DATA_DIR"]
    name = f"vor-aufraeumen-{datetime.now():%Y%m%d-%H%M%S}.tar.gz"
    (data_dir / name).write_bytes(backups.create(data_dir))  # Stand vorher, falls doch etwas fehlt
    erg = aufraeumen.ausfuehren(db(), g.account.get("team") or who(), behalten)
    flash(f"Aufgeräumt: {erg['leads']} Leads, {erg['projekte']} Projekte, {erg['aufgaben']} Aufgaben entfernt; "
          f"{erg['bleiben']} Leads bleiben. Sicherung vorher: {name} im Datenordner.")
    return redirect(url_for("crm.board"))


@bp.get("/einstellungen/sicherung")
@login_required
def backup_page():
    data_dir = SETTINGS["DATA_DIR"]
    return render_template("backup.html", account=g.account, admin=is_admin(g.account),
                           history=backups.history(data_dir), can_undo=backups.can_undo(data_dir),
                           pending=backups.pending(data_dir), host_backups=bool(SETTINGS["CONTROL_DIR"]))


@bp.get("/einstellungen/sicherung/herunterladen")
@login_required
def backup_download():
    require_admin()
    data = backups.create(SETTINGS["DATA_DIR"])
    name = f"ylva-mailer_{datetime.now():%Y-%m-%d_%H%M}.tar.gz"
    agent.log.info("Sicherung heruntergeladen von %s", who())
    _log("sicherung", "Sicherung heruntergeladen.")
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
    _log("sicherung", f"Sicherung „{file.filename[:120]}“ zum Zurückspielen bereitgelegt.")
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
