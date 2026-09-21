"""invoices report --json: read-only business figures for an SSH forced command. All data fictitious."""

import hashlib
import json
import socket
import sqlite3
from datetime import date

import pytest

from invoices import archive, cli, db, expenses, report
from invoices.config import load_sender, load_settings
from tests.conftest import invoice_form

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

# A sender whose every value is distinctive, so any of them showing up in the report is a leak.
SENDER = {
    "name": "Frieda Fiktiv",
    "tagline": "Erfundene Dienste",
    "street": "Nirgendweg 42",
    "city": "99999 Phantasiehausen",
    "email": "frieda@fiktiv.invalid",
    "website": "fiktiv.invalid",
    "tax_number": "99/999/99999",
    "iban": "DE00 9999 9999 9999 9999 99",
    "bic": "FIKTDEXX999",
    "account_holder": "Frieda Fiktiv Treuhand",
}


@pytest.fixture
def store(env, monkeypatch):
    sender_file = env / "sender.toml"
    sender_file.write_text("[sender]\n" + "".join(f'{k} = "{v}"\n' for k, v in SENDER.items()))
    monkeypatch.setenv("INVOICES_SENDER_FILE", str(sender_file))
    monkeypatch.setenv("INVOICES_FOUNDING_YEAR", "2025")
    s = load_settings()
    conn = db.connect(s.db_path)
    db.init_db(conn)
    yield s, conn
    conn.close()


def issue(s, conn, number, customer, amount, issue_on, paid_on=None):
    inv = archive.issue_invoice(conn, s.archive_dir, archive.parse_invoice_form(invoice_form(
        number=number, customer_name=customer, title=f"Leistung {number}", amount=amount,
        issue_date=issue_on, service_from=issue_on, limit_override="1", limit_reason="Testdaten")),
        load_sender(s.sender_file), s.retention_years)
    if paid_on:
        archive.set_status(conn, inv, "paid", date.fromisoformat(paid_on), payment_method="bank")
    return inv


def expense(s, conn, marker, amount, paid_on, treatment=""):
    exp = expenses.store_upload(conn, s.expenses_dir, PNG + marker, "beleg.png", s.retention_years,
                                today=date(2026, 9, 16))
    expenses.update_expense(conn, exp, expenses.parse_expense_form({
        "vendor": "Beispiel Bürobedarf", "expense_date": paid_on, "paid_date": paid_on, "category": "Büro",
        "payment_method": "bank", "amount": amount, "status": "paid", "treatment": treatment}))


@pytest.fixture
def book(store):
    """Fictitious 2026: two customers, one refunded cancellation, one open invoice, expenses."""
    s, conn = store
    issue(s, conn, "2025-001", "Kunde Alpha GmbH", "500,00", "2025-12-01", paid_on="2026-01-10")
    issue(s, conn, "2026-001", "Kunde Alpha GmbH", "3.000,00", "2026-02-01", paid_on="2026-02-20")
    beta = issue(s, conn, "2026-002", "Beta Studio", "1.000,00", "2026-03-01", paid_on="2026-03-15")
    issue(s, conn, "2026-003", "Beta Studio", "250,00", "2026-04-01")
    storno = archive.cancel_invoice(conn, s.archive_dir, beta, "Leistung nicht erbracht",
                                    load_sender(s.sender_file), s.retention_years, issue_date=date(2026, 4, 2))
    archive.set_status(conn, storno, "paid", date(2026, 4, 5), payment_method="bank")
    db.add_external_receipt(conn, "2026-05-01", 20000, "Marktplatz")
    expense(s, conn, b"a", "120,00", "2026-02-03")
    expense(s, conn, b"b", "1.200,00", "2026-06-10", treatment="asset")
    return s, conn


def run_report(capsys, *args):
    code = cli.main(["report", "--json", *args])
    out, err = capsys.readouterr()
    return code, out, err


