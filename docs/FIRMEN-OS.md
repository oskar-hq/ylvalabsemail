# Ylva Labs Firmen-OS – Bauplan

**Stand:** 01.11.2026 · alle zehn Baustufen gebaut · gehört zu [ROLLEN-UND-ABLAEUFE.md](ROLLEN-UND-ABLAEUFE.md)

Aus dem Ylva Labs Mailer (Akquise, CRM, Mailer) wird das Betriebssystem der Firma. Diese Datei hält fest, **was**
gebaut werden soll und **nach welchen Prinzipien**, damit alle, die am Code arbeiten (Menschen und KI), dasselbe
Bild im Kopf haben. Sie ist der Bauauftrag, auf den die Rollen-Datei verweist. Wer eine Baustufe umsetzt, hakt sie
hier ab und trägt Abweichungen ein.

## 0. Start für den Bau (hier anfangen)

**Was entschieden ist** (Planungsgespräch mit Oskar, 01.11.2026):

- Leitbild, Grundsätze G1–G10, Freigabe-Stufen und Module wie in dieser Datei.
- **Alles in den Einstellungen, nie auf den Server** (G9, Abschnitt 4.13). Gilt für jede Zeile Code.
- Die App läuft bereits unter `https://mail.ylvalabs.de`. Updates kommen per Knopf in der App.
- Personen und Rechte verwalten dürfen **Oskar und Finn**. Hamza und Omid nicht, bis ihr Status geklärt ist.
- Kalender: Jede Person verbindet ihr **eigenes** Google-Konto (alle haben private Google-Konten). Google-Projekt
  ist eingerichtet und in Produktion (4.3). Apple/iCloud und ICS-Abo erst danach.
- WhatsApp kommt später über Unipile, nicht jetzt.

**Stand des Baus:** Stufe 1 bis 10 sind gebaut. Was danach kommt (Ausbau, offene Punkte), steht in
[BAU-UEBERGABE.md](BAU-UEBERGABE.md).

**Womit der Bau anfing: Stufe 1** (Abschnitt 8). Der Kalender ist Stufe 3. Für Stufe 1 und 2 wird nichts von
Google gebraucht.

**Arbeitsweise für jede Baustufe:**

1. Diese Datei, [ROLLEN-UND-ABLAEUFE.md](ROLLEN-UND-ABLAEUFE.md), `CLAUDE.md` und den betroffenen Code lesen.
2. Vor dem Bauen einen kurzen Plan für die Stufe mit Oskar abstimmen (was genau, welche Seiten, welche Tabellen).
3. Bauen mit Tests (`python -m unittest discover tests`). Bestehende Funktionen (Leads, CRM, Mailer, Einstellungen,
   Updates, Sicherung) dürfen nicht kaputtgehen; Daten auf dem Server werden nur per `MIGRATIONS` erweitert, nie
   gelöscht.
4. Jede neue Funktion bringt ihre Einstellungen mit. Nichts darf eine Änderung in der `.env` oder auf dem Server
   voraussetzen.
5. Stufe hier abhaken, Abweichungen in dieser Datei eintragen, README/UEBERGABE nachziehen, Pull Request.

Noch offen, aber **kein Hindernis** für den Bau (siehe Abschnitt 10): Status von Hamza und Omid, Stunden pro Woche,
Buchungslink für Kunden, Zeitpunkt Kunden-Bereich, Name des OS. Alles davon wird später Einstellung oder betrifft
spätere Stufen.

---

## 1. Leitbild: Das Programm ist die Firma

> Das Firmen-OS ist nicht ein Werkzeug, das die Firma benutzt. **Das Firmen-OS ist die Firma.**
> Die Menschen sind seine Angestellten.

Das Programm kennt alle Kunden, Aufgaben, Termine, Zahlen und Regeln. Es verteilt die Arbeit, erinnert, misst und
erledigt selbst, was es sicher allein erledigen kann. Menschen übernehmen genau zwei Arten von Arbeit:

1. **Arbeit, bei der das Programm sicher sein muss, dass sie richtig ist** (Human in the Loop): Das Programm
   bereitet vor, ein Mensch prüft und gibt frei. Beispiele: eine Nachricht an einen Kunden, ein Preis, eine Abnahme.
2. **Arbeit, die nur im echten Leben geht**: Gespräche führen, telefonieren, beim Kunden vor Ort sein, Verträge
   unterschreiben, Entscheidungen mit Verantwortung treffen.

Alles andere macht die Software: Daten erfassen, Termine verschicken, Links erzeugen, nachfassen, Berichte und
KPIs ausrechnen, Aufgaben anlegen, Fristen überwachen.

### Die Grundsätze

| # | Grundsatz | Was das im Code heißt |
|---|---|---|
| G1 | **Was nicht im OS steht, ist nicht passiert.** | Jede Aufgabe, jeder Termin, jede Kundennachricht, jede Entscheidung hat einen Datensatz. „Kannst du mal kurz?“ wird zur Aufgabe im OS. |
| G2 | **Eine Fläche für alles.** | Kunden, Aufgaben, Kalender, Nachrichten, Meet-Links, Dokumente und Zahlen sind in einer App erreichbar. Kein Wechsel zu WhatsApp, Notizzettel oder privater Liste. |
| G3 | **Erfassen nebenbei, nicht extra.** | KPIs entstehen aus dem, was ohnehin passiert (Aufgabe erledigt, Termin stattgefunden, Mail gesendet, Status geändert). Niemand pflegt Zahlen von Hand, wenn das OS sie selbst zählen kann. |
| G4 | **Jede Aufgabe hat genau einen Bereich, eine Person und einen Termin.** | Pflichtfelder. Ohne Person landet sie beim Bereichsverantwortlichen. |
| G5 | **Die Software schlägt vor, der Mensch gibt frei.** | Alles, was nach außen geht oder Geld/Versprechen bedeutet, durchläuft eine Freigabe (siehe Abschnitt 3). |
| G6 | **Alles ist nachvollziehbar.** | Jede Änderung erzeugt ein Ereignis im Firmen-Protokoll: wer, wann, was, warum. Daraus entstehen KPIs, Berichte und die Case Studys. |
| G7 | **Regeln stehen im OS, nicht in Köpfen.** | Entscheidungsrechte, Lernstufen, Preise, Abnahme-Checklisten und Bewertungsregeln sind Daten im OS und werden dort durchgesetzt. |
| G8 | **Kostenlos und regelbasiert zuerst, KI wo sie wirklich hilft.** | Wie bisher beim Lead-Scoring: feste Regeln, sichtbare Kosten, Budgets als harte Grenze. |
| G9 | **Alles in den Einstellungen, nie auf den Server.** | Das OS wird bedient wie eine normale App. Schlüssel, Zugänge, Personen, Rechte, Regeln, Anbindungen und Updates ändert man in der Web-App. Niemand muss für den Alltag per Konsole auf den Server, in die `.env` oder in den Code. Siehe 4.13. |
| G10 | **Neue Fälle sind Daten, kein Code.** | Neue Person, neuer Bereich, neue Rolle, neue Rechte, neue Testbranche, neue Vorlage: alles in den Einstellungen anlegbar. Code ändert sich nur, wenn eine wirklich neue Funktion gebraucht wird. |

---

## 2. Die Bausteine (Kernbegriffe)

Diese Begriffe gelten im Code, in der Oberfläche und in Gesprächen gleich.

| Begriff | Bedeutung | Gibt es schon? |
|---|---|---|
| **Person** | Mitarbeitende mit Login, Bereich(en), Rolle, Lernstufe, Kalender-Link | ja (`team` mit Rolle, Status, Einladung; Stufe 1) |
| **Bereich** | Produkt & Technik · Markt, Kunden & Betrieb · Unterbereich Akquise & Recherche. Hat eine verantwortliche Person und KPIs. Später teilbar. | ja (`bereiche`, ohne KPIs; Stufe 1) |
| **Betrieb / Kunde** | Eine Firma vom ersten Lead bis zum laufenden Kunden. *Eine* Akte über die ganze Customer Journey. | ja als Lead (`leads`) |
| **Projekt (Build)** | Was für einen Kunden gebaut wird: Anforderung → Bau → Abnahme → Live | ja (`projekte`; Stufe 5) |
| **Änderungswunsch** | Wunsch eines Kunden nach dem Start, mit Größe, Zeit, Abrechnung | ja (`aenderungen`; Stufe 5) |
| **Aufgabe** | Etwas, das eine Person bis zu einem Termin erledigen muss. Kann an Kunde, Projekt, Lead hängen oder frei sein. | ja (`aufgaben`, inkl. Anfragen und Wiedervorlagen; Stufe 1) |
| **Termin** | Zeitpunkt mit Teilnehmenden, optional Kunde und Meet-Link | ja (`termine`; Stufe 3) |
| **Freigabe** | Ein Vorschlag des OS, den ein Mensch annehmen, ändern oder ablehnen muss | nur implizit (Entwürfe) |
| **Ereignis** | Eintrag im Firmen-Protokoll. Rohstoff für alle Zahlen. | ja (`ereignisse`, `firma.log`; Stufe 1) |
| **KPI** | Kennzahl je Bereich/Person, aus Ereignissen berechnet | ja (`app/kpis.py`; Stufe 4) |
| **Ziel** | Wochenziel einer Person (z. B. Hamza: 40 qualifizierte Leads) | ja (`ziele`; Stufe 4) |
| **Entscheidung** | Festgehaltene Entscheidung mit Begründung, wer entschieden hat und auf welcher Grundlage | nein |
| **Dokument** | Angebot, AV-Vertrag, Anforderung, Abnahme-Protokoll, Rechnung, Vorlage | nein |
| **Geldbewegung** | Rechnung, Zahlung, laufende Kosten, KI-Kosten je Kunde | nur KI-Kosten (`ai_usage`) |

---

## 3. Human in the Loop: die Freigabe-Stufen

