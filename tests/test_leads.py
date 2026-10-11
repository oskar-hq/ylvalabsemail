"""Tests für Akquise und CRM. Aufruf: python -m unittest discover tests
Die KI selbst wird dabei nicht gefragt (kein API-Schlüssel nötig), ihre Antworten sind nachgebaut."""
import io
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

from app import agent, ai, crm, finder, leaddb, scoring, webpush  # noqa: E402
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
        self.assertTrue(any(link.endswith("/galerie.html") for link in res["links"]))


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
        fake.messages.create.return_value.content[0].text = '{"betreff": "Re: x", "anrede": "Moin,", "text": "Kurz."}'
        seen = []
        with mock.patch.object(ai, "client", return_value=fake), mock.patch.object(ai, "on_usage", lambda *a: seen.append(a)), \
                mock.patch.dict(ai.MODELS, {"erinnerung": "claude-haiku-4-5"}):
            ai.write_followup("kurz", {"name": "X", "id": 1}, 7)
        kwargs = fake.messages.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "claude-haiku-4-5")
        self.assertNotIn("thinking", kwargs)
        self.assertNotIn("effort", kwargs["output_config"])
        self.assertEqual(seen[0][0], "erinnerung")


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

    SITE = {"pages": [{"url": "https://tischlerei-a.de", "title": "Tischlerei A",
                       "text": "Tischlerei A – Meisterbetrieb seit 1987. Unser Team aus 14 Mitarbeitern baut Küchen. "
                               "Fax: 04642 1234. Karriere: Wir suchen eine Bürokauffrau (m/w/d). "
                               "Impressum Inhaber: Jens Hansen Telefon 04642 1"}],
            "emails": ["info@tischlerei-a.de", "jens.hansen@tischlerei-a.de", "web@agentur.example"],
            "links": ["https://tischlerei-a.de/downloads/auftragsformular.pdf"], "errors": []}

    def test_score_stage_puts_lead_into_pipeline(self):
        lead_id = self.add("Tischlerei A", website="https://tischlerei-a.de")
        with mock.patch.object(finder, "research_website", return_value=self.SITE):
            self.assertTrue(agent.stage_score(self.c, lead_id, {}))
        lead = leaddb.get_lead(self.c, lead_id)
        self.assertEqual((lead["status"], lead["stage"]), ("passt", "neu"))
        self.assertGreaterEqual(lead["fit_score"], 60)
        self.assertEqual(lead["email"], "jens.hansen@tischlerei-a.de")  # persönlich, eigene Domain
        self.assertEqual(lead["contact_name"], "Jens Hansen")
        self.assertIn("Sucht Personal fürs Büro oder die Verwaltung", [r for _, r in lead["research"]["signale"]])
        self.assertEqual(leaddb.tab_of(lead), "passt")

    def test_score_stage_rejects_without_ai(self):
        lead_id = self.add("Webagentur", branche="dienstleistung", category="office=company", distance_km=45,
                           website="https://agentur.example")
        site = {"pages": [{"url": "https://agentur.example", "title": "Webdesign", "text": "Webdesign und SEO-Agentur."}],
                "emails": [], "links": [], "errors": []}
        with mock.patch.object(finder, "research_website", return_value=site), \
                mock.patch.object(ai, "write_draft") as w:
            self.assertFalse(agent.stage_score(self.c, lead_id, {}))
            w.assert_not_called()
        lead = leaddb.get_lead(self.c, lead_id)
        self.assertEqual((lead["status"], lead["rejected_by"], lead["stage"]), ("aussortiert", "regel", None))
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM ai_usage").fetchone()[0], 0)

    def test_imported_lead_stays_in_pipeline(self):
        lead_id, _ = crm.create_lead(self.c, {"name": "Kleiner Betrieb", "website": "klein.example"}, "t")
        site = {"pages": [{"url": "https://klein.example", "title": "", "text": "Hallo"}], "emails": [], "links": [],
                "errors": []}
        with mock.patch.object(finder, "research_website", return_value=site):
            self.assertFalse(agent.stage_score(self.c, lead_id, {}))
        lead = leaddb.get_lead(self.c, lead_id)
        self.assertEqual((lead["status"], lead["stage"]), ("passt", "neu"))

    def test_draft_from_template_and_ai(self):
        lead_id = self.add("Tischlerei A", status="passt", stage="neu", contact_name="Jens Hansen", city="Kappeln",
                           research=json.dumps({"zeitfresser": ["Angebote, Aufmaß und Nachkalkulation"], "auszug": "Küchen"}))
        fields = agent.template_draft(self.c, lead_id)
        self.assertIn("Tischlerei A", fields["subject"])
        self.assertEqual(fields["greeting"], "Guten Tag Jens Hansen,")
        self.assertIn("Angebote, Aufmaß und Nachkalkulation", fields["body"])
        self.assertNotIn("{", fields["body"])
        self.assertEqual(leaddb.get_lead(self.c, lead_id)["status"], "entwurf")
        with mock.patch.object(ai, "write_draft", return_value={"betreff": "B", "anrede": "Moin,", "text": "T"}) as w:
            agent.stage_write(self.c, lead_id, "gehirn")
        self.assertEqual(w.call_args[0][2]["auszug"], "Küchen")  # Auszug der Startseite statt ganzer Webseite
        self.assertEqual(leaddb.get_lead(self.c, lead_id)["subject"], "B")

    def _run(self, check, drafts, budget):
        """Ein ganzer Lauf: Bewertung ohne KI, Entwürfe mit nachgebauter KI, die wie die echte ihre Kosten meldet."""
        def fake(stage, usd, result):
            def call(*args, **kw):
                ai.on_usage(stage, ai.MODELS[stage], {"input": 1000, "output": 100, "cache_write": 0, "cache_read": 0},
                            usd, None)
                return result
            return call
        run_id, err = agent.start_run({"bewerten": check, "entwuerfe": drafts, "budget_eur": budget, "suchen": False},
                                      "test")
        self.assertIsNone(err)
        def site(url):  # jede Webseite mit eigener Adresse, sonst wären es Dubletten
            host = url.split("//")[1]
            return {**self.SITE, "emails": [f"chef@{host}"]}
        with mock.patch.object(finder, "research_website", side_effect=site), \
                mock.patch.object(ai, "write_draft", side_effect=fake("entwurf", 0.06, {"betreff": "b", "anrede": "a", "text": "t"})):
            agent.execute_run(self.c, run_id)
        return self.c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()

    def test_run_scores_for_free(self):
        for i in range(10):
            self.add(f"K{i}", website=f"https://k{i}.example.org")
        with mock.patch.object(ai, "available", return_value=False):
            run = self._run(check=8, drafts=0, budget=0)
        counts = json.loads(run["counts"])
        self.assertEqual((run["status"], counts["bewertet"], counts["entwuerfe"]), ("fertig", 8, 0))
        self.assertIn("Ohne KI-Kosten", run["message"])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM ai_usage").fetchone()[0], 0)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM leads WHERE status='kandidat'").fetchone()[0], 2)

    def test_run_with_drafts_and_budget(self):
        for i in range(10):
            self.add(f"B{i}", website=f"https://b{i}.example.org")
        with mock.patch.object(ai, "available", return_value=True):
            run = self._run(check=10, drafts=5, budget=0.1)
        self.assertEqual(run["status"], "gestoppt")
        self.assertIn("Budget", run["message"])
        self.assertLessEqual(ai.eur(leaddb._sum(self.c, "run_id=?", (run["id"],))["usd"]), 0.1)
        self.assertEqual(json.loads(run["counts"])["entwuerfe"], 1)

    def test_no_drafts_without_key(self):
        with mock.patch.object(ai, "available", return_value=False):
            run_id, err = agent.start_run({"bewerten": 1, "entwuerfe": 1, "budget_eur": 1}, "t")
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
        with mock.patch.object(ai, "available", return_value=True), \
                mock.patch.object(ai, "write_followup", return_value={"betreff": "Re: Hallo", "anrede": "Moin,", "text": "Kurz."}) as wf:
            run_id, _ = agent.start_run({"bewerten": 0, "entwuerfe": 0, "budget_eur": 5, "erinnerungen": True}, "t")
            agent.execute_run(self.c, run_id)
            run = self.c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
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
        agent.handle_reply(self.c, lead_id, msg)  # ohne KI, nach Stichworten
        lead = leaddb.get_lead(self.c, lead_id)
        self.assertEqual((lead["status"], lead["interest"], lead["stage"]), ("beantwortet", "qualified", "antwort"))
        self.assertEqual(leaddb.tab_of(lead), "qualified")
        tasks = crm.open_tasks(self.c, lead_id=lead_id)
        self.assertEqual((len(tasks), tasks[0]["due"]), (1, crm.today()))  # Wiedervorlage für heute
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM signals").fetchone()[0], 1)
        self.assertEqual(leaddb.get_lead(self.c, other)["status"], "versendet")

    def test_unsubscribe_blocks(self):
        lead_id = self.add("D", status="versendet", email="d@d-betrieb.de", sent_at=leaddb.now(), subject="", greeting="", body="")
        msg = EmailMessage()
        msg["From"] = "d@d-betrieb.de"
        msg.set_content("Bitte keine Werbung mehr.")
        agent.handle_reply(self.c, lead_id, msg)
        self.assertEqual(leaddb.get_lead(self.c, lead_id)["status"], "gesperrt")
        self.assertEqual(leaddb.get_lead(self.c, lead_id)["stage"], "verloren")
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