def test_report_contents(book, capsys):
    code, out, err = run_report(capsys, "--year", "2026")
    assert code == 0 and err == ""
    data = json.loads(out)
    assert data["year"] == 2026

    by_number = {i["number"]: i for i in data["invoices"]}
    assert set(by_number) == {"2025-001", "2026-001", "2026-002", "2026-003", "2026-004"}
    assert by_number["2026-002"] == {
        "number": "2026-002", "type": "invoice", "issue_date": "2026-03-01", "customer": "Beta Studio",
        "title": "Leistung 2026-002", "amount_cents": 100000, "status": "cancelled", "paid_date": "2026-03-15",
        "cancels": None, "cancelled_by": "2026-004"}
    assert by_number["2026-004"]["type"] == "cancellation"
    assert by_number["2026-004"]["cancels"] == "2026-002" and by_number["2026-004"]["amount_cents"] == -100000
    assert by_number["2026-003"]["status"] == "open" and by_number["2026-003"]["paid_date"] is None

    months = {m["month"]: m for m in data["cash"]["months"]}
    assert len(months) == 12
    assert months["2026-01"]["income_cents"] == 50000
    assert months["2026-02"] == {"month": "2026-02", "income_cents": 300000, "refunds_cents": 0,
                                  "expenses_cents": 12000, "assets_cents": 0,
                                  "surplus_before_afa_cents": 288000}
    assert months["2026-04"]["refunds_cents"] == 100000
    assert months["2026-06"]["assets_cents"] == 120000 and months["2026-06"]["surplus_before_afa_cents"] == 0
    assert data["cash"]["totals"] == {"income_cents": 450000, "refunds_cents": 100000, "expenses_cents": 12000,
                                      "assets_cents": 120000, "surplus_before_afa_cents": 338000}

    assert data["small_business"] == {
        "received_cents": 450000, "open_cents": 25000, "received_outside_cents": 20000,
        "limit_cents": 10_000_000, "headroom_cents": 10_000_000 - 495000, "founding_year": False,
        "previous_year_cents": 0, "lost": False}

    # Alpha received 3,500 € in 2026 (2025-001 was paid in January); Beta's 1,000 € were refunded.
    assert data["largest_customer"] == {"customer": "Kunde Alpha GmbH", "receipts_cents": 350000,
                                        "total_cents": 350000, "share_percent": 100.0}


def test_totals_match_the_web_overview(book):
    s, conn = book
    cash = report.build(conn, 2026)["cash"]["totals"]
    summary = expenses.cash_summary(conn, "2026")
    assert (cash["income_cents"], cash["refunds_cents"], cash["expenses_cents"], cash["assets_cents"],
            cash["surplus_before_afa_cents"]) == (summary["income"], summary["refunds"], summary["expenses"],
                                                  summary["assets"], summary["surplus_before_afa"])


def test_largest_customer_share(store):
    s, conn = store
    issue(s, conn, "2026-001", "Kunde Alpha GmbH", "3.000,00", "2026-02-01", paid_on="2026-02-20")
    issue(s, conn, "2026-002", "Beta Studio", "1.000,00", "2026-03-01", paid_on="2026-03-15")
    assert report.build(conn, 2026)["largest_customer"] == {
        "customer": "Kunde Alpha GmbH", "receipts_cents": 300000, "total_cents": 400000, "share_percent": 75.0}
    assert report.build(conn, 2027)["largest_customer"] is None


def test_no_sender_data_paths_or_system_details_in_output(book, capsys):
    s, conn = book
    code, out, _ = run_report(capsys, "--year", "2026")
    assert code == 0
    forbidden = list(SENDER.values()) + [
        str(s.data_dir), str(s.backup_dir), str(s.sender_file), str(s.db_path), "sqlite3", "archive/",
        "expenses/", ".pdf", ".png", "INVOICES_", socket.gethostname(), "Traceback"]
    for value in forbidden:
        assert value not in out, value
    data = json.loads(out)
    assert set(data) == {"year", "generated_at", "currency", "invoices", "cash", "small_business",
                         "largest_customer"}
    assert all(set(i) == {"number", "type", "issue_date", "customer", "title", "amount_cents", "status",
                          "paid_date", "cancels", "cancelled_by"} for i in data["invoices"])


