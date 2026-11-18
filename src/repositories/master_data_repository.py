"""CRUD persistence for supplier and customer master data."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.orm import Customer, Supplier, SupplierContact

__all__ = [
    "CustomerInput",
    "CustomerRepository",
    "CustomerView",
    "MasterDataConflict",
    "MasterDataNotFound",
    "MasterDataValidationError",
    "SupplierContactInput",
    "SupplierContactView",
    "SupplierInput",
    "SupplierRepository",
    "SupplierView",
]


class MasterDataConflict(ValueError):
    """Raised when a unique supplier/customer constraint is violated."""


class MasterDataNotFound(ValueError):
    """Raised when a master-data row does not exist."""


class MasterDataValidationError(ValueError):
    """Raised when a master-data payload cannot be applied."""


@dataclass(frozen=True, slots=True)
class SupplierContactInput:
    name: str
    role: str | None = None
    email: str | None = None
    phone: str | None = None
    is_primary: bool = False
    id: UUID | None = None


@dataclass(frozen=True, slots=True)
class SupplierInput:
    name: str
    legal_name: str | None = None
    email: str | None = None
    phone: str | None = None
    street: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country_code: str = "DE"
    payment_terms: str | None = None
    delivery_days: int | None = None
    notes: str | None = None
    is_active: bool = True
    contacts: tuple[SupplierContactInput, ...] = ()


@dataclass(frozen=True, slots=True)
class CustomerInput:
    name: str
    street: str
    postal_code: str
    city: str
    country_code: str = "DE"
    legal_name: str | None = None
    vat_id: str | None = None
    leitweg_id: str | None = None
    payment_terms: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    phone: str | None = None
    notes: str | None = None
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class SupplierContactView:
    id: UUID
    name: str
    role: str | None
    email: str | None
    phone: str | None
    is_primary: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "name": self.name,
            "role": self.role,
            "email": self.email,
            "phone": self.phone,
            "is_primary": self.is_primary,
        }


@dataclass(frozen=True, slots=True)
class SupplierView:
    id: UUID
    name: str
    legal_name: str | None
    erpnext_supplier_id: str | None
    email: str | None
    phone: str | None
    street: str | None
    postal_code: str | None
    city: str | None
    country_code: str
    payment_terms: str | None
    delivery_days: int | None
    notes: str | None
    is_active: bool
    contacts: tuple[SupplierContactView, ...]
    created_at: datetime
    updated_at: datetime

    @property
    def primary_contact(self) -> SupplierContactView | None:
        return next((contact for contact in self.contacts if contact.is_primary), None)

    def as_dict(self) -> dict[str, Any]:
        primary = self.primary_contact
        return {
            "id": str(self.id),
            "name": self.name,
            "legal_name": self.legal_name,
            "erpnext_supplier_id": self.erpnext_supplier_id,
            "email": self.email,
            "phone": self.phone,
            "street": self.street,
            "postal_code": self.postal_code,
            "city": self.city,
            "country_code": self.country_code,
            "payment_terms": self.payment_terms,
            "delivery_days": self.delivery_days,
            "notes": self.notes,
            "is_active": self.is_active,
            "contacts": [contact.as_dict() for contact in self.contacts],
            "primary_contact_name": primary.name if primary is not None else None,
            "primary_contact_email": primary.email if primary is not None else None,
            "contact_count": len(self.contacts),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class CustomerView:
    id: UUID
    name: str
    legal_name: str | None
    street: str
    postal_code: str
    city: str
    country_code: str
    vat_id: str | None
    leitweg_id: str | None
    payment_terms: str | None
    contact_name: str | None
    contact_email: str | None
    phone: str | None
    notes: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "name": self.name,
            "legal_name": self.legal_name,
            "street": self.street,
            "postal_code": self.postal_code,
            "city": self.city,
            "country_code": self.country_code,
            "vat_id": self.vat_id,
            "leitweg_id": self.leitweg_id,
            "payment_terms": self.payment_terms,
            "contact_name": self.contact_name,
            "contact_email": self.contact_email,
            "phone": self.phone,
            "notes": self.notes,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


def _contact_view(row: SupplierContact) -> SupplierContactView:
    return SupplierContactView(
        id=row.id,
        name=row.name,
        role=row.role,
        email=row.email,
        phone=row.phone,
        is_primary=row.is_primary,
    )


def _supplier_view(row: Supplier) -> SupplierView:
    contacts = tuple(_contact_view(contact) for contact in row.contacts or ())
    return SupplierView(
        id=row.id,
        name=row.name,
        legal_name=row.legal_name,
        erpnext_supplier_id=row.erpnext_supplier_id,
        email=row.email,
        phone=row.phone,
        street=row.street,
        postal_code=row.postal_code,
        city=row.city,
        country_code=row.country_code,
        payment_terms=row.payment_terms,
        delivery_days=row.delivery_days,
        notes=row.notes,
        is_active=row.is_active,
        contacts=contacts,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _customer_view(row: Customer) -> CustomerView:
    return CustomerView(
        id=row.id,
        name=row.name,
        legal_name=row.legal_name,
        street=row.street,
        postal_code=row.postal_code,
        city=row.city,
        country_code=row.country_code,
        vat_id=row.vat_id,
        leitweg_id=row.leitweg_id,
        payment_terms=row.payment_terms,
        contact_name=row.contact_name,
        contact_email=row.contact_email,
        phone=row.phone,
        notes=row.notes,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _normalized_contacts(
    contacts: tuple[SupplierContactInput, ...],
) -> tuple[SupplierContactInput, ...]:
    primaries = [contact for contact in contacts if contact.is_primary]
    if len(primaries) > 1:
        raise MasterDataValidationError("only one supplier contact can be primary")
    if primaries or not contacts:
        return contacts
    first = contacts[0]
    return (replace(first, is_primary=True), *contacts[1:])


class SupplierRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_suppliers(
        self,
        *,
        active: bool | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SupplierView]:
        statement = (
            select(Supplier)
            .options(selectinload(Supplier.contacts))
            .order_by(Supplier.name)
            .offset(offset)
            .limit(limit)
        )
        if active is not None:
            statement = statement.where(Supplier.is_active.is_(active))
        if search:
            pattern = f"%{search.strip()}%"
            statement = statement.where(
                or_(
                    Supplier.name.ilike(pattern),
                    Supplier.legal_name.ilike(pattern),
                    Supplier.email.ilike(pattern),
                )
            )
        rows = (await self.session.execute(statement)).scalars().all()
        return [_supplier_view(row) for row in rows]

    async def count_suppliers(
        self,
        *,
        active: bool | None = None,
        search: str | None = None,
    ) -> int:
        statement = select(func.count()).select_from(Supplier)
        if active is not None:
            statement = statement.where(Supplier.is_active.is_(active))
        if search:
            pattern = f"%{search.strip()}%"
            statement = statement.where(
                or_(
                    Supplier.name.ilike(pattern),
                    Supplier.legal_name.ilike(pattern),
                    Supplier.email.ilike(pattern),
                )
            )
        return int((await self.session.execute(statement)).scalar_one())

    async def get_supplier(self, supplier_id: UUID) -> SupplierView | None:
        row = await self.session.get(
            Supplier,
            supplier_id,
            options=(selectinload(Supplier.contacts),),
        )
        return None if row is None else _supplier_view(row)

    async def create_supplier(self, payload: SupplierInput) -> SupplierView:
        contacts = _normalized_contacts(payload.contacts)
        row = Supplier(
            name=payload.name,
            legal_name=payload.legal_name,
            email=payload.email,
            phone=payload.phone,
            street=payload.street,
            postal_code=payload.postal_code,
            city=payload.city,
            country_code=payload.country_code,
            payment_terms=payload.payment_terms,
            delivery_days=payload.delivery_days,
            notes=payload.notes,
            is_active=payload.is_active,
            contacts=[
                SupplierContact(
                    name=contact.name,
                    role=contact.role,
                    email=contact.email,
                    phone=contact.phone,
                    is_primary=contact.is_primary,
                )
                for contact in contacts
            ],
        )
        self.session.add(row)
        await self._commit(row)
        await self.session.refresh(row, attribute_names=["contacts"])
        return _supplier_view(row)

    async def update_supplier(self, supplier_id: UUID, payload: SupplierInput) -> SupplierView:
        row = await self.session.get(
            Supplier,
            supplier_id,
            options=(selectinload(Supplier.contacts),),
            with_for_update=True,
        )
        if row is None:
            raise MasterDataNotFound("supplier not found")
        contacts = _normalized_contacts(payload.contacts)
        row.name = payload.name
        row.legal_name = payload.legal_name
        row.email = payload.email
        row.phone = payload.phone
        row.street = payload.street
        row.postal_code = payload.postal_code
        row.city = payload.city
        row.country_code = payload.country_code
        row.payment_terms = payload.payment_terms
        row.delivery_days = payload.delivery_days
        row.notes = payload.notes
        row.is_active = payload.is_active
        # Clear the previous primary before inserting a replacement so the
        # partial unique index never sees two primary contacts in one flush.
        for existing_contact in row.contacts:
            existing_contact.is_primary = False
        await self.session.flush()
        existing = {contact.id: contact for contact in row.contacts}
        keep: set[UUID] = set()
        for contact in contacts:
            if contact.id is None:
                row.contacts.append(
                    SupplierContact(
                        name=contact.name,
                        role=contact.role,
                        email=contact.email,
                        phone=contact.phone,
                        is_primary=contact.is_primary,
                    )
                )
                continue
            target = existing.get(contact.id)
            if target is None:
                raise MasterDataValidationError("supplier contact not found")
            keep.add(contact.id)
            target.name = contact.name
            target.role = contact.role
            target.email = contact.email
            target.phone = contact.phone
            target.is_primary = contact.is_primary
        for contact_id, existing_contact in existing.items():
            if contact_id not in keep:
                row.contacts.remove(existing_contact)
        await self._commit(row)
        await self.session.refresh(row, attribute_names=["contacts"])
        return _supplier_view(row)

    async def deactivate_supplier(self, supplier_id: UUID) -> SupplierView:
        row = await self.session.get(
            Supplier,
            supplier_id,
            options=(selectinload(Supplier.contacts),),
        )
        if row is None:
            raise MasterDataNotFound("supplier not found")
        row.is_active = False
        await self._commit(row)
        return _supplier_view(row)

    async def _commit(self, row: Supplier) -> None:
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise MasterDataConflict("supplier name or primary contact conflicts") from exc
        # Server-side ON UPDATE timestamps are expired by SQLAlchemy; reload
        # them asynchronously before the caller builds a response view.
        await self.session.refresh(row, attribute_names=["updated_at"])


class CustomerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_customers(
        self,
        *,
        active: bool | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CustomerView]:
        statement = select(Customer).order_by(Customer.name).offset(offset).limit(limit)
        if active is not None:
            statement = statement.where(Customer.is_active.is_(active))
        if search:
            pattern = f"%{search.strip()}%"
            statement = statement.where(
                or_(
                    Customer.name.ilike(pattern),
                    Customer.legal_name.ilike(pattern),
                    Customer.city.ilike(pattern),
                    Customer.contact_email.ilike(pattern),
                )
            )
        rows = (await self.session.execute(statement)).scalars().all()
        return [_customer_view(row) for row in rows]

    async def count_customers(
        self,
        *,
        active: bool | None = None,
        search: str | None = None,
    ) -> int:
        statement = select(func.count()).select_from(Customer)
        if active is not None:
            statement = statement.where(Customer.is_active.is_(active))
        if search:
            pattern = f"%{search.strip()}%"
            statement = statement.where(
                or_(
                    Customer.name.ilike(pattern),
                    Customer.legal_name.ilike(pattern),
                    Customer.city.ilike(pattern),
                    Customer.contact_email.ilike(pattern),
                )
            )
        return int((await self.session.execute(statement)).scalar_one())

    async def get_customer(self, customer_id: UUID) -> CustomerView | None:
        row = await self.session.get(Customer, customer_id)
        return None if row is None else _customer_view(row)

    async def create_customer(self, payload: CustomerInput) -> CustomerView:
        row = Customer(**asdict(payload))
        self.session.add(row)
        await self._commit(row)
        return _customer_view(row)

    async def update_customer(self, customer_id: UUID, payload: CustomerInput) -> CustomerView:
        row = await self.session.get(Customer, customer_id, with_for_update=True)
        if row is None:
            raise MasterDataNotFound("customer not found")
        for field, value in asdict(payload).items():
            setattr(row, field, value)
        await self._commit(row)
        return _customer_view(row)

    async def deactivate_customer(self, customer_id: UUID) -> CustomerView:
        row = await self.session.get(Customer, customer_id)
        if row is None:
            raise MasterDataNotFound("customer not found")
        row.is_active = False
        await self._commit(row)
        return _customer_view(row)

    async def _commit(self, row: Customer) -> None:
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise MasterDataConflict("customer name conflicts") from exc
        await self.session.refresh(row, attribute_names=["updated_at"])