Jede Aktion, die das OS ausführen kann, hat eine von vier Stufen. Die Stufe ist eine Einstellung im OS (nicht fest
im Code), damit sie mit wachsendem Vertrauen hochgestuft werden kann.

| Stufe | Name | Ablauf | Beispiele |
|---|---|---|---|
| A | **Automatisch** | OS macht es, nur Protokoll | KPIs berechnen, Erinnerungen an das Team, Aufgabe aus Ereignis anlegen, Kalender-Feed, Termin-Erinnerung an Kunden 24 h vorher |
| B | **Automatisch mit Einspruch** | OS kündigt an, führt nach Frist aus, wenn niemand stoppt | Wochenbericht verschicken, Wiedervorlage verschieben, Lead aussortieren |
| C | **Vorschlag + Freigabe** | OS bereitet vor, Mensch klickt „Freigeben“ (oder ändert) | Nachricht an Kunden/Lead, Termin-Einladung an Kunden, Angebot, Rechnung, Abnahme |
| D | **Nur Mensch** | OS legt eine Aufgabe an und sammelt Unterlagen | Preisverhandlung, Vertrag unterschreiben, Anruf, Vor-Ort-Termin, Einstellung |

**Wer freigeben darf**, ergibt sich aus der Entscheidungstabelle und den Lernstufen in
[ROLLEN-UND-ABLAEUFE.md](ROLLEN-UND-ABLAEUFE.md). Das OS setzt das durch:

- Hamza/Omid auf Lernstufe 1: jeder Lead geht in Oskars Prüf-Warteschlange.
- Lernstufe 2: Entwürfe immer zu Oskar, Leads nur als Stichprobe (z. B. jeder fünfte, einstellbar).
- Lernstufe 3: Outreach selbst freigeben; Rechtsregeln (§ 7 UWG, Sperrliste) bleiben hart im Code.
- Kundenversprechen mit Technikzeit: Freigabe braucht **Oskar und Finn** (Finns Aufwandsschätzung ist Pflichtfeld).
- Ausgaben ab 200 €: Freigabe durch das Gründerteam.

**Lernstufen misst das OS selbst:** Es zählt angenommene Leads, Entwürfe ohne große Änderung (Textabstand
zwischen Entwurf und Freigabe), gebuchte Termine und begleitete Gespräche, und schlägt den Aufstieg vor, wenn die
Bedingung 4 Wochen lang erfüllt ist. Den Aufstieg selbst gibt Oskar frei (Stufe C).

**Hart im Code, nie per Einstellung abschaltbar:** Erstkontakt per Mail gesperrt, solange § 7 UWG nicht geklärt
ist (`OUTREACH_EMAIL`); Sperrliste nach Abmeldung; nichts an Kunden ohne Freigabe der zuständigen Person, solange
die Aktion auf Stufe C/D steht.

---

## 4. Die Module

Jedes Modul baut auf dem Firmen-Protokoll (Ereignisse) und den Aufgaben auf. Reihenfolge der Umsetzung siehe
Abschnitt 8.

### 4.1 Start: „Mein Tag“

Die erste Seite nach dem Login, für jede Person anders. Das OS sagt der Person, was sie heute tun soll:

- Freigaben, die auf mich warten (mit Zähler in der Glocke)
- Meine Aufgaben, sortiert nach Fälligkeit und Priorität, überfällige oben
- Meine Termine heute, mit Meet-Link und Kunden-Briefing direkt daneben
- Meine Wochenziele und KPIs mit Stand („22 von 40 Leads“)
- Fokus-Blöcke: Wer gerade im Fokus ist, wird für andere als „bitte nicht stören, ins OS eintragen“ angezeigt

### 4.2 Aufgaben

Ersetzt jede Liste, jedes „kannst du mal kurz“ und jede WhatsApp-Bitte.

- Pflicht: Titel, Bereich, verantwortliche Person, Termin. Optional: Kunde, Projekt, Lead, Priorität, Schätzung,
  Checkliste, Anhänge.
- **Anfragen an andere Bereiche:** Wer etwas von einem anderen Bereich braucht, legt eine Anfrage an. Sie landet
  beim Bereich, die verantwortliche Person priorisiert (annehmen, Termin setzen, ablehnen mit Grund).
- **Notfall-Knopf:** Nur für die drei Notfälle aus den Rollen (Kundensystem steht, Kundendaten in Gefahr, Abschluss
  platzt heute). Löst Push an die richtige Person aus und wird im Wochengespräch ausgewertet, damit er nicht
  missbraucht wird.
- **Automatisch angelegte Aufgaben** aus Ereignissen, z. B.:
  - Angebot unterschrieben → „AV-Vertrag abschließen“ (Oskar), „Anforderungstermin buchen“ (Oskar)
  - Anforderung vom Kunden bestätigt → „Bau starten“ (Finn) mit Termin aus der Schätzung
  - Hälfte der Bauzeit vorbei → „Zwischenstand zeigen“ (Finn)
  - Build fertig → „Abnahme-Checkliste durchgehen“ (Oskar)
  - Live → wöchentlicher Check-in als Serientermin, „Baseline-Zahlen erfassen“
  - Lead ohne Antwort nach 7 Tagen → „Nachfassen“ (gibt es schon)
- Die bisherigen Wiedervorlagen (`tasks`) werden zu normalen Aufgaben mit Bezug auf einen Lead.

### 4.3 Kalender und Termine

Das OS ist die Quelle für alle Firmentermine und plant um die privaten Termine jeder Person herum.

**Kalender verbinden (Entscheidung 01.11.2026):** Jede Person verbindet in ihrem Profil einmal ihren eigenen
Kalender, dort wo sie ihn ohnehin führt.

| Konto | Was das OS darf | Wie |
|---|---|---|
| **Google** (Standard, Oskar nutzt Google) | Frei/belegt lesen, Firmentermine direkt eintragen, **Meet-Links erzeugen** | „Mit Google verbinden“ (OAuth), Google Kalender API |
| **Apple / iCloud** | Frei/belegt lesen, Firmentermine eintragen, kein Meet | iCloud-Kalender über CalDAV mit app-spezifischem Passwort |
| Ohne Verbindung | nur Firmentermine anzeigen | ICS-Abo (siehe unten) |

- **Privatsphäre:** Aus privaten Kalendern liest das OS nur **frei/belegt**, keine Titel, Orte oder Teilnehmer
  (Google-Berechtigung nur für Frei/Belegt plus Termine anlegen). Für andere sieht ein privater Termin nur wie
  „belegt“ aus.
- **Planen um alle herum:** Das OS kennt so die freien Zeiten aller. Es schlägt Termine vor (Wochengespräch,
  Kundentermin mit Oskar und Finn), plant Fokus-Blöcke und zeigt Kunden im Buchungslink nur echte freie Zeiten.
- **Firmentermine landen direkt im eigenen Kalender** der Teilnehmenden (bei Google sofort, nicht erst beim
  nächsten Abo-Abgleich). Änderungen im OS werden dort nachgezogen.
- **Kundentermine mit Meet:** Termin im OS anlegen (oder Kunde bucht selbst über den Buchungslink) → das OS legt
  den Termin **im Google-Kalender der zuständigen Person** an, Google erzeugt den Meet-Link, und Google schickt die
  Einladung an den Kunden. Der Link steht auch in der Akte, in „Mein Tag“ und im Kunden-Bereich. Erinnerungen
  24 h und 1 h vorher, Briefing für die zuständige Person.
- **ICS-Abo als Rückfall:** eine geheime Adresse (`/kalender/<token>.ics`) für alle, die nichts verbinden wollen.
  Nur lesend; Token in der App erneuerbar.
- **Firmenkalender:** alle Firmentermine aller Personen, filterbar nach Person/Bereich/Kunde.
- **Nach dem Termin:** OS fragt die zuständige Person nach Ergebnis (stattgefunden / abgesagt / nicht erschienen)
  und Notiz, schiebt den Betrieb in der Pipeline weiter und legt Folgeaufgaben an. Das ist die Quelle für die KPIs
  „Termine pro Woche“ und „Abschlussquote“.
- **Wochengespräch zu viert:** Serientermin, Agenda erzeugt das OS (siehe 4.9).

**Technik und Stolpersteine bei Google:**

- Einmalig ein Projekt in der Google Cloud Console anlegen (kostenlos), Google Kalender API einschalten,
  OAuth-Zugang vom Typ „Webanwendung“ mit Rücksprung-Adresse `https://mail.ylvalabs.de/kalender/google/zurueck`.
  Kein Google Workspace nötig, normale Google-Konten reichen, und Meet-Links gehen damit auch.
- **Client-ID und Clientschlüssel trägt man in der App ein** (Einstellungen → Anbindungen → Google), nicht in der
  `.env` (G9). Die App zeigt dort auch die Rücksprung-Adresse zum Kopieren an.
- Berechtigungen (nur diese zwei): `calendar.events.freebusy` (frei/belegt, gilt als nicht vertraulich) und
  `calendar.events` (Termine anlegen mit Meet-Link und Einladung).
- **Zielgruppe „Intern“ oder „Extern“:** „Intern“ geht nur, wenn das Projekt zu einer Google-Organisation
  (Workspace) gehört, und dann können sich nur Konten dieser Organisation verbinden, ohne Prüfung und ohne Ablauf.
  Haben Teammitglieder private Gmail-Konten, muss die App auf „Extern“ stehen. Dann gilt:
- Bei „Extern“ gelten die Kalender-Berechtigungen als „sensibel“. Solange die App nicht von Google geprüft ist:
  - Im Modus **„Testing“** laufen die Anmeldungen **nach 7 Tagen ab**, alle müssten sich jede Woche neu verbinden.
    Deshalb nicht im Testmodus lassen.
  - Im Modus **„In production“ ohne Prüfung** zeigt Google beim Verbinden einmal den Hinweis „App nicht
    überprüft“ (über „Erweitert“ weiter), danach bleibt die Verbindung bestehen. Grenze: 100 Personen. Für das
    Team reicht das.
  - Eine Google-Prüfung lohnt sich erst, wenn Kunden selbst ihre Kalender verbinden sollen.
