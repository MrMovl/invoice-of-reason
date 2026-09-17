"""Importing invoices issued before the program: same archive rules as created invoices."""

import json
import shutil
import sqlite3
from datetime import date, timedelta

import pytest

from invoices import archive, chain, cli, db, expenses, invoice_import
from invoices.config import load_sender, load_settings
from tests.conftest import invoice_form, make_pdf

needs_pdftotext = pytest.mark.skipif(not shutil.which("pdftotext"), reason="pdftotext not installed")

ORIGINAL = make_pdf(["Rechnung Nr. 2026-001", "Rechnungsdatum 02.09.2026", "Beratung | 700,00 €"])
VALUES = {
    "number": "2026-001", "issue_date": "2026-09-02", "service_date": "01.09.2026",
    "due_date": "2026-09-16", "customer_name": "Nordlicht Werkstatt GmbH",
    "customer_street": "Hafenstraße 7", "customer_city": "24103 Kiel", "title": "Beratung",
    "description": "", "amount": "700,00", "reason": "Vor Einführung des Programms erstellt und versandt",
    "original_filename": "/home/me/Rechnung_2026-001.pdf",
}


@pytest.fixture
def store(env):
    s = load_settings()
    conn = db.connect(s.db_path)
    db.init_db(conn)
    yield s, conn
    conn.close()


def do_import(s, conn, pdf=ORIGINAL, accept=None, **overrides):
    inp = invoice_import.parse_import({**VALUES, **overrides})
    warnings = invoice_import.check_pdf(pdf, inp) if accept is None else accept
    return invoice_import.import_invoice(conn, s.archive_dir, pdf, inp, s.retention_years, warnings)


@needs_pdftotext
def test_import_archives_the_original_unchanged(store):
    s, conn = store
    inv_id = do_import(s, conn)
    row = conn.execute("SELECT * FROM invoices WHERE id = ?", (inv_id,)).fetchone()
    path = s.archive_dir / row["pdf_path"]
    assert path.read_bytes() == ORIGINAL
    assert row["pdf_sha256"] == archive.sha256_bytes(ORIGINAL) and row["pdf_size"] == len(ORIGINAL)
    assert not path.stat().st_mode & 0o222
    assert (row["source"], row["number"], row["amount_cents"], row["retain_until"]) == \
        ("imported", "2026-001", 70000, "2036-12-31")
    payload = json.loads(row["payload_json"])
    assert payload["sender"] is None and payload["import"]["original_filename"] == "Rechnung_2026-001.pdf"
    event = conn.execute("SELECT action, detail FROM events WHERE invoice_id = ?", (inv_id,)).fetchone()
    assert event["action"] == "imported"
    assert "Grund: „Vor Einführung des Programms erstellt und versandt“" in event["detail"]
    assert f"sha256={row['pdf_sha256']}" in event["detail"]
    assert chain.verify_chains(conn) == [] and archive.verify_all(conn, s.archive_dir) == []


@needs_pdftotext
def test_imported_invoice_is_immutable(store):
    s, conn = store
    inv_id = do_import(s, conn)
    for sql in ("UPDATE invoices SET amount_cents = 1 WHERE id = ?",
                "UPDATE invoices SET pdf_sha256 = 'x' WHERE id = ?",
                "UPDATE invoices SET source = 'generated' WHERE id = ?",
                "UPDATE invoices SET payload_json = '{}' WHERE id = ?"):
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            conn.execute(sql, (inv_id,))
    with pytest.raises(sqlite3.DatabaseError, match="cannot be deleted"):
        conn.execute("DELETE FROM invoices WHERE id = ?", (inv_id,))
    conn.rollback()
    with pytest.raises(archive.ArchiveError, match="bereits vergeben"):
        do_import(s, conn, pdf=ORIGINAL + b"\n")


@needs_pdftotext
def test_numbering_continues_after_import(store):
    s, conn = store
    do_import(s, conn)
    assert archive.next_number(conn, 2026) == "2026-002"
    inp = archive.parse_invoice_form(invoice_form(number="2026-002"))
    archive.issue_invoice(conn, s.archive_dir, inp, load_sender(s.sender_file), s.retention_years)
    assert archive.number_gaps(conn) == {}


@needs_pdftotext
def test_same_file_cannot_be_archived_twice(store):
    s, conn = store
    do_import(s, conn)
    with pytest.raises(archive.ArchiveError, match="bereits als Rechnung"):
        do_import(s, conn, number="2026-002")
    exp_pdf = make_pdf(["Rechnung Nr. 2026-003", "Beratung | 700,00 €"])
    expenses.store_upload(conn, s.expenses_dir, exp_pdf, "beleg.pdf", s.retention_years)
    with pytest.raises(archive.ArchiveError, match="bereits als Beleg"):
        do_import(s, conn, pdf=exp_pdf, number="2026-003")


