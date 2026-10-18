"""Sicherungen in der Web-App: herunterladen, hochladen und zurückspielen, rückgängig machen.

Eine Sicherung ist ein .tar.gz mit einem Ordner data/ (dasselbe Format wie deploy/backup.sh):
    mailer.db, vapid_private.pem, .secret_key, uploads/…  und aus der App zusätzlich settings.json, .settings_key

Zurückspielen geht in zwei Schritten, damit keine Datenbank ausgetauscht wird, während die App sie offen hat:
1. Die hochgeladene Datei wird geprüft und nach restore-pending/ entpackt.
2. Beim nächsten Start (die App startet dafür kurz neu) wird sie eingesetzt, bevor irgendetwas die Datenbank öffnet.
   Der bisherige Stand landet in before-restore/ und lässt sich mit „Rückgängig“ zurückholen.
"""
import io
import json
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

FILES = ("mailer.db", "vapid_private.pem", ".secret_key", "settings.json", ".settings_key")
DIRS = ("uploads",)
PENDING, BEFORE, LOGFILE = "restore-pending", "before-restore", "restore-log.json"
MAX_BYTES = 500 * 1024 * 1024


class BackupError(ValueError):
    pass


def create(data_dir):
    """Sicherung als Bytes (.tar.gz). Die Datenbank wird über die SQLite-Backup-API konsistent kopiert."""
    data_dir = Path(data_dir)
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "mailer.db"
        src = sqlite3.connect(data_dir / "mailer.db")
        dst = sqlite3.connect(snap)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(snap, arcname="data/mailer.db")
            for name in FILES[1:]:
                if (data_dir / name).is_file():
                    tar.add(data_dir / name, arcname=f"data/{name}")
            for d in DIRS:
                if (data_dir / d).is_dir():
                    tar.add(data_dir / d, arcname=f"data/{d}")
        return buf.getvalue()


def _allowed(name):
    parts = Path(name).parts
    if not parts or parts[0] != "data" or ".." in parts or Path(name).is_absolute():
        return False
    if len(parts) == 1:
        return True
    if len(parts) == 2:
        return parts[1] in FILES or parts[1] in DIRS
    return parts[1] in DIRS and len(parts) == 3


def stage(data_dir, fileobj):
    """Hochgeladene Sicherung prüfen und für den nächsten Start bereitlegen. Gibt eine kurze Übersicht zurück."""
    data_dir = Path(data_dir)
    target = data_dir / PENDING
    shutil.rmtree(target, ignore_errors=True)
    try:
        tar = tarfile.open(fileobj=fileobj, mode="r:gz")
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise BackupError("Das ist keine gültige Sicherung (.tar.gz).") from exc
    total = 0
    with tar:
        members = tar.getmembers()
        for m in members:
            if not _allowed(m.name) or not (m.isfile() or m.isdir()):
                raise BackupError(f"Unerwarteter Inhalt in der Sicherung: {m.name}")
            total += m.size
            if total > MAX_BYTES:
                raise BackupError("Die Sicherung ist zu groß.")
        if not any(m.name == "data/mailer.db" and m.isfile() for m in members):
            raise BackupError("In der Sicherung fehlt die Datenbank (data/mailer.db).")
        target.mkdir(parents=True)
        for m in members:
            if m.isfile():
                dest = target / Path(m.name).relative_to("data")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(m) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
    try:
        conn = sqlite3.connect(target / "mailer.db")
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("Die Datenbank in der Sicherung ist beschädigt.")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "leads" not in tables:
                raise BackupError("Die Datenbank in der Sicherung gehört nicht zu dieser App.")
            leads = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise BackupError("Die Datenbank in der Sicherung ist nicht lesbar.") from exc
    except BackupError:
        shutil.rmtree(target, ignore_errors=True)
        raise
    files = sorted(str(p.relative_to(target)) for p in target.rglob("*") if p.is_file())
    return {"leads": leads, "files": files, "settings": "settings.json" in files}


def stage_undo(data_dir):
    """Den Stand vor dem letzten Zurückspielen wieder bereitlegen."""
    data_dir = Path(data_dir)
    before = data_dir / BEFORE
    if not (before / "mailer.db").is_file():
        raise BackupError("Es gibt keinen Stand vor dem letzten Zurückspielen.")
    target = data_dir / PENDING
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(before, target)


def _log(data_dir, entry):
    path = Path(data_dir) / LOGFILE
    try:
        log = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log = []
    log.insert(0, entry)
    path.write_text(json.dumps(log[:20], ensure_ascii=False, indent=1), encoding="utf-8")


def history(data_dir):
    try:
        return json.loads((Path(data_dir) / LOGFILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def pending(data_dir):
    return (Path(data_dir) / PENDING / "mailer.db").is_file()


def can_undo(data_dir):
    return (Path(data_dir) / BEFORE / "mailer.db").is_file()


def apply_pending(data_dir, by="Start"):
    """Beim Start, bevor die Datenbank geöffnet wird: bereitgelegte Sicherung einsetzen."""
    data_dir = Path(data_dir)
    src = data_dir / PENDING
    if not (src / "mailer.db").is_file():
        return False
    meta_path = src / ".restore.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    before = data_dir / BEFORE
    tmp_before = data_dir / (BEFORE + ".neu")
    shutil.rmtree(tmp_before, ignore_errors=True)
    tmp_before.mkdir()
    # bisherigen Stand aufheben
    for name in FILES:
        if (data_dir / name).is_file():
            shutil.copy2(data_dir / name, tmp_before / name)
    for d in DIRS:
        if (data_dir / d).is_dir():
            shutil.copytree(data_dir / d, tmp_before / d)
    # einsetzen
    for suffix in ("-wal", "-shm"):
        (data_dir / f"mailer.db{suffix}").unlink(missing_ok=True)
    for name in FILES:
        if (src / name).is_file():  # fehlt etwas in der Sicherung (z. B. ältere ohne settings.json), bleibt das Bisherige
            (src / name).replace(data_dir / name)
    for d in DIRS:
        if (src / d).is_dir():
            shutil.rmtree(data_dir / d, ignore_errors=True)
            (src / d).replace(data_dir / d)
    for name in (".secret_key", ".settings_key", "vapid_private.pem", "settings.json"):
        if (data_dir / name).is_file():
            (data_dir / name).chmod(0o600)
    shutil.rmtree(src, ignore_errors=True)
    shutil.rmtree(before, ignore_errors=True)
    tmp_before.replace(before)
    _log(data_dir, {"at": datetime.now().strftime("%d.%m.%Y %H:%M"), "by": meta.get("by", by),
                    "file": meta.get("file", ""), "kind": meta.get("kind", "restore")})
    return True


def mark(data_dir, by, filename, kind="restore"):
    (Path(data_dir) / PENDING / ".restore.json").write_text(
        json.dumps({"by": by, "file": filename, "kind": kind}, ensure_ascii=False), encoding="utf-8")
