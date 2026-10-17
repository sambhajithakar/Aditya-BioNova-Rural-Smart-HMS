"""Pydantic request/response schemas (exported to OpenAPI / contracts)."""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

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


# --- M3: encounters ---------------------------------------------------------
_DX_KINDS = ("primary", "secondary")
_DX_STATUSES = ("suspected", "confirmed", "ruled_out")
_ITEM_TYPES = ("consultation", "procedure", "pharmacy", "lab", "other")
_PAY_METHODS = ("cash", "upi", "card", "insurance", "other")
_ROUTES = ("oral", "topical", "inhalation", "injectable", "sublingual",
           "rectal", "ophthalmic", "otic", "nasal", "other")


class EncounterIn(StrictModel):
    patient_id: uuid.UUID
    branch_id: uuid.UUID
    appointment_id: uuid.UUID | None = None
    queue_entry_id: uuid.UUID | None = None
    provider_user_id: uuid.UUID | None = None
    chief_complaint: str = Field(default="", max_length=500)
    history: str = Field(default="", max_length=5000)
    examination: str = Field(default="", max_length=5000)


class EncounterUpdateIn(StrictModel):
    chief_complaint: str | None = Field(default=None, max_length=500)
    history: str | None = Field(default=None, max_length=5000)
    examination: str | None = Field(default=None, max_length=5000)
    assessment: str | None = Field(default=None, max_length=5000)
    plan: str | None = Field(default=None, max_length=5000)
    provider_user_id: uuid.UUID | None = None


class VitalsIn(StrictModel):
    bp_systolic: int | None = Field(default=None, ge=40, le=300)
    bp_diastolic: int | None = Field(default=None, ge=20, le=200)
    pulse: int | None = Field(default=None, ge=20, le=250)
    temperature_c: float | None = Field(default=None, ge=30, le=45)
    spo2: int | None = Field(default=None, ge=40, le=100)
    respiratory_rate: int | None = Field(default=None, ge=5, le=80)
    weight_kg: float | None = Field(default=None, ge=0.5, le=400)
    height_cm: float | None = Field(default=None, ge=30, le=260)
    notes: str = Field(default="", max_length=500)

    @field_validator("temperature_c", "weight_kg", "height_cm")
    @classmethod
    def _round(cls, v: float | None) -> float | None:
        return None if v is None else round(v, 1)


class VitalsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    encounter_id: uuid.UUID
    bp_systolic: int | None
    bp_diastolic: int | None
    pulse: int | None
    temperature_c: float | None
    spo2: int | None
    respiratory_rate: int | None
    weight_kg: float | None
    height_cm: float | None
    notes: str
    recorded_at: datetime


class DiagnosisIn(StrictModel):
    code: str = Field(min_length=1, max_length=20)
    label: str = Field(min_length=2, max_length=300)
    kind: str = "primary"
    status: str = "confirmed"

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in _DX_KINDS:
            raise ValueError(f"kind must be one of {_DX_KINDS}")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in _DX_STATUSES:
            raise ValueError(f"status must be one of {_DX_STATUSES}")
        return v


class DiagnosisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    encounter_id: uuid.UUID
    code: str
    label: str
    kind: str
    status: str
    created_at: datetime


class EncounterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    branch_id: uuid.UUID
    patient_id: uuid.UUID
    appointment_id: uuid.UUID | None
    queue_entry_id: uuid.UUID | None
    provider_user_id: uuid.UUID | None
    chief_complaint: str
    history: str
    examination: str
    assessment: str
    plan: str
    status: str
    started_at: datetime
    ended_at: datetime | None
    created_at: datetime


class EncounterDetailOut(EncounterOut):
    vitals: list[VitalsOut] = []
    diagnoses: list[DiagnosisOut] = []
    prescription_ids: list[uuid.UUID] = []


# --- M3: prescriptions ------------------------------------------------------
class DrugOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    generic_name: str
    brand_hint: str
    drug_class: str


class RxItemIn(StrictModel):
    drug_id: uuid.UUID
    dose: str = Field(min_length=1, max_length=100)
    route: str = "oral"
    frequency: str = Field(min_length=1, max_length=100)
    duration_days: int = Field(ge=1, le=365)
    quantity: str = Field(default="", max_length=100)
    instructions: str = Field(default="", max_length=500)

    @field_validator("route")
    @classmethod
    def _route(cls, v: str) -> str:
        v = v.lower()
        if v not in _ROUTES:
            raise ValueError(f"route must be one of {_ROUTES}")
        return v


class RxItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    prescription_id: uuid.UUID
    drug_id: uuid.UUID
    dose: str
    route: str
    frequency: str
    duration_days: int
    quantity: str
    instructions: str
    seq: int


class PrescriptionIn(StrictModel):
    """Create a draft Rx (optionally from an encounter)."""

    patient_id: uuid.UUID
    encounter_id: uuid.UUID | None = None
    diagnosis_text: str = Field(default="", max_length=500)
    advice: str = Field(default="", max_length=5000)
    follow_up: str = Field(default="", max_length=300)
    items: list[RxItemIn] = Field(min_length=1, max_length=50)


class PrescriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    encounter_id: uuid.UUID | None
    patient_id: uuid.UUID
    prescriber_user_id: uuid.UUID | None
    status: str
    version: int
    supersedes_id: uuid.UUID | None
    diagnosis_text: str
    advice: str
    follow_up: str
    allergy_acknowledged: bool
    signed_at: datetime | None
    amendment_reason: str
    created_at: datetime


class PrescriptionDetailOut(PrescriptionOut):
    items: list[RxItemOut] = []


class SignRxIn(StrictModel):
    acknowledge_allergies: bool = False


class AmendRxIn(StrictModel):
    reason: str = Field(min_length=3, max_length=500)
    diagnosis_text: str = Field(default="", max_length=500)
    advice: str = Field(default="", max_length=5000)
    follow_up: str = Field(default="", max_length=300)
    items: list[RxItemIn] = Field(min_length=1, max_length=50)
    acknowledge_allergies: bool = False


class RxWarning(BaseModel):
    kind: str  # allergy | duplicate_drug
    message: str
    drug_ids: list[uuid.UUID] = []


class RxWarningsOut(BaseModel):
    """Pre-sign safety check (allergy + duplicate-drug)."""

    warnings: list[RxWarning] = []
    blocking: bool = False


