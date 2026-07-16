"""
Expense Claim & Reimbursement Router
Base prefix: /expense-claims
"""

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from modules.expense_claim_engine import ExpenseClaimEngine

router = APIRouter(prefix="/expense-claims", tags=["Expense Claim & Reimbursement"])


# ── Pydantic Models ──────────────────────────────────────────────────────────

class ClaimLineIn(BaseModel):
    category_id:      UUID
    expense_date:     date
    description:      str
    quantity:         Decimal = Decimal("1")
    unit_amount:      Decimal
    is_billable:      bool    = False
    gl_account_code:  Optional[str] = None
    receipt_filename: Optional[str] = None
    notes:            Optional[str] = None


class CreateClaimReq(BaseModel):
    entity_id:      UUID
    employee_id:    UUID
    claim_date:     date
    period_from:    date
    period_to:      date
    purpose:        str
    lines:          list[ClaimLineIn]
    project_id:     Optional[UUID] = None
    cost_center_id: Optional[UUID] = None
    created_by:     Optional[str]  = None


class SubmitReq(BaseModel):
    submitted_by: str


class ApproveReq(BaseModel):
    approved_by:     str
    approved_amount: Optional[Decimal] = None
    notes:           Optional[str]     = None


class RejectReq(BaseModel):
    rejected_by: str
    reason:      str


class LineUpdateIn(BaseModel):
    line_id:         UUID
    gl_account_code: Optional[str]     = None
    approved_amount: Optional[Decimal] = None


class VerifyReq(BaseModel):
    verified_by:  str
    line_updates: Optional[list[LineUpdateIn]] = None


class PayClaimReq(BaseModel):
    payment_method:  str   = Field(..., pattern="^(bank_transfer|cash|payroll_deduction)$")
    paid_by:         str
    payment_date:    date
    bank_account_id: Optional[UUID] = None


class CreateAdvanceReq(BaseModel):
    entity_id:        UUID
    employee_id:      UUID
    advance_date:     date
    purpose:          str
    amount_requested: Decimal
    project_id:       Optional[UUID] = None
    approved_by:      Optional[str]  = None


class DisburseAdvanceReq(BaseModel):
    disbursed_by:        str
    disburse_date:       date
    bank_account_id:     UUID
    kas_bon_account_code: str = "1-1300"


class SettleAdvanceReq(BaseModel):
    claim_id:            UUID
    settled_by:          str
    settle_date:         date
    kas_bon_account_code: str = "1-1300"


# ── Expense Categories ───────────────────────────────────────────────────────