- Die Zugangsschlüssel (Refresh-Token) werden verschlüsselt in der Datenbank gespeichert (`cryptography` ist schon
  installiert). In der App gibt es „Verbindung trennen“, und Google zeigt die Verbindung auch unter
  myaccount.google.com → Sicherheit.
- Der Hintergrund-Arbeiter gleicht frei/belegt regelmäßig ab (z. B. alle 10 Minuten) und vor jedem Terminvorschlag.

**Stand der Google-Einrichtung (01.11.2026, Oskar):** Projekt „Firmen OS“ angelegt, Google Calendar API aktiviert,
beide Berechtigungen eingetragen, OAuth-Client (Webanwendung) erstellt, JSON-Datei mit dem Clientschlüssel bei
Oskar gespeichert (nie ins Repository, nie in Chats). Branding ausgefüllt. Zielgruppe **„Extern“**,
Veröffentlichungsstatus **„In Produktion“**, **nicht** zur Überprüfung eingereicht (bewusst: Google zeigt beim
Verbinden einmal „App nicht überprüft“, Grenze 100 Personen über die ganze Lebensdauer des Projekts, Verbindungen
laufen nicht ab). Der gelbe Hinweis „Die Anwendung muss überprüft werden“ in der Console wird ignoriert.
Rücksprung-Adressen im Client: `https://mail.ylvalabs.de/kalender/google/zurueck` und
`http://localhost:8080/kalender/google/zurueck`. Die Kalender-Anbindung muss genau diese Pfade verwenden.
Gebaut (Stufe 3): Client-ID und Clientschlüssel trägt man unter Einstellungen → Google Kalender ein.

### 4.4 Akquise und Recherche (gibt es schon, wird erweitert)

Was heute „Leads“ und „CRM“ ist, bleibt der Kern dieses Moduls.

- **Branchentest:** die vier Testbranchen als feste Gruppen mit je 50 Betrieben, Zuordnung zu Hamza/Omid,
  Herkunft **warm** (Oskars Kontakte, zählen nicht mit) oder **kalt**. Auswertung je Branche: Kontakte,
  Antworten, Gespräche, **ernsthafte Preisgespräche**, Abschlüsse. Ampel bei 3 ernsthaften Preisgesprächen.
- **Lead-Prüfung:** Leads von Omid gehen erst an Hamza, Stichproben an Oskar. Jede Prüfung ist eine Freigabe mit
  Ergebnis (angenommen/abgelehnt + Grund). Daraus die KPI „Anteil angenommener Leads“.
- **Briefing vor Gesprächen:** Zu jedem gebuchten Termin erstellt das OS ein Briefing aus Akte, Webseite und
  Bewertung; Hamza/Omid ergänzen und geben frei.
- **Kontaktwege:** Brief, Telefon (Anruf protokollieren mit einem Klick), Mail erst nach Antwort, LinkedIn,
  später WhatsApp über Unipile. Jeder Kontakt ist ein Ereignis.

### 4.5 Kunden und Projekte (die Customer Journey)

Die Pipeline endet heute bei „Kunde“. Ab dort geht es im OS weiter, in derselben Akte.

| Schritt | Status im OS | Was das OS automatisch macht | Freigabe |
|---|---|---|---|
| Lead und Termin | `lead` → `termin` | Briefing, Einladung mit Meet-Link, Erinnerungen | Termin-Einladung: C |
| Erstgespräch, Angebot | `angebot` | Angebot aus Vorlage mit Preisstufe, Versand, Nachfassen | Angebot: C (Oskar) |
| Unterschrieben | `beauftragt` | AV-Vertrag anfordern, Rechnung Einmalbetrag vorbereiten, Anforderungstermin | Rechnung: C |
| Anforderungen klären | `anforderung` | Anforderungsdokument aus Vorlage, Finn als Teilnehmer, Aufwandsschätzung als Pflichtfeld | Kunde bestätigt, Oskar + Finn |
| Bauen | `bau` | Zeiterfassung läuft, Zwischenstand-Termin zur Hälfte | – |
| Abnahme | `abnahme` | Abnahme-Checkliste; nicht alles abgehakt → zurück zu `bau` | Oskar hat das letzte Wort (D) |
| Live, Onboarding | `live` | Einweisungstermin, erste Freigabeliste, 30-Tage-Frist für kostenlose Anpassungen | – |
| Im Betrieb | `betrieb` | wöchentlicher Check-in als Serie, Zahlen erfassen, Pauschale abrechnen | Rechnung: C → später B |
| Case Study | – | Zahlen vorher/nachher sammeln, Entwurf der Case Study | C (Oskar), Einverständnis des Kunden Pflicht |

Jeder Wechsel ist ein Ereignis mit Zeitstempel. Daraus kommen „Tage bis live“, „Freigaben ohne Änderung“,
„Stunden pro Build“.

### 4.6 Änderungswünsche und Zeiterfassung

Setzt die Regeln für Änderungswünsche aus den Rollen um.

- **Eine einzige Stelle:** Kunden schicken Wünsche über ein Formular im Kunden-Bereich (4.7) oder an eine feste
  Adresse; das OS legt daraus einen Änderungswunsch an. Nie direkt an Finn.
- Oskar ordnet ein: Vorrang, klein (< 1 h) oder groß. Finn schätzt.
- **Abrechnung automatisch nach Regel:** in den ersten 30 Tagen nach Live inklusive; danach klein = Pauschale,
  gesammelt und einmal pro Woche umgesetzt; groß = eigenes Angebot.
- **Zeiterfassung:** Start/Stopp an Aufgabe, Projekt oder Änderung, oder nachträglich eintragen. Pflicht für
  Finn bei Builds und Änderungen.
- **Warnung:** Liegt die echte Zeit pro Kunde und Monat dauerhaft über der Annahme im Preismodell (1 h), meldet
  das OS das an Oskar und Finn als Entscheidungsvorlage (Pauschale oder Regeln anpassen).

### 4.7 Kunden-Bereich (Portal)

Ein eigener Zugang für jeden Kunden, damit auch Kunden-Kommunikation im OS bleibt.

- Termine mit Meet-Links, Projektstand, Abnahme-Checkliste, Dokumente (Angebot, AV-Vertrag, Rechnungen)
- Änderungswünsche stellen und ihren Stand sehen
- Zahlen aus dem Betrieb (eingesparte Zeit), die auch in die Case Study gehen
- Anmeldung per Einmal-Link an die Mailadresse, kein Passwort nötig

### 4.8 Kommunikation: ein Posteingang

Ziel: keine Firmenkommunikation mehr außerhalb des OS.

- **Heute schon:** Mails über das Akquise-Postfach (IMAP/SMTP), Zuordnung zu Leads, Einordnung nach Stichworten.
- **Erweiterung:** gemeinsamer Posteingang für alle Kundenpostfächer, jede Nachricht an einer Akte, zuständige
  Person, „wartet auf Antwort seit“.
- **Intern:** Kommentare an Aufgaben, Kunden und Projekten statt Chat. @-Erwähnung erzeugt eine Meldung in der
  Glocke. Kein allgemeiner Chat-Kanal: Wer etwas will, legt eine Aufgabe oder einen Kommentar am richtigen Ding an.
- **Später, sobald Budget da ist:** WhatsApp (und LinkedIn-Nachrichten) über **Unipile** in denselben Posteingang.
  Bis dahin gilt: Kommt über WhatsApp etwas Wichtiges, wird es mit einem Klick als Notiz/Aufgabe ins OS übertragen.
  Der Code sieht dafür schon jetzt eine Kanal-Spalte vor (`kanal`: mail, brief, telefon, linkedin, whatsapp, portal).

### 4.9 KPIs, Ziele und Wochengespräch

Das Herz der Messung. Alle Zahlen entstehen aus Ereignissen, keine wird von Hand gepflegt, außer sie geht nicht
anders (z. B. Kassenstand, bis eine Bank angebunden ist).

**KPI-Katalog** (aus den Rollen):

| Bereich | KPI | Quelle im OS |
|---|---|---|
| Produkt & Technik | Stunden pro Build | Zeiterfassung an Projekten |
| | Freigaben ohne Änderung | Abnahmen beim ersten Mal bestanden / alle Abnahmen |
| | Ausfallzeit | Notfälle „System steht“ mit Start/Ende (später Uptime-Prüfung) |
| | Kosten pro Kunde | KI-Kosten (`ai_usage` je Kunde) + Serveranteil + Zeit |
| Markt, Kunden & Betrieb | Neue Kunden pro Monat | Statuswechsel nach `beauftragt` |
| | Abschlussquote | Abschlüsse / geführte Erstgespräche |
| | Tage bis live | `beauftragt` → `live` |
| | Kunden, die bleiben | aktive Kunden nach 3/6/12 Monaten |
| | Kassenstand | Finanzen (4.10) |
| Akquise & Recherche | Qualifizierte Leads pro Woche | Leads mit Prüfung „angenommen“ |
| | Anteil angenommener Leads | Prüfungen angenommen / alle |
| | Termine pro Woche | Termine mit Lead, gebucht durch Hamza/Omid |
| Branchentest | Preisgespräche, Abschlüsse je Branche | Termin-Ergebnis + Pipeline, nur kalte Leads |

- **Wochenziele:** Jede Person trägt ihre drei Wochenziele selbst ein (bei Hamza/Omid im Gespräch mit Oskar). Das
  OS verknüpft sie, wo möglich, mit einer KPI und zeigt den Stand live.
