"""
Multi-Currency Router
=====================
Endpoint untuk:
  /currencies    - master mata uang
  /exchange-rates - input dan query kurs
  /revaluation   - revaluasi FCY periodik
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
from .exchange_rate_engine import ExchangeRateEngine
from .revaluation_engine import RevaluationEngine

router = APIRouter(prefix="/multicurrency", tags=["Multi-Currency"])


# ── Pydantic Models ───────────────────────────────────────────────────────────

class CurrencyCreate(BaseModel):
    currency_code: str = Field(..., min_length=2, max_length=5)
    currency_name: str = Field(..., min_length=1, max_length=100)
    symbol: Optional[str] = None
    decimal_places: int = Field(2, ge=0, le=4)


class ExchangeRateSet(BaseModel):
    from_currency: str = Field(..., min_length=2, max_length=5)
    to_currency: str = Field("IDR", min_length=2, max_length=5)
    rate_date: date
    rate: Decimal = Field(..., gt=0)
    rate_type: str = Field("middle", pattern="^(middle|buying|selling)$")
    source: str = Field("manual", max_length=50)
    notes: Optional[str] = None

    @validator("from_currency", "to_currency")
    def upper(cls, v):
        return v.upper()


class ExchangeRateBatch(BaseModel):
    rates: List[ExchangeRateSet]


class BiRateImport(BaseModel):
    rates: List[dict]   # [{from_currency, rate_date, rate, rate_type?, source?}]


class RevaluationCreate(BaseModel):
    entity_id: str
    revaluation_date: date
    auto_reverse: bool = False
    gl_gain_account: str = Field("7-1000")
    gl_loss_account: str = Field("8-1000")
    rate_type: str = Field("middle", pattern="^(middle|buying|selling)$")
    notes: Optional[str] = None


class RevaluationPost(BaseModel):
    entity_id: str


class RevaluationReverse(BaseModel):
    entity_id: str
    reason: Optional[str] = None


class ConvertRequest(BaseModel):
    amount: Decimal = Field(..., gt=0)
    from_currency: str
    to_currency: str = "IDR"
    rate_date: Optional[date] = None
    rate_type: str = "middle"

    @validator("from_currency", "to_currency")
    def upper(cls, v):
        return v.upper()


# ── 1. Mata Uang (Currency Master) ───────────────────────────────────────────

@router.get("/currencies")
def list_currencies(
    active_only: bool = True,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Daftar semua mata uang yang terdaftar."""
    return ExchangeRateEngine.list_currencies(db, active_only=active_only)


