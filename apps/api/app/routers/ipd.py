"""M6 IPD API: wards/beds, admissions, transfers, discharge, nursing.

Blueprint rules enforced here (§R3.4):
* a bed can hold at most one inpatient and a patient at most one active
  admission — the partial unique indexes are the hard backstop on the
  database, the API answers 409 first;
* bed occupancy is DERIVED from admissions; ``beds.status`` only tracks
  physical availability (maintenance/blocked beds refuse admissions);
* an admission moves admitted -> discharged exactly once; discharge needs
  an outcome + summary; the trigger freezes everything afterwards;
* nursing records are append-only and only legal while the stay is open;
* cross-tenant identifiers return 404 — existence must not leak.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

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
    Admission,
    AdmissionTransfer,
    Bed,
    NursingRecord,
    Patient,
    Ward,
)
from ..schemas import (
    AdmissionIn,
    AdmissionOut,
    BedIn,
    BedOut,
    BedUpdateIn,
    DischargeIn,
    NursingRecordIn,
    NursingRecordOut,
    PageOut,
    TransferIn,
    WardIn,
    WardOut,
)
from ..services.clinical import idempotent_replay, idempotent_store

router = APIRouter(prefix="/ipd", tags=["ipd"])

MAX_LIMIT = 100


def _ward_out(db: Session, ward: Ward) -> WardOut:
    out = WardOut.model_validate(ward)
    beds = db.scalars(
        select(Bed).where(
            Bed.organization_id == ward.organization_id,
            Bed.ward_id == ward.id,
        )
    ).all()
    out.bed_count = len(beds)
    occupied = _occupied_bed_ids(db, ward.organization_id)
    out.occupied_count = sum(1 for b in beds if b.id in occupied)
    out.available_count = sum(
        1 for b in beds if b.status == "available" and b.id not in occupied
    )
    return out


def _occupied_bed_ids(db: Session, organization_id: uuid.UUID) -> set[uuid.UUID]:
    rows = db.scalars(
        select(Admission.bed_id).where(
            Admission.organization_id == organization_id,
            Admission.status == "admitted",
        )
    ).all()
    return set(rows)


def _bed_out_map(db: Session, organization_id: uuid.UUID) -> dict[uuid.UUID, tuple]:
    """bed_id -> (admission_id, patient_id, admitted_at) for active beds."""
    rows = db.execute(
        select(Admission.bed_id, Admission.id, Admission.patient_id,
               Admission.admitted_at)
        .where(
            Admission.organization_id == organization_id,
            Admission.status == "admitted",
        )
    ).all()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def _enrich(db: Session, rows: list[Admission]) -> list[AdmissionOut]:
    if not rows:
        return []
    patient_ids = {r.patient_id for r in rows}
    names = {
        p.id: (p.uhid, p.full_name)
        for p in db.scalars(
            select(Patient).where(Patient.id.in_(patient_ids))
        ).all()
    }
    out = []
    for row in rows:
        item = AdmissionOut.model_validate(row)
        uhid, name = names.get(row.patient_id, ("", ""))
        item.patient_uhid, item.patient_full_name = uhid, name
        out.append(item)
    return out


def _get_admission(db: Session, principal: Principal,
                   admission_id: uuid.UUID) -> Admission:
    row = db.scalar(
        select(Admission).where(
            Admission.id == admission_id,
            Admission.organization_id == principal.organization_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Admission not found")
    return row


# --- wards ------------------------------------------------------------------
@router.post("/wards", response_model=WardOut, status_code=status.HTTP_201_CREATED)
def create_ward(
    payload: WardIn,
    request: Request,
    principal: Principal = Depends(require_permission("bed.manage")),
    db: Session = Depends(get_db),
) -> WardOut:
    dup = db.scalar(
        select(Ward.id).where(
            Ward.organization_id == principal.organization_id,
            Ward.branch_id == payload.branch_id,
            Ward.code == payload.code,
        )
    )
    if dup is not None:
        raise HTTPException(
            status_code=409, detail="Ward code already exists at this branch"
        )
    ward = Ward(
        organization_id=principal.organization_id,
        branch_id=payload.branch_id,
        name=payload.name,
        code=payload.code,
        ward_type=payload.ward_type,
    )
    db.add(ward)
    db.flush()
    out = WardOut.model_validate(ward)
    record_event(
        db,
        action="ipd.ward_created",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="ward",
        resource_id=str(ward.id),
        metadata={"code": ward.code, "ward_type": ward.ward_type},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return out


@router.get("/wards", response_model=list[WardOut])
def list_wards(
    branch_id: uuid.UUID | None = None,
    principal: Principal = Depends(require_permission("bed.read")),
    db: Session = Depends(get_db),
) -> list[WardOut]:
    q = select(Ward).where(
        Ward.organization_id == principal.organization_id
    )
    if branch_id:
        q = q.where(Ward.branch_id == branch_id)
    rows = db.scalars(q.order_by(Ward.code)).all()
    return [_ward_out(db, w) for w in rows]


# --- beds -------------------------------------------------------------------
@router.post("/beds", response_model=BedOut, status_code=status.HTTP_201_CREATED)
def create_bed(
    payload: BedIn,
    request: Request,
    principal: Principal = Depends(require_permission("bed.manage")),
    db: Session = Depends(get_db),
) -> BedOut:
    ward = db.scalar(
        select(Ward).where(
            Ward.id == payload.ward_id,
            Ward.organization_id == principal.organization_id,
        )
    )
    if ward is None:
        raise HTTPException(status_code=404, detail="Ward not found")
    dup = db.scalar(
        select(Bed.id).where(
            Bed.organization_id == principal.organization_id,
            Bed.ward_id == payload.ward_id,
            Bed.label == payload.label,
        )
    )
    if dup is not None:
        raise HTTPException(
            status_code=409, detail="Bed label already exists in this ward"
        )
    bed = Bed(
        organization_id=principal.organization_id,
        ward_id=payload.ward_id,
        label=payload.label,
    )
    db.add(bed)
    db.flush()
    record_event(
        db,
        action="ipd.bed_created",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="bed",
        resource_id=str(bed.id),
        metadata={"ward_id": str(ward.id), "label": bed.label},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return BedOut.model_validate(bed)


@router.get("/beds", response_model=list[BedOut])
def list_beds(
    ward_id: uuid.UUID | None = None,
    bed_status: str | None = Query(default=None, alias="status"),
    principal: Principal = Depends(require_permission("bed.read")),
    db: Session = Depends(get_db),
) -> list[BedOut]:
    q = select(Bed).where(Bed.organization_id == principal.organization_id)
    if ward_id:
        q = q.where(Bed.ward_id == ward_id)
    if bed_status:
        q = q.where(Bed.status == bed_status)
    rows = db.scalars(q.order_by(Bed.label)).all()
    occupied = _bed_out_map(db, principal.organization_id)
    out = []
    for bed in rows:
        item = BedOut.model_validate(bed)
        hit = occupied.get(bed.id)
        if hit:
            item.admission_id, item.patient_id, item.admitted_at = hit
        out.append(item)
    return out


@router.patch("/beds/{bed_id}", response_model=BedOut)
def update_bed(
    bed_id: uuid.UUID,
    payload: BedUpdateIn,
    request: Request,
    principal: Principal = Depends(require_permission("bed.manage")),
    db: Session = Depends(get_db),
) -> BedOut:
    bed = db.scalar(
        select(Bed).where(
            Bed.id == bed_id,
            Bed.organization_id == principal.organization_id,
        )
    )
    if bed is None:
        raise HTTPException(status_code=404, detail="Bed not found")
    if bed_id in _occupied_bed_ids(db, principal.organization_id) \
            and payload.status != "available":
        raise HTTPException(
            status_code=409, detail="Bed is occupied; discharge first"
        )
    if bed.status != payload.status:
        bed.status = payload.status
        record_event(
            db,
            action="ipd.bed_status_changed",
            organization_id=str(principal.organization_id),
            actor_user_id=str(principal.user_id),
            resource_type="bed",
            resource_id=str(bed.id),
            metadata={"status": bed.status},
            correlation_id=get_correlation_id(request),
            ip_address=get_client_ip(request),
        )
    return BedOut.model_validate(bed)


# --- admissions -------------------------------------------------------------
@router.post("/admissions", response_model=AdmissionOut,
             status_code=status.HTTP_201_CREATED)
def admit_patient(
    payload: AdmissionIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_permission("admission.manage")),
    db: Session = Depends(get_db),
) -> AdmissionOut:
    body = payload.model_dump(mode="json")
    replay = idempotent_replay(
        db,
        organization_id=principal.organization_id,
        key=idempotency_key,
        endpoint="POST /ipd/admissions",
        payload=body,
    )
    if replay:
        return AdmissionOut.model_validate(replay["body"])

    patient = db.scalar(
        select(Patient).where(
            Patient.id == payload.patient_id,
            Patient.organization_id == principal.organization_id,
        )
    )
    if patient is None or patient.status == "merged":
        raise HTTPException(status_code=404, detail="Patient not found")

    ward = db.scalar(
        select(Ward).where(
            Ward.id == payload.ward_id,
            Ward.organization_id == principal.organization_id,
        )
    )
    if ward is None:
        raise HTTPException(status_code=404, detail="Ward not found")
    if ward.status != "active":
        raise HTTPException(status_code=409, detail="Ward is closed")
    bed = db.scalar(
        select(Bed).where(
            Bed.id == payload.bed_id,
            Bed.organization_id == principal.organization_id,
        )
    )
    if bed is None:
        raise HTTPException(status_code=404, detail="Bed not found")
    if bed.ward_id != ward.id:
        raise HTTPException(
            status_code=422, detail="Bed does not belong to the ward"
        )
    if bed.status != "available":
        raise HTTPException(
            status_code=409, detail=f"Bed is not available ({bed.status})"
        )

    occupied = db.scalar(
        select(Admission.id).where(
            Admission.organization_id == principal.organization_id,
            Admission.status == "admitted",
            Admission.bed_id == bed.id,
        )
    )
    if occupied is not None:
        raise HTTPException(status_code=409, detail="Bed is already occupied")
    already = db.scalar(
        select(Admission.id).where(
            Admission.organization_id == principal.organization_id,
            Admission.status == "admitted",
            Admission.patient_id == patient.id,
        )
    )
    if already is not None:
        raise HTTPException(
            status_code=409, detail="Patient already has an active admission"
        )

    admission = Admission(
        organization_id=principal.organization_id,
        patient_id=patient.id,
        branch_id=ward.branch_id,
        ward_id=ward.id,
        bed_id=bed.id,
        reason=payload.reason,
        attending_user_id=payload.attending_user_id,
        admitted_by=principal.user_id,
    )
    db.add(admission)
    try:
        db.flush()
    except IntegrityError:
        # partial unique index backstop: concurrent admit raced us
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Bed or patient was admitted concurrently, please retry",
        )

    out = _enrich(db, [admission])[0]
    record_event(
        db,
        action="ipd.admission_created",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="admission",
        resource_id=str(admission.id),
        metadata={
            "patient_id": str(patient.id),
            "bed_id": str(bed.id),
            "ward_id": str(ward.id),
            "reason": payload.reason,
        },
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    idempotent_store(
        db,
        organization_id=principal.organization_id,
        key=idempotency_key,
        endpoint="POST /ipd/admissions",
        payload=body,
        status=201,
        body=out.model_dump(mode="json"),
    )
    return out


@router.get("/admissions", response_model=PageOut)
def list_admissions(
    admission_status: str | None = Query(default=None, alias="status"),
    ward_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
    principal: Principal = Depends(require_permission("bed.read")),
    db: Session = Depends(get_db),
) -> PageOut:
    limit = min(max(limit, 1), MAX_LIMIT)
    q = select(Admission).where(
        Admission.organization_id == principal.organization_id
    )
    if admission_status:
        q = q.where(Admission.status == admission_status)
    if ward_id:
        q = q.where(Admission.ward_id == ward_id)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(
        q.order_by(Admission.admitted_at.desc())
        .limit(limit).offset(offset)
    ).all()
    return PageOut(
        items=_enrich(db, rows),
        total=total or 0, limit=limit, offset=offset,
    )


@router.get("/admissions/{admission_id}", response_model=AdmissionOut)
def get_admission(
    admission_id: uuid.UUID,
    principal: Principal = Depends(require_permission("bed.read")),
    db: Session = Depends(get_db),
) -> AdmissionOut:
    row = _get_admission(db, principal, admission_id)
    return _enrich(db, [row])[0]


@router.post("/admissions/{admission_id}/transfer", response_model=AdmissionOut)
def transfer_admission(
    admission_id: uuid.UUID,
    payload: TransferIn,
    request: Request,
    principal: Principal = Depends(require_permission("admission.manage")),
    db: Session = Depends(get_db),
) -> AdmissionOut:
    admission = _get_admission(db, principal, admission_id)
    if admission.status != "admitted":
        raise HTTPException(status_code=409, detail="Admission is discharged")

    target = db.scalar(
        select(Bed).where(
            Bed.id == payload.to_bed_id,
            Bed.organization_id == principal.organization_id,
        )
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Target bed not found")
    if target.id == admission.bed_id:
        raise HTTPException(
            status_code=422, detail="Patient is already in that bed"
        )
    if target.status != "available":
        raise HTTPException(
            status_code=409, detail=f"Target bed is not available ({target.status})"
        )
    occupied = db.scalar(
        select(Admission.id).where(
            Admission.organization_id == principal.organization_id,
            Admission.status == "admitted",
            Admission.bed_id == target.id,
        )
    )
    if occupied is not None:
        raise HTTPException(
            status_code=409, detail="Target bed is already occupied"
        )

    from_bed_id = admission.bed_id
    ward = db.get(Ward, target.ward_id)
    if ward is None or ward.organization_id != principal.organization_id:
        raise HTTPException(status_code=404, detail="Target ward not found")

    db.add(
        AdmissionTransfer(
            organization_id=principal.organization_id,
            admission_id=admission.id,
            from_bed_id=from_bed_id,
            to_bed_id=target.id,
            reason=payload.reason,
            transferred_by=principal.user_id,
        )
    )
    admission.bed_id = target.id
    admission.ward_id = ward.id
    db.flush()

    out = _enrich(db, [admission])[0]
    record_event(
        db,
        action="ipd.admission_transferred",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="admission",
        resource_id=str(admission.id),
        metadata={
            "from_bed_id": str(from_bed_id),
            "to_bed_id": str(target.id),
            "reason": payload.reason,
        },
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return out


@router.post("/admissions/{admission_id}/discharge", response_model=AdmissionOut)
def discharge_admission(
    admission_id: uuid.UUID,
    payload: DischargeIn,
    request: Request,
    principal: Principal = Depends(require_permission("discharge")),
    db: Session = Depends(get_db),
) -> AdmissionOut:
    admission = _get_admission(db, principal, admission_id)
    if admission.status != "admitted":
        raise HTTPException(
            status_code=409,
            detail=f"Admission already discharged ({admission.discharge_outcome})",
        )

    admission.status = "discharged"
    admission.discharge_outcome = payload.outcome
    admission.discharge_summary = payload.summary
    admission.discharged_at = datetime.now(timezone.utc)
    admission.discharged_by = principal.user_id
    db.flush()

    out = _enrich(db, [admission])[0]
    record_event(
        db,
        action="ipd.admission_discharged",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="admission",
        resource_id=str(admission.id),
        metadata={
            "outcome": payload.outcome,
            "bed_id": str(admission.bed_id),
            "los_hint": True,
        },
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return out


# --- nursing ----------------------------------------------------------------
@router.post("/admissions/{admission_id}/nursing",
             response_model=NursingRecordOut,
             status_code=status.HTTP_201_CREATED)
def add_nursing_record(
    admission_id: uuid.UUID,
    payload: NursingRecordIn,
    request: Request,
    principal: Principal = Depends(require_permission("nursing.write")),
    db: Session = Depends(get_db),
) -> NursingRecordOut:
    admission = _get_admission(db, principal, admission_id)
    if admission.status != "admitted":
        raise HTTPException(
            status_code=409,
            detail="Nursing records can only be added while admitted",
        )
    record = NursingRecord(
        organization_id=principal.organization_id,
        admission_id=admission.id,
        note_type=payload.note_type,
        note=payload.note,
        bp_systolic=payload.bp_systolic,
        bp_diastolic=payload.bp_diastolic,
        pulse=payload.pulse,
        respiratory_rate=payload.respiratory_rate,
        temperature_c=payload.temperature_c,
        spo2=payload.spo2,
        recorded_by=principal.user_id,
    )
    db.add(record)
    db.flush()
    record_event(
        db,
        action="ipd.nursing_recorded",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="nursing_record",
        resource_id=str(record.id),
        metadata={"admission_id": str(admission.id), "note_type": record.note_type},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return NursingRecordOut.model_validate(record)


@router.get("/admissions/{admission_id}/nursing", response_model=PageOut)
def list_nursing_records(
    admission_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
    principal: Principal = Depends(require_permission("nursing.read")),
    db: Session = Depends(get_db),
) -> PageOut:
    limit = min(max(limit, 1), MAX_LIMIT)
    admission = _get_admission(db, principal, admission_id)
    q = select(NursingRecord).where(
        NursingRecord.organization_id == principal.organization_id,
        NursingRecord.admission_id == admission.id,
    )
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(
        q.order_by(NursingRecord.recorded_at.desc())
        .limit(limit).offset(offset)
    ).all()
    return PageOut(
        items=[NursingRecordOut.model_validate(r) for r in rows],
        total=total or 0, limit=limit, offset=offset,
    )