# Ylva Labs Mailer

Ein kleines Web-Programm für den eigenen Server (z. B. Proxmox): anmelden, Vorlage und Bild wählen,
Empfänger, Betreff und Text eintippen. Beim Klick auf **Senden** wird die gestaltete HTML-Mail im
Ylva-Labs-Design gebaut und über das eigene Postfach verschickt.

Damit gehen aufwendige HTML-Designs, die normale Mailprogramme nicht bauen können.

## Was es kann

- **Anmeldung mit dem eigenen E-Mail-Konto.** Die Zugangsdaten werden direkt am Mailserver geprüft.
  Das Passwort liegt nur im Arbeitsspeicher, nie auf der Festplatte und nie im Cookie.
- **Zwei Vorlagen:** *Newsletter* (große Headline, Pixel-Band über die volle Breite) und *Kurz-Mail*.
- **Bild wählen:** das Original-Pixel-Band, 50 generierte Band-Varianten, 5 Pixel-Icons
  (Werkstatt, Sprache, Landwirtschaft, Talente, Förderer), ein eigenes Foto oder gar kein Bild.
  Eigene Bilder werden in die Mail eingebettet, sie hängen also nicht an einem externen Link.
- **Live-Vorschau** für Desktop und Handy, mit Betreff und Vorschautext wie im Posteingang.
- **Persönlich:** Jede Person bekommt eine eigene Mail und sieht die anderen Empfänger nicht.
  `Anna Muster <anna@firma.de>` füllt `{vorname}` mit „Anna“. Ohne Namen wird aus „Hallo {vorname},“ einfach „Hallo,“.
- **Einfache Formatierung** im Text: `**fett**`, `[Linktext](https://…)`, nackte Links, Leerzeile = neuer Absatz.
- Infozeilen (z. B. *Termin*, *Phase 1*), ein Button mit Link und ein zweiter Textblock nach dem Bild.
- **Testmail an mich** vor dem echten Versand.
- **Verlauf** aller gesendeten Mails. Eine alte Mail lässt sich mit einem Klick als Vorlage weiterverwenden.
- Entwürfe werden automatisch im Browser gespeichert.
- Optional wird eine Kopie im IMAP-Ordner „Gesendet“ abgelegt.
- Jede Mail enthält auch eine Textversion. Das ist gut für die Zustellbarkeit und für Leute, die nur Text lesen.

## KI-Akquise (Bereich „Leads“)

Unter **03 Leads** arbeitet eine KI für euch in der Akquise. Sie läuft **nie von selbst**: Ihr startet
jeden Lauf von Hand, mit Anzahl, höchstens so vielen Entwürfen und einem **Budget in Euro**. Vor dem Start
zeigt die Seite, was der Lauf ungefähr kostet. Während er läuft, seht ihr Tokens und Kosten live, und ihr
könnt ihn jederzeit stoppen. Dazu gibt es ein Monatsbudget als harte Grenze (`LEADS_MONTHLY_BUDGET_EUR`).

Ein Lauf arbeitet sich **von günstig nach teuer** vor. Nur wer eine Stufe besteht, kommt in die nächste,
damit nicht jede Webseite die teure Komplettanalyse bekommt:

| Stufe | Was passiert | Modell | ca. Kosten pro Betrieb |
|---|---|---|---|
| 1 · Suchen und Grobfilter | Betriebe aus OpenStreetMap im Umkreis von 50 km um Kappeln. Regeln sortieren ohne KI aus: Ketten, keine Webseite, nur Social-Media-Seite, Kleinstbranchen, Sperrliste und Branchen, die schon fünfmal nicht gepasst haben | keins | kostenlos |
| 2 · Vorprüfung | Nur die Startseite, grobe Punktzahl 0–100. Ab 50 geht es weiter | `claude-haiku-4-5` | ca. 0,3 Cent |
| 3 · Analyse | Startseite und Unterseiten (Impressum, Kontakt, Über uns). Passung, Zeitfresser, Ansprechperson. Ab 65 geht es weiter | `claude-sonnet-5` | ca. 3 Cent |
| 4 · Entwurf | Schreibt die Nachricht, nur für die besten der Analyse, und benachrichtigt euch | `claude-opus-5` | ca. 5 Cent |