class ScoringTest(unittest.TestCase):
    def test_signals_and_threshold(self):
        lead = {"name": "Pflege B", "branche": "pflege", "category": "healthcare=home_care", "distance_km": 8,
                "website": "https://pflege-b.de", "email": ""}
        good = {"pages": [{"url": "https://pflege-b.de", "title": "Pflegedienst B",
                           "text": "Ambulanter Pflegedienst mit 35 Mitarbeitenden. Touren durch Angeln. "
                                   "Pflegedokumentation. © 2016 Pflege B"}],
                "emails": ["kontakt@pflege-b.de"], "links": ["https://doctolib.example/b"], "errors": []}
        res = scoring.score(lead, good, now=datetime(2026, 10, 1))
        reasons = dict((r, p) for p, r in res["signals"])
        self.assertEqual(reasons["Etwa 35 Mitarbeitende, passende Größe"], 15)
        self.assertIn("Webseite lange nicht aktualisiert (© 2016)", reasons)
        self.assertEqual(reasons["Termine sind schon online buchbar"], -6)
        self.assertEqual(res["email"], "kontakt@pflege-b.de")
        self.assertIn("Dokumentation und Nachweise", res["zeitfresser"])
        self.assertGreaterEqual(res["score"], scoring.MIN_DEFAULT)
        tiny = {"pages": [{"url": "http://x.de", "title": "", "text": "Ich arbeite allein mit 1 Mitarbeiter."}],
                "emails": [], "links": [], "errors": []}
        self.assertLess(scoring.score({**lead, "branche": "handel"}, tiny)["score"], scoring.MIN_DEFAULT)
        self.assertEqual(scoring.extract_size("seit 1995, über 2000 Kunden, team aus 12 leuten"), 12)

    def test_learned_adjustments(self):
        adj = scoring.learned_adjustments([
            {"key": "craft=carpenter", "label": "Tischlerei", "pos": 4, "neg": 0},
            {"key": "shop=car", "label": "Autohaus", "pos": 0, "neg": 5},
            {"key": "craft=roofer", "label": "Dachdeckerei", "pos": 1, "neg": 0}])
        self.assertGreater(adj["craft=carpenter"][0], 0)
        self.assertLess(adj["shop=car"][0], 0)
        self.assertNotIn("craft=roofer", adj)  # zu wenig Erfahrung

    def test_reply_rules(self):
        cases = [("Re: Angebot", "Klingt gut, rufen Sie mich gern nächste Woche an.", {}, "qualified"),
                 ("Re: Angebot", "Danke, aber wir haben kein Interesse.", {}, "kein_interesse"),
                 ("Re: Angebot", "Bitte keine weiteren E-Mails schicken.", {}, "abmeldung"),
                 ("Re: Angebot", "Aktuell nicht, melden Sie sich im Frühjahr wieder.", {}, "mittel"),
                 ("Abwesenheitsnotiz", "Ich bin bis 20.10. nicht im Büro.", {}, "automatisch"),
                 ("Re: Angebot", "Danke", {"Auto-Submitted": "auto-replied"}, "automatisch"),
                 ("Re: Angebot", "Ich habe das an meinen Chef weitergeleitet.", {}, "mittel")]
        for subject, text, headers, expected in cases:
            self.assertEqual(scoring.classify_reply(subject, text, headers)["interesse"], expected, text)


