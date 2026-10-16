"""M5 pharmacy API: stock receiving, FEFO dispensing, expiry monitoring.

Blueprint rules enforced here (§R4):
* stock lives per branch in batches with explicit expiry — no free-text
  medication stock; every unit traces to a ``stock_movements`` ledger row;
* dispensing is FEFO and requires ``pharmacy.dispense``; pharmacists
  dispense against a SIGNED prescription (or a registered patient for
  OTC), never editing the prescription itself;
* expired or recalled stock cannot leave the shelf (DB trigger backstop);
* stock adjustments are audited and reason-coded.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

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
from sqlalchemy.orm import Session

from ..auditing import record_event
from ..db import get_db
from ..deps import (
    Principal,
    get_client_ip,
    get_correlation_id,
    require_permission,
)
from ..models import (
    Dispense,
    DrugCatalog,
    Patient,
    Prescription,
    StockBatch,
    StockMovement,
)
from ..schemas import (
    AdjustStockIn,
    DispenseIn,
    DispenseItemOut,
    DispenseOut,
    PageOut,
    ReceiveStockIn,
    StockAlertOut,
    StockBatchOut,
    StockMovementOut,
)
from ..services.clinical import idempotent_replay, idempotent_store
from ..services.pharmacy import (
    EXPIRY_WARNING_DAYS,
    adjust_stock,
    dispense_items,
    receive_batch,
)

router = APIRouter(prefix="/pharmacy", tags=["pharmacy"])

MAX_LIMIT = 100


def _batch_out(batch: StockBatch, drug_name: str) -> StockBatchOut:
    out = StockBatchOut.model_validate(batch)
    out.generic_name = drug_name
    return out


def _drug_names(db: Session, drug_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not drug_ids:
        return {}
    rows = db.execute(
        select(DrugCatalog.id, DrugCatalog.generic_name).where(
            DrugCatalog.id.in_(drug_ids)
        )
    ).all()
    return {r[0]: r[1] for r in rows}


def _get_batch(db: Session, principal: Principal, batch_id: uuid.UUID) -> StockBatch:
    row = db.scalar(
        select(StockBatch).where(
            StockBatch.id == batch_id,
            StockBatch.organization_id == principal.organization_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Stock batch not found")
    return row


# --- stock receiving --------------------------------------------------------
@router.post("/stock/batches", response_model=StockBatchOut,
             status_code=status.HTTP_201_CREATED)
def post_receive_stock(
    payload: ReceiveStockIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_permission("pharmacy.manage")),
    db: Session = Depends(get_db),
) -> StockBatchOut:
    body = payload.model_dump(mode="json")
    replay = idempotent_replay(
        db,
        organization_id=principal.organization_id,
        key=idempotency_key,
        endpoint="POST /pharmacy/stock/batches",
        payload=body,
    )
    if replay:
        return StockBatchOut.model_validate(replay["body"])

    # same batch number twice at the same branch for the same drug -> 409
    dup = db.scalar(
        select(StockBatch.id).where(
            StockBatch.organization_id == principal.organization_id,
            StockBatch.branch_id == payload.branch_id,
            StockBatch.drug_id == payload.drug_id,
            StockBatch.batch_number == payload.batch_number,
        )
    )
    if dup is not None:
        raise HTTPException(
            status_code=409,
            detail="Batch number already exists for this drug/branch",
        )

    batch = receive_batch(
        db,
        organization_id=principal.organization_id,
        branch_id=payload.branch_id,
        drug_id=payload.drug_id,
        batch_number=payload.batch_number,
        expiry_date=payload.expiry_date,
        quantity=payload.quantity,
        unit_cost=payload.unit_cost,
        supplier=payload.supplier,
        actor_user_id=principal.user_id,
    )
    drug = db.get(DrugCatalog, payload.drug_id)
    out = _batch_out(batch, drug.generic_name if drug else "")
    record_event(
        db,
        action="pharmacy.stock_received",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="stock_batch",
        resource_id=str(batch.id),
        metadata={
            "drug_id": str(payload.drug_id),
            "batch_number": payload.batch_number,
            "quantity": payload.quantity,
            "expiry_date": payload.expiry_date.isoformat(),
        },
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    idempotent_store(
        db,
        organization_id=principal.organization_id,
        key=idempotency_key,
        endpoint="POST /pharmacy/stock/batches",
        payload=body,
        status=201,
        body=out.model_dump(mode="json"),
    )
    return out


@router.get("/stock/batches", response_model=PageOut)
def list_stock_batches(
    branch_id: uuid.UUID | None = None,
    drug_id: uuid.UUID | None = None,
    batch_status: str | None = Query(default=None, alias="status"),
    limit: int = 50,
    offset: int = 0,
    principal: Principal = Depends(require_permission("pharmacy.read")),
    db: Session = Depends(get_db),
) -> PageOut:
    limit = min(max(limit, 1), MAX_LIMIT)
    q = select(StockBatch).where(
        StockBatch.organization_id == principal.organization_id
    )
    if branch_id:
        q = q.where(StockBatch.branch_id == branch_id)
    if drug_id:
        q = q.where(StockBatch.drug_id == drug_id)
    if batch_status:
        q = q.where(StockBatch.status == batch_status)
    total = db.scalar(
        select(func.count()).select_from(q.subquery())
    )
    rows = db.scalars(
        q.order_by(StockBatch.expiry_date, StockBatch.received_at)
        .limit(limit).offset(offset)
    ).all()
    names = _drug_names(db, {r.drug_id for r in rows})
    return PageOut(
        items=[_batch_out(r, names.get(r.drug_id, "")) for r in rows],
        total=total or 0, limit=limit, offset=offset,
    )


@router.get("/stock/alerts", response_model=StockAlertOut)
def stock_alerts(
    principal: Principal = Depends(require_permission("pharmacy.read")),
    db: Session = Depends(get_db),
) -> StockAlertOut:
    today = date.today()
    horizon = today + timedelta(days=EXPIRY_WARNING_DAYS)

    def _rows(rows: list[StockBatch]) -> list[StockBatchOut]:
        names = _drug_names(db, {r.drug_id for r in rows})
        return [_batch_out(r, names.get(r.drug_id, "")) for r in rows]

    expiring = db.scalars(
        select(StockBatch).where(
            StockBatch.organization_id == principal.organization_id,
            StockBatch.status == "active",
            StockBatch.quantity_on_hand > 0,
            StockBatch.expiry_date >= today,
            StockBatch.expiry_date <= horizon,
        ).order_by(StockBatch.expiry_date)
    ).all()
    expired = db.scalars(
        select(StockBatch).where(
            StockBatch.organization_id == principal.organization_id,
            StockBatch.quantity_on_hand > 0,
            StockBatch.expiry_date < today,
        ).order_by(StockBatch.expiry_date)
    ).all()
    low = db.execute(
        select(
            StockBatch.drug_id,
            func.sum(StockBatch.quantity_on_hand).label("on_hand"),
        )
        .where(
            StockBatch.organization_id == principal.organization_id,
            StockBatch.status.in_(("active", "depleted")),
        )
        .group_by(StockBatch.drug_id)
        .having(func.sum(StockBatch.quantity_on_hand) < 10)
    ).all()
    names = _drug_names(db, {r[0] for r in low})
    return StockAlertOut(
        expiring_soon=_rows(expiring),
        expired=_rows(expired),
        low_stock=[
            {"drug_id": str(d), "generic_name": names.get(d, ""),
             "on_hand": int(on_hand)}
            for d, on_hand in low
        ],
    )


@router.post("/stock/batches/{batch_id}/adjust",
             response_model=StockMovementOut,
             status_code=status.HTTP_201_CREATED)
def post_adjust_stock(
    batch_id: uuid.UUID,
    payload: AdjustStockIn,
    request: Request,
    principal: Principal = Depends(require_permission("pharmacy.manage")),
    db: Session = Depends(get_db),
) -> StockMovementOut:
    batch = _get_batch(db, principal, batch_id)
    if batch.status == "recalled":
        raise HTTPException(status_code=409, detail="Batch is recalled and frozen")
    movement = adjust_stock(
        db,
        organization_id=principal.organization_id,
        batch=batch,
        quantity=payload.quantity,
        write_off=payload.write_off,
        reason=payload.reason,
        actor_user_id=principal.user_id,
    )
    record_event(
        db,
        action="pharmacy.stock_adjusted",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="stock_batch",
        resource_id=str(batch_id),
        metadata={
            "quantity": payload.quantity,
            "write_off": payload.write_off,
            "reason": payload.reason,
            "on_hand_after": batch.quantity_on_hand,
        },
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return StockMovementOut.model_validate(movement)


@router.get("/stock/movements", response_model=PageOut)
def list_stock_movements(
    batch_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
    principal: Principal = Depends(require_permission("pharmacy.read")),
    db: Session = Depends(get_db),
) -> PageOut:
    limit = min(max(limit, 1), MAX_LIMIT)
    q = select(StockMovement).where(
        StockMovement.organization_id == principal.organization_id
    )
    if batch_id:
        q = q.where(StockMovement.batch_id == batch_id)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(
        q.order_by(StockMovement.id.desc()).limit(limit).offset(offset)
    ).all()
    return PageOut(items=[StockMovementOut.model_validate(r) for r in rows],
                   total=total or 0, limit=limit, offset=offset)


# --- dispensing -------------------------------------------------------------
@router.post("/dispenses", response_model=DispenseOut,
             status_code=status.HTTP_201_CREATED)
def post_dispense(
    payload: DispenseIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_permission("pharmacy.dispense")),
    db: Session = Depends(get_db),
) -> DispenseOut:
    body = payload.model_dump(mode="json")
    replay = idempotent_replay(
        db,
        organization_id=principal.organization_id,
        key=idempotency_key,
        endpoint="POST /pharmacy/dispenses",
        payload=body,
    )
    if replay:
        return DispenseOut.model_validate(replay["body"])

    if payload.prescription_id is None and payload.patient_id is None:
        raise HTTPException(
            status_code=422,
            detail="prescription_id or patient_id is required",
        )

    rx_drug_ids: set[uuid.UUID] = set()
    patient_id = payload.patient_id
    if payload.prescription_id is not None:
        rx = db.scalar(
            select(Prescription).where(
                Prescription.id == payload.prescription_id,
                Prescription.organization_id == principal.organization_id,
            )
        )
        if rx is None:
            raise HTTPException(status_code=404, detail="Prescription not found")
        if rx.status != "signed":
            raise HTTPException(
                status_code=409,
                detail=f"Prescription must be signed (status: {rx.status})",
            )
        if patient_id is not None and patient_id != rx.patient_id:
            raise HTTPException(
                status_code=422,
                detail="patient_id does not match the prescription",
            )
        patient_id = rx.patient_id
        from ..models import PrescriptionItem
        rx_drug_ids = set(
            db.scalars(
                select(PrescriptionItem.drug_id).where(
                    PrescriptionItem.organization_id == principal.organization_id,
                    PrescriptionItem.prescription_id == rx.id,
                )
            ).all()
        )

    if patient_id is not None:
        patient = db.scalar(
            select(Patient).where(
                Patient.id == patient_id,
                Patient.organization_id == principal.organization_id,
            )
        )
        if patient is None or patient.status == "merged":
            raise HTTPException(status_code=404, detail="Patient not found")

    # against a signed Rx: may only dispense drugs ON that prescription
    if rx_drug_ids:
        not_on_rx = [
            str(i.drug_id) for i in payload.items if i.drug_id not in rx_drug_ids
        ]
        if not_on_rx:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Drug(s) not on prescription: " + ", ".join(not_on_rx)
                ),
            )

    dispense = Dispense(
        organization_id=principal.organization_id,
        branch_id=payload.branch_id or _infer_branch(db, principal, payload),
        prescription_id=payload.prescription_id,
        patient_id=patient_id,
        notes=payload.notes,
        dispensed_by=principal.user_id,
    )
    db.add(dispense)
    db.flush()

    items = dispense_items(
        db,
        organization_id=principal.organization_id,
        dispense=dispense,
        items=[(i.drug_id, i.quantity) for i in payload.items],
        actor_user_id=principal.user_id,
    )
    out = DispenseOut.model_validate(dispense)
    out.items = [DispenseItemOut.model_validate(i) for i in items]
    record_event(
        db,
        action="pharmacy.dispensed",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="dispense",
        resource_id=str(dispense.id),
        metadata={
            "prescription_id": str(payload.prescription_id) if payload.prescription_id else None,
            "patient_id": str(patient_id) if patient_id else None,
            "items": [
                {"drug_id": str(i.drug_id), "quantity": i.quantity}
                for i in payload.items
            ],
        },
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    idempotent_store(
        db,
        organization_id=principal.organization_id,
        key=idempotency_key,
        endpoint="POST /pharmacy/dispenses",
        payload=body,
        status=201,
        body=out.model_dump(mode="json"),
    )
    return out


def _infer_branch(db: Session, principal: Principal, payload: DispenseIn) -> uuid.UUID:
    """Dispense at the branch where the stock lives: the FIRST batch the FEFO
    allocation will touch. Simplification for M5: resolve the branch with
    available stock for the first requested drug."""
    from ..services.pharmacy import allocate_fefo  # noqa: F401 (doc-only)

    first = payload.items[0].drug_id
    row = db.scalar(
        select(StockBatch.branch_id).where(
            StockBatch.organization_id == principal.organization_id,
            StockBatch.drug_id == first,
            StockBatch.status == "active",
        )
        .order_by(StockBatch.expiry_date)
        .limit(1)
    )
    if row is None:
        raise HTTPException(
            status_code=409,
            detail="No branch holds stock for the requested drug",
        )
    return row[0]


@router.get("/dispenses", response_model=PageOut)
def list_dispenses(
    limit: int = 50,
    offset: int = 0,
    principal: Principal = Depends(require_permission("pharmacy.read")),
    db: Session = Depends(get_db),
) -> PageOut:
    limit = min(max(limit, 1), MAX_LIMIT)
    q = select(Dispense).where(
        Dispense.organization_id == principal.organization_id
    )
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(
        q.order_by(Dispense.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return PageOut(items=[DispenseOut.model_validate(r) for r in rows],
                   total=total or 0, limit=limit, offset=offset)
