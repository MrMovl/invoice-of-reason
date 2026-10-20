import json
import shutil
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from invoices import pdf
from invoices.config import load_sender
from invoices.pdf import InvoiceData, LayoutOverflowError, LineItem, format_amount, render_invoice
from tests.conftest import page_count


def item(title="Beratung & Entwicklung <b>", description="Zeile 1\nZeile 2", amount="700.00"):
    return LineItem(title=title, description=description, amount=Decimal(amount))


def data(**kw):
    """One position by default; `title`, `description` and `amount` change it, `items` replaces it."""
    position = {k: kw.pop(k) for k in ("title", "description", "amount") if k in kw}
    base = dict(number="2026-001", issue_date=date(2026, 9, 16), service_date="16.09.2026",
                due_date=date(2026, 9, 30), payment_days=14, customer_name="Nordlicht Werkstatt GmbH",
                customer_street="Hafenstraße 7", customer_city="24103 Kiel",
                items=(item(**{k: str(v) for k, v in position.items()}),))
    base.update(kw)
    return InvoiceData(**base)


def test_format_amount():
    assert format_amount(Decimal("700")) == "700,00 €"
    assert format_amount(Decimal("1234567.5")) == "1.234.567,50 €"


def test_render_produces_deterministic_pdf():
    sender = load_sender(Path("config/sender.example.toml"))
    first = render_invoice(data(), sender)
    assert first.startswith(b"%PDF-")
    assert first == render_invoice(data(), sender)


def test_a_position_longer_than_a_whole_page_is_rejected():
    """Positions flow onto further pages, but a single one has to fit on a page of its own."""
    sender = load_sender(Path("config/sender.example.toml"))
    with pytest.raises(LayoutOverflowError):
        render_invoice(data(description="Sehr lange Beschreibung. " * 600), sender)


def test_a_long_description_continues_on_the_next_page():
    sender = load_sender(Path("config/sender.example.toml"))
    assert page_count(render_invoice(data(), sender)) == 1
    assert page_count(render_invoice(data(description="Sehr lange Beschreibung. " * 60), sender)) == 2


def pdf_text(data_: bytes) -> str:
    return subprocess.run(["pdftotext", "-", "-"], input=data_, capture_output=True, check=True).stdout.decode()


def test_small_business_note_uses_2025_wording():
    # § 34a Nr. 5 UStDV: the note must say that the exemption for Kleinunternehmer applies.
    assert pdf.SMALL_BUSINESS_NOTE == ("Für diese Leistung gilt die Steuerbefreiung für Kleinunternehmer "
                                       "(§ 19 UStG). Es wird keine Umsatzsteuer berechnet.")
    assert "Gemäß § 19 UStG" not in pdf.SMALL_BUSINESS_NOTE


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="pdftotext not installed")
def test_note_is_printed_in_full():
    sender = load_sender(Path("config/sender.example.toml"))
    text = " ".join(pdf_text(render_invoice(data(), sender)).split())
    assert pdf.SMALL_BUSINESS_NOTE in text


def test_note_fits_on_one_line_and_long_notes_wrap(monkeypatch):
    from reportlab.pdfgen import canvas
    import io

    pdf.register_fonts()
    c = canvas.Canvas(io.BytesIO())
    assert pdf.draw_note(c, pdf.SMALL_BUSINESS_NOTE, 400) == 0
    assert pdf.draw_note(c, pdf.SMALL_BUSINESS_NOTE + " " + pdf.SMALL_BUSINESS_NOTE, 400) == pdf.NOTE_LEADING


def test_a_wrapped_note_moves_the_closing_block_to_the_next_page(monkeypatch):
    """A longer note takes space from the closing block, which then moves on as a whole."""
    sender = load_sender(Path("config/sender.example.toml"))
    fits = None
    for lines in range(1, 40):
        if page_count(render_invoice(data(description="\n".join(["Zeile"] * lines)), sender)) == 1:
            fits = lines
        else:
            break
    assert fits
    monkeypatch.setattr(pdf, "SMALL_BUSINESS_NOTE", " ".join([pdf.SMALL_BUSINESS_NOTE] * 4))
    assert page_count(render_invoice(data(description="\n".join(["Zeile"] * fits)), sender)) == 2


def test_new_invoices_record_the_printed_note(env):
    from invoices import archive, db
    from invoices.config import load_settings
    from tests.conftest import invoice_form

    s = load_settings()
    conn = db.connect(s.db_path)
    db.init_db(conn)
    inp = archive.parse_invoice_form(invoice_form())
    inv_id = archive.issue_invoice(conn, s.archive_dir, inp, load_sender(s.sender_file), s.retention_years)
    payload = json.loads(conn.execute("SELECT payload_json FROM invoices WHERE id = ?", (inv_id,)).fetchone()[0])
    assert payload["texts"]["small_business_note"] == pdf.SMALL_BUSINESS_NOTE
    conn.close()
