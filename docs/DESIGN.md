# DESIGN.md — Design-System „Clean Finance"

> **Für Agents:** Dieses Dokument ist verbindlich. Baue jede UI exakt nach diesen Regeln. Wenn etwas hier nicht geregelt ist, entscheide im Geist der Grundprinzipien (Abschnitt 1) und wähle immer die *ruhigere, leerere, typografisch stärkere* Variante.
>
> Die Designsprache ist inspiriert von modernen Neobroker-Apps. **Keine fremden Logos, Markennamen, Wortmarken oder Icons übernehmen** — nur die Prinzipien.

---

## 1. Grundprinzipien

1. **Typografie ist das Design.** Große, fette, eng laufende Headlines tragen die Hierarchie. Keine Deko, keine Illustrationen, keine Farbflächen als Schmuck.
2. **Weiß ist Struktur.** Reines Weiß als Hintergrund. Abstände statt Linien — **keine Divider, keine Trennstriche**, fast keine Rahmen.
3. **Schwarz-Weiß + ein Akzent.** Die UI ist monochrom. Farbe nur mit Bedeutung: Akzent-Rot für die primäre Aktion, Grün/Rot für Kursentwicklung, Blau für Hilfe/Support-Links.
4. **Zwei Graustufen für Text.** Schwarz = Inhalt/Aktion. Kühles Hellgrau = Beschreibung, Label, inaktiv. Nichts dazwischen.
5. **Ein Screen, eine Aufgabe.** Flows werden in viele kleine Schritte zerlegt (Betrag → Intervall → Zusammenfassung → Bestätigen). Jeder Schritt hat eine große Frage als Titel.
6. **Daumenzone.** Primäre Aktionen schweben unten als große, abgerundete Buttons. Zurück-Button unten links, nicht oben.
7. **Bewegung erklärt Raum.** Alles bewegt sich physikalisch (Springs), nie linear, nie „verspielt". Siehe Abschnitt 9.

---

## 2. Farben (Tokens)

```css
:root {
  /* Flächen */
  --bg:               #FFFFFF;
  --surface:          #FFFFFF;   /* Karten = gleiche Farbe wie bg, nur Schatten/Border trennt */
  --surface-sunken:   #F2F4F6;   /* Suchfeld, aktiver Chip, Input-Hintergrund */
  --border-hairline:  #EEF0F2;   /* nur Kacheln, extrem dezent */

  /* Text */
  --text-primary:     #000000;
  --text-secondary:   #A8B0BA;   /* kühles Blaugrau: Untertitel, Labels, inaktive Tabs */
  --text-disabled:    #D0D5DB;   /* Platzhalter wie „0 €" */
  --icon-chevron:     #B9C0C8;

  /* Akzente */
  --accent:           #DE2E36;   /* Primär-CTA („Erstellen", „Weiter") */
  --accent-disabled:  #F4BEC8;   /* CTA deaktiviert: rosa, Text weiß */
  --accent-text:      #DE2E36;   /* kleine Eyebrow-Labels (z. B. Asset-Name über Titel) */
  --link:             #2F5BF5;   /* Support/Hilfe-Zeilen, Titel + Untertitel blau */
  --destructive:      #F01E1E;   /* Abmelden, Löschen */
  --positive:         #16C784;
  --negative:         #F0443A;

  /* Dunkle Elemente */
  --ink:              #141414;   /* schwebende Bottom-Buttons, Toast, Avatar, Toggle an */
  --on-ink:           #FFFFFF;

  /* Toggle */
  --toggle-off:       #BCC3CC;
}
```

**Dark Mode** (nicht im Original gezeigt, abgeleitet): `--bg #000`, `--surface #111`, `--surface-sunken #1C1C1E`, `--text-primary #FFF`, `--text-secondary #8A929C`, `--ink #FFFFFF` / `--on-ink #000` (Buttons invertieren). Akzentfarben bleiben.

---

## 3. Typografie

**Schrift:** Geometrisch-neutrale Grotesk mit kräftigem Bold.
- iOS/Web-Apple: `SF Pro Display` / `SF Pro Text`
- Web plattformneutral: `Inter` (Display-Schnitt / `font-optical-sizing: auto`), alternativ `Figtree`
- Zahlen immer `font-variant-numeric: tabular-nums;`

