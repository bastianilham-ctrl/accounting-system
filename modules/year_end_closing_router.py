"""
Year-End Closing Router
Base prefix: /year-end
"""

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from modules.year_end_closing_engine import YearEndClosingEngine

router = APIRouter(prefix="/year-end", tags=["Year-End Closing"])


# ── Pydantic Models ──────────────────────────────────────────────────────────

class SetupFiscalYearReq(BaseModel):
    entity_id:   UUID
    fiscal_year: int
    start_month: int = 1


class PreCheckReq(BaseModel):
    entity_id:   UUID
    fiscal_year: int


class IncomeSummaryReq(BaseModel):
    entity_id:              UUID
    fiscal_year:            int
    closing_date:           date
    closed_by:              str
    income_summary_account: Optional[str] = None


class RETransferReq(BaseModel):
    entity_id:                  UUID
    fiscal_year:                int
    closing_date:               date
    closed_by:                  str
    income_summary_account:     Optional[str] = None
    retained_earnings_account:  Optional[str] = None


class LockPeriodsReq(BaseModel):
    entity_id:   UUID
    fiscal_year: int
    locked_by:   str


class CloseFiscalYearReq(BaseModel):
    entity_id:                  UUID
    fiscal_year:                int
    closing_date:               date
    closed_by:                  str
    skip_checks:                bool         = False
    income_summary_account:     Optional[str] = None
    retained_earnings_account:  Optional[str] = None


class ReopenPeriodReq(BaseModel):
    entity_id:    UUID
    period_year:  int
    period_month: int
    reopened_by:  str
    reason:       str


class LockSinglePeriodReq(BaseModel):
    entity_id:    UUID
    period_year:  int
    period_month: int
    locked_by:    str


# ── Fiscal Year Setup ─────────────────────────────────────────────────────────

