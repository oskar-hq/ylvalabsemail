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


class WebBasis(unittest.TestCase):
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


class WebTest(WebBasis):

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
            if ep.startswith("portal."):  # Kunden-Bereich: eigene Anmeldung per Einmal-Link, kein Team-Modul
                continue
            if ep in main.FREI or ep in frei_ohne_modul or ep in ("static", "login", "logout", "demo_login",
                                                                  "forget_device", "healthz", "personen.einladung"):
                continue
            self.assertIsNotNone(main.modul_der_seite(ep), ep)


# ---------------------------------------------------------------- Stufe 2: Branchentest


def lead_anlegen(c, name, niche=None, **felder):
    cur = c.execute("INSERT INTO leads (name, created_at, updated_at, status, niche) VALUES (?,?,?, 'passt', ?)",
                    (name, HEUTE, HEUTE, niche))
    c.commit()
    if felder:
        leaddb.update_lead(c, cur.lastrowid, **felder)
    return cur.lastrowid


class BranchentestTest(unittest.TestCase):
    def setUp(self):
        from app import branchentest as bt
        self.bt = bt
        self.c = frische_db()
        self.omid = firma.person_anlegen(self.c, "oskar", "Omid Beispiel", "recherche", bereiche_neu=["akquise"],
                                         lernstufe=1)

    def test_startwerte_und_pruefer(self):
        bt, c = self.bt, self.c
        self.assertEqual([b["key"] for b in bt.branchen(c)], ["video", "design", "handwerk", "verwaltung"])
        self.assertEqual(bt.branche(c, "video")["person"], "hamza")
        self.assertIsNone(bt.branche(c, "handwerk")["person"])  # Omid trägt Oskar in der App ein
        self.assertEqual(bt.pruefer_fuer(c, self.omid), ("hamza", "oskar"))
        self.assertEqual(bt.pruefer_fuer(c, "hamza"), ("oskar", None))
        self.assertEqual(bt.pruefer_fuer(c, "oskar"), (None, None))

    def test_pruefung_omid_hamza_stichprobe_oskar(self):
        bt, c = self.bt, self.c
        leaddb.kv_set(c, "regel:branchentest", {"stufe2": 5, "stufe3": 10, "stichprobe": 2, "preisgespraeche": 3})
        ids = [lead_anlegen(c, f"Sanitär {n}", "sanitaer") for n in range(3)]
        for lid in ids:
            felder = bt.eingang(c, lid, self.omid)
            self.assertEqual(felder["testbranche"], "handwerk")  # nach Nische
            self.assertEqual(felder["pruef_status"], "wartet")  # Lernstufe 1: jeder Lead
        bt.eingang(c, ids[0], self.omid)  # zweimal schadet nicht
        offen = bt.pruefungen(c, "hamza")
        self.assertEqual(len(offen), 3)
        self.assertIn("Lead prüfen: Sanitär 0", [r["title"] for r in leaddb.notices(c, "h", 20, "hamza")[0]])
        lead = leaddb.get_lead(c, ids[0])
        with self.assertRaises(bt.BranchentestError):  # Kontakt erst nach der Prüfung
            bt.kontakt(c, self.omid, ids[0], "telefon")
        self.assertFalse(bt.darf_entscheiden(c, self.omid, offen[0]))
        self.assertTrue(bt.darf_entscheiden(c, "hamza", offen[0]))
        bt.entscheiden(c, "hamza", offen[0]["id"], True)
        bt.entscheiden(c, "hamza", offen[1]["id"], True)  # 2. bestandener: Stichprobe an Oskar (Quote 2)
        with self.assertRaises(bt.BranchentestError):
            bt.entscheiden(c, "hamza", offen[2]["id"], False, "")  # Ablehnen nur mit Grund
        bt.entscheiden(c, "hamza", offen[2]["id"], False, "Kein eigener Betrieb, nur Filiale")
        self.assertEqual(leaddb.get_lead(c, ids[2])["status"], "aussortiert")
        self.assertIn("Lead abgelehnt: Sanitär 2", [r["title"] for r in leaddb.notices(c, "o", 20, self.omid)[0]])
        stich = bt.pruefungen(c, "oskar")
        self.assertEqual([(p["objekt_id"], p["stufe"]) for p in stich], [(ids[1], "stichprobe")])
        self.assertTrue(bt.kontakt_frei(leaddb.get_lead(c, ids[1])))  # Stichprobe hält den Kontakt nicht auf
        bt.entscheiden(c, "oskar", stich[0]["id"], False, "Zu klein")
        empf = [r["title"] for r in leaddb.notices(c, "h", 30, "hamza")[0]]
        self.assertIn("Lead abgelehnt: Sanitär 1", empf)  # Hamza lernt aus Oskars Stichprobe
        kpi = bt.recherche_kpis(c, self.omid)
        self.assertEqual((kpi["entschieden"], kpi["anteil"], kpi["offen"]), (3, 67, 0))
        bt.kontakt(c, self.omid, ids[0], "telefon", "Inhaber erreicht")
        self.assertEqual(leaddb.get_lead(c, ids[0])["stage"], "kontaktiert")
        self.assertTrue(firma.protokoll(c, art="pruefung"))
        del lead

    def test_lernstufen(self):
        bt, c = self.bt, self.c
        firma.person_aendern(c, "oskar", self.omid, "Omid Beispiel", "recherche", bereiche_neu=["akquise"], lernstufe=2)
        geprueft = []
        for n in range(6):
            lid = lead_anlegen(c, f"Lead {n}")
            geprueft.append(bt.eingang(c, lid, self.omid).get("pruef_status") == "wartet")
        self.assertEqual(geprueft, [True, False, False, False, False, True])  # Stufe 2: jeder 5.
        firma.person_aendern(c, "oskar", self.omid, "Omid Beispiel", "recherche", bereiche_neu=["akquise"], lernstufe=4)
        self.assertNotIn("pruef_status", bt.eingang(c, lead_anlegen(c, "Frei"), self.omid))
        self.assertNotIn("pruef_status", bt.eingang(c, lead_anlegen(c, "Gründer"), "oskar"))  # ohne Lernstufe

    def test_auswertung_nur_kalt(self):
        bt, c = self.bt, self.c
        k1 = lead_anlegen(c, "Studio A", "video", testbranche="video", herkunft="kalt", stage="kontaktiert",
                          sent_at=HEUTE)
        k2 = lead_anlegen(c, "Studio B", "video", testbranche="video", stage="gespraech", sent_at=HEUTE,
                          replied_at=HEUTE)
        lead_anlegen(c, "Freund C", "video", testbranche="video", herkunft="warm", stage="kunde", sent_at=HEUTE)
        lead_anlegen(c, "Abgelehnt D", "video", testbranche="video", pruef_status="abgelehnt")
        self.assertTrue(bt.preisgespraech(c, "oskar", k2))
        self.assertFalse(bt.preisgespraech(c, "oskar", k2))  # schon markiert
        video = next(z for z in bt.auswertung(c) if z["key"] == "video")
        self.assertEqual((video["kalt"]["betriebe"], video["kalt"]["kontakte"], video["kalt"]["antworten"],
                          video["kalt"]["gespraeche"], video["kalt"]["preisgespraeche"], video["kalt"]["abschluesse"]),
                         (2, 2, 1, 1, 1, 0))
        self.assertEqual((video["warm"]["betriebe"], video["warm"]["abschluesse"]), (1, 1))
        self.assertEqual((video["ampel"], video["antwortquote"]), ("gelb", 50))
        for n in range(2):
            bt.preisgespraech(c, "oskar", lead_anlegen(c, f"P{n}", testbranche="video", stage="angebot"))
        self.assertEqual(next(z for z in bt.auswertung(c) if z["key"] == "video")["ampel"], "gruen")
        bt.herkunft_setzen(c, "oskar", k1, "warm", "video")
        self.assertEqual(leaddb.get_lead(c, k1)["herkunft"], "warm")

    def test_kontakt_regeln(self):
        bt, c = self.bt, self.c
        lid = lead_anlegen(c, "Elektro E", "elektro")
        with self.assertRaises(bt.BranchentestError):  # § 7 UWG: kein Erstkontakt per Mail
            bt.kontakt(c, "oskar", lid, "email")
        with self.assertRaises(bt.BranchentestError):
            bt.kontakt(c, "oskar", lid, "fax")
        bt.kontakt(c, "oskar", lid, "brief")
        lead = leaddb.get_lead(c, lid)
        self.assertEqual((lead["stage"], lead["owner"], lead["channel"]), ("kontaktiert", "oskar", "brief"))
        self.assertTrue(lead["sent_at"])

    def test_branche_und_regeln_speichern(self):
        bt, c = self.bt, self.c
        bt.branche_speichern(c, "oskar", "handwerk", "Handwerk", ["sanitaer", "elektro", "gibtsnicht"], self.omid, 40)
        b = bt.branche(c, "handwerk")
        self.assertEqual((b["person"], b["ziel"], b["nischen"]), (self.omid, 40, ["sanitaer", "elektro"]))
        key = bt.branche_speichern(c, "oskar", None, "Steuerbüros", [], None, 50)
        self.assertEqual(key, "steuerbueros")
        with self.assertRaises(bt.BranchentestError):
            bt.branche_speichern(c, "oskar", None, "", [], None, 50)
        with self.assertRaises(bt.BranchentestError):
            bt.regeln_speichern(c, "oskar", {"stufe2": "0", "stufe3": "10", "stichprobe": "5", "preisgespraeche": "3"})
        bt.regeln_speichern(c, "oskar", {"stufe2": "3", "stufe3": "10", "stichprobe": "5", "preisgespraeche": "4"})
        self.assertEqual(bt.regeln(c)["preisgespraeche"], 4)
        lid = lead_anlegen(c, "Kandidat", "elektro")
        self.assertEqual([k["id"] for k in bt.kandidaten(c, "handwerk")], [lid])


