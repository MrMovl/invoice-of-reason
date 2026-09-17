"""Datenüberlassung (GoBD Rz. 128, 167): CSV + index.xml export."""

import csv
import io
import re
import xml.etree.ElementTree as ET
import zipfile

import pytest

from invoices import archive, cli, db, expenses, export
from invoices.config import load_sender, load_settings
from tests.conftest import invoice_form

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def store(env):
    s = load_settings()
    conn = db.connect(s.db_path)
    db.init_db(conn)
    yield s, conn
    conn.close()


def issue(s, conn, **kw):
    inp = archive.parse_invoice_form(invoice_form(**kw))
    return archive.issue_invoice(conn, s.archive_dir, inp, load_sender(s.sender_file), s.retention_years)


def upload(s, conn, data, name, **values):
    exp_id = expenses.store_upload(conn, s.expenses_dir, data, name, s.retention_years)
    form = {"vendor": "Bauhaus", "amount": "49,99", "expense_date": "2026-09-10", "status": "paid",
            "category": "Werkzeug", "paid_date": "", "invoice_number": "", "notes": ""}
    expenses.update_expense(conn, exp_id, expenses.parse_expense_form({**form, **values}))
    return exp_id


def read_csv(zf, name):
    raw = zf.read(f"{name}.csv")
    return raw, list(csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""), delimiter=";"))


def runs(conn):
    return [(r["kind"], r["ok"]) for r in conn.execute("SELECT kind, ok FROM control_runs ORDER BY id")]


def test_csv_round_trip(store):
    s, conn = store
    inv_id = issue(s, conn, title='Beratung "Hafen"; Teil 1', description="Zeile 1\r\nZeile 2", amount="1.234,50")
    archive.set_notes(conn, inv_id, "Notiz mit ; und \"Zitat\"")
    path = export.create_export(s, None)
    assert export.EXPORT_RE.match(path.name) and "-alle-" in path.name
    assert path.stat().st_mode & 0o777 == 0o440
    with zipfile.ZipFile(path) as zf:
        raw, rows = read_csv(zf, "invoices")
        assert raw.startswith(b"id;") and raw.endswith(b"\r\n")
        assert raw.count(b"\r\n") == 2  # header and one record; the text's line break is LF only
        (row,) = rows
        assert row["title"] == 'Beratung "Hafen"; Teil 1'
        assert row["description"] == "Zeile 1\nZeile 2"
        assert row["notes"] == 'Notiz mit ; und "Zitat"'
        assert row["amount_cents"] == "123450" and row["amount_eur"] == "1234,50"
        assert row["issue_date"] == "2026-09-16"
        assert '"sender"' in row["payload_json"]
        _raw, events = read_csv(zf, "events")
        assert [e["action"] for e in events] == ["created", "notes"]
        assert {"README.txt", "index.xml", "system_events.csv", "control_runs.csv"} <= set(zf.namelist())
    assert runs(conn) == [("export", 1)]


def test_year_filter_includes_matching_events_only(store):
    s, conn = store
    old = issue(s, conn, number="2025-001", issue_date="2025-12-30", service_from="2025-12-30")
    new = issue(s, conn, number="2026-001")
    archive.set_notes(conn, old, "alt")
    archive.set_notes(conn, new, "neu")
    upload(s, conn, PNG, "a.png", expense_date="2025-12-28", paid_date="2026-01-03")  # booked 2026
    upload(s, conn, PNG + b"x", "b.png", expense_date="2025-06-01")

    with zipfile.ZipFile(export.create_export(s, "2026")) as zf:
        assert [r["number"] for r in read_csv(zf, "invoices")[1]] == ["2026-001"]
        assert {r["invoice_id"] for r in read_csv(zf, "events")[1]} == {str(new)}
        assert [r["original_filename"] for r in read_csv(zf, "expenses")[1]] == ["a.png"]
        assert {r["expense_id"] for r in read_csv(zf, "expense_events")[1]} == {"1"}
        assert read_csv(zf, "system_events")[1]  # never filtered
        docs = [n for n in zf.namelist() if n.startswith(("archive/", "expenses/"))]
        assert len(docs) == 2 and any("2026-001" in n for n in docs)
    with zipfile.ZipFile(export.create_export(s, "2025")) as zf:
        assert [r["number"] for r in read_csv(zf, "invoices")[1]] == ["2025-001"]
        assert [r["original_filename"] for r in read_csv(zf, "expenses")[1]] == ["b.png"]
    assert export.export_years(conn) == ["2026", "2025"]
    with pytest.raises(export.ExportError, match="vierstellig"):
        export.create_export(s, "26")


def test_columns_come_from_the_schema(store):
    s, conn = store
    issue(s, conn)
    conn.execute("ALTER TABLE invoices ADD COLUMN payment_method TEXT NOT NULL DEFAULT 'Überweisung'")
    conn.commit()
    with zipfile.ZipFile(export.create_export(s)) as zf:
        raw, rows = read_csv(zf, "invoices")
        assert rows[0]["payment_method"] == "Überweisung"
        index = ET.fromstring(zf.read("index.xml"))
    table = next(t for t in index.iter("Table") if t.findtext("Name") == "invoices")
    assert "payment_method" in [c.findtext("Name") for c in table.iter("VariableColumn")]


