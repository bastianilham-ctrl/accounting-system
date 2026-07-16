# modules/journal_entry_router.py
# Manual Journal Entry REST API
#
# Endpoint groups:
#   /journal-entries           — CRUD + workflow actions
#   /journal-entries/periods   — accounting period management
#   /journal-entries/matrix    — approval matrix setup
#   /journal-entries/reports   — pending queue, audit trail

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from sqlalchemy import text

from core.database import get_db
from modules.auth import require_min_role, get_current_active_user
from modules.journal_entry_engine import (
    JournalEntryEngine,
    JELineInput,
    _get_required_role,
)

router = APIRouter(prefix="/journal-entries", tags=["Journal Entry — Workflow & GL"])

_viewer  = [Depends(require_min_role("viewer"))]
_finance = [Depends(require_min_role("finance"))]
_admin   = [Depends(require_min_role("admin"))]


# ── Request Schemas ────────────────────────────────────────────────────────────

class JELineRequest(BaseModel):
    account_code:  str
    debit_amount:  float = Field(0, ge=0)
    credit_amount: float = Field(0, ge=0)
    description:   Optional[str] = None
    cost_center:   Optional[str] = None
    project_id:    Optional[str] = None
    vendor_id:     Optional[str] = None
    tax_code:      Optional[str] = None
    tax_amount:    float = Field(0, ge=0)

    @field_validator("debit_amount", "credit_amount")
    @classmethod
    def not_negative(cls, v):
        if v < 0:
            raise ValueError("Nilai tidak boleh negatif")
        return v


class JECreateRequest(BaseModel):
    entity_id:      str
    journal_date:   date
    journal_type:   str = Field("general",
                        pattern="^(general|adjustment|accrual|prepaid|depreciation|provision|write_off|closing)$")
    description:    str = Field(..., min_length=3)
    currency:       str = Field("IDR", min_length=3, max_length=3)
    exchange_rate:  float = Field(1.0, gt=0,
                        description="Rate: 1 unit currency = exchange_rate IDR. Untuk IDR = 1.")
    reference_no:   Optional[str] = None
    attachment_url: Optional[str] = None
    lines:          list[JELineRequest] = Field(..., min_length=2)


class JEUpdateRequest(BaseModel):
    journal_date:   Optional[date]   = None
    description:    Optional[str]    = None
    currency:       Optional[str]    = None
    exchange_rate:  Optional[float]  = Field(None, gt=0)
    lines:          Optional[list[JELineRequest]] = None


class RejectRequest(BaseModel):
    reason: str = Field(..., min_length=5)


class ReversalRequest(BaseModel):
    reversal_date: date
    reason:        str = Field(..., min_length=5)
    fast_track:    bool = Field(False,
                       description="True = reversal langsung approved, tinggal POST. Khusus admin.")


class ApprovalMatrixEntry(BaseModel):
    entity_id:      str
    level:          int = Field(..., ge=1)
    threshold_name: str
    journal_type:   Optional[str] = Field(None,
                        description="Isi untuk rule khusus tipe; kosongkan untuk rule berbasis nominal")
    min_amount:     float = Field(0, ge=0)
    max_amount:     Optional[float] = None
    required_role:  str = Field(..., pattern="^(finance|admin)$")


class PeriodAction(BaseModel):
    entity_id: str
    year:      int
    month:     int = Field(..., ge=1, le=12)
    notes:     Optional[str] = None


# ── Helper: konversi JELineRequest → JELineInput ───────────────────────────────

def _to_line_input(r: JELineRequest) -> JELineInput:
    return JELineInput(
        account_code  = r.account_code,
        debit_amount  = Decimal(str(r.debit_amount)),
        credit_amount = Decimal(str(r.credit_amount)),
        description   = r.description,
        cost_center   = r.cost_center,
        project_id    = r.project_id,
        vendor_id     = r.vendor_id,
        tax_code      = r.tax_code,
        tax_amount    = Decimal(str(r.tax_amount)),
    )


