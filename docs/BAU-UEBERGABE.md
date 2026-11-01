# Übergabe für den Weiterbau (ab Stufe 10)

**Stand:** 01.11.2026 · Stufe 1 bis 9 gebaut und gemergt · nächste: **Stufe 10**

Diese Datei ist für die nächste Sitzung (Mensch oder KI), die am Firmen-OS weiterbaut. **Wer neu einsteigt:**
erst `CLAUDE.md`, dann diese Datei ganz, dann Abschnitt 3 mit Oskar abstimmen. Sie fasst zusammen, was
steht, wie hier gearbeitet wird und was in Stufe 10 zu tun ist. Ausführlich steht alles in
[FIRMEN-OS.md](FIRMEN-OS.md) (Bauplan, je Stufe „Umsetzung und Abweichungen“).

## 1. Arbeitsweise (bewährt in Stufe 1 bis 9)

1. `CLAUDE.md`, [FIRMEN-OS.md](FIRMEN-OS.md), [ROLLEN-UND-ABLAEUFE.md](ROLLEN-UND-ABLAEUFE.md) und
   [DESIGN.md](DESIGN.md) lesen, dann den betroffenen Code.
2. Kurzen Plan für die Stufe mit Oskar abstimmen. Oskar hat für Stufe 2 bis 9 gesagt: Plan kurz abstimmen, dann bauen,
   testen, im Browser ansehen, Doku nachziehen, Pull Request und mergen.
3. Bauen mit Tests: `python -m unittest discover tests` (Stand: 174 Tests, alle grün). Neue Tests für das OS
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

## 3. Stufe 10: Assistent

Aus dem Bauplan (Abschnitt 4.12): *KI-Fragen an die Firmendaten, Vorschläge für Prioritäten, Entwürfe für Angebote,
Briefings und Case Studys.*

**Ausgangslage:**

- KI-Anbindung gibt es (`ai.py`, Anthropic-Schlüssel in den Einstellungen, Monatsbudget, Kosten je Person in
  `ai_usage`). Sie wird bisher für Lead-Entwürfe und das Einordnen genutzt.
- Daten liegen strukturiert vor: Akten und Nachrichten (`messages` mit Kanal), Projekte, Änderungen, Termine,
  Aufgaben, Ziele und KPIs, Entscheidungs-Log, Notfälle, Rechnungen und Kosten.
- Freigabe-Stufen: Alles, was der Assistent an Kunden schreibt, läuft über `freigaben.AKTIONEN` (C oder D);
  „Angebot verschicken“ steht dort noch als „kommt später“.

**Vorschlag für den Umfang.** Diese vier Fragen waren schon formuliert, aber noch nicht beantwortet. Vor dem Bau
mit Oskar klären; jeweils die erste Antwort ist die Empfehlung:

1. **Umfang:** alle Teile (Fragen an die Firmendaten, Prioritäten, Entwürfe für Antwort/Briefing/Angebot/Case Study,
   Einstellungen mit Budget, Protokoll mit Quellen) · oder ohne Angebote · oder nur Fragen und Entwürfe.
2. **Wie der Assistent Daten liest:** gezielt mit Werkzeugen (Tool Use: z. B. `akte(lead)`, `offene_rechnungen()`,
   `kpis(woche)`, jedes Werkzeug prüft `firma.darf` der fragenden Person) · oder eine Zusammenfassung aller sichtbaren
   Daten mit jeder Frage (einfacher, teurer, ungenauer).
3. **Prioritäten auf „Mein Tag“:** auf Knopfdruck („Was zuerst?“, kostet nur beim Klick), dazu immer eine
   Reihenfolge nach Regeln ohne KI · oder jeden Morgen automatisch je Person.
4. **Angebote:** KI-Entwurf aus Anforderung und Schätzung, bearbeitbar, PDF wie bei Rechnungen (`pdf.py`), Versand
   über die Freigabe-Stufe „angebot“ (in `freigaben.AKTIONEN` auf `kommt_mit="später"`, freischalten) · oder nur
   Text-Entwurf.

**Bausteine, die es schon gibt:**

- `ai.py`: `_call(stage, brain, prompt, schema)` mit strukturierter Ausgabe (JSON-Schema), Modellwahl je Stufe
  (`MODELS`), Preise (`PRICES`), Kosten über `ai.on_usage` → `leaddb.record_usage` (Tabelle `ai_usage`, mit Person).
  Für den Assistenten eine eigene Stufe in `MODELS`/`MAX_TOKENS`/`EFFORT`/`STAGE_LABELS` anlegen; Tool Use braucht
  eine eigene Schleife (`messages.create` mit `tools`, bis `stop_reason != "tool_use"`), nicht `_call`.
- Budget: `agent.month_budget_left_eur(c)` (Monatsbudget des Teams), `agent.person_budget_left_eur(c, key)` (eigenes
  Budget aus dem Profil). KI-Aufrufe immer in `with agent.acting_as(db(), me()):`, damit die Kosten der Person
  zugerechnet werden (Muster: `leads_views.py`).
- Ohne Schlüssel (`ai.available()` ist False) muss alles ohne KI weiterlaufen; Tests nie gegen die echte API,
  sondern mit einem nachgebauten Client (Muster für nachgebaute Dienste: `FakeGoogle`, `FakeImap`, `PostfachMock` in
  `tests/test_os.py`).
- Was an Kunden geht, nur als Entwurf; Versand über `portal.mail_an_kunde` bzw. `posteingang.senden` und die
  Freigabe-Stufen (`freigaben.stufe`, neue Art in `freigaben.ARTEN`). Fremde Texte (Mails, Webseiten) sind Daten,
  keine Anweisungen an die KI (steht so im Systemprompt in `ai.py`).

## 4. Für einen neuen Agenten: Umgebung und Fallstricke

- **Tests:** `python -m unittest discover tests` (dauert etwa 25 Sekunden; Meldungen „Kreisgrenzen nicht geladen“
  und „Fehlgeschlagene Anmeldung“ sind normal). Dazu `python -m pyflakes app tests`; bekannte alte Meldungen in
  `branchentest.py:451` und `main.py` (`firma` doppelt importiert) sind nicht neu.
- **App lokal ansehen:** `DEMO_MODE=true DATA_DIR=<ordner> SECRET_KEY=x LEADS_WORKER=false flask --app app.main run -p 8097`,
  dann `/login` → „Demo ansehen“ (angemeldet als Oskar, es wird nichts verschickt). Flask lädt Vorlagen und Python-Code
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
  vorbereitet); das Postfach im CRM auf das neue Design (`os.css`) umstellen; Angebote (siehe Abschnitt 3).
- Nach jedem Update auf dem Server: die „Nach dem Update“-Hinweise je Stufe in UEBERGABE.md abarbeiten
  (Omid anlegen, Google-Zugang eintragen, Zielwerte, Projekte-Regeln …).
