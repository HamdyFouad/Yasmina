"""Generated German invoice layout for the PDF/A-3 ZUGFeRD carrier document.

The layout is rendered with ReportLab using bundled, embedded DejaVu TrueType
fonts, then augmented with PDF/A-3 XMP identification and an sRGB output intent.
That keeps the generated carrier independent of the office/browser stack while
satisfying the font-embedding requirement of PDF/A.
"""

from __future__ import annotations

import base64
from decimal import Decimal
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    StreamObject,
    TextStringObject,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from src.generators.invoice_models import Invoice
from src.models.schemas import q2

__all__ = ["build_branded_pdf"]

_A4_WIDTH = 595
_A4_HEIGHT = 842
_LEFT = 48
_RIGHT = _A4_WIDTH - 48
_BOTTOM = 58
_BRAND_BLUE = (0.05, 0.32, 0.52)
_BRAND_LIGHT = (0.91, 0.95, 0.98)
_WHITE = (1.0, 1.0, 1.0)
_BLACK = (0.0, 0.0, 0.0)
_FONT_DIR = Path(__file__).resolve().parent / "fonts"
_REGULAR_FONT = "YasminaDejaVu"
_BOLD_FONT = "YasminaDejaVu-Bold"

# A compact, freely redistributable sRGB ICC profile (littleCMS sRGB).
_SRGB_ICC = base64.b64decode(
    "AAACTGxjbXMEQAAAbW50clJHQiBYWVogB+oACwAGABQALgAEYWNzcE1TRlQAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAPbWAAEAAAAA0y1sY21zAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAALZGVzYwAAAQgAAAA2Y3BydAAAAUAAAABMd3RwdAAAAYwAAAAUY2hhZAAAAaAAAAAsclhZWgAA"
    "AcwAAAAUYlhZWgAAAeAAAAAUZ1hZWgAAAfQAAAAUclRSQwAAAggAAAAgZ1RSQwAAAggAAAAgYlRSQwAA"
    "AggAAAAgY2hybQAAAigAAAAkbWx1YwAAAAAAAAABAAAADGVuVVMAAAAaAAAAHABzAFIARwBCACAAYgB1"
    "AGkAbAB0AC0AaQBuAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAADAAAAAcAE4AbwAgAGMAbwBwAHkAcgBp"
    "AGcAaAB0ACwAIAB1AHMAZQAgAGYAcgBlAGUAbAB5WFlaIAAAAAAAAPbWAAEAAAAA0y1zZjMyAAAAAAAB"
    "DEIAAAXe///zJQAAB5MAAP2Q///7of///aIAAAPcAADAblhZWiAAAAAAAABvoAAAOPUAAAOQWFlaIAAA"
    "AAAAACSfAAAPhAAAtsNYWVogAAAAAAAAYpcAALeHAAAY2XBhcmEAAAAAAAMAAAACZmYAAPKnAAANWQAA"
    "E9AAAApbY2hybQAAAAAAAwAAAACj1wAAVHsAAEzNAACZmgAAJmYAAA9c"
)


def _money(value: Decimal) -> str:
    text = f"{q2(value):,.2f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def _rate(value: Decimal) -> str:
    percent = value * Decimal(100)
    return f"{percent.normalize():.1f}".replace(".", ",") + " %"


