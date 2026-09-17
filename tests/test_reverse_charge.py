"""§ 13b UStG: marking purchases where the recipient owes the VAT, hints at upload, summary."""

import re
import shutil
import zipfile
from datetime import date
from pathlib import Path

import pytest

from invoices import archive, chain, cli, db, einvoice, expenses, export
from invoices.config import load_settings
from tests.conftest import csrf_from, make_pdf

FIXTURES = Path(__file__).parent / "fixtures"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
needs_pdftotext = pytest.mark.skipif(not shutil.which("pdftotext"), reason="pdftotext not installed")


@pytest.fixture
def store(env):
    s = load_settings()
    conn = db.connect(s.db_path)
    db.init_db(conn)
    yield s, conn
    conn.close()


def foreign(xml: bytes, country: str = "IE", category: str | None = "AE") -> bytes:
    """Turn a German sample invoice into one from a seller abroad."""
    xml = xml.replace(b"<cbc:IdentificationCode>DE</cbc:IdentificationCode>",
                      f"<cbc:IdentificationCode>{country}</cbc:IdentificationCode>".encode(), 1)
    xml = xml.replace(b"<ram:CountryID>DE</ram:CountryID>", f"<ram:CountryID>{country}</ram:CountryID>".encode(), 1)
    if category:
        xml = xml.replace(b"<cbc:ID>S</cbc:ID>", f"<cbc:ID>{category}</cbc:ID>".encode())
        xml = xml.replace(b"<ram:CategoryCode>S</ram:CategoryCode>", f"<ram:CategoryCode>{category}</ram:CategoryCode>".encode())
    return xml


def review(conn, exp_id, **overrides):
    form = {"vendor": "Cloud Ltd", "amount": "100,00", "expense_date": "2026-02-10", "status": "paid",
            "category": "Hosting", "paid_date": "", "invoice_number": "", "notes": "",
            "payment_method": "bank", **overrides}
    expenses.update_expense(conn, exp_id, expenses.parse_expense_form(form))


def upload(s, conn, data, name):
    return expenses.store_upload(conn, s.expenses_dir, data, name, s.retention_years, today=date(2026, 9, 16))


@pytest.mark.parametrize("fixture", ["ubl-invoice.xml", "cii-invoice.xml"])
def test_einvoice_seller_country_and_vat_categories(fixture):
    xml = (FIXTURES / fixture).read_bytes()
    home = einvoice.parse(xml)
    assert home.seller_country == "DE" and "S" in home.vat_categories
    assert expenses.reverse_charge_hint(home, "") == ""
    abroad = einvoice.parse(foreign(xml))
    assert abroad.seller_country == "IE" and "AE" in abroad.vat_categories
    hint = expenses.reverse_charge_hint(abroad, "")
    assert "Steuerkategorie AE" in hint and "Ausland (IE)" in hint


def test_foreign_seller_alone_is_a_hint(store):
    xml = foreign((FIXTURES / "ubl-invoice.xml").read_bytes(), country="NL", category=None)
    assert expenses.reverse_charge_hint(einvoice.parse(xml), "") == "Rechnungssteller mit Sitz im Ausland (NL)"


@pytest.mark.parametrize("text,expected", [
    ("VAT: Reverse charge applies", "Reverse charge"),
    ("Steuerschuldnerschaft des Leistungsempfängers", "Steuerschuldnerschaft des Leistungsempf"),
    ("Umkehrung der Steuerschuld", "Umkehrung der Steuerschuld"),
    ("Article 196 Council Directive 2006/112/EC", "Article 196"),
    ("Rechnung über 19 % Umsatzsteuer", None),
])
def test_text_hints(text, expected):
    hint = expenses.reverse_charge_hint(None, text)
    assert (expected in hint) if expected else hint == ""


def test_upload_stores_hint_but_never_sets_the_field(store):
    s, conn = store
    exp_id = upload(s, conn, foreign((FIXTURES / "ubl-invoice.xml").read_bytes()), "ie.xml")
    row = conn.execute("SELECT reverse_charge, suggestion_json FROM expenses WHERE id = ?", (exp_id,)).fetchone()
    assert row["reverse_charge"] == ""
    assert "Steuerkategorie AE" in row["suggestion_json"]


@needs_pdftotext
def test_pdf_text_hint_at_upload(store):
    s, conn = store
    exp_id = upload(s, conn, make_pdf(["Cloud Ltd, Dublin", "Reverse charge", "Total | 100,00 €"]), "c.pdf")
    row = conn.execute("SELECT reverse_charge, suggestion_json FROM expenses WHERE id = ?", (exp_id,)).fetchone()
    assert row["reverse_charge"] == "" and "Reverse charge" in row["suggestion_json"]


def test_setting_and_clearing_is_logged(store):
    s, conn = store
    exp_id = upload(s, conn, PNG, "a.png")
    review(conn, exp_id, reverse_charge="13b")
    assert conn.execute("SELECT reverse_charge FROM expenses").fetchone()[0] == "13b"
    assert "Steuerschuldnerschaft § 13b UStG: – → ja" in conn.execute(
        "SELECT detail FROM expense_events ORDER BY id DESC").fetchone()[0]
    review(conn, exp_id)
    assert conn.execute("SELECT detail FROM expense_events ORDER BY id DESC").fetchone()[0] == \
        "Steuerschuldnerschaft § 13b UStG: ja → –"
    with pytest.raises(archive.ArchiveError, match="Steuerschuldnerschaft"):
        expenses.parse_expense_form({"status": "void", "notes": "x", "reverse_charge": "yes"})
    assert chain.verify_chains(conn) == []


