#!/usr/bin/env bash
# Sichert alles, was nicht im Code steckt: Datenbank (Leads, CRM, Gehirn, Kosten), Push-Schlüssel,
# Sitzungsschlüssel, in der App geänderte Einstellungen (Passwörter darin verschlüsselt, mit Schlüssel) und hochgeladene
# Bilder. Die .env wird NICHT mitgesichert. Die Sicherung enthält trotzdem Zugangsdaten: sicher aufbewahren.
#
# Aufruf aus dem Projektordner:  bash deploy/backup.sh [Zielordner]
# Standard-Ziel: ~/ylva-backups, Sicherungen älter als 14 Tage werden gelöscht (KEEP_DAYS).
# Täglich automatisch: deploy/setup.sh richtet das auf Wunsch per cron ein.
set -euo pipefail
cd "$(dirname "$0")/.."

DEST="${1:-$HOME/ylva-backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date +%Y-%m-%d_%H%M)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$DEST" "$WORK/data"

SNAPSHOT='import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
src.backup(dst); dst.close(); src.close()'

if command -v docker >/dev/null 2>&1 && docker compose ps --status running --services 2>/dev/null | grep -qx mailer; then
  # Docker: konsistente Kopie der Datenbank im Container ziehen, dann herauskopieren
  docker compose exec -T mailer python -c "$SNAPSHOT" /data/mailer.db /data/.backup.db
  docker compose cp mailer:/data/.backup.db "$WORK/data/mailer.db" >/dev/null
  docker compose exec -T mailer rm -f /data/.backup.db
  for f in vapid_private.pem .secret_key settings.json .settings_key uploads; do
    docker compose cp "mailer:/data/$f" "$WORK/data/$f" >/dev/null 2>&1 || true
  done
elif [ -d /opt/ylva-mailer/data ]; then
  # Ohne Docker (deploy/install-lxc.sh)
  python3 -c "$SNAPSHOT" /opt/ylva-mailer/data/mailer.db "$WORK/data/mailer.db"
  for f in vapid_private.pem .secret_key settings.json .settings_key uploads; do
    cp -a "/opt/ylva-mailer/data/$f" "$WORK/data/$f" 2>/dev/null || true
  done
else
  echo "Keine laufende App gefunden (weder Docker-Container „mailer“ noch /opt/ylva-mailer)." >&2
  exit 1
fi

tar -czf "$DEST/ylva-mailer_$STAMP.tar.gz" -C "$WORK" data
chmod 600 "$DEST/ylva-mailer_$STAMP.tar.gz"
find "$DEST" -name 'ylva-mailer_*.tar.gz' -mtime +"$KEEP_DAYS" -delete
echo "Gesichert: $DEST/ylva-mailer_$STAMP.tar.gz"