Die Cent-Beträge sind Startwerte. Sobald es echte Messwerte gibt, rechnet die Schätzung mit euren
Durchschnitten. Unter **Kosten im Detail** steht, was jede Stufe und jeder Lauf gekostet hat.

Danach geht es so weiter:

1. **Prüfen:** Ihr lest den Entwurf, ändert ihn selbst oder sagt der KI im **Chat**, was anders werden soll
   (jede Überarbeitung zeigt ihre Kosten). Dann gebt ihr ihn frei: per Mail senden, als Brief drucken oder
   als „per Telefon“ markieren.
2. **Antworten lesen:** Die App liest das Akquise-Postfach, das ist kostenlos. Nur echte Antworten ordnet die
   KI als **Qualified**, **Mittel** oder **Kein Interesse** ein (ca. 1–2 Cent pro Antwort) und benachrichtigt
   euch. Wer keine Nachrichten mehr möchte, kommt auf eine Sperrliste.
3. **Lernen:** Alles, was ihr ändert, aussortiert oder im Chat sagt, und jede Antwort einer Firma landet im
   **Gehirn**. Am Ende jedes Laufs (oder per „Jetzt lernen“) schreibt die KI daraus den Abschnitt „Gelernt“
   fort. Branchen, bei denen es oft klappt, kommen häufiger dran, andere aber weiterhin ab und zu (15 % Zufall).

Die Übersicht ist nach Stand sortiert: *Qualified* ganz oben, dann *Mittel*, *Kein Interesse*,
*Zur Prüfung*, *Versendet*, *In Bearbeitung* und *Aussortiert*. Jeder Reiter lässt sich einzeln anklicken.
Das **Gehirn** (was die KI über Ylva Labs, die Zielgruppe und den Stil weiß) seht und bearbeitet ihr unter
„Gehirn ansehen“. Jede Fassung wird aufgehoben.

### Rechtliches vorab (wichtig)

Werbe-Mails an Firmen ohne deren vorherige Einwilligung sind in Deutschland nach **§ 7 UWG** unzulässig,
auch im B2B-Bereich. Es drohen Abmahnungen, und das Postfach kann als Spam-Quelle gesperrt werden.
Deshalb ist der Mailversand für den Erstkontakt **ausgeschaltet** (`OUTREACH_EMAIL=false`), bis ihr das
geklärt habt, z. B. mit einer Anwältin oder der IHK. Bis dahin taugt jeder Entwurf als **Brief**
(Knopf „Als Brief drucken“) oder als Leitfaden für einen **Anruf**. Beides ist im B2B-Bereich
bei mutmaßlichem Interesse erlaubt. Antworten per Mail werden trotzdem erkannt.

### Handy-App ohne App Store

Die Leads-Seite ist eine installierbare Web-App mit Push-Benachrichtigungen. Dafür braucht ihr
kein Apple-Entwicklerkonto.

- **iPhone (ab iOS 16.4):** `https://mail.ylvalabs.de/leads` in Safari öffnen → *Teilen* → *Zum Home-Bildschirm*.
  Die App „Ylva Leads“ öffnen, anmelden, **Benachrichtigungen an** tippen und erlauben.
- **Android / Desktop:** Seite öffnen und **Benachrichtigungen an** klicken. Installieren geht über das Browser-Menü.
- Voraussetzung ist HTTPS, also der Zugriff über das Internet (Cloudflare Tunnel, siehe unten).

### Einrichten (einmalig)

1. Server einrichten wie unter *Installation auf Proxmox* und *Zugriff über das Internet* beschrieben.
2. In der Anthropic Console (console.anthropic.com) ein Konto anlegen, Guthaben aufladen und einen
   API-Schlüssel erzeugen. Ihn in der `.env` als `ANTHROPIC_API_KEY` eintragen. Abgerechnet wird nach
   Verbrauch. Ein Lauf mit 30 Vorprüfungen und 5 Entwürfen kostet grob 0,50 bis 1 €. Tipp: In der
   Anthropic Console zusätzlich ein Ausgabenlimit setzen.
3. Ein eigenes Postfach für die Akquise anlegen (z. B. `kontakt@ylvalabs.de`) und als `OUTREACH_USER` /
   `OUTREACH_PASSWORD` eintragen. `IMAP_HOST` muss gesetzt sein, damit Antworten gelesen werden.