def db_state(s):
    """Database file content plus the tables every write path touches."""
    digest = hashlib.sha256(s.db_path.read_bytes()).hexdigest()
    conn = sqlite3.connect(s.db_path)
    try:
        counts = tuple(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                       for t in ("system_events", "control_runs", "events"))
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    return digest, counts, version


def test_report_writes_nothing(book, capsys):
    s, conn = book
    conn.close()   # last connection closed: the WAL is checkpointed into the file
    before = db_state(s)
    assert run_report(capsys, "--year", "2026")[0] == 0
    assert run_report(capsys)[0] == 0
    assert db_state(s) == before


def test_connection_refuses_writes(book):
    s, _ = book
    conn = report.connect_readonly(s.db_path)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO control_runs (at, kind, ok) VALUES ('x', 'verify', 1)")
    conn.close()


def test_no_migration_on_an_old_schema(env, monkeypatch, capsys):
    s = load_settings()
    conn = db.connect(s.db_path)
    with monkeypatch.context() as m:
        m.setattr(db, "MIGRATIONS", db.MIGRATIONS[:1])
        db.init_db(conn)
    conn.close()
    before = hashlib.sha256(s.db_path.read_bytes()).hexdigest()
    code, out, err = run_report(capsys, "--year", "2026")
    # The old schema lacks columns the report reads: it fails generically instead of migrating.
    assert code != 0 and out == "" and err.strip() == "FEHLER: Bericht nicht verfügbar."
    assert hashlib.sha256(s.db_path.read_bytes()).hexdigest() == before
    check = sqlite3.connect(s.db_path)
    assert check.execute("PRAGMA user_version").fetchone()[0] == 1
    check.close()


def test_missing_database_is_a_generic_error_and_creates_nothing(env, capsys):
    s = load_settings()
    code, out, err = run_report(capsys)
    assert code != 0 and out == ""
    assert err.strip() == "FEHLER: Bericht nicht verfügbar."
    assert not s.db_path.exists() and not s.data_dir.exists()


def test_unexpected_errors_reveal_nothing(book, capsys, monkeypatch):
    s, _ = book

    def boom(*_a, **_k):
        raise RuntimeError(f"secret detail {s.db_path} {socket.gethostname()}")

    monkeypatch.setattr(report, "build", boom)
    code, out, err = run_report(capsys, "--year", "2026")
    assert code != 0 and out == ""
    assert err.strip() == "FEHLER: Bericht nicht verfügbar."


def test_out_writes_the_same_report_to_a_world_readable_file(book, capsys, tmp_path):
    target = tmp_path / "reports" / "report.json"
    target.parent.mkdir()
    code, out, err = run_report(capsys, "--year", "2026", "--out", str(target))
    assert code == 0 and out == "" and err == ""
    written = json.loads(target.read_text())
    _, stdout, _ = run_report(capsys, "--year", "2026")
    expected = json.loads(stdout)
    written.pop("generated_at"), expected.pop("generated_at")
    assert written == expected
    assert target.stat().st_mode & 0o777 == 0o644
    assert [f.name for f in target.parent.iterdir()] == ["report.json"]   # no temp file left behind


def test_failed_run_keeps_the_last_report(book, capsys, tmp_path, monkeypatch):
    target = tmp_path / "report.json"
    assert run_report(capsys, "--out", str(target))[0] == 0
    before = target.read_text()
    monkeypatch.setattr(report, "build", lambda *_a, **_k: 1 / 0)
    code, out, err = run_report(capsys, "--out", str(target))
    assert code != 0 and out == "" and err.strip() == "FEHLER: Bericht nicht verfügbar."
    assert target.read_text() == before and [f.name for f in tmp_path.iterdir()].count("report.json") == 1


def test_every_needs_out(env, capsys):
    assert cli.main(["report", "--json", "--every", "60"]) != 0


def test_bad_arguments_are_refused(env, capsys):
    for argv in (["report", "--json", "--year", "26"], ["report", "--json", "--year", "2026; id"], ["report"]):
        with pytest.raises(SystemExit) as exc:
            cli.main(argv)
        assert exc.value.code != 0
    _, err = capsys.readouterr()
    assert str(env) not in err
