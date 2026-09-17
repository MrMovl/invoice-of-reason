"""Stornorechnung: cancelling an invoice the customer received (docs/CANCELLATION.md)."""

import json
import shutil
import sqlite3
import subprocess
import zipfile
from datetime import date

import pytest

from invoices import archive, chain, cli, db, expenses, export, pdf, turnover
from invoices.config import load_sender, load_settings
from tests.conftest import csrf_from, invoice_form

needs_pdftotext = pytest.mark.skipif(not shutil.which("pdftotext"), reason="pdftotext not installed")


@pytest.fixture
def store(env):
    s = load_settings()
    conn = db.connect(s.db_path)
    db.init_db(conn)
    yield s, conn
    conn.close()


def issue(s, conn, **kw):
    return archive.issue_invoice(conn, s.archive_dir, archive.parse_invoice_form(invoice_form(**kw)),
                                 load_sender(s.sender_file), s.retention_years)


def cancel(s, conn, invoice_id, reason="Leistung storniert", **kw):
    return archive.cancel_invoice(conn, s.archive_dir, invoice_id, reason, load_sender(s.sender_file),
                                  s.retention_years, **kw)


def row(conn, invoice_id):
    return conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()


# ── Schema ────────────────────────────────────────────────────────────────


def test_migration_keeps_an_existing_database_verifiable(env, monkeypatch):
    s = load_settings()
    conn = db.connect(s.db_path)
    number = next(i for i, (d, _) in enumerate(db.MIGRATIONS, start=1) if d == "cancellation documents on invoices")
    with monkeypatch.context() as m:
        m.setattr(db, "MIGRATIONS", db.MIGRATIONS[:number - 1])
        db.init_db(conn)
        with conn:
            conn.execute("""INSERT INTO invoices (number, issue_date, service_date, customer_name, title,
                            amount_cents, status, source, pdf_path, pdf_sha256, pdf_size, payload_json,
                            retain_until, created_at, updated_at)
                            VALUES ('2026-001', '2026-01-02', '02.01.2026', 'K', 'T', 100, 'open',
                            'imported', 'a.pdf', 'x', 1, '{}', '2036-12-31', 'now', 'now')""")
            db.add_event(conn, 1, "imported", "sha256=x")
    db.init_db(conn)
    assert conn.execute("SELECT kind, cancels_invoice_id FROM invoices").fetchone()[:] == ("", None)
    assert chain.verify_chains(conn) == []
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        conn.execute("UPDATE invoices SET kind = 'cancellation' WHERE id = 1")
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        conn.execute("UPDATE invoices SET cancels_invoice_id = 1 WHERE id = 1")
    conn.close()


def test_reference_trigger_and_unique_index(store):
    s, conn = store
    inv = issue(s, conn)
    def insert(kind, cancels, number="2026-900"):
        conn.execute("""INSERT INTO invoices (number, issue_date, service_date, customer_name, title,
                        amount_cents, source, pdf_path, pdf_sha256, pdf_size, payload_json, retain_until,
                        created_at, updated_at, kind, cancels_invoice_id)
                        VALUES (?, '2026-09-16', '16.09.2026', 'K', 'T', -100, 'generated', ?, 'x', 1, '{}',
                        '2036-12-31', 'now', 'now', ?, ?)""", (number, f"{number}.pdf", kind, cancels))
    with pytest.raises(sqlite3.DatabaseError, match="exactly one invoice"):
        insert("cancellation", None)
    with pytest.raises(sqlite3.DatabaseError, match="exactly one invoice"):
        insert("", inv)
    conn.rollback()
    doc = cancel(s, conn, inv)
    with pytest.raises(sqlite3.DatabaseError, match="exactly one invoice"):
        insert("cancellation", doc)      # cancelling a cancellation
    conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        insert("cancellation", inv, number="2026-901")   # second cancellation for the same invoice
    conn.rollback()