4. `NOTIFY_EMAILS` und `BASE_URL` setzen, Dienst neu starten.
5. Unter **03 Leads → Gehirn ansehen** die Texte prüfen und anpassen, dann den ersten **Lauf starten**,
   am besten klein (z. B. 10 Vorprüfungen, 2 Entwürfe, 1 € Budget).

Alle Einstellungen stehen kommentiert in `.env.example`. Mit `DEMO_MODE=true` lassen sich unter
„Leads“ Beispieldaten laden, um die Oberfläche ohne Schlüssel auszuprobieren.

## Installation auf Proxmox

### Variante A: Docker (empfohlen)

In einer VM oder einem LXC-Container mit Docker (bei LXC unter *Options → Features* „nesting“ aktivieren):

```bash
git clone https://github.com/oskar-hq/ylvalabsemail.git
cd ylvalabsemail
cp .env.example .env
nano .env                 # SMTP_HOST, SMTP_PORT, SMTP_SECURITY eintragen
docker compose up -d --build
```

Danach im Browser `http://<IP-des-Containers>:8080` öffnen.

Aktualisieren: `git pull && docker compose up -d --build`

### Variante B: direkt in einem Debian-/Ubuntu-LXC (ohne Docker)

```bash
git clone https://github.com/oskar-hq/ylvalabsemail.git
cd ylvalabsemail
bash deploy/install-lxc.sh
nano /opt/ylva-mailer/.env
systemctl restart ylva-mailer
```

Das Skript installiert die App nach `/opt/ylva-mailer` und richtet sie als systemd-Dienst ein.
Zum Aktualisieren `git pull` ausführen und das Skript erneut starten. `.env` und Verlauf bleiben erhalten.

## Konfiguration (`.env`)

| Variable | Bedeutung |
|---|---|
| `SMTP_HOST` / `SMTP_PORT` | Postausgangsserver, z. B. `smtp.ionos.de` / `587` |
| `SMTP_SECURITY` | `starttls` (Port 587), `ssl` (Port 465) oder `none` |
| `FROM_ADDRESS` | Nur nötig, wenn der Login-Name keine E-Mail-Adresse ist |
| `ALLOWED_DOMAINS` | Nur Postfächer dieser Domain dürfen sich anmelden, z. B. `ylvalabs.de` |
| `ALLOWED_USERS` | Zusätzlich oder stattdessen: einzelne erlaubte Adressen |
| `IMAP_HOST` / `IMAP_PORT` | Optional: legt eine Kopie im Ordner „Gesendet“ ab |
| `IMAP_SENT_FOLDER` | Leer = automatisch erkennen |
| `ORG_NAME` | Name im Logo und in der Signatur |
| `DEFAULT_SENDER_NAME`, `DEFAULT_FOOTER`, `DEFAULT_CLOSING` | Vorbelegte Felder im Formular |
| `SEND_DELAY` | Pause zwischen zwei Mails in Sekunden (Versandlimits des Providers) |
| `MAX_RECIPIENTS` | Maximale Zahl an Empfängern pro Versand |
| `COOKIE_SECURE` | `true`, sobald die App über HTTPS erreichbar ist |
| `TRUST_PROXY` | `cloudflare` hinter einem Cloudflare Tunnel, `proxy` hinter einem anderen Reverse Proxy, sonst `none` |
| `SESSION_HOURS` | Nach wie vielen Stunden man sich neu anmelden muss |
| `DEMO_MODE` | `true` zeigt „Demo ansehen“ auf der Anmeldeseite: alles ausprobieren ohne Postfach, statt zu senden wird die Mail als `.eml`-Datei geladen. Vor dem Betrieb im Internet auf `false` setzen. |

**Gmail / Microsoft 365:** Dort braucht man ein *App-Passwort*, oder SMTP-AUTH muss für das Postfach
freigeschaltet sein. Das normale Passwort wird bei aktivierter Zwei-Faktor-Anmeldung abgelehnt.

## Zugriff über das Internet (mail.ylvalabs.de)

Empfohlen ist ein **Cloudflare Tunnel** mit **Cloudflare Access** davor:

