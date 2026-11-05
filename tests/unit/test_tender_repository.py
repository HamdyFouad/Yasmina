"""Tender repository mapping tests (no database required)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from src.models.enums import (
    MaterialType,
    ProcurementProcedure,
    StandardizedColor,
    TenderSourceFormat,
    UnitOfMeasure,
)
from src.models.orm import Tender as TenderORM
from src.models.schemas import Tender, TenderItem
from src.repositories.tender_repository import orm_to_tender, tender_to_orm


def make_tender() -> Tender:
    now = datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc)
    item = TenderItem(
        position_number="1.1",
        short_text="Nitrilhandschuhe blau Gr. M",
        quantity=Decimal("10.0000"),
        unit=UnitOfMeasure.STUECK,
        material=MaterialType.NITRILE,
        color=StandardizedColor.BLUE,
        pack_size=1000,
        attributes={"source": "test"},
    )
    return Tender(
        tender_number="T-2026-001",
        title="Rahmenvertrag",
        client_name="Stadt Muster",
        procedure=ProcurementProcedure.OPEN_PROCEDURE,
        source_format=TenderSourceFormat.GAEB_X83,
        submission_deadline=now,
        items=[item],
        metadata={"source": "unit-test"},
        created_at=now,
        updated_at=now,
    )


def test_tender_orm_roundtrip() -> None:
    tender = make_tender()

    orm = tender_to_orm(tender)

    assert isinstance(orm, TenderORM)
    assert orm.id == tender.id
    assert orm.items[0].tender_id == tender.id
    assert orm.metadata_json == {"source": "unit-test"}

    restored = orm_to_tender(orm)

    assert restored.id == tender.id
    assert restored.tender_number == tender.tender_number
    assert restored.client_name == tender.client_name
    assert restored.items[0].id == tender.items[0].id
    assert restored.items[0].quantity == tender.items[0].quantity
    assert restored.items[0].material is MaterialType.NITRILE
    assert restored.metadata == {"source": "unit-test"}


def test_tender_orm_roundtrip_preserves_optional_fields() -> None:
    tender = make_tender().model_copy(
        update={
            "items": [
                make_tender().items[0].model_copy(
                    update={
                        "tender_id": uuid4(),
                        "lot": "Los 2",
                        "long_text": "Langtext",
                        "thickness_um": Decimal("120.00"),
                        "attributes": {},
                    }
                )
            ]
        }
    )

    restored = orm_to_tender(tender_to_orm(tender))

    item = restored.items[0]
    assert item.lot == "Los 2"
    assert item.long_text == "Langtext"
    assert item.thickness_um == Decimal("120.00")
    assert item.attributes == {}
