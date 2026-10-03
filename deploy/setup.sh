#!/usr/bin/env bash
# Einrichtung in einem Rutsch: fragt die Zugangsdaten ab, schreibt die .env, startet die App mit Docker
# und macht zum Schluss den Systemcheck.
#
# Aufruf im Projektordner (auf dem Server, Linux):   sudo bash deploy/setup.sh
# Auf einem Mac zum Testen geht es ohne sudo.
#
# Drei Varianten:
#   1) Gemieteter Server (VPS) mit eigener Domain → automatisches HTTPS über Caddy
#   2) Heimserver / Proxmox → App im Heimnetz auf Port 8080 (HTTPS später per Cloudflare Tunnel, siehe README)
#   3) Eigener Rechner zum Ausprobieren → http://localhost:8080
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$(pwd)"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ask() {  # ask VAR "Frage" "Vorgabe"
  local __var=$1 __q=$2 __def=${3:-} __ans
  if [ -n "$__def" ]; then read -r -p "$__q [$__def]: " __ans; else read -r -p "$__q: " __ans; fi
  printf -v "$__var" '%s' "${__ans:-$__def}"
}
ask_secret() {  # ask_secret VAR "Frage"
  local __var=$1 __q=$2 __ans
  read -r -s -p "$__q: " __ans; echo
  printf -v "$__var" '%s' "$__ans"
}
confirm() { local a; read -r -p "$1 [j/N]: " a; [[ "$a" =~ ^[jJyY] ]]; }
check_value() {  # Werte werden in einfache Anführungszeichen gesetzt; die dürfen selbst nicht vorkommen
  if [[ "$2" == *"'"* ]]; then echo "Fehler: $1 darf kein einfaches Anführungszeichen (') enthalten." >&2; exit 1; fi
}

bold "Ylva Labs Mailer · Einrichtung"
echo
echo "Wo soll die App laufen?"
echo "  1) Gemieteter Server mit eigener Domain (z. B. mail.ylvalabs.de), automatisches HTTPS"
echo "  2) Heimserver oder Proxmox im Büronetz (HTTPS später per Cloudflare Tunnel)"
echo "  3) Eigener Rechner zum Ausprobieren"
ask MODE "Auswahl" "1"
case "$MODE" in 1|2|3) ;; *) echo "Bitte 1, 2 oder 3 eingeben." >&2; exit 1 ;; esac

# ---------------------------------------------------------------- Docker
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo
  echo "Docker (mit „docker compose“) ist nicht installiert."
  if [ "$(uname)" = "Linux" ] && confirm "Jetzt mit dem offiziellen Installationsskript von docker.com installieren?"; then
    if [ "$(id -u)" -ne 0 ]; then echo "Dafür bitte mit sudo starten: sudo bash deploy/setup.sh" >&2; exit 1; fi
    curl -fsSL https://get.docker.com | sh
  else
    echo "Bitte Docker installieren (Mac/Windows: Docker Desktop) und das Skript erneut starten." >&2
    exit 1
  fi
fi

# ---------------------------------------------------------------- Angaben
if [ -f .env ]; then
  echo
  if ! confirm "Es gibt schon eine .env. Neu anlegen? (die alte wird als .env.bak gesichert)"; then
    echo "Die vorhandene .env bleibt. Starte die App damit …"
    SKIP_ENV=1
  else
    cp .env .env.bak && chmod 600 .env.bak
  fi
fi

