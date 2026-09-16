from datetime import date
from decimal import Decimal

import pytest

from invoices.config import load_sender
from invoices.pdf import InvoiceData, LayoutOverflowError, format_amount, render_invoice
from pathlib import Path


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
