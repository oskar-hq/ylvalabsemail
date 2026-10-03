"""Tests für die KI-Akquise. Aufruf: python -m unittest discover tests
Die KI selbst wird dabei nicht gefragt (kein API-Schlüssel nötig), ihre Antworten sind nachgebaut."""
import json
import os
import struct
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
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
        self.assertIsNone(found[0]["filter_reason"])
        self.assertIn("Webseite", found[1]["filter_reason"])  # nur E-Mail: nichts zu lesen
        self.assertIn("Kette", finder.rough_filter({"brand": "Edeka"}, "https://x.de", "x.de"))
        self.assertIn("Social", finder.rough_filter({}, "https://facebook.com/x", "facebook.com"))
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


class CostTest(unittest.TestCase):
    def test_cost(self):
        tokens = {"input": 1_000_000, "output": 100_000, "cache_write": 0, "cache_read": 1_000_000}
        self.assertAlmostEqual(ai.cost_usd("claude-haiku-4-5", tokens), 1.0 + 0.5 + 0.1)
        self.assertAlmostEqual(ai.cost_usd("claude-haiku-4-5-20251001", tokens), 1.6)  # lange ID mit Datum
        self.assertAlmostEqual(ai.cost_usd("claude-opus-5", {**tokens, "cache_read": 0, "cache_write": 1_000_000}),
                               5 + 2.5 + 6.25)

    def test_haiku_request_has_no_effort(self):
        fake = mock.MagicMock()
        fake.messages.create.return_value = mock.Mock(
            stop_reason="end_turn", model="claude-haiku-4-5",
            usage=mock.Mock(input_tokens=10, output_tokens=5, cache_creation_input_tokens=0, cache_read_input_tokens=0),
            content=[mock.Mock(type="text", text='{"score": 60, "grund": "ok"}')])
        seen = []
        with mock.patch.object(ai, "client", return_value=fake), mock.patch.object(ai, "on_usage", lambda *a: seen.append(a)):
            ai.quick_check("kurz", {"name": "X", "id": 1}, {"pages": []})
        kwargs = fake.messages.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "claude-haiku-4-5")
        self.assertNotIn("thinking", kwargs)
        self.assertNotIn("effort", kwargs["output_config"])
        self.assertEqual(seen[0][0], "vorpruefung")


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
        agent.CFG["settings"]["LEADS_WORKER"] = True
        ai.on_usage = agent._record_usage
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

    def test_stages_quick_analyse_write(self):
        lead_id = self.add("Tischlerei A")
        research = {"pages": [{"url": "https://example.org", "title": "A", "text": "Maßküchen"}],
                    "emails": ["chef@example.org", "info@example.org"], "errors": []}
        analysis = {"passt": True, "score": 80, "begruendung": "Passt gut.", "groesse": "ca. 12", "zeitfresser": ["Angebote"],
                    "ansprechpartner": "Jens", "email": "erfunden@example.org", "aufhaenger": "Maßküchen"}
        with mock.patch.object(finder, "research_website", return_value=research), \
                mock.patch.object(ai, "quick_check", return_value={"score": 70, "grund": "Handwerk mit Büro"}), \
                mock.patch.object(ai, "analyse", return_value=analysis), \
                mock.patch.object(ai, "write_draft", return_value={"betreff": "Hallo", "anrede": "Moin,", "text": "Text"}) as w:
            self.assertTrue(agent.stage_quick(self.c, lead_id, "kurz"))
            self.assertEqual(leaddb.get_lead(self.c, lead_id)["status"], "vorgeprueft")
            self.assertTrue(agent.stage_analyse(self.c, lead_id, "gehirn"))
            lead = leaddb.get_lead(self.c, lead_id)
            self.assertEqual(lead["status"], "analysiert")
            # erfundene Adresse wird nicht übernommen, sondern eine echte von der Webseite
            self.assertEqual(lead["email"], "chef@example.org")
            agent.stage_write(self.c, lead_id, "gehirn")
            # Der Entwurf bekommt die Analyse, nicht noch einmal die ganze Webseite
            self.assertEqual(w.call_args[0][2]["aufhaenger"], "Maßküchen")
        lead = leaddb.get_lead(self.c, lead_id)
        self.assertEqual((lead["status"], lead["subject"], lead["quick_score"]), ("entwurf", "Hallo", 70))

    def test_quick_check_filters_out(self):
        lead_id = self.add("Kette")
        with mock.patch.object(finder, "research_website", return_value={"pages": [{"url": "u", "title": "", "text": "x"}],
                                                                          "emails": [], "errors": []}), \
                mock.patch.object(ai, "quick_check", return_value={"score": 20, "grund": "Filiale"}), \
                mock.patch.object(ai, "analyse") as analyse:
            self.assertFalse(agent.stage_quick(self.c, lead_id, "kurz"))
            analyse.assert_not_called()
        self.assertEqual(leaddb.get_lead(self.c, lead_id)["rejected_by"], "ki")

    def test_ai_error_puts_lead_back(self):
        lead_id = self.add("Tischlerei B")
        with mock.patch.object(finder, "research_website", return_value={"pages": [{"url": "u", "title": "", "text": "x"}],
                                                                          "emails": [], "errors": []}), \
                mock.patch.object(ai, "quick_check", side_effect=ai.AIError("kaputt")):
            with self.assertRaises(ai.AIError):
                agent.stage_quick(self.c, lead_id, "kurz")
        self.assertEqual(leaddb.get_lead(self.c, lead_id)["status"], "kandidat")

    def _run(self, check, drafts, budget, quick_score=70):
        """Einen ganzen Lauf mit nachgebauter KI, die wie die echte ihre Kosten meldet."""
        def fake(stage, usd, result):
            def call(*args, **kw):
                ai.on_usage(stage, ai.MODELS[stage], {"input": 1000, "output": 100, "cache_write": 0, "cache_read": 0},
                            usd, None)
                return result
            return call
        research = {"pages": [{"url": "u", "title": "", "text": "x"}], "emails": [], "errors": []}
        analysis = {"passt": True, "score": 80, "begruendung": "gut", "groesse": "", "zeitfresser": [],
                    "ansprechpartner": "", "email": "", "aufhaenger": ""}
        run_id, err = agent.start_run({"vorpruefen": check, "entwuerfe": drafts, "budget_eur": budget, "suchen": False},
                                      "test")
        self.assertIsNone(err)
        with mock.patch.object(finder, "research_website", return_value=research), \
                mock.patch.object(ai, "quick_check", side_effect=fake("vorpruefung", 0.004, {"score": quick_score, "grund": "g"})), \
                mock.patch.object(ai, "analyse", side_effect=fake("analyse", 0.04, analysis)), \
                mock.patch.object(ai, "write_draft", side_effect=fake("entwurf", 0.06, {"betreff": "b", "anrede": "a", "text": "t"})):
            agent.execute_run(self.c, run_id)
        return self.c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()

    def test_run_funnel_and_costs(self):
        for i in range(10):
            self.add(f"K{i}", website=f"https://k{i}.example.org")
        with mock.patch.object(ai, "available", return_value=True):
            run = self._run(check=8, drafts=2, budget=5)
        counts = json.loads(run["counts"])
        self.assertEqual(run["status"], "fertig")
        self.assertEqual(counts["vorgeprueft"], 8)       # günstig: viele
        self.assertEqual(counts["analysiert"], 2)        # mittel: nur so viele wie nötig
        self.assertEqual(counts["entwuerfe"], 2)         # teuer: nur die besten
        stages = {r["stage"]: r["n"] for r in self.c.execute("SELECT stage, COUNT(*) n FROM ai_usage WHERE run_id=? GROUP BY stage", (run["id"],))}
        self.assertEqual(stages, {"vorpruefung": 8, "analyse": 2, "entwurf": 2})
        self.assertAlmostEqual(leaddb._sum(self.c, "run_id=?", (run["id"],))["usd"], 8 * 0.004 + 2 * 0.04 + 2 * 0.06)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM leads WHERE status='kandidat'").fetchone()[0], 2)

    def test_run_stops_at_budget(self):
        for i in range(30):
            self.add(f"B{i}", website=f"https://b{i}.example.org")
        with mock.patch.object(ai, "available", return_value=True):
            run = self._run(check=30, drafts=5, budget=0.05)
        self.assertEqual(run["status"], "gestoppt")
        self.assertIn("Budget", run["message"])
        spent = ai.eur(leaddb._sum(self.c, "run_id=?", (run["id"],))["usd"])
        self.assertLessEqual(spent, 0.05)

    def test_no_run_without_key(self):
        with mock.patch.object(ai, "available", return_value=False):
            run_id, err = agent.start_run({"vorpruefen": 1, "entwuerfe": 1, "budget_eur": 1}, "t")
        self.assertIsNone(run_id)
        self.assertIn("KI-Schlüssel", err)

    def test_followup_due_and_written_in_run(self):
        old = (datetime.now() - timedelta(days=8)).isoformat(timespec="seconds")
        fresh = leaddb.now()
        due = self.add("Alt", status="versendet", channel="email", email="a@alt.de", sent_at=old, message_id="<m1@y>",
                       subject="Hallo", greeting="Moin,", body="B")
        self.add("Neu", status="versendet", channel="email", email="n@neu.de", sent_at=fresh)
        self.add("Geantwortet", status="beantwortet", interest="mittel", sent_at=old)
        self.assertEqual(leaddb.tab_counts(self.c)["nachfassen"], 1)
        self.assertEqual(leaddb.tab_counts(self.c)["versendet"], 1)
        self.assertEqual(leaddb.tab_of(leaddb.get_lead(self.c, due)), "nachfassen")
        with mock.patch.object(agent, "notify") as n:
            self.assertEqual(agent.notify_due(self.c), 1)
            self.assertEqual(agent.notify_due(self.c), 0)  # nur einmal Bescheid geben
            self.assertEqual(n.call_count, 1)
        self.add("Kandidat", website="https://k.example.org")
        with mock.patch.object(ai, "available", return_value=True), \
                mock.patch.object(ai, "write_followup", return_value={"betreff": "Re: Hallo", "anrede": "Moin,", "text": "Kurz."}) as wf:
            run = self._run(check=1, drafts=0, budget=5, quick_score=10)
        self.assertEqual(json.loads(run["counts"])["erinnerungen"], 1)
        self.assertEqual(wf.call_args[0][2], leaddb.FOLLOWUP_DAYS)
        self.assertEqual(leaddb.get_lead(self.c, due)["followup_body"], "Kurz.")

    def test_followup_mail_threads_and_reply_matches(self):
        old = (datetime.now() - timedelta(days=8)).isoformat(timespec="seconds")
        lead_id = self.add("Alt", status="versendet", channel="email", email="a@alt.de", sent_at=old, message_id="<m1@y>",
                           subject="Hallo", greeting="Moin,", body="B", followup_greeting="Moin,", followup_body="Kurz.")
        sent = []
        smtp = mock.Mock(send_message=lambda m: sent.append(m))
        settings = dict(agent.CFG["settings"], OUTREACH_EMAIL=True, OUTREACH_USER="k@ylvalabs.de",
                        OUTREACH_PASSWORD="x", IMAP_HOST="")
        with mock.patch.dict(agent.CFG, {"settings": settings, "smtp_connect": lambda u, p: smtp}):
            agent.send_outreach(self.c, leaddb.get_lead(self.c, lead_id), "t", followup=True)
        msg = sent[0]
        self.assertEqual(msg["Subject"], "Re: Hallo")
        self.assertEqual(msg["In-Reply-To"], "<m1@y>")
        lead = leaddb.get_lead(self.c, lead_id)
        self.assertEqual(lead["followup_count"], 1)
        self.assertEqual(leaddb.tab_of(lead), "versendet")
        reply = EmailMessage()
        reply["From"] = "x@woanders.de"
        reply["In-Reply-To"] = lead["followup_message_id"]
        reply.set_content("Ja gern")
        self.assertEqual(agent._match_lead(self.c, reply), lead_id)

    def test_learned_filter(self):
        for i in range(5):
            self.add(f"F{i}", category="craft=hairdresser", category_label="Friseur", status="aussortiert", rejected_by="ki")
        waiting = self.add("F-neu", category="craft=hairdresser", category_label="Friseur")
        other = self.add("T-neu")
        self.assertEqual(agent.learned_filter(self.c), 1)
        self.assertEqual(leaddb.get_lead(self.c, waiting)["rejected_by"], "filter")
        self.assertEqual(leaddb.get_lead(self.c, other)["status"], "kandidat")

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
        # Demo-Daten: ein Betrieb ist zum Nachfassen fällig
        due = c.execute("SELECT id FROM leads WHERE name LIKE 'Steuerbüro%'").fetchone()[0]
        self.assertIn("Erinnerung schreiben lassen", self.client.get(f"/leads/{due}").get_data(as_text=True))
        r = self.client.post(f"/leads/{due}/erinnerung/auslassen", headers={"X-CSRF-Token": self.csrf})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(leaddb.get_lead(c, due)["followup_count"], -1)
        self.assertEqual(leaddb.tab_of(leaddb.get_lead(c, due)), "versendet")
        self.assertEqual(self.client.get("/sw.js").headers["Service-Worker-Allowed"], "/")
        # Systemcheck ohne echte Netzwerkzugriffe
        from app import systemcheck
        with mock.patch.object(systemcheck.urllib.request, "urlopen", side_effect=OSError("gesperrt")), \
                mock.patch.object(finder, "fetch_page", return_value=("https://ylvalabs.de/", "<html></html>")):
            html = self.client.get("/leads/system").get_data(as_text=True)
        self.assertIn("Kartensuche (OpenStreetMap)", html)
        self.assertIn("overpass-api.de erreichen", html)   # Hinweis bei Fehler
        self.assertIn("DEMO_MODE ist an", html)            # Test läuft im Demo-Modus
        self.assertEqual(len(webpush.b64u_decode(self.client.get("/push/key").get_json()["key"])), 65)


if __name__ == "__main__":
    unittest.main()