if [ -z "${SKIP_ENV:-}" ]; then
  echo
  bold "Adresse"
  DOMAIN=""
  if [ "$MODE" = 1 ]; then
    ask DOMAIN "Domain, unter der die App erreichbar sein soll" "mail.ylvalabs.de"
    BASE_URL="https://$DOMAIN"
    IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
    echo "→ Beim Domain-Anbieter (Strato) einen A-Eintrag anlegen: $DOMAIN → ${IP:-<IP dieses Servers>}"
    echo "  Nur diesen einen Eintrag hinzufügen, MX/SPF/DKIM für die Mails NICHT anfassen."
  elif [ "$MODE" = 2 ]; then
    ask BASE_URL "Spätere HTTPS-Adresse (leer lassen, wenn noch kein Cloudflare Tunnel da ist)" ""
  else
    BASE_URL="http://localhost:8080"
  fi

  echo
  bold "Mailserver (Strato)"
  ask SMTP_HOST "Postausgang (SMTP)" "smtp.strato.de"
  ask SMTP_PORT "Port" "465"
  if [ "$SMTP_PORT" = "465" ]; then SMTP_SECURITY=ssl; else SMTP_SECURITY=starttls; fi
  ask IMAP_HOST "Posteingang (IMAP)" "imap.strato.de"
  ask ALLOWED_DOMAINS "Welche Domain darf sich anmelden?" "ylvalabs.de"

  echo
  bold "Akquise-Postfach (darüber laufen Mails, dort werden Antworten gelesen)"
  ask OUTREACH_USER "E-Mail-Adresse" "kontakt@ylvalabs.de"
  ask_secret OUTREACH_PASSWORD "Passwort (wird nicht angezeigt)"
  ask NOTIFY_EMAILS "Wer bekommt Benachrichtigungen per Mail? (mehrere mit Komma)" "oskar@ylvalabs.de"
  ask SENDER "Name unter den Akquise-Nachrichten" "Oskar Jacobsen"
  ask FOOTER "Fußzeile der Mails (Firmenname und Anschrift wie im Impressum)" "Ylva Labs · Schleswig-Holstein"

  echo
  bold "KI"
  echo "Schlüssel aus console.anthropic.com → API Keys. Dort auch ein Ausgabenlimit setzen!"
  ask_secret ANTHROPIC_API_KEY "ANTHROPIC_API_KEY (leer lassen = später eintragen)"
  ask BUDGET "Monatsbudget für die KI in Euro (harte Grenze)" "20"

  for pair in "Passwort:$OUTREACH_PASSWORD" "Schlüssel:$ANTHROPIC_API_KEY" "Absendername:$SENDER" "Fußzeile:$FOOTER"; do
    check_value "${pair%%:*}" "${pair#*:}"
  done

  COOKIE_SECURE=false
  [[ "$BASE_URL" == https://* ]] && COOKIE_SECURE=true
  TRUST=none
  [ "$MODE" = 1 ] && TRUST=proxy

  umask 077
  {
    echo "# Angelegt von deploy/setup.sh am $(date '+%d.%m.%Y %H:%M'). Alle weiteren Einstellungen: siehe .env.example"
    [ "$MODE" = 1 ] && echo "COMPOSE_FILE=docker-compose.vps.yml" && echo "DOMAIN=$DOMAIN"
    echo "SMTP_HOST=$SMTP_HOST"
    echo "SMTP_PORT=$SMTP_PORT"
    echo "SMTP_SECURITY=$SMTP_SECURITY"
    echo "IMAP_HOST=$IMAP_HOST"
    echo "IMAP_PORT=993"
    echo "ALLOWED_DOMAINS=$ALLOWED_DOMAINS"
    echo "ORG_NAME=Ylva Labs"
    echo "DEFAULT_FOOTER='$FOOTER'"
    echo "DEMO_MODE=false"
    echo "COOKIE_SECURE=$COOKIE_SECURE"
    echo "TRUST_PROXY=$TRUST"
    echo "BASE_URL=$BASE_URL"
    echo
    echo "ANTHROPIC_API_KEY='$ANTHROPIC_API_KEY'"
    echo "OUTREACH_USER=$OUTREACH_USER"
    echo "OUTREACH_PASSWORD='$OUTREACH_PASSWORD'"
    echo "OUTREACH_EMAIL=false"
    echo "NOTIFY_EMAILS=$NOTIFY_EMAILS"
    echo "LEADS_SENDER_NAME='$SENDER'"
    echo "LEADS_MONTHLY_BUDGET_EUR=$BUDGET"
  } > .env
  chmod 600 .env
  echo
  echo "✓ .env geschrieben (nur für diesen Benutzer lesbar)."
  if [ "$MODE" = 1 ]; then
    echo
    confirm "Zeigt der A-Eintrag für $DOMAIN schon auf diesen Server? (sonst klappt das Zertifikat erst später)" || \
      echo "  Kein Problem: Caddy versucht es automatisch weiter, sobald der Eintrag da ist."
  fi
fi

# ---------------------------------------------------------------- Starten
echo
bold "Starte die App …"
docker compose up -d --build

echo -n "Warte auf den Start "
for _ in $(seq 1 30); do
  if docker compose exec -T mailer python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')" >/dev/null 2>&1; then
    echo " ✓"; break
  fi
  echo -n "."; sleep 2
done

echo
bold "Systemcheck"
docker compose exec -T -e LEADS_WORKER=true mailer python -m app.systemcheck || true

# ---------------------------------------------------------------- Sicherung
if [ "$MODE" != 3 ] && command -v crontab >/dev/null 2>&1; then
  echo
  if confirm "Jede Nacht um 3:30 automatisch sichern (nach ~/ylva-backups, 14 Tage aufbewahren)?"; then
    LINE="30 3 * * * cd $DIR && bash deploy/backup.sh >> $DIR/backup.log 2>&1"
    ( crontab -l 2>/dev/null | grep -v 'deploy/backup.sh' ; echo "$LINE" ) | crontab -
    echo "✓ Tägliche Sicherung eingerichtet."
  fi
fi

echo
bold "Fertig."
case "$MODE" in
  1) echo "Öffnen: https://${DOMAIN:-<deine Domain>}  (das Zertifikat kann beim ersten Aufruf ein paar Sekunden brauchen)" ;;
  2) echo "Im Heimnetz öffnen: http://$(hostname -I 2>/dev/null | awk '{print $1}'):8080"
     echo "Für die Handy-App fehlt noch HTTPS: Cloudflare Tunnel einrichten (README, „Zugriff über das Internet“)." ;;
  3) echo "Öffnen: http://localhost:8080" ;;
esac
echo "Anmelden mit einem @${ALLOWED_DOMAINS:-ylvalabs.de}-Postfach, dann „03 Leads“ → „Systemcheck“."
echo "Updates später:  git pull && docker compose up -d --build"
