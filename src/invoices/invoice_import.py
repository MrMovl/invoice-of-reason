"""One-off import of invoices issued before this program existed (e.g. 2026-001).

Only reachable from the command line, never from the web UI: normal invoices are created with
the form. An import follows the same archive rules as a created invoice (GoBD Rz. 58, 107–111,
119, 131), because it goes through `archive._record`:

- the PDF is stored byte-for-byte as it was sent: no rendering, conversion or rewriting;
- write-once file (exclusive create, fsync, 0444), SHA-256 in the row, row and file committed
  together or not at all;
- identity fields protected by the database triggers from then on, no deletion;
- an `imported` event in the hash chain with the record state, the reason and all checks.

On top of that, an import always needs a reason, refuses files already in the archive, refuses
invoice dates in the future and cross-checks number and amount against the PDF text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from . import archive, extract, system
from .archive import ArchiveError, quote
from .pdf import format_amount

MAX_PDF_SIZE = 20 * 1024 * 1024


@dataclass(frozen=True)
class ImportInput:
    number: str
    issue_date: date
    service_date: str  # as printed on the invoice
    due_date: date | None
    customer_name: str
    customer_street: str
    customer_city: str
    title: str
    description: str
    amount: Decimal
    reason: str
    original_filename: str


def parse_import(values: dict) -> ImportInput:
    """Validate raw values (e.g. CLI arguments) with the same rules as the invoice form."""
    issue_date = archive.parse_date(values.get("issue_date"), "Rechnungsdatum")
    if issue_date > date.today():
        raise ArchiveError("Rechnungsdatum liegt in der Zukunft.")
    due_date = archive.parse_date(values.get("due_date"), "Fälligkeitsdatum", required=False)
    if due_date and due_date < issue_date:
        raise ArchiveError("Fälligkeitsdatum liegt vor dem Rechnungsdatum.")
    return ImportInput(
        number=(values.get("number") or "").strip(),
        issue_date=issue_date,
        service_date=archive._clean(values.get("service_date"), "Leistungsdatum", 60),
        due_date=due_date,
        customer_name=archive._clean(values.get("customer_name"), "Kunde", 80),
        customer_street=archive._clean(values.get("customer_street"), "Straße", 80, required=False),
        customer_city=archive._clean(values.get("customer_city"), "PLZ und Ort", 80, required=False),
        title=archive._clean(values.get("title"), "Leistungstitel", 200),
        description=archive._clean(values.get("description"), "Beschreibung", 1000, required=False),
        amount=archive.parse_amount(values.get("amount") or ""),
        reason=archive._clean(values.get("reason"), "Grund für den Import", 500),
        original_filename=(values.get("original_filename") or "").replace("\\", "/").rsplit("/", 1)[-1][:200],
    )


def check_pdf(pdf: bytes, inp: ImportInput) -> list[str]:
    """Plausibility warnings from the PDF text layer. An empty list means nothing stood out."""
    text = extract.pdf_text(pdf)
    if not text.strip():
        return ["Die PDF hat keine lesbare Textebene; Nummer und Betrag konnten nicht abgeglichen werden."]
    warnings = []
    if inp.number not in text:
        warnings.append(f"Rechnungsnummer {inp.number} steht nicht im PDF-Text.")
    flat = re.sub(r"\s+", "", text)
    german = format_amount(inp.amount).replace(" €", "")
    plain = f"{inp.amount:.2f}"
    if german not in flat and german.replace(".", "") not in flat and plain not in flat:
        warnings.append(f"Betrag {format_amount(inp.amount)} steht nicht im PDF-Text.")
    return warnings


def import_invoice(conn, archive_dir, pdf: bytes, inp: ImportInput, retention_years: int,
                   accepted_warnings: list[str] | None = None) -> int:
    """Archive an externally issued invoice PDF unchanged. Returns the invoice id.

    `accepted_warnings` must equal `check_pdf(pdf, inp)`: warnings are never skipped silently,
    the caller has to have shown them and they are logged with the import.
    """
    if not pdf:
        raise ArchiveError("Die Datei ist leer.")
    if len(pdf) > MAX_PDF_SIZE:
        raise ArchiveError("Datei zu groß (max. 20 MB).")
    if not pdf.startswith(b"%PDF-"):
        raise ArchiveError("Die Datei ist keine PDF.")
    number = archive.validate_number(conn, inp.number)
    sha = archive.sha256_bytes(pdf)
    if conn.execute("SELECT 1 FROM invoices WHERE pdf_sha256 = ?", (sha,)).fetchone():
        raise ArchiveError("Diese PDF ist bereits als Rechnung archiviert.")
    if conn.execute("SELECT 1 FROM expenses WHERE doc_sha256 = ?", (sha,)).fetchone():
        raise ArchiveError("Diese PDF ist bereits als Beleg (Ausgabe) archiviert.")
    warnings = check_pdf(pdf, inp)
    if warnings != (accepted_warnings or []):
        raise ArchiveError("Hinweise zur PDF nicht bestätigt: " + " ".join(warnings))

    details = [f"Import: Grund: {quote(inp.reason)}"]
    if inp.original_filename:
        details.append(f"Originaldatei: {quote(inp.original_filename)}")
    problem = archive.number_problem(conn, number, inp.issue_date)
    if problem:
        details.append(f"Abweichende Nummer: {problem}")
    if warnings:
        details.append("Bestätigte Hinweise: " + " ".join(warnings))

    payload = {
        "import": {
            "reason": inp.reason,
            "original_filename": inp.original_filename,
            "warnings_accepted": warnings,
            "app_version": system.app_version(),
            "channel": "cli",
        },
        # The PDF is the original; its sender data is whatever it shows. Nothing is re-rendered.
        "sender": None,
    }
    rel_path = f"{inp.issue_date.year}/{archive.pdf_filename(number, inp.customer_name)}"
    return archive._record(
        conn,
        archive_dir,
        rel_path,
        pdf,
        source="imported",
        row={
            "number": number,
            "issue_date": inp.issue_date.isoformat(),
            "service_date": inp.service_date,
            "due_date": inp.due_date.isoformat() if inp.due_date else None,
            "customer_name": inp.customer_name,
            "customer_street": inp.customer_street,
            "customer_city": inp.customer_city,
            "title": inp.title,
            "description": inp.description,
            "amount_cents": int(inp.amount * 100),
            "payload_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            "retain_until": archive.retain_until(inp.issue_date, retention_years).isoformat(),
        },
        detail="; ".join(details),
    )


def summary(inp: ImportInput, warnings: list[str]) -> str:
    lines = [
        f"Rechnungsnummer:  {inp.number}",
        f"Rechnungsdatum:   {inp.issue_date:%d.%m.%Y}",
        f"Leistungsdatum:   {inp.service_date}",
        f"Fällig:           {inp.due_date:%d.%m.%Y}" if inp.due_date else "Fällig:           –",
        f"Kunde:            {inp.customer_name}",
        f"                  {inp.customer_street}",
        f"                  {inp.customer_city}",
        f"Leistung:         {inp.title}",
        f"Beschreibung:     {inp.description or '–'}",
        f"Betrag:           {format_amount(inp.amount)}",
        f"Grund:            {inp.reason}",
        f"Datei:            {inp.original_filename}",
    ]
    if warnings:
        lines += ["", "HINWEISE:"] + [f"  - {w}" for w in warnings]
    return "\n".join(lines)