def test_dropped_immutable_trigger_is_restored_with_the_new_columns(store):
    s, conn = store
    with conn:
        conn.execute("DROP TRIGGER invoices_immutable")
    db.init_db(conn)
    assert db.last_system_event(conn, "trigger_missing")["detail"] == "invoices_immutable"
    inv = issue(s, conn)
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        conn.execute("UPDATE invoices SET kind = 'cancellation' WHERE id = ?", (inv,))


# ── Cancelling ────────────────────────────────────────────────────────────


def test_unsent_invoice_is_cancelled_without_a_document(store):
    s, conn = store
    inv = issue(s, conn)
    archive.cancel_unsent(conn, inv, "Falscher Kunde, nie versandt")
    assert row(conn, inv)["status"] == "cancelled"
    assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 1
    detail = conn.execute("SELECT detail FROM events ORDER BY id DESC").fetchone()[0]
    assert "Nicht versandt. Falscher Kunde" in detail
    assert archive.lost_receipt_findings(conn) == []


def test_unsent_path_is_refused_for_paid_invoices(store):
    s, conn = store
    inv = issue(s, conn)
    archive.set_status(conn, inv, "paid", date(2026, 9, 20), payment_method="bank")
    with pytest.raises(archive.ArchiveError, match="offene, nicht versandte Rechnung"):
        archive.cancel_unsent(conn, inv, "Doch nicht")


@needs_pdftotext
def test_cancellation_document_for_an_unpaid_invoice(store):
    s, conn = store
    inv = issue(s, conn)
    doc = cancel(s, conn, inv, reason="Leistung entfällt", issue_date=date(2026, 10, 1))
    d, original = row(conn, doc), row(conn, inv)
    assert (d["number"], d["kind"], d["cancels_invoice_id"]) == ("2026-002", "cancellation", inv)
    assert d["amount_cents"] == -70000 and d["status"] == "cancelled" and d["due_date"] is None
    assert d["customer_name"] == original["customer_name"] and d["service_date"] == original["service_date"]
    assert d["title"] == "Storno: " + original["title"]
    assert original["status"] == "cancelled" and original["paid_date"] is None
    assert archive.status_label("cancellation", d["status"]) == "Keine Erstattung"

    text = " ".join(subprocess.run(["pdftotext", str(s.archive_dir / d["pdf_path"]), "-"],
                                   capture_output=True, check=True).stdout.decode().split())
    assert "Stornorechnung" in text and "Storno der Rechnung Nr. 2026-001 vom 16.09.2026" in text
    assert "-700,00 €" in text and "ist damit gegenstandslos" in text
    assert pdf.SMALL_BUSINESS_NOTE in text and "IBAN" not in text

    events = conn.execute("SELECT invoice_id, action, detail FROM events ORDER BY id").fetchall()
    assert events[-1]["invoice_id"] == inv and "Stornorechnung 2026-002" in events[-1]["detail"]
    assert "Grund: „Leistung entfällt“" in events[-1]["detail"]
    assert events[-2]["invoice_id"] == doc and "Storno der Rechnung Nr. 2026-001" in events[-2]["detail"]
    assert chain.verify_chains(conn) == [] and archive.verify_all(conn, s.archive_dir) == []
    payload = json.loads(d["payload_json"])
    assert payload["cancels"]["number"] == "2026-001" and payload["reason"] == "Leistung entfällt"


@needs_pdftotext
def test_cancellation_of_a_paid_invoice_keeps_the_receipt_and_opens_a_refund(store):
    s, conn = store
    inv = issue(s, conn)
    archive.set_status(conn, inv, "paid", date(2026, 9, 20), payment_method="bank")
    doc = cancel(s, conn, inv, issue_date=date(2026, 10, 1))
    original, d = row(conn, inv), row(conn, doc)
    assert (original["status"], original["paid_date"], original["payment_method"]) == ("cancelled", "2026-09-20", "bank")
    assert d["status"] == "open" and archive.status_label("cancellation", "open") == "Erstattung offen"
    text = " ".join(subprocess.run(["pdftotext", str(s.archive_dir / d["pdf_path"]), "-"],
                                   capture_output=True, check=True).stdout.decode().split())
    assert "Der Betrag von 700,00 € wird erstattet." in text

    archive.set_status(conn, doc, "paid", date(2026, 11, 5), payment_method="bank")
    assert row(conn, doc)["status"] == "paid"
    assert conn.execute("SELECT detail FROM events ORDER BY id DESC").fetchone()[0].startswith(
        "Erstattung: Erstattung offen → Erstattet")
    assert chain.verify_chains(conn) == []


