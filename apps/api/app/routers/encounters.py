"""Consultation workspace: encounters, vitals, structured diagnoses.

Rules enforced here (blueprint §R3.5):
* one OPEN encounter per patient per tenant (partial unique index — a
  second start attempt returns 409, never a silent duplicate);
* completing an encounter also advances its linked queue entry to
  ``completed`` (single audited action from the doctor's view);
* completed encounters are frozen by a database trigger — corrections go
  through a new encounter, never an edit of the record;
* all reads/writes are tenant-scoped from the verified token; foreign
  identifiers 404 (existence must not leak).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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
    Branch,
    Encounter,
    EncounterDiagnosis,
    EncounterVitals,
    OpdQueueEntry,
    Patient,
    Prescription,
)
from ..schemas import (
    DiagnosisIn,
    DiagnosisOut,
    EncounterDetailOut,
    EncounterIn,
    EncounterOut,
    EncounterUpdateIn,
    PageOut,
    VitalsIn,
    VitalsOut,
)

router = APIRouter(prefix="/encounters", tags=["encounters"])

MAX_LIMIT = 100


def _get_encounter(db: Session, principal: Principal, encounter_id: uuid.UUID) -> Encounter:
    row = db.scalar(
        select(Encounter).where(
            Encounter.id == encounter_id,
            Encounter.organization_id == principal.organization_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Encounter not found")
    return row


def _require_open(enc: Encounter) -> None:
    if enc.status != "open":
        raise HTTPException(
            status_code=409, detail="Encounter is completed and read-only"
        )


@router.post("", response_model=EncounterOut, status_code=status.HTTP_201_CREATED)
def start_encounter(
    payload: EncounterIn,
    request: Request,
    principal: Principal = Depends(require_permission("encounter.write")),
    db: Session = Depends(get_db),
) -> EncounterOut:
    patient = db.scalar(
        select(Patient).where(
            Patient.id == payload.patient_id,
            Patient.organization_id == principal.organization_id,
        )
    )
    if patient is None or patient.status == "merged":
        raise HTTPException(status_code=404, detail="Patient not found")
    branch = db.scalar(
        select(Branch).where(
            Branch.id == payload.branch_id,
            Branch.organization_id == principal.organization_id,
        )
    )
    if branch is None or branch.status != "active":
        raise HTTPException(status_code=404, detail="Branch not found or inactive")

    queue_entry = None
    if payload.queue_entry_id:
        queue_entry = db.scalar(
            select(OpdQueueEntry).where(
                OpdQueueEntry.id == payload.queue_entry_id,
                OpdQueueEntry.organization_id == principal.organization_id,
            )
        )
        if queue_entry is None:
            raise HTTPException(status_code=404, detail="Queue entry not found")
        if queue_entry.status not in ("called", "in_consultation"):
            raise HTTPException(
                status_code=409,
                detail=f"Queue entry is {queue_entry.status}; call the patient first",
            )
        if queue_entry.patient_id != payload.patient_id:
            raise HTTPException(
                status_code=409, detail="Queue entry belongs to another patient"
            )

    enc = Encounter(
        organization_id=principal.organization_id,
        branch_id=payload.branch_id,
        patient_id=payload.patient_id,
        appointment_id=payload.appointment_id,
        queue_entry_id=payload.queue_entry_id,
        provider_user_id=payload.provider_user_id or principal.user_id,
        chief_complaint=payload.chief_complaint,
        history=payload.history,
        examination=payload.examination,
        created_by=principal.user_id,
    )
    db.add(enc)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Patient already has an open encounter"
        )

    if queue_entry is not None and queue_entry.status == "called":
        queue_entry.status = "in_consultation"
        queue_entry.started_at = datetime.now(timezone.utc)
        queue_entry.updated_at = queue_entry.started_at

    record_event(
        db,
        action="encounter.started",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="encounter",
        resource_id=str(enc.id),
        metadata={
            "patient_id": str(enc.patient_id),
            "queue_entry_id": str(enc.queue_entry_id) if enc.queue_entry_id else None,
        },
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return EncounterOut.model_validate(enc)


@router.get("", response_model=PageOut)
def list_encounters(
    patient_id: uuid.UUID | None = None,
    enc_status: str | None = None,
    limit: int = 20,
    offset: int = 0,
    principal: Principal = Depends(require_permission("encounter.read")),
    db: Session = Depends(get_db),
) -> PageOut:
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)
    conditions = [Encounter.organization_id == principal.organization_id]
    if patient_id:
        conditions.append(Encounter.patient_id == patient_id)
    if enc_status:
        if enc_status not in ("open", "completed"):
            raise HTTPException(status_code=422, detail="Unknown status filter")
        conditions.append(Encounter.status == enc_status)

    total = db.scalar(
        select(func.count()).select_from(Encounter).where(*conditions)
    ) or 0
    rows = db.scalars(
        select(Encounter)
        .where(*conditions)
        .order_by(Encounter.started_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return PageOut(
        items=[EncounterOut.model_validate(r).model_dump(mode="json") for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{encounter_id}", response_model=EncounterDetailOut)
def get_encounter(
    encounter_id: uuid.UUID,
    principal: Principal = Depends(require_permission("encounter.read")),
    db: Session = Depends(get_db),
) -> EncounterDetailOut:
    enc = _get_encounter(db, principal, encounter_id)
    vitals = db.scalars(
        select(EncounterVitals)
        .where(
            EncounterVitals.organization_id == principal.organization_id,
            EncounterVitals.encounter_id == enc.id,
        )
        .order_by(EncounterVitals.recorded_at)
    ).all()
    diagnoses = db.scalars(
        select(EncounterDiagnosis)
        .where(
            EncounterDiagnosis.organization_id == principal.organization_id,
            EncounterDiagnosis.encounter_id == enc.id,
        )
        .order_by(EncounterDiagnosis.created_at)
    ).all()
    rx_ids = db.scalars(
        select(Prescription.id).where(
            Prescription.organization_id == principal.organization_id,
            Prescription.encounter_id == enc.id,
        )
    ).all()
    detail = EncounterDetailOut.model_validate(enc)
    detail.vitals = [VitalsOut.model_validate(v) for v in vitals]
    detail.diagnoses = [DiagnosisOut.model_validate(d) for d in diagnoses]
    detail.prescription_ids = list(rx_ids)
    return detail


@router.patch("/{encounter_id}", response_model=EncounterOut)
def update_encounter(
    encounter_id: uuid.UUID,
    payload: EncounterUpdateIn,
    request: Request,
    principal: Principal = Depends(require_permission("encounter.write")),
    db: Session = Depends(get_db),
) -> EncounterOut:
    enc = _get_encounter(db, principal, encounter_id)
    _require_open(enc)
    changes = payload.model_dump(exclude_unset=True)
    for field_name, value in changes.items():
        setattr(enc, field_name, value)
    enc.updated_at = datetime.now(timezone.utc)
    record_event(
        db,
        action="encounter.updated",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="encounter",
        resource_id=str(enc.id),
        metadata={"fields": sorted(changes.keys())},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return EncounterOut.model_validate(enc)


@router.post("/{encounter_id}/vitals", response_model=VitalsOut,
             status_code=status.HTTP_201_CREATED)
def record_vitals(
    encounter_id: uuid.UUID,
    payload: VitalsIn,
    request: Request,
    principal: Principal = Depends(require_permission("encounter.write")),
    db: Session = Depends(get_db),
) -> VitalsOut:
    enc = _get_encounter(db, principal, encounter_id)
    _require_open(enc)
    if payload.bp_systolic is not None and payload.bp_diastolic is not None:
        if payload.bp_systolic <= payload.bp_diastolic:
            raise HTTPException(
                status_code=422, detail="Systolic must exceed diastolic"
            )
    vitals = EncounterVitals(
        organization_id=principal.organization_id,
        encounter_id=enc.id,
        bp_systolic=payload.bp_systolic,
        bp_diastolic=payload.bp_diastolic,
        pulse=payload.pulse,
        temperature_c=payload.temperature_c,
        spo2=payload.spo2,
        respiratory_rate=payload.respiratory_rate,
        weight_kg=payload.weight_kg,
        height_cm=payload.height_cm,
        notes=payload.notes,
        recorded_by=principal.user_id,
    )
    db.add(vitals)
    db.flush()
    record_event(
        db,
        action="encounter.vitals_recorded",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="encounter",
        resource_id=str(enc.id),
        metadata={"spo2": payload.spo2, "pulse": payload.pulse},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return VitalsOut.model_validate(vitals)


@router.post("/{encounter_id}/diagnoses", response_model=DiagnosisOut,
             status_code=status.HTTP_201_CREATED)
def add_diagnosis(
    encounter_id: uuid.UUID,
    payload: DiagnosisIn,
    request: Request,
    principal: Principal = Depends(require_permission("encounter.write")),
    db: Session = Depends(get_db),
) -> DiagnosisOut:
    enc = _get_encounter(db, principal, encounter_id)
    _require_open(enc)
    dx = EncounterDiagnosis(
        organization_id=principal.organization_id,
        encounter_id=enc.id,
        code=payload.code,
        label=payload.label,
        kind=payload.kind,
        status=payload.status,
        recorded_by=principal.user_id,
    )
    db.add(dx)
    db.flush()
    record_event(
        db,
        action="encounter.diagnosis_recorded",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="encounter",
        resource_id=str(enc.id),
        metadata={"code": dx.code, "kind": dx.kind, "status": dx.status},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return DiagnosisOut.model_validate(dx)


@router.post("/{encounter_id}/complete", response_model=EncounterOut)
def complete_encounter(
    encounter_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_permission("encounter.write")),
    db: Session = Depends(get_db),
) -> EncounterOut:
    enc = _get_encounter(db, principal, encounter_id)
    _require_open(enc)
    now = datetime.now(timezone.utc)
    enc.status = "completed"
    enc.ended_at = now
    enc.updated_at = now

    if enc.queue_entry_id:
        entry = db.scalar(
            select(OpdQueueEntry).where(
                OpdQueueEntry.id == enc.queue_entry_id,
                OpdQueueEntry.organization_id == principal.organization_id,
            )
        )
        if entry is not None and entry.status in ("in_consultation", "called"):
            entry.status = "completed"
            entry.completed_at = now
            entry.updated_at = now

    record_event(
        db,
        action="encounter.completed",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="encounter",
        resource_id=str(enc.id),
        metadata={"patient_id": str(enc.patient_id)},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return EncounterOut.model_validate(enc)