@pytest.mark.parametrize("overrides,message", [
    ({"reason": " "}, "Grund für den Import fehlt"),
    ({"issue_date": (date.today() + timedelta(days=1)).isoformat()}, "Zukunft"),
    ({"due_date": "2026-09-01"}, "vor dem Rechnungsdatum"),
    ({"amount": "0"}, "größer als 0"),
    ({"service_date": ""}, "Leistungsdatum fehlt"),
])
def test_invalid_input_is_rejected(overrides, message):
    with pytest.raises(archive.ArchiveError, match=message):
        invoice_import.parse_import({**VALUES, **overrides})


@pytest.mark.parametrize("pdf,message", [(b"", "leer"), (b"<html>", "keine PDF")])
def test_non_pdf_is_rejected(store, pdf, message):
    s, conn = store
    with pytest.raises(archive.ArchiveError, match=message):
        do_import(s, conn, pdf=pdf, accept=[])
    assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0


@needs_pdftotext
def test_mismatch_with_pdf_text_must_be_confirmed_and_is_logged(store):
    s, conn = store
    inp = invoice_import.parse_import({**VALUES, "amount": "650,00"})
    warnings = invoice_import.check_pdf(ORIGINAL, inp)
    assert warnings == ["Betrag 650,00 € steht nicht im PDF-Text."]
    with pytest.raises(archive.ArchiveError, match="nicht bestätigt"):
        invoice_import.import_invoice(conn, s.archive_dir, ORIGINAL, inp, s.retention_years, [])
    assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0
    inv_id = invoice_import.import_invoice(conn, s.archive_dir, ORIGINAL, inp, s.retention_years, warnings)
    detail = conn.execute("SELECT detail FROM events WHERE invoice_id = ?", (inv_id,)).fetchone()[0]
    assert "Bestätigte Hinweise: Betrag 650,00 € steht nicht im PDF-Text." in detail


@needs_pdftotext
def test_out_of_sequence_import_logs_the_deviation(store):
    s, conn = store
    pdf = make_pdf(["Rechnung Nr. 2026-005", "700,00 €"])
    inv_id = do_import(s, conn, pdf=pdf, number="2026-005")
    detail = conn.execute("SELECT detail FROM events WHERE invoice_id = ?", (inv_id,)).fetchone()[0]
    assert "Abweichende Nummer: Rechnung 2026-005 lässt eine Lücke" in detail


@needs_pdftotext
def test_failed_file_write_leaves_neither_row_nor_file(store, monkeypatch):
    s, conn = store

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(archive, "_write_once", boom)
    with pytest.raises(OSError):
        do_import(s, conn)
    assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert not list(s.archive_dir.rglob("*.pdf")) if s.archive_dir.exists() else True
    assert chain.verify_chains(conn) == []


def cli_args(pdf_path, *extra):
    args = ["import-invoice", str(pdf_path)]
    for key, value in VALUES.items():
        if key != "original_filename":
            args += [f"--{key.replace('_', '-')}", value]
    return args + list(extra)


@needs_pdftotext
def test_cli_requires_typing_the_number(store, tmp_path, monkeypatch, capsys):
    s, conn = store
    pdf_path = tmp_path / "Rechnung_2026-001.pdf"
    pdf_path.write_bytes(ORIGINAL)
    monkeypatch.setattr("builtins.input", lambda _prompt: "ja")
    assert cli.main(cli_args(pdf_path)) == 1
    assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0

    monkeypatch.setattr("builtins.input", lambda _prompt: "2026-001")
    assert cli.main(cli_args(pdf_path)) == 0
    assert "Rechnung 2026-001 importiert" in capsys.readouterr().out
    row = conn.execute("SELECT source, pdf_sha256 FROM invoices").fetchone()
    assert tuple(row) == ("imported", archive.sha256_bytes(ORIGINAL))
    assert cli.main(["verify"]) == 0


@needs_pdftotext
def test_cli_refuses_unconfirmed_warnings(store, tmp_path):
    s, conn = store
    pdf_path = tmp_path / "scan.pdf"
    pdf_path.write_bytes(make_pdf(["Rechnung"]))
    assert cli.main(cli_args(pdf_path, "--yes")) == 1
    assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0
    assert cli.main(cli_args(pdf_path, "--yes", "--accept-warnings")) == 0