- **Wochengespräch:** Das OS erzeugt die Agenda: pro Person KPIs der Woche, Ziele (erreicht / nicht), wo es hängt
  (überfällige Aufgaben, offene Anfragen), Notfälle der Woche, offene Entscheidungen. Ergebnisse werden im
  Gespräch direkt als Aufgaben und Entscheidungen erfasst.
- **Cockpit:** eine Seite mit allen KPIs je Bereich, Verlauf über Wochen, Ampeln mit Zielwerten.

### 4.10 Finanzen

- Rechnungen (Einmalbetrag, monatliche Pauschale, Änderungen) aus Vorlagen, Nummernkreis, Status offen/bezahlt,
  Mahnung als Vorschlag.
- Laufende Kosten (Server, Lizenzen, KI) als Liste mit Monatsbetrag; KI-Kosten kommen automatisch.
- Kassenstand: vorerst von Hand gepflegt, später Bank-Anbindung prüfen.
- Kennzahlen aus den Rollen: Gewinnschwelle, Fixkosten, und die **Geld-Regel für Einstellungen** (3 Monate 1,5-fache
  Personalkosten übrig, 6 Monatskosten auf dem Konto) als Ampel.
- Export für den Steuerberater (CSV/PDF). Buchhaltung selbst bleibt extern.

### 4.11 Wissen, Regeln und Entscheidungen

- Das bestehende „Gehirn“ wird zum Firmenwissen: Wer wir sind, Angebot, Preise, Stil, Vorlagen, Checklisten.
- **Regeln als Daten:** Entscheidungsrechte, Lernstufen, Freigabe-Stufen, Abnahme-Checkliste,
  Änderungswunsch-Regeln, Notfall-Definition. Änderungen an Regeln sind selbst Entscheidungen mit Protokoll.
- **Entscheidungs-Log:** Was wurde entschieden, von wem, warum, auf welcher Datenbasis (z. B. Hauptzielgruppe nach
  dem Branchentest).
- **Team-Roadmap:** Das OS beobachtet die Auslöser aus der Rollen-Datei (Arbeit bindet 4 Wochen lang mehr als die
  Hälfte der Zeit einer Person) über die Zeiterfassung und meldet, wenn ein neuer Bereich fällig wird.

### 4.12 Der Assistent (KI-Schicht)

Die KI ist der „Büroleiter“ des OS, aber nie ohne Leitplanken:

- Schreibt Entwürfe (Nachrichten, Angebote, Briefings, Case Studys, Wochenagenda) → immer Freigabe-Stufe C.
- Beantwortet Fragen über die Firma („Was ist bei Kunde X offen?“, „Wie war die Woche?“) aus den Daten des OS.
- Schlägt Aufgaben und Prioritäten vor, legt sie aber nur auf Stufe A/B selbst an, wenn die Regel das erlaubt.
- Budget je Person und Monat wie heute, Kosten je Kunde getrennt.


### 4.13 Einstellungen: das Systemmenü des OS

Grundsatz G9: Wie bei einem Betriebssystem auf dem Handy oder Rechner wird alles über die Einstellungen gesteuert.
Die Konsole auf dem Server braucht man genau einmal, bei der Ersteinrichtung (`deploy/setup.sh`), und danach nur
noch im Notfall (Server startet gar nicht mehr).

**Was es schon gibt** (`app/config_store.py`, `app/settings_views.py`, `app/updater.py`): Einstellungen in der App
statt in der `.env` (gespeichert in `settings.json` im Datenordner, Passwörter und Schlüssel verschlüsselt, in der
Oberfläche nie wieder angezeigt), Neustart nach dem Speichern, Updates per Knopf mit Sicherung und automatischem
Zurückrollen, Sicherung herunter- und hochladen.

**Was dazukommt:**

| Bereich in den Einstellungen | Inhalt |
|---|---|
| **Personen** | Mitarbeitende anlegen, einladen (Einladungslink setzt das Passwort), sperren, archivieren. Daten bleiben beim Archivieren erhalten. Heute ist das Team fest im Code (`leaddb.py`, `INSERT INTO team`), das wird abgelöst. |
| **Rollen und Rechte** | Rollen (z. B. Gründer, Bereichsleitung, Recherche) mit Rechten je Modul: sehen / bearbeiten / freigeben / verwalten. Rechte zusätzlich je Person. Beispiele: Omid sieht keine Finanzen; nur Gründer dürfen Personen und Rechte ändern. |
| **Bereiche** | anlegen, teilen, verantwortliche Person, KPIs je Bereich (Team-Roadmap ohne Code umsetzbar) |
| **Lernstufen und Freigabe-Stufen** | Stufen, Aufstiegsbedingungen, Stufe A–D je Aktion, Stichprobenquote |
| **Entscheidungsrechte** | die Tabelle aus den Rollen, inklusive Betragsgrenzen (200 €) |
| **Anbindungen** | Google (Client-ID und Clientschlüssel), iCloud, Postfächer, KI-Schlüssel und Budgets, später Unipile und Bank. Jede Anbindung mit Knopf „Verbindung testen“ und Status. |
| **Vorlagen und Regeln** | Angebote, Rechnungen, Abnahme-Checkliste, Anforderungsdokument, Mails, Bewertungsregeln, Branchen und Nischen |
| **Firma** | Name, Adresse, Logo, Bankdaten für Rechnungen, Preise und Pauschalen |
| **Software und Updates** | gibt es schon; dazu eine Liste „Was ist neu“ je Version |
| **Sicherung** | gibt es schon; dazu Zeitplan und Speicherort (z. B. zusätzlich in die Cloud) |
| **Protokoll** | wer hat wann welche Einstellung geändert (Ereignisse, G6) |

**Regeln für die Umsetzung:**

- Jede neue Funktion bringt ihre Einstellungen gleich mit. Ein Wert, den jemand ändern wollen könnte, gehört in die
  Einstellungen, nicht als Konstante in den Code und nicht nur in die `.env`.
- Die `.env` bleibt nur für den Start (Datenordner, Port) und als Rückfall. Was in der App gespeichert ist, gilt vor
  der `.env` (so ist es schon heute).
- **Aussperren verhindern:** Mindestens eine Person mit Recht „Personen und Rechte verwalten“ muss es immer geben;
  die eigene Verwaltungsberechtigung kann man sich nicht selbst entziehen. Werte, die heute aus Sicherheitsgründen nur
  in der `.env` stehen (`ALLOWED_DOMAINS`, `ADMIN_USERS`, `SECRET_KEY` …), werden durch Personen/Rollen in der App
  ersetzt oder automatisch erzeugt, mit genau diesen Sicherungen. Für den Notfall gibt es einen dokumentierten
  Wiederherstellungsweg über die Konsole.
- Geheimnisse (Schlüssel, Passwörter, Tokens) werden verschlüsselt gespeichert, nach dem Speichern nie wieder
  angezeigt, nur ersetzt oder gelöscht.

---

## 5. Datenmodell (Skizze)

Weiter SQLite in `leaddb.py`, neue Tabellen per `SCHEMA`/`MIGRATIONS` wie bisher. Namen sind Vorschläge.

```
personen         = team (+ rolle, lernstufe, bereiche, kalender_token, aktiv)      ← Omid anlegen
bereiche         (key, name, verantwortlich, eltern_bereich)
ereignisse       (id, at, wer, art, objekt_typ, objekt_id, daten JSON)            ← Firmen-Protokoll, ersetzt
                                                                                    langfristig lead_events
aufgaben         (id, titel, bereich, person, faellig, prio, status, schaetzung_min,
                  objekt_typ, objekt_id, erstellt_von, erstellt_am, erledigt_am, quelle)
                                                                                    ← übernimmt tasks
anfragen         = aufgaben mit art='anfrage' und anfragende Person
termine          (id, titel, start, ende, ort, meet_link, kalender_event_id, objekt_typ,
                  objekt_id, ergebnis, notiz)
termin_teilnehmer(termin_id, person | email, zusage)
freigaben        (id, art, objekt_typ, objekt_id, vorschlag JSON, angefragt_von, pruefer,
                  status, entscheidung_am, geaendert JSON)
projekte         (id, kunde_id, titel, status, schaetzung_h, start, live_am, preis_einmal, pauschale)
aenderungen      (id, kunde_id, projekt_id, text, groesse, abrechnung, status, zeit_min)
zeiten           (id, person, start, ende, minuten, objekt_typ, objekt_id, notiz)
ziele            (id, person, woche, text, kpi_key, zielwert)
kpi_werte        (kpi_key, bereich, person, zeitraum, wert)                       ← Cache, aus ereignisse berechnet
entscheidungen   (id, at, wer, titel, begruendung, grundlage)
dokumente        (id, art, objekt_typ, objekt_id, datei, version, status)
rechnungen       (id, nummer, kunde_id, betrag, art, faellig, bezahlt_am)
kosten           (id, name, betrag_monat, ab, bis, kunde_id)
regeln           (key, wert JSON, geaendert_von, geaendert_am)                     ← Freigabe-Stufen, Lernstufen …
kunden_zugang    (kunde_id, email, token_hash, letzter_login)
```

Grundregel: **Jede schreibende Funktion erzeugt ein Ereignis.** Am besten über eine zentrale Funktion
(z. B. `os_log(wer, art, objekt, daten)`), damit kein Weg am Protokoll vorbeiführt.

---

## 6. Anbindungen

| Dienst | Wofür | Stand / Kosten |
|---|---|---|
| Strato IMAP/SMTP | Mails senden und lesen | gibt es |
| ICS-Abo | Termine in private Kalender | selbst gebaut, kostenlos |
| Google Kalender API | Kalender jeder Person verbinden: frei/belegt, Firmentermine eintragen, Meet-Links, Einladungen | kostenlos, normale Google-Konten reichen; Google-Cloud-Projekt mit OAuth nötig |
| iCloud CalDAV | Apple-Kalender verbinden: frei/belegt, Termine eintragen | kostenlos, app-spezifisches Passwort |
| Anthropic API | Entwürfe, Briefings, Assistent | gibt es, mit Budget |
| Web-Push | Meldungen aufs Handy | gibt es |
| Unipile | WhatsApp, LinkedIn-Nachrichten im Posteingang | später, wenn Budget da ist |
| HubSpot / Pipedrive / Notion | Andocken und Datenexport für Kunden | prüfen (Finn) |
| Bank | Kassenstand automatisch | später prüfen |

