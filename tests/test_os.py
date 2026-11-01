"""Tests für das Firmen-OS, Stufe 1: Personen, Rollen und Rechte, Schutz gegen Aussperren, Bereiche,
Firmen-Protokoll, Aufgaben und Anfragen, Wiedervorlagen, Kommentare mit @-Erwähnung, Mein Tag.
Aufruf: python -m unittest discover tests"""
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

if "DATA_DIR" not in os.environ:  # allein gestartet; sonst setzt test_leads die Umgebung
    os.environ.update(DATA_DIR=tempfile.mkdtemp(), SMTP_HOST="smtp.example.com", LEADS_WORKER="false",
                      DEMO_MODE="true")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import aufgaben, crm, firma, leaddb, main, team  # noqa: E402

HEUTE = date.today().isoformat()
MORGEN = (date.today() + timedelta(days=1)).isoformat()
GESTERN = (date.today() - timedelta(days=1)).isoformat()


def frische_db():
    return leaddb.connect(os.path.join(tempfile.mkdtemp(), "os.db"))


class StartwerteTest(unittest.TestCase):
    def test_startwerte(self):
        c = frische_db()
        self.assertEqual({p["key"]: p["rolle"] for p in firma.personen(c)},
                         {"finn": "gruender", "hamza": "recherche", "oskar": "gruender"})
        self.assertEqual(sorted(firma.verwalter(c)), ["finn", "oskar"])
        self.assertEqual(firma.bereich(c, "akquise")["verantwortlich"], "hamza")
        self.assertEqual(firma.bereich(c, "akquise")["eltern"], "markt")
        r = firma.rechte(c, "hamza")
        self.assertEqual((r["leads"], r["crm"], r["aufgaben"]), (2, 2, 2))
        self.assertEqual((r["einstellungen"], r["personen"], r["kosten"]), (0, 0, 0))
        # Startwerte laufen nur einmal: was in der App geändert wird, bleibt
        c.execute("UPDATE team SET rolle='recherche' WHERE key='finn'")
        c.commit()
        c2 = leaddb.connect(c.execute("PRAGMA database_list").fetchone()[2])
        self.assertEqual(firma.person(c2, "finn")["rolle"], "recherche")

    def test_wiedervorlagen_werden_aufgaben(self):
        """Bestehende Wiedervorlagen (Tabelle tasks) werden einmalig zu Aufgaben mit Bezug auf den Lead."""
        c = frische_db()
        for name, owner in [("A", "oskar"), ("B", None)]:
            c.execute("INSERT INTO leads (name, created_at, updated_at, owner, stage) VALUES (?,?,?,?, 'neu')",
                      (name, HEUTE, HEUTE, owner))
        c.execute("INSERT INTO tasks (lead_id, due, text, created_at, created_by) VALUES (1, ?, 'Anrufen', ?, 'Oskar')",
                  (HEUTE, HEUTE))
        c.execute("INSERT INTO tasks (lead_id, due, text, created_at, done_at) VALUES (2, ?, 'Mail', ?, ?)",
                  (HEUTE, HEUTE, HEUTE))
        c.execute(aufgaben.UEBERNAHME)
        c.execute(aufgaben.UEBERNAHME)  # zweimal schadet nicht
        rows = [dict(r) for r in c.execute("SELECT * FROM aufgaben ORDER BY alt_task_id")]
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]["person"], rows[0]["status"], rows[0]["objekt_id"]), ("oskar", "offen", 1))
        self.assertEqual((rows[1]["person"], rows[1]["status"]), ("hamza", "erledigt"))  # ohne Zuständige: Bereich
        self.assertEqual([t["text"] for t in crm.open_tasks(c)], ["Anrufen"])


