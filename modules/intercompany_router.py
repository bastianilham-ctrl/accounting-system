"""
Intercompany Transaction Router
Prefix: /intercompany
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session

from core.database import get_db
from modules.auth import get_current_user
from .intercompany_engine import IntercompanyEngine, _DEFAULTS

router = APIRouter(prefix="/intercompany", tags=["Intercompany"])


# ── Pydantic Models ───────────────────────────────────────────────────────────

class IntercompanyCreate(BaseModel):
    initiator_entity_id: str
    counterparty_entity_id: str
    transaction_type: str
    transaction_date: date
    description: str = Field(..., min_length=1)
    amount: Decimal = Field(..., gt=0)
    currency: str = Field("IDR")
    exchange_rate: Decimal = Field(Decimal("1"), gt=0)
    initiator_debit_account: Optional[str] = None
    initiator_credit_account: Optional[str] = None
    counterparty_debit_account: Optional[str] = None
    counterparty_credit_account: Optional[str] = None
    reference_number: Optional[str] = None
    tags: Optional[List[str]] = None
    notes: Optional[str] = None

    @validator("currency")
    def upper(cls, v):
        return v.upper()

    @validator("transaction_type")
    def valid_type(cls, v):
        if v not in _DEFAULTS:
            raise ValueError(f"Tipe tidak valid. Pilihan: {list(_DEFAULTS.keys())}")
        return v


class IntercompanySettle(BaseModel):
    settlement_date: date
    amount_idr: Decimal = Field(..., gt=0)
    payment_method: str = Field("bank_transfer")
    initiator_bank_account_id: Optional[str] = None
    counterparty_bank_account_id: Optional[str] = None
    bank_reference: Optional[str] = None
    currency: str = "IDR"
    amount_fcy: Optional[Decimal] = None
    exchange_rate: Optional[Decimal] = None
    notes: Optional[str] = None


class IntercompanyCancel(BaseModel):
    reason: str = Field(..., min_length=3)


class IntercompanyConfig(BaseModel):
    entity_id: str
    counterparty_entity_id: str
    due_from_account: str = "1-9100"
    due_to_account: str = "2-9100"
    default_charge_income_account: Optional[str] = None
    default_charge_expense_account: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/transaction-types")
def get_transaction_types():
    """Daftar tipe transaksi intercompany dan akun GL default-nya."""
    return [
        {"type": k, "accounts": v}
        for k, v in _DEFAULTS.items()
    ]


@router.post("/transactions", status_code=201)
def create_transaction(
    data: IntercompanyCreate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Buat transaksi intercompany baru (status: draft)."""
    try:
        return IntercompanyEngine.create(
            db=db,
            initiator_entity_id=data.initiator_entity_id,
            counterparty_entity_id=data.counterparty_entity_id,
            transaction_type=data.transaction_type,
            transaction_date=data.transaction_date,
            description=data.description,
            amount=data.amount,
            currency=data.currency,
            exchange_rate=data.exchange_rate,
            initiator_debit_account=data.initiator_debit_account,
            initiator_credit_account=data.initiator_credit_account,
            counterparty_debit_account=data.counterparty_debit_account,
            counterparty_credit_account=data.counterparty_credit_account,
            reference_number=data.reference_number,
            tags=data.tags,
            notes=data.notes,
            created_by=user.get("username"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/transactions")
def list_transactions(
    entity_id: str = Query(...),
    side: str = Query("both", pattern="^(both|initiator|counterparty)$"),
    status: Optional[str] = None,
    transaction_type: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, le=200),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Daftar transaksi intercompany untuk entity tertentu."""
    return IntercompanyEngine.list_transactions(
        db, entity_id=entity_id, side=side,
        status=status, transaction_type=transaction_type,
        date_from=date_from, date_to=date_to, page=page, size=size,
    )


@router.get("/transactions/{transaction_id}")
def get_transaction(
    transaction_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Detail transaksi beserta riwayat settlement."""
    try:
        return IntercompanyEngine.get_detail(db, transaction_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/transactions/{transaction_id}/submit")
def submit_transaction(
    transaction_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Initiator submit transaksi untuk approval counterparty."""
    try:
        return IntercompanyEngine.submit(db, transaction_id, user.get("username"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/transactions/{transaction_id}/confirm")
def confirm_transaction(
    transaction_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Counterparty konfirmasi (approve) transaksi."""
    try:
        return IntercompanyEngine.confirm(db, transaction_id, user.get("username"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/transactions/{transaction_id}/post")
def post_transaction(
    transaction_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Posting jurnal ke GL di KEDUA entity sekaligus.
    Membutuhkan akun interco (1-9100 / 2-9100) di CoA kedua entity.
    """
    try:
        return IntercompanyEngine.post(db, transaction_id, user.get("username"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/transactions/{transaction_id}/cancel")
def cancel_transaction(
    transaction_id: str,
    data: IntercompanyCancel,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Batalkan transaksi (hanya untuk draft/pending/approved)."""
    try:
        return IntercompanyEngine.cancel(db, transaction_id, user.get("username"), data.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/transactions/{transaction_id}/reverse")
def reverse_transaction(
    transaction_id: str,
    data: IntercompanyCancel,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Balik jurnal transaksi yang sudah diposting.
    Membuat jurnal reversal di kedua entity.
    """
    try:
        return IntercompanyEngine.reverse(db, transaction_id, user.get("username"), data.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/transactions/{transaction_id}/settle")
def settle_transaction(
    transaction_id: str,
    data: IntercompanySettle,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Catat pembayaran/pelunasan hutang intercompany.
    Posting jurnal di kedua entity: Dr Bank | Cr Due From (initiator)
    dan Dr Due To | Cr Bank (counterparty).
    """
    try:
        return IntercompanyEngine.settle(
            db=db,
            transaction_id=transaction_id,
            settlement_date=data.settlement_date,
            amount_idr=data.amount_idr,
            payment_method=data.payment_method,
            initiator_bank_account_id=data.initiator_bank_account_id,
            counterparty_bank_account_id=data.counterparty_bank_account_id,
            bank_reference=data.bank_reference,
            created_by=user.get("username"),
            currency=data.currency,
            amount_fcy=data.amount_fcy,
            exchange_rate=data.exchange_rate,
            notes=data.notes,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Reports ───────────────────────────────────────────────────────────────────

@router.get("/outstanding/{entity_id}")
def get_outstanding(
    entity_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Ringkasan piutang dan hutang intercompany untuk entity ini.
    """
    return IntercompanyEngine.get_outstanding_balances(db, entity_id)


@router.get("/aging/{entity_id}")
def get_aging(
    entity_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Aging piutang/hutang intercompany per bucket."""
    return IntercompanyEngine.get_aging_report(db, entity_id)


@router.get("/elimination")
def get_elimination_schedule(
    entity_ids: str = Query(..., description="Koma-separated entity IDs untuk grup konsolidasi"),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Jadwal eliminasi interco untuk konsolidasi.
    Pasangkan Due From di A dengan Due To di B.
    """
    ids = [eid.strip() for eid in entity_ids.split(",")]
    return IntercompanyEngine.get_elimination_schedule(db, ids)


# ── Config ────────────────────────────────────────────────────────────────────

@router.post("/config")
def set_config(
    data: IntercompanyConfig,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Konfigurasi akun interco default per pasangan entity.
    Berguna jika Due From / Due To menggunakan akun berbeda dari default.
    """
    try:
        return IntercompanyEngine.set_config(
            db=db,
            entity_id=data.entity_id,
            counterparty_entity_id=data.counterparty_entity_id,
            due_from_account=data.due_from_account,
            due_to_account=data.due_to_account,
            default_charge_income_account=data.default_charge_income_account,
            default_charge_expense_account=data.default_charge_expense_account,
        )
    except Exception as e:
        raise HTTPException(400, str(e))
