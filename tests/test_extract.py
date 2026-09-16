import shutil
from datetime import date
from decimal import Decimal

import pytest

from invoices import extract
from tests.conftest import SAMPLE_EXPENSE, make_pdf

GERMAN = """
    Musterfirma GmbH & Co. KG    Hauptstraße 1    20095 Hamburg

    Rechnung Nr. 2026/0815                       Datum: 16. September 2026
    Leistungszeitraum: 01.08.2026 - 31.08.2026

    Pos  Menge  Beschreibung                    Einzelpreis      Gesamt
    1    2      Lizenz                             500,00       1.000,00
    Zwischensumme                                               1.000,00 €
    zzgl. 19% MwSt.                                               190,00 €
    Rechnungsbetrag                                             1.190,00 €
    Zahlbar bis 30.09.2026 ohne Abzug.
"""

ENGLISH = """
Acme Ltd
Invoice number: INV-2026-0042
Invoice date: 2026-08-31
Subtotal          $80.00
VAT (20%)         $16.00
Total due
                  96.00 EUR
"""


def test_german_invoice():
    s = extract.suggest(GERMAN)
    assert s.vendor == "Musterfirma GmbH & Co. KG"
    assert s.invoice_number == "2026/0815"
    assert s.expense_date == date(2026, 9, 16)
    assert s.amount == Decimal("1190.00")


def test_english_invoice_amount_on_next_line():
    s = extract.suggest(ENGLISH)
    assert s.vendor == "Acme Ltd"
    assert s.invoice_number == "INV-2026-0042"
    assert s.expense_date == date(2026, 8, 31)
    assert s.amount == Decimal("96.00")


SAAS = """
Invoice
Invoice number 0A1B2C3D-0001
Date of issue  September 16, 2026
Date due       September 30, 2026

Example Cloud Ireland, Limited                Bill to
6th Floor, Some House                         Max Mustermann
Dublin 4                                      24103 Kiel

Description                                          Qty   Unit price   Tax      Amount
Max plan                                               1       €90.00   19%     €90.00
Unused time on old plan after 1 Sep 2026               1                19%          -
                                                                               €16.43
                                  Subtotal                                         €73.57
                                  Total excluding tax                              €73.57
                                  VAT - Germany (19% on €73.57)                    €13.98
                                  Total                                            €87.55
                                  Amount due                                       €87.55
"""


def test_english_saas_invoice():
    s = extract.suggest(SAAS)
    assert s.vendor == "Example Cloud Ireland, Limited"
    assert s.invoice_number == "0A1B2C3D-0001"
    assert s.expense_date == date(2026, 9, 16)
    assert s.amount == Decimal("87.55")


def test_receipt_without_labels_uses_largest_currency_amount():
    s = extract.suggest("Bäckerei Kranz\n12.09.26 08:14\nBrötchen 2,40 EUR\nKaffee 3,10 EUR\nBar 10,00\n")
    assert s.amount == Decimal("3.10")
    assert s.expense_date == date(2026, 9, 12)
    assert s.vendor == "Bäckerei Kranz"


@pytest.mark.parametrize("line,expected", [
    ("Gesamt 1.234,56 €", ["1234.56"]),
    ("Total 1,234.56", ["1234.56"]),
    ("Datum 16.09.2026 Betrag 12,00", ["12.00"]),
    ("19,00 % MwSt 3,80", ["3.80"]),
    ("Kaputt 1.234.56", []),
])
def test_amount_parsing(line, expected):
    assert [str(a) for a in extract._amounts(line)] == expected


def test_nothing_found():
    assert extract.suggest("") == extract.Suggestion()
    assert extract.suggest("Hallo\nWelt").amount is None


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="pdftotext not installed")
def test_pdf_text_layer_roundtrip():
    text = extract.pdf_text(make_pdf(SAMPLE_EXPENSE))
    s = extract.suggest(text)
    assert s.vendor == "Hetzner Online GmbH"
    assert s.invoice_number == "R0024567891"
    assert s.expense_date == date(2026, 9, 3)
    assert s.amount == Decimal("11.90")
    assert extract.pdf_text(b"%PDF-1.4 broken") == ""


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="pdftotext not installed")
def test_pdf_text_output_is_capped(monkeypatch):
    monkeypatch.setattr(extract, "MAX_OUTPUT", 64)
    assert extract.pdf_text(make_pdf(SAMPLE_EXPENSE * 5)) == ""
