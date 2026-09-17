"""Suggest booking data for an uploaded expense from the text layer of its PDF.

Uses poppler's `pdftotext`. Scans and photos have no text layer and get no suggestion.
Suggestions are heuristics: every expense stays marked "zu prüfen" until it was saved once.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

MAX_TEXT = 50_000
MAX_PAGES = 5
# Uploaded PDFs come from third parties and poppler is C code: cap what one run may use.
MAX_MEMORY = 256 * 1024 * 1024
MAX_OUTPUT = 1024 * 1024


@dataclass(frozen=True)
class Suggestion:
    vendor: str = ""
    invoice_number: str = ""
    expense_date: date | None = None
    amount: Decimal | None = None

    def as_dict(self) -> dict:
        return {
            "vendor": self.vendor,
            "invoice_number": self.invoice_number,
            "expense_date": self.expense_date.isoformat() if self.expense_date else None,
            "amount": str(self.amount) if self.amount is not None else None,
        }


def limited(cmd: list[str], max_output: int | None = None) -> list[str]:
    """Wrap a poppler command in prlimit (if available) to cap memory and written file size."""
    limit = shutil.which("prlimit")
    if limit:
        return [limit, f"--as={MAX_MEMORY}", f"--fsize={max_output or MAX_OUTPUT}", "--", *cmd]
    return cmd


def pdf_text(data: bytes, timeout: float = 20) -> str:
    """Text of the first pages, or '' if there is no text layer or pdftotext is unavailable."""
    exe = shutil.which("pdftotext")
    if not exe:
        return ""
    with tempfile.TemporaryDirectory(prefix="pdftext-") as tmp:
        out = Path(tmp) / "text.txt"
        cmd = limited([exe, "-layout", "-enc", "UTF-8", "-f", "1", "-l", str(MAX_PAGES), "-", str(out)])
        try:
            res = subprocess.run(cmd, input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if res.returncode != 0 or not out.is_file():
            return ""
        with out.open("rb") as f:
            raw = f.read(MAX_OUTPUT)
    return raw.decode("utf-8", "replace").replace("\f", "\n")[:MAX_TEXT]


# ── Amount ────────────────────────────────────────────────────────────────

# 119,00  1.234,56  1,234.56  1234.56 -- but not parts of dates (16.09.2026) or percentages.
# A plain space is no thousands separator: layout text puts "1 119,00" in neighbouring columns.
AMOUNT_RE = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:[.,\u00a0']\d{3})+|\d+)([.,])(\d{2})(?![.,]?\d)(?!\s*%)"
)
TOTAL_KEYS = [
    (3, re.compile(r"zahlbetrag|zu zahlen|zahlungsbetrag|rechnungsbetrag|endbetrag|gesamtbetrag|"
                   r"bruttobetrag|summe brutto|bruttosumme|gesamtsumme|gesamtpreis|amount due|total due|"
                   r"grand total|total amount")),
    (2, re.compile(r"\bgesamt\b|\btotal\b|\bsumme\b|\bbrutto\b")),
    (1, re.compile(r"\bbetrag\b|\bamount\b")),
]
NOT_TOTAL = re.compile(r"netto|zwischensumme|subtotal|mwst|ust\b|umsatzsteuer|mehrwertsteuer|steuer|"
                       r"\bvat\b|\btax\b|rabatt|skonto|anzahlung|bereits bezahlt")
GROSS_HINT = re.compile(r"brutto|inkl|incl")
CURRENCY = re.compile(r"€|\beur\b", re.I)


def _amounts(line: str) -> list[Decimal]:
    found = []
    for m in AMOUNT_RE.finditer(line):
        whole, sep, cents = m.groups()
        if sep in whole:  # 1.234.56 or 1,234,56: separators must differ
            continue
        found.append(Decimal(f"{re.sub(r'[^0-9]', '', whole)}.{cents}"))
    return [a for a in found if a > 0]


def find_amount(lines: list[str]) -> Decimal | None:
    best = None  # (score, line index, amount)
    for i, line in enumerate(lines):
        low = line.lower()
        score = next((w for w, rx in TOTAL_KEYS if rx.search(low)), 0)
        if not score or (NOT_TOTAL.search(low) and not GROSS_HINT.search(low)):
            continue
        amounts = _amounts(line)
        if not amounts and i + 1 < len(lines):
            amounts = _amounts(lines[i + 1])
        if amounts and (best is None or (score, i) >= best[:2]):
            best = (score, i, amounts[-1])
    if best:
        return best[2]
    # No labelled total: take the largest amount written with a currency.
    with_currency = [a for line in lines if CURRENCY.search(line) for a in _amounts(line)]
    return max(with_currency, default=None)


# ── Date ──────────────────────────────────────────────────────────────────

MONTHS = {
    "jan": 1, "feb": 2, "mär": 3, "mar": 3, "apr": 4, "mai": 5, "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "okt": 10, "oct": 10, "nov": 11, "dez": 12, "dec": 12,
}
DATE_RES = [
    (re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?!\d)"), "dmy"),
    (re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"), "ymd"),
    (re.compile(r"(?<!\d)(\d{1,2})\.?\s+([a-zä]{3})[a-zä]*\.?\s+(\d{4})(?!\d)", re.I), "dMy"),
    (re.compile(r"(?<![a-zä])([a-zä]{3})[a-zä]*\.?\s+(\d{1,2}),?\s+(\d{4})(?!\d)", re.I), "Mdy"),
]
INVOICE_DATE_KEY = re.compile(r"rechnungsdatum|belegdatum|ausstellungsdatum|invoice date|date of issue")
DATE_KEY = re.compile(r"datum|\bdate\b")
OTHER_DATE_KEY = re.compile(r"fällig|zahlbar|due|leistung|liefer|zeitraum|bis\b|geburt")


def _dates(line: str) -> list[date]:
    found = []
    for rx, order in DATE_RES:
        for m in rx.finditer(line):
            a, b, c = m.groups()
            try:
                if order == "dmy":
                    year = int(c) + (2000 if len(c) == 2 else 0)
                    found.append((m.start(), date(year, int(b), int(a))))
                elif order == "ymd":
                    found.append((m.start(), date(int(a), int(b), int(c))))
                elif order == "dMy" and b.lower() in MONTHS:
                    found.append((m.start(), date(int(c), MONTHS[b.lower()], int(a))))
                elif order == "Mdy" and a.lower() in MONTHS:
                    found.append((m.start(), date(int(c), MONTHS[a.lower()], int(b))))
            except ValueError:
                continue
    return [d for _, d in sorted(found) if 2000 <= d.year <= 2100]


def find_date(lines: list[str]) -> date | None:
    for key, skip_other in ((INVOICE_DATE_KEY, False), (DATE_KEY, True)):
        for i, line in enumerate(lines):
            low = line.lower()
            if not key.search(low) or (skip_other and OTHER_DATE_KEY.search(low)):
                continue
            dates = _dates(line) or (_dates(lines[i + 1]) if i + 1 < len(lines) else [])
            if dates:
                return dates[0]
    for line in lines:
        if not OTHER_DATE_KEY.search(line.lower()):
            dates = _dates(line)
            if dates:
                return dates[0]
    return None


# ── Invoice number and vendor ─────────────────────────────────────────────

NUMBER_RE = re.compile(
    r"(?:rechnungs-?\s*(?:nummer|nr\.?)|rechnung\s+(?:nr\.?|#)|beleg-?\s*(?:nummer|nr\.?)|"
    r"invoice\s*(?:no\.?|number|#))\s*[:#.]?\s*([A-Za-z0-9][A-Za-z0-9/._-]{1,39})",
    re.I,
)
LEGAL_FORM = re.compile(
    r"\b(?:GmbH|gGmbH|AG|UG|KG|OHG|GbR|SE|e\.\s?K\.|e\.\s?V\.|Ltd\.?|Limited|Inc\.?|LLC|LLP|PLC|"
    r"Corp\.?|Corporation|B\.V\.|S\.A\.|S\.à r\.l\.)(?!\w)"
)
DOC_TITLE = re.compile(r"rechnung|invoice|receipt|quittung|beleg|gutschrift", re.I)
SEGMENT_SPLIT = re.compile(r"\s{2,}|\s[·•|,]\s|\t")


def find_number(text: str) -> str:
    for m in NUMBER_RE.finditer(text):
        token = m.group(1).rstrip("./-")
        if any(ch.isdigit() for ch in token):
            return token
    return ""


def find_vendor(lines: list[str]) -> str:
    segments = [s.strip() for line in lines for s in SEGMENT_SPLIT.split(line) if s.strip()]
    for seg in segments:
        forms = list(LEGAL_FORM.finditer(seg))
        if forms and len(seg) <= 120:
            return seg[: forms[-1].end()].strip()
    for seg in segments[:8]:
        if sum(ch.isalpha() for ch in seg) >= 3 and not DOC_TITLE.search(seg) and len(seg) <= 120:
            return seg
    return ""


def suggest(text: str) -> Suggestion:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not lines:
        return Suggestion()
    return Suggestion(
        vendor=find_vendor(lines),
        invoice_number=find_number(text),
        expense_date=find_date(lines),
        amount=find_amount(lines),
    )
