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

from app import agent, ai, claude_research, crm, finder, known, leaddb, niches, regions, schedule, scoring, team, webpush  # noqa: E402
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
        query = finder.overpass_query(54.6614, 9.9311, 50)
        self.assertIn('nwr(around:50000,54.6614,9.9311)["craft"~"^(hvac|plumber|', query)
        self.assertNotIn("hairdresser", query)  # nur Branchen, die zu Ylva Labs passen
        self.assertNotIn('["shop"~"^(car', query)
        q = finder.overpass_query(54.6614, 9.9311, 50, "DE-SH")
        self.assertIn('area["ISO3166-2"="DE-SH"]->.gebiet;', q)
        self.assertIn('nwr(area.gebiet)["healthcare"~"^(home_care|nursing_home)$"]["name"];', q)
        self.assertNotIn("around", q)
        # Mit Region gilt kein Radius: auch Betriebe weit weg (aber im Land) bleiben drin
        self.assertIn("Zu weit", [f["name"] for f in finder.parse_elements(elements, 54.6614, 9.9311)])

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
        self.assertIn("Sucht Personal fürs Büro, die Verwaltung oder die Disposition", [r for _, r in lead["research"]["signale"]])
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

    def test_no_email_no_pipeline(self):
        lead_id = self.add("Tischlerei ohne Mail", website="https://tischlerei-a.de")
        with mock.patch.object(finder, "research_website", return_value={**self.SITE, "emails": []}):
            self.assertFalse(agent.stage_score(self.c, lead_id, {}))
        lead = leaddb.get_lead(self.c, lead_id)
        self.assertGreaterEqual(lead["fit_score"], 60)  # passt inhaltlich …
        self.assertEqual((lead["status"], lead["stage"]), ("aussortiert", None))  # … aber ohne Mail nicht ins CRM
        self.assertIn("keine e-mail-adresse", lead["fit_reason"].lower())

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
        self.assertIn("Tagsüber sind Sie und Ihr Team bei den Kunden", fields["body"])  # Branche Handwerk
        self.assertNotIn("Angebote, Aufmaß und Nachkalkulation", fields["body"])  # steckt schon im Branchen-Satz
        self.assertNotIn("Bei Betrieben wie", fields["body"])
        self.assertIn("Sprachnotiz", fields["body"])
        self.assertNotIn("{", fields["body"])
        self.assertEqual(leaddb.get_lead(self.c, lead_id)["status"], "entwurf")
        with mock.patch.object(ai, "write_draft", return_value={"betreff": "B", "anrede": "Moin,", "text": "T"}) as w:
            agent.stage_write(self.c, lead_id, "gehirn")
        self.assertEqual(w.call_args[0][2]["auszug"], "Küchen")  # Auszug der Startseite statt ganzer Webseite
        self.assertEqual(leaddb.get_lead(self.c, lead_id)["subject"], "B")

    def _run(self, check, drafts, budget, top=25):
        """Ein ganzer Lauf: Bewertung ohne KI, Entwürfe mit nachgebauter KI, die wie die echte ihre Kosten meldet."""
        def fake(stage, usd, result):
            def call(*args, **kw):
                ai.on_usage(stage, ai.MODELS[stage], {"input": 1000, "output": 100, "cache_write": 0, "cache_read": 0},
                            usd, None)
                return result
            return call
        run_id, err = agent.start_run({"bewerten": check, "top": top, "entwuerfe": drafts, "budget_eur": budget,
                                       "suchen": False}, "test")
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

    def test_run_puts_only_the_best_into_crm(self):
        for i in range(6):
            self.add(f"T{i}", website=f"https://t{i}.example.org")
        with mock.patch.object(ai, "available", return_value=False):
            run = self._run(check=6, drafts=0, budget=0, top=2)
        counts = json.loads(run["counts"])
        self.assertEqual((counts["passen"], counts["ins_crm"]), (6, 2))
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM leads WHERE stage='neu'").fetchone()[0], 2)
        self.assertEqual(leaddb.tab_counts(self.c)["passt"], 6)  # der Rest wartet im Reiter „Passt“

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

    def test_inbox_thread_reply_and_next_answer(self):
        from app import mailbox
        lead_id = self.add("Hansen", status="versendet", channel="email", email="jens@hansen.de",
                           sent_at=leaddb.now(), message_id="<m1@y>", subject="Eine Idee für Hansen", greeting="Moin,",
                           body="Erste Nachricht.")
        first = EmailMessage()
        first["From"] = "Jens Hansen <jens@hansen.de>"
        first["Subject"] = "Re: Eine Idee für Hansen"
        first["In-Reply-To"] = "<m1@y>"
        first.set_content("Das klingt spannend, rufen Sie gern an.")
        self.assertTrue(agent.handle_reply(self.c, lead_id, first, "<r1@hansen.de>"))
        groups = mailbox.grouped(self.c)
        self.assertEqual([c["id"] for c in groups["offen"]], [lead_id])  # offen zum Antworten
        self.assertEqual(groups["offen"][0]["mood"], "gut")
        self.assertEqual(mailbox.unread_count(self.c), 1)
        sent = []
        smtp = mock.Mock(send_message=lambda m: sent.append(m))
        settings = dict(agent.CFG["settings"], OUTREACH_USER="k@ylvalabs.de", OUTREACH_PASSWORD="x", IMAP_HOST="")
        with mock.patch.dict(agent.CFG, {"settings": settings, "smtp_connect": lambda u, p: smtp}):
            agent.send_reply(self.c, leaddb.get_lead(self.c, lead_id), "Gern, passt Dienstag?", "oskar")
        msg = sent[0]
        self.assertEqual((msg["To"], msg["Subject"], msg["In-Reply-To"]),
                         ("Jens Hansen <jens@hansen.de>", "Re: Eine Idee für Hansen", "<r1@hansen.de>"))
        self.assertIn("> Das klingt spannend", msg.get_content())
        groups = mailbox.grouped(self.c)
        self.assertEqual([c["id"] for c in groups["gesendet"]], [lead_id])  # nach dem Antworten: Gesendet
        self.assertEqual(mailbox.unread_count(self.c), 0)
        # Die nächste Antwort bezieht sich auf unsere Antwort und landet im selben Gespräch
        nxt = EmailMessage()
        nxt["From"] = "assistenz@ganz-anders.de"
        nxt["In-Reply-To"] = msg["Message-ID"]
        nxt.set_content("Dienstag ist leider schlecht.")
        self.assertEqual(agent._match_lead(self.c, nxt), lead_id)
        self.assertTrue(agent.handle_reply(self.c, lead_id, nxt, "<r2@x>"))
        self.assertEqual(len(mailbox.thread(self.c, leaddb.get_lead(self.c, lead_id))), 4)  # erste, Antwort, wir, Antwort
        self.assertEqual([c["id"] for c in mailbox.grouped(self.c)["offen"]], [lead_id])
        leaddb.update_lead(self.c, lead_id, mood="mittel")  # von Hand gesetzt geht vor
        self.assertEqual(mailbox.mood(leaddb.get_lead(self.c, lead_id), None)[0], "mittel")

    def test_inbox_takes_all_mail_but_not_bulk(self):
        from email.utils import format_datetime
        from app import mailbox

        def mail(frm, subject, text, mid, **headers):
            m = EmailMessage()
            m["From"], m["Subject"], m["Message-ID"] = frm, subject, mid
            m["Date"] = format_datetime(datetime.now().astimezone())
            for k, v in headers.items():
                m[k.replace("_", "-")] = v
            m.set_content(text)
            return m.as_bytes()

        old = EmailMessage()
        old["From"], old["Subject"], old["Message-ID"] = "alt@kunde.de", "Alt", "<old@x>"
        old["Date"] = "Mon, 01 Jan 2024 10:00:00 +0100"
        old.set_content("Uralt")
        inbox = [mail("Lisa Meier <lisa@partner.de>", "Kooperation?", "Hallo, wollen wir reden?", "<a@x>"),
                 mail("Shop <newsletter@shop.de>", "Angebote", "Rabatt", "<b@x>", List_Unsubscribe="<mailto:x@y>"),
                 mail("k@ylvalabs.de", "[Leads] Neue Antwort", "Benachrichtigung", "<c@x>"),
                 mail("Abwesend <lisa2@partner.de>", "Abwesend", "Bin weg", "<d@x>", Auto_Submitted="auto-replied"),
                 old.as_bytes()]

        class Imap:
            def select(self, *a, **k):
                return "OK", [b"5"]

            def search(self, *a):
                return "OK", [b" ".join(str(i + 1).encode() for i in range(len(inbox)))]

            def fetch(self, num, what):
                raw = inbox[int(num) - 1]
                if "HEADER" in what:
                    mid = email_mod.message_from_bytes(raw)["Message-ID"]
                    return "OK", [(b"x", f"Message-ID: {mid}\r\n\r\n".encode())]
                return "OK", [(b"x", raw)]

            def logout(self):
                pass
        import email as email_mod
        settings = dict(agent.CFG["settings"], OUTREACH_USER="k@ylvalabs.de", OUTREACH_PASSWORD="x", IMAP_HOST="imap.x")
        with mock.patch.dict(agent.CFG, {"settings": settings}), mock.patch.object(agent, "_imap", return_value=Imap()):
            self.assertEqual(agent.check_inbox(self.c), 1)  # nur Lisa: kein Newsletter, keine eigene, keine Abwesenheit
            self.assertEqual(agent.check_inbox(self.c), 0)  # schon gesehen
        contact = leaddb.get_lead(self.c, self.c.execute("SELECT id FROM leads WHERE email='lisa@partner.de'").fetchone()[0])
        self.assertEqual((contact["status"], contact["name"], contact["stage"]), ("kontakt", "Lisa Meier", None))
        self.assertEqual(mailbox.unread_count(self.c), 1)
        in_tabs = {r["id"] for k, _, _ in leaddb.TABS for r in leaddb.tab_leads(self.c, k)}
        self.assertNotIn(contact["id"], in_tabs)  # Kontakte stehen im Postfach, nicht bei den Leads
        self.notify.assert_called()
        self.assertIn("Neue Mail von Lisa Meier", self.notify.call_args[0][1])
        # Antwort von uns auf den Kontakt (kein Erstkontakt, die Person hat geschrieben)
        sent = []
        smtp = mock.Mock(send_message=lambda m: sent.append(m))
        with mock.patch.dict(agent.CFG, {"settings": dict(settings, IMAP_HOST=""), "smtp_connect": lambda u, p: smtp}):
            agent.send_reply(self.c, contact, "Sehr gern!", "oskar")
            agent.send_new(self.c, contact, "lisa@partner.de", "Unterlagen", "Anbei die Infos.", "oskar")
        self.assertEqual(sent[0]["To"], "Lisa Meier <lisa@partner.de>")
        self.assertEqual((sent[1]["Subject"], sent[1]["From"].addresses[0].addr_spec), ("Unterlagen", "k@ylvalabs.de"))
        self.assertIn("Viele Grüße", sent[1].get_content())

    def test_mail_errors_are_readable(self):
        import smtplib
        self.assertIn("Passwort", agent.mail_error(smtplib.SMTPAuthenticationError(535, b"wrong user/password")))
        self.assertIn("Passwort", agent.mail_error(Exception("LOGIN command error: BAD [CLIENTBUG] (Password required)")))
        self.assertIn("nicht erreichbar", agent.mail_error(ConnectionRefusedError("refused")))
        settings = dict(agent.CFG["settings"], OUTREACH_USER="k@ylvalabs.de", OUTREACH_PASSWORD="x", IMAP_HOST="")

        def fail(u, p):
            raise smtplib.SMTPAuthenticationError(535, b"no")
        lead = leaddb.get_lead(self.c, self.add("Fehler", email="a@b.de", status="kontakt"))
        with mock.patch.dict(agent.CFG, {"settings": settings, "smtp_connect": fail}):
            with self.assertRaisesRegex(RuntimeError, "Akquise-Postfach"):
                agent.send_new(self.c, lead, "a@b.de", "Hallo", "Text", "oskar")

    def test_boundaries_loaded_once_and_assigned(self):
        geo = {k: {"name": n, "rings": [[[lat - 0.05, lon - 0.05], [lat - 0.05, lon + 0.05], [lat + 0.05, lon + 0.05],
                                         [lat - 0.05, lon - 0.05]]]} for k, (n, lat, lon) in regions.KREISE.items()}
        lead_id = self.add("In Kiel", lat=54.33, lon=10.13)
        with mock.patch.dict(agent.CFG["settings"], {"LEADS_REGION": "DE-SH"}), \
                mock.patch.object(regions, "load_boundaries", return_value=geo) as load:
            self.assertTrue(agent.ensure_boundaries(self.c))
            self.assertFalse(agent.ensure_boundaries(self.c))  # schon da: nicht noch einmal laden
            self.assertEqual(load.call_count, 1)
        self.assertEqual(agent.assign_kreise(self.c), 1)
        self.assertEqual(leaddb.get_lead(self.c, lead_id)["kreis"], "01002")

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
        self.assertEqual(reasons["Kernbranche Pflege"], 20)
        self.assertEqual(reasons["Etwa 35 Mitarbeitende, ideale Größe"], 15)
        self.assertIn("Webseite lange nicht aktualisiert (© 2016)", reasons)
        self.assertEqual(reasons["Schon digital (Online-Buchung, Kundenportal oder App)"], -10)
        self.assertEqual(res["email"], "kontakt@pflege-b.de")
        self.assertIn("Dokumentation und Nachweise", res["zeitfresser"])
        self.assertEqual(res["ko"], [])
        # Mit Stellenanzeige fürs Büro und ohne Online-Buchung wird es ein Top-Lead
        hot = {**good, "links": [], "pages": [{**good["pages"][0], "text": good["pages"][0]["text"]
               + " Wir suchen eine Verwaltungsangestellte (m/w/d) und Pflegefachkräfte (m/w/d)."}]}
        self.assertGreaterEqual(scoring.score(lead, hot, now=datetime(2026, 10, 1))["score"], 80)
        tiny = {"pages": [{"url": "http://x.de", "title": "", "text": "Ich arbeite allein mit 1 Mitarbeiter."}],
                "emails": [], "links": [], "errors": []}
        self.assertEqual(scoring.score({**lead, "branche": "handel"}, tiny)["ko"],
                         ["Sehr klein (1 Mitarbeitende), kaum Verwaltung", "Keine E-Mail-Adresse gefunden"])
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