@router.post("/fiscal-years/setup", summary="Setup fiscal year + 12 periods")
def setup_fiscal_year(req: SetupFiscalYearReq, db: Session = Depends(get_db)):
    try:
        return YearEndClosingEngine.setup_fiscal_year(
            db, req.entity_id, req.fiscal_year, req.start_month
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/fiscal-years", summary="List fiscal years")
def list_fiscal_years(entity_id: UUID, db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT * FROM vw_fiscal_year_status
            WHERE entity_id=:eid
            ORDER BY fiscal_year DESC
        """),
        {"eid": str(entity_id)},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/fiscal-years/{fiscal_year}/status", summary="Status penutupan fiscal year")
def closing_status(fiscal_year: int, entity_id: UUID, db: Session = Depends(get_db)):
    return YearEndClosingEngine.get_closing_status(db, entity_id, fiscal_year)


# ── Fiscal Period ─────────────────────────────────────────────────────────────

@router.get("/periods", summary="List fiscal periods")
def list_periods(
    entity_id:   UUID,
    fiscal_year: Optional[int]  = None,
    status:      Optional[str]  = None,
    db: Session = Depends(get_db),
):
    filters = ["entity_id=:eid"]
    params: dict = {"eid": str(entity_id)}
    if fiscal_year:
        filters.append("period_year=:yr")
        params["yr"] = fiscal_year
    if status:
        filters.append("status=:status")
        params["status"] = status

    rows = db.execute(
        text(f"""
            SELECT id, period_year, period_month, start_date, end_date, status,
                   locked_by, locked_at
            FROM fiscal_period
            WHERE {" AND ".join(filters)}
            ORDER BY period_year DESC, period_month DESC
        """),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/periods/lock-single", summary="Lock satu periode (admin)")
def lock_single_period(req: LockSinglePeriodReq, db: Session = Depends(get_db)):
    result = db.execute(
        text("""
            UPDATE fiscal_period
            SET status='locked', locked_by=:by, locked_at=NOW()
            WHERE entity_id=:eid AND period_year=:yr AND period_month=:mo AND status='open'
        """),
        {
            "eid": str(req.entity_id), "yr": req.period_year,
            "mo":  req.period_month,    "by": req.locked_by,
        },
    )
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=400, detail="Periode tidak ditemukan atau sudah terkunci.")
    return {"status": "locked", "period": f"{req.period_year}-{req.period_month:02d}"}


@router.post("/periods/reopen", summary="Buka kembali periode terkunci (admin)")
def reopen_period(req: ReopenPeriodReq, db: Session = Depends(get_db)):
    try:
        return YearEndClosingEngine.reopen_period(
            db,
            entity_id    = req.entity_id,
            period_year  = req.period_year,
            period_month = req.period_month,
            reopened_by  = req.reopened_by,
            reason       = req.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Closing Steps ─────────────────────────────────────────────────────────────

@router.post("/pre-closing-checks", summary="Step 1: Jalankan pre-closing checks")
def pre_closing_checks(req: PreCheckReq, db: Session = Depends(get_db)):
    return YearEndClosingEngine.run_pre_closing_checks(db, req.entity_id, req.fiscal_year)


@router.post("/income-summary", summary="Step 2: Jurnal penutup revenue & expense")
def income_summary(req: IncomeSummaryReq, db: Session = Depends(get_db)):
    try:
        return YearEndClosingEngine.run_income_summary(
            db,
            entity_id               = req.entity_id,
            fiscal_year             = req.fiscal_year,
            closing_date            = req.closing_date,
            closed_by               = req.closed_by,
            income_summary_account  = req.income_summary_account,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/re-transfer", summary="Step 3: Transfer Ikhtisar L/R → Laba Ditahan")
def re_transfer(req: RETransferReq, db: Session = Depends(get_db)):
    try:
        return YearEndClosingEngine.run_re_transfer(
            db,
            entity_id                  = req.entity_id,
            fiscal_year                = req.fiscal_year,
            closing_date               = req.closing_date,
            closed_by                  = req.closed_by,
            income_summary_account     = req.income_summary_account,
            retained_earnings_account  = req.retained_earnings_account,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/lock-periods", summary="Step 4: Lock semua periode fiscal year")
def lock_periods(req: LockPeriodsReq, db: Session = Depends(get_db)):
    try:
        return YearEndClosingEngine.lock_periods(db, req.entity_id, req.fiscal_year, req.locked_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/close", summary="Tutup buku lengkap (semua langkah sekaligus)")
def close_fiscal_year(req: CloseFiscalYearReq, db: Session = Depends(get_db)):
    try:
        return YearEndClosingEngine.close_fiscal_year(
            db,
            entity_id                  = req.entity_id,
            fiscal_year                = req.fiscal_year,
            closing_date               = req.closing_date,
            closed_by                  = req.closed_by,
            skip_checks                = req.skip_checks,
            income_summary_account     = req.income_summary_account,
            retained_earnings_account  = req.retained_earnings_account,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Period Guard (middleware-style check, bisa dipanggil dari engine lain) ────

@router.get("/period-check", summary="Cek apakah periode open untuk posting")
def period_check(
    entity_id:    UUID,
    journal_date: date,
    db: Session = Depends(get_db),
):
    """
    Endpoint untuk validasi sebelum posting jurnal.
    Kembalikan {'can_post': true/false, 'reason': ...}
    """
    period = db.execute(
        text("""
            SELECT status FROM fiscal_period
            WHERE entity_id=:eid
              AND period_year=:yr AND period_month=:mo
        """),
        {
            "eid": str(entity_id),
            "yr":  journal_date.year,
            "mo":  journal_date.month,
        },
    ).fetchone()

    if not period:
        return {
            "can_post": True,
            "reason":   "Periode belum dikonfigurasi, posting diizinkan.",
            "status":   "unconfigured",
        }

    if period.status == "locked":
        return {
            "can_post": False,
            "reason":   f"Periode {journal_date.year}-{journal_date.month:02d} sudah terkunci.",
            "status":   "locked",
        }

    return {"can_post": True, "reason": "Periode open.", "status": "open"}
