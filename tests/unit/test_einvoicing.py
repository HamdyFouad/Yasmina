"""ZUGFeRD/Factur-X and XRechnung generator tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from lxml import etree
from pydantic import ValidationError

from src.generators.invoice_models import Invoice, InvoiceLine, InvoiceParty
from src.generators.leitweg import LeitwegIdError, validate_leitweg_id
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
            name="Yasmina GmbH",
            street="Hauptstr. 1",
            city="Berlin",
            postal_code="10115",
            vat_id="DE123456789",
        ),
        "buyer": InvoiceParty(
            name="Stadt Muster",
            street="Rathausstr. 2",
            city="Musterstadt",
            postal_code="12345",
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


def test_cii_xml_is_schema_valid() -> None:
    xml = build_cii_xml(make_invoice())

    assert facturx.xml_check_xsd(xml, flavor="factur-x", level="en16931") is True
    assert b"CrossIndustryInvoice" in xml
