import shutil
import sqlite3
from datetime import date

import pytest

from invoices import archive, backup, db, expenses
from invoices.config import load_settings
from tests.conftest import SAMPLE_EXPENSE, invoice_form, make_pdf

needs_pdftotext = pytest.mark.skipif(not shutil.which("pdftotext"), reason="pdftotext not installed")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def store(env):
    s = load_settings()
    conn = db.connect(s.db_path)
    db.init_db(conn)
    yield s, conn
    conn.close()


def upload(s, conn, data, name="beleg.pdf"):
    return expenses.store_upload(conn, s.expenses_dir, data, name, s.retention_years, today=date(2026, 9, 16))


@needs_pdftotext
def test_upload_archives_and_suggests(store):
    s, conn = store
    exp_id = upload(s, conn, make_pdf(SAMPLE_EXPENSE), "C:\\Users\\me\\Hetzner Rechnung.pdf")
    row = conn.execute("SELECT * FROM expenses WHERE id = ?", (exp_id,)).fetchone()
    assert (row["vendor"], row["invoice_number"], row["expense_date"], row["amount_cents"]) == \
        ("Hetzner Online GmbH", "R0024567891", "2026-09-03", 1190)
    assert row["reviewed"] == 0 and row["status"] == "paid"
    assert row["original_filename"] == "Hetzner Rechnung.pdf"
    assert row["doc_path"].startswith("2026/20260916_Hetzner-Rechnung_")
    assert row["retain_until"] == "2036-12-31"
    path = s.expenses_dir / row["doc_path"]
    assert archive.sha256_file(path) == row["doc_sha256"] and not path.stat().st_mode & 0o222
    assert "Gesamtbetrag" in row["doc_text"]


def test_upload_rejects_duplicates_and_unknown_types(store):
    s, conn = store
    exp_id = upload(s, conn, PNG, "foto.png")
    with pytest.raises(expenses.DuplicateError) as e:
        upload(s, conn, PNG, "nochmal.png")
    assert e.value.expense_id == exp_id
    with pytest.raises(archive.ArchiveError, match="Nur PDF"):
        upload(s, conn, b"GIF89a", "x.gif")
    with pytest.raises(archive.ArchiveError, match="nicht wohlgeformt"):
        upload(s, conn, b"<html>", "x.html")
    row = conn.execute("SELECT * FROM expenses").fetchone()
    assert row["amount_cents"] is None and row["doc_text"] == ""
    assert conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0] == 1


def test_review_update_logs_changes_and_document_stays_fixed(store):
    s, conn = store
    exp_id = upload(s, conn, PNG)
    form = {"vendor": "Bauhaus", "amount": "49,99", "expense_date": "2026-09-10", "status": "paid",
            "category": "Werkzeug", "paid_date": "", "invoice_number": "", "notes": "",
            "payment_method": "bank"}
    expenses.update_expense(conn, exp_id, expenses.parse_expense_form(form))
    row = conn.execute("SELECT * FROM expenses WHERE id = ?", (exp_id,)).fetchone()
    assert (row["amount_cents"], row["reviewed"], row["category"]) == (4999, 1, "Werkzeug")
    event = conn.execute("SELECT action, detail FROM expense_events ORDER BY id DESC").fetchone()
    assert event["action"] == "reviewed" and "Betrag: – → 49,99 €" in event["detail"]

    with pytest.raises(archive.ArchiveError, match="Betrag fehlt"):
        expenses.parse_expense_form({**form, "amount": ""})
    assert expenses.parse_expense_form({"status": "void"})["amount_cents"] is None

    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        conn.execute("UPDATE expenses SET doc_sha256 = 'x' WHERE id = ?", (exp_id,))
    with pytest.raises(sqlite3.DatabaseError, match="cannot be deleted"):
        conn.execute("DELETE FROM expenses WHERE id = ?", (exp_id,))


def test_cash_summary_by_payment_date(store):
    s, conn = store
    from invoices.config import load_sender

    inv = archive.issue_invoice(conn, s.archive_dir, archive.parse_invoice_form(invoice_form()),
                                load_sender(s.sender_file), s.retention_years)
    archive.set_status(conn, inv, "paid", date(2027, 1, 5), payment_method="bank")
    a = upload(s, conn, PNG)
    b = upload(s, conn, PNG + b"b")
    c = upload(s, conn, PNG + b"c")
    base = {"vendor": "V", "expense_date": "2026-12-20", "paid_date": "", "category": "Büro",
            "payment_method": "bank"}
    expenses.update_expense(conn, a, expenses.parse_expense_form({**base, "amount": "100", "status": "paid"}))
    expenses.update_expense(conn, b, expenses.parse_expense_form({**base, "amount": "30", "status": "paid", "paid_date": "2027-01-02"}))
    expenses.update_expense(conn, c, expenses.parse_expense_form({**base, "amount": "999", "status": "open"}))

    summary = expenses.cash_summary(conn, "2026")
    summary.pop("reverse_charge")
    assert summary == {"year": "2026", "income": 0, "expenses": 10000, "assets": 0,
                       "surplus_before_afa": -10000, "to_review": 0}
    assert expenses.cash_summary(conn, "2027")["surplus_before_afa"] == 70000 - 3000
    assert expenses.cash_summary(conn, "x' OR 1=1")["year"] == ""


def test_verify_and_backup_include_expenses(store, tmp_path):
    s, conn = store
    exp_id = upload(s, conn, PNG)
    path = backup.create_backup(s)
    manifest = backup.verify_backup(path)
    assert any(name.startswith("expenses/2026/") for name in manifest["files"])

    target = tmp_path / "restored"
    backup.restore_backup(path, target)
    restored = db.connect(target / "invoices.sqlite3")
    assert expenses.verify_all(restored, target / "expenses") == []
    restored.close()
    path.unlink()  # backups are named by the second

    doc = next(s.expenses_dir.rglob("*.png"))
    doc.chmod(0o644)
    doc.write_bytes(b"tampered")
    assert expenses.verify_all(conn, s.expenses_dir) == [(f"Beleg {exp_id}", "Prüfsumme stimmt nicht")]
    with pytest.raises(backup.BackupError, match="inkonsistent"):
        backup.create_backup(s)
