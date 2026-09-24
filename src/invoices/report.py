"""Read-only business report as JSON: `invoices report --json`, to stdout or, in the `report`
service, into a file that an unprivileged SSH user can read (README, "Read-only report").

The database is opened with mode=ro and query_only: no write, no migration, no system event, no
control run. Nothing but business figures leaves this module. Sender data (address, IBAN, tax
number), settings, paths, hosts and versions are never read into the report, so they cannot end
up in it; the CLI turns every failure into one generic message.

Definitions follow the web overview so both show the same numbers:
- Income, refunds and expenses by payment date as in expenses.cash_summary: income is every
  receipt of an invoice, also of one cancelled later; refunds are paid cancellation documents;
  expenses are paid, not voided, without assets. Surplus before AfA = income - refunds - expenses.
- The § 19 UStG figures come from turnover.status for the same year.
- Largest customer: receipts of the year per customer name, net of refunds paid in that year,
  as a share of all receipts of the year from invoices (receipts recorded outside the tool have
  no customer and are left out).
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from . import expenses, turnover
from .archive import CANCELLATION


class ReportError(RuntimeError):
    pass


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open the database without any way to change it. mode=ro never creates the file."""
    if not db_path.is_file():
        raise ReportError("database missing")
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _invoices(conn: sqlite3.Connection, year: str) -> list[dict]:
    """Invoices and cancellation documents issued or paid in the year, plus everything still open
    (open invoices count toward the § 19 projection whatever their year)."""
    rows = conn.execute(
        "SELECT i.number, i.kind, i.issue_date, i.customer_name, i.title, i.amount_cents, i.status,"
        " i.paid_date, c.number AS cancels, b.number AS cancelled_by"
        " FROM invoices i"
        " LEFT JOIN invoices c ON c.id = i.cancels_invoice_id"
        " LEFT JOIN invoices b ON b.cancels_invoice_id = i.id"
        " WHERE substr(i.issue_date, 1, 4) = ? OR substr(i.paid_date, 1, 4) = ? OR i.status = 'open'"
        " ORDER BY i.issue_date, i.number", (year, year))
    return [{
        "number": r["number"],
        "type": "cancellation" if r["kind"] == CANCELLATION else "invoice",
        "issue_date": r["issue_date"],
        "customer": r["customer_name"],
        "title": r["title"],
        "amount_cents": r["amount_cents"],
        "status": r["status"],
        "paid_date": r["paid_date"],
        "cancels": r["cancels"],
        "cancelled_by": r["cancelled_by"],
    } for r in rows]


def _by_month(conn: sqlite3.Connection, sql: str, params: tuple) -> dict[int, int]:
    return {int(m): cents for m, cents in conn.execute(sql, params)}


def _cash(conn: sqlite3.Connection, year: str) -> dict:
    """Income, refunds and expenses per month by payment date, with totals."""
    income = _by_month(conn,
        "SELECT substr(paid_date, 6, 2), SUM(amount_cents) FROM invoices WHERE kind = '' AND paid_date IS NOT NULL"
        " AND status IN ('paid', 'cancelled') AND substr(paid_date, 1, 4) = ? GROUP BY 1", (year,))
    refunds = _by_month(conn,
        "SELECT substr(paid_date, 6, 2), -SUM(amount_cents) FROM invoices WHERE kind = ? AND status = 'paid'"
        " AND paid_date IS NOT NULL AND substr(paid_date, 1, 4) = ? GROUP BY 1", (CANCELLATION, year))
    day = expenses.booking_date_sql()
    # Only the business share of an expense is deductible; the rest is private and counts nowhere.
    deductible = expenses.deductible_sql()
    spent = _by_month(conn,
        f"SELECT substr({day}, 6, 2), SUM({deductible}) FROM expenses WHERE status = 'paid' AND treatment = ''"
        f" AND substr({day}, 1, 4) = ? GROUP BY 1", (year,))
    assets = _by_month(conn,
        f"SELECT substr({day}, 6, 2), SUM({deductible}) FROM expenses WHERE status = 'paid' AND treatment = ?"
        f" AND substr({day}, 1, 4) = ? GROUP BY 1", (expenses.ASSET, year))
    months = []
    for m in range(1, 13):
        row = {"month": f"{year}-{m:02d}", "income_cents": income.get(m, 0), "refunds_cents": refunds.get(m, 0),
               "expenses_cents": spent.get(m, 0), "assets_cents": assets.get(m, 0)}
        row["surplus_before_afa_cents"] = row["income_cents"] - row["refunds_cents"] - row["expenses_cents"]
        months.append(row)
    totals = {key: sum(m[key] for m in months)
              for key in ("income_cents", "refunds_cents", "expenses_cents", "assets_cents",
                          "surplus_before_afa_cents")}
    return {"months": months, "totals": totals}


def _small_business(conn: sqlite3.Connection, year: int, founding_year: int | None) -> dict:
    st = turnover.status(conn, founding_year, year)
    return {
        "received_cents": st.received_internal,
        "open_cents": st.open,
        "received_outside_cents": st.received_external,
        "limit_cents": st.limit,
        "headroom_cents": st.headroom,
        "founding_year": st.is_founding_year,
        "previous_year_cents": st.previous_year,
        "lost": st.lost,
    }


def _largest_customer(conn: sqlite3.Connection, year: str) -> dict | None:
    per_customer: dict[str, int] = {}
    for r in conn.execute(
            "SELECT customer_name, amount_cents, kind FROM invoices WHERE paid_date IS NOT NULL"
            " AND substr(paid_date, 1, 4) = ? AND ((kind = '' AND status IN ('paid', 'cancelled'))"
            " OR (kind = ? AND status = 'paid'))", (year, CANCELLATION)):
        per_customer[r["customer_name"]] = per_customer.get(r["customer_name"], 0) + r["amount_cents"]
    total = sum(per_customer.values())
    if total <= 0:
        return None
    name, cents = max(per_customer.items(), key=lambda kv: (kv[1], kv[0]))
    return {"customer": name, "receipts_cents": cents, "total_cents": total,
            "share_percent": round(cents * 100 / total, 1)}


def build(conn: sqlite3.Connection, year: int | None = None, founding_year: int | None = None) -> dict:
    year = year or date.today().year
    y = f"{year:04d}"
    return {
        "year": year,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "currency": "EUR",
        "invoices": _invoices(conn, y),
        "cash": _cash(conn, y),
        "small_business": _small_business(conn, year, founding_year),
        "largest_customer": _largest_customer(conn, y),
    }


def write_atomic(path: Path, text: str) -> None:
    """Replace the report file in one step, so a reader never sees half a file. World-readable:
    the read-only SSH user is not in the container's group."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".report-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
