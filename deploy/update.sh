#!/usr/bin/env bash
# Update-Dienst: führt Aufträge aus der Web-App aus („nach Updates suchen“, „Update einspielen“).
# Wird von systemd gestartet (ylva-update.path / ylva-update-check.timer, siehe deploy/install-updater.sh).
# Von Hand:  sudo bash deploy/update.sh check   bzw.   sudo bash deploy/update.sh update
#
# Ablauf beim Update: Sicherung → neuen Stand von GitHub holen (nur Vorspulen, nie überschreiben) → neu bauen und
# starten → warten, bis die App antwortet. Antwortet sie nicht, wird die alte Version wieder eingespielt.
set -uo pipefail

# Bash liest Skripte stückweise. Weil ein Update diese Datei selbst ändern kann, läuft eine Kopie.
if [ -z "${YLVA_UPDATE_COPY:-}" ]; then
  COPY="$(mktemp)"
  cp "$0" "$COPY"
  YLVA_UPDATE_COPY="$COPY" YLVA_UPDATE_DIR="$(cd "$(dirname "$0")/.." && pwd)" exec bash "$COPY" "$@"
fi
rm -f "$YLVA_UPDATE_COPY"  # die Kopie
DIR="$YLVA_UPDATE_DIR"
cd "$DIR"
# Austauschordner mit der App: bei Docker control/ im Projekt, ohne Docker /opt/ylva-mailer/control
# systemd setzt für Dienste kein HOME; git und die Sicherung brauchen es
export HOME="${HOME:-$(getent passwd "$(id -u)" | cut -d: -f6)}"
export HOME="${HOME:-/root}"
CONTROL="${YLVA_CONTROL:-$DIR/control}"
mkdir -p "$CONTROL"
exec 9>"$CONTROL/.lock"
flock -n 9 || { echo "Läuft schon."; exit 0; }
export GIT_TERMINAL_PROMPT=0

LOG="$(mktemp)"
BUILD_LOG="$CONTROL/build.log"  # vollständige Ausgabe von Docker/Installation, im Status nur die letzten Zeilen bei Fehlern
trap 'rm -f "$LOG"' EXIT
log() { echo "$(date '+%H:%M:%S') $*" | tee -a "$LOG"; }

write_json() {  # write_json <Datei> <python-Ausdruck mit Variablen aus argv>
  local target="$1"; shift
  python3 - "$@" > "$target.tmp" && chmod 644 "$target.tmp" && mv "$target.tmp" "$target"
}

status() {  # status <Zustand> <Meldung>
  write_json "$CONTROL/status.json" "$1" "$2" "$LOG" "${STARTED:-}" "${OLD:-}" "${NEW:-}" <<'PY'
import json, sys
from datetime import datetime
state, message, logfile, started, old, new = sys.argv[1:7]
lines = open(logfile, encoding="utf-8", errors="replace").read().splitlines()[-60:]
print(json.dumps({"state": state, "message": message, "started": started or None,
                  "finished": None if state == "running" else datetime.now().strftime("%d.%m.%Y %H:%M"),
                  "from": old[:7] or None, "to": new[:7] or None, "log": lines}, ensure_ascii=False))
PY
}

version() {  # aktuellen Stand und verfügbare Änderungen ablegen
  local branch upstream
  branch="$(git rev-parse --abbrev-ref HEAD)"
  upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || echo "origin/$branch")"
  local fetch_error=""
  git fetch -q origin "$branch" 2>>"$LOG" || fetch_error="GitHub nicht erreichbar oder kein Zugriff (siehe Protokoll)."
  write_json "$CONTROL/version.json" "$branch" "$upstream" "$fetch_error" <<'PY'
import json, subprocess, sys
from datetime import datetime
branch, upstream, fetch_error = sys.argv[1:4]
def git(*a):
    return subprocess.run(["git", *a], capture_output=True, text=True).stdout.strip()
fmt = "%h|%ad|%s"
cur = git("log", "-1", f"--format={fmt}", "--date=format:%d.%m.%Y").split("|", 2)
commits = []
for line in git("log", f"--format={fmt}", "--date=format:%d.%m.%Y", f"HEAD..{upstream}").splitlines()[:40]:
    sha, date, subject = line.split("|", 2)
    commits.append({"sha": sha, "date": date, "subject": subject})
behind = git("rev-list", "--count", f"HEAD..{upstream}")
print(json.dumps({"checked_at": datetime.now().isoformat(timespec="seconds"), "branch": branch,
                  "current": {"sha": cur[0], "date": cur[1] if len(cur) > 1 else "", "subject": cur[2] if len(cur) > 2 else ""},
                  "behind": int(behind or 0), "commits": commits, "error": fetch_error or None}, ensure_ascii=False))
