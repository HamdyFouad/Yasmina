"""Factur-X / ZUGFeRD 2.2 hybrid PDF/A-3 invoice generator.

The CII XML is generated locally and embedded with the ``factur-x`` library.
A caller can supply a real PDF/A-3 base document; when omitted a minimal blank
PDF is used, which is suitable for tests and archival prototypes but should be
replaced by the application's PDF/A-3 renderer in production.
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from pathlib import Path

from lxml import etree

from src.generators.invoice_models import Invoice, InvoiceParty
from src.models.enums import UnitOfMeasure
from src.models.schemas import q2

__all__ = ["InvoiceGenerationError", "ZUGFeRDGenerator", "build_cii_xml"]

CII_RSM = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
CII_RAM = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
CII_QDT = "urn:un:unece:uncefact:data:standard:QualifiedDataType:100"
CII_UDT = "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"

FACTURX_GUIDELINE = "urn:cen.eu:en16931:2017"
FACTURX_LEVEL = "en16931"

_UNIT_CODES = {
    UnitOfMeasure.STUECK: "C62",
    UnitOfMeasure.PACK: "C62",
    UnitOfMeasure.KARTON: "C62",
    UnitOfMeasure.PALETTE: "C62",
    UnitOfMeasure.ROLLE: "C62",
    UnitOfMeasure.KILOGRAMM: "KGM",
    UnitOfMeasure.LITER: "LTR",
    UnitOfMeasure.METER: "MTR",
    UnitOfMeasure.QUADRATMETER: "MTK",
    UnitOfMeasure.NOT_APPLICABLE: "C62",
}

_NSMAP = {"rsm": CII_RSM, "ram": CII_RAM, "qdt": CII_QDT, "udt": CII_UDT}


class InvoiceGenerationError(ValueError):
    """Raised when invoice data cannot be serialized into a valid e-invoice."""


def _qn(prefix: str, local: str) -> str:
    return f"{{{_NSMAP[prefix]}}}{local}"


def _sub(parent: etree._Element, prefix: str, local: str, text: str | None = None, **attrs: str) -> etree._Element:
    element = etree.SubElement(parent, _qn(prefix, local), **attrs)
    if text is not None:
        element.text = text
    return element


def _amount(value: Decimal) -> str:
    return f"{value:.2f}"


def _quantity(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _rate(value: Decimal) -> str:
    return format((value * Decimal(100)).normalize(), "f")


def _date_102(value) -> str:
    return value.strftime("%Y%m%d")


def _party(parent: etree._Element, party: InvoiceParty, *, role: str) -> None:
    if not party.street or not party.city or not party.postal_code:
        raise InvoiceGenerationError(f"{role} address requires street, city and postal code")

    element = _sub(parent, "ram", "SellerTradeParty" if role == "seller" else "BuyerTradeParty")
    _sub(element, "ram", "Name", party.name)
    address = _sub(element, "ram", "PostalTradeAddress")
    _sub(address, "ram", "PostcodeCode", party.postal_code)
    _sub(address, "ram", "LineOne", party.street)
    _sub(address, "ram", "CityName", party.city)
    _sub(address, "ram", "CountryID", party.country_code.upper())
    if party.vat_id:
        registration = _sub(element, "ram", "SpecifiedTaxRegistration")
        _sub(registration, "ram", "ID", party.vat_id, schemeID="VA")


def build_cii_xml(invoice: Invoice) -> bytes:
    """Build a Factur-X EN16931 CII XML document."""

    if invoice.currency.value != "EUR":
        raise InvoiceGenerationError("Factur-X/ZUGFeRD generator requires EUR")

    root = etree.Element(_qn("rsm", "CrossIndustryInvoice"), nsmap=_NSMAP)
    context = _sub(root, "rsm", "ExchangedDocumentContext")
    guideline = _sub(context, "ram", "GuidelineSpecifiedDocumentContextParameter")
    _sub(guideline, "ram", "ID", FACTURX_GUIDELINE)

    document = _sub(root, "rsm", "ExchangedDocument")
    _sub(document, "ram", "ID", invoice.invoice_number)
    _sub(document, "ram", "TypeCode", "380")
    issue = _sub(document, "ram", "IssueDateTime")
    _sub(issue, "udt", "DateTimeString", _date_102(invoice.issue_date), format="102")

    transaction = _sub(root, "rsm", "SupplyChainTradeTransaction")

    for line in invoice.lines:
        line_el = _sub(transaction, "ram", "IncludedSupplyChainTradeLineItem")
        line_doc = _sub(line_el, "ram", "AssociatedDocumentLineDocument")
        _sub(line_doc, "ram", "LineID", str(line.line_id))
        product = _sub(line_el, "ram", "SpecifiedTradeProduct")
        _sub(product, "ram", "Name", line.name)
        if line.description:
            _sub(product, "ram", "Description", line.description)

        agreement = _sub(line_el, "ram", "SpecifiedLineTradeAgreement")
        net_price = _sub(agreement, "ram", "NetPriceProductTradePrice")
        _sub(net_price, "ram", "ChargeAmount", _amount(line.unit_price))

        delivery = _sub(line_el, "ram", "SpecifiedLineTradeDelivery")
        _sub(
            delivery,
            "ram",
            "BilledQuantity",
            _quantity(line.quantity),
            unitCode=_UNIT_CODES[line.unit],
        )

        settlement = _sub(line_el, "ram", "SpecifiedLineTradeSettlement")
        tax = _sub(settlement, "ram", "ApplicableTradeTax")
        _sub(tax, "ram", "TypeCode", "VAT")
        _sub(tax, "ram", "CategoryCode", "S")
        _sub(tax, "ram", "RateApplicablePercent", _rate(line.vat_rate))
        summation = _sub(settlement, "ram", "SpecifiedTradeSettlementLineMonetarySummation")
        _sub(summation, "ram", "LineTotalAmount", _amount(line.line_total))

    header_agreement = _sub(transaction, "ram", "ApplicableHeaderTradeAgreement")
    if invoice.buyer_reference:
        _sub(header_agreement, "ram", "BuyerReference", invoice.buyer_reference)
    _party(header_agreement, invoice.seller, role="seller")
    _party(header_agreement, invoice.buyer, role="buyer")
    if invoice.order_reference:
        order = _sub(header_agreement, "ram", "BuyerOrderReferencedDocument")
        _sub(order, "ram", "IssuerAssignedID", invoice.order_reference)

    header_delivery = _sub(transaction, "ram", "ApplicableHeaderTradeDelivery")
    if invoice.delivery_date:
        event = _sub(header_delivery, "ram", "ActualDeliverySupplyChainEvent")
        occurrence = _sub(event, "ram", "OccurrenceDateTime")
        _sub(occurrence, "udt", "DateTimeString", _date_102(invoice.delivery_date), format="102")

    settlement = _sub(transaction, "ram", "ApplicableHeaderTradeSettlement")
    _sub(settlement, "ram", "InvoiceCurrencyCode", invoice.currency.value)
    if invoice.iban:
        payment = _sub(settlement, "ram", "SpecifiedTradeSettlementPaymentMeans")
        _sub(payment, "ram", "TypeCode", "58")
        account = _sub(payment, "ram", "PayeePartyCreditorFinancialAccount")
        _sub(account, "ram", "IBANID", invoice.iban)

    groups: dict[Decimal, list] = {}
    for line in invoice.lines:
        groups.setdefault(line.vat_rate, []).append(line)
    for rate, lines in sorted(groups.items()):
        taxable = q2(sum((line.line_total for line in lines), start=Decimal("0.00")))
        tax = _sub(settlement, "ram", "ApplicableTradeTax")
        _sub(tax, "ram", "CalculatedAmount", _amount(q2(taxable * rate)))
        _sub(tax, "ram", "TypeCode", "VAT")
        _sub(tax, "ram", "BasisAmount", _amount(taxable))
        _sub(tax, "ram", "CategoryCode", "S")
        _sub(tax, "ram", "RateApplicablePercent", _rate(rate))

    if invoice.due_date:
        terms = _sub(settlement, "ram", "SpecifiedTradePaymentTerms")
        due = _sub(terms, "ram", "DueDateDateTime")
        _sub(due, "udt", "DateTimeString", _date_102(invoice.due_date), format="102")

    totals = _sub(settlement, "ram", "SpecifiedTradeSettlementHeaderMonetarySummation")
    _sub(totals, "ram", "LineTotalAmount", _amount(invoice.total_net))
    _sub(totals, "ram", "TaxBasisTotalAmount", _amount(invoice.total_net))
    _sub(totals, "ram", "TaxTotalAmount", _amount(invoice.total_vat), currencyID=invoice.currency.value)
    _sub(totals, "ram", "GrandTotalAmount", _amount(invoice.total_gross))
    _sub(totals, "ram", "DuePayableAmount", _amount(invoice.total_gross))

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def _minimal_pdf() -> bytes:
    try:
        from pypdf import PdfWriter
    except ImportError as exc:  # pragma: no cover - factur-x installs pypdf
        raise InvoiceGenerationError("pypdf is required to build a base PDF") from exc

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_metadata({"/Title": "Yasmina invoice", "/Author": "Project Yasmina"})
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class ZUGFeRDGenerator:
    """Generate a Factur-X/ZUGFeRD 2.2 PDF with embedded EN16931 CII XML."""

    def build_xml(self, invoice: Invoice) -> bytes:
        return build_cii_xml(invoice)

    def generate(
        self,
        invoice: Invoice,
        output_path: str | Path,
        *,
        base_pdf: bytes | str | Path | None = None,
        check_xsd: bool = True,
    ) -> Path:
        try:
            from facturx import generate_from_binary
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise InvoiceGenerationError("factur-x is not installed; install the 'e-invoicing' extra") from exc

        if base_pdf is None:
            pdf_bytes = _minimal_pdf()
        elif isinstance(base_pdf, (str, Path)):
            pdf_bytes = Path(base_pdf).read_bytes()
        else:
            pdf_bytes = base_pdf

        xml = self.build_xml(invoice)
        try:
            output = generate_from_binary(
                pdf_bytes,
                xml,
                flavor="factur-x",
                level=FACTURX_LEVEL,
                check_xsd=check_xsd,
                check_schematron=False,
                pdf_metadata={
                    "title": f"Invoice {invoice.invoice_number}",
                    "author": invoice.seller.name,
                    "subject": "ZUGFeRD 2.2 invoice",
                },
            )
        except Exception as exc:
            raise InvoiceGenerationError(f"factur-x could not generate the PDF: {exc}") from exc

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(output)
        return path
