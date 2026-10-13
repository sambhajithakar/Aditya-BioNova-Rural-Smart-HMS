"""M2 clinical helpers: UHID issuance, duplicate detection, idempotency.

UHID
----
Per-tenant sequential identifier ``P0000001`` (7 digits, unique inside the
organization only). Issuance takes a per-organization advisory transaction
lock so two concurrent registrations can never receive the same number; the
``uq_patient_org_uhid`` constraint remains the hard backstop (one retry on
IntegrityError).

Duplicate detection (supervised)
-------------------------------
Registration searches the tenant's own patients by normalized phone OR by
(lower(full_name), date_of_birth). Candidates are returned to the caller;
creation proceeds only if the operator confirms (``allow_duplicates``) —
matching the blueprint rule that patient merge/dedupe is a supervised,
audited action, never automatic.

Idempotency
-----------
POSTs may carry an ``Idempotency-Key`` header. The first request stores its
response (status + JSON) keyed by (organization, key); a retry with the same
payload replays the stored response instead of creating a second record, and
a retry with a *different* payload is rejected with 409.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import IdempotencyKey, Patient

UHID_ATTEMPTS = 3


def normalize_phone(value: str | None) -> str | None:
    """Digits only, drop a leading country code 91 / trunk 0 (10-digit base)."""
    if not value:
        return None
    digits = "".join(c for c in value if c.isdigit())
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits


def next_uhid(db: Session, organization_id: uuid.UUID) -> str:
    """Allocate the next UHID inside the tenant (serialized by advisory lock)."""
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"uhid:{organization_id}"},
    )
    last = db.scalar(
        select(func.max(Patient.uhid)).where(
            Patient.organization_id == organization_id
        )
    )
    seq = int(last[1:]) + 1 if last else 1
    return f"P{seq:07d}"


def create_patient_uhid_retry(
    db: Session, organization_id: uuid.UUID
) -> str:
    """Retry once on the (extremely unlikely) UHID race."""
    for attempt in range(UHID_ATTEMPTS):
        uhid = next_uhid(db, organization_id)
        exists = db.scalar(
            select(Patient.id).where(
                Patient.organization_id == organization_id,
                Patient.uhid == uhid,
            )
        )
        if exists is None:
            return uhid
        if attempt == UHID_ATTEMPTS - 1:
            raise HTTPException(status_code=503, detail="UHID allocation busy, retry")
    raise AssertionError("unreachable")


def find_duplicates(
    db: Session,
    *,
    organization_id: uuid.UUID,
    full_name: str,
    phone: str | None,
    date_of_birth,
    exclude_id: uuid.UUID | None = None,
) -> list[tuple[Patient, list[str]]]:
    """Return (patient, matched_fields) pairs from the SAME tenant only."""
    results: dict[uuid.UUID, tuple[Patient, list[str]]] = {}

    if phone:
        rows = db.scalars(
            select(Patient).where(
                Patient.organization_id == organization_id,
                func.lower(func.coalesce(Patient.phone, "")) == phone,
            )
        ).all()
        for row in rows:
            if exclude_id and row.id == exclude_id:
                continue
            results[row.id] = (row, ["phone"])

    if full_name and date_of_birth is not None:
        rows = db.scalars(
            select(Patient).where(
                Patient.organization_id == organization_id,
                func.lower(Patient.full_name) == full_name.lower(),
                Patient.date_of_birth == date_of_birth,
            )
        ).all()
        for row in rows:
            if exclude_id and row.id == exclude_id:
                continue
            if row.id in results:
                results[row.id][1].append("name+dob")
            else:
                results[row.id] = (row, ["name+dob"])

    return list(results.values())


# --- idempotency ------------------------------------------------------------
def _hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def idempotent_replay(
    db: Session,
    *,
    organization_id: uuid.UUID,
    key: str | None,
    endpoint: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the stored response for a retried request, or None.

    Raises 409 when the same key is reused with a DIFFERENT payload.
    """
    if not key:
        return None
    row = db.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.organization_id == organization_id,
            IdempotencyKey.idempotency_key == key,
        )
    )
    if row is None:
        return None
    if row.endpoint != endpoint or row.request_hash != _hash(payload):
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key reused with a different payload",
        )
    return {
        "replayed": True,
        "status": row.response_status,
        "body": row.response_body,
    }


def idempotent_store(
    db: Session,
    *,
    organization_id: uuid.UUID,
    key: str | None,
    endpoint: str,
    payload: dict[str, Any],
    status: int,
    body: dict[str, Any],
) -> None:
    """Persist the response for future retries (best-effort, same tx)."""
    if not key:
        return
    db.add(
        IdempotencyKey(
            organization_id=organization_id,
            idempotency_key=key[:100],
            endpoint=endpoint,
            request_hash=_hash(payload),
            response_status=status,
            response_body=body,
        )
    )
    try:
        db.flush()
    except IntegrityError:
        # concurrent duplicate: the other request already stored it
        db.rollback()
