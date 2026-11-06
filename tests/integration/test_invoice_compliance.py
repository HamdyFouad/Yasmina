"""End-to-end e-invoice compliance checks (KoSIT Schematron + PDF/A-3 carrier)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO

from lxml import etree
from pypdf import PdfReader

from src.generators.invoice_models import Invoice, InvoiceLine, InvoiceParty
from src.generators.schematron import KoSITSchematronValidator
from src.generators.xrechnung_generator import XRechnungGenerator
from src.generators.zugferd_generator import ZUGFeRDGenerator
from src.models.enums import UnitOfMeasure


def make_invoice() -> Invoice:
    return Invoice(
        invoice_number="INV-2026-0042",
        issue_date=date(2026, 4, 1),
        due_date=date(2026, 5, 1),
        delivery_date=date(2026, 4, 1),
        seller=InvoiceParty(
            name="BayerClean",
            street="Reithmayrstraße 52",
            city="Regensburg",
            postal_code="93055",
            vat_id="DE123456789",
            email="rechnung@bayerclean.de",
            contact_name="Buchhaltung",
            telephone="+49 941 123456",
        ),
        buyer=InvoiceParty(
            name="Stadt Muster",
            street="Rathausstr. 2",
            city="Musterstadt",
            postal_code="12345",
            email="einkauf@musterstadt.de",
        ),
        buyer_reference="991-03730-19",
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
                vat_rate=Decimal("0"),
            ),
        ],
        total_net=Decimal("250.00"),
        total_vat=Decimal("26.00"),
        total_gross=Decimal("276.00"),
        iban="DE89370400440532013000",
        payment_terms="Zahlbar innerhalb 30 Tagen",
    )


def test_xrechnung_passes_kosit_schematron_for_multi_rate_invoice() -> None:
    xml = XRechnungGenerator().build(make_invoice())

    report = KoSITSchematronValidator().validate(xml, syntax="ubl")
    assert report.valid is True
    assert report.errors == ()
    root = etree.fromstring(xml)
    ns = {
        "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
        "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    }
    categories = root.xpath("//cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:ID", namespaces=ns)
    assert [category.text for category in categories] == ["Z", "S", "S"]


def test_zugferd_carrier_has_pdfa_marker_and_branded_content() -> None:
    pdf = ZUGFeRDGenerator().build(make_invoice())
    reader = PdfReader(BytesIO(pdf))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    assert "BayerClean" in text
    assert "Reithmayr" in text
    assert "93055 Regensburg" in text
    assert "Umsatzsteueraufteilung" in text
    assert "7,0 %" in text
    assert reader.xmp_metadata is not None
    assert b"<pdfaid:part>3</pdfaid:part>" in reader.xmp_metadata.stream.get_data()
    assert "/OutputIntents" in reader.trailer["/Root"]
