# Auf dem eigenen PC ausprobieren

So läuft der Mailer mit Akquise und CRM auf deinem Laptop, ganz ohne Server. Gut für einen ersten
echten Test mit kleinem Budget, bevor alles auf einen Server umzieht.

**Was dabei nicht geht:** Push-Nachrichten aufs Handy (dafür braucht es HTTPS), und Läufe laufen nur,
solange der Laptop an ist. Alles andere funktioniert wie später auf dem Server.

## 1. Einmalig vorbereiten

1. **Docker Desktop** installieren: https://www.docker.com/products/docker-desktop/ und starten.
   Unten links in Docker Desktop muss „Engine running“ stehen.
2. **Nur Windows:** **Git für Windows** installieren (https://git-scm.com/download/win). Damit kommt
   „Git Bash“ mit, ein Terminal, in dem das Einrichtungs-Skript läuft.
3. **Code holen:** In einem Terminal (Mac: „Terminal“, Windows: „Git Bash“):

   ```bash
   git clone https://github.com/oskar-hq/ylvalabsemail.git
   cd ylvalabsemail
   ```

   Ist das Repository privat, fragt Git nach deinem GitHub-Login.

## 2. Einrichten und starten

```bash
bash deploy/setup.sh
```

- Bei „Wo soll die App laufen?“ **3** wählen (eigener Rechner).
- Bei den Fragen nach Mailserver, Postfach und Budget reichen meist die Vorschläge (einfach Enter drücken).
- Passwort und KI-Schlüssel werden beim Tippen nicht angezeigt. Das ist Absicht.
- Am Ende läuft automatisch der **Systemcheck** und sagt, ob etwas fehlt.

Dann im Browser **http://localhost:8080** öffnen und mit deinem `@ylvalabs.de`-Postfach anmelden.

## 3. Erster Lauf

1. „03 Leads“ öffnen. Oben bei „Systemcheck →“ sollte alles grün oder gelb sein.
2. Einen **kleinen** Lauf starten: 30 Betriebe bewerten, die besten 10 ins CRM, „Vorher neu suchen“ angehakt.
   Die Bewertung läuft nach festen Regeln und kostet nichts. Der erste Lauf lädt außerdem einmalig die Kreisgrenzen.
3. Nach ein paar Minuten liegen die besten Betriebe im CRM („04 CRM“). In jeder Akte steht, welche Regel wie viele
   Punkte gegeben hat. Unter „So wird bewertet“ seht ihr alle Regeln.
4. Für einen Betrieb einen Entwurf erstellen: mit der Vorlage (kostenlos) oder, falls ein KI-Schlüssel eingetragen
   ist, von der KI. Die Kosten der KI stehen unter „Kosten im Detail“.

## Stoppen, starten, aktualisieren

```bash
docker compose stop                       # anhalten
docker compose start                      # wieder starten
git pull && docker compose up -d --build  # auf den neuesten Stand bringen
```

Die Daten (Leads, CRM, Gehirn, Kosten) bleiben dabei erhalten. Sie liegen in einem Docker-Volume, nicht im Projektordner.

## Später auf den Server umziehen

1. Auf dem PC eine Sicherung machen: `bash deploy/backup.sh` (landet in `~/ylva-backups`).
2. Auf dem Server einrichten, siehe [SERVER-MIETEN.md](SERVER-MIETEN.md) oder [UEBERGABE.md](../UEBERGABE.md).
3. Die Sicherung zurückspielen, siehe „Sicherung zurückspielen“ in [SERVER-MIETEN.md](SERVER-MIETEN.md).

## Wenn etwas nicht klappt

| Meldung | Lösung |
|---|---|
| `docker: command not found` / „Cannot connect to the Docker daemon“ | Docker Desktop starten und warten, bis „Engine running“ steht. |
| Port 8080 belegt | Ein anderes Programm nutzt den Port. In `docker-compose.yml` `"8080:8080"` z. B. in `"8090:8080"` ändern und http://localhost:8090 öffnen. |
| Systemcheck: Postausgang/Posteingang „Fehlt“ | Adresse und Passwort des Akquise-Postfachs prüfen. Mit `bash deploy/setup.sh` neu eingeben. |
| Systemcheck: KI-Schlüssel „Hinweis“ | Normal ohne Schlüssel: Bewertung, CRM und Vorlage laufen trotzdem. Für KI-Entwürfe einen Schlüssel aus console.anthropic.com eintragen. |
| Systemcheck: Kreise „Hinweis“ | Die Kreisgrenzen kommen mit dem ersten Lauf. Echte Wachstumszahlen unter „CRM → Karte“ importieren. |
