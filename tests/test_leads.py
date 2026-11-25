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
os.environ.update(DATA_DIR=TMP, SMTP_HOST="smtp.example.com", LEADS_WORKER="false", OUTREACH_EMAIL="false")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402

from app import agent, ai, crm, finder, known, leaddb, scoring, team, webpush  # noqa: E402
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

    def test_draft_from_template_and_ai(self):
        lead_id = self.add("Tischlerei A", status="passt", stage="neu", contact_name="Jens Hansen", city="Kappeln",
                           research=json.dumps({"zeitfresser": ["Angebote, Aufmaß und Nachkalkulation"], "auszug": "Küchen"}))
        fields = agent.template_draft(self.c, lead_id)
        self.assertEqual(fields["subject"], "Ein Gedanke aus der Region")
        self.assertEqual(fields["greeting"], "Guten Tag Jens Hansen,")
        self.assertTrue(fields["body"].startswith("ich bin "))  # erst vorstellen, nicht „Ich kann mir vorstellen …“
        self.assertIn("Angebote und Aufmaß direkt beim Kunden", fields["body"])  # typische Aufgaben im Handwerk
        self.assertIn("Ein kleines Rechenbeispiel:", fields["body"])
        self.assertNotIn("Ich kann mir gut vorstellen", fields["body"])
        self.assertNotIn("Angebote, Aufmaß und Nachkalkulation", fields["body"])  # steckt schon im Branchen-Satz
        self.assertNotIn("Bei Betrieben wie", fields["body"])
        self.assertIn("Sie sprechen beim Kunden kurz ein", fields["body"])  # Beispiel zur Branche
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

    def test_heartbeat_notes_when_server_was_off(self):
        from datetime import datetime, timedelta
        t0 = datetime(2026, 10, 27, 18, 0)
        for t in (t0, t0 + timedelta(minutes=1), t0 + timedelta(hours=14)):
            agent.heartbeat(t)
        gaps = leaddb.kv_get(self.c, "offline_gaps")
        self.assertEqual(gaps, [{"from": "2026-10-27T18:01:00", "to": "2026-10-28T08:00:00"}])

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
        # Unbekannte, die wir nie angeschrieben haben: still unter „Sonstige“, nicht im Zähler der Glocke
        self.assertEqual(mailbox.unread_count(self.c), 0)
        self.assertEqual([x["id"] for x in mailbox.conversations(self.c, ordner="sonstige")], [contact["id"]])
        in_tabs = {r["id"] for k, _, _ in leaddb.TABS for r in leaddb.tab_leads(self.c, k)}
        self.assertNotIn(contact["id"], in_tabs)  # Kontakte stehen im Postfach, nicht bei den Leads
        self.notify.assert_not_called()  # keine Meldung für Unbekannte
        # Antwort von uns auf den Kontakt (kein Erstkontakt, die Person hat geschrieben)
        sent = []
        smtp = mock.Mock(send_message=lambda m: sent.append(m))
        with mock.patch.dict(agent.CFG, {"settings": dict(settings, IMAP_HOST=""), "smtp_connect": lambda u, p: smtp}):
            agent.send_reply(self.c, contact, "Sehr gern!", "oskar")
            agent.send_new(self.c, contact, "lisa@partner.de", "Unterlagen", "Anbei die Infos.", "oskar")
        self.assertEqual(sent[0]["To"], "Lisa Meier <lisa@partner.de>")
        self.assertEqual((sent[1]["Subject"], sent[1]["From"].addresses[0].addr_spec), ("Unterlagen", "k@ylvalabs.de"))
        self.assertIn("Viele Grüße", sent[1].get_content())

    def test_mailbox_connection_test(self):
        import smtplib
        settings = dict(agent.CFG["settings"], OUTREACH_USER="k@ylvalabs.de", OUTREACH_PASSWORD="falsch", IMAP_HOST="imap.x")

        def fail(u, p):
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed")
        with mock.patch.dict(agent.CFG, {"settings": settings, "smtp_connect": fail}), \
                mock.patch.object(agent, "_imap", side_effect=Exception("LOGIN command error: BAD (Password required)")):
            results = agent.test_mailbox()
        self.assertEqual([(n, ok) for n, ok, _ in results], [("Postausgang (SMTP)", False), ("Posteingang (IMAP)", False)])
        self.assertIn("Passwort", results[0][2])
        ok_smtp = mock.Mock()
        with mock.patch.dict(agent.CFG, {"settings": settings, "smtp_connect": lambda u, p: ok_smtp}), \
                mock.patch.object(agent, "_imap", return_value=mock.Mock()):
            self.assertTrue(all(ok for _, ok, _ in agent.test_mailbox()))

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


