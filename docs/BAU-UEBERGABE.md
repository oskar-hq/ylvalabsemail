# Übergabe für den Weiterbau (nach Stufe 11)

**Stand:** 25.11.2026 · Stufe 1 bis 11 gebaut und gemergt · nächste: Praxistest mit dem ersten Kunden (Abschnitt 3)

Diese Datei ist für die nächste Sitzung (Mensch oder KI), die am Firmen-OS weiterbaut. **Wer neu einsteigt:**
erst `CLAUDE.md`, dann diese Datei ganz (vor allem „Arbeitsbereiche“ in Abschnitt 2). Sie fasst zusammen, was
steht, wie hier gearbeitet wird und was als Nächstes sinnvoll ist. Ausführlich steht alles in
[FIRMEN-OS.md](FIRMEN-OS.md) (Bauplan, je Stufe „Umsetzung und Abweichungen“).

## 1. Arbeitsweise (bewährt in Stufe 1 bis 11)

1. `CLAUDE.md`, [FIRMEN-OS.md](FIRMEN-OS.md), [ROLLEN-UND-ABLAEUFE.md](ROLLEN-UND-ABLAEUFE.md) und
   [DESIGN.md](DESIGN.md) lesen, dann den betroffenen Code.
2. Kurzen Plan für die Stufe mit Oskar abstimmen. Oskar hat für Stufe 2 bis 10 gesagt: Plan kurz abstimmen, dann bauen,
   testen, im Browser ansehen, Doku nachziehen, Pull Request und mergen.
3. Bauen mit Tests: `python -m unittest discover tests` (Stand: 215 Tests, alle grün). Neue Tests für das OS
   kommen in `tests/test_os.py` (die Reihenfolge der Testdateien ist wichtig: `test_leads.py` setzt die
   Umgebung, Dateien davor dürfen `app.main` nicht importieren).
4. Seiten im Browser ansehen (Playwright ist vorinstalliert): Handy 390 px und Desktop, hell und dunkel.
5. In FIRMEN-OS.md die Stufe abhaken und „Umsetzung und Abweichungen“ eintragen, README und UEBERGABE nachziehen,
   Pull Request, mergen.

**Wichtig zum Repository:** Der Standard-Branch heißt `claude/practical-dijkstra-nnnz1b`, nicht `main`. Pull
Requests gehen dorthin. Nach einem Merge den Arbeits-Branch neu von dort abzweigen.

**Python:** Der Server läuft ggf. mit Python 3.11. Keine f-Strings mit gleichen Anführungszeichen innen
(erst ab 3.12 erlaubt). Prüfen: `python3.11 -m py_compile app/*.py`.

## 2. Was steht (Kurzüberblick)

| Stufe | Module (Code) | Seiten |
|---|---|---|
| 1 Fundament | `firma.py` (Personen, Rollen, Rechte, Bereiche, Protokoll `firma.log`), `aufgaben.py` | Mein Tag, Aufgaben, Einstellungen → Personen/Rollen/Bereiche/Protokoll |
| 2 Branchentest | `branchentest.py` (Testbranchen, Lead-Prüfung als Freigabe) | Branchentest, Leads prüfen |
| 3 Kalender | `kalender.py`, `google_kalender.py` (OAuth, Meet, frei/belegt) | Kalender, Termin, Mein Kalender, Einstellungen → Kalender |
| 4 KPIs | `kpis.py` (Katalog, Ziele, Agenda, Lernstufen) | Cockpit, Wochengespräch |
| 5 Kunden | `projekte.py` (Journey, Abnahme, Änderungen, Zeit, Kosten) | Kunden, Projekt, Einstellungen → Projekte |
| 6 Freigaben | `freigaben.py` (Zentrale mit Registry `ARTEN`, Stufen A–D, Entscheidungsrechte, Log), `notfall.py` | Freigaben, Entscheidungs-Log, Notfall, Einstellungen → Freigaben und Entscheidungen |
| 7 Kunden-Bereich | `portal.py` (Einmal-Link, Ansicht für Kunden, Dokumente, Wochenbericht, Termin-Erinnerung) | `/portal` (Kunden), Projekt → Kunden-Bereich/Dokumente/Wochenberichte, Einstellungen → Kunden-Bereich |
| 8 Finanzen | `finanzen.py` (Rechnungen mit Nummernkreis und Storno, Kosten, Kasse, Kennzahlen, Geld-Regel, Export), `pdf.py` | Finanzen, Rechnung, Laufende Kosten, Einstellungen → Finanzen, Projekt → Rechnungen |
| 9 Posteingang | `posteingang.py` (weitere Postfächer, Kanäle, von Hand festhalten, Wünsche aus Nachrichten, Antwortzeit), `mailbox.py` | CRM → Postfach und Gespräch, Einstellungen → Postfächer, Einstellungen → Unipile |
| Start | `startklar.py` (Checkliste für den echten Betrieb, nur gespeicherte Werte) | Einstellungen → Startklar, Hinweis auf Mein Tag, Mein Profil → Meldungen aufs Handy |
| 11 Arbeitsbereiche | `arbeitsbereiche.py` (aktiver Arbeitsbereich, Einstellungen und Postfach je Kunde, Zahlen für Kunden) | Auswahl oben, Einstellungen → Arbeitsbereiche, Kunden-Bereich → Outreach, Postfach-Ordner |
| 10 Assistent | `assistent.py` (Fragen mit Werkzeugen und Rechteprüfung, „Was zuerst?“, Entwürfe, Angebote mit PDF und Freigabe), `ai.anfrage` | Assistent, Mein Tag → Was zuerst?, Angebote, Einstellungen → Assistent, Entwurf-Knöpfe in Gespräch/Termin/Projekt |

