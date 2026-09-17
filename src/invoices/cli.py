"""Command line entry point: `invoices <command>`."""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
import time
from pathlib import Path

from . import archive, backup, chain, db, expenses, system
from .config import load_settings


def cmd_hash_password(_args) -> int:
    from werkzeug.security import generate_password_hash

    pw = getpass.getpass("Passwort: ")
    if len(pw) < 12:
        print("Passwort muss mindestens 12 Zeichen haben.", file=sys.stderr)
        return 1
    if pw != getpass.getpass("Wiederholen: "):
        print("Passwörter stimmen nicht überein.", file=sys.stderr)
        return 1
    print(generate_password_hash(pw, method="scrypt"))
    return 0


def cmd_secret_key(_args) -> int:
    print(secrets.token_urlsafe(48))
    return 0


def cmd_verify(_args) -> int:
    s = load_settings()
    conn = db.connect(s.db_path)
    db.init_db(conn)
    count = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    expense_count = conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
    problems = archive.verify_all(conn, s.archive_dir) + expenses.verify_all(conn, s.expenses_dir) \
        + chain.verify_chains(conn)
    for number, problem in problems:
        print(f"FEHLER {number}: {problem}", file=sys.stderr)
    summary = f"{count} Rechnungen und {expense_count} Belege geprüft"
    if problems:
        system.control_run(conn, "verify", False, "; ".join(f"{n}: {p}" for n, p in problems))
    else:
        system.control_run(conn, "verify", True, summary)
        print(f"OK: {summary}.")
    conn.close()
    return 1 if problems else 0


def _run_backup() -> int:
    try:
        path = backup.create_backup(load_settings())
    except backup.BackupError as e:
        print(f"{time.strftime('%F %T')} Backup FEHLGESCHLAGEN: {e}", file=sys.stderr, flush=True)
        return 1
    print(f"{time.strftime('%F %T')} Backup erstellt: {path}", flush=True)
    return 0


def cmd_backup(args) -> int:
    if not args.every:
        return _run_backup()
    while True:
        _run_backup()
        time.sleep(args.every)


def cmd_verify_backup(args) -> int:
    try:
        manifest = backup.verify_backup(Path(args.file))
    except (backup.BackupError, OSError) as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 1
    print(f"OK: {len(manifest['files'])} Dateien, erstellt {manifest['created_at']}.")
    return 0


def cmd_restore(args) -> int:
    try:
        backup.restore_backup(Path(args.file), Path(args.data_dir).resolve())
    except (backup.BackupError, OSError) as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 1
    print(f"Wiederhergestellt nach {args.data_dir}.")
    return 0


def cmd_restore_test(args) -> int:
    try:
        summary = backup.restore_test(load_settings(), Path(args.file))
    except backup.BackupError as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 1
    print(f"OK: {summary}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="invoices")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("hash-password", help="Passwort-Hash für INVOICES_PASSWORD_HASH erzeugen").set_defaults(func=cmd_hash_password)
    sub.add_parser("secret-key", help="Zufälligen INVOICES_SECRET_KEY erzeugen").set_defaults(func=cmd_secret_key)
    sub.add_parser("verify", help="Alle archivierten Rechnungen und Belege gegen ihre Prüfsummen prüfen").set_defaults(func=cmd_verify)
    p = sub.add_parser("backup", help="Backup erstellen")
    p.add_argument("--every", type=int, metavar="SEKUNDEN", help="Endlos wiederholen in diesem Abstand")
    p.set_defaults(func=cmd_backup)
    p = sub.add_parser("verify-backup", help="Backup-Datei gegen ihr Manifest prüfen")
    p.add_argument("file")
    p.set_defaults(func=cmd_verify_backup)
    p = sub.add_parser("restore-test", help="Backup testweise wiederherstellen, prüfen und protokollieren")
    p.add_argument("file")
    p.set_defaults(func=cmd_restore_test)
    p = sub.add_parser("restore", help="Backup in ein leeres Datenverzeichnis wiederherstellen")
    p.add_argument("file")
    p.add_argument("data_dir")
    p.set_defaults(func=cmd_restore)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
