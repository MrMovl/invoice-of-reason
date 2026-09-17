"""GoBD foundations: schema migrations, Programmidentität, configuration history, complete
change logging and the control log."""

import sqlite3
from dataclasses import replace
from datetime import date

import pytest

from invoices import archive, backup, cli, db, expenses, system
from invoices.config import load_sender, load_settings
from tests.conftest import invoice_form

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def store(env):
    s = load_settings()
    conn = db.connect(s.db_path)
    db.init_db(conn)
    yield s, conn
    conn.close()


def issue(s, conn, **kw):
    inp = archive.parse_invoice_form(invoice_form(**kw))
    return archive.issue_invoice(conn, s.archive_dir, inp, load_sender(s.sender_file), s.retention_years)


def system_events(conn, action):
    return [r["detail"] for r in conn.execute(
        "SELECT detail FROM system_events WHERE action = ? ORDER BY id", (action,))]


def last_event(conn, table="events"):
    return conn.execute(f"SELECT action, detail FROM {table} ORDER BY id DESC LIMIT 1").fetchone()


# ── Migrations ────────────────────────────────────────────────────────────


def test_fresh_database_is_migrated_once(store):
    s, conn = store
    assert db.schema_version(conn) == len(db.MIGRATIONS)
    db.init_db(conn)
    assert system_events(conn, "schema_migration") == [
        f"{i}: {desc}" for i, (desc, _sql) in enumerate(db.MIGRATIONS, start=1)]


def test_existing_database_keeps_data_when_migrated(env, tmp_path):
    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA)
    old.execute("""INSERT INTO invoices (number, issue_date, service_date, customer_name, title,
                   amount_cents, source, pdf_path, pdf_sha256, pdf_size, payload_json, retain_until,
                   created_at, updated_at)
                   VALUES ('2026-001', '2026-01-02', '02.01.2026', 'K', 'T', 100, 'imported', 'a.pdf',
                   'x', 1, '{}', '2036-12-31', 'now', 'now')""")
    old.commit()
    old.close()
    conn = db.connect(path)
    db.init_db(conn)
    assert db.schema_version(conn) == len(db.MIGRATIONS)
    assert conn.execute("SELECT number FROM invoices").fetchone()[0] == "2026-001"
    conn.close()