Je Modul gibt es `*_views.py` mit den Seiten. Gemeinsame Muster, die neue Module wieder nutzen sollten:

- **Datenbank:** `SCHEMA` im Modul, in `leaddb.connect` eingebunden; neue Spalten über `leaddb.MIGRATIONS`,
  einmalige Startwerte über `leaddb.DATA_MIGRATIONS` (nur hinten anfügen, nie löschen).
- **Rechte:** Neues Modul in `firma.MODULE` eintragen, per `DATA_MIGRATIONS` mit `json_set` in die bestehenden
  Rollen schreiben (Muster: `kalender`, `cockpit`, `kunden`), Blueprint in `main.MODUL_JE_BEREICH` zuordnen.
  Der Test `test_seiten_haben_ein_modul` prüft, dass jede Seite ein Modul hat.
- **Protokoll:** Jede schreibende Funktion ruft `firma.log(c, wer, art, objekt_typ, objekt_id, text, daten)`.
- **Meldungen:** `aufgaben.melden(c, person, titel, text, url, push=False)` (Glocke je Person, Push optional).
- **Regeln als Einstellung:** je Modul ein `REGELN`-Dict, gespeichert in `kv` unter `regel:<modul>`, mit
  `regeln()`/`regeln_speichern()` und einer Einstellungsseite.
- **Takt:** Hintergrund-Arbeiter `agent.tick()` ruft `kalender.takt`, `kpis.takt`, `projekte.takt`,
  `freigaben.takt`, `portal.takt`, `finanzen.takt`, `posteingang.takt` auf (je mit eigenem try/except).
- **Freigaben:** Was ein Mensch freigeben muss, als Zeile in `freigaben` anlegen und die Art in `freigaben.ARTEN`
  eintragen; dann erscheint es in der Zentrale, im Zähler und auf Mein Tag. Stufe der Aktion über
  `freigaben.stufe(c, key)` lesen (Katalog `freigaben.AKTIONEN`). Regeländerungen mit
  `firma.log(..., "einstellung", "regel", ...)` landen automatisch im Entscheidungs-Log.
- **Arbeitsbereiche (Stufe 11), wichtig für jeden neuen Code der Akquise:** Jede Abfrage auf `leads`, `runs`,
  `ai_usage`, `signals`, `brain_versions`, `known_firms` braucht `arbeitsbereich=ab()` (bzw. `l.arbeitsbereich=ab()`),
  sonst sieht ein Kunde Leads von Ylva Labs. Neue Zeilen bekommen den Arbeitsbereich automatisch. Gespeicherte Werte
  der Akquise, die je Kunde getrennt sein sollen, in `leaddb.AB_KV` eintragen. Einstellungen über `agent.S()` lesen
  (nicht `SETTINGS[...]`), dann gelten die Werte des Kunden. Firmen-OS-Module lesen Leads mit
  `leaddb.get_lead(c, id, alle=True)`. Im Hintergrund: `with arbeitsbereiche.im(key):`. Tests: `kunde_anlegen` in
  `tests/test_os.py`.
- **Design:** Bausteine in `static/os.css`, `static/os.js`, `templates/_os.html` (Listenzeilen, Sheets mit
  `data-sheet`, schwebende Knöpfe `os-float`, Formulare mit `data-os-fetch="reload"`, Toasts per `flash`).

## 3. Als Nächstes

Stufe 11 (Outreach für Kunden in Arbeitsbereichen) ist gebaut, Details und Abweichungen in FIRMEN-OS.md. Dazu kamen auf
Oskars Wunsch Ordner im Postfach (Gespräche, Sonstige, Fehler; Fehler-Mails auch im Mailprogramm in „Fehler“) und die
Karte im CRM ist entfernt.