def test_guards_around_cancelled_invoices(store):
    s, conn = store
    inv = issue(s, conn)
    doc = cancel(s, conn, inv)
    with pytest.raises(archive.ArchiveError, match="bereits durch die Stornorechnung"):
        cancel(s, conn, inv)
    with pytest.raises(archive.ArchiveError, match="Stornorechnung kann nicht storniert"):
        cancel(s, conn, doc)
    with pytest.raises(archive.ArchiveError, match="durch die Stornorechnung 2026-002 storniert"):
        archive.set_status(conn, inv, "open")
    with pytest.raises(archive.ArchiveError, match="keine Erstattung zu erfassen"):
        archive.set_status(conn, doc, "paid", date(2026, 10, 2), payment_method="bank")
    with pytest.raises(archive.ArchiveError, match="Grund"):
        cancel(s, conn, issue(s, conn, number="2026-003"), reason="  ")


def test_cancellation_can_be_added_later_for_an_already_cancelled_invoice(store):
    s, conn = store
    inv = issue(s, conn)
    archive.cancel_unsent(conn, inv, "Nie versandt")
    doc = cancel(s, conn, inv, reason="War doch versandt")
    assert row(conn, doc)["status"] == "cancelled" and row(conn, inv)["status"] == "cancelled"


@pytest.mark.parametrize("source,expected", [("generated", pdf.OLD_SMALL_BUSINESS_NOTE),
                                             ("imported", pdf.SMALL_BUSINESS_NOTE)])
def test_invoices_from_before_the_note_was_recorded(source, expected):
    # payload_json is immutable, so the old shape is passed directly.
    old = {"payload_json": json.dumps({"sender": {}}), "source": source}
    assert archive._printed_note(old) == expected


def test_new_invoices_use_their_recorded_note(store):
    s, conn = store
    assert archive._printed_note(row(conn, issue(s, conn))) == pdf.SMALL_BUSINESS_NOTE


# ── Sums (one test per sum, with a refunded invoice and one from last year) ─


@pytest.fixture
def cancelled_book(env):
    """2026-001 paid and refunded in 2026 (Storno 2026-002); 2026-003 paid in 2025 and refunded in
    2026 (Storno 2026-004); 2026-005 still open."""
    s = load_settings()
    conn = db.connect(s.db_path)
    db.init_db(conn)
    first = issue(s, conn, number="2026-001", amount="700,00", issue_date="2026-02-20",
                  service_from="2026-02-20")
    archive.set_status(conn, first, "paid", date(2026, 3, 1), payment_method="bank")
    doc1 = cancel(s, conn, first, issue_date=date(2026, 4, 1))
    archive.set_status(conn, doc1, "paid", date(2026, 4, 5), payment_method="bank")

    second = issue(s, conn, number="2026-003", issue_date="2026-01-10", service_from="2025-12-01",
                   amount="500,00")
    archive.set_status(conn, second, "paid", date(2025, 12, 20), payment_method="bank")
    doc2 = cancel(s, conn, second, issue_date=date(2026, 2, 1))
    archive.set_status(conn, doc2, "paid", date(2026, 2, 10), payment_method="bank")

    third = issue(s, conn, number="2026-005", amount="100,00", issue_date="2026-05-02", service_from="2026-05-02")
    yield s, conn, {"first": first, "doc1": doc1, "second": second, "doc2": doc2, "third": third}
    conn.close()


