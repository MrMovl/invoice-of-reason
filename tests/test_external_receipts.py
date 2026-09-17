"""Receipts earned outside this tool: they count for the § 19 limits and nothing else."""

import sqlite3
import zipfile
from datetime import date

import pytest

from invoices import archive, chain, cli, db, expenses, export, turnover
from invoices.config import load_sender, load_settings
from tests.conftest import csrf_from, invoice_form


@pytest.fixture
def store(env):
    s = load_settings()
    conn = db.connect(s.db_path)
    db.init_db(conn)
    yield s, conn
    conn.close()


def issue_paid(s, conn, number, amount, paid_on, issue_on):
    inv = archive.issue_invoice(conn, s.archive_dir, archive.parse_invoice_form(
        invoice_form(number=number, amount=amount, issue_date=issue_on, service_from=issue_on,
                     limit_override="1", limit_reason="Testdaten")),
        load_sender(s.sender_file), s.retention_years)
    archive.set_status(conn, inv, "paid", date.fromisoformat(paid_on), payment_method="bank")
    return inv


def test_entries_are_append_only_and_chained(store):
    s, conn = store
    db.add_external_receipt(conn, "2026-03-01", 120000, "PV-Einspeisung", "Jahresabrechnung")
    db.add_external_receipt(conn, "2026-04-01", -20000, "PV-Einspeisung", "Korrektur: doppelt erfasst")
    rows = conn.execute("SELECT * FROM external_receipts ORDER BY id").fetchall()
    assert [r["amount_cents"] for r in rows] == [120000, -20000]
    assert all(r["hash"] for r in rows)
    assert chain.verify_chains(conn) == [] and cli.main(["verify"]) == 0
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE external_receipts SET amount_cents = 1 WHERE id = 1")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM external_receipts WHERE id = 1")
    conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        db.add_external_receipt(conn, "2026-05-01", 0, "Nichts")
    conn.rollback()


def test_tampering_with_an_entry_is_detected(store):
    s, conn = store
    db.add_external_receipt(conn, "2026-03-01", 120000, "PV")
    trigger = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'external_receipts_append_only_update'").fetchone()[0]
    with conn:
        conn.execute("DROP TRIGGER external_receipts_append_only_update")
        conn.execute("UPDATE external_receipts SET amount_cents = 1 WHERE id = 1")
        conn.execute(trigger)
    assert chain.verify_chains(conn) == [("Umsätze außerhalb des Programms #1", "Hash-Kette unterbrochen (Eintrag verändert)")]


def test_counted_for_the_limits_in_both_years(store):
    s, conn = store
    issue_paid(s, conn, "2026-001", "10.000,00", "2026-02-01", "2026-01-15")
    db.add_external_receipt(conn, "2026-03-01", 500000, "Marktplatz")
    db.add_external_receipt(conn, "2025-06-01", 2400000, "PV-Einspeisung")
    st = turnover.status(conn, founding_year=2024, year=2026)
    assert (st.received_internal, st.received_external, st.received) == (1000000, 500000, 1500000)
    assert st.previous_year == 2400000
    previous = turnover.status(conn, founding_year=2024, year=2025)
    assert previous.received_external == 2400000 and previous.received_internal == 0


def test_external_receipt_can_be_the_crossing_one(store):
    s, conn = store
    issue_paid(s, conn, "2026-001", "20.000,00", "2026-02-01", "2026-01-15")
    db.add_external_receipt(conn, "2026-03-01", 400000, "PV-Einspeisung")
    db.add_external_receipt(conn, "2026-04-01", 300000, "Marktplatz")
    st = turnover.status(conn, founding_year=2026, year=2026)
    # 20.000 + 4.000 stays below 25.000; the second external receipt crosses the limit.
    assert st.lost and st.crossing.external and st.crossing.label == "außerhalb erfasst: Marktplatz"
    assert any("außerhalb erfasst: Marktplatz am 01.04.2026" in w for w in st.warnings)
    assert "Grenze von 25.000,00 €" in turnover.issue_problem(conn, 2026, date(2026, 5, 1), 100)


def test_not_part_of_the_cash_overview(store):
    s, conn = store
    issue_paid(s, conn, "2026-001", "700,00", "2026-02-01", "2026-01-15")
    db.add_external_receipt(conn, "2026-03-01", 500000, "Marktplatz")
    summary = expenses.cash_summary(conn, "2026")
    assert summary["income"] == 70000 and summary["surplus_before_afa"] == 70000


def test_migration_on_an_existing_database(env, monkeypatch):
    s = load_settings()
    conn = db.connect(s.db_path)
    number = next(i for i, (d, _) in enumerate(db.MIGRATIONS, start=1)
                  if d == "external receipts for the § 19 turnover limits")
    with monkeypatch.context() as m:
        m.setattr(db, "MIGRATIONS", db.MIGRATIONS[:number - 1])
        db.init_db(conn)
        db.add_system_event(conn, "version", "test")
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'external_receipts'").fetchone()
    db.init_db(conn)
    assert chain.verify_chains(conn) == []
    db.add_external_receipt(conn, "2026-03-01", 100, "Test")
    assert chain.verify_chains(conn) == []
    conn.close()


def test_export_includes_the_table(store):
    s, conn = store
    db.add_external_receipt(conn, "2026-03-01", 120000, "PV-Einspeisung", "Jahresabrechnung")
    with zipfile.ZipFile(export.create_export(s)) as zf:
        header, first = zf.read("external_receipts.csv").decode().split("\r\n")[:2]
        index = zf.read("index.xml").decode()
    values = dict(zip(header.split(";"), first.split(";")))
    assert values["source"] == "PV-Einspeisung" and values["amount_eur"] == "1200,00"
    assert "external_receipts.csv" in index and "Umsatzgrenzen" in index


def test_web_form_records_and_groups(logged_in):
    page = logged_in.get("/umsaetze-extern").get_data(as_text=True)
    token = csrf_from(page)
    assert "nicht der Gewinn" in page and "Differenzbesteuerung" in page
    logged_in.post("/umsaetze-extern", data={"received_on": "2026-03-01", "amount": "1.200,00",
                                             "source": "PV-Einspeisung", "direction": "receipt",
                                             "note": "", "csrf_token": token})
    logged_in.post("/umsaetze-extern", data={"received_on": "2026-04-01", "amount": "200,00",
                                             "source": "PV-Einspeisung", "direction": "correction",
                                             "note": "Doppelt erfasst", "csrf_token": token})
    refused = logged_in.post("/umsaetze-extern", data={"received_on": "2026-04-02", "amount": "50,00",
                                                      "source": "PV-Einspeisung", "direction": "correction",
                                                      "note": "", "csrf_token": token}, follow_redirects=True)
    assert "Notiz fehlt" in refused.get_data(as_text=True)
    html = logged_in.get("/umsaetze-extern").get_data(as_text=True)
    assert "1.000,00 €" in html and "-200,00 €" in html and "Doppelt erfasst" in html
    assert "1.000,00 €" in logged_in.get("/invoices").get_data(as_text=True)


def test_future_dates_are_refused(logged_in):
    page = logged_in.get("/umsaetze-extern").get_data(as_text=True)
    future = date(date.today().year + 1, 1, 2).isoformat()
    resp = logged_in.post("/umsaetze-extern", data={"received_on": future, "amount": "10,00", "source": "X",
                                                    "direction": "receipt", "note": "",
                                                    "csrf_token": csrf_from(page)}, follow_redirects=True)
    assert "Zukunft" in resp.get_data(as_text=True)