# ── Journal Entry CRUD ─────────────────────────────────────────────────────────

@router.post("", summary="Buat Journal Entry baru (status: Draft)")
def create_journal_entry(
    payload: JECreateRequest,
    db:      Session = Depends(get_db),
    user=    Depends(get_current_active_user),
):
    """
    Buat jurnal dalam status Draft.
    Belum ada dampak ke Laporan Keuangan — angka baru masuk setelah POST ke GL.
    """
    # Validate currency — IDR wajib exchange_rate = 1
    if payload.currency.upper() == "IDR" and payload.exchange_rate != 1.0:
        raise HTTPException(400, "Currency IDR harus menggunakan exchange_rate = 1")

    # Validate account codes exist
    acc_codes = [ln.account_code for ln in payload.lines]
    existing = db.execute(
        text("""
            SELECT account_code FROM chart_of_accounts
            WHERE entity_id = :eid AND account_code = ANY(:codes) AND is_active = TRUE
        """),
        {"eid": payload.entity_id, "codes": acc_codes}
    ).fetchall()
    existing_codes = {r.account_code for r in existing}
    missing = [c for c in acc_codes if c not in existing_codes]
    if missing:
        raise HTTPException(400, f"Kode akun tidak ditemukan atau tidak aktif: {', '.join(missing)}")

    engine = JournalEntryEngine(db)
    try:
        result = engine.create_draft(
            entity_id     = payload.entity_id,
            journal_date  = payload.journal_date,
            journal_type  = payload.journal_type,
            description   = payload.description,
            lines         = [_to_line_input(ln) for ln in payload.lines],
            currency      = payload.currency.upper(),
            exchange_rate = payload.exchange_rate,
            reference_no  = payload.reference_no,
            attachment_url = payload.attachment_url,
            created_by    = user.get("email", "system"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.get("", dependencies=_viewer, summary="List journal entries")
def list_journal_entries(
    entity_id:    str = Query(...),
    status:       Optional[str] = Query(None),
    journal_type: Optional[str] = Query(None),
    currency:     Optional[str] = Query(None),
    year:         Optional[int] = Query(None),
    month:        Optional[int] = Query(None, ge=1, le=12),
    created_by:   Optional[str] = Query(None),
    db:           Session = Depends(get_db),
):
    conds = ["je.entity_id = :eid"]
    params: dict = {"eid": entity_id}
    if status:
        conds.append("je.status = :status")
        params["status"] = status
    if journal_type:
        conds.append("je.journal_type = :jtype")
        params["jtype"] = journal_type
    if currency:
        conds.append("je.currency = :cur")
        params["cur"] = currency.upper()
    if year:
        conds.append("je.period_year = :yr")
        params["yr"] = year
    if month:
        conds.append("je.period_month = :mo")
        params["mo"] = month
    if created_by:
        conds.append("je.created_by ILIKE :cby")
        params["cby"] = f"%{created_by}%"

    where = " AND ".join(conds)
    rows = db.execute(
        text(f"""
            SELECT
                je.id, je.entry_no, je.journal_date, je.journal_type, je.status,
                je.currency, je.exchange_rate,
                je.total_debit_currency, je.total_credit_currency,
                je.total_debit_idr, je.total_credit_idr,
                je.description, je.reference_no,
                je.required_approval_role, je.submitted_by, je.submitted_at,
                je.reviewed_by, je.reviewed_at,
                je.posted_by, je.posted_at,
                je.rejection_reason,
                je.is_reversal, je.reversal_of_id,
                je.created_by, je.created_at,
                (SELECT COUNT(*) FROM journal_entry_line jel WHERE jel.entry_id = je.id) AS line_count
            FROM journal_entry je
            WHERE {where}
            ORDER BY je.journal_date DESC, je.entry_no DESC
        """),
        params
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/pending-my-review", dependencies=_finance,
            summary="List jurnal yang menunggu review saya")
def list_pending_my_review(
    entity_id: str = Query(...),
    db:        Session = Depends(get_db),
    user=      Depends(require_min_role("finance")),
):
    """Tampilkan jurnal pending_approval yang sesuai dengan role reviewer."""
    user_role = user.get("role", "finance")
    role_cond = "je.required_approval_role = :role"
    if user_role == "admin":
        # Admin bisa review semua, termasuk yang butuh 'finance'
        role_cond = "je.required_approval_role IN ('finance', 'admin')"

    rows = db.execute(
        text(f"""
            SELECT je.*,
                   (SELECT COUNT(*) FROM journal_entry_line jel WHERE jel.entry_id = je.id) AS line_count,
                   EXTRACT(DAYS FROM (NOW() - je.submitted_at)) AS days_pending
            FROM journal_entry je
            WHERE je.entity_id = :eid
              AND je.status    = 'pending_approval'
              AND {role_cond}
            ORDER BY je.submitted_at ASC
        """),
        {"eid": entity_id, "role": user_role}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/{entry_id}", dependencies=_viewer, summary="Detail journal entry + lines + log")
def get_journal_entry(entry_id: str, db: Session = Depends(get_db)):
    entry = db.execute(
        text("SELECT * FROM journal_entry WHERE id = :id"),
        {"id": entry_id}
    ).fetchone()
    if not entry:
        raise HTTPException(404, "Journal entry tidak ditemukan")

    lines = db.execute(
        text("SELECT * FROM journal_entry_line WHERE entry_id = :id ORDER BY line_no"),
        {"id": entry_id}
    ).fetchall()

    approval_log = db.execute(
        text("SELECT * FROM journal_approval_log WHERE entry_id = :id ORDER BY acted_at"),
        {"id": entry_id}
    ).fetchall()

    # Ambil info jurnal terkait (reversal / reversed)
    related = None
    if entry.reversal_of_id:
        rel = db.execute(
            text("SELECT id, entry_no, status FROM journal_entry WHERE id = :id"),
            {"id": str(entry.reversal_of_id)}
        ).fetchone()
        related = {"type": "reversal_of", **dict(rel._mapping)} if rel else None
    elif entry.reversed_by_id:
        rel = db.execute(
            text("SELECT id, entry_no, status FROM journal_entry WHERE id = :id"),
            {"id": str(entry.reversed_by_id)}
        ).fetchone()
        related = {"type": "reversed_by", **dict(rel._mapping)} if rel else None

    return {
        **dict(entry._mapping),
        "lines":        [dict(r._mapping) for r in lines],
        "approval_log": [dict(r._mapping) for r in approval_log],
        "related_entry": related,
    }


@router.put("/{entry_id}", summary="Update draft journal entry")
def update_journal_entry(
    entry_id: str,
    payload:  JEUpdateRequest,
    db:       Session = Depends(get_db),
    user=     Depends(get_current_active_user),
):
    """Hanya boleh diupdate saat status = draft atau rejected."""
    engine = JournalEntryEngine(db)
    try:
        result = engine.update_draft(
            entry_id    = entry_id,
            journal_date = payload.journal_date,
            description = payload.description,
            currency    = payload.currency,
            exchange_rate = payload.exchange_rate,
            lines       = [_to_line_input(ln) for ln in payload.lines] if payload.lines else None,
            updated_by  = user.get("email", "system"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


# ── Workflow Actions ───────────────────────────────────────────────────────────

@router.post("/{entry_id}/submit",
             summary="Submit jurnal untuk approval (auto-validate balance + period)")
def submit_journal_entry(
    entry_id: str,
    db:       Session = Depends(get_db),
    user=     Depends(get_current_active_user),
):
    """
    Sistem menjalankan dua validasi hardcoded sebelum submit:
    1. Balance Check  — debet harus = kredit (toleransi Rp1)
    2. Period Check   — tanggal jurnal harus di periode open
    Jika gagal, jurnal TIDAK akan disubmit dan error dikembalikan.
    """
    engine = JournalEntryEngine(db)
    try:
        result = engine.submit(entry_id, submitted_by=user.get("email", "system"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.post("/{entry_id}/approve",
             dependencies=_finance,
             summary="Setujui jurnal (Reviewer: Accounting Supervisor / Finance Manager)")
def approve_journal_entry(
    entry_id: str,
    notes:    Optional[str] = Query(None),
    db:       Session = Depends(get_db),
    user=     Depends(require_min_role("finance")),
):
    """
    Approve jurnal — status → approved.
    Role reviewer harus sesuai dengan required_approval_role di jurnal.
    Pembuatan jurnal dan approval TIDAK BOLEH dilakukan oleh orang yang sama.
    """
    engine = JournalEntryEngine(db)
    try:
        result = engine.approve(
            entry_id      = entry_id,
            reviewer      = user.get("email", "system"),
            reviewer_role = user.get("role", "finance"),
            notes         = notes,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.post("/{entry_id}/reject",
             dependencies=_finance,
             summary="Tolak jurnal — kembalikan ke staff")
def reject_journal_entry(
    entry_id: str,
    payload:  RejectRequest,
    db:       Session = Depends(get_db),
    user=     Depends(require_min_role("finance")),
):
    """Reject jurnal — status → rejected. Staff bisa edit dan resubmit."""
    engine = JournalEntryEngine(db)
    try:
        result = engine.reject(
            entry_id      = entry_id,
            reviewer      = user.get("email", "system"),
            reviewer_role = user.get("role", "finance"),
            reason        = payload.reason,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.post("/{entry_id}/post",
             dependencies=_finance,
             summary="Post jurnal approved ke General Ledger (status: Posted, IMMUTABLE)")
def post_journal_entry(
    entry_id: str,
    db:       Session = Depends(get_db),
    user=     Depends(require_min_role("finance")),
):
    """
    Posting jurnal ke GL. Setelah posted:
    - Angka masuk ke Trial Balance & Laporan Keuangan secara real-time
    - Status IMMUTABLE — tidak bisa diedit atau dihapus
    - Koreksi hanya via Reversal atau Adjustment Entry baru
    """
    engine = JournalEntryEngine(db)
    try:
        result = engine.post(
            entry_id       = entry_id,
            posted_by      = user.get("email", "system"),
            posted_by_role = user.get("role", "finance"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.post("/{entry_id}/reverse",
             dependencies=_finance,
             summary="Full Reversal — buat jurnal balik otomatis (debet↔kredit)")
def reverse_journal_entry(
    entry_id: str,
    payload:  ReversalRequest,
    db:       Session = Depends(get_db),
    user=     Depends(require_min_role("finance")),
):
    """
    Buat jurnal balik otomatis untuk jurnal yang sudah posted.
    Jurnal asli TIDAK dihapus — tetap ada di database untuk audit trail.
    Jurnal reversal baru dibuat dan harus melalui workflow approval seperti biasa.

    fast_track=true (hanya admin): jurnal reversal langsung approved, tinggal POST.
    """
    user_role = user.get("role", "finance")
    if payload.fast_track and user_role != "admin":
        raise HTTPException(403, "fast_track reversal hanya diizinkan untuk role admin")

    engine = JournalEntryEngine(db)
    try:
        result = engine.full_reversal(
            entry_id         = entry_id,
            reversal_date    = payload.reversal_date,
            reversed_by      = user.get("email", "system"),
            reversed_by_role = user_role,
            reason           = payload.reason,
            fast_track       = payload.fast_track,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.post("/{entry_id}/cancel", summary="Batalkan jurnal (hanya draft/rejected)")
def cancel_journal_entry(
    entry_id: str,
    reason:   str = Query(..., min_length=3),
    db:       Session = Depends(get_db),
    user=     Depends(get_current_active_user),
):
    engine = JournalEntryEngine(db)
    try:
        result = engine.cancel(entry_id, user.get("email", "system"), reason)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


# ── Approval Log ───────────────────────────────────────────────────────────────

@router.get("/{entry_id}/audit-trail", dependencies=_viewer,
            summary="Audit trail lengkap sebuah jurnal entry")
def get_audit_trail(entry_id: str, db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT jal.*, je.entry_no
            FROM journal_approval_log jal
            JOIN journal_entry je ON je.id = jal.entry_id
            WHERE jal.entry_id = :id
            ORDER BY jal.acted_at ASC
        """),
        {"id": entry_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Accounting Period Endpoints ────────────────────────────────────────────────

@router.get("/periods/list", dependencies=_viewer, summary="List accounting periods")
def list_periods(
    entity_id: str = Query(...),
    year:      Optional[int] = Query(None),
    db:        Session = Depends(get_db),
):
    cond = "entity_id = :eid"
    params: dict = {"eid": entity_id}
    if year:
        cond += " AND period_year = :yr"
        params["yr"] = year

    rows = db.execute(
        text(f"""
            SELECT ap.*,
                   (SELECT COUNT(*) FROM journal_entry je
                    WHERE je.entity_id = ap.entity_id
                      AND je.period_year = ap.period_year
                      AND je.period_month = ap.period_month
                      AND je.status = 'posted') AS posted_journal_count
            FROM accounting_period ap
            WHERE {cond}
            ORDER BY period_year DESC, period_month DESC
        """),
        params
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/periods/status/{entity_id}/{year}/{month}",
            dependencies=_viewer, summary="Status satu periode akuntansi")
def get_period_status(
    entity_id: str, year: int, month: int,
    db: Session = Depends(get_db),
):
    engine = JournalEntryEngine(db)
    data   = engine.ensure_period(entity_id, year, month)
    # Tambah statistik jurnal
    stats = db.execute(
        text("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'draft')           AS draft_count,
                COUNT(*) FILTER (WHERE status = 'pending_approval') AS pending_count,
                COUNT(*) FILTER (WHERE status = 'approved')        AS approved_count,
                COUNT(*) FILTER (WHERE status = 'posted')          AS posted_count,
                COUNT(*) FILTER (WHERE status = 'rejected')        AS rejected_count,
                COALESCE(SUM(total_debit_idr) FILTER (WHERE status = 'posted'), 0) AS total_posted_idr
            FROM journal_entry
            WHERE entity_id = :eid AND period_year = :yr AND period_month = :mo
        """),
        {"eid": entity_id, "yr": year, "mo": month}
    ).fetchone()
    return {**data, "journal_stats": dict(stats._mapping) if stats else {}}


@router.post("/periods/close", dependencies=_admin, summary="Tutup periode akuntansi")
def close_period(
    payload: PeriodAction,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("admin")),
):
    """
    Tutup periode. Jurnal baru tidak bisa disubmit ke periode yang sudah closed.
    Bisa dibuka kembali oleh admin (kecuali sudah locked).
    """
    # Cek apakah ada jurnal pending yang belum diselesaikan
    pending = db.execute(
        text("""
            SELECT COUNT(*) FROM journal_entry
            WHERE entity_id = :eid AND period_year = :yr AND period_month = :mo
              AND status IN ('pending_approval', 'approved')
        """),
        {"eid": payload.entity_id, "yr": payload.year, "mo": payload.month}
    ).scalar()
    if (pending or 0) > 0:
        raise HTTPException(
            400,
            f"Tidak bisa menutup periode — masih ada {pending} jurnal dalam status "
            "pending_approval atau approved yang belum diposting."
        )

    engine = JournalEntryEngine(db)
    try:
        result = engine.close_period(
            payload.entity_id, payload.year, payload.month,
            closed_by=user.get("email", "admin")
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.post("/periods/lock", dependencies=_admin,
             summary="Lock permanen periode (setelah audit/filing pajak)")
def lock_period(
    payload: PeriodAction,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("admin")),
):
    engine = JournalEntryEngine(db)
    try:
        result = engine.lock_period(
            payload.entity_id, payload.year, payload.month,
            locked_by=user.get("email", "admin")
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.post("/periods/reopen", dependencies=_admin,
             summary="Buka kembali periode yang sudah closed (bukan locked)")
def reopen_period(
    payload: PeriodAction,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("admin")),
):
    engine = JournalEntryEngine(db)
    try:
        result = engine.reopen_period(
            payload.entity_id, payload.year, payload.month,
            reopened_by=user.get("email", "admin")
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


# ── Approval Matrix Setup ──────────────────────────────────────────────────────

@router.post("/matrix/setup", dependencies=_admin,
             summary="Setup / update approval matrix")
def setup_approval_matrix(
    entries: list[ApprovalMatrixEntry],
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("admin")),
):
    """
    Upsert approval matrix.
    Default yang disarankan:
    - Level 1: semua tipe, ≤Rp50jt → required_role: finance
    - Level 2: semua tipe, >Rp50jt → required_role: admin
    - Level 3: journal_type=adjustment → required_role: admin
    - Level 4: journal_type=write_off  → required_role: admin
    """
    for e in entries:
        db.execute(
            text("""
                INSERT INTO journal_approval_matrix (
                    id, entity_id, level, threshold_name,
                    journal_type, min_amount, max_amount,
                    required_role, is_active, created_by
                ) VALUES (
                    uuid_generate_v4(), :eid, :lvl, :name,
                    :jtype, :min, :max, :role, TRUE, :by
                )
                ON CONFLICT (entity_id, level) DO UPDATE SET
                    threshold_name = EXCLUDED.threshold_name,
                    journal_type   = EXCLUDED.journal_type,
                    min_amount     = EXCLUDED.min_amount,
                    max_amount     = EXCLUDED.max_amount,
                    required_role  = EXCLUDED.required_role,
                    is_active      = TRUE
            """),
            {
                "eid": e.entity_id, "lvl": e.level, "name": e.threshold_name,
                "jtype": e.journal_type, "min": e.min_amount, "max": e.max_amount,
                "role": e.required_role, "by": user.get("email", "admin"),
            }
        )
    db.commit()
    return {"updated": len(entries)}


@router.post("/matrix/seed-defaults", dependencies=_admin,
             summary="Seed default approval matrix (≤50jt→finance, >50jt→admin, adj/write_off→admin)")
def seed_default_matrix(
    entity_id: str = Query(...),
    threshold: float = Query(50_000_000, description="Batas nominal untuk level pertama (default Rp50jt)"),
    db:        Session = Depends(get_db),
    user=      Depends(require_min_role("admin")),
):
    defaults = [
        {"level": 1, "threshold_name": f"Semua Tipe ≤ Rp{threshold:,.0f}",
         "journal_type": None, "min": 0, "max": threshold, "role": "finance"},
        {"level": 2, "threshold_name": f"Semua Tipe > Rp{threshold:,.0f}",
         "journal_type": None, "min": threshold + 1, "max": None, "role": "admin"},
        {"level": 3, "threshold_name": "Adjustment — semua nominal",
         "journal_type": "adjustment", "min": 0, "max": None, "role": "admin"},
        {"level": 4, "threshold_name": "Write-off — semua nominal",
         "journal_type": "write_off", "min": 0, "max": None, "role": "admin"},
    ]
    for d in defaults:
        db.execute(
            text("""
                INSERT INTO journal_approval_matrix (
                    id, entity_id, level, threshold_name,
                    journal_type, min_amount, max_amount, required_role, is_active, created_by
                ) VALUES (
                    uuid_generate_v4(), :eid, :lvl, :name,
                    :jtype, :min, :max, :role, TRUE, :by
                )
                ON CONFLICT (entity_id, level) DO UPDATE SET
                    threshold_name = EXCLUDED.threshold_name,
                    journal_type   = EXCLUDED.journal_type,
                    min_amount     = EXCLUDED.min_amount,
                    max_amount     = EXCLUDED.max_amount,
                    required_role  = EXCLUDED.required_role
            """),
            {
                "eid": entity_id, "lvl": d["level"], "name": d["threshold_name"],
                "jtype": d["journal_type"], "min": d["min"], "max": d["max"],
                "role": d["role"], "by": user.get("email", "admin"),
            }
        )
    db.commit()
    return {"seeded": len(defaults), "entity_id": entity_id}


@router.get("/matrix/view", dependencies=_viewer, summary="Lihat approval matrix")
def view_matrix(entity_id: str = Query(...), db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT * FROM journal_approval_matrix
            WHERE entity_id = :eid AND is_active = TRUE
            ORDER BY level
        """),
        {"eid": entity_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/matrix/simulate", summary="Simulasi: required role untuk nominal / tipe tertentu")
def simulate_approval(
    entity_id:    str   = Query(...),
    journal_type: str   = Query("general"),
    total_amount: float = Query(...),
    db:           Session = Depends(get_db),
):
    """Simulasi approval matrix tanpa perlu membuat jurnal."""
    required = _get_required_role(db, entity_id, journal_type, total_amount)
    return {
        "entity_id":    entity_id,
        "journal_type": journal_type,
        "total_amount": total_amount,
        "required_approval_role": required,
        "message": f"Jurnal ini membutuhkan persetujuan dari role: {required}",
    }


# ── Reports ────────────────────────────────────────────────────────────────────

@router.get("/reports/pending-queue", dependencies=_finance,
            summary="Antrian jurnal pending approval (semua entity)")
def report_pending_queue(
    entity_id: str = Query(...),
    db:        Session = Depends(get_db),
):
    rows = db.execute(
        text("""
            SELECT je.*,
                   EXTRACT(DAYS FROM (NOW() - je.submitted_at)) AS days_pending,
                   (SELECT COUNT(*) FROM journal_entry_line jel WHERE jel.entry_id = je.id) AS line_count
            FROM journal_entry je
            WHERE je.entity_id = :eid AND je.status = 'pending_approval'
            ORDER BY je.required_approval_role, je.submitted_at ASC
        """),
        {"eid": entity_id}
    ).fetchall()
    return {
        "total_pending": len(rows),
        "items":         [dict(r._mapping) for r in rows],
    }


@router.get("/reports/monthly-summary", dependencies=_viewer,
            summary="Ringkasan aktivitas jurnal per bulan")
def report_monthly_summary(
    entity_id: str = Query(...),
    year:      int = Query(...),
    db:        Session = Depends(get_db),
):
    rows = db.execute(
        text("""
            SELECT * FROM vw_je_monthly_summary
            WHERE entity_id = :eid AND period_year = :yr
            ORDER BY period_month, journal_type
        """),
        {"eid": entity_id, "yr": year}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/reports/currency-exposure", dependencies=_viewer,
            summary="Eksposur multi-currency dari jurnal posted")
def report_currency_exposure(
    entity_id: str = Query(...),
    year:      int = Query(...),
    month:     int = Query(..., ge=1, le=12),
    db:        Session = Depends(get_db),
):
    """Ringkasan jurnal berdasarkan currency dan exchange rate yang digunakan."""
    rows = db.execute(
        text("""
            SELECT
                je.currency,
                AVG(je.exchange_rate)                   AS avg_exchange_rate,
                MIN(je.exchange_rate)                   AS min_exchange_rate,
                MAX(je.exchange_rate)                   AS max_exchange_rate,
                COUNT(*)                                AS journal_count,
                COALESCE(SUM(je.total_debit_currency), 0)  AS total_debit_currency,
                COALESCE(SUM(je.total_debit_idr), 0)       AS total_debit_idr
            FROM journal_entry je
            WHERE je.entity_id   = :eid
              AND je.period_year  = :yr
              AND je.period_month = :mo
              AND je.status       = 'posted'
              AND je.currency    != 'IDR'
            GROUP BY je.currency
            ORDER BY total_debit_idr DESC
        """),
        {"eid": entity_id, "yr": year, "mo": month}
    ).fetchall()
    return [dict(r._mapping) for r in rows]