---

## 7. Technische Leitplanken

- **Bleibt ein Programm:** Flask + SQLite + ein Gunicorn-Worker + Hintergrund-Arbeiter, wie heute. Kein Umbau auf
  Microservices, keine zweite Datenbank, solange das reicht. Neue Module als eigene Dateien
  (`app/aufgaben.py`, `app/kalender.py`, … mit `*_views.py` für die Seiten), wie bei `crm.py`/`crm_views.py`.
- **Der Hintergrund-Arbeiter wird zum Taktgeber des OS:** Er prüft jede Minute fällige Erinnerungen, Fristen,
  Regeln (z. B. „Abnahme seit 3 Tagen offen“) und legt Aufgaben/Meldungen an.
- **Handy zuerst:** Alles, was im Alltag gebraucht wird (Mein Tag, Freigaben, Aufgaben, Termine), muss auf dem Handy
  in der installierten Web-App gut gehen.
- **Bedienung nur über die App (G9):** Neue Einstellungen werden in `app/config_store.py` (Gruppen) bzw. in
  eigenen Einstellungsseiten ergänzt, nie nur als Umgebungsvariable. Personen, Rollen und Rechte liegen in der
  Datenbank, nicht im Code. Updates laufen über `app/updater.py` (Knopf in der App); Migrationen der Datenbank laufen
  beim Start automatisch (`MIGRATIONS`), damit ein Update nie Handarbeit auf dem Server braucht.
- **Sicherheit und Datenschutz:** Kundendaten bleiben auf dem eigenen Server; AV-Verträge mit Kunden; Rechte je
  Rolle (Omid sieht z. B. keine Finanzen); Kunden-Zugang nur auf eigene Daten; Kalender-Token geheim und erneuerbar;
  `.env` nie ins Repository.
- **Tests:** Jede neue Regel (Freigabe-Stufe, Lernstufe, Abrechnungsregel, KPI-Berechnung) bekommt einen Test in
  `tests/`.
- **Sprache:** Oberfläche, Code-Begriffe der Fachlichkeit und Doku auf Deutsch, wie bisher.

---

## 8. Baustufen

Gebaut wird in kleinen Stufen, jede für sich nutzbar. Jede Stufe endet damit, dass das Team sie im Alltag benutzt.

- [x] **Stufe 1 – Fundament: Personen, Rechte, Aufgaben, Protokoll** (gebaut 01.11.2026, Abweichungen unten)
  Personen in den Einstellungen anlegen und einladen (statt fest im Code; Omid als erster neuer), Rollen und
  Rechte je Modul, Bereiche, Schutz gegen Aussperren, Firmen-Protokoll (`ereignisse`) mit zentraler Log-Funktion,
  Aufgaben mit Bereich/Person/Termin (Wiedervorlagen übernehmen), Anfragen an andere Bereiche, Seite „Mein Tag“,
  Kommentare mit @-Erwähnung. *Ab hier gilt: Was nicht im OS steht, ist keine Aufgabe.*
  *Umsetzung und Abweichungen:*
  - Personen bleiben in der Tabelle `team` (statt `personen`), damit Leads, CRM und KI-Budget unverändert
    weiterlaufen; neue Spalten `rolle`, `lernstufe`, `status`, `email`, `rechte`, `einladung_hash/_bis`.
    Bereiche je Person in `person_bereiche`. Das Team steht nicht mehr fest im Schema, sondern wird einmalig per
    `DATA_MIGRATIONS` angelegt. Omid wird in der App angelegt, nicht im Code.
  - Rechte je Modul: Aufgaben, Leads, CRM, Mailer, KI-Kosten, Protokoll, Einstellungen, Personen. Stufen 0–4
    (kein Zugriff, sehen, bearbeiten, freigeben, verwalten); GET braucht „sehen“, POST „bearbeiten“. Jede Seite ist
    einem Modul zugeordnet (`main.modul_der_seite`, Test prüft das). `ADMIN_USERS` gilt nur noch als Rückfall.
  - Mit dem Postfach angemeldet: nur Mailer, und nur Personen ohne Passwort wählbar.
  - Einladungslink statt Einladungsmail (Link zum Kopieren, Gültigkeit einstellbar unter Personen).
  - `ereignisse` mit `firma.log()`; alles aus `lead_events` landet zusätzlich dort. Seite Einstellungen →
    Protokoll.
  - Aufgaben in `aufgaben` (art `aufgabe`/`anfrage`), Wiedervorlagen einmalig aus `tasks` übernommen (alte
    Tabelle bleibt liegen). Bereich für Wiedervorlagen ist eine Regel unter Einstellungen → Bereiche.
  - Kommentare in `kommentare` an Aufgaben und Leads; Meldungen in der Glocke jetzt auch je Person (`notices.fuer`),
    Push je Person (`push_subs.person`).
  - Mein Tag zeigt noch keine Termine, Ziele und Freigaben (kommen mit Stufe 3, 4 und 6). Notfall-Knopf und
    automatisch angelegte Aufgaben aus Ereignissen folgen mit den Stufen, die diese Ereignisse erzeugen.
  - Design nach [DESIGN.md](DESIGN.md): neue Seiten mit Sheets, schwebenden Knöpfen, Federn; die älteren Seiten
    haben die neuen Farben bekommen, ihr Aufbau ist noch der alte.
- [x] **Stufe 2 – Branchentest** (gebaut 01.11.2026, Abweichungen unten)
  Vier Testbranchen, warm/kalt, Zuordnung Hamza/Omid, Lead-Prüfung als Freigabe (Omid → Hamza → Stichprobe Oskar),
  Auswertung je Branche. *Zeitkritisch, weil der Test als Nächstes läuft.*
  *Umsetzung und Abweichungen:*
  - Testbranchen in `testbranchen` (Name, Nischen, Recherche-Person, Ziel, aktiv), in der App anleg- und änderbar.
    Startwerte wie in den Rollen; Handwerk und Verwaltung haben noch niemanden, weil Omid erst in der App angelegt
    wird. Neue Leads kommen über ihre Nische automatisch in die passende Branche.
  - Leads bekommen `testbranche`, `herkunft` (warm/kalt), `recherche_von`, `pruef_status`, `preisgespraech_am`.
  - Lead-Prüfung ist die erste Freigabe-Art in der Tabelle `freigaben` (aus Abschnitt 5, wird in Stufe 6
    verallgemeinert). Wer prüft, ergibt sich aus den Bereichen: verantwortliche Person der Akquise, bei ihr selbst
    eine Ebene höher. Prüfquoten je Lernstufe und die Stichprobe der nächsten Ebene sind Regeln in der App
    (Branchentest → Regeln). Kontakt (Brief, Telefon, Mail) ist bis zur bestandenen Prüfung gesperrt.
  - Kontakt mit einem Klick in der Akte (Telefon, Brief, LinkedIn, vor Ort; Mail nur als Antwort, § 7 UWG).
    „Ernsthaftes Preisgespräch“ ist ein Knopf in der Akte; Termin-Ergebnisse folgen mit Stufe 3.
  - Auswertung zählt Betriebe, Kontakte, Antworten, Gespräche, Preisgespräche, Abschlüsse je Branche, nur kalt;
    Ampel ab der eingestellten Zahl Preisgespräche. KPIs „angenommene Leads pro Woche“ und „Anteil angenommen“
    je Person auf „Leads prüfen“ und „Mein Tag“ (das Cockpit kommt mit Stufe 4).
- [x] **Stufe 3 – Kalender und Termine** (gebaut 01.11.2026, Abweichungen unten)
  Termine im OS, eigenen Google-Kalender verbinden (frei/belegt, Termine eintragen), Kundentermine mit Meet-Link
  und Einladung über das Google-Konto der zuständigen Person (Freigabe C), Terminvorschläge um alle herum,
  Erinnerungen, Ergebnis nach dem Termin, Briefing, Serientermin Wochengespräch. Danach iCloud und ICS-Abo.
  *Umsetzung und Abweichungen:*
  - Google über OAuth ohne Zusatzbibliothek (`app/google_kalender.py`), genau die zwei Berechtigungen, Rücksprung
    `/kalender/google/zurueck`. Client-ID und Schlüssel unter Einstellungen → Google Kalender, Status und
    Rücksprung-Adresse unter Einstellungen → Kalender. Refresh-Token verschlüsselt in `kalender_konten`.
  - Tabellen `termine`, `termin_teilnehmer` (je Person die Google-Termin-ID), `belegt` (nur Zeiten), `serien`.
    Interne Termine landen in jedem verbundenen Kalender der Teilnehmenden, ohne Einladung. Kundentermine nur im
    Kalender der verantwortlichen Person mit Gast, Meet und Einladung durch Google.
  - Freigabe C als `freigaben.art = 'termin_einladung'`: Legt jemand anderes den Kundentermin an, wartet die
    Einladung auf die verantwortliche Person. Legt sie ihn selbst an und schaltet „Einladung jetzt senden“ ein,
    gilt das als Freigabe. Ein Lead in der Lead-Prüfung bekommt noch keinen Termin.
  - Erinnerungen 24 h und 1 h vorher an die Teilnehmenden aus dem OS (Glocke, 1 h auch Push). Kunden erinnert
    Google über die Einladung; eigene Erinnerungsmails an Kunden schickt das OS nicht.
  - Terminvorschläge aus frei/belegt (Google) und OS-Terminen, innerhalb von Arbeitszeit, Wochentagen und Puffer
    (Regeln unter Einstellungen → Kalender, für alle gleich; je Person kommt mit den Stunden pro Woche).
  - Ergebnis nach dem Termin (stattgefunden, abgesagt, nicht erschienen) mit Notiz, Preisgespräch und Folgeaufgabe;
    „stattgefunden“ schiebt den Lead auf „Gespräch“. Briefing aus der Akte ohne KI, mit eigenen Notizen.
  - Serien als einzelne Termine (sechs Wochen voraus, täglich aufgefüllt), nicht als Google-Serie.
  - ICS-Abo je Person (`/kalender/<token>.ics`, nur der Hash gespeichert, in „Mein Kalender“ erneuerbar).
    iCloud über CalDAV ist noch offen; das Abo deckt Apple-Kalender lesend ab.
  - Buchungslink für Kunden (offene Frage 4) ist nicht gebaut.
