"""Turnover limits of the Kleinunternehmerregelung (§ 19 UStG in the version from 1.1.2025).

Rules as implemented (§ 19 Abs. 1 UStG; BMF-Schreiben vom 18.03.2025, UStAE 19.1 ff.):

- The Gesamtumsatz is counted by receipts: invoices with status paid, in the year of paid_date,
  plus receipts of the same Unternehmer recorded outside this tool (external_receipts). For VAT a
  person has one Unternehmen covering all self-employed activities (§ 2 Abs. 1 Satz 2 UStG).
- Previous year above 25,000 €: no Kleinunternehmer status for the whole current year.
- Current year above 100,000 €: the status ends immediately. The receipt that crosses the limit is
  already taxable, and so is every later receipt, even for work done earlier.
- Founding year: there is no previous year, and 25,000 € is the limit for the founding year itself,
  with the same immediate effect. No extrapolation to a full year.

Only what is recorded counts: invoices here plus the external receipts entered by hand. Whether a
given activity is unternehmerisch at all stays a question for the Steuerberater (docs/GOBD.md).

This is a warning system, not a tax calculation: the tool cannot issue invoices with VAT.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .pdf import format_amount

# § 19 Abs. 1 UStG (as of 1.1.2025): previous calendar year.
PREVIOUS_YEAR_LIMIT_CENTS = 2_500_000
# § 19 Abs. 1 UStG (as of 1.1.2025): current calendar year, exceeding it ends the status at once.
CURRENT_YEAR_LIMIT_CENTS = 10_000_000
# § 19 Abs. 1 UStG, UStAE 19.1: in the founding year the limit is 25,000 € for that year itself.
FOUNDING_YEAR_LIMIT_CENTS = 2_500_000
# Warn from this share of the applicable limit, counting open invoices as a projection.
WARN_SHARE = Decimal("0.8")


def eur(cents: int) -> str:
    return format_amount(Decimal(cents) / 100)


@dataclass(frozen=True)
class Receipt:
    """One receipt counting toward the Gesamtumsatz, from an invoice or recorded externally."""

    day: str                 # ISO date
    label: str               # for messages, e.g. 'zu Rechnung 2026-003'
    cents: int
    external: bool


@dataclass
class Status:
    year: int
    founding_year: int | None
    received_internal: int   # paid invoices in `year`, by paid_date
    received_external: int   # external receipts in `year`, by received_on
    open: int                # all open invoices, projected into `year`
    previous_year: int       # receipts in `year - 1`, both sources
    limit: int               # applicable hard limit for `year`
    is_founding_year: bool
    crossing: Receipt | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def received(self) -> int:
        return self.received_internal + self.received_external

    @property
    def projected(self) -> int:
        return self.received + self.open

    @property
    def headroom(self) -> int:
        return self.limit - self.projected

    @property
    def lost_by_previous_year(self) -> bool:
        return not self.is_founding_year and self.previous_year > PREVIOUS_YEAR_LIMIT_CENTS

    @property
    def lost(self) -> bool:
        return self.lost_by_previous_year or self.received > self.limit


# Receipts: invoices (kind '') with a payment date, paid or cancelled later by a cancellation
# document (which keeps the payment). Cancellation documents and their refunds are not subtracted
# until the Steuerberater question on refunds is answered (docs/GOBD.md); that errs on the safe side.
RECEIPTS_SQL = ("FROM invoices WHERE kind = '' AND paid_date IS NOT NULL AND status IN ('paid', 'cancelled') "
                "AND substr(paid_date, 1, 4) = ?")


def _received(conn: sqlite3.Connection, year: int) -> int:
    return conn.execute(f"SELECT COALESCE(SUM(amount_cents), 0) {RECEIPTS_SQL}", (f"{year:04d}",)).fetchone()[0]


def _external(conn: sqlite3.Connection, year: int) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM external_receipts WHERE substr(received_on, 1, 4) = ?",
        (f"{year:04d}",)).fetchone()[0]


def receipts(conn: sqlite3.Connection, year: int) -> list[Receipt]:
    """All receipts of one year in the order they arrived, invoices and external entries together."""
    out = [Receipt(day=row["paid_date"], label=f"zu Rechnung {row['number']}", cents=row["amount_cents"],
                   external=False)
           for row in conn.execute(f"SELECT number, paid_date, amount_cents {RECEIPTS_SQL}", (f"{year:04d}",))]
    out += [Receipt(day=row["received_on"], label=f"außerhalb erfasst: {row['source']}",
                    cents=row["amount_cents"], external=True)
            for row in conn.execute(
                "SELECT received_on, amount_cents, source FROM external_receipts "
                "WHERE substr(received_on, 1, 4) = ?", (f"{year:04d}",))]
    return sorted(out, key=lambda r: (r.day, r.external, r.label))


def status(conn: sqlite3.Connection, founding_year: int | None, year: int | None = None) -> Status:
    year = year or date.today().year
    is_founding = founding_year == year
    st = Status(
        year=year,
        founding_year=founding_year,
        received_internal=_received(conn, year),
        received_external=_external(conn, year),
        open=conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM invoices WHERE kind = '' AND status = 'open'").fetchone()[0],
        previous_year=0 if is_founding else _received(conn, year - 1) + _external(conn, year - 1),
        limit=FOUNDING_YEAR_LIMIT_CENTS if is_founding else CURRENT_YEAR_LIMIT_CENTS,
        is_founding_year=is_founding,
    )
    running = 0
    for receipt in receipts(conn, year):
        running += receipt.cents
        if running > st.limit:
            st.crossing = receipt
            break
    st.warnings = _warnings(st)
    return st


def _warnings(st: Status) -> list[str]:
    out = []
    if st.founding_year is None:
        out.append("Gründungsjahr ist nicht eingestellt (INVOICES_FOUNDING_YEAR). Die Grenzen werden wie für "
                   "ein Jahr nach der Gründung geprüft; im Gründungsjahr gilt aber 25.000 € für das laufende Jahr.")
    if st.lost_by_previous_year:
        out.append(f"Umsatz {st.year - 1}: {eur(st.previous_year)}, über {eur(PREVIOUS_YEAR_LIMIT_CENTS)}. "
                   f"Die Kleinunternehmerregelung gilt {st.year} nicht.")
    if st.crossing:
        out.append(f"Grenze {eur(st.limit)} überschritten mit dem Umsatz {st.crossing.label} am "
                   f"{date.fromisoformat(st.crossing.day):%d.%m.%Y}. Dieser und alle späteren Umsätze sind "
                   "steuerpflichtig.")
    elif st.projected > st.limit:
        out.append(f"Eingenommen und offen zusammen {eur(st.projected)}: über der Grenze von {eur(st.limit)}. "
                   "Der Zahlungseingang, der die Grenze überschreitet, wäre bereits steuerpflichtig.")
    elif st.projected >= st.limit * WARN_SHARE:
        out.append(f"Eingenommen und offen zusammen {eur(st.projected)}: mindestens "
                   f"{int(WARN_SHARE * 100)} % der Grenze von {eur(st.limit)}.")
    if not st.is_founding_year and not st.lost and st.received > PREVIOUS_YEAR_LIMIT_CENTS:
        out.append(f"Umsatz {st.year} über {eur(PREVIOUS_YEAR_LIMIT_CENTS)}: Die Kleinunternehmerregelung gilt "
                   f"{st.year + 1} nicht mehr.")
    return out


def issue_problem(conn: sqlite3.Connection, founding_year: int | None, issue_date: date, amount_cents: int) -> str | None:
    """Why issuing a § 19 invoice is likely wrong, or None. Checked for the year of the invoice date."""
    st = status(conn, founding_year, issue_date.year)
    if st.lost_by_previous_year:
        reason = (f"Der Umsatz {st.year - 1} lag mit {eur(st.previous_year)} über {eur(PREVIOUS_YEAR_LIMIT_CENTS)}; "
                  f"die Kleinunternehmerregelung gilt {st.year} nicht.")
    elif st.projected + amount_cents > st.limit:
        reason = (f"Eingenommen, offen und diese Rechnung zusammen ({eur(st.projected + amount_cents)}) liegen über "
                  f"der Grenze von {eur(st.limit)} für {st.year}"
                  + (" (Gründungsjahr)" if st.is_founding_year else "") + ".")
    else:
        return None
    return (f"{reason} Für eine steuerpflichtige Leistung wäre eine Rechnung mit dem Hinweis auf § 19 UStG "
            "falsch, und das Programm kann noch keine Rechnungen mit Umsatzsteuer erstellen.")