# --- M3: billing ------------------------------------------------------------
class InvoiceItemIn(StrictModel):
    item_type: str
    description: str = Field(min_length=1, max_length=300)
    quantity: int = Field(default=1, ge=1, le=10000)
    unit_price: float = Field(ge=0, le=10_000_000)
    tax_rate: float = Field(default=0, ge=0, le=100)

    @field_validator("item_type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in _ITEM_TYPES:
            raise ValueError(f"item_type must be one of {_ITEM_TYPES}")
        return v


class InvoiceItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_id: uuid.UUID
    item_type: str
    description: str
    quantity: int
    unit_price: float
    tax_rate: float
    line_total: float
    seq: int


class InvoiceIn(StrictModel):
    patient_id: uuid.UUID
    branch_id: uuid.UUID
    encounter_id: uuid.UUID | None = None
    discount: float = Field(default=0, ge=0)
    notes: str = Field(default="", max_length=500)
    items: list[InvoiceItemIn] = Field(min_length=1, max_length=200)


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    branch_id: uuid.UUID
    patient_id: uuid.UUID
    encounter_id: uuid.UUID | None
    invoice_number: str
    status: str
    subtotal: float
    discount: float
    tax_total: float
    total: float
    amount_paid: float
    notes: str
    issued_at: datetime | None
    created_at: datetime


class InvoiceDetailOut(InvoiceOut):
    items: list[InvoiceItemOut] = []
    payments: list["PaymentOut"] = []


class PaymentIn(StrictModel):
    amount: float = Field(gt=0, le=10_000_000)
    method: str = "cash"
    gateway: str = "manual"

    @field_validator("method")
    @classmethod
    def _method(cls, v: str) -> str:
        if v not in _PAY_METHODS:
            raise ValueError(f"method must be one of {_PAY_METHODS}")
        return v

    @field_validator("gateway")
    @classmethod
    def _gateway(cls, v: str) -> str:
        if v not in ("manual", "sandbox"):
            raise ValueError("gateway must be 'manual' or 'sandbox'")
        return v


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_id: uuid.UUID
    amount: float
    method: str
    gateway: str
    status: str
    gateway_ref: str
    failure_reason: str
    created_at: datetime


class RefundIn(StrictModel):
    amount: float = Field(gt=0, le=10_000_000)
    reason: str = Field(min_length=3, max_length=300)


class RefundOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    payment_id: uuid.UUID
    amount: float
    reason: str
    status: str
    created_at: datetime


class LedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_id: uuid.UUID
    payment_id: uuid.UUID | None
    refund_id: uuid.UUID | None
    entry_type: str
    direction: str
    amount: float
    description: str
    created_at: datetime


class ReceiptOut(BaseModel):
    """Printable receipt: invoice + items + payments + ledger position."""

    invoice: InvoiceOut
    items: list[InvoiceItemOut] = []
    payments: list[PaymentOut] = []
    refunds: list[RefundOut] = []
    ledger: list[LedgerEntryOut] = []
    balance_due: float
    org_name: str
    branch_name: str
    patient_name: str
    patient_uhid: str


# --- M5: pharmacy -----------------------------------------------------------
_BATCH_STATUSES = ("active", "depleted", "expired", "recalled")
_MOVEMENT_TYPES = ("receive", "dispense", "adjustment", "return")


class ReceiveStockIn(StrictModel):
    branch_id: uuid.UUID
    drug_id: uuid.UUID
    batch_number: str = Field(min_length=1, max_length=60)
    expiry_date: date
    quantity: int = Field(gt=0, le=1_000_000)
    unit_cost: float = Field(default=0, ge=0)
    supplier: str = Field(default="", max_length=200)

    @field_validator("expiry_date")
    @classmethod
    def _future_expiry(cls, v: date) -> date:
        if v <= date.today():
            raise ValueError("expiry_date must be in the future")
        return v


class StockBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    branch_id: uuid.UUID
    drug_id: uuid.UUID
    generic_name: str = ""
    batch_number: str
    expiry_date: date
    quantity_received: int
    quantity_on_hand: int
    unit_cost: float
    supplier: str
    status: str
    received_at: datetime

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in _BATCH_STATUSES:
            raise ValueError(f"status must be one of {_BATCH_STATUSES}")
        return v


class AdjustStockIn(StrictModel):
    """Stock correction: positive quantity IN (found stock), negative OUT
    (spoilage/breakage). Expiry write-offs use write_off=true (OUT)."""

    quantity: int = Field(default=0)
    reason: str = Field(min_length=3, max_length=300)
    write_off: bool = False


class DispenseItemIn(StrictModel):
    drug_id: uuid.UUID
    quantity: int = Field(gt=0, le=100_000)


class DispenseIn(StrictModel):
    prescription_id: uuid.UUID | None = None
    patient_id: uuid.UUID | None = None
    branch_id: uuid.UUID | None = None   # defaults to the FEFO source branch
    notes: str = Field(default="", max_length=500)
    items: list[DispenseItemIn] = Field(min_length=1, max_length=100)

    @field_validator("items")
    @classmethod
    def _unique_drugs(cls, v: list[DispenseItemIn]) -> list[DispenseItemIn]:
        ids = [i.drug_id for i in v]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate drug_id in dispense items")
        return v


class DispenseItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    batch_id: uuid.UUID
    drug_id: uuid.UUID
    quantity: int
    unit_price: float


class DispenseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    branch_id: uuid.UUID
    prescription_id: uuid.UUID | None
    patient_id: uuid.UUID | None
    notes: str
    created_at: datetime
    items: list[DispenseItemOut] = []


class StockMovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: uuid.UUID
    movement_type: str
    direction: str
    quantity: int
    dispense_id: uuid.UUID | None
    reason: str
    created_at: datetime

    @field_validator("movement_type")
    @classmethod
    def _mtype(cls, v: str) -> str:
        if v not in _MOVEMENT_TYPES:
            raise ValueError(f"movement_type must be one of {_MOVEMENT_TYPES}")
        return v


class StockAlertOut(BaseModel):
    """FEFO / expiry monitoring report."""

    expiring_soon: list[StockBatchOut] = []   # <= 90 days
    expired: list[StockBatchOut] = []         # past expiry, still on hand
    low_stock: list[dict] = []                # drug-level on-hand summary


# --- M5: laboratory ---------------------------------------------------------
_LAB_FLAGS = ("normal", "low", "high", "abnormal", "critical")


class LabTestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    specimen_type: str
    unit: str
    reference_range: str


class LabOrderIn(StrictModel):
    patient_id: uuid.UUID
    branch_id: uuid.UUID
    encounter_id: uuid.UUID | None = None
    priority: str = "routine"
    clinical_notes: str = Field(default="", max_length=2000)
    test_ids: list[uuid.UUID] = Field(min_length=1, max_length=30)

    @field_validator("priority")
    @classmethod
    def _priority(cls, v: str) -> str:
        if v not in ("routine", "urgent"):
            raise ValueError("priority must be 'routine' or 'urgent'")
        return v

    @field_validator("test_ids")
    @classmethod
    def _unique_tests(cls, v: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(v)) != len(v):
            raise ValueError("duplicate test_ids")
        return v


class LabResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    test_id: uuid.UUID
    specimen_id: uuid.UUID | None
    value_text: str
    value_numeric: float | None
    unit: str
    reference_range: str
    flag: str
    status: str
    superseded_by_id: uuid.UUID | None
    amendment_reason: str
    verified_at: datetime | None
    released_at: datetime | None
    created_at: datetime

    @field_validator("flag")
    @classmethod
    def _flag(cls, v: str) -> str:
        if v not in _LAB_FLAGS:
            raise ValueError(f"flag must be one of {_LAB_FLAGS}")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in ("imported", "verified", "released", "superseded"):
            raise ValueError("illegal lab result status")
        return v


class LabOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    encounter_id: uuid.UUID | None
    branch_id: uuid.UUID
    status: str
    priority: str
    clinical_notes: str
    cancel_reason: str
    created_at: datetime

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in ("ordered", "specimen_collected", "in_progress",
                     "released", "cancelled"):
            raise ValueError("illegal lab order status")
        return v


class LabOrderDetailOut(LabOrderOut):
    specimens: list["LabSpecimenOut"] = []
    results: list[LabResultOut] = []


class LabSpecimenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    barcode: str
    specimen_type: str
    status: str
    rejection_reason: str
    collected_at: datetime


class CollectSpecimenIn(StrictModel):
    specimen_type: str

    @field_validator("specimen_type")
    @classmethod
    def _stype(cls, v: str) -> str:
        if v not in ("blood", "urine", "stool", "swab", "sputum",
                     "tissue", "other"):
            raise ValueError("illegal specimen_type")
        return v


class RejectSpecimenIn(StrictModel):
    reason: str = Field(min_length=3, max_length=300)


class LabResultValueIn(StrictModel):
    test_id: uuid.UUID
    value_text: str = Field(default="", max_length=200)
    value_numeric: float | None = None
    flag: str = "normal"

    @field_validator("flag")
    @classmethod
    def _flag(cls, v: str) -> str:
        if v not in _LAB_FLAGS:
            raise ValueError(f"flag must be one of {_LAB_FLAGS}")
        return v


class ImportResultsIn(StrictModel):
    specimen_id: uuid.UUID | None = None
    results: list[LabResultValueIn] = Field(min_length=1, max_length=30)


class AmendResultIn(StrictModel):
    value_text: str = Field(default="", max_length=200)
    value_numeric: float | None = None
    flag: str = "normal"
    reason: str = Field(min_length=3, max_length=300)

    @field_validator("flag")
    @classmethod
    def _flag(cls, v: str) -> str:
        if v not in _LAB_FLAGS:
            raise ValueError(f"flag must be one of {_LAB_FLAGS}")
        return v


class CancelLabOrderIn(StrictModel):
    reason: str = Field(min_length=3, max_length=300)


# --- M6: IPD + operations ---------------------------------------------------
_WARD_TYPES = ("general", "icu", "maternity", "pediatric", "isolation",
               "day_care", "private", "other")
_BED_STATUSES = ("available", "maintenance", "blocked")
_DISCHARGE_OUTCOMES = ("recovered", "improved", "referred",
                       "left_against_advice", "death", "transfer_out")
_NURSING_NOTE_TYPES = ("vitals", "note", "intake_output", "care")
_INV_MOVEMENT_TYPES = ("receive", "issue", "adjustment", "return")
_EXPENSE_CATEGORIES = ("rent", "salaries", "utilities", "supplies",
                       "equipment", "maintenance", "service", "transport",
                       "marketing", "other")


class WardIn(StrictModel):
    branch_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=2, max_length=30)
    ward_type: str = "general"

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        if not _CODE_RE.match(v):
            raise ValueError("code must be alphanumeric (also - or _), 2-30 chars")
        return v

    @field_validator("ward_type")
    @classmethod
    def _wtype(cls, v: str) -> str:
        if v not in _WARD_TYPES:
            raise ValueError(f"ward_type must be one of {_WARD_TYPES}")
        return v


class WardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    branch_id: uuid.UUID
    name: str
    code: str
    ward_type: str
    status: str
    created_at: datetime
    # derived occupancy (counted from beds + active admissions)
    bed_count: int = 0
    occupied_count: int = 0
    available_count: int = 0


class BedIn(StrictModel):
    ward_id: uuid.UUID
    label: str = Field(min_length=1, max_length=30)


class BedUpdateIn(StrictModel):
    status: str

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in _BED_STATUSES:
            raise ValueError(f"status must be one of {_BED_STATUSES}")
        return v


class BedOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ward_id: uuid.UUID
    label: str
    status: str
    # derived: set when an active admission holds this bed
    admission_id: uuid.UUID | None = None
    patient_id: uuid.UUID | None = None
    admitted_at: datetime | None = None


class AdmissionIn(StrictModel):
    patient_id: uuid.UUID
    ward_id: uuid.UUID
    bed_id: uuid.UUID
    reason: str = Field(min_length=3, max_length=2000)
    attending_user_id: uuid.UUID | None = None


class AdmissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    branch_id: uuid.UUID
    ward_id: uuid.UUID
    bed_id: uuid.UUID
    reason: str
    attending_user_id: uuid.UUID | None
    status: str
    admitted_at: datetime
    discharge_outcome: str | None
    discharge_summary: str
    discharged_at: datetime | None
    # patient enrichment (same pattern as StockBatchOut.generic_name)
    patient_uhid: str = ""
    patient_full_name: str = ""

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in ("admitted", "discharged"):
            raise ValueError("illegal admission status")
        return v


