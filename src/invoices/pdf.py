"""Invoice PDF renderer.

Ported from the original rechnung_branded.py. The canvas drawing model, colours,
fonts, spacing and wave parameters are intentionally unchanged; only the hardcoded
values were replaced by an InvoiceData and a Sender.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Paragraph

FONT_DIR = Path(__file__).parent / "fonts"
FONT_FILES = {
    "Lora": "Lora-Variable.ttf",
    "Lora-Italic": "Lora-Italic-Variable.ttf",
    "Poppins": "Poppins-Regular.ttf",
    "Poppins-Bold": "Poppins-Bold.ttf",
    "Poppins-Light": "Poppins-Light.ttf",
}

# ── Colours (from the brand website) ───────────────────────────────────
BG = colors.HexColor("#EDEAE4")  # warm linen
TEAL = colors.HexColor("#1D3B4A")  # dark teal
GOLD = colors.HexColor("#B8963E")  # gold accent
WHITE = colors.white
LWAV = colors.HexColor("#B8C8CE")  # wave lines (light teal-gray)
MGRAY = colors.HexColor("#5C5C5C")  # secondary text
ROWBG = colors.HexColor("#E4E1DB")  # alternating row bg
FOOT_SUBTEXT = colors.HexColor("#A8B8BE")

# ── Page geometry ──────────────────────────────────────────────────────────
W, H = A4  # 595.28 x 841.89 pt
LM = 20 * mm
RM = 20 * mm
CW = W - LM - RM  # ~155mm usable
FOOT_H = 26 * mm
WAVE_ZONE_Y = FOOT_H + 22 * mm  # top of footer wave transition zone

SMALL_BUSINESS_NOTE = "Gemäß § 19 UStG wird keine Umsatzsteuer berechnet."


class FontsMissingError(RuntimeError):
    pass


class LayoutOverflowError(ValueError):
    """Content does not fit on a single page."""


@dataclass(frozen=True)
class Sender:
    name: str
    tagline: str
    street: str
    city: str
    email: str
    website: str
    tax_number: str
    iban: str
    bic: str
    account_holder: str

    @property
    def address(self) -> str:
        return f"{self.street}, {self.city}"

    @property
    def contact(self) -> str:
        return f"{self.email}  ·  {self.website}"


@dataclass(frozen=True)
class InvoiceData:
    number: str
    issue_date: date
    service_date: str  # free text, e.g. "16.09.2026" or "01.09.2026 bis 15.09.2026"
    due_date: date
    payment_days: int
    customer_name: str
    customer_street: str
    customer_city: str
    title: str
    description: str
    amount: Decimal


def format_date(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def format_amount(amount: Decimal) -> str:
    """Decimal('1234.5') -> '1.234,50 €'"""
    s = f"{amount:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".") + " €"


_fonts_registered = False


def register_fonts() -> None:
    global _fonts_registered
    if _fonts_registered:
        return
    missing = [f for f in FONT_FILES.values() if not (FONT_DIR / f).is_file()]
    if missing:
        raise FontsMissingError(
            f"Fonts fehlen in {FONT_DIR}: {', '.join(missing)}"
        )
    for alias, filename in FONT_FILES.items():
        pdfmetrics.registerFont(TTFont(alias, str(FONT_DIR / filename)))
    _fonts_registered = True


# ── Helper: wave lines ─────────────────────────────────────────────────────
def draw_waves(c, x, y, width, n=3, amp=1.8, wl=22 * mm, gap=3.5):
    c.saveState()
    c.setStrokeColor(LWAV)
    c.setLineWidth(0.6)
    steps = 300
    for i in range(n):
        ly = y - i * gap
        p = c.beginPath()
        for j in range(steps + 1):
            fx = x + (j / steps) * width
            angle = (j / steps) * (width / wl) * 2 * math.pi
            fy = ly + amp * math.sin(angle)
            if j == 0:
                p.moveTo(fx, fy)
            else:
                p.lineTo(fx, fy)
        c.drawPath(p, stroke=1, fill=0)
    c.restoreState()


# ── Helper: footer waves (teal on linen → darker) ─────────────────────────
def draw_footer_waves(c, x, y, width, n=4, amp=3.0, wl=28 * mm, gap=5):
    """Waves that sit in the transition zone between linen and teal footer."""
    c.saveState()
    for i in range(n):
        # Gradually darken from LWAV toward TEAL
        t = i / max(n - 1, 1)
        r = 0xB8 + int((0x1D - 0xB8) * t)
        g = 0xC8 + int((0x3B - 0xC8) * t)
        b = 0xCE + int((0x4A - 0xCE) * t)
        col = colors.Color(r / 255, g / 255, b / 255, alpha=0.6 + 0.4 * t)
        c.setStrokeColor(col)
        c.setLineWidth(0.7 + i * 0.15)
        steps = 300
        ly = y - i * gap
        p = c.beginPath()
        for j in range(steps + 1):
            fx = x + (j / steps) * width
            angle = (j / steps) * (width / wl) * 2 * math.pi + i * 0.8
            fy = ly + amp * math.sin(angle)
            if j == 0:
                p.moveTo(fx, fy)
            else:
                p.lineTo(fx, fy)
        c.drawPath(p, stroke=1, fill=0)
    c.restoreState()


# ── Helper: draw a Paragraph on canvas ────────────────────────────────────
def draw_para(c, text, x, y, max_w, style):
    p = Paragraph(text, style)
    w, h = p.wrapOn(c, max_w, 9999)
    p.drawOn(c, x, y - h)
    return h


def _para_text(text: str) -> str:
    """Paragraph parses markup, so user text is escaped and newlines kept."""
    return escape(text.strip()).replace("\n", "<br/>")


def render_invoice(data: InvoiceData, sender: Sender) -> bytes:
    """Render the invoice and return the PDF bytes."""
    register_fonts()
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4, invariant=1)
    c.setTitle(f"Rechnung {data.number}")
    c.setAuthor(sender.name)
    c.setSubject(f"Rechnung {data.number} an {data.customer_name}")
    c.setCreator("invoice-of-reason")

    # Background
    c.setFillColor(BG)
    c.rect(0, 0, W, H, fill=1, stroke=0)

    cur = H - 15 * mm  # running cursor (top → down)

    # ── LOGO + HEADER ──────────────────────────────────────────────────────
    logo_sz = 13 * mm
    logo_x = LM
    logo_y = cur - logo_sz

    # Logo rounded square
    c.setFillColor(TEAL)
    c.roundRect(logo_x, logo_y, logo_sz, logo_sz, radius=1.8 * mm, fill=1, stroke=0)
    # Initial in logo
    c.setFillColor(WHITE)
    c.setFont("Lora-Italic", 17)
    initial = sender.name.split()[-1][:1].upper() if sender.name.split() else ""
    c.drawCentredString(logo_x + logo_sz / 2, logo_y + 3 * mm, initial)

    # Name beside logo
    name_x = logo_x + logo_sz + 4 * mm
    c.setFillColor(TEAL)
    c.setFont("Lora", 13)
    c.drawString(name_x, logo_y + logo_sz / 2 + 1.5 * mm, sender.name)
    c.setFont("Poppins", 7)
    c.setFillColor(MGRAY)
    c.drawString(name_x, logo_y + logo_sz / 2 - 4.5 * mm, sender.tagline)

    # Sender details top-right
    sender_lines = [
        sender.address,
        sender.contact,
        f"Steuernummer: {sender.tax_number}",
    ]
    c.setFont("Poppins-Light", 7)
    c.setFillColor(MGRAY)
    for i, line in enumerate(sender_lines):
        c.drawRightString(W - RM, cur - 2 * mm - i * 4.5 * mm, line)

    cur = logo_y - 8 * mm

    # ── WAVE SEPARATOR ────────────────────────────────────────────────────
    draw_waves(c, LM, cur, CW, n=3)
    cur -= 12 * mm

    # ── RECIPIENT ─────────────────────────────────────────────────────────
    c.setFillColor(GOLD)
    c.setFont("Poppins", 6.5)
    c.drawString(LM, cur, "AN")
    cur -= 1.5 * mm

    c.setFillColor(TEAL)
    c.setFont("Lora", 10)
    for line in [data.customer_name, data.customer_street, data.customer_city]:
        cur -= 5.5 * mm
        c.drawString(LM, cur, line)

    cur -= 12 * mm

    # ── RECHNUNG TITLE ────────────────────────────────────────────────────
    c.setFillColor(TEAL)
    c.setFont("Lora", 32)
    c.drawString(LM, cur, "Rechnung")

    cur -= 3 * mm
    # Gold divider line
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.2)
    c.line(LM, cur, W - RM, cur)
    cur -= 9 * mm

    # ── META TABLE ────────────────────────────────────────────────────────
    meta_rows = [
        ("Rechnungsnummer", data.number, True),
        ("Rechnungsdatum", format_date(data.issue_date), False),
        ("Liefer-/Leistungsdatum", data.service_date, True),
        (
            "Zahlungsziel",
            f"{data.payment_days} Tage netto, fällig bis {format_date(data.due_date)}",
            False,
        ),
    ]
    row_h = 7 * mm
    for label, value, shaded in meta_rows:
        if shaded:
            c.setFillColor(ROWBG)
            c.rect(LM, cur - row_h, CW, row_h, fill=1, stroke=0)
        c.setFillColor(MGRAY)
        c.setFont("Poppins-Light", 7.5)
        c.drawString(LM + 3 * mm, cur - 4.5 * mm, label)
        c.setFillColor(TEAL)
        font = "Poppins-Bold" if label == "Rechnungsnummer" else "Poppins"
        c.setFont(font, 8)
        c.drawString(LM + 70 * mm, cur - 4.5 * mm, value)
        cur -= row_h

    cur -= 10 * mm

    # ── LEISTUNGEN ────────────────────────────────────────────────────────
    c.setFillColor(GOLD)
    c.setFont("Lora-Italic", 10)
    c.drawString(LM, cur, "Leistungen")
    cur -= 6 * mm

    # Table header
    c.setFillColor(TEAL)
    c.rect(LM, cur - 8 * mm, CW, 8 * mm, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Poppins", 7.5)
    c.drawString(LM + 3 * mm, cur - 5.5 * mm, "Beschreibung")
    c.drawRightString(W - RM - 3 * mm, cur - 5.5 * mm, "Betrag")
    cur -= 8 * mm

    # Position
    title_style = ParagraphStyle(
        "pt", fontName="Lora", fontSize=9, textColor=TEAL, leading=13
    )
    desc_style = ParagraphStyle(
        "pd", fontName="Lora-Italic", fontSize=8.5, textColor=MGRAY, leading=12.5
    )

    title_h = draw_para(
        c, _para_text(data.title), LM + 3 * mm, cur - 6 * mm, CW - 45 * mm, title_style
    )
    desc_h = 0
    if data.description.strip():
        desc_h = draw_para(
            c,
            _para_text(data.description),
            LM + 3 * mm,
            cur - 7.5 * mm - title_h,
            CW - 45 * mm,
            desc_style,
        )

    pos_h = 6 * mm + title_h + desc_h + 4 * mm
    amount_text = format_amount(data.amount)
    # Amount right-aligned, centred vertically in row
    c.setFillColor(TEAL)
    c.setFont("Lora", 9)
    c.drawRightString(W - RM - 3 * mm, cur - pos_h / 2, amount_text)
    cur -= pos_h + 2 * mm

    # Separator line
    c.setStrokeColor(LWAV)
    c.setLineWidth(0.5)
    c.line(LM, cur, W - RM, cur)
    cur -= 7 * mm

    # Gesamtbetrag
    c.setFillColor(ROWBG)
    c.rect(LM, cur - 8 * mm, CW, 8.5 * mm, fill=1, stroke=0)
    c.setFillColor(TEAL)
    c.setFont("Lora", 11)
    c.drawString(LM + 3 * mm, cur - 5.5 * mm, "Gesamtbetrag")
    c.drawRightString(W - RM - 3 * mm, cur - 5.5 * mm, amount_text)
    cur -= 8 * mm

    cur -= 9 * mm

    # ── §19 NOTE ──────────────────────────────────────────────────────────
    c.setFillColor(MGRAY)
    c.setFont("Lora-Italic", 8.5)
    c.drawString(LM, cur, SMALL_BUSINESS_NOTE)
    cur -= 14 * mm

    # ── BANKVERBINDUNG ────────────────────────────────────────────────────
    c.setFillColor(GOLD)
    c.setFont("Lora-Italic", 10)
    c.drawString(LM, cur, "Bankverbindung")
    cur -= 7 * mm

    bank = [
        ("Kontoinhaber", sender.account_holder),
        ("IBAN", sender.iban),
        ("BIC", sender.bic),
    ]
    for label, val in bank:
        c.setFillColor(MGRAY)
        c.setFont("Poppins-Light", 7.5)
        c.drawString(LM, cur, label + ":")
        c.setFillColor(TEAL)
        c.setFont("Poppins", 8)
        c.drawString(LM + 28 * mm, cur, val)
        cur -= 5 * mm

    # The last bank line sits at cur + 5mm; it must stay above the footer waves.
    if cur + 5 * mm < WAVE_ZONE_Y + 6 * mm:
        raise LayoutOverflowError(
            "Titel und Beschreibung sind zu lang für eine Seite. Bitte kürzen."
        )

    # ── FOOTER WAVES + DARK BLOCK ─────────────────────────────────────────
    draw_footer_waves(c, 0, WAVE_ZONE_Y, W, n=4, amp=4.0, wl=32 * mm, gap=5)

    # Dark teal footer block
    c.setFillColor(TEAL)
    c.rect(0, 0, W, FOOT_H, fill=1, stroke=0)

    # Footer content
    c.setFillColor(WHITE)
    c.setFont("Lora", 9)
    c.drawCentredString(W / 2, FOOT_H / 2 + 4.5 * mm, f"{sender.name}  ·  {sender.tagline}")
    c.setFont("Poppins-Light", 6.5)
    c.setFillColor(FOOT_SUBTEXT)
    c.drawCentredString(
        W / 2,
        FOOT_H / 2 - 0.5 * mm,
        f"{sender.address}  ·  Steuernummer: {sender.tax_number}",
    )
    c.drawCentredString(W / 2, FOOT_H / 2 - 5.5 * mm, sender.contact)

    c.save()
    return buf.getvalue()
