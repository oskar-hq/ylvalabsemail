# Eigenen Server mieten und einrichten

Für die App reicht der kleinste Server, den die üblichen Anbieter haben: **1–2 CPU-Kerne, 2 GB RAM,
20 GB Speicher**. Das kostet grob 4–6 € im Monat; aktuelle Preise beim Anbieter prüfen. Einen eigenen
Rechner zu kaufen lohnt sich dafür nicht.

Danach dauert die Einrichtung etwa 20 Minuten. HTTPS (nötig für die Handy-App) richtet sich dabei
automatisch ein. Cloudflare oder Änderungen an euren Mail-Einträgen braucht es nicht.

## 1. Server bestellen

- **Anbieter mit Rechenzentrum in Deutschland**, z. B. Hetzner Cloud, netcup oder IONOS. Das ist gut für die DSGVO.
  Mit dem Anbieter einen Vertrag zur Auftragsverarbeitung (AVV) abschließen; das geht meist mit einem Klick im Kundenkonto.
- **Betriebssystem:** Ubuntu 24.04 (oder Debian 12).
- **Anmeldung per SSH-Schlüssel** statt Passwort, wenn der Anbieter das anbietet.
- Optional: automatische Sicherungen („Backups“ / „Snapshots“) beim Anbieter dazubuchen, kostet meist 20 % extra.

Nach der Bestellung zeigt der Anbieter die **IP-Adresse** des Servers (z. B. `203.0.113.10`).

## 2. Domain auf den Server zeigen lassen

Bei Strato in der Domainverwaltung von `ylvalabs.de` **einen** neuen DNS-Eintrag anlegen:

| Typ | Name | Wert |
|---|---|---|
| A | `mail` | die IP-Adresse des Servers |

Damit ist die App später unter `https://mail.ylvalabs.de` erreichbar. Die Mail-Einträge (MX, SPF, DKIM)
**nicht** anfassen. Bis der Eintrag überall bekannt ist, kann es bis zu einer Stunde dauern.

## 3. Auf dem Server einrichten

Mit dem Server verbinden (Mac/Linux: Terminal, Windows: PowerShell oder Git Bash):

```bash
ssh root@<IP-des-Servers>
```

Dann nacheinander:

```bash
apt update && apt upgrade -y
apt install -y git ufw
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable

git clone https://github.com/oskar-hq/ylvalabsemail.git
cd ylvalabsemail
bash deploy/setup.sh
```

- Bei „Wo soll die App laufen?“ **1** wählen (gemieteter Server).
- Domain: `mail.ylvalabs.de`.
- Das Skript bietet an, Docker zu installieren: mit **j** bestätigen.
- Zugangsdaten eintippen: Akquise-Postfach, Suchgebiet (Standard: ganz Schleswig-Holstein), optional KI-Schlüssel und Monatsbudget.
- Das Skript fragt, wer **Admin** ist (darf Einstellungen ändern und Updates einspielen).
- Am Ende bietet es an, **Updates per Knopf in der App** einzurichten und die **tägliche Sicherung**: beides mit **j** bestätigen.
  Danach muss niemand mehr auf den Server; Einstellungen und Updates gehen in der App.

**Privates Repository:** Der Server braucht dauerhaft einen Lesezugang, sonst schlagen Updates aus der App fehl.
Auf GitHub einen „Fine-grained personal access token“ anlegen (nur dieses Repository, nur „Contents: Read“) und
**vor** dem `git clone` einmal `git config --global credential.helper store` ausführen. Beim `git clone` dann den
GitHub-Namen und als Passwort den Token eingeben; er wird in `/root/.git-credentials` gespeichert (nur für root lesbar).

## 4. Fertig

- `https://mail.ylvalabs.de` öffnen und mit einem `@ylvalabs.de`-Postfach anmelden.
- „03 Leads“ → „Systemcheck →“: Alles sollte grün sein. Gelb heißt Hinweis, Rot heißt, dass etwas fehlt;
  dabei steht jeweils, was zu tun ist.
- Auf dem iPhone: In Safari `https://mail.ylvalabs.de/leads` öffnen → *Teilen* → *Zum Home-Bildschirm*,
  App öffnen, **Benachrichtigungen an** tippen.

## Alltag

Im Alltag läuft alles über die App: **Einstellungen** und **Einstellungen → Software und Updates**.
Für den Notfall die Befehle auf dem Server:

```bash
cd ~/ylvalabsemail                         # bzw. /root/ylvalabsemail
sudo bash deploy/update.sh update          # Update einspielen (wie der Knopf in der App)
git pull && docker compose up -d --build   # Update von Hand, ohne Sicherung und Zurückrollen
docker compose logs -f mailer              # Protokoll ansehen (Strg+C beendet)
docker compose exec mailer python -m app.systemcheck   # Systemcheck im Terminal
bash deploy/backup.sh                      # Sicherung von Hand
```

Sicherungen liegen in `~/ylva-backups`, 14 Tage lang. Ab und zu eine davon auf einen anderen Rechner
kopieren, z. B. mit `scp root@<IP>:ylva-backups/*.tar.gz .`

## Sicherung zurückspielen

Am einfachsten in der App: **Einstellungen → Sicherung**. Dort lässt sich der aktuelle Stand herunterladen und eine
Sicherung hochladen (aus der App oder von `deploy/backup.sh`). Die App prüft die Datei, startet kurz neu und setzt
sie ein; der vorherige Stand lässt sich mit „Rückgängig“ zurückholen. So geht auch ein Umzug ohne Terminal:
auf dem alten Server herunterladen, auf dem neuen hochladen.

Ohne App, zum Beispiel beim Umzug vom eigenen PC oder vom Heimserver auf den gemieteten Server:

```bash
cd ~/ylvalabsemail
bash deploy/restore.sh ~/ylva-backups/ylva-mailer_<DATUM>.tar.gz
```

Die Sicherungsdatei vorher auf den Server kopieren, z. B. vom PC aus mit
`scp ylva-mailer_<DATUM>.tar.gz root@<IP>:ylva-backups/`.

Danach sind Leads, CRM, Gehirn, Kosten und angemeldete Geräte wieder da. Die Handys müssen sich
nach einem Umzug auf eine andere Adresse einmal neu für Benachrichtigungen anmelden.

## Sicherheit

- Die `.env` enthält Passwörter und den KI-Schlüssel. Sie ist nur für root lesbar und gehört nie ins Repository.
- Von außen sind nur die Ports 80 und 443 (Caddy) und 22 (SSH) offen. Die App selbst ist nicht direkt erreichbar.
- Wer zusätzlich eine Schranke vor der Anmeldeseite möchte: Cloudflare Access wie in der README beschrieben,
  oder die Anmeldeseite nur aus bestimmten Netzen erlauben.
- `apt upgrade` und `git pull && docker compose up -d --build` gelegentlich ausführen, damit alles aktuell bleibt.
