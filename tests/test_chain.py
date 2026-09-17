"""Hash chain over the logs and record states (GoBD Rz. 110)."""

from datetime import date

import pytest

from invoices import archive, backup, chain, cli, db, expenses, system
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


def populate(s, conn, paid=True):
    inv_id = issue(s, conn)
    if paid:
        archive.set_status(conn, inv_id, "paid", date(2026, 9, 20), payment_method="bank")
    archive.set_notes(conn, inv_id, "Notiz")
    exp_id = expenses.store_upload(conn, s.expenses_dir, PNG, "a.png", s.retention_years)
    form = {"vendor": "Bauhaus", "amount": "49,99", "expense_date": "2026-09-10", "status": "paid",
            "category": "Werkzeug", "paid_date": "", "invoice_number": "", "notes": "", "payment_method": "bank"}
    if paid:
        expenses.update_expense(conn, exp_id, expenses.parse_expense_form(form))
    system.control_run(conn, "verify", True, "ok")
    return inv_id, exp_id


def tamper(conn, trigger, sql, params=()):
    """What someone with the database file could do: drop the protection, change, restore it."""
    trigger_sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (trigger,)).fetchone()[0]
    with conn:
        conn.execute(f"DROP TRIGGER {trigger}")
        conn.execute(sql, params)
        conn.execute(trigger_sql)


def test_untouched_database_verifies(store):
    s, conn = store
    populate(s, conn)
    assert chain.verify_chains(conn) == []
    assert all(r["hash"] for r in conn.execute("SELECT hash FROM events"))
    # Saving an expense without changes touches only updated_at, which is not part of the state.
    form = {"vendor": "Bauhaus", "amount": "49,99", "expense_date": "2026-09-10", "status": "paid",
            "category": "Werkzeug", "paid_date": "", "invoice_number": "", "notes": "", "payment_method": "bank"}
    expenses.update_expense(conn, 1, expenses.parse_expense_form(form))
    assert chain.verify_chains(conn) == []


def test_edited_log_entry_breaks_chain(store):
    s, conn = store
    populate(s, conn)
    tamper(conn, "events_append_only_update", "UPDATE events SET detail = 'harmlos' WHERE id = 2")
    assert chain.verify_chains(conn) == [("Rechnungsverlauf #2", "Hash-Kette unterbrochen (Eintrag verändert)")]


def test_removed_log_entry_is_detected(store):
    s, conn = store
    populate(s, conn)
    tamper(conn, "system_events_append_only_delete", "DELETE FROM system_events WHERE id = 1")
    assert ("Systemprotokoll #2", "Eintrag #1 fehlt") in chain.verify_chains(conn)


def test_silent_record_changes_are_detected(store):
    s, conn = store
    inv_id, exp_id = populate(s, conn)
    with conn:
        conn.execute("UPDATE invoices SET status = 'open', paid_date = NULL WHERE id = ?", (inv_id,))
    tamper(conn, "invoices_immutable", "UPDATE invoices SET amount_cents = 1 WHERE id = ?", (inv_id,))
    with conn:
        conn.execute("UPDATE expenses SET amount_cents = 100 WHERE id = ?", (exp_id,))
    assert chain.verify_chains(conn) == [
        ("Rechnung 2026-001", "Datensatz ohne protokollierte Änderung verändert"),
        ("Beleg 1", "Datensatz ohne protokollierte Änderung verändert"),
    ]
    assert cli.main(["verify"]) == 1


def test_dropped_trigger_is_reported_and_logged(store):
    s, conn = store
    with conn:
        conn.execute("DROP TRIGGER invoices_no_delete")
    assert chain.verify_chains(conn) == [("Datenbank", "Schutz-Trigger fehlt: invoices_no_delete")]
    db.init_db(conn)
    assert chain.verify_chains(conn) == []
    assert db.last_system_event(conn, "trigger_missing")["detail"] == "invoices_no_delete"


def test_new_columns_with_empty_default_keep_hashes_valid(store):
    s, conn = store
    populate(s, conn)
    with conn:
        conn.execute("ALTER TABLE invoices ADD COLUMN extra TEXT NOT NULL DEFAULT ''")
        conn.execute("ALTER TABLE events ADD COLUMN extra TEXT")
    assert chain.verify_chains(conn) == []


def test_migration_seals_existing_data(env, monkeypatch):
    s = load_settings()
    conn = db.connect(s.db_path)
    chain_migration = next(i for i, (desc, _) in enumerate(db.MIGRATIONS) if desc == "hash chain over all logs")
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:chain_migration])
    db.init_db(conn)
    # Only operations the older schema supports (payment_method comes in a later migration).
    inv_id, exp_id = populate(s, conn, paid=False)
    before = conn.execute("SELECT id, action, detail FROM events ORDER BY id").fetchall()
    monkeypatch.undo()

    db.init_db(conn)
    assert chain.verify_chains(conn) == []
    after = conn.execute("SELECT id, action, detail FROM events ORDER BY id").fetchall()
    assert [tuple(r) for r in after[:len(before)]] == [tuple(r) for r in before]
    assert after[-1]["action"] == "sealed"
    assert conn.execute("SELECT action FROM expense_events ORDER BY id DESC").fetchone()[0] == "sealed"
    assert chain.verify_chains(conn) == []
    conn.close()


def test_backup_records_chain_heads_and_restore_test_detects_truncation(store):
    s, conn = store
    populate(s, conn)
    path = backup.create_backup(s)
    manifest = backup.verify_backup(path)
    assert manifest["chain_heads"]["events"] == db.chain_head(conn, "events")

    backup.restore_test(s, path)
    head = db.chain_head(conn, "control_runs")
    # Removing the newest entries leaves a valid, shorter chain; only the backup reveals it.
    tamper(conn, "control_runs_append_only_delete", "DELETE FROM control_runs WHERE id >= ?",
           (manifest["chain_heads"]["control_runs"]["id"],))
    assert chain.verify_chains(conn) == []
    with pytest.raises(backup.BackupError, match="weicht vom Stand im Backup ab"):
        backup.restore_test(s, path)
    assert head["id"] > 0
