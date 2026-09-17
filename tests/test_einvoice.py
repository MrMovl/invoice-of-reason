"""Received e-invoices: XRechnung UBL/CII parsing, byte-exact archiving, ZUGFeRD attachments,
the readable view and the doc_type migration."""

import io
import json
import shutil
import sqlite3
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from invoices import archive, db, einvoice, expenses
from invoices.config import load_settings

FIXTURES = Path(__file__).parent / "fixtures"
UBL = (FIXTURES / "ubl-invoice.xml").read_bytes()
CII = (FIXTURES / "cii-invoice.xml").read_bytes()
CII_CREDIT = CII.replace(b"<ram:TypeCode>380</ram:TypeCode>", b"<ram:TypeCode>381</ram:TypeCode>") \
    .replace(b"R-4711", b"GS-4712")
UBL_CREDIT = b"""\xef\xbb\xbf
<CreditNote xmlns="urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
    xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:ID>GS-1</cbc:ID>
  <cbc:IssueDate>2026-09-12</cbc:IssueDate>
  <cbc:CreditNoteTypeCode>381</cbc:CreditNoteTypeCode>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty><cac:Party><cac:PartyName><cbc:Name>Muster Shop</cbc:Name></cac:PartyName></cac:Party></cac:AccountingSupplierParty>
  <cac:PaymentMeans><cbc:PaymentMeansCode>58</cbc:PaymentMeansCode><cbc:PaymentDueDate>2026-09-30</cbc:PaymentDueDate></cac:PaymentMeans>
  <cac:LegalMonetaryTotal><cbc:TaxInclusiveAmount currencyID="EUR">-11.90</cbc:TaxInclusiveAmount><cbc:PayableAmount currencyID="EUR">-11.90</cbc:PayableAmount></cac:LegalMonetaryTotal>
  <cac:CreditNoteLine><cbc:ID>1</cbc:ID><cbc:CreditedQuantity unitCode="C62">1</cbc:CreditedQuantity>
    <cbc:LineExtensionAmount currencyID="EUR">-10.00</cbc:LineExtensionAmount><cac:Item><cbc:Name>Retoure</cbc:Name></cac:Item></cac:CreditNoteLine>
</CreditNote>
"""


@pytest.fixture
def store(env):
    s = load_settings()
    conn = db.connect(s.db_path)
    db.init_db(conn)
    yield s, conn
    conn.close()


def upload(s, conn, data, name):
    return expenses.store_upload(conn, s.expenses_dir, data, name, s.retention_years, today=date(2026, 9, 16))


# ── Parsing ───────────────────────────────────────────────────────────────


def test_parse_ubl_invoice():
    inv = einvoice.parse(UBL)
    assert (inv.syntax, inv.type_code, inv.credit_note) == ("UBL", "380", False)
    assert (inv.number, inv.issue_date, inv.due_date) == ("RE-2026-0815", date(2026, 9, 3), date(2026, 9, 17))
    assert (inv.seller, inv.buyer, inv.currency) == ("Muster Hosting GmbH", "Erika Beispiel", "EUR")
    assert [(l.name, l.quantity, l.unit_label, l.net) for l in inv.lines] == [
        ("Cloud Server <M> & Backup", Decimal("2"), "Monate", Decimal("15.00")),
        ("Domain muster.example", Decimal("1.5"), "Stk.", Decimal("5.00")),
    ]
    assert (inv.net_total, inv.tax_total, inv.gross_total, inv.payable) == \
        (Decimal("20.00"), Decimal("3.80"), Decimal("23.80"), Decimal("23.80"))
    assert inv.suggestion().as_dict() == {"vendor": "Muster Hosting GmbH", "invoice_number": "RE-2026-0815",
                                          "expense_date": "2026-09-03", "amount": "23.80"}
    text = inv.as_text()
    assert "Rechnungssteller: Muster Hosting GmbH" in text and "Zahlbetrag: 23,80 EUR" in text
    assert "1,5 Stk.  Domain muster.example  5,00 EUR" in text and "<cbc:" not in text