class AussperrenTest(unittest.TestCase):
    def setUp(self):
        self.c = frische_db()

    def test_letzter_verwalter_bleibt(self):
        c = self.c
        firma.status_setzen(c, "oskar", "finn", "gesperrt")  # einer bleibt: geht
        self.assertEqual(firma.verwalter(c), ["oskar"])
        with self.assertRaises(firma.FirmaError):
            firma.person_aendern(c, "oskar", "oskar", "Oskar Jacobsen", "recherche", bereiche_neu=["markt"])
        self.assertEqual(firma.person(c, "oskar")["rolle"], "gruender")  # zurückgerollt
        with self.assertRaises(firma.FirmaError):  # Rolle so ändern, dass niemand mehr verwalten darf
            firma.rolle_speichern(c, "oskar", "gruender", "Gründer", dict(firma.GRUENDER, personen=3))
        self.assertEqual(firma.rolle(c, "gruender")["rechte"]["personen"], 4)
        with self.assertRaises(firma.FirmaError):
            firma.status_setzen(c, "oskar", "oskar", "archiviert")  # sich selbst nicht

    def test_eigenes_recht_nicht_entziehen(self):
        c = self.c
        with self.assertRaises(firma.FirmaError) as ctx:  # Finn bleibt Verwalter, aber Oskar nähme es sich selbst
            firma.person_aendern(c, "oskar", "oskar", "Oskar Jacobsen", "gruender", bereiche_neu=["markt"],
                                 eigene_rechte={"personen": 2})
        self.assertIn("selbst", str(ctx.exception))
        firma.person_aendern(c, "finn", "oskar", "Oskar Jacobsen", "gruender", bereiche_neu=["markt"],
                             eigene_rechte={"personen": 2})  # Finn darf das bei Oskar
        self.assertFalse(firma.darf(c, "oskar", "personen", 4))
        self.assertTrue(firma.darf(c, "oskar", "einstellungen", 4))

    def test_verantwortliche_nicht_archivieren(self):
        with self.assertRaises(firma.FirmaError):
            firma.status_setzen(self.c, "oskar", "hamza", "archiviert")  # verantwortet Akquise
        firma.bereich_speichern(self.c, "oskar", "akquise", "Akquise & Recherche", "oskar", "markt")
        firma.status_setzen(self.c, "oskar", "hamza", "archiviert")
        self.assertNotIn("hamza", [p["key"] for p in firma.personen(self.c)])
        self.assertIn("hamza", team.names(self.c))  # Name bleibt für Akten und Verläufe

    def test_notfall(self):
        c = self.c
        firma.status_setzen(c, "oskar", "finn", "gesperrt")
        c.execute("UPDATE team SET rolle='recherche', status='gesperrt' WHERE key='oskar'")  # „aus Versehen“
        c.commit()
        self.assertEqual(firma.verwalter(c), [])
        token = firma.notfall(c, "oskar")
        self.assertEqual(firma.verwalter(c), ["oskar"])
        self.assertEqual(firma.einladung(c, token)["key"], "oskar")


class PersonenTest(unittest.TestCase):
    def test_anlegen_und_einladen(self):
        c = frische_db()
        key = firma.person_anlegen(c, "oskar", "Omid Beispiel", "recherche", "omid@example.com", ["akquise"], 1)
        self.assertEqual(key, "omid")
        self.assertEqual(firma.person_anlegen(c, "oskar", "Omid Zweiter", "recherche"), "omid-2")
        with self.assertRaises(firma.FirmaError):
            firma.person_anlegen(c, "oskar", "Omid Beispiel", "recherche")  # gleicher Name
        token = firma.einladen(c, "oskar", key)
        self.assertNotIn(token, str([tuple(r) for r in c.execute("SELECT * FROM team")]))  # nur der Hash
        with self.assertRaises(ValueError):
            firma.einladung_einloesen(c, token, "kurz")
        firma.einladung_einloesen(c, token, "omid-passwort-1")
        self.assertTrue(team.check_password(c, key, "omid-passwort-1"))
        self.assertIsNone(firma.einladung(c, token))  # nur einmal
        token2 = firma.einladen(c, "oskar", key)
        c.execute("UPDATE team SET einladung_bis='2000-01-01T00:00:00' WHERE key=?", (key,))
        self.assertIsNone(firma.einladung(c, token2))  # abgelaufen
        firma.einladung_tage_setzen(c, "oskar", 3)
        self.assertEqual(firma.einladung_tage(c), 3)
        with self.assertRaises(firma.FirmaError):
            firma.einladung_tage_setzen(c, "oskar", 0)
        arten = [e["art"] for e in firma.protokoll(c, objekt=("person", key))]
        self.assertIn("einladung", arten)
        self.assertIn("person", arten)

    def test_eigene_rechte_und_sperre(self):
        c = frische_db()
        firma.person_aendern(c, "oskar", "hamza", "Hamza Al-Shebli", "recherche", bereiche_neu=["akquise"],
                             eigene_rechte={"kosten": 1})
        self.assertTrue(firma.darf(c, "hamza", "kosten"))
        self.assertFalse(firma.darf(c, "hamza", "einstellungen"))
        firma.status_setzen(c, "oskar", "hamza", "gesperrt")
        self.assertEqual(set(firma.rechte(c, "hamza").values()), {0})  # gesperrt: gar nichts
        self.assertNotIn("hamza", [m["key"] for m in team.members(c)])


