"""
Contract Tracker Router — REST API
Prefix: /contracts

Endpoint utama:
  Legal   : create, activate, terminate, amendments
  PM      : milestone CRUD, BAST completion
  Finance : generate invoice dari milestone, record payment
  Reports : dashboard, AR aging, expiry alerts, outstanding
"""

from __future__ import annotations

from datetime import date
from typing import Any, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from modules.auth import get_current_user
from modules.contract_engine import ContractEngine

router = APIRouter(prefix="/contracts", tags=["contract-tracker"])


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _require_role(user: dict, *roles: str) -> None:
    if user.get("role") not in roles:
        raise HTTPException(403, detail=f"Butuh role: {', '.join(roles)}")


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Schemas
# ─────────────────────────────────────────────────────────────────────────────

class ContractCreate(BaseModel):
    entity_id: str
    project_id: str
    client_id: Optional[str] = None
    contract_number: str
    contract_title: str
    total_value: float = Field(..., gt=0)
    currency: str = "IDR"
    exchange_rate: float = 1.0
    term_of_payment_days: int = Field(30, ge=1, le=365)
    retention_pct: float = Field(0.0, ge=0, le=20)
    start_date: date
    end_date: date
    po_number: Optional[str] = None
    scope_summary: Optional[str] = None
    notes: Optional[str] = None


class ContractUpdate(BaseModel):
    contract_title: Optional[str] = None
    total_value: Optional[float] = None
    term_of_payment_days: Optional[int] = None
    end_date: Optional[date] = None
    po_number: Optional[str] = None
    scope_summary: Optional[str] = None
    notes: Optional[str] = None


class MilestoneCreate(BaseModel):
    sequence: int = Field(1, ge=1)
    milestone_name: str
    description: Optional[str] = None
    percentage: float = Field(..., gt=0, le=100)
    trigger_condition: Optional[str] = None
    pm_milestone_id: Optional[str] = None
    notes: Optional[str] = None


class MilestoneUpdate(BaseModel):
    milestone_name: Optional[str] = None
    percentage: Optional[float] = None
    trigger_condition: Optional[str] = None
    notes: Optional[str] = None


class BASTRequest(BaseModel):
    bast_number: str
    bast_date: date
    bast_signed_by: str


class InvoiceFromMilestoneRequest(BaseModel):
    invoice_date: date
    tax_rate: float = Field(0.11, ge=0, le=1)
    notes: Optional[str] = None


class PaymentRecordRequest(BaseModel):
    payment_date: date
    amount_received: float = Field(..., gt=0)
    payment_method: str = "bank_transfer"
    bank_reference: Optional[str] = None
    notes: Optional[str] = None

    @validator("payment_method")
    def valid_method(cls, v):
        if v not in ("bank_transfer", "cheque", "giro", "cash", "other"):
            raise ValueError("payment_method: bank_transfer | cheque | giro | cash | other")
        return v


class AmendmentCreate(BaseModel):
    amendment_type: str
    amendment_title: str
    reason: str
    new_value: Optional[float] = None
    new_end_date: Optional[date] = None
    impact_description: Optional[str] = None

    @validator("amendment_type")
    def valid_atype(cls, v):
        valid = ("value_increase", "value_decrease", "extension", "scope_change", "termination")
        if v not in valid:
            raise ValueError(f"amendment_type: {' | '.join(valid)}")
        return v


class AmendmentSignRequest(BaseModel):
    signing_date: date
    signed_by: str


class DocumentUpload(BaseModel):
    document_type: str = "other"
    document_name: str
    file_url: Optional[str] = None
    file_size_kb: Optional[int] = None

    @validator("document_type")
    def valid_dtype(cls, v):
        valid = ("contract_signed", "amendment", "bast", "nda", "po", "warranty", "other")
        if v not in valid:
            raise ValueError(f"document_type: {' | '.join(valid)}")
        return v


class BulkMilestoneCreate(BaseModel):
    """Buat beberapa milestone sekaligus — sistem otomatis hitung amount_target."""
    milestones: List[MilestoneCreate]


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT — CRUD + Status Workflow
# ─────────────────────────────────────────────────────────────────────────────

