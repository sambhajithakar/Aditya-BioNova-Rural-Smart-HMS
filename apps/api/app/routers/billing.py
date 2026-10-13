"""Billing: invoices, payments (idempotent), refunds, ledger, receipt.

Financial integrity (all proven by tests):
* invoice totals are COMPUTED SERVER-SIDE from line items — client-supplied
  totals are never trusted; they freeze when the invoice is issued (DB
  trigger) and line items become immutable at the same moment;
* the ledger is append-only (trigger + revoked grants) and double posting
  is blocked by unique partial indexes — a retried payment cannot credit
  twice;
* POST /invoices/{id}/payments honours ``Idempotency-Key`` (replay stores
  the first response; same key + different payload -> 409);
* refunds can never exceed the amount actually settled on a payment;
* ``gateway=sandbox`` simulates the M7 provider flow (pending -> webhook
  style confirm/reject) without touching any real money.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auditing import record_event
from ..db import get_db
from ..deps import (
    Principal,
    get_client_ip,
    get_correlation_id,
    require_permission,
)
from ..models import (
    Branch,
    Encounter,
    Invoice,
    InvoiceItem,
    LedgerEntry,
    Organization,
    Patient,
    Payment,
    Refund,
)
from ..schemas import (
    InvoiceDetailOut,
    InvoiceIn,
    InvoiceItemOut,
    InvoiceOut,
    LedgerEntryOut,
    PageOut,
    PaymentIn,
    PaymentOut,
    ReceiptOut,
    RefundIn,
    RefundOut,
)
from ..services.clinical import idempotent_replay, idempotent_store
from ..services.billing import (
    assert_ledger_consistent,
    balance_due,
    compute_line_total,
    money,
    next_invoice_number,
    post_ledger,
    refund_total_for_payment,
    settle_paid_status,
)

router = APIRouter(prefix="/billing", tags=["billing"])

MAX_LIMIT = 100


def _get_invoice(db: Session, principal: Principal, invoice_id: uuid.UUID) -> Invoice:
    row = db.scalar(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.organization_id == principal.organization_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return row


def _invoice_detail(db: Session, principal: Principal, inv: Invoice) -> InvoiceDetailOut:
    items = db.scalars(
        select(InvoiceItem)
        .where(
            InvoiceItem.organization_id == principal.organization_id,
            InvoiceItem.invoice_id == inv.id,
        )
        .order_by(InvoiceItem.seq)
    ).all()
    payments = db.scalars(
        select(Payment)
        .where(
            Payment.organization_id == principal.organization_id,
            Payment.invoice_id == inv.id,
        )
        .order_by(Payment.created_at)
    ).all()
    detail = InvoiceDetailOut.model_validate(inv)
    detail.items = [InvoiceItemOut.model_validate(i) for i in items]
    detail.payments = [PaymentOut.model_validate(p) for p in payments]
    return detail


# --- invoices ---------------------------------------------------------------
@router.post("/invoices", response_model=InvoiceDetailOut,
             status_code=status.HTTP_201_CREATED)
def create_invoice(
    payload: InvoiceIn,
    request: Request,
    principal: Principal = Depends(require_permission("billing.invoice")),
    db: Session = Depends(get_db),
) -> InvoiceDetailOut:
    patient = db.scalar(
        select(Patient).where(
            Patient.id == payload.patient_id,
            Patient.organization_id == principal.organization_id,
        )
    )
    if patient is None or patient.status == "merged":
        raise HTTPException(status_code=404, detail="Patient not found")
    branch = db.scalar(
        select(Branch).where(
            Branch.id == payload.branch_id,
            Branch.organization_id == principal.organization_id,
        )
    )
    if branch is None or branch.status != "active":
        raise HTTPException(status_code=404, detail="Branch not found or inactive")
    if payload.encounter_id:
        enc = db.scalar(
            select(Encounter).where(
                Encounter.id == payload.encounter_id,
                Encounter.organization_id == principal.organization_id,
            )
        )
        if enc is None:
            raise HTTPException(status_code=404, detail="Encounter not found")

    # server-side totals — client totals are never accepted
    subtotal = Decimal("0")
    tax_total = Decimal("0")
    computed: list[tuple] = []
    for seq, item in enumerate(payload.items):
        line = compute_line_total(item.quantity, item.unit_price, item.tax_rate)
        base = money(Decimal(str(item.quantity)) * Decimal(str(item.unit_price)))
        tax = money(line - base)
        subtotal += base
        tax_total += tax
        computed.append((seq, item, line))

    discount = money(payload.discount)
    if discount > subtotal:
        raise HTTPException(status_code=422, detail="Discount exceeds subtotal")
    total = money(subtotal - discount + tax_total)

    invoice_number = next_invoice_number(db, principal.organization_id)
    inv = Invoice(
        organization_id=principal.organization_id,
        branch_id=payload.branch_id,
        patient_id=payload.patient_id,
        encounter_id=payload.encounter_id,
        invoice_number=invoice_number,
        status="draft",
        subtotal=subtotal,
        discount=discount,
        tax_total=tax_total,
        total=total,
        amount_paid=0,
        notes=payload.notes,
        created_by=principal.user_id,
    )
    db.add(inv)
    db.flush()
    for seq, item, line in computed:
        db.add(
            InvoiceItem(
                organization_id=principal.organization_id,
                invoice_id=inv.id,
                item_type=item.item_type,
                description=item.description,
                quantity=item.quantity,
                unit_price=item.unit_price,
                tax_rate=item.tax_rate,
                line_total=line,
                seq=seq,
            )
        )
    db.flush()

    record_event(
        db,
        action="invoice.created",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="invoice",
        resource_id=str(inv.id),
        metadata={"invoice_number": invoice_number, "total": str(total)},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return _invoice_detail(db, principal, inv)


@router.post("/invoices/{invoice_id}/issue", response_model=InvoiceDetailOut)
def issue_invoice(
    invoice_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_permission("billing.invoice")),
    db: Session = Depends(get_db),
) -> InvoiceDetailOut:
    inv = _get_invoice(db, principal, invoice_id)
    if inv.status != "draft":
        raise HTTPException(status_code=409, detail=f"Invoice is {inv.status}")
    if money(Decimal(str(inv.total))) <= 0:
        raise HTTPException(status_code=422, detail="Cannot issue a zero invoice")

    inv.status = "issued"
    inv.issued_at = datetime.now(timezone.utc)
    inv.updated_at = datetime.now(timezone.utc)
    post_ledger(
        db,
        organization_id=principal.organization_id,
        invoice_id=inv.id,
        entry_type="charge",
        direction="debit",
        amount=Decimal(str(inv.total)),
        description=f"Invoice {inv.invoice_number} issued",
        actor_user_id=principal.user_id,
    )
    record_event(
        db,
        action="invoice.issued",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="invoice",
        resource_id=str(inv.id),
        metadata={"invoice_number": inv.invoice_number, "total": str(inv.total)},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return _invoice_detail(db, principal, inv)


@router.post("/invoices/{invoice_id}/void", response_model=InvoiceDetailOut)
def void_invoice(
    invoice_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_permission("billing.invoice")),
    db: Session = Depends(get_db),
) -> InvoiceDetailOut:
    inv = _get_invoice(db, principal, invoice_id)
    if inv.status not in ("draft", "issued"):
        raise HTTPException(
            status_code=409, detail=f"Cannot void an invoice in {inv.status}"
        )
    if money(Decimal(str(inv.amount_paid))) > 0:
        raise HTTPException(
            status_code=409, detail="Refund settled payments before voiding"
        )
    was_issued = inv.status == "issued"
    inv.status = "void"
    inv.updated_at = datetime.now(timezone.utc)
    if was_issued:
        post_ledger(
            db,
            organization_id=principal.organization_id,
            invoice_id=inv.id,
            entry_type="adjustment",
            direction="credit",
            amount=Decimal(str(inv.total)),
            description=f"Invoice {inv.invoice_number} voided",
            actor_user_id=principal.user_id,
        )
    record_event(
        db,
        action="invoice.voided",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="invoice",
        resource_id=str(inv.id),
        metadata={"invoice_number": inv.invoice_number, "was_issued": was_issued},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return _invoice_detail(db, principal, inv)


@router.get("/invoices", response_model=PageOut)
def list_invoices(
    patient_id: uuid.UUID | None = None,
    inv_status: str | None = None,
    limit: int = 20,
    offset: int = 0,
    principal: Principal = Depends(require_permission("billing.read")),
    db: Session = Depends(get_db),
) -> PageOut:
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)
    conditions = [Invoice.organization_id == principal.organization_id]
    if patient_id:
        conditions.append(Invoice.patient_id == patient_id)
    if inv_status:
        if inv_status not in ("draft", "issued", "paid", "void"):
            raise HTTPException(status_code=422, detail="Unknown status filter")
        conditions.append(Invoice.status == inv_status)

    total = db.scalar(
        select(func.count()).select_from(Invoice).where(*conditions)
    ) or 0
    rows = db.scalars(
        select(Invoice)
        .where(*conditions)
        .order_by(Invoice.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return PageOut(
        items=[InvoiceOut.model_validate(r).model_dump(mode="json") for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/invoices/{invoice_id}", response_model=InvoiceDetailOut)
def get_invoice(
    invoice_id: uuid.UUID,
    principal: Principal = Depends(require_permission("billing.read")),
    db: Session = Depends(get_db),
) -> InvoiceDetailOut:
    return _invoice_detail(db, principal, _get_invoice(db, principal, invoice_id))


# --- payments ---------------------------------------------------------------
@router.post("/invoices/{invoice_id}/payments", response_model=PaymentOut,
             status_code=status.HTTP_201_CREATED)
def record_payment(
    invoice_id: uuid.UUID,
    payload: PaymentIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_permission("billing.payment")),
    db: Session = Depends(get_db),
) -> PaymentOut:
    body = payload.model_dump(mode="json")
    replay = idempotent_replay(
        db,
        organization_id=principal.organization_id,
        key=idempotency_key,
        endpoint=f"POST /billing/invoices/{invoice_id}/payments",
        payload=body,
    )
    if replay:
        return PaymentOut.model_validate(replay["body"])

    inv = _get_invoice(db, principal, invoice_id)
    if inv.status not in ("issued", "paid"):
        raise HTTPException(
            status_code=409,
            detail=f"Payments need an issued invoice (status: {inv.status})",
        )
    amount = money(payload.amount)
    outstanding = balance_due(db, inv)
    if amount > outstanding:
        raise HTTPException(
            status_code=422,
            detail=f"Amount {amount} exceeds balance due {outstanding}",
        )

    payment = Payment(
        organization_id=principal.organization_id,
        invoice_id=inv.id,
        amount=amount,
        method=payload.method,
        gateway=payload.gateway,
        status="pending",
        created_by=principal.user_id,
    )
    db.add(payment)
    db.flush()

    if payload.gateway == "manual":
        # counter payment: settled immediately (cash/card terminal on site)
        _settle_payment(db, principal, inv, payment, success=True)
        response_status = 201
    else:
        # sandbox gateway: pending until the signed callback confirms it
        payment.gateway_ref = f"sbox_{payment.id.hex[:16]}"
        response_status = 201

    out = PaymentOut.model_validate(payment)
    record_event(
        db,
        action="payment.created",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="payment",
        resource_id=str(payment.id),
        metadata={
            "invoice_id": str(inv.id),
            "amount": str(amount),
            "method": payload.method,
            "gateway": payload.gateway,
            "status": payment.status,
        },
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    idempotent_store(
        db,
        organization_id=principal.organization_id,
        key=idempotency_key,
        endpoint=f"POST /billing/invoices/{invoice_id}/payments",
        payload=body,
        status=response_status,
        body=out.model_dump(mode="json"),
    )
    return out


def _settle_payment(
    db: Session,
    principal: Principal,
    inv: Invoice,
    payment: Payment,
    *,
    success: bool,
    failure_reason: str = "",
) -> None:
    now = datetime.now(timezone.utc)
    if success:
        payment.status = "succeeded"
        inv.amount_paid = money(Decimal(str(inv.amount_paid)) + Decimal(str(payment.amount)))
        post_ledger(
            db,
            organization_id=principal.organization_id,
            invoice_id=inv.id,
            entry_type="payment",
            direction="credit",
            amount=Decimal(str(payment.amount)),
            description=f"Payment {payment.method} for {inv.invoice_number}",
            actor_user_id=principal.user_id,
            payment_id=payment.id,
        )
        settle_paid_status(db, inv)
    else:
        payment.status = "failed"
        payment.failure_reason = failure_reason[:300]
    payment.updated_at = now
    inv.updated_at = now


@router.post("/payments/{payment_id}/confirm", response_model=PaymentOut)
def sandbox_confirm(
    payment_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_permission("billing.payment")),
    db: Session = Depends(get_db),
) -> PaymentOut:
    """Sandbox gateway callback: confirm a pending payment (M7 spike)."""
    payment = _load_payment(db, principal, payment_id)
    if payment.gateway != "sandbox":
        raise HTTPException(status_code=409, detail="Not a sandbox payment")
    if payment.status != "pending":
        raise HTTPException(status_code=409, detail=f"Payment is {payment.status}")
    inv = _get_invoice(db, principal, payment.invoice_id)
    _settle_payment(db, principal, inv, payment, success=True)
    record_event(
        db,
        action="payment.succeeded",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="payment",
        resource_id=str(payment.id),
        metadata={"invoice_id": str(inv.id), "gateway_ref": payment.gateway_ref},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return PaymentOut.model_validate(payment)


@router.post("/payments/{payment_id}/fail", response_model=PaymentOut)
def sandbox_fail(
    payment_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_permission("billing.payment")),
    db: Session = Depends(get_db),
) -> PaymentOut:
    payment = _load_payment(db, principal, payment_id)
    if payment.gateway != "sandbox":
        raise HTTPException(status_code=409, detail="Not a sandbox payment")
    if payment.status != "pending":
        raise HTTPException(status_code=409, detail=f"Payment is {payment.status}")
    inv = _get_invoice(db, principal, payment.invoice_id)
    _settle_payment(
        db, principal, inv, payment, success=False, failure_reason="Sandbox decline"
    )
    record_event(
        db,
        action="payment.failed",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="payment",
        resource_id=str(payment.id),
        metadata={"invoice_id": str(inv.id)},
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return PaymentOut.model_validate(payment)


def _load_payment(db: Session, principal: Principal, payment_id: uuid.UUID) -> Payment:
    row = db.scalar(
        select(Payment).where(
            Payment.id == payment_id,
            Payment.organization_id == principal.organization_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return row


# --- refunds ----------------------------------------------------------------
@router.post("/payments/{payment_id}/refund", response_model=RefundOut,
             status_code=status.HTTP_201_CREATED)
def refund_payment(
    payment_id: uuid.UUID,
    payload: RefundIn,
    request: Request,
    principal: Principal = Depends(require_permission("billing.refund")),
    db: Session = Depends(get_db),
) -> RefundOut:
    payment = _load_payment(db, principal, payment_id)
    if payment.status not in ("succeeded", "partially_refunded", "refunded"):
        raise HTTPException(
            status_code=409, detail=f"Cannot refund a {payment.status} payment"
        )
    amount = money(payload.amount)
    already = refund_total_for_payment(db, payment.id)
    max_refund = money(Decimal(str(payment.amount)) - already)
    if amount > max_refund:
        raise HTTPException(
            status_code=422,
            detail=f"Refund {amount} exceeds refundable {max_refund}",
        )

    refund = Refund(
        organization_id=principal.organization_id,
        payment_id=payment.id,
        amount=amount,
        reason=payload.reason,
        status="succeeded",
        gateway_ref=f"sbox_rf_{refund_uuid()}",
        created_by=principal.user_id,
    )
    db.add(refund)
    db.flush()

    inv = _get_invoice(db, principal, payment.invoice_id)
    inv.amount_paid = money(Decimal(str(inv.amount_paid)) - amount)
    inv.updated_at = datetime.now(timezone.utc)
    post_ledger(
        db,
        organization_id=principal.organization_id,
        invoice_id=inv.id,
        entry_type="refund",
        direction="debit",
        amount=amount,
        description=f"Refund for {inv.invoice_number}: {payload.reason}",
        actor_user_id=principal.user_id,
        refund_id=refund.id,
    )
    refunded_total = already + amount
    if refunded_total >= money(Decimal(str(payment.amount))):
        payment.status = "refunded"
    else:
        payment.status = "partially_refunded"
    payment.updated_at = datetime.now(timezone.utc)
    if inv.status == "paid" and money(Decimal(str(inv.amount_paid))) < money(
        Decimal(str(inv.total))
    ):
        inv.status = "issued"

    record_event(
        db,
        action="payment.refunded",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="payment",
        resource_id=str(payment.id),
        metadata={
            "invoice_id": str(inv.id),
            "amount": str(amount),
            "reason": payload.reason,
        },
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return RefundOut.model_validate(refund)


def refund_uuid() -> str:
    return uuid.uuid4().hex[:16]


# --- ledger + receipt -------------------------------------------------------
@router.get("/invoices/{invoice_id}/ledger", response_model=list[LedgerEntryOut])
def invoice_ledger(
    invoice_id: uuid.UUID,
    principal: Principal = Depends(require_permission("billing.read")),
    db: Session = Depends(get_db),
) -> list[LedgerEntryOut]:
    inv = _get_invoice(db, principal, invoice_id)
    rows = db.scalars(
        select(LedgerEntry)
        .where(
            LedgerEntry.organization_id == principal.organization_id,
            LedgerEntry.invoice_id == inv.id,
        )
        .order_by(LedgerEntry.id)
    ).all()
    return [LedgerEntryOut.model_validate(r) for r in rows]


@router.get("/invoices/{invoice_id}/receipt", response_model=ReceiptOut)
def invoice_receipt(
    invoice_id: uuid.UUID,
    principal: Principal = Depends(require_permission("billing.read")),
    db: Session = Depends(get_db),
) -> ReceiptOut:
    """Receipt payload for the printable A4/A5 view (trilingual in web)."""
    inv = _get_invoice(db, principal, invoice_id)
    if inv.status == "draft":
        raise HTTPException(status_code=409, detail="Issue the invoice first")

    items = db.scalars(
        select(InvoiceItem)
        .where(
            InvoiceItem.organization_id == principal.organization_id,
            InvoiceItem.invoice_id == inv.id,
        )
        .order_by(InvoiceItem.seq)
    ).all()
    payments = db.scalars(
        select(Payment)
        .where(
            Payment.organization_id == principal.organization_id,
            Payment.invoice_id == inv.id,
        )
        .order_by(Payment.created_at)
    ).all()
    payment_ids = [p.id for p in payments]
    refunds = (
        db.scalars(
            select(Refund)
            .where(
                Refund.organization_id == principal.organization_id,
                Refund.payment_id.in_(payment_ids) if payment_ids else None,
            )
            .order_by(Refund.created_at)
        ).all()
        if payment_ids
        else []
    )
    ledger = db.scalars(
        select(LedgerEntry)
        .where(
            LedgerEntry.organization_id == principal.organization_id,
            LedgerEntry.invoice_id == inv.id,
        )
        .order_by(LedgerEntry.id)
    ).all()

    org = db.get(Organization, principal.organization_id)
    branch = db.scalar(
        select(Branch).where(
            Branch.id == inv.branch_id,
            Branch.organization_id == principal.organization_id,
        )
    )
    patient = db.scalar(
        select(Patient).where(
            Patient.id == inv.patient_id,
            Patient.organization_id == principal.organization_id,
        )
    )
    assert_ledger_consistent(db, inv)

    return ReceiptOut(
        invoice=InvoiceOut.model_validate(inv),
        items=[InvoiceItemOut.model_validate(i) for i in items],
        payments=[PaymentOut.model_validate(p) for p in payments],
        refunds=[RefundOut.model_validate(r) for r in refunds],
        ledger=[LedgerEntryOut.model_validate(e) for e in ledger],
        balance_due=float(balance_due(db, inv)),
        org_name=org.name if org else "",
        branch_name=branch.name if branch else "",
        patient_name=patient.full_name if patient else "",
        patient_uhid=patient.uhid if patient else "",
    )


@router.get("/ledger", response_model=PageOut)
def org_ledger(
    limit: int = 50,
    offset: int = 0,
    principal: Principal = Depends(require_permission("billing.read")),
    db: Session = Depends(get_db),
) -> PageOut:
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)
    conditions = [LedgerEntry.organization_id == principal.organization_id]
    total = db.scalar(
        select(func.count()).select_from(LedgerEntry).where(*conditions)
    ) or 0
    rows = db.scalars(
        select(LedgerEntry)
        .where(*conditions)
        .order_by(LedgerEntry.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return PageOut(
        items=[LedgerEntryOut.model_validate(r).model_dump(mode="json") for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