class RegionsTest(unittest.TestCase):
    def test_boundaries_stitch_and_locate(self):
        # Kiel als Quadrat aus zwei Wegen (einer rückwärts), dazu eine Insel und ein Loch
        def way(points, role="outer"):
            return {"type": "way", "role": role, "geometry": [{"lat": a, "lon": b} for a, b in points]}
        payload = {"elements": [{"type": "relation", "tags": {"de:amtlicher_gemeindeschluessel": "01002", "name": "Kiel"},
                                 "members": [way([(54.2, 10.0), (54.2, 10.3), (54.4, 10.3)]),
                                             way([(54.2, 10.0), (54.4, 10.0), (54.4, 10.3)]),
                                             way([(54.30, 10.10), (54.30, 10.12), (54.32, 10.12), (54.32, 10.10),
                                                  (54.30, 10.10)], "inner"),
                                             way([(54.6, 10.6), (54.6, 10.7), (54.7, 10.7), (54.6, 10.6)])]},
                                {"type": "relation", "tags": {"de:regionalschluessel": "099990000000"}, "members": []}]}
        geo = regions.parse_boundaries(payload)
        self.assertEqual(list(geo), ["01002"])
        self.assertEqual(len(geo["01002"]["rings"]), 3)
        self.assertEqual(regions.kreis_of(geo, 54.25, 10.2), "01002")
        self.assertIsNone(regions.kreis_of(geo, 54.31, 10.11))   # im Loch
        self.assertEqual(regions.kreis_of(geo, 54.62, 10.65), "01002")  # Insel
        self.assertIsNone(regions.kreis_of(geo, 53.5, 9.5))
        self.assertIn('["admin_level"="6"]', regions.boundary_query())

    def test_stats_csv_and_summary(self):
        csv = ("Gewerbeanmeldungen;;2025\n01001;Flensburg, kreisfreie Stadt;1.000\n01056;Pinneberg, Kreis;4.800\n"
               "01058;Rendsburg-Eckernförde, Kreis;-\n\"Kiel, Landeshauptstadt\";2.480\n")
        values = regions.parse_stats_csv(csv.encode("utf-8"))
        self.assertEqual(values, {"01001": 1000.0, "01056": 4800.0, "01002": 2480.0})
        rows = {r["key"]: r for r in regions.summary({"gewerbe": values, "einwohner": {"01001": 100000}})}
        self.assertEqual(rows["01001"]["gewerbe_1000"], 10.0)  # importierte Einwohner gehen vor
        self.assertEqual((rows["01056"]["gewerbe_rang"], rows["01001"]["gewerbe_rang"]), (1, 2))  # 15 vor 10 je 1.000
        self.assertTrue(rows["01056"]["gewerbe_top"])
        self.assertIsNone(rows["01058"]["gewerbe_1000"])
        self.assertFalse(regions.bonus_info({})["01002"]["bau_top"])

    def test_growth_bonus_in_scoring(self):
        site = {"pages": [{"url": "https://d.de", "title": "", "text": "Dachdeckerei mit 20 Mitarbeitern."}],
                "emails": ["info@d.de"], "links": [], "errors": []}
        lead = {"name": "Dach", "branche": "handwerk", "category": "craft=roofer", "website": "https://d.de", "kreis": "01059"}
        info = {"01059": {"name": "Schleswig-Flensburg", "gewerbe_top": True, "bau_top": True}}
        base = scoring.score(lead, site)["score"]
        res = scoring.score(lead, site, region=info)
        self.assertEqual(res["score"], base + 13)
        reasons = [r for _, r in res["signals"]]
        self.assertIn("Viele Baugenehmigungen im Kreis Schleswig-Flensburg, volle Auftragsbücher absehbar", reasons)
        pflege = scoring.score({**lead, "branche": "pflege", "category": "healthcare=home_care"}, site, region=info)
        self.assertNotIn("Viele Baugenehmigungen", " ".join(r for _, r in pflege["signals"]))  # nur Bau-Gewerke


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

    def test_hotspots(self):
        pts = [{"id": i, "name": f"K{i}", "lat": 54.32 + i * 0.001, "lon": 10.12, "fit_score": 60 + i, "passend": True,
                "stage": "neu" if i == 0 else None, "city": "Kiel"} for i in range(3)]
        pts += [{"id": 9, "name": "F", "lat": 54.78, "lon": 9.43, "fit_score": 90, "passend": True, "stage": None,
                 "city": "Flensburg"},
                {"id": 10, "name": "X", "lat": 54.78, "lon": 9.43, "fit_score": 20, "passend": False, "stage": None,
                 "city": "Flensburg"}]
        hs = crm.hotspots(pts)
        self.assertEqual([(h["name"], h["count"]) for h in hs], [("Kiel", 3), ("Flensburg", 1)])
        self.assertEqual((hs[0]["in_pipeline"], hs[0]["top"][0]["name"]), (1, "K2"))  # beste zuerst

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


