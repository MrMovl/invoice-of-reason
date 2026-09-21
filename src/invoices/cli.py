"""Command line entry point: `invoices <command>`."""

from __future__ import annotations

import argparse
import getpass
import json
import re
import secrets
import sys
import time
from pathlib import Path

from . import archive, backup, chain, db, expenses, export, system
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


def cmd_import_invoice(args) -> int:
    """Archive an invoice PDF issued before this program existed. Irreversible: asks first."""
    from . import invoice_import

    s = load_settings()
    path = Path(args.pdf)
    try:
        pdf = path.read_bytes()
        inp = invoice_import.parse_import({**vars(args), "original_filename": path.name})
    except (OSError, archive.ArchiveError) as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 1
    warnings = invoice_import.check_pdf(pdf, inp)
    print(invoice_import.summary(inp, warnings))
    print("\nDie Rechnung wird unveränderbar archiviert und kann nicht gelöscht oder geändert werden.")
    if warnings and not args.accept_warnings:
        print("Import abgebrochen: Hinweise prüfen und mit --accept-warnings bestätigen.", file=sys.stderr)
        return 1
    if not args.yes and input("Importieren? Zum Bestätigen die Rechnungsnummer eingeben: ").strip() != inp.number:
        print("Import abgebrochen.", file=sys.stderr)
        return 1
    conn = db.connect(s.db_path)
    try:
        db.init_db(conn)
        invoice_id = invoice_import.import_invoice(conn, s.archive_dir, pdf, inp, s.retention_years,
                                                   accepted_warnings=warnings)
        problems = archive.verify_all(conn, s.archive_dir) + chain.verify_chains(conn)
    except archive.ArchiveError as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(f"OK: Rechnung {inp.number} importiert (id {invoice_id}).")
    for where, problem in problems:
        print(f"WARNUNG {where}: {problem}", file=sys.stderr)
    return 0


def cmd_restore_test(args) -> int:
    try:
        summary = backup.restore_test(load_settings(), Path(args.file))
    except backup.BackupError as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 1
    print(f"OK: {summary}.")
    return 0


def cmd_export(args) -> int:
    try:
        path = export.create_export(load_settings(), args.year)
    except (export.ExportError, OSError) as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 1
    print(path)
    return 0


def _report_json(year: str | None) -> str:
    from . import report

    s = load_settings()
    conn = report.connect_readonly(s.db_path)
    try:
        data = report.build(conn, int(year) if year else None, s.founding_year)
    finally:
        conn.close()
    return json.dumps(data, ensure_ascii=False, indent=2)


def cmd_report(args) -> int:
    """Read-only business report as JSON (README, "Read-only report"). Every failure ends in one
    generic message: no path, setting, version or stack trace may reach whoever reads the output.
    With --out the file is replaced only by a complete new report; a failed run leaves the last
    one in place, and its generated_at shows how old it is."""
    from . import report

    if args.every and not args.out:
        print("--every nur zusammen mit --out.", file=sys.stderr)
        return 2
    stamp = (lambda: f"{time.strftime('%F %T')} ") if args.every else (lambda: "")
    while True:
        try:
            out = _report_json(args.year)
            if args.out:
                report.write_atomic(Path(args.out), out + "\n")
            else:
                print(out)
            code = 0
        except Exception:
            print(f"{stamp()}FEHLER: Bericht nicht verfügbar.", file=sys.stderr, flush=True)
            code = 1
        if not args.every:
            return code
        time.sleep(args.every)


def _year(value: str) -> str:
    if not re.fullmatch(r"\d{4}", value):
        raise argparse.ArgumentTypeError("Jahr als JJJJ angeben")
    return value


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
    p = sub.add_parser("import-invoice", help="Vor dem Programm erstellte Rechnung (PDF) unverändert archivieren")
    p.add_argument("pdf", help="Original-PDF, wie versandt")
    p.add_argument("--number", required=True, help="Rechnungsnummer, z. B. 2026-001")
    p.add_argument("--issue-date", dest="issue_date", required=True, help="Rechnungsdatum JJJJ-MM-TT")
    p.add_argument("--service-date", dest="service_date", required=True, help="Leistungsdatum wie gedruckt")
    p.add_argument("--due-date", dest="due_date", help="Fälligkeitsdatum JJJJ-MM-TT")
    p.add_argument("--customer-name", dest="customer_name", required=True)
    p.add_argument("--customer-street", dest="customer_street", default="")
    p.add_argument("--customer-city", dest="customer_city", default="")
    p.add_argument("--title", required=True, help="Leistungstitel")
    p.add_argument("--description", default="")
    p.add_argument("--amount", required=True, help="Rechnungsbetrag, z. B. 700,00")
    p.add_argument("--reason", required=True, help="Warum die Rechnung importiert wird")
    p.add_argument("--accept-warnings", dest="accept_warnings", action="store_true",
                   help="Hinweise aus dem Abgleich mit dem PDF-Text bestätigen")
    p.add_argument("--yes", action="store_true", help="Ohne Rückfrage importieren")
    p.set_defaults(func=cmd_import_invoice)
    p = sub.add_parser("restore-test", help="Backup testweise wiederherstellen, prüfen und protokollieren")
    p.add_argument("file")
    p.set_defaults(func=cmd_restore_test)
    p = sub.add_parser("export", help="Datenexport für die Betriebsprüfung (GoBD, CSV + index.xml) erstellen")
    p.add_argument("--year", metavar="JJJJ", help="Nur dieses Jahr exportieren (Standard: alles)")
    p.set_defaults(func=cmd_export)
    p = sub.add_parser("report", help="Geschäftszahlen nur lesend als JSON ausgeben (für SSH mit Forced Command)")
    p.add_argument("--json", action="store_true", required=True, help="Ausgabe als JSON (einziges Format)")
    p.add_argument("--year", type=_year, metavar="JJJJ", help="Jahr (Standard: laufendes Jahr)")
    p.add_argument("--out", metavar="DATEI", help="In diese Datei schreiben statt auf die Standardausgabe")
    p.add_argument("--every", type=int, metavar="SEKUNDEN", help="Endlos wiederholen in diesem Abstand (mit --out)")
    p.set_defaults(func=cmd_report)
    p = sub.add_parser("restore", help="Backup in ein leeres Datenverzeichnis wiederherstellen")
    p.add_argument("file")
    p.add_argument("data_dir")
    p.set_defaults(func=cmd_restore)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
