"""Backups: one self-contained tar.gz per run holding a consistent SQLite snapshot,
every archived PDF, every uploaded expense document and a manifest with SHA-256 checksums.

The backup directory is meant to be picked up by whatever off-site mechanism is
chosen later (rclone, restic, a USB disk, ...). See docs/BACKUP.md.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import archive, db, expenses, system
from .config import Settings

BACKUP_RE = re.compile(r"^invoices-backup-\d{8}-\d{6}\.tar\.gz$")


class BackupError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    return archive.sha256_file(path)


def create_backup(settings: Settings) -> Path:
    """Create a backup and record the run, successful or not, in control_runs."""
    try:
        target = _create_backup(settings)
    except Exception as e:
        _record(settings, "backup", False, str(e))
        raise
    _record(settings, "backup", True, target.name)
    return target


def _record(settings: Settings, kind: str, ok: bool, detail: str) -> None:
    conn = db.connect(settings.db_path)
    try:
        db.init_db(conn)
        system.control_run(conn, kind, ok, detail)
    finally:
        conn.close()


def _create_backup(settings: Settings) -> Path:
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = settings.backup_dir / f"invoices-backup-{stamp}.tar.gz"
    if target.exists():
        raise BackupError(f"{target.name} existiert bereits.")

    with tempfile.TemporaryDirectory(dir=settings.backup_dir, prefix=".tmp-") as tmp:
        snapshot = Path(tmp) / "invoices.sqlite3"
        src = db.connect(settings.db_path)
        try:
            db.init_db(src)
            problems = archive.verify_all(src, settings.archive_dir) \
                + expenses.verify_all(src, settings.expenses_dir)
            if problems:
                raise BackupError(
                    "Archiv inkonsistent, Backup abgebrochen: "
                    + "; ".join(f"{n}: {p}" for n, p in problems)
                )
            dst = sqlite3.connect(snapshot)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

        files = {"invoices.sqlite3": snapshot}
        files.update(_tree("archive", settings.archive_dir, "*.pdf"))
        files.update(_tree("expenses", settings.expenses_dir, "*"))
        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "files": {name: _sha256(p) for name, p in files.items()},
        }

        partial = Path(tmp) / target.name
        with tarfile.open(partial, "w:gz") as tar:
            for name, p in files.items():
                tar.add(p, arcname=name)
            raw = json.dumps(manifest, indent=2, sort_keys=True).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(raw)
            info.mtime = int(datetime.now().timestamp())
            tar.addfile(info, io.BytesIO(raw))
        partial.rename(target)

    target.chmod(0o440)
    rotate(settings)
    return target


def _tree(prefix: str, root: Path, pattern: str) -> dict[str, Path]:
    if not root.is_dir():
        return {}
    return {f"{prefix}/{p.relative_to(root)}": p for p in sorted(root.rglob(pattern)) if p.is_file()}


def list_backups(settings: Settings) -> list[Path]:
    if not settings.backup_dir.is_dir():
        return []
    return sorted(
        (p for p in settings.backup_dir.iterdir() if BACKUP_RE.match(p.name)),
        reverse=True,
    )


def rotate(settings: Settings) -> list[Path]:
    """Keep the newest `backup_keep` backups plus the newest backup of every month."""
    backups = list_backups(settings)
    keep = set(backups[: settings.backup_keep])
    seen_months = set()
    for p in backups:  # newest first
        month = p.name[len("invoices-backup-"):][:6]
        if month not in seen_months:
            seen_months.add(month)
            keep.add(p)
    removed = [p for p in backups if p not in keep]
    for p in removed:
        p.unlink()
    return removed


def verify_backup(path: Path) -> dict:
    """Check every file in a backup against its manifest. Returns the manifest."""
    with tarfile.open(path, "r:gz") as tar:
        try:
            manifest = json.load(tar.extractfile("manifest.json"))
        except KeyError:
            raise BackupError("manifest.json fehlt im Backup.") from None
        names = {m.name for m in tar.getmembers() if m.isfile()} - {"manifest.json"}
        if names != set(manifest["files"]):
            raise BackupError("Backup-Inhalt passt nicht zum Manifest.")
        for name, expected in manifest["files"].items():
            h = hashlib.sha256(tar.extractfile(name).read()).hexdigest()
            if h != expected:
                raise BackupError(f"Prüfsumme falsch: {name}")
    return manifest


def restore_backup(path: Path, data_dir: Path) -> None:
    """Restore a backup into an empty or non-existent data directory."""
    verify_backup(path)
    if data_dir.exists() and any(data_dir.iterdir()):
        raise BackupError(f"{data_dir} ist nicht leer. Restore nur in ein leeres Verzeichnis.")
    data_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "r:gz") as tar:
        members = [m for m in tar.getmembers() if m.isfile() and m.name != "manifest.json"]
        tar.extractall(data_dir, members=members, filter="data")
    for p in (*(data_dir / "archive").rglob("*.pdf"), *(data_dir / "expenses").rglob("*")):
        if p.is_file():
            p.chmod(0o444)


def restore_test(settings: Settings, path: Path) -> str:
    """Restore a backup into a temporary directory and verify every file against the restored
    database. Records the result in control_runs. Returns a summary, raises BackupError."""
    try:
        with tempfile.TemporaryDirectory(prefix="restore-test-") as tmp:
            target = Path(tmp) / "data"
            restore_backup(path, target)
            conn = db.connect(target / "invoices.sqlite3")
            try:
                problems = archive.verify_all(conn, target / "archive") \
                    + expenses.verify_all(conn, target / "expenses")
                invoices = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
                documents = conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
            finally:
                conn.close()
        if problems:
            raise BackupError("; ".join(f"{n}: {p}" for n, p in problems))
    except (BackupError, OSError, tarfile.TarError, sqlite3.DatabaseError) as e:
        _record(settings, "restore_test", False, f"{path.name}: {e}")
        raise BackupError(str(e)) from e
    summary = f"{path.name}: {invoices} Rechnungen, {documents} Belege wiederhergestellt und geprüft"
    _record(settings, "restore_test", True, summary)
    return summary
