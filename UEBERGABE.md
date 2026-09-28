# Übergabe: Ylva Labs Mailer mit KI-Akquise

Diese Datei ist für die Person, die den Server betreut, und für das Ylva-Labs-Team.
Sie fasst zusammen, was gebaut wurde, was noch offen ist und welche Fragen vor der
Installation geklärt werden müssen. Antworten könnt ihr direkt in diese Datei schreiben
(auf GitHub: Datei öffnen → Stift-Symbol → „Commit changes“) oder als Kommentar im Pull Request.

**Stand:** 28.09.2026

## Links

| Was | Wo |
|---|---|
| Repository | https://github.com/oskar-hq/ylvalabsemail |
| Pull Request mit allen Änderungen | https://github.com/oskar-hq/ylvalabsemail/pull/1 |
| Branch mit dem neuen Stand | `claude/peaceful-ramanujan-ls4osx` |
| Bisheriger Stand des Mailers | `claude/practical-dijkstra-nnnz1b` (es gibt keinen `main`-Branch) |
| Ausführliche Anleitung | [README.md](README.md), Abschnitte „KI-Akquise“, „Installation auf Proxmox“, „Zugriff über das Internet“ |
| Alle Einstellungen, kommentiert | [.env.example](.env.example) |
| Klickbare Vorschau mit Beispieldaten | https://claude.ai/artifact/NDSnMqjBbghuj9dqbCPXmg (privat, Oskar muss sie erst über „Teilen“ freigeben) |

Ist das Repository privat, muss Oskar dich unter *Settings → Collaborators* einladen,
damit du es sehen und klonen kannst.

## Worum es geht

Der **Ylva Labs Mailer** ist eine kleine Web-App (Python/Flask), die gestaltete HTML-Mails
über das eigene Postfach verschickt. Neu ist der Bereich **„03 Leads“**, eine KI-Akquise:

1. Sie sucht Betriebe im Umkreis von 50 km um Kappeln (OpenStreetMap).
2. Ein kostenloser Grobfilter sortiert Unpassendes aus. Danach prüft die KI in Stufen,
   von günstig nach teuer: Haiku sieht sich die Startseite an, Sonnet analysiert gründlicher,
   und Opus schreibt nur für die besten Betriebe einen Entwurf.
3. Das Team prüft jeden Entwurf, ändert ihn oder sagt der KI im Chat, was anders werden soll.
   **Nichts geht ohne Freigabe raus.**
4. Antworten der Firmen werden gelesen und als Qualified, Mittel oder Kein Interesse eingeordnet.
   Nach 7 Tagen ohne Antwort schlägt die App einmalig eine kurze Erinnerung vor.
5. Die KI lernt aus Feedback und Antworten (das „Gehirn“).
6. Die KI läuft **nur, wenn jemand einen Lauf startet**, mit Budget in Euro. Die Kosten sind
   jederzeit sichtbar, dazu gibt es ein Monatsbudget als harte Grenze.
7. Benachrichtigungen kommen per Push aufs Handy (installierbare Web-App, kein App Store) und per Mail.

## Was fertig ist und was nicht

- Fertig und getestet: 22 automatische Tests (`python -m unittest discover tests`).
  Die Oberfläche lief im Browser auf Desktop und Handy ohne Fehler.
- **Noch nie getestet:** mit einem echten KI-Schlüssel, mit der echten Kartensuche und mit
  dem echten Postfach. In der Entwicklungsumgebung waren diese Dienste gesperrt.
  Deshalb sollte der erste echte Lauf klein sein (siehe Schritt 7 unten).

---

## Fragen an dich (Server)

Bitte kurz beantworten, dann lässt sich die Installation passend vorbereiten.

1. **Was läuft auf dem Server?** Proxmox, Ubuntu/Debian direkt, Raspberry Pi oder etwas anderes?
   Antwort:
