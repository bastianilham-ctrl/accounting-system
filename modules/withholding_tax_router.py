"""
Withholding Tax Router — PPh 23 & PPh 4(2)
Base prefix: /wht
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
from modules.withholding_tax_engine import WithholdingTaxEngine

router = APIRouter(prefix="/wht", tags=["PPh 23 & PPh 4(2)"])


# ── Pydantic Models ──────────────────────────────────────────────────────────

class CreateFromInvoiceReq(BaseModel):
    ap_invoice_id:   UUID
    tax_type:        str   = Field(..., pattern="^(PPh23|PPh4_2)$")
    income_type_code: str
    dpp:             Decimal
    created_by:      Optional[str] = None


class CreateManualReq(BaseModel):
    entity_id:       UUID
    vendor_id:       UUID
    tax_type:        str   = Field(..., pattern="^(PPh23|PPh4_2)$")
    income_type_code: str
    transaction_date: date
    dpp:             Decimal
    description:     Optional[str] = None
    has_npwp:        bool   = True


class ConfirmReq(BaseModel):
    confirmed_by:       str
    gl_account_payable: Optional[str] = None
    gl_account_expense: Optional[str] = None


class IssueBuktiPotongReq(BaseModel):
    bukti_potong_date: date
    issued_by:         str


class CreateSPTReq(BaseModel):
    entity_id:    UUID
    tax_type:     str   = Field(..., pattern="^(PPh23|PPh4_2)$")
    period_year:  int
    period_month: int   = Field(..., ge=1, le=12)
    created_by:   str


class SubmitSPTReq(BaseModel):
    payment_date:       date
    ntpn:               str
    submitted_by:       str
    bank_account_id:    UUID
    gl_account_payable: Optional[str] = None


class VoidReq(BaseModel):
    voided_by: str
    reason:    str


# ── Tarif ───────────────────────────────────────────────────────────────────

@router.get("/rates", summary="Daftar tarif PPh 23 & PPh 4(2)")
def list_rates(
    tax_type: Optional[str] = None,
    db: Session = Depends(get_db),
):
    params: dict = {}
    extra = ""
    if tax_type:
        extra = "WHERE tax_type = :tt"
        params["tt"] = tax_type

    rows = db.execute(
        text(f"""
            SELECT tax_type, income_type, income_type_code,
                   rate_pct, rate_npwp_pct, effective_date, notes
            FROM wht_rate {extra}
            ORDER BY tax_type, income_type_code
        """),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Transactions ─────────────────────────────────────────────────────────────

@router.post("/transactions/from-invoice", summary="Buat WHT dari AP invoice")
def create_from_invoice(req: CreateFromInvoiceReq, db: Session = Depends(get_db)):
    try:
        return WithholdingTaxEngine.create_from_invoice(
            db,
            ap_invoice_id    = req.ap_invoice_id,
            tax_type         = req.tax_type,
            income_type_code = req.income_type_code,
            dpp              = req.dpp,
            created_by       = req.created_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/transactions/manual", summary="Buat WHT manual")
def create_manual(req: CreateManualReq, db: Session = Depends(get_db)):
    try:
        return WithholdingTaxEngine.create_manual(
            db,
            entity_id        = req.entity_id,
            vendor_id        = req.vendor_id,
            tax_type         = req.tax_type,
            income_type_code = req.income_type_code,
            transaction_date = req.transaction_date,
            dpp              = req.dpp,
            description      = req.description,
            has_npwp         = req.has_npwp,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/transactions", summary="List transaksi WHT")
def list_transactions(
    entity_id:    UUID,
    tax_type:     Optional[str]  = None,
    status:       Optional[str]  = None,
    period_year:  Optional[int]  = None,
    period_month: Optional[int]  = None,
    vendor_id:    Optional[UUID] = None,
    page:         int = Query(default=1, ge=1),
    size:         int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    filters = ["wt.entity_id = :eid"]
    params: dict = {"eid": str(entity_id), "offset": (page - 1) * size, "limit": size}

    if tax_type:
        filters.append("wt.tax_type = :tt")
        params["tt"] = tax_type
    if status:
        filters.append("wt.status = :status")
        params["status"] = status
    if period_year:
        filters.append("wt.period_year = :yr")
        params["yr"] = period_year
    if period_month:
        filters.append("wt.period_month = :mo")
        params["mo"] = period_month
    if vendor_id:
        filters.append("wt.vendor_id = :vid")
        params["vid"] = str(vendor_id)

    where = " AND ".join(filters)
    rows = db.execute(
        text(f"""
            SELECT wt.id, wt.tax_type, wt.income_type_code,
                   wt.transaction_date, wt.period_year, wt.period_month,
                   v.vendor_name, v.npwp, wt.has_npwp,
                   wt.dpp, wt.rate_pct, wt.tax_amount,
                   wt.status, wt.bukti_potong_no, wt.bukti_potong_date,
                   wt.description
            FROM wht_transaction wt
            JOIN vendor v ON v.id = wt.vendor_id
            WHERE {where}
            ORDER BY wt.transaction_date DESC
            OFFSET :offset LIMIT :limit
        """),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/transactions/{wht_id}/confirm", summary="Konfirmasi WHT + posting GL")
def confirm(wht_id: UUID, req: ConfirmReq, db: Session = Depends(get_db)):
    try:
        return WithholdingTaxEngine.confirm_transaction(
            db, wht_id,
            confirmed_by       = req.confirmed_by,
            gl_account_payable = req.gl_account_payable,
            gl_account_expense = req.gl_account_expense,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/transactions/{wht_id}/issue-bukti-potong",
             summary="Terbitkan bukti potong")
def issue_bukti_potong(wht_id: UUID, req: IssueBuktiPotongReq, db: Session = Depends(get_db)):
    try:
        return WithholdingTaxEngine.issue_bukti_potong(
            db, wht_id,
            bukti_potong_date = req.bukti_potong_date,
            issued_by         = req.issued_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/transactions/{wht_id}/void", summary="Void transaksi WHT")
def void(wht_id: UUID, req: VoidReq, db: Session = Depends(get_db)):
    try:
        return WithholdingTaxEngine.void_transaction(db, wht_id, req.voided_by, req.reason)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/transactions/pending-bukti-potong",
            summary="Transaksi yang belum diterbitkan bukti potong")
def pending_bukti_potong(entity_id: UUID, tax_type: Optional[str] = None, db: Session = Depends(get_db)):
    params: dict = {"eid": str(entity_id)}
    extra = ""
    if tax_type:
        extra = "AND tax_type = :tt"
        params["tt"] = tax_type

    rows = db.execute(
        text(f"SELECT * FROM vw_wht_pending_bukti_potong WHERE entity_id=:eid {extra} ORDER BY transaction_date"),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── SPT Masa ─────────────────────────────────────────────────────────────────

@router.post("/spt-masa", summary="Buat SPT Masa (rekap bulanan)")
def create_spt(req: CreateSPTReq, db: Session = Depends(get_db)):
    try:
        return WithholdingTaxEngine.create_spt_masa(
            db,
            entity_id    = req.entity_id,
            tax_type     = req.tax_type,
            period_year  = req.period_year,
            period_month = req.period_month,
            created_by   = req.created_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/spt-masa", summary="List SPT Masa")
def list_spt(
    entity_id:    UUID,
    tax_type:     Optional[str] = None,
    period_year:  Optional[int] = None,
    db: Session = Depends(get_db),
):
    filters = ["entity_id = :eid"]
    params: dict = {"eid": str(entity_id)}
    if tax_type:
        filters.append("tax_type = :tt")
        params["tt"] = tax_type
    if period_year:
        filters.append("period_year = :yr")
        params["yr"] = period_year

    rows = db.execute(
        text(f"""
            SELECT id, tax_type, period_year, period_month,
                   total_dpp, total_tax, total_bukti_potong,
                   status, payment_date, payment_ntpn
            FROM wht_spt_masa
            WHERE {" AND ".join(filters)}
            ORDER BY period_year DESC, period_month DESC, tax_type
        """),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/spt-masa/{spt_id}/submit", summary="Submit SPT + setor ke DJP + posting GL")
def submit_spt(spt_id: UUID, req: SubmitSPTReq, db: Session = Depends(get_db)):
    try:
        return WithholdingTaxEngine.submit_spt(
            db, spt_id,
            payment_date       = req.payment_date,
            ntpn               = req.ntpn,
            submitted_by       = req.submitted_by,
            bank_account_id    = req.bank_account_id,
            gl_account_payable = req.gl_account_payable,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/spt-masa/{spt_id}/detail", summary="Detail SPT Masa + bukti potong")
def spt_detail(spt_id: UUID, db: Session = Depends(get_db)):
    rows = db.execute(
        text("SELECT * FROM vw_spt_masa_detail WHERE spt_id=:id ORDER BY vendor_name"),
        {"id": str(spt_id)},
    ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="SPT Masa tidak ditemukan.")
    return [dict(r._mapping) for r in rows]


# ── Summary / Reports ─────────────────────────────────────────────────────────

@router.get("/summary", summary="Ringkasan WHT per periode")
def summary(
    entity_id:    UUID,
    tax_type:     str   = Query(..., pattern="^(PPh23|PPh4_2)$"),
    period_year:  int   = Query(...),
    period_month: int   = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
):
    try:
        return WithholdingTaxEngine.get_summary(db, entity_id, tax_type, period_year, period_month)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/lookup-rate", summary="Lookup tarif berdasarkan income type")
def lookup_rate(
    tax_type:        str,
    income_type_code: str,
    has_npwp:        bool = True,
    db: Session = Depends(get_db),
):
    try:
        rate = WithholdingTaxEngine._get_rate(db, tax_type, income_type_code, has_npwp)
        return {"tax_type": tax_type, "income_type_code": income_type_code,
                "has_npwp": has_npwp, "rate_pct": float(rate)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