@router.post("/currencies", status_code=201)
def add_currency(
    data: CurrencyCreate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Tambah atau update mata uang baru."""
    try:
        return ExchangeRateEngine.add_currency(
            db,
            currency_code=data.currency_code.upper(),
            currency_name=data.currency_name,
            symbol=data.symbol,
            decimal_places=data.decimal_places,
        )
    except Exception as e:
        raise HTTPException(400, str(e))


@router.delete("/currencies/{currency_code}")
def deactivate_currency(
    currency_code: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Nonaktifkan mata uang (tidak bisa untuk IDR)."""
    try:
        ok = ExchangeRateEngine.deactivate_currency(db, currency_code.upper())
        if not ok:
            raise HTTPException(404, "Mata uang tidak ditemukan.")
        return {"success": True}
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── 2. Kurs (Exchange Rate) ───────────────────────────────────────────────────

@router.get("/exchange-rates/latest")
def get_latest_rates(
    currencies: Optional[str] = Query(None, description="Koma-separated, e.g. USD,SGD,EUR"),
    rate_type: str = "middle",
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Kurs terbaru semua atau mata uang tertentu."""
    currency_list = [c.strip().upper() for c in currencies.split(",")] if currencies else None
    return ExchangeRateEngine.get_latest_rates(db, currency_list=currency_list, rate_type=rate_type)


@router.get("/exchange-rates/{from_currency}")
def get_rate_history(
    from_currency: str,
    to_currency: str = "IDR",
    rate_type: str = "middle",
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = Query(90, le=365),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Histori kurs mata uang tertentu."""
    return ExchangeRateEngine.get_rate_history(
        db,
        from_currency=from_currency.upper(),
        to_currency=to_currency.upper(),
        rate_type=rate_type,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )


@router.post("/exchange-rates")
def set_rate(
    data: ExchangeRateSet,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Simpan atau update kurs untuk tanggal tertentu."""
    try:
        return ExchangeRateEngine.set_rate(
            db,
            from_currency=data.from_currency,
            to_currency=data.to_currency,
            rate_date=data.rate_date,
            rate=data.rate,
            rate_type=data.rate_type,
            source=data.source,
            notes=data.notes,
            created_by=user.get("username"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/exchange-rates/batch")
def set_rates_batch(
    data: ExchangeRateBatch,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Batch input kurs sekaligus.
    Berguna untuk input kurs harian dari BI secara massal.
    """
    bi_rates = []
    for r in data.rates:
        bi_rates.append({
            "from_currency": r.from_currency,
            "rate_date": r.rate_date,
            "rate": r.rate,
            "rate_type": r.rate_type,
            "source": r.source,
        })
    return ExchangeRateEngine.import_bi_rates(db, bi_rates, created_by=user.get("username"))


@router.delete("/exchange-rates")
def delete_rate(
    from_currency: str = Query(...),
    to_currency: str = Query("IDR"),
    rate_date: date = Query(...),
    rate_type: str = Query("middle"),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Hapus kurs untuk tanggal dan tipe tertentu."""
    ok = ExchangeRateEngine.delete_rate(
        db,
        from_currency=from_currency.upper(),
        to_currency=to_currency.upper(),
        rate_date=rate_date,
        rate_type=rate_type,
    )
    if not ok:
        raise HTTPException(404, "Kurs tidak ditemukan.")
    return {"success": True}


# ── 3. Konversi ───────────────────────────────────────────────────────────────

@router.post("/convert")
def convert_amount(
    data: ConvertRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Konversi jumlah dari satu mata uang ke lain.
    Mencari kurs pada tanggal yang diminta (fallback ke kurs terdekat sebelumnya).
    """
    try:
        rate = ExchangeRateEngine.get_rate(
            db,
            from_currency=data.from_currency,
            to_currency=data.to_currency,
            rate_date=data.rate_date or date.today(),
            rate_type=data.rate_type,
        )
        if rate is None:
            raise HTTPException(
                404,
                f"Kurs {data.from_currency}/{data.to_currency} tidak tersedia. "
                "Silakan input kurs terlebih dahulu."
            )

        converted = ExchangeRateEngine.convert(
            db, data.amount, data.from_currency, data.to_currency,
            data.rate_date, data.rate_type
        )
        return {
            "from_currency": data.from_currency,
            "to_currency": data.to_currency,
            "amount": str(data.amount),
            "rate": str(rate),
            "rate_date": str(data.rate_date or date.today()),
            "converted_amount": str(converted),
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── 4. Revaluasi ──────────────────────────────────────────────────────────────

@router.get("/revaluation/{entity_id}/preview")
def preview_revaluation(
    entity_id: str,
    revaluation_date: date = Query(...),
    rate_type: str = "middle",
    gl_gain_account: str = "7-1000",
    gl_loss_account: str = "8-1000",
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Preview revaluasi: hitung adjustment tanpa memposting.
    Lihat dulu sebelum eksekusi.
    """
    return RevaluationEngine.preview_revaluation(
        db,
        entity_id=entity_id,
        revaluation_date=revaluation_date,
        rate_type=rate_type,
        gl_gain_account=gl_gain_account,
        gl_loss_account=gl_loss_account,
    )


@router.post("/revaluation")
def create_revaluation(
    data: RevaluationCreate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Buat draft revaluation run.
    Belum posting ke GL. Gunakan /revaluation/{id}/post untuk eksekusi.
    """
    try:
        return RevaluationEngine.create_run(
            db,
            entity_id=data.entity_id,
            revaluation_date=data.revaluation_date,
            run_by=user.get("username"),
            auto_reverse=data.auto_reverse,
            gl_gain_account=data.gl_gain_account,
            gl_loss_account=data.gl_loss_account,
            rate_type=data.rate_type,
            notes=data.notes,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/revaluation/{run_id}/post")
def post_revaluation(
    run_id: str,
    data: RevaluationPost,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Posting jurnal revaluasi ke GL.
    Setelah posted, tidak bisa diedit kecuali di-reverse.
    """
    try:
        return RevaluationEngine.post_revaluation(
            db,
            run_id=run_id,
            entity_id=data.entity_id,
            posted_by=user.get("username"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/revaluation/{run_id}/reverse")
def reverse_revaluation(
    run_id: str,
    data: RevaluationReverse,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Manual reverse revaluasi yang sudah diposting.
    Akan membuat jurnal balik dengan tanggal hari ini.
    """
    try:
        return RevaluationEngine.reverse_revaluation(
            db,
            run_id=run_id,
            entity_id=data.entity_id,
            reversed_by=user.get("username"),
            reason=data.reason,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/revaluation/{entity_id}/history")
def get_revaluation_history(
    entity_id: str,
    limit: int = Query(24, le=120),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Riwayat semua revaluation run untuk entity."""
    return RevaluationEngine.get_run_history(db, entity_id=entity_id, limit=limit)


@router.get("/revaluation/{entity_id}/run/{run_id}")
def get_revaluation_detail(
    entity_id: str,
    run_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Detail revaluation run beserta semua entry-nya."""
    try:
        return RevaluationEngine.get_run_detail(db, run_id=run_id, entity_id=entity_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


# ── 5. Laporan FCY ────────────────────────────────────────────────────────────

@router.get("/exposure/{entity_id}")
def get_fcy_exposure(
    entity_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    FCY Exposure: per mata uang, berapa total saldo FCY, nilai IDR di buku,
    nilai IDR di kurs sekarang, dan posisi unrealized G/L.
    """
    return RevaluationEngine.get_fcy_exposure(db, entity_id=entity_id)


@router.get("/gl-report/{entity_id}")
def get_gl_fcy_report(
    entity_id: str,
    currency: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Semua transaksi GL yang melibatkan mata uang asing.
    Menampilkan amount_fcy, exchange_rate, dan IDR equivalent.
    """
    return RevaluationEngine.get_gl_fcy_report(
        db,
        entity_id=entity_id,
        currency=currency.upper() if currency else None,
        date_from=date_from,
        date_to=date_to,
    )