class ClaudeResearchTest(unittest.TestCase):
    CSV = ("Firma;Webseite;E-Mail;Telefon;Ansprechperson;Anrede;Straße;PLZ;Ort;Branche;Kategorie;Mitarbeitende;"
           "Zeitfresser;Aufhänger;Begründung;Quelle;Breite;Länge\n"
           "Tischlerei A;tischlerei-a.de;jens.hansen@tischlerei-a.de;04642 1;Jens Hansen;Guten Tag Herr Hansen,;"
           "Hafenstr. 1;24376;Kappeln;handwerk;Tischlerei;14;Aufmaß und Angebote|Tourenplanung;"
           "Auf Ihrer Webseite habe ich gesehen, dass Sie eine Bürokraft suchen.;Sucht Büro;"
           "https://tischlerei-a.de/impressum;54,66;9,93\n"
           "Ohne Kontakt;;;;;;;;Kiel;pflege;Pflege;;;;;;;\n"
           "Tischlerei A doppelt;https://www.tischlerei-a.de;;;;;;;Kappeln;handwerk;Tischlerei;;;;;;;\n")

    def setUp(self):
        self.c = leaddb.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
        agent.CFG["db_path"] = ":memory:"

    def test_prompt_lists_columns_and_placeholders(self):
        brain = leaddb.BRAIN_SEED.read_text(encoding="utf-8")
        template = leaddb.brain_sections(brain, [agent.TEMPLATE_HEADING])
        prompt = claude_research.build_prompt(brain, {"LEADS_REGION": "DE-SH"}, 30, "pflege", {"alt.example"}, template)
        self.assertIn(";".join(claude_research.HEADERS), prompt)
        self.assertIn("Finde 30 Betriebe in ganz Schleswig-Holstein", prompt)
        self.assertIn("{aufhaenger}", prompt)
        self.assertIn("alt.example", prompt)
        self.assertIn("Wen wir suchen", prompt)
        self.assertIn("{aufhaenger}", claude_research.placeholders(template))

    def test_import_score_and_template(self):
        result = claude_research.import_csv(self.c, self.CSV.encode("utf-8-sig"), "t", (54.6614, 9.9311))
        self.assertEqual(result["neu"], 1)
        self.assertEqual([line for line, _ in result["uebersprungen"]], [3, 4])  # ohne Kontakt, doppelt
        lead = leaddb.get_lead(self.c, self.c.execute("SELECT id FROM leads").fetchone()[0])
        self.assertEqual((lead["status"], lead["branche"], lead["city"]), ("kandidat", "handwerk", "Kappeln"))
        self.assertEqual(lead["website"], "https://tischlerei-a.de")
        self.assertLess(lead["distance_km"], 5)
        self.assertEqual(lead["research"]["import"]["zeitfresser"], ["Aufmaß und Angebote", "Tourenplanung"])
        # zuerst bewertet, vor Kandidaten aus der Kartensuche
        self.c.execute("INSERT INTO leads (source_id, created_at, updated_at, name, branche, status) "
                       "VALUES ('osm:1', ?, ?, 'Karte', 'handwerk', 'kandidat')", (leaddb.now(), leaddb.now()))
        self.assertEqual(agent.next_candidate(self.c), lead["id"])
        site = {"pages": [{"url": "https://tischlerei-a.de", "title": "Tischlerei A", "text": "Meisterbetrieb. Fax: 04642 1"}],
                "emails": [], "links": [], "errors": []}
        with mock.patch.object(finder, "research_website", return_value=site):
            agent.stage_score(self.c, lead["id"], {})
        lead = leaddb.get_lead(self.c, lead["id"])
        self.assertIn("Etwa 14 Mitarbeitende, ideale Größe", [r for _, r in lead["research"]["signale"]])
        self.assertEqual(lead["research"]["zeitfresser"][0], "Aufmaß und Angebote")
        self.assertEqual(lead["email"], "jens.hansen@tischlerei-a.de")
        fields = agent.template_draft(self.c, lead["id"])
        self.assertEqual(fields["greeting"], "Guten Tag Herr Hansen,")
        self.assertIn("dass Sie eine Bürokraft suchen", fields["body"])
        self.assertIn("Dazu kommt bei Ihnen vermutlich noch: Aufmaß und Angebote und Tourenplanung.", fields["body"])

    def test_codeblock_and_missing_header(self):
        text = "```csv\nFirma,Webseite,Ort,Branche,Kategorie\nHof B,hof-b.example,Süderbrarup,landwirtschaft,Hof\n```"
        self.assertEqual(claude_research.import_csv(self.c, text, "t", (54.66, 9.93))["neu"], 1)
        with self.assertRaises(claude_research.CsvError):
            claude_research.import_csv(self.c, "a;b\n1;2\n", "t", (54.66, 9.93))


class TemplateUpgradeTest(unittest.TestCase):
    def test_old_template_is_replaced_once_custom_stays(self):
        c = leaddb.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
        seed = leaddb.BRAIN_SEED.read_text(encoding="utf-8")
        old = seed.replace("Dazu kommt bei Ihnen vermutlich noch", agent.OLD_TEMPLATE_MARK)
        leaddb.save_brain(c, old.replace("## Über uns", "## Über uns\n\n- Eigene Zeile"), "t", "alt")
        self.assertTrue(agent.upgrade_template(c))
        brain = leaddb.brain(c)
        self.assertNotIn(agent.OLD_TEMPLATE_MARK, brain)
        self.assertIn("{verstaendnis}", brain)
        self.assertIn("- Eigene Zeile", brain)  # der Rest des Gehirns bleibt
        self.assertFalse(agent.upgrade_template(c))  # nur einmal
        c2 = leaddb.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
        leaddb.save_brain(c2, seed.replace("{beispiel}", "Unser eigener Text."), "t", "eigen")
        self.assertFalse(agent.upgrade_template(c2))  # selbst geänderte Vorlage bleibt
        self.assertIn("Unser eigener Text.", leaddb.brain(c2))


class ProfileTest(unittest.TestCase):
    def test_profile_and_missing(self):
        lead = {"name": "A", "city": "Kappeln", "distance_km": 3.2, "kreis": "01059", "category_label": "Tischlerei",
                "website": "https://www.a.example/", "email": "chef@a.example", "contact_name": "",
                "research": json.dumps({"beschreibung": "Küchen nach Maß aus Angeln.", "groesse": "ca. 14 Mitarbeitende"})}
        p = crm.profile(lead)
        self.assertEqual((p["beschreibung"], p["quelle"], p["groesse"], p["kreis"], p["web"]),
                         ("Küchen nach Maß aus Angeln.", "Webseite", "ca. 14 Mitarbeitende", "Schleswig-Flensburg", "a.example"))
        self.assertEqual(p["fehlt"], ["Ansprechperson"])
        p = crm.profile({"name": "B", "research": json.dumps({"groesse": "unklar"})})
        self.assertEqual(p["fehlt"], ["Was sie machen", "Größe", "Ansprechperson", "E-Mail"])

    def test_meta_description_is_read(self):
        parser = finder._TextExtractor()
        parser.feed('<html><head><meta name="description" content="Wir  bauen Dächer."></head><body>x</body></html>')
        self.assertEqual(parser.description, "Wir bauen Dächer.")


