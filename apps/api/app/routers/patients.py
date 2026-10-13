"""Patient registration, search, profile and supervised duplicate merge.

Tenant rules: organization_id always comes from the verified token; RLS is
the enforcement backstop. Cross-tenant identifiers return 404 (the row is
invisible), never 403 — existence itself must not leak.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
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
from ..models import Patient
from ..schemas import (
    DuplicateCandidate,
    PageOut,
    PatientIn,
    PatientMergeIn,
    PatientOut,
    PatientUpdateIn,
)
from ..services.clinical import (
    create_patient_uhid_retry,
    find_duplicates,
    idempotent_replay,
    idempotent_store,
)

router = APIRouter(prefix="/patients", tags=["patients"])

MAX_LIMIT = 100


@router.post("", response_model=PatientOut, status_code=status.HTTP_201_CREATED)
def register_patient(
    payload: PatientIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_permission("patient.write")),
    db: Session = Depends(get_db),
) -> PatientOut:
    body = payload.model_dump(mode="json")
    replay = idempotent_replay(
        db,
        organization_id=principal.organization_id,
        key=idempotency_key,
        endpoint="POST /patients",
        payload=body,
    )
    if replay:
        return PatientOut.model_validate(replay["body"])

    matches = find_duplicates(
        db,
        organization_id=principal.organization_id,
        full_name=payload.full_name,
        phone=payload.phone,
        date_of_birth=payload.date_of_birth,
    )
    if matches and not payload.allow_duplicates:
        candidates = [
            DuplicateCandidate(
                id=p.id,
                uhid=p.uhid,
                full_name=p.full_name,
                gender=p.gender,
                date_of_birth=p.date_of_birth,
                phone=p.phone,
                matched_on=fields,
            )
            for p, fields in matches
        ]
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "Possible duplicate registration. Confirm to create anyway "
                    "(allow_duplicates=true) or open the existing record."
                ),
                "candidates": [c.model_dump(mode="json") for c in candidates],
            },
        )

    uhid = create_patient_uhid_retry(db, principal.organization_id)
    patient = Patient(
        organization_id=principal.organization_id,  # from token
        uhid=uhid,
        full_name=payload.full_name,
        gender=payload.gender,
        date_of_birth=payload.date_of_birth,
        dob_estimated=payload.dob_estimated,
        phone=payload.phone,
        address=payload.address,
        blood_group=payload.blood_group,
        allergies=payload.allergies,
        created_by=principal.user_id,
    )
    db.add(patient)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="UHID race, please retry")

    out = PatientOut.model_validate(patient)
    record_event(
        db,
        action="patient.created",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="patient",
        resource_id=str(patient.id),
        metadata={
            "uhid": patient.uhid,
            "allow_duplicates": payload.allow_duplicates,
            "duplicate_matches": len(matches),
        },
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    idempotent_store(
        db,
        organization_id=principal.organization_id,
        key=idempotency_key,
        endpoint="POST /patients",
        payload=body,
        status=201,
        body=out.model_dump(mode="json"),
    )
    return out


@router.get("", response_model=PageOut)
def search_patients(
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
    principal: Principal = Depends(require_permission("patient.read")),
    db: Session = Depends(get_db),
) -> PageOut:
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)

    base = select(Patient).where(
        Patient.organization_id == principal.organization_id,
        Patient.status != "merged",
    )
    count_base = (
        select(func.count())
        .select_from(Patient)
        .where(
            Patient.organization_id == principal.organization_id,
            Patient.status != "merged",
        )
    )

    if q:
        needle = f"%{q.strip().lower()}%"
        match = (
            func.lower(Patient.full_name).like(needle)
            | func.lower(Patient.uhid).like(needle)
            | func.lower(func.coalesce(Patient.phone, "")).like(needle)
        )
        base = base.where(match)
        count_base = count_base.where(match)

    total = db.scalar(count_base) or 0
    rows = db.scalars(
        base.order_by(Patient.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return PageOut(
        items=[PatientOut.model_validate(r).model_dump(mode="json") for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{patient_id}", response_model=PatientOut)
def get_patient(
    patient_id: uuid.UUID,
    principal: Principal = Depends(require_permission("patient.read")),
    db: Session = Depends(get_db),
) -> PatientOut:
    row = db.scalar(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.organization_id == principal.organization_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return PatientOut.model_validate(row)


@router.patch("/{patient_id}", response_model=PatientOut)
def update_patient(
    patient_id: uuid.UUID,
    payload: PatientUpdateIn,
    request: Request,
    principal: Principal = Depends(require_permission("patient.write")),
    db: Session = Depends(get_db),
) -> PatientOut:
    row = db.scalar(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.organization_id == principal.organization_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    if row.status == "merged":
        raise HTTPException(
            status_code=409,
            detail="Record was merged into another patient and is read-only",
        )

    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(row, field, value)

    record_event(
        db,
        action="patient.updated",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="patient",
        resource_id=str(row.id),
        metadata={"fields": sorted(changes.keys())},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return PatientOut.model_validate(row)


@router.post("/{patient_id}/merge", response_model=PatientOut)
def merge_patient(
    patient_id: uuid.UUID,
    payload: PatientMergeIn,
    request: Request,
    principal: Principal = Depends(require_permission("patient.merge")),
    db: Session = Depends(get_db),
) -> PatientOut:
    """Supervised duplicate resolution: source record is retired into target.

    Nothing is deleted — the source keeps its UHID, points at the survivor
    (``merged_into_id``) and becomes read-only; both sides are audited.
    """
    if patient_id == payload.target_patient_id:
        raise HTTPException(status_code=409, detail="Cannot merge a patient into itself")

    source = db.scalar(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.organization_id == principal.organization_id,
        )
    )
    target = db.scalar(
        select(Patient).where(
            Patient.id == payload.target_patient_id,
            Patient.organization_id == principal.organization_id,
        )
    )
    if source is None or target is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    if source.status == "merged":
        raise HTTPException(status_code=409, detail="Source patient is already merged")
    if target.status == "merged":
        raise HTTPException(
            status_code=409,
            detail="Target patient is itself merged; pick the surviving record",
        )

    source.status = "merged"
    source.merged_into_id = target.id
    record_event(
        db,
        action="patient.merged",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="patient",
        resource_id=str(source.id),
        metadata={"merged_into": str(target.id), "source_uhid": source.uhid,
                  "target_uhid": target.uhid},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return PatientOut.model_validate(target)
