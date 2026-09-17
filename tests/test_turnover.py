"""Turnover limit monitor for the Kleinunternehmerregelung (§ 19 UStG since 2025)."""

from dataclasses import replace
from datetime import date

import pytest

from invoices import archive, db, system, turnover
from invoices.config import ConfigError, load_sender, load_settings
from tests.conftest import csrf_from, invoice_form


@pytest.fixture
def store(env):
    s = load_settings()
    conn = db.connect(s.db_path)
    db.init_db(conn)
    yield s, conn
    conn.close()


class Book:
    """Issues invoices without tripping the monitor itself, to build up a turnover history."""

    def __init__(self, s, conn):
        self.s, self.conn, self.n = s, conn, {}

    def invoice(self, amount: str, issue: str, paid: str | None = None) -> int:
        year = int(issue[:4])
        self.n[year] = self.n.get(year, 0) + 1
        form = invoice_form(number=f"{year}-{self.n[year]:03d}", issue_date=issue, service_from=issue,
                            amount=amount, limit_override="1", limit_reason="Testdaten")
        inv = archive.issue_invoice(self.conn, self.s.archive_dir, archive.parse_invoice_form(form),
                                    load_sender(self.s.sender_file), self.s.retention_years)
        if paid:
            archive.set_status(self.conn, inv, "paid", date.fromisoformat(paid), payment_method="bank")
        return inv


def test_limits_are_named_constants():
    assert turnover.PREVIOUS_YEAR_LIMIT_CENTS == 2_500_000
    assert turnover.CURRENT_YEAR_LIMIT_CENTS == 10_000_000
    assert turnover.FOUNDING_YEAR_LIMIT_CENTS == 2_500_000


def test_receipts_count_by_payment_year_and_open_invoices_are_projected(store):
    s, conn = store
    book = Book(s, conn)
    book.invoice("1.000,00", "2025-12-10", paid="2026-01-05")   # issued 2025, received 2026
    book.invoice("2.000,00", "2026-03-01", paid="2026-03-10")
    book.invoice("500,00", "2026-04-01")                         # open
    book.invoice("9.999,00", "2025-11-01", paid="2025-11-20")   # previous year
    st = turnover.status(conn, founding_year=2024, year=2026)
    assert (st.received, st.open, st.previous_year, st.limit) == (300000, 50000, 999900, 10_000_000)
    assert st.headroom == 10_000_000 - 350000 and not st.lost and st.warnings == []


def test_founding_year_uses_25000_for_the_year_itself_without_previous_year(store):
    s, conn = store
    book = Book(s, conn)
    book.invoice("19.000,00", "2026-02-01", paid="2026-02-10")
    book.invoice("1.500,00", "2026-03-01")
    st = turnover.status(conn, founding_year=2026, year=2026)
    assert st.is_founding_year and st.limit == 2_500_000 and st.previous_year == 0
    assert any("80 %" in w for w in st.warnings)
    problem = turnover.issue_problem(conn, 2026, date(2026, 5, 1), 500_000)
    assert "Gründungsjahr" in problem and "§ 19 UStG" in problem and "Umsatzsteuer" in problem
    assert turnover.issue_problem(conn, 2026, date(2026, 5, 1), 100_000) is None


def test_crossing_receipt_is_named(store):
    s, conn = store
    book = Book(s, conn)
    book.invoice("20.000,00", "2026-01-10", paid="2026-01-20")
    book.invoice("4.000,00", "2026-02-10", paid="2026-02-20")
    book.invoice("2.000,00", "2026-03-10", paid="2026-03-20")
    book.invoice("100,00", "2026-04-10", paid="2026-04-20")
    st = turnover.status(conn, founding_year=2026, year=2026)
    assert st.lost and (st.crossing.label, st.crossing.day) == ("zu Rechnung 2026-003", "2026-03-20")
    assert any("zu Rechnung 2026-003 am 20.03.2026" in w and "alle späteren" in w for w in st.warnings)


def test_passing_25000_warns_for_next_year_and_previous_year_blocks(store):
    s, conn = store
    book = Book(s, conn)
    book.invoice("26.000,00", "2025-06-01", paid="2025-06-15")
    st2025 = turnover.status(conn, founding_year=2024, year=2025)
    assert any("gilt 2026 nicht mehr" in w for w in st2025.warnings) and not st2025.lost
    st2026 = turnover.status(conn, founding_year=2024, year=2026)
    assert st2026.lost_by_previous_year
    assert "gilt 2026 nicht" in turnover.issue_problem(conn, 2024, date(2026, 1, 5), 100)