def test_parse_cii_invoice_with_date_format_102():
    inv = einvoice.parse(CII)
    assert (inv.syntax, inv.credit_note, inv.number) == ("CII", False, "R-4711")
    assert (inv.issue_date, inv.due_date) == (date(2026, 9, 11), date(2026, 9, 25))
    assert (inv.seller, inv.buyer) == ("Muster Bürobedarf GmbH", "Erika Beispiel")
    assert [(l.name, l.quantity, l.unit_label, l.net) for l in inv.lines] == \
        [("Druckerpapier A4", Decimal("10"), "Stk.", Decimal("42.00"))]
    assert (inv.tax_total, inv.payable, inv.amount) == (Decimal("7.98"), Decimal("49.98"), Decimal("49.98"))

    no_due = CII.replace(b"<ram:DuePayableAmount>49.98</ram:DuePayableAmount>", b"")
    assert einvoice.parse(no_due).amount == Decimal("49.98")  # falls back to GrandTotalAmount
    other_format = CII.replace(b'format="102">20260911', b'format="610">202609')
    assert einvoice.parse(other_format).issue_date is None


def test_credit_notes_are_marked_and_suggest_positive_amounts():
    cii = einvoice.parse(CII_CREDIT)
    assert cii.credit_note and cii.type_code == "381" and cii.amount == Decimal("49.98")
    assert cii.as_text().startswith("Gutschrift")
    ubl = einvoice.parse(UBL_CREDIT)  # with BOM and leading whitespace
    assert (ubl.credit_note, ubl.number, ubl.seller, ubl.due_date) == (True, "GS-1", "Muster Shop", date(2026, 9, 30))
    assert ubl.amount == Decimal("11.90") and ubl.lines[0].net == Decimal("-10.00")


