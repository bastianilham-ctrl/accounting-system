# modules/vendor_tax_router.py
# Endpoint untuk manage tax profile vendor: set SKB, UMKM, override PPh

from uuid import UUID
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger
import shutil
from pathlib import Path

from core.database import get_db
from config.settings import settings

router = APIRouter(prefix="/vendors", tags=["Vendor Tax Profile"])


# ----------------------------------------------------------
# REQUEST MODELS
# ----------------------------------------------------------

class VendorCategoryUpdate(BaseModel):
    vendor_category: str   # PT, CV, Firma, Perorangan, UMKM, Asing
    is_pkp: bool = True
    reviewed_by: str = "system"

class SKBUpdate(BaseModel):
    has_skb: bool
    skb_number: Optional[str] = None
    skb_expiry: Optional[date] = None
    reason: str = ""
    reviewed_by: str = "system"

class UMKMUpdate(BaseModel):
    umkm_cert_number: Optional[str] = None
    umkm_cert_expiry: Optional[date] = None
    omzet_per_tahun: Optional[float] = None
    reviewed_by: str = "system"

class PPHOverride(BaseModel):
    pph_override_type: Optional[str] = None   # PPh23, PPh4(2), PPh21, bebas
    pph_override_rate: Optional[float] = None
    pph_override_reason: str
    reviewed_by: str = "system"


# ----------------------------------------------------------
# ENDPOINTS
# ----------------------------------------------------------

