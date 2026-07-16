"""
Opening Balance Router
Base prefix: /opening-balance

Workflow endpoint:
  1. POST /opening-balance/session            — buat atau cek session
  2. PUT  /opening-balance/{id}/gl            — input GL Trial Balance
  3. PUT  /opening-balance/{id}/ar            — input AR outstanding
  4. PUT  /opening-balance/{id}/ap            — input AP outstanding
  5. PUT  /opening-balance/{id}/assets        — input Fixed Asset Register
  6. PUT  /opening-balance/{id}/inventory     — input stok awal
  7. PUT  /opening-balance/{id}/banks         — input saldo bank
  8. PUT  /opening-balance/{id}/leave         — input saldo cuti
  9. POST /opening-balance/{id}/validate      — validasi & cross-check
  10. POST /opening-balance/{id}/finalize     — finalisasi (tidak bisa diundur)
"""

from datetime import date
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.database import get_db
from modules.opening_balance_engine import OpeningBalanceEngine

router = APIRouter(prefix="/opening-balance", tags=["Opening Balance"])


# ── Pydantic Models ──────────────────────────────────────────────────────────

class CreateSessionReq(BaseModel):
    entity_id:    UUID
    opening_date: date
    fiscal_year:  int
    is_mid_year:  bool = False
    notes:        Optional[str] = None
    created_by:   str = ""


class GLItem(BaseModel):
    account_code:   str
    account_name:   Optional[str] = None
    debit_balance:  float = 0.0
    credit_balance: float = 0.0
    notes:          Optional[str] = None


class SaveGLReq(BaseModel):
    balances:    list[GLItem] = Field(..., min_length=1)
    replace_all: bool = True


class ARItem(BaseModel):
    customer_name:    str
    customer_id:      Optional[UUID] = None
    invoice_number:   str
    invoice_date:     date
    due_date:         Optional[date] = None
    original_amount:  float
    amount_remaining: Optional[float] = None
    currency:         str = "IDR"
    exchange_rate:    float = 1.0
    description:      Optional[str] = None


class SaveARReq(BaseModel):
    items:       list[ARItem] = Field(..., min_length=1)
    replace_all: bool = True


class APItem(BaseModel):
    vendor_name:      str
    vendor_id:        Optional[UUID] = None
    invoice_number:   str
    invoice_date:     date
    due_date:         Optional[date] = None
    original_amount:  float
    amount_remaining: Optional[float] = None
    currency:         str = "IDR"
    exchange_rate:    float = 1.0
    description:      Optional[str] = None


class SaveAPReq(BaseModel):
    items:       list[APItem] = Field(..., min_length=1)
    replace_all: bool = True


class AssetItem(BaseModel):
    asset_name:               str
    asset_code:               Optional[str] = None
    category:                 Optional[str] = None
    location:                 Optional[str] = None
    acquisition_date:         date
    acquisition_cost:         float
    accumulated_depreciation: float = 0.0
    useful_life_months:       int = 60
    depreciation_method:      str = "straight_line"
    salvage_value:            float = 0.0
    gl_asset_account:         Optional[str] = None
    gl_depr_account:          Optional[str] = None
    gl_expense_account:       Optional[str] = None
    serial_number:            Optional[str] = None
    notes:                    Optional[str] = None


class SaveAssetReq(BaseModel):
    assets:      list[AssetItem] = Field(..., min_length=1)
    replace_all: bool = True


class InventoryItem(BaseModel):
    product_code:     str
    product_name:     Optional[str] = None
    product_id:       Optional[UUID] = None
    warehouse_code:   Optional[str] = "DEFAULT"
    warehouse_id:     Optional[UUID] = None
    quantity:         float
    unit_cost:        float
    unit_of_measure:  Optional[str] = None
    notes:            Optional[str] = None


class SaveInventoryReq(BaseModel):
    items:       list[InventoryItem] = Field(..., min_length=1)
    replace_all: bool = True