class NoticeTest(unittest.TestCase):
    def test_notices_in_app_push_only_for_manual_runs(self):
        db_path = os.path.join(tempfile.mkdtemp(), "n.db")
        c = leaddb.connect(db_path)
        agent.CFG["db_path"] = db_path
        c.execute("INSERT INTO push_subs (endpoint, data, created_at) VALUES ('https://fcm.googleapis.com/x', '{}', 'x')")
        c.commit()
        with mock.patch.object(webpush, "send", return_value=True) as send, mock.patch.object(agent, "vapid", return_value={}):
            agent.notify(c, "Neue Antwort von Hansen", "Text", "/crm/postfach/1")
            self.assertEqual(send.call_count, 0)  # nur in der Glocke
            smtp = mock.Mock()
            with mock.patch.dict(agent.CFG, {"smtp_connect": smtp}), \
                    mock.patch.dict(agent.CFG["settings"], {"NOTIFY_EMAILS": ["chef@ylvalabs.de"]}):
                agent.notify(c, "Lauf fertig", "3 passen", "/leads/pruefen", push=True)
            self.assertEqual(send.call_count, 1)
            smtp.assert_not_called()  # keine Benachrichtigungen per Mail mehr
        rows, unseen = leaddb.notices(c, "finn")
        self.assertEqual(([r["title"] for r in rows], unseen), (["Lauf fertig", "Neue Antwort von Hansen"], 2))
        leaddb.notices_seen(c, "finn")
        self.assertEqual(leaddb.notices(c, "finn")[1], 0)
        self.assertEqual(leaddb.notices(c, "hamza")[1], 2)  # jede Person für sich


class TemplateUpgradeTest(unittest.TestCase):
    def test_outreach_sentence_added_once(self):
        c = leaddb.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
        seed = leaddb.BRAIN_SEED.read_text(encoding="utf-8")
        self.assertIn(agent.NEW_OUTREACH_SENTENCE, seed)
        leaddb.kv_set(c, "template_v3", True)
        old = seed.replace(agent.NEW_OUTREACH_SENTENCE, agent.OLD_OUTREACH_SENTENCE)
        old = old.replace("Betreff: Ein Gedanke aus der Region", "Betreff: {firma}: kurze Frage aus Kappeln")
        leaddb.save_brain(c, old, "t", "alt")
        team.save_settings(c, "finn", vorlage="Betreff: X\n\nMein Text. " + agent.OLD_OUTREACH_SENTENCE)
        text = agent.template_text(c, "oskar")
        self.assertIn("Ansprache neuer Kunden zu automatisieren", text)
        self.assertIn("Betreff: Ein Gedanke aus der Region", text)
        self.assertNotIn("Weniger Büroarbeit", text)
        self.assertIn("Mein Text. " + agent.NEW_OUTREACH_SENTENCE, team.settings(c, "finn")["vorlage"])
        self.assertFalse(agent.upgrade_template_lines(c))

    V2 = """## Vorlage (ohne KI)

Betreff: Eine Idee für {firma}
Anrede: {anrede}

{aufhaenger}

{verstaendnis}

Dazu kommt bei Ihnen vermutlich noch: {zeitfresser}.

{beispiel}

Ich bin Oskar Jacobsen von Ylva Labs aus Kappeln. Wir bauen solche Werkzeuge gemeinsam mit Betrieben.

Hätten Sie Lust auf ein Gespräch von 30 Minuten? Gern bei Ihnen vor Ort oder am Telefon, ganz wie es Ihnen passt.
"""

    def test_old_template_is_replaced_once_custom_stays(self):
        c = leaddb.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
        seed = leaddb.BRAIN_SEED.read_text(encoding="utf-8")
        new_section = leaddb.brain_sections(seed, [agent.TEMPLATE_HEADING]).strip()
        old = seed.replace(new_section, self.V2.strip()).replace("## Über uns", "## Über uns\n\n- Eigene Zeile")
        leaddb.save_brain(c, old, "t", "alt")
        team.save_settings(c, "finn", vorlage=self.V2.split("\n", 2)[2])  # Finns Kopie der alten Standard-Vorlage
        team.save_settings(c, "hamza", vorlage="Betreff: Meins\n\nMein eigener Text.")
        self.assertTrue(agent.upgrade_template(c))
        brain = leaddb.brain(c)
        self.assertNotIn("Dazu kommt bei Ihnen vermutlich noch", brain)
        self.assertIn("{rechenbeispiel}", brain)
        self.assertIn("- Eigene Zeile", brain)  # der Rest des Gehirns bleibt
        self.assertEqual(team.settings(c, "finn")["vorlage"], "")  # alte Standard-Kopie: wieder gemeinsam
        self.assertIn("Mein eigener Text.", team.settings(c, "hamza")["vorlage"])  # eigene bleibt
        self.assertFalse(agent.upgrade_template(c))  # nur einmal
        c2 = leaddb.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
        leaddb.save_brain(c2, old.replace("{beispiel}", "Unser eigener Text.").replace("{verstaendnis}", ""), "t", "e")
        self.assertFalse(agent.upgrade_template(c2))  # selbst geänderte Vorlage bleibt
        self.assertIn("Unser eigener Text.", leaddb.brain(c2))


