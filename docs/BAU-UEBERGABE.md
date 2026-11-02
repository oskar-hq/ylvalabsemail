# Übergabe für den Weiterbau (nach Stufe 10)

**Stand:** 01.11.2026 · alle zehn Baustufen gebaut und gemergt · weiter mit Ausbau und Praxistest

Diese Datei ist für die nächste Sitzung (Mensch oder KI), die am Firmen-OS weiterbaut. **Wer neu einsteigt:**
erst `CLAUDE.md`, dann diese Datei ganz, dann Abschnitt 3 mit Oskar abstimmen. Sie fasst zusammen, was
steht, wie hier gearbeitet wird und was als Nächstes sinnvoll ist. Ausführlich steht alles in
[FIRMEN-OS.md](FIRMEN-OS.md) (Bauplan, je Stufe „Umsetzung und Abweichungen“).

## 1. Arbeitsweise (bewährt in Stufe 1 bis 10)

1. `CLAUDE.md`, [FIRMEN-OS.md](FIRMEN-OS.md), [ROLLEN-UND-ABLAEUFE.md](ROLLEN-UND-ABLAEUFE.md) und
   [DESIGN.md](DESIGN.md) lesen, dann den betroffenen Code.
2. Kurzen Plan für die Stufe mit Oskar abstimmen. Oskar hat für Stufe 2 bis 10 gesagt: Plan kurz abstimmen, dann bauen,
   testen, im Browser ansehen, Doku nachziehen, Pull Request und mergen.
3. Bauen mit Tests: `python -m unittest discover tests` (Stand: 190 Tests, alle grün). Neue Tests für das OS
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
- **Design:** Bausteine in `static/os.css`, `static/os.js`, `templates/_os.html` (Listenzeilen, Sheets mit
  `data-sheet`, schwebende Knöpfe `os-float`, Formulare mit `data-os-fetch="reload"`, Toasts per `flash`).

## 3. Nach Stufe 10: was als Nächstes sinnvoll ist

Alle Baustufen aus FIRMEN-OS.md Abschnitt 8 sind gebaut. Vorschläge für die nächsten Schritte (mit Oskar abstimmen,
jeweils die erste Option ist die Empfehlung):

1. **Praxistest vor Ausbau:** Zwei Wochen echter Alltag mit dem OS, Fehler und Wünsche als Aufgaben im Bereich
   Technik sammeln, dann gezielt nachbessern. Besonders noch nie echt getestet: Google-Kalender, Mailversand der
   Angebote und Rechnungen, weitere Postfächer, Assistent mit echtem Schlüssel (in den Tests nachgebaut).
2. **Assistent ausbauen:** weitere Werkzeuge (z. B. Entscheidungs-Log durchsuchen, Kosten je Projekt), „Was zuerst?“
   optional jeden Morgen automatisch, Fragen per Push-Meldung beantworten lassen.
3. **Unipile** für WhatsApp und LinkedIn verbinden (vorbereitet in Stufe 9), sobald Budget da ist.
4. **Restliche alte Seiten** (CRM-Board, Leads, Akte, Mailer, Einstellungen) nach und nach auf `os.css` umstellen;
   das Postfach im CRM ist schon umgestellt. Die alten Seiten sehen in hell und dunkel ordentlich aus, eilig ist das
   nicht.

**Vor dem ersten echten Arbeitstag:** Einstellungen → Startklar abarbeiten (neue Prüfpunkte als `_punkt(...)` in
`app/startklar.py` ergänzen), dann Systemcheck.

**Bausteine des Assistenten** (für Erweiterungen):

- Neues Werkzeug: Funktion `_w_<name>(c, person, eingabe)` in `assistent.py` (Recht mit `_darf` prüfen, bei Fehlern
  `_Kein` werfen, Rückgabe `(daten, quellen)`), Schema in `WERKZEUGE` (strikt, `additionalProperties: false`) und
  Eintrag in `AUSFUEHREN`.
- KI-Aufruf: `ai.anfrage(stage, model, effort, system, messages, tools=None, schema=None)` in
  `with agent.acting_as(c, person):`; `ai.text_von(response)` für den Text. Tests mit `FakeClaude` in
  `tests/test_os.py` (nie gegen die echte API).
- Alles, was an Kunden geht, bleibt Entwurf oder läuft über `freigaben` (Art `angebot` als Muster).

## 4. Für einen neuen Agenten: Umgebung und Fallstricke

- **Tests:** `python -m unittest discover tests` (dauert etwa 25 Sekunden; Meldungen „Kreisgrenzen nicht geladen“
  und „Fehlgeschlagene Anmeldung“ sind normal). Dazu `python -m pyflakes app tests`; bekannte alte Meldungen in
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