PY
}

if docker compose ps --services 2>/dev/null | grep -qx mailer; then RUNTIME=docker
elif [ -d /opt/ylva-mailer ]; then RUNTIME=lxc
else RUNTIME=none; fi

deploy() {
  local rc=0
  : > "$BUILD_LOG"
  case "$RUNTIME" in
    docker) docker compose up -d --build >>"$BUILD_LOG" 2>&1 || rc=$? ;;
    lxc) bash deploy/install-lxc.sh >>"$BUILD_LOG" 2>&1 || rc=$? ;;
    *) log "Keine laufende App gefunden (weder Docker noch /opt/ylva-mailer)."; return 1 ;;
  esac
  chmod 644 "$BUILD_LOG"
  if [ "$rc" -ne 0 ]; then
    log "Bauen fehlgeschlagen. Letzte Zeilen:"
    tail -n 15 "$BUILD_LOG" | sed 's/^/    /' >> "$LOG"
  fi
  return "$rc"
}

app_errors() {  # warum startet die App nicht? Letzte Zeilen ihres Protokolls
  if [ "$RUNTIME" = docker ]; then docker compose logs --tail 12 mailer 2>&1; else journalctl -u ylva-mailer -n 12 --no-pager 2>&1; fi \
    | sed 's/^/    /' >> "$LOG"
}

healthy() {  # bis zu 2 Minuten warten, bis die App antwortet
  local probe='import urllib.request; urllib.request.urlopen("http://127.0.0.1:8080/healthz", timeout=5)'
  for _ in $(seq 1 40); do
    if [ "$RUNTIME" = docker ]; then
      docker compose exec -T mailer python -c "$probe" >/dev/null 2>&1 && return 0
    else
      python3 -c "$probe" >/dev/null 2>&1 && return 0
    fi
    sleep 3
  done
  return 1
}

# ---------------------------------------------------------------- Auftrag bestimmen
TASK="${1:-auto}"
if [ "$TASK" = auto ]; then
  if [ -f "$CONTROL/update-requested" ]; then TASK=update; else TASK=check; fi
fi
REQUESTER="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("by","?")[:80])' "$CONTROL/$TASK-requested" 2>/dev/null || echo "Server")"
rm -f "$CONTROL/update-requested" "$CONTROL/check-requested"

if [ "$TASK" = check ]; then
  version
  exit 0
fi

# ---------------------------------------------------------------- Update
STARTED="$(date '+%d.%m.%Y %H:%M')"
OLD="$(git rev-parse HEAD)"
NEW=""
log "Update angefordert von $REQUESTER (Betrieb: $RUNTIME)."
status running "Update läuft …"

log "Sicherung …"
if ! bash deploy/backup.sh >>"$LOG" 2>&1; then
  log "Sicherung fehlgeschlagen, Update abgebrochen."
  status failed "Sicherung fehlgeschlagen, nichts geändert."
  exit 1
fi

log "Neuen Stand von GitHub holen …"
if ! git fetch origin >>"$LOG" 2>&1; then
  log "GitHub nicht erreichbar oder kein Zugriff. Bei privatem Repository: Zugangsschlüssel einrichten (docs/SERVER-MIETEN.md)."
  status failed "GitHub nicht erreichbar, nichts geändert."
  exit 1
fi
if ! git merge --ff-only '@{u}' >>"$LOG" 2>&1; then
  log "Der Stand auf dem Server weicht von GitHub ab (lokale Änderungen?). Nichts geändert."
  git status --short >>"$LOG" 2>&1
  status failed "Lokale Änderungen auf dem Server verhindern das Update, nichts geändert."
  exit 1
fi
NEW="$(git rev-parse HEAD)"
if [ "$OLD" = "$NEW" ]; then log "Schon aktuell, baue trotzdem neu."; else
  git log --format='    %h %s' "$OLD..$NEW" >> "$LOG"; fi

log "Neu bauen und starten …"
if deploy && healthy; then
  log "Fertig. Die App läuft mit $(git rev-parse --short HEAD)."
  status done "Update auf $(git rev-parse --short HEAD) eingespielt."
  version
  exit 0
fi

log "Die neue Version startet nicht. Protokoll der App:"
app_errors
log "Spiele die alte Version $(git rev-parse --short "$OLD") wieder ein …"
git reset -q --keep "$OLD" >>"$LOG" 2>&1
if deploy && healthy; then
  log "Alte Version läuft wieder."
  status rolled_back "Neue Version startete nicht, die alte läuft wieder. Protokoll prüfen."
else
  log "Auch die alte Version startet nicht. Bitte auf dem Server nachsehen: docker compose logs mailer"
  status failed "App startet nicht. Bitte auf dem Server prüfen."
fi
version
exit 1