class ProfileTest(unittest.TestCase):
    def test_profile_and_missing(self):
        lead = {"name": "A", "city": "Kappeln", "distance_km": 3.2, "kreis": "01059", "category_label": "Tischlerei",
                "website": "https://www.a.example/", "email": "chef@a.example", "contact_name": "",
                "research": json.dumps({"beschreibung": "Küchen nach Maß aus Angeln.", "groesse": "ca. 14 Mitarbeitende"})}
        p = crm.profile(lead)
        self.assertEqual((p["beschreibung"], p["quelle"], p["groesse"], p["web"]),
                         ("Küchen nach Maß aus Angeln.", "Webseite", "ca. 14 Mitarbeitende", "a.example"))
        self.assertEqual(p["fehlt"], ["Ansprechperson"])
        p = crm.profile({"name": "B", "research": json.dumps({"groesse": "unklar"})})
        self.assertEqual(p["fehlt"], ["Was sie machen", "Größe", "Ansprechperson", "E-Mail"])

    def test_meta_description_is_read(self):
        parser = finder._TextExtractor()
        parser.feed('<html><head><meta name="description" content="Wir  bauen Dächer."></head><body>x</body></html>')
        self.assertEqual(parser.description, "Wir bauen Dächer.")


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

    def test_writer_is_acting_person(self):
        from app import agent, ai
        lead_id = self.lead("Petersen")
        with agent.acting_as(self.c, "finn"):
            self.assertEqual(agent.writer_name(self.c, {"owner": "oskar"}), "Finn Adams")
            fields = agent.template_draft(self.c, lead_id)
        self.assertIn("Finn Adams", fields["body"])
        self.assertNotIn("Oskar", fields["body"])
        self.assertEqual(agent.writer_name(self.c, {"owner": "hamza"}), "Hamza Al-Shebli")
        self.assertIn("Es schreibt Finn Adams", ai._writer("Finn Adams"))

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
        self.assertEqual(other["subject"], "Ein Gedanke aus der Region")
        self.assertIn("Finn Adams", other["body"])
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
            team.parse_prefs({"MY_MONTHLY_BUDGET_EUR": "5000"})
        values = team.parse_prefs({"MY_MONTHLY_BUDGET_EUR": "2,5", "LEADS_RULE_MIN": "65"})  # Lead-Suche gibt es nicht mehr
        self.assertEqual(values, {"MY_MONTHLY_BUDGET_EUR": 2.5})
        team.save_settings(self.c, "finn", einstellungen=values)
        with agent.acting_as(self.c, "finn"):
            self.assertEqual(agent.S("MY_MONTHLY_BUDGET_EUR"), 2.5)  # Finns eigener Wert
            self.assertEqual(agent.S("LEADS_FOLLOWUP_DAYS"), agent.CFG["settings"]["LEADS_FOLLOWUP_DAYS"])  # Team-Standard
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
        # Passwort einer anderen Person: nur wer Personen verwalten darf (Finn als Gründer ja, Hamza nicht)
        client.post("/profil/passwort", data={"member": "hamza", "new": "hamza-pass-1", "again": "hamza-pass-1"},
                    headers={"X-CSRF-Token": csrf})
        self.assertTrue(team.check_password(c, "hamza", "hamza-pass-1"))
        other = app.test_client()
        csrf2 = other.get("/login").get_data(as_text=True).split('name="csrf-token" content="')[1].split('"')[0]
        self.assertEqual(other.post("/login", data={"member": "hamza", "password": "hamza-pass-1"},
                                    headers={"X-CSRF-Token": csrf2}).status_code, 302)
        csrf2 = other.get("/aufgaben").get_data(as_text=True).split('name="csrf-token" content="')[1].split('"')[0]
        r = other.post("/profil/passwort", data={"member": "finn", "new": "boese-pass-1", "again": "boese-pass-1"},
                       headers={"X-CSRF-Token": csrf2})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(team.check_password(c, "finn", "neu-passwort-2"))
        for key in ("finn", "hamza"):
            c.execute("UPDATE team SET pw_hash=NULL WHERE key=?", (key,))
        c.commit()

    def test_device_remembers_last_person(self):
        from app.main import _sessions
        client = app.test_client()
        csrf = lambda: client.get("/login", follow_redirects=True).get_data(as_text=True) \
            .split('name="csrf-token" content="')[1].split('"')[0]
        c = leaddb.connect(os.path.join(TMP, "mailer.db"))
        self.addCleanup(lambda: (c.execute("UPDATE team SET pw_hash=NULL"), c.execute("DELETE FROM remember_tokens"),
                                 c.commit()))
        team.set_password(c, "finn", "finn-passwort-1")
        team.set_password(c, "hamza", "hamza-pass-1")
        login = lambda member, pw="": client.post("/login", data={"member": member, "password": pw},
                                                  headers={"X-CSRF-Token": csrf()})
        self.assertEqual(login("finn").status_code, 401)  # noch nichts gemerkt: Passwort nötig
        self.assertEqual(login("finn", "finn-passwort-1").status_code, 302)
        cookie = client.get_cookie("ylva_geraet")
        self.assertTrue(cookie and cookie.http_only)
        self.assertNotIn(cookie.value, json.dumps([list(r) for r in c.execute("SELECT * FROM remember_tokens")]))
        # Sitzung weg (Neustart, abgelaufen): weiter angemeldet, ohne Login
        _sessions.clear()
        self.assertEqual(client.get("/crm").status_code, 200)
        client.post("/logout", headers={"X-CSRF-Token": csrf()})
        html = client.get("/login").get_data(as_text=True)
        self.assertIn("Weiter als Finn Adams", html)
        self.assertEqual(login("finn").status_code, 302)  # ohne Passwort
        client.post("/logout", headers={"X-CSRF-Token": csrf()})
        self.assertEqual(login("hamza").status_code, 401)  # jemand anderes: Passwort nötig
        self.assertEqual(login("hamza", "hamza-pass-1").status_code, 302)
        client.post("/logout", headers={"X-CSRF-Token": csrf()})
        self.assertEqual(login("finn").status_code, 401)  # Finn ist jetzt vergessen
        self.assertEqual(login("hamza").status_code, 302)
        # neues Passwort (von woanders gesetzt): gemerkte Geräte gelten nicht mehr
        team.set_password(c, "hamza", "hamza-pass-2")
        _sessions.clear()
        self.assertEqual(client.get("/crm").status_code, 302)
        self.assertEqual(login("hamza").status_code, 401)
        # Gerät vergessen
        self.assertEqual(login("hamza", "hamza-pass-2").status_code, 302)
        client.post("/login/vergessen", headers={"X-CSRF-Token": csrf()})
        self.assertIsNone(client.get_cookie("ylva_geraet"))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM remember_tokens").fetchone()[0], 0)

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


