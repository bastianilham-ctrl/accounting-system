# modules/procurement_router.py
# Purchase Requisition (PR) & Purchase Order (PO) API
#
# Flow:
#   PR: draft → submitted → approved → converted
#   PO: draft → submitted → approved → sent → [partial_]received → closed
#
# Integrasi:
#   - Budget check saat PR dibuat/disubmit
#   - Encumbrance (commitment) dibuat saat PO approved
#   - Commitment dirilis saat PO item ditagihkan (AP invoice)

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text

from core.database import get_db
from modules.auth import require_min_role, get_current_active_user
from modules.budget_engine import BudgetEngine
from modules.approval_engine import ApprovalEngine

router = APIRouter(prefix="/procurement", tags=["Procurement — PR & PO"])

_viewer  = [Depends(require_min_role("viewer"))]
_finance = [Depends(require_min_role("finance"))]
_admin   = [Depends(require_min_role("admin"))]


# ── Request schemas ────────────────────────────────────────────────────────────

class PRItemInput(BaseModel):
    item_no:     int = Field(..., ge=1)
    description: str
    category:    str = Field("services", pattern="^(goods|services|asset)$")
    unit:        Optional[str] = None
    qty:         float = Field(1, gt=0)
    unit_price:  float = Field(..., ge=0)
    item_id:      Optional[str] = None  # procurement_item.id — kalau diisi, account_code & budget_line_id di-derive otomatis (override input manual)
    account_code: Optional[str] = None
    budget_line_id: Optional[str] = None
    notes:        Optional[str] = None


class PRCreate(BaseModel):
    entity_id:    str
    department:   Optional[str] = None
    cost_center:  Optional[str] = None
    requested_by: str
    required_date: Optional[date] = None
    purpose:      Optional[str] = None
    items:        list[PRItemInput] = Field(..., min_length=1)


class PRApprovalAction(BaseModel):
    action: str = Field(..., pattern="^(approved|rejected|returned)$")
    notes:  Optional[str] = None


class POItemInput(BaseModel):
    item_no:     int = Field(..., ge=1)
    description: str
    category:    str = Field("services", pattern="^(goods|services|asset)$")
    unit:        Optional[str] = None
    qty:         float = Field(..., gt=0)
    unit_price:  float = Field(..., ge=0)
    account_code: Optional[str] = None
    cost_center:  Optional[str] = None
    notes:        Optional[str] = None


class POCreate(BaseModel):
    """PO langsung (tanpa PR) — opsional, pr_id bisa dikosongkan."""
    entity_id:        str
    vendor_id:        str
    pr_id:            Optional[str] = None
    po_date:          date
    required_date:    Optional[date] = None
    delivery_address: Optional[str] = None
    payment_terms:    str = "Net 30"
    currency:         str = "IDR"
    tax_amount:       float = 0
    items:            list[POItemInput] = Field(..., min_length=1)
    notes:            Optional[str] = None


class POApprovalAction(BaseModel):
    action: str = Field(..., pattern="^(approved|rejected|returned)$")
    notes:  Optional[str] = None


class GoodsReceiptItem(BaseModel):
    po_item_id:   str
    received_qty: float = Field(..., gt=0)
    notes:        Optional[str] = None


class GoodsReceiptCreate(BaseModel):
    po_id:        str
    entity_id:    str
    receipt_date: date
    received_by:  Optional[str] = None
    notes:        Optional[str] = None
    items:        list[GoodsReceiptItem] = Field(..., min_length=1)


class ApprovalMatrixEntry(BaseModel):
    entity_id:      str
    level:          int = Field(..., ge=1)
    threshold_name: str
    min_amount:     float = 0
    max_amount:     Optional[float] = None
    approver_role:  str


class POVendorQuoteInput(BaseModel):
    """Quote lumpsum 1 vendor untuk PO yang masih status 'open' (tender)."""
    vendor_id:      str
    quoted_amount:  float = Field(..., ge=0)
    quote_date:     Optional[date] = None
    payment_terms:  Optional[str] = None
    notes:          Optional[str] = None


class POSelectVendorInput(BaseModel):
    quote_id:   str
    tax_amount: float = 0


class MaterialGroupInput(BaseModel):
    entity_id:  str
    group_code: str
    group_name: str
    is_active:  bool = True


class ProcurementItemInput(BaseModel):
    entity_id:         str
    sku_code:          str
    item_name:         str
    item_type:         str = Field("expense", pattern="^(goods|services|asset|expense)$")
    material_group_id: str
    uom:               str = "unit"
    is_stock_managed:  bool = False
    is_active:         bool = True


class AccountMappingInput(BaseModel):
    entity_id:         str
    material_group_id: str
    account_code:      str


class EvaluateLineInput(BaseModel):
    entity_id:    str
    item_id:      str
    cost_center:  str
    qty:          float = Field(..., gt=0)
    unit_price:   float = Field(..., ge=0)


# ── PR Endpoints ──────────────────────────────────────────────────────────────

