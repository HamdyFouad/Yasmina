"""ZUGFeRD/Factur-X and XRechnung generator tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from lxml import etree
from pydantic import ValidationError

from src.generators.invoice_models import Invoice, InvoiceLine, InvoiceParty
from src.generators.leitweg import LeitwegIdError, validate_leitweg_id
from src.generators.schematron import KoSITSchematronValidator
from src.generators.xrechnung_generator import (
    InvoiceGenerationError,
    XRechnungGenerator,
)
from src.generators.zugferd_generator import ZUGFeRDGenerator, build_cii_xml
from src.models.enums import UnitOfMeasure

facturx = pytest.importorskip("facturx")


def make_invoice(**overrides: object) -> Invoice:
    data: dict[str, object] = {
        "invoice_number": "INV-2026-001",
        "issue_date": date(2026, 1, 15),
        "due_date": date(2026, 2, 14),
        "delivery_date": date(2026, 1, 15),
        "seller": InvoiceParty(
            name="BayerClean",
            street="Reithmayrstraße 52",
            city="Regensburg",
            postal_code="93055",
            vat_id="DE123456789",
            email="rechnung@bayerclean.de",
            contact_name="Buchhaltung",
            telephone="+49 941 123456",
        ),
        "buyer": InvoiceParty(
            name="Stadt Muster",
            street="Rathausstr. 2",
            city="Musterstadt",
            postal_code="12345",
            email="einkauf@musterstadt.de",
        ),
        "buyer_reference": "991-03730-19",
        "order_reference": "PO-1",
        "lines": [
            InvoiceLine(
                line_id=1,
                name="Nitrilhandschuhe blau Gr. M",
                quantity=Decimal("10"),
                unit=UnitOfMeasure.STUECK,
                unit_price=Decimal("10.00"),
                line_total=Decimal("100.00"),
            )
        ],
        "total_net": Decimal("100.00"),
        "total_vat": Decimal("19.00"),
        "total_gross": Decimal("119.00"),
        "iban": "DE89370400440532013000",
        "payment_terms": "Zahlbar innerhalb 30 Tagen",
    }
    data.update(overrides)
    return Invoice(**data)  # type: ignore[arg-type]


def test_leitweg_id_validation() -> None:
    assert validate_leitweg_id("991-03730-19") == "991-03730-19"
    assert validate_leitweg_id("991-03073-50") == "991-03073-50"

    with pytest.raises(LeitwegIdError, match="check digit"):
        validate_leitweg_id("991-03730-20")
    with pytest.raises(LeitwegIdError, match="segment"):
        validate_leitweg_id("99103730")
    with pytest.raises(LeitwegIdError, match="spaces"):
        validate_leitweg_id("991 03730 19")
    with pytest.raises(LeitwegIdError, match="2-12"):
        validate_leitweg_id("9-03730-19")


def test_invoice_rejects_inconsistent_totals() -> None:
    with pytest.raises(ValidationError, match="total_net"):
        make_invoice(total_net=Decimal("99.00"))


def test_xrechnung_generates_valid_ubl(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    invoice = make_invoice()
    output = workspace_tmp / "invoice.xml"

    path = XRechnungGenerator().generate(invoice, output)

    assert path.is_file()
    xml = path.read_bytes()
    assert facturx.xml_check_xsd(xml, flavor="ubl-2.1-invoice", level="en16931") is True

    root = etree.fromstring(xml)
    ns = {"cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"}
    assert root.findtext("cbc:CustomizationID", namespaces=ns) == (
        "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
    )
    assert root.findtext("cbc:BuyerReference", namespaces=ns) == "991-03730-19"
    assert root.findtext("cbc:DocumentCurrencyCode", namespaces=ns) == "EUR"

    report = KoSITSchematronValidator().validate(xml, syntax="ubl")
    assert report.valid is True
    assert report.errors == ()


def test_kosit_schematron_rejects_missing_mandatory_business_terms() -> None:
    invoice = make_invoice()
    xml = XRechnungGenerator().build(invoice, validate_schematron=False)
    root = etree.fromstring(xml)
    ns = {"cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"}
    buyer_reference = root.find("cbc:BuyerReference", namespaces=ns)
    assert buyer_reference is not None
    root.remove(buyer_reference)
    invalid = etree.tostring(root, xml_declaration=True, encoding="UTF-8")

    report = KoSITSchematronValidator().validate(invalid, syntax="ubl")

    assert report.valid is False
    assert any(issue.rule_id in {"BR-DE-15", "BR-DE-1"} for issue in report.errors)


def test_xrechnung_generator_aborts_on_kosit_contact_violation() -> None:
    invoice = make_invoice(
        seller=InvoiceParty(
            name="BayerClean",
            street="Reithmayrstraße 52",
            city="Regensburg",
            postal_code="93055",
            vat_id="DE123456789",
            email="rechnung@bayerclean.de",
            contact_name="Buchhaltung",
        )
    )

    with pytest.raises(InvoiceGenerationError, match="BR-DE-6"):
        XRechnungGenerator().build(invoice)


def test_xrechnung_requires_valid_leitweg_id(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    invoice = make_invoice(buyer_reference="991-03730-20")

    with pytest.raises(InvoiceGenerationError, match="check digit"):
        XRechnungGenerator().generate(invoice, workspace_tmp / "invoice.xml")

    with pytest.raises(InvoiceGenerationError, match="buyer reference"):
        XRechnungGenerator().generate(
            make_invoice(buyer_reference=None),
            workspace_tmp / "invoice.xml",
        )


def test_zugferd_generates_hybrid_pdf(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    invoice = make_invoice()
    output = workspace_tmp / "invoice.pdf"

    path = ZUGFeRDGenerator().generate(invoice, output)

    assert path.is_file()
    pdf_bytes = path.read_bytes()
    assert pdf_bytes.startswith(b"%PDF-")

    filename, xml = facturx.get_facturx_xml_from_pdf(pdf_bytes)
    assert filename == "factur-x.xml"
    assert b"INV-2026-001" in xml
    assert facturx.xml_check_xsd(xml, flavor="factur-x", level="en16931") is True


def test_zugferd_pdf_contains_branded_layout_and_pdfa_metadata() -> None:
    from io import BytesIO

    from pypdf import PdfReader

    invoice = make_invoice()
    pdf_bytes = ZUGFeRDGenerator().build(invoice)
    reader = PdfReader(BytesIO(pdf_bytes))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    assert "BayerClean" in text
    assert "Reithmayr" in text
    assert "93055 Regensburg" in text
    assert "Rechnungsempf" in text
    assert "Nettobetrag 100,00 EUR" in text
    assert "Gesamtbetrag 119,00 EUR" in text
    assert "/OutputIntents" in reader.trailer["/Root"]
    assert reader.xmp_metadata is not None
    xmp = reader.xmp_metadata.stream.get_data()
    assert b"<pdfaid:part>3</pdfaid:part>" in xmp
    assert b"<pdfaid:conformance>B</pdfaid:conformance>" in xmp


def test_zugferd_pdf_renders_multi_rate_vat_breakdown() -> None:
    from io import BytesIO

    from pypdf import PdfReader

    invoice = make_invoice(
        lines=[
            InvoiceLine(
                line_id=1,
                name="Nitrilhandschuhe",
                quantity=Decimal("10"),
                unit=UnitOfMeasure.STUECK,
                unit_price=Decimal("10.00"),
                line_total=Decimal("100.00"),
                vat_rate=Decimal("0.19"),
            ),
            InvoiceLine(
                line_id=2,
                name="Papierhandtücher",
                quantity=Decimal("2"),
                unit=UnitOfMeasure.PACK,
                unit_price=Decimal("50.00"),
                line_total=Decimal("100.00"),
                vat_rate=Decimal("0.07"),
            ),
            InvoiceLine(
                line_id=3,
                name="Pflegeleistung",
                quantity=Decimal("1"),
                unit=UnitOfMeasure.STUECK,
                unit_price=Decimal("50.00"),
                line_total=Decimal("50.00"),
                vat_rate=Decimal("0.00"),
            ),
        ],
        total_net=Decimal("250.00"),
        total_vat=Decimal("26.00"),
        total_gross=Decimal("276.00"),
    )
    pdf_bytes = ZUGFeRDGenerator().build(invoice)
    text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(pdf_bytes)).pages)

    assert "19,0 %" in text
    assert "7,0 %" in text
    assert "0,0 %" in text
    assert "Umsatzsteueraufteilung" in text
    assert "Gesamtbetrag 276,00 EUR" in text


def test_cii_xml_is_schema_valid() -> None:
    xml = build_cii_xml(make_invoice())

    assert facturx.xml_check_xsd(xml, flavor="factur-x", level="en16931") is True
    assert b"CrossIndustryInvoice" in xml
