"""Received e-invoices: XRechnung / EN 16931 in UBL 2.1 or UN/CEFACT CII syntax.

The XML is the invoice (GoBD Rz. 118, 119, 125, 131 as amended 14.07.2025): it is archived byte
for byte and only read here, never converted. For hybrid invoices the GoBD only require the XML;
the whole PDF is kept anyway, in case it carries extra tax-relevant information. Reading gives booking suggestions and a readable view (Rz. 156). ZUGFeRD and
Factur-X PDFs carry the same CII XML as an attachment, which `pdfdetach` extracts.

Uploads come from third parties. ElementTree on Python >= 3.11 (bundled expat) resolves no
external entities; documents with a DOCTYPE or entity declaration are refused outright anyway.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import extract
from .archive import ArchiveError

MAX_SIZE = 20 * 1024 * 1024

UBL_INVOICE = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
UBL_CREDIT_NOTE = "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
CII = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
ROOTS = {
    f"{{{UBL_INVOICE}}}Invoice": "UBL",
    f"{{{UBL_CREDIT_NOTE}}}CreditNote": "UBL",
    f"{{{CII}}}CrossIndustryInvoice": "CII",
}
NS = {
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "rsm": CII,
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}
# UNTDID 1001 credit note codes (381 credit note, 396 factored, 532 forwarder's credit note).
CREDIT_NOTE_CODES = {"381", "396", "532"}
# Attachment names used by ZUGFeRD 1/2, Factur-X and XRechnung-in-PDF.
EMBEDDED_NAMES = {"factur-x.xml", "zugferd-invoice.xml", "xrechnung.xml"}
# Common UN/ECE Recommendation 20 unit codes, for display only.
UNITS = {"C62": "Stk.", "H87": "Stk.", "EA": "Stk.", "XPP": "Stk.", "HUR": "Std.", "DAY": "Tage",
         "MON": "Monate", "ANN": "Jahre", "LS": "pauschal", "KGM": "kg", "MTR": "m", "GB": "GB"}
XML_START = re.compile(rb"\A(?:\xef\xbb\xbf)?\s*<(?:\?xml[\s?]|[A-Za-z_])")


class EInvoiceError(ArchiveError):
    pass


@dataclass(frozen=True)
class Line:
    name: str
    quantity: Decimal | None
    unit: str
    net: Decimal | None

    @property
    def unit_label(self) -> str:
        return UNITS.get(self.unit, self.unit)


@dataclass(frozen=True)
class EInvoice:
    syntax: str                      # "UBL" or "CII"
    type_code: str
    credit_note: bool
    number: str = ""
    issue_date: date | None = None
    due_date: date | None = None
    seller: str = ""
    buyer: str = ""
    currency: str = ""
    lines: list[Line] = field(default_factory=list)
    net_total: Decimal | None = None
    tax_total: Decimal | None = None
    gross_total: Decimal | None = None
    payable: Decimal | None = None
    seller_country: str = ""         # ISO 3166-1 alpha-2 of the seller's postal address
    vat_categories: tuple[str, ...] = ()  # UNCL 5305 codes used in the document, e.g. S, AE
    vat_rates: tuple[Decimal, ...] = ()   # percentages stated in the document

    @property
    def amount(self) -> Decimal | None:
        """The amount to book: payable, else gross (prepaid invoices have payable 0). Credit
        notes may carry signed values; the booking schema only knows positive amounts."""
        value = self.payable if self.payable else self.gross_total
        return abs(value) if value else None

    def suggestion(self) -> extract.Suggestion:
        return extract.Suggestion(vendor=self.seller, invoice_number=self.number,
                                  expense_date=self.issue_date, amount=self.amount)

    def as_text(self) -> str:
        """Plain-text rendering for search and the text dialog; not the raw XML."""
        def money(v):
            return f"{_de_number(v, 2)} {self.currency}".strip() if v is not None else "–"

        def day(d):
            return d.strftime("%d.%m.%Y") if d else "–"

        out = [
            f"{'Gutschrift' if self.credit_note else 'Rechnung'} (E-Rechnung {self.syntax}, Typ {self.type_code or '–'})",
            f"Rechnungssteller: {self.seller or '–'}",
            f"Rechnungsempfänger: {self.buyer or '–'}",
            f"Rechnungsnummer: {self.number or '–'}",
            f"Rechnungsdatum: {day(self.issue_date)}",
            f"Fällig am: {day(self.due_date)}",
            f"Währung: {self.currency or '–'}",
        ]
        if self.lines:
            out.append("Positionen:")
            for line in self.lines:
                qty = f"{_de_number(line.quantity)} {line.unit_label}".strip() if line.quantity is not None else ""
                out.append(f"  {qty}  {line.name}  {money(line.net)}")
        out += [f"Summe netto: {money(self.net_total)}", f"Umsatzsteuer: {money(self.tax_total)}",
                f"Gesamtbetrag: {money(self.gross_total)}", f"Zahlbetrag: {money(self.payable)}"]
        return "\n".join(out)


def looks_like_xml(data: bytes) -> bool:
    return bool(XML_START.match(data[:1024]))


def parse(data: bytes) -> EInvoice:
    """Parse an XRechnung/EN 16931 invoice or raise EInvoiceError with a German message."""
    if len(data) > MAX_SIZE:
        raise EInvoiceError("XML-Datei zu groß.")
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise EInvoiceError("XML mit DOCTYPE- oder ENTITY-Deklaration wird nicht angenommen.")
    try:
        # Only the parser sees the copy without BOM and leading whitespace; the archive keeps the original.
        root = ET.fromstring(data.removeprefix(b"\xef\xbb\xbf").lstrip())
    except ET.ParseError:
        raise EInvoiceError("Die XML-Datei ist nicht wohlgeformt.") from None
    syntax = ROOTS.get(root.tag)
    if syntax is None:
        raise EInvoiceError("Die XML-Datei ist keine E-Rechnung (XRechnung/EN 16931 in UBL oder CII).")
    return _ubl(root) if syntax == "UBL" else _cii(root)


def _text(el, path: str) -> str:
    found = el.find(path, NS) if el is not None else None
    return " ".join((found.text or "").split()) if found is not None else ""


def _decimal(value: str) -> Decimal | None:
    try:
        d = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def _cii_date(el, path: str) -> date | None:
    found = el.find(path, NS)
    if found is None or found.get("format", "102") != "102":
        return None
    value = (found.text or "").strip()
    if not re.fullmatch(r"\d{8}", value):
        return None
    return _iso_date(f"{value[:4]}-{value[4:6]}-{value[6:]}")


def _ubl(root) -> EInvoice:
    credit = root.tag.endswith("}CreditNote")
    type_code = _text(root, "cbc:CreditNoteTypeCode" if credit else "cbc:InvoiceTypeCode")

    def party(path):
        p = root.find(f"{path}/cac:Party", NS)
        return _text(p, "cac:PartyLegalEntity/cbc:RegistrationName") or _text(p, "cac:PartyName/cbc:Name")

    lines = []
    for el in root.findall("cac:CreditNoteLine" if credit else "cac:InvoiceLine", NS):
        qty = el.find("cbc:CreditedQuantity" if credit else "cbc:InvoicedQuantity", NS)
        lines.append(Line(
            name=_text(el, "cac:Item/cbc:Name"),
            quantity=_decimal((qty.text or "").strip()) if qty is not None else None,
            unit=qty.get("unitCode", "") if qty is not None else "",
            net=_decimal(_text(el, "cbc:LineExtensionAmount")),
        ))
    totals = "cac:LegalMonetaryTotal/cbc:"
    currency = _text(root, "cbc:DocumentCurrencyCode")
    return EInvoice(
        syntax="UBL",
        type_code=type_code,
        credit_note=credit or type_code in CREDIT_NOTE_CODES,
        number=_text(root, "cbc:ID"),
        issue_date=_iso_date(_text(root, "cbc:IssueDate")),
        due_date=_iso_date(_text(root, "cbc:DueDate") or _text(root, "cac:PaymentMeans/cbc:PaymentDueDate")),
        seller=party("cac:AccountingSupplierParty"),
        buyer=party("cac:AccountingCustomerParty"),
        currency=currency,
        lines=lines,
        net_total=_decimal(_text(root, totals + "TaxExclusiveAmount")),
        tax_total=_decimal(_currency_amount(root.findall("cac:TaxTotal/cbc:TaxAmount", NS), currency)),
        gross_total=_decimal(_text(root, totals + "TaxInclusiveAmount")),
        payable=_decimal(_text(root, totals + "PayableAmount")),
        seller_country=_text(root, "cac:AccountingSupplierParty/cac:Party/cac:PostalAddress/"
                                   "cac:Country/cbc:IdentificationCode").upper(),
        vat_categories=_codes(root.findall(".//cac:TaxCategory/cbc:ID", NS)
                              + root.findall(".//cac:ClassifiedTaxCategory/cbc:ID", NS)),
        vat_rates=_rates(root.findall(".//cac:TaxCategory/cbc:Percent", NS)
                         + root.findall(".//cac:ClassifiedTaxCategory/cbc:Percent", NS)),
    )


def _rates(elements) -> tuple[Decimal, ...]:
    values = {_decimal((el.text or "").strip()) for el in elements}
    return tuple(sorted(v for v in values if v is not None))


def _codes(elements) -> tuple[str, ...]:
    return tuple(sorted({(el.text or "").strip().upper() for el in elements} - {""}))


def _currency_amount(elements, currency: str) -> str:
    """Tax totals may be given twice (invoice and accounting currency): take the invoice one."""
    for el in elements:
        if el.get("currencyID", currency) == currency:
            return (el.text or "").strip()
    return (elements[0].text or "").strip() if elements else ""


def _cii(root) -> EInvoice:
    doc = root.find("rsm:ExchangedDocument", NS)
    trade = root.find("rsm:SupplyChainTradeTransaction", NS)
    if doc is None or trade is None:
        raise EInvoiceError("Die CII-Rechnung ist unvollständig (ExchangedDocument fehlt).")
    agreement = trade.find("ram:ApplicableHeaderTradeAgreement", NS)
    settlement = trade.find("ram:ApplicableHeaderTradeSettlement", NS)
    type_code = _text(doc, "ram:TypeCode")
    currency = _text(settlement, "ram:InvoiceCurrencyCode")
    sums = settlement.find("ram:SpecifiedTradeSettlementHeaderMonetarySummation", NS) \
        if settlement is not None else None

    lines = []
    for el in trade.findall("ram:IncludedSupplyChainTradeLineItem", NS):
        qty = el.find("ram:SpecifiedLineTradeDelivery/ram:BilledQuantity", NS)
        lines.append(Line(
            name=_text(el, "ram:SpecifiedTradeProduct/ram:Name"),
            quantity=_decimal((qty.text or "").strip()) if qty is not None else None,
            unit=qty.get("unitCode", "") if qty is not None else "",
            net=_decimal(_text(el, "ram:SpecifiedLineTradeSettlement/"
                                   "ram:SpecifiedTradeSettlementLineMonetarySummation/ram:LineTotalAmount")),
        ))
    due = None
    if settlement is not None:
        for terms in settlement.findall("ram:SpecifiedTradePaymentTerms", NS):
            due = _cii_date(terms, "ram:DueDateDateTime/udt:DateTimeString")
            if due:
                break
    return EInvoice(
        syntax="CII",
        type_code=type_code,
        credit_note=type_code in CREDIT_NOTE_CODES,
        number=_text(doc, "ram:ID"),
        issue_date=_cii_date(doc, "ram:IssueDateTime/udt:DateTimeString"),
        due_date=due,
        seller=_text(agreement, "ram:SellerTradeParty/ram:Name"),
        buyer=_text(agreement, "ram:BuyerTradeParty/ram:Name"),
        currency=currency,
        lines=lines,
        net_total=_decimal(_text(sums, "ram:TaxBasisTotalAmount")),
        tax_total=_decimal(_currency_amount(sums.findall("ram:TaxTotalAmount", NS), currency)
                           if sums is not None else ""),
        gross_total=_decimal(_text(sums, "ram:GrandTotalAmount")),
        payable=_decimal(_text(sums, "ram:DuePayableAmount")),
        seller_country=_text(agreement, "ram:SellerTradeParty/ram:PostalTradeAddress/ram:CountryID").upper(),
        vat_categories=_codes(trade.findall(".//ram:ApplicableTradeTax/ram:CategoryCode", NS)),
        vat_rates=_rates(trade.findall(".//ram:ApplicableTradeTax/ram:RateApplicablePercent", NS)),
    )


def _de_number(value: Decimal | None, places: int | None = None) -> str:
    if value is None:
        return ""
    if places is None:  # quantities: 2.000 -> "2", 1.50 -> "1,5"
        return f"{value.normalize():f}".replace(".", ",")
    return f"{value:,.{places}f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ── ZUGFeRD / Factur-X ────────────────────────────────────────────────────


def embedded_xml(pdf: bytes, timeout: float = 20) -> bytes | None:
    """The invoice XML attached to a ZUGFeRD/Factur-X PDF, or None (also if pdfdetach is missing)."""
    exe = shutil.which("pdfdetach")
    if not exe:
        return None
    with tempfile.TemporaryDirectory(prefix="pdfdetach-") as tmp:
        src = Path(tmp) / "in.pdf"
        src.write_bytes(pdf)
        listing = _run(extract.limited([exe, "-enc", "UTF-8", "-list", str(src)]), timeout, capture=True)
        if listing is None:
            return None
        number = None
        for line in listing.decode("utf-8", "replace").splitlines():
            m = re.match(r"\s*(\d+):\s*(.+?)\s*$", line)
            if m and m.group(2).lower() in EMBEDDED_NAMES:
                number = m.group(1)
                break
        if number is None:
            return None
        out = Path(tmp) / "invoice.xml"
        if _run(extract.limited([exe, "-save", number, "-o", str(out), str(src)], MAX_SIZE), timeout) is None \
                or not out.is_file():
            return None
        with out.open("rb") as f:
            data = f.read(MAX_SIZE + 1)
    return data if len(data) <= MAX_SIZE else None


def _run(cmd: list[str], timeout: float, capture: bool = False) -> bytes | None:
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    return res.stdout[:extract.MAX_OUTPUT] if capture else b""


def from_pdf(pdf: bytes) -> EInvoice | None:
    """Invoice data from an embedded ZUGFeRD/Factur-X XML; None if there is none or it is unusable."""
    data = embedded_xml(pdf)
    if not data:
        return None
    try:
        return parse(data)
    except EInvoiceError:
        return None