@router.post("/pr", summary="Buat Purchase Requisition (PR)")
def create_pr(
    payload: PRCreate,
    db:      Session = Depends(get_db),
    user=    Depends(get_current_active_user),
):
    pr_id = str(uuid4())
    req_no = _gen_pr_no(db, payload.entity_id)

    # Resolve & validasi referensi budget_line per item (kalau diisi)
    resolved_items = []
    effective_cost_center = payload.cost_center
    for item in payload.items:
        account_code = item.account_code
        budget_line_id = item.budget_line_id

        if item.item_id:
            # Account determination: item -> material_group -> account_expense_mapping -> COA.
            # Mengabaikan account_code/budget_line_id manual yang dikirim bersamaan item_id.
            if not payload.cost_center:
                raise HTTPException(
                    400,
                    f"Cost center wajib diisi di header PR kalau item {item.item_no} memakai item master (item_id)."
                )
            mapped = db.execute(
                text("""
                    SELECT m.account_code
                    FROM procurement_item pi
                    JOIN account_expense_mapping m ON m.material_group_id = pi.material_group_id
                        AND m.entity_id = pi.entity_id
                    WHERE pi.id = :item_id AND pi.entity_id = :eid
                """),
                {"item_id": item.item_id, "eid": payload.entity_id}
            ).fetchone()
            if not mapped:
                raise HTTPException(
                    400,
                    f"Item master pada item {item.item_no} belum punya mapping COA (account_expense_mapping) — hubungi Finance."
                )
            account_code = mapped.account_code
            effective_cost_center = payload.cost_center

            now = datetime.now()
            bl = db.execute(
                text("""
                    SELECT bl.id FROM budget_line bl
                    JOIN budget_period bp ON bp.id = bl.period_id
                    WHERE bl.entity_id = :eid AND bl.cost_center = :cc AND bl.account_code = :acc
                      AND bl.year = :yr AND bl.month = :mo AND bp.status IN ('released', 'closed')
                    ORDER BY bl.created_at DESC LIMIT 1
                """),
                {"eid": payload.entity_id, "cc": payload.cost_center, "acc": account_code,
                 "yr": now.year, "mo": now.month}
            ).fetchone()
            budget_line_id = str(bl.id) if bl else None

        elif item.budget_line_id:
            bl = db.execute(
                text("""
                    SELECT bl.id, bl.cost_center, bl.account_code, bp.status AS period_status
                    FROM budget_line bl
                    JOIN budget_period bp ON bp.id = bl.period_id
                    WHERE bl.id = :id
                """),
                {"id": item.budget_line_id}
            ).fetchone()
            if not bl:
                raise HTTPException(404, f"Referensi budget tidak ditemukan untuk item {item.item_no}")
            if bl.period_status not in ("released", "closed"):
                raise HTTPException(
                    400,
                    f"Budget yang dirujuk item {item.item_no} belum released (status periode: '{bl.period_status}')."
                )
            account_code = bl.account_code
            if not effective_cost_center:
                effective_cost_center = bl.cost_center

        resolved_items.append({"item": item, "account_code": account_code, "budget_line_id": budget_line_id})

    # Budget check semua item
    budget_status = "pending"
    budget_available = None
    budget_total = None

    if effective_cost_center:
        now = datetime.now()
        engine = BudgetEngine(db)
        items_for_check = [
            {"account_code": r["account_code"] or "", "total_amount": r["item"].qty * r["item"].unit_price}
            for r in resolved_items
        ]
        check = engine.check_pr_items(
            entity_id   = payload.entity_id,
            cost_center = effective_cost_center,
            items       = items_for_check,
            year        = now.year,
            month       = now.month,
        )
        budget_status = check["budget_check_status"]
        # Ambil available dari item pertama yang dicek
        if check["items"]:
            budget_available = check["items"][0].get("available")
            budget_total     = check["items"][0].get("budget")

        if budget_status == "blocked":
            raise HTTPException(
                400,
                f"Budget HABIS untuk cost center '{effective_cost_center}'. "
                f"Tersedia Rp {budget_available:,.0f}. "
                f"Ajukan budget supplement atau transfer terlebih dahulu."
            )

    # Insert PR
    db.execute(
        text("""
            INSERT INTO purchase_requisition (
                id, entity_id, req_no, department, cost_center,
                requested_by, required_date, purpose,
                budget_check_status, budget_available, budget_total,
                status, created_by, created_at, updated_at
            ) VALUES (
                :id, :eid, :no, :dept, :cc,
                :reqby, :reqdate, :purpose,
                :bstatus, :bavail, :btotal,
                'draft', :by, NOW(), NOW()
            )
        """),
        {
            "id": pr_id, "eid": payload.entity_id, "no": req_no,
            "dept": payload.department, "cc": effective_cost_center,
            "reqby": payload.requested_by, "reqdate": payload.required_date,
            "purpose": payload.purpose,
            "bstatus": budget_status, "bavail": budget_available, "btotal": budget_total,
            "by": user.get("email", "system"),
        }
    )

    # Insert items
    for r in resolved_items:
        item = r["item"]
        db.execute(
            text("""
                INSERT INTO pr_item (id, pr_id, item_no, description, category, unit,
                    qty, unit_price, account_code, budget_line_id, item_id, notes)
                VALUES (uuid_generate_v4(), :pr_id, :no, :desc, :cat, :unit,
                    :qty, :price, :acc, :bl_id, :item_id, :notes)
            """),
            {
                "pr_id": pr_id, "no": item.item_no, "desc": item.description,
                "cat": item.category, "unit": item.unit, "qty": item.qty,
                "price": item.unit_price, "acc": r["account_code"],
                "bl_id": r["budget_line_id"], "item_id": item.item_id, "notes": item.notes,
            }
        )

    db.commit()
    return {
        "pr_id":    pr_id,
        "req_no":   req_no,
        "status":   "draft",
        "budget_check_status": budget_status,
        "budget_available": budget_available,
        "item_count": len(payload.items),
    }


