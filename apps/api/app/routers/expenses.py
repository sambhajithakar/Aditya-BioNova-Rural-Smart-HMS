"""M6 expenses API: recorded operational expenditure and category summaries.

Financial integrity (same posture as billing):
* amounts, dates and categories are FROZEN at creation (DB trigger);
* the only legal change is recorded -> void with a reason; DELETE is
  blocked — a voided expense stays on the books for audit;
* writes are idempotent under ``Idempotency-Key`` and audited;
* ``support`` and ``auditor`` hold zero financial access (least privilege).
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from sqlalchemy import func, select
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
from ..models import Branch, Expense
from ..schemas import (
    ExpenseIn,
    ExpenseOut,
    ExpenseVoidIn,
    PageOut,
)
from ..services.clinical import idempotent_replay, idempotent_store

router = APIRouter(prefix="/expenses", tags=["expenses"])

MAX_LIMIT = 100


@router.post("", response_model=ExpenseOut, status_code=status.HTTP_201_CREATED)
def record_expense(
    payload: ExpenseIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_permission("expense.manage")),
    db: Session = Depends(get_db),
) -> ExpenseOut:
    body = payload.model_dump(mode="json")
    replay = idempotent_replay(
        db,
        organization_id=principal.organization_id,
        key=idempotency_key,
        endpoint="POST /expenses",
        payload=body,
    )
    if replay:
        return ExpenseOut.model_validate(replay["body"])

    if payload.branch_id is not None:
        branch = db.scalar(
            select(Branch).where(
                Branch.id == payload.branch_id,
                Branch.organization_id == principal.organization_id,
            )
        )
        if branch is None:
            raise HTTPException(status_code=404, detail="Branch not found")

    expense = Expense(
        organization_id=principal.organization_id,
        branch_id=payload.branch_id,
        category=payload.category,
        amount=payload.amount,
        expense_date=payload.expense_date,
        description=payload.description,
        reference=payload.reference,
        created_by=principal.user_id,
    )
    db.add(expense)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Expense creation conflict")

    out = ExpenseOut.model_validate(expense)
    record_event(
        db,
        action="expense.recorded",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="expense",
        resource_id=str(expense.id),
        metadata={
            "category": expense.category,
            "amount": str(expense.amount),
            "expense_date": expense.expense_date.isoformat(),
        },
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    idempotent_store(
        db,
        organization_id=principal.organization_id,
        key=idempotency_key,
        endpoint="POST /expenses",
        payload=body,
        status=201,
        body=out.model_dump(mode="json"),
    )
    return out


@router.get("", response_model=PageOut)
def list_expenses(
    category: str | None = None,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    expense_status: str | None = Query(default=None, alias="status"),
    limit: int = 50,
    offset: int = 0,
    principal: Principal = Depends(require_permission("expense.read")),
    db: Session = Depends(get_db),
) -> PageOut:
    limit = min(max(limit, 1), MAX_LIMIT)
    q = select(Expense).where(
        Expense.organization_id == principal.organization_id
    )
    if category:
        q = q.where(Expense.category == category)
    if date_from:
        q = q.where(Expense.expense_date >= date_from)
    if date_to:
        q = q.where(Expense.expense_date <= date_to)
    if expense_status:
        q = q.where(Expense.status == expense_status)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(
        q.order_by(Expense.expense_date.desc(), Expense.created_at.desc())
        .limit(limit).offset(offset)
    ).all()
    return PageOut(
        items=[ExpenseOut.model_validate(r) for r in rows],
        total=total or 0, limit=limit, offset=offset,
    )


@router.post("/{expense_id}/void", response_model=ExpenseOut)
def void_expense(
    expense_id: uuid.UUID,
    payload: ExpenseVoidIn,
    request: Request,
    principal: Principal = Depends(require_permission("expense.manage")),
    db: Session = Depends(get_db),
) -> ExpenseOut:
    expense = db.scalar(
        select(Expense).where(
            Expense.id == expense_id,
            Expense.organization_id == principal.organization_id,
        )
    )
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    if expense.status == "void":
        raise HTTPException(status_code=409, detail="Expense is already void")

    expense.status = "void"
    expense.void_reason = payload.reason
    db.flush()

    record_event(
        db,
        action="expense.voided",
        organization_id=str(principal.organization_id),
        actor_user_id=str(principal.user_id),
        resource_type="expense",
        resource_id=str(expense.id),
        metadata={
            "reason": payload.reason,
            "amount": str(expense.amount),
            "category": expense.category,
        },
        correlation_id=get_correlation_id(request),
        ip_address=get_client_ip(request),
    )
    return ExpenseOut.model_validate(expense)


@router.get("/summary", response_model=PageOut)
def expense_summary(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    principal: Principal = Depends(require_permission("expense.read")),
    db: Session = Depends(get_db),
) -> PageOut:
    """Per-category totals for the period (recorded expenses only)."""
    if date_to is None:
        date_to = date.today()
    if date_from is None:
        date_from = date_to - timedelta(days=30)
    rows = db.execute(
        select(
            Expense.category,
            func.count().label("count"),
            func.sum(Expense.amount).label("total"),
        )
        .where(
            Expense.organization_id == principal.organization_id,
            Expense.status == "recorded",
            Expense.expense_date >= date_from,
            Expense.expense_date <= date_to,
        )
        .group_by(Expense.category)
        .order_by(func.sum(Expense.amount).desc())
    ).all()
    items = [
        {"category": r[0], "count": int(r[1]), "total": float(r[2] or 0)}
        for r in rows
    ]
    return PageOut(
        items=items, total=len(items), limit=limit_default(), offset=0
    )


def limit_default() -> int:
    return MAX_LIMIT