@pytest.mark.parametrize("data, message", [
    (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>', "DOCTYPE"),
    (UBL.replace(b"<ubl:Invoice", b'<!DOCTYPE ubl:Invoice SYSTEM "file:///etc/passwd">\n<ubl:Invoice', 1), "DOCTYPE"),
    (b'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><script/></html>', "keine E-Rechnung"),
    (b"<Invoice><ID>1</ID></Invoice>", "keine E-Rechnung"),  # right name, no namespace
    (b"<?xml version='1.0'?><unclosed>", "nicht wohlgeformt"),
])
def test_rejects_unsafe_and_foreign_xml(data, message):
    with pytest.raises(einvoice.EInvoiceError, match=message):
        einvoice.parse(data)


def test_size_is_capped(monkeypatch):
    monkeypatch.setattr(einvoice, "MAX_SIZE", 100)
    with pytest.raises(einvoice.EInvoiceError, match="zu groß"):
        einvoice.parse(UBL)


# ── Upload ────────────────────────────────────────────────────────────────


def test_xml_upload_is_stored_byte_for_byte(store):
    s, conn = store
    exp_id = upload(s, conn, UBL, "rechnung.xml")
    row = conn.execute("SELECT * FROM expenses WHERE id = ?", (exp_id,)).fetchone()
    assert row["doc_type"] == "xml" and row["doc_path"].endswith(".xml")
    path = s.expenses_dir / row["doc_path"]
    assert path.read_bytes() == UBL
    assert archive.sha256_file(path) == row["doc_sha256"] == archive.sha256_bytes(UBL)
    assert (row["vendor"], row["invoice_number"], row["expense_date"], row["amount_cents"]) == \
        ("Muster Hosting GmbH", "RE-2026-0815", "2026-09-03", 2380)
    suggestion = json.loads(row["suggestion_json"])
    assert (suggestion["source"], suggestion["credit_note"], suggestion["currency"]) == ("xml", False, "EUR")
    assert "Cloud Server <M> & Backup" in row["doc_text"] and "<?xml" not in row["doc_text"]
    assert expenses.mimetype("xml") == "application/xml"
    assert expenses.verify_all(conn, s.expenses_dir) == []

    # Detection is by content: a UBL file named .pdf is still XML, a PDF named .xml still a PDF.
    exp2 = upload(s, conn, UBL_CREDIT, "gutschrift.pdf")
    assert conn.execute("SELECT doc_type FROM expenses WHERE id = ?", (exp2,)).fetchone()[0] == "xml"
    with pytest.raises(archive.ArchiveError, match="keine E-Rechnung"):
        upload(s, conn, b"<?xml version='1.0'?><note>hi</note>", "note.xml")


def test_foreign_currency_is_not_prefilled(store):
    s, conn = store
    usd = UBL.replace(b">EUR<", b">USD<").replace(b'currencyID="EUR"', b'currencyID="USD"')
    row = conn.execute("SELECT * FROM expenses WHERE id = ?", (upload(s, conn, usd, "usd.xml"),)).fetchone()
    assert row["amount_cents"] is None and row["vendor"] == "Muster Hosting GmbH"
    assert json.loads(row["suggestion_json"])["currency"] == "USD"


# ── ZUGFeRD / Factur-X ────────────────────────────────────────────────────


def pdf_with_attachment(name: str, payload: bytes) -> bytes:
    """A PDF with an embedded file, built with reportlab's low-level objects (no public API)."""
    from reportlab.pdfbase import pdfdoc
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(50, 800, "Muster Bürobedarf GmbH")
    c.drawString(50, 780, "Rechnungsnummer: FALSCH-1")
    c.drawString(50, 760, "Gesamtbetrag 999,00 €")
    doc = c._doc
    stream = doc.Reference(pdfdoc.PDFStream(
        pdfdoc.PDFDictionary({"Type": pdfdoc.PDFName("EmbeddedFile")}), content=payload))
    spec = doc.Reference(pdfdoc.PDFDictionary({
        "Type": pdfdoc.PDFName("Filespec"), "F": pdfdoc.PDFString(name), "UF": pdfdoc.PDFString(name),
        "AFRelationship": pdfdoc.PDFName("Data"), "EF": pdfdoc.PDFDictionary({"F": stream})}))
    doc.Catalog.Names = pdfdoc.PDFDictionary({"EmbeddedFiles": pdfdoc.PDFDictionary(
        {"Names": pdfdoc.PDFArray([pdfdoc.PDFString(name), spec])})})
    c.save()
    return buf.getvalue()


@pytest.mark.skipif(not shutil.which("pdfdetach"), reason="pdfdetach (poppler-utils) not installed")
def test_zugferd_pdf_prefers_embedded_xml(store):
    s, conn = store
    pdf = pdf_with_attachment("factur-x.xml", CII)
    assert einvoice.embedded_xml(pdf) == CII
    exp_id = upload(s, conn, pdf, "zugferd.pdf")
    row = conn.execute("SELECT * FROM expenses WHERE id = ?", (exp_id,)).fetchone()
    assert row["doc_type"] == "pdf" and (s.expenses_dir / row["doc_path"]).read_bytes() == pdf
    assert (row["invoice_number"], row["amount_cents"], row["expense_date"]) == ("R-4711", 4998, "2026-09-11")
    assert json.loads(row["suggestion_json"])["source"] == "zugferd"

    other = pdf_with_attachment("anhang.xml", CII)
    assert einvoice.embedded_xml(other) is None
    assert einvoice.from_pdf(pdf_with_attachment("xrechnung.xml", b"<nope/>")) is None


def test_pdfdetach_wrapper(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if "-list" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=b"2 embedded files\n1: logo.png\n2: ZUGFeRD-invoice.xml\n")
        Path(cmd[cmd.index("-o") + 1]).write_bytes(CII)
        return subprocess.CompletedProcess(cmd, 0, stdout=None)

    monkeypatch.setattr(einvoice.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "pdfdetach" else None)
    monkeypatch.setattr(einvoice.subprocess, "run", fake_run)
    assert einvoice.embedded_xml(b"%PDF-1.7") == CII
    assert calls[1][:3] == ["/usr/bin/pdfdetach", "-save", "2"]

    monkeypatch.setattr(einvoice.subprocess, "run", lambda cmd, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd, 1)))
    assert einvoice.embedded_xml(b"%PDF-1.7") is None
    monkeypatch.setattr(einvoice.shutil, "which", lambda name: None)
    assert einvoice.embedded_xml(b"%PDF-1.7") is None


# ── Readable view and safe serving ────────────────────────────────────────


def test_readable_view_and_plain_text_serving(logged_in, csrf):
    c = logged_in
    resp = c.post("/expenses/upload", content_type="multipart/form-data", data={
        "csrf_token": csrf, "files": [(io.BytesIO(UBL), "rechnung.xml")]})
    detail_url = resp.headers["Location"]
    html = c.get(detail_url).get_data(as_text=True)
    assert "E-Rechnung UBL" in html and "Erika Beispiel" in html and "17.09.2026" in html
    assert "Cloud Server &lt;M&gt; &amp; Backup" in html and "<M>" not in html
    assert "23,80 €" in html and "2 Monate" in html
    assert "E-Rechnung (XML)" in html and 'value="23,80"' in html
    assert "doc-text-dialog" not in html and "XML ansehen" in html

    inline = c.get(detail_url + "/file?inline=1")
    assert inline.data == UBL
    assert inline.headers["Content-Type"] == "text/plain; charset=utf-8"
    assert inline.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in inline.headers["Content-Security-Policy"]
    download = c.get(detail_url + "/file")
    assert download.data == UBL and download.mimetype == "application/xml"
    assert download.headers["Content-Disposition"].startswith("attachment")

    resp = c.post("/expenses/upload", content_type="multipart/form-data", data={
        "csrf_token": csrf, "files": [(io.BytesIO(CII_CREDIT), "gutschrift.xml")]})
    html = c.get(resp.headers["Location"]).get_data(as_text=True)
    assert "E-Rechnung CII" in html and 'badge-overdue">Gutschrift' in html
    assert "Gutschrift: Der Betrag ist als positiver Wert vorgeschlagen" in html

    listing = c.get("/expenses").get_data(as_text=True)
    assert "XRechnung" in listing and ".xml" in listing


