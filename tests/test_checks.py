"""Completeness and timeliness checks (GoBD Rz. 40, 46–50, 79): invoice number gap analysis,
review deadline for expenses, required category and payment method."""

import sqlite3
from datetime import date, timedelta

import pytest

from invoices import archive, db, expenses
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


def override(number, reason="Nummer aus Altsystem übernommen", **kw):
    return {"number": number, "number_override": "1", "number_reason": reason, **kw}


def last_event(conn, table="events"):
    return conn.execute(f"SELECT action, detail FROM {table} ORDER BY id DESC LIMIT 1").fetchone()


# ── Invoice number gap analysis (Rz. 40, 50) ──────────────────────────────


def test_clean_sequence_has_no_findings(store):
    s, conn = store
    issue(s, conn, number="2026-001")
    issue(s, conn, number="2026-002")
    inv = issue(s, conn, number="2026-003")
    archive.set_status(conn, inv, "cancelled", note="Doppelt")  # cancelled numbers still count
    assert archive.number_gaps(conn) == {}
    assert archive.number_findings(conn) == []


def test_gaps_scheme_and_year_mismatch_are_reported(store):
    s, conn = store
    issue(s, conn, number="2026-001")
    issue(s, conn, **override("2026-003"))
    issue(s, conn, **override("2026-007"))
    issue(s, conn, **override("RE-17"))
    issue(s, conn, **override("2025-001", issue_date="2026-01-02", service_from="2026-01-02"))
    issue(s, conn, number="2025-002", issue_date="2025-12-01", service_from="2025-12-01")
    gaps = archive.number_gaps(conn)
    assert list(gaps) == ["2026"]
    assert gaps["2026"]["missing"] == ["2026-002", "2026-004", "2026-005", "2026-006"]
    assert [n for n, _problem in gaps["2026"]["irregular"]] == ["2025-001", "RE-17"]
    assert archive.number_findings(conn) == [
        "Nummernkreis 2026: Lücke bei 2026-002, 2026-004 bis 2026-006",
        "Rechnung 2025-001 passt nicht zum Rechnungsdatum 02.01.2026",
        "Rechnung RE-17 passt nicht zum Nummernschema JJJJ-NNN",
    ]


@pytest.mark.parametrize("number,issue_date,expected", [
    ("2026-002", "2026-09-16", None),
    ("2026-003", "2026-09-16", "Lücke im Nummernkreis 2026 (nächste Nummer: 2026-002)"),
    ("2026-02", "2026-09-16", "Nummernschema"),
    ("2025-002", "2026-09-16", "Rechnungsdatum 16.09.2026"),
])
def test_number_problem(store, number, issue_date, expected):
    s, conn = store
    issue(s, conn, number="2026-001")
    problem = archive.number_problem(conn, number, date.fromisoformat(issue_date))
    assert problem is None if expected is None else expected in problem


def test_filling_a_gap_is_allowed(store):
    s, conn = store
    issue(s, conn, number="2026-001")
    issue(s, conn, **override("2026-003"))
    issue(s, conn, number="2026-002")
    assert archive.number_gaps(conn) == {}


def test_deviating_number_is_rejected_without_override(store):
    s, conn = store
    issue(s, conn, number="2026-001")
    with pytest.raises(archive.ArchiveError, match="Lücke.*Abweichende Nummer bewusst verwenden"):
        issue(s, conn, number="2026-005")
    with pytest.raises(archive.ArchiveError, match="Nummernschema"):
        issue(s, conn, number="RE-2026-2")
    with pytest.raises(archive.ArchiveError, match="Grund für die abweichende Nummer fehlt"):
        issue(s, conn, **override("2026-005", reason="  "))
    assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 1
    assert not list(s.archive_dir.rglob("*2026-005*"))


def test_override_logs_reason_in_created_event(store):
    s, conn = store
    issue(s, conn, number="2026-001")
    issue(s, conn, **override("2026-010", reason="Nummern 002–009 im Altsystem vergeben"))
    action, detail = last_event(conn)
    assert action == "created" and detail.startswith("sha256=")
    assert "; Abweichende Nummer: Rechnung 2026-010 lässt eine Lücke im Nummernkreis 2026 " \
           "(nächste Nummer: 2026-002). Grund: „Nummern 002–009 im Altsystem vergeben“" in detail


def test_override_without_deviation_logs_nothing_extra(store):
    s, conn = store
    issue(s, conn, **override("2026-001"))
    assert last_event(conn)["detail"].count(";") == 0


# ── Review deadline (Rz. 47) ──────────────────────────────────────────────


