"""Invoices with more than one position (Rechnungspositionen)."""

import json
import shutil
import subprocess

import pytest

from invoices import archive, db
from invoices.config import load_sender, load_settings
from tests.conftest import csrf_from, invoice_form, page_count


def two_positions(**overrides):
    return invoice_form(
        item_title=["Beratung", "Hosting <Server>"],
        item_description=["Konzeption und Umsetzung.", ""],
        item_amount=["700,00", "120,50"],
        **overrides,
    )


def issued(conn, s, form):
    inp = archive.parse_invoice_form(form)
    return archive.issue_invoice(conn, s.archive_dir, inp, load_sender(s.sender_file), s.retention_years)


@pytest.fixture
def conn_and_settings(env):
    s = load_settings()
    conn = db.connect(s.db_path)
    db.init_db(conn)
    yield conn, s
    conn.close()


# ── Parsing ───────────────────────────────────────────────────────────────


def test_positions_are_parsed_in_order_and_summed():
    inp = archive.parse_invoice_form(two_positions())
    assert [i.title for i in inp.items] == ["Beratung", "Hosting <Server>"]
    assert [str(i.amount) for i in inp.items] == ["700.00", "120.50"]
    assert str(inp.amount) == "820.50"


def test_a_single_position_still_works():
    inp = archive.parse_invoice_form(invoice_form())
    assert len(inp.items) == 1
    assert inp.items[0].title == "Entwicklung & Beratung <intern>"
    assert str(inp.amount) == "700.00"


def test_an_empty_added_row_is_ignored():
    form = invoice_form(item_title=["Beratung", ""], item_description=["", ""],
                        item_amount=["700,00", ""])
    assert len(archive.parse_invoice_form(form).items) == 1


def test_a_half_filled_row_names_its_position():
    form = invoice_form(item_title=["Beratung", "Hosting"], item_description=["", ""],
                        item_amount=["700,00", ""])
    with pytest.raises(archive.ArchiveError, match="Position 2: Betrag fehlt."):
        archive.parse_invoice_form(form)


def test_a_missing_title_names_its_position():
    form = invoice_form(item_title=["Beratung", ""], item_description=["", ""],
                        item_amount=["700,00", "120,00"])
    with pytest.raises(archive.ArchiveError, match="Position 2: Leistungstitel fehlt."):
        archive.parse_invoice_form(form)


def test_a_single_position_keeps_the_plain_messages():
    with pytest.raises(archive.ArchiveError, match="^Betrag fehlt.$"):
        archive.parse_invoice_form(invoice_form(amount=""))


def test_no_position_at_all_is_rejected():
    form = invoice_form(item_title="", item_description="", item_amount="")
    with pytest.raises(archive.ArchiveError, match="mindestens eine Position"):
        archive.parse_invoice_form(form)


def test_too_many_positions_are_rejected():
    count = archive.MAX_ITEMS + 1
    form = invoice_form(item_title=["Beratung"] * count, item_description=[""] * count,
                        item_amount=["10,00"] * count)
    with pytest.raises(archive.ArchiveError, match=f"Mehr als {archive.MAX_ITEMS} Positionen"):
        archive.parse_invoice_form(form)


def test_a_description_keeps_its_line_breaks_in_a_later_position():
    form = invoice_form(item_title=["Beratung", "Hosting"],
                        item_description=["", "Zeile 1\r\nZeile 2"],
                        item_amount=["700,00", "120,00"])
    assert archive.parse_invoice_form(form).items[1].description == "Zeile 1\nZeile 2"


# ── Archived record ───────────────────────────────────────────────────────


def test_the_record_holds_the_total_and_all_positions(conn_and_settings):
    conn, s = conn_and_settings
    invoice_id = issued(conn, s, two_positions())
    row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    assert row["amount_cents"] == 82050
    assert row["title"] == "Beratung; Hosting <Server>"
    assert row["description"].splitlines() == [
        "1. Beratung – 700,00 €",
        "   Konzeption und Umsetzung.",
        "2. Hosting <Server> – 120,50 €",
    ]
    items = json.loads(row["payload_json"])["invoice"]["items"]
    assert items == [
        {"title": "Beratung", "description": "Konzeption und Umsetzung.", "amount": "700.00"},
        {"title": "Hosting <Server>", "description": "", "amount": "120.50"},
    ]
    assert json.loads(row["payload_json"])["invoice"]["amount"] == "820.50"


def test_a_single_position_records_title_and_description_unchanged(conn_and_settings):
    conn, s = conn_and_settings
    invoice_id = issued(conn, s, invoice_form())
    row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    assert row["title"] == "Entwicklung & Beratung <intern>"
    assert row["description"] == "Konzeption und Umsetzung."


