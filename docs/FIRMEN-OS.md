# Ylva Labs Firmen-OS – Bauplan

**Stand:** 01.11.2026 · Skizze, noch kein Code · Kalender-Entscheidung eingetragen · gehört zu [ROLLEN-UND-ABLAEUFE.md](ROLLEN-UND-ABLAEUFE.md)

Aus dem Ylva Labs Mailer (Akquise, CRM, Mailer) wird das Betriebssystem der Firma. Diese Datei hält fest, **was**
gebaut werden soll und **nach welchen Prinzipien**, damit alle, die am Code arbeiten (Menschen und KI), dasselbe
Bild im Kopf haben. Sie ist der Bauauftrag, auf den die Rollen-Datei verweist. Wer eine Baustufe umsetzt, hakt sie
hier ab und trägt Abweichungen ein.

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

---

## 2. Die Bausteine (Kernbegriffe)

Diese Begriffe gelten im Code, in der Oberfläche und in Gesprächen gleich.

| Begriff | Bedeutung | Gibt es schon? |
|---|---|---|
| **Person** | Mitarbeitende mit Login, Bereich(en), Rolle, Lernstufe, Kalender-Link | teilweise (`team`, ohne Omid, ohne Rollen) |
| **Bereich** | Produkt & Technik · Markt, Kunden & Betrieb · Unterbereich Akquise & Recherche. Hat eine verantwortliche Person und KPIs. Später teilbar. | nein |
| **Betrieb / Kunde** | Eine Firma vom ersten Lead bis zum laufenden Kunden. *Eine* Akte über die ganze Customer Journey. | ja als Lead (`leads`) |
| **Projekt (Build)** | Was für einen Kunden gebaut wird: Anforderung → Bau → Abnahme → Live | nein |
| **Änderungswunsch** | Wunsch eines Kunden nach dem Start, mit Größe, Zeit, Abrechnung | nein |
| **Aufgabe** | Etwas, das eine Person bis zu einem Termin erledigen muss. Kann an Kunde, Projekt, Lead hängen oder frei sein. | nur als Wiedervorlage an Leads (`tasks`) |
| **Termin** | Zeitpunkt mit Teilnehmenden, optional Kunde und Meet-Link | nein |
| **Freigabe** | Ein Vorschlag des OS, den ein Mensch annehmen, ändern oder ablehnen muss | nur implizit (Entwürfe) |
| **Ereignis** | Eintrag im Firmen-Protokoll. Rohstoff für alle Zahlen. | nur für Leads (`lead_events`) |
| **KPI** | Kennzahl je Bereich/Person, aus Ereignissen berechnet | nein (nur Lauf-Berichte, KI-Kosten) |
| **Ziel** | Wochenziel einer Person (z. B. Hamza: 40 qualifizierte Leads) | nein |
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
- Die Kalender-Berechtigungen gelten bei Google als „sensibel“. Solange die App nicht von Google geprüft ist:
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
- **Sicherheit und Datenschutz:** Kundendaten bleiben auf dem eigenen Server; AV-Verträge mit Kunden; Rechte je
  Rolle (Omid sieht z. B. keine Finanzen); Kunden-Zugang nur auf eigene Daten; Kalender-Token geheim und erneuerbar;
  `.env` nie ins Repository.
- **Tests:** Jede neue Regel (Freigabe-Stufe, Lernstufe, Abrechnungsregel, KPI-Berechnung) bekommt einen Test in
  `tests/`.
- **Sprache:** Oberfläche, Code-Begriffe der Fachlichkeit und Doku auf Deutsch, wie bisher.

---

## 8. Baustufen

Gebaut wird in kleinen Stufen, jede für sich nutzbar. Jede Stufe endet damit, dass das Team sie im Alltag benutzt.

- [ ] **Stufe 1 – Fundament: Aufgaben, Bereiche, Protokoll**
  Omid als Person anlegen, Rollen und Bereiche, Firmen-Protokoll (`ereignisse`) mit zentraler Log-Funktion,
  Aufgaben mit Bereich/Person/Termin (Wiedervorlagen übernehmen), Anfragen an andere Bereiche, Seite „Mein Tag“,
  Kommentare mit @-Erwähnung. *Ab hier gilt: Was nicht im OS steht, ist keine Aufgabe.*
- [ ] **Stufe 2 – Branchentest**
  Vier Testbranchen, warm/kalt, Zuordnung Hamza/Omid, Lead-Prüfung als Freigabe (Omid → Hamza → Stichprobe Oskar),
  Auswertung je Branche. *Zeitkritisch, weil der Test als Nächstes läuft.*
- [ ] **Stufe 3 – Kalender und Termine**
  Termine im OS, eigenen Google-Kalender verbinden (frei/belegt, Termine eintragen), Kundentermine mit Meet-Link
  und Einladung über das Google-Konto der zuständigen Person (Freigabe C), Terminvorschläge um alle herum,
  Erinnerungen, Ergebnis nach dem Termin, Briefing, Serientermin Wochengespräch. Danach iCloud und ICS-Abo.
- [ ] **Stufe 4 – KPIs und Wochengespräch**
  KPI-Katalog aus Ereignissen, Wochenziele, Cockpit, automatische Agenda fürs Wochengespräch, Lernstufen-Messung.
- [ ] **Stufe 5 – Kunden, Projekte, Abnahme**
  Customer Journey nach „Kunde“, Projekte, Anforderung und Abnahme-Checkliste, Zeiterfassung, Änderungswünsche mit
  Abrechnungsregeln, Kosten je Kunde.
- [ ] **Stufe 6 – Freigabe-Zentrale und Regeln als Daten**
  Alle Vorschläge in einer Freigabe-Warteschlange, Freigabe-Stufen und Entscheidungsrechte einstellbar,
  Entscheidungs-Log.
- [ ] **Stufe 7 – Kunden-Bereich**
  Portal mit Einmal-Link-Login, Termine, Projektstand, Änderungswünsche, Dokumente.
- [ ] **Stufe 8 – Finanzen**
  Rechnungen, laufende Kosten, Kassenstand, Geld-Regel als Ampel, Export für Steuerberater.
- [ ] **Stufe 9 – Ein Posteingang**
  Alle Kanäle an einer Stelle; Unipile für WhatsApp/LinkedIn, sobald Budget da ist.
- [ ] **Stufe 10 – Assistent**
  KI-Fragen an die Firmendaten, Vorschläge für Prioritäten, Entwürfe für Angebote, Briefings und Case Studys.

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
   Offen: Nutzen Finn, Hamza und Omid Google oder Apple?
   Antwort:
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
