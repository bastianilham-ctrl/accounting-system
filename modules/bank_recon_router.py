"""
Bank Reconciliation Router
Base prefix: /bank-recon
"""

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.database import get_db
from modules.bank_recon_engine import BankReconEngine

router = APIRouter(prefix="/bank-recon", tags=["Bank Reconciliation"])


# ── Pydantic Models ──────────────────────────────────────────────────────────

class CreateStatementReq(BaseModel):
    entity_id:              UUID
    bank_account_id:        UUID
    statement_period_year:  int  = Field(..., ge=2000, le=2099)
    statement_period_month: int  = Field(..., ge=1,    le=12)
    statement_date:         date
    opening_balance:        Decimal
    closing_balance:        Decimal
    source:                 str  = Field(default="manual", pattern="^(manual|csv_import|api)$")
    imported_by:            Optional[str] = None


class StatementLineIn(BaseModel):
    transaction_date: date
    value_date:       Optional[date]  = None
    description:      str
    reference_no:     Optional[str]   = None
    debit_amount:     Decimal         = Decimal("0")
    credit_amount:    Decimal         = Decimal("0")
    running_balance:  Optional[Decimal] = None


class ImportLinesReq(BaseModel):
    lines: list[StatementLineIn]


class ManualMatchReq(BaseModel):
    bank_line_id: UUID
    gl_line_id:   UUID
    matched_by:   str


class ConfirmSuggestionReq(BaseModel):
    bank_line_id: UUID
    gl_line_id:   UUID
    confirmed_by: str


class AdjustmentReq(BaseModel):
    adjustment_type:     str   = Field(..., pattern="^(bank_only|gl_only|timing_diff)$")
    source:              str   = Field(..., pattern="^(bank_line|gl_line)$")
    description:         str
    amount:              Decimal
    bank_line_id:        Optional[UUID] = None
    gl_line_id:          Optional[UUID] = None
    debit_account_code:  Optional[str]  = None   # wajib jika bank_only
    credit_account_code: Optional[str]  = None   # wajib jika bank_only
    created_by:          str


class FinalizeReq(BaseModel):
    finalized_by: str