@router.get("/pr", dependencies=_viewer, summary="List PR")
def list_pr(
    entity_id:   str  = Query(...),
    status:      Optional[str] = Query(None),
    department:  Optional[str] = Query(None),
    db:          Session = Depends(get_db),
):
    conditions = ["pr.entity_id = :eid"]
    params: dict = {"eid": entity_id}
    if status:
        conditions.append("pr.status = :status")
        params["status"] = status
    if department:
        conditions.append("pr.department = :dept")
        params["dept"] = department

    where = " AND ".join(conditions)
    rows = db.execute(
        text(f"""
            SELECT pr.*, COUNT(pi.id) AS item_count,
                   COALESCE(SUM(pi.total_amount), 0) AS total_amount,
                   cur.approver_label AS current_approver,
                   po.id AS po_id, po.po_no AS po_no, po.status AS po_status,
                   COALESCE(poq.quote_count, 0) AS po_quote_count
            FROM purchase_requisition pr
            LEFT JOIN pr_item pi ON pi.pr_id = pr.id
            LEFT JOIN LATERAL (
                SELECT s.approver_label
                FROM approval_request r
                JOIN approval_step s ON s.request_id = r.id AND s.status = 'pending'
                WHERE r.document_type = 'purchase_requisition' AND r.document_id = pr.id
                ORDER BY s.level LIMIT 1
            ) cur ON TRUE
            LEFT JOIN purchase_order po ON po.id = pr.converted_to_po
            LEFT JOIN LATERAL (
                SELECT COUNT(*) AS quote_count FROM po_vendor_quote WHERE po_id = po.id
            ) poq ON TRUE
            WHERE {where}
            GROUP BY pr.id, cur.approver_label, po.id, po.po_no, po.status, poq.quote_count
            ORDER BY pr.created_at DESC
        """),
        params
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/pr/{pr_id}", dependencies=_viewer, summary="Detail PR + items")
def get_pr(pr_id: str, db: Session = Depends(get_db)):
    pr = db.execute(
        text("SELECT * FROM purchase_requisition WHERE id = :id"),
        {"id": pr_id}
    ).fetchone()
    if not pr:
        raise HTTPException(404, "PR tidak ditemukan")

    items = db.execute(
        text("""
            SELECT pi.*, bl.budget_no, bl.activity_description AS budget_activity_description
            FROM pr_item pi
            LEFT JOIN budget_line bl ON bl.id = pi.budget_line_id
            WHERE pi.pr_id = :id ORDER BY pi.item_no
        """),
        {"id": pr_id}
    ).fetchall()
    approvals = db.execute(
        text("SELECT * FROM pr_approval WHERE pr_id = :id ORDER BY level, acted_at"),
        {"id": pr_id}
    ).fetchall()
    steps = ApprovalEngine(db).get_steps("purchase_requisition", pr_id)

    return {
        **dict(pr._mapping),
        "items":     [dict(r._mapping) for r in items],
        "approvals": [dict(r._mapping) for r in approvals],
        "steps":     steps,
    }


@router.post("/pr/{pr_id}/submit", summary="Submit PR — mulai rantai approval berjenjang")
def submit_pr(
    pr_id: str,
    db:    Session = Depends(get_db),
    user=  Depends(get_current_active_user),
):
    missing = db.execute(
        text("SELECT item_no FROM pr_item WHERE pr_id = :id AND budget_line_id IS NULL ORDER BY item_no"),
        {"id": pr_id}
    ).fetchall()
    if missing:
        nos = ", ".join(str(r.item_no) for r in missing)
        raise HTTPException(
            400,
            f"Semua item PR harus punya referensi nomor budget sebelum disubmit "
            f"(item tanpa referensi: {nos})."
        )

    pr = db.execute(
        text("""
            SELECT pr.entity_id, pr.req_no, pr.cost_center,
                   COALESCE(SUM(pi.total_amount), 0) AS total_amount
            FROM purchase_requisition pr
            LEFT JOIN pr_item pi ON pi.pr_id = pr.id
            WHERE pr.id = :id
            GROUP BY pr.entity_id, pr.req_no, pr.cost_center
        """),
        {"id": pr_id}
    ).fetchone()
    if not pr:
        raise HTTPException(404, "PR tidak ditemukan")

    # Hard mitigation: kunci & reservasi budget (encumbrance) di level PR, sebelum
    # rantai approval berjalan — supaya 2 PR yang submit bersamaan tidak bisa
    # dobel-pakai sisa budget yang sama. Kalau ada line yang overlimit, batalkan
    # submit (PR tetap draft, tidak ada commitment yang dibuat).
    if pr.cost_center:
        items = db.execute(
            text("SELECT account_code, total_amount FROM pr_item WHERE pr_id = :id"),
            {"id": pr_id}
        ).fetchall()
        now = datetime.now()
        reservation = BudgetEngine(db).reserve_for_pr(
            pr_id       = pr_id,
            pr_no       = pr.req_no,
            entity_id   = str(pr.entity_id),
            cost_center = pr.cost_center,
            items       = [dict(r._mapping) for r in items],
            year        = now.year,
            month       = now.month,
        )
        if reservation["action"] == "block":
            db.rollback()
            raise HTTPException(
                400,
                "Submit PR dibatalkan — sisa budget tidak cukup untuk satu atau lebih item "
                "(kemungkinan terpakai PR/PO lain yang baru saja submit/approve). Cek ulang sisa budget."
            )

    _pr_transition(db, pr_id, "draft", "submitted",
                   submitted_by=user.get("email"), submitted_at="NOW()")

    result = ApprovalEngine(db).start(
        entity_id=str(pr.entity_id), document_type="purchase_requisition", document_id=pr_id,
        document_ref=pr.req_no, document_amount=float(pr.total_amount), requested_by_email=user.get("email"),
    )
    return {"pr_id": pr_id, "status": "submitted", "steps": result["steps"]}


@router.post("/pr/{pr_id}/approve", summary="Approve/reject PR (approval berjenjang via ApprovalEngine)")
def approve_pr(
    pr_id:   str,
    payload: PRApprovalAction,
    db:      Session = Depends(get_db),
    user=    Depends(get_current_active_user),
):
    pr = db.execute(text("SELECT id, status FROM purchase_requisition WHERE id = :id"), {"id": pr_id}).fetchone()
    if not pr:
        raise HTTPException(404, "PR tidak ditemukan")
    if pr.status != "submitted":
        raise HTTPException(400, f"PR status '{pr.status}' — harus 'submitted' untuk di-approve")

    result = ApprovalEngine(db).act(
        document_type="purchase_requisition", document_id=pr_id,
        acting_user=user, action=payload.action, notes=payload.notes,
    )
    _log_pr_approval(db, pr_id, result["level"], user.get("email", ""), payload.action, payload.notes)

    if result["is_final"]:
        new_status = result["request_status"]  # 'approved' atau 'rejected'
        if new_status == "approved":
            _pr_transition(db, pr_id, "submitted", "approved",
                           approved_by=user.get("email"), approved_at="NOW()")
            po_id, po_no = _open_po_from_pr(db, pr_id, user.get("email", "system"))
            _pr_transition(db, pr_id, "approved", "converted",
                           converted_to_po=po_id, converted_at="NOW()", converted_by=user.get("email"))
            return {"pr_id": pr_id, "status": "converted", "po_id": po_id, "po_no": po_no,
                    "message": f"PR disetujui — PO {po_no} otomatis dibuka untuk tender vendor."}
        _pr_transition(db, pr_id, "submitted", "rejected",
                       rejected_by=user.get("email"), rejection_reason=payload.notes)
        BudgetEngine(db).cancel_pr_commitment(pr_id)
        return {"pr_id": pr_id, "status": "rejected"}

    return {"pr_id": pr_id, "status": "submitted", "message": "Lanjut ke level approval berikutnya."}


def _open_po_from_pr(db, pr_id: str, created_by_email: str) -> tuple[str, str]:
    """Begitu PR final approved, otomatis bikin PO status 'open' (tender, tanpa vendor)
    dengan item di-copy dari PR. Tim procurement input vendor quote lalu pilih pemenang
    lewat /po/{id}/select-vendor — baru di situ PO pindah ke 'draft' dan lanjut flow
    submit/approve biasa."""
    pr = db.execute(text("SELECT * FROM purchase_requisition WHERE id = :id"), {"id": pr_id}).fetchone()
    pr_items = db.execute(
        text("SELECT * FROM pr_item WHERE pr_id = :id ORDER BY item_no"),
        {"id": pr_id}
    ).fetchall()

    po_id  = str(uuid4())
    po_no  = _gen_po_no(db, str(pr.entity_id))
    subtotal = sum(float(i.total_amount) for i in pr_items)

    db.execute(
        text("""
            INSERT INTO purchase_order (
                id, entity_id, po_no, pr_id, po_date,
                subtotal, tax_amount, total_amount, status, created_by, created_at, updated_at
            ) VALUES (
                :id, :eid, :no, :prid, CURRENT_DATE,
                :sub, 0, :sub, 'open', :by, NOW(), NOW()
            )
        """),
        {"id": po_id, "eid": str(pr.entity_id), "no": po_no, "prid": pr_id,
         "sub": subtotal, "by": created_by_email}
    )

    for item in pr_items:
        db.execute(
            text("""
                INSERT INTO po_item (id, po_id, pr_item_id, item_no, description, category,
                    unit, qty, unit_price, account_code, cost_center)
                VALUES (uuid_generate_v4(), :po_id, :pr_item_id, :no, :desc, :cat,
                    :unit, :qty, :price, :acc, :cc)
            """),
            {
                "po_id": po_id, "pr_item_id": str(item.id),
                "no": item.item_no, "desc": item.description, "cat": item.category,
                "unit": item.unit, "qty": float(item.qty), "price": float(item.unit_price),
                "acc": item.account_code, "cc": pr.cost_center,
            }
        )

    db.commit()
    return po_id, po_no


# ── PO Endpoints ───────────────────────────────────────────────────────────────

@router.post("/po", dependencies=_finance, summary="Buat Purchase Order (manual atau dari PR)")
def create_po(
    payload: POCreate,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("finance")),
):
    # Jika ada pr_id, validasi PR sudah approved
    if payload.pr_id:
        pr = db.execute(
            text("SELECT id, status FROM purchase_requisition WHERE id = :id"),
            {"id": payload.pr_id}
        ).fetchone()
        if not pr:
            raise HTTPException(404, "PR tidak ditemukan")
        if pr.status != "approved":
            raise HTTPException(400, f"PR status '{pr.status}' — hanya PR approved yang bisa dikonversi ke PO")

    po_id  = str(uuid4())
    po_no  = _gen_po_no(db, payload.entity_id)
    subtotal = sum(item.qty * item.unit_price for item in payload.items)
    total_amount = subtotal + payload.tax_amount

    db.execute(
        text("""
            INSERT INTO purchase_order (
                id, entity_id, po_no, pr_id, vendor_id,
                po_date, required_date, delivery_address, payment_terms, currency,
                subtotal, tax_amount, total_amount,
                status, notes, created_by, created_at, updated_at
            ) VALUES (
                :id, :eid, :no, :prid, :vid,
                :dt, :reqdt, :addr, :terms, :curr,
                :sub, :tax, :total,
                'draft', :notes, :by, NOW(), NOW()
            )
        """),
        {
            "id": po_id, "eid": payload.entity_id, "no": po_no,
            "prid": payload.pr_id, "vid": payload.vendor_id,
            "dt": payload.po_date, "reqdt": payload.required_date,
            "addr": payload.delivery_address, "terms": payload.payment_terms,
            "curr": payload.currency,
            "sub": subtotal, "tax": payload.tax_amount, "total": total_amount,
            "notes": payload.notes, "by": user.get("email", "system"),
        }
    )

    # Insert PO items
    for item in payload.items:
        db.execute(
            text("""
                INSERT INTO po_item (id, po_id, item_no, description, category,
                    unit, qty, unit_price, account_code, cost_center)
                VALUES (uuid_generate_v4(), :po_id, :no, :desc, :cat,
                    :unit, :qty, :price, :acc, :cc)
            """),
            {
                "po_id": po_id, "no": item.item_no, "desc": item.description,
                "cat": item.category, "unit": item.unit, "qty": item.qty,
                "price": item.unit_price, "acc": item.account_code, "cc": item.cost_center,
            }
        )

    db.commit()
    return {"po_id": po_id, "po_no": po_no, "total_amount": total_amount, "status": "draft"}