1. **Erster Kunde im Alltag:** Postfach des Kunden anlegen, Arbeitsbereich einrichten (UEBERGABE.md, „Nach dem
   Update“), einen kleinen Lauf, Entwürfe freigeben, Antworten im Postfach prüfen. Vorher mit dem Anwalt: AV-Vertrag,
   § 7 UWG im Namen des Kunden, Impressum und **Abmeldelink** in seinen Mails (noch nicht gebaut).
2. **Noch nie mit echten Diensten getestet:** Versand und Abruf aus dem Postfach eines Kunden, das Verschieben in den
   Ordner „Fehler“ bei Strato (IMAP COPY/EXPUNGE).
3. Danach weiter wie bisher: Praxistest im Alltag, Assistent ausbauen, Unipile, restliche alte Seiten auf `os.css`.

## 4. Für einen neuen Agenten: Umgebung und Fallstricke

- **Tests:** `python -m unittest discover tests` (dauert etwa 30 Sekunden; die Meldung „Fehlgeschlagene Anmeldung“
  ist normal). Dazu `python -m pyflakes app tests`; bekannte alte Meldungen in
  `branchentest.py:451` und `main.py` (`firma` doppelt importiert) sind nicht neu.
- **App lokal ansehen:** Den Demo-Modus gibt es nicht mehr. Einmal `python tests/vorschau.py <ordner>` (erfundene
  Beispieldaten, Oskar bekommt das Passwort `vorschau123`), dann
  `DATA_DIR=<ordner> SECRET_KEY=x LEADS_WORKER=false OUTREACH_EMAIL=false flask --app app.main run -p 8097` und unter
  `/login` als Oskar anmelden. Ohne Postfach in den Einstellungen geht keine Mail raus. Flask lädt Vorlagen und Python-Code
  nicht neu: nach Änderungen den Server neu starten. Screenshots mit Playwright (Handy 390×844, Desktop 1300×900,
  `data-theme` auf `light`/`dark`), auf Überlauf prüfen (`document.documentElement.scrollWidth`).
- **Skripte außerhalb der App** (z. B. zum Anlegen von Testdaten): `agent.CFG` ist dann leer. Funktionen, die
  `agent.S(...)` oder `agent.CFG["data_dir"]` brauchen (Mailversand, verschlüsselte Passwörter), vorher mit
  `agent.CFG["data_dir"] = ...` versorgen oder nur über die App/Tests aufrufen.
- **Muster je Stufe:** Modul `app/<name>.py` (SCHEMA, Logik, `firma.log` bei jedem Schreiben), Seiten in
  `app/<name>_views.py` (Blueprint in `main.py` registrieren und in `MODUL_JE_BEREICH` einem Rechte-Modul zuordnen),
  Vorlagen mit den Bausteinen aus `_os.html`/`os.css`, Einstellungen als `REGELN` in `kv` `regel:<name>`, Takt in
  `agent.tick()`, Tests als neue Klassen am Ende von `tests/test_os.py`, Doku in FIRMEN-OS.md (abhaken +
  „Umsetzung und Abweichungen“), README, UEBERGABE („Nach dem Update“) und diese Datei.
- **Commits und Pull Requests:** auf Deutsch, PR gegen `claude/practical-dijkstra-nnnz1b`, danach mergen und den
  Arbeits-Branch neu vom Standard-Branch abzweigen.

## 5. Offen und gut zu wissen

- **Google-Kalender** ist im Code fertig, aber noch nie mit echtem Google getestet (in der Entwicklungsumgebung
  gesperrt). Erster echter Test steht in UEBERGABE.md.
- **Offene Fragen** aus FIRMEN-OS.md Abschnitt 10: Status Hamza/Omid, Stunden pro Woche (für Terminvorschläge je
  Person), Buchungslink für Kunden, Zeitpunkt Kunden-Bereich, Name des OS.
- **Noch nie mit echten Diensten getestet** (nur mit nachgebauten in den Tests): Mailversand an Kunden
  (Kunden-Bereich, Wochenbericht, Rechnungen), weitere Postfächer (IMAP/SMTP), Rechnungs-Mail mit PDF-Anhang.
- **Später fällig:** E-Rechnung (XRechnung/ZUGFeRD) vor 2028; Unipile-Anbindung für WhatsApp/LinkedIn (nur
  vorbereitet).
- Nach jedem Update auf dem Server: die „Nach dem Update“-Hinweise je Stufe in UEBERGABE.md abarbeiten
  (Omid anlegen, Google-Zugang eintragen, Zielwerte, Projekte-Regeln …).