2. **Docker oder direkt?** Es gibt beide Wege: `docker compose` oder das Skript
   `deploy/install-lxc.sh` für einen Debian/Ubuntu-Container. Was ist dir lieber?
   Antwort:
3. **Ressourcen:** Die App ist sparsam. 1 CPU-Kern, 1 GB RAM und 5 GB Speicher reichen.
   Passt das?
   Antwort:
4. **Ausgehende Verbindungen:** Der Server muss diese Adressen erreichen können:
   - `api.anthropic.com` (KI)
   - `overpass-api.de` (Kartensuche)
   - beliebige Firmen-Webseiten über HTTP/HTTPS (Recherche)
   - den Mailserver von Strato (SMTP 587 oder 465, IMAP 993)
   - bei Push-Benachrichtigungen die Push-Dienste von Apple, Google und Mozilla

   Gibt es eine Firewall, die das einschränkt?
   Antwort:
5. **Zugriff von außen:** Für HTTPS und die Handy-App empfiehlt die README einen
   **Cloudflare Tunnel** mit Cloudflare Access davor. Dafür muss kein Port geöffnet werden.
   Ist das für dich in Ordnung, oder gibt es schon einen anderen Weg (Reverse Proxy, VPN)?
   Antwort:
6. **Backups:** Alle Daten liegen in einem Ordner: Datenbank, Gehirn und Schlüssel für Push.
   Bei Docker ist das das Volume `mailer-data`, sonst `/opt/ylva-mailer/data`.
   Wird dieser Ordner bereits gesichert, oder soll das eingerichtet werden?
   Antwort:
7. **Wer darf auf den Server?** Wer außer dir soll Updates einspielen können?
   Antwort:

## Offene Entscheidungen fürs Team (Oskar)

- [ ] **Rechtliches:** Werbe-Mails ohne vorherige Einwilligung sind nach § 7 UWG auch an Firmen
      unzulässig. Der Mailversand für den Erstkontakt ist deshalb aus (`OUTREACH_EMAIL=false`).
      Bis das geklärt ist (Anwalt oder IHK), laufen Erstkontakte per Brief oder Telefon.
- [ ] **KI-Schlüssel:** In console.anthropic.com Konto anlegen, Guthaben aufladen, API-Schlüssel
      erzeugen **und ein Ausgabenlimit setzen**. Den Schlüssel nur direkt in die `.env` auf dem
      Server eintragen, nie in Chats, Mails oder ins Repository.
- [ ] **Akquise-Postfach** bei Strato anlegen, z. B. `kontakt@ylvalabs.de`. Darüber gehen
      Nachrichten raus, und dort liest die App die Antworten.
- [ ] **Budget:** Monatsbudget festlegen (Standard 20 €, `LEADS_MONTHLY_BUDGET_EUR`).
- [ ] **Modelle:** Vorprüfung mit Haiku, Analyse mit Sonnet, Entwurf mit Opus. Passt das, oder soll
      auch das Schreiben günstiger mit Sonnet laufen (`AI_MODEL`)?
- [ ] **Antworten automatisch einordnen:** Kostet ca. 1–2 Cent pro echter Antwort und läuft
      ohne Knopfdruck. Beibehalten oder auch auf „nur von Hand“ stellen?
- [ ] **Pull Request** durchsehen und mergen. Danach sinnvoll: einen `main`-Branch anlegen und auf
      GitHub als Standard einstellen, damit der Server immer denselben Branch holt.
- [ ] **Gehirn prüfen:** In der App unter „Leads → Gehirn ansehen“ die Texte über Ylva Labs,
      Zielgruppe und Stil kontrollieren. Die Startfassung steht in `app/brain_seed.md`.

---

## Einrichtung Schritt für Schritt

Die Details stehen in der [README](README.md). Hier ist die kurze Reihenfolge.

**1. Code holen** (solange der Pull Request nicht gemergt ist, den Branch angeben):

