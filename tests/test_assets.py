"""Capital assets: excluded from the expense total, hint above the GWG limit."""

import zipfile
from datetime import date

import pytest

from invoices import archive, chain, cli, db, expenses, export
from invoices.config import load_sender, load_settings
from tests.conftest import csrf_from, invoice_form

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def store(env):
    s = load_settings()
    conn = db.connect(s.db_path)
    db.init_db(conn)
    yield s, conn
    conn.close()


def upload(s, conn, suffix=b""):
    return expenses.store_upload(conn, s.expenses_dir, PNG + suffix, "a.png", s.retention_years,
                                 today=date(2026, 9, 16))


def review(conn, exp_id, **overrides):
    form = {"vendor": "Hardware GmbH", "amount": "1.190,00", "expense_date": "2026-03-01", "status": "paid",
            "category": "Hardware", "paid_date": "", "invoice_number": "", "notes": "",
            "payment_method": "bank", **overrides}
    expenses.update_expense(conn, exp_id, expenses.parse_expense_form(form))


def test_threshold_constant():
    assert expenses.GWG_LIMIT_NET_CENTS == 80_000


def test_marking_is_logged_and_reversible(store):
    s, conn = store
    exp_id = upload(s, conn)
    review(conn, exp_id, treatment="asset")
    assert conn.execute("SELECT treatment FROM expenses").fetchone()[0] == "asset"
    assert "Anlagegut (AfA): – → ja" in conn.execute("SELECT detail FROM expense_events ORDER BY id DESC").fetchone()[0]
    review(conn, exp_id)
    assert conn.execute("SELECT detail FROM expense_events ORDER BY id DESC").fetchone()[0] == "Anlagegut (AfA): ja → –"
    with pytest.raises(archive.ArchiveError, match="Behandlung"):
        expenses.parse_expense_form({"status": "void", "notes": "x", "treatment": "gwg"})
    assert chain.verify_chains(conn) == []


def test_assets_are_excluded_from_expenses_and_surplus_is_before_afa(store):
    s, conn = store
    inv = archive.issue_invoice(conn, s.archive_dir, archive.parse_invoice_form(invoice_form()),
                                load_sender(s.sender_file), s.retention_years)
    archive.set_status(conn, inv, "paid", date(2026, 9, 20), payment_method="bank")
    laptop, cable, old = upload(s, conn, b"1"), upload(s, conn, b"2"), upload(s, conn, b"3")
    review(conn, laptop, treatment="asset")
    review(conn, cable, amount="19,90")
    review(conn, old, amount="2.000,00", treatment="asset", expense_date="2025-06-01")
    summary = expenses.cash_summary(conn, "2026")
    assert (summary["expenses"], summary["assets"], summary["surplus_before_afa"]) == (1990, 119000, 70000 - 1990)
    assert expenses.cash_summary(conn)["assets"] == 319000


@pytest.mark.parametrize("values,hint", [
    ({"amount_cents": 80001, "status": "paid", "treatment": ""}, True),
    ({"amount_cents": 80000, "status": "paid", "treatment": ""}, False),
    ({"amount_cents": 95200, "status": "paid", "treatment": "asset"}, False),
    ({"amount_cents": 95200, "status": "void", "treatment": ""}, False),
    ({"amount_cents": None, "status": "open", "treatment": ""}, False),
])
def test_hint_above_limit_unless_marked(values, hint):
    assert expenses.asset_hint(values) is hint


def test_review_form_hint_does_not_block(logged_in, app):
    s = app.config["SETTINGS"]
    conn = db.connect(s.db_path)
    exp_id = upload(s, conn)
    review(conn, exp_id)
    conn.close()
    page = logged_in.get(f"/expenses/{exp_id}").get_data(as_text=True)
    assert "GWG-Grenze" in page
    resp = logged_in.post(f"/expenses/{exp_id}", data={
        "csrf_token": csrf_from(page), "vendor": "Hardware GmbH", "amount": "1.190,00", "expense_date": "2026-03-01",
        "status": "paid", "category": "Hardware", "payment_method": "bank", "treatment": "asset"})
    assert resp.status_code == 302
    assert "GWG-Grenze" not in logged_in.get(f"/expenses/{exp_id}").get_data(as_text=True)
    assert "Anlagegut" in logged_in.get("/expenses?asset=1").get_data(as_text=True)
    html = logged_in.get("/invoices?year=2026").get_data(as_text=True)
    assert "Überschuss vor AfA" in html and "Anlagegüter" in html


def test_migration_keeps_existing_database_verifiable(env, monkeypatch):
    s = load_settings()
    conn = db.connect(s.db_path)
    number = next(i for i, (desc, _) in enumerate(db.MIGRATIONS, start=1) if desc == "treatment on expenses")
    with monkeypatch.context() as m:
        m.setattr(db, "MIGRATIONS", db.MIGRATIONS[:number - 1])
        db.init_db(conn)
        exp_id = upload(s, conn)
        columns = {r[1] for r in conn.execute("PRAGMA table_info(expenses)")}
        form = {"vendor": "V", "amount": "900,00", "expense_date": "2026-01-02", "status": "paid",
                "category": "Hardware", "payment_method": "bank"}
        expenses.update_expense(conn, exp_id, {k: v for k, v in expenses.parse_expense_form(form).items()
                                               if k in columns})
    db.init_db(conn)
    assert conn.execute("SELECT treatment FROM expenses").fetchone()[0] == ""
    assert chain.verify_chains(conn) == [] and cli.main(["verify"]) == 0
    conn.close()


def test_export_describes_the_column(store):
    s, conn = store
    review(conn, upload(s, conn), treatment="asset")
    with zipfile.ZipFile(export.create_export(s)) as zf:
        header, first = zf.read("expenses.csv").decode().split("\r\n")[:2]
        assert "Anlagegut" in zf.read("index.xml").decode()
    assert first.split(";")[header.split(";").index("treatment")] == "asset"
