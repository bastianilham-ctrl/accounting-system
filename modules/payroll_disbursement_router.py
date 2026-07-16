"""
Payroll Disbursement Router
Prefix: /payroll-disbursement
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.database import get_db
from modules.auth import get_current_user
from .payroll_disbursement_engine import PayrollDisbursementEngine

router = APIRouter(prefix="/payroll-disbursement", tags=["Payroll Disbursement"])


# ── Pydantic Models ───────────────────────────────────────────────────────────

class DisbursementCreate(BaseModel):
    entity_id: str
    payroll_run_id: str
    disbursement_date: date
    bank_account_id: str
    gl_salary_payable: str = "2-2000"
    gl_pph21_payable: str = "2-1200"
    gl_bpjs_tk_payable: str = "2-1300"
    gl_bpjs_kes_payable: str = "2-1400"
    gl_salary_expense: str = "6-1000"
    gl_bpjs_employer_expense: str = "6-1100"
    notes: Optional[str] = None


class AccrualPost(BaseModel):
    entity_id: str
    journal_date: date


class DisbursePost(BaseModel):
    entity_id: str
    disbursement_date: Optional[date] = None


class MarkTransferred(BaseModel):
    employee_id: str
    transfer_reference: str


class MarkFailed(BaseModel):
    employee_id: str
    reason: str


class BulkTransfer(BaseModel):
    transfers: List[dict]  # [{employee_id, transfer_reference}]


class CancelRequest(BaseModel):
    reason: str = Field(..., min_length=3)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", status_code=201)
def create_disbursement(
    data: DisbursementCreate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Buat disbursement batch dari payroll_run yang sudah diapprove.
    Otomatis load semua karyawan dan komponen gaji dari payroll_detail.
    """
    try:
        return PayrollDisbursementEngine.create_disbursement(
            db=db,
            entity_id=data.entity_id,
            payroll_run_id=data.payroll_run_id,
            disbursement_date=data.disbursement_date,
            bank_account_id=data.bank_account_id,
            gl_salary_payable=data.gl_salary_payable,
            gl_pph21_payable=data.gl_pph21_payable,
            gl_bpjs_tk_payable=data.gl_bpjs_tk_payable,
            gl_bpjs_kes_payable=data.gl_bpjs_kes_payable,
            gl_salary_expense=data.gl_salary_expense,
            gl_bpjs_employer_expense=data.gl_bpjs_employer_expense,
            notes=data.notes,
            created_by=user.get("username"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("")
def list_disbursements(
    entity_id: str = Query(...),
    fiscal_year: Optional[int] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(24, le=100),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Daftar semua disbursement untuk entity, dengan filter opsional."""
    return PayrollDisbursementEngine.list_disbursements(
        db, entity_id=entity_id, fiscal_year=fiscal_year,
        status=status, page=page, size=size,
    )


@router.get("/{disbursement_id}")
def get_disbursement(
    disbursement_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Detail disbursement beserta semua line karyawan."""
    try:
        return PayrollDisbursementEngine.get_detail(db, disbursement_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/{disbursement_id}/submit")
def submit_disbursement(
    disbursement_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Submit disbursement untuk approval."""
    try:
        return PayrollDisbursementEngine.submit(db, disbursement_id, user.get("username"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{disbursement_id}/approve")
def approve_disbursement(
    disbursement_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Approve disbursement. Setelah approve, siap untuk posting."""
    try:
        return PayrollDisbursementEngine.approve(db, disbursement_id, user.get("username"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{disbursement_id}/post-accrual")
def post_accrual(
    disbursement_id: str,
    data: AccrualPost,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    (Opsional) Posting jurnal akrual beban gaji:
      Dr Beban Gaji + Dr Beban BPJS Employer
        Cr Hutang Gaji + Cr Hutang PPh 21 + Cr Hutang BPJS

    Lewati langkah ini jika payroll_run sudah punya accrual journal sendiri.
    """
    try:
        return PayrollDisbursementEngine.post_accrual(
            db=db,
            disbursement_id=disbursement_id,
            entity_id=data.entity_id,
            journal_date=data.journal_date,
            posted_by=user.get("username"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{disbursement_id}/disburse")
def disburse(
    disbursement_id: str,
    data: DisbursePost,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Eksekusi pembayaran gaji:
      1. Posting jurnal: Dr Hutang Gaji | Cr Bank
      2. Semua karyawan dengan rekening bank → status 'transferred'
      3. Karyawan tanpa rekening bank → status 'skipped'

    Gunakan /mark-transferred untuk konfirmasi per-karyawan dari bank.
    """
    try:
        return PayrollDisbursementEngine.disburse(
            db=db,
            disbursement_id=disbursement_id,
            entity_id=data.entity_id,
            posted_by=user.get("username"),
            disbursement_date=data.disbursement_date,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{disbursement_id}/mark-transferred")
def mark_transferred(
    disbursement_id: str,
    data: MarkTransferred,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Konfirmasi transfer berhasil untuk satu karyawan dari bukti bank."""
    try:
        return PayrollDisbursementEngine.mark_transferred(
            db, disbursement_id, data.employee_id, data.transfer_reference
        )
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/{disbursement_id}/mark-failed")
def mark_failed(
    disbursement_id: str,
    data: MarkFailed,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Mark transfer gagal untuk satu karyawan (misal: rekening salah, ditolak bank)."""
    try:
        return PayrollDisbursementEngine.mark_failed(
            db, disbursement_id, data.employee_id, data.reason
        )
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/{disbursement_id}/bulk-confirm")
def bulk_confirm(
    disbursement_id: str,
    data: BulkTransfer,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Bulk konfirmasi transfer dari file konfirmasi bank.
    Body: {transfers: [{employee_id, transfer_reference}]}
    """
    return PayrollDisbursementEngine.bulk_mark_transferred(
        db, disbursement_id, data.transfers
    )


@router.get("/{disbursement_id}/pending-transfers")
def get_pending_transfers(
    disbursement_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Daftar karyawan yang transfer belum terkonfirmasi atau gagal."""
    return PayrollDisbursementEngine.get_pending_transfers(db, disbursement_id)


@router.get("/{disbursement_id}/export-bank-file")
def export_bank_file(
    disbursement_id: str,
    format: str = Query("standard", pattern="^(standard|bca|mandiri|bni|bri)$"),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Export file transfer untuk bank (CSV).
    Format tersedia: standard, bca, mandiri, bni, bri

    File ini diserahkan ke bank untuk proses bulk transfer gaji.
    """
    try:
        content, filename = PayrollDisbursementEngine.export_bank_file(
            db, disbursement_id, format=format
        )
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/{disbursement_id}/cancel")
def cancel_disbursement(
    disbursement_id: str,
    data: CancelRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Batalkan disbursement (hanya untuk draft/pending/approved).
    Disbursement yang sudah dieksekusi tidak bisa dibatalkan via API.
    """
    try:
        return PayrollDisbursementEngine.cancel(
            db, disbursement_id, user.get("username"), data.reason
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