def test_cash_summary_counts_receipts_and_refunds_in_their_own_years(cancelled_book):
    s, conn, ids = cancelled_book
    y2025 = expenses.cash_summary(conn, "2025")
    assert (y2025["income"], y2025["refunds"]) == (50000, 0)
    y2026 = expenses.cash_summary(conn, "2026")
    assert (y2026["income"], y2026["refunds"]) == (70000, 120000)
    assert y2026["surplus_before_afa"] == 70000 - 120000
    everything = expenses.cash_summary(conn)
    assert (everything["income"], everything["refunds"]) == (120000, 120000)


def test_turnover_counts_receipts_of_cancelled_invoices_and_ignores_cancellations(cancelled_book):
    s, conn, ids = cancelled_book
    st = turnover.status(conn, founding_year=2024, year=2026)
    assert st.received == 70000          # the receipt stays, the refund is not subtracted
    assert st.previous_year == 50000
    assert st.open == 10000              # only the open invoice, no cancellation document
    assert turnover._received(conn, 2026) == 70000


def test_turnover_crossing_ignores_cancellation_documents(cancelled_book):
    s, conn, ids = cancelled_book
    big = issue(s, conn, number="2026-006", amount="30.000,00", issue_date="2026-06-01",
                service_from="2026-06-01", limit_override="1", limit_reason="Test")
    archive.set_status(conn, big, "paid", date(2026, 6, 1), payment_method="bank")
    st = turnover.status(conn, founding_year=2026, year=2026)
    assert (st.crossing.label, st.crossing.day) == ("zu Rechnung 2026-006", "2026-06-01")


def test_list_totals_separate_invoices_and_cancellations(cancelled_book, logged_in):
    s, conn, ids = cancelled_book
    html = logged_in.get("/invoices?year=2026").get_data(as_text=True)
    assert "Stornorechnungen" in html and "Storno" in html
    assert "Erstattung offen" not in html          # both refunds are paid out
    cancel(s, conn, ids["third"], issue_date=date(2026, 6, 1))   # unpaid original: no refund
    html = logged_in.get("/invoices?year=2026").get_data(as_text=True)
    assert "Keine Erstattung" in html and "Erstattung offen" not in html


def test_export_marks_cancellations(cancelled_book):
    s, conn, ids = cancelled_book
    with zipfile.ZipFile(export.create_export(s, "2026")) as zf:
        rows = zf.read("invoices.csv").decode().split("\r\n")
        index = zf.read("index.xml").decode()
        readme = zf.read("README.txt").decode()
    header = rows[0].split(";")
    by_number = {r.split(";")[header.index("number")]: r.split(";") for r in rows[1:] if r}
    doc = by_number['"2026-004"'] if '"2026-004"' in by_number else by_number["2026-004"]
    assert doc[header.index("kind")] == "cancellation"
    assert doc[header.index("amount_cents")].startswith("-") and doc[header.index("amount_eur")].startswith("-")
    assert "cancels_invoice_id" in index and "Stornorechnung" in index
    assert "Summen über invoices" in readme and "kind = cancellation" in readme


def test_web_creates_a_cancellation_and_links_both_ways(logged_in, csrf, app):
    resp = logged_in.post("/invoices", data={**invoice_form(), "csrf_token": csrf})
    url = resp.headers["Location"]
    page = logged_in.get(url).get_data(as_text=True)
    logged_in.post(url + "/status", data={"status": "paid", "paid_date": "2026-09-20",
                                          "payment_method": "bank", "csrf_token": csrf_from(page)})
    page = logged_in.get(url).get_data(as_text=True)
    created = logged_in.post(url + "/cancel", data={"mode": "sent", "reason": "Kunde storniert",
                                                    "csrf_token": csrf_from(page)})
    doc_url = created.headers["Location"]
    doc_page = logged_in.get(doc_url).get_data(as_text=True)
    assert "Stornorechnung 2026-002" in doc_page and "Storno der Rechnung" in doc_page
    assert "Erstattung offen" in doc_page and "Erstattung erfassen" in doc_page
    original = logged_in.get(url).get_data(as_text=True)
    assert "Storniert durch die Stornorechnung" in original and "Erstattung offen" in original
    assert "Wieder auf offen setzen" not in original
