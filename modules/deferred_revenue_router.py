# modules/deferred_revenue_router.py
# Deferred Revenue Engine REST API — PSA gap #3 (BRD user 2026-06-28).

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.database import get_db
from modules.auth import require_min_role
from modules.deferred_revenue_engine import DeferredRevenueEngine

router = APIRouter(prefix="/deferred-revenue", tags=["Deferred Revenue (PSA)"])

_finance = [Depends(require_min_role("finance"))]
_viewer  = [Depends(require_min_role("viewer"))]


class RecordPaymentPayload(BaseModel):
    amount:             Decimal = Field(..., gt=0)
    payment_date:       date
    bank_account_code:  str = "1-1-001"
    notes:              Optional[str] = None


class RecognizeRevenuePayload(BaseModel):
    recognition_date: Optional[date] = None


@router.post("/milestones/{milestone_id}/payment", dependencies=_finance,
             summary="Catat penerimaan termin pembayaran milestone (Dr Bank, Cr Deferred Revenue)")
def record_payment(
    milestone_id: str,
    payload:      RecordPaymentPayload,
    db:           Session = Depends(get_db),
    user=         Depends(require_min_role("finance")),
):
    engine = DeferredRevenueEngine(db)
    try:
        return engine.record_payment(
            milestone_id=milestone_id,
            amount=payload.amount,
            payment_date=payload.payment_date,
            created_by=user.get("email", "system"),
            bank_account_code=payload.bank_account_code,
            notes=payload.notes,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/milestones/{milestone_id}/recognize", dependencies=_finance,
             summary="Rekognisi revenue proporsional ke progress milestone (Dr Deferred Revenue, Cr Revenue)")
def recognize_revenue(
    milestone_id: str,
    payload:      RecognizeRevenuePayload,
    db:           Session = Depends(get_db),
    user=         Depends(require_min_role("finance")),
):
    engine = DeferredRevenueEngine(db)
    try:
        return engine.recognize_revenue(
            milestone_id=milestone_id,
            created_by=user.get("email", "system"),
            recognition_date=payload.recognition_date,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/projects/{project_id}/summary", dependencies=_viewer,
            summary="Ringkasan deferred revenue per milestone (billing/paid/recognized/balance)")
def get_summary(project_id: str, db: Session = Depends(get_db)):
    return DeferredRevenueEngine(db).get_summary(project_id)
