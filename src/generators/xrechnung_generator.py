"""XRechnung 3.0 (UBL 2.1 / EN 16931) XML generator."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import cast

from lxml import etree

from src.generators.invoice_models import Invoice, InvoiceLine, InvoiceParty
from src.generators.leitweg import LeitwegIdError, validate_leitweg_id
from src.generators.schematron import (
    KoSITSchematronError,
    KoSITSchematronValidator,
    SchematronExecutionError,
    SchematronUnavailableError,
)
from src.models.enums import UnitOfMeasure
from src.models.schemas import q2

__all__ = ["InvoiceGenerationError", "XRechnungGenerator"]

UBL_INVOICE = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
UBL_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
UBL_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"

XRechnung_CUSTOMIZATION_ID = "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
XRechnung_PROFILE_ID = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"

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


class InvoiceGenerationError(ValueError):
    """Raised when invoice data cannot be serialized into a valid e-invoice."""


def _sub(
    parent: etree._Element, ns: str, local: str, text: str | None = None, **attrs: str
) -> etree._Element:
    element = etree.SubElement(parent, f"{{{ns}}}{local}", **attrs)
    if text is not None:
        element.text = text
    return element


def _amount(value: Decimal) -> str:
    return f"{value:.2f}"


def _quantity(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _rate_percent(value: Decimal) -> str:
    percent = value * Decimal(100)
    return format(percent.normalize(), "f")


def _tax_category(rate: Decimal) -> str:
    return "Z" if rate == 0 else "S"


def _check_party(party: InvoiceParty, role: str) -> None:
    if not party.street or not party.city or not party.postal_code:
        raise InvoiceGenerationError(f"{role} address requires street, city and postal code")


def _party(parent: etree._Element, party: InvoiceParty, *, role: str) -> None:
    party_el = _sub(parent, UBL_CAC, "Party")
    endpoint = party.endpoint_id or (str(party.email) if party.email is not None else None)
    if endpoint:
        _sub(
            party_el,
            UBL_CBC,
            "EndpointID",
            endpoint,
            schemeID=party.endpoint_scheme,
        )
    _sub(_sub(party_el, UBL_CAC, "PartyName"), UBL_CBC, "Name", party.name)
    address = _sub(party_el, UBL_CAC, "PostalAddress")
    _sub(address, UBL_CBC, "StreetName", party.street)
    _sub(address, UBL_CBC, "CityName", party.city)
    _sub(address, UBL_CBC, "PostalZone", party.postal_code)
    country = _sub(address, UBL_CAC, "Country")
    _sub(country, UBL_CBC, "IdentificationCode", party.country_code.upper())
    if party.vat_id:
        party_tax = _sub(party_el, UBL_CAC, "PartyTaxScheme")
        _sub(party_tax, UBL_CBC, "CompanyID", party.vat_id)
        tax_scheme = _sub(party_tax, UBL_CAC, "TaxScheme")
        _sub(tax_scheme, UBL_CBC, "ID", "VAT")
    legal = _sub(party_el, UBL_CAC, "PartyLegalEntity")
    _sub(legal, UBL_CBC, "RegistrationName", party.name)
    if role == "seller" or party.contact_name or party.telephone or party.email:
        contact = _sub(party_el, UBL_CAC, "Contact")
        _sub(contact, UBL_CBC, "Name", party.contact_name or party.name)
        if party.telephone:
            _sub(contact, UBL_CBC, "Telephone", party.telephone)
        if party.email:
            _sub(contact, UBL_CBC, "ElectronicMail", str(party.email))


class XRechnungGenerator:
    """Generate a standalone XRechnung 3.0 UBL invoice."""

    def build(
        self,
        invoice: Invoice,
        *,
        require_leitweg: bool = True,
        validate_schematron: bool = True,
        schematron_validator: KoSITSchematronValidator | None = None,
    ) -> bytes:
        if invoice.currency.value != "EUR":
            raise InvoiceGenerationError("XRechnung requires EUR")
        if not invoice.buyer_reference:
            raise InvoiceGenerationError("XRechnung requires a buyer reference (BT-10/Leitweg-ID)")
        if require_leitweg:
            try:
                validate_leitweg_id(invoice.buyer_reference)
            except LeitwegIdError as exc:
                raise InvoiceGenerationError(str(exc)) from exc

        _check_party(invoice.seller, "seller")
        _check_party(invoice.buyer, "buyer")

        root = etree.Element(
            f"{{{UBL_INVOICE}}}Invoice", nsmap={None: UBL_INVOICE, "cac": UBL_CAC, "cbc": UBL_CBC}
        )
        _sub(root, UBL_CBC, "CustomizationID", XRechnung_CUSTOMIZATION_ID)
        _sub(root, UBL_CBC, "ProfileID", XRechnung_PROFILE_ID)
        _sub(root, UBL_CBC, "ID", invoice.invoice_number)
        _sub(root, UBL_CBC, "IssueDate", invoice.issue_date.isoformat())
        if invoice.due_date:
            _sub(root, UBL_CBC, "DueDate", invoice.due_date.isoformat())
        _sub(root, UBL_CBC, "InvoiceTypeCode", "380")
        if invoice.payment_terms:
            _sub(root, UBL_CBC, "Note", invoice.payment_terms)
        _sub(root, UBL_CBC, "DocumentCurrencyCode", invoice.currency.value)
        _sub(root, UBL_CBC, "BuyerReference", invoice.buyer_reference)
        if invoice.order_reference:
            order = _sub(root, UBL_CAC, "OrderReference")
            _sub(order, UBL_CBC, "ID", invoice.order_reference)

        supplier = _sub(root, UBL_CAC, "AccountingSupplierParty")
        _party(supplier, invoice.seller, role="seller")
        customer = _sub(root, UBL_CAC, "AccountingCustomerParty")
        _party(customer, invoice.buyer, role="buyer")

        if invoice.delivery_date:
            delivery = _sub(root, UBL_CAC, "Delivery")
            _sub(delivery, UBL_CBC, "ActualDeliveryDate", invoice.delivery_date.isoformat())

        if invoice.iban:
            payment = _sub(root, UBL_CAC, "PaymentMeans")
            _sub(payment, UBL_CBC, "PaymentMeansCode", "58")
            _sub(payment, UBL_CBC, "PaymentID", invoice.invoice_number)
            account = _sub(payment, UBL_CAC, "PayeeFinancialAccount")
            _sub(account, UBL_CBC, "ID", invoice.iban)

        tax_total = _sub(root, UBL_CAC, "TaxTotal")
        _sub(tax_total, UBL_CBC, "TaxAmount", _amount(invoice.total_vat), currencyID=invoice.currency.value)

        groups: dict[Decimal, list[InvoiceLine]] = {}
        for line in invoice.lines:
            groups.setdefault(line.vat_rate, []).append(line)
        for rate, lines in sorted(groups.items()):
            subtotal = _sub(tax_total, UBL_CAC, "TaxSubtotal")
            taxable = q2(sum((line.line_total for line in lines), start=Decimal("0.00")))
            tax = q2(taxable * rate)
            _sub(subtotal, UBL_CBC, "TaxableAmount", _amount(taxable), currencyID=invoice.currency.value)
            _sub(subtotal, UBL_CBC, "TaxAmount", _amount(tax), currencyID=invoice.currency.value)
            category = _sub(subtotal, UBL_CAC, "TaxCategory")
            _sub(category, UBL_CBC, "ID", _tax_category(rate))
            _sub(category, UBL_CBC, "Percent", _rate_percent(rate))
            scheme = _sub(category, UBL_CAC, "TaxScheme")
            _sub(scheme, UBL_CBC, "ID", "VAT")

        monetary = _sub(root, UBL_CAC, "LegalMonetaryTotal")
        _sub(
            monetary,
            UBL_CBC,
            "LineExtensionAmount",
            _amount(invoice.total_net),
            currencyID=invoice.currency.value,
        )
        _sub(
            monetary,
            UBL_CBC,
            "TaxExclusiveAmount",
            _amount(invoice.total_net),
            currencyID=invoice.currency.value,
        )
        _sub(
            monetary,
            UBL_CBC,
            "TaxInclusiveAmount",
            _amount(invoice.total_gross),
            currencyID=invoice.currency.value,
        )
        _sub(
            monetary,
            UBL_CBC,
            "PayableAmount",
            _amount(invoice.total_gross),
            currencyID=invoice.currency.value,
        )

        for line in invoice.lines:
            line_el = _sub(root, UBL_CAC, "InvoiceLine")
            _sub(line_el, UBL_CBC, "ID", str(line.line_id))
            _sub(
                line_el,
                UBL_CBC,
                "InvoicedQuantity",
                _quantity(line.quantity),
                unitCode=_UNIT_CODES[line.unit],
            )
            _sub(
                line_el,
                UBL_CBC,
                "LineExtensionAmount",
                _amount(line.line_total),
                currencyID=invoice.currency.value,
            )
            item = _sub(line_el, UBL_CAC, "Item")
            _sub(item, UBL_CBC, "Name", line.name)
            if line.description:
                _sub(item, UBL_CBC, "Description", line.description)
            category = _sub(item, UBL_CAC, "ClassifiedTaxCategory")
            _sub(category, UBL_CBC, "ID", _tax_category(line.vat_rate))
            _sub(category, UBL_CBC, "Percent", _rate_percent(line.vat_rate))
            scheme = _sub(category, UBL_CAC, "TaxScheme")
            _sub(scheme, UBL_CBC, "ID", "VAT")
            price = _sub(line_el, UBL_CAC, "Price")
            _sub(price, UBL_CBC, "PriceAmount", _amount(line.unit_price), currencyID=invoice.currency.value)

        xml = cast(
            bytes,
            etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True),
        )
        if validate_schematron:
            try:
                import facturx
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise InvoiceGenerationError(
                    "factur-x is required for UBL XSD validation; install the 'e-invoicing' extra"
                ) from exc
            try:
                facturx.xml_check_xsd(xml, flavor="ubl-2.1-invoice", level="en16931")
            except Exception as exc:
                raise InvoiceGenerationError(f"UBL 2.1 XSD validation failed: {exc}") from exc
            validator = schematron_validator or KoSITSchematronValidator()
            try:
                validator.validate_or_raise(xml, syntax="ubl")
            except KoSITSchematronError as exc:
                raise InvoiceGenerationError(str(exc)) from exc
            except (SchematronUnavailableError, SchematronExecutionError) as exc:
                raise InvoiceGenerationError(f"KoSIT Schematron validation is unavailable: {exc}") from exc
        return xml

    def generate(
        self,
        invoice: Invoice,
        output_path: str | Path,
        *,
        require_leitweg: bool = True,
        validate_schematron: bool = True,
        schematron_validator: KoSITSchematronValidator | None = None,
    ) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            self.build(
                invoice,
                require_leitweg=require_leitweg,
                validate_schematron=validate_schematron,
                schematron_validator=schematron_validator,
            )
        )
        return path
