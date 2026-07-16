"""
Cash & Bank Router — Kas/Bank Transactions (non-AP/AR), Petty Cash (Imprest), In-house Transfer
Base prefix: /cash-bank

Catatan: bank account master, import rekening koran, dan rekonsiliasi formal
sudah ada di /bank (bank_router.py) dan /bank-recon (bank_recon_router.py).
Modul ini menambahkan transaksi kas/bank non-AP/AR (mis. angsuran bank, bunga,
bank charge, WHT bunga di sisi bank — bukan hanya kas tunai), kas kecil, dan
transfer antar rekening/kas internal — bisa berdiri sendiri tanpa modul AR/AP.
"""

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.database import get_db
from modules.cash_bank_engine import CashBankEngine

router = APIRouter(prefix="/cash-bank", tags=["Cash & Bank (Kas Tunai, Petty Cash, Transfer)"])


# ── Pydantic Models ──────────────────────────────────────────────────────────

class CreateCashAccountReq(BaseModel):
    entity_id:      UUID
    account_name:   str
    coa_code:       str
    account_type:   str = "cash"   # 'cash' | 'petty_cash'
    custodian_name: Optional[str] = None
    float_amount:   Decimal = Decimal("0")


class CashTxnLineIn(BaseModel):
    account_code: str
    description:  Optional[str] = None
    amount:       Decimal
    cost_center:  Optional[str] = None
    project_id:   Optional[UUID] = None


class CreateCashTxnReq(BaseModel):
    entity_id:        UUID
    account_type:     str   # 'bank' | 'cash'
    account_id:       UUID
    transaction_date: date
    direction:        str   # 'in' | 'out'
    description:      str
    lines:            list[CashTxnLineIn]
    currency:         str = "IDR"
    created_by:       str = "system"


class PostReq(BaseModel):
    posted_by: str = "system"


class CreatePettyCashExpenseReq(BaseModel):
    entity_id:       UUID
    cash_account_id: UUID
    expense_date:    date
    account_code:    str
    amount:          Decimal
    description:     Optional[str] = None
    cost_center:     Optional[str] = None
    project_id:      Optional[UUID] = None
    receipt_ref:     Optional[str] = None
    currency:        str = "IDR"
    created_by:      str = "system"


class CreateInHouseTransferReq(BaseModel):
    entity_id:     UUID
    transfer_date: date
    source_type:   str   # 'bank' | 'cash'
    source_id:     UUID
    dest_type:     str   # 'bank' | 'cash'
    dest_id:       UUID
    amount:        Decimal
    purpose:       str = "transfer"   # 'transfer' | 'petty_cash_topup'
    description:   Optional[str] = None
    currency:      str = "IDR"
    created_by:    str = "system"


class PostInHouseTransferReq(BaseModel):
    posted_by:             str = "system"
    replenish_expense_ids: Optional[list[UUID]] = None


# ── Cash Account Master ──────────────────────────────────────────────────────

