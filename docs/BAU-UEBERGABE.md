# Übergabe für den Weiterbau (ab Stufe 10)

**Stand:** 01.11.2026 · Stufe 1 bis 9 gebaut und gemergt · nächste: **Stufe 10**

Diese Datei ist für die nächste Sitzung (Mensch oder KI), die am Firmen-OS weiterbaut. Sie fasst zusammen, was
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

**Vorschlag für den Umfang (mit Oskar abstimmen):**

1. **Fragen an die Firmendaten** (Chat), nur mit den Daten, die die fragende Person nach ihren Rechten sieht.
2. **Prioritäten für „Mein Tag“** als Vorschlag (überfällig, wartet auf Antwort, Fristen), nie automatisch.
3. **Entwürfe:** Antwort im Postfach, Briefing vor einem Termin, Angebot aus Anforderung und Schätzung, Case Study aus
   einem Projekt im Betrieb. Alles als Entwurf, Versand über die Freigabe-Stufen.
4. **Kosten und Grenzen** in den Einstellungen: welches Modell, Budget je Monat, was der Assistent lesen darf.
5. Protokoll: Jede Antwort des Assistenten mit Quelle (welche Daten), damit nachvollziehbar bleibt, woher sie kommt.

## 4. Offen und gut zu wissen

- **Google-Kalender** ist im Code fertig, aber noch nie mit echtem Google getestet (in der Entwicklungsumgebung
  gesperrt). Erster echter Test steht in UEBERGABE.md.
- **Offene Fragen** aus FIRMEN-OS.md Abschnitt 10: Status Hamza/Omid, Stunden pro Woche (für Terminvorschläge je
  Person), Buchungslink für Kunden, Zeitpunkt Kunden-Bereich, Name des OS.
- Nach jedem Update auf dem Server: die „Nach dem Update“-Hinweise je Stufe in UEBERGABE.md abarbeiten
  (Omid anlegen, Google-Zugang eintragen, Zielwerte, Projekte-Regeln …).