class CrmTest(unittest.TestCase):
    def setUp(self):
        self.c = leaddb.connect(os.path.join(tempfile.mkdtemp(), "crm.db"))

    def test_advance_only_forward(self):
        lead_id, _ = crm.create_lead(self.c, {"name": "A", "email": "a@a.de"}, "t")
        self.assertEqual(leaddb.get_lead(self.c, lead_id)["stage"], "neu")
        self.assertTrue(crm.advance(self.c, lead_id, "kontaktiert"))
        crm.set_stage(self.c, lead_id, "angebot")
        self.assertFalse(crm.advance(self.c, lead_id, "antwort"))   # nicht zurück
        self.assertFalse(crm.advance(self.c, lead_id, "verloren"))  # Angebot läuft schon
        self.assertEqual(leaddb.get_lead(self.c, lead_id)["stage"], "angebot")
        board = dict((k, cards) for k, _, _, cards, _ in crm.board(self.c))
        self.assertEqual([c["name"] for c in board["angebot"]], ["A"])

    def test_tasks(self):
        lead_id, _ = crm.create_lead(self.c, {"name": "B"}, "t")
        crm.add_task(self.c, lead_id, crm.today(), "Anrufen", "t")
        later = crm.add_task(self.c, lead_id, crm.quick_due("1w"), "Nachfassen", "t")
        self.assertEqual(crm.due_count(self.c), 1)
        self.assertEqual(len(crm.open_tasks(self.c, until=crm.today())), 1)
        crm.complete_task(self.c, later, "t")
        self.assertEqual(len(crm.open_tasks(self.c, lead_id=lead_id)), 1)
        with self.assertRaises(ValueError):
            crm.add_task(self.c, lead_id, "morgen", "x", "t")

    def test_csv_roundtrip(self):
        raw = ("Firma;E-Mail;Telefon;Webseite;Ansprechpartner;Ort\n"
               "Tischlerei Nord;Info@Nord.de;0461 1;nord.de;Anna Nord;Flensburg\n"
               "Tischlerei Nord Zwei;info@nord.de;;;;\n"
               ";;;;;\n"
               "Ohne Mail;;;https://www.ohne.de;;Kiel\n").encode("utf-8-sig")
        result = crm.import_csv(self.c, raw, "t")
        self.assertEqual(result["neu"], 2)
        self.assertEqual(len(result["uebersprungen"]), 1)  # doppelte Adresse
        lead = leaddb.get_lead(self.c, crm.find_duplicate(self.c, "", "info@nord.de", "")["id"])
        self.assertEqual((lead["contact_name"], lead["website"], lead["stage"]), ("Anna Nord", "https://nord.de", "neu"))
        out = crm.export_csv(self.c)
        self.assertTrue(out.startswith("\ufeffFirma;Stufe;"))
        self.assertIn("Tischlerei Nord;Neu;", out)
        with self.assertRaises(ValueError):
            crm.import_csv(self.c, b"foo;bar\n1;2\n", "t")

    def test_may_email_respects_first_contact_rule(self):
        lead = {"email": "a@a.de", "status": "passt", "stage": "neu", "replied_at": None, "interest": None}
        self.assertFalse(crm.may_email(lead, False)[0])
        self.assertTrue(crm.may_email(lead, True)[0])
        self.assertTrue(crm.may_email({**lead, "stage": "antwort"}, False)[0])  # hat geantwortet
        self.assertFalse(crm.may_email({**lead, "status": "gesperrt", "stage": "antwort"}, True)[0])
        self.assertFalse(crm.may_email({**lead, "email": None}, True)[0])

    def test_migration_of_old_ai_statuses(self):
        path = os.path.join(tempfile.mkdtemp(), "alt.db")
        c = leaddb.connect(path)
        c.execute("UPDATE kv SET value='0' WHERE key='data_version'")
        for i, (status, interest) in enumerate([("analysiert", None), ("vorgeprueft", None), ("versendet", None),
                                                 ("beantwortet", "kein_interesse")]):
            c.execute("INSERT INTO leads (source_id, created_at, updated_at, name, status, interest) VALUES (?,?,?,?,?,?)",
                      (f"m{i}", leaddb.now(), leaddb.now(), f"M{i}", status, interest))
        c.commit()
        c = leaddb.connect(path)
        rows = [tuple(r) for r in c.execute("SELECT status, stage FROM leads ORDER BY id")]
        self.assertEqual(rows, [("passt", "neu"), ("kandidat", None), ("versendet", "kontaktiert"),
                                ("beantwortet", "verloren")])


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
        # CRM
        html = self.client.get("/crm").get_data(as_text=True)
        self.assertIn("Pipeline.", html)
        self.assertIn("Anrufen, vormittags", html)  # Wiedervorlage von heute aus den Beispieldaten
        self.assertEqual(self.client.get("/leads/regeln").status_code, 200)
        r = self.client.post(f"/crm/{lead_id}/stufe", data={"stage": "gespraech"}, headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(r.get_json()["label"], "Gespräch")
        r = self.client.post(f"/crm/{lead_id}/aufgabe", data={"text": "Angebot schicken", "quick": "3t"},
                             headers={"X-CSRF-Token": self.csrf})
        self.assertTrue(r.get_json()["ok"])
        r = self.client.post(f"/crm/{lead_id}/kontakt", data={"deal_eur": "4.500", "phone": "0461 99"},
                             headers={"X-CSRF-Token": self.csrf})
        self.assertTrue(r.get_json()["changed"])
        self.assertEqual(leaddb.get_lead(c, lead_id)["deal_eur"], 4500)
        r = self.client.post("/crm/neu", data={"name": "Neue Firma", "email": "neu@firma.example"},
                             headers={"X-CSRF-Token": self.csrf})
        new_id = r.get_json()["lead_id"]
        self.assertEqual(self.client.post("/crm/neu", data={"name": "Neue Firma"}, headers={"X-CSRF-Token": self.csrf})
                         .status_code, 400)  # Dublette
        csv_text = self.client.get("/crm/export.csv").get_data(as_text=True)
        self.assertIn("Neue Firma;Neu;", csv_text)
        r = self.client.post("/crm/import", data={"file": (io.BytesIO(b"Firma;Ort\nImport GmbH;Kiel\n"), "l.csv")},
                             headers={"X-CSRF-Token": self.csrf}, content_type="multipart/form-data")
        self.assertEqual(r.get_json()["created"], 1)
        # Vorlage ohne KI, dann gestaltet im Mailer: Erstkontakt per Mail ist aus (OUTREACH_EMAIL=false)
        self.assertTrue(self.client.post(f"/leads/{new_id}/entwurf/vorlage", headers={"X-CSRF-Token": self.csrf}).get_json()["ok"])
        compose = self.client.get(f"/compose?lead={new_id}").get_data(as_text=True)
        self.assertIn(f'name="lead_id" value="{new_id}"', compose)
        self.assertIn("neu@firma.example", compose)
        form = {"lead_id": str(new_id), "recipients": "neu@firma.example", "subject": "Hallo", "body": "Text",
                "template": "brief", "graphic": "none"}
        r = self.client.post("/send", data=form, headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(r.status_code, 400)
        self.assertIn("OUTREACH_EMAIL", r.get_json()["error"])
        crm.set_stage(c, new_id, "antwort")  # nach einer Antwort ist Mail in Ordnung
        r = self.client.post("/send", data=form, headers={"X-CSRF-Token": self.csrf})
        self.assertTrue(r.get_json()["demo"])
        lead = leaddb.get_lead(c, new_id)
        self.assertEqual(lead["status"], "versendet")
        self.assertIn("Gestaltete Mail", c.execute("SELECT text FROM lead_events WHERE lead_id=? AND kind='sent'",
                                                   (new_id,)).fetchone()[0])
        self.assertEqual(len(webpush.b64u_decode(self.client.get("/push/key").get_json()["key"])), 65)


if __name__ == "__main__":
    unittest.main()