class ScheduleTest(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(tempfile.mkdtemp(), "t.db")
        self.c = leaddb.connect(self.db_path)
        agent.CFG["db_path"] = self.db_path
        agent.CFG["settings"]["LEADS_WORKER"] = True
        agent.RUN.update(id=None, cancel=False)

    def form(self, **kw):
        data = {"enabled": "1", "start": "08:00", "end": "18:00", "every": "30", "bewerten": "20", "top": "5",
                "neu_suchen_h": "24", "entwuerfe": "2", "budget_eur": "1,50", "tag_max_eur": "3", "erinnerungen": "1"}
        data.update(kw)
        days = data.pop("days", ["0", "1", "2", "3", "4"])

        class F(dict):
            def getlist(self, key):
                return days if key == "days" else []
        return F(data)

    def test_window_and_interval(self):
        cfg = schedule.parse("frei", self.form())
        monday9 = datetime(2026, 10, 19, 9, 0)
        self.assertTrue(schedule.in_window(cfg, monday9))
        self.assertFalse(schedule.in_window(cfg, monday9.replace(hour=18)))  # bis 18:00, nicht mehr um 18:00
        self.assertFalse(schedule.in_window(cfg, datetime(2026, 10, 18, 9, 0)))  # Sonntag
        self.assertTrue(schedule.due(cfg, monday9, None))
        self.assertFalse(schedule.due(cfg, monday9, (monday9 - timedelta(minutes=20)).isoformat()))
        self.assertTrue(schedule.due(cfg, monday9, (monday9 - timedelta(minutes=30)).isoformat()))
        night = schedule.parse("frei", self.form(start="22:00", end="06:00", days=["4"]))  # Freitagnacht
        self.assertTrue(schedule.in_window(night, datetime(2026, 10, 23, 23, 0)))
        self.assertTrue(schedule.in_window(night, datetime(2026, 10, 24, 5, 0)))  # Samstag früh gehört zu Freitag
        self.assertFalse(schedule.in_window(night, datetime(2026, 10, 24, 23, 0)))
        self.assertEqual(schedule.next_start(cfg, datetime(2026, 10, 18, 20, 0), None), datetime(2026, 10, 19, 8, 0))
        self.assertEqual(schedule.describe(cfg), "Mo–Fr 08:00–18:00 Uhr, alle 30 Min.")

    def test_validation(self):
        with self.assertRaises(schedule.ScheduleError):
            schedule.parse("frei", self.form(every="5"))  # mindestens 10 Minuten
        with self.assertRaises(schedule.ScheduleError):
            schedule.parse("frei", self.form(start="25:00"))
        with self.assertRaises(schedule.ScheduleError):
            schedule.parse("ki", self.form(entwuerfe="0", erinnerungen=""))
        self.assertEqual(schedule.parse("ki", self.form())["budget_eur"], 1.5)

    def test_starts_runs_by_itself(self):
        schedule.save(self.c, "frei", schedule.parse("frei", self.form(days=[str(d) for d in range(7)],
                                                                    start="00:00", end="23:59")))
        now = datetime.now().replace(hour=12)
        run_id = agent.start_scheduled(self.c, now)
        self.assertTrue(run_id)
        run = self.c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        params = json.loads(run["params"])
        self.assertEqual((run["started_by"], params["bewerten"], params["entwuerfe"], params["suchen"]),
                         ("Automatik · kostenlos", 20, 0, True))  # noch nie gesucht: neu suchen
        self.assertIsNone(agent.start_scheduled(self.c, now + timedelta(minutes=10)))  # Abstand 30 Minuten
        self.c.execute("UPDATE runs SET status='fertig'")
        self.c.commit()
        leaddb.kv_set(self.c, "last_search", {"at": (now + timedelta(minutes=5)).isoformat()})
        second = agent.start_scheduled(self.c, now + timedelta(minutes=31))
        self.assertFalse(json.loads(self.c.execute("SELECT params FROM runs WHERE id=?", (second,)).fetchone()[0])["suchen"])

    def test_ai_runs_need_key_and_respect_day_cap(self):
        schedule.save(self.c, "ki", schedule.parse("ki", self.form(days=[str(d) for d in range(7)], start="00:00",
                                                                end="23:59", tag_max_eur="0,50")))
        now = datetime.now().replace(hour=12)
        with mock.patch.object(ai, "available", return_value=False):
            self.assertIsNone(agent.start_scheduled(self.c, now))
        self.assertIn("KI-Schlüssel", leaddb.kv_get(self.c, "schedule_state")["ki"]["note"])
        leaddb.record_usage(self.c, "entwurf", "m", {"input": 1, "output": 1, "cache_write": 0, "cache_read": 0}, 1.0)
        with mock.patch.object(ai, "available", return_value=True):
            self.assertIsNone(agent.start_scheduled(self.c, now + timedelta(hours=1)))
        self.assertIn("Tagesgrenze", leaddb.kv_get(self.c, "schedule_state")["ki"]["note"])


class TeamTest(unittest.TestCase):
    def setUp(self):
        self.c = leaddb.connect(os.path.join(tempfile.mkdtemp(), "team.db"))

    def lead(self, name, **kw):
        fields = dict(source_id=f"t:{name}", created_at=leaddb.now(), updated_at=leaddb.now(), name=name,
                      branche="handwerk", category_label="Tischlerei", status="passt", email=f"{name}@x.example")
        fields.update(kw)
        cur = self.c.execute(f"INSERT INTO leads ({','.join(fields)}) VALUES ({','.join('?' * len(fields))})",
                             list(fields.values()))
        self.c.commit()
        return cur.lastrowid

    def test_members_and_passwords(self):
        self.assertEqual([m["name"] for m in team.members(self.c)], ["Finn Adams", "Hamza Al-Shebli", "Oskar Jacobsen"])
        self.assertFalse(team.check_password(self.c, "finn", ""))  # ohne Passwort keine Anmeldung
        with self.assertRaises(team.TeamError):
            team.set_password(self.c, "finn", "kurz")
        team.set_password(self.c, "finn", "geheim-123")
        self.assertTrue(team.check_password(self.c, "finn", "geheim-123"))
        self.assertFalse(team.check_password(self.c, "finn", "geheim-124"))
        self.assertTrue(team.get(self.c, "finn")["pw_hash"].startswith("scrypt:"))  # nie im Klartext
        self.assertFalse(team.check_password(self.c, "niemand", "geheim-123"))

    def test_sender_signature_and_template(self):
        snd = team.sender(self.c, "hamza", "Ylva Labs")
        self.assertEqual(snd["display"], "Hamza Al-Shebli | Ylva Labs")
        self.assertEqual(team.signature(snd), "Viele Grüße\nHamza Al-Shebli\nYlva Labs")
        team.save_settings(self.c, "hamza", gruss="Beste Grüße", signatur="Hamza Al-Shebli\nTel. 0151 000",
                           vorlage="Betreff: Hallo {firma}\nAnrede: Moin,\n\nIch bin {absender}.\n\n{aufhaenger}")
        lead_id = self.lead("Hansen")
        fields = agent.template_draft(self.c, lead_id, "hamza")
        self.assertEqual(fields, {"subject": "Hallo Hansen", "greeting": "Moin,", "body": "Ich bin Hamza Al-Shebli."})
        other = agent.template_draft(self.c, lead_id, "finn")  # Finn hat keine eigene: die aus dem Gehirn
        self.assertIn("Hansen", other["subject"])
        sent = []
        smtp = mock.Mock(send_message=lambda m: sent.append(m))
        settings = dict(agent.CFG["settings"], OUTREACH_USER="k@ylvalabs.de", OUTREACH_PASSWORD="x", IMAP_HOST="")
        with mock.patch.dict(agent.CFG, {"settings": settings, "smtp_connect": lambda u, p: smtp,
                                         "db_path": ":memory:"}):
            agent.send_new(self.c, leaddb.get_lead(self.c, lead_id), "hansen@x.example", "Hallo", "Text", "x", sender="hamza")
        self.assertEqual(str(sent[0]["From"]), "Hamza Al-Shebli | Ylva Labs <k@ylvalabs.de>")
        self.assertIn("Beste Grüße\nHamza Al-Shebli\nTel. 0151 000", sent[0].get_content())
        self.assertEqual(leaddb.get_lead(self.c, lead_id)["owner"], "hamza")  # wer schreibt, ist zuständig
        self.assertEqual(self.c.execute("SELECT sent_by FROM messages").fetchone()[0], "Hamza Al-Shebli")

    def test_personal_settings(self):
        with self.assertRaises(team.TeamError):
            team.parse_prefs({"LEADS_RULE_MIN": "120"})
        values = team.parse_prefs({"LEADS_RULE_MIN": "65", "LEADS_RUN_BUDGET_EUR": "1,5", "LEADS_RUN_CHECK": "",
                                   "MY_MONTHLY_BUDGET_EUR": "2"})
        self.assertEqual(values, {"LEADS_RULE_MIN": 65, "LEADS_RUN_BUDGET_EUR": 1.5, "MY_MONTHLY_BUDGET_EUR": 2.0})
        team.save_settings(self.c, "finn", einstellungen=values)
        base = agent.CFG["settings"]["LEADS_RULE_MIN"]
        with agent.acting_as(self.c, "finn"):
            self.assertEqual(agent.S("LEADS_RULE_MIN"), 65)  # Finns eigener Wert
            self.assertEqual(agent.S("LEADS_RUN_CHECK"), agent.CFG["settings"]["LEADS_RUN_CHECK"])  # Team-Standard
        with agent.acting_as(self.c, "hamza"):
            self.assertEqual(agent.S("LEADS_RULE_MIN"), base)  # Hamza hat nichts eigenes
        self.assertEqual(agent.S("LEADS_RULE_MIN"), base)  # danach wieder für alle
        tokens = {"input": 1000, "output": 100, "cache_write": 0, "cache_read": 0}
        leaddb.record_usage(self.c, "entwurf", "m", tokens, 2.0, person="finn")
        self.assertLess(agent.person_budget_left_eur(self.c, "finn"), 2.0)  # 2 US-Dollar gezählt, in Euro umgerechnet
        self.assertIsNone(agent.person_budget_left_eur(self.c, "hamza"))  # kein eigenes Limit

    def test_own_crm_and_stats(self):
        a = self.lead("A", stage="neu", owner="finn")
        b = self.lead("B", stage="kontaktiert", owner="oskar", sent_at=leaddb.now())
        free = self.lead("C", stage="neu")
        self.lead("D", stage="kunde", owner="oskar", sent_at=leaddb.now(), replied_at=leaddb.now(), deal_eur=5000)
        ids = lambda cols: {c["id"] for col in cols for c in col[3]}
        self.assertEqual(ids(crm.board(self.c, "finn", "finn")), {a})  # nur die eigenen
        self.assertEqual(ids(crm.board(self.c, "meine", "finn")), {a})
        self.assertIn(free, ids(crm.board(self.c, "alle", "finn")))  # noch niemandem zugeordnet: nur im Gesamt
        self.assertEqual(len(ids(crm.board(self.c, "alle", "finn"))), 4)
        self.assertNotIn(free, ids(crm.board(self.c, "oskar", "finn")))
        crm.claim(self.c, free, "hamza")
        crm.claim(self.c, b, "hamza")  # schon zugeordnet: bleibt bei Oskar
        self.assertEqual(leaddb.get_lead(self.c, free)["owner"], "hamza")
        self.assertEqual(leaddb.get_lead(self.c, b)["owner"], "oskar")
        people, total = team.stats(self.c)
        oskar = next(p for p in people if p["key"] == "oskar")
        self.assertEqual((oskar["im_crm"], oskar["angeschrieben"], oskar["antworten"], oskar["kunden"], oskar["umsatz"]),
                         (2, 2, 1, 1, 5000))
        self.assertEqual(oskar["quote"], 50)
        self.assertEqual(total["im_crm"], 4)


class KnownTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "known.db")
        self.c = leaddb.connect(self.path)

    def test_keys_match_variants(self):
        known.remember(self.c, "Tischlerei Hansen GmbH & Co. KG", "Kappeln", "https://www.tischlerei-hansen.de/kontakt",
                       "Info@Hansen.de", "osm:node/1", "osm", "Finn Adams")
        self.assertTrue(known.find(self.c, website="http://tischlerei-hansen.de"))  # ohne www, anderer Pfad
        self.assertTrue(known.find(self.c, email="info@hansen.de"))
        self.assertTrue(known.find(self.c, source_id="osm:node/1"))
        self.assertTrue(known.find(self.c, name="Tischlerei Hansen", city="kappeln"))  # ohne Rechtsform
        self.assertIsNone(known.find(self.c, name="Tischlerei Hansen", city="Husum"))  # gleicher Name, anderer Ort
        self.assertIsNone(known.find(self.c, website="https://andere.de", email="x@andere.de"))
        self.assertEqual(known.name_key("Müller & Söhne GmbH", "Kiel"), "müller söhne|kiel")

    def test_search_skips_known_even_after_removal(self):
        found = [{"source_id": "osm:node/7", "name": "Dach Petersen", "branche": "handwerk", "category": "craft=roofer",
                  "category_label": "Dachdeckerei", "niche": "dach", "address": "", "city": "Schleswig", "lat": 54.5,
                  "lon": 9.5, "distance_km": 10, "website": "https://dach-petersen.de", "email": "", "phone": "",
                  "filter_reason": None}]
        agent.CFG["db_path"] = self.path
        with mock.patch.object(finder, "search_osm", return_value=found):
            self.assertEqual(agent.run_search(self.c)[0], 1)
            lead_id = self.c.execute("SELECT id FROM leads WHERE name='Dach Petersen'").fetchone()[0]
            self.assertEqual(known.count(self.c), 1)
            self.c.execute("DELETE FROM leads WHERE id=?", (lead_id,))  # z. B. später entfernt
            self.c.commit()
            self.assertEqual(agent.run_search(self.c)[0], 0)  # kommt trotzdem nicht wieder
            moved = [dict(found[0], source_id="osm:way/99", website="https://www.dach-petersen.de/")]
            with mock.patch.object(finder, "search_osm", return_value=moved):
                self.assertEqual(agent.run_search(self.c)[0], 0)  # anderer Karteneintrag, gleiche Webseite
        self.assertEqual(leaddb.kv_get(self.c, "last_search")["known"], 1)
        rows = known.listing(self.c)
        self.assertEqual(rows[0]["name"], "Dach Petersen")
        self.assertIsNone(rows[0]["status"])  # Betrieb weg, Eintrag bleibt

    def test_import_and_manual_are_checked(self):
        known.remember(self.c, "Pflegedienst Sonne", "Kiel", "https://pflege-sonne.de", "", "", "osm", "Hamza Al-Shebli")
        csv_text = ("Firma;Webseite;Ort\nPflegedienst Sonne;https://pflege-sonne.de;Kiel\n"
                    "Neuer Betrieb;https://neu.example;Kiel\n")
        r = claude_research.import_csv(self.c, csv_text.encode(), "Oskar Jacobsen", (54.6, 9.9))
        self.assertEqual(r["neu"], 1)
        self.assertIn("Hamza Al-Shebli", r["uebersprungen"][0][1])
        self.assertTrue(known.find(self.c, website="https://neu.example"))  # der neue steht jetzt auch drauf
        lead_id, why = crm.create_lead(self.c, {"name": "Pflegedienst Sonne GmbH", "city": "Kiel"}, "Finn Adams")
        self.assertIsNone(lead_id)
        self.assertIn("Merkliste", why)
        self.assertNotIn("pflege-sonne.de", claude_research.build_prompt(leaddb.brain(self.c), {"LEADS_REGION": ""} | {
            "LEADS_CENTER": (54.6, 9.9), "LEADS_RADIUS_KM": 50}, 10, "", set()))
        self.assertIn("pflege-sonne.de", claude_research.known_hosts(self.c))

    def test_researched_sentences_fill_template(self):
        csv_text = ("Firma;Webseite;Ort;Branche;Aufhänger;Verständnis;Zeitfresser;Beispiel\n"
                    "Bau Jensen;https://bau-jensen.example;Husum;handwerk;Sie suchen gerade eine Bürokraft für die "
                    "Auftragsannahme.;Bei vielen Baustellen gleichzeitig laufen Rückfragen oft übers Telefon ins Büro.;"
                    "die Übertragung von Angaben aus Anrufen in Auftragsunterlagen;Denkbar wäre zum Beispiel, dass "
                    "eine Sprachnotiz von der Baustelle als fertiger Auftragseintrag im Büro ankommt.\n")
        claude_research.import_csv(self.c, csv_text.encode(), "Finn Adams", (54.6, 9.9))
        lead_id = self.c.execute("SELECT id FROM leads WHERE name='Bau Jensen'").fetchone()[0]
        body = agent.template_draft(self.c, lead_id)["body"]
        self.assertIn("Sie suchen gerade eine Bürokraft", body)
        self.assertIn("Rückfragen oft übers Telefon ins Büro", body)  # recherchiert statt Branchen-Satz
        self.assertIn("vermutlich noch: die Übertragung von Angaben aus Anrufen in Auftragsunterlagen.", body)
        self.assertIn("Sprachnotiz von der Baustelle", body)
        self.assertNotIn("Tagsüber sind Sie und Ihr Team", body)
        prompt = claude_research.build_prompt(leaddb.brain(self.c), {"LEADS_REGION": "DE-SH"}, 10)
        self.assertIn("Verständnis;Zeitfresser;Beispiel", prompt)
        self.assertIn("Eingabe → Verarbeitung → Ergebnis", prompt)
        self.assertIn("Erfinde keine Probleme", prompt)

    def test_backfill_existing_leads(self):
        self.c.execute("INSERT INTO leads (source_id, created_at, updated_at, name, city, website, status) "
                       "VALUES ('osm:node/5', '2026-01-01T10:00:00', '2026-01-01T10:00:00', 'Alt GmbH', 'Kiel', "
                       "'https://alt.example', 'aussortiert')")
        self.c.commit()
        self.assertEqual(known.backfill(self.c), 1)
        self.assertEqual(known.backfill(self.c), 0)
        self.assertEqual(known.find(self.c, website="https://alt.example")["first_seen_at"], "2026-01-01T10:00:00")


