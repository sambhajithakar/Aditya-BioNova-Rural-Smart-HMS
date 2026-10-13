"""Pydantic request/response schemas (exported to OpenAPI / contracts)."""
from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,29}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- Auth -------------------------------------------------------------------
class RegisterIn(StrictModel):
    organization_name: str = Field(min_length=2, max_length=200)
    organization_slug: str = Field(min_length=3, max_length=80)
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)

    @field_validator("organization_slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        v = v.lower()
        if not _SLUG_RE.match(v):
            raise ValueError(
                "slug must be lowercase letters, digits or hyphens, 3-80 chars"
            )
        return v


class LoginIn(StrictModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)
    organization_slug: str | None = None


class RefreshIn(StrictModel):
    refresh_token: str = Field(min_length=20, max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    status: str


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    status: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class AuthSessionOut(BaseModel):
    user: UserOut
    organization: OrganizationOut
    role: str
    permissions: list[str]
    tokens: TokenPair


class MeOut(BaseModel):
    user: UserOut
    organization: OrganizationOut
    membership_id: uuid.UUID
    branch_id: uuid.UUID | None
    role: str
    permissions: list[str]


# --- Branches ----------------------------------------------------------------
class BranchIn(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=2, max_length=30)

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        if not _CODE_RE.match(v):
            raise ValueError("code must be alphanumeric (also - or _), 2-30 chars")
        return v


class BranchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    code: str
    status: str
    created_at: datetime


# --- Members ----------------------------------------------------------------
class MemberCreateIn(StrictModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=10, max_length=200)
    role_key: str = Field(min_length=2, max_length=40)
    branch_id: uuid.UUID | None = None


class MemberOut(BaseModel):
    membership_id: uuid.UUID
    user_id: uuid.UUID
    email: EmailStr
    full_name: str
    role_key: str
    branch_id: uuid.UUID | None
    status: str
    created_at: datetime


# --- Audit --------------------------------------------------------------------
class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    organization_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    action: str
    resource_type: str | None
    resource_id: str | None
    metadata: dict = Field(validation_alias="event_metadata")
    correlation_id: str | None
    created_at: datetime


class Page(BaseModel):
    items: list
    total: int
    limit: int
    offset: int


class HealthOut(BaseModel):
    status: str
    service: str
    version: str
    environment: str


# --- M2: patients -----------------------------------------------------------
_GENDERS = ("male", "female", "other", "unknown")
_BLOOD_GROUPS = ("A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-")
_APPT_STATUSES = ("scheduled", "checked_in", "completed", "cancelled", "no_show")
_QUEUE_STATUSES = (
    "waiting", "called", "in_consultation", "completed", "skipped", "cancelled"
)
_PHONE_RE = re.compile(r"\D")


def _normalize_phone_digits(v: str) -> str:
    """Digits only; drop a +91 country code or trunk 0 (base 10 digits)."""
    digits = _PHONE_RE.sub("", v)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits


_UHID_RE = re.compile(r"^P\d{7}$")


class PatientIn(StrictModel):
    full_name: str = Field(min_length=2, max_length=200)
    gender: str
    date_of_birth: datetime | None = None
    dob_estimated: bool = False
    phone: str | None = Field(default=None, max_length=20)
    address: str = Field(default="", max_length=2000)
    blood_group: str | None = None
    allergies: str = Field(default="", max_length=2000)
    allow_duplicates: bool = False

    @field_validator("gender")
    @classmethod
    def _gender(cls, v: str) -> str:
        v = v.lower()
        if v not in _GENDERS:
            raise ValueError(f"gender must be one of {_GENDERS}")
        return v

    @field_validator("blood_group")
    @classmethod
    def _blood(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.upper()
        if v not in _BLOOD_GROUPS:
            raise ValueError(f"blood_group must be one of {_BLOOD_GROUPS}")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        if v is None:
            return None
        digits = _normalize_phone_digits(v)
        if not (10 <= len(digits) <= 15):
            raise ValueError("phone must contain 10-15 digits")
        return digits


class PatientUpdateIn(StrictModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    gender: str | None = None
    date_of_birth: datetime | None = None
    dob_estimated: bool | None = None
    phone: str | None = Field(default=None, max_length=20)
    address: str | None = Field(default=None, max_length=2000)
    blood_group: str | None = None
    allergies: str | None = Field(default=None, max_length=2000)
    status: str | None = None

    @field_validator("gender")
    @classmethod
    def _gender(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.lower()
        if v not in _GENDERS:
            raise ValueError(f"gender must be one of {_GENDERS}")
        return v

    @field_validator("blood_group")
    @classmethod
    def _blood(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.upper()
        if v not in _BLOOD_GROUPS:
            raise ValueError(f"blood_group must be one of {_BLOOD_GROUPS}")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        if v is None:
            return None
        digits = _normalize_phone_digits(v)
        if not (10 <= len(digits) <= 15):
            raise ValueError("phone must contain 10-15 digits")
        return digits

    @field_validator("status")
    @classmethod
    def _status(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in ("active", "inactive"):
            raise ValueError("status must be 'active' or 'inactive'")
        return v


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    uhid: str
    full_name: str
    gender: str
    date_of_birth: datetime | None
    dob_estimated: bool
    phone: str | None
    address: str
    blood_group: str | None
    allergies: str
    status: str
    merged_into_id: uuid.UUID | None
    created_at: datetime


class DuplicateCandidate(BaseModel):
    """A possible existing match shown at registration (supervised dedupe)."""

    id: uuid.UUID
    uhid: str
    full_name: str
    gender: str
    date_of_birth: datetime | None
    phone: str | None
    matched_on: list[str]


class PatientMergeIn(StrictModel):
    target_patient_id: uuid.UUID


# --- M2: appointments -------------------------------------------------------
class AppointmentIn(StrictModel):
    patient_id: uuid.UUID
    branch_id: uuid.UUID
    provider_user_id: uuid.UUID | None = None
    starts_at: datetime
    ends_at: datetime
    reason: str = Field(default="", max_length=300)
    notes: str = Field(default="", max_length=2000)


class AppointmentUpdateIn(StrictModel):
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    provider_user_id: uuid.UUID | None = None
    reason: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=2000)
    status: str | None = None

    @field_validator("status")
    @classmethod
    def _status(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in _APPT_STATUSES:
            raise ValueError(f"status must be one of {_APPT_STATUSES}")
        return v


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    branch_id: uuid.UUID
    patient_id: uuid.UUID
    provider_user_id: uuid.UUID | None
    starts_at: datetime
    ends_at: datetime
    reason: str
    status: str
    notes: str
    created_at: datetime


# --- M2: OPD queue ----------------------------------------------------------
class QueueJoinIn(StrictModel):
    patient_id: uuid.UUID
    branch_id: uuid.UUID
    appointment_id: uuid.UUID | None = None
    priority: str = "normal"

    @field_validator("priority")
    @classmethod
    def _priority(cls, v: str) -> str:
        if v not in ("normal", "urgent"):
            raise ValueError("priority must be 'normal' or 'urgent'")
        return v


class QueueStatusIn(StrictModel):
    """Doctor assignment for call/start transitions."""

    doctor_user_id: uuid.UUID | None = None


class QueueEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    branch_id: uuid.UUID
    patient_id: uuid.UUID
    appointment_id: uuid.UUID | None
    service_date: datetime
    token_number: int
    status: str
    priority: str
    doctor_user_id: uuid.UUID | None
    called_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class PageOut(BaseModel):
    items: list
    total: int
    limit: int
    offset: int
