"""A paid invoice cannot simply be cancelled: that used to erase the receipt."""

import sqlite3
from datetime import date

import pytest

from invoices import archive, chain, db, expenses, turnover
from invoices.config import load_sender, load_settings
from tests.conftest import csrf_from, invoice_form


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


def test_paid_invoice_cannot_be_cancelled_and_receipt_stays(store):
    s, conn = store
    inv = issue(s, conn)
    archive.set_status(conn, inv, "paid", date(2026, 9, 20), payment_method="bank")
    with pytest.raises(archive.ArchiveError, match="Stornorechnung und die Erfassung der Erstattung"):
        archive.set_status(conn, inv, "cancelled", note="Doppelt")
    row = conn.execute("SELECT status, paid_date, payment_method FROM invoices").fetchone()
    assert tuple(row) == ("paid", "2026-09-20", "bank")
    assert expenses.cash_summary(conn, "2026")["income"] == 70000
    assert turnover.status(conn, 2026, 2026).received == 70000
    assert conn.execute("SELECT COUNT(*) FROM events WHERE action = 'status:cancelled'").fetchone()[0] == 0


def test_mistaken_payment_can_be_undone_then_cancelled(store):
    s, conn = store
    inv = issue(s, conn)
    archive.set_status(conn, inv, "paid", date(2026, 9, 20), payment_method="bank")
    archive.set_status(conn, inv, "open")
    archive.set_status(conn, inv, "cancelled", note="Nie versandt")
    assert conn.execute("SELECT status FROM invoices").fetchone()[0] == "cancelled"
    assert archive.lost_receipt_findings(conn) == []


def test_old_paid_then_cancelled_invoices_are_reported(store):
    s, conn = store
    inv = issue(s, conn)
    archive.set_status(conn, inv, "paid", date(2025, 12, 20), payment_method="bank")
    # What the code did before the guard: cancel directly, clearing the payment.
    with conn:
        conn.execute("UPDATE invoices SET status = 'cancelled', paid_date = NULL, payment_method = '' WHERE id = ?", (inv,))
        db.add_event(conn, inv, "status:cancelled",
                     "Status: Bezahlt → Storniert; Bezahlt am: 20.12.2025 → –; Zahlungsart: Überweisung/Karte → –; Grund: „x“")
    findings = archive.lost_receipt_findings(conn)
    assert len(findings) == 1 and "Rechnung 2026-001" in findings[0] and "20.12.2025" in findings[0]
    assert chain.verify_chains(conn) == []
    before = conn.execute("SELECT * FROM invoices").fetchone()
    archive.lost_receipt_findings(conn)
    assert tuple(conn.execute("SELECT * FROM invoices").fetchone()) == tuple(before)


def test_web_hides_cancel_form_for_paid_and_lists_findings(logged_in, csrf, app):
    resp = logged_in.post("/invoices", data={**invoice_form(), "csrf_token": csrf})
    url = resp.headers["Location"]
    page = logged_in.get(url).get_data(as_text=True)
    logged_in.post(url + "/status", data={"status": "paid", "paid_date": "2026-09-20", "payment_method": "bank",
                                          "csrf_token": csrf_from(page)})
    page = logged_in.get(url).get_data(as_text=True)
    assert 'value="cancelled"' not in page and "Diese Funktion folgt in Kürze" in page
    refused = logged_in.post(url + "/status", data={"status": "cancelled", "note": "x", "csrf_token": csrf_from(page)},
                             follow_redirects=True).get_data(as_text=True)
    assert "Stornorechnung" in refused
    assert "Zahlungseingänge prüfen" not in logged_in.get("/invoices").get_data(as_text=True)