def test_summary_per_quarter_with_orientation_value(store):
    s, conn = store
    dates = [("2026-02-10", "100,00", "paid"), ("2026-03-31", "50,55", "open"), ("2026-04-01", "10,00", "paid"),
             ("2026-12-31", "1,00", "paid"), ("2025-12-31", "999,00", "paid"), ("2026-05-05", "500,00", "void")]
    for i, (day, amount, status) in enumerate(dates):
        exp_id = upload(s, conn, PNG + bytes([i]), f"{i}.png")
        review(conn, exp_id, expense_date=day, amount=amount, status=status, reverse_charge="13b",
               notes="Fehl-Upload" if status == "void" else "",
               payment_method="bank" if status == "paid" else "")
    plain = upload(s, conn, PNG + b"plain", "p.png")
    review(conn, plain, amount="700,00")

    summary = expenses.reverse_charge_summary(conn, 2026)
    assert [q["base"] for q in summary["quarters"]] == [15055, 1000, 0, 100]
    assert summary["base"] == 16155 and summary["count"] == 4
    assert summary["tax_orientation"] == 3069  # 161,55 € × 19 % = 30,69 €
    assert summary["quarters"][0]["tax_orientation"] == 2860  # 150,55 € × 19 % = 28,6045 €
    assert expenses.cash_summary(conn, "2026")["reverse_charge"]["base"] == 16155


def test_migration_keeps_existing_database_verifiable(env, monkeypatch):
    s = load_settings()
    conn = db.connect(s.db_path)
    number = next(i for i, (desc, _) in enumerate(db.MIGRATIONS, start=1) if desc == "reverse_charge on expenses")
    with monkeypatch.context() as m:
        m.setattr(db, "MIGRATIONS", db.MIGRATIONS[:number - 1])
        db.init_db(conn)
        exp_id = upload(s, conn, PNG, "a.png")
        form = {"vendor": "V", "amount": "10,00", "expense_date": "2026-01-02", "status": "paid",
                "category": "Hosting", "payment_method": "bank"}
        columns = {r[1] for r in conn.execute("PRAGMA table_info(expenses)")}
        values = {k: v for k, v in expenses.parse_expense_form(form).items() if k in columns}
        expenses.update_expense(conn, exp_id, values)
    db.init_db(conn)
    assert db.schema_version(conn) >= number
    assert conn.execute("SELECT reverse_charge FROM expenses").fetchone()[0] == ""
    assert chain.verify_chains(conn) == []
    assert cli.main(["verify"]) == 0
    conn.close()


def test_web_filter_badge_hint_and_summary(logged_in, app):
    s = app.config["SETTINGS"]
    conn = db.connect(s.db_path)
    marked = upload(s, conn, foreign((FIXTURES / "ubl-invoice.xml").read_bytes()), "ie.xml")
    review(conn, marked, vendor="Cloud Ltd", reverse_charge="13b")
    hinted = upload(s, conn, foreign((FIXTURES / "cii-invoice.xml").read_bytes()), "nl.xml")
    other = upload(s, conn, PNG, "x.png")
    review(conn, other, vendor="Bäckerei Kranz")
    conn.close()

    everything = logged_in.get("/expenses?year=2026").get_data(as_text=True)
    assert "Bäckerei Kranz" in everything and "§ 13b UStG 2026" in everything
    assert re.search(r"Q1</td><td class=\"num\">100,00 €", everything)
    only = logged_in.get("/expenses?rc=1").get_data(as_text=True)
    assert "Cloud Ltd" in only and "Bäckerei Kranz" not in only

    detail = logged_in.get(f"/expenses/{hinted}").get_data(as_text=True)
    assert "Möglicherweise § 13b UStG" in detail
    page = logged_in.get(f"/expenses/{hinted}").get_data(as_text=True)
    resp = logged_in.post(f"/expenses/{hinted}", data={
        "csrf_token": csrf_from(page), "vendor": "Muster", "amount": "49,98", "expense_date": "2026-03-01",
        "status": "paid", "category": "Büro", "payment_method": "bank", "reverse_charge": "13b"})
    assert resp.status_code == 302
    assert "Möglicherweise § 13b UStG" not in logged_in.get(f"/expenses/{hinted}").get_data(as_text=True)


def test_export_has_the_column_with_description(store):
    s, conn = store
    exp_id = upload(s, conn, PNG, "a.png")
    review(conn, exp_id, reverse_charge="13b")
    with zipfile.ZipFile(export.create_export(s)) as zf:
        header, first = zf.read("expenses.csv").decode().split("\r\n")[:2]
        index = zf.read("index.xml").decode()
    column = header.split(";").index("reverse_charge")
    assert first.split(";")[column] == "13b"
    assert "§ 13b UStG" in index