- [x] **Stufe 4 – KPIs und Wochengespräch** (gebaut 01.11.2026, Abweichungen unten)
  KPI-Katalog aus Ereignissen, Wochenziele, Cockpit, automatische Agenda fürs Wochengespräch, Lernstufen-Messung.
  *Umsetzung und Abweichungen:*
  - KPI-Katalog in `app/kpis.py`, je Woche (ISO-Kalenderwoche) aus Prüfungen, Terminen, Kontakten, Stufen,
    Aufgaben und KI-Kosten berechnet. KPIs aus späteren Stufen (Stunden pro Build, Abnahmen, Ausfallzeit, Tage
    bis live, Kunden, die bleiben, Kassenstand) stehen im Katalog mit „kommt mit Stufe 5/8“. Kein Cache
    (`kpi_werte`): die Zahlen werden beim Anzeigen gerechnet, das reicht bei der Datenmenge.
  - Zielwerte je KPI fürs Team oder je Person (`kpi_ziele`), Ampel grün ab Ziel, gelb ab 70 %.
  - Wochenziele (`ziele`), höchstens drei je Person und Woche, optional mit KPI und Zielwert (Stand live).
    Setzen darf die Person selbst und wer sie führt.
  - Sichtbarkeit: persönliche Zahlen sehen die Person, wer sie führt (verantwortlich für ihren Bereich oder einen
    darüber) und wer „Zahlen, Ziele und Wochengespräch: Verwalten“ hat (neues Modul `cockpit`).
  - Agenda fürs Wochengespräch: Teamzahlen, je Person Zahlen, Ziele, wo es hängt, Ziele für nächste Woche,
    offene Entscheidungen (Freigaben, Aufstiege). Ergebnisse direkt als Aufgabe oder Ziel. Notfälle und
    Entscheidungs-Log fehlen noch (Stufe 6).
  - Lernstufen-Messung: Stufe 1 → 2 über angenommene Leads, Stufe 2 → 3 über Entwürfe ohne große Änderung
    (Textähnlichkeit zwischen Entwurf der schreibenden Person und freigegebenem Text, neu gemessen in
    `leads.entwurf_original`), Stufe 3 → 4 über gebuchte Termine und begleitete Gespräche. Schwellen einstellbar.
    Der Hintergrund-Arbeiter prüft täglich und legt eine Freigabe `lernstufe` für die führende Person an.
- [x] **Stufe 5 – Kunden, Projekte, Abnahme** (gebaut 01.11.2026, Abweichungen unten)
  Customer Journey nach „Kunde“, Projekte, Anforderung und Abnahme-Checkliste, Zeiterfassung, Änderungswünsche mit
  Abrechnungsregeln, Kosten je Kunde.
  *Umsetzung und Abweichungen:*
  - `app/projekte.py`, Tabellen `projekte`, `abnahme_punkte`, `abnahmen`, `aenderungen`, `zeiten`. Steht ein Lead im
    CRM auf „Kunde“, legt das OS das Projekt an (Status `beauftragt`) mit Aufgaben für AV-Vertrag, Rechnung und
    Anforderungstermin. Verantwortlich und Technik kommen aus den Bereichen (einstellbar).
  - Bau erst nach schriftlicher Anforderung, Bestätigung des Kunden (von Hand vermerkt, das Portal kommt mit
    Stufe 7), Schätzung der Technik und Freigabe beider. Schätzen darf nur die Technik selbst. Der geplante Termin
    ergibt sich aus der Schätzung, dazu „Zwischenstand zeigen“ zur Hälfte.
  - Abnahme: Checkliste aus den Einstellungen, letztes Wort nur die verantwortliche Person; nicht alles abgehakt →
    zurück in den Bau mit Aufgabe für die Technik. Jede Abnahme wird gezählt (KPI „Abnahmen beim ersten Mal“).
  - Aufgaben eines Schritts schließt das OS beim Weiterschalten selbst (Anforderung, Schätzung, Bau, Abnahme).
  - Änderungswünsche: Einordnen durch die verantwortliche Person, Schätzen durch die Technik, Abrechnung nach
    Regel (vor Live klein inklusive/groß Angebot, in den kostenlosen Tagen inklusive, danach Pauschale/Angebot).
    Kleine Änderungen bekommen den eingestellten Umsetzungstag. Wünsche per Formular oder Mail kommen mit Stufe 7/9.
  - Zeiterfassung mit Start/Stopp (immer nur eine laufende Zeit je Person) oder Nachtragen; Pflicht für den Bau
    (abschaltbar). Monatliche Warnung, wenn die Zeit je Kunde zwei Monate am Stück über der Annahme liegt.
  - Kosten je Kunde: KI-Kosten (je Lead abgerechnet), Zeit × interner Stundensatz, Serveranteil; dazu Umsatz aus
    Einmalbetrag, Pauschale und abgerechneten kleinen Änderungen. Rechnungen selbst kommen mit Stufe 8.
  - KPIs jetzt berechnet: Stunden pro Build, Abnahmen beim ersten Mal, Tage bis live, Kunden, die bleiben.
    Ausfallzeit braucht den Notfall-Knopf (Stufe 6). Neues Rechte-Modul „Kunden, Projekte und Änderungen“
    (Gründer verwalten, Recherche ohne Zugriff).
  - Der wöchentliche Check-in ist eine Kalender-Serie, die man am Projekt mit einem Klick anlegt.
- [x] **Stufe 6 – Freigabe-Zentrale und Regeln als Daten** (gebaut 01.11.2026, Abweichungen unten)
  Alle Vorschläge in einer Freigabe-Warteschlange, Freigabe-Stufen und Entscheidungsrechte einstellbar,
  Entscheidungs-Log.
  *Umsetzung und Abweichungen:*
  - `app/freigaben.py` (Zentrale, Stufen, Entscheidungsrechte, Log), `app/notfall.py`, Seiten in
    `app/freigaben_views.py`. Neue Tabellen `entscheidungsrechte`, `entscheidungen`, `notfaelle`. Neues
    Rechte-Modul „Freigabe-Stufen, Entscheidungsrechte und Entscheidungs-Log“ (Gründer verwalten, Recherche
    bearbeitet: eigene Entscheidungen festhalten).
  - **Freigabe-Zentrale** (`/freigaben`): Jede Art steht einmal in `freigaben.ARTEN` (Beschreibung, Entscheiden,
    Modul). Aus der Tabelle `freigaben`: Lead-Prüfung, Einladung an Kunden, Aufstieg, Entscheidung. Abgeleitet aus
    den Daten: Anforderung im Projekt (nur „Freigeben“; ändern geht im Projekt) und Erstkontakt-Entwürfe (nur Link
    zur Akte, dort wird weiter geprüft und versendet). Ablehnen immer mit Grund. „Alle offenen“ für alle, die ein
    betroffenes Modul verwalten. Zähler in der Navigation, Mein Tag zeigt die Freigaben in einem Abschnitt statt
    getrennt nach Leads, Kalender und Aufstiegen. Die Seite „Leads prüfen“ (Branchentest) bleibt zusätzlich.
  - **Freigabe-Stufen A–D je Aktion** als Einstellung (kv `regel:stufen`) mit erlaubten Stufen je Aktion. Wirklich
    umschaltbar ist jetzt die Einladung an Kunden (A/B/C, Standard C). Stufe B legt eine Freigabe mit Frist an
    (Frist im Feld `vorschlag`, keine eigene Spalte); der Takt im Hintergrund-Arbeiter verschickt nach der
    Einspruchsfrist (Standard 24 Stunden, einstellbar 1–168), „Stoppen“ macht daraus eine normale Freigabe (C).
    Ist die Aktion nicht mehr ausführbar (Termin abgesagt), bleibt sie als C stehen und steht im Protokoll. Feste
    Aktionen (Erstkontakt C, Anforderung C, Abnahme D, Lead-Prüfung über Lernstufen, Team-Meldungen A) stehen zur
    Übersicht im Katalog; Wochenbericht, Erinnerung, Angebot, Rechnung sind als „kommt mit Stufe 7/8“ sichtbar,
    aber noch gesperrt. Erstkontakt per Mail bleibt hart gesperrt (§ 7 UWG).
  - **Entscheidungsrechte** als Daten mit Startwerten aus ROLLEN-UND-ABLAEUFE.md: Wer entscheidet (verantwortliche
    Person des eigenen Bereichs, die Person selbst, Bereich, Rolle, Person), „eine reicht“ oder „alle gemeinsam“,
    wer vorher gefragt wird, Betragsgrenze und welches Recht ab der Grenze gilt (Startwert: ab 200 € das
    Gründerteam). Anlegen, ändern, löschen in Einstellungen → Freigaben und Entscheidungen.
  - **Entscheidungs-Log** (`/entscheidungen`): Was, wer, warum, Grundlage, Recht, Betrag. Darf die Person nach dem
    Recht nicht allein entscheiden, landet die Entscheidung bei den übrigen in der Zentrale (Push); eine Ablehnung
    beendet sie. Gefragte Personen bekommen eine Meldung. Änderungen an Regeln, Kennzahl-Zielen und Rollen trägt das
    OS selbst ein (Haken in `firma.log`). Im Wochengespräch: offene Entscheidungen, Entscheidungen der Woche und
    „Entscheidung festhalten“ direkt aus der Agenda.
  - **Notfall-Knopf** (`/notfall`, im Konto-Menü): Arten Kundensystem gestört, Kundendaten in Gefahr, Kunde droht
    abzuspringen. Benachrichtigt sofort per Glocke und Push die Verantwortlichen der eingestellten Bereiche
    (Startwerte wie im Rollen-Dokument), rotes Band oben für alle, bis „Behoben“ mit Notiz. Notfälle der Woche im
    Wochengespräch. Die KPI „Ausfallzeit“ ist jetzt berechnet (Stunden mit gestörtem Kundensystem, Überschneidungen
    einmal gezählt).
