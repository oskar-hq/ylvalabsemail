"""Tests für die KI-Akquise. Aufruf: python -m unittest discover tests
Die KI selbst wird dabei nicht gefragt (kein API-Schlüssel nötig), ihre Antworten sind nachgebaut."""
import json
import os
import struct
import sys
import tempfile
import threading
import unittest
from email.message import EmailMessage
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

TMP = tempfile.mkdtemp()
os.environ.update(DATA_DIR=TMP, SMTP_HOST="smtp.example.com", LEADS_WORKER="false", DEMO_MODE="true")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402

from app import agent, ai, finder, leaddb, webpush  # noqa: E402
from app.main import app  # noqa: E402


class WebPushTest(unittest.TestCase):
    def test_roundtrip(self):
        """Verschlüsseln wie der Server, entschlüsseln wie der Browser (RFC 8291)."""
        ua_key = ec.generate_private_key(ec.SECP256R1())
        ua_pub = ua_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        auth = os.urandom(16)
        body = webpush.encrypt(b'{"title":"Hallo"}', webpush.b64u(ua_pub), webpush.b64u(auth))
        salt, (rs, idlen) = body[:16], struct.unpack("!IB", body[16:21])
        as_pub = body[21:21 + idlen]
        self.assertEqual((rs, idlen), (4096, 65))
        shared = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
        ikm = webpush._hkdf(auth, shared, b"WebPush: info\x00" + ua_pub + as_pub, 32)
        cek = webpush._hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = webpush._hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
        plain = AESGCM(cek).decrypt(nonce, body[21 + idlen:], None)
        self.assertEqual(plain, b'{"title":"Hallo"}\x02')

    def test_vapid_header(self):
        v = webpush.Vapid(os.path.join(TMP, "vapid_test.pem"))
        header = v.auth_header("https://web.push.apple.com/abc", "mailto:info@ylvalabs.de")
        token = header.split("t=")[1].split(",")[0]
        claims = json.loads(webpush.b64u_decode(token.split(".")[1]))
        self.assertEqual(claims["aud"], "https://web.push.apple.com")
        self.assertEqual(len(webpush.b64u_decode(v.public_key)), 65)


