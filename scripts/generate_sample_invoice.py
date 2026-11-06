"""Generate a representative ZUGFeRD invoice for the veraPDF CI gate."""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.generators.invoice_models import Invoice, InvoiceLine, InvoiceParty
from src.generators.zugferd_generator import ZUGFeRDGenerator
from src.models.enums import UnitOfMeasure


def build_sample() -> Invoice:
    return Invoice(
        invoice_number="INV-2026-0001",
        issue_date=date(2026, 1, 15),
        due_date=date(2026, 2, 14),
        delivery_date=date(2026, 1, 15),
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
        ],
        total_net=Decimal("200.00"),
        total_vat=Decimal("26.00"),
        total_gross=Decimal("226.00"),
        iban="DE89370400440532013000",
        payment_terms="Zahlbar innerhalb 30 Tagen",
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    output = Path(args[0] if args else "sample-invoice.pdf")
    output.write_bytes(ZUGFeRDGenerator().build(build_sample()))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
