"""Tests für Einstellungen in der App und Updates aus der App. Aufruf: python -m unittest discover tests"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

if "DATA_DIR" not in os.environ:  # allein gestartet; sonst setzt test_leads die Umgebung
    os.environ.update(DATA_DIR=tempfile.mkdtemp(), SMTP_HOST="smtp.example.com", LEADS_WORKER="false",
                      DEMO_MODE="true")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import io  # noqa: E402
import sqlite3  # noqa: E402
import tarfile  # noqa: E402

from app import backups, config_store, main, updater  # noqa: E402


class ConfigStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_normalize(self):
        self.assertEqual(config_store.normalize("SMTP_PORT", " 465 "), "465")
        self.assertEqual(config_store.normalize("EUR_PER_USD", "0,9"), "0.9")
        self.assertEqual(config_store.normalize("OUTREACH_EMAIL", "on"), "true")
        self.assertEqual(config_store.normalize("NOTIFY_EMAILS", "a@x.de; b@y.de"), "a@x.de, b@y.de")
        self.assertEqual(config_store.normalize("BASE_URL", "https://mail.ylvalabs.de/"), "https://mail.ylvalabs.de")
        self.assertEqual(config_store.normalize("LEADS_REGION", ""), "")
        for key, bad in [("SMTP_PORT", "abc"), ("NOTIFY_EMAILS", "kein-at"), ("BASE_URL", "mail.ylvalabs.de"),
                         ("AI_EFFORT", "max"), ("LEADS_CENTER", "Kappeln"), ("ORG_NAME", "zwei\nZeilen")]:
            with self.assertRaises(config_store.ValidationError, msg=key):
                config_store.normalize(key, bad)

    def test_secret_is_encrypted_and_overrides_env(self):
        config_store.save(self.dir, {"OUTREACH_PASSWORD": "geheim123", "ORG_NAME": "Ylva Test"})
        raw = (Path(self.dir) / config_store.STORE).read_text()
        self.assertNotIn("geheim123", raw)
        self.assertEqual(oct((Path(self.dir) / config_store.STORE).stat().st_mode & 0o777), "0o600")
        environ = {"ORG_NAME": "aus env", "OUTREACH_PASSWORD": "alt"}
        config_store.apply_overrides(self.dir, environ)
        self.assertEqual(environ["OUTREACH_PASSWORD"], "geheim123")
        self.assertEqual(environ["ORG_NAME"], "Ylva Test")
        self.assertEqual(config_store.env_value("ORG_NAME", environ), "aus env")
        config_store.save(self.dir, {}, removals=["ORG_NAME"])
        self.assertNotIn("ORG_NAME", config_store.load(self.dir))
        self.assertEqual(config_store.load(self.dir)["OUTREACH_PASSWORD"], "geheim123")

    def test_view_hides_secrets(self):
        config_store.save(self.dir, {"ANTHROPIC_API_KEY": "sk-ant-xyz"})
        fields = {f["key"]: f for g in config_store.view(self.dir, {}) for f in g["fields"]}
        self.assertEqual(fields["ANTHROPIC_API_KEY"]["value"], "")
        self.assertTrue(fields["ANTHROPIC_API_KEY"]["is_set"])
        self.assertEqual(fields["ANTHROPIC_API_KEY"]["source"], "app")
        self.assertEqual(fields["SMTP_PORT"]["value"], "587")  # Vorgabe
        self.assertNotIn("ALLOWED_DOMAINS", fields)  # Zugang nur in der .env


class UpdaterTest(unittest.TestCase):
    def test_request_and_state(self):
        control = Path(tempfile.mkdtemp())
        self.assertFalse(updater.state(None)["available"])
        updater.request(control, "update", "oskar@ylvalabs.de")
        self.assertEqual(json.loads((control / "update-requested").read_text())["by"], "oskar@ylvalabs.de")
        st = updater.state(control)
        self.assertTrue(st["pending"]["update"])
        self.assertFalse(st["service_ok"])  # noch nie gemeldet
        (control / "version.json").write_text(json.dumps({"checked_at": "2999-01-01T00:00:00", "behind": 2}))
        self.assertEqual(updater.state(control)["version"]["behind"], 2)
        with self.assertRaises(ValueError):
            updater.request(control, "rm -rf", "x")


class BackupsTest(unittest.TestCase):
    def make_data(self, name):
        d = Path(tempfile.mkdtemp())
        c = sqlite3.connect(d / "mailer.db")
        c.execute("CREATE TABLE leads (id INTEGER PRIMARY KEY, name TEXT)")
        c.execute("INSERT INTO leads (name) VALUES (?)", (name,))
        c.commit()
        c.close()
        (d / ".secret_key").write_text("geheim-" + name)
        (d / "uploads").mkdir()
        (d / "uploads" / "bild.png").write_bytes(b"\x89PNG")
        config_store.save(d, {"ORG_NAME": name, "OUTREACH_PASSWORD": "pw-" + name})
        return d

    def names(self, d):
        c = sqlite3.connect(d / "mailer.db")
        try:
            return [r[0] for r in c.execute("SELECT name FROM leads")]
        finally:
            c.close()

    def test_roundtrip_and_undo(self):
        alt, neu = self.make_data("Alt"), self.make_data("Neu")
        archive = backups.create(alt)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            self.assertIn("data/settings.json", tar.getnames())
        info = backups.stage(neu, io.BytesIO(archive))
        self.assertEqual((info["leads"], info["settings"]), (1, True))
        self.assertTrue(backups.pending(neu))
        self.assertEqual(self.names(neu), ["Neu"])  # erst beim Start eingesetzt
        backups.mark(neu, "oskar@ylvalabs.de", "sicherung.tar.gz")
        self.assertTrue(backups.apply_pending(neu))
        self.assertEqual(self.names(neu), ["Alt"])
        self.assertEqual(config_store.load(neu)["OUTREACH_PASSWORD"], "pw-Alt")  # Schlüssel kam mit
        self.assertEqual(backups.history(neu)[0]["by"], "oskar@ylvalabs.de")
        self.assertFalse(backups.pending(neu))
        backups.stage_undo(neu)
        backups.apply_pending(neu)
        self.assertEqual(self.names(neu), ["Neu"])
        self.assertEqual(config_store.load(neu)["ORG_NAME"], "Neu")

    def test_rejects_bad_archives(self):
        d = self.make_data("X")
        for content, msg in [(b"kein tar", "keine gültige"), (self.tar({"data/../../etc/passwd": b"x"}), "Unerwartet"),
                             (self.tar({"data/notes.txt": b"x"}), "Unerwartet"),
                             (self.tar({"data/vapid_private.pem": b"x"}), "fehlt die Datenbank"),
                             (self.tar({"data/mailer.db": b"kaputt"}), "nicht lesbar")]:
            with self.assertRaisesRegex(backups.BackupError, msg):
                backups.stage(d, io.BytesIO(content))
            self.assertFalse(backups.pending(d))
        self.assertEqual(self.names(d), ["X"])

    def tar(self, files):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, data in files.items():
                ti = tarfile.TarInfo(name)
                ti.size = len(data)
                tar.addfile(ti, io.BytesIO(data))
        return buf.getvalue()


class SettingsViewsTest(unittest.TestCase):
    def setUp(self):
        self.client = main.app.test_client()
        self.client.get("/login")
        with self.client.session_transaction() as sess:
            sess["sid"] = "test-admin"
            sess["csrf"] = "tok"
        main._sessions["test-admin"] = {"user": "oskar@ylvalabs.de", "password": "x", "since": 9e18,
                                        "address": "oskar@ylvalabs.de"}
        self.addCleanup(main._sessions.pop, "test-admin", None)
        self.store = Path(main.SETTINGS["DATA_DIR"]) / config_store.STORE
        self.addCleanup(lambda: self.store.unlink(missing_ok=True))

    def post(self, url, data=None):
        return self.client.post(url, data={**(data or {}), "_csrf": "tok"}, headers={"X-CSRF-Token": "tok"})

    def test_page_and_save(self):
        html = self.client.get("/einstellungen").get_data(as_text=True)
        self.assertIn("Postfach und Versand", html)
        self.assertIn("Speichern und neu starten", html)
        form = {"ORG_NAME": "Ylva Labs GmbH", "OUTREACH_PASSWORD": "neu-geheim", "SMTP_PORT": "465"}
        r = self.post("/einstellungen", form)
        self.assertEqual(r.status_code, 302)  # kein Gunicorn im Test: kein Neustart, nur Hinweis
        stored = config_store.load(main.SETTINGS["DATA_DIR"])
        self.assertEqual((stored["ORG_NAME"], stored["OUTREACH_PASSWORD"], stored["SMTP_PORT"]),
                         ("Ylva Labs GmbH", "neu-geheim", "465"))
        self.assertNotIn("OUTREACH_EMAIL", stored)  # Häkchen-Felder nicht mitgeschickt: Vorgabe false, keine Änderung
        r = self.post("/einstellungen", {"SMTP_PORT": "abc"})
        self.assertIn("ganze Zahl", self.client.get("/einstellungen").get_data(as_text=True))
        r = self.post("/einstellungen", {"reset_ORG_NAME": "1"})
        self.assertNotIn("ORG_NAME", config_store.load(main.SETTINGS["DATA_DIR"]))

    def test_backup_pages(self):
        self.assertIn("Sicherung herunterladen", self.client.get("/einstellungen/sicherung").get_data(as_text=True))
        r = self.client.get("/einstellungen/sicherung/herunterladen")
        self.assertEqual(r.mimetype, "application/gzip")
        self.assertIn("attachment", r.headers["Content-Disposition"])
        archive = r.get_data()
        r = self.client.post("/einstellungen/sicherung/zurueckspielen", content_type="multipart/form-data",
                             data={"_csrf": "tok", "file": (io.BytesIO(archive), "s.tar.gz")})
        self.assertIn("bestätigen", self.client.get("/einstellungen/sicherung").get_data(as_text=True))
        self.assertFalse(backups.pending(main.SETTINGS["DATA_DIR"]))
        r = self.client.post("/einstellungen/sicherung/zurueckspielen", content_type="multipart/form-data",
                             data={"_csrf": "tok", "confirm": "1", "file": (io.BytesIO(archive), "s.tar.gz")})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(backups.pending(main.SETTINGS["DATA_DIR"]))
        self.assertIn("bereitgelegt", self.client.get("/einstellungen/sicherung").get_data(as_text=True))
        import shutil
        shutil.rmtree(Path(main.SETTINGS["DATA_DIR"]) / backups.PENDING)
        main._sessions["test-admin"]["demo"] = True
        self.assertEqual(self.client.get("/einstellungen/sicherung/herunterladen").status_code, 403)

    def test_restart_only_under_gunicorn(self):
        from app import settings_views
        with mock.patch.object(settings_views.os, "kill") as kill, mock.patch.object(settings_views.time, "sleep"):
            self.assertFalse(settings_views.restart_soon({"SERVER_SOFTWARE": "Werkzeug/3"}))
            self.assertTrue(settings_views.restart_soon({"SERVER_SOFTWARE": "gunicorn/26.2.0"}))
            for t in __import__("threading").enumerate():
                if t.daemon and t.name != "MainThread":
                    t.join(timeout=2)
            kill.assert_called_once()

    def test_everyone_may_edit_except_demo(self):
        main._sessions["test-admin"].update(user="kollegin@ylvalabs.de", address="kollegin@ylvalabs.de")
        self.assertIn("Speichern und neu starten", self.client.get("/einstellungen").get_data(as_text=True))
        main._sessions["test-admin"]["demo"] = True
        self.assertEqual(self.post("/einstellungen", {"ORG_NAME": "X"}).status_code, 403)
        self.assertEqual(self.post("/einstellungen/software/update").status_code, 403)
        self.assertNotIn("Speichern und neu starten", self.client.get("/einstellungen").get_data(as_text=True))

    def test_software_requests(self):
        self.assertIn("nicht eingerichtet", self.client.get("/einstellungen/software").get_data(as_text=True))
        self.assertEqual(self.post("/einstellungen/software/update").status_code, 400)
        control = Path(tempfile.mkdtemp())
        (control / "version.json").write_text(json.dumps({
            "checked_at": "2999-01-01T00:00:00", "branch": "main", "behind": 1,
            "current": {"sha": "abc1234", "date": "15.10.2026", "subject": "Alt"},
            "commits": [{"sha": "def5678", "date": "18.10.2026", "subject": "Einstellungen in der App"}]}))
        with mock.patch.dict(main.SETTINGS, {"CONTROL_DIR": control}):
            html = self.client.get("/einstellungen/software").get_data(as_text=True)
            self.assertIn("Einstellungen in der App", html)
            self.assertIn("Update einspielen", html)
            r = self.post("/einstellungen/software/update")
            self.assertTrue(r.get_json()["ok"])
            self.assertTrue((control / "update-requested").exists())
            self.assertEqual(self.post("/einstellungen/software/loeschen").status_code, 404)


if __name__ == "__main__":
    unittest.main()