def test_unreviewed_expense_is_late_after_ten_days(store):
    s, conn = store
    exp_id = expenses.store_upload(conn, s.expenses_dir, PNG, "a.png", s.retention_years)
    row = conn.execute("SELECT * FROM expenses WHERE id = ?", (exp_id,)).fetchone()
    uploaded = date.fromisoformat(row["created_at"][:10])
    assert not expenses.review_overdue(row, today=uploaded + timedelta(days=10))
    assert expenses.review_overdue(row, today=uploaded + timedelta(days=11))

    expenses.update_expense(conn, exp_id, expenses.parse_expense_form(
        {"status": "void", "notes": "Fehl-Upload"}))
    row = conn.execute("SELECT * FROM expenses WHERE id = ?", (exp_id,)).fetchone()
    assert not expenses.review_overdue(row, today=uploaded + timedelta(days=30))


def test_reviewed_expense_is_never_late(store):
    s, conn = store
    exp_id = expenses.store_upload(conn, s.expenses_dir, PNG, "a.png", s.retention_years)
    expenses.update_expense(conn, exp_id, expenses.parse_expense_form(expense_form()))
    row = conn.execute("SELECT * FROM expenses WHERE id = ?", (exp_id,)).fetchone()
    assert not expenses.review_overdue(row, today=date.today() + timedelta(days=365))


# ── Category and payment method on expenses (Rz. 50, 79) ──────────────────


def expense_form(**kw):
    return {"vendor": "Bauhaus", "amount": "49,99", "expense_date": "2026-09-10", "status": "paid",
            "category": "Werkzeug", "paid_date": "", "invoice_number": "", "notes": "",
            "payment_method": "cash", **kw}


def test_category_required_unless_void(store):
    with pytest.raises(archive.ArchiveError, match="Kategorie fehlt"):
        expenses.parse_expense_form(expense_form(category=" "))
    with pytest.raises(archive.ArchiveError, match="Kategorie fehlt"):
        expenses.parse_expense_form(expense_form(category="", status="open"))
    assert expenses.parse_expense_form({"status": "void", "notes": "x"})["category"] == ""


def test_payment_method_required_when_paid(store):
    with pytest.raises(archive.ArchiveError, match="Zahlungsart fehlt"):
        expenses.parse_expense_form(expense_form(payment_method=""))
    with pytest.raises(archive.ArchiveError, match="Unbekannte Zahlungsart"):
        expenses.parse_expense_form(expense_form(payment_method="crypto"))
    assert expenses.parse_expense_form(expense_form(status="open", payment_method="bank"))["payment_method"] == ""


def test_expense_payment_method_changes_are_logged(store):
    s, conn = store
    exp_id = expenses.store_upload(conn, s.expenses_dir, PNG, "a.png", s.retention_years)
    expenses.update_expense(conn, exp_id, expenses.parse_expense_form(expense_form()))
    assert "Zahlungsart: – → Bar" in last_event(conn, "expense_events")["detail"]
    expenses.update_expense(conn, exp_id, expenses.parse_expense_form(expense_form(payment_method="private")))
    assert tuple(last_event(conn, "expense_events")) == \
        ("updated", "Zahlungsart: Bar → Privat bezahlt (Einlage)")
    expenses.update_expense(conn, exp_id, expenses.parse_expense_form(expense_form(status="open")))
    assert last_event(conn, "expense_events")["detail"].endswith("; Zahlungsart: Privat bezahlt (Einlage) → –")


# ── Payment method on invoices (Rz. 48, 79) ───────────────────────────────


def test_invoice_payment_method_required_and_cleared(store):
    s, conn = store
    inv = issue(s, conn)
    with pytest.raises(archive.ArchiveError, match="Zahlungsart fehlt"):
        archive.set_status(conn, inv, "paid", date(2026, 9, 20))
    archive.set_status(conn, inv, "paid", date(2026, 9, 20), payment_method="cash")
    assert last_event(conn)["detail"] == "Status: Offen → Bezahlt; Bezahlt am: – → 20.09.2026; Zahlungsart: – → Bar"
    archive.set_status(conn, inv, "open")
    assert last_event(conn)["detail"] == "Status: Bezahlt → Offen; Bezahlt am: 20.09.2026 → –; Zahlungsart: Bar → –"
    assert conn.execute("SELECT payment_method FROM invoices").fetchone()[0] == ""


