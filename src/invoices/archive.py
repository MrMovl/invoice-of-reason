"""Invoice archive: numbering, issuing, status changes and integrity checks.

Archived PDFs are write-once. They are created with exclusive mode, made read-only,
and their SHA-256 is stored so any later modification is detectable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import db
from .pdf import InvoiceData, Sender, format_date, render_invoice

NUMBER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
MAX_AMOUNT = Decimal("10000000")


class ArchiveError(ValueError):
    pass


def parse_amount(raw: str) -> Decimal:
    """Accept '700', '700.00', '700,00', '1.234,56' and '1,234.56'."""
    s = raw.strip().replace("€", "").replace(" ", "")
    if not s:
        raise ArchiveError("Betrag fehlt.")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        value = Decimal(s)
    except InvalidOperation:
        raise ArchiveError(f"Ungültiger Betrag: {raw!r}") from None
    if not value.is_finite() or value <= 0 or value >= MAX_AMOUNT:
        raise ArchiveError("Betrag muss größer als 0 sein.")
    if value != value.quantize(Decimal("0.01")):
        raise ArchiveError("Betrag darf höchstens zwei Nachkommastellen haben.")
    return value.quantize(Decimal("0.01"))


def slugify(text: str, fallback: str = "Kunde") -> str:
    """'Nordlicht Werkstatt GmbH' -> 'Nordlicht-Werkstatt-GmbH', umlauts transliterated."""
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"), ("ß", "ss")):
        text = text.replace(a, b)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    return slug[:60].rstrip("-") or fallback


def pdf_filename(number: str, customer_name: str) -> str:
    return f"Rechnung_{number}_{slugify(customer_name)}.pdf"


def retain_until(issue_date: date, years: int) -> date:
    # The retention period starts at the end of the calendar year of issue.
    return date(issue_date.year + years, 12, 31)


def next_number(conn: sqlite3.Connection, year: int) -> str:
    highest = 0
    pattern = re.compile(rf"^{year}-(\d+)$")
    for (number,) in conn.execute(
        "SELECT number FROM invoices WHERE number LIKE ?", (f"{year}-%",)
    ):
        m = pattern.match(number)
        if m:
            highest = max(highest, int(m.group(1)))
    return f"{year}-{highest + 1:03d}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_number(conn: sqlite3.Connection, number: str) -> str:
    number = number.strip()
    if not NUMBER_RE.match(number):
        raise ArchiveError(
            "Rechnungsnummer darf nur Buchstaben, Ziffern, '-', '_' und '.' enthalten."
        )
    if conn.execute("SELECT 1 FROM invoices WHERE number = ?", (number,)).fetchone():
        raise ArchiveError(f"Rechnungsnummer {number} ist bereits vergeben.")
    return number


def _write_once(archive_dir: Path, rel_path: str, data: bytes) -> None:
    target = archive_dir / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except FileExistsError:
        raise ArchiveError(f"Archivdatei existiert bereits: {rel_path}") from None
    target.chmod(0o444)


@dataclass(frozen=True)
class InvoiceInput:
    """Validated form input for a new invoice."""

    number: str
    issue_date: date
    service_from: date
    service_to: date | None
    payment_days: int
    customer_name: str
    customer_street: str
    customer_city: str
    title: str
    description: str
    amount: Decimal

    def to_invoice_data(self) -> InvoiceData:
        service = format_date(self.service_from)
        if self.service_to and self.service_to != self.service_from:
            service = f"{service} bis {format_date(self.service_to)}"
        return InvoiceData(
            number=self.number,
            issue_date=self.issue_date,
            service_date=service,
            due_date=self.issue_date + timedelta(days=self.payment_days),
            payment_days=self.payment_days,
            customer_name=self.customer_name,
            customer_street=self.customer_street,
            customer_city=self.customer_city,
            title=self.title,
            description=self.description,
            amount=self.amount,
        )


def _clean(value: str | None, label: str, max_len: int, required: bool = True) -> str:
    value = (value or "").replace("\r\n", "\n").strip()
    if required and not value:
        raise ArchiveError(f"{label} fehlt.")
    if len(value) > max_len:
        raise ArchiveError(f"{label} ist zu lang (max. {max_len} Zeichen).")
    if label != "Beschreibung" and "\n" in value:
        value = " ".join(value.split())
    return value


def parse_date(value: str | None, label: str, required: bool = True) -> date | None:
    value = (value or "").strip()
    if not value:
        if required:
            raise ArchiveError(f"{label} fehlt.")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ArchiveError(f"{label} ist kein gültiges Datum.") from None


def parse_invoice_form(form) -> InvoiceInput:
    issue_date = parse_date(form.get("issue_date"), "Rechnungsdatum")
    service_from = parse_date(form.get("service_from"), "Leistungsdatum")
    service_to = parse_date(form.get("service_to"), "Leistungsdatum bis", required=False)
    if service_to and service_to < service_from:
        raise ArchiveError("Leistungszeitraum endet vor seinem Beginn.")
    try:
        payment_days = int(form.get("payment_days") or 14)
    except ValueError:
        raise ArchiveError("Zahlungsziel muss eine Zahl sein.") from None
    if not 0 <= payment_days <= 120:
        raise ArchiveError("Zahlungsziel muss zwischen 0 und 120 Tagen liegen.")
    return InvoiceInput(
        number=(form.get("number") or "").strip(),
        issue_date=issue_date,
        service_from=service_from,
        service_to=service_to,
        payment_days=payment_days,
        customer_name=_clean(form.get("customer_name"), "Kunde", 80),
        customer_street=_clean(form.get("customer_street"), "Straße", 80),
        customer_city=_clean(form.get("customer_city"), "PLZ und Ort", 80),
        title=_clean(form.get("title"), "Leistungstitel", 200),
        description=_clean(form.get("description"), "Beschreibung", 1000, required=False),
        amount=parse_amount(form.get("amount") or ""),
    )


def issue_invoice(
    conn: sqlite3.Connection,
    archive_dir: Path,
    inp: InvoiceInput,
    sender: Sender,
    retention_years: int,
) -> int:
    """Render, archive and record a new invoice. Returns the invoice id."""
    number = validate_number(conn, inp.number)
    data = inp.to_invoice_data()
    pdf = render_invoice(data, sender)
    rel_path = f"{data.issue_date.year}/{pdf_filename(number, data.customer_name)}"
    payload = {
        "invoice": {k: str(v) for k, v in asdict(data).items()},
        "sender": asdict(sender),
    }
    return _record(
        conn,
        archive_dir,
        rel_path,
        pdf,
        source="generated",
        row={
            "number": number,
            "issue_date": data.issue_date.isoformat(),
            "service_date": data.service_date,
            "due_date": data.due_date.isoformat(),
            "customer_name": data.customer_name,
            "customer_street": data.customer_street,
            "customer_city": data.customer_city,
            "title": data.title,
            "description": data.description,
            "amount_cents": int(data.amount * 100),
            "payload_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            "retain_until": retain_until(data.issue_date, retention_years).isoformat(),
        },
    )


def _record(conn, archive_dir: Path, rel_path: str, pdf: bytes, source: str, row: dict) -> int:
    now = db.now_iso()
    row = {
        **row,
        "source": source,
        "pdf_path": rel_path,
        "pdf_sha256": sha256_bytes(pdf),
        "pdf_size": len(pdf),
        "created_at": now,
        "updated_at": now,
    }
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    written = False
    try:
        cur = conn.execute(f"INSERT INTO invoices ({cols}) VALUES ({marks})", tuple(row.values()))
        invoice_id = cur.lastrowid
        db.add_event(conn, invoice_id, "created" if source == "generated" else "imported",
                     f"sha256={row['pdf_sha256']}")
        _write_once(archive_dir, rel_path, pdf)
        written = True
        conn.commit()
    except BaseException as e:
        conn.rollback()
        if written:
            (archive_dir / rel_path).unlink(missing_ok=True)
        if isinstance(e, sqlite3.IntegrityError):
            raise ArchiveError(f"Rechnung konnte nicht gespeichert werden: {e}") from None
        raise
    return invoice_id


def set_status(conn: sqlite3.Connection, invoice_id: int, status: str,
               paid_date: date | None = None, note: str = "") -> None:
    if status not in ("open", "paid", "cancelled"):
        raise ArchiveError("Unbekannter Status.")
    if status == "paid" and paid_date is None:
        raise ArchiveError("Zahlungsdatum fehlt.")
    note = note.strip()[:500]
    with conn:
        updated = conn.execute(
            "UPDATE invoices SET status = ?, paid_date = ?, updated_at = ? WHERE id = ?",
            (status, paid_date.isoformat() if status == "paid" else None, db.now_iso(), invoice_id),
        ).rowcount
        if not updated:
            raise ArchiveError("Rechnung nicht gefunden.")
        detail = f"paid_date={paid_date.isoformat()}" if status == "paid" else ""
        if note:
            detail = f"{detail} {note}".strip()
        db.add_event(conn, invoice_id, f"status:{status}", detail)


def set_notes(conn: sqlite3.Connection, invoice_id: int, notes: str) -> None:
    notes = notes.replace("\r\n", "\n").strip()[:2000]
    with conn:
        conn.execute("UPDATE invoices SET notes = ?, updated_at = ? WHERE id = ?",
                     (notes, db.now_iso(), invoice_id))
        db.add_event(conn, invoice_id, "notes")


def verify_file(path: Path, sha256: str, missing: str = "PDF fehlt") -> str | None:
    """Return None if the file matches its recorded hash, else a problem description."""
    if not path.is_file():
        return missing
    if sha256_file(path) != sha256:
        return "Prüfsumme stimmt nicht"
    return None


def verify_invoice(archive_dir: Path, row) -> str | None:
    """Return None if the archived PDF matches its recorded hash, else a problem description."""
    return verify_file(archive_dir / row["pdf_path"], row["pdf_sha256"])


def verify_all(conn: sqlite3.Connection, archive_dir: Path) -> list[tuple[str, str]]:
    problems = []
    for row in conn.execute("SELECT number, pdf_path, pdf_sha256 FROM invoices ORDER BY number"):
        problem = verify_invoice(archive_dir, row)
        if problem:
            problems.append((row["number"], problem))
    return problems