| Token | Größe / Zeilenhöhe | Gewicht | Tracking | Einsatz |
|---|---|---|---|---|
| `display-xl` | 64 / 68 | 800 | -0.03em | Betragseingabe („0 €") |
| `display` | 40 / 44 | 700 | -0.025em | Hero-Betrag, Kontostand |
| `title-xl` | 36 / 40 | 700 | -0.025em | Top-Tabs („Portfolio  Cash") |
| `title-l` | 34 / 40 | 700 | -0.02em | Screen-Titel („Einstellungen", „Konto") |
| `title-m` | 30 / 34 | 700 | -0.02em | Flow-Fragen / Zusammenfassungen, darf 2–3 Zeilen umbrechen |
| `headline` | 18 / 24 | 600 | -0.01em | Listen-Titel, Section-Header („Services") |
| `body` | 17 / 24 | 500 | 0 | Key-Value-Werte, Buttons |
| `subhead` | 15 / 20 | 500 | 0 | Untertitel unter Listen-Titeln (Farbe `--text-secondary`) |
| `caption` | 13 / 18 | 500 | 0 | Rechtstexte, Kleingedrucktes |
| `eyebrow` | 12 / 16 | 500 | 0 | Kontext über Titel (z. B. Asset-Name, in `--accent-text`) |
| `keypad` | 34 / 40 | 700 | 0 | Ziffern der Zahlentastatur |

Regeln:
- Titel immer **linksbündig**, nie zentriert (Ausnahme: Betragseingabe zentriert).
- Inaktive Tabs = gleiche Größe wie aktive, nur `--text-secondary`.
- Rechtstexte in `--text-secondary`, Links darin **schwarz + bold**, nicht unterstrichen.

---

## 4. Layout & Spacing

- **Raster:** 4-pt-Basis. Tokens: `4, 8, 12, 16, 20, 24, 32, 40, 48, 64`.
- **Seitenrand Mobile:** `32px` links/rechts (großzügig — das ist wichtig für den Look).
- **Titel-Abstand:** Grabber → Titel `56px`; Titel → erster Inhalt `64px`.
- **Listenzeilen:**
  - nur Titel: Zeilenhöhe `52px`, Abstand zwischen Zeilen ~`104px` Mittelpunkt-zu-Mittelpunkt bei Icon-Listen → praktisch `min-height: 64px` + `padding-block: 20px`
  - Titel + Untertitel: `padding-block: 22px`, Titel→Untertitel `6px`
- **Key-Value-Zeilen** (Zusammenfassung): Label links grau, Wert rechts schwarz `font-weight: 700`, Zeilenabstand `40px`, keine Linien.
- **Max-Breite Content (Web):** `1120px`; Hauptspalte ~ `640px`, rechte Spalte ~ `360px`.

---

## 5. Radien, Schatten, Flächen

```css
--radius-sm: 12px;   /* Chips, Suchfeld */
--radius-md: 20px;   /* Kacheln, Bottom-Buttons */
--radius-lg: 28px;   /* Sheets oben */
--radius-full: 9999px;

--shadow-float: 0 8px 24px rgba(0,0,0,0.10), 0 2px 6px rgba(0,0,0,0.06);  /* schwebende Buttons */
--shadow-tile:  0 4px 20px rgba(0,0,0,0.04);                               /* Kacheln */
--shadow-back:  0 6px 24px rgba(0,0,0,0.08);                               /* Zurück-Button weiß */
```

- Abgerundet = **kontinuierliche Kurven** (iOS: `.continuous`; Web: großzügige Radien, ggf. `corner-shape: squircle` wo unterstützt).
- Kacheln: `--surface`, `1px solid --border-hairline`, `--shadow-tile`. Fast unsichtbar — sie heben sich nur leicht ab.

---

## 6. Komponenten

### 6.1 Sheet (Grundcontainer jedes Unter-Screens)
- Jeder Unterbereich öffnet als **Bottom-Sheet**, nicht als Push.
- Oben zentriert ein **Grabber**: `36×5px`, `--radius-full`, `#D9DCE0`, `16px` vom oberen Rand.
- **Gestapelte Sheets:** Liegt ein Sheet auf einem anderen, zeige zwei Grabber übereinander (oberer schmaler/heller) als Tiefenhinweis.
- Hintergrund weiß, kein sichtbarer Header-Balken, Titel direkt im Inhalt.

### 6.2 Zurück-Button
- Schwebend unten links: `56×56px`, `--radius-md`, weiß, `--shadow-back`, Chevron-links schwarz `16px`, `strokeWidth 2.5`.
- Position: `left: 32px; bottom: 32px + safe-area`.

### 6.3 Primär-Button (CTA)
- Schwebend unten rechts, Breite ~ 50 % (`calc(50% - 40px)`), Höhe `64px`, `--radius-md`.
- Hintergrund `--accent`, Text weiß `body` 600, **Label linksbündig**, Icon rechtsbündig (Chevron `›` für „Weiter", Häkchen `✓` für finale Bestätigung).
- Deaktiviert: `--accent-disabled`, kein Schatten, nicht klickbar.

### 6.4 Bottom-Action-Bar (Home)
- Zwei gleich breite schwebende Buttons, `64px` hoch, `--radius-md`, Hintergrund `--ink`, Text weiß, Gap `12px`.
- Label links, Icon rechts (Lupe für „Suche", Doppelpfeil ↕ für „Überweisen").
- Darunter ein weicher weißer Verlauf (`linear-gradient(transparent, #fff 40%)`), damit Inhalte darunter wegfaden.

### 6.5 Listenzeile
- Optionales Icon links: `28px`, **gefüllte schwarze Kreis-Glyphe** mit weißem Symbol darin (Person, Schloss, Auge, Glocke, Dokument, „…").
- Text-Einzug bei Icon: Titel beginnt bei `x = 32 + 28 + 48 = 108px` (deutlicher Abstand Icon→Text).
- Titel `headline`, Untertitel `subhead` grau.
- Rechts: Chevron `›` in `--icon-chevron`, `14px`, vertikal zentriert.
- **Destruktive Zeile** („Abmelden"): Icon-Kreis rot gefüllt, Titel rot, kein Chevron.
- **Support-Zeile:** Icon-Kreis blau gefüllt, Titel + Untertitel in `--link`.

### 6.6 Kachel-Karussell
- Horizontal scrollbar, Kacheln `~140×112px`, `--radius-md`, Gap `12px`.
- Inhalt: Label oben in `--text-secondary` (`headline`-Größe, 500), darunter Wert/Icon schwarz.
- Letzte Kachel ragt **angeschnitten** aus dem rechten Rand → signalisiert Scrollbarkeit. Scroll-Snap auf Kachelstart.

### 6.7 Top-Tabs
- Zwei bis drei Wörter in `title-xl`, nebeneinander, Gap `16px`. Aktiv schwarz, inaktiv grau. Kein Unterstrich, kein Indikator.
- Rechts oben: **Avatar** `48px` Kreis, `--ink`, weißer Initial-Buchstabe bold. Inaktiver Zustand (anderer Tab aktiv): `--toggle-off`-grau.

### 6.8 Betragseingabe
- Titel oben links (`title-l`, z. B. „Spare"), Eyebrow darüber in Akzentfarbe.
- Betrag groß zentriert (`display-xl`), leer = `--text-disabled`, gefüllt = schwarz.
- **Eigene Zahlentastatur** (keine System-Tastatur): 3×4-Raster, Ziffern `keypad`, letzte Zeile: leer · 0 · Backspace-Pfeil ←. Keine Tastenhintergründe, nur Ziffern.
- CTA „Weiter" unten rechts, deaktiviert bis Betrag > 0.

### 6.9 Optionsliste (Auswahl-Schritt)
- Titel fasst bisherige Eingabe zusammen („Spare 12 €"). Darunter Zeilen mit Outline-Icon `28px` (2px Stroke), Titel, Chevron. Tap = nächster Schritt.

### 6.10 Zusammenfassung / Bestätigung
- Titel als vollständiger Satz in `title-m` (z. B. „Spare 12 € monatlich zum Monatsanfang und zahle mit Cash").
- Key-Value-Zeilen (Abschnitt 4). Editierbare Labels bekommen einen Chevron direkt hinter dem Label.
- Rechtstext darunter in `caption`.
- CTA mit Häkchen.

### 6.11 Toggle
- iOS-Stil, `52×32px`. An: Track `--ink`, Knopf weiß mit dünnem dunklem Rand. Aus: Track `--toggle-off`.

### 6.12 Toast
- Oben zentriert schwebende Kapsel, `--ink`, Höhe `48px`, `--radius-full`, Icon links in weißem Kreis, Text weiß `body` 600.
- Erscheint von oben (siehe Motion), verschwindet nach 2,5 s.

### 6.13 Transaktionszeile
- Logo links `32px` (ohne Hintergrund), Titel `headline`, darunter Datum + Status grau („8. Okt. · Abgebrochen").
- Betrag rechts schwarz bold, darunter optional Sekundärwert grau. Abgebrochene Einträge: Betrag durchgestrichen oder grau.

### 6.14 Privatsphäre-Modus („Beträge ausblenden")
- Beträge werden **nicht durch Punkte ersetzt**, sondern durch **verwürfelte/gespiegelte Glyphen gleicher Länge** → Layout springt nicht.
- Umschalten löst Toast aus.

### 6.15 Web / Desktop
- Header: Logo-Glyphe links, Suchfeld (`--surface-sunken`, `--radius-sm`, Platzhalter grau, Lupe links), rechts Nav-Wörter (aktiv schwarz, inaktiv grau) + Avatar-Kreis.
- Hauptspalte: Label „Portfolio" `headline`, Wert `display`, Veränderung darunter klein mit Pfeil.
- **Chart:** einzelne Linie, `1.5px`, schwarz, kein Fill, keine Gitternetzlinien außer sehr hellen horizontalen Hilfslinien; Vortagesschluss als **gepunktete** Linie; Achsenwerte rechts klein grau; Scrub-Punkt als grauer Kreis mit Wert-Label.
- Zeitraum-Chips (1D, 1W, 1M, 6M, 1Y, All): Text grau, aktiv = schwarz auf `--surface-sunken`-Pill.
- Rechte Spalte: Listen „Investments" / „Watchlist" mit kleinem Icon, Name, Preis grau darunter, Tagesperformance rechts in Grün/Rot. Sortier-Dropdown als kleines blaues Label.

---

## 7. Icons

- Stil: **SF Symbols** bzw. Lucide/Phosphor in passender Optik.
- Navigations-Icons in Listen: gefüllt, in schwarzem Kreis.
- Auswahl-Icons (z. B. Kalender-Varianten): Outline, 2px.
- Chevrons immer klein und grau. Nie farbige Icons außer bei Bedeutung (Support blau, Destruktiv rot).

---

## 8. Inhalt & Tonalität

- **Du-Ansprache**, kurze aktive Sätze („Verwalte deine Abos", „Erreiche uns rund um die Uhr").
- Titel = Substantiv oder vollständiger Satz, nie Fragezeichen-Floskeln.
- Zahlen deutsch formatiert: `12,00 €`, `2. Nov.`, Tausenderpunkt.

---

## 9. Motion — Apple-Style (verbindlich)

**Philosophie:** Bewegung ist physikalisch, unterbrechbar und zurückhaltend. Sie zeigt, *woher* etwas kommt und *wohin* es geht. Nie dekorativ, nie bouncy-verspielt.

### 9.1 Springs statt Durations
Alle Übergänge mit Spring-Kurven. Standardwerte:

| Token | Response / Damping | Einsatz |
|---|---|---|
| `spring-snappy` | 0.30 s / 0.85 | Button-Feedback, Toggles, Chips |
| `spring-smooth` | 0.45 s / 1.0 (kritisch gedämpft) | Sheets, Seitenwechsel, Layout-Änderungen |
| `spring-bouncy` | 0.50 s / 0.75 | Nur Erfolgsmomente (Häkchen nach Bestätigung) |

Web-Fallback, wenn keine Spring-Lib: `cubic-bezier(0.32, 0.72, 0, 1)` mit 400–500 ms (Sheets), `cubic-bezier(0.25, 1, 0.5, 1)` 250 ms (kleine Elemente). Empfohlen: **Framer Motion / Motion One** mit `type: "spring"`; iOS: SwiftUI `.spring(response:dampingFraction:)`.

### 9.2 Muster
- **Sheets:** fahren von unten ein (`translateY 100% → 0`, `spring-smooth`), Hintergrund-Screen skaliert leicht auf `0.94` und rundet oben ab, abdunkelnde Ebene `rgba(0,0,0,0.25)`. Per Grabber **ziehbar**, folgt dem Finger 1:1, Schließen ab 30 % Weg oder Velocity > 500 px/s. Mit Rubberbanding nach oben.
- **Flow-Schritte** (Betrag → Intervall → Zusammenfassung): neuer Inhalt kommt von rechts (`x: 24px → 0`, `opacity 0 → 1`), alter geht nach links (`x: 0 → -24px`, Fade). Kurze Wege, keine vollen Slides.
- **Press-State:** jeder tappbare Bereich skaliert beim Drücken auf `0.97` (Buttons) bzw. Zeile bekommt `--surface-sunken`-Hintergrund mit 120 ms Fade; Release mit `spring-snappy`.
- **Titel-Wechsel** (z. B. „Spare" → „Spare 12 €"): Text crossfade + leichter Blur (`blur 4px → 0`), Höhe animiert mit Layout-Animation.
- **Zahlen:** Beim Ändern rollen die Ziffern einzeln (Odometer/„Number Flow"-Effekt), tabular-nums, jede Ziffer mit leichtem Versatz (20 ms Stagger).
- **Betragseingabe:** jede neue Ziffer erscheint mit kurzem Scale `0.8 → 1` + Fade; bei ungültiger Eingabe horizontaler **Shake** (3 Schwingungen, 8px) + Haptik.
- **CTA aktivieren:** Farbe rosa → rot mit 200 ms Crossfade, minimaler Scale-Puls `1 → 1.02 → 1`.
- **Tabs (Portfolio/Cash):** Farbwechsel grau↔schwarz 200 ms, Inhalt darunter crossfade + horizontaler Shift 16px in Richtung des Tabs. Swipe zwischen Tabs möglich.
- **Toast:** von oben `y: -100% → 0` mit `spring-bouncy` light, Ausblenden nach oben mit Fade.
- **Privatsphäre-Toggle:** Ziffern verwürfeln sich kurz (Scramble-Animation ~300 ms) bevor sie im Endzustand stehen.
- **Karussell:** Momentum-Scroll mit Snap; Kacheln am Rand minimal kleiner/transparenter (parallax-subtil, optional).
- **Listen beim Erscheinen:** Zeilen staggered einblenden (`y: 8px → 0`, Fade, 30 ms Versatz, max. 8 Zeilen animiert).
- **Chart:** Linie zeichnet sich beim Laden von links nach rechts (`stroke-dashoffset`, 600 ms, ease-out). Beim Scrubben folgt der Punkt dem Finger ohne Verzögerung, Wert oben aktualisiert per Odometer.
- **Erfolg:** Nach „Erstellen" morpht der Button zur Häkchen-Kapsel, dann schließt das Sheet.

### 9.3 Haptik (iOS / Vibration API wo möglich)
- Leicht (`.light`) bei Keypad-Taps und Toggles.
- `.selection` beim Chart-Scrubben über Datenpunkte und beim Snap im Karussell.
- `.success` nach finaler Bestätigung, `.error` beim Shake.

### 9.4 Regeln
- Alles **unterbrechbar**: neue Geste übernimmt aktuelle Geschwindigkeit, kein Warten auf Animationsende.
- Nie mehr als ~500 ms für eine UI-Reaktion.
- Kein Bounce bei Navigations- oder Layout-Übergängen — nur bei Erfolg/Feedback.
- `prefers-reduced-motion`: Springs → 150 ms Crossfades, keine Translates/Scales, Odometer → direkter Wechsel, Chart ohne Zeichen-Animation.

---

## 10. Checkliste vor Abgabe (Agent)

- [ ] Reines Weiß, keine Divider, keine farbigen Flächen ohne Bedeutung
- [ ] Seitenrand 32px mobil
- [ ] Titel groß, bold, linksbündig, enges Tracking
- [ ] Nur zwei Textfarben (schwarz / kühles Grau) + Akzente mit Bedeutung
- [ ] Unter-Screens als Sheets mit Grabber, Zurück unten links
- [ ] CTA unten rechts schwebend, Label links / Icon rechts, Disabled-Zustand rosa
- [ ] tabular-nums, deutsche Zahlenformate
- [ ] Alle Übergänge Springs, Press-States auf allem Tappbaren, unterbrechbar
- [ ] `prefers-reduced-motion` berücksichtigt
- [ ] Keine fremden Logos/Marken
