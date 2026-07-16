# modules/vendor_registration_router.py
# Vendor Registration — full ERP-grade workflow:
#   draft → submitted → internal_review → banking_validation → approval_l1 → approval_l2 → active
#
# Security design:
#   - Rekening bank dikunci (read-only) begitu vendor status = active
#   - Perubahan rekening hanya via vendor_bank_change_request, tidak bisa edit langsung
#   - Setiap step approval tersimpan di vendor_registration_approval (audit trail)

from uuid import UUID, uuid4
from datetime import date, datetime
from typing import Optional, List
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger

from core.database import get_db
from modules.auth import require_min_role

router = APIRouter(prefix="/vendor-registration", tags=["Vendor Registration"])


# ── Status machine ─────────────────────────────────────────────────────────────

WORKFLOW_STEPS = [
    "draft",
    "submitted",
    "internal_review",
    "banking_validation",
    "approval_l1",
    "approval_l2",
    "active",
]

APPROVAL_LEVEL_FOR_STATUS = {
    "internal_review":    "internal_review",
    "banking_validation": "banking_validation",
    "approval_l1":        "approval_l1",
    "approval_l2":        "approval_l2",
}

NEXT_STATUS = {
    "submitted":          "internal_review",
    "internal_review":    "banking_validation",
    "banking_validation": "approval_l1",
    "approval_l1":        "approval_l2",
    "approval_l2":        "active",
}

# Minimum role required per approval level
APPROVAL_ROLES = {
    "internal_review":    "finance",
    "banking_validation": "finance",
    "approval_l1":        "finance",   # Purchasing Manager (mapped ke finance role)
    "approval_l2":        "admin",     # Finance Controller
}


# ── Request models ─────────────────────────────────────────────────────────────

class VendorRegistrationCreate(BaseModel):
    entity_id:          UUID
    legal_name:         str
    trading_name:       Optional[str]   = None
    legal_entity_type:  str             = "PT"
    head_office_address: Optional[str] = None
    warehouse_address:  Optional[str]   = None
    gps_lat:            Optional[float] = None
    gps_lon:            Optional[float] = None
    contact_person:     Optional[str]   = None
    contact_title:      Optional[str]   = None
    contact_phone:      Optional[str]   = None
    contact_email:      str
    npwp:               Optional[str]   = None
    nib:                Optional[str]   = None
    nib_expiry:         Optional[date]  = None
    nppkp:              Optional[str]   = None
    is_pkp:             bool            = False
    kbli:               Optional[str]   = None
    payment_terms:      str             = "NET30"
    incoterms:          Optional[str]   = None
    order_currency:     str             = "IDR"
    min_order_value:    Optional[Decimal] = None
    lead_time_days:     Optional[int]   = None


class BankAccountCreate(BaseModel):
    bank_country:        str   = "ID"
    bank_name:           str
    bank_code:           Optional[str] = None
    swift_code:          Optional[str] = None
    branch_name:         Optional[str] = None
    account_no:          str
    account_holder_name: str
    currency:            str  = "IDR"
    is_primary:          bool = True


class ApprovalAction(BaseModel):
    action:  str          # "approve" | "reject" | "revision_needed"
    notes:   Optional[str] = None
    approved_by: str


class FinanceReviewUpdate(BaseModel):
    """Data yang diisi tim Finance/Procurement saat internal review."""
    ap_control_account:      Optional[str]     = None
    advance_payment_account: Optional[str]     = None
    vendor_category:         Optional[str]     = None
    default_pph_type:        Optional[str]     = None
    default_pph_rate:        Optional[float]   = None
    is_pkp_confirmed:        Optional[bool]    = None
    payment_terms:           Optional[str]     = None
    incoterms:               Optional[str]     = None
    lead_time_days:          Optional[int]     = None


class BankVerificationUpdate(BaseModel):
    """Banking validation step."""
    is_verified:         bool
    verification_method: str = "manual"  # manual | api_inquiry | dokumen
    notes:               Optional[str] = None
    verified_by:         str


class BankChangeRequest(BaseModel):
    new_bank_country:   str   = "ID"
    new_bank_name:      str
    new_bank_code:      Optional[str] = None
    new_swift_code:     Optional[str] = None
    new_branch_name:    Optional[str] = None
    new_account_no:     str
    new_account_holder: str
    new_currency:       str  = "IDR"
    reason:             str
    requested_by:       str


