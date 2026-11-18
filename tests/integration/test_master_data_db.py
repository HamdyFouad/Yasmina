"""Live PostgreSQL tests for supplier and customer master data."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete

from src.config import Settings
from src.db import dispose_engine, get_session_factory
from src.models.orm import Customer, Supplier
from src.repositories.master_data_repository import (
    CustomerInput,
    CustomerRepository,
    SupplierContactInput,
    SupplierInput,
    SupplierRepository,
)

DATABASE_URL = os.getenv("YASMINA_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set YASMINA_TEST_DATABASE_URL to run PostgreSQL integration tests",
)


def settings() -> Settings:
    assert DATABASE_URL is not None
    return Settings(
        _env_file=None,
        database_url=DATABASE_URL,
        app_env="test",
        jwt_secret_key="master-data-live-secret-with-32-bytes",
        api_admin_password="test-admin-password",
    )


async def test_supplier_contacts_and_customer_crud_commit() -> None:
    suffix = datetime.now(UTC).strftime("%H%M%S%f")
    supplier_name = f"MASTER-SUP-{suffix}"
    customer_name = f"MASTER-CUS-{suffix}"
    factory = get_session_factory(settings())
    try:
        async with factory() as session:
            suppliers = SupplierRepository(session)
            supplier = await suppliers.create_supplier(
                SupplierInput(
                    name=supplier_name,
                    email="supplier@example.de",
                    country_code="DE",
                    contacts=(
                        SupplierContactInput(
                            name="Primary Contact",
                            email="primary@example.de",
                            is_primary=True,
                        ),
                    ),
                )
            )
            assert supplier.primary_contact is not None
            assert supplier.primary_contact.email == "primary@example.de"
            listed = await suppliers.list_suppliers(search=suffix[:8])
            assert any(item.id == supplier.id for item in listed)

            updated = await suppliers.update_supplier(
                supplier.id,
                SupplierInput(
                    name=supplier_name,
                    email="supplier@example.de",
                    country_code="DE",
                    contacts=(
                        SupplierContactInput(
                            id=supplier.contacts[0].id,
                            name="Updated Contact",
                            email="updated@example.de",
                            is_primary=True,
                        ),
                    ),
                ),
            )
            assert updated.contacts[0].name == "Updated Contact"

            customers = CustomerRepository(session)
            customer = await customers.create_customer(
                CustomerInput(
                    name=customer_name,
                    street="Rathausplatz 1",
                    postal_code="93047",
                    city="Regensburg",
                    country_code="DE",
                    leitweg_id="991-03730-19",
                    contact_email="buyer@example.de",
                )
            )
            assert customer.leitweg_id == "991-03730-19"
            fetched = await customers.get_customer(customer.id)
            assert fetched is not None
            assert fetched.name == customer_name

        async with factory() as session:
            await session.execute(delete(Supplier).where(Supplier.id == supplier.id))
            await session.execute(delete(Customer).where(Customer.id == customer.id))
            await session.commit()
    finally:
        await dispose_engine()