class BranchentestWebTest(WebBasis):
    def test_seiten_rendern(self):
        from app import branchentest as bt
        c = self.c
        lid = lead_anlegen(c, "Webtest Sanitär", "sanitaer")
        self.addCleanup(lambda: (c.execute("DELETE FROM leads WHERE id=?", (lid,)),
                                 c.execute("DELETE FROM freigaben WHERE objekt_id=?", (lid,)), c.commit()))
        c.execute("UPDATE team SET lernstufe=1 WHERE key='hamza'")
        c.commit()
        self.addCleanup(lambda: (c.execute("UPDATE team SET lernstufe=1 WHERE key='hamza'"), c.commit()))
        bt.eingang(c, lid, "hamza")  # Hamza auf Lernstufe 1: Oskar prüft
        for url in ("/branchentest", "/branchentest/handwerk", "/branchentest/pruefen", f"/leads/{lid}", "/mein-tag",
                    "/leads"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertIn("Webtest Sanitär", self.client.get("/branchentest/pruefen").get_data(as_text=True))
        self.assertIn("Leads prüfen", self.client.get("/mein-tag").get_data(as_text=True))
        self.assertEqual(self.post(f"/leads/{lid}/mark-sent", {"channel": "telefon"}).status_code, 409)
        fid = bt.offene_pruefung(c, lid)["id"]
        self.als("hamza")
        self.assertEqual(self.post(f"/branchentest/pruefung/{fid}", {"angenommen": "1"}).status_code, 403)
        self.assertEqual(self.post("/branchentest/regeln", {"stufe2": "5"}).status_code, 403)  # nur Verwalten
        self.als("oskar")
        r = self.post(f"/branchentest/pruefung/{fid}", {"angenommen": "1"}, headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"])
        r = self.post(f"/leads/{lid}/kontakt", {"kanal": "telefon", "notiz": "erreicht"},
                      headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"], r.get_data(as_text=True))
        self.post(f"/leads/{lid}/preisgespraech", {"an": "1"})
        self.assertTrue(leaddb.get_lead(c, lid)["preisgespraech_am"])


# ---------------------------------------------------------------- Stufe 3: Kalender und Termine


class FakeGoogle:
    """Nachgebautes Google: merkt sich jede Anfrage, legt Termine an, liefert frei/belegt."""

    def __init__(self):
        from app import google_kalender
        self.g = google_kalender
        self.calls, self.events, self.busy, self.kaputt = [], {}, [], set()

    def __call__(self, method, url, form=None, body=None, token=None):
        self.calls.append((method, url, form, body, token))
        if url == self.g.TOKEN_URL:
            if form.get("grant_type") == "authorization_code":
                return {"refresh_token": "r-" + form["code"], "access_token": "a", "scope": " ".join(self.g.SCOPES)}
            if form["refresh_token"] in self.kaputt:
                raise self.g.GoogleError("Google: invalid_grant (400)", abgelaufen=True)
            return {"access_token": "tok-" + form["refresh_token"]}
        if url == self.g.REVOKE_URL:
            return {}
        if url.endswith("/freeBusy"):
            return {"calendars": {"primary": {"busy": self.busy}}}
        if "/events" in url and method == "POST":
            eid = f"ev{len(self.events) + 1}"
            self.events[eid] = {"token": token, "body": body, "url": url}
            res = {"id": eid}
            if body.get("conferenceData"):
                res["hangoutLink"] = f"https://meet.google.com/{eid}"
            return res
        if "/events/" in url and method in ("PATCH", "DELETE"):
            return {"id": url.split("/events/")[1].split("?")[0]}
        raise AssertionError(url)

    def anfragen(self, method, teil):
        return [x for x in self.calls if x[0] == method and teil in x[1]]


class KalenderTest(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        from app import google_kalender, kalender
        self.k = kalender
        self.fake = FakeGoogle()
        p1 = mock.patch.object(google_kalender, "_http", self.fake)
        p2 = mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "sec"})
        p1.start(), p2.start()
        self.addCleanup(p1.stop), self.addCleanup(p2.stop)
        self.c = frische_db()
        self.morgen10 = (date.today() + timedelta(days=1)).isoformat() + "T10:00"

    def verbinden(self, *personen):
        for p in personen:
            self.k.konto_speichern(self.c, p, "r-" + p)

    def test_interner_termin_in_alle_kalender(self):
        k, c = self.k, self.c
        self.verbinden("oskar", "finn")
        tid = k.anlegen(c, "oskar", "Wochenplanung", self.morgen10, dauer=30, personen=["finn", "hamza"])
        t = k.get(c, tid)
        self.assertEqual((t["status"], t["art"], t["ende"][11:]), ("geplant", "intern", "10:30"))
        self.assertEqual(sorted(e["token"] for e in self.fake.events.values()), ["tok-r-finn", "tok-r-oskar"])
        self.assertTrue(all("attendees" not in e["body"] for e in self.fake.events.values()))  # keine Einladungen
        self.assertTrue(all("sendUpdates=none" in e["url"] for e in self.fake.events.values()))
        self.assertIsNone(t["sync_fehler"])  # Hamza ist nicht verbunden: kein Fehler, nur kein Eintrag
        self.assertIn("Neuer Termin: Wochenplanung", [r["title"] for r in leaddb.notices(c, "f", 9, "finn")[0]])
        with self.assertRaises(k.KalenderError):
            k.anlegen(c, "oskar", "Kaputt", self.morgen10, ende=self.morgen10)

    def test_kundentermin_braucht_freigabe(self):
        k, c = self.k, self.c
        self.verbinden("oskar")
        lid = lead_anlegen(c, "Tischlerei Kunde", contact_name="Frau Hansen")
        tid = k.anlegen(c, "hamza", "Erstgespräch", self.morgen10, verantwortlich="oskar", personen=["hamza"],
                        gaeste="kunde@tischlerei.de", lead_id=lid, meet=True)
        t = k.get(c, tid)
        self.assertEqual((t["status"], t["art"], t["gaeste"]), ("vorschlag", "kunde", ["kunde@tischlerei.de"]))
        self.assertEqual(self.fake.events, {})  # nichts an den Kunden ohne Freigabe
        self.assertEqual([x["id"] for x in k.freigaben_fuer(c, "oskar")], [tid])
        with self.assertRaises(k.KalenderError):
            k.freigeben(c, "hamza", tid, True)  # nur die verantwortliche Person
        with self.assertRaises(k.KalenderError):
            k.freigeben(c, "oskar", tid, False, "")  # Verwerfen nur mit Grund
        k.freigeben(c, "oskar", tid, True)
        t = k.get(c, tid)
        ev = next(iter(self.fake.events.values()))
        self.assertEqual(ev["body"]["attendees"], [{"email": "kunde@tischlerei.de"}])
        self.assertIn("sendUpdates=all", ev["url"])
        self.assertIn("conferenceDataVersion=1", ev["url"])
        self.assertEqual((t["status"], t["meet_link"]), ("geplant", "https://meet.google.com/ev1"))
        self.assertIn("Einladung raus: Erstgespräch", [r["title"] for r in leaddb.notices(c, "h", 20, "hamza")[0]])
        self.assertTrue(firma.protokoll(c, art="freigabe"))
        # Verantwortlich und selbst freigegeben: geht sofort raus
        tid2 = k.anlegen(c, "oskar", "Folgetermin", self.morgen10, gaeste="kunde@tischlerei.de", lead_id=lid,
                         einladung_freigeben=True)
        self.assertEqual(k.get(c, tid2)["status"], "geplant")
        # Absagen: Google sagt dem Gast ab
        k.absagen(c, "oskar", tid2, "Kunde krank")
        self.assertIn("sendUpdates=all", self.fake.anfragen("DELETE", "/events/")[-1][1])

    def test_ohne_verbindung_und_abgelaufen(self):
        k, c = self.k, self.c
        tid = k.anlegen(c, "oskar", "Kunde", self.morgen10, gaeste="a@b.de", einladung_freigeben=True)
        self.assertIn("nicht verbunden", k.get(c, tid)["sync_fehler"])
        self.verbinden("finn")
        self.fake.kaputt.add("r-finn")
        k.anlegen(c, "finn", "Intern", self.morgen10)
        self.assertEqual(k.konto(c, "finn")["status"], "abgelaufen")
        self.assertIn("Google-Kalender neu verbinden", [r["title"] for r in leaddb.notices(c, "f", 9, "finn")[0]])
        self.assertFalse(k.verbunden(c, "finn"))

    def test_lead_in_pruefung_bekommt_keinen_termin(self):
        lid = lead_anlegen(self.c, "Wartet", pruef_status="wartet")
        with self.assertRaises(self.k.KalenderError):
            self.k.anlegen(self.c, "oskar", "Zu früh", self.morgen10, lead_id=lid)

    def test_vorschlaege_um_alle_herum(self):
        k, c = self.k, self.c
        r = dict(k.regeln(c))
        tag_ = date.today() + timedelta(days=1)
        while tag_.weekday() > 4:
            tag_ += timedelta(days=1)
        d = tag_.isoformat()
        c.execute("INSERT INTO belegt (person, start, ende) VALUES ('finn', ?, ?)", (d + "T09:00", d + "T12:00"))
        k.anlegen(c, "oskar", "Schon belegt", d + "T12:30", dauer=60)
        out = k.vorschlaege(c, ["oskar", "finn"], dauer=45, ab=datetime_combine(tag_))
        erster = out[0]
        self.assertEqual(erster["start"], d + "T14:00")  # nach Finns Block und Oskars Termin plus 15 Min. Puffer
        self.assertTrue(all(datetime_fromiso(s["start"]).weekday() in r["tage_liste"] for s in out))
        self.assertTrue(all("09:00" <= s["start"][11:] and s["ende"][11:] <= "18:00" for s in out))
        self.assertEqual(k.konflikte(c, ["finn", "hamza"], d + "T10:00", d + "T11:00"), ["Finn Adams"])

    def test_belegt_abgleich_nur_zeiten(self):
        k, c = self.k, self.c
        self.verbinden("oskar")
        self.fake.busy = [{"start": "2030-01-07T09:00:00Z", "end": "2030-01-07T10:00:00Z"}]
        self.assertTrue(k.belegt_abgleichen(c, "oskar"))
        row = c.execute("SELECT start, ende FROM belegt WHERE person='oskar'").fetchone()
        self.assertEqual((row["start"], row["ende"]), ("2030-01-07T10:00", "2030-01-07T11:00"))  # Winterzeit +1
        raw = c.execute("SELECT refresh_token FROM kalender_konten WHERE person='oskar'").fetchone()[0]
        self.assertNotIn("r-oskar", raw)  # verschlüsselt gespeichert

    def test_takt_erinnert_und_fragt_nach(self):
        from datetime import datetime as dt
        k, c = self.k, self.c
        bald = (dt.now() + timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M")
        vorbei = (dt.now() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
        a = k.anlegen(c, "oskar", "Gleich", bald, dauer=30, personen=["finn"])
        b = k.anlegen(c, "oskar", "War schon", vorbei, dauer=30)
        z = k.takt(c)
        self.assertEqual((z["erinnert"], z["ergebnis"]), (2, 1))  # 24h und 1h für „Gleich“
        self.assertIn("In einer Stunde: Gleich", [r["title"] for r in leaddb.notices(c, "f", 9, "finn")[0]])
        self.assertIn("Wie war der Termin? War schon", [r["title"] for r in leaddb.notices(c, "o", 9, "oskar")[0]])
        self.assertEqual(k.takt(c)["erinnert"], 0)  # nur einmal
        self.assertEqual([t["id"] for t in k.offene_ergebnisse(c, "oskar")], [b])
        del a

    def test_ergebnis_schiebt_lead_und_zaehlt(self):
        from datetime import datetime as dt
        from app import aufgaben as au
        k, c = self.k, self.c
        lid = lead_anlegen(c, "Studio X", stage="kontaktiert")
        tid = k.anlegen(c, "oskar", "Erstgespräch", (dt.now() - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M"),
                        lead_id=lid)
        with self.assertRaises(k.KalenderError):
            k.ergebnis(c, "oskar", tid, "super")
        k.ergebnis(c, "oskar", tid, "stattgefunden", "will Angebot", preisgespraech=True, folge_titel="Angebot schicken",
                   folge_faellig=MORGEN)
        lead = leaddb.get_lead(c, lid)
        self.assertEqual(lead["stage"], "gespraech")
        self.assertTrue(lead["preisgespraech_am"])
        self.assertEqual([a["titel"] for a in au.zum_objekt(c, "lead", lid)], ["Angebot schicken"])
        self.assertEqual(k.offene_ergebnisse(c, "oskar"), [])
        self.assertTrue(firma.protokoll(c, art="termin_ergebnis"))
        b = k.briefing(c, k.get(c, tid))
        self.assertEqual(b["fakten"][0], ("Betrieb", "Studio X"))
        self.assertEqual(b["aufgaben"][0]["titel"], "Angebot schicken")

    def test_serie_und_abo(self):
        k, c = self.k, self.c
        sid = k.serie_anlegen(c, "oskar", "Wochengespräch", 0, "17:00", 60, "oskar", ["finn", "hamza"])
        n = c.execute("SELECT COUNT(*) FROM termine WHERE serie_id=?", (sid,)).fetchone()[0]
        self.assertIn(n, (5, 6))
        self.assertEqual(k.serien_auffuellen(c), 0)  # schon da
        alle = c.execute("SELECT start FROM termine WHERE serie_id=?", (sid,)).fetchall()
        self.assertTrue(all(datetime_fromiso(r["start"]).weekday() == 0 and r["start"][11:] == "17:00" for r in alle))
        token = k.ics_token_erneuern(c, "finn")
        self.assertEqual(k.ics_person(c, token), "finn")
        text = k.ics(c, "finn")
        self.assertIn("SUMMARY:Wochengespräch", text)
        self.assertIn("BEGIN:VCALENDAR", text)
        k.ics_token_erneuern(c, "finn")
        self.assertIsNone(k.ics_person(c, token))  # alte Adresse gilt nicht mehr
        k.serie_beenden(c, "oskar", sid)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM termine WHERE serie_id=? AND status != 'abgesagt'",
                                   (sid,)).fetchone()[0], 0)

    def test_regeln(self):
        k, c = self.k, self.c

        class Form(dict):
            def getlist(self, key):
                return self.get(key, [])
        gut = Form(von="08:00", bis="16:00", tage=["0", "2"], dauer="30", puffer="10", abgleich="5",
                   erinnerung_24h="1", zeitzone="Europe/Berlin")
        k.regeln_speichern(c, "oskar", gut)
        r = k.regeln(c)
        self.assertEqual((r["von"], r["tage_liste"], r["dauer"], r["erinnerung_1h"]), ("08:00", [0, 2], 30, False))
        for schlecht in (dict(gut, von="17:00"), dict(gut, tage=[]), dict(gut, zeitzone="Mars/Olymp"),
                         dict(gut, dauer="viel")):
            with self.assertRaises(k.KalenderError):
                k.regeln_speichern(c, "oskar", Form(schlecht))


def datetime_combine(d):
    from datetime import datetime as dt, time
    return dt.combine(d, time(7, 0))


def datetime_fromiso(v):
    from datetime import datetime as dt
    return dt.fromisoformat(v)


class KalenderWebTest(WebBasis):
    def setUp(self):
        super().setUp()
        from unittest import mock
        from app import google_kalender
        self.fake = FakeGoogle()
        p1 = mock.patch.object(google_kalender, "_http", self.fake)
        p2 = mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "sec"})
        p1.start(), p2.start()
        self.addCleanup(p1.stop), self.addCleanup(p2.stop)
        self.addCleanup(lambda: (self.c.execute("DELETE FROM kalender_konten"), self.c.commit()))

    def test_google_verbinden(self):
        r = self.client.get("/kalender/google/verbinden")
        self.assertTrue(r.headers["Location"].startswith("https://accounts.google.com/"))
        self.assertIn("calendar.events.freebusy", r.headers["Location"])
        self.assertIn("kalender%2Fgoogle%2Fzurueck", r.headers["Location"])
        state = r.headers["Location"].split("state=")[1].split("&")[0]
        self.client.get("/kalender/google/zurueck?state=falsch&code=x")  # falscher state: nichts gespeichert
        self.assertIsNone(self.c.execute("SELECT 1 FROM kalender_konten").fetchone())
        self.client.get("/kalender/google/verbinden")
        with self.client.session_transaction() as sess:
            state = sess["google_state"]["state"]
        r = self.client.get(f"/kalender/google/zurueck?state={state}&code=abc")
        self.assertEqual(r.headers["Location"], "/kalender/mein")
        self.assertEqual(self.c.execute("SELECT status FROM kalender_konten WHERE person='oskar'").fetchone()[0], "ok")
        self.assertIn("Verbunden", self.client.get("/kalender/mein").get_data(as_text=True))
        self.post("/kalender/google/trennen")
        self.assertIsNone(self.c.execute("SELECT 1 FROM kalender_konten").fetchone())

    def test_seiten_und_rechte(self):
        tag_ = (date.today() + timedelta(days=2)).isoformat()
        r = self.post("/kalender/neu", {"titel": "Webtest Termin", "datum": tag_, "uhrzeit": "11:00", "dauer": "30",
                                        "personen": ["oskar", "finn"]}, headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"], r.get_data(as_text=True))
        tid = int(r.get_json()["url"].rsplit("/", 1)[1])
        self.addCleanup(lambda: (self.c.execute("DELETE FROM termine WHERE id=?", (tid,)),
                                 self.c.execute("DELETE FROM termin_teilnehmer WHERE termin_id=?", (tid,)),
                                 self.c.commit()))
        for url in ("/kalender", "/kalender?ansicht=alle", f"/kalender/{tid}", "/kalender/mein",
                    "/einstellungen/kalender", "/mein-tag", "/kalender/vorschlaege.json?personen=oskar&dauer=30"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertIn("Webtest Termin", self.client.get("/kalender").get_data(as_text=True))
        self.post("/kalender/abo/neu")
        link = self.client.get("/kalender/mein").get_data(as_text=True).split('id="icsLink" value="')[1].split('"')[0]
        ics = main.app.test_client().get("/kalender/" + link.rsplit("/", 1)[1])
        self.assertEqual(ics.status_code, 200)
        self.assertIn("Webtest Termin", ics.get_data(as_text=True))
        self.assertEqual(main.app.test_client().get("/kalender/falsch.ics").status_code, 404)
        self.als("hamza")
        self.assertEqual(self.client.get("/kalender").status_code, 200)
        self.assertEqual(self.post("/einstellungen/kalender/regeln", {"von": "08:00"}).status_code, 403)
        self.assertEqual(self.post(f"/kalender/{tid}/absagen").status_code, 403)  # nicht seiner


# ---------------------------------------------------------------- Stufe 4: KPIs, Ziele, Wochengespräch, Lernstufen


class KpiTest(unittest.TestCase):
    def setUp(self):
        from app import kpis
        self.kp = kpis
        self.c = frische_db()
        self.omid = firma.person_anlegen(self.c, "oskar", "Omid Beispiel", "recherche", bereiche_neu=["akquise"],
                                         lernstufe=1)
        self.woche = kpis.woche_von()

    def pruefung(self, person, status, wochen_zurueck=0, tag=1):
        from datetime import datetime as dt
        von, _ = self.kp.woche_grenzen(self.kp.woche_plus(self.woche, -wochen_zurueck))
        at = (dt.fromisoformat(von) + timedelta(days=tag, hours=10)).isoformat(timespec="seconds")
        lid = lead_anlegen(self.c, f"L{status}{wochen_zurueck}{tag}")
        self.c.execute("INSERT INTO freigaben (art, objekt_typ, objekt_id, stufe, angefragt_von, angefragt_am, pruefer, "
                       "status, entschieden_am) VALUES ('lead_pruefung','lead',?,'erst',?,?, 'hamza', ?, ?)",
                       (lid, person, at, status, at))
        self.c.commit()

    def test_wochen(self):
        kp = self.kp
        self.assertEqual(kp.woche_grenzen("2026-W45"), ("2026-11-02T00:00:00", "2026-11-09T00:00:00"))
        self.assertEqual(kp.woche_plus("2026-W01", -1), "2025-W52")
        self.assertEqual(kp.woche_text("2026-W45"), "KW 45 · 2.11. bis 8.11.")
        with self.assertRaises(kp.KpiError):
            kp.woche_grenzen("quatsch")

    def test_kennzahlen_aus_ereignissen(self):
        kp, c = self.kp, self.c
        for st in ("angenommen", "angenommen", "angenommen", "abgelehnt"):
            self.pruefung(self.omid, st)
        self.pruefung("hamza", "angenommen")
        self.pruefung(self.omid, "angenommen", wochen_zurueck=1)
        self.assertEqual(kp.wert(c, "qualifizierte_leads", self.woche, self.omid), 3)
        self.assertEqual(kp.wert(c, "qualifizierte_leads", self.woche), 4)
        self.assertEqual(kp.wert(c, "anteil_angenommen", self.woche, self.omid), 75)
        z = kp.zeile(c, "qualifizierte_leads", self.woche, self.omid)
        self.assertEqual((len(z["verlauf"]), z["verlauf"][-2:], z["vorwoche"]), (8, [1, 3], 1))
        # Termine, Kontakte, Gespräche, Kunden, Aufgaben, KI-Kosten
        from app import aufgaben, crm, kalender
        jetzt = datetime_now()
        lid = lead_anlegen(c, "Kunde K", stage="kontaktiert", owner="oskar")
        kalender.anlegen(c, self.omid, "Erstgespräch", (jetzt - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
                         verantwortlich="oskar", personen=[self.omid], lead_id=lid)
        tid = c.execute("SELECT MAX(id) FROM termine").fetchone()[0]
        kalender.ergebnis(c, "oskar", tid, "stattgefunden")
        crm.set_stage(c, lid, "kunde", author="Oskar Jacobsen")
        from app import branchentest
        branchentest.kontakt(c, self.omid, lead_anlegen(c, "Kontakt K"), "telefon")
        aid = aufgaben.anlegen(c, "oskar", "X", "markt", HEUTE, person="oskar")
        aufgaben.erledigen(c, "oskar", aid)
        leaddb.record_usage(c, "entwurf", "m", {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}, 1.0, person="oskar")
        self.assertEqual(kp.wert(c, "termine_gebucht", self.woche, self.omid), 1)
        self.assertEqual(kp.wert(c, "kontakte", self.woche, self.omid), 1)
        self.assertEqual(kp.wert(c, "erstgespraeche", self.woche, "oskar"), 1)
        self.assertEqual(kp.wert(c, "neue_kunden", self.woche), 1)
        self.assertEqual(kp.wert(c, "abschlussquote", self.woche), 100)
        self.assertEqual(kp.wert(c, "aufgaben_erledigt", self.woche, "oskar"), 1)
        self.assertGreater(kp.wert(c, "ki_kosten", self.woche, "oskar"), 0)
        self.assertIsNone(kp.wert(c, "aufgaben_ueberfaellig", kp.woche_plus(self.woche, -2)))  # nur Stand jetzt
        self.assertIsNone(kp.wert(c, "stunden_pro_build", self.woche))  # kommt mit Stufe 5

    def test_zielwerte_und_ampel(self):
        kp, c = self.kp, self.c
        self.assertEqual(kp.ziel(c, "anteil_angenommen"), 80)  # Startwert aus den Rollen
        kp.ziel_setzen(c, "oskar", "qualifizierte_leads", "40")
        kp.ziel_setzen(c, "oskar", "qualifizierte_leads", "20", self.omid)
        self.assertEqual((kp.ziel(c, "qualifizierte_leads"), kp.ziel(c, "qualifizierte_leads", self.omid)), (40, 20))
        self.assertEqual(kp.ziel(c, "qualifizierte_leads", "hamza"), 40)  # ohne eigenes: Team
        self.assertEqual([kp.ampel("qualifizierte_leads", w, 40) for w in (40, 30, 10, None)],
                         ["gruen", "gelb", "rot", "grau"])
        self.assertEqual(kp.ampel("ki_kosten", 5, 10), "gruen")
        with self.assertRaises(kp.KpiError):
            kp.ziel_setzen(c, "oskar", "kassenstand", "1000")
        kp.ziel_setzen(c, "oskar", "qualifizierte_leads", "")
        self.assertIsNone(kp.ziel(c, "qualifizierte_leads"))

    def test_wer_sieht_wessen_zahlen(self):
        kp, c = self.kp, self.c
        self.assertTrue(kp.fuehrt(c, "hamza", self.omid))  # verantwortlich für Akquise
        self.assertTrue(kp.fuehrt(c, "oskar", "hamza"))  # Markt liegt über Akquise
        self.assertFalse(kp.fuehrt(c, self.omid, "hamza"))
        self.assertFalse(kp.fuehrt(c, "finn", "hamza"))
        self.assertEqual({p["key"] for p in kp.sichtbare_personen(c, "hamza")}, {"hamza", self.omid})
        self.assertEqual({p["key"] for p in kp.sichtbare_personen(c, self.omid)}, {self.omid})
        self.assertEqual(len(kp.sichtbare_personen(c, "finn")), 4)  # Gründer verwalten das Cockpit

    def test_wochenziele(self):
        kp, c = self.kp, self.c
        self.pruefung(self.omid, "angenommen")
        zid = kp.ziel_anlegen(c, "hamza", self.omid, self.woche, "Zwei gute Leads", "qualifizierte_leads", "2")
        kp.ziel_anlegen(c, self.omid, self.omid, self.woche, "Branchenliste fertig")
        z = kp.ziele_der_woche(c, self.woche, self.omid)
        self.assertEqual((z[0]["ist"], z[0]["fortschritt"], z[0]["erreicht_live"]), (1, 50, False))
        self.assertIsNone(z[1]["kpi_key"])
        kp.ziel_anlegen(c, self.omid, self.omid, self.woche, "Drittes")
        with self.assertRaises(kp.KpiError):
            kp.ziel_anlegen(c, self.omid, self.omid, self.woche, "Viertes")  # höchstens drei
        with self.assertRaises(kp.KpiError):
            kp.ziel_anlegen(c, "oskar", "hamza", self.woche, "Mit Kennzahl", "kontakte", "")  # Zielwert fehlt
        self.assertFalse(kp.darf_ziele(c, self.omid, "hamza"))
        with self.assertRaises(kp.KpiError):
            kp.ziel_bewerten(c, "finn" if False else self.omid, zid + 99, "erreicht")
        kp.ziel_bewerten(c, self.omid, zid, "erreicht", "mit Hilfe")
        self.assertEqual(kp.ziele_der_woche(c, self.woche, self.omid)[0]["status"], "erreicht")
        self.assertTrue(firma.protokoll(c, art="ziel"))

    def test_entwurf_aenderung_messen(self):
        kp, c = self.kp, self.c
        lid = lead_anlegen(c, "Entwurf E")
        text = "Guten Tag, wir helfen kleinen Betrieben, Büroarbeit zu sparen. Hätten Sie kurz Zeit für ein Gespräch?"
        kp.entwurf_merken(c, lid, self.omid, text)
        kp.entwurf_merken(c, lid, "oskar", text + " Viele Grüße")  # Oskar ändert: Omids Stand bleibt
        self.assertGreater(kp.entwurf_freigegeben(c, lid, "oskar", text + " Viele Grüße"), 0.9)
        self.assertEqual(kp.entwuerfe(c, *kp.woche_grenzen(self.woche), self.omid), (1, 1))
        lid2 = lead_anlegen(c, "Entwurf F")
        kp.entwurf_merken(c, lid2, self.omid, text)
        kp.entwurf_freigegeben(c, lid2, "oskar", "Komplett anderer Text, neu geschrieben.")
        self.assertEqual(kp.wert(c, "entwuerfe_ohne_aenderung", self.woche, self.omid), 50)
        lid3 = lead_anlegen(c, "Selbst")
        kp.entwurf_merken(c, lid3, "oskar", text)
        self.assertIsNone(kp.entwurf_freigegeben(c, lid3, "oskar", text))  # selbst freigegeben zählt nicht

    def test_lernstufe_1_aufstieg(self):
        kp, c = self.kp, self.c
        for w in range(1, 5):  # vier abgeschlossene Wochen mit je 5 Prüfungen, 4 angenommen = 80 %
            for n, st in enumerate(["angenommen"] * 4 + ["abgelehnt"]):
                self.pruefung(self.omid, st, wochen_zurueck=w, tag=n % 5)
        stand = kp.lernstand(c, self.omid)
        self.assertTrue(stand["erfuellt"], stand)
        fid = kp.aufstieg_pruefen(c, self.omid)
        self.assertIsNotNone(fid)
        self.assertIsNone(kp.aufstieg_pruefen(c, self.omid))  # kein zweiter Vorschlag
        f = kp.aufstiege(c, "oskar")[0]
        self.assertEqual((f["person"], f["nach"]), (self.omid, 2))
        self.assertIn("Aufstieg freigeben: Omid Beispiel", [r["title"] for r in leaddb.notices(c, "o", 9, "oskar")[0]])
        with self.assertRaises(kp.KpiError):
            kp.aufstieg_entscheiden(c, self.omid, fid, True)  # nicht selbst
        with self.assertRaises(kp.KpiError):
            kp.aufstieg_entscheiden(c, "oskar", fid, False, "")
        kp.aufstieg_entscheiden(c, "oskar", fid, True)
        self.assertEqual(firma.person(c, self.omid)["lernstufe"], 2)
        self.assertEqual(kp.lernstand(c, self.omid)["stufe"], 2)
        self.assertFalse(kp.lernstand(c, self.omid)["erfuellt"])  # jetzt zählen Entwürfe

    def test_lernstufe_1_noch_nicht(self):
        kp, c = self.kp, self.c
        for w in range(1, 5):
            for n, st in enumerate(["angenommen"] * 3 + ["abgelehnt"] * 2):  # 60 %
                self.pruefung(self.omid, st, wochen_zurueck=w, tag=n % 5)
        self.assertFalse(kp.lernstand(c, self.omid)["erfuellt"])
        self.assertIsNone(kp.aufstieg_pruefen(c, self.omid))

    def test_lernstufe_3(self):
        kp, c = self.kp, self.c
        firma.person_aendern(c, "oskar", self.omid, "Omid Beispiel", "recherche", bereiche_neu=["akquise"], lernstufe=3)
        leaddb.kv_set(c, "regel:lernstufen", dict(kp.lernregeln(c), s3_termine=1, s3_gespraeche=1))
        from app import kalender
        lid = lead_anlegen(c, "Termin T")
        tid = kalender.anlegen(c, self.omid, "Gespräch", (datetime_now() - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M"),
                               verantwortlich="oskar", personen=[self.omid], lead_id=lid)
        self.assertFalse(kp.lernstand(c, self.omid)["erfuellt"])
        kalender.ergebnis(c, "oskar", tid, "stattgefunden")
        self.assertTrue(kp.lernstand(c, self.omid)["erfuellt"])

    def test_agenda(self):
        kp, c = self.kp, self.c
        kp.ziel_anlegen(c, "oskar", "hamza", self.woche, "Liste fertig")
        a = kp.agenda(c, "oskar", self.woche)
        self.assertEqual(len(a["personen"]), 4)
        hamza = next(p for p in a["personen"] if p["person"]["key"] == "hamza")
        self.assertEqual(hamza["ziele"][0]["text"], "Liste fertig")
        self.assertEqual(len(kp.agenda(c, self.omid, self.woche)["personen"]), 1)


def datetime_now():
    from datetime import datetime as dt
    return dt.now()


class CockpitWebTest(WebBasis):
    def test_seiten_und_rechte(self):
        from app import kpis
        c = self.c
        omid = firma.person_anlegen(c, "oskar", "Omid Webtest", "recherche", bereiche_neu=["akquise"], lernstufe=1)
        self.addCleanup(lambda: (c.execute("DELETE FROM team WHERE key=?", (omid,)),
                                 c.execute("DELETE FROM person_bereiche WHERE person=?", (omid,)),
                                 c.execute("DELETE FROM ziele"), c.commit()))
        for url in ("/cockpit", f"/cockpit?person={omid}", "/cockpit?woche=2026-W01", "/wochengespraech",
                    "/mein-tag"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        r = self.post("/ziele/neu", {"person": omid, "text": "Zehn Leads", "kpi": "qualifizierte_leads",
                                     "zielwert": "10"}, headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"], r.get_data(as_text=True))
        self.assertIn("Zehn Leads", self.client.get("/wochengespraech").get_data(as_text=True))
        self.assertEqual(self.post("/cockpit/ziel", {"kpi": "kontakte", "wert": "30"}).status_code, 302)
        self.addCleanup(lambda: (c.execute("DELETE FROM kpi_ziele WHERE kpi_key='kontakte'"), c.commit()))
        self.als("hamza")
        self.assertEqual(self.client.get(f"/cockpit?person={omid}").status_code, 200)  # Hamza führt Omid
        self.assertEqual(self.client.get("/cockpit?person=finn").status_code, 403)
        self.assertEqual(self.post("/cockpit/ziel", {"kpi": "kontakte", "wert": "1"}).status_code, 403)
        self.assertEqual(self.post("/cockpit/lernstufen", {"wochen": "1"}).status_code, 403)
        r = self.post("/ziele/neu", {"person": "finn", "text": "Fremdes Ziel"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("Meine Wochenziele", self.client.get("/mein-tag").get_data(as_text=True))
        self.assertEqual(kpis.woche_von(date(2026, 11, 1)), "2026-W44")


# ---------------------------------------------------------------- Stufe 5: Kunden, Projekte, Abnahme


class ProjektTest(unittest.TestCase):
    def setUp(self):
        from app import projekte
        self.pj = projekte
        self.c = frische_db()
        self.lid = lead_anlegen(self.c, "Tischlerei Hansen", stage="angebot", owner="oskar")

    def projekt(self):
        from app import crm
        crm.set_stage(self.c, self.lid, "kunde", author="Oskar Jacobsen")  # legt das Projekt automatisch an
        return self.pj.zum_lead(self.c, self.lid)["id"]

    def test_journey_mit_regeln(self):
        pj, c = self.pj, self.c
        pid = self.projekt()
        p = pj.get(c, pid)
        self.assertEqual((p["status"], p["verantwortlich"], p["technik"]), ("beauftragt", "oskar", "finn"))
        self.assertIsNone(pj.aus_kunde(c, self.lid, "oskar"))  # nur einmal
        from app import aufgaben
        titel = [a["titel"] for a in aufgaben.zum_objekt(c, "projekt", pid)]
        self.assertIn("AV-Vertrag abschließen: Tischlerei Hansen", titel)
        with self.assertRaises(pj.ProjektError):
            pj.weiter(c, "hamza", pid)  # den Schritt setzt die verantwortliche Person
        pj.weiter(c, "oskar", pid)
        with self.assertRaises(pj.ProjektError):
            pj.weiter(c, "oskar", pid)  # ohne Freigaben kein Bau
        with self.assertRaises(pj.ProjektError):
            pj.kunde_bestaetigt(c, "oskar", pid)  # erst Anforderung
        pj.aendern(c, "oskar", pid, anforderung="Anfragen automatisch sortieren und beantworten.")
        pj.kunde_bestaetigt(c, "oskar", pid)
        with self.assertRaises(pj.ProjektError):
            pj.anforderung_freigeben(c, "oskar", pid)  # ohne Schätzung
        with self.assertRaises(pj.ProjektError):
            pj.schaetzen(c, "oskar", pid, 8)  # schätzen tut die Technik
        pj.schaetzen(c, "finn", pid, "12")
        pj.anforderung_freigeben(c, "oskar", pid)
        with self.assertRaises(pj.ProjektError):
            pj.anforderung_freigeben(c, "hamza", pid)
        pj.anforderung_freigeben(c, "finn", pid)
        pj.weiter(c, "oskar", pid)
        p = pj.get(c, pid)
        self.assertEqual((p["status"], bool(p["bau_ende_plan"])), ("bau", True))
        offen = [a["titel"] for a in aufgaben.zum_objekt(c, "projekt", pid)]
        self.assertFalse([t for t in offen if t.startswith(("Anforderung schreiben", "Aufwand schätzen"))])
        self.assertIn("AV-Vertrag abschließen: Tischlerei Hansen", offen)  # Menschenarbeit bleibt
        with self.assertRaises(pj.ProjektError):
            pj.weiter(c, "finn", pid)  # Zeit ist Pflicht
        pj.nachtragen(c, "finn", "projekt", pid, 300, notiz="Grundgerüst")
        pj.weiter(c, "finn", pid)
        p = pj.get(c, pid)
        self.assertEqual((p["status"], len(p["punkte"])), ("abnahme", len(pj.CHECKLISTE)))
        with self.assertRaises(pj.ProjektError):
            pj.abnahme(c, "finn", pid)  # das letzte Wort hat Oskar
        pj.punkt(c, "oskar", pid, p["punkte"][0]["id"])
        self.assertFalse(pj.abnahme(c, "oskar", pid))  # nicht alles abgehakt: zurück in den Bau
        self.assertEqual(pj.get(c, pid)["status"], "bau")
        pj.weiter(c, "finn", pid)
        for x in pj.get(c, pid)["punkte"]:
            pj.punkt(c, "oskar", pid, x["id"])
        self.assertTrue(pj.abnahme(c, "oskar", pid))
        p = pj.get(c, pid)
        self.assertEqual((p["status"], p["kostenlos"]), ("live", True))
        self.assertEqual(len(p["abnahmen"]), 2)
        from app import kpis
        w = kpis.woche_von()
        self.assertEqual(kpis.wert(c, "freigaben_ohne_aenderung", w), 0)  # erste Abnahme nicht bestanden
        self.assertEqual(kpis.wert(c, "stunden_pro_build", w), 5)
        self.assertEqual(kpis.wert(c, "tage_bis_live", w), 0)
        pj.weiter(c, "oskar", pid)
        self.assertEqual(pj.get(c, pid)["status"], "betrieb")
        self.assertTrue(firma.protokoll(c, art="projekt", objekt=("projekt", pid)))

    def test_aenderungswuensche_und_abrechnung(self):
        pj, c = self.pj, self.c
        pid = self.projekt()
        a1 = pj.aenderung_anlegen(c, "hamza", pid, "Logo im Kopf größer")
        self.assertIn("Änderungswunsch einordnen: Tischlerei Hansen",
                      [r["title"] for r in leaddb.notices(c, "o", 9, "oskar")[0]])
        with self.assertRaises(pj.ProjektError):
            pj.einordnen(c, "hamza", a1, "klein")  # einordnen tut Oskar (oder wer Kunden verwaltet)
        pj.einordnen(c, "oskar", a1, "klein")
        self.assertEqual(pj.aenderung_get(c, a1)["abrechnung"], "inklusive")  # vor Live klein: inklusive
        # nach Live, nach den kostenlosen Tagen
        c.execute("UPDATE projekte SET status='betrieb', live_am=? WHERE id=?",
                  ((date.today() - timedelta(days=60)).isoformat() + "T10:00:00", pid))
        c.commit()
        a2 = pj.aenderung_anlegen(c, "oskar", pid, "Neuer Export als PDF")
        pj.einordnen(c, "oskar", a2, "klein")
        self.assertEqual(pj.aenderung_get(c, a2)["abrechnung"], "pauschale")
        pj.aenderung_schaetzen(c, "finn", a2, 180)  # doch groß
        a = pj.aenderung_get(c, a2)
        self.assertEqual((a["groesse"], a["abrechnung"]), ("gross", "angebot"))
        from app import aufgaben
        self.assertTrue(aufgaben.zum_objekt(c, "aenderung", a2))
        pj.aenderung_abschliessen(c, "finn", a2)
        self.assertEqual(aufgaben.zum_objekt(c, "aenderung", a2), [])
        with self.assertRaises(pj.ProjektError):
            pj.aenderung_abschliessen(c, "oskar", a1, False, "")

    def test_zeiterfassung(self):
        pj, c = self.pj, self.c
        pid = self.projekt()
        aid = pj.aenderung_anlegen(c, "oskar", pid, "Kleinigkeit")
        pj.start(c, "finn", "projekt", pid)
        self.assertEqual(pj.laufend(c, "finn")["objekt_typ"], "projekt")
        pj.start(c, "finn", "aenderung", aid)  # stoppt die erste
        self.assertEqual(c.execute("SELECT COUNT(*) FROM zeiten WHERE person='finn' AND ende IS NULL").fetchone()[0], 1)
        self.assertEqual(pj.stopp(c, "finn"), 1)
        self.assertIsNone(pj.laufend(c, "finn"))
        with self.assertRaises(pj.ProjektError):
            pj.nachtragen(c, "finn", "projekt", pid, 0)
        with self.assertRaises(pj.ProjektError):
            pj.start(c, "finn", "projekt", 999)
        self.assertEqual(pj.get(c, pid)["zeit_min"], 2)
        self.assertEqual(pj.aenderung_get(c, aid)["zeit_min"], 1)

    def test_kosten_und_warnung(self):
        pj, c = self.pj, self.c
        pid = self.projekt()
        pj.aendern(c, "oskar", pid, preis_einmal="4.500", pauschale_monat="99,50")
        p = pj.get(c, pid)
        self.assertEqual((p["preis_einmal"], p["pauschale_monat"]), (4500, 99.5))
        live = date.today().replace(day=1) - timedelta(days=70)
        c.execute("UPDATE projekte SET status='betrieb', live_am=? WHERE id=?", (live.isoformat() + "T10:00:00", pid))
        vor1 = date.today().replace(day=1) - timedelta(days=1)
        vor2 = vor1.replace(day=1) - timedelta(days=1)
        for d in (vor1, vor2):
            pj.nachtragen(c, "finn", "projekt", pid, 120, d.replace(day=5).isoformat())
        k = pj.kosten(c, pid)
        self.assertEqual(k["zeit_h"], 4)
        self.assertGreaterEqual(k["monate"], 2)
        self.assertEqual(k["umsatz"], 4500 + 99.5 * k["monate"])
        self.assertEqual(pj.takt(c), 1)  # zwei Monate über 1 h: Vorlage an Oskar und Finn
        self.assertIn("Mehr Zeit als angenommen: Tischlerei Hansen",
                      [r["title"] for r in leaddb.notices(c, "f", 9, "finn")[0]])
        self.assertEqual(pj.takt(c), 0)  # einmal im Monat

    def test_regeln(self):
        pj, c = self.pj, self.c
        form = {"kostenlose_tage": "14", "klein_min": "45", "pauschale_klein": "40", "umsetzungstag": "1",
                "stunden_pro_tag": "5", "annahme_h_monat": "2", "stundensatz": "70", "server_monat": "30",
                "zeit_pflicht": "", "bereich_kunden": "markt", "bereich_technik": "technik",
                "checkliste": "Punkt A\n\nPunkt B"}
        pj.regeln_speichern(c, "oskar", form)
        r = pj.regeln(c)
        self.assertEqual((r["kostenlose_tage"], r["zeit_pflicht"], r["checkliste"]), (14, False, ["Punkt A", "Punkt B"]))
        with self.assertRaises(pj.ProjektError):
            pj.regeln_speichern(c, "oskar", dict(form, checkliste=""))


class ProjektWebTest(WebBasis):
    def test_seiten_und_rechte(self):
        from app import crm, projekte
        c = self.c
        lid = lead_anlegen(c, "Web Kunde", stage="angebot")
        crm.set_stage(c, lid, "kunde", author="oskar")
        pid = projekte.zum_lead(c, lid)["id"]
        self.addCleanup(lambda: (c.execute("DELETE FROM projekte WHERE id=?", (pid,)), c.commit()))
        for url in ("/kunden", f"/kunden/{pid}", "/einstellungen/projekte", f"/leads/{lid}", "/mein-tag", "/cockpit"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        r = self.post(f"/kunden/{pid}/aktion", {"was": "aenderung", "text": "Bitte Farbe ändern"},
                      headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"])
        aid = c.execute("SELECT MAX(id) FROM aenderungen").fetchone()[0]
        r = self.post("/kunden/aenderung/0", {"aid": aid, "was": "einordnen", "groesse": "klein"},
                      headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"], r.get_data(as_text=True))
        self.post("/zeit/start", {"typ": "projekt", "id": pid})
        self.assertIn("Zeit läuft", self.client.get("/mein-tag").get_data(as_text=True))
        self.post("/zeit/stopp")
        self.als("hamza")
        self.assertEqual(self.client.get("/kunden").status_code, 403)  # Recherche sieht keine Kunden
        self.assertEqual(self.post("/zeit/start", {"typ": "projekt", "id": pid}).status_code, 403)


class FreigabeZentraleTest(unittest.TestCase):
    """Stufe 6: Freigabe-Zentrale über alle Arten, Stufen A–D je Aktion, Frist auf Stufe B."""

    def setUp(self):
        from unittest import mock
        from app import freigaben, google_kalender, kalender
        self.f, self.k = freigaben, kalender
        self.fake = FakeGoogle()
        p1 = mock.patch.object(google_kalender, "_http", self.fake)
        p2 = mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "sec"})
        p1.start(), p2.start()
        self.addCleanup(p1.stop), self.addCleanup(p2.stop)
        self.c = frische_db()
        self.k.konto_speichern(self.c, "oskar", "r-oskar")
        self.morgen10 = MORGEN + "T10:00"

    def einladung(self, wer="hamza"):
        lid = lead_anlegen(self.c, "Kunde Zentrale")
        return self.k.anlegen(self.c, wer, "Erstgespräch", self.morgen10, verantwortlich="oskar", personen=["hamza"],
                              gaeste="kunde@example.de", lead_id=lid)

    def test_alle_arten_an_einer_stelle(self):
        from app import branchentest as bt, projekte, crm
        f, c = self.f, self.c
        tid = self.einladung()
        omid = firma.person_anlegen(c, "oskar", "Omid Beispiel", "recherche", bereiche_neu=["akquise"], lernstufe=1)
        lid = lead_anlegen(c, "Sanitär Zentrale", "sanitaer")
        bt.eingang(c, lid, omid)
        # Anforderung im Projekt: wartet auf beide Freigaben (Verantwortlich und Technik)
        kid = lead_anlegen(c, "Projekt Zentrale", stage="angebot", owner="oskar")
        crm.set_stage(c, kid, "kunde", author="Oskar")
        pid = projekte.zum_lead(c, kid)["id"]
        projekte.weiter(c, "oskar", pid)
        projekte.aendern(c, "oskar", pid, anforderung="Anfragen sortieren.")
        projekte.kunde_bestaetigt(c, "oskar", pid)
        projekte.schaetzen(c, "finn", pid, "12")
        # Erstkontakt-Entwurf ohne schreibende Person: bei der zuständigen Person
        eid = lead_anlegen(c, "Entwurf Zentrale", owner="oskar")
        c.execute("UPDATE leads SET status='entwurf' WHERE id=?", (eid,))
        c.commit()
        oskar = {(x["art"], x["oid"]) for x in f.offen(c, "oskar")}
        self.assertIn(("termin_einladung", f.offen(c, "oskar")[0]["oid"]), oskar)
        self.assertEqual({a for a, _ in oskar}, {"termin_einladung", "anforderung", "erstkontakt"})
        self.assertIn(("anforderung", pid), oskar)
        self.assertEqual({x["art"] for x in f.offen(c, "hamza")}, {"lead_pruefung"})
        self.assertEqual({x["art"] for x in f.offen(c, "finn")}, {"anforderung"})  # Technik gibt das Wie frei
        self.assertEqual(f.zaehlen(c, "oskar"), len(oskar))
        # Entscheiden aus der Zentrale: jede Art prüft selbst, wer darf
        fid = c.execute("SELECT id FROM freigaben WHERE art='termin_einladung' AND objekt_id=?", (tid,)).fetchone()[0]
        with self.assertRaises(self.k.KalenderError):
            f.entscheiden(c, "hamza", "termin_einladung", fid, True)
        f.entscheiden(c, "oskar", "termin_einladung", fid, True)
        self.assertEqual(self.k.get(c, tid)["status"], "geplant")
        with self.assertRaises(f.FreigabeError):
            f.entscheiden(c, "oskar", "termin_einladung", fid, True)  # schon entschieden
        with self.assertRaises(f.FreigabeError):
            f.entscheiden(c, "oskar", "anforderung", pid, False, "nein")  # Anforderung: nur freigeben
        f.entscheiden(c, "oskar", "anforderung", pid, True)
        f.entscheiden(c, "finn", "anforderung", pid, True)
        self.assertEqual({x["art"] for x in f.offen(c, "oskar")}, {"erstkontakt"})
        pf = f.offen(c, "hamza")[0]
        with self.assertRaises(Exception):
            f.entscheiden(c, omid, "lead_pruefung", pf["oid"], True)  # nicht die eigene Recherche
        f.entscheiden(c, "hamza", "lead_pruefung", pf["oid"], False, "Keine Website")
        self.assertEqual(leaddb.get_lead(c, lid)["pruef_status"], "abgelehnt")
        # „Alle offenen“: wer verwaltet, sieht auch, was bei anderen liegt
        alle = f.offen(c, "finn", alle=True)
        self.assertIn("erstkontakt", {x["art"] for x in alle})
        self.assertEqual(f.offen(c, omid, alle=True), [])

    def test_stufen_je_aktion_und_frist_b(self):
        f, k, c = self.f, self.k, self.c
        self.assertEqual(f.stufe(c, "termin_einladung"), "C")
        with self.assertRaises(f.FreigabeError):
            f.stufen_speichern(c, "oskar", {"termin_einladung": "D"})  # nicht erlaubt
        with self.assertRaises(f.FreigabeError):
            f.stufen_speichern(c, "oskar", {"frist_h": "0"})
        f.stufen_speichern(c, "oskar", {"termin_einladung": "B", "frist_h": "24", "erstkontakt": "A",
                                        "angebot": "D"})
        s = f.stufen(c)
        self.assertEqual((s["termin_einladung"], s["frist_h"]), ("B", 24))
        self.assertEqual(s["erstkontakt"], "C")  # fest: nie ohne Freigabe (§ 7 UWG, hart im Code)
        self.assertEqual(s["angebot"], "C")  # kommt erst mit Stufe 8
        self.assertTrue(any(e["quelle"] == "regel" and "Freigabe-Stufen" in e["titel"]
                            for e in f.entscheidungen(c)))  # Regeländerung steht im Entscheidungs-Log
        # Stufe B: angekündigt, nicht verschickt; nach der Frist geht es ohne Einspruch raus
        tid = self.einladung()
        self.assertEqual(k.get(c, tid)["status"], "vorschlag")
        self.assertEqual(self.fake.events, {})
        offen = f.offen(c, "oskar")[0]
        self.assertEqual((offen["stufe"], offen["art"]), ("B", "termin_einladung"))
        self.assertTrue(offen["frist"] > leaddb.now())
        self.assertEqual(f.takt(c), 0)  # Frist läuft noch
        self.assertEqual(f.takt(c, jetzt=(date.today() + timedelta(days=3)).isoformat()), 1)
        self.assertEqual(k.get(c, tid)["status"], "geplant")
        self.assertEqual(len(self.fake.anfragen("POST", "/events")), 1)
        self.assertIn("Frist ohne Einspruch", firma.protokoll(c, objekt=("termin", tid))[0]["text"])
        # Stoppen: aus B wird C, der Takt tut nichts mehr
        tid2 = self.einladung()
        fid = f.offen(c, "oskar")[0]["oid"]
        with self.assertRaises(f.FreigabeError):
            f.stoppen(c, "finn", fid + 999)
        f.stoppen(c, "oskar", fid)
        self.assertEqual(f.takt(c, jetzt=(date.today() + timedelta(days=3)).isoformat()), 0)
        self.assertEqual(k.get(c, tid2)["status"], "vorschlag")
        self.assertEqual(f.offen(c, "oskar")[0]["stufe"], "C")
        # Stufe A: Einladung geht sofort raus
        f.stufen_speichern(c, "oskar", {"termin_einladung": "A"})
        tid3 = self.einladung()
        self.assertEqual(k.get(c, tid3)["status"], "geplant")
        # Abgesagter Termin auf B: nicht ausführbar, bleibt als Freigabe stehen statt still zu verschwinden
        f.stufen_speichern(c, "oskar", {"termin_einladung": "B"})
        tid4 = self.einladung()
        c.execute("UPDATE termine SET status='abgesagt' WHERE id=?", (tid4,))
        c.commit()
        self.assertEqual(f.takt(c, jetzt=(date.today() + timedelta(days=3)).isoformat()), 0)
        self.assertEqual(k.get(c, tid4)["status"], "abgesagt")  # nicht wieder aufgeweckt
        f4 = c.execute("SELECT stufe, status FROM freigaben WHERE objekt_id=? AND art='termin_einladung'",
                       (str(tid4),)).fetchone()
        self.assertEqual(tuple(f4), ("C", "offen"))
        self.assertIn("nicht ausführbar", firma.protokoll(c, objekt=("termin", tid4))[0]["text"])


class EntscheidungenTest(unittest.TestCase):
    """Stufe 6: Entscheidungsrechte als Daten und Entscheidungs-Log."""

    def setUp(self):
        from app import freigaben
        self.f = freigaben
        self.c = frische_db()

    def test_rechte_startwerte_und_aufloesen(self):
        f, c = self.f, self.c
        keys = [r["key"] for r in f.rechte(c)]
        self.assertEqual(keys, ["eigener_bereich", "lernstufe", "kundenversprechen", "preis", "gruenderteam"])
        self.assertEqual(firma.rechte(c, "oskar")["entscheidungen"], 4)
        self.assertEqual(firma.rechte(c, "hamza")["entscheidungen"], 2)
        r, noetig, gefragt = f.wer_entscheidet(c, "eigener_bereich", "hamza", 50)
        self.assertEqual((r["key"], noetig), ("eigener_bereich", ["hamza"]))  # Hamza verantwortet Akquise
        r, noetig, _ = f.wer_entscheidet(c, "eigener_bereich", "hamza", 250)  # über der Grenze: Gründerteam
        self.assertEqual((r["key"], sorted(noetig)), ("gruenderteam", ["finn", "oskar"]))
        _, noetig, gefragt = f.wer_entscheidet(c, "preis", "hamza")
        self.assertEqual((noetig, gefragt), (["oskar"], ["finn"]))
        omid = firma.person_anlegen(c, "oskar", "Omid Beispiel", "recherche", bereiche_neu=["akquise"])
        self.assertEqual(f.wer_entscheidet(c, "eigener_bereich", omid)[1], ["hamza"])

    def test_log_allein_gemeinsam_und_ablehnen(self):
        f, c = self.f, self.c
        with self.assertRaises(f.FreigabeError):
            f.entscheiden_festhalten(c, "oskar", "Ohne Grund", "", recht_key="eigener_bereich")
        e1 = f.entscheiden_festhalten(c, "oskar", "Neue Visitenkarten", "Alte sind leer", recht_key="eigener_bereich",
                                      betrag="80")
        self.assertEqual(f.entscheidung_get(c, e1)["status"], "gilt")
        # Kundenversprechen: Markt und Technik gemeinsam, wartet auf Finn in der Zentrale
        e2 = f.entscheiden_festhalten(c, "oskar", "Sonderanpassung für Sievers", "Großer Kunde",
                                      recht_key="kundenversprechen")
        e = f.entscheidung_get(c, e2)
        self.assertEqual((e["status"], e["fehlt"]), ("wartet", ["Finn Adams"]))
        offen = f.offen(c, "finn")
        self.assertEqual([(x["art"], x["titel"]) for x in offen], [("entscheidung", "Sonderanpassung für Sievers")])
        self.assertEqual(f.offen(c, "oskar"), [])
        with self.assertRaises(f.FreigabeError):
            f.entscheiden(c, "hamza", "entscheidung", offen[0]["oid"], True)
        f.entscheiden(c, "finn", "entscheidung", offen[0]["oid"], True)
        self.assertEqual(f.entscheidung_get(c, e2)["status"], "gilt")
        self.assertIn("Entscheidung gilt jetzt: Sonderanpassung für Sievers",
                      [r["title"] for r in leaddb.notices(c, "o", 20, "oskar")[0]])
        # Ausgabe über der Grenze von Hamza: Gründerteam gemeinsam, eine Ablehnung reicht
        e3 = f.entscheiden_festhalten(c, "hamza", "Neues Recherche-Tool", "Spart Zeit", recht_key="eigener_bereich",
                                      betrag="1.200,00")
        e = f.entscheidung_get(c, e3)
        self.assertEqual((e["status"], e["recht"], e["betrag"]), ("wartet", "gruenderteam", 1200.0))
        fid = f.offen(c, "oskar")[0]["oid"]
        with self.assertRaises(f.FreigabeError):
            f.entscheiden(c, "oskar", "entscheidung", fid, False, "")
        f.entscheiden(c, "oskar", "entscheidung", fid, False, "Erst im Wochengespräch")
        self.assertEqual(f.entscheidung_get(c, e3)["status"], "abgelehnt")
        self.assertEqual(f.offen(c, "finn"), [])  # Finns Anfrage ist hinfällig

    def test_rechte_pflegen_und_regel_im_log(self):
        f, c = self.f, self.c
        with self.assertRaises(f.FreigabeError):
            f.recht_speichern(c, "oskar", "", "Werbung", "", ["unbekannt:x"], "eine", [])
        key = f.recht_speichern(c, "oskar", "", "Werbebudget", "Anzeigen", ["bereich:markt"], "eine", [], "500",
                                "gruenderteam")
        self.assertEqual(f.recht(c, key)["grenze_eur"], 500)
        f.recht_speichern(c, "oskar", key, "Werbebudget", "Anzeigen", ["person:finn"], "eine", ["bereich:markt"])
        self.assertEqual(f.wer_entscheidet(c, key, "hamza")[1:], (["finn"], ["oskar"]))
        with self.assertRaises(f.FreigabeError):
            f.recht_loeschen(c, "oskar", "gruenderteam")  # „Alles im eigenen Bereich“ verweist ab 200 € darauf
        f.recht_loeschen(c, "oskar", key)
        log = [e["titel"] for e in f.entscheidungen(c)]
        self.assertTrue(any("Werbebudget" in t and "gelöscht" in t for t in log))
        firma.rolle_speichern(c, "oskar", "", "Praktikum", {"aufgaben": 2})
        self.assertTrue(any(e["quelle"] == "regel" and "Praktikum" in e["titel"] for e in f.entscheidungen(c)))


class NotfallTest(unittest.TestCase):
    def setUp(self):
        from app import notfall
        self.n = notfall
        self.c = frische_db()

    def test_melden_band_beheben_und_ausfallzeit(self):
        n, c = self.n, self.c
        with self.assertRaises(n.NotfallError):
            n.melden(c, "hamza", "kundensystem", "")
        nid = n.melden(c, "hamza", "kundensystem", "Buchungsseite zeigt Fehler")
        self.assertEqual(n.get(c, nid)["zustaendig"], ["finn"])  # Technik
        self.assertIn("Notfall: Kundensystem gestört", [r["title"] for r in leaddb.notices(c, "f", 9, "finn")[0]])
        self.assertEqual(sorted(n.zustaendige(c, "kundendaten")), ["finn", "oskar"])
        self.assertEqual([x["id"] for x in n.aktive(c)], [nid])
        with self.assertRaises(n.NotfallError):
            n.beheben(c, "finn", nid, "")
        # Ausfallzeit: zwei Stunden in dieser Woche, Überschneidungen zählen einmal
        from app import kpis
        von, bis = kpis.woche_grenzen(kpis.woche_von())
        start = von[:10] + "T09:00:00"
        c.execute("UPDATE notfaelle SET gemeldet_am=? WHERE id=?", (start, nid))
        n.beheben(c, "finn", nid, "Zertifikat erneuert")
        c.execute("UPDATE notfaelle SET behoben_am=? WHERE id=?", (von[:10] + "T11:00:00", nid))
        n2 = n.melden(c, "oskar", "kundensystem", "Noch ein Fehler")
        c.execute("UPDATE notfaelle SET gemeldet_am=?, behoben_am=? WHERE id=?",
                  (von[:10] + "T10:00:00", von[:10] + "T10:30:00", n2))
        n.melden(c, "oskar", "abschluss", "Kunde will kündigen")  # zählt nicht zur Ausfallzeit
        c.commit()
        self.assertEqual(n.ausfallzeit(c, von, bis), 2.0)
        self.assertEqual(kpis.wert(c, "ausfallzeit", kpis.woche_von()), 2.0)
        self.assertEqual(len(n.aktive(c)), 1)
        # Zuständigkeit in den Einstellungen
        from werkzeug.datastructures import MultiDict
        with self.assertRaises(n.NotfallError):
            n.regeln_speichern(c, "oskar", MultiDict({"kundensystem": "technik", "kundendaten": "", "abschluss": "markt"}))
        n.regeln_speichern(c, "oskar", MultiDict([("kundensystem", "technik"), ("kundensystem", "akquise"),
                                                  ("kundendaten", "technik"), ("abschluss", "markt")]))
        self.assertEqual(sorted(n.zustaendige(c, "kundensystem")), ["finn", "hamza"])


class FreigabenWebTest(WebBasis):
    def test_seiten_rechte_und_ablauf(self):
        from app import freigaben, notfall
        c = self.c
        for url in ("/freigaben", "/freigaben?alle=1", "/entscheidungen", "/notfall", "/einstellungen/freigaben",
                    "/mein-tag", "/wochengespraech", "/einstellungen"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        r = self.post("/entscheidungen", {"titel": "Web-Entscheidung", "begruendung": "Weil es passt",
                                          "recht": "kundenversprechen", "quelle": "wochengespraech"},
                      headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"], r.get_data(as_text=True))
        e = freigaben.entscheidungen(c, limit=1)[0]
        self.addCleanup(lambda: (c.execute("DELETE FROM entscheidungen WHERE id=?", (e["id"],)),
                                 c.execute("DELETE FROM freigaben WHERE art='entscheidung'"), c.commit()))
        self.assertEqual((e["status"], e["quelle"]), ("wartet", "wochengespraech"))
        self.assertIn("Web-Entscheidung", self.client.get("/wochengespraech").get_data(as_text=True))
        self.als("finn")
        html = self.client.get("/freigaben").get_data(as_text=True)
        self.assertIn("Web-Entscheidung", html)
        self.assertIn("Freigaben", self.client.get("/mein-tag").get_data(as_text=True))
        fid = next(x["oid"] for x in freigaben.offen(c, "finn") if x["art"] == "entscheidung")
        r = self.post(f"/freigaben/entscheidung/{fid}", {"ok": "1"}, headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json().get("ok"), r.get_json())
        self.assertEqual(freigaben.entscheidung_get(c, e["id"])["status"], "gilt")
        # Notfall: Band für alle, bis behoben
        self.als("hamza")
        r = self.post("/notfall", {"art": "abschluss", "text": "Kunde springt ab"}, headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"])
        nid = notfall.aktive(c)[0]["id"]
        self.addCleanup(lambda: (c.execute("DELETE FROM notfaelle WHERE id=?", (nid,)), c.commit()))
        self.assertIn("os-notfall-band", self.client.get("/aufgaben").get_data(as_text=True))
        self.post(f"/notfall/{nid}/behoben", {"notiz": "Telefoniert, bleibt"})
        self.assertNotIn("os-notfall-band", self.client.get("/aufgaben").get_data(as_text=True))
        # Einstellungen nur mit Verwalten
        self.assertEqual(self.client.get("/einstellungen/freigaben").status_code, 200)  # Sehen geht
        self.assertEqual(self.post("/einstellungen/freigaben/stufen", {"termin_einladung": "A"}).status_code, 403)
        self.als("oskar")
        r = self.post("/einstellungen/freigaben/stufen", {"termin_einladung": "C", "frist_h": "24"},
                      headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"])


# ---------------------------------------------------------------- Stufe 7: Kunden-Bereich


class PostfachMock:
    """Akquise-Postfach zum Mitlesen: alle Mails landen in `self.mails`."""

    def __init__(self, test):
        from unittest import mock
        from app import agent
        self.mails = []
        smtp = mock.Mock(send_message=lambda m: self.mails.append(m))
        settings = dict(agent.CFG["settings"], OUTREACH_USER="kontakt@ylvalabs.de", OUTREACH_PASSWORD="x",
                        IMAP_HOST="", BASE_URL="https://os.example.de")
        p = mock.patch.dict(agent.CFG, {"settings": settings, "smtp_connect": lambda u, pw: smtp})
        p.start()
        test.addCleanup(p.stop)

    def text(self, i=-1):
        return self.mails[i].get_content()


def projekt_anlegen(c, name="Tischlerei Portal", email="kunde@tischlerei.de"):
    from app import crm, projekte
    lid = lead_anlegen(c, name, stage="angebot", owner="oskar", email=email, contact_name="Frau Hansen")
    crm.set_stage(c, lid, "kunde", author="Oskar")
    return projekte.zum_lead(c, lid)["id"]


class PortalTest(unittest.TestCase):
    def setUp(self):
        from app import portal, projekte
        self.pt, self.pj = portal, projekte
        self.c = frische_db()
        self.post = PostfachMock(self)
        self.pid = projekt_anlegen(self.c)

    def token_aus_mail(self):
        import re
        return re.search(r"/portal/login/([\w-]+)", self.post.text()).group(1)

    def test_zugang_einmal_link_sitzung_sperren(self):
        pt, c = self.pt, self.c
        with self.assertRaises(pt.PortalError):
            pt.zugang_schicken(c, "hamza", self.pid)  # nur die verantwortliche Person
        link = pt.zugang_schicken(c, "oskar", self.pid)
        self.assertTrue(link.startswith("https://os.example.de/portal/login/"))
        self.assertEqual(self.post.mails[0]["To"], "Frau Hansen <kunde@tischlerei.de>")
        self.assertIn("Guten Tag Frau Hansen", self.post.text())
        token = self.token_aus_mail()
        self.assertNotIn(token, str([tuple(r) for r in c.execute("SELECT * FROM kunden_zugaenge")]))  # nur der Hash
        self.assertTrue(pt.link_pruefen(c, token))
        sitzung, tage = pt.anmelden(c, token)
        self.assertEqual(tage, 90)
        with self.assertRaises(pt.PortalError):
            pt.anmelden(c, token)  # einmal gültig
        z = pt.sitzung(c, sitzung)
        self.assertEqual(z["email"], "kunde@tischlerei.de")
        self.assertTrue(z["letzter_login"])
        # Neuen Link anfordern: gleiche Antwort für unbekannte Adressen, höchstens einmal in zehn Minuten
        antwort = pt.link_anfordern(c, "fremd@example.de")
        self.assertEqual(len(self.post.mails), 1)
        self.assertEqual(pt.link_anfordern(c, "kunde@tischlerei.de"), antwort)
        self.assertEqual(len(self.post.mails), 1)  # gerade erst verschickt
        c.execute("UPDATE kunden_zugaenge SET link_angefordert='2000-01-01T00:00:00'")
        pt.link_anfordern(c, "Kunde@Tischlerei.de ")
        self.assertEqual(len(self.post.mails), 2)
        # Abgelaufener Link
        c.execute("UPDATE kunden_zugaenge SET link_bis='2000-01-01T00:00:00'")
        self.assertIsNone(pt.link_pruefen(c, self.token_aus_mail()))
        # Sperren beendet die Sitzung
        pt.zugang_sperren(c, "oskar", z["id"])
        self.assertIsNone(pt.sitzung(c, sitzung))
        # Gesperrter Betrieb bekommt keine Mails, Link zum Kopieren geht ohne Mail
        c.execute("UPDATE leads SET status='gesperrt'")
        with self.assertRaises(pt.PortalError):
            pt.zugang_schicken(c, "oskar", self.pid, "neu@tischlerei.de")
        self.assertIn("/portal/login/", pt.zugang_schicken(c, "oskar", self.pid, "neu@tischlerei.de", senden=False))

    def test_ansicht_bestaetigen_wunsch(self):
        pt, pj, c = self.pt, self.pj, self.c
        link = pt.zugang_schicken(c, "oskar", self.pid, senden=False)
        z = pt.sitzung(c, pt.anmelden(c, link.rsplit("/", 1)[1])[0])
        pj.weiter(c, "oskar", self.pid)
        pj.aendern(c, "oskar", self.pid, anforderung="Anfragen automatisch sortieren.")
        a = pt.ansicht(c, z)
        self.assertEqual((a["p"]["schritt"], a["p"]["kann_bestaetigen"]), ("Anforderung", True))
        self.assertNotIn("zeit_min", a["p"])  # keine Zeiten, keine Kosten
        pt.bestaetigen(c, z)
        self.assertTrue(pj.get(c, self.pid)["kunde_bestaetigt_am"])
        self.assertIn("Anforderung bestätigt: Tischlerei Portal",
                      [r["title"] for r in leaddb.notices(c, "f", 20, "finn")[0]])
        with self.assertRaises(pt.PortalError):
            pt.wunsch(c, z, "")
        aid = pt.wunsch(c, z, "Bitte auch die Telefonnummer abfragen.")
        self.assertEqual(pj.aenderung_get(c, aid)["quelle"], "portal")
        a = pt.ansicht(c, z)
        self.assertEqual([(w["text"], w["status"]) for w in a["aenderungen"]],
                         [("Bitte auch die Telefonnummer abfragen.", "Eingegangen")])

    def test_dokumente(self):
        import io
        from werkzeug.datastructures import FileStorage
        pt, c = self.pt, self.c
        ordner = tempfile.mkdtemp()
        with self.assertRaises(pt.PortalError):
            pt.dokument_speichern(c, "oskar", self.pid, FileStorage(io.BytesIO(b"x"), "boese.exe"), "", "angebot", True,
                                  ordner)
        d1 = pt.dokument_speichern(c, "oskar", self.pid, FileStorage(io.BytesIO(b"%PDF-1"), "Angebot.pdf"), "",
                                   "angebot", True, ordner)
        d2 = pt.dokument_speichern(c, "oskar", self.pid, FileStorage(io.BytesIO(b"intern"), "kalk.xlsx"),
                                   "Kalkulation", "sonstiges", False, ordner)
        self.assertEqual([d["titel"] for d in pt.dokumente(c, self.pid, nur_sichtbar=True)], ["Angebot"])
        self.assertEqual(len(pt.dokumente(c, self.pid)), 2)
        datei = Path(ordner) / "dokumente" / pt.dokument(c, d2)["datei"]
        self.assertTrue(datei.exists())
        pt.dokument_aendern(c, "oskar", d1, sichtbar=False)
        self.assertEqual(pt.dokumente(c, self.pid, nur_sichtbar=True), [])
        pt.dokument_aendern(c, "oskar", d2, loeschen=True, data_dir=ordner)
        self.assertFalse(datei.exists())

    def test_wochenbericht_stufe_b_und_c(self):
        from datetime import datetime as dt
        from app import freigaben, kpis
        pt, pj, c = self.pt, self.pj, self.c
        pt.zugang_schicken(c, "oskar", self.pid, senden=False)
        c.execute("UPDATE projekte SET status='bau', bau_ende_plan=? WHERE id=?", (MORGEN, self.pid))
        c.commit()
        aid = pj.aenderung_anlegen(c, "oskar", self.pid, "Logo austauschen")
        freitag = date.today() + timedelta(days=(4 - date.today().weekday()) % 7)
        self.assertEqual(pt.takt(c, dt.combine(freitag, dt.min.time()).replace(hour=9))["berichte"], 0)  # zu früh
        self.assertEqual(pt.takt(c, dt.combine(freitag, dt.min.time()).replace(hour=13))["berichte"], 1)
        self.assertEqual(pt.takt(c, dt.combine(freitag, dt.min.time()).replace(hour=14))["berichte"], 0)  # einmal
        b = pt.berichte(c, self.pid)[0]
        self.assertIn("Stand: Im Bau", b["text"])
        self.assertIn("Logo austauschen (Eingegangen)", b["text"])
        self.assertIn("https://os.example.de/portal", b["text"])
        offen = [x for x in freigaben.offen(c, "oskar") if x["art"] == "wochenbericht"]
        self.assertEqual((len(offen), offen[0]["stufe"]), (1, "B"))
        self.assertEqual(self.post.mails, [])  # nichts vor der Frist
        pt.bericht_bearbeiten(c, "oskar", b["id"], b["betreff"], b["text"] + "\n\nSchönes Wochenende!")
        freigaben.takt(c, jetzt=(date.today() + timedelta(days=9)).isoformat())
        self.assertEqual(pt.bericht_get(c, b["id"])["status"], "verschickt")
        self.assertIn("Schönes Wochenende!", self.post.text())
        self.assertEqual(self.post.mails[0]["To"], "Frau Hansen <kunde@tischlerei.de>")
        # Stufe C: wartet, Ablehnen mit Grund stoppt
        freigaben.stufen_speichern(c, "oskar", {"wochenbericht": "C"})
        bid = pt.bericht_anlegen(c, self.pid, kpis.woche_plus(kpis.woche_von(), 3), "oskar")
        self.assertTrue(bid)
        f = next(x for x in freigaben.offen(c, "oskar") if x["art"] == "wochenbericht")
        self.assertEqual(f["stufe"], "C")
        with self.assertRaises(freigaben.FreigabeError):
            freigaben.entscheiden(c, "hamza", "wochenbericht", f["oid"], True)
        freigaben.entscheiden(c, "oskar", "wochenbericht", f["oid"], False, "Diese Woche telefoniert")
        self.assertEqual(pt.bericht_get(c, bid)["status"], "gestoppt")
        self.assertEqual(len(self.post.mails), 1)
        _ = aid

    def test_termin_erinnerung_an_kunden(self):
        from datetime import datetime as dt
        from app import freigaben
        pt, c = self.pt, self.c
        lid = c.execute("SELECT lead_id FROM projekte WHERE id=?", (self.pid,)).fetchone()[0]

        def termin(stunden):
            start = (dt.now() + timedelta(hours=stunden)).strftime("%Y-%m-%dT%H:%M")
            ende = (dt.now() + timedelta(hours=stunden + 1)).strftime("%Y-%m-%dT%H:%M")
            tid = c.execute("INSERT INTO termine (titel, art, start, ende, verantwortlich, lead_id, status, meet_link, "
                            "erstellt_am) VALUES ('Abnahme', 'kunde', ?, ?, 'oskar', ?, 'geplant', 'https://meet/x', ?)",
                            (start, ende, lid, leaddb.now())).lastrowid
            c.execute("INSERT INTO termin_teilnehmer (termin_id, email) VALUES (?, 'kunde@tischlerei.de')", (tid,))
            c.commit()
            return tid
        termin(30)  # noch nicht dran
        t1 = termin(20)
        self.assertEqual(pt.takt(c)["erinnerungen"], 1)
        self.assertIn("https://meet/x", self.post.text())
        self.assertEqual(pt.takt(c)["erinnerungen"], 0)  # nur einmal
        self.assertIn("Erinnerung an den Kunden verschickt", firma.protokoll(c, objekt=("termin", t1))[0]["text"])
        freigaben.stufen_speichern(c, "oskar", {"erinnerung_kunde": "C"})
        termin(22)
        pt.takt(c)
        self.assertEqual(len(self.post.mails), 1)
        f = next(x for x in freigaben.offen(c, "oskar") if x["art"] == "erinnerung_kunde")
        freigaben.entscheiden(c, "oskar", "erinnerung_kunde", f["oid"], True)
        self.assertEqual(len(self.post.mails), 2)


class PortalWebTest(WebBasis):
    def test_team_und_kunde(self):
        import io
        import re
        from app import portal
        c = self.c
        pid = projekt_anlegen(c, "Web Portal Kunde", "web@kunde.de")
        self.addCleanup(lambda: (c.execute("DELETE FROM projekte WHERE id=?", (pid,)),
                                 c.execute("DELETE FROM kunden_zugaenge WHERE projekt_id=?", (pid,)), c.commit()))
        for url in (f"/kunden/{pid}", "/einstellungen/kunden-bereich", "/portal"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        r = self.post(f"/kunden/{pid}/zugang", {"email": "web@kunde.de", "name": "Herr Web", "senden": "0"})
        self.assertEqual(r.status_code, 302)
        html = self.client.get(f"/kunden/{pid}").get_data(as_text=True)
        token = re.search(r"/portal/login/([\w-]+)", html).group(1)
        self.assertNotIn(token, self.client.get(f"/kunden/{pid}").get_data(as_text=True))  # nur einmal gezeigt
        r = self.post(f"/kunden/{pid}/dokument", {"datei": (io.BytesIO(b"%PDF"), "Angebot.pdf"), "art": "angebot",
                                                  "sichtbar": "1"}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        did = portal.dokumente(c, pid)[0]["id"]
        self.addCleanup(lambda: portal.dokument_aendern(c, "oskar", did, loeschen=True, data_dir=main.SETTINGS["DATA_DIR"]))
        # Kunde: eigener Browser ohne Team-Sitzung
        kunde = main.app.test_client()
        kunde.get("/portal")
        with kunde.session_transaction() as sess:
            sess["csrf"] = "k"
        self.assertIn("Anmelden", kunde.get(f"/portal/login/{token}").get_data(as_text=True))
        r = kunde.post(f"/portal/login/{token}", data={"_csrf": "k"})
        self.assertIn("ylva_kunde=", r.headers.get("Set-Cookie", ""))
        html = kunde.get("/portal").get_data(as_text=True)
        self.assertIn("Web Portal Kunde", html)
        self.assertIn("Herr Web", html)
        self.assertNotIn("Kosten", html)
        self.assertEqual(kunde.get(f"/portal/dokument/{did}").status_code, 200)
        self.assertEqual(kunde.get(f"/kunden/{pid}").status_code, 302)  # Team-Seiten bleiben zu
        kunde.post("/portal/wunsch", data={"_csrf": "k", "text": "Bitte grüner Knopf"})
        self.assertIn("Bitte grüner Knopf", kunde.get("/portal").get_data(as_text=True))
        kunde.post("/portal/abmelden", data={"_csrf": "k"})
        self.assertIn("Link schicken", kunde.get("/portal").get_data(as_text=True))
        self.assertEqual(kunde.get(f"/portal/dokument/{did}").status_code, 302)
        # Recherche sieht Kunden nicht, also auch die Einstellungen nicht
        self.als("hamza")
        self.assertEqual(self.client.get("/einstellungen/kunden-bereich").status_code, 403)


if __name__ == "__main__":
    unittest.main()
