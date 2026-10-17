"""M6 inventory API: general (non-pharmacy) supplies, movements, alerts.

The pharmacy ledger pattern applies unchanged:
* every quantity change is exactly ONE append-only ``inventory_movements``
  row; the item's on-hand cache is maintained by the
  ``apply_inventory_movement`` trigger, so negative stock is impossible
  even through raw SQL;
* receive/return move stock IN, issue moves it OUT, adjustments follow the
  signed-quantity / write_off rule of the pharmacy service;
* retired items are frozen (no movements);
* cross-tenant identifiers return 404 — existence must not leak.
"""
from __future__ import annotations

import uuid

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from ..auditing import record_event
from ..db import get_db
from ..deps import (
    Principal,
    get_client_ip,
    get_correlation_id,
    require_permission,
)
from ..models import InventoryItem, InventoryMovement
from ..schemas import (
    InventoryItemIn,
    InventoryItemOut,
    InventoryItemUpdateIn,
    InventoryMovementIn,
    InventoryMovementOut,
    PageOut,
)
from ..services.clinical import idempotent_replay, idempotent_store

router = APIRouter(prefix="/inventory", tags=["inventory"])

MAX_LIMIT = 100

_IN_BY_TYPE = ("receive", "return")


def _item_out(item: InventoryItem) -> InventoryItemOut:
    out = InventoryItemOut.model_validate(item)
    out.low_stock = (
        item.status == "active"
        and item.reorder_level > 0
        and item.quantity_on_hand <= item.reorder_level
    )
    return out


