"""Organization members (staff accounts and their roles)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auditing import record_event
from ..db import get_db
from ..deps import Principal, get_client_ip, get_correlation_id, require_permission
from ..models import Branch, Membership, Role, User
from ..schemas import MemberCreateIn, MemberOut
from ..security import hash_password

router = APIRouter(prefix="/members", tags=["members"])

_ASSIGNABLE_NON_OWNER_ROLES = (
    "admin",
    "doctor",
    "nurse",
    "receptionist",
    "pharmacist",
    "lab_technician",
    "billing",
    "auditor",
    "support",
)


def _member_out(db: Session, m: Membership) -> MemberOut:
    user = db.get(User, m.user_id)
    role = db.get(Role, m.role_id)
    assert user is not None and role is not None
    return MemberOut(
        membership_id=m.id,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role_key=role.key,
        branch_id=m.branch_id,
        status=m.status,
        created_at=m.created_at,
    )


@router.post("", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def create_member(
    payload: MemberCreateIn,
    request: Request,
    principal: Principal = Depends(require_permission("member.manage")),
    db: Session = Depends(get_db),
) -> MemberOut:
    if payload.role_key == "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The owner role is granted only during organization registration",
        )
    role = db.scalar(select(Role).where(Role.key == payload.role_key))
    if role is None or role.key not in _ASSIGNABLE_NON_OWNER_ROLES:
        raise HTTPException(status_code=422, detail="Unknown or non-assignable role_key")

    if payload.branch_id is not None:
        branch = db.get(Branch, payload.branch_id)
        if branch is None:
            raise HTTPException(status_code=422, detail="branch_id not found in this organization")

    email = payload.email.lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            email=email,
            full_name=payload.full_name,
            password_hash=hash_password(payload.password),
        )
        db.add(user)
        db.flush()
        user_created = True
    else:
        user_created = False

    existing = db.scalar(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.organization_id == principal.organization_id,
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="User is already a member of this organization")

    membership = Membership(
        user_id=user.id,
        organization_id=principal.organization_id,
        branch_id=payload.branch_id,
        role_id=role.id,
    )
    db.add(membership)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Membership conflict")

    record_event(
        db,
        action="member.created",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="membership",
        resource_id=str(membership.id),
        metadata={"email": email, "role_key": role.key, "user_created": user_created},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return _member_out(db, membership)


@router.get("", response_model=list[MemberOut])
def list_members(
    principal: Principal = Depends(require_permission("member.read")),
    db: Session = Depends(get_db),
) -> list[MemberOut]:
    rows = db.scalars(
        select(Membership)
        .where(Membership.organization_id == principal.organization_id)
        .order_by(Membership.created_at)
    ).all()
    return [_member_out(db, m) for m in rows]