class SecurityTest(unittest.TestCase):
    def test_team_login(self):
        client = app.test_client()
        csrf = client.get("/login").get_data(as_text=True).split('name="csrf-token" content="')[1].split('"')[0]
        c = leaddb.connect(os.path.join(TMP, "mailer.db"))
        team.set_password(c, "finn", "finn-passwort-1")
        r = client.post("/login", data={"member": "finn", "password": "falsch"}, headers={"X-CSRF-Token": csrf})
        self.assertEqual(r.status_code, 401)
        r = client.post("/login", data={"member": "hamza", "password": "irgendwas"}, headers={"X-CSRF-Token": csrf})
        self.assertIn("noch kein Passwort", r.get_data(as_text=True))  # ohne Passwort keine Anmeldung
        r = client.post("/login?next=/team", data={"member": "finn", "password": "finn-passwort-1"},
                        headers={"X-CSRF-Token": csrf})
        self.assertEqual(r.headers["Location"], "/team")
        html = client.get("/crm").get_data(as_text=True)
        self.assertIn("Finn Adams", html)
        self.assertIn('class="on">Finn <em class="you">du</em>', html)  # automatisch das eigene CRM
        self.assertNotIn(">Meine<", html)
        csrf = html.split('name="csrf-token" content="')[1].split('"')[0]
        # eigenes Passwort ändern nur mit dem bisherigen
        r = client.post("/profil/passwort", data={"current": "falsch", "new": "neu-passwort-2", "again": "neu-passwort-2"},
                        headers={"X-CSRF-Token": csrf})
        self.assertFalse(team.check_password(c, "finn", "neu-passwort-2"))
        client.post("/profil/passwort", data={"current": "finn-passwort-1", "new": "neu-passwort-2",
                                              "again": "neu-passwort-2"}, headers={"X-CSRF-Token": csrf})
        self.assertTrue(team.check_password(c, "finn", "neu-passwort-2"))
        # erstes Passwort für jemanden ohne Passwort: erlaubt; bestehendes fremdes nur als Admin
        client.post("/profil/passwort", data={"member": "hamza", "new": "hamza-pass-1", "again": "hamza-pass-1"},
                    headers={"X-CSRF-Token": csrf})
        self.assertTrue(team.check_password(c, "hamza", "hamza-pass-1"))
        with mock.patch.dict("app.main.SETTINGS", {"ADMIN_USERS": ["oskar"]}):
            r = client.post("/profil/passwort", data={"member": "hamza", "new": "boese-pass-1", "again": "boese-pass-1"},
                            headers={"X-CSRF-Token": csrf})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(team.check_password(c, "hamza", "hamza-pass-1"))
        for key in ("finn", "hamza"):
            c.execute("UPDATE team SET pw_hash=NULL WHERE key=?", (key,))
        c.commit()

    def test_login_redirect_stays_in_app(self):
        from app.main import safe_next
        self.assertEqual(safe_next("/leads/suche?x=1"), "/leads/suche?x=1")
        for bad in ("//evil.example", "/\\evil.example", "https://evil.example", "/\tevil", ""):
            self.assertEqual(safe_next(bad), "", bad)

    def test_push_only_to_browser_services(self):
        self.assertTrue(webpush.valid_endpoint("https://fcm.googleapis.com/fcm/send/abc"))
        self.assertTrue(webpush.valid_endpoint("https://web.push.apple.com/QAbc"))
        self.assertTrue(webpush.valid_endpoint("https://updates.push.services.mozilla.com/wpush/v2/x"))
        for bad in ("https://192.168.1.10:8006/api2/json", "https://evil.example/push.apple.com",
                    "https://push.apple.com.evil.example/x", "http://fcm.googleapis.com/x",
                    "https://fcm.googleapis.com:8443/x"):
            self.assertFalse(webpush.valid_endpoint(bad), bad)

    def test_csv_export_neutralizes_formulas(self):
        self.assertEqual(crm._cell('=HYPERLINK("http://x","klick")'), '\'=HYPERLINK("http://x","klick")')
        self.assertEqual(crm._cell("@SUM(A1)"), "'@SUM(A1)")
        self.assertEqual(crm._cell("Tischlerei"), "Tischlerei")
        self.assertEqual(crm._cell(2.5), "2,5")

    def test_peer_must_be_public(self):
        class Sock:
            def __init__(self, ip):
                self.ip, self.closed = ip, False

            def getpeername(self):
                return (self.ip, 443)

            def close(self):
                self.closed = True
        with mock.patch.object(finder, "ALLOW_PRIVATE", False):
            sock = Sock("192.168.178.20")
            with self.assertRaises(PermissionError):
                finder._check_peer(sock)
            self.assertTrue(sock.closed)
            finder._check_peer(Sock("93.184.216.34"))  # öffentlich: in Ordnung

    def test_headers(self):
        client = app.test_client()
        r = client.get("/login")
        self.assertIn("script-src 'self'", r.headers["Content-Security-Policy"])
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        for tpl in Path(__file__).resolve().parent.parent.glob("templates/*.html"):
            html = tpl.read_text(encoding="utf-8")
            self.assertNotRegex(html, r"<script>|\son[a-z]+=\"", tpl.name)  # keine Inline-Skripte (CSP)