```bash
git clone -b claude/peaceful-ramanujan-ls4osx https://github.com/oskar-hq/ylvalabsemail.git
cd ylvalabsemail
cp .env.example .env
chmod 600 .env
```

**2. `.env` ausfüllen.** Diese Werte werden gebraucht:

| Einstellung | Wert | Von wem |
|---|---|---|
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY` | `smtp.strato.de`, `587`, `starttls` | fest |
| `IMAP_HOST` | `imap.strato.de` | fest |
| `ALLOWED_DOMAINS` | `ylvalabs.de` | fest |
| `ANTHROPIC_API_KEY` | der Schlüssel | Oskar trägt ihn selbst ein |
| `OUTREACH_USER`, `OUTREACH_PASSWORD` | Akquise-Postfach | Oskar trägt es selbst ein |
| `NOTIFY_EMAILS` | wer benachrichtigt wird | Oskar |
| `BASE_URL` | z. B. `https://mail.ylvalabs.de` | nach Schritt 4 |
| `DEMO_MODE` | `false` | fest |
| `COOKIE_SECURE`, `TRUST_PROXY` | `true`, `cloudflare` | nach Schritt 4 |

Alles andere kann erst einmal so bleiben.

**3. Starten**, entweder mit Docker:

```bash
docker compose up -d --build
```

oder ohne Docker in einem Debian/Ubuntu-Container:

```bash
bash deploy/install-lxc.sh
```

Danach ist die App im Heimnetz unter `http://<IP>:8080` erreichbar.

**4. Zugriff übers Internet** mit Cloudflare Tunnel und Cloudflare Access, wie in der README
beschrieben. Achtung beim Umzug der Nameserver: Die MX-, SPF- und DKIM-Einträge von Strato
müssen übernommen werden, sonst kommen keine Mails mehr an.

**5. Anmelden** mit einem `@ylvalabs.de`-Postfach. Oben unter „03 Leads“ darf keine gelbe Warnung
mehr stehen. Tut sie es doch, steht darin, welche Einstellung fehlt.

**6. Handy:** In Safari `https://mail.ylvalabs.de/leads` öffnen → *Teilen* → *Zum Home-Bildschirm*.
Dann die App öffnen und auf **Benachrichtigungen an** tippen (ab iOS 16.4).

**7. Erster echter Lauf, bewusst klein:** 10 Vorprüfungen, 2 Entwürfe, 1 € Budget,
„Vorher neu suchen“ anhaken. Danach die Kosten unter „Kosten im Detail“ ansehen.

**Updates später:**

```bash
git pull && docker compose up -d --build
```

Ohne Docker: `git pull && bash deploy/install-lxc.sh`.

## Sicherheit, kurz

- Die `.env` enthält Passwörter und den KI-Schlüssel. Sie gehört nie ins Repository
  (steht in `.gitignore`) und bekommt die Rechte `chmod 600`.
- `DEMO_MODE=false` im Betrieb, `ALLOWED_DOMAINS=ylvalabs.de` gesetzt lassen.
- Die App braucht genau einen Gunicorn-Worker (ist so eingestellt), weil Anmeldungen und der
  Hintergrund-Arbeiter im Arbeitsspeicher dieses Prozesses laufen.
- Die KI liest nur öffentliche Webseiten. Adressen im eigenen Netz sind gesperrt, damit niemand
  über manipulierte Kartendaten Geräte im Heimnetz abfragen lassen kann.

## Ideen für später

- Test mit echtem Schlüssel und kleinem Budget, danach Schwellenwerte nachjustieren
  (`LEADS_QUICK_MIN`, `LEADS_ANALYSE_MIN`).
- Wochenbericht per Mail: neue Qualified, Kosten, was die KI gelernt hat.
- Export der Qualified-Leads als CSV, z. B. für ein CRM.
- Website ylvalabs.de (Repository `oskar-hq/kilab-`): offene Punkte aus deren README,
  z. B. „Enforce HTTPS“ in GitHub Pages aktivieren.