@pytest.mark.parametrize("table", ["system_events", "control_runs"])
def test_log_tables_are_append_only(store, table):
    s, conn = store
    system.control_run(conn, "verify", True, "ok")
    db.add_system_event(conn, "version", "x")
    conn.commit()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute(f"UPDATE {table} SET at = 'x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute(f"DELETE FROM {table}")


# ── Programmidentität and configuration ───────────────────────────────────


def test_version_changes_are_recorded(store, monkeypatch):
    s, conn = store
    monkeypatch.setenv("INVOICES_VERSION", "abc123 2026-09-17")
    system.record_version(conn)
    system.record_version(conn)
    monkeypatch.setenv("INVOICES_VERSION", "def456 2026-09-18")
    system.record_version(conn)
    assert system_events(conn, "version") == ["abc123 2026-09-17", "def456 2026-09-18"]


def test_config_changes_are_recorded(store):
    s, conn = store
    system.record_config(conn, s)
    system.record_config(conn, s)
    system.record_config(conn, replace(s, retention_years=8))
    history = system_events(conn, "config_changed")
    assert len(history) == 2
    assert '"retention_years": 10' in history[0] and '"retention_years": 8' in history[1]
    assert '"tax_number": "12/345/67890"' in history[0]


def test_app_start_records_version_and_config(app, monkeypatch):
    s = app.config["SETTINGS"]
    conn = db.connect(s.db_path)
    assert system_events(conn, "version") == ["dev"]
    assert len(system_events(conn, "config_changed")) == 1
    conn.close()


def test_footer_shows_version(logged_in):
    assert "Version dev" in logged_in.get("/invoices").get_data(as_text=True)


# ── Complete change log (Rz. 58) ──────────────────────────────────────────


def test_notes_log_old_and_new_text(store):
    s, conn = store
    inv_id = issue(s, conn)
    archive.set_notes(conn, inv_id, "erste Notiz")
    archive.set_notes(conn, inv_id, "zweite Notiz")
    assert tuple(last_event(conn)) == ("notes", "Notiz: „erste Notiz“ → „zweite Notiz“")
    count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    archive.set_notes(conn, inv_id, "zweite Notiz")
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == count
    with pytest.raises(archive.ArchiveError, match="zu lang"):
        archive.set_notes(conn, inv_id, "x" * 2001)


def test_status_logs_old_and_new_values(store):
    s, conn = store
    inv_id = issue(s, conn)
    archive.set_status(conn, inv_id, "paid", date(2026, 9, 20), payment_method="bank")
    assert tuple(last_event(conn)) == ("status:paid", "Status: Offen → Bezahlt; Bezahlt am: – → 20.09.2026; "
                                                      "Zahlungsart: – → Überweisung/Karte")
    archive.set_status(conn, inv_id, "cancelled", note="Doppelt gestellt")
    assert last_event(conn)["detail"] == \
        "Status: Bezahlt → Storniert; Bezahlt am: 20.09.2026 → –; Zahlungsart: Überweisung/Karte → –; " \
        "Grund: „Doppelt gestellt“"


def test_cancel_requires_reason(store):
    s, conn = store
    inv_id = issue(s, conn)
    with pytest.raises(archive.ArchiveError, match="Grund"):
        archive.set_status(conn, inv_id, "cancelled", note="  ")
    assert conn.execute("SELECT status FROM invoices").fetchone()[0] == "open"


def test_expense_changes_are_logged_in_full(store):
    s, conn = store
    exp_id = expenses.store_upload(conn, s.expenses_dir, PNG, "a.png", s.retention_years)
    base = {"vendor": "Bauhaus", "amount": "49,99", "expense_date": "2026-09-10", "status": "paid",
            "category": "Werkzeug", "paid_date": "", "invoice_number": "", "notes": "alt",
            "payment_method": "bank"}
    expenses.update_expense(conn, exp_id, expenses.parse_expense_form(base))
    long_note = "n" * 1500
    expenses.update_expense(conn, exp_id, expenses.parse_expense_form({**base, "notes": long_note}))
    assert last_event(conn, "expense_events")["detail"] == f"Notiz: „alt“ → „{long_note}“"


def test_voiding_an_expense_requires_reason(store):
    s, conn = store
    exp_id = expenses.store_upload(conn, s.expenses_dir, PNG, "a.png", s.retention_years)
    with pytest.raises(archive.ArchiveError, match="Grund"):
        expenses.update_expense(conn, exp_id, expenses.parse_expense_form({"status": "void"}))
    expenses.update_expense(conn, exp_id, expenses.parse_expense_form({"status": "void", "notes": "Fehl-Upload"}))
    assert conn.execute("SELECT status FROM expenses").fetchone()[0] == "void"


# ── Control log (Rz. 88, 100) ─────────────────────────────────────────────


def runs(conn):
    return [(r["kind"], r["ok"]) for r in conn.execute("SELECT kind, ok FROM control_runs ORDER BY id")]


def test_verify_and_backup_are_recorded(store):
    s, conn = store
    issue(s, conn)
    assert cli.main(["verify"]) == 0
    path = backup.create_backup(s)
    assert runs(conn) == [("verify", 1), ("backup", 1)]

    summary = backup.restore_test(s, path)
    assert "1 Rechnungen" in summary
    assert runs(conn)[-1] == ("restore_test", 1)

    pdf = next(s.archive_dir.rglob("*.pdf"))
    pdf.chmod(0o644)
    pdf.write_bytes(b"tampered")
    assert cli.main(["verify"]) == 1
    with pytest.raises(backup.BackupError):
        backup.create_backup(s)
    assert runs(conn)[-2:] == [("verify", 0), ("backup", 0)]


def test_failed_restore_test_is_recorded(store, tmp_path):
    s, conn = store
    broken = tmp_path / "invoices-backup-20260101-000000.tar.gz"
    broken.write_bytes(b"not a tarball")
    with pytest.raises(backup.BackupError):
        backup.restore_test(s, broken)
    assert runs(conn) == [("restore_test", 0)]


def test_backups_page_shows_control_log(logged_in, csrf):
    logged_in.post("/backups", data={"csrf_token": csrf})
    html = logged_in.get("/backups").get_data(as_text=True)
    assert "Kontrollprotokoll" in html and "Backup" in html and "OK" in html