class NicheTest(unittest.TestCase):
    def setUp(self):
        self.c = leaddb.connect(os.path.join(tempfile.mkdtemp(), "n.db"))

    def test_weights(self):
        self.assertEqual(niches.equal(["a", "b", "c"]), {"a": 34, "b": 33, "c": 33})
        self.assertEqual(sum(niches.load(self.c).values()), 100)  # ohne Auswahl: die passenden Nischen, gleich verteilt
        self.assertEqual(set(niches.load(self.c)), set(niches.DEFAULT_KEYS))
        w = niches.parse(["tischlerei", "video", "werbeagentur"],
                         {"werbeagentur": "70", "tischlerei": "15", "video": "15"})
        self.assertEqual(w, {"tischlerei": 15, "werbeagentur": 70, "video": 15})
        for bad in ({"werbeagentur": "50", "tischlerei": "70", "video": "15"},   # über 100
                    {"werbeagentur": "50", "tischlerei": "20", "video": "15"},   # unter 100
                    {"werbeagentur": "100", "tischlerei": "0", "video": "0"}):   # 0 % gibt es nicht
            with self.assertRaises(niches.NicheError):
                niches.parse(["tischlerei", "video", "werbeagentur"], bad)
        with self.assertRaises(niches.NicheError):
            niches.parse([], None)
        niches.save(self.c, w)
        self.assertEqual(niches.load(self.c), w)
        self.assertTrue(niches.is_custom(self.c))
        niches.save(self.c, niches.parse(["tischlerei", "video"]))
        self.assertEqual(niches.load(self.c), {"tischlerei": 50, "video": 50})
        self.assertFalse(niches.is_custom(self.c))

    def test_search_and_classify(self):
        keys = ["werbeagentur", "foto", "tischlerei"]
        q = finder.overpass_query(54.6, 9.9, 50, "DE-SH", keys)
        self.assertIn('["office"="advertising_agency"]', q)
        self.assertIn('["craft"~"^(photographer|carpenter|joiner|cabinet_maker)$"]', q)
        self.assertIn('[~"^(craft|office|shop)$"~"."]["name"~"werbeagentur|', q)
        self.assertNotIn("(?", q)  # Overpass kennt keine Python-Gruppen
        self.assertNotIn("home_care", q)
        self.assertEqual(finder.classify({"office": "advertising_agency"}, keys)[3], "werbeagentur")
        self.assertEqual(finder.classify({"office": "company", "name": "Nordlicht Werbeagentur"}, keys)[:2],
                         ("kreativ", "office=company"))
        self.assertEqual(finder.classify({"craft": "roofer"}, keys)[0], "sonstige")  # nicht gewählt
        # Fotografen gelten sonst als zu klein; gezielt gewählt nicht
        self.assertIsNone(finder.rough_filter({"craft": "photographer"}, "https://f.de", "f.de", "craft=photographer"))
        self.assertIn("Verwaltung", finder.rough_filter({"craft": "photographer"}, "https://f.de", "f.de"))
        # Agenturen fallen nur raus, wenn ihr sie nicht gezielt sucht
        site = {"pages": [{"url": "https://a.de", "title": "Agentur", "text": "Ihre Werbeagentur im Norden."}],
                "emails": ["chef@a.de"], "links": []}
        self.assertTrue(scoring.score({"branche": "handwerk"}, site)["ko"])
        self.assertFalse([k for k in scoring.score({"branche": "kreativ"}, site)["ko"] if "Agentur" in k])

    def test_profiles(self):
        niches.save(self.c, niches.parse(["tischlerei", "werbeagentur"]))
        self.assertEqual(niches.load(self.c, "auto"), {"tischlerei": 50, "werbeagentur": 50})  # wie die manuelle
        self.assertFalse(niches.has_own(self.c, "auto"))
        niches.save(self.c, niches.parse(["video", "werbeagentur"], {"video": "30", "werbeagentur": "70"}), "auto")
        self.assertEqual(niches.load(self.c, "auto"), {"werbeagentur": 70, "video": 30})
        self.assertEqual(niches.load(self.c), {"tischlerei": 50, "werbeagentur": 50})  # manuelle bleibt
        self.assertEqual(niches.search_keys(self.c), ["tischlerei", "werbeagentur", "video"])  # eine Suche für beide
        self.assertTrue(niches.is_custom(self.c, "auto"))
        niches.reset(self.c, "auto")
        self.assertEqual(niches.load(self.c, "auto"), niches.load(self.c))
        cfg = schedule.load(self.c)["frei"]
        self.assertEqual(schedule.params_for("frei", cfg, self.c, datetime.now(), lambda usd: usd)[0]["nischen"], "auto")

    def test_pick_follows_weights(self):
        weights, taken = {"a": 70, "b": 15, "c": 15}, {}
        for _ in range(100):
            k = niches.pick(weights, taken, {"a", "b", "c"})
            taken[k] = taken.get(k, 0) + 1
        self.assertEqual(taken, {"a": 70, "b": 15, "c": 15})
        self.assertEqual(niches.pick(weights, {}, {"b"}), "b")  # nichts mehr in a: die nächste mit Betrieben
        self.assertIsNone(niches.pick(weights, {}, set()))
        self.assertEqual(claude_research.niche_plan({"a": 70, "b": 15, "c": 15}, 40), {"a": 28, "b": 6, "c": 6})
        self.assertEqual(sum(claude_research.niche_plan({"a": 34, "b": 33, "c": 33}, 10).values()), 10)

    def test_run_respects_weights(self):
        db_path = os.path.join(tempfile.mkdtemp(), "r.db")
        c = leaddb.connect(db_path)
        for i in range(30):
            for niche, cat in (("tischlerei", "craft=carpenter"), ("werbeagentur", "office=advertising_agency"),
                               ("dach", "craft=roofer")):
                c.execute("INSERT INTO leads (source_id, created_at, updated_at, name, branche, category, "
                          "category_label, distance_km, status, website) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (f"t:{niche}{i}", leaddb.now(), leaddb.now(), f"{niche} {i}", "handwerk", cat, "x", 5,
                           "kandidat", "https://x.example"))
        c.commit()
        niches.backfill(c)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM leads WHERE niche='werbeagentur'").fetchone()[0], 30)
        plan = {"weights": {"werbeagentur": 70, "tischlerei": 30}, "taken": {}}
        for _ in range(20):
            lead = leaddb.get_lead(c, agent.next_candidate(c, plan))
            plan["taken"][lead["niche"]] = plan["taken"].get(lead["niche"], 0) + 1
            c.execute("UPDATE leads SET status='passt' WHERE id=?", (lead["id"],))
        self.assertEqual(plan["taken"], {"werbeagentur": 14, "tischlerei": 6})  # Dachdecker nicht gewählt

    def test_prompt_and_import(self):
        brain = leaddb.brain(self.c)
        p = claude_research.build_prompt(brain, {"LEADS_REGION": "DE-SH"}, 20, "", (), "",
                                         {"werbeagentur": 70, "tischlerei": 30})
        self.assertIn("- Werbeagentur: 14 (70 %)", p)
        self.assertIn("- Tischlerei und Schreinerei: 6 (30 %)", p)
        self.assertNotIn("keine IT-Firmen oder Agenturen", p)
        self.assertIn("Nische", p)
        p = claude_research.build_prompt(brain, {"LEADS_REGION": "DE-SH"}, 20, "", (), "", {"tischlerei": 100})
        self.assertIn("keine IT-Firmen oder Agenturen", p)
        csv_text = "Firma;Webseite;Ort;Nische;Kategorie\nNordlicht;https://nordlicht.example;Kiel;Werbeagentur;Agentur\n"
        r = claude_research.import_csv(self.c, csv_text.encode(), "test", (54.6, 9.9))
        self.assertEqual(r["neu"], 1)
        row = self.c.execute("SELECT branche, niche FROM leads WHERE name='Nordlicht'").fetchone()
        self.assertEqual((row["branche"], row["niche"]), ("kreativ", "werbeagentur"))


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
        html = self.client.get("/leads/nischen").get_data(as_text=True)
        self.assertIn("Werbeagentur", html)
        self.assertIn("Gewichtung", html)
        r = self.client.post("/leads/nischen", data={"niche": ["werbeagentur", "video", "tischlerei"], "eigene": "1",
                                                     "w_werbeagentur": "70", "w_video": "20", "w_tischlerei": "20"},
                             headers={"X-CSRF-Token": self.csrf})
        self.assertIn("/leads/nischen", r.headers["Location"])  # 110 %: nicht gespeichert
        r = self.client.post("/leads/nischen", data={"niche": ["werbeagentur", "video", "tischlerei"], "eigene": "1",
                                                     "w_werbeagentur": "70", "w_video": "15", "w_tischlerei": "15"},
                             headers={"X-CSRF-Token": self.csrf})
        self.assertIn("/leads/suche", r.headers["Location"])
        self.assertEqual(niches.load(c), {"tischlerei": 15, "werbeagentur": 70, "video": 15})
        self.assertIn("Werbeagentur <i>70 %</i>", self.client.get("/leads/suche").get_data(as_text=True))
        # automatische Suche: eigene Auswahl, im Demo-Modus (kein Admin) nur ansehen
        html = self.client.get("/leads/nischen?fuer=auto").get_data(as_text=True)
        self.assertIn("dieselben Nischen wie die manuelle", html)
        r = self.client.post("/leads/nischen", data={"fuer": "auto", "niche": ["video"]}, headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(r.status_code, 403)
        self.assertIn("Nischen und Gewichtung", self.client.get("/leads/automatik").get_data(as_text=True))
        # Schnellsuche: Seiten mit Stichworten, Betriebe nach Namen
        pages = self.client.get("/suche.json").get_json()["pages"]
        auto = next(p for p in pages if p["t"] == "Automatisch suchen")
        self.assertEqual(auto["u"], "/leads/automatik")
        self.assertTrue(any(p["t"] == "Postausgang" and "SMTP" in p["k"] for p in pages))
        for p in pages:
            if p.get("u"):
                self.assertLess(self.client.get(p["u"]).status_code, 400, p["u"])
        leads = self.client.get("/suche.json?q=Tischlerei Hansen").get_json()["leads"]
        self.assertTrue(leads and leads[0]["u"].startswith("/leads/"))
        # Postfach: neue Mail an eine unbekannte Adresse (Demo: wird nicht verschickt), Glocke, Erstkontakt-Regel
        r = self.client.post("/crm/postfach/schreiben", data={"to": "neu@partner.example", "subject": "Hallo",
                                                             "text": "Kurze Frage.", "name": "Neue Partnerin"},
                             headers={"X-CSRF-Token": self.csrf})
        self.assertTrue(r.get_json()["ok"])
        cid = c.execute("SELECT id FROM leads WHERE email='neu@partner.example'").fetchone()[0]
        self.assertEqual(leaddb.get_lead(c, cid)["status"], "kontakt")
        self.assertIn("Kurze Frage.", self.client.get(r.get_json()["url"]).get_data(as_text=True))
        prospect = c.execute("SELECT id, email FROM leads WHERE status='passt' AND email IS NOT NULL LIMIT 1").fetchone()
        if prospect:
            r = self.client.post("/crm/postfach/schreiben", data={"to": prospect["email"], "subject": "x", "text": "y"},
                                 headers={"X-CSRF-Token": self.csrf})
            self.assertEqual(r.status_code, 400)  # Betrieb hat nie geschrieben: Erstkontakt per Mail ist aus
            self.assertIn("OUTREACH_EMAIL", r.get_json()["error"])
        news = self.client.get("/crm/postfach/neu.json").get_json()
        self.assertIn("count", news)
        self.assertIn('id="bellBtn"', self.client.get("/crm/postfach").get_data(as_text=True))
        self.assertEqual(self.client.get("/crm/postfach/schreiben").status_code, 200)
        self.assertTrue(self.client.post("/crm/postfach/abrufen", headers={"X-CSRF-Token": self.csrf}).get_json()["ok"])
        # Team: Demo arbeitet als Oskar; Vorlage mit Vorschau, Team-Zahlen, eigenes CRM
        self.assertIn("Meine Vorlage", self.client.get("/vorlage").get_data(as_text=True))
        r = self.client.post("/vorlage/vorschau", data={"text": "Betreff: Test {firma}\nAnrede: Moin,\n\nHallo von {absender}.",
                                                        "template": "brief", "graphic": "none"},
                             headers={"X-CSRF-Token": self.csrf})
        self.assertIn("Hallo von Oskar Jacobsen.", r.get_data(as_text=True))
        r = self.client.post("/vorlage", data={"text": "Betreff: Mein Betreff {firma}\n\nMein Text.", "template": "brief",
                                               "graphic": "none", "gruss": "Bis bald"}, headers={"X-CSRF-Token": self.csrf})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(team.settings(c, "oskar")["gruss"], "Bis bald")
        self.assertIn("Team und Zahlen", self.client.get("/team").get_data(as_text=True))
        self.assertIn("owner-filter", self.client.get("/crm?wer=alle").get_data(as_text=True))
        self.assertEqual(self.client.post("/profil/passwort", data={"new": "abcdefgh1", "again": "abcdefgh1"},
                                          headers={"X-CSRF-Token": self.csrf}).status_code, 403)  # Demo: nicht ändern
        r = self.client.post("/profil/meine-einstellungen", data={"LEADS_RUN_CHECK": "33", "LEADS_RULE_MIN": ""},
                             headers={"X-CSRF-Token": self.csrf})
        self.assertIn('value="33"', self.client.get("/leads/suche").get_data(as_text=True))  # eigener Vorschlag
        self.assertIn("Meine Einstellungen", self.client.get("/profil").get_data(as_text=True))
        self.assertIn("Team-Standards", self.client.get("/einstellungen/suche/bewertung").get_data(as_text=True))
        team.save_settings(c, "oskar", einstellungen={})
        team.save_settings(c, "oskar", vorlage="", gruss="Viele Grüße")
        html = self.client.get("/leads/schon-gefunden").get_data(as_text=True)
        self.assertIn("Schon gefunden", html)
        self.assertIn("Tischlerei Hansen", html)
        self.assertIn("Firma;Ort", self.client.get("/leads/schon-gefunden?format=csv").get_data(as_text=True))
        r = self.client.post("/push/subscribe", data={"subscription": json.dumps(
            {"endpoint": "https://192.168.1.1:8006/x", "keys": {"p256dh": "a", "auth": "b"}})},
            headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(r.status_code, 400)  # Abo auf ein Gerät im eigenen Netz: abgelehnt
        # Demo-Daten: ein Betrieb ist zum Nachfassen fällig
        due = c.execute("SELECT id FROM leads WHERE name LIKE 'Steuerbüro%'").fetchone()[0]
        self.assertIn("Erinnerung schreiben lassen", self.client.get(f"/leads/{due}").get_data(as_text=True))
        r = self.client.post(f"/leads/{due}/erinnerung/auslassen", headers={"X-CSRF-Token": self.csrf})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(leaddb.get_lead(c, due)["followup_count"], -1)
        self.assertEqual(leaddb.tab_of(leaddb.get_lead(c, due)), "versendet")
        self.assertEqual(self.client.get("/sw.js").headers["Service-Worker-Allowed"], "/")
        # CRM
        html = self.client.get("/crm?wer=alle").get_data(as_text=True)  # Gesamt: auch noch niemandem zugeordnete
        self.assertIn("Pipeline.", html)
        self.assertIn("Anrufen, vormittags", html)  # Wiedervorlage von heute aus den Beispieldaten
        self.assertEqual(self.client.get("/leads/regeln").status_code, 200)
        # Heute mit Prüf-Stapel, Prüfen per Urteil (gut / nicht gut / rückgängig)
        heute = self.client.get("/heute").get_data(as_text=True)
        self.assertIn("Neue Betriebe prüfen", heute)
        cand = c.execute("SELECT id FROM leads WHERE status='passt' AND stage IS NULL ORDER BY fit_score DESC LIMIT 2").fetchall()
        r = self.client.post(f"/leads/{cand[0][0]}/urteil", data={"gut": "1"}, headers={"X-CSRF-Token": self.csrf})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(leaddb.get_lead(c, cand[0][0])["stage"], "neu")
        self.client.post(f"/leads/{cand[1][0]}/urteil", data={"gut": "0"}, headers={"X-CSRF-Token": self.csrf})
        bad = leaddb.get_lead(c, cand[1][0])
        self.assertEqual((bad["status"], bad["rejected_by"], bad["stage"]), ("aussortiert", "team", None))
        self.client.post(f"/leads/{cand[1][0]}/urteil/zurueck", headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(leaddb.get_lead(c, cand[1][0])["status"], "passt")
        # Mail-Fenster: Vorlage wird eingesetzt (Firmenname), Demo darf „senden“ (nur markieren)
        fresh = c.execute("SELECT id, name FROM leads WHERE status='passt' AND IFNULL(body,'')='' AND email IS NOT NULL "
                          "LIMIT 1").fetchone()
        m = self.client.get(f"/leads/{fresh[0]}/mail.json").get_json()
        self.assertIn(fresh[1], m["subject"] + m["body"])
        self.assertTrue(m["can_email"] and m["editable"])
        self.assertEqual(leaddb.get_lead(c, fresh[0])["status"], "entwurf")
        r = self.client.post(f"/leads/{fresh[0]}/send", data={"subject": "Hallo", "greeting": "Moin,", "body": "Kurz."},
                             headers={"X-CSRF-Token": self.csrf})
        self.assertTrue(r.get_json()["ok"])
        sent = leaddb.get_lead(c, fresh[0])
        self.assertEqual((sent["status"], sent["stage"], sent["subject"]), ("versendet", "kontaktiert", "Hallo"))
        self.assertFalse(self.client.get(f"/leads/{fresh[0]}/mail.json").get_json()["can_email"])
        # Postfach: Demo-Antworten stehen drin, Gespräch öffnen, antworten (Demo: nur vermerkt), Stimmung setzen
        inbox = self.client.get("/crm/postfach").get_data(as_text=True)
        self.assertIn("Offen zum Antworten", inbox)
        self.assertIn("Tischlerei Hansen (Beispiel)", inbox)
        hansen = c.execute("SELECT id FROM leads WHERE name='Tischlerei Hansen (Beispiel)'").fetchone()[0]
        self.assertIn("rufen", self.client.get(f"/crm/postfach/{hansen}").get_data(as_text=True).lower())
        r = self.client.post(f"/crm/postfach/{hansen}/antwort", data={"text": "Gern, Dienstag 10 Uhr?"},
                             headers={"X-CSRF-Token": self.csrf})
        self.assertTrue(r.get_json()["ok"])
        self.assertIn("Gern, Dienstag 10 Uhr?", self.client.get(f"/crm/postfach/{hansen}").get_data(as_text=True))
        self.client.post(f"/crm/postfach/{hansen}/stimmung", data={"mood": "gut"}, headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(leaddb.get_lead(c, hansen)["mood"], "gut")
        self.assertIn("Neue Betriebe prüfen", self.client.get("/leads/pruefen").get_data(as_text=True))
        table = self.client.get("/crm?ansicht=tabelle").get_data(as_text=True)
        self.assertIn('id="crmTable"', table)
        self.assertIn("Ansprechperson", table)
        self.assertIn("Automatisch suchen", self.client.get("/leads/automatik").get_data(as_text=True))
        self.assertEqual(self.client.post("/leads/automatik/frei", data={"_csrf": self.csrf}).status_code, 403)  # Demo
        self.assertIn("Prompt kopieren", self.client.get("/leads/claude?anzahl=20&branche=pflege").get_data(as_text=True))
        r = self.client.post("/leads/claude/import", data={"_csrf": self.csrf, "text": ClaudeResearchTest.CSV})
        self.assertEqual(r.status_code, 302)
        self.assertIn("1 Betriebe übernommen", self.client.get("/leads/claude").get_data(as_text=True))
        karte = self.client.get("/crm/karte").get_data(as_text=True)
        self.assertIn("Gegend Kiel", karte)  # Beispieldaten haben Koordinaten
        self.assertIn('id="mapData"', karte)
        self.assertIn("Beispielzahlen, erfunden", karte)  # Demo-Zahlen sind gekennzeichnet
        self.assertIn("Schleswig-Flensburg", karte)
        r = self.client.post("/crm/regionen/import", headers={"X-CSRF-Token": self.csrf}, content_type="multipart/form-data",
                             data={"kind": "bau", "label": "2025", "file": (io.BytesIO(
                                 "\n".join(f"{k};{n};{100 + i}" for i, (k, (n, _, _)) in enumerate(regions.KREISE.items()))
                                 .encode()), "bau.csv")})
        self.assertEqual((r.get_json()["count"], r.get_json()["missing"]), (15, []))
        self.assertEqual(leaddb.kv_get(c, "regionen")["bau"]["01062"], 114)
        r = self.client.post("/crm/regionen/import", headers={"X-CSRF-Token": self.csrf}, content_type="multipart/form-data",
                             data={"kind": "bau", "file": (io.BytesIO(b"foo;1\n"), "x.csv")})
        self.assertEqual(r.status_code, 400)
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
        # Systemcheck ohne echte Netzwerkzugriffe
        from app import systemcheck
        with mock.patch.object(systemcheck.urllib.request, "urlopen", side_effect=OSError("gesperrt")), \
                mock.patch.object(finder, "fetch_page", return_value=("https://ylvalabs.de/", "<html></html>")):
            html = self.client.get("/leads/system").get_data(as_text=True)
        self.assertIn("Kartensuche (OpenStreetMap)", html)
        self.assertIn("overpass-api.de erreichen", html)   # Hinweis bei Fehler
        self.assertIn("DEMO_MODE ist an", html)            # Test läuft im Demo-Modus
        self.assertIn("Bewertung, CRM und Vorlage laufen trotzdem", html)  # ohne KI-Schlüssel nur ein Hinweis
        self.assertIn("Kreise und Wachstumszahlen", html)
        self.assertEqual(len(webpush.b64u_decode(self.client.get("/push/key").get_json()["key"])), 65)


if __name__ == "__main__":
    unittest.main()
