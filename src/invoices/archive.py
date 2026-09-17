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
from .pdf import SMALL_BUSINESS_NOTE, InvoiceData, Sender, format_date, render_invoice

NUMBER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
SCHEME_RE = re.compile(r"^(\d{4})-(\d{3,})$")
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


def number_problem(conn: sqlite3.Connection, number: str, issue_date: date) -> str | None:
    """Why a new invoice number breaks the running sequence (GoBD Rz. 50), or None.

    A number fits if it follows `YYYY-NNN`, its year is the issue year, and it leaves no gap
    after the highest number issued so far. Filling an existing gap is allowed.
    """
    m = SCHEME_RE.match(number)
    if not m:
        return f"Rechnung {number} passt nicht zum Nummernschema JJJJ-NNN."
    if int(m.group(1)) != issue_date.year:
        return f"Rechnung {number} passt nicht zum Rechnungsdatum {format_date(issue_date)}."
    expected = next_number(conn, int(m.group(1)))
    if int(m.group(2)) > int(expected.split("-")[1]):
        return f"Rechnung {number} lässt eine Lücke im Nummernkreis {m.group(1)} (nächste Nummer: {expected})."
    return None


def number_gaps(conn: sqlite3.Connection) -> dict[str, dict]:
    """Gap analysis of the invoice numbers (GoBD Rz. 40 Lückenanalyse, Rz. 50).

    Returns only years with findings, newest first:
    {year: {"missing": [numbers], "irregular": [(number, problem)]}}.
    Gaps are found per number year between 1 and the highest issued number; cancelled invoices
    keep their number. Irregular numbers (outside `YYYY-NNN`, or a year other than the issue
    year) are listed under their issue year. Duplicates cannot exist (UNIQUE).
    """
    found: dict[str, dict] = {}

    def year(y: str) -> dict:
        return found.setdefault(y, {"missing": [], "irregular": []})

    used: dict[str, set[int]] = {}
    for row in conn.execute("SELECT number, issue_date FROM invoices ORDER BY issue_date, number"):
        issue_year = row["issue_date"][:4]
        m = SCHEME_RE.match(row["number"])
        if not m:
            year(issue_year)["irregular"].append((row["number"], "passt nicht zum Nummernschema JJJJ-NNN"))
            continue
        used.setdefault(m.group(1), set()).add(int(m.group(2)))
        if m.group(1) != issue_year:
            year(issue_year)["irregular"].append(
                (row["number"], f"passt nicht zum Rechnungsdatum {_de(row['issue_date'])}"))
    for y, numbers in used.items():
        missing = [f"{y}-{n:03d}" for n in range(1, max(numbers)) if n not in numbers]
        if missing:
            year(y)["missing"] = missing
    return dict(sorted(found.items(), reverse=True))


def number_findings(conn: sqlite3.Connection) -> list[str]:
    """number_gaps as German messages, consecutive missing numbers joined into ranges."""
    messages = []
    for y, result in number_gaps(conn).items():
        ranges: list[list[str]] = []
        previous = None
        for number in result["missing"]:
            n = int(number.split("-")[1])
            if previous is not None and n == previous + 1:
                ranges[-1][1] = number
            else:
                ranges.append([number, number])
            previous = n
        if ranges:
            gaps = ", ".join(a if a == b else f"{a} bis {b}" for a, b in ranges)
            messages.append(f"Nummernkreis {y}: Lücke bei {gaps}")
        messages += [f"Rechnung {number} {problem}" for number, problem in result["irregular"]]
    return messages


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
    number_reason: str = ""  # why a number outside the running sequence is used on purpose

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
        number_reason=_number_reason(form),
    )


def _number_reason(form) -> str:
    if not form.get("number_override"):
        return ""
    return _clean(form.get("number_reason"), "Grund für die abweichende Nummer", 200)


