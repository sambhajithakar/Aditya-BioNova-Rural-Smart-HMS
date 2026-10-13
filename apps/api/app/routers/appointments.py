"""Appointments: scheduling with provider-conflict detection.

Conflict rule: for the same organization and provider, an active (scheduled
or checked_in) appointment overlapping the requested window is rejected with
409. Patient and branch references are validated INSIDE the tenant — a
foreign identifier returns 404, never a foreign key error leak.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..auditing import record_event
from ..db import get_db
from ..deps import (
    Principal,
    get_client_ip,
    get_correlation_id,
    require_permission,
)
from ..models import Appointment, Branch, Patient
from ..schemas import AppointmentIn, AppointmentOut, AppointmentUpdateIn, PageOut

router = APIRouter(prefix="/appointments", tags=["appointments"])

MAX_LIMIT = 100
_ACTIVE = ("scheduled", "checked_in")


def _load_patient_branch(
    db: Session, org_id: uuid.UUID, patient_id: uuid.UUID, branch_id: uuid.UUID
) -> None:
    patient = db.scalar(
        select(Patient).where(Patient.id == patient_id, Patient.organization_id == org_id)
    )
    if patient is None or patient.status == "merged":
        raise HTTPException(status_code=404, detail="Patient not found")
    branch = db.scalar(
        select(Branch).where(Branch.id == branch_id, Branch.organization_id == org_id)
    )
    if branch is None or branch.status != "active":
        raise HTTPException(status_code=404, detail="Branch not found or inactive")


def _assert_no_conflict(
    db: Session,
    *,
    org_id: uuid.UUID,
    provider_user_id: uuid.UUID | None,
    starts_at: datetime,
    ends_at: datetime,
    exclude_id: uuid.UUID | None = None,
) -> None:
    if provider_user_id is None:
        return
    stmt = select(func.count()).select_from(Appointment).where(
        Appointment.organization_id == org_id,
        Appointment.provider_user_id == provider_user_id,
        Appointment.status.in_(_ACTIVE),
        Appointment.starts_at < ends_at,
        Appointment.ends_at > starts_at,
    )
    if exclude_id:
        stmt = stmt.where(Appointment.id != exclude_id)
    if (db.scalar(stmt) or 0) > 0:
        raise HTTPException(
            status_code=409,
            detail="Provider already has an appointment in this time window",
        )


@router.post("", response_model=AppointmentOut, status_code=status.HTTP_201_CREATED)
def create_appointment(
    payload: AppointmentIn,
    request: Request,
    principal: Principal = Depends(require_permission("appointment.write")),
    db: Session = Depends(get_db),
) -> AppointmentOut:
    if payload.ends_at <= payload.starts_at:
        raise HTTPException(status_code=422, detail="ends_at must be after starts_at")

    _load_patient_branch(
        db, principal.organization_id, payload.patient_id, payload.branch_id
    )
    _assert_no_conflict(
        db,
        org_id=principal.organization_id,
        provider_user_id=payload.provider_user_id,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
    )

    appt = Appointment(
        organization_id=principal.organization_id,
        branch_id=payload.branch_id,
        patient_id=payload.patient_id,
        provider_user_id=payload.provider_user_id,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        reason=payload.reason,
        notes=payload.notes,
        created_by=principal.user_id,
    )
    db.add(appt)
    db.flush()

    record_event(
        db,
        action="appointment.created",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="appointment",
        resource_id=str(appt.id),
        metadata={"patient_id": str(appt.patient_id), "starts_at": appt.starts_at.isoformat()},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return AppointmentOut.model_validate(appt)


@router.get("", response_model=PageOut)
def list_appointments(
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    patient_id: uuid.UUID | None = None,
    status_filter: str | None = None,
    limit: int = 50,
    offset: int = 0,
    principal: Principal = Depends(require_permission("appointment.read")),
    db: Session = Depends(get_db),
) -> PageOut:
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)

    conditions = [Appointment.organization_id == principal.organization_id]
    if from_date:
        conditions.append(Appointment.starts_at >= from_date)
    if to_date:
        conditions.append(Appointment.starts_at < to_date)
    if patient_id:
        conditions.append(Appointment.patient_id == patient_id)
    if status_filter:
        if status_filter not in _ACTIVE + ("completed", "cancelled", "no_show"):
            raise HTTPException(status_code=422, detail="Unknown status filter")
        conditions.append(Appointment.status == status_filter)

    total = db.scalar(
        select(func.count()).select_from(Appointment).where(and_(*conditions))
    ) or 0
    rows = db.scalars(
        select(Appointment)
        .where(and_(*conditions))
        .order_by(Appointment.starts_at)
        .limit(limit)
        .offset(offset)
    ).all()
    return PageOut(
        items=[AppointmentOut.model_validate(r).model_dump(mode="json") for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{appointment_id}", response_model=AppointmentOut)
def get_appointment(
    appointment_id: uuid.UUID,
    principal: Principal = Depends(require_permission("appointment.read")),
    db: Session = Depends(get_db),
) -> AppointmentOut:
    row = db.scalar(
        select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.organization_id == principal.organization_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return AppointmentOut.model_validate(row)


@router.patch("/{appointment_id}", response_model=AppointmentOut)
def update_appointment(
    appointment_id: uuid.UUID,
    payload: AppointmentUpdateIn,
    request: Request,
    principal: Principal = Depends(require_permission("appointment.write")),
    db: Session = Depends(get_db),
) -> AppointmentOut:
    row = db.scalar(
        select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.organization_id == principal.organization_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    if row.status in ("completed", "cancelled"):
        raise HTTPException(
            status_code=409, detail=f"Cannot modify a {row.status} appointment"
        )

    changes = payload.model_dump(exclude_unset=True)
    new_start = changes.get("starts_at", row.starts_at)
    new_end = changes.get("ends_at", row.ends_at)
    if new_end <= new_start:
        raise HTTPException(status_code=422, detail="ends_at must be after starts_at")

    if "provider_user_id" in changes or "starts_at" in changes or "ends_at" in changes:
        _assert_no_conflict(
            db,
            org_id=principal.organization_id,
            provider_user_id=changes.get("provider_user_id", row.provider_user_id),
            starts_at=new_start,
            ends_at=new_end,
            exclude_id=row.id,
        )

    for field, value in changes.items():
        setattr(row, field, value)

    record_event(
        db,
        action="appointment.updated",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="appointment",
        resource_id=str(row.id),
        metadata={"fields": sorted(changes.keys()), "status": row.status},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return AppointmentOut.model_validate(row)
