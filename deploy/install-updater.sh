#!/usr/bin/env bash
# Richtet einmalig den Update-Dienst ein, damit Updates aus der Web-App (Einstellungen → Software) eingespielt werden
# können, ohne sich je auf den Server einzuloggen. deploy/setup.sh ruft das auf Wunsch automatisch auf.
#
# Aufruf im Projektordner auf dem Server:  sudo bash deploy/install-updater.sh
#
# Was passiert:
# - Ordner control/ als Austausch zwischen App und Server (die App darf dort nur Aufträge ablegen)
# - systemd: ylva-update.path reagiert auf Aufträge, ylva-update-check.timer sucht alle 6 Stunden nach Updates
# - die App erfährt über CONTROL_DIR, wo der Austauschordner liegt
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$(pwd)"

[ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo starten: sudo bash deploy/install-updater.sh" >&2; exit 1; }
command -v systemctl >/dev/null 2>&1 || { echo "Kein systemd gefunden. Updates dann von Hand: git pull && docker compose up -d --build" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || apt-get install -y python3 >/dev/null

if docker compose ps --services 2>/dev/null | grep -qx mailer; then RUNTIME=docker
elif [ -d /opt/ylva-mailer ]; then RUNTIME=lxc
else echo "Keine laufende App gefunden. Erst deploy/setup.sh oder deploy/install-lxc.sh ausführen." >&2; exit 1; fi

# Austauschordner: bei Docker control/ im Projekt (als /control im Container), ohne Docker neben der App,
# weil der Benutzer „mailer“ oft nicht in den Projektordner (z. B. unter /root) hineinkommt.
if [ "$RUNTIME" = lxc ]; then CONTROL=/opt/ylva-mailer/control; else CONTROL="$DIR/control"; fi
mkdir -p "$CONTROL"
if [ "$RUNTIME" = lxc ]; then chown mailer:mailer "$CONTROL"; else chown 1000:1000 "$CONTROL"; fi  # Container läuft als UID 1000
chmod 770 "$CONTROL"

if ! GIT_TERMINAL_PROMPT=0 git ls-remote -q origin >/dev/null 2>&1; then
  echo
  echo "Achtung: Der Server kommt ohne Passwortabfrage nicht an das GitHub-Repository."
  echo "Bei einem privaten Repository einmalig einen Lesezugang hinterlegen, z. B.:"
  echo "  git config credential.helper store && git fetch    (dann GitHub-Name und Token mit Leserecht eingeben)"
  echo "Bis dahin schlagen Updates aus der App mit „GitHub nicht erreichbar“ fehl."
  echo
fi

cat > /etc/systemd/system/ylva-update.service <<EOF
[Unit]
Description=Ylva Mailer: Auftrag aus der Web-App ausführen (Update suchen oder einspielen)
After=network-online.target docker.service

[Service]
Type=oneshot
WorkingDirectory=$DIR
Environment=YLVA_CONTROL=$CONTROL
ExecStart=/usr/bin/env bash $DIR/deploy/update.sh
TimeoutStartSec=30min
EOF

cat > /etc/systemd/system/ylva-update.path <<EOF
[Unit]
Description=Ylva Mailer: auf Aufträge aus der Web-App warten

[Path]
PathExists=$CONTROL/update-requested
PathExists=$CONTROL/check-requested
Unit=ylva-update.service

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/ylva-update-check.timer <<EOF
[Unit]
Description=Ylva Mailer: alle 6 Stunden nach Updates suchen

[Timer]
OnBootSec=5min
OnCalendar=*-*-* 00/6:17:00
Persistent=true
Unit=ylva-update.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now ylva-update.path ylva-update-check.timer >/dev/null

# Der App sagen, wo der Austauschordner liegt
if [ "$RUNTIME" = docker ]; then
  grep -q '^CONTROL_DIR=' .env 2>/dev/null || echo "CONTROL_DIR=/control" >> .env
  docker compose up -d >/dev/null 2>&1
else
  grep -q '^CONTROL_DIR=' /opt/ylva-mailer/.env 2>/dev/null || echo "CONTROL_DIR=$CONTROL" >> /opt/ylva-mailer/.env
  systemctl restart ylva-mailer
fi

YLVA_CONTROL="$CONTROL" bash deploy/update.sh check || true
echo "✓ Update-Dienst eingerichtet. Updates ab jetzt in der App unter Einstellungen → Software und Updates."