- [x] **Stufe 7 – Kunden-Bereich** (gebaut 01.11.2026, Abweichungen unten)
  Portal mit Einmal-Link-Login, Termine, Projektstand, Änderungswünsche, Dokumente.
  *Umsetzung und Abweichungen:*
  - `app/portal.py` (Zugang, Ansicht, Dokumente, Wochenbericht, Termin-Erinnerung, Regeln), Kundenseiten in
    `app/portal_views.py` (`/portal`, eigene Anmeldung, kein Team-Modul), Team-Seiten am Projekt in
    `app/projekte_views.py`. Neue Tabellen `kunden_zugaenge`, `kunden_sitzungen`, `dokumente`, `wochenberichte`.
    Rechte: über das bestehende Modul „Kunden, Projekte und Änderungen“; kein neues Modul.
  - **Zugang:** Die verantwortliche Person (oder wer Kunden verwaltet) klickt am Projekt „Zugang per Mail schicken“;
    der Klick ist die Freigabe. Alternativ „Nur Link erzeugen“ zum Kopieren (im Demo-Modus immer so). Der Link gilt
    einmal und 7 Tage, danach bleibt der Kunde 90 Tage auf dem Gerät angemeldet (eigenes Cookie nur für `/portal`,
    getrennt von den Team-Sitzungen). In der Datenbank liegen nur Hashes. Der Link öffnet erst eine Seite mit
    „Anmelden“, damit Virenscanner in Mailprogrammen ihn nicht verbrauchen. Einen neuen Link fordert der Kunde mit
    seiner Adresse selbst an (höchstens alle 10 Minuten, gleiche Antwort für unbekannte Adressen). Sperren beendet
    alle Sitzungen.
  - **Portal:** Projektstand in Kundensprache, Anforderung lesen und selbst bestätigen (meldet Verantwortlich und
    Technik), Termine mit Meet-Link, Abnahme-Checkliste (ab Abnahme), Wünsche stellen (landen als Änderungswunsch mit
    `quelle='portal'` bei der verantwortlichen Person) und ihren Stand sehen, freigegebene Dokumente, verschickte
    Wochenberichte. Nie sichtbar: Zeiten, Kosten, interne Notizen und Namen.
  - **Dokumente:** am Projekt hochladen (PDF, Bild, Word, Excel, Text, ZIP; bis 8 MB wie die App-Grenze), unter
    zufälligem Namen in `DATA_DIR/dokumente`, je Dokument „Kunde sieht es“ oder „nur intern“.
  - **Wochenbericht:** am eingestellten Tag (Standard Freitag ab 12 Uhr) für Projekte im Bau, in der Abnahme, live und
    im Betrieb, aus Stand, umgesetzten und offenen Wünschen und nächsten Terminen. Freigabe-Stufe `wochenbericht`
    (Standard B, 24 Stunden Einspruch; C möglich). Vor dem Versand am Projekt bearbeitbar, in der Zentrale als neue
    Art. Empfänger: aktive Zugänge, sonst die Adresse aus der Akte.
  - **Termin-Erinnerung an Kunden** 24 Stunden vorher an die Gäste eines Kundentermins (Stufe `erinnerung_kunde`,
    Standard A, C möglich; B ergibt bei 24 Stunden Vorlauf keinen Sinn). Abweichung von Stufe 3 (dort: „eigene
    Erinnerungsmails an Kunden schickt das OS nicht“).
  - Mails an Kunden gehen über das Akquise-Postfach im Namen der verantwortlichen Person (`agent.send_new`), stehen
    in der Akte und gehen nie an gesperrte Betriebe oder Adressen auf der Sperrliste. Kein Erstkontakt: der
    Mailschalter `OUTREACH_EMAIL` bleibt davon unberührt.
  - Einstellungen → Kunden-Bereich: Gültigkeit von Link und Sitzung, Texte der Mails, Berichtstag und -uhrzeit,
    ob Kunden Termine und Abnahme-Checkliste sehen.
- [x] **Stufe 8 – Finanzen** (gebaut 01.11.2026, Abweichungen unten)
  Rechnungen, laufende Kosten, Kassenstand, Geld-Regel als Ampel, Export für Steuerberater.
  *Umsetzung und Abweichungen:*
  - `app/finanzen.py`, Seiten in `app/finanzen_views.py`, PDF ohne zusätzliche Bibliothek in `app/pdf.py` (Helvetica,
    A4). Neue Tabellen `rechnungen`, `rechnung_positionen`, `kosten`, `kasse`. Neues Rechte-Modul „Finanzen“
    (Gründer verwalten, Recherche ohne Zugriff). Seite „Finanzen“ im Konto-Menü, Einstellungen → Finanzen.
  - **Rechnungen:** Entwurf ohne Nummer (Positionen, Empfänger, Leistungszeitraum bearbeitbar) → „Zur Freigabe“
    (Freigabe-Stufe `rechnung`, Standard C, B möglich; neue Art in der Zentrale) → mit der Freigabe fortlaufende
    Nummer je Jahr (Format einstellbar, Standard `RE-2026-0001`), Datum, Fälligkeit, PDF, Dokument im Kunden-Bereich
    und Mail mit PDF-Anhang an den Kunden (abschaltbar; über das Akquise-Postfach, in der Akte, nie an gesperrte
    Betriebe). Ablehnen schickt die Rechnung mit Grund zurück in den Entwurf. Ausgestellte Rechnungen ändert man nicht:
    Stornieren erzeugt eine Stornorechnung mit eigener Nummer und negativen Beträgen (gleicher Steuersatz wie die
    Rechnung). Bezahlt von Hand markieren; überfällige zeigt die Übersicht. Ohne Firmendaten (Name, Anschrift,
    Steuernummer oder USt-IdNr.) geht keine Rechnung zur Freigabe.
  - Vorlagen aus dem Projekt: „Rechnung Einmalbetrag“ (einmal je Projekt, außer nach Storno) und Monatsrechnung
    (Pauschale des Monats plus kleine Änderungen mit Abrechnung „Pauschale“, die im Vormonat erledigt und noch nicht
    abgerechnet wurden). Monatsrechnungen bereitet der Takt am eingestellten Tag (Standard 1., ab 6 Uhr) für Projekte
    live und im Betrieb vor und gibt sie zur Freigabe. Frei angelegte Rechnungen gehen auch.
  - **Umsatzsteuer:** Startwert Kleinunternehmer (§ 19 UStG, Hinweis auf der Rechnung), umstellbar auf
    Regelbesteuerung mit Steuersatz. Gilt für neue Entwürfe; ausgestellte Rechnungen behalten ihren Satz.
    **E-Rechnung** (XRechnung/ZUGFeRD) noch nicht: bis Ende 2027 reicht für kleine Firmen das PDF, vorher als eigene
    Stufe nachziehen.
  - **Laufende Kosten** mit Monatsbetrag, Kategorie, seit/bis; KI-Kosten automatisch aus `ai_usage` (Schnitt der
    letzten drei Monate). **Kassenstand** von Hand mit Datum.
  - **Kennzahlen:** Pauschalen im Monat (Projekte live/Betrieb), Monatskosten, offene und überfällige Forderungen,
    Reichweite des Kontos, Gewinnschwelle (Pauschalen gegen Monatskosten), Umsatz/Kosten/übrig der letzten drei
    Monate. **Geld-Regel** als Ampel mit den Personalkosten der nächsten Stelle aus den Einstellungen (grau, solange
    keine eingetragen sind). KPI „Umsatz (Rechnungen, netto)“ im Katalog.
  - **Export** (`/finanzen/export`): ZIP mit `rechnungen.csv`, `kosten.csv`, `kasse.csv` (Semikolon, UTF-8 mit
    BOM für Excel) und den PDFs der Rechnungen im Zeitraum.
  - Angebote verschicken bleibt im Katalog als „kommt später“ (nicht Teil dieser Stufe).
