# modules/ocr_router.py
# FastAPI router untuk upload dan proses OCR invoice

import shutil
import uuid
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger
import os

from core.database import get_db, SessionLocal
from modules.ocr_service import proses_ai_ocr # Mengganti OCRInvoice dengan proses_ai_ocr
from modules.ap_classifier import APClassifier
from modules.journal_engine import JournalEngine, JournalEntry, JournalLine
from config.settings import settings
from decimal import Decimal
from datetime import date

router = APIRouter(prefix="/ocr", tags=["OCR Invoice"])


# --- Background Task Function ---
async def _process_invoice_background_task(
    file_path: str,
    entity_id: str,
    auto_post: bool,
    original_filename: str,
    created_by: str = "system" # Default user for background tasks
):
    db = None
    try:
        # Create a new DB session for the background task
        db = SessionLocal()

        logger.info(f"Memulai proses background untuk file: {original_filename}")

        # 1. Proses OCR menggunakan AI-OCR service baru
        ocr_result = await proses_ai_ocr(file_path)

        if not ocr_result or not ocr_result.get("success"):
            logger.error(f"AI-OCR gagal untuk {original_filename}: {ocr_result.get('error', 'Tidak ada data kembali')}")
            return

        # Map ocr_result dari ocr_service.py ke format yang diharapkan oleh helper functions
        # ocr_service.py mengembalikan: {"vendor_name": ..., "invoice_date": ..., "invoice_no": ..., "total_amount": ...}
        # Helper functions mengharapkan: "subtotal", "ppn_amount", "pph_amount", "description", "confidence"
        # Kita akan mengisi nilai default atau menyederhanakan untuk field yang tidak diekstrak oleh ocr_service.py
        mapped_ocr_result = {
            "vendor_name": ocr_result.get("vendor_name"),
            "invoice_no": ocr_result.get("invoice_no"),
            "invoice_date": ocr_result.get("invoice_date"),
            "total_amount": ocr_result.get("total_amount", Decimal("0")),
            "subtotal": ocr_result.get("subtotal") or ocr_result.get("total_amount", Decimal("0")),
            "ppn_amount": ocr_result.get("ppn_amount", Decimal("0")),
            "pph_amount": ocr_result.get("pph_amount", Decimal("0")),
            "faktur_pajak_no": None,
            "due_date": None,
            "description": f"Invoice {ocr_result.get('invoice_no', 'Unknown')} from {ocr_result.get('vendor_name', 'Unknown')}",
            "confidence": 0.8, # Default confidence for AI-OCR
            "success": True
        }

        if not mapped_ocr_result.get("vendor_name") or not mapped_ocr_result.get("invoice_no") or not mapped_ocr_result.get("invoice_date") or mapped_ocr_result.get("total_amount") <= 0:
            logger.error(f"Data OCR tidak lengkap dari AI untuk {original_filename}. Melewati proses selanjutnya.")
            return

        # 2. Cari atau buat vendor berdasarkan nama hasil OCR
        vendor_id = _get_or_create_vendor(
            db=db,
            entity_id=entity_id,
            vendor_name=mapped_ocr_result["vendor_name"],
            vendor_npwp=None, # ocr_service.py tidak mengekstrak NPWP
        )

        # 3. Klasifikasi akun
        classification = None
        if vendor_id and mapped_ocr_result.get("description"):
            classifier = APClassifier(db)
            classification = classifier.classify(
                vendor_id=vendor_id,
                description=mapped_ocr_result.get("description", ""),
                amount=mapped_ocr_result.get("subtotal", Decimal("0")),
                service_period_months=1,
            )

        # 4. Simpan ke ap_invoice
        invoice_id = _save_ap_invoice(
            db=db,
            entity_id=entity_id,
            vendor_id=vendor_id,
            ocr_result=mapped_ocr_result,
            classification=classification,
            file_path=file_path,
        )

        # 5. Auto-post jurnal AP jika diminta
        if auto_post and entity_id and vendor_id and classification:
            _post_ap_journal(
                db=db,
                entity_id=entity_id,
                vendor_id=vendor_id,
                ocr_result=mapped_ocr_result,
                classification=classification,
                invoice_id=invoice_id,
                created_by=created_by,
            )
        logger.info(f"Proses background selesai untuk {original_filename}. Invoice ID: {invoice_id}")

    except Exception as e:
        logger.error(f"Error dalam proses OCR background untuk {original_filename}: {e}", exc_info=True)
    finally:
        if db:
            db.close()
        # Hapus file sementara setelah diproses (berhasil atau gagal)
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"File sementara dihapus: {file_path}")


