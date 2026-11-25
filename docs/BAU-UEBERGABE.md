# Übergabe für den Weiterbau (Stufe 12: Outreach für Kunden)

**Stand:** 25.11.2026 · Stufe 1 bis 11 gebaut und gemergt (11: Agenten-Schnittstelle, [AGENTEN.md](AGENTEN.md)) · nächste: **Stufe 12 (Outreach für Kunden)**, Plan abgestimmt

Diese Datei ist für die nächste Sitzung (Mensch oder KI), die am Firmen-OS weiterbaut. **Wer neu einsteigt:**
erst `CLAUDE.md`, dann diese Datei ganz, dann Abschnitt 3 (Stufe 12, schon mit Oskar abgestimmt) bauen. Sie fasst zusammen, was
steht, wie hier gearbeitet wird und was als Nächstes sinnvoll ist. Ausführlich steht alles in
[FIRMEN-OS.md](FIRMEN-OS.md) (Bauplan, je Stufe „Umsetzung und Abweichungen“).

## 1. Arbeitsweise (bewährt in Stufe 1 bis 11)

1. `CLAUDE.md`, [FIRMEN-OS.md](FIRMEN-OS.md), [ROLLEN-UND-ABLAEUFE.md](ROLLEN-UND-ABLAEUFE.md) und
   [DESIGN.md](DESIGN.md) lesen, dann den betroffenen Code.
2. Kurzen Plan für die Stufe mit Oskar abstimmen. Oskar hat für Stufe 2 bis 10 gesagt: Plan kurz abstimmen, dann bauen,
   testen, im Browser ansehen, Doku nachziehen, Pull Request und mergen.
3. Bauen mit Tests: `python -m unittest discover tests` (Stand: 210 Tests, alle grün). Neue Tests für das OS
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
| 11 Agents (MCP) | `agenten.py` (Agenten-Konten, OAuth, Entwürfe, Zuständigkeit, Freigaben mit Ausführung, Infos), `mcp_server.py` (Protokoll, Werkzeuge), `agenten_prompts.py` | `/mcp/<key>`, `/oauth/*`, Einstellungen → Agents (Einrichten), Freigaben-Karten, Mein Tag → Von den Agents, Boards unter Kunden und Aufgaben |

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

## 3. Stufe 12: Outreach für Kunden (Arbeitsbereiche)

**Auftrag (Oskar, 25.11.2026):** „Umbauen, sodass wir auf einem Server sowohl für uns als auch für mehrere Kunden
Outreach machen können“; die Kunden bezahlen uns dafür. Mit Oskar abgestimmt (siehe auch FIRMEN-OS.md, Stufe 12):

| Frage | Entscheidung |
| --- | --- |
| Trennung | **Arbeitsbereich je Kunde**, eine Datenbank, Spalte `arbeitsbereich` (nicht je Kunde eine eigene Datenbank) |
| Absender | **Postfach des Kunden** je Arbeitsbereich; Antworten landen dort |
| Kundensicht | **Ja, im Kunden-Bereich**: Zahlen und interessierte Kontakte; Freigaben bleiben bei uns |

**Datenmodell (Vorschlag):**

- Neue Tabelle `arbeitsbereiche(key, name, projekt_id, aktiv, erstellt_am)`. Startwert `ylva` (Ylva Labs selbst),
  per DATA_MIGRATIONS anlegen; alle bestehenden Daten gehören zu `ylva`.
- Spalte `arbeitsbereich TEXT NOT NULL DEFAULT 'ylva'` (über `leaddb.MIGRATIONS`) an: `leads`, `runs`, `ai_usage`
  (Lead-Läufe), `tasks` (CRM-Wiedervorlagen), `signals`, `brain_versions`, `known_firms`, `run_results`, `notices`
  (nur Lead-Meldungen), `postfaecher`. `messages`, `lead_events` hängen über `lead_id` dran (kein eigenes Feld nötig).
- Bisher globale Werte je Arbeitsbereich: Gehirn (`brain_versions`), Nischen und Suchgebiet (heute `config_store`:
  `LEADS_REGION`, `LEADS_CENTER`, `LEADS_RADIUS_KM`, `LEADS_RULE_MIN` …), `blocklist`, `inbox_state`, `schedule`
  (automatische Suche), Vorlagen. Muster: kv-Schlüssel mit Präfix `ab:<key>:` und Fallback auf den globalen Wert.
