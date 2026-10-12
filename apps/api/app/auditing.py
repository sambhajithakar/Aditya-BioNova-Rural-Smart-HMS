"""Append-only audit trail helpers.

Every sensitive read/write path records an event. Events are written through
the ``insert_audit_event()`` SECURITY DEFINER function (migration 0003):
it preserves the tenancy write-guard while avoiding ``INSERT ... RETURNING``
(PostgreSQL evaluates the table's SELECT row policies against RETURNING
output, which would reject org-NULL system events such as failed logins).

``record_security_event`` writes in its OWN transaction because it is used
on request paths that are about to roll back (bad credentials, refresh-token
reuse) — otherwise those events would be lost.
"""
from __future__ import annotations

from typing import Any

import json

from sqlalchemy import text
from sqlalchemy.orm import Session

_INSERT_FN = text(
    "SELECT insert_audit_event("
    ":organization_id, :actor_user_id, :action, :resource_type, :resource_id, "
    "CAST(:metadata AS jsonb), :correlation_id, :ip_address)"
)


def _params(
    *,
    action: str,
    organization_id: str | None,
    actor_user_id: str | None,
    resource_type: str | None,
    resource_id: str | None,
    metadata: dict[str, Any] | None,
    correlation_id: str | None,
    ip_address: str | None,
) -> dict:
    return {
        "organization_id": organization_id,
        "actor_user_id": actor_user_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "metadata": json.dumps(metadata or {}, ensure_ascii=False),
        "correlation_id": correlation_id,
        "ip_address": ip_address,
    }


def record_event(
    db: Session,
    *,
    action: str,
    organization_id: str | None = None,
    actor_user_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    correlation_id: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Record an audit event inside the caller's transaction."""
    db.execute(
        _INSERT_FN,
        _params(
            action=action,
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata,
            correlation_id=correlation_id,
            ip_address=ip_address,
        ),
    )


def record_security_event(
    *,
    action: str,
    organization_id: str | None = None,
    actor_user_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    correlation_id: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Write a security-relevant audit event in its OWN committed transaction."""
    from .db import SessionLocal

    db = SessionLocal()
    try:
        db.execute(
            _INSERT_FN,
            _params(
                action=action,
                organization_id=organization_id,
                actor_user_id=actor_user_id,
                resource_type=resource_type,
                resource_id=resource_id,
                metadata=metadata,
                correlation_id=correlation_id,
                ip_address=ip_address,
            ),
        )
        db.commit()
    finally:
        db.close()