def test_payment_method_migration_on_existing_database(env, tmp_path, monkeypatch):
    path = tmp_path / "old.sqlite3"
    conn = db.connect(path)
    number = next(i for i, (desc, _sql) in enumerate(db.MIGRATIONS, start=1)
                  if desc == "payment_method on invoices and expenses")
    with monkeypatch.context() as m:
        m.setattr(db, "MIGRATIONS", db.MIGRATIONS[:number - 1])
        db.init_db(conn)
    assert db.schema_version(conn) == number - 1
    conn.execute("""INSERT INTO invoices (number, issue_date, service_date, customer_name, title,
                    amount_cents, status, paid_date, source, pdf_path, pdf_sha256, pdf_size, payload_json,
                    retain_until, created_at, updated_at)
                    VALUES ('2026-001', '2026-01-02', '02.01.2026', 'K', 'T', 100, 'paid', '2026-01-10',
                    'imported', 'a.pdf', 'x', 1, '{}', '2036-12-31', 'now', 'now')""")
    conn.execute("""INSERT INTO expenses (doc_path, doc_sha256, doc_size, doc_type, suggestion_json,
                    retain_until, created_at, updated_at)
                    VALUES ('e.png', 'y', 1, 'png', '{}', '2036-12-31', 'now', 'now')""")
    conn.commit()

    db.init_db(conn)
    assert db.schema_version(conn) == len(db.MIGRATIONS)
    assert conn.execute("SELECT payment_method FROM invoices").fetchone()[0] == ""
    assert conn.execute("SELECT payment_method FROM expenses").fetchone()[0] == ""
    assert f"{number}: payment_method on invoices and expenses" in [r[0] for r in conn.execute(
        "SELECT detail FROM system_events WHERE action = 'schema_migration'")]

    conn.execute("UPDATE invoices SET payment_method = 'bank'")  # not covered by the immutable trigger
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute("UPDATE expenses SET payment_method = 'crypto'")
    conn.close()


# ── Web ───────────────────────────────────────────────────────────────────


def test_web_rejects_gap_and_accepts_override(logged_in, csrf):
    c = logged_in
    c.post("/invoices", data={**invoice_form(), "csrf_token": csrf})
    new_form = c.get("/invoices/new").get_data(as_text=True)
    assert "Abweichende Nummer bewusst verwenden" in new_form

    bad = c.post("/invoices", data={**invoice_form(number="2026-004"), "csrf_token": csrf})
    html = bad.get_data(as_text=True)
    assert bad.status_code == 400 and "Lücke im Nummernkreis 2026" in html
    assert 'value="2026-004"' in html

    ok = c.post("/invoices", data={**invoice_form(**override("2026-004")), "csrf_token": csrf})
    assert ok.status_code == 302
    detail = c.get(ok.headers["Location"]).get_data(as_text=True)
    assert "Grund: „Nummer aus Altsystem übernommen“" in detail

    listing = c.get("/invoices").get_data(as_text=True)
    assert "Nummernkreis 2026: Lücke bei 2026-002 bis 2026-003" in listing


def test_web_invoice_payment_method(logged_in, csrf):
    c = logged_in
    url = c.post("/invoices", data={**invoice_form(), "csrf_token": csrf}).headers["Location"]
    html = c.get(url).get_data(as_text=True)
    assert 'name="payment_method"' in html and "GoBD Rz. 48" in html
    c.post(url + "/status", data={"status": "paid", "paid_date": "2026-09-20", "csrf_token": csrf})
    assert "Zahlungsart fehlt" in c.get(url).get_data(as_text=True)
    c.post(url + "/status", data={"status": "paid", "paid_date": "2026-09-20", "payment_method": "cash",
                                  "csrf_token": csrf})
    html = c.get(url).get_data(as_text=True)
    assert "Zahlungsart: – → Bar" in html and "<dd>Bar</dd>" in html


def test_web_late_review_marker(logged_in, csrf, monkeypatch):
    import io

    c = logged_in
    url = c.post("/expenses/upload", content_type="multipart/form-data", data={
        "csrf_token": csrf, "files": [(io.BytesIO(PNG), "quittung.png")]}).headers["Location"]
    assert "Tage ungeprüft" not in c.get("/expenses").get_data(as_text=True)

    class Later(date):
        @classmethod
        def today(cls):
            return date.today() + timedelta(days=11)

    monkeypatch.setattr(expenses, "date", Later)
    assert "über 10 Tage ungeprüft" in c.get("/expenses").get_data(as_text=True)
    assert "Über 10 Tage ungeprüft" in c.get(url).get_data(as_text=True)

    missing = c.post(url, data={"csrf_token": csrf, **expense_form(category="")})
    assert missing.status_code == 400 and "Kategorie fehlt" in missing.get_data(as_text=True)
    assert c.post(url, data={"csrf_token": csrf, **expense_form()}).status_code == 302
    assert "Tage ungeprüft" not in c.get("/expenses").get_data(as_text=True)
    html = c.get(url).get_data(as_text=True)
    assert "Zahlungsart: – → Bar" in html and '<option value="cash" selected>' in html
