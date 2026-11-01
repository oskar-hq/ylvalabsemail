# Übergabe für den Weiterbau (ab Stufe 6)

**Stand:** 01.11.2026 · Stufe 1 bis 5 gebaut und gemergt (PRs #41 bis #44) · nächste: **Stufe 6**

Diese Datei ist für die nächste Sitzung (Mensch oder KI), die am Firmen-OS weiterbaut. Sie fasst zusammen, was
steht, wie hier gearbeitet wird und was in Stufe 6 zu tun ist. Ausführlich steht alles in
[FIRMEN-OS.md](FIRMEN-OS.md) (Bauplan, je Stufe „Umsetzung und Abweichungen“).

## 1. Arbeitsweise (bewährt in Stufe 1 bis 5)

1. `CLAUDE.md`, [FIRMEN-OS.md](FIRMEN-OS.md), [ROLLEN-UND-ABLAEUFE.md](ROLLEN-UND-ABLAEUFE.md) und
   [DESIGN.md](DESIGN.md) lesen, dann den betroffenen Code.
2. Kurzen Plan für die Stufe mit Oskar abstimmen. Oskar hat für Stufe 2 bis 5 gesagt: selbst entscheiden, bauen,
   danach Pull Request und mergen. Für Stufe 6 gilt das, sofern er nichts anderes sagt.
3. Bauen mit Tests: `python -m unittest discover tests` (Stand: 153 Tests, alle grün). Neue Tests für das OS
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
- **Takt:** Hintergrund-Arbeiter `agent.tick()` ruft `kalender.takt`, `kpis.takt`, `projekte.takt` auf (je mit
  eigenem try/except).
- **Design:** Bausteine in `static/os.css`, `static/os.js`, `templates/_os.html` (Listenzeilen, Sheets mit
  `data-sheet`, schwebende Knöpfe `os-float`, Formulare mit `data-os-fetch="reload"`, Toasts per `flash`).

## 3. Stufe 6: Freigabe-Zentrale und Regeln als Daten

Aus dem Bauplan: *Alle Vorschläge in einer Freigabe-Warteschlange, Freigabe-Stufen und Entscheidungsrechte
einstellbar, Entscheidungs-Log.* Dazu, was in früheren Stufen ausdrücklich auf Stufe 6 verschoben wurde.

**Ausgangslage:** Die Tabelle `freigaben` gibt es schon (Abschnitt 5 im Bauplan). Darin liegen heute drei Arten:

| art | angelegt in | entschieden in | prüft |
|---|---|---|---|
| `lead_pruefung` | `branchentest.eingang` | `branchentest.entscheiden` | Akquise-Kette (Omid → Hamza → Oskar) |
| `termin_einladung` | `kalender.anlegen` (Kundentermin) | `kalender.freigeben` | verantwortliche Person des Termins |
| `lernstufe` | `kpis.aufstieg_pruefen` (täglicher Takt) | `kpis.aufstieg_entscheiden` | führende Person |

Dazu Freigaben, die noch nicht über `freigaben` laufen: Anforderung im Projekt (zwei Felder in `projekte`),
Entwürfe von Erstkontakten (Leads im Status `entwurf`, Reiter „Zur Freigabe“).

**Vorschlag für den Umfang (mit Oskar abstimmen):**

1. **Freigabe-Zentrale** (Seite „Freigaben“): alle offenen Freigaben, die auf mich warten, aus allen Arten an
   einer Stelle, mit Annehmen/Ablehnen (Grund) direkt dort. Ein gemeinsames Modul (z. B. `app/freigaben.py`)
   mit Registry je Art (Titel, Link, Entscheiden-Funktion), damit neue Arten nur Daten sind (G10). Zähler in der
   Navigation und auf „Mein Tag“ (die einzelnen Abschnitte dort können dann zusammengefasst werden).
2. **Freigabe-Stufen A–D je Aktion als Einstellung** (Abschnitt 3 im Bauplan): Tabelle oder `kv` `regel:stufen`
   mit Aktionen (Termin-Einladung an Kunden, Erinnerung an Kunden, Wochenbericht, Lead aussortieren …) und ihrer
   Stufe. Hart im Code und nicht abschaltbar bleiben: Erstkontakt per Mail gesperrt (`OUTREACH_EMAIL`, § 7 UWG),
   Sperrliste, nichts an Kunden ohne Freigabe solange C/D. Stufe B („mit Einspruch“) braucht im Takt eine Frist.
3. **Entscheidungsrechte als Daten** (Tabelle aus den Rollen, inkl. Betragsgrenze 200 € für Ausgaben durch das
   Gründerteam, Kundenversprechen mit Technikzeit nur mit Oskar und Finn).
4. **Entscheidungs-Log** (Tabelle `entscheidungen`: wer, was, warum, Grundlage), erfassbar im Wochengespräch;
   Änderungen an Regeln erzeugen automatisch einen Eintrag. In der Agenda (Stufe 4) gibt es schon den Platz
   „Offene Entscheidungen“ mit Hinweis auf Stufe 6.
5. **Notfall-Knopf** (Abschnitt 4.2, aus Stufe 1 verschoben): nur für die drei Notfälle aus den Rollen
   (Kundensystem steht, Kundendaten in Gefahr, Abschluss platzt heute), Push an die richtige Person, mit Start
   und Ende. Liefert die KPI „Ausfallzeit“ (`kpis.KATALOG`, Eintrag `ausfallzeit`, heute `kommt_mit=6`) und die
   „Notfälle der Woche“ für die Agenda des Wochengesprächs.

## 4. Offen und gut zu wissen

- **Google-Kalender** ist im Code fertig, aber noch nie mit echtem Google getestet (in der Entwicklungsumgebung
  gesperrt). Erster echter Test steht in UEBERGABE.md.
- **Offene Fragen** aus FIRMEN-OS.md Abschnitt 10: Status Hamza/Omid, Stunden pro Woche (für Terminvorschläge je
  Person), Buchungslink für Kunden, Zeitpunkt Kunden-Bereich, Name des OS.
- Nach jedem Update auf dem Server: die „Nach dem Update“-Hinweise je Stufe in UEBERGABE.md abarbeiten
  (Omid anlegen, Google-Zugang eintragen, Zielwerte, Projekte-Regeln …).