@router.get("/po/{po_id}/quotes", dependencies=_viewer, summary="List vendor quote untuk PO open (tender)")
def list_po_quotes(po_id: str, db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT q.*, v.vendor_name
            FROM po_vendor_quote q
            JOIN vendor v ON v.id = q.vendor_id
            WHERE q.po_id = :id
            ORDER BY q.quoted_amount ASC
        """),
        {"id": po_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/po/{po_id}/quotes", dependencies=_finance, summary="Tambah/update quote vendor untuk PO open (tender)")
def upsert_po_quote(
    po_id:   str,
    payload: POVendorQuoteInput,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("finance")),
):
    po = db.execute(text("SELECT id, status FROM purchase_order WHERE id = :id"), {"id": po_id}).fetchone()
    if not po:
        raise HTTPException(404, "PO tidak ditemukan")
    if po.status != "open":
        raise HTTPException(400, f"PO status '{po.status}' — quote vendor hanya bisa diinput saat status 'open'")

    quote_id = db.execute(
        text("""
            INSERT INTO po_vendor_quote (id, po_id, vendor_id, quoted_amount, quote_date, payment_terms, notes, created_by)
            VALUES (uuid_generate_v4(), :po_id, :vid, :amt, :qdate, :terms, :notes, :by)
            ON CONFLICT (po_id, vendor_id) DO UPDATE SET
                quoted_amount = EXCLUDED.quoted_amount, quote_date = EXCLUDED.quote_date,
                payment_terms = EXCLUDED.payment_terms, notes = EXCLUDED.notes, updated_at = NOW()
            RETURNING id
        """),
        {"po_id": po_id, "vid": payload.vendor_id, "amt": payload.quoted_amount,
         "qdate": payload.quote_date, "terms": payload.payment_terms, "notes": payload.notes,
         "by": user.get("email", "system")}
    ).scalar()
    db.commit()
    return {"quote_id": str(quote_id), "message": "Quote vendor tersimpan."}


@router.delete("/po/{po_id}/quotes/{quote_id}", dependencies=_finance, summary="Hapus quote vendor dari PO open")
def delete_po_quote(po_id: str, quote_id: str, db: Session = Depends(get_db)):
    po = db.execute(text("SELECT id, status FROM purchase_order WHERE id = :id"), {"id": po_id}).fetchone()
    if not po:
        raise HTTPException(404, "PO tidak ditemukan")
    if po.status != "open":
        raise HTTPException(400, f"PO status '{po.status}' — quote vendor hanya bisa diubah saat status 'open'")
    db.execute(text("DELETE FROM po_vendor_quote WHERE id = :id AND po_id = :po_id"), {"id": quote_id, "po_id": po_id})
    db.commit()
    return {"message": "Quote vendor dihapus."}


@router.post("/po/{po_id}/select-vendor", dependencies=_finance,
             summary="Pilih vendor pemenang tender — PO pindah dari 'open' ke 'draft'")
def select_po_vendor(
    po_id:   str,
    payload: POSelectVendorInput,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("finance")),
):
    po = db.execute(text("SELECT * FROM purchase_order WHERE id = :id"), {"id": po_id}).fetchone()
    if not po:
        raise HTTPException(404, "PO tidak ditemukan")
    if po.status != "open":
        raise HTTPException(400, f"PO status '{po.status}' — harus 'open' untuk memilih vendor pemenang")

    quote = db.execute(
        text("SELECT * FROM po_vendor_quote WHERE id = :id AND po_id = :po_id"),
        {"id": payload.quote_id, "po_id": po_id}
    ).fetchone()
    if not quote:
        raise HTTPException(404, "Quote tidak ditemukan untuk PO ini")

    # Skala harga per-item secara proporsional supaya basis encumbrance budget
    # mengikuti nilai komersial hasil tender (bukan estimasi harga PR awal),
    # sambil tetap menjaga alokasi cost_center/account_code per item.
    items = db.execute(text("SELECT * FROM po_item WHERE po_id = :id"), {"id": po_id}).fetchall()
    original_subtotal = sum(float(i.total_amount) for i in items)
    scale = float(quote.quoted_amount) / original_subtotal if original_subtotal > 0 else 1.0
    for item in items:
        db.execute(
            text("UPDATE po_item SET unit_price = unit_price * :scale WHERE id = :id"),
            {"scale": scale, "id": item.id}
        )

    subtotal = float(quote.quoted_amount)
    total_amount = subtotal + payload.tax_amount

    db.execute(text("UPDATE po_vendor_quote SET is_selected = FALSE WHERE po_id = :po_id"), {"po_id": po_id})
    db.execute(text("UPDATE po_vendor_quote SET is_selected = TRUE WHERE id = :id"), {"id": quote.id})

    db.execute(
        text("""
            UPDATE purchase_order SET
                vendor_id = :vid, payment_terms = COALESCE(:terms, payment_terms),
                subtotal = :sub, tax_amount = :tax, total_amount = :total,
                status = 'draft', updated_at = NOW()
            WHERE id = :id
        """),
        {"vid": quote.vendor_id, "terms": quote.payment_terms, "sub": subtotal,
         "tax": payload.tax_amount, "total": total_amount, "id": po_id}
    )
    db.commit()

    return {
        "po_id": po_id, "vendor_id": str(quote.vendor_id),
        "subtotal": subtotal, "total_amount": total_amount, "status": "draft",
        "message": "Vendor pemenang dipilih. PO siap di-submit untuk approval.",
    }


@router.get("/po", dependencies=_viewer, summary="List PO")
def list_po(
    entity_id: str = Query(...),
    status:    Optional[str] = Query(None),
    vendor_id: Optional[str] = Query(None),
    db:        Session = Depends(get_db),
):
    conditions = ["po.entity_id = :eid"]
    params: dict = {"eid": entity_id}
    if status:
        conditions.append("po.status = :status")
        params["status"] = status
    if vendor_id:
        conditions.append("po.vendor_id = :vid")
        params["vid"] = vendor_id

    where = " AND ".join(conditions)
    rows = db.execute(
        text(f"""
            SELECT po.*, v.vendor_name, v.email AS vendor_email,
                   COALESCE(poq.quote_count, 0) AS quote_count
            FROM purchase_order po
            LEFT JOIN vendor v ON v.id = po.vendor_id
            LEFT JOIN LATERAL (
                SELECT COUNT(*) AS quote_count FROM po_vendor_quote WHERE po_id = po.id
            ) poq ON TRUE
            WHERE {where}
            ORDER BY po.created_at DESC
        """),
        params
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/po/{po_id}", dependencies=_viewer, summary="Detail PO + items + approval trail")
def get_po(po_id: str, db: Session = Depends(get_db)):
    po = db.execute(
        text("""
            SELECT po.*, v.vendor_name, v.email AS vendor_email,
                   v.npwp AS vendor_npwp, v.address AS vendor_address
            FROM purchase_order po
            LEFT JOIN vendor v ON v.id = po.vendor_id
            WHERE po.id = :id
        """),
        {"id": po_id}
    ).fetchone()
    if not po:
        raise HTTPException(404, "PO tidak ditemukan")

    items = db.execute(
        text("SELECT * FROM po_item WHERE po_id = :id ORDER BY item_no"),
        {"id": po_id}
    ).fetchall()
    approvals = db.execute(
        text("SELECT * FROM po_approval WHERE po_id = :id ORDER BY level, acted_at"),
        {"id": po_id}
    ).fetchall()
    quotes = db.execute(
        text("""
            SELECT q.*, v.vendor_name
            FROM po_vendor_quote q
            JOIN vendor v ON v.id = q.vendor_id
            WHERE q.po_id = :id
            ORDER BY q.quoted_amount ASC
        """),
        {"id": po_id}
    ).fetchall()

    return {
        **dict(po._mapping),
        "items":     [dict(r._mapping) for r in items],
        "approvals": [dict(r._mapping) for r in approvals],
        "quotes":    [dict(r._mapping) for r in quotes],
    }


@router.post("/po/{po_id}/submit", dependencies=_finance, summary="Submit PO — mulai approval (matriks nominal via ApprovalEngine)")
def submit_po(
    po_id: str,
    db:    Session = Depends(get_db),
    user=  Depends(require_min_role("finance")),
):
    po = db.execute(text("SELECT entity_id, po_no, total_amount FROM purchase_order WHERE id = :id"), {"id": po_id}).fetchone()
    if not po:
        raise HTTPException(404, "PO tidak ditemukan")

    _po_transition(db, po_id, "draft", "submitted",
                   submitted_by=user.get("email"), submitted_at="NOW()")

    ApprovalEngine(db).start(
        entity_id=str(po.entity_id), document_type="purchase_order", document_id=po_id,
        document_ref=po.po_no, document_amount=float(po.total_amount), requested_by_email=user.get("email"),
    )
    return {"po_id": po_id, "status": "submitted"}


@router.post("/po/{po_id}/approve", summary="Approve/reject PO (matriks kewenangan via ApprovalEngine)")
def approve_po(
    po_id:   str,
    payload: POApprovalAction,
    db:      Session = Depends(get_db),
    user=    Depends(get_current_active_user),
):
    """
    Approval PO berdasarkan matrix (po_approval_matrix), dijalankan lewat ApprovalEngine
    strategi 'amount_matrix' — perilaku sama seperti sebelumnya (1 kali approve oleh role
    yang sesuai nominal), hanya jalur internalnya sekarang lewat tabel approval_request/step
    yang generic (reusable untuk modul lain).
    """
    po = db.execute(
        text("SELECT * FROM purchase_order WHERE id = :id"),
        {"id": po_id}
    ).fetchone()
    if not po:
        raise HTTPException(404, "PO tidak ditemukan")
    if po.status != "submitted":
        raise HTTPException(400, f"PO status '{po.status}' — harus 'submitted' untuk di-approve")

    result = ApprovalEngine(db).act(
        document_type="purchase_order", document_id=po_id,
        acting_user=user, action=payload.action, notes=payload.notes,
    )
    required_role = _get_required_approver_role(db, str(po.entity_id), float(po.total_amount))
    _log_po_approval(db, po_id, result["level"], required_role, user.get("email", ""), payload.action, payload.notes)

    if not result["is_final"]:
        # Strategi amount_matrix selalu 1 step — seharusnya tidak pernah sampai sini.
        return {"po_id": po_id, "status": "submitted", "message": "Lanjut ke level approval berikutnya."}

    if result["request_status"] == "approved":
        # Buat budget commitment (encumbrance)
        items = db.execute(
            text("SELECT * FROM po_item WHERE po_id = :id"),
            {"id": po_id}
        ).fetchall()
        items_data = [dict(r._mapping) for r in items]

        engine    = BudgetEngine(db)
        commit_id = engine.create_po_commitment(
            po_id     = po_id,
            po_no     = po.po_no,
            entity_id = str(po.entity_id),
            items     = items_data,
            year      = po.po_date.year,
            month     = po.po_date.month,
        )

        db.execute(
            text("""
                UPDATE purchase_order SET
                    status = 'approved', approved_by = :by, approved_at = NOW(),
                    commitment_id = :cid, updated_at = NOW()
                WHERE id = :id
            """),
            {"id": po_id, "by": user.get("email"), "cid": commit_id or None}
        )
        if po.pr_id:
            # PO approved dengan commitment riil (hasil tender) — lepas reservasi
            # sementara yang dibuat saat PR submit, supaya tidak double-count.
            BudgetEngine(db).cancel_pr_commitment(str(po.pr_id))
    else:
        db.execute(
            text("""
                UPDATE purchase_order SET
                    status = 'rejected', rejected_by = :by, rejection_reason = :notes, updated_at = NOW()
                WHERE id = :id
            """),
            {"id": po_id, "by": user.get("email"), "notes": payload.notes}
        )
        if po.pr_id:
            # PO turunan PR ditolak — seluruh upaya procurement gagal, lepas reservasi PR.
            BudgetEngine(db).cancel_pr_commitment(str(po.pr_id))

    db.commit()
    return {"po_id": po_id, "status": result["request_status"]}


@router.post("/po/{po_id}/send", dependencies=_finance,
             summary="Tandai PO sebagai sudah dikirim ke vendor")
def mark_po_sent(
    po_id:      str,
    sent_email: str = Query(..., description="Email vendor yang dituju"),
    db:         Session = Depends(get_db),
    user=       Depends(require_min_role("finance")),
):
    po = db.execute(
        text("SELECT id, status FROM purchase_order WHERE id = :id"),
        {"id": po_id}
    ).fetchone()
    if not po or po.status != "approved":
        raise HTTPException(400, "PO harus berstatus 'approved' sebelum dikirim")

    db.execute(
        text("""
            UPDATE purchase_order SET
                status = 'sent', sent_at = NOW(), sent_to_email = :email, updated_at = NOW()
            WHERE id = :id
        """),
        {"id": po_id, "email": sent_email}
    )
    db.commit()

    # Dalam produksi: trigger email PDF ke vendor via background task
    return {
        "po_id":    po_id,
        "status":   "sent",
        "sent_to":  sent_email,
        "message":  "PO telah dikirim ke vendor. Background task akan mengirim PDF.",
    }


@router.post("/po/{po_id}/cancel", dependencies=_finance, summary="Batalkan PO + lepas commitment")
def cancel_po(
    po_id:  str,
    reason: str = Query(...),
    db:     Session = Depends(get_db),
    user=   Depends(require_min_role("finance")),
):
    po = db.execute(
        text("SELECT id, status FROM purchase_order WHERE id = :id"),
        {"id": po_id}
    ).fetchone()
    if not po:
        raise HTTPException(404, "PO tidak ditemukan")
    if po.status in ("closed", "cancelled"):
        raise HTTPException(400, f"PO sudah '{po.status}'")

    # Lepas commitment
    engine = BudgetEngine(db)
    engine.cancel_commitment(po_id)

    db.execute(
        text("""
            UPDATE purchase_order SET
                status = 'cancelled', rejection_reason = :reason, updated_at = NOW()
            WHERE id = :id
        """),
        {"id": po_id, "reason": reason}
    )
    db.commit()
    return {"po_id": po_id, "status": "cancelled", "commitment": "released"}


# ── Goods Receipt ──────────────────────────────────────────────────────────────

@router.post("/receipt", dependencies=_finance, summary="Catat Goods Receipt (GR)")
def create_receipt(
    payload: GoodsReceiptCreate,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("finance")),
):
    po = db.execute(
        text("SELECT id, status FROM purchase_order WHERE id = :id"),
        {"id": payload.po_id}
    ).fetchone()
    if not po:
        raise HTTPException(404, "PO tidak ditemukan")
    if po.status not in ("sent", "partial_received", "approved"):
        raise HTTPException(400, f"PO status '{po.status}' — tidak bisa dicatat penerimaan")

    gr_id  = str(uuid4())
    gr_no  = _gen_gr_no(db, payload.entity_id)

    db.execute(
        text("""
            INSERT INTO po_receipt (id, po_id, entity_id, receipt_no, receipt_date, received_by, notes)
            VALUES (:id, :po_id, :eid, :no, :dt, :by, :notes)
        """),
        {
            "id": gr_id, "po_id": payload.po_id, "eid": payload.entity_id,
            "no": gr_no, "dt": payload.receipt_date,
            "by": payload.received_by or user.get("email"), "notes": payload.notes,
        }
    )

    total_items   = 0
    fully_received = True

    for item in payload.items:
        db.execute(
            text("""
                INSERT INTO po_receipt_item (id, receipt_id, po_item_id, received_qty)
                VALUES (uuid_generate_v4(), :rid, :piid, :qty)
            """),
            {"rid": gr_id, "piid": item.po_item_id, "qty": item.received_qty}
        )
        # Update po_item.received_qty
        db.execute(
            text("""
                UPDATE po_item SET received_qty = received_qty + :qty
                WHERE id = :id
            """),
            {"id": item.po_item_id, "qty": item.received_qty}
        )
        total_items += 1

    # Periksa apakah semua item sudah diterima penuh
    remaining = db.execute(
        text("""
            SELECT COUNT(*) FROM po_item
            WHERE po_id = :id AND qty > received_qty
        """),
        {"id": payload.po_id}
    ).scalar()

    new_po_status = "received" if (remaining or 0) == 0 else "partial_received"
    db.execute(
        text("UPDATE purchase_order SET status = :s, updated_at = NOW() WHERE id = :id"),
        {"id": payload.po_id, "s": new_po_status}
    )
    db.commit()

    return {
        "receipt_id":  gr_id,
        "receipt_no":  gr_no,
        "po_status":   new_po_status,
        "items_received": total_items,
    }


# ── PO Approval Matrix ─────────────────────────────────────────────────────────

@router.post("/approval-matrix", dependencies=_admin,
             summary="Set matriks approval PO (level + nominal)")
def upsert_approval_matrix(
    entries: list[ApprovalMatrixEntry],
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("admin")),
):
    for e in entries:
        db.execute(
            text("""
                INSERT INTO po_approval_matrix (id, entity_id, level, threshold_name,
                    min_amount, max_amount, approver_role)
                VALUES (uuid_generate_v4(), :eid, :level, :name, :min, :max, :role)
                ON CONFLICT (entity_id, level) DO UPDATE SET
                    threshold_name = EXCLUDED.threshold_name,
                    min_amount     = EXCLUDED.min_amount,
                    max_amount     = EXCLUDED.max_amount,
                    approver_role  = EXCLUDED.approver_role
            """),
            {
                "eid": e.entity_id, "level": e.level, "name": e.threshold_name,
                "min": e.min_amount, "max": e.max_amount, "role": e.approver_role,
            }
        )
    db.commit()
    return {"updated": len(entries)}


@router.get("/approval-matrix", dependencies=_viewer, summary="Lihat matriks approval PO")
def get_approval_matrix(entity_id: str = Query(...), db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT * FROM po_approval_matrix
            WHERE entity_id = :eid AND is_active = TRUE
            ORDER BY level
        """),
        {"eid": entity_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Traceability ───────────────────────────────────────────────────────────────

@router.get("/traceability", dependencies=_viewer, summary="PR → PO traceability report")
def traceability(
    entity_id: str  = Query(...),
    db:        Session = Depends(get_db),
):
    rows = db.execute(
        text("""
            SELECT * FROM vw_pr_po_traceability
            WHERE (SELECT entity_id FROM purchase_requisition WHERE req_no = vw_pr_po_traceability.req_no LIMIT 1) = :eid
            ORDER BY req_no DESC
        """),
        {"eid": entity_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/open-po", dependencies=_viewer, summary="List PO open (belum closed/cancelled)")
def open_po_report(entity_id: str = Query(...), db: Session = Depends(get_db)):
    rows = db.execute(
        text("SELECT * FROM vw_po_open WHERE entity_id = :eid ORDER BY po_date DESC"),
        {"eid": entity_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Account Determination Engine: Material Group / Item Master / Mapping ──────

@router.get("/material-groups", dependencies=_viewer, summary="List material group")
def list_material_groups(entity_id: str = Query(...), db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT mg.*, m.account_code
            FROM material_group mg
            LEFT JOIN account_expense_mapping m ON m.material_group_id = mg.id
            WHERE mg.entity_id = :eid
            ORDER BY mg.group_code
        """),
        {"eid": entity_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/material-groups", dependencies=_finance, summary="Buat material group")
def create_material_group(
    payload: MaterialGroupInput,
    db:      Session = Depends(get_db),
    user=    Depends(get_current_active_user),
):
    existing = db.execute(
        text("SELECT id FROM material_group WHERE entity_id = :eid AND group_code = :code"),
        {"eid": payload.entity_id, "code": payload.group_code}
    ).fetchone()
    if existing:
        raise HTTPException(400, f"Kode material group '{payload.group_code}' sudah dipakai.")

    row = db.execute(
        text("""
            INSERT INTO material_group (id, entity_id, group_code, group_name, is_active, created_by)
            VALUES (uuid_generate_v4(), :eid, :code, :name, :active, :by)
            RETURNING id
        """),
        {"eid": payload.entity_id, "code": payload.group_code, "name": payload.group_name,
         "active": payload.is_active, "by": user.get("email", "system")}
    ).fetchone()
    db.commit()
    return {"id": str(row.id), "group_code": payload.group_code}


@router.get("/items", dependencies=_viewer, summary="List item master (SKU)")
def list_items(
    entity_id:         str = Query(...),
    material_group_id: Optional[str] = Query(None),
    search:            Optional[str] = Query(None),
    db:                Session = Depends(get_db),
):
    conditions = ["pi.entity_id = :eid", "pi.is_active = TRUE"]
    params: dict = {"eid": entity_id}
    if material_group_id:
        conditions.append("pi.material_group_id = :mgid")
        params["mgid"] = material_group_id
    if search:
        conditions.append("(pi.sku_code ILIKE :q OR pi.item_name ILIKE :q)")
        params["q"] = f"%{search}%"
    where = " AND ".join(conditions)

    rows = db.execute(
        text(f"""
            SELECT pi.*, mg.group_code, mg.group_name, m.account_code
            FROM procurement_item pi
            JOIN material_group mg ON mg.id = pi.material_group_id
            LEFT JOIN account_expense_mapping m ON m.material_group_id = pi.material_group_id AND m.entity_id = pi.entity_id
            WHERE {where}
            ORDER BY pi.sku_code
        """),
        params
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/items", dependencies=_finance, summary="Buat item master (SKU)")
def create_item(
    payload: ProcurementItemInput,
    db:      Session = Depends(get_db),
    user=    Depends(get_current_active_user),
):
    existing = db.execute(
        text("SELECT id FROM procurement_item WHERE entity_id = :eid AND sku_code = :sku"),
        {"eid": payload.entity_id, "sku": payload.sku_code}
    ).fetchone()
    if existing:
        raise HTTPException(400, f"SKU '{payload.sku_code}' sudah dipakai.")

    row = db.execute(
        text("""
            INSERT INTO procurement_item (
                id, entity_id, sku_code, item_name, item_type,
                material_group_id, uom, is_stock_managed, is_active, created_by
            ) VALUES (
                uuid_generate_v4(), :eid, :sku, :name, :type,
                :mgid, :uom, :stock, :active, :by
            ) RETURNING id
        """),
        {"eid": payload.entity_id, "sku": payload.sku_code, "name": payload.item_name,
         "type": payload.item_type, "mgid": payload.material_group_id, "uom": payload.uom,
         "stock": payload.is_stock_managed, "active": payload.is_active, "by": user.get("email", "system")}
    ).fetchone()
    db.commit()
    return {"id": str(row.id), "sku_code": payload.sku_code}


@router.get("/account-mapping", dependencies=_viewer, summary="List mapping material group -> COA")
def list_account_mapping(entity_id: str = Query(...), db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT m.*, mg.group_code, mg.group_name, coa.account_name
            FROM account_expense_mapping m
            JOIN material_group mg ON mg.id = m.material_group_id
            LEFT JOIN chart_of_accounts coa ON coa.entity_id = m.entity_id AND coa.account_code = m.account_code
            WHERE m.entity_id = :eid
            ORDER BY mg.group_code
        """),
        {"eid": entity_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/account-mapping", dependencies=_finance, summary="Set/update mapping material group -> COA (1 group = 1 COA)")
def upsert_account_mapping(
    payload: AccountMappingInput,
    db:      Session = Depends(get_db),
    user=    Depends(get_current_active_user),
):
    coa = db.execute(
        text("SELECT account_code FROM chart_of_accounts WHERE entity_id = :eid AND account_code = :acc"),
        {"eid": payload.entity_id, "acc": payload.account_code}
    ).fetchone()
    if not coa:
        raise HTTPException(404, f"COA '{payload.account_code}' tidak ditemukan di entity ini.")

    db.execute(
        text("""
            INSERT INTO account_expense_mapping (id, entity_id, material_group_id, account_code, created_by)
            VALUES (uuid_generate_v4(), :eid, :mgid, :acc, :by)
            ON CONFLICT (entity_id, material_group_id)
            DO UPDATE SET account_code = :acc, updated_at = NOW()
        """),
        {"eid": payload.entity_id, "mgid": payload.material_group_id, "acc": payload.account_code,
         "by": user.get("email", "system")}
    )
    db.commit()
    return {"material_group_id": payload.material_group_id, "account_code": payload.account_code}


@router.post("/pr/evaluate-line", dependencies=_viewer,
             summary="Evaluasi 1 baris PR (item+cost_center+qty+harga) realtime — account determination + sisa budget")
def evaluate_pr_line(payload: EvaluateLineInput, db: Session = Depends(get_db)):
    item = db.execute(
        text("""
            SELECT pi.id, pi.item_name, pi.material_group_id
            FROM procurement_item pi
            WHERE pi.id = :id AND pi.entity_id = :eid AND pi.is_active = TRUE
        """),
        {"id": payload.item_id, "eid": payload.entity_id}
    ).fetchone()
    if not item:
        return {"status": "BLOCKED", "message": "Item tidak ditemukan atau tidak aktif."}

    mapping = db.execute(
        text("SELECT account_code FROM account_expense_mapping WHERE entity_id = :eid AND material_group_id = :mgid"),
        {"eid": payload.entity_id, "mgid": item.material_group_id}
    ).fetchone()
    if not mapping:
        return {
            "status": "BLOCKED",
            "message": f"Item '{item.item_name}' belum punya mapping COA — hubungi Finance untuk set account_expense_mapping.",
        }
    account_code = mapping.account_code

    now = datetime.now()
    bl = db.execute(
        text("""
            SELECT bl.id, bl.budget_no FROM budget_line bl
            JOIN budget_period bp ON bp.id = bl.period_id
            WHERE bl.entity_id = :eid AND bl.cost_center = :cc AND bl.account_code = :acc
              AND bl.year = :yr AND bl.month = :mo AND bp.status IN ('released', 'closed')
            ORDER BY bl.created_at DESC LIMIT 1
        """),
        {"eid": payload.entity_id, "cc": payload.cost_center, "acc": account_code,
         "yr": now.year, "mo": now.month}
    ).fetchone()
    if not bl:
        return {
            "status": "BLOCKED",
            "coa_id": account_code,
            "message": f"Tidak ada budget_line untuk cost center '{payload.cost_center}' + COA '{account_code}' periode {now.year}-{now.month:02d}.",
        }

    requested = Decimal(str(payload.qty)) * Decimal(str(payload.unit_price))
    check = BudgetEngine(db).check(
        entity_id=payload.entity_id, cost_center=payload.cost_center, account_code=account_code,
        year=now.year, month=now.month, requested=requested,
    )

    status = "OVERLIMIT" if check.action == "block" else "SUCCESS"
    return {
        "status":          status,
        "coa_id":          account_code,
        "budget_code":     bl.budget_no,
        "remaining_budget": float(check.available),
        "message":         check.message,
    }


# ── Helpers ────────────────────────────────────────────────────────────────────

def _gen_pr_no(db, entity_id: str) -> str:
    now    = datetime.now()
    prefix = f"PR/{now.year}/{now.month:02d}"
    count  = db.execute(
        text("SELECT COUNT(*) FROM purchase_requisition WHERE req_no LIKE :p"),
        {"p": f"{prefix}/%"}
    ).scalar()
    return f"{prefix}/{(count or 0) + 1:04d}"


def _gen_po_no(db, entity_id: str) -> str:
    now    = datetime.now()
    prefix = f"PO/{now.year}/{now.month:02d}"
    count  = db.execute(
        text("SELECT COUNT(*) FROM purchase_order WHERE po_no LIKE :p"),
        {"p": f"{prefix}/%"}
    ).scalar()
    return f"{prefix}/{(count or 0) + 1:04d}"


def _gen_gr_no(db, entity_id: str) -> str:
    now    = datetime.now()
    prefix = f"GR/{now.year}/{now.month:02d}"
    count  = db.execute(
        text("SELECT COUNT(*) FROM po_receipt WHERE receipt_no LIKE :p"),
        {"p": f"{prefix}/%"}
    ).scalar()
    return f"{prefix}/{(count or 0) + 1:04d}"


def _pr_transition(db, pr_id, expected, new_status, **fields):
    row = db.execute(
        text("SELECT id, status FROM purchase_requisition WHERE id = :id"),
        {"id": pr_id}
    ).fetchone()
    if not row:
        raise HTTPException(404, "PR tidak ditemukan")
    if row.status != expected:
        raise HTTPException(400, f"PR status '{row.status}', dibutuhkan '{expected}'")

    sets = ["status = :new_status", "updated_at = NOW()"]
    params: dict = {"id": pr_id, "new_status": new_status}
    for k, v in fields.items():
        if v == "NOW()":
            sets.append(f"{k} = NOW()")
        else:
            sets.append(f"{k} = :{k}")
            params[k] = v

    db.execute(
        text(f"UPDATE purchase_requisition SET {', '.join(sets)} WHERE id = :id"),
        params
    )
    db.commit()


def _po_transition(db, po_id, expected, new_status, **fields):
    row = db.execute(
        text("SELECT id, status FROM purchase_order WHERE id = :id"),
        {"id": po_id}
    ).fetchone()
    if not row:
        raise HTTPException(404, "PO tidak ditemukan")
    if row.status != expected:
        raise HTTPException(400, f"PO status '{row.status}', dibutuhkan '{expected}'")

    sets = ["status = :new_status", "updated_at = NOW()"]
    params: dict = {"id": po_id, "new_status": new_status}
    for k, v in fields.items():
        if v == "NOW()":
            sets.append(f"{k} = NOW()")
        else:
            sets.append(f"{k} = :{k}")
            params[k] = v

    db.execute(
        text(f"UPDATE purchase_order SET {', '.join(sets)} WHERE id = :id"),
        params
    )
    db.commit()


def _get_required_approver_role(db, entity_id: str, amount: float) -> str:
    """Tentukan role approver yang dibutuhkan berdasarkan matriks approval."""
    rows = db.execute(
        text("""
            SELECT level, approver_role, min_amount, max_amount
            FROM po_approval_matrix
            WHERE entity_id = :eid AND is_active = TRUE
              AND min_amount <= :amt
              AND (max_amount IS NULL OR max_amount >= :amt)
            ORDER BY level DESC LIMIT 1
        """),
        {"eid": entity_id, "amt": amount}
    ).fetchone()
    return rows.approver_role if rows else "finance"


def _log_pr_approval(db, pr_id, level, approver, action, notes):
    db.execute(
        text("""
            INSERT INTO pr_approval (id, pr_id, level, approver, action, notes)
            VALUES (uuid_generate_v4(), :pr_id, :level, :approver, :action, :notes)
        """),
        {"pr_id": pr_id, "level": level, "approver": approver, "action": action, "notes": notes}
    )
    db.commit()


def _log_po_approval(db, po_id, level, required_role, approver, action, notes):
    db.execute(
        text("""
            INSERT INTO po_approval (id, po_id, level, required_role, approver, action, notes)
            VALUES (uuid_generate_v4(), :po_id, :level, :role, :approver, :action, :notes)
        """),
        {"po_id": po_id, "level": level, "role": required_role,
         "approver": approver, "action": action, "notes": notes}
    )
    db.commit()