@router.post("/upload")
async def upload_invoice(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    entity_id: str = None,
    auto_post: bool = False,
):
    """
    Upload PDF invoice → OCR extract → klasifikasi akun → (opsional) auto-post jurnal AP.
    Proses dilakukan secara asinkron menggunakan BackgroundTasks.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Hanya file PDF yang diterima")

    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(exist_ok=True)
    file_id   = str(uuid.uuid4())
    file_path = upload_dir / f"{file_id}_{file.filename}"

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    logger.info(f"File uploaded and queued for processing: {file_path}")

    # Daftarkan proses ke background task
    background_tasks.add_task(
        _process_invoice_background_task,
        str(file_path),
        entity_id,
        auto_post,
        file.filename
    )

    return {
        "success": True,
        "message": "Invoice berhasil diunggah. AI sedang mengekstrak data di latar belakang.",
        "file_id": file_id
    }


@router.post("/extract")
async def extract_invoice(
    file: UploadFile = File(...),
    entity_id: str = None,
    db: Session = Depends(get_db),
):
    """
    Ekstrak data invoice dari PDF/JPG/PNG secara sinkron untuk auto-fill form manual.
    Tidak menyimpan apapun ke ap_invoice — murni OCR + pencarian vendor existing.
    """
    ext = Path(file.filename).suffix.lower()
    if ext not in (".pdf", ".jpg", ".jpeg", ".png"):
        raise HTTPException(status_code=400, detail="Hanya PDF, JPG, atau PNG yang diterima")

    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(exist_ok=True)
    file_id   = str(uuid.uuid4())
    file_path = upload_dir / f"{file_id}_{file.filename}"

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        ocr_result = await proses_ai_ocr(str(file_path))
    finally:
        if file_path.exists():
            os.remove(file_path)

    if not ocr_result or not ocr_result.get("success"):
        return {"success": False, "error": ocr_result.get("error", "OCR gagal membaca file") if ocr_result else "OCR gagal membaca file"}

    vendor_name = ocr_result.get("vendor_name")
    vendor_id = None
    if entity_id and vendor_name:
        existing = db.execute(
            text("""
                SELECT id FROM vendor
                WHERE entity_id = :eid AND LOWER(vendor_name) = LOWER(:name)
                LIMIT 1
            """),
            {"eid": entity_id, "name": vendor_name}
        ).fetchone()
        if existing:
            vendor_id = str(existing.id)

    return {
        "success":      True,
        "vendor_id":    vendor_id,
        "vendor_name":  vendor_name,
        "vendor_npwp":  ocr_result.get("vendor_npwp"),
        "invoice_no":   ocr_result.get("invoice_no"),
        "invoice_date": ocr_result.get("invoice_date"),
        "subtotal":     float(ocr_result.get("subtotal") or 0),
        "ppn_amount":   float(ocr_result.get("ppn_amount") or 0),
        "pph_amount":   float(ocr_result.get("pph_amount") or 0),
        "total_amount": float(ocr_result.get("total_amount") or 0),
        "confidence":   0.8,
    }


@router.get("/invoices/{entity_id}")
def list_invoices(entity_id: str, status: str = None, db: Session = Depends(get_db)):
    """List semua AP invoice untuk satu entity."""
    query = """
        SELECT
            ai.id, ai.invoice_no, ai.invoice_date, ai.due_date,
            ai.total_amount, ai.paid_amount, ai.status,
            ai.classification, ai.pph_type, ai.pph_rate,
            v.vendor_name, v.vendor_code
        FROM ap_invoice ai
        JOIN vendor v ON v.id = ai.vendor_id
        WHERE ai.entity_id = :eid
    """
    params = {"eid": entity_id}
    if status:
        query += " AND ai.status = :status"
        params["status"] = status
    query += " ORDER BY ai.invoice_date DESC"

    rows = db.execute(text(query), params).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/invoices/{invoice_id}/post-journal")
def post_invoice_journal(invoice_id: str, created_by: str = "system", db: Session = Depends(get_db)):
    """Manual posting jurnal AP dari invoice yang sudah di-OCR."""
    invoice = db.execute(
        text("SELECT * FROM ap_invoice WHERE id = :id"),
        {"id": invoice_id}
    ).fetchone()

    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice tidak ditemukan")

    inv = dict(invoice._mapping)
    result = _post_ap_journal(
        db=db,
        entity_id=str(inv["entity_id"]),
        vendor_id=str(inv["vendor_id"]),
        ocr_result={
            "invoice_no":   inv["invoice_no"],
            "invoice_date": str(inv["invoice_date"]),
            "description":  f"Invoice {inv['invoice_no']}",
            "subtotal":     Decimal(str(inv["subtotal"])),
            "ppn_amount":   Decimal(str(inv["ppn_amount"])),
            "pph_amount":   Decimal(str(inv["pph_amount"])),
            "total_amount": Decimal(str(inv["total_amount"])),
        },
        classification={
            "classification": inv.get("classification", "expense"),
            "coa_code":       inv.get("coa_expense", "6-1-001"),
            "pph_type":       inv.get("pph_type"),
            "pph_rate":       inv.get("pph_rate", 0),
        },
        invoice_id=invoice_id,
        created_by=created_by,
    )
    return result


# ----------------------------------------------------------
# HELPER FUNCTIONS
# ----------------------------------------------------------

def _get_or_create_vendor(db: Session, entity_id: str, vendor_name: str, vendor_npwp: str = None) -> uuid.UUID:
    """Cari vendor by nama, buat baru jika belum ada."""
    existing = db.execute(
        text("""
            SELECT id FROM vendor
            WHERE entity_id = :eid
            AND LOWER(vendor_name) = LOWER(:name)
            LIMIT 1
        """),
        {"eid": entity_id, "name": vendor_name}
    ).fetchone()

    if existing:
        return existing.id

    # Buat vendor baru
    vendor_id   = uuid.uuid4()
    vendor_code = f"VND-{str(vendor_id)[:8].upper()}"

    db.execute(
        text("""
            INSERT INTO vendor
                (id, entity_id, vendor_code, vendor_name, npwp, scrape_status)
            VALUES
                (:id, :eid, :code, :name, :npwp, 'pending')
        """),
        {
            "id": str(vendor_id), "eid": entity_id,
            "code": vendor_code, "name": vendor_name,
            "npwp": vendor_npwp,
        }
    )
    db.commit()
    logger.info(f"Vendor baru dibuat: {vendor_name} ({vendor_code})")
    return vendor_id


def _save_ap_invoice(
    db: Session, entity_id: str, vendor_id, ocr_result: dict,
    classification: dict, file_path: str
) -> uuid.UUID:
    """Simpan hasil OCR ke tabel ap_invoice."""
    invoice_id = uuid.uuid4()

    subtotal     = float(ocr_result.get("subtotal") or 0)
    ppn_amount   = float(ocr_result.get("ppn_amount") or 0)
    pph_amount   = float(ocr_result.get("pph_amount") or 0)
    total_amount = float(ocr_result.get("total_amount") or subtotal + ppn_amount)

    db.execute(
        text("""
            INSERT INTO ap_invoice (
                id, entity_id, vendor_id, invoice_no, invoice_date, due_date,
                subtotal, ppn_amount, pph_amount, total_amount,
                status, ocr_source, ocr_file_path, ocr_confidence,
                faktur_pajak_no, pph_type, pph_rate,
                coa_expense, classification, classification_confidence,
                payable_category, payable_coa
            ) VALUES (
                :id, :eid, :vid, :inv_no, :inv_date, :due_date,
                :sub, :ppn, :pph, :total,
                'draft', 'upload', :fpath, :conf,
                :faktur, :pph_type, :pph_rate,
                :coa, :cls, :cls_conf,
                'trade', '2-1-001'
            )
        """),
        {
            "id": str(invoice_id), "eid": entity_id, "vid": str(vendor_id),
            "inv_no":   ocr_result.get("invoice_no", f"INV-{str(invoice_id)[:8]}"),
            "inv_date": ocr_result.get("invoice_date") or date.today().isoformat(),
            "due_date": ocr_result.get("due_date"),
            "sub": subtotal, "ppn": ppn_amount,
            "pph": pph_amount, "total": total_amount,
            "fpath":    file_path,
            "conf":     float(ocr_result.get("confidence", 0.5)),
            "faktur":   ocr_result.get("faktur_pajak_no"),
            "pph_type": classification.get("pph_type") if classification else None,
            "pph_rate": classification.get("pph_rate") if classification else None,
            "coa":      classification.get("coa_code") if classification else None,
            "cls":      classification.get("classification") if classification else None,
            "cls_conf": float(classification.get("confidence", 0.5)) if classification else None,
        }
    )
    db.commit()
    return invoice_id


def _post_ap_journal(
    db: Session, entity_id: str, vendor_id,
    ocr_result: dict, classification: dict,
    invoice_id=None, created_by: str = "system"
) -> dict:
    """
    Posting jurnal AP otomatis:
    Dr. Beban/Prepaid/Aset     xxx
    Dr. PPN Masukan            xxx  (jika ada)
      Cr. Hutang PPh 23        xxx  (jika kena withholding)
      Cr. Hutang Usaha (AP)    xxx
    """
    engine    = JournalEngine(db)
    subtotal  = Decimal(str(ocr_result.get("subtotal") or 0))
    ppn       = Decimal(str(ocr_result.get("ppn_amount") or 0))
    pph       = Decimal(str(ocr_result.get("pph_amount") or 0))
    total_ap  = subtotal + ppn - pph  # yang harus dibayar ke vendor

    coa_expense = classification.get("coa_code", "6-1-001")
    lines = []

    # Debit: Beban / Prepaid / Aset
    lines.append(JournalLine(
        account_code=coa_expense,
        description=f"Invoice {ocr_result.get('invoice_no')} — {ocr_result.get('description', '')}",
        debit_idr=subtotal,
        vendor_id=vendor_id,
    ))

    # Debit: PPN Masukan (jika ada)
    if ppn > 0:
        lines.append(JournalLine(
            account_code="1-2-002",  # PPN Masukan — tambahkan ke COA jika belum ada
            description=f"PPN Masukan Invoice {ocr_result.get('invoice_no')}",
            debit_idr=ppn,
        ))

    # Credit: Hutang PPh 23 (jika kena withholding)
    if pph > 0:
        lines.append(JournalLine(
            account_code="2-1-002",  # Hutang PPh 23
            description=f"PPh {classification.get('pph_type', '23')} Invoice {ocr_result.get('invoice_no')}",
            credit_idr=pph,
            tax_code=classification.get("pph_type"),
            tax_amount=pph,
        ))

    # Credit: Hutang Usaha AP
    lines.append(JournalLine(
        account_code="2-1-001",  # Hutang Usaha
        description=f"AP Invoice {ocr_result.get('invoice_no')} — {ocr_result.get('vendor_name', '')}",
        credit_idr=total_ap,
        vendor_id=vendor_id,
    ))

    inv_date = ocr_result.get("invoice_date")
    try:
        from datetime import datetime
        journal_date = datetime.strptime(inv_date, "%Y-%m-%d").date() if inv_date else date.today()
    except Exception:
        journal_date = date.today()

    entry = JournalEntry(
        entity_id=entity_id,
        journal_type="AP",
        journal_date=journal_date,
        description=f"AP Invoice {ocr_result.get('invoice_no')} — {ocr_result.get('vendor_name', '')}",
        lines=lines,
        reference_no=ocr_result.get("invoice_no"),
        source="OCR",
        created_by=created_by,
    )

    result = engine.post_journal(entry)

    # Update ap_invoice dengan journal_id
    if result.get("success") and invoice_id:
        db.execute(
            text("""
                UPDATE ap_invoice SET
                    journal_id = :jid,
                    status = 'approved'
                WHERE id = :iid
            """),
            {"jid": result["journal_id"], "iid": str(invoice_id)}
        )
        db.commit()

    return result