class AufgabenTest(unittest.TestCase):
    def setUp(self):
        self.c = frische_db()

    def notices(self, person):
        return [r["title"] for r in leaddb.notices(self.c, person, 50, person)[0]]

    def test_pflichtfelder_und_standardperson(self):
        c = self.c
        with self.assertRaises(aufgaben.AufgabeError):
            aufgaben.anlegen(c, "oskar", "", "markt", HEUTE)
        with self.assertRaises(aufgaben.AufgabeError):
            aufgaben.anlegen(c, "oskar", "Angebot", "gibtsnicht", HEUTE)
        with self.assertRaises(aufgaben.AufgabeError):
            aufgaben.anlegen(c, "oskar", "Angebot", "markt", "kein-datum")
        aid = aufgaben.anlegen(c, "oskar", "Server prüfen", "technik", MORGEN)
        self.assertEqual(aufgaben.get(c, aid)["person"], "finn")  # ohne Person: verantwortlich für den Bereich
        self.assertIn("Neue Aufgabe von Oskar Jacobsen", self.notices("finn"))
        self.assertNotIn("Neue Aufgabe von Oskar Jacobsen", self.notices("hamza"))  # Meldung nur für Finn

    def test_anfrage_annehmen_ablehnen(self):
        c = self.c
        aid = aufgaben.anlegen(c, "hamza", "Neue Testbranche anlegen", "technik", MORGEN, person="hamza",
                               anfrage=True)
        a = aufgaben.get(c, aid)
        self.assertEqual((a["art"], a["status"], a["person"], a["anfrage_von"]), ("anfrage", "angefragt", "finn", "hamza"))
        self.assertEqual([x["id"] for x in aufgaben.anfragen_an(c, "finn")], [aid])
        self.assertEqual(aufgaben.offen_zaehlen(c, "finn"), 1)
        self.assertIn("Anfrage von Hamza Al-Shebli", self.notices("finn"))
        self.assertFalse(aufgaben.darf_entscheiden(c, "hamza", a))
        self.assertTrue(aufgaben.darf_entscheiden(c, "finn", a))
        with self.assertRaises(aufgaben.AufgabeError):
            aufgaben.erledigen(c, "finn", aid)  # erst annehmen
        aufgaben.annehmen(c, "finn", aid, HEUTE)
        self.assertEqual(aufgaben.get(c, aid)["status"], "offen")
        self.assertIn("Angenommen: Neue Testbranche anlegen", self.notices("hamza"))
        aufgaben.erledigen(c, "finn", aid)
        self.assertIn("Erledigt: Neue Testbranche anlegen", self.notices("hamza"))
        bid = aufgaben.anlegen(c, "hamza", "Logo ändern", "technik", MORGEN, anfrage=True)
        with self.assertRaises(aufgaben.AufgabeError):
            aufgaben.ablehnen(c, "finn", bid, "")  # ohne Grund nicht
        aufgaben.ablehnen(c, "finn", bid, "Erst nach dem Branchentest")
        self.assertEqual(aufgaben.get(c, bid)["status"], "abgelehnt")
        self.assertEqual(aufgaben.mein_tag(c, "hamza")["meine_anfragen"][0]["antwort"], "Erst nach dem Branchentest")
        # Anfrage an den eigenen Bereich ist einfach eine Aufgabe
        cid = aufgaben.anlegen(c, "finn", "Backup testen", "technik", MORGEN, anfrage=True)
        self.assertEqual(aufgaben.get(c, cid)["art"], "aufgabe")

    def test_aendern_und_listen(self):
        c = self.c
        aid = aufgaben.anlegen(c, "oskar", "Angebot schreiben", "markt", GESTERN, person="oskar", prio=1)
        bid = aufgaben.anlegen(c, "oskar", "Rückruf", "akquise", HEUTE, person="hamza")
        tag = aufgaben.mein_tag(c, "oskar")
        self.assertEqual([a["id"] for a in tag["ueberfaellig"]], [aid])
        self.assertTrue(tag["ueberfaellig"][0]["ueberfaellig"])
        self.assertEqual([a["id"] for a in aufgaben.liste(c, "oskar", "vergeben")], [bid])
        self.assertEqual([a["id"] for a in aufgaben.liste(c, "hamza", "meine")], [bid])
        self.assertEqual({a["id"] for a in aufgaben.liste(c, "hamza", "bereich")}, {bid})
        self.assertEqual(len(aufgaben.liste(c, "oskar", "alle")), 2)
        a = aufgaben.get(c, bid)
        omid = firma.person_anlegen(c, "oskar", "Omid Beispiel", "recherche", bereiche_neu=["akquise"])
        self.assertFalse(aufgaben.darf_aendern(c, omid, a))  # nicht beteiligt, darf Aufgaben nicht verwalten
        self.assertTrue(aufgaben.darf_aendern(c, "finn", a))  # Gründer verwalten Aufgaben
        self.assertTrue(aufgaben.darf_aendern(c, "hamza", a))
        self.assertTrue(aufgaben.aendern(c, "hamza", bid, faellig=MORGEN, person="finn"))
        self.assertEqual(aufgaben.get(c, bid)["person"], "finn")
        self.assertFalse(aufgaben.aendern(c, "hamza", bid, faellig=MORGEN))  # nichts geändert
        aufgaben.erledigen(c, "oskar", aid)
        self.assertEqual([a["id"] for a in aufgaben.liste(c, "oskar", "meine", erledigt=True)], [aid])
        aufgaben.erledigen(c, "oskar", aid, erledigt=False)
        self.assertEqual(aufgaben.get(c, aid)["status"], "offen")
        texte = [e["text"] for e in firma.protokoll(c, objekt=("aufgabe", aid))]
        self.assertTrue(any(t.startswith("Erledigt") for t in texte))
        self.assertTrue(any(t.startswith("Wieder offen") for t in texte))

    def test_wiedervorlage_an_lead(self):
        c = self.c
        c.execute("INSERT INTO leads (name, created_at, updated_at, owner, stage) VALUES ('Tischlerei', ?, ?, 'oskar', 'neu')",
                  (HEUTE, HEUTE))
        c.execute("INSERT INTO leads (name, created_at, updated_at, stage) VALUES ('Bäckerei', ?, ?, 'neu')",
                  (HEUTE, HEUTE))
        t1 = crm.add_task(c, 1, HEUTE, "Anrufen", "Oskar Jacobsen")
        t2 = crm.add_task(c, 2, MORGEN, "Nachfassen", "Regeln")
        self.assertEqual(aufgaben.get(c, t1)["person"], "oskar")
        self.assertEqual(aufgaben.get(c, t2)["person"], "hamza")  # Bereich Akquise, niemand zuständig
        self.assertEqual(aufgaben.get(c, t1)["bereich"], "akquise")
        self.assertEqual(crm.due_count(c, "oskar"), 1)
        self.assertEqual(crm.due_count(c), 1)
        self.assertEqual([t["lead_id"] for t in crm.open_tasks(c, who="hamza")], [2])
        board = {k: cards for k, _, _, cards, _ in crm.board(c)}
        self.assertEqual(board["neu"][0]["open_tasks"] + board["neu"][1]["open_tasks"], 2)
        self.assertEqual(crm.complete_task(c, t1, "Oskar Jacobsen", wer="oskar"), 1)
        self.assertEqual(crm.due_count(c), 0)
        # Regel aus den Einstellungen: Wiedervorlagen gehen an einen anderen Bereich
        leaddb.kv_set(c, "regel:wiedervorlage_bereich", "markt")
        t3 = crm.add_task(c, 2, MORGEN, "Angebot", "Regeln")
        self.assertEqual((aufgaben.get(c, t3)["bereich"], aufgaben.get(c, t3)["person"]), ("markt", "oskar"))
        # Alles am Lead steht auch im Firmen-Protokoll
        self.assertTrue(firma.protokoll(c, art="lead", objekt=("lead", 1)))


