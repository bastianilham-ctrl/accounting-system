# modules/asset_router.py
# REST endpoints untuk Fixed Asset, jadwal depresiasi, dan Prepaid Expense

from uuid import UUID
from decimal import Decimal
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger

from core.database import get_db
from modules.asset_engine import AssetEngine, FISCAL_GROUPS

router = APIRouter(tags=["Fixed Assets & Prepaid"])


# ----------------------------------------------------------
# REQUEST MODELS
# ----------------------------------------------------------

class AssetCreateRequest(BaseModel):
    entity_id: UUID
    asset_name: str
    category: str               # kelompok_1..4 / bangunan_permanen / bangunan_tidak_permanen / intangible
    acquisition_date: date
    acquisition_cost: Decimal
    salvage_value: Decimal = Decimal("0")
    useful_life_months: Optional[int] = None   # default: sesuai kelompok fiskal
    method: str = "straight_line"              # straight_line | declining_balance
    fiscal_method: str = "straight_line"
    coa_asset: str = "1-6-001"
    coa_accum_dep: str = "1-7-001"
    coa_dep_expense: str = "6-2-001"
    ap_invoice_id: Optional[UUID] = None


class PostPeriodRequest(BaseModel):
    entity_id: UUID
    period_date: date           # konvensi: tanggal terakhir bulan, mis. 2026-05-31
    posted_by: str = "system"


class PrepaidCreateRequest(BaseModel):
    entity_id: UUID
    description: str
    start_date: date
    end_date: date
    total_amount: Decimal
    coa_prepaid: str = "1-5-001"
    coa_expense: str = "6-1-003"
    ap_invoice_id: Optional[UUID] = None


# ----------------------------------------------------------
# FIXED ASSET ENDPOINTS
# ----------------------------------------------------------

@router.post("/assets")
def create_asset(req: AssetCreateRequest, db: Session = Depends(get_db)):
    """
    Daftarkan aset tetap baru.
    Jadwal depresiasi komersial + fiskal dibuat otomatis.
    Referensi fiskal: PMK 96/2009.
    """
    valid_categories = [k for k in FISCAL_GROUPS if k != "prepaid"]
    if req.category not in valid_categories:
        raise HTTPException(400, f"Kategori tidak valid. Pilih: {valid_categories}")

    engine = AssetEngine(db)
    try:
        result = engine.create_asset(
            entity_id=req.entity_id,
            asset_name=req.asset_name,
            category=req.category,
            acquisition_date=req.acquisition_date,
            acquisition_cost=req.acquisition_cost,
            salvage_value=req.salvage_value,
            useful_life_months=req.useful_life_months,
            method=req.method,
            coa_asset=req.coa_asset,
            coa_accum_dep=req.coa_accum_dep,
            coa_dep_expense=req.coa_dep_expense,
            ap_invoice_id=req.ap_invoice_id,
            fiscal_method=req.fiscal_method,
        )
    except Exception as e:
        raise HTTPException(500, str(e))
    return result


