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