def test_current_year_projection_above_100000_blocks(store):
    s, conn = store
    book = Book(s, conn)
    book.invoice("24.000,00", "2025-03-01", paid="2025-03-10")
    book.invoice("90.000,00", "2026-03-01", paid="2026-03-10")
    book.invoice("9.000,00", "2026-04-01")
    assert turnover.issue_problem(conn, 2024, date(2026, 5, 1), 99_900) is None
    assert "100.000,00 €" in turnover.issue_problem(conn, 2024, date(2026, 5, 1), 100_100)


def test_issuing_requires_override_with_reason_and_logs_it(store):
    s, conn = store
    book = Book(s, conn)
    book.invoice("24.000,00", "2026-02-01", paid="2026-02-10")
    form = invoice_form(number="2026-002", issue_date="2026-03-01", service_from="2026-03-01", amount="1.500,00")
    with pytest.raises(archive.ArchiveError, match="Trotz Umsatzgrenze erstellen"):
        archive.issue_invoice(conn, s.archive_dir, archive.parse_invoice_form(form), load_sender(s.sender_file),
                              s.retention_years, founding_year=2026)
    assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 1
    with pytest.raises(archive.ArchiveError, match="Grund"):
        archive.parse_invoice_form({**form, "limit_override": "1", "limit_reason": ""})
    inv = archive.issue_invoice(conn, s.archive_dir,
                                archive.parse_invoice_form({**form, "limit_override": "1",
                                                            "limit_reason": "Zahlung erst 2027 erwartet"}),
                                load_sender(s.sender_file), s.retention_years, founding_year=2026)
    detail = conn.execute("SELECT detail FROM events WHERE invoice_id = ?", (inv,)).fetchone()[0]
    assert "Umsatzgrenze § 19 UStG:" in detail and "Grund: „Zahlung erst 2027 erwartet“" in detail


def test_unknown_founding_year_is_flagged(store):
    s, conn = store
    assert any("INVOICES_FOUNDING_YEAR" in w for w in turnover.status(conn, None, 2026).warnings)


def test_founding_year_config_is_validated_and_logged(env, monkeypatch):
    monkeypatch.setenv("INVOICES_FOUNDING_YEAR", "2026")
    s = load_settings()
    assert s.founding_year == 2026
    conn = db.connect(s.db_path)
    db.init_db(conn)
    system.record_config(conn, s)
    system.record_config(conn, replace(s, founding_year=2025))
    history = [r[0] for r in conn.execute("SELECT detail FROM system_events WHERE action = 'config_changed'")]
    assert '"founding_year": 2026' in history[0] and '"founding_year": 2025' in history[1]
    conn.close()
    monkeypatch.setenv("INVOICES_FOUNDING_YEAR", "zwanzig")
    with pytest.raises(ConfigError):
        load_settings()


def test_status_block_and_override_in_the_web_ui(env, monkeypatch):
    monkeypatch.setenv("INVOICES_FOUNDING_YEAR", str(date.today().year))
    from invoices import auth, create_app
    from tests.conftest import PASSWORD

    auth.throttle = auth.LoginThrottle()
    app = create_app({"TESTING": True})
    c = app.test_client()
    page = c.get("/login").get_data(as_text=True)
    c.post("/login", data={"username": "testuser", "password": PASSWORD, "csrf_token": csrf_from(page)})
    year = date.today().year
    new = c.get("/invoices/new").get_data(as_text=True)
    assert f"Kleinunternehmergrenze {year}" in new and "Gründungsjahr" in new and "25.000,00 €" in new
    form = {**invoice_form(number=f"{year}-001", issue_date=date.today().isoformat(),
                           service_from=date.today().isoformat(), amount="26.000,00"),
            "csrf_token": csrf_from(new)}
    blocked = c.post("/invoices", data=form)
    assert blocked.status_code == 400 and "Trotz Umsatzgrenze erstellen" in blocked.get_data(as_text=True)
    created = c.post("/invoices", data={**form, "limit_override": "1", "limit_reason": "Geprüft"})
    assert created.status_code == 302
    listing = c.get("/invoices").get_data(as_text=True)
    assert "Kleinunternehmergrenze" in listing and "26.000,00 €" in listing