class ViewsTest(unittest.TestCase):
    """Seiten und Aktionen als Oskar; der Mailserver ist nachgebaut (es geht nichts raus)."""

    def setUp(self):
        from app import main
        self.client = app.test_client()
        self.client.get("/login")
        with self.client.session_transaction() as sess:
            sess["sid"] = "views-test"
        main._sessions["views-test"] = {"user": "oskar", "password": "", "since": 9e18, "address": "",
                                        "team": "oskar", "name": "Oskar Jacobsen"}
        self.addCleanup(main._sessions.pop, "views-test", None)
        self.sent = []
        smtp = mock.Mock()
        smtp.send_message.side_effect = lambda m, *a, **k: self.sent.append(m)
        for patch in (mock.patch.dict(main.SETTINGS, {"OUTREACH_USER": "k@ylvalabs.de", "OUTREACH_PASSWORD": "x",
                                                      "IMAP_HOST": ""}),
                      mock.patch.object(main, "smtp_connect", lambda u, p: smtp),
                      mock.patch.dict(agent.CFG, {"smtp_connect": lambda u, p: smtp})):
            patch.start()
            self.addCleanup(patch.stop)
        self.csrf = self._csrf("/crm")

    def _csrf(self, url):
        html = self.client.get(url).get_data(as_text=True)
        return html.split('name="csrf-token" content="')[1].split('"')[0]

    def test_pages_and_actions(self):
        import beispieldaten
        beispieldaten.seed(leaddb.connect(os.path.join(TMP, "mailer.db")))
        html = self.client.get("/crm?wer=alle").get_data(as_text=True)
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
        # Schnellsuche: Seiten mit Stichworten, Betriebe nach Namen
        pages = self.client.get("/suche.json").get_json()["pages"]
        self.assertEqual(next(p for p in pages if p["t"] == "Pipeline")["u"], "/pipeline")
        self.assertFalse(any("automatik" in (p.get("u") or "") for p in pages))  # Lead-Suche gibt es nicht mehr
        self.assertTrue(any(p["t"] == "Postausgang" and "SMTP" in p["k"] for p in pages))
        for p in pages:
            if p.get("u"):
                self.assertLess(self.client.get(p["u"]).status_code, 400, p["u"])
        leads = self.client.get("/suche.json?q=Tischlerei Hansen").get_json()["leads"]
        self.assertTrue(leads and leads[0]["u"].startswith("/leads/"))
        # Postfach: neue Mail an eine unbekannte Adresse, Glocke, Erstkontakt-Regel
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
            self.assertIn("Erstkontakt per Mail ist ausgeschaltet", r.get_json()["error"])
        news = self.client.get("/crm/postfach/neu.json").get_json()
        self.assertIn("count", news)
        self.assertIn('id="bellBtn"', self.client.get("/chat").get_data(as_text=True))
        self.assertEqual(self.client.get("/crm/postfach/schreiben").status_code, 200)
        self.assertEqual(self.client.post("/crm/postfach/abrufen", headers={"X-CSRF-Token": self.csrf}).status_code,
                         400)  # ohne Posteingang (IMAP) nichts abzurufen
        # Team: Vorlage mit Vorschau, Team-Zahlen, eigenes CRM
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
        r = self.client.post("/profil/meine-einstellungen", data={"MY_MONTHLY_BUDGET_EUR": "33"},
                             headers={"X-CSRF-Token": self.csrf})
        self.assertIn('value="33.0"', self.client.get("/profil").get_data(as_text=True))  # eigener Wert
        self.assertIn("Meine Einstellungen", self.client.get("/profil").get_data(as_text=True))
        team.save_settings(c, "oskar", einstellungen={})
        team.save_settings(c, "oskar", vorlage="", gruss="Viele Grüße")
        self.assertIn("Verbindung testen", self.client.get("/einstellungen/postfach/zugang").get_data(as_text=True))
        self.assertTrue(self.client.post("/einstellungen/postfach/test", headers={"X-CSRF-Token": self.csrf}).get_json()["results"])
        r = self.client.post("/push/subscribe", data={"subscription": json.dumps(
            {"endpoint": "https://192.168.1.1:8006/x", "keys": {"p256dh": "a", "auth": "b"}})},
            headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(r.status_code, 400)  # Abo auf ein Gerät im eigenen Netz: abgelehnt
        # Beispieldaten: ein Betrieb ist zum Nachfassen fällig
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
        self.assertEqual(self.client.get("/heute").headers["Location"], "/aufgaben")  # Web-App auf dem Handy
        self.assertEqual(self.client.get("/leads").headers["Location"], "/aufgaben")
        for alt in ("/leads/regeln", "/leads/pruefen", "/leads/automatik", "/leads/claude", "/leads/suche",
                    "/leads/nischen", "/leads/schon-gefunden", "/branchentest"):
            self.assertEqual(self.client.get(alt).status_code, 404, alt)  # Lead-Suche und Branchentest sind raus
        # Mail-Fenster: Vorlage wird eingesetzt, mit eingeschaltetem Erstkontakt per Mail senden
        from app import main
        mail_an = mock.patch.dict(main.SETTINGS, {"OUTREACH_EMAIL": True})
        mail_an.start()
        fresh = c.execute("SELECT id, name FROM leads WHERE status='passt' AND IFNULL(body,'')='' AND email IS NOT NULL "
                          "LIMIT 1").fetchone()
        m = self.client.get(f"/leads/{fresh[0]}/mail.json").get_json()
        self.assertEqual(m["subject"], "Ein Gedanke aus der Region")
        self.assertTrue(m["body"].startswith("ich bin "))
        self.assertTrue(m["can_email"] and m["editable"])
        self.assertEqual(leaddb.get_lead(c, fresh[0])["status"], "entwurf")
        r = self.client.post(f"/leads/{fresh[0]}/send", data={"subject": "Hallo", "greeting": "Moin,", "body": "Kurz."},
                             headers={"X-CSRF-Token": self.csrf})
        self.assertTrue(r.get_json()["ok"])
        sent = leaddb.get_lead(c, fresh[0])
        self.assertEqual((sent["status"], sent["stage"], sent["subject"]), ("versendet", "kontaktiert", "Hallo"))
        self.assertFalse(self.client.get(f"/leads/{fresh[0]}/mail.json").get_json()["can_email"])
        mail_an.stop()
        self.assertIn(leaddb.get_lead(c, fresh[0])["email"], self.sent[-1]["To"])
        # Postfach: Antworten aus den Beispieldaten stehen drin, Gespräch öffnen, antworten, Stimmung setzen
        self.assertEqual(self.client.get("/crm/postfach").headers["Location"], "/chat")  # Postfach ist jetzt der Chat
        inbox = self.client.get("/chat?wer=alle").get_data(as_text=True)
        self.assertIn("Wartet auf euch", inbox)
        self.assertIn("Tischlerei Hansen (Beispiel)", inbox)
        hansen = c.execute("SELECT id FROM leads WHERE name='Tischlerei Hansen (Beispiel)'").fetchone()[0]
        self.assertIn("rufen", self.client.get(f"/chat/{hansen}").get_data(as_text=True).lower())
        r = self.client.post(f"/crm/postfach/{hansen}/antwort", data={"text": "Gern, Dienstag 10 Uhr?"},
                             headers={"X-CSRF-Token": self.csrf})
        self.assertTrue(r.get_json()["ok"])
        self.assertIn("Gern, Dienstag 10 Uhr?", self.client.get(f"/chat/{hansen}").get_data(as_text=True))
        self.client.post(f"/crm/postfach/{hansen}/stimmung", data={"mood": "gut"}, headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(leaddb.get_lead(c, hansen)["mood"], "gut")
        table = self.client.get("/crm?ansicht=tabelle").get_data(as_text=True)
        self.assertIn('id="crmTable"', table)
        self.assertIn("Ansprechperson", table)
        self.assertEqual(self.client.get("/crm/karte").status_code, 404)  # Karte gibt es nicht mehr
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
        self.assertIn("Erstkontakt per Mail ist ausgeschaltet", r.get_json()["error"])
        crm.set_stage(c, new_id, "antwort")  # nach einer Antwort ist Mail in Ordnung
        r = self.client.post("/send", data=form, headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(r.get_json()["sent"], ["neu@firma.example"])
        lead = leaddb.get_lead(c, new_id)
        self.assertEqual(lead["status"], "versendet")
        self.assertIn("Gestaltete Mail", c.execute("SELECT text FROM lead_events WHERE lead_id=? AND kind='sent'",
                                                   (new_id,)).fetchone()[0])
        # Systemcheck ohne echte Netzwerkzugriffe
        with mock.patch.object(finder, "fetch_page", side_effect=OSError("gesperrt")):
            html = self.client.get("/leads/system").get_data(as_text=True)
        self.assertIn("Webseiten lesen", html)
        self.assertNotIn("Kartensuche", html)  # Lead-Suche gibt es nicht mehr
        self.assertIn("Bewertung, CRM und Vorlage laufen trotzdem", html)  # ohne KI-Schlüssel nur ein Hinweis
        self.assertNotIn("Kreise und Wachstumszahlen", html)  # Karte und Kreisdaten gibt es nicht mehr
        self.assertEqual(len(webpush.b64u_decode(self.client.get("/push/key").get_json()["key"])), 65)


if __name__ == "__main__":
    unittest.main()
