"""OPD queue: token issue and the doctor's authorized queue view.

Tokens are sequential per organization per service day and allocated under
a per-day advisory lock (safe under concurrent reception desks). State
machine: waiting -> called -> in_consultation -> completed; waiting may be
skipped or cancelled. Terminal states never move backwards (no silent
rewrites of clinical flow — every transition is audited).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import func, select, text
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
from ..models import Branch, OpdQueueEntry, Patient
from ..schemas import PageOut, QueueEntryOut, QueueJoinIn, QueueStatusIn
from ..services.clinical import idempotent_replay, idempotent_store

router = APIRouter(prefix="/opd/queue", tags=["opd-queue"])

MAX_LIMIT = 100
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "waiting": {"called", "cancelled", "skipped"},
    "called": {"in_consultation", "waiting", "cancelled", "skipped"},
    "in_consultation": {"completed"},
    "completed": set(),
    "skipped": {"called"},  # recall a patient who was about to leave
    "cancelled": set(),
}


def _today() -> date:
    return datetime.now(timezone.utc).date()


@router.post("", response_model=QueueEntryOut, status_code=status.HTTP_201_CREATED)
def join_queue(
    payload: QueueJoinIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_permission("queue.manage")),
    db: Session = Depends(get_db),
) -> QueueEntryOut:
    body = payload.model_dump(mode="json")
    replay = idempotent_replay(
        db,
        organization_id=principal.organization_id,
        key=idempotency_key,
        endpoint="POST /opd/queue",
        payload=body,
    )
    if replay:
        return QueueEntryOut.model_validate(replay["body"])

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

    service_day = _today()

    # already in today's active queue?
    already = db.scalar(
        select(func.count())
        .select_from(OpdQueueEntry)
        .where(
            OpdQueueEntry.organization_id == principal.organization_id,
            OpdQueueEntry.patient_id == payload.patient_id,
            OpdQueueEntry.service_date == service_day,
            OpdQueueEntry.status.in_(("waiting", "called", "in_consultation")),
        )
    )
    if already:
        raise HTTPException(
            status_code=409, detail="Patient already in today's active queue"
        )

    # serialize token allocation per tenant per day
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"opdqueue:{principal.organization_id}:{service_day}"},
    )
    last_token = db.scalar(
        select(func.max(OpdQueueEntry.token_number)).where(
            OpdQueueEntry.organization_id == principal.organization_id,
            OpdQueueEntry.service_date == service_day,
        )
    )
    entry = OpdQueueEntry(
        organization_id=principal.organization_id,
        branch_id=payload.branch_id,
        patient_id=payload.patient_id,
        appointment_id=payload.appointment_id,
        service_date=service_day,
        token_number=(last_token or 0) + 1,
        priority=payload.priority,
        created_by=principal.user_id,
    )
    db.add(entry)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Token race, please retry")

    out = QueueEntryOut.model_validate(entry)
    record_event(
        db,
        action="opd.queue_joined",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="opd_queue",
        resource_id=str(entry.id),
        metadata={"token": entry.token_number, "patient_id": str(entry.patient_id),
                  "priority": entry.priority},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    idempotent_store(
        db,
        organization_id=principal.organization_id,
        key=idempotency_key,
        endpoint="POST /opd/queue",
        payload=body,
        status=201,
        body=out.model_dump(mode="json"),
    )
    return out


@router.get("", response_model=PageOut)
def queue_board(
    service_date: date | None = None,
    status_filter: str | None = None,
    limit: int = 100,
    offset: int = 0,
    principal: Principal = Depends(require_permission("queue.read")),
    db: Session = Depends(get_db),
) -> PageOut:
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)
    day = service_date or _today()

    conditions = [
        OpdQueueEntry.organization_id == principal.organization_id,
        OpdQueueEntry.service_date == day,
    ]
    if status_filter:
        if status_filter not in (
            "waiting", "called", "in_consultation", "completed", "skipped", "cancelled"
        ):
            raise HTTPException(status_code=422, detail="Unknown status filter")
        conditions.append(OpdQueueEntry.status == status_filter)

    total = db.scalar(
        select(func.count()).select_from(OpdQueueEntry).where(*conditions)
    ) or 0
    rows = db.scalars(
        select(OpdQueueEntry)
        .where(*conditions)
        .order_by(OpdQueueEntry.token_number)
        .limit(limit)
        .offset(offset)
    ).all()
    return PageOut(
        items=[QueueEntryOut.model_validate(r).model_dump(mode="json") for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _transition_target(current: str, target: str) -> None:
    if target not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise HTTPException(
            status_code=409,
            detail=f"Illegal queue transition {current} -> {target}",
        )


@router.post("/{entry_id}/call", response_model=QueueEntryOut)
def call_patient(
    entry_id: uuid.UUID,
    payload: QueueStatusIn,
    request: Request,
    principal: Principal = Depends(require_permission("queue.manage")),
    db: Session = Depends(get_db),
) -> QueueEntryOut:
    return _advance(
        db,
        principal,
        request,
        entry_id,
        new_status="called",
        doctor_user_id=payload.doctor_user_id,
    )


@router.post("/{entry_id}/start", response_model=QueueEntryOut)
def start_consultation(
    entry_id: uuid.UUID,
    payload: QueueStatusIn,
    request: Request,
    principal: Principal = Depends(require_permission("queue.manage")),
    db: Session = Depends(get_db),
) -> QueueEntryOut:
    return _advance(
        db,
        principal,
        request,
        entry_id,
        new_status="in_consultation",
        doctor_user_id=payload.doctor_user_id,
    )


@router.post("/{entry_id}/complete", response_model=QueueEntryOut)
def complete_consultation(
    entry_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_permission("queue.manage")),
    db: Session = Depends(get_db),
) -> QueueEntryOut:
    return _advance(db, principal, request, entry_id, new_status="completed")


@router.post("/{entry_id}/skip", response_model=QueueEntryOut)
def skip_patient(
    entry_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_permission("queue.manage")),
    db: Session = Depends(get_db),
) -> QueueEntryOut:
    return _advance(db, principal, request, entry_id, new_status="skipped")


def _advance(
    db: Session,
    principal: Principal,
    request: Request,
    entry_id: uuid.UUID,
    *,
    new_status: str,
    doctor_user_id: uuid.UUID | None = None,
) -> QueueEntryOut:
    entry = db.scalar(
        select(OpdQueueEntry).where(
            OpdQueueEntry.id == entry_id,
            OpdQueueEntry.organization_id == principal.organization_id,
        )
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="Queue entry not found")
    _transition_target(entry.status, new_status)

    now = datetime.now(timezone.utc)
    entry.status = new_status
    entry.updated_at = now
    if new_status == "called":
        entry.called_at = now
        if doctor_user_id:
            entry.doctor_user_id = doctor_user_id
    elif new_status == "in_consultation":
        entry.started_at = now
        if doctor_user_id:
            entry.doctor_user_id = doctor_user_id
    elif new_status == "completed":
        entry.completed_at = now

    record_event(
        db,
        action=f"opd.queue_{new_status}",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="opd_queue",
        resource_id=str(entry.id),
        metadata={"token": entry.token_number,
                  "doctor_user_id": str(entry.doctor_user_id) if entry.doctor_user_id else None},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return QueueEntryOut.model_validate(entry)