class BankChangeReview(BaseModel):
    action:      str  # "approve" | "reject"
    review_notes: Optional[str] = None
    reviewed_by: str


# ── Registration CRUD ──────────────────────────────────────────────────────────

@router.post("/", dependencies=[Depends(require_min_role("viewer"))])
def create_registration(req: VendorRegistrationCreate, db: Session = Depends(get_db)):
    """
    Vendor atau staf internal membuat form pendaftaran vendor baru.
    Status awal: draft. Dokumen belum wajib ada di sini.
    """
    entity = db.execute(
        text("SELECT id FROM entity WHERE id = :id"), {"id": str(req.entity_id)}
    ).fetchone()
    if not entity:
        raise HTTPException(404, f"Entity {req.entity_id} tidak ditemukan")

    reg_no = _generate_reg_no(db)
    reg_id = uuid4()

    db.execute(
        text("""
            INSERT INTO vendor_registration (
                id, entity_id, registration_no, status,
                legal_name, trading_name, legal_entity_type,
                head_office_address, warehouse_address,
                gps_lat, gps_lon,
                contact_person, contact_title, contact_phone, contact_email,
                npwp, nib, nib_expiry, nppkp, is_pkp, kbli,
                payment_terms, incoterms, order_currency, min_order_value, lead_time_days,
                created_at, updated_at
            ) VALUES (
                :id, :eid, :reg_no, 'draft',
                :legal_name, :trading_name, :entity_type,
                :addr_hq, :addr_wh,
                :lat, :lon,
                :contact_person, :contact_title, :contact_phone, :contact_email,
                :npwp, :nib, :nib_expiry, :nppkp, :is_pkp, :kbli,
                :pay_terms, :incoterms, :order_ccy, :min_order, :lead_time,
                NOW(), NOW()
            )
        """),
        {
            "id": str(reg_id), "eid": str(req.entity_id), "reg_no": reg_no,
            "legal_name": req.legal_name, "trading_name": req.trading_name,
            "entity_type": req.legal_entity_type,
            "addr_hq": req.head_office_address, "addr_wh": req.warehouse_address,
            "lat": req.gps_lat, "lon": req.gps_lon,
            "contact_person": req.contact_person, "contact_title": req.contact_title,
            "contact_phone": req.contact_phone, "contact_email": req.contact_email,
            "npwp": req.npwp, "nib": req.nib, "nib_expiry": req.nib_expiry,
            "nppkp": req.nppkp, "is_pkp": req.is_pkp, "kbli": req.kbli,
            "pay_terms": req.payment_terms, "incoterms": req.incoterms,
            "order_ccy": req.order_currency,
            "min_order": float(req.min_order_value) if req.min_order_value else None,
            "lead_time": req.lead_time_days,
        }
    )
    db.commit()

    logger.info(f"Vendor registration created: {reg_no} | {req.legal_name}")
    return {"registration_id": str(reg_id), "registration_no": reg_no, "status": "draft"}


@router.get("/{reg_id}")
def get_registration(reg_id: str, db: Session = Depends(get_db)):
    """Detail pendaftaran vendor + status setiap step approval + data rekening."""
    row = db.execute(
        text("SELECT * FROM vw_vendor_registration_summary WHERE id = :id"),
        {"id": reg_id}
    ).fetchone()
    if not row:
        raise HTTPException(404, "Registration tidak ditemukan")

    # Detail approvals
    approvals = db.execute(
        text("""
            SELECT approval_level, status, approved_by, approved_at, notes
            FROM vendor_registration_approval
            WHERE registration_id = :id
            ORDER BY created_at
        """),
        {"id": reg_id}
    ).fetchall()

    result = dict(row._mapping)
    result["approvals"] = [dict(a._mapping) for a in approvals]
    return result