- Am Router wird **kein Port geöffnet**, und die IP-Adresse des Büros bleibt verborgen.
- HTTPS gibt es automatisch.
- Bevor jemand die Anmeldeseite überhaupt sieht, verlangt Cloudflare einen Einmal-Code per Mail an eine
  `@ylvalabs.de`-Adresse. Bots und Passwort-Rater kommen gar nicht bis zur App.
- Kostenlos bis 50 Personen.

Einrichtung, einmalig:

1. Kostenloses Konto auf cloudflare.com anlegen und `ylvalabs.de` hinzufügen. Cloudflare übernimmt
   dabei die vorhandenen DNS-Einträge. Prüfen, dass die **MX-, SPF- (TXT) und DKIM-Einträge von Strato**
   vollständig übernommen wurden, sonst kommen keine Mails mehr an.
2. Bei Strato in der Domainverwaltung die Nameserver von `ylvalabs.de` auf die beiden Nameserver ändern, die Cloudflare anzeigt.
3. In Cloudflare unter *Zero Trust → Networks → Tunnels* einen Tunnel anlegen und den angezeigten
   Installationsbefehl im Container ausführen. Als *Public Hostname* `mail.ylvalabs.de` → `http://localhost:8080` eintragen.
4. Unter *Zero Trust → Access → Applications* eine Anwendung für `mail.ylvalabs.de` anlegen, mit der Regel
   *Include → Emails ending in → @ylvalabs.de*.
5. In der `.env`: `COOKIE_SECURE=true`, `TRUST_PROXY=cloudflare`, `ALLOWED_DOMAINS=ylvalabs.de`, danach
   `systemctl restart ylva-mailer`.

## Sicherheit

- **Mehrere Postfächer:** Jede Person meldet sich mit ihrem eigenen Postfach an und verschickt darüber.
  Jede Person sieht nur ihren eigenen Verlauf. Mit `ALLOWED_DOMAINS=ylvalabs.de` kommen nur Firmen-Postfächer rein.
  Neue Kolleginnen und Kollegen brauchen also nur ein Strato-Postfach, in der App ist nichts einzurichten.
- Sperre bei Fehlversuchen: 5 falsche Anmeldungen pro IP in 10 Minuten, 10 pro Postfach in 15 Minuten.
  Jeder Fehlversuch wird mit IP-Adresse im Log vermerkt (`journalctl -u ylva-mailer`).
- `TRUST_PROXY` nur setzen, wenn die App wirklich hinter Cloudflare oder einem Proxy läuft. Sonst könnte
  man die Sperre mit gefälschten Headern umgehen.
- Alle Formulare sind gegen CSRF geschützt, und Eingaben werden in der Mail sauber maskiert.
- Nach einem Neustart des Dienstes muss man sich neu anmelden, weil das Passwort nur im Arbeitsspeicher liegt.

## Aufbau

```
app/main.py            Web-App: Login, Vorschau, Upload, Versand, Verlauf
app/emailbuild.py      Formularfelder → HTML-Mail + Textversion (MIME)
app/pixel.py           Pixel-Band und Pixel-Icons als mailtaugliche Tabellen
app/leads_views.py     KI-Akquise: Übersicht, Firma mit Chat, Gehirn, Web-App und Push
app/agent.py           Hintergrund-Arbeiter: suchen, recherchieren, Antworten lesen, lernen
app/ai.py              Fragen an die KI (Claude) mit festen Antwortformaten
app/finder.py          Betriebe aus OpenStreetMap, Webseiten lesen
app/leaddb.py          Datenbank der Leads und das Gehirn (Startfassung: app/brain_seed.md)
app/webpush.py         Push-Benachrichtigungen (VAPID, ohne App Store)
tests/                 python -m unittest discover tests
email_templates/       Die Mail-Vorlagen (Jinja2), Original-Band und Logo
templates/, static/    Oberfläche im Stil der Brand Guidelines
```

Eine neue Vorlage anlegen: eine HTML-Datei in `email_templates/` ablegen (am besten `brief.html` kopieren)
und in `app/emailbuild.py` unter `TEMPLATES` eintragen.

## Lokal entwickeln

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
SMTP_HOST=smtp.example.com flask --app app.main run --debug
```

Schrift: Inter Tight (SIL Open Font License, siehe `static/fonts/OFL-LICENSE.txt`).