@router.post("/cash-accounts", summary="Daftarkan kas tunai / kas kecil baru")
def create_cash_account(req: CreateCashAccountReq, db: Session = Depends(get_db)):
    try:
        return CashBankEngine.create_cash_account(
            db, entity_id=req.entity_id, account_name=req.account_name,
            coa_code=req.coa_code, account_type=req.account_type,
            custodian_name=req.custodian_name, float_amount=req.float_amount,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/cash-accounts", summary="List kas tunai / kas kecil")
def list_cash_accounts(entity_id: UUID, account_type: Optional[str] = None, db: Session = Depends(get_db)):
    return CashBankEngine.list_cash_accounts(db, entity_id=entity_id, account_type=account_type)


@router.get("/cash-accounts/{cash_account_id}/balance", summary="Saldo kas berdasarkan GL")
def get_cash_account_balance(cash_account_id: UUID, db: Session = Depends(get_db)):
    try:
        return CashBankEngine.get_cash_account_balance(db, cash_account_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Cash Transaction ─────────────────────────────────────────────────────────

@router.post("/transactions", summary="Buat transaksi kas/bank (draft)")
def create_cash_transaction(req: CreateCashTxnReq, db: Session = Depends(get_db)):
    try:
        return CashBankEngine.create_cash_transaction(
            db, entity_id=req.entity_id, account_type=req.account_type, account_id=req.account_id,
            transaction_date=req.transaction_date, direction=req.direction,
            description=req.description, lines=[l.model_dump() for l in req.lines],
            currency=req.currency, created_by=req.created_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/transactions", summary="List transaksi kas/bank")
def list_cash_transactions(
    entity_id: UUID,
    account_type: Optional[str] = None,
    account_id: Optional[UUID] = None,
    status: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
):
    return CashBankEngine.list_cash_transactions(
        db, entity_id=entity_id, account_type=account_type, account_id=account_id,
        status=status, date_from=date_from, date_to=date_to,
    )


@router.get("/transactions/{transaction_id}", summary="Detail transaksi kas + baris")
def get_cash_transaction(transaction_id: UUID, db: Session = Depends(get_db)):
    try:
        return CashBankEngine.get_cash_transaction(db, transaction_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/transactions/{transaction_id}/post", summary="Posting jurnal transaksi kas")
def post_cash_transaction(transaction_id: UUID, req: PostReq, db: Session = Depends(get_db)):
    try:
        return CashBankEngine.post_cash_transaction(db, transaction_id, posted_by=req.posted_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Petty Cash Expense ───────────────────────────────────────────────────────

@router.post("/petty-cash/expenses", summary="Catat pengeluaran kas kecil (draft)")
def create_petty_cash_expense(req: CreatePettyCashExpenseReq, db: Session = Depends(get_db)):
    try:
        return CashBankEngine.create_petty_cash_expense(
            db, entity_id=req.entity_id, cash_account_id=req.cash_account_id,
            expense_date=req.expense_date, account_code=req.account_code,
            amount=req.amount, description=req.description, cost_center=req.cost_center,
            project_id=req.project_id, receipt_ref=req.receipt_ref,
            currency=req.currency, created_by=req.created_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/petty-cash/expenses", summary="List pengeluaran kas kecil")
def list_petty_cash_expenses(
    entity_id: UUID,
    cash_account_id: Optional[UUID] = None,
    status: Optional[str] = None,
    replenished: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    return CashBankEngine.list_petty_cash_expenses(
        db, entity_id=entity_id, cash_account_id=cash_account_id,
        status=status, replenished=replenished,
    )


@router.post("/petty-cash/expenses/{expense_id}/post", summary="Posting jurnal pengeluaran kas kecil")
def post_petty_cash_expense(expense_id: UUID, req: PostReq, db: Session = Depends(get_db)):
    try:
        return CashBankEngine.post_petty_cash_expense(db, expense_id, posted_by=req.posted_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/petty-cash/cash-accounts/{cash_account_id}/outstanding", summary="Total kas kecil belum direplenish (dasar top-up)")
def get_outstanding_petty_cash(cash_account_id: UUID, db: Session = Depends(get_db)):
    return CashBankEngine.get_outstanding_petty_cash(db, cash_account_id)


# ── In-house Transfer ────────────────────────────────────────────────────────

@router.post("/transfers", summary="Buat transfer antar rekening/kas internal (draft)")
def create_in_house_transfer(req: CreateInHouseTransferReq, db: Session = Depends(get_db)):
    try:
        return CashBankEngine.create_in_house_transfer(
            db, entity_id=req.entity_id, transfer_date=req.transfer_date,
            source_type=req.source_type, source_id=req.source_id,
            dest_type=req.dest_type, dest_id=req.dest_id, amount=req.amount,
            purpose=req.purpose, description=req.description,
            currency=req.currency, created_by=req.created_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/transfers", summary="List transfer antar rekening/kas internal")
def list_in_house_transfers(
    entity_id: UUID,
    source_type: Optional[str] = None,
    dest_type: Optional[str] = None,
    status: Optional[str] = None,
    purpose: Optional[str] = None,
    db: Session = Depends(get_db),
):
    return CashBankEngine.list_in_house_transfers(
        db, entity_id=entity_id, source_type=source_type, dest_type=dest_type,
        status=status, purpose=purpose,
    )


@router.post("/transfers/{transfer_id}/post", summary="Posting jurnal transfer (Dr Tujuan | Cr Asal)")
def post_in_house_transfer(transfer_id: UUID, req: PostInHouseTransferReq, db: Session = Depends(get_db)):
    try:
        return CashBankEngine.post_in_house_transfer(
            db, transfer_id, posted_by=req.posted_by,
            replenish_expense_ids=req.replenish_expense_ids,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