@router.get("/assets/{entity_id}")
def list_assets(
    entity_id: str,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List aset tetap per entity lengkap dengan nilai buku saat ini."""
    query = """
        SELECT
            fa.id, fa.asset_code, fa.asset_name, fa.category,
            fa.acquisition_date, fa.acquisition_cost, fa.salvage_value,
            fa.useful_life_months, fa.fiscal_life_months,
            fa.method, fa.fiscal_method, fa.fiscal_rate, fa.status,
            fa.coa_asset, fa.coa_accum_dep, fa.coa_dep_expense,
            COALESCE(SUM(ads.commercial_dep) FILTER (WHERE ads.is_posted), 0)  AS accum_dep_posted,
            fa.acquisition_cost
                - COALESCE(SUM(ads.commercial_dep) FILTER (WHERE ads.is_posted), 0)  AS book_value
        FROM fixed_asset fa
        LEFT JOIN asset_depreciation_schedule ads ON ads.asset_id = fa.id
        WHERE fa.entity_id = :eid
    """
    params: dict = {"eid": entity_id}
    if status:
        query += " AND fa.status = :status"
        params["status"] = status
    query += " GROUP BY fa.id ORDER BY fa.asset_code"

    rows = db.execute(text(query), params).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/assets/{asset_id}/schedule")
def get_depreciation_schedule(asset_id: UUID, db: Session = Depends(get_db)):
    """
    Jadwal depresiasi lengkap satu aset:
    - commercial_dep: depresiasi akuntansi
    - fiscal_dep: depresiasi fiskal (PMK 96/2009)
    - temp_diff: beda waktu (commercial - fiscal) → dasar koreksi fiskal
    """
    rows = db.execute(
        text("""
            SELECT
                period_date, commercial_dep, fiscal_dep, temp_diff,
                book_value_end, fiscal_value_end, is_posted
            FROM asset_depreciation_schedule
            WHERE asset_id = :id
            ORDER BY period_date
        """),
        {"id": str(asset_id)}
    ).fetchall()
    if not rows:
        raise HTTPException(404, "Aset tidak ditemukan atau belum ada jadwal")
    return [dict(r._mapping) for r in rows]


@router.post("/assets/{asset_id}/regenerate-schedule")
def regenerate_schedule(asset_id: UUID, db: Session = Depends(get_db)):
    """
    Regenerate jadwal depresiasi (hapus yg belum diposting, buat ulang).
    Berguna setelah perubahan data aset.
    """
    engine = AssetEngine(db)
    count  = engine.generate_depreciation_schedule(asset_id)
    if count == 0:
        raise HTTPException(404, "Aset tidak ditemukan")
    return {"success": True, "schedule_periods": count}


@router.post("/assets/post-depreciation")
def post_monthly_depreciation(req: PostPeriodRequest, db: Session = Depends(get_db)):
    """
    Batch posting jurnal depresiasi untuk semua aset aktif pada periode tertentu.
    Gunakan tanggal akhir bulan sebagai period_date, mis. 2026-05-31.
    """
    engine = AssetEngine(db)
    result = engine.post_monthly_depreciation(req.entity_id, req.period_date, req.posted_by)
    return result


@router.get("/assets/{entity_id}/fiscal-correction")
def fiscal_correction_report(
    entity_id: str,
    year: int,
    db: Session = Depends(get_db),
):
    """
    Laporan koreksi fiskal depresiasi untuk SPT Badan.
    Koreksi positif: depresiasi komersial > fiskal → laba fiskal lebih tinggi.
    Koreksi negatif: depresiasi fiskal > komersial → laba fiskal lebih rendah.
    """
    engine = AssetEngine(db)
    return engine.get_fiscal_correction_summary(UUID(entity_id), year)


# ----------------------------------------------------------
# PREPAID EXPENSE ENDPOINTS
# ----------------------------------------------------------

@router.post("/prepaid")
def create_prepaid(req: PrepaidCreateRequest, db: Session = Depends(get_db)):
    """
    Daftarkan biaya dibayar dimuka (prepaid expense).
    Jadwal amortisasi bulanan dibuat otomatis.
    Contoh: sewa tahunan, premi asuransi, lisensi software tahunan.
    """
    engine = AssetEngine(db)
    try:
        result = engine.create_prepaid(
            entity_id=req.entity_id,
            description=req.description,
            start_date=req.start_date,
            end_date=req.end_date,
            total_amount=req.total_amount,
            coa_prepaid=req.coa_prepaid,
            coa_expense=req.coa_expense,
            ap_invoice_id=req.ap_invoice_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.get("/prepaid/{entity_id}")
def list_prepaid(entity_id: str, db: Session = Depends(get_db)):
    """List semua prepaid expense per entity."""
    rows = db.execute(
        text("""
            SELECT
                id, prepaid_code, description,
                start_date, end_date,
                total_amount, monthly_amount, status
            FROM prepaid_expense
            WHERE entity_id = :eid
            ORDER BY start_date DESC
        """),
        {"eid": entity_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/prepaid/{prepaid_id}/schedule")
def get_amortization_schedule(prepaid_id: UUID, db: Session = Depends(get_db)):
    """Jadwal amortisasi bulanan satu prepaid expense."""
    rows = db.execute(
        text("""
            SELECT period_date, amortize_amount, remaining_amount, is_posted
            FROM prepaid_amortization_schedule
            WHERE prepaid_id = :id
            ORDER BY period_date
        """),
        {"id": str(prepaid_id)}
    ).fetchall()
    if not rows:
        raise HTTPException(404, "Prepaid tidak ditemukan atau belum ada jadwal")
    return [dict(r._mapping) for r in rows]


@router.post("/prepaid/post-amortization")
def post_monthly_amortization(req: PostPeriodRequest, db: Session = Depends(get_db)):
    """Batch posting jurnal amortisasi prepaid untuk periode tertentu."""
    engine = AssetEngine(db)
    result = engine.post_monthly_amortization(req.entity_id, req.period_date, req.posted_by)
    return result


# ----------------------------------------------------------
# REFERENSI
# ----------------------------------------------------------

@router.get("/assets/reference/fiscal-groups")
def get_fiscal_groups():
    """Daftar kelompok aset fiskal PMK 96/2009 beserta tarif dan masa manfaat."""
    return {
        key: {
            "label":       val["label"],
            "life_months": val["life_months"],
            "sl_rate_pct": float(val["sl_rate"]) if val["sl_rate"] else None,
            "db_rate_pct": float(val["db_rate"]) if val["db_rate"] else None,
        }
        for key, val in FISCAL_GROUPS.items()
    }