def issue_invoice(
    conn: sqlite3.Connection,
    archive_dir: Path,
    inp: InvoiceInput,
    sender: Sender,
    retention_years: int,
) -> int:
    """Render, archive and record a new invoice. Returns the invoice id."""
    number = validate_number(conn, inp.number)
    detail = ""
    problem = number_problem(conn, number, inp.issue_date)
    if problem:
        if not inp.number_reason:
            raise ArchiveError(f"{problem} Um sie trotzdem zu verwenden, „Abweichende Nummer bewusst "
                               "verwenden“ ankreuzen und einen Grund angeben.")
        detail = f"Abweichende Nummer: {problem} Grund: {quote(inp.number_reason)}"
    data = inp.to_invoice_data()
    pdf = render_invoice(data, sender)
    rel_path = f"{data.issue_date.year}/{pdf_filename(number, data.customer_name)}"
    payload = {
        "invoice": {k: str(v) for k, v in asdict(data).items()},
        "sender": asdict(sender),
        # Fixed texts printed on this PDF; they change over time (e.g. the § 19 note in 2026).
        "texts": {"small_business_note": SMALL_BUSINESS_NOTE},
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
        detail=detail,
    )


def _record(conn, archive_dir: Path, rel_path: str, pdf: bytes, source: str, row: dict,
            detail: str = "") -> int:
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
                     "; ".join(filter(None, [f"sha256={row['pdf_sha256']}", detail])))
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


STATUS_NAMES = {"open": "Offen", "paid": "Bezahlt", "cancelled": "Storniert"}
# GoBD Rz. 79. '' = unknown (recorded before the payment method existed).
PAYMENT_METHODS = {"bank": "Überweisung/Karte", "cash": "Bar", "private": "Privat bezahlt (Einlage)"}


def payment_label(method: str | None) -> str:
    return PAYMENT_METHODS.get(method or "", "–")


def quote(text: str) -> str:
    return f"„{text}“" if text else "–"


def set_status(conn: sqlite3.Connection, invoice_id: int, status: str,
               paid_date: date | None = None, note: str = "", payment_method: str = "") -> None:
    """Change the payment status. The event records old and new values (GoBD Rz. 58).
    Payment date and method belong to the payment: they are cleared when it is undone."""
    if status not in STATUS_NAMES:
        raise ArchiveError("Unbekannter Status.")
    if status == "paid" and paid_date is None:
        raise ArchiveError("Zahlungsdatum fehlt.")
    # Money received on a private account is still a bank receipt; "private" only fits expenses.
    if status == "paid" and payment_method not in ("bank", "cash"):
        raise ArchiveError("Zahlungsart fehlt.")
    note = note.replace("\r\n", "\n").strip()
    if len(note) > 500:
        raise ArchiveError("Grund ist zu lang (max. 500 Zeichen).")
    if status == "cancelled" and not note:
        raise ArchiveError("Grund für die Stornierung fehlt.")
    new_paid = paid_date.isoformat() if status == "paid" else None
    new_method = payment_method if status == "paid" else ""
    with conn:
        row = conn.execute("SELECT status, paid_date, payment_method FROM invoices WHERE id = ?",
                           (invoice_id,)).fetchone()
        if row is None:
            raise ArchiveError("Rechnung nicht gefunden.")
        conn.execute("UPDATE invoices SET status = ?, paid_date = ?, payment_method = ?, updated_at = ? "
                     "WHERE id = ?", (status, new_paid, new_method, db.now_iso(), invoice_id))
        changes = [f"Status: {STATUS_NAMES[row['status']]} → {STATUS_NAMES[status]}"]
        if row["paid_date"] != new_paid:
            changes.append(f"Bezahlt am: {_de(row['paid_date'])} → {_de(new_paid)}")
        if row["payment_method"] != new_method:
            changes.append(f"Zahlungsart: {payment_label(row['payment_method'])} → {payment_label(new_method)}")
        if note:
            changes.append(f"Grund: {quote(note)}")
        db.add_event(conn, invoice_id, f"status:{status}", "; ".join(changes))


def _de(iso: str | None) -> str:
    return format_date(date.fromisoformat(iso)) if iso else "–"


def set_notes(conn: sqlite3.Connection, invoice_id: int, notes: str) -> None:
    notes = notes.replace("\r\n", "\n").strip()
    if len(notes) > 2000:
        raise ArchiveError("Notiz ist zu lang (max. 2000 Zeichen).")
    with conn:
        row = conn.execute("SELECT notes FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if row is None:
            raise ArchiveError("Rechnung nicht gefunden.")
        if row["notes"] == notes:
            return
        conn.execute("UPDATE invoices SET notes = ?, updated_at = ? WHERE id = ?",
                     (notes, db.now_iso(), invoice_id))
        db.add_event(conn, invoice_id, "notes", f"Notiz: {quote(row['notes'])} → {quote(notes)}")


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