- Akquise-Postfach je Arbeitsbereich: für `ylva` weiter `OUTREACH_USER`/`OUTREACH_PASSWORD`; für Kunden ein
  Eintrag in `postfaecher` mit `arbeitsbereich` und Kennzeichen „Akquise“. `OUTREACH_EMAIL` je Arbeitsbereich
  (Standard aus, bis der Kunde bestätigt, dass er Erstkontakt per Mail will).

**Umsetzung in Schritten (je Schritt Tests grün, dann weiter):**

1. **Arbeitsbereich wählen:** Auswahl in der Navigation (nur sichtbar, wenn es mehr als einen gibt), gespeichert in
   der Sitzung; `g.arbeitsbereich`. Rechte: neues Modul oder Feld „Arbeitsbereiche“ je Person (wer für welchen
   Kunden arbeitet), Verwalten in Einstellungen → Arbeitsbereiche.
2. **Leads und CRM filtern:** Jede Abfrage auf `leads` (rund 80 Stellen, `grep -n "FROM leads\|UPDATE leads\|INTO leads" app/*.py`)
   bekommt den Arbeitsbereich. Am sichersten über Hilfsfunktionen in `leaddb` (z. B. `leads_where(c, ab)`) statt
   überall von Hand; neue Leads bekommen den Arbeitsbereich beim Anlegen. Test: Lead aus A ist in B nie sichtbar
   (Liste, Akte, Suche, Palette, CRM, Postfach, Export, KPIs).
3. **Hintergrund-Arbeiter:** Läufe, automatische Suche, Postfach-Abruf, Nachfassen und Lernen je Arbeitsbereich
   (Schleife über aktive Arbeitsbereiche in `agent.tick`), Budget je Arbeitsbereich (KI-Kosten an den Kunden
   weiterberechenbar).
4. **Gehirn, Vorlagen, Suchgebiet, Nischen, Sperrliste je Arbeitsbereich** in den Einstellungen; Absender-Postfach
   des Kunden; Unterschrift im Namen des Kunden.
5. **Kunden-Bereich:** neuer Abschnitt „Outreach“ für Kunden mit Arbeitsbereich: angeschrieben, Antworten,
   Interesse, Termine je Woche und die interessierten Kontakte (ohne interne Notizen).
6. **Abrechnung:** Arbeitsbereich mit Projekt verknüpfen; Monatsrechnung um eine Position „Outreach“ und optional
   KI-Kosten ergänzen.
7. Doku: FIRMEN-OS.md Stufe 12 abhaken mit „Umsetzung und Abweichungen“, README, UEBERGABE („Nach dem Update“).

**Agents in Stufe 12:** Seit Stufe 11 arbeiten die Grok-Bots über MCP im OS ([AGENTEN.md](AGENTEN.md)). Für
Arbeitsbereiche brauchen die Werkzeuge (`app/mcp_server.py`) den Arbeitsbereich: am einfachsten ein Feld
„Arbeitsbereiche“ am Agenten-Konto (wie bei Personen) und jede Abfrage auf Leads über die neue Hilfsfunktion
filtern. Test: Ein Agent eines Kunden sieht nie Leads aus dem Arbeitsbereich `ylva`.

**Neue Werkzeuge für Agents:** Jede neue Funktion, die Agents brauchen, bekommt einen Eintrag in
`mcp_server.WERKZEUGE` (deutsche Beschreibung, wann man es nutzt, Modul und Stufe). Schreibende Werkzeuge laufen
über eine Funktion, die `firma.log` mit dem Agent als Urheber schreibt. Was nach außen geht, nie direkt: Entwurf +
`agenten.anfragen`.

**Achtung:** Rechtlich ist der Kunde der Absender. Vor dem ersten Kunden mit dem Anwalt klären: AV-Vertrag
(wir verarbeiten Kontaktdaten im Auftrag), Erstkontakt per Mail im Namen des Kunden (§ 7 UWG), Impressum und
Abmeldelink in den Mails des Kunden.

**Danach** (aus der Übergabe nach Stufe 10, weiter gültig): Praxistest im Alltag, Assistent ausbauen, Unipile,
restliche alte Seiten auf `os.css`.

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
