"""
Leave Management Router
Base prefix: /leave
"""

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from modules.leave_engine import LeaveEngine

router = APIRouter(prefix="/leave", tags=["Leave Management"])


# ── Pydantic Models ──────────────────────────────────────────────────────────

class InitEntitlementsReq(BaseModel):
    entity_id:   UUID
    fiscal_year: int
    created_by:  Optional[str] = None


class CarryForwardReq(BaseModel):
    entity_id: UUID
    from_year: int


class CreateRequestReq(BaseModel):
    entity_id:     UUID
    employee_id:   UUID
    leave_type_id: UUID
    date_from:     date
    date_to:       date
    reason:        Optional[str] = None
    document_url:  Optional[str] = None


class SubmitReq(BaseModel):
    submitted_by: str


class ApproveReq(BaseModel):
    approved_by: str
    notes:       Optional[str] = None


class RejectReq(BaseModel):
    rejected_by: str
    reason:      str


class CancelReq(BaseModel):
    cancelled_by: str


class AdjustBalanceReq(BaseModel):
    entity_id:       UUID
    employee_id:     UUID
    leave_type_id:   UUID
    fiscal_year:     int
    adjustment_days: Decimal
    reason:          str
    adjusted_by:     str


class HolidayIn(BaseModel):
    holiday_date: date
    description:  str


# ── Leave Type Endpoints ─────────────────────────────────────────────────────