class UnmatchReq(BaseModel):
    bank_line_id: UUID


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/statements", summary="Buat header bank statement baru")
def create_statement(req: CreateStatementReq, db: Session = Depends(get_db)):
    try:
        result = BankReconEngine.create_statement(
            db,
            entity_id              = req.entity_id,
            bank_account_id        = req.bank_account_id,
            statement_period_year  = req.statement_period_year,
            statement_period_month = req.statement_period_month,
            statement_date         = req.statement_date,
            opening_balance        = req.opening_balance,
            closing_balance        = req.closing_balance,
            source                 = req.source,
            imported_by            = req.imported_by,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/statements", summary="List statement per entity")
def list_statements(
    entity_id:      UUID,
    bank_account_id: Optional[UUID] = None,
    year:           Optional[int]   = None,
    status:         Optional[str]   = None,
    db: Session = Depends(get_db),
):
    from sqlalchemy import text
    filters = ["bs.entity_id = :eid"]
    params: dict = {"eid": str(entity_id)}

    if bank_account_id:
        filters.append("bs.bank_account_id = :ba_id")
        params["ba_id"] = str(bank_account_id)
    if year:
        filters.append("bs.statement_period_year = :yr")
        params["yr"] = year
    if status:
        filters.append("bs.status = :status")
        params["status"] = status

    where = " AND ".join(filters)
    rows = db.execute(
        text(f"""
            SELECT bs.id, bs.bank_account_id, ba.account_name, ba.account_number,
                   bs.statement_period_year, bs.statement_period_month,
                   bs.statement_date, bs.opening_balance, bs.closing_balance,
                   bs.status, bs.imported_at
            FROM bank_statement bs
            JOIN bank_account ba ON ba.id = bs.bank_account_id
            WHERE {where}
            ORDER BY bs.statement_period_year DESC, bs.statement_period_month DESC
        """),
        params,
    ).fetchall()

    return [dict(r._mapping) for r in rows]


@router.get("/statements/{statement_id}", summary="Detail statement + summary")
def get_statement(statement_id: UUID, db: Session = Depends(get_db)):
    from sqlalchemy import text
    row = db.execute(
        text("""
            SELECT * FROM vw_recon_summary WHERE statement_id = :sid
        """),
        {"sid": str(statement_id)},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Statement tidak ditemukan.")
    return dict(row._mapping)


@router.post("/statements/{statement_id}/lines", summary="Import baris rekening koran")
def import_lines(
    statement_id: UUID,
    req: ImportLinesReq,
    db: Session = Depends(get_db),
):
    try:
        result = BankReconEngine.import_lines(
            db,
            statement_id = statement_id,
            lines        = [l.model_dump() for l in req.lines],
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/statements/{statement_id}/lines", summary="List baris bank statement")
def list_lines(
    statement_id: UUID,
    match_status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    from sqlalchemy import text
    params: dict = {"sid": str(statement_id)}
    extra = ""
    if match_status:
        extra = "AND bsl.match_status = :ms"
        params["ms"] = match_status

    rows = db.execute(
        text(f"""
            SELECT bsl.id, bsl.line_no, bsl.transaction_date, bsl.value_date,
                   bsl.description, bsl.reference_no,
                   bsl.debit_amount, bsl.credit_amount, bsl.running_balance,
                   bsl.match_status
            FROM bank_statement_line bsl
            WHERE bsl.statement_id = :sid {extra}
            ORDER BY bsl.line_no
        """),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/statements/{statement_id}/auto-match", summary="Jalankan auto-match")
def run_auto_match(statement_id: UUID, db: Session = Depends(get_db)):
    try:
        return BankReconEngine.run_auto_match(db, statement_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/statements/{statement_id}/suggestions/{bank_line_id}",
            summary="Lihat kandidat GL untuk bank line suggested")
def get_suggestions(
    statement_id: UUID,
    bank_line_id: UUID,
    db: Session = Depends(get_db),
):
    try:
        return BankReconEngine.get_suggestions(db, statement_id, bank_line_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/statements/{statement_id}/confirm-suggestion",
             summary="Konfirmasi satu kandidat suggested")
def confirm_suggestion(
    statement_id: UUID,
    req: ConfirmSuggestionReq,
    db: Session = Depends(get_db),
):
    try:
        return BankReconEngine.confirm_suggestion(
            db,
            statement_id = statement_id,
            bank_line_id = req.bank_line_id,
            gl_line_id   = req.gl_line_id,
            confirmed_by = req.confirmed_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/statements/{statement_id}/manual-match",
             summary="Cocokkan manual satu bank line ke satu GL entry")
def manual_match(
    statement_id: UUID,
    req: ManualMatchReq,
    db: Session = Depends(get_db),
):
    try:
        return BankReconEngine.create_manual_match(
            db,
            statement_id = statement_id,
            bank_line_id = req.bank_line_id,
            gl_line_id   = req.gl_line_id,
            matched_by   = req.matched_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/statements/{statement_id}/unmatch",
             summary="Batalkan match bank line")
def unmatch(
    statement_id: UUID,
    req: UnmatchReq,
    db: Session = Depends(get_db),
):
    try:
        return BankReconEngine.unmatch(db, statement_id, req.bank_line_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/statements/{statement_id}/adjustments",
             summary="Catat adjustment item (biaya bank, cek beredar, dll)")
def create_adjustment(
    statement_id: UUID,
    req: AdjustmentReq,
    db: Session = Depends(get_db),
):
    if req.adjustment_type == "bank_only" and not (req.debit_account_code and req.credit_account_code):
        raise HTTPException(
            status_code=422,
            detail="Untuk bank_only adjustment, debit_account_code dan credit_account_code wajib diisi.",
        )
    try:
        return BankReconEngine.create_adjustment(
            db,
            statement_id         = statement_id,
            adjustment_type      = req.adjustment_type,
            source               = req.source,
            description          = req.description,
            amount               = req.amount,
            bank_line_id         = req.bank_line_id,
            gl_line_id           = req.gl_line_id,
            debit_account_code   = req.debit_account_code,
            credit_account_code  = req.credit_account_code,
            created_by           = req.created_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/statements/{statement_id}/adjustments",
            summary="List adjustment items")
def list_adjustments(statement_id: UUID, db: Session = Depends(get_db)):
    from sqlalchemy import text
    rows = db.execute(
        text("""
            SELECT id, adjustment_type, source, bank_line_id, gl_line_id,
                   description, amount, adjustment_journal_id, created_by, created_at
            FROM recon_adjustment
            WHERE statement_id = :sid
            ORDER BY created_at
        """),
        {"sid": str(statement_id)},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/statements/{statement_id}/finalize",
             summary="Finalisasi dan kunci rekonsiliasi")
def finalize(
    statement_id: UUID,
    req: FinalizeReq,
    db: Session = Depends(get_db),
):
    try:
        return BankReconEngine.finalize_reconciliation(
            db,
            statement_id  = statement_id,
            finalized_by  = req.finalized_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/statements/{statement_id}/report",
            summary="Laporan rekonsiliasi bank lengkap")
def get_report(statement_id: UUID, db: Session = Depends(get_db)):
    try:
        return BankReconEngine.get_report(db, statement_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/unmatched-gl", summary="GL entries belum direkonsiliasi")
def unmatched_gl(
    entity_id:      UUID,
    bank_account_id: Optional[UUID] = None,
    date_from:      Optional[date]  = None,
    date_to:        Optional[date]  = None,
    page:           int = Query(default=1, ge=1),
    size:           int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    from sqlalchemy import text
    filters = ["gj.entity_id = :eid", "COALESCE(grs.match_status,'unmatched')='unmatched'"]
    params: dict = {"eid": str(entity_id), "offset": (page - 1) * size, "limit": size}

    if bank_account_id:
        filters.append("coa.account_code = (SELECT gl_account_code FROM bank_account WHERE id=:ba_id)")
        params["ba_id"] = str(bank_account_id)
    if date_from:
        filters.append("gj.journal_date >= :d_from")
        params["d_from"] = date_from
    if date_to:
        filters.append("gj.journal_date <= :d_to")
        params["d_to"] = date_to

    where = " AND ".join(filters)
    rows = db.execute(
        text(f"""
            SELECT gl.id AS gl_line_id, gj.journal_date, gj.description,
                   gj.reference_no, coa.account_code, coa.account_name,
                   gl.debit_idr, gl.credit_idr
            FROM gl_line gl
            JOIN gl_journal gj         ON gj.id = gl.journal_id AND gj.status='posted'
            JOIN chart_of_accounts coa ON coa.id = gl.account_id
                                      AND coa.account_type='asset'
                                      AND coa.account_name ILIKE '%bank%'
            LEFT JOIN gl_recon_status grs ON grs.gl_line_id = gl.id
            WHERE {where}
            ORDER BY gj.journal_date DESC
            OFFSET :offset LIMIT :limit
        """),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]