@router.get("/categories", summary="List kategori expense")
def list_categories(entity_id: UUID, db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT id, category_code, category_name, expense_type,
                   gl_account_code, max_amount, requires_receipt,
                   is_billable_default, is_active
            FROM expense_category
            WHERE entity_id = :eid AND is_active = TRUE
            ORDER BY expense_type, category_name
        """),
        {"eid": str(entity_id)},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/categories", summary="Tambah kategori expense")
def create_category(
    entity_id:          UUID,
    category_code:      str,
    category_name:      str,
    expense_type:       str,
    gl_account_code:    Optional[str]  = None,
    max_amount:         Optional[Decimal] = None,
    requires_receipt:   bool = True,
    is_billable_default: bool = False,
    db: Session = Depends(get_db),
):
    row = db.execute(
        text("""
            INSERT INTO expense_category (
                entity_id, category_code, category_name, expense_type,
                gl_account_code, max_amount, requires_receipt, is_billable_default
            ) VALUES (
                :eid, :code, :name, :type,
                :gl, :max, :receipt, :billable
            ) RETURNING id
        """),
        {
            "eid":     str(entity_id), "code": category_code, "name": category_name,
            "type":    expense_type,   "gl":   gl_account_code,
            "max":     float(max_amount) if max_amount else None,
            "receipt": requires_receipt, "billable": is_billable_default,
        },
    ).fetchone()
    db.commit()
    return {"category_id": str(row.id)}


# ── Claims ───────────────────────────────────────────────────────────────────

@router.post("", summary="Buat klaim expense baru (draft)")
def create_claim(req: CreateClaimReq, db: Session = Depends(get_db)):
    try:
        return ExpenseClaimEngine.create_claim(
            db,
            entity_id      = req.entity_id,
            employee_id    = req.employee_id,
            claim_date     = req.claim_date,
            period_from    = req.period_from,
            period_to      = req.period_to,
            purpose        = req.purpose,
            lines          = [l.model_dump() for l in req.lines],
            project_id     = req.project_id,
            cost_center_id = req.cost_center_id,
            created_by     = req.created_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", summary="List klaim expense")
def list_claims(
    entity_id:   UUID,
    employee_id: Optional[UUID] = None,
    status:      Optional[str]  = None,
    project_id:  Optional[UUID] = None,
    date_from:   Optional[date] = None,
    date_to:     Optional[date] = None,
    page:        int = Query(default=1, ge=1),
    size:        int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return ExpenseClaimEngine.list_claims(
        db,
        entity_id   = entity_id,
        employee_id = employee_id,
        status      = status,
        project_id  = project_id,
        date_from   = date_from,
        date_to     = date_to,
        page        = page,
        size        = size,
    )


@router.get("/{claim_id}", summary="Detail klaim + baris")
def get_claim(claim_id: UUID, db: Session = Depends(get_db)):
    try:
        return ExpenseClaimEngine.get_claim_detail(db, claim_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{claim_id}/submit", summary="Submit klaim ke manager")
def submit(claim_id: UUID, req: SubmitReq, db: Session = Depends(get_db)):
    try:
        return ExpenseClaimEngine.submit_claim(db, claim_id, req.submitted_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{claim_id}/approve", summary="Manager approve klaim")
def approve(claim_id: UUID, req: ApproveReq, db: Session = Depends(get_db)):
    try:
        return ExpenseClaimEngine.approve_claim(
            db, claim_id,
            approved_by     = req.approved_by,
            approved_amount = req.approved_amount,
            notes           = req.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{claim_id}/reject", summary="Manager reject klaim")
def reject(claim_id: UUID, req: RejectReq, db: Session = Depends(get_db)):
    try:
        return ExpenseClaimEngine.reject_claim(db, claim_id, req.rejected_by, req.reason)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{claim_id}/verify", summary="Finance verifikasi klaim + set GL")
def verify(claim_id: UUID, req: VerifyReq, db: Session = Depends(get_db)):
    try:
        return ExpenseClaimEngine.verify_claim(
            db, claim_id,
            verified_by  = req.verified_by,
            line_updates = [u.model_dump() for u in req.line_updates] if req.line_updates else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{claim_id}/pay", summary="Finance bayar reimbursement + posting GL")
def pay(claim_id: UUID, req: PayClaimReq, db: Session = Depends(get_db)):
    try:
        return ExpenseClaimEngine.pay_claim(
            db, claim_id,
            payment_method  = req.payment_method,
            paid_by         = req.paid_by,
            payment_date    = req.payment_date,
            bank_account_id = req.bank_account_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Advance ──────────────────────────────────────────────────────────────────

@router.post("/advances", summary="Buat permohonan uang muka")
def create_advance(req: CreateAdvanceReq, db: Session = Depends(get_db)):
    try:
        return ExpenseClaimEngine.create_advance(
            db,
            entity_id        = req.entity_id,
            employee_id      = req.employee_id,
            advance_date     = req.advance_date,
            purpose          = req.purpose,
            amount_requested = req.amount_requested,
            project_id       = req.project_id,
            approved_by      = req.approved_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/advances", summary="List uang muka")
def list_advances(
    entity_id:   UUID,
    employee_id: Optional[UUID] = None,
    status:      Optional[str]  = None,
    db: Session = Depends(get_db),
):
    filters = ["ea.entity_id = :eid"]
    params: dict = {"eid": str(entity_id)}
    if employee_id:
        filters.append("ea.employee_id = :emp")
        params["emp"] = str(employee_id)
    if status:
        filters.append("ea.status = :status")
        params["status"] = status

    where = " AND ".join(filters)
    rows = db.execute(
        text(f"""
            SELECT ea.id, ea.advance_no, e.full_name AS employee_name,
                   ea.advance_date, ea.purpose,
                   ea.amount_requested, ea.amount_approved,
                   ea.amount_disbursed, ea.amount_settled, ea.balance_due,
                   ea.status, ea.project_id
            FROM expense_advance ea
            JOIN employee e ON e.id = ea.employee_id
            WHERE {where}
            ORDER BY ea.advance_date DESC
        """),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/advances/{advance_id}/disburse", summary="Cair uang muka + posting GL")
def disburse(advance_id: UUID, req: DisburseAdvanceReq, db: Session = Depends(get_db)):
    try:
        return ExpenseClaimEngine.disburse_advance(
            db, advance_id,
            disbursed_by         = req.disbursed_by,
            disburse_date        = req.disburse_date,
            bank_account_id      = req.bank_account_id,
            kas_bon_account_code = req.kas_bon_account_code,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/advances/{advance_id}/settle", summary="Pertanggungjawaban uang muka ke klaim")
def settle(advance_id: UUID, req: SettleAdvanceReq, db: Session = Depends(get_db)):
    try:
        return ExpenseClaimEngine.settle_advance(
            db, advance_id,
            claim_id             = req.claim_id,
            settled_by           = req.settled_by,
            settle_date          = req.settle_date,
            kas_bon_account_code = req.kas_bon_account_code,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/advances/outstanding", summary="Uang muka yang belum dipertanggungjawabkan")
def outstanding_advances(entity_id: UUID, db: Session = Depends(get_db)):
    rows = db.execute(
        text("SELECT * FROM vw_advance_outstanding WHERE entity_id=:eid ORDER BY advance_date"),
        {"eid": str(entity_id)},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Reports ──────────────────────────────────────────────────────────────────

@router.get("/reports/project-passthrough", summary="Biaya billable per proyek")
def project_passthrough(entity_id: UUID, project_id: Optional[UUID] = None, db: Session = Depends(get_db)):
    filters = ["entity_id = :eid"]
    params: dict = {"eid": str(entity_id)}
    if project_id:
        filters.append("project_id = :proj")
        params["proj"] = str(project_id)

    rows = db.execute(
        text(f"""
            SELECT * FROM vw_project_expense_passthrough
            WHERE {" AND ".join(filters)}
            ORDER BY expense_date DESC
        """),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]