@router.get("/types", summary="List tipe cuti")
def list_leave_types(entity_id: UUID, db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT id, type_code, type_name, leave_category, is_paid,
                   default_days_per_year, max_carry_forward,
                   max_consecutive_days, requires_document,
                   notice_days_required, is_active
            FROM leave_type
            WHERE entity_id = :eid
            ORDER BY leave_category, type_name
        """),
        {"eid": str(entity_id)},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/types", summary="Tambah tipe cuti")
def create_leave_type(
    entity_id:              UUID,
    type_code:              str,
    type_name:              str,
    leave_category:         str,
    is_paid:                bool    = True,
    default_days_per_year:  Decimal = Decimal("12"),
    max_carry_forward:      Decimal = Decimal("0"),
    max_consecutive_days:   Optional[int] = None,
    requires_document:      bool    = False,
    notice_days_required:   int     = 1,
    db: Session = Depends(get_db),
):
    try:
        row = db.execute(
            text("""
                INSERT INTO leave_type (
                    entity_id, type_code, type_name, leave_category,
                    is_paid, default_days_per_year, max_carry_forward,
                    max_consecutive_days, requires_document, notice_days_required
                ) VALUES (
                    :eid, :code, :name, :cat,
                    :paid, :days, :carry,
                    :max_cons, :req_doc, :notice
                ) RETURNING id
            """),
            {
                "eid":     str(entity_id), "code": type_code, "name": type_name,
                "cat":     leave_category, "paid": is_paid,
                "days":    float(default_days_per_year), "carry": float(max_carry_forward),
                "max_cons": max_consecutive_days, "req_doc": requires_document,
                "notice":  notice_days_required,
            },
        ).fetchone()
        db.commit()
        return {"leave_type_id": str(row.id)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Public Holiday ───────────────────────────────────────────────────────────

@router.post("/holidays", summary="Tambah hari libur nasional")
def add_holiday(entity_id: UUID, holidays: list[HolidayIn], db: Session = Depends(get_db)):
    inserted = 0
    for h in holidays:
        db.execute(
            text("""
                INSERT INTO public_holiday (entity_id, holiday_date, description)
                VALUES (:eid, :dt, :desc)
                ON CONFLICT (entity_id, holiday_date) DO UPDATE SET description = :desc
            """),
            {"eid": str(entity_id), "dt": h.holiday_date, "desc": h.description},
        )
        inserted += 1
    db.commit()
    return {"inserted": inserted}


@router.get("/holidays", summary="List hari libur")
def list_holidays(entity_id: UUID, year: Optional[int] = None, db: Session = Depends(get_db)):
    params: dict = {"eid": str(entity_id)}
    extra = ""
    if year:
        extra = "AND EXTRACT(YEAR FROM holiday_date) = :yr"
        params["yr"] = year

    rows = db.execute(
        text(f"""
            SELECT holiday_date, description FROM public_holiday
            WHERE entity_id = :eid {extra}
            ORDER BY holiday_date
        """),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Entitlement ──────────────────────────────────────────────────────────────

@router.post("/entitlements/initialize", summary="Inisialisasi jatah cuti awal tahun")
def initialize_entitlements(req: InitEntitlementsReq, db: Session = Depends(get_db)):
    try:
        return LeaveEngine.initialize_entitlements(db, req.entity_id, req.fiscal_year, req.created_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/entitlements/carry-forward", summary="Rollover sisa cuti ke tahun berikutnya")
def carry_forward(req: CarryForwardReq, db: Session = Depends(get_db)):
    return LeaveEngine.carry_forward_balances(db, req.entity_id, req.from_year)


@router.get("/entitlements", summary="Saldo cuti per karyawan")
def get_entitlements(
    entity_id:   UUID,
    fiscal_year: int,
    employee_id: Optional[UUID] = None,
    department:  Optional[str]  = None,
    db: Session = Depends(get_db),
):
    filters = ["entity_id = :eid", "fiscal_year = :yr"]
    params: dict = {"eid": str(entity_id), "yr": fiscal_year}
    if employee_id:
        filters.append("employee_id = :emp")
        params["emp"] = str(employee_id)
    if department:
        filters.append("department = :dept")
        params["dept"] = department

    rows = db.execute(
        text(f"""
            SELECT * FROM vw_leave_balance
            WHERE {" AND ".join(filters)}
            ORDER BY employee_name, type_name
        """),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/entitlements/adjust", summary="Koreksi manual saldo cuti")
def adjust_balance(req: AdjustBalanceReq, db: Session = Depends(get_db)):
    try:
        return LeaveEngine.adjust_balance(
            db,
            entity_id       = req.entity_id,
            employee_id     = req.employee_id,
            leave_type_id   = req.leave_type_id,
            fiscal_year     = req.fiscal_year,
            adjustment_days = req.adjustment_days,
            reason          = req.reason,
            adjusted_by     = req.adjusted_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Leave Requests ───────────────────────────────────────────────────────────

@router.post("/requests", summary="Buat pengajuan cuti baru (draft)")
def create_request(req: CreateRequestReq, db: Session = Depends(get_db)):
    try:
        return LeaveEngine.create_request(
            db,
            entity_id     = req.entity_id,
            employee_id   = req.employee_id,
            leave_type_id = req.leave_type_id,
            date_from     = req.date_from,
            date_to       = req.date_to,
            reason        = req.reason,
            document_url  = req.document_url,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/requests", summary="List pengajuan cuti")
def list_requests(
    entity_id:   UUID,
    employee_id: Optional[UUID] = None,
    status:      Optional[str]  = None,
    date_from:   Optional[date] = None,
    date_to:     Optional[date] = None,
    department:  Optional[str]  = None,
    page:        int = Query(default=1, ge=1),
    size:        int = Query(default=30, ge=1, le=200),
    db: Session = Depends(get_db),
):
    filters = ["lr.entity_id = :eid"]
    params: dict = {"eid": str(entity_id), "offset": (page - 1) * size, "limit": size}

    if employee_id:
        filters.append("lr.employee_id = :emp")
        params["emp"] = str(employee_id)
    if status:
        filters.append("lr.status = :status")
        params["status"] = status
    if date_from:
        filters.append("lr.date_from >= :d_from")
        params["d_from"] = date_from
    if date_to:
        filters.append("lr.date_to <= :d_to")
        params["d_to"] = date_to
    if department:
        filters.append("e.department = :dept")
        params["dept"] = department

    where = " AND ".join(filters)
    rows = db.execute(
        text(f"""
            SELECT lr.id, lr.request_no, lr.employee_id,
                   e.full_name AS employee_name, e.department,
                   lt.type_name, lt.leave_category,
                   lr.date_from, lr.date_to, lr.total_days,
                   lr.reason, lr.status, lr.submitted_at,
                   lr.approved_by, lr.is_unpaid_deduction
            FROM leave_request lr
            JOIN employee   e  ON e.id  = lr.employee_id
            JOIN leave_type lt ON lt.id = lr.leave_type_id
            WHERE {where}
            ORDER BY lr.date_from DESC
            OFFSET :offset LIMIT :limit
        """),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/requests/{request_id}/submit", summary="Submit pengajuan ke manager")
def submit(request_id: UUID, req: SubmitReq, db: Session = Depends(get_db)):
    try:
        return LeaveEngine.submit_request(db, request_id, req.submitted_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/requests/{request_id}/approve", summary="Manager approve cuti")
def approve(request_id: UUID, req: ApproveReq, db: Session = Depends(get_db)):
    try:
        return LeaveEngine.approve_request(db, request_id, req.approved_by, req.notes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/requests/{request_id}/reject", summary="Manager reject cuti")
def reject(request_id: UUID, req: RejectReq, db: Session = Depends(get_db)):
    try:
        return LeaveEngine.reject_request(db, request_id, req.rejected_by, req.reason)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/requests/{request_id}/cancel", summary="Batalkan cuti")
def cancel(request_id: UUID, req: CancelReq, db: Session = Depends(get_db)):
    try:
        return LeaveEngine.cancel_request(db, request_id, req.cancelled_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Calendar & Reports ───────────────────────────────────────────────────────

@router.get("/calendar", summary="Kalender cuti karyawan")
def leave_calendar(
    entity_id:  UUID,
    date_from:  date,
    date_to:    date,
    department: Optional[str] = None,
    db: Session = Depends(get_db),
):
    return LeaveEngine.get_leave_calendar(db, entity_id, date_from, date_to, department)


@router.get("/reports/summary", summary="Ringkasan saldo cuti per karyawan")
def summary(entity_id: UUID, fiscal_year: int, db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT * FROM vw_leave_summary_by_employee
            WHERE entity_id = :eid AND fiscal_year = :yr
            ORDER BY department, employee_name
        """),
        {"eid": str(entity_id), "yr": fiscal_year},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/reports/pending", summary="Pengajuan yang menunggu approval")
def pending_requests(entity_id: UUID, db: Session = Depends(get_db)):
    rows = db.execute(
        text("SELECT * FROM vw_pending_leave_requests WHERE entity_id=:eid ORDER BY submitted_at"),
        {"eid": str(entity_id)},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/reports/unpaid-deductions", summary="LWOP untuk integrasi payroll")
def unpaid_deductions(
    entity_id:   UUID,
    year:        int,
    month:       int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
):
    return LeaveEngine.get_unpaid_deductions(db, entity_id, year, month)
