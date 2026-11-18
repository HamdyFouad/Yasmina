"""Supplier and customer master-data CRUD."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.deps import (
    get_audit_service,
    get_customer_repository,
    get_supplier_repository,
    require_roles,
)
from src.api.schemas import CustomerPayload, SupplierPayload
from src.api.security import Principal, Role
from src.models.enums import AuditEventType
from src.repositories.master_data_repository import (
    CustomerInput,
    CustomerRepository,
    MasterDataConflict,
    MasterDataNotFound,
    MasterDataValidationError,
    SupplierContactInput,
    SupplierInput,
    SupplierRepository,
)
from src.services.audit_service import AuditService

router = APIRouter(tags=["master-data"])

READ_ROLES = (Role.ADMIN, Role.SALES, Role.APPROVER, Role.VIEWER)


def _supplier_input(payload: SupplierPayload) -> SupplierInput:
    return SupplierInput(
        name=payload.name.strip(),
        legal_name=payload.legal_name.strip() if payload.legal_name else None,
        email=str(payload.email) if payload.email else None,
        phone=payload.phone,
        street=payload.street,
        postal_code=payload.postal_code,
        city=payload.city,
        country_code=payload.country_code,
        payment_terms=payload.payment_terms,
        delivery_days=payload.delivery_days,
        notes=payload.notes,
        is_active=payload.is_active,
        contacts=tuple(
            SupplierContactInput(
                id=contact.id,
                name=contact.name.strip(),
                role=contact.role,
                email=str(contact.email) if contact.email else None,
                phone=contact.phone,
                is_primary=contact.is_primary,
            )
            for contact in payload.contacts
        ),
    )


def _customer_input(payload: CustomerPayload) -> CustomerInput:
    return CustomerInput(
        name=payload.name.strip(),
        legal_name=payload.legal_name.strip() if payload.legal_name else None,
        street=payload.street.strip(),
        postal_code=payload.postal_code.strip(),
        city=payload.city.strip(),
        country_code=payload.country_code,
        vat_id=payload.vat_id,
        leitweg_id=payload.leitweg_id,
        payment_terms=payload.payment_terms,
        contact_name=payload.contact_name,
        contact_email=str(payload.contact_email) if payload.contact_email else None,
        phone=payload.phone,
        notes=payload.notes,
        is_active=payload.is_active,
    )


def _master_data_error(exc: Exception) -> HTTPException:
    if isinstance(exc, MasterDataNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, MasterDataConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get("/suppliers")
async def list_suppliers(
    active: bool | None = Query(default=None),
    search: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    repository: SupplierRepository = Depends(get_supplier_repository),
    _: Principal = Depends(require_roles(*READ_ROLES)),
) -> dict[str, object]:
    rows = await repository.list_suppliers(
        active=active,
        search=search,
        limit=limit,
        offset=offset,
    )
    total = await repository.count_suppliers(active=active, search=search)
    return {
        "suppliers": [row.as_dict() for row in rows],
        "count": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/suppliers", status_code=status.HTTP_201_CREATED)
async def create_supplier(
    payload: SupplierPayload,
    repository: SupplierRepository = Depends(get_supplier_repository),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, object]:
    try:
        supplier = await repository.create_supplier(_supplier_input(payload))
    except (MasterDataConflict, MasterDataValidationError) as exc:
        raise _master_data_error(exc) from exc
    await audit.append(
        event_type=AuditEventType.SYSTEM_SUPPLIER_CREATED,
        actor=principal.sub,
        entity_type="supplier",
        entity_id=str(supplier.id),
        payload={"name": supplier.name, "contact_count": len(supplier.contacts)},
    )
    return {"supplier": supplier.as_dict()}


@router.get("/suppliers/{supplier_id}")
async def get_supplier(
    supplier_id: UUID,
    repository: SupplierRepository = Depends(get_supplier_repository),
    _: Principal = Depends(require_roles(*READ_ROLES)),
) -> dict[str, object]:
    supplier = await repository.get_supplier(supplier_id)
    if supplier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="supplier not found")
    return {"supplier": supplier.as_dict()}


@router.put("/suppliers/{supplier_id}")
async def update_supplier(
    supplier_id: UUID,
    payload: SupplierPayload,
    repository: SupplierRepository = Depends(get_supplier_repository),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, object]:
    try:
        supplier = await repository.update_supplier(supplier_id, _supplier_input(payload))
    except (MasterDataNotFound, MasterDataConflict, MasterDataValidationError) as exc:
        raise _master_data_error(exc) from exc
    await audit.append(
        event_type=AuditEventType.SYSTEM_SUPPLIER_UPDATED,
        actor=principal.sub,
        entity_type="supplier",
        entity_id=str(supplier.id),
        payload={"name": supplier.name, "contact_count": len(supplier.contacts)},
    )
    return {"supplier": supplier.as_dict()}


@router.delete("/suppliers/{supplier_id}")
async def deactivate_supplier(
    supplier_id: UUID,
    repository: SupplierRepository = Depends(get_supplier_repository),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, object]:
    try:
        supplier = await repository.deactivate_supplier(supplier_id)
    except MasterDataNotFound as exc:
        raise _master_data_error(exc) from exc
    await audit.append(
        event_type=AuditEventType.SYSTEM_SUPPLIER_DEACTIVATED,
        actor=principal.sub,
        entity_type="supplier",
        entity_id=str(supplier.id),
        payload={"name": supplier.name},
    )
    return {"supplier": supplier.as_dict()}


@router.get("/customers")
async def list_customers(
    active: bool | None = Query(default=None),
    search: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    repository: CustomerRepository = Depends(get_customer_repository),
    _: Principal = Depends(require_roles(*READ_ROLES)),
) -> dict[str, object]:
    rows = await repository.list_customers(
        active=active,
        search=search,
        limit=limit,
        offset=offset,
    )
    total = await repository.count_customers(active=active, search=search)
    return {
        "customers": [row.as_dict() for row in rows],
        "count": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/customers", status_code=status.HTTP_201_CREATED)
async def create_customer(
    payload: CustomerPayload,
    repository: CustomerRepository = Depends(get_customer_repository),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, object]:
    try:
        customer = await repository.create_customer(_customer_input(payload))
    except MasterDataConflict as exc:
        raise _master_data_error(exc) from exc
    await audit.append(
        event_type=AuditEventType.SYSTEM_CUSTOMER_CREATED,
        actor=principal.sub,
        entity_type="customer",
        entity_id=str(customer.id),
        payload={"name": customer.name},
    )
    return {"customer": customer.as_dict()}


@router.get("/customers/{customer_id}")
async def get_customer(
    customer_id: UUID,
    repository: CustomerRepository = Depends(get_customer_repository),
    _: Principal = Depends(require_roles(*READ_ROLES)),
) -> dict[str, object]:
    customer = await repository.get_customer(customer_id)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="customer not found")
    return {"customer": customer.as_dict()}


@router.put("/customers/{customer_id}")
async def update_customer(
    customer_id: UUID,
    payload: CustomerPayload,
    repository: CustomerRepository = Depends(get_customer_repository),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, object]:
    try:
        customer = await repository.update_customer(customer_id, _customer_input(payload))
    except (MasterDataNotFound, MasterDataConflict) as exc:
        raise _master_data_error(exc) from exc
    await audit.append(
        event_type=AuditEventType.SYSTEM_CUSTOMER_UPDATED,
        actor=principal.sub,
        entity_type="customer",
        entity_id=str(customer.id),
        payload={"name": customer.name},
    )
    return {"customer": customer.as_dict()}


@router.delete("/customers/{customer_id}")
async def deactivate_customer(
    customer_id: UUID,
    repository: CustomerRepository = Depends(get_customer_repository),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, object]:
    try:
        customer = await repository.deactivate_customer(customer_id)
    except MasterDataNotFound as exc:
        raise _master_data_error(exc) from exc
    await audit.append(
        event_type=AuditEventType.SYSTEM_CUSTOMER_DEACTIVATED,
        actor=principal.sub,
        entity_type="customer",
        entity_id=str(customer.id),
        payload={"name": customer.name},
    )
    return {"customer": customer.as_dict()}
