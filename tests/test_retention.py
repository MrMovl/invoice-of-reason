"""Retention is a minimum: shown and exported as such, never used to delete anything."""

from datetime import date

from invoices import archive, export
from tests.conftest import invoice_form


def test_retention_starts_at_year_end_with_conservative_default(env):
    from invoices.config import load_settings

    assert load_settings().retention_years == 10
    assert archive.retain_until(date(2026, 1, 2), 10) == date(2036, 12, 31)


def test_detail_page_labels_retention_as_minimum(logged_in, csrf):
    resp = logged_in.post("/invoices", data={**invoice_form(), "csrf_token": csrf})
    html = logged_in.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Aufbewahren mindestens bis" in html


def test_export_describes_retention_as_minimum():
    assert "Mindestfrist" in export.DESCRIPTIONS["retain_until"]
    assert "§ 147 Abs. 3 AO" in export.DESCRIPTIONS["retain_until"]