@router.get("/{vendor_id}/tax-profile")
def get_tax_profile(vendor_id: UUID, db: Session = Depends(get_db)):
    """Lihat tax profile lengkap vendor termasuk treatment PPh efektif."""
    row = db.execute(
        text("SELECT * FROM vw_vendor_tax_status WHERE id = :id"),
        {"id": str(vendor_id)}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Vendor tidak ditemukan")
    return dict(row._mapping)


@router.get("/tax-review-queue/{entity_id}")
def get_review_queue(entity_id: str, db: Session = Depends(get_db)):
    """
    Daftar vendor yang perlu review tax profile:
    - Vendor baru belum pernah di-review
    - SKB expired
    - Surat UMKM expired
    """
    rows = db.execute(
        text("""
            SELECT v.id, v.vendor_code, v.vendor_name, v.npwp,
                   v.vendor_category, v.has_skb, v.skb_expiry,
                   v.umkm_cert_expiry, v.tax_reviewed_at,
                   vts.pph_treatment, vts.skb_expired,
                   CASE
                       WHEN v.tax_reviewed_at IS NULL THEN 'Belum pernah di-review'
                       WHEN v.has_skb AND v.skb_expiry < CURRENT_DATE THEN 'SKB expired'
                       WHEN v.vendor_category = 'UMKM' AND v.umkm_cert_expiry < CURRENT_DATE
                           THEN 'Surat UMKM expired'
                       ELSE NULL
                   END AS review_reason
            FROM vendor v
            JOIN vw_vendor_tax_status vts ON vts.id = v.id
            WHERE v.entity_id = :eid
              AND (
                v.tax_reviewed_at IS NULL
                OR (v.has_skb = TRUE AND v.skb_expiry < CURRENT_DATE)
                OR (v.vendor_category = 'UMKM' AND v.umkm_cert_expiry IS NOT NULL
                    AND v.umkm_cert_expiry < CURRENT_DATE)
              )
            ORDER BY v.tax_reviewed_at ASC NULLS FIRST
        """),
        {"eid": entity_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.put("/{vendor_id}/category")
def update_vendor_category(
    vendor_id: UUID,
    req: VendorCategoryUpdate,
    db: Session = Depends(get_db)
):
    """Update kategori vendor (PT/CV/UMKM/dll) dan status PKP."""
    valid_categories = ["PT", "CV", "Firma", "Perorangan", "UMKM", "Asing", "Lainnya"]
    if req.vendor_category not in valid_categories:
        raise HTTPException(400, f"Kategori tidak valid. Pilih: {valid_categories}")

    old = db.execute(
        text("SELECT vendor_category, is_pkp FROM vendor WHERE id = :id"),
        {"id": str(vendor_id)}
    ).fetchone()

    db.execute(
        text("""
            UPDATE vendor SET
                vendor_category  = :cat,
                is_pkp           = :pkp,
                tax_reviewed_by  = :by,
                tax_reviewed_at  = NOW()
            WHERE id = :id
        """),
        {"cat": req.vendor_category, "pkp": req.is_pkp,
         "by": req.reviewed_by, "id": str(vendor_id)}
    )

    # Jika UMKM, set tarif PPh ke 0.5% otomatis
    if req.vendor_category == "UMKM":
        db.execute(
            text("""UPDATE vendor SET
                        default_pph_type = 'PPh_Final_UMKM',
                        default_pph_rate = 0.5
                    WHERE id = :id"""),
            {"id": str(vendor_id)}
        )

    _log_tax_change(db, vendor_id, req.reviewed_by, "vendor_category",
                    str(old.vendor_category) if old else None, req.vendor_category)
    db.commit()
    return {"success": True, "message": f"Kategori vendor diupdate ke {req.vendor_category}"}


@router.put("/{vendor_id}/skb")
def update_skb(vendor_id: UUID, req: SKBUpdate, db: Session = Depends(get_db)):
    """
    Update status SKB (Surat Keterangan Bebas PPh) vendor.
    Jika has_skb=True dan SKB masih berlaku → tarif PPh = 0%.
    """
    db.execute(
        text("""
            UPDATE vendor SET
                has_skb         = :has_skb,
                skb_number      = :skb_no,
                skb_expiry      = :expiry,
                tax_reviewed_by = :by,
                tax_reviewed_at = NOW()
            WHERE id = :id
        """),
        {
            "has_skb": req.has_skb, "skb_no": req.skb_number,
            "expiry": req.skb_expiry, "by": req.reviewed_by,
            "id": str(vendor_id)
        }
    )
    _log_tax_change(db, vendor_id, req.reviewed_by, "has_skb",
                    None, str(req.has_skb), req.reason)
    db.commit()

    status = "aktif" if req.has_skb else "tidak memiliki SKB"
    return {"success": True, "message": f"Status SKB vendor: {status}"}


@router.put("/{vendor_id}/umkm")
def update_umkm(vendor_id: UUID, req: UMKMUpdate, db: Session = Depends(get_db)):
    """
    Set vendor sebagai UMKM dengan Surat Keterangan UMKM.
    PPh Final 0.5% sesuai PP 55/2022.
    """
    db.execute(
        text("""
            UPDATE vendor SET
                vendor_category  = 'UMKM',
                umkm_cert_number = :cert_no,
                umkm_cert_expiry = :expiry,
                omzet_per_tahun  = :omzet,
                default_pph_type = 'PPh_Final_UMKM',
                default_pph_rate = 0.5,
                tax_reviewed_by  = :by,
                tax_reviewed_at  = NOW()
            WHERE id = :id
        """),
        {
            "cert_no": req.umkm_cert_number,
            "expiry":  req.umkm_cert_expiry,
            "omzet":   req.omzet_per_tahun,
            "by":      req.reviewed_by,
            "id":      str(vendor_id)
        }
    )
    _log_tax_change(db, vendor_id, req.reviewed_by, "umkm_status",
                    None, f"UMKM cert {req.umkm_cert_number}")
    db.commit()
    return {"success": True, "message": "Vendor diset sebagai UMKM — PPh Final 0.5%"}


@router.put("/{vendor_id}/pph-override")
def override_pph(vendor_id: UUID, req: PPHOverride, db: Session = Depends(get_db)):
    """
    Override manual tarif PPh vendor untuk kasus khusus.
    Contoh: vendor asing (PPh 26), vendor dengan DTA/P3B, dll.
    Wajib isi alasan untuk audit trail.
    """
    if not req.pph_override_reason:
        raise HTTPException(400, "Alasan override wajib diisi untuk audit trail")

    db.execute(
        text("""
            UPDATE vendor SET
                pph_override_type   = :type,
                pph_override_rate   = :rate,
                pph_override_reason = :reason,
                tax_reviewed_by     = :by,
                tax_reviewed_at     = NOW()
            WHERE id = :id
        """),
        {
            "type":   req.pph_override_type,
            "rate":   req.pph_override_rate,
            "reason": req.pph_override_reason,
            "by":     req.reviewed_by,
            "id":     str(vendor_id)
        }
    )
    _log_tax_change(db, vendor_id, req.reviewed_by, "pph_override",
                    None, f"{req.pph_override_type} {req.pph_override_rate}%",
                    req.pph_override_reason)
    db.commit()
    return {"success": True, "message": f"PPh override diset: {req.pph_override_type} {req.pph_override_rate}%"}


@router.post("/{vendor_id}/documents")
async def upload_tax_document(
    vendor_id: UUID,
    doc_type: str,
    doc_number: Optional[str] = None,
    doc_date: Optional[date] = None,
    expiry_date: Optional[date] = None,
    notes: Optional[str] = None,
    uploaded_by: str = "system",
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """Upload dokumen pajak vendor: SKB, Surat UMKM, NPWP, dll."""
    valid_types = ["SKB", "UMKM_CERT", "NPWP", "NIB", "PKP_CERT", "OTHER"]
    if doc_type not in valid_types:
        raise HTTPException(400, f"Tipe dokumen tidak valid. Pilih: {valid_types}")

    # Simpan file
    upload_dir = Path(settings.UPLOAD_DIR) / "vendor_docs" / str(vendor_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / f"{doc_type}_{file.filename}"
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    from uuid import uuid4
    db.execute(
        text("""
            INSERT INTO vendor_tax_document
                (id, vendor_id, doc_type, doc_number, doc_date,
                 expiry_date, file_path, notes, uploaded_by)
            VALUES
                (:id, :vid, :dtype, :dno, :ddate,
                 :exp, :fpath, :notes, :by)
        """),
        {
            "id": str(uuid4()), "vid": str(vendor_id),
            "dtype": doc_type, "dno": doc_number,
            "ddate": doc_date, "exp": expiry_date,
            "fpath": str(file_path), "notes": notes,
            "by": uploaded_by,
        }
    )
    db.commit()
    return {"success": True, "file_path": str(file_path), "doc_type": doc_type}


@router.get("/{vendor_id}/tax-log")
def get_tax_log(vendor_id: UUID, db: Session = Depends(get_db)):
    """Lihat audit trail semua perubahan tax profile vendor."""
    rows = db.execute(
        text("""
            SELECT * FROM vendor_tax_log
            WHERE vendor_id = :id
            ORDER BY changed_at DESC
        """),
        {"id": str(vendor_id)}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ----------------------------------------------------------
# HELPER
# ----------------------------------------------------------

def _log_tax_change(db, vendor_id, changed_by, field, old_val, new_val, reason=None):
    from uuid import uuid4
    try:
        db.execute(
            text("""
                INSERT INTO vendor_tax_log
                    (id, vendor_id, changed_by, field_changed, old_value, new_value, reason)
                VALUES
                    (:id, :vid, :by, :field, :old, :new, :reason)
            """),
            {
                "id": str(uuid4()), "vid": str(vendor_id),
                "by": changed_by, "field": field,
                "old": old_val, "new": new_val, "reason": reason,
            }
        )
    except Exception as e:
        logger.warning(f"Tax log error: {e}")