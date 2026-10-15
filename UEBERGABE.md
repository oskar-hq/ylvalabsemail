# Übergabe: Ylva Labs Mailer mit Akquise und CRM

Diese Datei ist für die Person, die den Server betreut, und für das Ylva-Labs-Team.
Sie fasst zusammen, was gebaut wurde, was noch offen ist und welche Fragen vor der
Installation geklärt werden müssen. Antworten könnt ihr direkt in diese Datei schreiben
(auf GitHub: Datei öffnen → Stift-Symbol → „Commit changes“) oder als Kommentar im Pull Request.

**Stand:** 15.10.2026

## Links

| Was | Wo |
|---|---|
| Repository | https://github.com/oskar-hq/ylvalabsemail |
| Aktueller Stand | Standard-Branch `claude/practical-dijkstra-nnnz1b` (es gibt keinen `main`; ein einfaches `git clone` holt automatisch den richtigen) |
| Einrichtung auf einem gemieteten Server | [docs/SERVER-MIETEN.md](docs/SERVER-MIETEN.md) |
| Ausprobieren auf dem eigenen PC | [docs/LOKAL-TESTEN.md](docs/LOKAL-TESTEN.md) |
| Ausführliche Anleitung | [README.md](README.md), Abschnitte „Akquise und CRM“, „Installation auf Proxmox“, „Zugriff über das Internet“ |
| Alle Einstellungen, kommentiert | [.env.example](.env.example) |
| Bisherige Pull Requests | [#1 KI-Akquise](https://github.com/oskar-hq/ylvalabsemail/pull/1), [#2 Regeln und CRM](https://github.com/oskar-hq/ylvalabsemail/pull/2) |

Ist das Repository privat, muss Oskar dich unter *Settings → Collaborators* einladen,
damit du es sehen und klonen kannst.

## Worum es geht

Der **Ylva Labs Mailer** ist eine kleine Web-App (Python/Flask), die gestaltete HTML-Mails
über das eigene Postfach verschickt. Dazu kommen zwei Bereiche:

**03 Leads: passende Betriebe finden**

1. Die App sucht Betriebe in ganz Schleswig-Holstein (OpenStreetMap), nur in passenden Branchen und nur mit E-Mail-Adresse.
2. Sie liest die Webseiten und bewertet jeden Betrieb **nach festen Regeln, ohne KI und kostenlos**
   (`app/scoring.py`). Zum Beispiel: sucht Personal fürs Büro +25, Kernbranche +20, 10–100 Mitarbeitende +15,
   wächst +10. Ketten, IT-Firmen und Behörden fallen raus. In der Akte steht jede Regel, die gegriffen hat.
3. Pro Lauf kommen nur die besten (Standard 25) ins CRM. Läufe startet ihr von Hand.
4. Antworten der Firmen werden nach Stichworten eingeordnet (Interesse, später, Absage, Abmeldung), auch ohne KI.
5. Nach 7 Tagen ohne Antwort schlägt die App einmalig eine Erinnerung vor.
6. Die Bewertung lernt aus euren Ergebnissen mit (bis zu ±10 Punkte je Branche).

**04 CRM: Kontakte im Blick**

- Pipeline von Neu bis Kunde mit Ziehen und Ablegen, Akte je Betrieb, Auftragswert, Wiedervorlagen.
- Karte mit Heatmap der passenden Betriebe und Wachstum je Kreis (Gewerbeanmeldungen, Baugenehmigungen).
- Firmen von Hand anlegen, CSV-Import und -Export.

**KI: nur zum Schreiben, nur auf Knopfdruck, optional**

- Entwurf, Chat zum Überarbeiten und Erinnerung schreibt die KI, wenn ihr es anfordert, mit sichtbaren Kosten und
  Monatsbudget. Ohne KI-Schlüssel gibt es eine feste Vorlage (im „Gehirn“ anpassbar).
- **Nichts geht ohne Freigabe raus.** Der Erstkontakt per Mail ist gesperrt, bis § 7 UWG geklärt ist (siehe unten).

Benachrichtigungen kommen per Push aufs Handy (installierbare Web-App, kein App Store) und per Mail.

## Was fertig ist und was nicht

- 38 automatische Tests sind grün (`python -m unittest discover tests`). Die Oberfläche lief im Browser auf
  Desktop und Handy ohne Fehler.
- Einrichtungs-Skript, Docker-Container, HTTPS über Caddy, Sicherung und Zurückspielen wurden
  echt in Docker durchgespielt.
- **Noch nie getestet:** die echte Kartensuche über ganz Schleswig-Holstein, echte Firmen-Webseiten, das
  echte Postfach und ein echter KI-Schlüssel. In der Entwicklungsumgebung waren diese Dienste gesperrt.
  Deshalb sollte der erste echte Lauf klein sein, danach die Gewichte unter „So wird bewertet“ nachjustieren.

---

## Fragen an dich (Server)

Bitte kurz beantworten, dann lässt sich die Installation passend vorbereiten.

1. **Was läuft auf dem Server?** Proxmox, Ubuntu/Debian direkt, Raspberry Pi oder etwas anderes?
   Antwort:
2. **Docker oder direkt?** Empfohlen ist Docker mit `deploy/setup.sh`. Ohne Docker gibt es
   `deploy/install-lxc.sh` für einen Debian/Ubuntu-Container. Was ist dir lieber?
   Antwort:
3. **Ressourcen:** Die App ist sparsam. 1 CPU-Kern, 1 GB RAM und 5 GB Speicher reichen.
   Passt das?
   Antwort:
4. **Ausgehende Verbindungen:** Der Server muss diese Adressen erreichen können:
   - `overpass-api.de` (Kartensuche und Kreisgrenzen)
   - beliebige Firmen-Webseiten über HTTP/HTTPS (für die Bewertung)
   - den Mailserver von Strato (SMTP 465 oder 587, IMAP 993)
   - `api.anthropic.com`, nur wenn KI-Entwürfe genutzt werden
   - bei Push-Benachrichtigungen die Push-Dienste von Apple, Google und Mozilla

   Gibt es eine Firewall, die das einschränkt?
   Antwort:
5. **Zugriff von außen:** Für HTTPS und die Handy-App gibt es zwei Wege: Caddy mit eigenem Zertifikat
   (wenn der Server eine öffentliche IP hat, Ports 80 und 443) oder ein **Cloudflare Tunnel** ohne offenen Port.
   Was passt bei dir?
   Antwort:
6. **Backups:** Alle Daten liegen in einem Ordner: Datenbank (Leads, CRM, Gehirn, Kosten) und Schlüssel für Push.
   Bei Docker ist das das Volume `mailer-data`, sonst `/opt/ylva-mailer/data`. `deploy/setup.sh` kann eine
   tägliche Sicherung einrichten. Reicht das, oder sicherst du ohnehin schon?
   Antwort:
7. **Wer darf auf den Server?** Wer außer dir soll Updates einspielen können?
   Antwort:

## Offene Entscheidungen fürs Team (Oskar)

- [ ] **Rechtliches:** Werbe-Mails ohne vorherige Einwilligung sind nach § 7 UWG auch an Firmen
      unzulässig. Der Mailversand für den Erstkontakt ist deshalb aus (`OUTREACH_EMAIL=false`).
      Bis das geklärt ist (Anwalt oder IHK), laufen Erstkontakte per Brief oder Telefon. Nach einer Antwort
      des Betriebs sind gestaltete Mails möglich.
- [ ] **Akquise-Postfach** bei Strato anlegen, z. B. `kontakt@ylvalabs.de`. Darüber gehen
      Nachrichten raus, und dort liest die App die Antworten.
- [ ] **KI ja oder nein:** Ohne Schlüssel läuft alles außer KI-Entwürfen. Wer die KI nutzen will:
      in console.anthropic.com Konto anlegen, API-Schlüssel erzeugen **und ein Ausgabenlimit setzen**.
      Den Schlüssel nur direkt in die `.env` auf dem Server eintragen, nie in Chats, Mails oder ins Repository.
- [ ] **Wo läuft die App?** Server des Freundes oder ein eigener gemieteter Server (ca. 4–6 € im Monat,
      siehe [docs/SERVER-MIETEN.md](docs/SERVER-MIETEN.md)). Empfehlung: mieten, dann ist niemand
      auf fremde Hardware und Zeit angewiesen.
- [ ] **Gehirn und Vorlage prüfen:** In der App unter „Leads → Gehirn und Vorlage“ die Texte über Ylva Labs,
      Zielgruppe, Stil und die Vorlage ohne KI kontrollieren. Die Startfassung steht in `app/brain_seed.md`.
- [ ] **Wachstumszahlen** (optional): Gewerbeanmeldungen und Baugenehmigungen je Kreis als CSV vom
      Statistikamt Nord laden und unter „CRM → Karte“ importieren. Bis dahin gibt es keinen Wachstumszuschlag.
- [ ] Optional: den Standard-Branch auf GitHub in `main` umbenennen (Settings → Branches), das ist
      übersichtlicher. Server holen sich dann einfach den neuen Namen per `git pull`.

---

## Einrichtung Schritt für Schritt

Das meiste erledigt das Einrichtungs-Skript `deploy/setup.sh`. Es fragt die Zugangsdaten und das Suchgebiet ab,
schreibt die `.env` (nur für root lesbar), installiert bei Bedarf Docker, startet die App, macht den Systemcheck
und richtet auf Wunsch die tägliche Sicherung ein.

```bash
git clone https://github.com/oskar-hq/ylvalabsemail.git
cd ylvalabsemail
sudo bash deploy/setup.sh
```

Das Skript fragt zuerst, wo die App läuft:

| Auswahl | Wann | HTTPS |
|---|---|---|
| 1 · Gemieteter Server | VPS mit eigener IP, z. B. Hetzner, netcup, IONOS | automatisch über Caddy (`docker-compose.vps.yml`). Nur ein A-Eintrag `mail.ylvalabs.de` → Server-IP bei Strato nötig |
| 2 · Heimserver / Proxmox | Server im Heim- oder Büronetz | später per Cloudflare Tunnel (README, „Zugriff über das Internet“) |
| 3 · Eigener Rechner | zum Ausprobieren | keins, http://localhost:8080 |

Die Zugangsdaten tippt am besten Oskar selbst ein, wenn das Skript danach fragt (Passwort des
Akquise-Postfachs, optional KI-Schlüssel). Sie werden beim Tippen nicht angezeigt und landen nur in der `.env`.

**Danach:**

1. Die App öffnen, mit einem `@ylvalabs.de`-Postfach anmelden, „03 Leads“ → **Systemcheck**. Dort steht für
   jeden Punkt, ob er passt und was sonst zu tun ist. Im Terminal geht dasselbe mit
   `docker compose exec mailer python -m app.systemcheck`.
2. **Handy:** In Safari `https://mail.ylvalabs.de/leads` öffnen → *Teilen* → *Zum Home-Bildschirm*,
   App öffnen und auf **Benachrichtigungen an** tippen (ab iOS 16.4).
3. **Erster echter Lauf, bewusst klein:** 30 Betriebe bewerten, die besten 10 ins CRM, „Vorher neu suchen“ anhaken.
   Danach unter „So wird bewertet“ prüfen, ob die Punkte passen, und im CRM die ersten Kontakte anlegen.

**Wichtige Befehle:**

```bash
git pull && docker compose up -d --build                 # Update einspielen
docker compose logs -f mailer                            # Protokoll ansehen
bash deploy/backup.sh                                    # Sicherung nach ~/ylva-backups
bash deploy/restore.sh ~/ylva-backups/<Datei>.tar.gz     # Sicherung zurückspielen (z. B. beim Umzug)
```

Ohne Docker (Debian/Ubuntu-Container, z. B. Proxmox-LXC) geht es weiterhin mit `bash deploy/install-lxc.sh`
und der `.env` von Hand, siehe README.

## Sicherheit, kurz

- Die `.env` enthält Passwörter und den KI-Schlüssel. Sie gehört nie ins Repository
  (steht in `.gitignore`) und bekommt die Rechte `chmod 600`.
- `DEMO_MODE=false` im Betrieb, `ALLOWED_DOMAINS=ylvalabs.de` gesetzt lassen.
- Die App braucht genau einen Gunicorn-Worker (ist so eingestellt), weil Anmeldungen und der
  Hintergrund-Arbeiter im Arbeitsspeicher dieses Prozesses laufen.
- Webseiten werden nur unter öffentlichen Adressen gelesen. Adressen im eigenen Netz sind gesperrt, damit niemand
  über manipulierte Kartendaten Geräte im Heimnetz abfragen lassen kann.

## Ideen für später

- Nach den ersten echten Läufen die Gewichte der Regeln nachjustieren (`app/scoring.py`, `LEADS_RULE_MIN`, Standard 50).
- Wochenbericht per Mail: neue Kontakte im CRM, fällige Wiedervorlagen, Antworten.
- Website ylvalabs.de (Repository `oskar-hq/kilab-`): offene Punkte aus deren README,
  z. B. „Enforce HTTPS“ in GitHub Pages aktivieren.
