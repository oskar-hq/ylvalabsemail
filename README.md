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