def _get_item(db: Session, principal: Principal,
              item_id: uuid.UUID) -> InventoryItem:
    row = db.scalar(
        select(InventoryItem).where(
            InventoryItem.id == item_id,
            InventoryItem.organization_id == principal.organization_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    return row


# --- items ------------------------------------------------------------------
@router.post("/items", response_model=InventoryItemOut,
             status_code=status.HTTP_201_CREATED)
def create_item(
    payload: InventoryItemIn,
    request: Request,
    principal: Principal = Depends(require_permission("inventory.manage")),
    db: Session = Depends(get_db),
) -> InventoryItemOut:
    dup = db.scalar(
        select(InventoryItem.id).where(
            InventoryItem.organization_id == principal.organization_id,
            InventoryItem.branch_id == payload.branch_id,
            InventoryItem.name == payload.name,
        )
    )
    if dup is not None:
        raise HTTPException(
            status_code=409,
            detail="An item with this name already exists at this branch",
        )
    item = InventoryItem(
        organization_id=principal.organization_id,
        branch_id=payload.branch_id,
        name=payload.name,
        sku=payload.sku,
        unit=payload.unit,
        reorder_level=payload.reorder_level,
        unit_cost=payload.unit_cost,
    )
    db.add(item)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Item name already exists")
    record_event(
        db,
        action="inventory.item_created",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="inventory_item",
        resource_id=str(item.id),
        metadata={"name": item.name, "unit": item.unit},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return _item_out(item)


@router.get("/items", response_model=PageOut)
def list_items(
    q: str | None = None,
    branch_id: uuid.UUID | None = None,
    item_status: str | None = Query(default=None, alias="status"),
    low_only: bool = False,
    limit: int = 50,
    offset: int = 0,
    principal: Principal = Depends(require_permission("inventory.read")),
    db: Session = Depends(get_db),
) -> PageOut:
    limit = min(max(limit, 1), MAX_LIMIT)
    query = select(InventoryItem).where(
        InventoryItem.organization_id == principal.organization_id
    )
    if q:
        query = query.where(InventoryItem.name.ilike(f"%{q}%"))
    if branch_id:
        query = query.where(InventoryItem.branch_id == branch_id)
    if item_status:
        query = query.where(InventoryItem.status == item_status)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(
        query.order_by(InventoryItem.name).limit(limit).offset(offset)
    ).all()
    items = [_item_out(r) for r in rows]
    if low_only:
        items = [i for i in items if i.low_stock]
    return PageOut(items=items, total=total or 0, limit=limit, offset=offset)


@router.patch("/items/{item_id}", response_model=InventoryItemOut)
def update_item(
    item_id: uuid.UUID,
    payload: InventoryItemUpdateIn,
    request: Request,
    principal: Principal = Depends(require_permission("inventory.manage")),
    db: Session = Depends(get_db),
) -> InventoryItemOut:
    item = _get_item(db, principal, item_id)
    changed: dict[str, object] = {}
    if payload.unit is not None and payload.unit != item.unit:
        item.unit = payload.unit
        changed["unit"] = payload.unit
    if payload.reorder_level is not None \
            and payload.reorder_level != item.reorder_level:
        item.reorder_level = payload.reorder_level
        changed["reorder_level"] = payload.reorder_level
    if payload.unit_cost is not None and payload.unit_cost != item.unit_cost:
        item.unit_cost = payload.unit_cost
        changed["unit_cost"] = payload.unit_cost
    if payload.status is not None and payload.status != item.status:
        item.status = payload.status
        changed["status"] = payload.status
    if changed:
        record_event(
            db,
            action="inventory.item_updated",
            organization_id=str(principal.organization_id),
            actor_user_id=str(principal.user_id),
            resource_type="inventory_item",
            resource_id=str(item.id),
            metadata=changed,
            correlation_id=get_correlation_id(request),
            ip_address=get_client_ip(request),
        )
    return _item_out(item)


# --- movements --------------------------------------------------------------
def _movement_direction(payload: InventoryMovementIn) -> str:
    if payload.movement_type in _IN_BY_TYPE:
        return "in"
    if payload.movement_type == "issue":
        return "out"
    # adjustment: signed quantity / explicit write-off (pharmacy rule)
    if payload.write_off:
        return "out"
    return "in" if payload.quantity > 0 else "out"


@router.post("/items/{item_id}/movements",
             response_model=InventoryMovementOut,
             status_code=status.HTTP_201_CREATED)
def post_movement(
    item_id: uuid.UUID,
    payload: InventoryMovementIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_permission("inventory.manage")),
    db: Session = Depends(get_db),
) -> InventoryMovementOut:
    # canonical body for idempotency: direction is derived, not accepted
    body = payload.model_dump(mode="json")
    body["item_id"] = str(item_id)
    body["direction"] = _movement_direction(payload)
    replay = idempotent_replay(
        db,
        organization_id=principal.organization_id,
        key=idempotency_key,
        endpoint="POST /inventory/movements",
        payload=body,
    )
    if replay:
        return InventoryMovementOut.model_validate(replay["body"])

    item = _get_item(db, principal, item_id)
    if item.status != "active":
        raise HTTPException(
            status_code=409, detail=f"Item is {item.status} and frozen"
        )

    movement = InventoryMovement(
        organization_id=principal.organization_id,
        item_id=item.id,
        movement_type=payload.movement_type,
        direction=body["direction"],
        quantity=abs(payload.quantity),
        reason=payload.reason,
        created_by=principal.user_id,
    )
    db.add(movement)
    try:
        db.flush()
    except DBAPIError as exc:
        # the apply trigger refuses oversell / frozen items (plpgsql raise)
        db.rollback()
        reason = str(getattr(exc, "orig", None) or exc).replace("\n", " ")
        raise HTTPException(
            status_code=409,
            detail=f"Stock movement refused: {reason}"[:300],
        )

    out = InventoryMovementOut.model_validate(movement)
    record_event(
        db,
        action="inventory.movement",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="inventory_item",
        resource_id=str(item.id),
        metadata={
            "movement_type": movement.movement_type,
            "direction": movement.direction,
            "quantity": movement.quantity,
            "on_hand_after": item.quantity_on_hand,
        },
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    idempotent_store(
        db,
        organization_id=principal.organization_id,
        key=idempotency_key,
        endpoint="POST /inventory/movements",
        payload=body,
        status=201,
        body=out.model_dump(mode="json"),
    )
    return out


@router.get("/movements", response_model=PageOut)
def list_movements(
    item_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
    principal: Principal = Depends(require_permission("inventory.read")),
    db: Session = Depends(get_db),
) -> PageOut:
    limit = min(max(limit, 1), MAX_LIMIT)
    q = select(InventoryMovement).where(
        InventoryMovement.organization_id == principal.organization_id
    )
    if item_id:
        q = q.where(InventoryMovement.item_id == item_id)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(
        q.order_by(InventoryMovement.id.desc()).limit(limit).offset(offset)
    ).all()
    return PageOut(
        items=[InventoryMovementOut.model_validate(r) for r in rows],
        total=total or 0, limit=limit, offset=offset,
    )


@router.get("/alerts", response_model=PageOut)
def low_stock_alerts(
    limit: int = 50,
    offset: int = 0,
    principal: Principal = Depends(require_permission("inventory.read")),
    db: Session = Depends(get_db),
) -> PageOut:
    limit = min(max(limit, 1), MAX_LIMIT)
    q = select(InventoryItem).where(
        InventoryItem.organization_id == principal.organization_id,
        InventoryItem.status == "active",
        InventoryItem.reorder_level > 0,
        InventoryItem.quantity_on_hand <= InventoryItem.reorder_level,
    )
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(
        q.order_by(InventoryItem.quantity_on_hand).limit(limit).offset(offset)
    ).all()
    return PageOut(
        items=[_item_out(r) for r in rows],
        total=total or 0, limit=limit, offset=offset,
    )