class FinderTest(unittest.TestCase):
    def test_parse_and_classify(self):
        elements = [
            {"type": "node", "id": 1, "lat": 54.66, "lon": 9.93,
             "tags": {"name": "Tischlerei A", "craft": "carpenter", "website": "www.tischlerei-a.de"}},
            {"type": "way", "id": 2, "center": {"lat": 54.60, "lon": 9.80},
             "tags": {"name": "Pflege B", "healthcare": "home_care", "contact:email": "info@pflege-b.de"}},
            {"type": "node", "id": 3, "lat": 54.66, "lon": 9.93, "tags": {"name": "Ohne Kontakt", "craft": "painter"}},
            {"type": "node", "id": 4, "lat": 54.66, "lon": 9.93,
             "tags": {"name": "Tischlerei A Filiale", "craft": "carpenter", "website": "https://tischlerei-a.de/filiale"}},
            {"type": "node", "id": 5, "lat": 53.0, "lon": 9.93, "tags": {"name": "Zu weit", "craft": "roofer", "website": "x.de"}},
            {"type": "node", "id": 6, "lat": 54.7, "lon": 9.9,
             "tags": {"name": "Hof C", "place": "farm", "website": "hof-c.de"}},
        ]
        found = finder.parse_elements(elements, 54.6614, 9.9311, 50)
        self.assertEqual([f["name"] for f in found], ["Tischlerei A", "Pflege B", "Hof C"])
        self.assertEqual(found[0]["website"], "https://www.tischlerei-a.de")
        self.assertEqual((found[0]["branche"], found[0]["category_label"]), ("handwerk", "Tischlerei"))
        self.assertEqual((found[1]["branche"], found[1]["category_label"]), ("pflege", "Ambulante Pflege"))
        self.assertEqual(found[2]["branche"], "landwirtschaft")
        self.assertIn('nwr(around:50000,54.6614,9.9311)["craft"]["name"];', finder.overpass_query(54.6614, 9.9311, 50))

    def test_emails(self):
        html = 'Mail: <a href="mailto:chef@betrieb.de">x</a> oder buero [at] betrieb.de, logo@2x.png'
        self.assertEqual(finder.extract_emails(html), ["buero@betrieb.de", "chef@betrieb.de"])

    def test_research_website(self):
        site = tempfile.mkdtemp()
        Path(site, "index.html").write_text(
            '<html><title>Tischlerei</title><body><script>var x=1</script><h1>Maßküchen</h1>'
            '<a href="/impressum.html">Impressum</a><a href="/galerie.html">Galerie</a></body></html>', encoding="utf-8")
        Path(site, "impressum.html").write_text("<p>Inhaber: Jens Hansen</p><p>info@tischlerei.de</p>", encoding="utf-8")
        Path(site, "robots.txt").write_text("User-agent: *\nDisallow: /privat\n")
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=site))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/"
            self.assertEqual(finder.research_website(url)["pages"], [])  # interne Adresse: gesperrt
            with mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}), \
                    mock.patch.object(finder, "ALLOW_PRIVATE", True):
                res = finder.research_website(url)
        finally:
            server.shutdown()
        self.assertEqual(len(res["pages"]), 2)
        self.assertIn("Maßküchen", res["pages"][0]["text"])
        self.assertNotIn("var x", res["pages"][0]["text"])
        self.assertIn("Jens Hansen", res["pages"][1]["text"])
        self.assertEqual(res["emails"], ["info@tischlerei.de"])


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class BrainTest(unittest.TestCase):
    def test_split_and_replace_learned(self):
        text = "# G\n\n## Über uns\nA\n\n## Gelernt\n\n- alt\n\n## Danach\nB\n"
        base, learned = leaddb.split_learned(text)
        self.assertEqual(learned, "- alt")
        self.assertIn("## Danach", base)
        new = leaddb.with_learned(text, "- neu")
        self.assertIn("## Gelernt\n\n- neu", new)
        self.assertNotIn("- alt", new)
        self.assertIn("## Über uns\nA", new)


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(tempfile.mkdtemp(), "t.db")
        self.c = leaddb.connect(self.db_path)
        agent.CFG["db_path"] = self.db_path
        agent.CFG["settings"]["NOTIFY_EMAILS"] = []
        self.notify = mock.patch.object(agent, "notify").start()
        self.addCleanup(mock.patch.stopall)

    def add(self, name, **kw):
        fields = dict(source_id=f"t:{name}", created_at=leaddb.now(), updated_at=leaddb.now(), name=name,
                      branche="handwerk", category="craft=carpenter", category_label="Tischlerei", distance_km=5,
                      website="https://example.org", status="kandidat")
        fields.update(kw)
        cur = self.c.execute(f"INSERT INTO leads ({','.join(fields)}) VALUES ({','.join('?' * len(fields))})",
                             list(fields.values()))
        self.c.commit()
        return cur.lastrowid

    def test_process_lead_draft(self):
        lead_id = self.add("Tischlerei A")
        research = {"pages": [{"url": "https://example.org", "title": "A", "text": "Maßküchen"}],
                    "emails": ["chef@example.org", "info@example.org"], "errors": []}
        result = {"passt": True, "score": 80, "begruendung": "Passt gut.", "groesse": "ca. 12", "zeitfresser": ["Angebote"],
                  "ansprechpartner": "Jens", "email": "erfunden@example.org", "betreff": "Hallo", "anrede": "Moin,", "text": "Text"}
        with mock.patch.object(finder, "research_website", return_value=research), \
                mock.patch.object(ai, "qualify", return_value=result):
            self.assertTrue(agent.process_lead(self.c, lead_id))
        lead = leaddb.get_lead(self.c, lead_id)
        self.assertEqual(lead["status"], "entwurf")
        # erfundene Adresse wird nicht übernommen, sondern eine echte von der Webseite
        self.assertEqual(lead["email"], "chef@example.org")
        self.assertEqual(lead["research"]["zeitfresser"], ["Angebote"])
        self.notify.assert_called_once()

    def test_ai_error_puts_lead_back(self):
        lead_id = self.add("Tischlerei B")
        with mock.patch.object(finder, "research_website", return_value={"pages": [{"url": "u", "title": "", "text": "x"}],
                                                                          "emails": [], "errors": []}), \
                mock.patch.object(ai, "qualify", side_effect=ai.AIError("kaputt")):
            with self.assertRaises(ai.AIError):
                agent.process_lead(self.c, lead_id)
        self.assertEqual(leaddb.get_lead(self.c, lead_id)["status"], "kandidat")

    def test_reply_matching_and_classification(self):
        lead_id = self.add("Tischlerei C", status="versendet", channel="email", email="info@tischlerei-c.de",
                           message_id="<abc@ylvalabs.de>", sent_at=leaddb.now(), subject="S", greeting="Moin,", body="B")
        other = self.add("Andere", status="versendet", channel="email", email="x@anders.de", sent_at=leaddb.now())
        msg = EmailMessage()
        msg["From"] = "Jens <jens@tischlerei-c.de>"
        msg["Subject"] = "Re: S"
        msg["In-Reply-To"] = "<abc@ylvalabs.de>"
        msg.set_content("Klingt gut, rufen Sie an.\n\nAm 1.10. schrieb Oskar:\n> alter Text")
        self.assertEqual(agent._match_lead(self.c, msg), lead_id)
        del msg["In-Reply-To"]
        self.assertEqual(agent._match_lead(self.c, msg), lead_id)  # über die Domain
        self.assertEqual(agent._message_text(msg), "Klingt gut, rufen Sie an.")
        verdict = {"interesse": "qualified", "zusammenfassung": "Will Anruf.", "grund": "Angebote",
                   "naechster_schritt": "Anrufen", "lernpunkt": "Tischlereien mögen das."}
        with mock.patch.object(ai, "available", return_value=True), mock.patch.object(ai, "classify_reply", return_value=verdict):
            agent.handle_reply(self.c, lead_id, msg)
        lead = leaddb.get_lead(self.c, lead_id)
        self.assertEqual((lead["status"], lead["interest"]), ("beantwortet", "qualified"))
        self.assertEqual(leaddb.tab_of(lead), "qualified")
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM signals").fetchone()[0], 1)
        self.assertEqual(leaddb.get_lead(self.c, other)["status"], "versendet")

    def test_unsubscribe_blocks(self):
        lead_id = self.add("D", status="versendet", email="d@d-betrieb.de", sent_at=leaddb.now(), subject="", greeting="", body="")
        msg = EmailMessage()
        msg["From"] = "d@d-betrieb.de"
        msg.set_content("Bitte keine Werbung mehr.")
        verdict = {"interesse": "abmeldung", "zusammenfassung": "Will nichts.", "grund": "", "naechster_schritt": "",
                   "lernpunkt": ""}
        with mock.patch.object(ai, "available", return_value=True), mock.patch.object(ai, "classify_reply", return_value=verdict):
            agent.handle_reply(self.c, lead_id, msg)
        self.assertEqual(leaddb.get_lead(self.c, lead_id)["status"], "gesperrt")
        self.assertIn("d@d-betrieb.de", leaddb.kv_get(self.c, "blocklist"))

    def test_next_candidate_prefers_successful_branch(self):
        for i in range(4):
            self.add(f"q{i}", branche="pflege", category="healthcare=home_care", status="beantwortet", interest="qualified")
            self.add(f"k{i}", branche="handel", category="shop=car", status="beantwortet", interest="kein_interesse")
        pflege = self.add("neu-pflege", branche="pflege", category="healthcare=home_care")
        self.add("neu-handel", branche="handel", category="shop=car")
        picks = [agent.next_candidate(self.c) for _ in range(200)]
        self.assertGreater(picks.count(pflege), 150)
        self.assertLess(picks.count(pflege), 200)  # die andere Branche kommt trotzdem ab und zu dran

    def test_reflect_updates_brain(self):
        leaddb.add_signal(self.c, "feedback", "Bitte immer duzen.")
        with mock.patch.object(ai, "reflect", return_value={"gelernt": "### Nachrichten\n- Immer duzen.", "aenderung": "Duzen."}):
            self.assertTrue(agent.maybe_reflect(self.c, force=True))
        brain = leaddb.brain(self.c)
        self.assertIn("- Immer duzen.", brain)
        self.assertIn("## Wen wir suchen", brain)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM signals WHERE processed=0").fetchone()[0], 0)


class ViewsTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.client.post("/demo", headers={"X-CSRF-Token": self._csrf("/login")})
        self.csrf = self._csrf("/leads")

    def _csrf(self, url):
        html = self.client.get(url).get_data(as_text=True)
        return html.split('name="csrf-token" content="')[1].split('"')[0]

    def test_pages_and_actions(self):
        self.assertEqual(self.client.post("/leads/demo", headers={"X-CSRF-Token": self.csrf}).status_code, 302)
        html = self.client.get("/leads").get_data(as_text=True)
        self.assertIn("Tischlerei Hansen (Beispiel)", html)
        c = leaddb.connect(os.path.join(TMP, "mailer.db"))
        lead_id = c.execute("SELECT id FROM leads WHERE status='entwurf' ORDER BY id LIMIT 1").fetchone()[0]
        self.assertEqual(self.client.get(f"/leads/{lead_id}").status_code, 200)
        preview = self.client.get(f"/leads/{lead_id}/preview")
        self.assertIn("Viele Grüße", preview.get_data(as_text=True))
        self.assertEqual(preview.headers["X-Frame-Options"], "SAMEORIGIN")
        r = self.client.post(f"/leads/{lead_id}/save", data={"subject": "Neu", "greeting": "Moin,", "body": "Kurz."},
                             headers={"X-CSRF-Token": self.csrf})
        self.assertTrue(r.get_json()["changed"])
        self.assertIn("Kurz.", self.client.get(f"/leads/{lead_id}/brief").get_data(as_text=True))
        r = self.client.post(f"/leads/{lead_id}/mark-sent", data={"channel": "brief"}, headers={"X-CSRF-Token": self.csrf})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(leaddb.get_lead(c, lead_id)["status"], "versendet")
        self.assertEqual(leaddb.get_lead(c, lead_id)["body"], "Kurz.")  # ohne mitgeschickten Text nicht überschrieben
        self.assertEqual(self.client.get("/leads/gehirn").status_code, 200)
        self.assertEqual(self.client.get("/sw.js").headers["Service-Worker-Allowed"], "/")
        self.assertEqual(len(webpush.b64u_decode(self.client.get("/push/key").get_json()["key"])), 65)


if __name__ == "__main__":
    unittest.main()