@lru_cache(maxsize=1)
def _register_fonts() -> None:
    pdfmetrics.registerFont(TTFont(_REGULAR_FONT, str(_FONT_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont(_BOLD_FONT, str(_FONT_DIR / "DejaVuSans-Bold.ttf")))


class _Page:
    def __init__(self, document_canvas: canvas.Canvas) -> None:
        self.canvas = document_canvas
        self._has_page = False
        self.reset()

    def reset(self) -> None:
        if self._has_page:
            self.canvas.showPage()
        self._has_page = True
        self.y = _A4_HEIGHT - 54
        self.canvas.setFillColorRGB(*_BLACK)
        self.canvas.setStrokeColorRGB(*_BLACK)
        self.canvas.setFont(_REGULAR_FONT, 9)

    def text(
        self,
        x: float,
        y: float,
        size: float,
        value: str,
        *,
        bold: bool = False,
        color: tuple[float, float, float] = _BLACK,
    ) -> None:
        self.canvas.setFillColorRGB(*color)
        self.canvas.setFont(_BOLD_FONT if bold else _REGULAR_FONT, size)
        self.canvas.drawString(x, y, value)

    def rect(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        color: tuple[float, float, float],
    ) -> None:
        self.canvas.setFillColorRGB(*color)
        self.canvas.rect(x, y, width, height, stroke=0, fill=1)


def _wrap(value: str, width: int) -> list[str]:
    words = value.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _draw_header(page: _Page, invoice: Invoice) -> None:
    page.rect(0, _A4_HEIGHT - 92, _A4_WIDTH, 92, _BRAND_BLUE)
    page.text(_LEFT, _A4_HEIGHT - 49, 20, invoice.seller.name, bold=True, color=_WHITE)
    if invoice.seller.street:
        page.text(_LEFT, _A4_HEIGHT - 65, 9, invoice.seller.street, color=_WHITE)
    if invoice.seller.city:
        line = f"{invoice.seller.postal_code or ''} {invoice.seller.city}".strip()
        page.text(_LEFT, _A4_HEIGHT - 77, 9, line, color=_WHITE)
    page.text(_RIGHT - 170, _A4_HEIGHT - 49, 15, "Rechnung", bold=True, color=_WHITE)
    page.text(_RIGHT - 170, _A4_HEIGHT - 65, 9, invoice.invoice_number, color=_WHITE)
    page.text(
        _RIGHT - 170,
        _A4_HEIGHT - 77,
        9,
        invoice.issue_date.strftime("%d.%m.%Y"),
        color=_WHITE,
    )


def _draw_address_and_meta(page: _Page, invoice: Invoice) -> None:
    y = _A4_HEIGHT - 118
    page.text(_LEFT, y, 8, "Rechnungsempfänger", bold=True)
    y -= 14
    page.text(_LEFT, y, 10, invoice.buyer.name, bold=True)
    y -= 13
    if invoice.buyer.street:
        page.text(_LEFT, y, 9, invoice.buyer.street)
        y -= 12
    if invoice.buyer.city:
        page.text(
            _LEFT,
            y,
            9,
            f"{invoice.buyer.postal_code or ''} {invoice.buyer.city}".strip(),
        )
        y -= 12
    if invoice.buyer.vat_id:
        page.text(_LEFT, y, 9, f"USt-IdNr. {invoice.buyer.vat_id}")
        y -= 12

    meta_x = 345
    meta_y = _A4_HEIGHT - 132
    rows = [
        ("Rechnungsnummer", invoice.invoice_number),
        ("Rechnungsdatum", invoice.issue_date.strftime("%d.%m.%Y")),
        ("Fällig am", invoice.due_date.strftime("%d.%m.%Y")),
    ]
    if invoice.delivery_date:
        rows.append(("Lieferdatum", invoice.delivery_date.strftime("%d.%m.%Y")))
    if invoice.buyer_reference:
        rows.append(("Leitweg-ID", invoice.buyer_reference))
    if invoice.order_reference:
        rows.append(("Bestellung", invoice.order_reference))
    for label, value in rows:
        page.text(meta_x, meta_y, 8, label)
        page.text(meta_x + 82, meta_y, 8, value, bold=True)
        meta_y -= 13

    page.y = min(y, meta_y) - 16


def _draw_table_header(page: _Page) -> None:
    page.rect(_LEFT, page.y - 5, _RIGHT - _LEFT, 18, _BRAND_LIGHT)
    page.text(_LEFT + 4, page.y, 8, "Pos.", bold=True)
    page.text(_LEFT + 40, page.y, 8, "Beschreibung", bold=True)
    page.text(_LEFT + 318, page.y, 8, "Menge", bold=True)
    page.text(_LEFT + 370, page.y, 8, "Einzelpreis", bold=True)
    page.text(_LEFT + 445, page.y, 8, "USt.", bold=True)
    page.text(_LEFT + 485, page.y, 8, "Gesamt", bold=True)
    page.y -= 22


def _draw_lines(page: _Page, invoice: Invoice) -> None:
    _draw_table_header(page)
    for line in invoice.lines:
        wrapped = _wrap(line.name, 48)
        needed = 12 * len(wrapped) + 6
        if page.y - needed < _BOTTOM + 90:
            page.reset()
            page.text(
                _LEFT,
                page.y,
                10,
                f"Rechnung {invoice.invoice_number} — Fortsetzung",
                bold=True,
            )
            page.y -= 24
            _draw_table_header(page)
        line_y = page.y
        page.text(_LEFT + 4, line_y, 8, str(line.line_id))
        for offset, text in enumerate(wrapped):
            page.text(_LEFT + 40, line_y - offset * 11, 8, text)
        page.text(_LEFT + 318, line_y, 8, f"{line.quantity.normalize():f}".replace(".", ","))
        page.text(_LEFT + 370, line_y, 8, _money(line.unit_price))
        page.text(_LEFT + 445, line_y, 8, _rate(line.vat_rate))
        page.text(_LEFT + 485, line_y, 8, _money(line.line_total))
        page.y -= needed


def _draw_totals(page: _Page, invoice: Invoice) -> None:
    if page.y < _BOTTOM + 170:
        page.reset()
        page.text(
            _LEFT,
            page.y,
            10,
            f"Rechnung {invoice.invoice_number} — Fortsetzung",
            bold=True,
        )
        page.y -= 24
    page.y -= 8
    page.text(_LEFT, page.y, 9, "Umsatzsteueraufteilung", bold=True)
    page.y -= 16
    grouped: dict[Decimal, Decimal] = {}
    for line in invoice.lines:
        grouped[line.vat_rate] = grouped.get(line.vat_rate, Decimal("0.00")) + line.line_total
    for rate in sorted(grouped, reverse=True):
        taxable = q2(grouped[rate])
        tax = q2(taxable * rate)
        page.text(_LEFT, page.y, 8, f"{_rate(rate)} auf {_money(taxable)} EUR")
        page.text(_LEFT + 250, page.y, 8, f"USt. {_money(tax)} EUR")
        page.text(_LEFT + 360, page.y, 8, f"Brutto {_money(q2(taxable + tax))} EUR")
        page.y -= 13

    page.y -= 4
    page.text(_LEFT + 300, page.y, 9, "Nettobetrag")
    page.text(_RIGHT - 90, page.y, 9, _money(invoice.total_net) + " EUR", bold=True)
    page.y -= 14
    page.text(_LEFT + 300, page.y, 9, "Umsatzsteuer")
    page.text(_RIGHT - 90, page.y, 9, _money(invoice.total_vat) + " EUR", bold=True)
    page.y -= 14
    page.text(_LEFT + 300, page.y, 10, "Gesamtbetrag", bold=True)
    page.text(_RIGHT - 90, page.y, 10, _money(invoice.total_gross) + " EUR", bold=True)
    page.y -= 24

    if invoice.payment_terms:
        page.text(_LEFT, page.y, 8, invoice.payment_terms)
        page.y -= 12
    if invoice.iban:
        page.text(_LEFT, page.y, 8, f"IBAN: {invoice.iban}")
        page.y -= 12


def _xmp(invoice: Invoice) -> bytes:
    issued = invoice.issue_date.strftime("%Y-%m-%d")
    packet = f"""<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Project Yasmina">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">
   <pdfaid:part>3</pdfaid:part>
   <pdfaid:conformance>B</pdfaid:conformance>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">
   <dc:title><rdf:Alt><rdf:li xml:lang="de-DE">Rechnung {invoice.invoice_number}</rdf:li></rdf:Alt></dc:title>
   <dc:creator><rdf:Seq><rdf:li>{invoice.seller.name}</rdf:li></rdf:Seq></dc:creator>
   <dc:date><rdf:Seq><rdf:li>{issued}</rdf:li></rdf:Seq></dc:date>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">
   <xmp:CreatorTool>Project Yasmina</xmp:CreatorTool>
   <xmp:CreateDate>{issued}T00:00:00Z</xmp:CreateDate>
   <xmp:ModifyDate>{issued}T00:00:00Z</xmp:ModifyDate>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""
    return packet.encode("utf-8")


def _replace_base14_fonts(reader: PdfReader) -> None:
    """Point ReportLab's default font resource at an embedded TrueType font."""

    base14 = {"/Helvetica", "/Helvetica-Bold", "/Times-Roman", "/Courier"}
    for page in reader.pages:
        resources = page.get("/Resources")
        if resources is None:
            continue
        fonts = resources.get_object().get("/Font")
        if fonts is None:
            continue
        font_dict = fonts.get_object()
        embedded = next(
            (
                reference
                for reference in font_dict.values()
                if reference.get_object().get("/Subtype") == "/TrueType"
            ),
            None,
        )
        if embedded is None:
            continue
        for key, reference in list(font_dict.items()):
            if reference.get_object().get("/BaseFont") in base14:
                font_dict[key] = embedded


def _add_pdfa_metadata(pdf_bytes: bytes, invoice: Invoice) -> bytes:
    reader = PdfReader(BytesIO(pdf_bytes))
    _replace_base14_fonts(reader)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.pdf_header = b"%PDF-1.7"
    writer.root_object[NameObject("/Lang")] = TextStringObject("de-DE")
    writer.root_object[NameObject("/MarkInfo")] = DictionaryObject(
        {NameObject("/Marked"): BooleanObject(True)}
    )
    writer.xmp_metadata = _xmp(invoice)
    profile = StreamObject()
    profile.set_data(_SRGB_ICC)
    profile[NameObject("/N")] = NumberObject(3)
    profile_ref = writer._add_object(profile)
    output_intent = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/OutputIntent"),
            NameObject("/S"): NameObject("/GTS_PDFA1"),
            NameObject("/OutputConditionIdentifier"): TextStringObject("sRGB IEC61966-2.1"),
            NameObject("/Info"): TextStringObject("sRGB IEC61966-2.1"),
            NameObject("/DestOutputProfile"): profile_ref,
        }
    )
    writer.root_object[NameObject("/OutputIntents")] = ArrayObject([writer._add_object(output_intent)])
    writer.generate_file_identifiers()
    writer.add_metadata(
        {
            "/Title": f"Rechnung {invoice.invoice_number}",
            "/Author": invoice.seller.name,
            "/Subject": f"Rechnung vom {invoice.issue_date.isoformat()}",
            "/Creator": "Project Yasmina invoice template",
            "/Producer": "Project Yasmina",
        }
    )
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def build_branded_pdf(invoice: Invoice) -> bytes:
    """Render the branded PDF/A-3 carrier with embedded fonts."""

    _register_fonts()
    buffer = BytesIO()
    document = canvas.Canvas(buffer, pagesize=(_A4_WIDTH, _A4_HEIGHT), pageCompression=1)
    document.setTitle(f"Rechnung {invoice.invoice_number}")
    document.setAuthor(invoice.seller.name)
    document.setSubject(f"Rechnung vom {invoice.issue_date.isoformat()}")
    document.setCreator("Project Yasmina invoice template")
    document.setProducer("Project Yasmina")
    page = _Page(document)
    _draw_header(page, invoice)
    _draw_address_and_meta(page, invoice)
    _draw_lines(page, invoice)
    _draw_totals(page, invoice)
    document.save()
    return _add_pdfa_metadata(buffer.getvalue(), invoice)
