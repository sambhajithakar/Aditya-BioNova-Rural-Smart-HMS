"""M5 pharmacy services: FEFO allocation and stock bookkeeping.

FEFO (first-expiry-first-out)
-----------------------------
Dispensing allocates quantity across batches ordered by the EARLIEST
``expiry_date`` first (ties broken by ``received_at`` then id), skipping
expired and recalled batches. Batches are locked ``FOR UPDATE`` in that
order so two concurrent dispensations of the same stock serialize instead
of overselling; the append-only ``stock_movements`` ledger plus the
``apply_stock_movement`` trigger are the hard backstop (negative stock is
impossible even via raw SQL).

Stock bookkeeping
-----------------
Every quantity change writes exactly ONE movement row. The trigger keeps
``stock_batches.quantity_on_hand`` equal to SUM(in) - SUM(out); the
application never writes that column.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Dispense, DispenseItem, StockBatch, StockMovement

EXPIRY_WARNING_DAYS = 90


def receive_batch(
    db: Session,
    *,
    organization_id: uuid.UUID,
    branch_id: uuid.UUID,
    drug_id: uuid.UUID,
    batch_number: str,
    expiry_date: date,
    quantity: int,
    unit_cost: float,
    supplier: str,
    actor_user_id: uuid.UUID | None,
) -> StockBatch:
    """Create a batch + its opening 'receive' movement (one transaction)."""
    from ..models import DrugCatalog

    drug = db.get(DrugCatalog, drug_id)
    if drug is None or not drug.is_active:
        raise HTTPException(status_code=404, detail="Drug not found in catalog")

    batch = StockBatch(
        organization_id=organization_id,
        branch_id=branch_id,
        drug_id=drug_id,
        batch_number=batch_number,
        expiry_date=expiry_date,
        quantity_received=quantity,
        quantity_on_hand=0,
        unit_cost=unit_cost,
        supplier=supplier,
        status="active",
        received_by=actor_user_id,
    )
    db.add(batch)
    db.flush()

    db.add(
        StockMovement(
            organization_id=organization_id,
            batch_id=batch.id,
            movement_type="receive",
            direction="in",
            quantity=quantity,
            reason=f"Received batch {batch_number}",
            created_by=actor_user_id,
        )
    )
    db.flush()
    db.refresh(batch)
    return batch


def allocate_fefo(
    db: Session,
    *,
    organization_id: uuid.UUID,
    branch_id: uuid.UUID,
    drug_id: uuid.UUID,
    quantity: int,
) -> list[tuple[StockBatch, int]]:
    """Lock and allocate ``quantity`` units FEFO. Returns [(batch, qty)].

    Raises 409 when unexpired, un-recalled stock is insufficient.
    """
    today = date.today()
    batches = db.scalars(
        select(StockBatch)
        .where(
            StockBatch.organization_id == organization_id,
            StockBatch.branch_id == branch_id,
            StockBatch.drug_id == drug_id,
            StockBatch.status.in_(("active", "depleted")),
            StockBatch.expiry_date >= today,
            StockBatch.quantity_on_hand > 0,
        )
        .order_by(StockBatch.expiry_date, StockBatch.received_at, StockBatch.id)
        .with_for_update()
    ).all()

    remaining = quantity
    allocations: list[tuple[StockBatch, int]] = []
    for batch in batches:
        if remaining <= 0:
            break
        take = min(remaining, batch.quantity_on_hand)
        if take > 0:
            allocations.append((batch, take))
            remaining -= take

    if remaining > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Insufficient unexpired stock for drug {drug_id}: "
                f"{quantity - remaining} of {quantity} units available (FEFO)"
            ),
        )
    return allocations


def dispense_items(
    db: Session,
    *,
    organization_id: uuid.UUID,
    dispense: Dispense,
    items: list[tuple[uuid.UUID, int]],
    actor_user_id: uuid.UUID | None,
) -> list[DispenseItem]:
    """Allocate FEFO per drug, write dispense items + 'dispense' movements."""
    out: list[DispenseItem] = []
    for drug_id, qty in items:
        allocations = allocate_fefo(
            db,
            organization_id=organization_id,
            branch_id=dispense.branch_id,
            drug_id=drug_id,
            quantity=qty,
        )
        for batch, take in allocations:
            item = DispenseItem(
                organization_id=organization_id,
                dispense_id=dispense.id,
                batch_id=batch.id,
                drug_id=drug_id,
                quantity=take,
                unit_price=batch.unit_cost,
            )
            db.add(item)
            db.flush()
            db.add(
                StockMovement(
                    organization_id=organization_id,
                    batch_id=batch.id,
                    movement_type="dispense",
                    direction="out",
                    quantity=take,
                    dispense_id=dispense.id,
                    patient_id=dispense.patient_id,
                    reason=f"Dispense {dispense.id}",
                    created_by=actor_user_id,
                )
            )
            db.flush()
            out.append(item)
    return out


def adjust_stock(
    db: Session,
    *,
    organization_id: uuid.UUID,
    batch: StockBatch,
    quantity: int,
    write_off: bool,
    reason: str,
    actor_user_id: uuid.UUID | None,
) -> StockMovement:
    """Stock correction: quantity>0 IN (found), write_off=True OUT (spoilage).

    A write-off forces direction OUT regardless of sign, so expiry
    write-offs are always a decrement.
    """
    if quantity == 0:
        raise HTTPException(status_code=422, detail="quantity must be non-zero")
    direction = "out" if (write_off or quantity < 0) else "in"
    movement = StockMovement(
        organization_id=organization_id,
        batch_id=batch.id,
        movement_type="adjustment",
        direction=direction,
        quantity=abs(quantity),
        reason=reason,
        created_by=actor_user_id,
    )
    db.add(movement)
    db.flush()
    db.refresh(batch)
    return movement


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