@router.post("", status_code=201, summary="Buat kontrak baru (IN_REVIEW)")
def create_contract(
    req: ContractCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Kontrak baru selalu dibuat dengan status IN_REVIEW.
    Billing belum bisa dilakukan sampai status ACTIVE.
    """
    existing = db.execute(
        text("SELECT id FROM project_contract WHERE contract_number = :cn"),
        {"cn": req.contract_number},
    ).fetchone()
    if existing:
        raise HTTPException(400, f"contract_number '{req.contract_number}' sudah ada")

    if req.start_date >= req.end_date:
        raise HTTPException(400, "end_date harus setelah start_date")

    contract_id = str(uuid4())
    db.execute(
        text(
            "INSERT INTO project_contract "
            "(id, entity_id, project_id, client_id, contract_number, contract_title, "
            " total_value, currency, exchange_rate, term_of_payment_days, retention_pct, "
            " start_date, end_date, po_number, scope_summary, notes, created_by) "
            "VALUES (:id, :eid, :pid, :cid, :cn, :ct, "
            "        :tv, :cur, :er, :top, :ret, "
            "        :sd, :ed, :po, :scope, :notes, :cby)"
        ),
        {
            "id": contract_id, "eid": req.entity_id, "pid": req.project_id,
            "cid": req.client_id, "cn": req.contract_number, "ct": req.contract_title,
            "tv": req.total_value, "cur": req.currency, "er": req.exchange_rate,
            "top": req.term_of_payment_days, "ret": req.retention_pct,
            "sd": req.start_date, "ed": req.end_date,
            "po": req.po_number, "scope": req.scope_summary, "notes": req.notes,
            "cby": current_user["username"],
        },
    )
    db.commit()
    return {
        "contract_id": contract_id,
        "contract_number": req.contract_number,
        "contract_status": "IN_REVIEW",
        "message": "Kontrak dibuat. Tambahkan milestone lalu aktifkan kontrak.",
    }


@router.get("", summary="Daftar kontrak")
def list_contracts(
    entity_id: str = Query(...),
    project_id: Optional[str] = Query(None),
    contract_status: Optional[str] = Query(None),
    client_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    filters = ["c.entity_id = :eid"]
    params: dict[str, Any] = {"eid": entity_id}

    if project_id:
        filters.append("c.project_id = :pid"); params["pid"] = project_id
    if contract_status:
        filters.append("c.contract_status = :cs"); params["cs"] = contract_status
    if client_id:
        filters.append("c.client_id = :clid"); params["clid"] = client_id
    if search:
        filters.append("(c.contract_number ILIKE :s OR c.contract_title ILIKE :s)")
        params["s"] = f"%{search}%"

    where = " AND ".join(filters)
    total = db.execute(
        text(
            f"SELECT COUNT(*) FROM project_contract c WHERE {where}"
        ),
        params,
    ).scalar()

    params["offset"] = (page - 1) * size
    params["limit"]  = size
    rows = db.execute(
        text(
            f"SELECT c.*, v.vendor_name AS client_name, p.project_name "
            f"FROM project_contract c "
            f"LEFT JOIN vendor v ON v.id = c.client_id "
            f"LEFT JOIN project p ON p.id = c.project_id "
            f"WHERE {where} "
            f"ORDER BY c.created_at DESC LIMIT :limit OFFSET :offset"
        ),
        params,
    ).fetchall()

    return {"total": total, "page": page, "size": size, "items": [dict(r._mapping) for r in rows]}


@router.get("/{contract_id}", summary="Detail kontrak lengkap (milestone + invoice + amendment + payment)")
def get_contract(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        return ContractEngine.get_contract_detail(db, contract_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.put("/{contract_id}", summary="Update detail kontrak (hanya saat IN_REVIEW)")
def update_contract(
    contract_id: str,
    req: ContractUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    row = db.execute(
        text("SELECT contract_status FROM project_contract WHERE id = :cid"),
        {"cid": contract_id},
    ).fetchone()
    if not row:
        raise HTTPException(404, "Kontrak tidak ditemukan")
    if row.contract_status not in ("IN_REVIEW",):
        raise HTTPException(400, f"Kontrak berstatus {row.contract_status} tidak bisa diedit. Gunakan amendment.")

    updates = ["updated_at = NOW()"]
    params: dict[str, Any] = {"cid": contract_id}

    if req.contract_title   is not None: updates.append("contract_title = :ct");   params["ct"]  = req.contract_title
    if req.total_value      is not None: updates.append("total_value = :tv");       params["tv"]  = req.total_value
    if req.term_of_payment_days is not None: updates.append("term_of_payment_days = :top"); params["top"] = req.term_of_payment_days
    if req.end_date         is not None: updates.append("end_date = :ed");           params["ed"]  = req.end_date
    if req.po_number        is not None: updates.append("po_number = :po");          params["po"]  = req.po_number
    if req.scope_summary    is not None: updates.append("scope_summary = :scope");   params["scope"] = req.scope_summary
    if req.notes            is not None: updates.append("notes = :notes");           params["notes"] = req.notes

    db.execute(text(f"UPDATE project_contract SET {', '.join(updates)} WHERE id = :cid"), params)
    db.commit()
    return {"contract_id": contract_id, "updated": True}


@router.post("/{contract_id}/activate", summary="Aktifkan kontrak (tanda tangan legal) — buka kunci billing")
def activate_contract(
    contract_id: str,
    signing_date: date = Query(...),
    legal_reviewed_by: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Mengaktifkan kontrak. Sebelum aktivasi:
    - Validasi total percentage milestone = 100%
    - Set contract_status = ACTIVE + isi signing_date
    - Setelah ACTIVE: Finance bisa membuat invoice untuk milestone COMPLETED
    """
    _require_role(current_user, "admin")

    row = db.execute(
        text("SELECT contract_status FROM project_contract WHERE id = :cid"),
        {"cid": contract_id},
    ).fetchone()
    if not row:
        raise HTTPException(404, "Kontrak tidak ditemukan")
    if row.contract_status != "IN_REVIEW":
        raise HTTPException(400, f"Kontrak sudah berstatus {row.contract_status}")

    # Validasi total milestone = 100%
    validation = ContractEngine.validate_milestone_total(db, contract_id)
    if not validation["is_valid"]:
        raise HTTPException(400, f"Kontrak tidak bisa diaktifkan: {validation['message']}")

    db.execute(
        text(
            "UPDATE project_contract "
            "SET contract_status = 'ACTIVE', signing_date = :sd, "
            "    legal_reviewed_by = :lrb, legal_reviewed_at = NOW(), updated_at = NOW() "
            "WHERE id = :cid"
        ),
        {
            "sd": signing_date,
            "lrb": legal_reviewed_by or current_user["username"],
            "cid": contract_id,
        },
    )
    db.commit()
    return {
        "contract_id":    contract_id,
        "contract_status": "ACTIVE",
        "signing_date":   str(signing_date),
        "message":        "Kontrak AKTIF. Billing sekarang diizinkan untuk milestone yang COMPLETED.",
    }


@router.post("/{contract_id}/terminate", summary="Hentikan kontrak")
def terminate_contract(
    contract_id: str,
    reason: str = Query(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "admin")
    row = db.execute(
        text("SELECT contract_status FROM project_contract WHERE id = :cid"),
        {"cid": contract_id},
    ).fetchone()
    if not row:
        raise HTTPException(404, "Kontrak tidak ditemukan")
    if row.contract_status in ("TERMINATED", "COMPLETED"):
        raise HTTPException(400, f"Kontrak sudah berstatus {row.contract_status}")

    db.execute(
        text(
            "UPDATE project_contract "
            "SET contract_status = 'TERMINATED', notes = COALESCE(notes,'') || :reason, "
            "    updated_at = NOW() "
            "WHERE id = :cid"
        ),
        {"reason": f"\n[TERMINATED] {reason}", "cid": contract_id},
    )
    db.commit()
    return {"contract_id": contract_id, "contract_status": "TERMINATED"}


@router.post("/{contract_id}/complete", summary="Tandai kontrak selesai penuh")
def complete_contract(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "admin")
    # Cek semua milestone sudah lunas
    unpaid = db.execute(
        text(
            "SELECT COUNT(*) AS cnt FROM contract_milestone "
            "WHERE contract_id = :cid AND billing_status NOT IN ('PAID','UNINVOICED')"
            "  AND work_status = 'COMPLETED'"
        ),
        {"cid": contract_id},
    ).fetchone()

    if unpaid and unpaid.cnt > 0:
        raise HTTPException(
            400,
            f"Masih ada {unpaid.cnt} milestone yang belum lunas. Selesaikan pembayaran dulu.",
        )

    db.execute(
        text(
            "UPDATE project_contract SET contract_status = 'COMPLETED', updated_at = NOW() "
            "WHERE id = :cid"
        ),
        {"cid": contract_id},
    )
    db.commit()
    return {"contract_id": contract_id, "contract_status": "COMPLETED"}


# ─────────────────────────────────────────────────────────────────────────────
# MILESTONES — Jadwal Penagihan Termin
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{contract_id}/milestones", status_code=201, summary="Tambah satu milestone")
def add_milestone(
    contract_id: str,
    req: MilestoneCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    contract = db.execute(
        text("SELECT total_value, retention_pct, contract_status FROM project_contract WHERE id = :cid"),
        {"cid": contract_id},
    ).fetchone()
    if not contract:
        raise HTTPException(404, "Kontrak tidak ditemukan")
    if contract.contract_status == "TERMINATED":
        raise HTTPException(400, "Kontrak sudah dihentikan")

    total_value     = Decimal(str(contract.total_value))
    percentage      = Decimal(str(req.percentage))
    amount_target   = (total_value * percentage / 100).quantize(Decimal("1"))
    retention_pct_v = Decimal(str(contract.retention_pct or 0))
    retention_held  = (amount_target * retention_pct_v / 100).quantize(Decimal("1"))

    ms_id = str(uuid4())
    db.execute(
        text(
            "INSERT INTO contract_milestone "
            "(id, contract_id, pm_milestone_id, sequence, milestone_name, description, "
            " percentage, amount_target, retention_held, trigger_condition, notes) "
            "VALUES (:id, :cid, :pmid, :seq, :n, :desc, "
            "        :pct, :amt, :ret, :trig, :notes)"
        ),
        {
            "id": ms_id, "cid": contract_id, "pmid": req.pm_milestone_id,
            "seq": req.sequence, "n": req.milestone_name, "desc": req.description,
            "pct": float(percentage), "amt": float(amount_target),
            "ret": float(retention_held), "trig": req.trigger_condition,
            "notes": req.notes,
        },
    )
    db.commit()
    return {
        "milestone_id":  ms_id,
        "percentage":    float(percentage),
        "amount_target": float(amount_target),
        "retention_held": float(retention_held),
    }


@router.post("/{contract_id}/milestones/bulk", status_code=201, summary="Buat semua milestone sekaligus")
def bulk_create_milestones(
    contract_id: str,
    req: BulkMilestoneCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Buat beberapa milestone dalam satu request.
    Validasi total percentage = 100% dilakukan di sini.
    """
    total_pct = sum(m.percentage for m in req.milestones)
    if abs(total_pct - 100.0) > 0.01:
        raise HTTPException(400, f"Total persentase {total_pct}% ≠ 100%")

    contract = db.execute(
        text("SELECT total_value, retention_pct FROM project_contract WHERE id = :cid"),
        {"cid": contract_id},
    ).fetchone()
    if not contract:
        raise HTTPException(404, "Kontrak tidak ditemukan")

    # Hapus milestone lama yang belum diinvoice
    db.execute(
        text(
            "DELETE FROM contract_milestone "
            "WHERE contract_id = :cid AND billing_status = 'UNINVOICED'"
        ),
        {"cid": contract_id},
    )

    total_value    = Decimal(str(contract.total_value))
    retention_pct_ = Decimal(str(contract.retention_pct or 0))
    created = []

    for ms in req.milestones:
        percentage    = Decimal(str(ms.percentage))
        amount_target = (total_value * percentage / 100).quantize(Decimal("1"))
        retention_held = (amount_target * retention_pct_ / 100).quantize(Decimal("1"))
        ms_id = str(uuid4())

        db.execute(
            text(
                "INSERT INTO contract_milestone "
                "(id, contract_id, pm_milestone_id, sequence, milestone_name, description, "
                " percentage, amount_target, retention_held, trigger_condition, notes) "
                "VALUES (:id, :cid, :pmid, :seq, :n, :desc, "
                "        :pct, :amt, :ret, :trig, :notes)"
            ),
            {
                "id": ms_id, "cid": contract_id, "pmid": ms.pm_milestone_id,
                "seq": ms.sequence, "n": ms.milestone_name, "desc": ms.description,
                "pct": float(percentage), "amt": float(amount_target),
                "ret": float(retention_held), "trig": ms.trigger_condition,
                "notes": ms.notes,
            },
        )
        created.append({"milestone_id": ms_id, "name": ms.milestone_name, "amount": float(amount_target)})

    db.commit()
    return {"created_count": len(created), "milestones": created}


@router.get("/{contract_id}/milestones", summary="Daftar milestone + billing status")
def list_milestones(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    rows = db.execute(
        text("SELECT * FROM vw_milestone_billing WHERE contract_id = :cid ORDER BY sequence"),
        {"cid": contract_id},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/{contract_id}/milestones/{milestone_id}/complete-bast", summary="Konfirmasi BAST — buka kunci invoice")
def complete_bast(
    contract_id: str,
    milestone_id: str,
    req: BASTRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    PM / Tim Lapangan melaporkan pekerjaan selesai dan BAST diterbitkan.
    Efek: work_status → COMPLETED, billing_status → READY_TO_BILL.
    Finance kemudian bisa membuat invoice via /milestones/{id}/create-invoice.
    """
    try:
        result = ContractEngine.complete_milestone_bast(
            db=db,
            milestone_id=milestone_id,
            bast_number=req.bast_number,
            bast_date=req.bast_date,
            bast_signed_by=req.bast_signed_by,
            completed_by=current_user["username"],
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{contract_id}/milestones/{milestone_id}/create-invoice", status_code=201, summary="Buat invoice dari milestone (Logika B + C)")
def create_invoice(
    contract_id: str,
    milestone_id: str,
    req: InvoiceFromMilestoneRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Logika B (gate validation) + buat AR invoice otomatis.

    Validasi otomatis:
    - Gate 1: milestone.work_status == COMPLETED (BAST terbit)
    - Gate 2: contract.contract_status == ACTIVE
    - Gate 3: milestone.billing_status == READY_TO_BILL (belum diinvoice)

    Amount invoice = amount_target - retention_held (jika ada retensi)
    due_date = invoice_date + contract.term_of_payment_days
    """
    _require_role(current_user, "finance", "admin")
    try:
        result = ContractEngine.create_invoice_from_milestone(
            db=db,
            milestone_id=milestone_id,
            invoice_date=req.invoice_date,
            created_by=current_user["username"],
            notes=req.notes,
            tax_rate=req.tax_rate,
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# PAYMENT — Catat Pembayaran dari Klien
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/invoices/{invoice_id}/record-payment", status_code=201, summary="Catat pembayaran dari klien")
def record_payment(
    invoice_id: str,
    req: PaymentRecordRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Catat pembayaran aktual dari klien.
    Jika jumlah mencukupi: invoice → PAID, milestone → PAID.
    """
    _require_role(current_user, "finance", "admin")
    try:
        result = ContractEngine.record_payment(
            db=db,
            invoice_id=invoice_id,
            payment_date=req.payment_date,
            amount_received=req.amount_received,
            payment_method=req.payment_method,
            bank_reference=req.bank_reference,
            recorded_by=current_user["username"],
            notes=req.notes,
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/invoices/{invoice_id}/send", summary="Ubah status invoice ke SENT (sudah dikirim ke klien)")
def mark_invoice_sent(
    invoice_id: str,
    sent_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "finance", "admin")
    db.execute(
        text(
            "UPDATE ar_invoice "
            "SET payment_status = 'SENT', status = 'sent' "
            "WHERE id = :iid AND payment_status = 'DRAFT'"
        ),
        {"iid": invoice_id},
    )
    db.commit()
    return {"invoice_id": invoice_id, "payment_status": "SENT"}


@router.put("/invoices/{invoice_id}/cancel", summary="Batalkan invoice (hanya DRAFT)")
def cancel_invoice(
    invoice_id: str,
    reason: str = Query(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "finance", "admin")
    row = db.execute(
        text(
            "SELECT ai.payment_status, ai.milestone_id "
            "FROM ar_invoice ai WHERE id = :iid"
        ),
        {"iid": invoice_id},
    ).fetchone()
    if not row:
        raise HTTPException(404, "Invoice tidak ditemukan")
    if row.payment_status not in ("DRAFT",):
        raise HTTPException(400, f"Invoice berstatus {row.payment_status} tidak bisa di-cancel")

    db.execute(
        text(
            "UPDATE ar_invoice SET payment_status = 'CANCELLED', status = 'cancelled' "
            "WHERE id = :iid"
        ),
        {"iid": invoice_id},
    )

    # Reset milestone ke READY_TO_BILL agar bisa diinvoice ulang
    if row.milestone_id:
        db.execute(
            text(
                "UPDATE contract_milestone "
                "SET billing_status = 'READY_TO_BILL', invoice_id = NULL, invoiced_at = NULL "
                "WHERE id = :mid"
            ),
            {"mid": str(row.milestone_id)},
        )

    db.commit()
    return {"invoice_id": invoice_id, "payment_status": "CANCELLED", "milestone_reset": bool(row.milestone_id)}


# ─────────────────────────────────────────────────────────────────────────────
# AMENDMENTS
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{contract_id}/amendments", status_code=201, summary="Buat draft amendment / addendum")
def create_amendment(
    contract_id: str,
    req: AmendmentCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "admin")
    try:
        result = ContractEngine.create_amendment(
            db=db,
            contract_id=contract_id,
            amendment_type=req.amendment_type,
            amendment_title=req.amendment_title,
            reason=req.reason,
            new_value=req.new_value,
            new_end_date=req.new_end_date,
            impact_description=req.impact_description,
            created_by=current_user["username"],
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{contract_id}/amendments/{amendment_id}/sign", summary="Tanda tangan amendment — terapkan ke kontrak")
def sign_amendment(
    contract_id: str,
    amendment_id: str,
    req: AmendmentSignRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Saat amendment ditandatangani:
    - contract_status → AMENDED
    - total_value/end_date diperbarui (jika ada perubahan)
    - milestone.amount_target yang belum diinvoice dihitung ulang secara proporsional
    """
    _require_role(current_user, "admin")
    try:
        return ContractEngine.sign_amendment(
            db=db,
            amendment_id=amendment_id,
            signing_date=req.signing_date,
            signed_by=req.signed_by,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{contract_id}/amendments", summary="Daftar amendments kontrak")
def list_amendments(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    rows = db.execute(
        text(
            "SELECT * FROM contract_amendment "
            "WHERE contract_id = :cid ORDER BY created_at DESC"
        ),
        {"cid": contract_id},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# DOCUMENTS
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{contract_id}/documents", status_code=201, summary="Lampirkan dokumen ke kontrak")
def upload_document(
    contract_id: str,
    req: DocumentUpload,
    amendment_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    doc_id = str(uuid4())
    db.execute(
        text(
            "INSERT INTO contract_document "
            "(id, contract_id, amendment_id, document_type, document_name, "
            " file_url, file_size_kb, uploaded_by) "
            "VALUES (:id, :cid, :aid, :dt, :dn, :url, :sz, :uby)"
        ),
        {
            "id": doc_id, "cid": contract_id, "aid": amendment_id,
            "dt": req.document_type, "dn": req.document_name,
            "url": req.file_url, "sz": req.file_size_kb,
            "uby": current_user["username"],
        },
    )
    db.commit()
    return {"document_id": doc_id, "document_type": req.document_type}


@router.get("/{contract_id}/documents", summary="Daftar dokumen kontrak")
def list_documents(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    rows = db.execute(
        text(
            "SELECT * FROM contract_document "
            "WHERE contract_id = :cid ORDER BY uploaded_at DESC"
        ),
        {"cid": contract_id},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# REPORTS & DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/project/{project_id}/dashboard", summary="Dashboard finansial semua kontrak per proyek (Logika C)")
def contract_dashboard(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Logika C — satu halaman memuat:
    - total_contract_value per kontrak
    - total_invoiced / total_uninvoiced
    - total_collected_cash (benar-benar masuk bank)
    - total_outstanding (sudah ditagih belum dibayar)
    - total_overdue (lewat jatuh tempo)
    - billing_progress_pct
    - milestone stats (completed / ready_to_bill / invoiced / paid)
    """
    return ContractEngine.get_contract_dashboard(db, project_id)


@router.get("/entity/{entity_id}/ar-aging", summary="AR Aging Buckets (CURRENT/1-30/31-60/61-90/>90)")
def ar_aging(
    entity_id: str,
    client_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return ContractEngine.get_ar_aging(db, entity_id, client_id, project_id)


@router.get("/entity/{entity_id}/client-outstanding", summary="Total outstanding per klien")
def client_outstanding(
    entity_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return ContractEngine.get_client_outstanding(db, entity_id)


@router.get("/entity/{entity_id}/expiry-alerts", summary="Kontrak mendekati batas waktu (≤60 hari)")
def expiry_alerts(
    entity_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return ContractEngine.get_expiry_alerts(db, entity_id)


@router.post("/run-aging", summary="Trigger manual: update status OVERDUE (biasanya dijalankan scheduler)")
def run_aging_manual(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Logika A — biasanya dijalankan otomatis setiap hari jam 00:00 oleh scheduler.
    Endpoint ini untuk trigger manual / testing.
    """
    _require_role(current_user, "admin")
    result = ContractEngine.run_invoice_aging(db)
    return result


@router.get("/{contract_id}/validate-milestones", summary="Validasi total persentase milestone = 100%")
def validate_milestones(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return ContractEngine.validate_milestone_total(db, contract_id)