class KommentarTest(unittest.TestCase):
    def test_erwaehnung(self):
        c = frische_db()
        self.assertEqual(aufgaben.erwaehnte(c, "@Finn und @hamza, bitte. mail@oskar.de zählt nicht. @Niemand"),
                         ["finn", "hamza"])
        aid = aufgaben.anlegen(c, "oskar", "Preis klären", "markt", MORGEN, person="oskar")
        aufgaben.kommentieren(c, "oskar", "aufgabe", aid, "@Finn wie lange dauert das?")
        self.assertIn("Oskar Jacobsen hat dich erwähnt", [r["title"] for r in leaddb.notices(c, "finn", 9, "finn")[0]])
        self.assertNotIn("Oskar Jacobsen hat dich erwähnt", [r["title"] for r in leaddb.notices(c, "x", 9, "hamza")[0]])
        tag = aufgaben.mein_tag(c, "finn")
        self.assertEqual(tag["erwaehnungen"][0]["titel"], "Preis klären")
        self.assertTrue(tag["erwaehnungen"][0]["neu"])
        aufgaben.erwaehnungen_gesehen(c, "finn")
        self.assertFalse(aufgaben.erwaehnungen(c, "finn")[0]["neu"])
        aufgaben.kommentieren(c, "finn", "aufgabe", aid, "Zwei Stunden.")  # Person der Aufgabe bekommt Bescheid
        self.assertIn("Neuer Kommentar von Finn Adams", [r["title"] for r in leaddb.notices(c, "o", 9, "oskar")[0]])
        with self.assertRaises(aufgaben.AufgabeError):
            aufgaben.kommentieren(c, "finn", "aufgabe", 999, "Hallo")
        with self.assertRaises(aufgaben.AufgabeError):
            aufgaben.kommentieren(c, "finn", "aufgabe", aid, "   ")
        self.assertEqual(len(aufgaben.kommentare(c, "aufgabe", aid)), 2)


