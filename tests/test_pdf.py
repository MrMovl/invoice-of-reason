import json
import shutil
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from invoices import pdf
from invoices.config import load_sender
from invoices.pdf import InvoiceData, LayoutOverflowError, format_amount, render_invoice


def data(**kw):
    base = dict(number="2026-001", issue_date=date(2026, 9, 16), service_date="16.09.2026",
                due_date=date(2026, 9, 30), payment_days=14, customer_name="Nordlicht Werkstatt GmbH",
                customer_street="Hafenstraße 7", customer_city="24103 Kiel",
                title="Beratung & Entwicklung <b>", description="Zeile 1\nZeile 2", amount=Decimal("700.00"))
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


def test_overlong_text_is_rejected():
    sender = load_sender(Path("config/sender.example.toml"))
    with pytest.raises(LayoutOverflowError):
        render_invoice(data(description="Sehr lange Beschreibung. " * 60), sender)


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


def test_wrapped_note_moves_overflow_limit(monkeypatch):
    """A longer note takes space from the text above the footer, and the check still catches it."""
    sender = load_sender(Path("config/sender.example.toml"))
    fits = None
    for lines in range(1, 40):
        try:
            render_invoice(data(description="\n".join(["Zeile"] * lines)), sender)
            fits = lines
        except LayoutOverflowError:
            break
    assert fits
    monkeypatch.setattr(pdf, "SMALL_BUSINESS_NOTE", " ".join([pdf.SMALL_BUSINESS_NOTE] * 4))
    with pytest.raises(LayoutOverflowError):
        render_invoice(data(description="\n".join(["Zeile"] * fits)), sender)


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
