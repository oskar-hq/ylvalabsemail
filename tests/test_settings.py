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

from app import config_store, main, updater  # noqa: E402


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

    def test_restart_only_under_gunicorn(self):
        from app import settings_views
        with mock.patch.object(settings_views.os, "kill") as kill, mock.patch.object(settings_views.time, "sleep"):
            self.assertFalse(settings_views.restart_soon({"SERVER_SOFTWARE": "Werkzeug/3"}))
            self.assertTrue(settings_views.restart_soon({"SERVER_SOFTWARE": "gunicorn/26.2.0"}))
            for t in __import__("threading").enumerate():
                if t.daemon and t.name != "MainThread":
                    t.join(timeout=2)
            kill.assert_called_once()

    def test_admin_only(self):
        with mock.patch.dict(main.SETTINGS, {"ADMIN_USERS": ["chef@ylvalabs.de"]}):
            self.assertEqual(self.post("/einstellungen", {"ORG_NAME": "X"}).status_code, 403)
            self.assertEqual(self.post("/einstellungen/software/update").status_code, 403)
            self.assertIn("Nur Admins", self.client.get("/einstellungen").get_data(as_text=True))

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