def test_index_xml_describes_every_csv_in_order(store):
    s, conn = store
    issue(s, conn)
    with zipfile.ZipFile(export.create_export(s)) as zf:
        raw_index = zf.read("index.xml")
        index = ET.fromstring(raw_index)
        csvs = sorted(n for n in zf.namelist() if n.endswith(".csv"))
        assert b'<!DOCTYPE DataSet SYSTEM "gdpdu-01-09-2004.dtd">' in raw_index
        assert index.tag == "DataSet" and index.findtext("Version") == "1.0"
        assert index.find("DataSupplier").findtext("Name") == "Erika Mustermann"
        tables = index.find("Media").findall("Table")
        assert sorted(t.findtext("URL") for t in tables) == csvs
        for t in tables:
            assert t.find("UTF8") is not None and t.findtext("DecimalSymbol") == ","
            layout = t.find("VariableLength")
            assert layout.findtext("ColumnDelimiter") == ";"
            assert layout.findtext("RecordDelimiter") == "\r\n"
            assert layout.findtext("TextEncapsulator") == '"'
            columns = [c.findtext("Name") for c in layout if c.tag in ("VariablePrimaryKey", "VariableColumn")]
            header = zf.read(t.findtext("URL")).split(b"\r\n", 1)[0].decode()
            assert columns == header.split(";")
            assert layout[3].tag == "VariablePrimaryKey" and layout[3].findtext("Name") == "id"
        invoices = next(t for t in tables if t.findtext("Name") == "invoices")
        cols = {c.findtext("Name"): c for c in invoices.iter("VariableColumn")}
        assert cols["issue_date"].find("Date").findtext("Format") == "YYYY-MM-DD"
        assert cols["amount_eur"].find("Numeric").findtext("Accuracy") == "2"
        assert cols["amount_cents"].find("Numeric") is not None
        assert cols["title"].find("AlphaNumeric") is not None
        assert cols["number"].findtext("Description") == "Rechnungsnummer"
        events = next(t for t in tables if t.findtext("Name") == "events")
        fk = events.find("VariableLength/ForeignKey")
        assert (fk.findtext("Name"), fk.findtext("References")) == ("invoice_id", "invoices")
        readme = zf.read("README.txt").decode("utf-8")
        assert "amount_cents" in readme and "\r\n" in readme


def test_documents_are_included_and_match_hashes(store):
    s, conn = store
    issue(s, conn)
    upload(s, conn, PNG, "quittung.png")
    with zipfile.ZipFile(export.create_export(s)) as zf:
        inv = read_csv(zf, "invoices")[1][0]
        exp = read_csv(zf, "expenses")[1][0]
        assert archive.sha256_bytes(zf.read(f"archive/{inv['pdf_path']}")) == inv["pdf_sha256"]
        assert archive.sha256_bytes(zf.read(f"expenses/{exp['doc_path']}")) == exp["doc_sha256"]


def test_tampered_document_aborts_export(store):
    s, conn = store
    issue(s, conn)
    pdf = next(s.archive_dir.rglob("*.pdf"))
    pdf.chmod(0o644)
    pdf.write_bytes(b"tampered")
    with pytest.raises(export.ExportError, match="Prüfsumme"):
        export.create_export(s, "2026")
    assert runs(conn) == [("export", 0)]
    assert export.list_exports(s) == []
    assert not any((s.backup_dir / "exports").iterdir())


def test_cli_export(store, capsys):
    s, conn = store
    issue(s, conn)
    assert cli.main(["export", "--year", "2026"]) == 0
    assert "gobd-export-2026-" in capsys.readouterr().out
    assert cli.main(["export", "--year", "x"]) == 1


def test_web_export(logged_in, csrf):
    logged_in.post("/invoices", data={**invoice_form(), "csrf_token": csrf})
    page = logged_in.get("/backups").get_data(as_text=True)
    assert "Datenexport für die Betriebsprüfung" in page and '<option value="2026">' in page
    resp = logged_in.post("/backups/exports", data={"csrf_token": csrf, "year": "2026"})
    assert resp.status_code == 302
    name = re.search(r"gobd-export-2026-\d{8}-\d{6}\.zip", logged_in.get("/backups").get_data(as_text=True)).group(0)
    resp = logged_in.get(f"/backups/exports/{name}")
    assert resp.status_code == 200 and resp.mimetype == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
        assert "invoices.csv" in zf.namelist()
    assert logged_in.get("/backups/exports/..%2Finvoices.sqlite3").status_code == 404
    assert logged_in.get("/backups/exports/gobd-export-2026-x.zip").status_code == 404
    assert logged_in.post("/backups/exports", data={"year": "2026"}).status_code == 400
