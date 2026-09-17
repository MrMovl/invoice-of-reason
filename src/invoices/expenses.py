"""Expenses: received invoices and receipts, uploaded as PDF, image or e-invoice XML.

The uploaded document is archived like an issued invoice: exclusive create, read-only,
SHA-256 recorded, never deleted. Its booking data (vendor, date, amount, ...) starts as a
suggestion read from the e-invoice XML (plain or embedded in a ZUGFeRD PDF) or else from the PDF
text layer, and stays correctable; every change is logged.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from . import db, einvoice, extract
from .archive import (
    PAYMENT_METHODS,
    ArchiveError,
    payment_label,
    quote,
    _clean,
    _write_once,
    parse_amount,
    parse_date,
    retain_until,
    sha256_bytes,
    slugify,
    verify_file,
)
from .pdf import format_amount, format_date

MAX_FILE_SIZE = 20 * 1024 * 1024
DOC_TYPES = {
    "pdf": (b"%PDF-", "application/pdf"),
    "jpg": (b"\xff\xd8\xff", "image/jpeg"),
    "png": (b"\x89PNG\r\n\x1a\n", "image/png"),
    "xml": (None, "application/xml"),  # detected by parsing, see detect_type
}
STATUSES = ("paid", "open", "void")
STATUS_NAMES = {"paid": "Bezahlt", "open": "Offen", "void": "Verworfen"}
REVIEW_DAYS = 10  # GoBD Rz. 47: unbare Geschäftsvorfälle within ten days
FIELD_LABELS = {
    "vendor": "Lieferant",
    "invoice_number": "Rechnungsnummer",
    "expense_date": "Rechnungsdatum",
    "amount_cents": "Betrag",
    "category": "Kategorie",
    "status": "Status",
    "paid_date": "Bezahlt am",
    "payment_method": "Zahlungsart",
    "reverse_charge": "Steuerschuldnerschaft § 13b UStG",
    "notes": "Notiz",
}
REVERSE_CHARGE = "13b"
# § 13b Abs. 5 UStG: a Kleinunternehmer owes the VAT on these purchases (typically services from
# suppliers abroad, § 13b Abs. 1 UStG) and has to declare it. Standard rate § 12 Abs. 1 UStG;
# shown as an orientation value only, the correct rate depends on the supply.
REVERSE_CHARGE_ORIENTATION_RATE = Decimal("0.19")
# Wording on PDF invoices that points to reverse charge. Only a hint for the review, never set
# automatically.
REVERSE_CHARGE_TEXT = re.compile(
    r"reverse[\s-]*charge|steuerschuldnerschaft\s+des\s+leistungsempf|umkehr(?:ung)?\s+der\s+steuerschuld"
    r"|art(?:icle|\.|ikel)?\s*196\b",
    re.IGNORECASE,
)


class DuplicateError(ArchiveError):
    def __init__(self, expense_id: int):
        super().__init__(f"Dieser Beleg wurde bereits hochgeladen (Beleg {expense_id}).")
        self.expense_id = expense_id


def detect_type(data: bytes) -> str:
    """By content, not by file name. XML counts only if it parses as an e-invoice."""
    for ext, (magic, _mime) in DOC_TYPES.items():
        if magic and data.startswith(magic):
            return ext
    if einvoice.looks_like_xml(data):
        einvoice.parse(data)
        return "xml"
    raise ArchiveError("Nur PDF, JPEG, PNG oder E-Rechnungen (XRechnung-XML) werden unterstützt.")


def mimetype(doc_type: str) -> str:
    return DOC_TYPES[doc_type][1]


def store_upload(
    conn: sqlite3.Connection,
    expenses_dir: Path,
    data: bytes,
    original_filename: str,
    retention_years: int,
    today: date | None = None,
) -> int:
    """Archive an uploaded document and record it with suggested booking data. Returns the id."""
    today = today or date.today()
    if not data:
        raise ArchiveError("Die Datei ist leer.")
    if len(data) > MAX_FILE_SIZE:
        raise ArchiveError("Datei zu groß (max. 20 MB pro Beleg).")
    doc_type = detect_type(data)
    sha = sha256_bytes(data)
    existing = conn.execute("SELECT id FROM expenses WHERE doc_sha256 = ?", (sha,)).fetchone()
    if existing:
        raise DuplicateError(existing["id"])

    original_filename = original_filename.replace("\\", "/").rsplit("/", 1)[-1][:200]
    text = extract.pdf_text(data) if doc_type == "pdf" else ""
    text_layer = bool(text.strip())
    # Structured e-invoice data beats text heuristics; a ZUGFeRD PDF stays the archived document.
    invoice = einvoice.parse(data) if doc_type == "xml" else None
    if doc_type == "pdf":
        invoice = einvoice.from_pdf(data)
    if invoice:
        suggestion = invoice.suggestion()
        source = "xml" if doc_type == "xml" else "zugferd"
        text = text if text_layer else invoice.as_text()
        extra = {"credit_note": invoice.credit_note, "currency": invoice.currency}
    else:
        suggestion = extract.suggest(text)
        source = "text" if text_layer else "none"
        extra = {}
    hint = reverse_charge_hint(invoice, text)
    if hint:
        extra["reverse_charge_hint"] = hint
    in_euro = extra.get("currency") in (None, "", "EUR")  # other currencies are not prefilled
    stem = slugify(original_filename.rsplit(".", 1)[0], fallback="Beleg")
    rel_path = f"{today.year}/{today:%Y%m%d}_{stem}_{sha[:8]}.{doc_type}"
    now = db.now_iso()
    row = {
        "vendor": suggestion.vendor[:120],
        "invoice_number": suggestion.invoice_number[:60],
        "expense_date": suggestion.expense_date.isoformat() if suggestion.expense_date else None,
        "amount_cents": int(suggestion.amount * 100)
        if in_euro and suggestion.amount and suggestion.amount < 10_000_000 else None,
        "doc_path": rel_path,
        "doc_sha256": sha,
        "doc_size": len(data),
        "doc_type": doc_type,
        "original_filename": original_filename,
        "doc_text": text,
        "suggestion_json": json.dumps({**suggestion.as_dict(), **extra, "source": source,
                                       "text_layer": text_layer},
                                      ensure_ascii=False, sort_keys=True),
        # Counted from the upload, which is never earlier than the document date.
        "retain_until": retain_until(today, retention_years).isoformat(),
        "created_at": now,
        "updated_at": now,
    }
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    written = False
    try:
        cur = conn.execute(f"INSERT INTO expenses ({cols}) VALUES ({marks})", tuple(row.values()))
        expense_id = cur.lastrowid
        db.add_expense_event(conn, expense_id, "uploaded", f"sha256={sha}")
        _write_once(expenses_dir, rel_path, data)
        written = True
        conn.commit()
    except BaseException as e:
        conn.rollback()
        if written:
            (expenses_dir / rel_path).unlink(missing_ok=True)
        if isinstance(e, sqlite3.IntegrityError):
            raise ArchiveError(f"Beleg konnte nicht gespeichert werden: {e}") from None
        raise
    return expense_id


def reverse_charge_hint(invoice: einvoice.EInvoice | None, text: str) -> str:
    """Why an upload may fall under § 13b UStG, or ''. A hint for the review only: the field is
    never set without a person deciding (a foreign seller may also charge German VAT)."""
    reasons = []
    if invoice and "AE" in invoice.vat_categories:
        reasons.append("E-Rechnung mit Steuerkategorie AE (Steuerschuldnerschaft des Leistungsempfängers)")
    if invoice and invoice.seller_country and invoice.seller_country != "DE":
        reasons.append(f"Rechnungssteller mit Sitz im Ausland ({invoice.seller_country})")
    match = REVERSE_CHARGE_TEXT.search(text or "")
    if match:
        reasons.append(f"Belegtext enthält „{' '.join(match.group(0).split())}“")
    return "; ".join(reasons)


def parse_expense_form(form) -> dict:
    """Validate the review form. A voided expense (wrong upload) needs no booking data.
    The category is the minimum business assignment (GoBD Rz. 50); a paid expense needs its
    payment method (Rz. 79)."""
    status = form.get("status") or ""
    if status not in STATUSES:
        raise ArchiveError("Unbekannter Status.")
    required = status != "void"
    amount = (form.get("amount") or "").strip()
    paid_date = parse_date(form.get("paid_date"), "Zahlungsdatum", required=False)
    payment_method = form.get("payment_method") or ""
    if payment_method and payment_method not in PAYMENT_METHODS:
        raise ArchiveError("Unbekannte Zahlungsart.")
    return {
        "vendor": _clean(form.get("vendor"), "Lieferant", 120, required=required),
        "invoice_number": _clean(form.get("invoice_number"), "Rechnungsnummer", 60, required=False),
        "expense_date": _iso(parse_date(form.get("expense_date"), "Rechnungsdatum", required=required)),
        "amount_cents": int(parse_amount(amount) * 100) if amount or required else None,
        "category": _clean(form.get("category"), "Kategorie", 60, required=required),
        "status": status,
        "paid_date": _iso(paid_date) if status == "paid" else None,
        "payment_method": _payment_method(status, payment_method),
        "reverse_charge": _reverse_charge(form.get("reverse_charge")),
        "notes": _clean_notes(form.get("notes")),
    }


def _reverse_charge(value: str | None) -> str:
    if value in (None, ""):
        return ""
    if value != REVERSE_CHARGE:
        raise ArchiveError("Unbekannter Wert für die Steuerschuldnerschaft.")
    return value


def _payment_method(status: str, method: str) -> str:
    if status != "paid":
        return ""
    if not method:
        raise ArchiveError("Zahlungsart fehlt.")
    return method


def review_overdue(row, today: date | None = None) -> bool:
    """True if an unreviewed expense was uploaded more than REVIEW_DAYS days ago (GoBD Rz. 47)."""
    if row["reviewed"] or row["status"] == "void":
        return False
    today = today or date.today()
    return date.fromisoformat(row["created_at"][:10]) < today - timedelta(days=REVIEW_DAYS)


def _clean_notes(value: str | None) -> str:
    notes = (value or "").replace("\r\n", "\n").strip()
    if len(notes) > 2000:
        raise ArchiveError("Notiz ist zu lang (max. 2000 Zeichen).")
    return notes


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _show(field: str, value) -> str:
    if value in (None, ""):
        return "–"
    if field == "amount_cents":
        return format_amount(Decimal(value) / 100)
    if field in ("expense_date", "paid_date"):
        return format_date(date.fromisoformat(value))
    if field == "notes":
        return quote(value)
    if field == "payment_method":
        return payment_label(value)
    if field == "status":
        return STATUS_NAMES.get(value, value)
    if field == "reverse_charge":
        return "ja"
    return str(value)


def update_expense(conn: sqlite3.Connection, expense_id: int, values: dict) -> None:
    """Store reviewed booking data, mark the expense as checked and log what changed."""
    row = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    if row is None:
        raise ArchiveError("Beleg nicht gefunden.")
    if values["status"] == "void" and row["status"] != "void" and not values["notes"]:
        raise ArchiveError("Grund für das Verwerfen fehlt (Notiz).")
    changes = [
        f"{FIELD_LABELS[k]}: {_show(k, row[k])} → {_show(k, v)}"
        for k, v in values.items() if row[k] != v
    ]
    with conn:
        sets = ", ".join(f"{k} = ?" for k in values)
        conn.execute(f"UPDATE expenses SET {sets}, reviewed = 1, updated_at = ? WHERE id = ?",
                     (*values.values(), db.now_iso(), expense_id))
        if changes or not row["reviewed"]:
            db.add_expense_event(conn, expense_id, "reviewed" if not row["reviewed"] else "updated",
                                 "; ".join(changes))


def booking_date_sql() -> str:
    """SQL expression for the date an expense counts in: payment, else document, else upload."""
    return "COALESCE(paid_date, expense_date, substr(created_at, 1, 10))"


def verify_expense(expenses_dir: Path, row) -> str | None:
    return verify_file(expenses_dir / row["doc_path"], row["doc_sha256"], missing="Datei fehlt")


def verify_all(conn: sqlite3.Connection, expenses_dir: Path) -> list[tuple[str, str]]:
    problems = []
    for row in conn.execute("SELECT id, doc_path, doc_sha256 FROM expenses ORDER BY id"):
        problem = verify_expense(expenses_dir, row)
        if problem:
            problems.append((f"Beleg {row['id']}", problem))
    return problems


def cash_summary(conn: sqlite3.Connection, year: str = "") -> dict:
    """Income and expenses by payment date (Zufluss-/Abflussprinzip), optionally for one year."""
    year_ok = bool(re.fullmatch(r"\d{4}", year))
    income = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM invoices WHERE status = 'paid'"
        + (" AND substr(paid_date, 1, 4) = ?" if year_ok else ""),
        (year,) if year_ok else (),
    ).fetchone()[0]
    spent = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM expenses WHERE status = 'paid'"
        + (f" AND substr({booking_date_sql()}, 1, 4) = ?" if year_ok else ""),
        (year,) if year_ok else (),
    ).fetchone()[0]
    to_review = conn.execute(
        "SELECT COUNT(*) FROM expenses WHERE reviewed = 0 AND status != 'void'").fetchone()[0]
    return {"year": year if year_ok else "", "income": income, "expenses": spent,
            "surplus": income - spent, "to_review": to_review,
            "reverse_charge": reverse_charge_summary(conn, int(year) if year_ok else date.today().year)}


def reverse_charge_date_sql() -> str:
    """The date a § 13b purchase counts in: the invoice date, else payment, else upload. The tax
    arises with the end of the period of the supply (§ 13b Abs. 1 and 2 UStG), which the tool does
    not record; the invoice date is the closest available approximation."""
    return "COALESCE(expense_date, paid_date, substr(created_at, 1, 10))"


def reverse_charge_summary(conn: sqlite3.Connection, year: int) -> dict:
    """Tax base of § 13b purchases per quarter of one year, plus 19 % as an orientation value.
    Open and paid expenses count (the tax does not depend on payment), voided ones do not."""
    day = reverse_charge_date_sql()
    quarters = {q: 0 for q in (1, 2, 3, 4)}
    count = 0
    for month, cents in conn.execute(
        f"SELECT CAST(substr({day}, 6, 2) AS INTEGER), amount_cents FROM expenses "
        f"WHERE reverse_charge = ? AND status != 'void' AND substr({day}, 1, 4) = ?",
        (REVERSE_CHARGE, f"{year:04d}"),
    ):
        quarters[(month - 1) // 3 + 1] += cents or 0
        count += 1
    base = sum(quarters.values())

    def tax(cents: int) -> int:
        return int((Decimal(cents) * REVERSE_CHARGE_ORIENTATION_RATE).quantize(Decimal("1"), "ROUND_HALF_UP"))

    return {
        "year": year,
        "count": count,
        "base": base,
        "tax_orientation": tax(base),
        "quarters": [{"quarter": q, "base": quarters[q], "tax_orientation": tax(quarters[q])}
                     for q in (1, 2, 3, 4)],
        "rate_percent": int(REVERSE_CHARGE_ORIENTATION_RATE * 100),
    }
