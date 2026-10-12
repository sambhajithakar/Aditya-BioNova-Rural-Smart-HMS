"""Authentication: register (org onboarding), login, refresh rotation, logout, me."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auditing import record_event, record_security_event
from ..config import get_settings
from ..db import get_db, set_request_context
from ..deps import (
    Principal,
    get_client_ip,
    get_correlation_id,
    get_current_principal,
)
from ..models import Membership, Organization, RefreshSession, Role, RolePermission, User
from ..schemas import (
    AuthSessionOut,
    LoginIn,
    MeOut,
    OrganizationOut,
    RefreshIn,
    RegisterIn,
    TokenPair,
    UserOut,
)
from ..security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

settings = get_settings()


def _invalid_credentials() -> HTTPException:
    # Generic message: never reveal whether the email exists.
    return HTTPException(status_code=401, detail="Invalid credentials")


def _new_session(
    db: Session,
    *,
    user: User,
    org_id: uuid.UUID,
    request: Request,
    family_id: uuid.UUID | None = None,
) -> tuple[RefreshSession, str, str]:
    """Create a session row; return (row, access_token, refresh_token)."""
    refresh_token, token_hash = new_refresh_token()
    session_row = RefreshSession(
        family_id=family_id or uuid.uuid4(),
        user_id=user.id,
        organization_id=org_id,
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=settings.refresh_token_ttl_seconds),
        user_agent=(request.headers.get("user-agent") or "")[:400] or None,
        ip_address=get_client_ip(request),
    )
    db.add(session_row)
    db.flush()
    access = create_access_token(
        user_id=str(user.id), session_id=str(session_row.id), org_id=str(org_id)
    )
    return session_row, access, refresh_token


def _token_pair(access: str, refresh: str) -> TokenPair:
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_ttl_seconds,
    )



def _revoke_session_family(family_id, when) -> None:
    """Revoke every live session of a rotation family (own transaction)."""
    from ..db import SessionLocal

    local = SessionLocal()
    try:
        local.execute(
            RefreshSession.__table__.update()
            .where(RefreshSession.family_id == family_id)
            .where(RefreshSession.revoked_at.is_(None))
            .values(revoked_at=when)
        )
        local.commit()
    finally:
        local.close()


def _permissions_for_role(db: Session, role_id: uuid.UUID) -> list[str]:
    rows = db.scalars(
        select(RolePermission.permission_key).where(RolePermission.role_id == role_id)
    ).all()
    return sorted(set(rows))


@router.post(
    "/register",
    response_model=AuthSessionOut,
    status_code=status.HTTP_201_CREATED,
)
def register(
    payload: RegisterIn,
    request: Request,
    db: Session = Depends(get_db),
) -> AuthSessionOut:
    """Onboard a new clinic/organization with its owner account.

    Creates organization + owner user + owner membership in one transaction.
    The transaction-local ``app.is_bootstrap`` flag is the only path through
    the organizations INSERT row-level policy.
    """
    email = payload.email.lower()
    ip = get_client_ip(request)
    cid = get_correlation_id(request)

    try:
        if db.scalar(select(User.id).where(User.email == email)):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email already exists",
            )

        user = User(
            email=email,
            full_name=payload.full_name,
            password_hash=hash_password(payload.password),
        )
        db.add(user)
        db.flush()

        # Claim the new org's id as tenant context BEFORE the row exists:
        # PostgreSQL applies SELECT row-policies to INSERT ... RETURNING, and
        # our org SELECT policy accepts `id = app.current_org_id`.
        org = Organization(
            id=uuid.uuid4(),
            name=payload.organization_name,
            slug=payload.organization_slug,
        )
        set_request_context(db, user_id=str(user.id), org_id=str(org.id), bootstrap=True)
        db.add(org)
        db.flush()

        set_request_context(db, user_id=str(user.id), org_id=str(org.id), bootstrap=False)
        owner_role = db.scalar(select(Role).where(Role.key == "owner"))
        if owner_role is None:  # pragma: no cover - migrations seed this
            raise RuntimeError("roles missing: run alembic migrations")
        membership = Membership(
            user_id=user.id, organization_id=org.id, role_id=owner_role.id
        )
        db.add(membership)
        db.flush()

        session_row, access, refresh_token = _new_session(
            db, user=user, org_id=org.id, request=request
        )

        record_event(
            db,
            action="auth.register",
            organization_id=str(org.id),
            actor_user_id=str(user.id),
            resource_type="organization",
            resource_id=str(org.id),
            metadata={"slug": org.slug},
            correlation_id=cid,
            ip_address=ip,
        )
        record_event(
            db,
            action="auth.login",
            organization_id=str(org.id),
            actor_user_id=str(user.id),
            resource_type="session",
            resource_id=str(session_row.id),
            metadata={"method": "password", "via": "register"},
            correlation_id=cid,
            ip_address=ip,
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Organization slug or email already in use",
        )

    owner_role_id = membership.role_id
    return AuthSessionOut(
        user=UserOut.model_validate(user),
        organization=OrganizationOut.model_validate(org),
        role="owner",
        permissions=_permissions_for_role(db, owner_role_id),
        tokens=_token_pair(access, refresh_token),
    )


@router.post("/login", response_model=AuthSessionOut)
def login(payload: LoginIn, request: Request, db: Session = Depends(get_db)) -> AuthSessionOut:
    email = payload.email.lower()
    ip = get_client_ip(request)
    cid = get_correlation_id(request)

    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        verify_password(payload.password, DUMMY_PASSWORD_HASH)  # timing equalisation
        record_security_event(
            action="auth.login_failed",
            metadata={"reason": "unknown_email"},
            correlation_id=cid,
            ip_address=ip,
        )
        raise _invalid_credentials()

    if not verify_password(payload.password, user.password_hash):
        record_security_event(
            action="auth.login_failed",
            actor_user_id=str(user.id),
            metadata={"reason": "bad_password"},
            correlation_id=cid,
            ip_address=ip,
        )
        raise _invalid_credentials()

    if user.status != "active":
        raise HTTPException(status_code=403, detail="Account is disabled")

    # Tenant context first: RLS then reveals only this user's memberships.
    set_request_context(db, user_id=str(user.id))
    memberships = list(db.scalars(select(Membership).where(Membership.user_id == user.id)))
    if not memberships:
        raise HTTPException(status_code=403, detail="No organization membership")

    if payload.organization_slug:
        wanted = payload.organization_slug.lower()
        chosen = None
        for m in memberships:
            org_row = db.get(Organization, m.organization_id)
            if org_row is not None and org_row.slug == wanted:
                chosen = m
                break
        if chosen is None:
            raise _invalid_credentials()
    elif len(memberships) == 1:
        chosen = memberships[0]
    else:
        slugs = []
        for m in memberships:
            org_row = db.get(Organization, m.organization_id)
            if org_row is not None:
                slugs.append(org_row.slug)
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Multiple organizations; specify organization_slug",
                "organization_slugs": slugs,
            },
        )

    if chosen.status != "active":
        raise HTTPException(status_code=403, detail="Membership is suspended")

    org_row = db.get(Organization, chosen.organization_id)
    if org_row is None or org_row.status != "active":
        raise HTTPException(status_code=403, detail="Organization is not active")

    set_request_context(db, user_id=str(user.id), org_id=str(org_row.id))
    session_row, access, refresh_token = _new_session(
        db, user=user, org_id=org_row.id, request=request
    )

    record_event(
        db,
        action="auth.login",
        organization_id=str(org_row.id),
        actor_user_id=str(user.id),
        resource_type="session",
        resource_id=str(session_row.id),
        metadata={"method": "password"},
        correlation_id=cid,
        ip_address=ip,
    )

    role = db.get(Role, chosen.role_id)
    return AuthSessionOut(
        user=UserOut.model_validate(user),
        organization=OrganizationOut.model_validate(org_row),
        role=role.key if role else "unknown",
        permissions=_permissions_for_role(db, chosen.role_id),
        tokens=_token_pair(access, refresh_token),
    )


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: RefreshIn, request: Request, db: Session = Depends(get_db)) -> TokenPair:
    """Rotate the refresh token. Replaying an already-rotated token revokes
    the whole session family (reuse detection) and is audited."""
    ip = get_client_ip(request)
    cid = get_correlation_id(request)
    token_hash = hash_refresh_token(payload.refresh_token)

    row = db.scalar(select(RefreshSession).where(RefreshSession.token_hash == token_hash))
    if row is None:
        raise _invalid_credentials()

    set_request_context(db, user_id=str(row.user_id), org_id=str(row.organization_id))
    now = datetime.now(timezone.utc)

    if row.revoked_at is not None:
        _revoke_session_family(row.family_id, now)
        record_security_event(
            action="auth.refresh_reuse_detected",
            organization_id=str(row.organization_id),
            actor_user_id=str(row.user_id),
            resource_type="session",
            resource_id=str(row.id),
            metadata={"family_id": str(row.family_id)},
            correlation_id=cid,
            ip_address=ip,
        )
        raise _invalid_credentials()

    if row.expires_at <= now:
        raise _invalid_credentials()

    user = db.get(User, row.user_id)
    if user is None or user.status != "active":
        raise _invalid_credentials()

    membership = db.scalar(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.organization_id == row.organization_id,
        )
    )
    if membership is None or membership.status != "active":
        raise HTTPException(status_code=403, detail="Membership is not active")

    row.revoked_at = now
    new_session, access, refresh_token = _new_session(
        db, user=user, org_id=row.organization_id, request=request, family_id=row.family_id
    )
    row.replaced_by = new_session.id

    record_event(
        db,
        action="auth.refresh",
        organization_id=str(row.organization_id),
        actor_user_id=str(user.id),
        resource_type="session",
        resource_id=str(new_session.id),
        correlation_id=cid,
        ip_address=ip,
    )
    return _token_pair(access, refresh_token)


@router.post("/logout")
def logout(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(RefreshSession, principal.session_id)
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
    record_event(
        db,
        action="auth.logout",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="session",
        resource_id=str(principal.session_id),
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return {"status": "logged_out"}


@router.get("/me", response_model=MeOut)
def me(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> MeOut:
    org_row = db.get(Organization, principal.organization_id)
    if org_row is None:
        raise HTTPException(status_code=403, detail="Organization not found")
    return MeOut(
        user=UserOut.model_validate(principal.user),
        organization=OrganizationOut.model_validate(org_row),
        membership_id=principal.membership.id,
        branch_id=principal.membership.branch_id,
        role=principal.role_key,
        permissions=sorted(principal.permissions),
    )