- [x] **Stufe 9 – Ein Posteingang** (gebaut 01.11.2026, Abweichungen unten)
  Alle Kanäle an einer Stelle; Unipile für WhatsApp/LinkedIn, sobald Budget da ist.
  *Umsetzung und Abweichungen:*
  - `app/posteingang.py`, Einstellungen in `app/posteingang_views.py`; das bestehende Postfach im CRM
    (`mailbox.py`, `crm_views.py`) ist der gemeinsame Posteingang. `messages` hat jetzt `kanal` (mail, telefon, brief,
    whatsapp, linkedin, portal), `postfach_id`, `wunsch`, `aenderung_id`, `erfasst_von`. Neue Tabellen `postfaecher`,
    `postfach_gesehen`. Kein neues Rechte-Modul: Postfach über „CRM“, Einstellungen → Postfächer über
    „Einstellungen: Verwalten“.
  - **Weitere Postfächer:** beliebig viele mit IMAP/SMTP, Passwort verschlüsselt (gleicher Schlüssel wie die übrigen
    Einstellungen), „gehört“ einer Person oder dem Team. Der Takt ruft alle zehn Minuten ab (beim ersten Mal die letzten
    drei Tage), „Jetzt abrufen“ im Postfach und je Postfach. Zuordnung wie beim Akquise-Postfach (Antwort auf unsere
    Mail, Absender, Domain, sonst neuer Kontakt), Newsletter bleiben draußen; Fehler stehen am Postfach. Antworten im
    Gespräch mit Wahl des Postfachs (vorbelegt: das, über das die letzte Mail kam); ein persönliches Postfach nutzt nur
    die Person, der es gehört. Sperrliste (Adresse oder Domain) und Erstkontakt-Sperre (§ 7 UWG) gelten für jedes
    Postfach. Die Meldung zu einer neuen Mail geht an die Person, der das Postfach gehört, sonst an die zuständige
    Person des Kontakts.
  - **Von Hand festhalten** im Gespräch: Telefonat, Brief, WhatsApp, LinkedIn, vom Kunden oder von uns, mit Zeitpunkt.
    **Unipile** nur vorbereitet: Einstellungen → WhatsApp und LinkedIn (Unipile) mit Adresse und Schlüssel, noch ohne
    Verbindung.
  - **Wünsche:** Eingehende Nachricht (Mail, Telefonat, Brief …) eines Kontakts mit laufendem Projekt wird als
    „möglicher Änderungswunsch“ markiert und der verantwortlichen Person gemeldet; sie übernimmt ihn mit einem Klick als
    Änderungswunsch (Quelle mail/telefon/…) oder verwirft ihn. Wünsche aus dem Kunden-Bereich stehen jetzt auch im
    Posteingang (Kanal portal, schon übernommen).
  - **Wartet auf Antwort seit** im Postfach (älteste zuerst) und im Gespräch, zuständige Person in der Liste.
    KPIs „Antwortzeit auf Kunden“ (Stunden bis zu unserer nächsten Nachricht, alle Kanäle) und „Wartet auf unsere
    Antwort“ im Katalog.
  - Das Postfach im CRM behält seine bisherige Gestaltung (noch nicht auf `os.css` umgestellt).
- [x] **Stufe 10 – Assistent** (gebaut 01.11.2026, Abweichungen unten)
  KI-Fragen an die Firmendaten, Vorschläge für Prioritäten, Entwürfe für Angebote, Briefings und Case Studys.
  *Umsetzung und Abweichungen* (Umfang mit Oskar abgestimmt: alles, Werkzeuge, Prioritäten auf Knopfdruck, Angebote
  mit PDF und Freigabe):
  - `app/assistent.py`, Seiten in `app/assistent_views.py`; neues Rechte-Modul „Assistent (KI)“ (Gründer Verwalten,
    Recherche Bearbeiten). Tabellen `assistent_fragen` (Verlauf mit Quellen, Kosten, Fehler) und `angebote`; Spalte
    `projekte.case_study`. KI-Aufruf `ai.anfrage` (Stufe „assistent“): Claude Opus 5.5, adaptives Denken, Aufwand
    einstellbar, serverseitiges Ersatzmodell bei Ablehnung, Kosten je Person in `ai_usage`.
  - **Fragen an die Firmendaten** (`/assistent`): Werkzeug-Schleife (Tool Use, strikte Schemas) mit `suche`, `akte`,
    `projekt`, `aufgaben`, `termine`, `kennzahlen`, `rechnungen`, `offene_punkte`. Jedes Werkzeug prüft `firma.darf`
    der fragenden Person; fehlt das Recht, bekommt das Modell „Keine Berechtigung“ als Fehler-Ergebnis. Antworten mit
    Links nur auf Seiten der App, Quellen als Chips, Verlauf je Person (Verwalten: alle im Team), jede Frage im
    Firmen-Protokoll. Der Assistent verschickt und ändert nichts.
  - **Was zuerst?** auf Mein Tag: immer eine Reihenfolge nach festen Regeln ohne KI (Notfälle, Freigaben, Termine
    heute, Anfragen, Überfälliges, Heutiges, Woche). Auf Knopfdruck ordnet die KI die Liste neu und begründet je
    Punkt (nur Punkte aus der Liste); das Ergebnis bleibt für den Tag stehen.
  - **Entwürfe:** Antwort im Gespräch (Knopf „Entwurf vom Assistenten“ füllt Betreff und Text, gesendet wird von
    Hand), Briefing am Termin (landet in den Briefing-Notizen), Case Study am Projekt (Abschnitt „Case Study“,
    bearbeitbar; Zahlen nur aus den Daten, Fehlendes unter „Noch zu klären“).
  - **Angebote** (`/angebote`, Rechte-Modul „Kunden“): anlegen an der Akte, am Projekt oder an einer großen Änderung,
    mit oder ohne KI-Text. Preise nie von der KI: Positionen aus Einmalbetrag/Pauschale des Projekts oder Schätzung ×
    Stundensatz, sonst 0 € zum Ausfüllen. Ohne Aufwandsschätzung der Technik keine Freigabe (nur die Technik trägt
    sie ein). Freigabe-Stufe „angebot“ jetzt einstellbar: C (verantwortliche Person gibt frei → Nummer
    `A-{jahr}-{nr:03d}`, PDF, Dokument im Kunden-Bereich, Mail mit PDF aus dem Akquise-Postfach, Akte auf „Angebot“)
    oder D (nur PDF und Aufgabe „persönlich übergeben“). Danach „Angenommen“ (→ Kunde, Preise ins Projekt) oder
    „Abgelehnt“ mit Grund. Klappt der Mailversand nicht, gilt das Angebot als freigegeben und wird persönlich
    übergeben (Meldung an die Person).
  - **Einstellungen → Assistent:** ein/aus, „Was zuerst?“ ein/aus, Modell (Opus/Sonnet/Haiku 5.5), Denk-Aufwand,
    höchstens Werkzeug-Schritte, Gültigkeit und Nummernformat der Angebote. Budget wie bisher unter KI und Budget
    (Team) und im Profil (je Person); ist eines aufgebraucht, fragt der Assistent nicht.
  - Abweichung: Prioritäten nicht automatisch jeden Morgen (nur auf Knopfdruck, Kosten nur beim Klick).

Die Reihenfolge 2 und 3 kann getauscht werden, je nachdem, was im Alltag zuerst fehlt.

---

## 9. Was das OS ausdrücklich nicht sein soll

- Kein Ersatz für Steuerberater, Anwalt oder Buchhaltung (nur sauber zuarbeiten).
- Keine Überwachung um ihrer selbst willen: Gemessen wird, was für Entscheidungen und für die Kunden zählt.
  Persönliche Zahlen sieht die Person selbst und wer sie führt (Hamza/Omid → Oskar), nicht automatisch alle.
- Kein Automatismus, der Kunden etwas verspricht oder schickt, ohne dass die zuständige Person es freigegeben hat
  (außer Stufe A/B, die ausdrücklich so eingestellt wurde, z. B. Termin-Erinnerungen).
- Kein Nachbau großer Tools in voller Breite: lieber schmal, aber alles an einer Stelle und miteinander verbunden.

---

## 10. Offene Fragen

Bitte direkt hier beantworten (Antwort: …), dann wird der Plan angepasst.

1. ~~**Google oder nicht?**~~ Entschieden (01.11.2026): Jede Person verbindet ihren eigenen Kalender (Google,
   Apple). Meet-Links erzeugt das OS über das Google-Konto der zuständigen Person. Siehe 4.3.
   Offen: Zu welcher Organisation gehört das Google-Konto, unter dem das Projekt „Firmen OS“ liegt? Liegen die
   Kalender von Oskar, Finn, Hamza und Omid in genau dieser Organisation (dann „Intern“ lassen) oder auf privaten
   Gmail-Konten bzw. bei Apple (dann „Als extern festlegen“ und „App veröffentlichen“)?
   Antwort (01.11.2026): Finn, Hamza und Omid haben private Google-Konten. Entschieden: „Extern“, „In Produktion“,
   ohne Google-Überprüfung. Jede Person verbindet ihr eigenes Konto. Erledigt, siehe 4.3.
2. **Status von Hamza und Omid** (Mitgründer oder Mitarbeit) – davon hängen Rechte im OS ab (Finanzen, Entscheidungen).
   Antwort:
3. **Wie viele Stunden pro Woche** hat jede Person neben der Schule? Das OS kann Aufgaben nur sinnvoll verteilen und
   Fokus-Blöcke planen, wenn es die verfügbare Zeit kennt.
   Antwort:
4. **Buchungslink für Kunden:** Sollen Leads/Kunden selbst Termine in freien Zeiten buchen dürfen, oder legt immer
   jemand aus dem Team den Termin an?
   Antwort:
5. **Kunden-Bereich:** gleich mit Stufe 5 oder erst, wenn es mehr als 2–3 Kunden gibt?
   Antwort:
6. **Was ist heute der größte Schmerz?** Danach richtet sich, ob nach Stufe 1 der Branchentest oder der Kalender kommt.
   Antwort:
7. **Name:** Bleibt es „Ylva Labs Mailer“, oder bekommt das OS einen eigenen Namen in der Oberfläche?
   Antwort:
8. **Läuft die App schon auf dem Server?** Google braucht für das Verbinden der Kalender die echte Adresse mit HTTPS
   (`https://mail.ylvalabs.de`). Und damit nie wieder jemand auf den Server muss, sollten „Updates per Knopf“ und die
   tägliche Sicherung beim Einrichten eingeschaltet worden sein (siehe [UEBERGABE.md](../UEBERGABE.md)).
   Antwort (01.11.2026): Ja, die App läuft unter `https://mail.ylvalabs.de`.
9. **Wer darf Personen und Rechte verwalten?** Vorschlag: Oskar und Finn als Gründer; Hamza und Omid nicht, bis ihr
   Status geklärt ist. In der App später jederzeit änderbar.
   Antwort (01.11.2026): So machen wir es, Oskar und Finn.
