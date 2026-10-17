"""M6 reports API: IPD, finance and inventory summaries for one period.

Reports only aggregate THIS tenant's rows (the queries all carry
``organization_id`` and RLS is the backstop), and every endpoint needs
``report.read`` — support and auditor hold no M6 grants (least privilege).
Numbers are read-only derivations of frozen records: nothing is written or
mutated by a report.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, require_permission
from ..models import (
    Admission,
    Bed,
    Expense,
    InventoryItem,
    Payment,
    Refund,
)
from ..schemas import (
    FinanceCategoryRow,
    FinanceReport,
    IpdReport,
    InventoryReport,
    ReportSummaryOut,
)

router = APIRouter(prefix="/reports", tags=["reports"])


def _day_start(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


def _day_end(d: date) -> datetime:
    return datetime.combine(d, time.max, tzinfo=timezone.utc)


@router.get("/summary", response_model=ReportSummaryOut)
def report_summary(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    principal: Principal = Depends(require_permission("report.read")),
    db: Session = Depends(get_db),
) -> ReportSummaryOut:
    today = date.today()
    date_to = date_to or today
    date_from = date_from or (date_to - timedelta(days=30))
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    org = principal.organization_id

    beds_total = db.scalar(
        select(func.count()).select_from(Bed).where(Bed.organization_id == org)
    ) or 0
    beds_maintenance = db.scalar(
        select(func.count()).select_from(Bed).where(
            Bed.organization_id == org, Bed.status == "maintenance"
        )
    ) or 0
    beds_occupied = db.scalar(
        select(func.count()).select_from(Admission).where(
            Admission.organization_id == org, Admission.status == "admitted"
        )
    ) or 0
    currently_admitted = beds_occupied
    admissions_in_period = db.scalar(
        select(func.count()).select_from(Admission).where(
            Admission.organization_id == org,
            Admission.admitted_at >= _day_start(date_from),
            Admission.admitted_at <= _day_end(date_to),
        )
    ) or 0
    discharges_in_period = db.scalar(
        select(func.count()).select_from(Admission).where(
            Admission.organization_id == org,
            Admission.status == "discharged",
            Admission.discharged_at >= _day_start(date_from),
            Admission.discharged_at <= _day_end(date_to),
        )
    ) or 0
    deaths_in_period = db.scalar(
        select(func.count()).select_from(Admission).where(
            Admission.organization_id == org,
            Admission.status == "discharged",
            Admission.discharge_outcome == "death",
            Admission.discharged_at >= _day_start(date_from),
            Admission.discharged_at <= _day_end(date_to),
        )
    ) or 0

    revenue = db.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.organization_id == org,
            Payment.status == "succeeded",
            Payment.created_at >= _day_start(date_from),
            Payment.created_at <= _day_end(date_to),
        )
    ) or 0
    refunds = db.scalar(
        select(func.coalesce(func.sum(Refund.amount), 0)).where(
            Refund.organization_id == org,
            Refund.status == "succeeded",
            Refund.created_at >= _day_start(date_from),
            Refund.created_at <= _day_end(date_to),
        )
    ) or 0

    expense_rows = db.execute(
        select(
            Expense.category,
            func.sum(Expense.amount),
        )
        .where(
            Expense.organization_id == org,
            Expense.status == "recorded",
            Expense.expense_date >= date_from,
            Expense.expense_date <= date_to,
        )
        .group_by(Expense.category)
        .order_by(func.sum(Expense.amount).desc())
    ).all()
    expense_total = sum(float(r[1] or 0) for r in expense_rows)
    by_category = [
        FinanceCategoryRow(category=r[0], total=float(r[1] or 0))
        for r in expense_rows
    ]

    items_active = db.scalar(
        select(func.count()).select_from(InventoryItem).where(
            InventoryItem.organization_id == org,
            InventoryItem.status == "active",
        )
    ) or 0
    low_rows = db.scalars(
        select(InventoryItem).where(
            InventoryItem.organization_id == org,
            InventoryItem.status == "active",
            InventoryItem.reorder_level > 0,
            InventoryItem.quantity_on_hand <= InventoryItem.reorder_level,
        )
        .order_by(InventoryItem.quantity_on_hand)
        .limit(20)
    ).all()

    return ReportSummaryOut(
        from_date=date_from,
        to_date=date_to,
        ipd=IpdReport(
            currently_admitted=currently_admitted,
            admissions_in_period=admissions_in_period,
            discharges_in_period=discharges_in_period,
            deaths_in_period=deaths_in_period,
            beds_total=beds_total,
            beds_occupied=beds_occupied,
            beds_maintenance=beds_maintenance,
        ),
        finance=FinanceReport(
            revenue=float(revenue),
            refunds=float(refunds),
            expenses=expense_total,
            net=float(revenue) - float(refunds) - expense_total,
            by_category=by_category,
        ),
        inventory=InventoryReport(
            items_active=items_active,
            low_stock=[
                {
                    "id": str(i.id),
                    "name": i.name,
                    "unit": i.unit,
                    "quantity_on_hand": i.quantity_on_hand,
                    "reorder_level": i.reorder_level,
                }
                for i in low_rows
            ],
        ),
    )