class WebTest(unittest.TestCase):
    """Seiten und Rechte über den Browser-Weg (gemeinsame Test-Datenbank, Änderungen werden zurückgesetzt)."""

    def setUp(self):
        self.client = main.app.test_client()
        self.client.get("/login")
        with self.client.session_transaction() as sess:
            sess["sid"] = "test-os"
            sess["csrf"] = "tok"
        self.als("oskar")
        self.addCleanup(main._sessions.pop, "test-os", None)
        self.c = leaddb.connect(main.DB_PATH)
        self.addCleanup(self.c.close)

    def als(self, key, **extra):
        name = {"oskar": "Oskar Jacobsen", "finn": "Finn Adams", "hamza": "Hamza Al-Shebli"}.get(key, key)
        main._sessions["test-os"] = {"user": key or "postfach@ylvalabs.de", "password": "x" if not key else "",
                                     "since": 9e18, "address": "", "team": key, "name": name, **extra}

    def post(self, url, data=None, **kw):
        return self.client.post(url, data={"_csrf": "tok", **(data or {})}, **kw)

    def test_seiten_rendern(self):
        html = self.client.get("/mein-tag").get_data(as_text=True)
        self.assertIn("Oskar", html)
        self.assertIn("os.css", html)
        self.assertIn("Neue Aufgabe", html)
        self.assertEqual(self.client.get("/").headers["Location"], "/mein-tag")
        r = self.post("/aufgaben/neu", {"titel": "Website-Text prüfen", "bereich": "technik", "schnell": "heute"},
                      headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"], r.get_data(as_text=True))
        aid = self.c.execute("SELECT MAX(id) FROM aufgaben").fetchone()[0]
        self.addCleanup(lambda: (self.c.execute("DELETE FROM aufgaben WHERE id=?", (aid,)), self.c.commit()))
        for url in ("/aufgaben", "/aufgaben?ansicht=alle", "/aufgaben?ansicht=vergeben", "/aufgaben?erledigt=1",
                    f"/aufgaben/{aid}", "/einstellungen/personen", "/einstellungen/personen/hamza",
                    "/einstellungen/rollen", "/einstellungen/rollen/recherche", "/einstellungen/rollen/neu",
                    "/einstellungen/bereiche", "/einstellungen/protokoll", "/einstellungen", "/profil"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
        self.assertIn("Website-Text prüfen", self.client.get("/aufgaben?ansicht=vergeben").get_data(as_text=True))
        r = self.post(f"/kommentar/aufgabe/{aid}", {"text": "@Finn magst du schauen?"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("<mark>@Finn</mark>", self.client.get(f"/aufgaben/{aid}").get_data(as_text=True))
        self.assertEqual(self.client.get("/aufgaben/999999").status_code, 404)

    def test_person_anlegen_und_einladung(self):
        r = self.post("/einstellungen/personen/neu", {"name": "Omid Testperson", "rolle": "recherche",
                                                      "bereiche": "akquise", "lernstufe": "1"})
        self.assertEqual(r.status_code, 302)
        key = r.headers["Location"].rsplit("/", 1)[-1]
        self.addCleanup(lambda: (self.c.execute("DELETE FROM team WHERE key=?", (key,)),
                                 self.c.execute("DELETE FROM person_bereiche WHERE person=?", (key,)), self.c.commit()))
        html = self.client.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn("Einladungslink", html)
        link = html.split('id="einladungLink" value="')[1].split('"')[0]
        self.assertNotIn(link, self.client.get(r.headers["Location"]).get_data(as_text=True))  # nur einmal gezeigt
        path = "/einladung/" + link.rsplit("/", 1)[-1]
        guest = main.app.test_client()
        page = guest.get(path).get_data(as_text=True)
        self.assertIn("Willkommen, Omid", page)
        csrf = page.split('name="csrf-token" content="')[1].split('"')[0]
        r = guest.post(path, data={"new": "omid-pass-12", "again": "omid-pass-12"}, headers={"X-CSRF-Token": csrf})
        self.assertEqual(r.headers["Location"], "/mein-tag")
        self.assertIn("Omid", guest.get("/mein-tag").get_data(as_text=True))
        self.assertEqual(guest.get("/einstellungen").status_code, 403)  # Recherche
        self.assertEqual(guest.get(path).status_code, 410)  # Link verbraucht
        # Sperren wirft die laufende Sitzung raus
        self.post(f"/einstellungen/personen/{key}/status", {"status": "gesperrt"})
        self.assertEqual(guest.get("/mein-tag").status_code, 302)

    def test_rechte(self):
        self.als("hamza")
        self.assertEqual(self.client.get("/mein-tag").status_code, 200)
        self.assertEqual(self.client.get("/leads").status_code, 200)
        self.assertEqual(self.client.get("/einstellungen").status_code, 403)
        self.assertEqual(self.client.get("/einstellungen/personen").status_code, 403)
        self.assertEqual(self.client.get("/leads/kosten").status_code, 403)
        self.assertEqual(self.post("/einstellungen/personen/neu", {"name": "X Y", "rolle": "gruender"}).status_code, 403)
        html = self.client.get("/mein-tag").get_data(as_text=True)
        self.assertNotIn('href="/einstellungen"', html)
        self.assertNotIn('href="/leads/kosten"', html)
        # Ein Modul abschalten: CRM weg für Hamza (eigenes Recht), gilt sofort
        self.c.execute("""UPDATE team SET rechte='{"crm": 0}' WHERE key='hamza'""")
        self.c.commit()
        self.addCleanup(lambda: (self.c.execute("UPDATE team SET rechte=NULL WHERE key='hamza'"), self.c.commit()))
        self.assertEqual(self.client.get("/crm").status_code, 403)
        self.assertEqual(self.post("/crm/neu", {"name": "X"}).status_code, 403)
        self.assertNotIn('href="/crm"', self.client.get("/mein-tag").get_data(as_text=True))

    def test_postfach_ohne_person(self):
        """Mit dem Postfach angemeldet: nur Mailer, und wählbar sind nur Personen ohne Passwort."""
        self.als(None)
        self.assertEqual(self.client.get("/compose").status_code, 200)
        self.assertTrue(self.client.get("/aufgaben").headers["Location"].startswith("/profil/wer"))
        self.assertEqual(self.post("/crm/neu", {"name": "X"}).status_code, 403)
        team.set_password(self.c, "finn", "finn-passwort-9")
        self.addCleanup(lambda: (self.c.execute("UPDATE team SET pw_hash=NULL WHERE key='finn'"), self.c.commit()))
        html = self.client.get("/profil/wer").get_data(as_text=True)
        self.assertNotIn("Finn Adams", html)
        self.assertIn("Hamza Al-Shebli", html)
        r = self.post("/profil/wer", {"member": "finn"})
        self.assertEqual(main._sessions["test-os"]["team"], None)  # nicht übernommen
        self.assertIn("/profil/wer", r.headers["Location"])

    def test_seiten_haben_ein_modul(self):
        """Jede Seite hinter der Anmeldung ist einem Modul zugeordnet oder bewusst frei (Mein Tag, Profil …)."""
        frei_ohne_modul = {"aufgaben.mein_tag", "aufgaben.kommentar", "team.who_am_i", "team.profile",
                           "team.password", "team.save_profile", "team.save_prefs", "team.overview",
                           "team.template_editor", "team.template_preview", "team.template_save"}
        for rule in main.app.url_map.iter_rules():
            ep = rule.endpoint
            if ep in main.FREI or ep in frei_ohne_modul or ep in ("static", "login", "logout", "demo_login",
                                                                  "forget_device", "healthz", "personen.einladung"):
                continue
            self.assertIsNotNone(main.modul_der_seite(ep), ep)


if __name__ == "__main__":
    unittest.main()
