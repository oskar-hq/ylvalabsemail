#!/usr/bin/env bash
# Installation ohne Docker, z. B. in einem Debian-/Ubuntu-LXC-Container auf Proxmox.
# Aufruf im Container als root aus dem Projektordner:  bash deploy/install-lxc.sh
set -euo pipefail

DEST=/opt/ylva-mailer
SRC="$(cd "$(dirname "$0")/.." && pwd)"

apt-get update
apt-get install -y python3 python3-venv rsync

id mailer >/dev/null 2>&1 || useradd --system --home "$DEST" --shell /usr/sbin/nologin mailer
mkdir -p "$DEST/data"
rsync -a --delete --exclude .git --exclude data --exclude .env --exclude .venv "$SRC/" "$DEST/"

python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install --quiet -r "$DEST/requirements.txt"

if [ ! -f "$DEST/.env" ]; then
  cp "$DEST/.env.example" "$DEST/.env"
  echo ">>> Bitte $DEST/.env anpassen (SMTP_HOST usw.) und dann: systemctl restart ylva-mailer"
fi
chown -R mailer:mailer "$DEST"
chmod 600 "$DEST/.env"

cp "$DEST/deploy/ylva-mailer.service" /etc/systemd/system/ylva-mailer.service
systemctl daemon-reload
systemctl enable --now ylva-mailer
systemctl restart ylva-mailer

echo ">>> Läuft auf http://$(hostname -I | awk '{print $1}'):8080"