class TransferIn(StrictModel):
    to_bed_id: uuid.UUID
    reason: str = Field(min_length=3, max_length=300)


class DischargeIn(StrictModel):
    outcome: str
    summary: str = Field(min_length=3, max_length=5000)

    @field_validator("outcome")
    @classmethod
    def _outcome(cls, v: str) -> str:
        if v not in _DISCHARGE_OUTCOMES:
            raise ValueError(f"outcome must be one of {_DISCHARGE_OUTCOMES}")
        return v


class NursingRecordIn(StrictModel):
    note_type: str = "note"
    note: str = Field(default="", max_length=4000)
    bp_systolic: int | None = Field(default=None, ge=40, le=300)
    bp_diastolic: int | None = Field(default=None, ge=20, le=200)
    pulse: int | None = Field(default=None, ge=20, le=250)
    respiratory_rate: int | None = Field(default=None, ge=5, le=80)
    temperature_c: float | None = Field(default=None, ge=30, le=45)
    spo2: int | None = Field(default=None, ge=50, le=100)

    @field_validator("note_type")
    @classmethod
    def _ntype(cls, v: str) -> str:
        if v not in _NURSING_NOTE_TYPES:
            raise ValueError(f"note_type must be one of {_NURSING_NOTE_TYPES}")
        return v

    @field_validator("temperature_c")
    @classmethod
    def _temp(cls, v: float | None) -> float | None:
        if v is not None and not round(v, 1) == v:
            raise ValueError("temperature_c supports one decimal place")
        return v

    @field_validator("spo2")
    @classmethod
    def _spo2(cls, v: int | None) -> int | None:
        if v is not None and v < 85:
            raise ValueError(
                "spo2 below 85 needs immediate clinical review — "
                "record it as a note with escalation"
            )
        return v

    @model_validator(mode="after")
    def _has_content_and_ordered_bp(self) -> "NursingRecordIn":
        vital = (self.bp_systolic is not None or self.pulse is not None
                 or self.temperature_c is not None or self.spo2 is not None
                 or self.respiratory_rate is not None)
        if not self.note.strip() and not vital:
            raise ValueError("a nursing record needs a note or at least one vital")
        if (self.bp_systolic is not None and self.bp_diastolic is not None
                and self.bp_systolic <= self.bp_diastolic):
            raise ValueError("bp_systolic must exceed bp_diastolic")
        return self


class NursingRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    admission_id: uuid.UUID
    note_type: str
    note: str
    bp_systolic: int | None
    bp_diastolic: int | None
    pulse: int | None
    respiratory_rate: int | None
    temperature_c: float | None
    spo2: int | None
    recorded_at: datetime
    recorded_by: uuid.UUID | None

    @field_validator("note_type")
    @classmethod
    def _ntype(cls, v: str) -> str:
        if v not in _NURSING_NOTE_TYPES:
            raise ValueError(f"note_type must be one of {_NURSING_NOTE_TYPES}")
        return v


