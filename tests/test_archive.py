import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from invoices import archive, backup, db
from invoices.config import load_sender, load_settings
from tests.conftest import invoice_form


@pytest.mark.parametrize("raw,expected", [
    ("700", "700.00"), ("700,5", "700.50"), ("1.234,56", "1234.56"), ("1,234.56", "1234.56"), ("12 €", "12.00"),
])
def test_parse_amount(raw, expected):
    assert archive.parse_amount(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["", "abc", "0", "-5", "1,234", "nan", "1.001", "Infinity"])
def test_parse_amount_rejects(raw):
    with pytest.raises(archive.ArchiveError):
        archive.parse_amount(raw)


def test_slug_and_retention():
    assert archive.pdf_filename("2026-001", "Nordlicht Werkstatt GmbH") == "Rechnung_2026-001_Nordlicht-Werkstatt-GmbH.pdf"
    assert archive.slugify("Müller & Söhne / Büro") == "Mueller-Soehne-Buero"
    assert archive.slugify("../../etc") == "etc"
    assert archive.retain_until(date(2026, 9, 16), 10) == date(2036, 12, 31)


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


def test_issue_archives_write_once(store):
    s, conn = store
    inv_id = issue(s, conn)
    row = conn.execute("SELECT * FROM invoices WHERE id = ?", (inv_id,)).fetchone()
    path = s.archive_dir / row["pdf_path"]
    assert row["pdf_path"] == "2026/Rechnung_2026-001_Nordlicht-Werkstatt-GmbH.pdf"
    assert row["due_date"] == "2026-09-30"
    assert row["amount_cents"] == 70000
    assert archive.sha256_file(path) == row["pdf_sha256"]
    assert not path.stat().st_mode & 0o222
    assert archive.next_number(conn, 2026) == "2026-002"
    with pytest.raises(archive.ArchiveError, match="bereits vergeben"):
        issue(s, conn)


def test_db_rejects_tampering(store):
    s, conn = store
    inv_id = issue(s, conn)
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        conn.execute("UPDATE invoices SET amount_cents = 1 WHERE id = ?", (inv_id,))
    with pytest.raises(sqlite3.DatabaseError, match="cannot be deleted"):
        conn.execute("DELETE FROM invoices WHERE id = ?", (inv_id,))
    conn.rollback()
    archive.set_status(conn, inv_id, "paid", date(2026, 9, 20), payment_method="bank")
    assert conn.execute("SELECT status, paid_date FROM invoices").fetchone()[:] == ("paid", "2026-09-20")


def test_failed_file_write_leaves_no_row(store, monkeypatch):
    s, conn = store

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(archive, "_write_once", boom)
    with pytest.raises(OSError):
        issue(s, conn)
    assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0


def test_verify_detects_modification(store):
    s, conn = store
    issue(s, conn)
    assert archive.verify_all(conn, s.archive_dir) == []
    path = next(s.archive_dir.rglob("*.pdf"))
    path.chmod(0o644)
    path.write_bytes(path.read_bytes() + b"x")
    assert archive.verify_all(conn, s.archive_dir) == [("2026-001", "Prüfsumme stimmt nicht")]
    with pytest.raises(backup.BackupError, match="inkonsistent"):
        backup.create_backup(s)


def test_backup_and_restore_roundtrip(store, tmp_path):
    s, conn = store
    issue(s, conn)
    issue(s, conn, number="2026-002", customer_name="Kunde Zwei")
    path = backup.create_backup(s)
    manifest = backup.verify_backup(path)
    assert len(manifest["files"]) == 3

    target = tmp_path / "restored"
    backup.restore_backup(path, target)
    restored = db.connect(target / "invoices.sqlite3")
    assert archive.verify_all(restored, target / "archive") == []
    assert restored.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 2
    restored.close()
    with pytest.raises(backup.BackupError, match="nicht leer"):
        backup.restore_backup(path, target)


def test_rotation_keeps_newest_and_monthly(env):
    s = load_settings()
    s = s.__class__(**{**s.__dict__, "backup_keep": 2})
    s.backup_dir.mkdir(parents=True)
    names = ["20260101-000000", "20260115-000000", "20260201-000000", "20260210-000000", "20260211-000000"]
    for n in names:
        (s.backup_dir / f"invoices-backup-{n}.tar.gz").write_bytes(b"")
    backup.rotate(s)
    left = sorted(p.name[16:31] for p in backup.list_backups(s))
    assert left == ["20260115-000000", "20260210-000000", "20260211-000000"]
