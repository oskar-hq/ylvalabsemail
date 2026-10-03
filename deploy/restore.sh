#!/usr/bin/env bash
# Spielt eine Sicherung aus deploy/backup.sh zurück (Docker-Variante), z. B. beim Umzug auf einen neuen Server.
# Aufruf aus dem Projektordner:  bash deploy/restore.sh ~/ylva-backups/ylva-mailer_<DATUM>.tar.gz
# Achtung: Ersetzt die aktuelle Datenbank (Leads, Gehirn, Kosten) durch die aus der Sicherung.
set -euo pipefail
cd "$(dirname "$0")/.."

ARCHIVE="${1:?Bitte die Sicherungsdatei angeben, z. B. bash deploy/restore.sh ~/ylva-backups/ylva-mailer_2026-10-03_0330.tar.gz}"
[ -f "$ARCHIVE" ] || { echo "Datei nicht gefunden: $ARCHIVE" >&2; exit 1; }
read -r -p "Aktuelle Daten durch $(basename "$ARCHIVE") ersetzen? [j/N]: " ok
[[ "$ok" =~ ^[jJyY] ]] || { echo "Abgebrochen."; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
tar -xzf "$ARCHIVE" -C "$WORK"
[ -f "$WORK/data/mailer.db" ] || { echo "In der Sicherung fehlt data/mailer.db." >&2; exit 1; }

docker compose up -d --no-recreate mailer >/dev/null   # Container muss existieren
docker compose stop mailer
# Reste des Schreib-Logs der alten Datenbank entfernen, sonst würden sie auf die zurückgespielte angewendet
docker compose run --rm --no-deps --user root --entrypoint rm mailer -f /data/mailer.db-wal /data/mailer.db-shm
docker compose cp "$WORK/data/." mailer:/data/
# Kopierte Dateien gehören sonst root; die App läuft als Benutzer „mailer“
docker compose run --rm --no-deps --user root --entrypoint chown mailer -R mailer:mailer /data
docker compose start mailer
echo "Zurückgespielt. Prüfen: docker compose exec mailer python -m app.systemcheck"