# ── Migration: doc_type 'xml' ─────────────────────────────────────────────


def test_doc_type_migration_keeps_rows_triggers_and_foreign_keys(env, tmp_path, monkeypatch):
    number = next(i for i, (desc, _sql) in enumerate(db.MIGRATIONS, start=1) if "'xml'" in desc)
    path = tmp_path / "old.sqlite3"
    old = db.connect(path)
    with monkeypatch.context() as m:
        m.setattr(db, "MIGRATIONS", db.MIGRATIONS[:number - 1])
        db.init_db(old)
    assert db.schema_version(old) == number - 1
    for i in (1, 2):
        old.execute("""INSERT INTO expenses (id, vendor, amount_cents, reviewed, doc_path, doc_sha256,
                       doc_size, doc_type, suggestion_json, retain_until, created_at, updated_at)
                       VALUES (?, 'Muster GmbH', 1190, 1, ?, ?, 10, 'pdf', '{}', '2036-12-31', 'now', 'now')""",
                    (i * 7, f"2026/{i}.pdf", f"sha{i}"))
        db.add_expense_event(old, i * 7, "uploaded", f"sha256=sha{i}")
    old.commit()
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        old.execute("""INSERT INTO expenses (doc_path, doc_sha256, doc_size, doc_type, suggestion_json,
                       retain_until, created_at, updated_at) VALUES ('x', 'x', 1, 'xml', '{}', 'x', 'x', 'x')""")
    old.rollback()
    before = old.execute("SELECT * FROM expenses ORDER BY id").fetchall()
    old.close()

    conn = db.connect(path)
    db.init_db(conn)
    assert db.schema_version(conn) == len(db.MIGRATIONS)
    # Later migrations may add columns; the columns that existed before must be unchanged.
    columns = ", ".join(before[0].keys())
    assert [tuple(r) for r in conn.execute(f"SELECT {columns} FROM expenses ORDER BY id")] == \
        [tuple(r) for r in before]
    assert conn.execute("SELECT COUNT(*) FROM expense_events").fetchone()[0] == 2
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE tbl_name = 'expenses'")}
    assert {"expenses_no_delete", "expenses_immutable", "idx_expenses_expense_date"} <= names
    assert "expenses_new" not in {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert f"{number}: expenses: allow XML" in conn.execute(
        "SELECT group_concat(detail) FROM system_events WHERE action = 'schema_migration'").fetchone()[0]

    # Protective triggers still abort.
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        conn.execute("UPDATE expenses SET doc_sha256 = 'x' WHERE id = 7")
    with pytest.raises(sqlite3.DatabaseError, match="cannot be deleted"):
        conn.execute("DELETE FROM expenses WHERE id = 7")
    conn.rollback()

    # Foreign keys are enforced again and point at the rebuilt table.
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert [r["table"] for r in conn.execute("PRAGMA foreign_key_list(expense_events)")] == ["expenses"]
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        conn.execute("INSERT INTO expense_events (expense_id, at, action) VALUES (999, 'now', 'x')")
    conn.rollback()
    db.add_expense_event(conn, 14, "updated")
    conn.execute("""INSERT INTO expenses (doc_path, doc_sha256, doc_size, doc_type, suggestion_json,
                    retain_until, created_at, updated_at) VALUES ('x.xml', 'x', 1, 'xml', '{}', 'x', 'x', 'x')""")
    conn.commit()
    conn.close()


def test_migration_refuses_broken_foreign_keys(env, tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "fk.sqlite3")
    db.init_db(conn)
    version = db.schema_version(conn)
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, (
        "orphan event", "INSERT INTO expense_events (expense_id, at, action) VALUES (404, 'now', 'x');")])
    with pytest.raises(sqlite3.IntegrityError, match="foreign keys"):
        db.migrate(conn)
    assert db.schema_version(conn) == version
    assert conn.execute("SELECT COUNT(*) FROM expense_events").fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()