def test_a_long_position_list_keeps_the_title_column_short(conn_and_settings):
    conn, s = conn_and_settings
    count = 4
    invoice_id = issued(conn, s, invoice_form(
        item_title=[f"Sehr ausführlich und genau benannte Leistung Nummer {n}" for n in range(count)],
        item_description=[""] * count, item_amount=["10,00"] * count))
    row = conn.execute("SELECT title FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    assert len(row["title"]) <= 200 and row["title"].endswith("…")


def test_cancelling_a_multi_position_invoice_refunds_the_total(conn_and_settings):
    conn, s = conn_and_settings
    invoice_id = issued(conn, s, two_positions())
    doc_id = archive.cancel_invoice(conn, s.archive_dir, invoice_id, "Leistung entfällt",
                                    load_sender(s.sender_file), s.retention_years)
    doc = conn.execute("SELECT * FROM invoices WHERE id = ?", (doc_id,)).fetchone()
    assert doc["amount_cents"] == -82050
    assert doc["title"] == "Storno: Beratung; Hosting <Server>"
    items = json.loads(doc["payload_json"])["invoice"]["items"]
    assert len(items) == 1 and items[0]["amount"] == "-820.50"


# ── PDF ───────────────────────────────────────────────────────────────────


def pdf_text(data: bytes) -> str:
    return subprocess.run(["pdftotext", "-", "-"], input=data, capture_output=True,
                          check=True).stdout.decode()


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="pdftotext not installed")
def test_the_pdf_prints_every_position_and_the_total(env):
    from invoices.pdf import render_invoice

    inp = archive.parse_invoice_form(two_positions())
    text = " ".join(pdf_text(render_invoice(inp.to_invoice_data(),
                                            load_sender(load_settings().sender_file))).split())
    assert "Beratung" in text and "Hosting <Server>" in text
    assert "700,00 €" in text and "120,50 €" in text
    assert "Gesamtbetrag 820,50 €" in text


def rendered(form):
    from invoices.pdf import render_invoice

    inp = archive.parse_invoice_form(form)
    return render_invoice(inp.to_invoice_data(), load_sender(load_settings().sender_file))


def many_positions(count):
    return invoice_form(item_title=[f"Leistung Nummer {n + 1}" for n in range(count)],
                        item_description=["Eine Beschreibung dazu."] * count,
                        item_amount=["10,00"] * count)


def test_a_short_list_of_positions_stays_on_one_page(env):
    assert page_count(rendered(many_positions(3))) == 1
    without_descriptions = {**many_positions(4), "item_description": [""] * 4}
    assert page_count(rendered(without_descriptions)) == 1


def test_more_positions_continue_on_the_next_page(env):
    assert page_count(rendered(many_positions(12))) == 2
    assert page_count(rendered(many_positions(archive.MAX_ITEMS))) == 3


def test_a_position_too_long_for_the_rest_of_page_one_moves_over(env):
    """It only has to fit on a page of its own, not on what is left of the first one."""
    long_description = "Eine ausführliche Beschreibung der Leistung. " * 22
    form = invoice_form(item_title=["Beratung", "Hosting"], item_description=["", long_description],
                        item_amount=["700,00", "120,00"])
    assert page_count(rendered(form)) == 2


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="pdftotext not installed")
def test_a_continued_page_names_the_invoice_and_repeats_the_table_header(env):
    pages = pdf_text(rendered(many_positions(12))).split("\f")
    assert "Seite 2" in pages[1] and "Rechnung 2026-001" in pages[1]
    assert pages[0].count("Beschreibung") and "Beschreibung" in pages[1]
    text = " ".join(" ".join(pages).split())
    for number in range(1, 13):
        assert f"Leistung Nummer {number}" in text
    assert "Gesamtbetrag 120,00 €" in text
    # The closing block stays together on the last page.
    last = " ".join(pages[-2].split())  # pdftotext ends with an empty page after the last \f
    assert "Gesamtbetrag" in last and "Bankverbindung" in last and "§ 19 UStG" in last


# ── Web ───────────────────────────────────────────────────────────────────


def test_the_form_offers_one_position_and_a_template_for_more(logged_in):
    html = logged_in.get("/invoices/new").get_data(as_text=True)
    assert html.count('name="item_title"') == 2  # the one row plus the <template>
    assert 'id="item-add"' in html


def test_creating_and_showing_an_invoice_with_two_positions(logged_in, csrf):
    resp = logged_in.post("/invoices", data={**two_positions(), "csrf_token": csrf})
    assert resp.status_code == 302
    detail = logged_in.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Beratung" in detail and "Hosting &lt;Server&gt;" in detail
    assert "700,00 €" in detail and "120,50 €" in detail and "820,50 €" in detail
    assert "Leistungen" in detail
    assert "820,50 €" in logged_in.get("/invoices").get_data(as_text=True)


def test_a_rejected_form_comes_back_with_every_position(logged_in, csrf):
    form = two_positions(number="nicht gültig!")
    resp = logged_in.post("/invoices", data={**form, "csrf_token": csrf})
    assert resp.status_code == 400
    html = resp.get_data(as_text=True)
    assert html.count('name="item_title"') == 3  # two filled rows plus the <template>
    assert 'value="Beratung"' in html and "Hosting &lt;Server&gt;" in html
    assert 'value="120,50"' in html


def test_the_preview_renders_all_positions(logged_in, csrf):
    resp = logged_in.post("/invoices/preview", data={**two_positions(), "csrf_token": csrf})
    assert resp.status_code == 200 and resp.data.startswith(b"%PDF-")


def test_a_template_copies_all_positions_without_their_amounts(logged_in, csrf):
    resp = logged_in.post("/invoices", data={**two_positions(), "csrf_token": csrf})
    invoice_id = resp.headers["Location"].rstrip("/").split("/")[-1]
    html = logged_in.get(f"/invoices/new?from={invoice_id}").get_data(as_text=True)
    assert html.count('name="item_title"') == 3
    assert 'value="Beratung"' in html and "Hosting &lt;Server&gt;" in html
    assert 'value="700,00"' not in html
    assert csrf_from(html)