@router.get("/")
def list_registrations(
    entity_id: Optional[str]  = None,
    status:    Optional[str]  = None,
    limit:     int             = Query(default=50, le=200),
    db: Session = Depends(get_db),
):
    """Daftar pendaftaran vendor dengan filter status."""
    filters = []
    params: dict = {"limit": limit}

    if entity_id:
        filters.append("entity_id = :eid")
        params["eid"] = entity_id
    if status:
        filters.append("status = :status")
        params["status"] = status

    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    rows = db.execute(
        text(f"""
            SELECT * FROM vw_vendor_registration_summary
            {where}
            ORDER BY created_at DESC
            LIMIT :limit
        """),
        params
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Document checklist ─────────────────────────────────────────────────────────

@router.patch("/{reg_id}/docs", dependencies=[Depends(require_min_role("viewer"))])
def update_doc_checklist(
    reg_id: str,
    doc_npwp:      bool = False,
    doc_nib:       bool = False,
    doc_bank_book: bool = False,
    doc_deed:      bool = False,
    db: Session = Depends(get_db),
):
    """Update checklist dokumen yang sudah diupload."""
    _require_registration(db, reg_id, allowed_statuses=["draft", "submitted"])
    db.execute(
        text("""
            UPDATE vendor_registration SET
                doc_npwp_uploaded      = :npwp,
                doc_nib_uploaded       = :nib,
                doc_bank_book_uploaded = :bank,
                doc_deed_uploaded      = :deed,
                updated_at             = NOW()
            WHERE id = :id
        """),
        {"id": reg_id, "npwp": doc_npwp, "nib": doc_nib, "bank": doc_bank_book, "deed": doc_deed}
    )
    db.commit()
    return {"message": "Checklist dokumen diperbarui"}


# ── Bank account management ────────────────────────────────────────────────────

@router.post("/{reg_id}/bank-accounts", dependencies=[Depends(require_min_role("viewer"))])
def add_bank_account(
    reg_id: str,
    req: BankAccountCreate,
    db: Session = Depends(get_db),
):
    """
    Tambah rekening bank ke pendaftaran vendor.
    Rekening belum dikunci — masih bisa diubah saat status draft/submitted/internal_review.
    """
    reg = _require_registration(
        db, reg_id,
        allowed_statuses=["draft", "submitted", "internal_review", "banking_validation"]
    )

    # Jika is_primary=True, reset primary flag rekening lain
    if req.is_primary:
        db.execute(
            text("UPDATE vendor_bank_account SET is_primary = FALSE WHERE registration_id = :rid"),
            {"rid": reg_id}
        )

    acc_id = uuid4()
    db.execute(
        text("""
            INSERT INTO vendor_bank_account (
                id, registration_id,
                bank_country, bank_name, bank_code, swift_code, branch_name,
                account_no, account_holder_name, currency,
                is_primary, is_active, created_at, updated_at
            ) VALUES (
                :id, :rid,
                :country, :bank_name, :bank_code, :swift, :branch,
                :acc_no, :holder, :ccy,
                :primary, TRUE, NOW(), NOW()
            )
        """),
        {
            "id": str(acc_id), "rid": reg_id,
            "country": req.bank_country, "bank_name": req.bank_name,
            "bank_code": req.bank_code, "swift": req.swift_code, "branch": req.branch_name,
            "acc_no": req.account_no, "holder": req.account_holder_name,
            "ccy": req.currency, "primary": req.is_primary,
        }
    )
    db.commit()
    return {"bank_account_id": str(acc_id), "message": "Rekening ditambahkan"}


@router.get("/{reg_id}/bank-accounts")
def list_bank_accounts(reg_id: str, db: Session = Depends(get_db)):
    """Daftar rekening bank untuk registrasi ini."""
    rows = db.execute(
        text("""
            SELECT id, bank_country, bank_name, bank_code, swift_code, branch_name,
                   account_no, account_holder_name, currency,
                   is_verified, verification_method, is_locked, is_primary, is_active,
                   bank_book_path, created_at
            FROM vendor_bank_account
            WHERE registration_id = :rid
            ORDER BY is_primary DESC, created_at
        """),
        {"rid": reg_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Workflow: submit & approve/reject ──────────────────────────────────────────

@router.post("/{reg_id}/submit", dependencies=[Depends(require_min_role("viewer"))])
def submit_registration(reg_id: str, submitted_by: str, db: Session = Depends(get_db)):
    """
    Vendor atau staf submit form pendaftaran untuk direview.
    Validasi: semua dokumen wajib sudah dicentang, minimal 1 rekening sudah ada.
    """
    reg = _require_registration(db, reg_id, allowed_statuses=["draft"])

    # Cek kelengkapan dokumen
    missing = []
    if not reg["doc_npwp_uploaded"]:       missing.append("NPWP")
    if not reg["doc_nib_uploaded"]:        missing.append("NIB")
    if not reg["doc_bank_book_uploaded"]:  missing.append("Buku Tabungan/Koran")
    if not reg["doc_deed_uploaded"]:       missing.append("Akta/KTP")
    if missing:
        raise HTTPException(400, f"Dokumen belum diupload: {', '.join(missing)}")

    # Cek minimal 1 rekening
    bank_count = db.execute(
        text("SELECT COUNT(*) FROM vendor_bank_account WHERE registration_id = :rid AND is_active = TRUE"),
        {"rid": reg_id}
    ).scalar()
    if not bank_count:
        raise HTTPException(400, "Minimal 1 rekening bank harus ditambahkan sebelum submit")

    db.execute(
        text("""
            UPDATE vendor_registration SET
                status       = 'submitted',
                submitted_by = :by,
                submitted_at = NOW(),
                updated_at   = NOW()
            WHERE id = :id
        """),
        {"id": reg_id, "by": submitted_by}
    )
    # Buat record approval step pertama
    _create_approval_record(db, reg_id, "internal_review")
    db.commit()

    logger.info(f"Registration {reg_id} submitted by {submitted_by}")
    return {"status": "submitted", "next_step": "internal_review (Procurement & Tax)"}


@router.post("/{reg_id}/review/internal", dependencies=[Depends(require_min_role("finance"))])
def internal_review(
    reg_id:  str,
    action:  ApprovalAction,
    updates: Optional[FinanceReviewUpdate] = None,
    db: Session = Depends(get_db),
):
    """
    Step 1 — Procurement & Tax mereview kelengkapan dokumen, NPWP, NIB.
    Finance juga mengisi data akuntansi dan pajak default vendor.
    """
    reg = _require_registration(db, reg_id, allowed_statuses=["submitted", "internal_review"])
    _process_approval(db, reg_id, "internal_review", action)

    if action.action == "approve":
        # Update data keuangan/pajak jika diisi Finance
        if updates:
            _apply_finance_review_updates(db, reg_id, updates)

        db.execute(
            text("""
                UPDATE vendor_registration
                SET status = 'banking_validation', updated_at = NOW()
                WHERE id = :id
            """),
            {"id": reg_id}
        )
        _create_approval_record(db, reg_id, "banking_validation")
        db.commit()
        return {"status": "banking_validation", "next_step": "Banking validation (Finance)"}

    db.commit()
    return {"status": reg["status"], "action": action.action}


@router.post("/{reg_id}/review/banking", dependencies=[Depends(require_min_role("finance"))])
def banking_validation(
    reg_id:    str,
    bank_id:   str,
    verify:    BankVerificationUpdate,
    action:    ApprovalAction,
    db: Session = Depends(get_db),
):
    """
    Step 2 — Finance memverifikasi nomor rekening.
    Bisa manual (cek ke bank) atau via Bank Inquiry API.
    Rekening harus diverifikasi sebelum approval bisa dilanjutkan.
    """
    _require_registration(db, reg_id, allowed_statuses=["banking_validation"])

    # Update status verifikasi rekening
    db.execute(
        text("""
            UPDATE vendor_bank_account SET
                is_verified          = :verified,
                verified_by          = :by,
                verified_at          = NOW(),
                verification_method  = :method,
                verification_notes   = :notes,
                updated_at           = NOW()
            WHERE id = :bid AND registration_id = :rid
        """),
        {
            "bid": bank_id, "rid": reg_id,
            "verified": verify.is_verified,
            "by": verify.verified_by,
            "method": verify.verification_method,
            "notes": verify.notes,
        }
    )

    if not verify.is_verified:
        _process_approval(db, reg_id, "banking_validation",
                          ApprovalAction(action="rejected",
                                         notes=f"Rekening tidak terverifikasi: {verify.notes}",
                                         approved_by=verify.verified_by))
        db.execute(
            text("UPDATE vendor_registration SET status='rejected', updated_at=NOW() WHERE id=:id"),
            {"id": reg_id}
        )
        db.commit()
        return {"status": "rejected", "reason": "Rekening bank tidak terverifikasi"}

    _process_approval(db, reg_id, "banking_validation", action)

    if action.action == "approve":
        db.execute(
            text("""
                UPDATE vendor_registration
                SET status = 'approval_l1', updated_at = NOW()
                WHERE id = :id
            """),
            {"id": reg_id}
        )
        _create_approval_record(db, reg_id, "approval_l1")
        db.commit()
        return {"status": "approval_l1", "next_step": "Level 1: Purchasing Manager"}

    db.commit()
    return {"status": "banking_validation", "action": action.action}


@router.post("/{reg_id}/review/approve-l1", dependencies=[Depends(require_min_role("finance"))])
def approval_l1(reg_id: str, action: ApprovalAction, db: Session = Depends(get_db)):
    """
    Step 3 — Purchasing Manager: validasi kapabilitas vendor.
    Memeriksa: kategori usaha, incoterms, payment terms, lead time.
    """
    _require_registration(db, reg_id, allowed_statuses=["approval_l1"])
    _process_approval(db, reg_id, "approval_l1", action)

    if action.action == "approve":
        db.execute(
            text("""
                UPDATE vendor_registration
                SET status = 'approval_l2', updated_at = NOW()
                WHERE id = :id
            """),
            {"id": reg_id}
        )
        _create_approval_record(db, reg_id, "approval_l2")
        db.commit()
        return {"status": "approval_l2", "next_step": "Level 2: Finance Controller (final)"}

    if action.action == "rejected":
        db.execute(
            text("""
                UPDATE vendor_registration
                SET status = 'rejected', rejection_reason = :reason, updated_at = NOW()
                WHERE id = :id
            """),
            {"id": reg_id, "reason": action.notes}
        )
    db.commit()
    return {"status": "rejected" if action.action == "rejected" else "approval_l1"}


@router.post("/{reg_id}/review/approve-l2", dependencies=[Depends(require_min_role("admin"))])
def approval_l2(reg_id: str, action: ApprovalAction, db: Session = Depends(get_db)):
    """
    Step 4 — Finance Controller: validasi final rekening & perpajakan.
    Jika approve → sistem otomatis aktivasi vendor dan kunci rekening.
    """
    _require_registration(db, reg_id, allowed_statuses=["approval_l2"])
    _process_approval(db, reg_id, "approval_l2", action)

    if action.action == "approve":
        vendor_id = _activate_vendor(db, reg_id, activated_by=action.approved_by)
        db.commit()
        logger.info(f"Registration {reg_id} activated → vendor {vendor_id}")
        return {
            "status":    "active",
            "vendor_id": str(vendor_id),
            "message":   "Vendor aktif. Rekening bank dikunci — perubahan hanya via change request.",
        }

    if action.action == "rejected":
        db.execute(
            text("""
                UPDATE vendor_registration
                SET status = 'rejected', rejection_reason = :reason, updated_at = NOW()
                WHERE id = :id
            """),
            {"id": reg_id, "reason": action.notes}
        )
    db.commit()
    return {"status": "rejected" if action.action == "rejected" else "approval_l2"}


# ── Bank Change Request (rekening aktif tidak bisa diedit langsung) ───────────

@router.post("/vendor/{vendor_id}/bank-change-request",
             dependencies=[Depends(require_min_role("viewer"))])
def request_bank_change(vendor_id: str, req: BankChangeRequest, db: Session = Depends(get_db)):
    """
    Ajukan perubahan rekening bank vendor yang sudah aktif.
    Rekening lama tetap aktif sampai request disetujui Finance Controller.
    """
    vendor = db.execute(
        text("SELECT id, registration_status, is_bank_locked FROM vendor WHERE id = :id"),
        {"id": vendor_id}
    ).fetchone()
    if not vendor:
        raise HTTPException(404, "Vendor tidak ditemukan")
    if not vendor.is_bank_locked:
        raise HTTPException(400, "Rekening vendor belum dikunci — edit langsung masih diperbolehkan")

    current_bank = db.execute(
        text("""
            SELECT id FROM vendor_bank_account
            WHERE vendor_id = :vid AND is_primary = TRUE AND is_active = TRUE
        """),
        {"vid": vendor_id}
    ).fetchone()
    if not current_bank:
        raise HTTPException(404, "Rekening bank primary tidak ditemukan")

    req_id = uuid4()
    db.execute(
        text("""
            INSERT INTO vendor_bank_change_request (
                id, vendor_id, current_bank_id,
                new_bank_country, new_bank_name, new_bank_code, new_swift_code,
                new_branch_name, new_account_no, new_account_holder, new_currency,
                reason, status, requested_by, requested_at
            ) VALUES (
                :id, :vid, :bid,
                :country, :bank_name, :bank_code, :swift,
                :branch, :acc_no, :holder, :ccy,
                :reason, 'pending', :by, NOW()
            )
        """),
        {
            "id": str(req_id), "vid": vendor_id, "bid": str(current_bank.id),
            "country": req.new_bank_country, "bank_name": req.new_bank_name,
            "bank_code": req.new_bank_code, "swift": req.new_swift_code,
            "branch": req.new_branch_name, "acc_no": req.new_account_no,
            "holder": req.new_account_holder, "ccy": req.new_currency,
            "reason": req.reason, "by": req.requested_by,
        }
    )
    db.commit()
    logger.info(f"Bank change request {req_id} for vendor {vendor_id}")
    return {
        "change_request_id": str(req_id),
        "status":            "pending",
        "message":           "Request perubahan rekening dikirim ke Finance Controller untuk disetujui.",
    }


@router.get("/vendor/{vendor_id}/bank-change-requests",
            dependencies=[Depends(require_min_role("viewer"))])
def list_bank_change_requests(vendor_id: str, db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT r.*, b.bank_name AS current_bank_name, b.account_no AS current_account_no
            FROM vendor_bank_change_request r
            JOIN vendor_bank_account b ON b.id = r.current_bank_id
            WHERE r.vendor_id = :vid
            ORDER BY r.requested_at DESC
        """),
        {"vid": vendor_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/vendor/{vendor_id}/bank-change-request/{req_id}/review",
             dependencies=[Depends(require_min_role("admin"))])
def review_bank_change_request(
    vendor_id: str,
    req_id:    str,
    review:    BankChangeReview,
    db: Session = Depends(get_db),
):
    """
    Finance Controller menyetujui atau menolak perubahan rekening.
    Jika approve: rekening lama di-deactivate, rekening baru dibuat dan dikunci.
    """
    change_req = db.execute(
        text("""
            SELECT * FROM vendor_bank_change_request
            WHERE id = :id AND vendor_id = :vid AND status = 'pending'
        """),
        {"id": req_id, "vid": vendor_id}
    ).fetchone()
    if not change_req:
        raise HTTPException(404, "Change request tidak ditemukan atau sudah diproses")

    db.execute(
        text("""
            UPDATE vendor_bank_change_request SET
                status       = :status,
                reviewed_by  = :by,
                reviewed_at  = NOW(),
                review_notes = :notes
            WHERE id = :id
        """),
        {"id": req_id, "status": review.action + "d",  # approved / rejected
         "by": review.reviewed_by, "notes": review.review_notes}
    )

    if review.action == "approve":
        # Non-aktifkan rekening lama
        db.execute(
            text("""
                UPDATE vendor_bank_account
                SET is_active = FALSE, is_primary = FALSE, updated_at = NOW()
                WHERE id = :bid
            """),
            {"bid": str(change_req.current_bank_id)}
        )

        # Buat rekening baru langsung dalam keadaan locked dan verified
        new_bank_id = uuid4()
        db.execute(
            text("""
                INSERT INTO vendor_bank_account (
                    id, vendor_id,
                    bank_country, bank_name, bank_code, swift_code, branch_name,
                    account_no, account_holder_name, currency,
                    is_verified, verification_method, verified_by, verified_at,
                    is_locked, locked_by, locked_at,
                    is_primary, is_active, created_at, updated_at
                ) VALUES (
                    :id, :vid,
                    :country, :bank_name, :bank_code, :swift, :branch,
                    :acc_no, :holder, :ccy,
                    TRUE, 'change_request', :by, NOW(),
                    TRUE, :by, NOW(),
                    TRUE, TRUE, NOW(), NOW()
                )
            """),
            {
                "id": str(new_bank_id), "vid": vendor_id,
                "country": change_req.new_bank_country,
                "bank_name": change_req.new_bank_name,
                "bank_code": change_req.new_bank_code,
                "swift": change_req.new_swift_code,
                "branch": change_req.new_branch_name,
                "acc_no": change_req.new_account_no,
                "holder": change_req.new_account_holder,
                "ccy": change_req.new_currency,
                "by": review.reviewed_by,
            }
        )
        db.commit()
        logger.info(f"Bank change request {req_id} approved — new bank {new_bank_id}")
        return {
            "status":       "approved",
            "new_bank_id":  str(new_bank_id),
            "message":      "Rekening baru aktif dan dikunci.",
        }

    db.commit()
    return {"status": "rejected"}


# ── Vendor rating update (auto atau manual) ────────────────────────────────────

@router.patch("/vendor/{vendor_id}/rating", dependencies=[Depends(require_min_role("finance"))])
def update_vendor_rating(
    vendor_id:  str,
    rating:     float = Query(..., ge=0.0, le=10.0),
    updated_by: str   = Query(...),
    db: Session = Depends(get_db),
):
    """Update skor performa vendor (0.0–10.0). Basis: ketepatan waktu, kualitas, harga."""
    result = db.execute(
        text("""
            UPDATE vendor SET vendor_rating = :rating, updated_at = NOW()
            WHERE id = :id
        """),
        {"rating": rating, "id": vendor_id}
    )
    if result.rowcount == 0:
        raise HTTPException(404, "Vendor tidak ditemukan")
    db.commit()
    return {"vendor_id": vendor_id, "vendor_rating": rating, "updated_by": updated_by}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _generate_reg_no(db: Session) -> str:
    now = datetime.now()
    prefix = f"VR/{now.year}/{now.month:02d}"
    row = db.execute(
        text("""
            SELECT COUNT(*) FROM vendor_registration
            WHERE registration_no LIKE :prefix
        """),
        {"prefix": f"{prefix}/%"}
    ).scalar()
    return f"{prefix}/{(row or 0) + 1:04d}"


def _require_registration(db: Session, reg_id: str, allowed_statuses: list) -> dict:
    row = db.execute(
        text("SELECT * FROM vendor_registration WHERE id = :id"), {"id": reg_id}
    ).fetchone()
    if not row:
        raise HTTPException(404, "Registration tidak ditemukan")
    d = dict(row._mapping)
    if d["status"] not in allowed_statuses:
        raise HTTPException(400,
            f"Status '{d['status']}' tidak valid untuk aksi ini. "
            f"Harus salah satu dari: {', '.join(allowed_statuses)}"
        )
    return d


def _create_approval_record(db: Session, reg_id: str, level: str):
    db.execute(
        text("""
            INSERT INTO vendor_registration_approval (id, registration_id, approval_level, status)
            VALUES (:id, :rid, :level, 'pending')
        """),
        {"id": str(uuid4()), "rid": reg_id, "level": level}
    )


def _process_approval(db: Session, reg_id: str, level: str, action: ApprovalAction):
    if action.action not in ("approve", "reject", "rejected", "revision_needed"):
        raise HTTPException(400, "action harus: approve | reject | revision_needed")
    actual_status = "rejected" if action.action == "reject" else action.action
    db.execute(
        text("""
            UPDATE vendor_registration_approval SET
                status      = :status,
                approved_by = :by,
                approved_at = NOW(),
                notes       = :notes
            WHERE registration_id = :rid AND approval_level = :level
        """),
        {
            "rid": reg_id, "level": level,
            "status": actual_status,
            "by": action.approved_by,
            "notes": action.notes,
        }
    )


def _apply_finance_review_updates(db: Session, reg_id: str, upd: FinanceReviewUpdate):
    fields = {k: v for k, v in upd.model_dump().items() if v is not None}
    if not fields:
        return
    set_parts = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = reg_id
    db.execute(
        text(f"UPDATE vendor_registration SET {set_parts}, updated_at = NOW() WHERE id = :id"),
        fields
    )


def _activate_vendor(db: Session, reg_id: str, activated_by: str) -> UUID:
    """
    Buat record vendor aktif dari data registrasi, lalu kunci semua rekening bank.
    """
    reg = db.execute(
        text("SELECT * FROM vendor_registration WHERE id = :id"),
        {"id": reg_id}
    ).fetchone()
    r = dict(reg._mapping)

    vendor_id = uuid4()
    # Generate vendor_code sequential
    entity_id = r["entity_id"]
    count = db.execute(
        text("SELECT COUNT(*) FROM vendor WHERE entity_id = :eid"), {"eid": entity_id}
    ).scalar()
    vendor_code = f"VND-{(count or 0) + 1:05d}"

    db.execute(
        text("""
            INSERT INTO vendor (
                id, entity_id, vendor_code, vendor_name, npwp, nib, kbli,
                tax_status, is_foreign, country,
                email, phone, address,
                legal_name, trading_name, legal_entity_type,
                head_office_address, warehouse_address, gps_lat, gps_lon,
                contact_person, contact_title, contact_phone, contact_email,
                nib_expiry, nppkp, is_pkp,
                ap_control_account, advance_payment_account,
                vendor_category, default_pph_type, default_pph_rate,
                payment_terms, incoterms, order_currency, min_order_value, lead_time_days,
                registration_status, is_bank_locked, activated_at, activated_by,
                scrape_status, created_at, updated_at
            ) VALUES (
                :id, :eid, :vcode, :vname, :npwp, :nib, :kbli,
                'unknown', FALSE, 'ID',
                :email, :phone, :addr,
                :legal_name, :trading_name, :entity_type,
                :hq, :wh, :lat, :lon,
                :contact_person, :contact_title, :contact_phone, :contact_email,
                :nib_expiry, :nppkp, :is_pkp,
                :ap_acc, :adv_acc,
                :category, :pph_type, :pph_rate,
                :pay_terms, :incoterms, :order_ccy, :min_order, :lead_time,
                'active', TRUE, NOW(), :activated_by,
                'manual', NOW(), NOW()
            )
        """),
        {
            "id": str(vendor_id), "eid": entity_id,
            "vcode": vendor_code, "vname": r["legal_name"],
            "npwp": r["npwp"], "nib": r["nib"], "kbli": r["kbli"],
            "email": r["contact_email"], "phone": r["contact_phone"], "addr": r["head_office_address"],
            "legal_name": r["legal_name"], "trading_name": r["trading_name"],
            "entity_type": r["legal_entity_type"],
            "hq": r["head_office_address"], "wh": r["warehouse_address"],
            "lat": r["gps_lat"], "lon": r["gps_lon"],
            "contact_person": r["contact_person"], "contact_title": r["contact_title"],
            "contact_phone": r["contact_phone"], "contact_email": r["contact_email"],
            "nib_expiry": r["nib_expiry"], "nppkp": r["nppkp"], "is_pkp": r["is_pkp"],
            "ap_acc": r.get("ap_control_account", "2-1-001"),
            "adv_acc": r.get("advance_payment_account", "1-3-001"),
            "category": r.get("vendor_category"), "pph_type": r.get("default_pph_type"),
            "pph_rate": r.get("default_pph_rate"),
            "pay_terms": r.get("payment_terms", "NET30"),
            "incoterms": r.get("incoterms"), "order_ccy": r.get("order_currency", "IDR"),
            "min_order": r.get("min_order_value"), "lead_time": r.get("lead_time_days"),
            "activated_by": activated_by,
        }
    )

    # Assign vendor_id ke rekening bank + lock semua rekening
    db.execute(
        text("""
            UPDATE vendor_bank_account SET
                vendor_id   = :vid,
                is_locked   = TRUE,
                locked_at   = NOW(),
                locked_by   = :by,
                updated_at  = NOW()
            WHERE registration_id = :rid
        """),
        {"vid": str(vendor_id), "rid": reg_id, "by": activated_by}
    )

    # Update registration: set vendor_id + status active
    db.execute(
        text("""
            UPDATE vendor_registration SET
                status     = 'active',
                vendor_id  = :vid,
                updated_at = NOW()
            WHERE id = :rid
        """),
        {"vid": str(vendor_id), "rid": reg_id}
    )

    return vendor_id
