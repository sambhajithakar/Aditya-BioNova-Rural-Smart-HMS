"""Prescriptions: draft -> sign -> amend (versioned, immutable).

Blueprint rules enforced (§R3.5):
* ONLY practitioners with ``prescription.sign`` sign; the signer is stored
  on the row and in the audit event — never the drafting clerk;
* no silent post-sign changes: content edits and deletes are rejected by
  the database trigger; an amendment inserts a NEW version (version+1,
  supersedes_id) and flips the previous row to ``superseded`` (the single
  legal post-sign UPDATE, checked column-by-column by the trigger);
* allergy check runs BEFORE signing: a hit blocks the sign unless the
  prescriber explicitly acknowledges it (acknowledgement is persisted and
  audited);
* every drug must come from the validated ``drug_catalog`` — free-text
  medication entry is impossible by construction;
* duplicate-drug warnings are advisory but returned by POST /warnings and
  embedded in sign failures.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
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
    DrugCatalog,
    Patient,
    Prescription,
    PrescriptionItem,
)
from ..schemas import (
    AmendRxIn,
    DrugOut,
    PageOut,
    PrescriptionDetailOut,
    PrescriptionIn,
    PrescriptionOut,
    RxItemOut,
    RxWarningsOut,
    SignRxIn,
)
from ..services.billing import assert_valid_drugs, rx_warnings

router = APIRouter(prefix="/prescriptions", tags=["prescriptions"])

MAX_LIMIT = 100


def _get_rx(db: Session, principal: Principal, rx_id: uuid.UUID) -> Prescription:
    row = db.scalar(
        select(Prescription).where(
            Prescription.id == rx_id,
            Prescription.organization_id == principal.organization_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Prescription not found")
    return row


def _detail(db: Session, principal: Principal, rx: Prescription) -> PrescriptionDetailOut:
    items = db.scalars(
        select(PrescriptionItem)
        .where(
            PrescriptionItem.organization_id == principal.organization_id,
            PrescriptionItem.prescription_id == rx.id,
        )
        .order_by(PrescriptionItem.seq)
    ).all()
    detail = PrescriptionDetailOut.model_validate(rx)
    detail.items = [RxItemOut.model_validate(i) for i in items]
    return detail


def _load_patient(db: Session, principal: Principal, patient_id: uuid.UUID) -> Patient:
    patient = db.scalar(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.organization_id == principal.organization_id,
        )
    )
    if patient is None or patient.status == "merged":
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


def _build_items(
    db: Session,
    principal: Principal,
    rx: Prescription,
    items_in: list,
) -> list[uuid.UUID]:
    drug_ids = [i.drug_id for i in items_in]
    assert_valid_drugs(db, drug_ids)
    for seq, item in enumerate(items_in):
        db.add(
            PrescriptionItem(
                organization_id=principal.organization_id,
                prescription_id=rx.id,
                drug_id=item.drug_id,
                dose=item.dose,
                route=item.route,
                frequency=item.frequency,
                duration_days=item.duration_days,
                quantity=item.quantity,
                instructions=item.instructions,
                seq=seq,
            )
        )
    db.flush()
    return drug_ids


def _evaluate_warnings(
    db: Session, principal: Principal, patient: Patient, drug_ids: list[uuid.UUID]
) -> RxWarningsOut:
    raw = rx_warnings(
        db,
        organization_id=principal.organization_id,
        patient=patient,
        item_drug_ids=drug_ids,
    )
    warnings = [
        {"kind": w["kind"], "message": w["message"],
         "drug_ids": [uuid.UUID(d) for d in w["drug_ids"]]}
        for w in raw
    ]
    return RxWarningsOut(
        warnings=warnings,
        blocking=any(w["kind"] == "allergy" for w in warnings),
    )


@router.get("/drugs", response_model=list[DrugOut])
def list_drugs(
    q: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(require_permission("prescription.read")),
    db: Session = Depends(get_db),
) -> list[DrugOut]:
    limit = max(1, min(limit, 200))
    stmt = select(DrugCatalog).where(DrugCatalog.is_active.is_(True))
    if q:
        needle = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            func.lower(DrugCatalog.generic_name).like(needle)
            | func.lower(DrugCatalog.brand_hint).like(needle)
        )
    rows = db.scalars(stmt.order_by(DrugCatalog.generic_name).limit(limit)).all()
    return [DrugOut.model_validate(r) for r in rows]


@router.post("", response_model=PrescriptionDetailOut,
             status_code=status.HTTP_201_CREATED)
def create_draft(
    payload: PrescriptionIn,
    request: Request,
    principal: Principal = Depends(require_permission("prescription.sign")),
    db: Session = Depends(get_db),
) -> PrescriptionDetailOut:
    patient = _load_patient(db, principal, payload.patient_id)
    rx = Prescription(
        organization_id=principal.organization_id,
        encounter_id=payload.encounter_id,
        patient_id=payload.patient_id,
        created_by=principal.user_id,
        diagnosis_text=payload.diagnosis_text,
        advice=payload.advice,
        follow_up=payload.follow_up,
        status="draft",
        version=1,
    )
    db.add(rx)
    db.flush()
    drug_ids = _build_items(db, principal, rx, payload.items)

    record_event(
        db,
        action="prescription.drafted",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="prescription",
        resource_id=str(rx.id),
        metadata={"patient_id": str(patient.id), "items": len(drug_ids)},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return _detail(db, principal, rx)


@router.post("/{rx_id}/warnings", response_model=RxWarningsOut)
def check_warnings(
    rx_id: uuid.UUID,
    principal: Principal = Depends(require_permission("prescription.sign")),
    db: Session = Depends(get_db),
) -> RxWarningsOut:
    """Pre-flight safety check (allergy + duplicate) without signing."""
    rx = _get_rx(db, principal, rx_id)
    patient = _load_patient(db, principal, rx.patient_id)
    drug_ids = db.scalars(
        select(PrescriptionItem.drug_id).where(
            PrescriptionItem.organization_id == principal.organization_id,
            PrescriptionItem.prescription_id == rx.id,
        )
    ).all()
    return _evaluate_warnings(db, principal, patient, list(drug_ids))


@router.get("", response_model=PageOut)
def list_prescriptions(
    patient_id: uuid.UUID | None = None,
    rx_status: str | None = None,
    limit: int = 20,
    offset: int = 0,
    principal: Principal = Depends(require_permission("prescription.read")),
    db: Session = Depends(get_db),
) -> PageOut:
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)
    conditions = [Prescription.organization_id == principal.organization_id]
    if patient_id:
        conditions.append(Prescription.patient_id == patient_id)
    if rx_status:
        if rx_status not in ("draft", "signed", "superseded"):
            raise HTTPException(status_code=422, detail="Unknown status filter")
        conditions.append(Prescription.status == rx_status)

    total = db.scalar(
        select(func.count()).select_from(Prescription).where(*conditions)
    ) or 0
    rows = db.scalars(
        select(Prescription)
        .where(*conditions)
        .order_by(Prescription.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return PageOut(
        items=[PrescriptionOut.model_validate(r).model_dump(mode="json") for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{rx_id}", response_model=PrescriptionDetailOut)
def get_prescription(
    rx_id: uuid.UUID,
    principal: Principal = Depends(require_permission("prescription.read")),
    db: Session = Depends(get_db),
) -> PrescriptionDetailOut:
    return _detail(db, principal, _get_rx(db, principal, rx_id))


@router.post("/{rx_id}/sign", response_model=PrescriptionDetailOut)
def sign_prescription(
    rx_id: uuid.UUID,
    payload: SignRxIn,
    request: Request,
    principal: Principal = Depends(require_permission("prescription.sign")),
    db: Session = Depends(get_db),
) -> PrescriptionDetailOut:
    rx = _get_rx(db, principal, rx_id)
    if rx.status != "draft":
        raise HTTPException(
            status_code=409, detail=f"Prescription is {rx.status}, not a draft"
        )
    patient = _load_patient(db, principal, rx.patient_id)
    drug_ids = list(
        db.scalars(
            select(PrescriptionItem.drug_id).where(
                PrescriptionItem.organization_id == principal.organization_id,
                PrescriptionItem.prescription_id == rx.id,
            )
        ).all()
    )
    if not drug_ids:
        raise HTTPException(status_code=422, detail="Prescription has no items")

    warnings = _evaluate_warnings(db, principal, patient, drug_ids)
    blocking = [w for w in warnings.warnings if w.kind == "allergy"]
    if blocking and not payload.acknowledge_allergies:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Allergy conflict — acknowledge to sign anyway",
                "warnings": [w.model_dump(mode="json") for w in blocking],
            },
        )

    now = datetime.now(timezone.utc)
    rx.status = "signed"
    rx.signed_at = now
    rx.prescriber_user_id = principal.user_id  # signer != drafter is allowed
    rx.allergy_acknowledged = bool(blocking) and payload.acknowledge_allergies
    rx.updated_at = now

    record_event(
        db,
        action="prescription.signed",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="prescription",
        resource_id=str(rx.id),
        metadata={
            "patient_id": str(rx.patient_id),
            "version": rx.version,
            "allergy_acknowledged": rx.allergy_acknowledged,
            "warnings": [w.kind for w in warnings.warnings],
        },
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return _detail(db, principal, rx)


@router.post("/{rx_id}/amend", response_model=PrescriptionDetailOut,
             status_code=status.HTTP_201_CREATED)
def amend_prescription(
    rx_id: uuid.UUID,
    payload: AmendRxIn,
    request: Request,
    principal: Principal = Depends(require_permission("prescription.sign")),
    db: Session = Depends(get_db),
) -> PrescriptionDetailOut:
    """Create version N+1. The old row is flipped to 'superseded' — the only
    UPDATE the database trigger permits on a signed prescription."""
    old = _get_rx(db, principal, rx_id)
    if old.status == "superseded":
        raise HTTPException(
            status_code=409,
            detail="Prescription already superseded; amend the latest version",
        )
    if old.status == "draft":
        raise HTTPException(
            status_code=409, detail="Draft can be edited directly, not amended"
        )
    patient = _load_patient(db, principal, old.patient_id)

    new_rx = Prescription(
        organization_id=principal.organization_id,
        encounter_id=old.encounter_id,
        patient_id=old.patient_id,
        created_by=principal.user_id,
        diagnosis_text=payload.diagnosis_text or old.diagnosis_text,
        advice=payload.advice or old.advice,
        follow_up=payload.follow_up or old.follow_up,
        status="draft",
        version=old.version + 1,
        supersedes_id=old.id,
        amendment_reason=payload.reason,
    )
    db.add(new_rx)
    db.flush()
    drug_ids = _build_items(db, principal, new_rx, payload.items)

    warnings = _evaluate_warnings(db, principal, patient, drug_ids)
    blocking = [w for w in warnings.warnings if w.kind == "allergy"]
    if blocking and not payload.acknowledge_allergies:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Allergy conflict — acknowledge to sign anyway",
                "warnings": [w.model_dump(mode="json") for w in blocking],
            },
        )

    now = datetime.now(timezone.utc)
    # flip old -> superseded (trigger verifies everything else unchanged)
    old.status = "superseded"
    old.updated_at = now
    new_rx.status = "signed"
    new_rx.signed_at = now
    new_rx.prescriber_user_id = principal.user_id
    new_rx.allergy_acknowledged = bool(blocking) and payload.acknowledge_allergies
    new_rx.updated_at = now

    record_event(
        db,
        action="prescription.amended",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="prescription",
        resource_id=str(new_rx.id),
        metadata={
            "supersedes": str(old.id),
            "version": new_rx.version,
            "reason": payload.reason,
        },
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return _detail(db, principal, new_rx)
