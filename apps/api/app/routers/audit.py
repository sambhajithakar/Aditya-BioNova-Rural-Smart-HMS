"""Audit trail access (restricted; tenant-scoped by RLS)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, require_permission
from ..models import AuditEvent
from ..schemas import AuditEventOut, Page

router = APIRouter(prefix="/audit-events", tags=["audit"])


@router.get("", response_model=Page)
def list_audit_events(
    principal: Principal = Depends(require_permission("audit.read")),
    db: Session = Depends(get_db),
    action: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page:
    base = select(AuditEvent).where(
        AuditEvent.organization_id == principal.organization_id
    )
    if action:
        base = base.where(AuditEvent.action == action)
    total = db.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = db.scalars(
        base.order_by(AuditEvent.id.desc()).limit(limit).offset(offset)
    ).all()
    return Page(
        items=[AuditEventOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