class BankItem(BaseModel):
    bank_name:        str
    account_number:   Optional[str] = None
    account_holder:   Optional[str] = None
    bank_account_id:  Optional[UUID] = None
    currency:         str = "IDR"
    opening_balance:  float
    gl_account_code:  Optional[str] = None
    notes:            Optional[str] = None


class SaveBankReq(BaseModel):
    banks:       list[BankItem] = Field(..., min_length=1)
    replace_all: bool = True


class LeaveItem(BaseModel):
    employee_code:   str
    employee_id:     Optional[UUID] = None
    employee_name:   Optional[str] = None
    leave_type_code: str
    fiscal_year:     int
    entitled_days:   float = 0.0
    used_days:       float = 0.0
    carry_forward:   float = 0.0
    notes:           Optional[str] = None


class SaveLeaveReq(BaseModel):
    leaves:      list[LeaveItem] = Field(..., min_length=1)
    replace_all: bool = True


class FinalizeReq(BaseModel):
    finalized_by: str


# ── Session ───────────────────────────────────────────────────────────────────

@router.post("/session", summary="Buat atau cek session opening balance")
def create_session(req: CreateSessionReq, db: Session = Depends(get_db)):
    try:
        return OpeningBalanceEngine.create_session(
            db,
            entity_id    = req.entity_id,
            opening_date = req.opening_date,
            fiscal_year  = req.fiscal_year,
            is_mid_year  = req.is_mid_year,
            notes        = req.notes,
            created_by   = req.created_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/status", summary="Status & progress opening balance")
def get_status(entity_id: UUID, db: Session = Depends(get_db)):
    return OpeningBalanceEngine.get_status(db, entity_id)


@router.get("/session/{session_id}", summary="Detail session + semua subsidiary counts")
def get_session(session_id: UUID, db: Session = Depends(get_db)):
    from sqlalchemy import text
    row = db.execute(
        text("SELECT * FROM vw_opening_balance_status WHERE session_id=:sid"),
        {"sid": str(session_id)},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session tidak ditemukan.")
    return dict(row._mapping)


# ── GL Trial Balance ───────────────────────────────────────────────────────────

@router.put("/{session_id}/gl", summary="Input / update GL Trial Balance")
def save_gl(session_id: UUID, req: SaveGLReq, db: Session = Depends(get_db)):
    """
    Input semua saldo akun per tanggal opening.
    Setiap akun masukkan SALAH SATU: debit_balance ATAU credit_balance (bukan keduanya).

    Contoh:
      - Kas: debit_balance=50000000, credit_balance=0
      - Modal: debit_balance=0, credit_balance=200000000
    """
    try:
        return OpeningBalanceEngine.save_gl_balances(
            db, session_id,
            [b.model_dump() for b in req.balances],
            req.replace_all,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{session_id}/gl", summary="Lihat GL Trial Balance yang sudah diinput")
def get_gl(session_id: UUID, db: Session = Depends(get_db)):
    from sqlalchemy import text
    rows = db.execute(
        text("""
            SELECT account_code, account_name, debit_balance, credit_balance,
                   debit_balance - credit_balance AS net_balance, notes
            FROM opening_balance_gl
            WHERE session_id=:sid
            ORDER BY account_code
        """),
        {"sid": str(session_id)},
    ).fetchall()

    totals = db.execute(
        text("SELECT SUM(debit_balance) AS dr, SUM(credit_balance) AS cr FROM opening_balance_gl WHERE session_id=:sid"),
        {"sid": str(session_id)},
    ).fetchone()

    return {
        "items":        [dict(r._mapping) for r in rows],
        "total_debit":  float(totals.dr or 0),
        "total_credit": float(totals.cr or 0),
        "is_balanced":  abs((totals.dr or 0) - (totals.cr or 0)) <= 1,
    }


# ── AR ────────────────────────────────────────────────────────────────────────

@router.put("/{session_id}/ar", summary="Input AR outstanding (piutang yang belum dibayar)")
def save_ar(session_id: UUID, req: SaveARReq, db: Session = Depends(get_db)):
    try:
        return OpeningBalanceEngine.save_ar_items(
            db, session_id, [i.model_dump() for i in req.items], req.replace_all
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{session_id}/ar", summary="Lihat AR items yang sudah diinput")
def get_ar(session_id: UUID, db: Session = Depends(get_db)):
    from sqlalchemy import text
    rows = db.execute(
        text("""
            SELECT customer_name, invoice_number, invoice_date, due_date,
                   original_amount, amount_remaining, currency, description
            FROM opening_balance_ar WHERE session_id=:sid ORDER BY invoice_date
        """),
        {"sid": str(session_id)},
    ).fetchall()
    total = sum(float(r.amount_remaining) for r in rows)
    return {"items": [dict(r._mapping) for r in rows], "total_remaining": total}


# ── AP ────────────────────────────────────────────────────────────────────────

@router.put("/{session_id}/ap", summary="Input AP outstanding (hutang yang belum dibayar)")
def save_ap(session_id: UUID, req: SaveAPReq, db: Session = Depends(get_db)):
    try:
        return OpeningBalanceEngine.save_ap_items(
            db, session_id, [i.model_dump() for i in req.items], req.replace_all
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{session_id}/ap", summary="Lihat AP items yang sudah diinput")
def get_ap(session_id: UUID, db: Session = Depends(get_db)):
    from sqlalchemy import text
    rows = db.execute(
        text("""
            SELECT vendor_name, invoice_number, invoice_date, due_date,
                   original_amount, amount_remaining, currency, description
            FROM opening_balance_ap WHERE session_id=:sid ORDER BY invoice_date
        """),
        {"sid": str(session_id)},
    ).fetchall()
    total = sum(float(r.amount_remaining) for r in rows)
    return {"items": [dict(r._mapping) for r in rows], "total_remaining": total}


# ── Fixed Assets ──────────────────────────────────────────────────────────────

@router.put("/{session_id}/assets", summary="Input daftar aset tetap (Fixed Asset Register)")
def save_assets(session_id: UUID, req: SaveAssetReq, db: Session = Depends(get_db)):
    try:
        return OpeningBalanceEngine.save_asset_register(
            db, session_id, [a.model_dump() for a in req.assets], req.replace_all
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{session_id}/assets", summary="Lihat Fixed Asset Register yang sudah diinput")
def get_assets(session_id: UUID, db: Session = Depends(get_db)):
    from sqlalchemy import text
    rows = db.execute(
        text("""
            SELECT asset_code, asset_name, category, acquisition_date,
                   acquisition_cost, accumulated_depreciation, net_book_value,
                   useful_life_months, depreciation_method
            FROM opening_balance_asset WHERE session_id=:sid ORDER BY asset_name
        """),
        {"sid": str(session_id)},
    ).fetchall()
    total_nbv = sum(float(r.net_book_value or 0) for r in rows)
    return {"items": [dict(r._mapping) for r in rows], "total_nbv": total_nbv}


# ── Inventory ─────────────────────────────────────────────────────────────────

@router.put("/{session_id}/inventory", summary="Input stok awal per produk per gudang")
def save_inventory(session_id: UUID, req: SaveInventoryReq, db: Session = Depends(get_db)):
    try:
        return OpeningBalanceEngine.save_inventory(
            db, session_id, [i.model_dump() for i in req.items], req.replace_all
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{session_id}/inventory", summary="Lihat stok awal yang sudah diinput")
def get_inventory(session_id: UUID, db: Session = Depends(get_db)):
    from sqlalchemy import text
    rows = db.execute(
        text("""
            SELECT product_code, product_name, warehouse_code,
                   quantity, unit_cost, total_value, unit_of_measure
            FROM opening_balance_inventory WHERE session_id=:sid ORDER BY product_code
        """),
        {"sid": str(session_id)},
    ).fetchall()
    total_val = sum(float(r.total_value or 0) for r in rows)
    return {"items": [dict(r._mapping) for r in rows], "total_value": total_val}


# ── Bank ──────────────────────────────────────────────────────────────────────

@router.put("/{session_id}/banks", summary="Input saldo awal per rekening bank")
def save_banks(session_id: UUID, req: SaveBankReq, db: Session = Depends(get_db)):
    try:
        return OpeningBalanceEngine.save_bank_balances(
            db, session_id, [b.model_dump() for b in req.banks], req.replace_all
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{session_id}/banks", summary="Lihat saldo bank yang sudah diinput")
def get_banks(session_id: UUID, db: Session = Depends(get_db)):
    from sqlalchemy import text
    rows = db.execute(
        text("""
            SELECT bank_name, account_number, account_holder,
                   currency, opening_balance, gl_account_code
            FROM opening_balance_bank WHERE session_id=:sid ORDER BY bank_name
        """),
        {"sid": str(session_id)},
    ).fetchall()
    total = sum(float(r.opening_balance) for r in rows)
    return {"items": [dict(r._mapping) for r in rows], "total_balance": total}


# ── Leave ─────────────────────────────────────────────────────────────────────

@router.put("/{session_id}/leave", summary="Input saldo cuti karyawan")
def save_leave(session_id: UUID, req: SaveLeaveReq, db: Session = Depends(get_db)):
    try:
        return OpeningBalanceEngine.save_leave_balances(
            db, session_id, [lv.model_dump() for lv in req.leaves], req.replace_all
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{session_id}/leave", summary="Lihat saldo cuti yang sudah diinput")
def get_leave(session_id: UUID, db: Session = Depends(get_db)):
    from sqlalchemy import text
    rows = db.execute(
        text("""
            SELECT employee_code, employee_name, leave_type_code, fiscal_year,
                   entitled_days, carry_forward, used_days, balance_days
            FROM opening_balance_leave WHERE session_id=:sid
            ORDER BY employee_code, leave_type_code
        """),
        {"sid": str(session_id)},
    ).fetchall()
    return {"items": [dict(r._mapping) for r in rows]}


# ── Validate ──────────────────────────────────────────────────────────────────

@router.post("/{session_id}/validate", summary="Validasi: balance check + cross-check subsidiary")
def validate(session_id: UUID, db: Session = Depends(get_db)):
    """
    Jalankan validasi sebelum finalisasi:
    - GL harus balance (Σ debit = Σ credit)
    - AR subsidiary ≈ GL Piutang Usaha
    - AP subsidiary ≈ GL Hutang Usaha
    - Inventory subsidiary ≈ GL Persediaan
    - Fixed Asset NBV ≈ GL Aset Tetap Neto

    Hasil: {is_valid, can_finalize, checks, warnings, errors}
    """
    try:
        return OpeningBalanceEngine.validate_session(db, session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Finalize ──────────────────────────────────────────────────────────────────

@router.post("/{session_id}/finalize", summary="FINALISASI — tidak bisa dibatalkan")
def finalize(session_id: UUID, req: FinalizeReq, db: Session = Depends(get_db)):
    """
    **Finalisasi Opening Balance — TIDAK BISA DIBATALKAN.**

    Yang terjadi setelah finalisasi:
    1. GL Opening Journal terposting (type='opening')
    2. AR Invoice dibuat untuk setiap piutang outstanding
    3. AP Invoice dibuat untuk setiap hutang outstanding
    4. Fixed Asset Register terisi ke modul aset
    5. Stok awal masuk sebagai stock_move type='opening'
    6. Bank account opening_balance diupdate
    7. Leave entitlement karyawan disiapkan
    8. Fiscal year & period untuk tahun opening dibuat
    9. Session dikunci (status=finalized)
    """
    try:
        return OpeningBalanceEngine.finalize(db, session_id, req.finalized_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
