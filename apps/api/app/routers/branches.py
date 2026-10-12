"""Branch management. organization_id is taken from the verified token only."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
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
from ..models import Branch
from ..schemas import BranchIn, BranchOut

router = APIRouter(prefix="/branches", tags=["branches"])


@router.post("", response_model=BranchOut, status_code=status.HTTP_201_CREATED)
def create_branch(
    payload: BranchIn,
    request: Request,
    principal: Principal = Depends(require_permission("branch.manage")),
    db: Session = Depends(get_db),
) -> BranchOut:
    branch = Branch(
        organization_id=principal.organization_id,  # from token, never from body
        name=payload.name,
        code=payload.code,
    )
    db.add(branch)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Branch code already exists in this organization",
        )

    record_event(
        db,
        action="branch.created",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="branch",
        resource_id=str(branch.id),
        metadata={"code": branch.code, "name": branch.name},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return BranchOut.model_validate(branch)


@router.get("", response_model=list[BranchOut])
def list_branches(
    principal: Principal = Depends(require_permission("branch.read")),
    db: Session = Depends(get_db),
) -> list[BranchOut]:
    rows = db.scalars(
        select(Branch)
        .where(Branch.organization_id == principal.organization_id)
        .order_by(Branch.created_at)
    ).all()
    return [BranchOut.model_validate(r) for r in rows]


@router.get("/{branch_id}", response_model=BranchOut)
def get_branch(
    branch_id: uuid.UUID,
    principal: Principal = Depends(require_permission("branch.read")),
    db: Session = Depends(get_db),
) -> BranchOut:
    """Identifier manipulation across tenants returns 404 (RLS hides the row)."""
    row = db.scalar(
        select(Branch).where(
            Branch.id == branch_id,
            Branch.organization_id == principal.organization_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Branch not found")
    return BranchOut.model_validate(row)
