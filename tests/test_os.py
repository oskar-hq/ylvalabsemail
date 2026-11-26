"""Tests für das Firmen-OS, Stufe 1: Personen, Rollen und Rechte, Schutz gegen Aussperren, Bereiche,
Firmen-Protokoll, Aufgaben und Anfragen, Wiedervorlagen, Kommentare mit @-Erwähnung, Mein Tag.
Aufruf: python -m unittest discover tests"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

if "DATA_DIR" not in os.environ:  # allein gestartet; sonst setzt test_leads die Umgebung
    os.environ.update(DATA_DIR=tempfile.mkdtemp(), SMTP_HOST="smtp.example.com", LEADS_WORKER="false", OUTREACH_EMAIL="false")
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


class AlteSqliteTest(unittest.TestCase):
    def test_trigger_ohne_qualifizierte_namen(self):
        """Auf dem Server läuft SQLite 3.46: Dort sind im Rumpf eines Triggers keine Namen wie „main.leads“ erlaubt
        (neuere Versionen erlauben es). Sonst lässt sich die Datenbank nicht öffnen und jede Seite zeigt Fehler 500."""
        import re
        c = frische_db()
        for (sql,) in c.execute("SELECT sql FROM sqlite_temp_master WHERE type='trigger'"):
            rumpf = sql.split("BEGIN", 1)[1]
            self.assertIsNone(re.search(r"\b(main|temp)\.", rumpf), sql)

    def test_healthz_oeffnet_die_datenbank(self):
        from unittest import mock
        self.assertEqual(main.app.test_client().get("/healthz").status_code, 200)
        with mock.patch.object(leaddb, "connect", side_effect=sqlite3.OperationalError("kaputt")):
            self.assertEqual(main.app.test_client().get("/healthz").status_code, 500)


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
        html = self.client.get("/aufgaben").get_data(as_text=True)
        self.assertIn("Oskar", html)
        self.assertIn("os.css", html)
        self.assertIn("Neue Aufgabe", html)
        self.assertEqual(self.client.get("/").headers["Location"], "/freigaben")
        r = self.post("/aufgaben/neu", {"titel": "Website-Text prüfen", "bereich": "technik", "schnell": "heute"},
                      headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"], r.get_data(as_text=True))
        aid = self.c.execute("SELECT MAX(id) FROM aufgaben").fetchone()[0]
        self.addCleanup(lambda: (self.c.execute("DELETE FROM aufgaben WHERE id=?", (aid,)), self.c.commit()))
        for url in ("/aufgaben", "/aufgaben/alle?ansicht=alle", "/aufgaben/alle?ansicht=vergeben", "/aufgaben/alle?erledigt=1",
                    f"/aufgaben/{aid}", "/einstellungen/personen", "/einstellungen/personen/hamza",
                    "/einstellungen/rollen", "/einstellungen/rollen/recherche", "/einstellungen/rollen/neu",
                    "/einstellungen/bereiche", "/einstellungen/protokoll", "/einstellungen", "/profil"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
        self.assertIn("Website-Text prüfen", self.client.get("/aufgaben/alle?ansicht=vergeben").get_data(as_text=True))
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
        self.assertEqual(r.headers["Location"], "/freigaben")
        self.assertIn("Omid", guest.get("/freigaben").get_data(as_text=True))
        self.assertEqual(guest.get("/einstellungen").status_code, 403)  # Recherche
        self.assertEqual(guest.get(path).status_code, 410)  # Link verbraucht
        # Sperren wirft die laufende Sitzung raus
        self.post(f"/einstellungen/personen/{key}/status", {"status": "gesperrt"})
        self.assertEqual(guest.get("/aufgaben").status_code, 302)

    def test_rechte(self):
        self.als("hamza")
        self.assertEqual(self.client.get("/aufgaben").status_code, 200)
        self.assertEqual(self.client.get("/crm").status_code, 200)
        self.assertEqual(self.client.get("/einstellungen").status_code, 403)
        self.assertEqual(self.client.get("/einstellungen/personen").status_code, 403)
        self.assertEqual(self.client.get("/leads/kosten").status_code, 403)
        self.assertEqual(self.post("/einstellungen/personen/neu", {"name": "X Y", "rolle": "gruender"}).status_code, 403)
        html = self.client.get("/aufgaben").get_data(as_text=True)
        self.assertNotIn('href="/einstellungen"', html)
        self.assertNotIn('href="/leads/kosten"', html)
        # Ein Modul abschalten: CRM weg für Hamza (eigenes Recht), gilt sofort
        self.c.execute("""UPDATE team SET rechte='{"crm": 0}' WHERE key='hamza'""")
        self.c.commit()
        self.addCleanup(lambda: (self.c.execute("UPDATE team SET rechte=NULL WHERE key='hamza'"), self.c.commit()))
        self.assertEqual(self.client.get("/crm").status_code, 403)
        self.assertEqual(self.post("/crm/neu", {"name": "X"}).status_code, 403)
        self.assertNotIn('href="/crm"', self.client.get("/aufgaben").get_data(as_text=True))

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
            if ep in main.FREI or ep in main.OEFFENTLICH or ep in frei_ohne_modul or ep in ("static", "login", "logout",
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


# ---------------------------------------------------------------- Stufe 3: Kalender und Termine


class FakeGoogle:
    """Nachgebautes Google: merkt sich jede Anfrage, legt Termine an, liefert frei/belegt."""

    def __init__(self):
        from app import google_kalender
        self.g = google_kalender
        self.calls, self.events, self.busy, self.kaputt = [], {}, [], set()
        self.listen = [{"id": "ich@example.com", "summary": "Ich", "primary": True, "accessRole": "owner"}]
        self.busy_je, self.ohne_liste = {}, False  # belegt je weiterem Kalender; Recht für die Liste fehlt

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
        if "/users/me/calendarList" in url:
            if self.ohne_liste:
                raise self.g.GoogleError("Google: Insufficient Permission (403)")
            return {"items": self.listen}
        if "/events?" in url and method == "GET":
            import urllib.parse
            kid = urllib.parse.unquote(url.split("/calendars/")[1].split("/events")[0])
            liste = self.busy if kid in ("primary", "ich@example.com") else self.busy_je.get(kid, [])
            return {"items": [{"id": b.get("id", f"{kid}-b{i}"), "start": {"dateTime": b["start"]},
                               "end": {"dateTime": b["end"]},
                               **{k: v for k, v in b.items() if k not in ("id", "start", "end")}}
                              for i, b in enumerate(liste)]}
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
        tid = k.anlegen(c, "oskar", "Aus dem OS", self.morgen10, dauer=30)
        eigene = c.execute("SELECT google_event_id FROM termin_teilnehmer WHERE termin_id=?", (tid,)).fetchone()[0]
        self.fake.busy = [{"start": "2030-01-07T09:00:00Z", "end": "2030-01-07T10:00:00Z"},
                          {"id": eigene, "start": "2030-01-08T09:00:00Z", "end": "2030-01-08T10:00:00Z"},
                          {"start": "2030-01-09T09:00:00Z", "end": "2030-01-09T10:00:00Z", "transparency": "transparent"},
                          {"start": "2030-01-10T09:00:00Z", "end": "2030-01-10T10:00:00Z",
                           "attendees": [{"self": True, "responseStatus": "declined"}]}]
        self.assertTrue(k.belegt_abgleichen(c, "oskar"))
        rows = c.execute("SELECT start, ende FROM belegt WHERE person='oskar'").fetchall()
        self.assertEqual([(r["start"], r["ende"]) for r in rows], [("2030-01-07T10:00", "2030-01-07T11:00")])  # +1 h
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
                    "/einstellungen/kalender", "/aufgaben", "/kalender/vorschlaege.json?personen=oskar&dauer=30"):
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
        leaddb.add_event(c, lead_anlegen(c, "Kontakt K"), "kontakt", "Angerufen.", author=self.omid)
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
                    "/aufgaben"):
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
        self.assertIn("Meine Wochenziele", self.client.get("/aufgaben").get_data(as_text=True))
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
        for url in ("/kunden", f"/kunden/{pid}", "/einstellungen/projekte", f"/leads/{lid}", "/aufgaben", "/cockpit"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        r = self.post(f"/kunden/{pid}/aktion", {"was": "aenderung", "text": "Bitte Farbe ändern"},
                      headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"])
        aid = c.execute("SELECT MAX(id) FROM aenderungen").fetchone()[0]
        r = self.post("/kunden/aenderung/0", {"aid": aid, "was": "einordnen", "groesse": "klein"},
                      headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"], r.get_data(as_text=True))
        self.post("/zeit/start", {"typ": "projekt", "id": pid})
        self.assertIn("Zeit läuft", self.client.get("/aufgaben").get_data(as_text=True))
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
        from app import projekte, crm
        f, c = self.f, self.c
        tid = self.einladung()
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
        self.assertEqual(f.offen(c, "hamza"), [])  # Lead-Prüfung gibt es nicht mehr
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
        # „Alle offenen“: wer verwaltet, sieht auch, was bei anderen liegt
        alle = f.offen(c, "finn", alle=True)
        self.assertIn("erstkontakt", {x["art"] for x in alle})
        self.assertEqual(f.offen(c, "hamza", alle=True), [])

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
        self.assertEqual(s["angebot"], "D")  # seit Stufe 10 einstellbar (C oder D)
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
                    "/aufgaben", "/wochengespraech", "/einstellungen"):
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
        self.assertIn("Freigaben", self.client.get("/aufgaben").get_data(as_text=True))
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

    def __init__(self, test, versand="agent"):
        from unittest import mock
        from app import agent
        self.mails = []
        smtp = mock.Mock(send_message=lambda m: self.mails.append(m))
        settings = dict(agent.CFG["settings"], OUTREACH_USER="kontakt@ylvalabs.de", OUTREACH_PASSWORD="x",
                        IMAP_HOST="", BASE_URL="https://os.example.de", MAIL_VERSAND=versand)
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


# ---------------------------------------------------------------- Stufe 8: Finanzen

FIRMENDATEN = {"firma_name": "Ylva Labs", "firma_anschrift": "Hauptstraße 1\n24103 Kiel", "steuernummer": "19/123/45678",
               "iban": "DE02 1203 0000 0000 2020 51", "steuer": "klein", "ust_satz": "19", "zahlungsziel": "14",
               "nummer_format": "RE-{jahr}-{n:04}", "versand": "1", "monat_tag": "1", "personalkosten_neu": "0",
               "betreff": "Rechnung {nummer} von {firma}",
               "text": "Guten Tag {name},\n\nanbei die Rechnung {nummer} über {betrag}, zahlbar bis {faellig}."}


def pdf_text(daten):
    import re
    import zlib
    return b"".join(zlib.decompress(m) for m in re.findall(rb"stream\n(.*?)\nendstream", daten, re.S)).decode("cp1252")


class FinanzenTest(unittest.TestCase):
    def setUp(self):
        from app import finanzen, projekte
        self.f, self.pj = finanzen, projekte
        self.c = frische_db()
        self.post = PostfachMock(self)
        self.daten = tempfile.mkdtemp()
        self.pid = projekt_anlegen(self.c, "Tischlerei Finanz", "rechnung@tischlerei.de")
        projekte.aendern(self.c, "oskar", self.pid, preis_einmal="2.000", pauschale_monat="249")

    def test_rechnung_freigabe_nummer_pdf_mail_storno_export(self):
        import zipfile
        from app import freigaben, portal
        f, c = self.f, self.c
        rid = f.einmal_anlegen(c, "oskar", self.pid)
        r = f.get(c, rid)
        self.assertEqual((r["status"], r["nummer"], r["brutto"], r["ust"]), ("entwurf", None, 2000.0, 0))
        self.assertIn("Tischlerei Finanz", r["empfaenger"])
        with self.assertRaises(f.FinanzError):
            f.einmal_anlegen(c, "oskar", self.pid)  # nur einmal
        with self.assertRaises(f.FinanzError):
            f.zur_freigabe(c, "oskar", rid)  # Firmendaten fehlen
        f.regeln_speichern(c, "oskar", FIRMENDATEN)
        self.assertEqual(f.regeln(c)["iban"], "DE02120300000000202051")
        f.bearbeiten(c, "oskar", rid, positionen=[("Einrichtung", "1", "2.000,00"), ("Schulung", "2", "150"), ("", "", "")])
        self.assertEqual(f.get(c, rid)["brutto"], 2300.0)
        f.zur_freigabe(c, "oskar", rid)
        with self.assertRaises(f.FinanzError):
            f.bearbeiten(c, "oskar", rid, empfaenger="x")  # nicht mehr Entwurf
        offen = [x for x in freigaben.offen(c, "oskar") if x["art"] == "rechnung"]
        self.assertEqual((len(offen), offen[0]["stufe"]), (1, "C"))
        with self.assertRaises(freigaben.FreigabeError):
            freigaben.entscheiden(c, "hamza", "rechnung", offen[0]["oid"], True)
        f.entscheiden(c, "oskar", rid, True, data_dir=self.daten)
        r = f.get(c, rid)
        jahr = date.today().year
        self.assertEqual((r["status"], r["nummer"]), ("offen", f"RE-{jahr}-0001"))
        self.assertTrue(r["faellig"] > r["datum"])
        text = pdf_text((Path(self.daten) / "rechnungen" / r["pdf"]).read_bytes())
        for teil in ("Rechnung", f"RE-{jahr}-0001", "Gemäß § 19 UStG", "2.300,00 €", "Steuernummer 19/123/45678"):
            self.assertIn(teil, text)
        mail = self.post.mails[0]
        self.assertEqual(mail["Subject"], f"Rechnung RE-{jahr}-0001 von Ylva Labs")
        self.assertEqual([a.get_filename() for a in mail.iter_attachments()], [f"RE-{jahr}-0001.pdf"])
        self.assertTrue(r["verschickt_am"])
        self.assertEqual([d["art"] for d in portal.dokumente(c, self.pid, nur_sichtbar=True)], ["rechnung"])
        f.bezahlt(c, "oskar", rid)
        self.assertEqual(f.get(c, rid)["status"], "bezahlt")
        with self.assertRaises(f.FinanzError):
            f.stornieren(c, "oskar", rid, "")
        sid = f.stornieren(c, "oskar", rid, "Doppelt berechnet", self.daten)
        st = f.get(c, sid)
        self.assertEqual((st["nummer"], st["brutto"], st["art"], st["status"]), (f"RE-{jahr}-0002", -2300.0, "storno",
                                                                                 "storniert"))
        self.assertEqual(f.get(c, rid)["status"], "storniert")
        self.assertIn("Stornorechnung", pdf_text(f.pdf_bytes(c, sid)))
        self.assertEqual(f.umsatz(c, date.today().replace(day=1).isoformat(), "2999-01-01"), 0)  # gleicht aus
        f.einmal_anlegen(c, "oskar", self.pid)  # nach dem Storno wieder möglich
        daten, name = f.export_zip(c, date.today().replace(day=1).isoformat(),
                                   (date.today() + timedelta(days=1)).isoformat(), self.daten)
        z = zipfile.ZipFile(io_bytes(daten))
        self.assertIn(f"rechnungen/RE-{jahr}-0001.pdf", z.namelist())
        csv_text = z.read("rechnungen.csv").decode("utf-8-sig")
        self.assertIn(f"RE-{jahr}-0002;", csv_text)
        self.assertIn("-2300,00", csv_text)
        self.assertTrue(name.endswith(".zip"))

    def test_monatsrechnung_takt_regelsteuer_zurueck(self):
        from datetime import datetime as dt
        from app import freigaben
        f, pj, c = self.f, self.pj, self.c
        f.regeln_speichern(c, "oskar", dict(FIRMENDATEN, steuer="regel"))
        c.execute("UPDATE projekte SET status='live' WHERE id=?", (self.pid,))
        aid = pj.aenderung_anlegen(c, "oskar", self.pid, "Logo austauschen")
        monat = date.today().replace(day=1)
        vormonat = (monat - timedelta(days=1)).replace(day=10)
        c.execute("UPDATE aenderungen SET status='erledigt', abrechnung='pauschale', erledigt_am=? WHERE id=?",
                  (vormonat.isoformat() + "T10:00:00", aid))
        c.commit()
        self.assertEqual(f.takt(c, dt.combine(monat, dt.min.time()).replace(hour=3)), 0)  # zu früh am Tag
        self.assertEqual(f.takt(c, dt.combine(monat, dt.min.time()).replace(hour=7)), 1)
        self.assertEqual(f.takt(c, dt.combine(monat, dt.min.time()).replace(hour=8)), 0)  # einmal je Monat
        r = f.liste(c, projekt_id=self.pid)[0]
        preis = pj.regeln(c)["pauschale_klein"]
        self.assertEqual((r["art"], r["status"], len(r["positionen"])), ("monat", "wartet", 2))
        self.assertEqual(r["netto"], 249 + preis)
        self.assertEqual(r["brutto"], round((249 + preis) * 1.19, 2))
        fid = next(x["oid"] for x in freigaben.offen(c, "oskar") if x["art"] == "rechnung")
        freigaben.entscheiden(c, "oskar", "rechnung", fid, False, "Pauschale ist diesen Monat kostenlos")
        r = f.get(c, r["id"])
        self.assertEqual((r["status"], r["grund"]), ("entwurf", "Pauschale ist diesen Monat kostenlos"))
        self.assertIsNone(f.monat_anlegen(c, "oskar", self.pid, monat.strftime("%Y-%m")))  # Entwurf gibt es schon
        f.loeschen(c, "oskar", r["id"])
        self.assertTrue(f.monat_anlegen(c, "oskar", self.pid, monat.strftime("%Y-%m")))

    def test_kosten_kasse_kennzahlen_geldregel(self):
        f, c = self.f, self.c
        with self.assertRaises(f.FinanzError):
            f.kosten_speichern(c, "oskar", None, "", "10")
        kid = f.kosten_speichern(c, "oskar", None, "Server", "39,90", "server", "2026-01-01")
        f.kosten_speichern(c, "oskar", None, "Alte Lizenz", "20", "lizenz", "2025-01-01", "2025-12-31")
        self.assertEqual(f.fixkosten(c), 39.9)
        f.kosten_speichern(c, "oskar", kid, "Server", "60", "server", "2026-01-01")
        k = f.kennzahlen(c)
        self.assertEqual((k["fixkosten"], k["mrr"], k["ampel"], k["kasse"]), (60.0, 0, "grau", None))
        c.execute("UPDATE projekte SET status='betrieb' WHERE id=?", (self.pid,))
        c.commit()
        f.kasse_eintragen(c, "oskar", "12.000", "2026-10-31")
        f.regeln_speichern(c, "oskar", dict(FIRMENDATEN, personalkosten_neu="1000"))
        k = f.kennzahlen(c)
        self.assertEqual((k["mrr"], k["gewinnschwelle"], k["kasse"]), (249.0, True, 12000.0))
        self.assertEqual(k["ziel_kasse"], round(6 * (k["monatskosten"] + 1000), 2))
        self.assertEqual((k["bed_uebrig"], k["bed_kasse"], k["ampel"]), (False, True, "gelb"))  # kein Umsatz bisher
        with self.assertRaises(f.FinanzError):
            f.regeln_speichern(c, "oskar", dict(FIRMENDATEN, nummer_format="RE-{jahr}"))


def io_bytes(daten):
    import io
    return io.BytesIO(daten)


class FinanzenWebTest(WebBasis):
    def test_seiten_rechte_pdf_export(self):
        from app import finanzen
        c = self.c
        pid = projekt_anlegen(c, "Web Finanz Kunde", "web@finanz.de")
        c.execute("UPDATE projekte SET preis_einmal=990 WHERE id=?", (pid,))
        c.commit()
        self.addCleanup(lambda: (c.execute("DELETE FROM rechnungen WHERE projekt_id=?", (pid,)),
                                 c.execute("DELETE FROM projekte WHERE id=?", (pid,)), c.commit()))
        r = self.post("/finanzen/rechnung/neu", {"projekt_id": pid, "art": "einmal"})
        rid = int(r.headers["Location"].rsplit("/", 1)[1])
        for url in ("/finanzen", "/finanzen/kosten", "/einstellungen/finanzen", f"/finanzen/rechnung/{rid}",
                    f"/kunden/{pid}", "/einstellungen", "/freigaben"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        r = self.client.get(f"/finanzen/rechnung/{rid}.pdf")
        self.assertEqual((r.status_code, r.mimetype), (200, "application/pdf"))
        self.assertIn("ENTWURF", pdf_text(r.data))
        r = self.post(f"/finanzen/rechnung/{rid}", {"was": "speichern", "empfaenger": "Web Finanz Kunde\nKiel",
                                                    "email": "web@finanz.de", "leistung": "11/2026",
                                                    "pos_text": ["Einrichtung", ""], "pos_menge": ["1", "1"],
                                                    "pos_einzel": ["990", ""]}, headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"], r.get_data(as_text=True))
        self.assertEqual(finanzen.get(c, rid)["leistung"], "11/2026")
        self.post("/finanzen/kasse", {"stand": "1.000", "datum": "2026-10-01"})
        r = self.client.get("/finanzen/export?von=2026-01-01&bis=2027-01-01")
        self.assertEqual((r.status_code, r.mimetype), (200, "application/zip"))
        self.als("hamza")
        self.assertEqual(self.client.get("/finanzen").status_code, 403)
        self.assertNotIn("/finanzen/rechnung/", self.client.get("/aufgaben").get_data(as_text=True))


# ---------------------------------------------------------------- Stufe 9: Ein Posteingang


def mail_bytes(von, betreff, text, extra=None, tage=0):
    from email.message import EmailMessage
    from email.utils import formatdate, make_msgid
    import time
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = von, "kontakt@ylvalabs.de", betreff
    m["Date"] = formatdate(time.time() - tage * 86400, localtime=True)
    m["Message-ID"] = make_msgid(domain="example.de")
    for k, v in (extra or {}).items():
        m[k] = v
    m.set_content(text)
    return m.as_bytes()


class FakeImap:
    def __init__(self, mails):
        self.mails = mails

    def select(self, *a, **kw):
        return "OK", [b"1"]

    def search(self, *a):
        return "OK", [" ".join(str(i + 1) for i in range(len(self.mails))).encode()]

    def fetch(self, num, spec):
        return "OK", [(b"1 (BODY[] {1}", self.mails[int(num) - 1])]

    def logout(self):
        pass


class PosteingangTest(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        from app import posteingang
        self.pe = posteingang
        self.c = frische_db()
        self.post = PostfachMock(self)
        self.imap_mails, self.smtp_mails = [], []
        smtp = mock.Mock(send_message=lambda m: self.smtp_mails.append(m))
        p1 = mock.patch.object(posteingang, "IMAP", lambda p: FakeImap(self.imap_mails))
        p2 = mock.patch.object(posteingang, "SMTP", lambda p: smtp)
        p1.start(), p2.start()
        self.addCleanup(p1.stop), self.addCleanup(p2.stop)
        self.pid_projekt = projekt_anlegen(self.c, "Tischlerei Post", "info@tischlerei-post.de")
        self.lid = self.c.execute("SELECT lead_id FROM projekte WHERE id=?", (self.pid_projekt,)).fetchone()[0]

    def postfach(self, besitzer="oskar"):
        return self.pe.postfach_speichern(self.c, "oskar", None, {
            "name": "Oskar", "adresse": "oskar@ylvalabs.de", "passwort": "geheim", "imap_host": "imap.example.de",
            "smtp_host": "smtp.example.de", "besitzer": besitzer, "aktiv": "1"})

    def test_postfach_abrufen_zuordnen_senden(self):
        pe, c = self.pe, self.c
        with self.assertRaises(pe.PosteingangError):
            pe.postfach_speichern(c, "oskar", None, {"name": "X", "adresse": "kaputt", "imap_host": "a", "smtp_host": "b"})
        pid = self.postfach()
        self.assertNotIn("geheim", str([tuple(r) for r in c.execute("SELECT * FROM postfaecher")]))  # verschlüsselt
        self.assertEqual(pe._passwort(pe.postfach(c, pid)), "geheim")
        self.imap_mails += [
            mail_bytes("Frau Post <info@tischlerei-post.de>", "Kleine Bitte", "Können Sie das Logo größer machen?"),
            mail_bytes("News <news@shop.de>", "Angebote", "Rabatt!", {"List-Unsubscribe": "<mailto:x@shop.de>"}),
            mail_bytes("Neu Kunde <neu@firma-neu.de>", "Anfrage", "Was kostet das?"),
            mail_bytes("Alt <alt@firma.de>", "Alt", "Uralt", tage=10),
        ]
        self.assertEqual(pe.abrufen(c, pid), 2)
        self.assertEqual(pe.abrufen(c, pid), 0)  # schon gesehen
        m = c.execute("SELECT * FROM messages WHERE lead_id=? AND direction='in'", (self.lid,)).fetchone()
        self.assertEqual((m["postfach_id"], m["kanal"], m["wunsch"], m["read"]), (pid, "mail", "vorschlag", 0))
        self.assertTrue(c.execute("SELECT 1 FROM leads WHERE email='neu@firma-neu.de' AND status='kontakt'").fetchone())
        self.assertIn("Neue Mail von Tischlerei Post", [r["title"] for r in leaddb.notices(c, "o", 20, "oskar")[0]])
        self.assertIn("Möglicher Änderungswunsch: Tischlerei Post",
                      [r["title"] for r in leaddb.notices(c, "o", 20, "oskar")[0]])
        self.assertTrue(pe.wartet_seit(c, self.lid))
        self.assertEqual([w["id"] for w in pe.wartend(c, "oskar") if w["id"] == self.lid], [self.lid])
        lead = leaddb.get_lead(c, self.lid)
        with self.assertRaises(pe.PosteingangError):
            pe.senden(c, "hamza", pid, lead, "info@tischlerei-post.de", "Re: Kleine Bitte", "Klar.")  # Oskars Postfach
        pe.senden(c, "oskar", pid, lead, "info@tischlerei-post.de", "Re: Kleine Bitte", "Klar, machen wir.", m["message_id"])
        sent = self.smtp_mails[0]
        self.assertIn("oskar@ylvalabs.de", sent["From"])
        self.assertEqual(sent["In-Reply-To"], m["message_id"])
        self.assertIsNone(pe.wartet_seit(c, self.lid))
        self.assertEqual(c.execute("SELECT postfach_id FROM messages WHERE lead_id=? AND direction='out'",
                                   (self.lid,)).fetchone()[0], pid)
        # Sperrliste (Domain) und Erstkontakt-Regel gelten für jedes Postfach
        leaddb.kv_set(c, "blocklist", ["tischlerei-post.de"])
        with self.assertRaises(pe.PosteingangError):
            pe.senden(c, "oskar", pid, lead, "info@tischlerei-post.de", "Noch was", "Hallo")
        kalt = leaddb.get_lead(c, lead_anlegen(c, "Kalter Lead", email="kalt@betrieb.de"))
        with self.assertRaises(pe.PosteingangError):
            pe.senden(c, "oskar", pid, kalt, "kalt@betrieb.de", "Hallo", "Erstkontakt")
        # Fehler am Postfach landen am Postfach
        from unittest import mock

        def kaputt(p):
            raise OSError("Verbindung abgelehnt")
        with mock.patch.object(pe, "IMAP", kaputt):
            with self.assertRaises(pe.PosteingangError):
                pe.abrufen(c, pid)
        self.assertTrue(pe.postfach(c, pid)["fehler"])

    def test_festhalten_und_wunsch(self):
        from app import projekte
        pe, c = self.pe, self.c
        with self.assertRaises(pe.PosteingangError):
            pe.festhalten(c, "hamza", self.lid, "fax", "in", "Hallo")
        mid = pe.festhalten(c, "hamza", self.lid, "telefon", "in", "Kundin möchte einen zweiten Benutzer.")
        m = c.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        self.assertEqual((m["kanal"], m["erfasst_von"], m["wunsch"]), ("telefon", "hamza", "vorschlag"))
        with self.assertRaises(pe.PosteingangError):
            pe.wunsch_entscheiden(c, "hamza", mid, True)  # entscheidet die verantwortliche Person
        aid = pe.wunsch_entscheiden(c, "oskar", mid, True)
        a = projekte.aenderung_get(c, aid)
        self.assertEqual(a["quelle"], "telefon")
        self.assertIn("zweiten Benutzer", a["text"])
        with self.assertRaises(pe.PosteingangError):
            pe.wunsch_entscheiden(c, "oskar", mid, True)  # nur einmal
        mid2 = pe.festhalten(c, "oskar", self.lid, "whatsapp", "in", "Danke für das Update!")
        self.assertIsNone(pe.wunsch_entscheiden(c, "oskar", mid2, False))
        self.assertEqual(c.execute("SELECT wunsch FROM messages WHERE id=?", (mid2,)).fetchone()[0], "nein")
        # Von uns festgehalten: kein Wunsch, beantwortet die Wartenden
        pe.festhalten(c, "oskar", self.lid, "telefon", "out", "Zurückgerufen, alles geklärt.")
        self.assertIsNone(pe.wartet_seit(c, self.lid))
        # Wünsche aus dem Kunden-Bereich stehen auch im Posteingang, schon übernommen
        from app import portal
        link = portal.zugang_schicken(c, "oskar", self.pid_projekt, senden=False)
        z = portal.sitzung(c, portal.anmelden(c, link.rsplit("/", 1)[1])[0])
        portal.wunsch(c, z, "Bitte Farbe ändern")
        m = c.execute("SELECT * FROM messages WHERE kanal='portal'").fetchone()
        self.assertEqual((m["wunsch"], m["read"], m["direction"]), ("uebernommen", 0, "in"))

    def test_antwortzeit_kpi(self):
        from app import kpis, mailbox
        pe, c = self.pe, self.c
        von, bis = kpis.woche_grenzen(kpis.woche_von())
        tag = von[:10]
        mailbox.add(c, self.lid, "in", "Frage", at=tag + "T09:00:00")
        mailbox.add(c, self.lid, "out", "Antwort", at=tag + "T11:00:00")
        mailbox.add(c, self.lid, "in", "Noch eine", at=tag + "T12:00:00")
        mailbox.add(c, self.lid, "out", "Antwort 2", at=tag + "T16:00:00")
        self.assertEqual(pe.antwortzeit(c, von, bis), 3.0)
        self.assertEqual(kpis.wert(c, "antwortzeit", kpis.woche_von()), 3.0)
        self.assertEqual(kpis.wert(c, "antwortzeit", kpis.woche_von(), "hamza"), None)  # Oskar ist zuständig


class PosteingangWebTest(WebBasis):
    def test_gespraech_festhalten_wunsch_einstellungen(self):
        c = self.c
        pid = projekt_anlegen(c, "Web Post Kunde", "web@post-kunde.de")
        lid = c.execute("SELECT lead_id FROM projekte WHERE id=?", (pid,)).fetchone()[0]
        self.addCleanup(lambda: (c.execute("DELETE FROM projekte WHERE id=?", (pid,)),
                                 c.execute("DELETE FROM messages WHERE lead_id=?", (lid,)), c.commit()))
        r = self.post(f"/crm/postfach/{lid}/festhalten", {"kanal": "brief", "richtung": "in", "text": "Brief mit Wunsch"})
        self.assertTrue(r.get_json()["ok"], r.get_data(as_text=True))
        html = self.client.get(f"/chat/{lid}").get_data(as_text=True)
        self.assertIn("Brief mit Wunsch", html)
        self.assertIn("Als Wunsch übernehmen", html)
        self.assertIn("wartet seit", html)
        mid = c.execute("SELECT id FROM messages WHERE lead_id=? AND kanal='brief'", (lid,)).fetchone()[0]
        r = self.post(f"/crm/postfach/nachricht/{mid}/wunsch", {"ja": "1"})
        self.assertTrue(r.get_json()["ok"])
        self.assertIn("Wartet auf euch", self.client.get("/chat?wer=alle").get_data(as_text=True))
        self.assertEqual(self.client.get("/einstellungen/postfaecher").status_code, 200)
        r = self.post("/einstellungen/postfaecher", {"name": "Kontakt", "adresse": "kontakt2@ylvalabs.de",
                                                     "passwort": "x", "imap_host": "imap.example.de",
                                                     "smtp_host": "smtp.example.de", "aktiv": "1"},
                      headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"], r.get_data(as_text=True))
        self.addCleanup(lambda: (c.execute("DELETE FROM postfaecher"), c.commit()))
        self.assertIn("Kontakt (kontakt2@ylvalabs.de)", self.client.get(f"/crm/postfach/{lid}?alt=1").get_data(as_text=True))
        self.als("hamza")
        self.assertEqual(self.client.get("/einstellungen/postfaecher").status_code, 403)
        # Ein Postfach des Teams steht allen beim Antworten zur Wahl
        self.assertIn("Kontakt (kontakt2@ylvalabs.de)", self.client.get(f"/crm/postfach/{lid}?alt=1").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- Stufe 10: Assistent und Angebote

class FakeClaude:
    """Spielt die Claude-API nach: gibt vorbereitete Antworten der Reihe nach zurück und merkt sich die Anfragen."""

    def __init__(self, antworten):
        from types import SimpleNamespace as NS
        self.NS = NS
        self.antworten = list(antworten)
        self.anfragen = []
        self.messages = NS(create=self._create)
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **params):
        import copy
        self.anfragen.append(copy.deepcopy({k: v for k, v in params.items() if k != "messages"})
                             | {"messages": list(params["messages"])})
        return self.antworten.pop(0)

    def antwort(self, *bloecke, stop="end_turn"):
        NS = self.NS
        content = []
        for b in bloecke:
            if isinstance(b, str):
                content.append(NS(type="text", text=b))
            else:
                content.append(NS(type="tool_use", id=b[0], name=b[1], input=b[2]))
        return NS(content=content, stop_reason=stop, model="claude-opus-5-5",
                  usage=NS(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0, cache_read_input_tokens=0))


class AssistentTest(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        from app import ai, assistent
        self.ai, self.a = ai, assistent
        self.c = frische_db()
        self.lid = lead_anlegen(self.c, "Tischlerei Hansen", stage="gespraech", owner="oskar",
                                email="info@hansen.example", city="Kiel")
        env = mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"})
        env.start()
        self.addCleanup(env.stop)

    def fake(self, *antworten):
        from unittest import mock
        f = FakeClaude([])
        f.antworten = [a(f) if callable(a) else a for a in antworten]
        p = mock.patch.object(self.ai, "client", return_value=f)
        p.start()
        self.addCleanup(p.stop)
        return f

    def test_bereit_ohne_schluessel_und_ausgeschaltet(self):
        a = self.a
        self.assertEqual(a.bereit(self.c, "oskar"), (True, None))
        from unittest import mock
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            ok, warum = a.bereit(self.c, "oskar")
            self.assertFalse(ok)
            self.assertIn("KI-Schlüssel", warum)
        a.regeln_speichern(self.c, "oskar", {"modell": "claude-sonnet-5-5", "aufwand": "low", "schritte": "4",
                                             "angebot_tage": "21", "angebot_nummer": "A-{jahr}-{nr:03d}"})
        r = a.regeln(self.c)
        self.assertEqual((r["an"], r["modell"], r["aufwand"], r["schritte"], r["angebot_tage"]),
                         (False, "claude-sonnet-5-5", "low", 4, 21))
        with self.assertRaises(a.AssistentError):
            a.fragen(self.c, "oskar", "Was ist offen?")
        with self.assertRaises(a.AssistentError):
            a.regeln_speichern(self.c, "oskar", {"an": "1", "modell": "gpt-9", "aufwand": "low", "schritte": "4",
                                                 "angebot_tage": "21", "angebot_nummer": "A-{nr}"})

    def test_frage_mit_werkzeugen_und_quellen(self):
        a = self.a
        f = self.fake(lambda f: f.antwort("Ich suche.", ("t1", "suche", {"stichwort": "Hansen"}), stop="tool_use"),
                      lambda f: f.antwort(("t2", "akte", {"lead_id": self.lid}), stop="tool_use"),
                      lambda f: f.antwort(f"Die [Tischlerei Hansen](/leads/{self.lid}) ist im Gespräch."))
        fid = a.fragen(self.c, "oskar", "Wie steht es um Hansen?")
        eintrag = a.verlauf(self.c, "oskar")[0]
        self.assertEqual(eintrag["id"], fid)
        self.assertIn("im Gespräch", eintrag["antwort"])
        self.assertEqual(eintrag["quellen"], [{"titel": "Tischlerei Hansen", "url": f"/leads/{self.lid}"}])
        self.assertEqual(len(f.anfragen), 3)
        erste = f.anfragen[0]
        self.assertEqual(erste["model"], "claude-opus-5-5")
        self.assertEqual(erste["thinking"], {"type": "adaptive"})
        self.assertEqual(erste["fallbacks"], "default")
        self.assertTrue(all(t["strict"] for t in erste["tools"]))
        # Werkzeug-Ergebnis geht mit der passenden ID zurück, die Assistenten-Antwort vollständig davor
        letzte = f.anfragen[2]["messages"]
        self.assertEqual(letzte[-2]["role"], "assistant")
        self.assertEqual(letzte[-1]["content"][0]["tool_use_id"], "t2")
        self.assertIn("Tischlerei Hansen", letzte[-1]["content"][0]["content"])
        prot = firma.protokoll(self.c, limit=5)
        self.assertTrue(any(p["art"] == "assistent" for p in prot))

    def test_werkzeuge_halten_die_rechte_ein(self):
        a = self.a
        text, quellen, fehler = a.werkzeug(self.c, "hamza", "rechnungen", {"status": ""})
        self.assertTrue(fehler)
        self.assertIn("Keine Berechtigung", text)
        _t, _q, fehler = a.werkzeug(self.c, "oskar", "rechnungen", {"status": ""})
        self.assertFalse(fehler)
        _t, _q, fehler = a.werkzeug(self.c, "oskar", "gibtsnicht", {})
        self.assertTrue(fehler)
        # Im Gespräch: der Fehler geht als is_error ans Modell, die Antwort kommt trotzdem
        f = self.fake(lambda f: f.antwort(("t1", "rechnungen", {"status": ""}), stop="tool_use"),
                      lambda f: f.antwort("Das darfst du nicht sehen."))
        a.fragen(self.c, "hamza", "Welche Rechnungen sind offen?")
        ergebnis = f.anfragen[1]["messages"][-1]["content"][0]
        self.assertTrue(ergebnis["is_error"])

    def test_fehler_der_ki_wird_gespeichert(self):
        a = self.a
        self.fake(lambda f: f.antwort("", stop="refusal"))
        with self.assertRaises(a.AssistentError):
            a.fragen(self.c, "oskar", "Irgendwas?")
        self.assertEqual(a.verlauf(self.c, "oskar")[0]["status"], "fehler")

    def test_reihenfolge_und_ki_prioritaeten(self):
        a = self.a
        aufgaben.anlegen(self.c, "oskar", "Steuer vorbereiten", "markt", GESTERN, person="oskar")
        aufgaben.anlegen(self.c, "oskar", "Webseite lesen", "markt", HEUTE, person="oskar")
        liste = a.reihenfolge(self.c, "oskar")
        titel = [x["titel"] for x in liste]
        self.assertLess(titel.index("Steuer vorbereiten"), titel.index("Webseite lesen"))
        nr = {x["titel"]: x["nr"] for x in liste}
        import json as _j
        self.fake(lambda f: f.antwort(_j.dumps({"reihenfolge": [{"nr": nr["Webseite lesen"], "warum": "Schnell erledigt."},
                                                               {"nr": 99, "warum": "erfunden"}], "hinweis": "Nichts."})))
        ergebnis, hinweis = a.prioritaeten_ki(self.c, "oskar")
        self.assertEqual([x["titel"] for x in ergebnis], ["Webseite lesen"])  # erfundene Nummer fällt raus
        self.assertEqual(a.letzte_prioritaeten(self.c, "oskar")["reihenfolge"][0]["warum_ki"], "Schnell erledigt.")

    def test_entwurf_antwort_braucht_nachricht(self):
        a = self.a
        with self.assertRaises(a.AssistentError):
            a.entwurf_antwort(self.c, "oskar", self.lid)
        lead = leaddb.get_lead(self.c, self.lid)
        crm.add_event(self.c, self.lid, "reply", "Tischlerei Hansen", "Was kostet das?") if hasattr(crm, "add_event") \
            else None
        from app import agent
        agent.record_reply(self.c, lead, "Was kostet das ungefähr?", "Tischlerei Hansen", "Ihre Mail",
                           "info@hansen.example", "<m1@x>", what="Antwort", direction="in") \
            if "direction" in agent.record_reply.__code__.co_varnames else self.c.execute(
                "INSERT INTO messages (lead_id, direction, at, subject, text, from_addr) VALUES (?,?,?,?,?,?)",
                (self.lid, "in", HEUTE + "T09:00", "Ihre Mail", "Was kostet das ungefähr?", "info@hansen.example"))
        import json as _j
        self.fake(lambda f: f.antwort(_j.dumps({"betreff": "Re: Ihre Mail", "text": "Gern im Gespräch."})))
        e = a.entwurf_antwort(self.c, "oskar", self.lid)
        self.assertEqual(e["betreff"], "Re: Ihre Mail")

    def test_angebot_ablauf_c_und_d(self):
        a, c = self.a, self.c
        with self.assertRaises(a.AssistentError):
            a.angebot_anlegen(c, "hamza", self.lid, mit_ki=False)  # kein Kunden-Recht
        aid = a.angebot_anlegen(c, "oskar", self.lid, mit_ki=False)
        x = a.angebot_get(c, aid)
        self.assertEqual((x["status"], x["email"], x["ki"]), ("entwurf", "info@hansen.example", 0))
        with self.assertRaises(a.AssistentError):
            a.zur_freigabe(c, "oskar", aid)  # ohne Schätzung
        with self.assertRaises(a.AssistentError):
            a.schaetzung_setzen(c, "oskar", aid, "10")  # nur die Technik schätzt
        a.schaetzung_setzen(c, "finn", aid, "12,5")
        with self.assertRaises(a.AssistentError):
            a.zur_freigabe(c, "oskar", aid)  # Beträge 0 und kein Text
        a.angebot_bearbeiten(c, "oskar", aid, "Anfragen sortieren", "Ausgangslage\nViele Mails.",
                             [{"text": "Einrichtung", "betrag": "4.500"}, {"text": "Betrieb", "betrag": "149,50",
                                                                           "monatlich": True}, {"text": "", "betrag": ""}])
        x = a.angebot_get(c, aid)
        self.assertEqual((x["summe"], x["monatlich"], len(x["positionen"])), (4500.0, 149.5, 2))
        a.zur_freigabe(c, "oskar", aid)
        f = c.execute("SELECT * FROM freigaben WHERE art='angebot'").fetchone()
        self.assertEqual((f["stufe"], f["pruefer"], f["status"]), ("C", "oskar", "offen"))
        from app import freigaben
        self.assertTrue(any(o["art"] == "angebot" for o in freigaben.offen(c, "oskar")))
        # Ohne Postfach: freigegeben, aber nicht verschickt → von Hand übergeben
        tmp = tempfile.mkdtemp()
        x = a.entscheiden(c, "oskar", aid, True, data_dir=tmp)
        jahr = date.today().year
        self.assertEqual(x["nummer"], f"A-{jahr}-001")
        self.assertIn(x["status"], ("versendet", "uebergeben"))
        self.assertTrue(a.pdf_bytes(c, aid).startswith(b"%PDF"))
        self.assertTrue(a.pfad(c, aid, tmp).exists())
        self.assertEqual(leaddb.get_lead(c, self.lid)["stage"], "angebot")
        a.ergebnis(c, "oskar", aid, True)
        self.assertEqual(leaddb.get_lead(c, self.lid)["stage"], "kunde")
        from app import projekte
        p = projekte.zum_lead(c, self.lid)
        self.assertEqual((p["preis_einmal"], p["pauschale_monat"]), (4500.0, 149.5))
        # Stufe D: nur persönlich übergeben, mit Aufgabe
        freigaben.stufen_speichern(c, "oskar", {"angebot": "D"})
        aid2 = a.angebot_anlegen(c, "oskar", self.lid, mit_ki=False)
        a.schaetzung_setzen(c, "finn", aid2, "3")
        a.angebot_bearbeiten(c, "oskar", aid2, "Zusatz", "Text", [{"text": "Zusatz", "betrag": "900"}])
        a.zur_freigabe(c, "oskar", aid2)
        fid = c.execute("SELECT id FROM freigaben WHERE art='angebot' AND status='offen'").fetchone()["id"]
        with self.assertRaises(freigaben.FreigabeError):
            freigaben.entscheiden(c, "oskar", "angebot", fid, False, "")  # Ablehnen braucht Grund
        freigaben.entscheiden(c, "oskar", "angebot", fid, True)
        x = a.angebot_get(c, aid2)
        self.assertEqual((x["status"], x["nummer"]), ("uebergeben", f"A-{jahr}-002"))
        self.assertTrue(any("persönlich übergeben" in t["titel"] for t in aufgaben.zum_objekt(c, "lead", self.lid)))

    def test_angebot_mit_ki_ohne_preise_der_ki(self):
        a = self.a
        import json as _j
        self.fake(lambda f: f.antwort(_j.dumps({"titel": "Angebot: Anfragen sortieren", "text": "Ausgangslage\nX"})))
        aid = a.angebot_anlegen(self.c, "oskar", self.lid, mit_ki=True)
        x = a.angebot_get(self.c, aid)
        self.assertEqual((x["titel"], x["ki"]), ("Angebot: Anfragen sortieren", 1))
        self.assertTrue(all(p["betrag"] == 0 for p in x["positionen"]))  # Preise nie von der KI


class AssistentWebTest(WebBasis):
    def test_seiten_und_rechte(self):
        aid0 = aufgaben.anlegen(self.c, "oskar", "Angebot vorbereiten", "markt", HEUTE, person="oskar")
        self.addCleanup(lambda: (self.c.execute("DELETE FROM aufgaben WHERE id=?", (aid0,)), self.c.commit()))
        for url in ("/assistent", "/einstellungen/assistent", "/angebote", "/aufgaben"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
        self.assertIn("Angebot vorbereiten", self.client.get("/aufgaben").get_data(as_text=True))  # im Stapel
        lid = lead_anlegen(self.c, "Malerei Web", stage="gespraech", owner="oskar")
        r = self.post("/angebote/neu", {"lead_id": lid, "ki": "0"}, headers={"X-Requested-With": "fetch"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        aid = int(r.get_json()["url"].rsplit("/", 1)[1])
        self.addCleanup(lambda: (self.c.execute("DELETE FROM angebote WHERE id=?", (aid,)), self.c.commit()))
        html = self.client.get(f"/angebote/{aid}").get_data(as_text=True)
        self.assertIn("Malerei Web", html)
        r = self.post(f"/angebote/{aid}", {"was": "speichern", "titel": "Neu", "text": "T", "pos_text": ["A", "B"],
                                          "pos_betrag": ["100", "20"], "pos_monatlich": ["1"]},
                      headers={"X-Requested-With": "fetch"})
        self.assertEqual(r.status_code, 200)
        x = __import__("app.assistent", fromlist=["x"]).angebot_get(self.c, aid)
        self.assertEqual((x["summe"], x["monatlich"]), (100.0, 20.0))
        self.assertEqual(self.client.get(f"/angebote/{aid}/pdf").data[:4], b"%PDF")
        self.als("hamza")
        self.assertEqual(self.client.get("/angebote").status_code, 403)
        self.assertEqual(self.post("/einstellungen/assistent", {"an": "1"}).status_code, 403)
        self.assertEqual(self.client.get("/assistent").status_code, 200)

    def test_antwort_text_ist_sicher(self):
        from app.assistent_views import _text
        html = str(_text("**fett** <script>x</script>\n- [Akte](/leads/1)\n- [böse](https://evil.example)"))
        self.assertIn("<b>fett</b>", html)
        self.assertNotIn("<script>", html)
        self.assertIn('<a href="/leads/1">Akte</a>', html)
        self.assertNotIn('href="https://evil', html)


class StartklarTest(WebBasis):
    def _punkte(self):
        from app import startklar
        with main.app.test_request_context():
            from flask import url_for
            return {p["key"]: p for p in startklar.punkte(self.c, url_for)}

    def test_punkte_zeigen_was_fehlt(self):
        from app import finanzen
        p = self._punkte()
        for key in ("personen", "bereiche", "postfach", "erstkontakt", "adresse", "rechnung", "sicherung", "ki", "google", "push"):
            self.assertIn(key, p)
            self.assertTrue(p[key]["ziel"].startswith("/"), key)
        self.assertFalse(p["rechnung"]["ok"])
        self.assertIn("IBAN", p["rechnung"]["text"])
        alt = finanzen.regeln(self.c)
        self.addCleanup(lambda: (self.c.execute("DELETE FROM kv WHERE key='regel:finanzen'"), self.c.commit()))
        finanzen.regeln_speichern(self.c, "oskar", {**{k: str(v) for k, v in alt.items()}, "firma_anschrift": "Weg 1",
                                                   "steuernummer": "12/345", "iban": "DE00 1234"})
        self.assertTrue(self._punkte()["rechnung"]["ok"])
        alt_key = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        self.addCleanup(lambda: os.environ.__setitem__("ANTHROPIC_API_KEY", alt_key) if alt_key is not None
                        else os.environ.pop("ANTHROPIC_API_KEY", None))
        self.assertTrue(self._punkte()["ki"]["ok"])
        self.assertEqual(self._punkte()["ki"]["wichtig"], "empfohlen")

    def test_erstkontakt_per_mail(self):
        alt = os.environ.get("OUTREACH_EMAIL")
        self.addCleanup(lambda: os.environ.__setitem__("OUTREACH_EMAIL", alt) if alt is not None
                        else os.environ.pop("OUTREACH_EMAIL", None))
        os.environ["OUTREACH_EMAIL"] = "false"
        self.assertFalse(self._punkte()["erstkontakt"]["ok"])
        os.environ["OUTREACH_EMAIL"] = "true"
        self.assertTrue(self._punkte()["erstkontakt"]["ok"])
        self.assertEqual(self.post("/demo").status_code, 404)  # Demo-Modus gibt es nicht mehr

    def test_bereich_ohne_verantwortliche_person(self):
        self.c.execute("INSERT INTO bereiche (key, name, verantwortlich) VALUES ('test_leer', 'Testbereich', 'weg')")
        self.c.commit()
        self.addCleanup(lambda: (self.c.execute("DELETE FROM bereiche WHERE key='test_leer'"), self.c.commit()))
        p = self._punkte()["bereiche"]
        self.assertFalse(p["ok"])
        self.assertIn("Testbereich", p["text"])

    def test_seiten(self):
        html = self.client.get("/einstellungen/startklar").get_data(as_text=True)
        self.assertIn("Für den Start", html)
        self.assertIn("Firmendaten für Rechnungen", html)
        self.assertIn("Startklar", self.client.get("/einstellungen").get_data(as_text=True))
        self.assertIn("bis zum Start", self.client.get("/freigaben").get_data(as_text=True))
        self.assertIn("osPush", self.client.get("/profil").get_data(as_text=True))
        self.als("hamza")
        self.assertEqual(self.client.get("/einstellungen/startklar").status_code, 403)
        self.assertNotIn("bis zum Start", self.client.get("/aufgaben").get_data(as_text=True))


class GoogleDateiTest(WebBasis):
    WEB = {"web": {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-geheim",
                   "redirect_uris": ["https://anders.example/zurueck"]}}

    def test_datei_pruefen(self):
        import json
        from app import google_kalender as gk
        cid, secret, uris = gk.client_aus_datei(json.dumps(self.WEB).encode())
        self.assertEqual((cid, secret), ("123-abc.apps.googleusercontent.com", "GOCSPX-geheim"))
        for falsch in (b"", b"kein json", json.dumps({"type": "service_account"}).encode(),
                       json.dumps({"installed": self.WEB["web"]}).encode(),
                       json.dumps({"web": {"client_id": "x", "client_secret": ""}}).encode()):
            with self.assertRaises(gk.GoogleError):
                gk.client_aus_datei(falsch)

    def test_hochladen(self):
        import io
        import json
        from app import config_store, kalender
        alt = {k: os.environ.get(k) for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")}

        def zurueck():
            config_store.save(main.SETTINGS["DATA_DIR"], {}, ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"])
            for k, v in alt.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.addCleanup(zurueck)
        r = self.post("/einstellungen/kalender/google-datei", {"datei": (io.BytesIO(json.dumps(self.WEB).encode()),
                                                                        "client.json")},
                      content_type="multipart/form-data", headers={"X-Requested-With": "fetch"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertIn("siehe Hinweis", r.get_json()["message"])  # Adresse fehlt im Google-Projekt
        self.addCleanup(lambda: (self.c.execute("DELETE FROM kv WHERE key='google_rueckwege'"), self.c.commit()))
        self.assertIn("Autorisierte Weiterleitungs-URIs", self.client.get("/einstellungen/kalender").get_data(as_text=True))
        self.assertEqual(kalender.google_cfg(), ("123-abc.apps.googleusercontent.com", "GOCSPX-geheim"))
        gespeichert = config_store.load(main.SETTINGS["DATA_DIR"])
        self.assertEqual(gespeichert["GOOGLE_CLIENT_SECRET"], "GOCSPX-geheim")
        self.assertNotIn("GOCSPX-geheim", (Path(main.SETTINGS["DATA_DIR"]) / config_store.STORE).read_text())
        r = self.post("/einstellungen/kalender/google-datei", {"datei": (io.BytesIO(b'{"installed": {}}'), "x.json")},
                      content_type="multipart/form-data", headers={"X-Requested-With": "fetch"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Desktop-App", r.get_json()["error"])
        self.assertIn("Datei hochladen", self.client.get("/einstellungen/kalender").get_data(as_text=True)
                      .replace("Datei ersetzen", "Datei hochladen"))
        self.als("hamza")
        self.assertEqual(self.post("/einstellungen/kalender/google-datei", {}).status_code, 403)


class SerienFlexibelTest(unittest.TestCase):
    """Serien mit Rhythmus und automatischem Verschieben (Weekly Check-in)."""
    setUp = KalenderTest.setUp
    verbinden = KalenderTest.verbinden

    def _naechster(self, wochentag):
        heute = date.today()
        return heute + timedelta(days=(wochentag - heute.weekday()) % 7 or 7)

    def test_rhythmen(self):
        k = self.k
        anker = date(2026, 11, 1)  # ein Sonntag
        s = {"wochentag": 6, "start_datum": anker.isoformat()}
        bis = anker + timedelta(days=63)
        self.assertEqual(len(k.serien_tage(dict(s, rhythmus="taeglich"), anker, anker + timedelta(days=6))), 7)
        self.assertEqual(len(k.serien_tage(dict(s, rhythmus="werktags"), anker, anker + timedelta(days=6))), 5)
        woche = k.serien_tage(dict(s, rhythmus="woche"), anker, bis)
        self.assertTrue(all(d.weekday() == 6 for d in woche))
        zwei = k.serien_tage(dict(s, rhythmus="zweiwochen"), anker, bis)
        self.assertEqual(zwei[1] - zwei[0], timedelta(days=14))
        monat = k.serien_tage(dict(s, rhythmus="monat"), anker, bis)
        self.assertEqual([d.isoformat() for d in monat], ["2026-11-01", "2026-12-06", "2027-01-03"])  # erster Sonntag
        self.assertEqual(k._rhythmus_text(dict(s, rhythmus="monat")), "Jeden 1. Sonntag im Monat")

    def test_verschiebt_wenn_jemand_keine_zeit_hat(self):
        k, c = self.k, self.c
        so = self._naechster(6)
        c.execute("INSERT INTO belegt (person, start, ende) VALUES ('finn', ?, ?)",
                  (f"{so}T17:45", f"{so}T19:00"))
        c.commit()
        sid = k.serie_anlegen(c, "oskar", "Weekly Check-in", 6, "18:00", 60, "oskar", ["finn", "hamza"],
                              flexibel=True, spielraum=120)
        t = c.execute("SELECT * FROM termine WHERE serie_id=? AND substr(start,1,10)=?", (sid, so.isoformat())).fetchone()
        self.assertEqual((t["start"][11:], t["wunsch_start"][11:]), ("19:00", "18:00"))  # später zuerst
        meldungen = [(r["title"], r["body"]) for r in leaddb.notices(c, "f", 20, "hamza")[0]]
        self.assertTrue(any(ti == "Verschoben: Weekly Check-in" and "weil Finn" in b for ti, b in meldungen), meldungen)
        andere = c.execute("SELECT start FROM termine WHERE serie_id=? AND substr(start,1,10)!=?",
                           (sid, so.isoformat())).fetchall()
        self.assertTrue(andere and all(r["start"][11:] == "18:00" for r in andere))  # sonst zur Wunschzeit
        self.assertEqual(k.serie(c, sid)["rhythmus_text"], "Sonntags")

        # Finn wird frei: zurück zur Wunschzeit, mit Meldung
        c.execute("DELETE FROM belegt")
        c.commit()
        self.assertGreaterEqual(k.serien_pruefen(c), 1)
        t = k.get(c, t["id"])
        self.assertEqual(t["start"][11:], "18:00")
        self.assertIn("wieder zur gewohnten Zeit", leaddb.notices(c, "f", 5, "finn")[0][0]["body"])

        # Hamza ist um 18 Uhr bei einem anderen OS-Termin: rückt wieder
        k.anlegen(c, "hamza", "Kundentermin", f"{so}T18:00", dauer=60)
        k.serien_pruefen(c)
        self.assertEqual(k.get(c, t["id"])["start"][11:], "19:00")

        # Finn hat den ganzen Tag keine Zeit: der Call bleibt um 19 Uhr, ohne Finn
        c.execute("INSERT INTO belegt (person, start, ende) VALUES ('finn', ?, ?)", (f"{so}T07:00", f"{so}T22:00"))
        c.commit()
        k.serien_pruefen(c)
        t2 = k.get(c, t["id"])
        self.assertEqual((t2["start"][11:], t2["abwesend"], t2["dabei"]), ("19:00", ["finn"], ["oskar", "hamza"]))
        self.assertEqual(leaddb.notices(c, "f", 5, "finn")[0][0]["title"], "Ohne dich: Weekly Check-in")
        self.assertIn("Ohne Finn", leaddb.notices(c, "f", 5, "oskar")[0][0]["title"])
        self.assertEqual(k.serien_pruefen(c), 0)  # nichts Neues: keine weiteren Meldungen
        # Finn wird wieder frei: wieder dabei, und zurück zur Wunschzeit, wenn Hamza dann kann
        c.execute("DELETE FROM belegt")
        c.commit()
        k.serien_pruefen(c)
        t3 = k.get(c, t["id"])
        self.assertEqual((t3["abwesend"], t3["start"][11:]), ([], "19:00"))  # Hamza hat um 18 Uhr noch den Kundentermin
        self.assertEqual(leaddb.notices(c, "f", 5, "finn")[0][0]["title"], "Wieder dabei: Weekly Check-in")

    def test_eigener_google_eintrag_zaehlt_nicht(self):
        k, c = self.k, self.c
        so = self._naechster(6)
        self.verbinden("finn")
        sid = k.serie_anlegen(c, "oskar", "Check-in", 6, "18:00", 60, "oskar", ["finn"], flexibel=True)
        t = c.execute("SELECT * FROM termine WHERE serie_id=? AND substr(start,1,10)=?", (sid, so.isoformat())).fetchone()
        eid = c.execute("SELECT google_event_id FROM termin_teilnehmer WHERE termin_id=? AND person='finn'",
                        (t["id"],)).fetchone()[0]
        lokal = lambda h: f"{so}T{h}:00"  # Test-Zeitzone egal: nur die Zuordnung zählt
        self.fake.busy = [{"id": eid, "start": t["start"] + ":00Z", "end": t["ende"] + ":00Z"},
                          {"start": lokal("18") + "Z", "end": lokal("19") + "Z"}]
        k.belegt_abgleichen(c, "finn")
        self.assertEqual(c.execute("SELECT COUNT(*) FROM belegt WHERE person='finn'").fetchone()[0], 1)  # ohne eigenen
        self.assertEqual(k.besetzt(c, ["finn"], t["start"], t["start"], k.get(c, t["id"])), [])

    def test_taeglich_ohne_meldungsflut(self):
        k, c = self.k, self.c
        vorher = len(leaddb.notices(c, "f", 200, "finn")[0])
        sid = k.serie_anlegen(c, "oskar", "Daily", 0, "09:00", 15, "oskar", ["finn"], rhythmus="taeglich")
        n = c.execute("SELECT COUNT(*) FROM termine WHERE serie_id=?", (sid,)).fetchone()[0]
        self.assertGreaterEqual(n, 13)  # heute schon vorbei: ein Termin weniger
        self.assertEqual(len(leaddb.notices(c, "f", 200, "finn")[0]) - vorher, 1)  # nur „Neue Serie“
        with self.assertRaises(k.KalenderError):
            k.serie_anlegen(c, "oskar", "X", 0, "09:00", 15, "oskar", [], rhythmus="jahr")


class AlleKalenderTest(unittest.TestCase):
    """Alle Google-Kalender lesen (Schule, Sport, Arbeit), Termine in den gewählten eintragen."""
    setUp = KalenderTest.setUp
    verbinden = KalenderTest.verbinden

    def test_liest_alle_und_schreibt_in_gewaehlten(self):
        k, c = self.k, self.c
        self.fake.listen += [
            {"id": "schule@group.calendar.google.com", "summary": "Schule", "accessRole": "owner"},
            {"id": "sport@group.calendar.google.com", "summary": "Sport", "accessRole": "owner"},
            {"id": "de.german#holiday@group.v.calendar.google.com", "summary": "Feiertage", "accessRole": "reader"}]
        self.fake.busy_je = {
            "schule@group.calendar.google.com": [{"start": "2030-01-07T07:00:00Z", "end": "2030-01-07T12:00:00Z"}],
            "sport@group.calendar.google.com": [{"start": "2030-01-07T17:00:00Z", "end": "2030-01-07T18:30:00Z"}],
            "de.german#holiday@group.v.calendar.google.com": [{"start": "2030-01-07T00:00:00Z",
                                                                "end": "2030-01-08T00:00:00Z"}]}
        self.verbinden("oskar")
        liste = k.kalender_laden(c, "oskar")
        self.assertEqual([x["name"] for x in liste], ["Ich", "Schule", "Sport", "Feiertage"])  # eigene zuerst
        self.assertEqual({x["name"]: x["lesen"] for x in liste},
                         {"Ich": True, "Schule": True, "Sport": True, "Feiertage": False})
        self.assertTrue(k.belegt_abgleichen(c, "oskar"))
        zeiten = sorted(r["start"][11:] for r in c.execute("SELECT start FROM belegt WHERE person='oskar'"))
        self.assertEqual(zeiten, ["08:00", "18:00"])  # Schule und Sport, Feiertag nicht (Winterzeit +1)

        # Sport soll nicht zählen, neue Termine in den eigenen Arbeitskalender
        self.fake.listen.append({"id": "arbeit@group.calendar.google.com", "summary": "Arbeit", "accessRole": "writer"})
        k.kalender_laden(c, "oskar")
        k.kalender_wahl_speichern(c, "oskar", "arbeit@group.calendar.google.com",
                                  ["ich@example.com", "schule@group.calendar.google.com",
                                   "arbeit@group.calendar.google.com"])
        k.belegt_abgleichen(c, "oskar")
        self.assertEqual([r["start"][11:] for r in c.execute("SELECT start FROM belegt WHERE person='oskar'")], ["08:00"])
        tid = k.anlegen(c, "oskar", "Check-in", self.morgen10, dauer=30)
        post = self.fake.anfragen("POST", "/events")[-1]
        self.assertIn("/calendars/arbeit%40group.calendar.google.com/events", post[1])
        x = c.execute("SELECT google_kalender_id FROM termin_teilnehmer WHERE termin_id=?", (tid,)).fetchone()
        self.assertEqual(x[0], "arbeit@group.calendar.google.com")
        # Ändern und Absagen gehen in denselben Kalender, auch wenn die Wahl inzwischen anders ist
        k.kalender_wahl_speichern(c, "oskar", "primary", ["ich@example.com"])
        k.absagen(c, "oskar", tid)
        self.assertIn("/calendars/arbeit%40group.calendar.google.com/events/", self.fake.anfragen("DELETE", "/events/")[-1][1])
        with self.assertRaises(k.KalenderError):  # nur lesbare Kalender sind kein Ziel
            k.kalender_wahl_speichern(c, "oskar", "de.german#holiday@group.v.calendar.google.com", [])

    def test_ohne_recht_fuer_die_liste_nur_hauptkalender(self):
        k, c = self.k, self.c
        self.fake.ohne_liste = True
        self.fake.busy = [{"start": "2030-01-07T09:00:00Z", "end": "2030-01-07T10:00:00Z"}]
        self.verbinden("oskar")
        self.assertIsNone(k.kalender_laden(c, "oskar"))
        self.assertTrue(k.belegt_abgleichen(c, "oskar"))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM belegt WHERE person='oskar'").fetchone()[0], 1)
        self.assertEqual(k.ziel_kalender(c, "oskar"), "primary")


class AlleKalenderWebTest(WebBasis):
    def test_mein_kalender_wahl(self):
        from unittest import mock
        from app import google_kalender, kalender
        fake = FakeGoogle()
        fake.listen.append({"id": "sport@group.calendar.google.com", "summary": "Sport", "accessRole": "owner"})
        for p in (mock.patch.object(google_kalender, "_http", fake),
                  mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "sec"})):
            p.start()
            self.addCleanup(p.stop)
        kalender.konto_speichern(self.c, "oskar", "r-oskar")
        self.addCleanup(lambda: (self.c.execute("DELETE FROM kalender_konten WHERE person='oskar'"), self.c.commit()))
        html = self.client.get("/kalender/mein").get_data(as_text=True)
        self.assertIn("Neue Termine eintragen in", html)
        self.assertIn("Sport", html)
        r = self.post("/kalender/mein/kalender", {"ziel": "sport@group.calendar.google.com", "lesen": ["ich@example.com"]},
                      headers={"X-Requested-With": "fetch"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(kalender.ziel_kalender(self.c, "oskar"), "sport@group.calendar.google.com")
        self.assertEqual(self.post("/kalender/mein/kalender/laden", {}, headers={"X-Requested-With": "fetch"})
                         .status_code, 200)


class OhneDichTest(unittest.TestCase):
    """Wer an einem Serientag keine Zeit hat, ist nicht dabei und bekommt danach die Zusammenfassung."""
    setUp = KalenderTest.setUp
    verbinden = KalenderTest.verbinden

    def test_ganzer_tag_belegt_dann_zusammenfassung(self):
        k, c = self.k, self.c
        heute = date.today()
        so = heute + timedelta(days=(6 - heute.weekday()) % 7 or 7)
        c.execute("INSERT INTO belegt (person, start, ende) VALUES ('finn', ?, ?)", (f"{so}T06:00", f"{so}T23:00"))
        c.commit()
        self.verbinden("finn")
        sid = k.serie_anlegen(c, "oskar", "Weekly Check-in", 6, "18:00", 60, "oskar", ["finn", "hamza"],
                              flexibel=True, spielraum=120)
        t = k.get(c, c.execute("SELECT id FROM termine WHERE serie_id=? AND substr(start,1,10)=?",
                               (sid, so.isoformat())).fetchone()[0])
        self.assertEqual((t["start"][11:], t["abwesend"]), ("18:00", ["finn"]))  # zur Wunschzeit, ohne Finn
        self.assertFalse(any(x["google_event_id"] for x in t["teilnehmer"] if x["person"] == "finn"))  # nicht im Kalender
        self.assertIn("Ohne dich", leaddb.notices(c, "f", 5, "finn")[0][0]["title"])
        # Finns anderer Termin am selben Tag ist nicht durch den Call blockiert
        self.assertEqual(k.besetzt(c, ["finn"], f"{so}T18:00", f"{so}T19:00", {"id": 0}), ["finn"])  # nur Google
        c.execute("DELETE FROM belegt WHERE person='finn'")
        c.commit()
        self.assertEqual(k.besetzt(c, ["finn"], f"{so}T18:00", f"{so}T19:00", {"id": 0}), [])
        # Erinnerungen nur an die, die dabei sind
        self.assertNotIn("finn", t["dabei"])
        # Nach dem Call: Oskar schreibt die Zusammenfassung, Finn bekommt sie
        self.assertTrue(k.zusammenfassung_speichern(c, "oskar", t["id"], "Entschieden: Angebot für Hansen bis Freitag."))
        n = leaddb.notices(c, "f", 5, "finn")[0][0]
        self.assertEqual(n["title"], "Zusammenfassung: Weekly Check-in")
        self.assertIn("Angebot für Hansen", n["body"])
        self.assertFalse(k.zusammenfassung_speichern(c, "oskar", t["id"], "Ergänzt."))
        self.assertEqual(leaddb.notices(c, "f", 5, "finn")[0][0]["title"], "Zusammenfassung geändert: Weekly Check-in")
        with self.assertRaises(k.KalenderError):
            k.zusammenfassung_speichern(c, "oskar", t["id"], "  ")


class ZusammenfassungWebTest(WebBasis):
    def test_seite_und_rechte(self):
        from app import kalender
        gestern = (date.today() - timedelta(days=1)).isoformat() + "T10:00"
        tid = kalender.anlegen(self.c, "oskar", "Weekly Check-in", gestern, dauer=60, personen=["finn", "hamza"])
        self.addCleanup(lambda: (self.c.execute("DELETE FROM termine WHERE id=?", (tid,)),
                                 self.c.execute("DELETE FROM termin_teilnehmer WHERE termin_id=?", (tid,)), self.c.commit()))
        kalender.abwesend_setzen(self.c, tid, ["finn", "hamza"])
        html = self.client.get(f"/kalender/{tid}").get_data(as_text=True)
        self.assertIn("Nicht dabei", html)
        self.assertIn("Speichern und schicken", html)
        r = self.post(f"/kalender/{tid}/zusammenfassung", {"text": "Alles besprochen."},
                      headers={"X-Requested-With": "fetch"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.als("hamza")
        html = self.client.get(f"/kalender/{tid}").get_data(as_text=True)
        self.assertIn("Alles besprochen.", html)
        self.assertEqual(self.post(f"/kalender/{tid}/zusammenfassung", {"text": "x"}).status_code, 403)  # war nicht dabei


# ---------------------------------------------------------------- Stufe 11: Outreach für Kunden (Arbeitsbereiche)

def kunde_anlegen(c, name="Kunde A", postfach=True, **werte):
    """Arbeitsbereich für einen Kunden, mit Akquise-Postfach (vertrieb@kunde-a.de) und Werten aus FELDER."""
    from app import arbeitsbereiche, posteingang
    key = arbeitsbereiche.anlegen(c, "oskar", name)
    pid = None
    if postfach:
        pid = posteingang.postfach_speichern(c, "oskar", None, {
            "name": f"Vertrieb {name}", "adresse": f"vertrieb@{key}.de", "passwort": "geheim",
            "imap_host": "imap.kunde.de", "smtp_host": "smtp.kunde.de", "aktiv": "1"})
    form = {"name": name, "aktiv": "1", "postfach_id": str(pid or ""), **werte}
    arbeitsbereiche.speichern(c, "oskar", key, form)
    return key


class ArbeitsbereicheTest(unittest.TestCase):
    def setUp(self):
        from app import arbeitsbereiche, known
        self.ab, self.known = arbeitsbereiche, known
        self.c = frische_db()
        self.addCleanup(arbeitsbereiche.cache_leeren)
        self.kunde = kunde_anlegen(self.c, "Kunde A", LEADS_SENDER_NAME="Maria Muster", DEFAULT_FOOTER="Kunde A GmbH, Hamburg")
        self.ylva_lead = lead_anlegen(self.c, "Tischlerei Ylva", email="info@tischlerei-ylva.de", stage="neu")
        with arbeitsbereiche.im(self.kunde):
            self.kunden_lead = lead_anlegen(self.c, "Bäckerei Kunde", email="info@baeckerei-kunde.de", stage="neu")

    def test_neue_zeilen_gehoeren_zum_aktiven_arbeitsbereich(self):
        ab = dict(self.c.execute("SELECT id, arbeitsbereich FROM leads").fetchall())
        self.assertEqual(ab[self.ylva_lead], "ylva")
        self.assertEqual(ab[self.kunden_lead], self.kunde)
        with self.ab.im(self.kunde):
            leaddb.add_signal(self.c, "gut", "passt gut", self.kunden_lead)
            leaddb.save_brain(self.c, "# Gehirn Kunde A\n", "Oskar", "Start")
        self.assertEqual(self.c.execute("SELECT arbeitsbereich FROM signals").fetchone()[0], self.kunde)
        self.assertEqual(self.c.execute("SELECT arbeitsbereich FROM brain_versions").fetchone()[0], self.kunde)

    def test_lead_aus_a_ist_in_b_nie_sichtbar(self):
        from app import mailbox
        mailbox.add(self.c, self.kunden_lead, "in", "Klingt gut", subject="Re: Hallo", from_addr="info@baeckerei-kunde.de",
                    read=False)
        mailbox.add(self.c, self.ylva_lead, "in", "Gern", subject="Re: Hallo", from_addr="info@tischlerei-ylva.de",
                    read=False)
        for ab, eigen, fremd in (("ylva", self.ylva_lead, self.kunden_lead), (self.kunde, self.kunden_lead, self.ylva_lead)):
            with self.ab.im(ab):
                self.assertIsNotNone(leaddb.get_lead(self.c, eigen))
                self.assertIsNone(leaddb.get_lead(self.c, fremd))
                self.assertIsNotNone(leaddb.get_lead(self.c, fremd, alle=True))  # Firmen-OS darf alle
                ids = [l["id"] for col in crm.board(self.c) for l in col[3]]
                self.assertEqual(ids, [eigen])
                self.assertEqual([x["id"] for x in mailbox.conversations(self.c)], [eigen])
                self.assertEqual(mailbox.unread_count(self.c), 1)
                self.assertEqual(sum(1 for _ in crm.export_csv(self.c, everything=True).splitlines()) - 1, 1)
                self.assertEqual(leaddb.tab_counts(self.c)["passt"], 1)

    def test_derselbe_betrieb_darf_in_zwei_arbeitsbereichen_sein(self):
        daten = {"name": "Malerei Blau", "city": "Kiel", "email": "info@malerei-blau.de", "website": "malerei-blau.de"}
        a = crm.create_lead(self.c, dict(daten), "Oskar")
        with self.ab.im(self.kunde):
            self.assertIsNone(self.known.find(self.c, daten["name"], daten["city"], email=daten["email"]))
            b = crm.create_lead(self.c, dict(daten), "Oskar")
        self.assertNotEqual(a, b)
        sids = [r[0] for r in self.c.execute("SELECT source_id FROM leads WHERE name='Malerei Blau' ORDER BY id")]
        self.assertTrue(sids[1].endswith(f"@ab:{self.kunde}"))
        self.assertEqual(self.known.source_of(sids[1]), "hand")

    def test_gespeicherte_werte_und_gehirn_je_arbeitsbereich(self):
        leaddb.kv_set(self.c, "blocklist", ["konkurrenz.de"])
        with self.ab.im(self.kunde):
            self.assertEqual(leaddb.kv_get(self.c, "blocklist", []), [])
            self.assertIn("Gehirn für einen Kunden", leaddb.brain(self.c))
            self.assertNotIn("Ylva Labs ist", leaddb.brain(self.c))
        self.assertEqual(leaddb.kv_get(self.c, "blocklist"), ["konkurrenz.de"])
        self.assertIn("Ylva Labs", leaddb.brain(self.c))

    def test_einstellungen_des_kunden(self):
        from app import agent
        with self.ab.im(self.kunde):
            self.assertEqual(agent.S("OUTREACH_USER"), "vertrieb@kunde-a.de")
            self.assertEqual(agent.S("OUTREACH_PASSWORD"), "geheim")
            self.assertEqual(agent.S("SMTP_HOST"), "smtp.kunde.de")
            self.assertEqual(agent.S("ORG_NAME"), "Kunde A")
            self.assertEqual(agent.S("LEADS_SENDER_NAME"), "Maria Muster")
            self.assertFalse(agent.S("OUTREACH_EMAIL"))  # Standard aus, bis der Kunde es bestätigt
            self.assertEqual(agent.S("LEADS_FOLLOWUP_DAYS"), agent.CFG["settings"]["LEADS_FOLLOWUP_DAYS"])  # Rückfall
        ohne = kunde_anlegen(self.c, "Kunde ohne Postfach", postfach=False)
        with self.ab.im(ohne):
            self.assertEqual(agent.S("OUTREACH_USER"), "")  # nie das Postfach von Ylva Labs
            self.assertFalse(agent.outreach_configured())

    def test_versand_aus_dem_postfach_des_kunden(self):
        from unittest import mock
        from app import agent, posteingang
        gesendet = []
        smtp = mock.Mock(send_message=gesendet.append)
        self.ab.speichern(self.c, "oskar", self.kunde, {
            "name": "Kunde A", "aktiv": "1", "postfach_id": str(self.ab.get(self.c, self.kunde)["postfach_id"]),
            "LEADS_SENDER_NAME": "Maria Muster", "DEFAULT_FOOTER": "Kunde A GmbH, Hamburg", "OUTREACH_EMAIL": "1"})
        with self.ab.im(self.kunde), mock.patch.object(posteingang, "SMTP", lambda p: smtp), \
                mock.patch.object(posteingang, "IMAP", side_effect=Exception("kein IMAP")), \
                mock.patch.dict(agent.CFG, {"smtp_connect": mock.Mock(side_effect=AssertionError("Ylva-Postfach"))}):
            leaddb.update_lead(self.c, self.kunden_lead, status="entwurf", subject="Hallo", greeting="Guten Tag,",
                               body="Kurze Frage.", owner="oskar")
            agent.send_outreach(self.c, leaddb.get_lead(self.c, self.kunden_lead), "Oskar")
        self.assertEqual(len(gesendet), 1)
        msg = gesendet[0]
        self.assertIn("vertrieb@kunde-a.de", msg["From"])
        self.assertNotIn("Ylva", msg["From"])
        self.assertNotIn("Oskar", msg.as_string())
        self.assertIn("Maria Muster", msg.as_string())

    def test_ohne_erlaubnis_kein_erstkontakt_per_mail(self):
        from app import agent
        with self.ab.im(self.kunde):
            leaddb.update_lead(self.c, self.kunden_lead, status="entwurf", subject="Hallo", body="x")
            with self.assertRaises(RuntimeError):
                agent.send_outreach(self.c, leaddb.get_lead(self.c, self.kunden_lead), "Oskar")

    def test_zugriff_je_person(self):
        self.assertTrue(self.ab.darf(self.c, "finn", self.kunde))  # leere Liste: alle mit Leads oder CRM
        self.ab.speichern(self.c, "oskar", self.kunde, {"name": "Kunde A", "aktiv": "1", "personen": ["finn"]})
        self.assertTrue(self.ab.darf(self.c, "finn", self.kunde))
        self.assertFalse(self.ab.darf(self.c, "hamza", self.kunde))
        self.assertTrue(self.ab.darf(self.c, "oskar", self.kunde))  # verwaltet Personen: sieht alle
        self.assertEqual([a["key"] for a in self.ab.fuer_person(self.c, "hamza")], ["ylva"])

    def test_akquise_postfach_des_kunden_nicht_im_posteingang(self):
        from app import posteingang
        pid = self.ab.get(self.c, self.kunde)["postfach_id"]
        self.assertNotIn(pid, [p["id"] for p in posteingang.fuer_person(self.c, "oskar")])
        with self.ab.im(self.kunde):
            self.assertEqual([p["id"] for p in posteingang.fuer_person(self.c, "oskar")], [pid])
        abgerufen = []
        from unittest import mock
        with mock.patch.object(posteingang, "abrufen", lambda c, p: abgerufen.append(p) or 0):
            posteingang.takt(self.c)
        self.assertNotIn(pid, abgerufen)

    def test_freigaben_nennen_den_kunden(self):
        from app import freigaben
        with self.ab.im(self.kunde):
            leaddb.update_lead(self.c, self.kunden_lead, status="entwurf", owner="oskar")
        offen = [f for f in freigaben._entwuerfe_offen(self.c, "oskar") if f["oid"] == self.kunden_lead]
        self.assertEqual(len(offen), 1)
        self.assertIn("Kunde A", offen[0]["eyebrow"])


class ArbeitsbereicheWebTest(WebBasis):
    def setUp(self):
        super().setUp()
        from app import arbeitsbereiche
        self.ab = arbeitsbereiche
        self.kunde = kunde_anlegen(self.c, "Kunde Web")
        with arbeitsbereiche.im(self.kunde):
            self.lid = lead_anlegen(self.c, "Gärtnerei Kunde Web", stage="neu")

        def aufraeumen():
            self.c.execute("DELETE FROM leads WHERE arbeitsbereich=?", (self.kunde,))
            self.c.execute("DELETE FROM postfaecher WHERE id=(SELECT postfach_id FROM arbeitsbereiche WHERE key=?)",
                           (self.kunde,))
            self.c.execute("DELETE FROM arbeitsbereiche WHERE key=?", (self.kunde,))
            self.c.commit()
            arbeitsbereiche.cache_leeren()
        self.addCleanup(aufraeumen)

    def test_auswahl_und_wechsel(self):
        html = self.client.get("/crm").get_data(as_text=True)
        self.assertIn("ab-pick", html)
        self.assertIn("Kunde Web", html)
        self.assertNotIn("Gärtnerei Kunde Web", self.client.get("/crm?wer=alle").get_data(as_text=True))
        r = self.post("/arbeitsbereich", {"ab": self.kunde, "next": "/crm?wer=alle"})
        self.assertEqual(r.headers["Location"], "/crm?wer=alle")
        self.assertIn("Gärtnerei Kunde Web", self.client.get("/crm?wer=alle").get_data(as_text=True))
        self.post("/arbeitsbereich", {"ab": "ylva", "next": f"/leads/{self.lid}"})
        self.assertEqual(self.client.get("/crm").status_code, 200)

    def test_link_auf_lead_wechselt_den_arbeitsbereich(self):
        r = self.client.get(f"/leads/{self.lid}")
        self.assertEqual(r.status_code, 200)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess["ab"], self.kunde)

    def test_ohne_zugang_kein_lead_und_kein_wechsel(self):
        self.ab.speichern(self.c, "oskar", self.kunde, {"name": "Kunde Web", "aktiv": "1", "personen": ["finn"]})
        self.als("hamza")
        self.assertEqual(self.client.get(f"/leads/{self.lid}").status_code, 404)
        self.assertEqual(self.post("/arbeitsbereich", {"ab": self.kunde}).status_code, 403)
        self.assertEqual(self.post(f"/crm/{self.lid}/stufe", {"stage": "kunde"}).status_code, 404)

    def test_einstellungen_seite(self):
        self.assertIn("Kunde Web", self.client.get("/einstellungen/arbeitsbereiche").get_data(as_text=True))
        html = self.client.get(f"/einstellungen/arbeitsbereiche/{self.kunde}").get_data(as_text=True)
        self.assertIn("Akquise-Postfach des Kunden", html)
        r = self.post(f"/einstellungen/arbeitsbereiche/{self.kunde}", {"name": "Kunde Web", "aktiv": "1",
                                                                         "LEADS_FOLLOWUP_DAYS": "abc"},
                      headers={"X-Requested-With": "fetch"})
        self.assertEqual(r.status_code, 400)


class OutreachKundeTest(unittest.TestCase):
    """Stufe 11, Schritt 5 und 6: Ergebnisse im Kunden-Bereich und auf der Monatsrechnung."""

    def setUp(self):
        from app import arbeitsbereiche
        self.ab = arbeitsbereiche
        self.c = frische_db()
        self.addCleanup(arbeitsbereiche.cache_leeren)
        self.pid = projekt_anlegen(self.c, "Hansen Outreach", "kunde@hansen-outreach.de")
        self.kunde = kunde_anlegen(self.c, "Hansen", projekt_id=str(self.pid), PAUSCHALE_EUR="490",
                                   KI_WEITERBERECHNEN="1")
        vormonat = (date.today().replace(day=1) - timedelta(days=3)).isoformat() + "T10:00:00"
        with arbeitsbereiche.im(self.kunde):
            self.a = lead_anlegen(self.c, "Interessent A", stage="gespraech", interest="qualified", email="a@a.de",
                                  sent_at=vormonat, replied_at=vormonat, contact_name="Herr A", fit_reason="intern!")
            lead_anlegen(self.c, "Ohne Antwort B", stage="kontaktiert", sent_at=vormonat)
            leaddb.record_usage(self.c, "entwurf", "m", {"input": 1, "output": 1, "cache_write": 0, "cache_read": 0},
                                1.0)
            leaddb.kv_set(self.c, "x", 1)
        self.c.execute("UPDATE ai_usage SET at=?", (vormonat,))
        self.c.commit()

    def test_zahlen_und_kontakte_ohne_interna(self):
        o = self.ab.outreach_zahlen(self.c, self.kunde)
        self.assertEqual(o["gesamt"]["angeschrieben"], 2)
        self.assertEqual(o["gesamt"]["antworten"], 1)
        self.assertEqual(o["gesamt"]["interesse"], 1)
        self.assertEqual([k["name"] for k in o["kontakte"]], ["Interessent A"])
        self.assertNotIn("intern!", str(o))
        self.assertEqual(self.ab.outreach_zahlen(self.c, "ylva")["gesamt"]["angeschrieben"], 0)

    def test_kunden_bereich_zeigt_outreach(self):
        from app import portal
        z = {"projekt_id": self.pid, "email": "kunde@hansen-outreach.de", "name": "Frau Hansen"}
        a = portal.ansicht(self.c, z)
        self.assertEqual(a["outreach"]["gesamt"]["interesse"], 1)
        portal.regeln_speichern(self.c, "oskar", {**portal.regeln(self.c), "zeige_outreach": ""})
        self.assertIsNone(portal.ansicht(self.c, z)["outreach"])

    def test_monatsrechnung_mit_outreach_und_ki(self):
        from app import finanzen
        rid = finanzen.monat_anlegen(self.c, "oskar", self.pid)
        texte = {p["text"]: p["betrag"] for p in finanzen.get(self.c, rid)["positionen"]}
        outreach = [t for t in texte if t.startswith("Outreach")]
        self.assertEqual(len(outreach), 1)
        self.assertIn("2 Betriebe angeschrieben", outreach[0])
        self.assertEqual(texte[outreach[0]], 490)
        ki = [t for t in texte if t.startswith("KI-Kosten")]
        self.assertEqual(len(ki), 1)
        self.assertGreater(texte[ki[0]], 0)


class FehlerOrdnerTest(unittest.TestCase):
    """Postfach: Zustellfehler und Abwesenheit im Ordner „Fehler“, Meldungen nur für Angeschriebene."""

    def setUp(self):
        from unittest import mock
        from app import agent
        self.agent = agent
        self.c = frische_db()
        self.lid = lead_anlegen(self.c, "Tischlerei Fehler", email="info@tischlerei-fehler.de", stage="kontaktiert",
                                status="versendet", message_id="<outreach-1@ylvalabs.de>", sent_at=HEUTE)
        self.verschoben = []
        settings = dict(agent.CFG["settings"], OUTREACH_USER="k@ylvalabs.de", OUTREACH_PASSWORD="x", IMAP_HOST="imap.x",
                        FEHLER_ORDNER="Fehler")
        p = mock.patch.dict(agent.CFG, {"settings": settings})
        p.start()
        self.addCleanup(p.stop)

    def _mail(self, frm, subject, text, mid, ctype=None, **headers):
        from email.message import EmailMessage
        from email.utils import format_datetime
        from datetime import datetime
        m = EmailMessage()
        m["From"], m["Subject"], m["Message-ID"] = frm, subject, mid
        m["Date"] = format_datetime(datetime.now().astimezone())
        for k, v in headers.items():
            m[k.replace("_", "-")] = v
        m.set_content(text)
        return m.as_bytes()

    def _abrufen(self, mails):
        from unittest import mock
        test = self

        class Imap(FakeImap):
            def __init__(self):
                super().__init__(mails)
                self.readonly = None

            def select(self, *a, **kw):
                self.readonly = kw.get("readonly")
                return "OK", [b"1"]

            def fetch(self, num, spec):
                raw = mails[int(num) - 1]
                if "HEADER" in spec:
                    import email as em
                    return "OK", [(b"x", f"Message-ID: {em.message_from_bytes(raw)['Message-ID']}\r\n\r\n".encode())]
                return "OK", [(b"x", raw)]

            def create(self, name):
                return "OK", []

            def copy(self, num, name):
                test.verschoben.append((num, name))
                return "OK", []

            def store(self, *a):
                return "OK", []

            def expunge(self):
                return "OK", []
        self.imap = Imap()
        with mock.patch.object(self.agent, "_imap", return_value=self.imap):
            return self.agent.check_inbox(self.c)

    def test_zustellfehler_und_abwesenheit_in_den_ordner_fehler(self):
        from app import mailbox
        bounce = self._mail("Mail Delivery System <MAILER-DAEMON@strato.de>", "Undelivered Mail Returned to Sender",
                            "Die Mail an info@tischlerei-fehler.de konnte nicht zugestellt werden.\n"
                            "Final-Recipient: rfc822; info@tischlerei-fehler.de\n"
                            "Message-ID: <outreach-1@ylvalabs.de>", "<bounce@strato>")
        abwesend = self._mail("info@tischlerei-fehler.de", "Abwesenheitsnotiz: Ein Gedanke", "Ich bin bis Montag nicht "
                              "im Büro.", "<ooo@x>", In_Reply_To="<outreach-1@ylvalabs.de>", Auto_Submitted="auto-replied")
        echt = self._mail("info@tischlerei-fehler.de", "Re: Ein Gedanke", "Klingt gut, rufen Sie an.", "<echt@x>",
                          In_Reply_To="<outreach-1@ylvalabs.de>")
        self.assertEqual(self._abrufen([bounce, abwesend, echt]), 1)  # nur die echte Antwort zählt
        self.assertFalse(self.imap.readonly)
        self.assertEqual([n for n, _ in self.verschoben], [b"1", b"2"])
        self.assertTrue(all(o == '"Fehler"' for _, o in self.verschoben))
        fehler = mailbox.fehler_liste(self.c)
        self.assertEqual(len(fehler), 2)
        self.assertTrue(all(f["lead_id"] == self.lid for f in fehler))
        self.assertEqual(mailbox.ordner_zahlen(self.c)["fehler"], 2)
        self.assertEqual(mailbox.unread_count(self.c), 1)
        self.assertEqual(mailbox.last_incoming(self.c, self.lid)["subject"], "Re: Ein Gedanke")
        titel = [r["title"] for r in self.c.execute("SELECT title FROM notices")]
        self.assertFalse(any("Undelivered" in t or "Abwesen" in t for t in titel))
        lead = leaddb.get_lead(self.c, self.lid)
        self.assertEqual(lead["interest"], "qualified")  # die echte Antwort wurde eingeordnet

    def test_ohne_ordner_bleibt_der_posteingang_unberuehrt(self):
        from unittest import mock
        with mock.patch.dict(self.agent.CFG["settings"], {"FEHLER_ORDNER": ""}):
            self._abrufen([self._mail("MAILER-DAEMON@strato.de", "Undelivered Mail", "x", "<b2@strato>")])
        self.assertTrue(self.imap.readonly)
        self.assertEqual(self.verschoben, [])

    def test_unbekannte_ohne_meldung(self):
        from app import mailbox
        self._abrufen([self._mail("Fremd <fremd@irgendwo.de>", "Frage", "Hallo, kurze Frage.", "<f@x>")])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM notices").fetchone()[0], 0)
        self.assertEqual(len(mailbox.conversations(self.c, ordner="sonstige")), 1)
        self.assertEqual(mailbox.conversations(self.c), [])


class WiedervorlageJeArbeitsbereichTest(unittest.TestCase):
    def test_heute_dran_nur_im_eigenen_arbeitsbereich(self):
        from app import arbeitsbereiche
        c = frische_db()
        self.addCleanup(arbeitsbereiche.cache_leeren)
        kunde = kunde_anlegen(c, "Kunde WV", postfach=False)
        lid = lead_anlegen(c, "Ylva-Lead", stage="neu")
        crm.add_task(c, lid, HEUTE, "Anrufen", "Oskar", person="oskar")
        self.assertEqual(len(crm.open_tasks(c)), 1)
        self.assertEqual(crm.due_count(c), 1)
        with arbeitsbereiche.im(kunde):
            self.assertEqual(crm.open_tasks(c), [])
            self.assertEqual(crm.due_count(c), 0)

# ---------------------------------------------------------------- Stufe 12: Agenten-Schnittstelle (MCP)


def _agent_freigabe(c, agent="outreach", **kw):
    from app import agenten
    werte = dict(art="entscheidung", titel="Welle 3 starten", was="Sollen wir Welle 3 an Agenturen schicken?",
                 optionen=[{"key": "A", "text": "Ja, Montag"}, {"key": "B", "text": "Später"}], empfehlung="A")
    werte.update(kw)
    return agenten.anfragen(c, agent, **werte)["freigabe_ids"]


class AgentenTest(unittest.TestCase):
    def setUp(self):
        self.c = frische_db()
        from app import agent
        self.postfach = PostfachMock(self)
        self.assertTrue(agent.CFG.get("settings"))

    def test_startwerte_und_rechte_nie_ueber_bearbeiten(self):
        from app import agenten
        c = self.c
        self.assertEqual([a["key"] for a in agenten.liste(c)], ["ops", "delivery", "outreach", "content"])
        self.assertEqual(firma.rechte(c, "agent:outreach")["leads"], 2)
        self.assertEqual(firma.name_von(c, "agent:ops"), "Ops (Agent)")
        form = {f"r_{k}": "4" for k in firma.MODUL_KEYS}
        agenten.speichern(c, "oskar", "ops", form)
        self.assertEqual(set(firma.rechte(c, "agent:ops").values()), {2})  # nie Freigeben oder Verwalten
        self.assertFalse(firma.darf(c, "agent:ops", "entscheidungen", firma.FREIGEBEN))
        agenten.status_setzen(c, "oskar", "ops", "gesperrt")
        self.assertEqual(set(firma.rechte(c, "agent:ops").values()), {0})

    def test_agent_kann_nie_freigeben(self):
        from app import agenten, freigaben, mcp_server
        c = self.c
        fid = _agent_freigabe(c)[0]
        for fn in (lambda: agenten.entscheiden(c, "agent:outreach", fid, True, option="A"),
                   lambda: agenten.entscheiden(c, "agent:ops", fid, True, option="A"),
                   lambda: agenten.ausfuehren(c, "agent:ops", fid)):
            with self.assertRaises(agenten.AgentError):
                fn()
        with self.assertRaises(freigaben.FreigabeError):
            freigaben.entscheiden(c, "agent:outreach", "agent", fid, True)
        with self.assertRaises(freigaben.FreigabeError):
            freigaben.entscheiden_festhalten(c, "agent:ops", "Selbst entschieden", "Weil", recht_key="eigener_bereich")
        # andere Arten ebenso (z. B. eine Entscheidung, die auf Zustimmung wartet)
        eid = freigaben.entscheiden_festhalten(c, "hamza", "Neue Nische", "Test", recht_key="gruenderteam")
        f = c.execute("SELECT id FROM freigaben WHERE art='entscheidung' AND objekt_id=?", (str(eid),)).fetchone()
        with self.assertRaises(freigaben.FreigabeError):
            freigaben.entscheiden(c, "agent:ops", "entscheidung", f["id"], True)
        # Es gibt kein Werkzeug zum Freigeben
        namen = {t["name"] for t in mcp_server.tools_liste(c, "agent:ops")}
        self.assertFalse(any(n in namen for n in ("freigeben", "entscheiden", "freigabe_entscheiden")))
        self.assertEqual(c.execute("SELECT status FROM freigaben WHERE id=?", (fid,)).fetchone()[0], "offen")

    def test_freigabe_landet_bei_der_richtigen_person(self):
        from app import agenten
        c = self.c
        hamza_lead = lead_anlegen(c, "Videoagentur Nord", stage="neu", owner="hamza", email="a@nord.de")
        finn_lead = lead_anlegen(c, "Studio Süd", stage="neu", owner="finn", email="b@sued.de")
        # Hamza (Recherche) darf nicht freigeben: es geht den Bereich hinauf zu Oskar, egal was der Agent vorschlägt
        e1 = agenten.entwurf_anlegen(c, "agent:outreach", "mail", "Hallo Nord", lead_id=hamza_lead, betreff="Idee")
        f1 = agenten.anfragen(c, "outreach", "mail", "Erstmail Videoagentur Nord", "Erstkontakt zur Freigabe.",
                              entwurf_id=e1, vorschlag_person="hamza")["freigabe_ids"]
        self.assertEqual(c.execute("SELECT pruefer FROM freigaben WHERE id=?", (f1[0],)).fetchone()[0], "oskar")
        # Finns Lead: Finn entscheidet im eigenen Bereich selbst
        e2 = agenten.entwurf_anlegen(c, "agent:outreach", "mail", "Hallo Süd", lead_id=finn_lead, betreff="Idee")
        f2 = agenten.anfragen(c, "outreach", "mail", "Erstmail Studio Süd", "Erstkontakt zur Freigabe.", entwurf_id=e2)
        self.assertEqual(c.execute("SELECT pruefer FROM freigaben WHERE id=?", (f2["freigabe_ids"][0],)).fetchone()[0],
                         "finn")
        # Kundenversprechen: Markt und Technik müssen beide freigeben; erst danach passiert etwas
        ids = agenten.anfragen(c, "delivery", "kundenversprechen", "Sonderexport für Nord", "Export als CSV zusagen?",
                               lead_id=hamza_lead)["freigabe_ids"]
        pruefer = sorted(r[0] for r in c.execute(f"SELECT pruefer FROM freigaben WHERE id IN ({ids[0]}, {ids[1]})"))
        self.assertEqual(pruefer, ["finn", "oskar"])
        fo = next(i for i in ids if c.execute("SELECT pruefer FROM freigaben WHERE id=?", (i,)).fetchone()[0] == "oskar")
        self.assertEqual(agenten.entscheiden(c, "oskar", fo, True)["status"], "wartet_auf_weitere")
        with self.assertRaises(agenten.AgentError):  # Hamza ist nicht zuständig
            agenten.entscheiden(c, "hamza", next(i for i in ids if i != fo), True)
        self.assertEqual(agenten.entscheiden(c, "finn", next(i for i in ids if i != fo), True)["status"], "entschieden")
        # Grundsatz: Gründerteam, landet im Entscheidungs-Log
        gid = _agent_freigabe(c, "ops", art="grundsatz", titel="Zielnische festlegen")[0]
        erg = agenten.entscheiden(c, "oskar", gid, True, option="A")
        self.assertEqual(erg["status"], "entschieden")
        from app import freigaben
        self.assertIn("Zielnische festlegen: Ja, Montag", freigaben.entscheidung_get(c, erg["entscheidung_id"])["titel"])
        self.assertEqual(freigaben.entscheidung_get(c, erg["entscheidung_id"])["status"], "wartet")  # Finn fehlt noch
        self.assertIn("finn", [f["pruefer"] for f in freigaben.offen(c, "finn") if f["art"] == "entscheidung"])

    def test_ausfuehrung_nur_mit_freigegebenem_inhalt(self):
        """Einstellung „Das OS verschickt“ (MAIL_VERSAND=os): das OS schickt genau die freigegebene Fassung."""
        from app import agenten
        c = self.c
        self.postfach = PostfachMock(self, versand="os")
        lid = lead_anlegen(c, "Cutter Kiel", stage="antwort", owner="oskar", email="info@cutter.de", replied_at=HEUTE)
        eid = agenten.entwurf_anlegen(c, "agent:outreach", "mail", "Danke für die Antwort!", lead_id=lid, betreff="Re")
        fid = agenten.anfragen(c, "outreach", "mail", "Antwort an Cutter Kiel", "Kurze Antwort.", entwurf_id=eid)["freigabe_ids"][0]
        # Agent ändert danach: Freigabe hinfällig, nichts geht raus
        agenten.entwurf_aendern(c, "agent:outreach", eid, text="Anderer Text mit neuem Preis")
        with self.assertRaises(agenten.AgentError):
            agenten.entscheiden(c, "oskar", fid, True)
        self.assertEqual(self.postfach.mails, [])
        # Heimlich in der Datenbank geändert (ohne neuen Hash): das OS rechnet nach und verschickt nichts
        fid = agenten.anfragen(c, "outreach", "mail", "Antwort an Cutter Kiel", "Kurze Antwort.", entwurf_id=eid)["freigabe_ids"][0]
        c.execute("UPDATE agent_entwuerfe SET text='Manipuliert' WHERE id=?", (eid,))
        c.commit()
        with self.assertRaises(agenten.AgentError):
            agenten.entscheiden(c, "oskar", fid, True)
        self.assertEqual(self.postfach.mails, [])
        # Sauber: neu anfragen, Oskar ändert beim Freigeben, genau seine Fassung geht raus
        c.execute("UPDATE agent_entwuerfe SET text='Danke für die Antwort!', status='entwurf' WHERE id=?", (eid,))
        c.commit()
        agenten.entwurf_aendern(c, "agent:outreach", eid, text="Danke für die schnelle Antwort!")
        fid = agenten.anfragen(c, "outreach", "mail", "Antwort an Cutter Kiel", "Kurze Antwort.", entwurf_id=eid)["freigabe_ids"][0]
        erg = agenten.entscheiden(c, "oskar", fid, True, text="Danke für die schnelle Antwort! Passt Dienstag?")
        self.assertEqual(erg["status"], "ausgefuehrt", erg)
        self.assertEqual(len(self.postfach.mails), 1)
        self.assertIn("Passt Dienstag?", self.postfach.text())
        self.assertEqual(str(self.postfach.mails[0]["To"]), "info@cutter.de")
        # Nach der Freigabe lässt sich nichts mehr ändern, und der Agent sieht die geänderte Fassung
        with self.assertRaises(agenten.AgentError):
            agenten.entwurf_aendern(c, "agent:outreach", eid, text="Noch anders")
        st = agenten.status_fuer_agent(c, "outreach")
        eintrag = next(x for x in st["entschieden"] if x["freigabe_id"] == fid)
        self.assertEqual(eintrag["ausfuehrung"], "ausgefuehrt")
        self.assertIn("Passt Dienstag?", eintrag["freigegebene_fassung"]["text"])
        self.assertEqual(agenten.status_fuer_agent(c, "outreach")["entschieden"], [])  # nur Neues
        # Sperrliste gilt immer
        leaddb.kv_set(c, "blocklist", ["info@cutter.de"])
        e2 = agenten.entwurf_anlegen(c, "agent:outreach", "mail", "Noch eine", lead_id=lid, betreff="Re")
        f2 = agenten.anfragen(c, "outreach", "mail", "Zweite Mail Cutter", "Noch eine.", entwurf_id=e2)["freigabe_ids"][0]
        self.assertEqual(agenten.entscheiden(c, "oskar", f2, True)["status"], "fehler")
        self.assertEqual(len(self.postfach.mails), 1)

    def test_ausfuehren_im_arbeitsbereich_des_leads(self):
        """Oskar arbeitet gerade im Arbeitsbereich eines Kunden: die freigegebene Agent-Mail an einen Lead von Ylva Labs
        geht trotzdem über das Postfach von Ylva Labs raus (Stufe 11 + 12)."""
        from app import agenten, arbeitsbereiche
        c = self.c
        self.postfach = PostfachMock(self, versand="os")
        kunde = kunde_anlegen(c, "Kunde Agent")
        lid = lead_anlegen(c, "Studio Ylva", stage="antwort", owner="oskar", email="hallo@studio.de", replied_at=HEUTE)
        eid = agenten.entwurf_anlegen(c, "agent:outreach", "mail", "Danke, passt!", lead_id=lid, betreff="Re: Termin")
        fid = agenten.anfragen(c, "outreach", "mail", "Antwort Studio Ylva", "Kurze Zusage.", entwurf_id=eid)["freigabe_ids"][0]
        with arbeitsbereiche.im(kunde):
            erg = agenten.entscheiden(c, "oskar", fid, True)
        self.assertEqual(erg["status"], "ausgefuehrt", erg)
        self.assertEqual(self.postfach.mails[-1]["From"].addresses[0].addr_spec, "kontakt@ylvalabs.de")

    def test_alles_lesen_und_bearbeiten(self):
        from app import agenten
        c = self.c
        key = agenten.anlegen(c, "oskar", "Claude", "Allgemein", None,
                              rechte_neu={k: 4 for k in firma.MODUL_KEYS})  # mehr als erlaubt: wird gedeckelt
        self.assertEqual(set(firma.rechte(c, f"agent:{key}").values()), {agenten.MAX_STUFE})
        agenten.speichern(c, "oskar", key, {"name": "Claude"})
        self.assertEqual(set(firma.rechte(c, f"agent:{key}").values()), {0})
        agenten.alles_erlauben(c, "oskar", key)
        self.assertEqual(set(firma.rechte(c, f"agent:{key}").values()), {agenten.MAX_STUFE})
        self.assertFalse(firma.darf(c, f"agent:{key}", "entscheidungen", firma.FREIGEBEN))  # freigeben nie

    def test_aufraeumen_nur_nach_freigabe(self):
        from app import agenten, mcp_server, aufraeumen
        c = self.c
        alt = lead_anlegen(c, "Alte Tischlerei", stage="kontaktiert")
        alt2 = lead_anlegen(c, "Altes Autohaus")
        bleibt = lead_anlegen(c, "Neue Filmproduktion", stage="neu")
        rech = lead_anlegen(c, "Kunde mit Rechnung", stage="kunde")
        c.execute("INSERT INTO rechnungen (lead_id, nummer, status, erstellt_am) VALUES (?, 'R-1', 'entwurf', "
                  "'2026-11-01')", (rech,))
        c.commit()
        agenten.alles_erlauben(c, "oskar", "ops")
        aid = aufgaben.anlegen(c, "oskar", "Alte Aufgabe", "markt", HEUTE, person="oskar")
        d, f = mcp_server.aufrufen(c, "agent:ops", "aufraeumen_vorschlagen",
                                   {"behalten_lead_ids": [bleibt], "aufgaben_entfernen": [aid], "nur_ansehen": True})
        self.assertFalse(f, d)
        self.assertIn("Neue Filmproduktion", d["bleiben"])
        self.assertTrue(leaddb.get_lead(c, alt, alle=True))  # nur angesehen: nichts weg
        d, f = mcp_server.aufrufen(c, "agent:ops", "aufraeumen_vorschlagen",
                                   {"behalten_lead_ids": [bleibt], "aufgaben_entfernen": [aid],
                                    "begruendung": "Neustart mit dem Second Brain"})
        self.assertFalse(f, d)
        fid = d["als_freigabe"]["freigabe_ids"][0]
        self.assertTrue(leaddb.get_lead(c, alt, alle=True))  # erst nach dem Ja
        with self.assertRaises(agenten.AgentError):
            agenten.entscheiden(c, "agent:ops", fid, True)  # Agents geben nie frei
        for f_ in c.execute("SELECT id, pruefer FROM freigaben WHERE angefragt_von='agent:ops' AND status='offen' "
                            "AND vorschlag LIKE '%aufraeumen%'").fetchall():
            agenten.entscheiden(c, f_["pruefer"], f_["id"], True)
        self.assertIsNone(leaddb.get_lead(c, alt, alle=True))
        self.assertIsNone(leaddb.get_lead(c, alt2, alle=True))
        self.assertTrue(leaddb.get_lead(c, bleibt, alle=True))
        self.assertIsNone(aufgaben.get(c, aid))
        self.assertTrue(leaddb.get_lead(c, rech, alle=True))  # Rechnung: bleibt immer
        self.assertEqual(aufraeumen.plan(c, [bleibt])["leads_weg"], [])

    def test_projekt_stand_uebernehmen(self):
        from app import agenten, mcp_server, projekte
        c = self.c
        agenten.alles_erlauben(c, "oskar", "ops")
        lid = lead_anlegen(c, "Alter Kunde", stage="kunde")
        pid = projekte.anlegen(c, "oskar", lid, verantwortlich="oskar", technik="finn")
        projekte.weiter(c, "oskar", pid)  # Anforderung: OS legt zwei Schritt-Aufgaben an
        schritt = lambda: [a["titel"] for a in aufgaben.zum_objekt(c, "projekt", pid)  # noqa: E731
                           if a["status"] == "offen" and a["titel"].startswith(("Anforderung schreiben", "Aufwand"))]
        self.assertEqual(len(schritt()), 2)
        _, f = mcp_server.aufrufen(c, "agent:ops", "projekt_status_vorschlagen",
                                   {"projekt_id": pid, "ziel": "betrieb", "live_am": "3.10."})
        self.assertTrue(f)  # Datum falsch: gleich abgelehnt
        d, f = mcp_server.aufrufen(c, "agent:ops", "projekt_status_vorschlagen",
                                   {"projekt_id": pid, "ziel": "betrieb", "beauftragt_am": "2026-10-03",
                                    "live_am": "2026-10-21", "preis_einmal": 415, "anforderung": "Outreach-Software"})
        self.assertFalse(f, d)
        self.assertEqual(projekte.get(c, pid)["status"], "anforderung")  # erst nach dem Ja
        for f_ in c.execute("SELECT id, pruefer FROM freigaben WHERE angefragt_von='agent:ops' AND status='offen' "
                            "AND vorschlag LIKE '%projekt_status%'").fetchall():
            agenten.entscheiden(c, f_["pruefer"], f_["id"], True)
        p = projekte.get(c, pid)
        self.assertEqual(p["status"], "betrieb")
        self.assertEqual((p["live_am"], p["betrieb_am"], p["beauftragt_am"]), ("2026-10-21", "2026-10-21", "2026-10-03"))
        self.assertEqual((p["preis_einmal"], p["anforderung"]), (415, "Outreach-Software"))
        self.assertTrue(p["kunde_bestaetigt_am"])
        self.assertEqual(schritt(), [])  # Schritt-Aufgaben zu; AV-Vertrag u. Ä. aus „Beauftragt“ bleiben
        # Mensch direkt: nur verantwortlich oder Kunden verwalten
        with self.assertRaises(projekte.ProjektError):
            projekte.stand_setzen(c, "agent:ops", pid, "abnahme")
        self.assertEqual(projekte.stand_setzen(c, "oskar", pid, "abnahme")["status"], "abnahme")
        self.assertTrue(projekte.get(c, pid)["punkte"])  # Checkliste steht

    def test_intern_direkt_aendern(self):
        from app import agenten, mcp_server, projekte
        c = self.c
        agenten.alles_erlauben(c, "oskar", "ops")
        lid = lead_anlegen(c, "Direkt Kunde", stage="kunde")
        pid = projekte.anlegen(c, "oskar", lid, verantwortlich="oskar", technik="finn")
        self.assertFalse(agenten.direkt(c, "agent:ops"))
        agenten.direkt_setzen(c, "oskar", "ops", True)
        d, f = mcp_server.aufrufen(c, "agent:ops", "projekt_status_vorschlagen",
                                   {"projekt_id": pid, "ziel": "abnahme", "beauftragt_am": "2026-11-10",
                                    "preis_einmal": 200})
        self.assertFalse(f, d)
        self.assertTrue(d["gesetzt"])
        p = projekte.get(c, pid)
        self.assertEqual((p["status"], p["preis_einmal"], p["beauftragt_am"]), ("abnahme", 200, "2026-11-10"))
        self.assertFalse(c.execute("SELECT 1 FROM freigaben WHERE angefragt_von='agent:ops' AND status='offen' AND "
                                   "vorschlag LIKE '%projekt_status%'").fetchone())
        andere = lead_anlegen(c, "Direkt Lead", stage="neu")
        d, f = mcp_server.aufrufen(c, "agent:ops", "lead_stufe_setzen", {"lead_id": andere, "stufe": "kontaktiert"})
        self.assertTrue(d["gesetzt"], d)
        agenten.direkt_setzen(c, "oskar", "ops", False)
        self.assertFalse(agenten.direkt(c, "agent:ops"))

    def test_software_update_per_agent(self):
        import tempfile
        from pathlib import Path
        from app import agenten, mcp_server
        from app.main import SETTINGS
        c = self.c
        agenten.alles_erlauben(c, "oskar", "ops")
        ordner = Path(tempfile.mkdtemp())
        alt = SETTINGS.get("CONTROL_DIR")
        SETTINGS["CONTROL_DIR"] = ordner
        self.addCleanup(lambda: SETTINGS.__setitem__("CONTROL_DIR", alt))
        d, f = mcp_server.aufrufen(c, "agent:ops", "software_update", {"aktion": "status"})
        self.assertFalse(f, d)
        _, f = mcp_server.aufrufen(c, "agent:ops", "software_update", {"aktion": "update"})
        self.assertTrue(f)  # ohne „Intern direkt ändern“ nicht
        self.assertFalse((ordner / "update-requested").exists())
        agenten.direkt_setzen(c, "oskar", "ops", True)
        d, f = mcp_server.aufrufen(c, "agent:ops", "software_update", {"aktion": "update"})
        self.assertFalse(f, d)
        self.assertTrue((ordner / "update-requested").exists())

    def test_zustaendig_und_standard_person(self):
        from app import agenten, mcp_server
        c = self.c
        agenten.alles_erlauben(c, "oskar", "ops")
        c.execute("UPDATE agenten SET bereich=NULL WHERE key='ops'")
        c.commit()
        d, f = mcp_server.aufrufen(c, "agent:ops", "lead_anlegen", {"name": "Kunde Zuständig", "zustaendig": "oskar"})
        self.assertFalse(f, d)
        self.assertEqual(leaddb.get_lead(c, d["lead_id"], alle=True)["owner"], "oskar")
        _, f = mcp_server.aufrufen(c, "agent:ops", "lead_aendern", {"lead_id": d["lead_id"], "zustaendig": "niemand"})
        self.assertTrue(f)
        alt = agenten.regeln(c)
        form = {"redirects": "\n".join(alt["redirects"]), "standard_person": "oskar",
                **{f"art_{a}": v for a, v in alt["arten"].items()}}
        agenten.regeln_speichern(c, "oskar", form)
        self.assertEqual(agenten._kontext_person(c, "ops"), "oskar")

    def test_mail_verschickt_der_agent(self):
        """Standard seit Stufe 14: Nach dem Ja verschickt der Agent; das OS prüft Sperrliste und Erstkontakt-Regel und
        hält die Mail nach der Rückmeldung im Chat fest."""
        from app import agenten, mailbox
        c = self.c
        lid = lead_anlegen(c, "Agentur Nord", stage="antwort", owner="oskar", email="hi@nord.de", replied_at=HEUTE)
        eid = agenten.entwurf_anlegen(c, "agent:outreach", "mail", "Gern, Dienstag passt.", lead_id=lid, betreff="Re: Call")
        fid = agenten.anfragen(c, "outreach", "mail", "Antwort Agentur Nord", "Kurze Zusage.", entwurf_id=eid)["freigabe_ids"][0]
        erg = agenten.entscheiden(c, "oskar", fid, True)
        self.assertEqual(erg["status"], "bitte_ausfuehren", erg)
        self.assertEqual(self.postfach.mails, [])  # das OS schickt nichts
        st = agenten.status_fuer_agent(c, "outreach")
        eintrag = next(x for x in st["entschieden"] if x["freigabe_id"] == fid)
        self.assertEqual(eintrag["ausfuehrung"], "bitte_ausfuehren")
        self.assertIn("Dienstag", eintrag["freigegebene_fassung"]["text"])
        agenten.erledigt_melden(c, "outreach", fid, "gesendet")
        raus = [m for m in mailbox.thread(c, leaddb.get_lead(c, lid)) if m["direction"] == "out"]
        self.assertTrue(any("Dienstag passt" in m["text"] for m in raus))
        # Sperrliste gilt auch, wenn der Agent verschickt: dann gar nicht erst „bitte ausführen“
        leaddb.kv_set(c, "blocklist", ["hi@nord.de"])
        self.addCleanup(leaddb.kv_set, c, "blocklist", [])
        e2 = agenten.entwurf_anlegen(c, "agent:outreach", "mail", "Noch eine", lead_id=lid, betreff="Re")
        f2 = agenten.anfragen(c, "outreach", "mail", "Zweite Mail Nord", "Noch eine.", entwurf_id=e2)["freigabe_ids"][0]
        self.assertEqual(agenten.entscheiden(c, "oskar", f2, True)["status"], "fehler")

    def test_status_kontaktloses_und_manuelles(self):
        from app import agenten, crm
        c = self.c
        lid = lead_anlegen(c, "Studio West", stage="neu", owner="oskar")
        from app import mcp_server
        daten, fehler = mcp_server.aufrufen(c, "agent:outreach", "lead_stufe_setzen",
                                            {"lead_id": lid, "stufe": "gespraech", "begruendung": "Termin steht."})
        self.assertFalse(fehler, daten)
        fid = daten["als_freigabe"]["freigabe_ids"][0]
        self.assertEqual(leaddb.get_lead(c, lid)["stage"], "neu")  # erst nach der Freigabe
        agenten.entscheiden(c, "oskar", fid, True)
        self.assertEqual(leaddb.get_lead(c, lid)["stage"], "gespraech")
        self.assertEqual(crm.STAGE_LABELS["gespraech"], "Gespräch")
        # Content-Post: OS kann es nicht selbst, Agent bekommt „bitte ausführen“ und meldet erledigt
        eid = agenten.entwurf_anlegen(c, "agent:content", "content", "Post über Automationen", kanal="linkedin")
        fid = agenten.anfragen(c, "content", "content", "LinkedIn-Post KW 48", "Post zur Freigabe.",
                               entwurf_id=eid)["freigabe_ids"][0]
        self.assertEqual(agenten.entscheiden(c, "oskar", fid, True)["status"], "bitte_ausfuehren")
        self.assertIn(fid, agenten.status_fuer_agent(c, "content")["bitte_ausfuehren"])
        with self.assertRaises(agenten.AgentError):
            agenten.erledigt_melden(c, "ops", fid)  # nicht seine
        agenten.erledigt_melden(c, "content", fid, "veröffentlicht")
        self.assertEqual(agenten.entwurf_get(c, eid)["status"], "ausgefuehrt")

    def test_kurzformat_limit_und_buendel(self):
        from app import agenten
        c = self.c
        with self.assertRaises(agenten.AgentError):
            _agent_freigabe(c, titel="Dieser Titel hat ganz eindeutig mehr als fünf Wörter")
        with self.assertRaises(agenten.AgentError):
            _agent_freigabe(c, empfehlung="C")
        c.execute("UPDATE agenten SET max_karten_tag=2 WHERE key='outreach'")
        c.commit()
        for n in range(3):  # ein Bündel zählt einmal
            lid = lead_anlegen(c, f"Agentur {n}", stage="neu", owner="oskar", email=f"x{n}@a.de")
            eid = agenten.entwurf_anlegen(c, "agent:outreach", "mail", f"Hallo {n}", lead_id=lid, betreff="Idee")
            agenten.anfragen(c, "outreach", "mail", f"Erstmail Agentur {n}", "Erstkontakt.", entwurf_id=eid,
                             buendel="Welle 2")
        _agent_freigabe(c)
        with self.assertRaises(agenten.AgentError):  # dritte Karte
            _agent_freigabe(c, titel="Noch eine Frage")
        karten, _ = agenten.karten(c, "oskar")
        buendel = next(k for k in karten if k["buendel"] == "Welle 2")
        self.assertEqual(len(buendel["eintraege"]), 3)
        # Später: verschwindet bis morgen früh aus Zähler und Liste
        fid = buendel["eintraege"][0]["id"]
        n = agenten.offen_zaehlen(c, "oskar")
        agenten.spaeter(c, "oskar", fid)
        self.assertEqual(agenten.offen_zaehlen(c, "oskar"), n - 1)

    def test_werkzeuge_dubletten_und_protokoll(self):
        from app import mcp_server
        c = self.c
        d, f = mcp_server.aufrufen(c, "agent:ops", "aufgabe_anlegen", {"titel": "Rechnung Nr. 11 prüfen",
                                                                        "person": "oskar", "faellig": MORGEN})
        self.assertTrue(d["angelegt"], d)
        d2, _ = mcp_server.aufrufen(c, "agent:ops", "aufgabe_anlegen", {"titel": "Rechnung 11 prüfen", "person": "oskar"})
        self.assertFalse(d2["angelegt"])
        self.assertEqual(d2["dubletten"][0]["id"], d["aufgabe_id"])
        e = c.execute("SELECT wer FROM ereignisse WHERE objekt_typ='aufgabe' AND objekt_id=?",
                      (str(d["aufgabe_id"]),)).fetchone()
        self.assertEqual(e["wer"], "agent:ops")
        self.assertEqual(aufgaben.get(c, d["aufgabe_id"])["erstellt_von_name"], "Ops (Agent)")
        self.assertIn("Neue Aufgabe von Ops (Agent)", leaddb.notices(c, "o", 5, "oskar")[0][0]["title"])
        # Lead-Dubletten
        d, _ = mcp_server.aufrufen(c, "agent:outreach", "lead_anlegen", {"name": "Filmstudio Ost", "email": "o@ost.de"})
        self.assertTrue(d["angelegt"])
        d2, _ = mcp_server.aufrufen(c, "agent:outreach", "lead_anlegen", {"name": "Anders", "email": "o@ost.de"})
        self.assertEqual((d2["angelegt"], d2["vorhandener_lead_id"]), (False, d["lead_id"]))
        # Ohne Recht: Werkzeug unsichtbar und gesperrt, Daten nicht über die Suche
        from app import agenten
        key = agenten.anlegen(c, "oskar", "Test-Bot", rechte_neu={"aufgaben": 1})
        namen = {t["name"] for t in mcp_server.tools_liste(c, f"agent:{key}")}
        self.assertNotIn("lead_akte", namen)
        self.assertNotIn("aufgabe_anlegen", namen)  # nur Sehen
        daten, fehler = mcp_server.aufrufen(c, f"agent:{key}", "lead_akte", {"lead_id": d["lead_id"]})
        self.assertTrue(fehler)
        self.assertIn("Berechtigung", daten["fehler"])
        daten, fehler = mcp_server.aufrufen(c, f"agent:{key}", "suche", {"stichwort": "Filmstudio"})
        self.assertNotIn("leads", daten)
        # Infos landen im Tagesüberblick, nicht als Freigabe
        mcp_server.aufrufen(c, "agent:content", "info_melden", {"titel": "Markt-Scan fertig", "text": "3 Chancen"})
        self.assertEqual(agenten.infos_fuer(c, "finn")[0]["titel"], "Markt-Scan fertig")


class AgentenWebTest(WebBasis):
    """OAuth wie bei Grok und Claude, dann MCP-Aufrufe über HTTP."""

    def _aufraeumen(self):
        c = self.c
        for t in ("oauth_clients", "oauth_codes", "agent_tokens", "agent_entwuerfe", "agent_infos"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM freigaben WHERE art='agent'")
        c.execute("DELETE FROM aufgaben WHERE erstellt_von LIKE 'agent:%'")
        c.execute("UPDATE agenten SET status='aktiv'")
        c.commit()

    def setUp(self):
        super().setUp()
        self.addCleanup(self._aufraeumen)

    def _verbinden(self, agent="outreach", redirect="https://grok.com/connectors-oauth-exchange-code/"):
        import base64
        import hashlib
        from urllib.parse import parse_qs, urlparse
        r = self.client.post("/oauth/register", json={"redirect_uris": [redirect], "client_name": "Grok",
                                                      "token_endpoint_auth_method": "none"})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        cid = r.get_json()["client_id"]
        verifier = "v" * 50
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        q = {"client_id": cid, "redirect_uri": redirect, "response_type": "code", "state": "xyz",
             "code_challenge": challenge, "code_challenge_method": "S256",
             "resource": f"https://os.example.de/mcp/{agent}"}
        r = self.client.get("/oauth/authorize", query_string=q)
        self.assertEqual(r.status_code, 200)
        self.assertIn("verbinden", r.get_data(as_text=True))
        # Der Browser muss nach „Verbinden“ zur Rücksprung-Adresse weiterleiten dürfen (form-action)
        self.assertIn("form-action 'self' https://grok.com", r.headers["Content-Security-Policy"])
        r = self.post("/oauth/authorize", {**q, "agent": agent})
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True))
        ziel = urlparse(r.headers["Location"])
        params = parse_qs(ziel.query)
        self.assertEqual(params["state"], ["xyz"])
        r = self.client.post("/oauth/token", data={"grant_type": "authorization_code", "code": params["code"][0],
                                                   "client_id": cid, "redirect_uri": redirect, "code_verifier": verifier})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return cid, r.get_json()

    def _mcp(self, token, methode, params=None, pfad="/mcp", rid=1):
        return self.client.post(pfad, json={"jsonrpc": "2.0", "id": rid, "method": methode, "params": params or {}},
                                headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"})

    def test_discovery_und_ohne_anmeldung(self):
        r = self.client.get("/.well-known/oauth-authorization-server")
        self.assertEqual(r.get_json()["token_endpoint"].rsplit("/", 2)[-2:], ["oauth", "token"])
        self.assertEqual(r.get_json()["code_challenge_methods_supported"], ["S256"])
        r = self.client.get("/.well-known/oauth-protected-resource/mcp/ops")
        self.assertTrue(r.get_json()["resource"].endswith("/mcp/ops"))
        r = self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(r.status_code, 401)
        self.assertIn("resource_metadata=", r.headers["WWW-Authenticate"])
        # Fremde Rücksprung-Adresse wird nicht registriert
        r = self.client.post("/oauth/register", json={"redirect_uris": ["https://boese.example/cb"]})
        self.assertEqual(r.status_code, 400)

    def test_verbinden_aufrufen_sperren(self):
        c = self.c
        cid, tok = self._verbinden("outreach")
        r = self._mcp(tok["access_token"], "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                          "clientInfo": {"name": "grok", "version": "1"}})
        self.assertEqual(r.get_json()["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("nie selbst frei", r.get_json()["result"]["instructions"])
        r = self.client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                             headers={"Authorization": f"Bearer {tok['access_token']}"})
        self.assertEqual(r.status_code, 202)
        namen = {t["name"] for t in self._mcp(tok["access_token"], "tools/list").get_json()["result"]["tools"]}
        self.assertIn("freigabe_anfragen", namen)
        self.assertNotIn("kunden_und_projekte", namen)  # Outreach hat kein Recht auf Kunden
        r = self._mcp(tok["access_token"], "tools/call", {"name": "kunden_und_projekte", "arguments": {}})
        self.assertTrue(r.get_json()["result"]["isError"])
        r = self._mcp(tok["access_token"], "tools/call", {"name": "aufgabe_anlegen", "arguments": {
            "titel": "Welle 3 recherchieren", "person": "hamza", "faellig": MORGEN}}, pfad="/mcp/outreach")
        daten = json.loads(r.get_json()["result"]["content"][0]["text"])
        self.assertTrue(daten["angelegt"], daten)
        self.assertEqual(c.execute("SELECT wer FROM ereignisse WHERE objekt_typ='aufgabe' AND objekt_id=? ORDER BY id",
                                   (str(daten["aufgabe_id"]),)).fetchone()[0], "agent:outreach")
        # Token eines Agents gilt nicht unter der Adresse eines anderen
        self.assertEqual(self._mcp(tok["access_token"], "tools/list", pfad="/mcp/ops").status_code, 403)
        # Neue Protokoll-Version ohne Handschlag
        r = self._mcp(tok["access_token"], "server/discover", {"_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28"}})
        self.assertIn("2026-07-28", r.get_json()["result"]["supportedVersions"])
        # Erneuern rotiert: altes Erneuerungs-Token gilt danach nicht mehr
        r = self.client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                                   "client_id": cid})
        neu = r.get_json()
        self.assertEqual(r.status_code, 200)
        r = self.client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                                   "client_id": cid})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self._mcp(neu["access_token"], "ping").status_code, 200)
        # Verbindung trennen wirkt sofort
        fam = c.execute("SELECT familie FROM agent_tokens WHERE agent='outreach' LIMIT 1").fetchone()[0]
        r = self.post(f"/einstellungen/agenten/verbindung/{fam}/trennen", headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(self._mcp(neu["access_token"], "tools/list").status_code, 401)
        # Agent sperren wirkt sofort, auch für eine andere Verbindung
        _, tok2 = self._verbinden("outreach")
        self.assertEqual(self._mcp(tok2["access_token"], "ping").status_code, 200)
        self.post("/einstellungen/agenten/outreach/status", {"status": "gesperrt"})
        self.assertEqual(self._mcp(tok2["access_token"], "ping").status_code, 401)

    def test_nur_verwalter_verbinden(self):
        self.als("hamza")
        r = self.client.post("/oauth/register", json={"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]})
        cid = r.get_json()["client_id"]
        r = self.client.get("/oauth/authorize", query_string={
            "client_id": cid, "redirect_uri": "https://claude.ai/api/mcp/auth_callback", "response_type": "code",
            "code_challenge": "x" * 43, "code_challenge_method": "S256"})
        self.assertEqual(r.status_code, 403)

    def test_seiten_und_freigabe_im_browser(self):
        from app import agenten
        c = self.c
        for url in ("/einstellungen/agenten", "/einstellungen/agenten/ops", "/einstellungen/agenten/einrichten",
                    "/einstellungen/protokoll?wer=agent:*", "/einstellungen"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertIn("/mcp/outreach", self.client.get("/einstellungen/agenten/einrichten").get_data(as_text=True))
        self.assertIn("Agents einrichten", self.client.get("/freigaben").get_data(as_text=True))
        fid = _agent_freigabe(c, "ops", art="entscheidung", titel="Rechnungs-Erinnerung schicken")[0]
        agenten.info(c, "ops", "Rechnungen geprüft", "Nichts überfällig.")
        html = self.client.get("/freigaben/alle").get_data(as_text=True)
        self.assertIn("Rechnungs-Erinnerung schicken", html)
        self.assertIn("Empfohlen", html)
        tag = self.client.get("/freigaben").get_data(as_text=True)
        self.assertIn("Rechnungen geprüft", tag)
        self.assertIn("Rechnungs-Erinnerung schicken", tag)
        r = self.post(f"/freigaben/agent/{fid}", {"aktion": "option", "option": "B"}, headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"], r.get_json())
        self.assertEqual(json.loads(c.execute("SELECT geaendert FROM freigaben WHERE id=?", (fid,)).fetchone()[0])["option"], "B")
        c.execute("DELETE FROM entscheidungen WHERE titel LIKE 'Rechnungs-Erinnerung%'")
        c.commit()
        # Bündel: mehrere auf einmal
        ids = [_agent_freigabe(c, "ops", art="sonstiges", titel=f"Punkt {n}", optionen=None, empfehlung=None,
                               buendel="Kleinkram")[0] for n in range(2)]
        self.assertIn("2 × Kleinkram", self.client.get("/freigaben").get_data(as_text=True))
        r = self.post("/freigaben/agent/buendel", {"ids": [str(i) for i in ids], "aktion": "freigeben"},
                      headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"], r.get_json())
        self.assertEqual({x[0] for x in c.execute(f"SELECT status FROM freigaben WHERE id IN ({ids[0]},{ids[1]})")},
                         {"angenommen"})
        # Hamza sieht Oskars Karten nicht und kann sie nicht entscheiden
        fid = _agent_freigabe(c, "ops", titel="Nur für Oskar")[0]
        self.als("hamza")
        self.assertNotIn("Nur für Oskar", self.client.get("/freigaben").get_data(as_text=True))
        r = self.post(f"/freigaben/agent/{fid}", {"aktion": "option", "option": "A"}, headers={"X-Requested-With": "fetch"})
        self.assertEqual(r.status_code, 400)


class SlackUeberAgentsTest(unittest.TestCase):
    """Stufe 13: Slack (und WhatsApp, LinkedIn) laufen über die Agents. Sie tragen ein, was reinkommt, und
    verschicken, was im Chat beantwortet wurde."""

    def setUp(self):
        self.c = frische_db()

    def test_eintragen_senden_melden(self):
        from app import mcp_server
        c = self.c
        lid = lead_anlegen(c, "Slack Kunde", stage="gespraech")
        d, f = mcp_server.aufrufen(c, "agent:outreach", "nachricht_eintragen",
                                   {"lead_id": lid, "kanal": "slack", "richtung": "in", "text": "Moin, kurze Frage",
                                    "extern_id": "T1", "zeitpunkt": "2026-11-25T09:30"})
        self.assertFalse(f, d)
        self.assertTrue(d["eingetragen"])
        d2, _ = mcp_server.aufrufen(c, "agent:outreach", "nachricht_eintragen",
                                    {"lead_id": lid, "kanal": "slack", "text": "Moin, kurze Frage", "extern_id": "T1"})
        self.assertFalse(d2["eingetragen"])  # dieselbe Nachricht nur einmal
        row = c.execute("SELECT direction, kanal, read, at FROM messages WHERE lead_id=?", (lid,)).fetchone()
        self.assertEqual(tuple(row), ("in", "slack", 0, "2026-11-25T09:30:00"))  # ungelesen: wartet im Chat
        _, f = mcp_server.aufrufen(c, "agent:outreach", "nachricht_eintragen",
                                   {"lead_id": lid, "kanal": "telefon", "text": "x"})
        self.assertTrue(f)  # Telefon trägt man von Hand ein, nicht über Agents
        # Antwort aus dem Chat wartet, bis ein Agent sie verschickt
        from app import posteingang
        mid = posteingang.zum_senden(c, "oskar", lid, "slack", "Ja, morgen 10 Uhr.")
        d, _ = mcp_server.aufrufen(c, "agent:outreach", "nachrichten_zu_senden", {})
        self.assertEqual([x["id"] for x in d["zu_senden"]], [mid])
        self.assertEqual(mcp_server.aufrufen(c, "agent:outreach", "nachrichten_zu_senden", {"kanal": "whatsapp"})[0]
                         ["zu_senden"], [])
        d, f = mcp_server.aufrufen(c, "agent:outreach", "nachricht_gesendet_melden", {"nachricht_id": mid})
        self.assertFalse(f, d)
        self.assertEqual(c.execute("SELECT versand FROM messages WHERE id=?", (mid,)).fetchone()[0], "gesendet")
        _, f = mcp_server.aufrufen(c, "agent:outreach", "nachricht_gesendet_melden", {"nachricht_id": mid})
        self.assertTrue(f)  # nicht zweimal
        with self.assertRaises(posteingang.PosteingangError):
            posteingang.zum_senden(c, "oskar", lid, "telefon", "Telefon verschickt kein Agent")
        # Mail tragen die Agents ebenfalls ein (der Server ruft keine Postfächer mehr ab); No-Reply zählt nie als wartend
        d, f = mcp_server.aufrufen(c, "agent:outreach", "nachricht_eintragen",
                                   {"lead_id": lid, "kanal": "mail", "text": "Können wir telefonieren?", "betreff": "Frage",
                                    "adresse": "chef@slack-kunde.de", "extern_id": "<m1@x>",
                                    "zeitpunkt": "2099-01-01T10:00"})
        self.assertFalse(f, d)
        self.assertIn(lid, [w["id"] for w in posteingang.wartend(c)])
        werbung = lead_anlegen(c, "Hosting GmbH", stage="kunde")
        posteingang.agent_nachricht(c, "agent:ops", werbung, "mail", "in", "Plan abgelaufen", adresse="no-reply@host.example")
        self.assertNotIn(werbung, [w["id"] for w in posteingang.wartend(c)])


class AufgabenstapelWebTest(WebBasis):
    """Stufe 13: Freigaben wie im Cockpit (eine Zahl, Starten, eine Karte pro Bildschirm); seit 13b Aufgaben als
    Erinnerungsliste auf eigener Seite."""

    def test_aufgaben_wie_erinnerungen(self):
        from app import mcp_server, stapel
        c = self.c
        d, f = mcp_server.aufrufen(c, "agent:ops", "aufgabe_anlegen", {"titel": "Vertrag Testkunde unterschreiben",
                                                                        "person": "finn", "faellig": aufgaben.heute()})
        self.assertFalse(f, d)
        aid = d["aufgabe_id"]
        self.addCleanup(lambda: (c.execute("DELETE FROM aufgaben WHERE id=? OR titel=?", (aid, "Server prüfen")),
                                 c.commit()))
        self.assertNotIn(f"aufgabe:{aid}", [x["key"] for x in stapel.stapel(c, "finn")])  # keine Freigabe
        html = self.client.get("/aufgaben?fuer=finn").get_data(as_text=True)
        self.assertIn("Vertrag Testkunde unterschreiben", html)
        self.assertIn("von Ops (Agent)", html)
        self.assertNotIn("Vertrag Testkunde unterschreiben", self.client.get("/aufgaben").get_data(as_text=True))
        # schnell eintragen: nur Was, Für wen und Wann; der Bereich ergibt sich aus der Person
        r = self.post("/aufgaben/neu", {"titel": "Server prüfen", "person": "finn", "schnell": "morgen"},
                      headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"], r.get_data(as_text=True))
        neu = c.execute("SELECT person, bereich FROM aufgaben WHERE titel='Server prüfen'").fetchone()
        self.assertEqual(neu["person"], "finn")
        self.assertTrue(neu["bereich"])
        # abhaken
        self.als("finn")
        r = self.post(f"/aufgaben/{aid}/erledigt", {}, headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"])
        self.assertIn("Erledigt", self.client.get("/aufgaben").get_data(as_text=True))

    def test_stapel_fokus_und_entscheiden(self):
        from app import stapel
        c = self.c
        fid = _agent_freigabe(c, "ops", titel="Stapel-Test entscheiden")[0]
        self.addCleanup(lambda: (c.execute("DELETE FROM freigaben WHERE id=?", (fid,)),
                                 c.execute("DELETE FROM entscheidungen WHERE titel LIKE 'Stapel-Test%'"), c.commit()))
        items = stapel.stapel(c, "oskar")
        self.assertIn(f"agent:{fid}", [x["key"] for x in items])
        self.assertEqual(items[0]["typ"], "agent")  # Freigaben vor allem anderen
        html = self.client.get("/freigaben").get_data(as_text=True)
        self.assertIn("Starten", html)
        self.assertIn("Stapel-Test entscheiden", html)
        r = self.client.get("/freigaben/fokus?neu=1")
        self.assertEqual(r.headers["Location"], "/freigaben/fokus")
        karte = self.client.get("/freigaben/fokus").get_data(as_text=True)
        self.assertIn("Stapel-Test entscheiden", karte)
        self.assertIn('data-taste="1"', karte)  # Optionen per Taste 1–3
        # Jetzt nicht: die nächste Karte kommt, die übersprungene bleibt offen
        self.post("/freigaben/fokus/weiter", {"key": f"agent:{fid}"})
        self.assertNotIn("Stapel-Test entscheiden", self.client.get("/freigaben/fokus").get_data(as_text=True))
        self.assertEqual(c.execute("SELECT status FROM freigaben WHERE id=?", (fid,)).fetchone()[0], "offen")
        # neu starten: wieder vorne, dann entscheiden
        self.client.get("/freigaben/fokus?neu=1")
        r = self.post(f"/freigaben/agent/{fid}", {"aktion": "option", "option": "A", "next": "/freigaben/fokus"},
                      headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"], r.get_json())
        self.assertNotIn(f"agent:{fid}", [x["key"] for x in stapel.stapel(c, "oskar")])
        # alte Links auf die Liste führen weiter
        self.assertEqual(self.client.get("/aufgaben?ansicht=alle").headers["Location"], "/aufgaben/alle?ansicht=alle")
        self.assertEqual(self.client.get("/mein-tag").headers["Location"], "/freigaben")
        self.assertEqual(self.client.get("/aufgaben/fokus").headers["Location"], "/freigaben")

    def test_pipeline_und_chat(self):
        from app import posteingang
        c = self.c
        html = self.client.get("/pipeline").get_data(as_text=True)
        for text in ("Agents heute", "Ops", "Outreach", "Freigaben offen"):
            self.assertIn(text, html)
        lid = lead_anlegen(c, "Chat Kunde", stage="gespraech", owner="oskar", email="chat@kunde.example")
        self.addCleanup(lambda: (c.execute("DELETE FROM messages WHERE lead_id=?", (lid,)),
                                 c.execute("DELETE FROM leads WHERE id=?", (lid,)), c.commit()))
        posteingang.agent_nachricht(c, "agent:outreach", lid, "slack", "in", "Moin aus Slack", extern_id="S9")
        liste = self.client.get("/chat").get_data(as_text=True)
        self.assertIn("Wartet auf euch", liste)
        self.assertIn("Chat Kunde", liste)
        verlauf = self.client.get(f"/chat/{lid}").get_data(as_text=True)
        self.assertIn("Moin aus Slack", verlauf)
        self.assertIn('data-ch-kanal="slack" class="on"', verlauf)  # Antwort auf dem Kanal der letzten Nachricht
        r = self.post(f"/chat/{lid}/senden", {"kanal": "slack", "text": "Gern, bis gleich."},
                      headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"], r.get_data(as_text=True))
        self.assertIn("wartet auf Versand durch einen Agent", self.client.get(f"/chat/{lid}").get_data(as_text=True))
        self.assertEqual([x["lead_id"] for x in posteingang.offene_sendungen(c, "slack")], [lid])
        c.execute("UPDATE messages SET versand=NULL WHERE lead_id=?", (lid,))
        c.commit()
        self.assertEqual(self.client.get(f"/crm/postfach/{lid}").headers["Location"], f"/chat/{lid}")


class AufraeumenWebTest(WebBasis):
    """Stufe 15: Neustart über Einstellungen → Alte Daten entfernen (nur mit „Einstellungen: Verwalten“)."""

    def test_seite_und_loeschen(self):
        c = self.c
        alt = lead_anlegen(c, "Uralt Tischlerei", stage="kontaktiert")
        neu = lead_anlegen(c, "Frische Filmproduktion", stage="neu")
        bleiben = [r[0] for r in c.execute("SELECT id FROM leads WHERE id != ?", (alt,))]
        html = self.client.get(f"/einstellungen/aufraeumen?behalten={neu}").get_data(as_text=True)
        self.assertIn("Alte Daten entfernen", html)
        self.assertIn("Uralt Tischlerei", html)
        self.assertIn("Alte Daten entfernen", self.client.get("/einstellungen").get_data(as_text=True))
        self.als("hamza")
        self.assertEqual(self.post("/einstellungen/aufraeumen", {"behalten": [str(neu)]}).status_code, 403)
        self.als("oskar")
        r = self.post("/einstellungen/aufraeumen", {"behalten": [str(x) for x in bleiben]})
        self.assertEqual(r.status_code, 302)
        self.assertIsNone(leaddb.get_lead(c, alt, alle=True))
        self.assertTrue(leaddb.get_lead(c, neu, alle=True))
        self.addCleanup(lambda: (c.execute("DELETE FROM leads WHERE id=?", (neu,)), c.commit()))
        from app import main
        self.assertTrue(list(main.SETTINGS["DATA_DIR"].glob("vor-aufraeumen-*.tar.gz")))
