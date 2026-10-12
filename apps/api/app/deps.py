"""Authentication, tenant context and RBAC dependencies.

Security invariants enforced here:
* Tenant/org identity comes from the verified JWT **and** the server-side
  session — never from headers or query parameters supplied by the client.
* Access tokens are rejected once their backing session is revoked/expired,
  so logout and refresh-rotation take effect immediately.
* Every authenticated request installs the transaction-local RLS context.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db, set_request_context
from .models import Membership, RefreshSession, Role, RolePermission, User
from .security import decode_access_token

_bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


@dataclass
class Principal:
    user: User
    organization_id: uuid.UUID
    session_id: uuid.UUID
    membership: Membership
    role_key: str
    permissions: set[str] = field(default_factory=set)

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id


def get_client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:60]
    return request.client.host[:60] if request.client else None


def get_correlation_id(request: Request) -> str:
    return getattr(request.state, "correlation_id", "")


def get_current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()

    try:
        claims = decode_access_token(credentials.credentials)
    except pyjwt.PyJWTError:
        raise _unauthorized("Invalid or expired token")

    session_id = uuid.UUID(claims["sid"])
    org_id = uuid.UUID(claims["org"])
    user_id = uuid.UUID(claims["sub"])

    session_row = db.get(RefreshSession, session_id)
    if (
        session_row is None
        or session_row.revoked_at is not None
        or session_row.expires_at <= datetime.now(timezone.utc)
        or session_row.user_id != user_id
        or session_row.organization_id != org_id
    ):
        raise _unauthorized("Session is no longer valid")

    user = db.get(User, user_id)
    if user is None or user.status != "active":
        raise _unauthorized("Account is not active")

    # Install tenant context for PostgreSQL row-level security BEFORE any
    # tenant-scoped query runs.
    set_request_context(
        db, user_id=str(user.id), org_id=str(org_id), bootstrap=False
    )

    membership = db.scalar(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.organization_id == org_id,
            Membership.status == "active",
        )
    )
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No active membership for this organization",
        )

    role = db.get(Role, membership.role_id)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Membership role missing"
        )

    if user.is_superadmin:
        from .models import Permission

        permissions = set(db.scalars(select(Permission.key)).all())
    else:
        permissions = set(
            db.scalars(
                select(RolePermission.permission_key).where(
                    RolePermission.role_id == role.id
                )
            ).all()
        )

    return Principal(
        user=user,
        organization_id=org_id,
        session_id=session_id,
        membership=membership,
        role_key=role.key,
        permissions=permissions,
    )


def require_permission(permission_key: str):
    """Route guard: 403 unless the principal's role holds the permission."""

    def checker(
        principal: Principal = Depends(get_current_principal),
    ) -> Principal:
        if permission_key not in principal.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {permission_key}",
            )
        return principal

    return checker