class InventoryItemIn(StrictModel):
    branch_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    sku: str = Field(default="", max_length=60)
    unit: str = Field(default="pcs", min_length=1, max_length=30)
    reorder_level: int = Field(default=0, ge=0, le=1_000_000)
    unit_cost: float = Field(default=0, ge=0)


class InventoryItemUpdateIn(StrictModel):
    """Partial update: only the non-None fields change."""

    unit: str | None = Field(default=None, min_length=1, max_length=30)
    reorder_level: int | None = Field(default=None, ge=0, le=1_000_000)
    unit_cost: float | None = Field(default=None, ge=0)
    status: str | None = None

    @field_validator("status")
    @classmethod
    def _status(cls, v: str | None) -> str | None:
        if v is not None and v not in ("active", "retired"):
            raise ValueError("status must be 'active' or 'retired'")
        return v


class InventoryItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    branch_id: uuid.UUID
    name: str
    sku: str
    unit: str
    quantity_on_hand: int
    reorder_level: int
    unit_cost: float
    status: str
    created_at: datetime
    low_stock: bool = False   # derived: active, reorder_level > 0, below it


class InventoryMovementIn(StrictModel):
    """receive/return move stock IN, issue moves stock OUT, adjustment
    follows the signed quantity / write_off rule of the pharmacy ledger."""

    movement_type: str
    quantity: int = Field(ge=-1_000_000, le=1_000_000)
    reason: str = Field(min_length=3, max_length=300)
    write_off: bool = False

    @field_validator("movement_type")
    @classmethod
    def _mtype(cls, v: str) -> str:
        if v not in _INV_MOVEMENT_TYPES:
            raise ValueError(f"movement_type must be one of {_INV_MOVEMENT_TYPES}")
        return v

    @field_validator("quantity")
    @classmethod
    def _qty(cls, v: int) -> int:
        if v == 0:
            raise ValueError("quantity must be non-zero")
        return v


class InventoryMovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_id: uuid.UUID
    movement_type: str
    direction: str
    quantity: int
    reason: str
    created_at: datetime

    @field_validator("movement_type")
    @classmethod
    def _mtype(cls, v: str) -> str:
        if v not in _INV_MOVEMENT_TYPES:
            raise ValueError(f"movement_type must be one of {_INV_MOVEMENT_TYPES}")
        return v


class ExpenseIn(StrictModel):
    category: str
    amount: float = Field(gt=0, le=10_000_000)
    expense_date: date
    description: str = Field(default="", max_length=500)
    reference: str = Field(default="", max_length=100)
    branch_id: uuid.UUID | None = None

    @field_validator("category")
    @classmethod
    def _category(cls, v: str) -> str:
        if v not in _EXPENSE_CATEGORIES:
            raise ValueError(f"category must be one of {_EXPENSE_CATEGORIES}")
        return v


class ExpenseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    branch_id: uuid.UUID | None
    category: str
    amount: float
    expense_date: date
    description: str
    reference: str
    status: str
    void_reason: str
    created_at: datetime

    @field_validator("category")
    @classmethod
    def _category(cls, v: str) -> str:
        if v not in _EXPENSE_CATEGORIES:
            raise ValueError(f"category must be one of {_EXPENSE_CATEGORIES}")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in ("recorded", "void"):
            raise ValueError("illegal expense status")
        return v


class ExpenseVoidIn(StrictModel):
    reason: str = Field(min_length=3, max_length=300)


# --- M6: reports ------------------------------------------------------------
class IpdReport(BaseModel):
    currently_admitted: int = 0
    admissions_in_period: int = 0
    discharges_in_period: int = 0
    deaths_in_period: int = 0
    beds_total: int = 0
    beds_occupied: int = 0
    beds_maintenance: int = 0


class FinanceCategoryRow(BaseModel):
    category: str
    total: float


class FinanceReport(BaseModel):
    revenue: float = 0          # succeeded payments in period
    refunds: float = 0          # succeeded refunds in period
    expenses: float = 0         # non-void expenses booked in period
    net: float = 0              # revenue - refunds - expenses
    by_category: list[FinanceCategoryRow] = []


class InventoryReport(BaseModel):
    items_active: int = 0
    low_stock: list[dict] = []


class ReportSummaryOut(BaseModel):
    from_date: date
    to_date: date
    ipd: IpdReport
    finance: FinanceReport
    inventory: InventoryReport
