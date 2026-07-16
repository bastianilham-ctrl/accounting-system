# modules/budget_router.py
# Budget Management API — period, lines, approval, transfer, variance report

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text

from core.database import get_db
from modules.auth import require_min_role
from modules.budget_engine import BudgetEngine

router = APIRouter(prefix="/budget", tags=["Budget Management"])

_viewer  = [Depends(require_min_role("viewer"))]
_finance = [Depends(require_min_role("finance"))]
_admin   = [Depends(require_min_role("admin"))]


# ── Request schemas ────────────────────────────────────────────────────────────

class BudgetPeriodCreate(BaseModel):
    entity_id:      str
    fiscal_year:    int
    budget_version: str  = "ORIGINAL"
    description:    Optional[str] = None
    control_mode:   str  = Field("soft", pattern="^(hard|soft|off)$")


class BudgetLineInput(BaseModel):
    cost_center:          str
    account_code:         str
    month:                int = Field(..., ge=1, le=12)
    budgeted_amount:      float
    activity_description: str
    notes:                Optional[str] = None


class BudgetLineBulk(BaseModel):
    """Upload semua baris anggaran sekaligus (bottom-up atau top-down)."""
    lines: list[BudgetLineInput]


class BudgetTransferCreate(BaseModel):
    entity_id:         str
    fiscal_year:       int
    month:             int = Field(..., ge=1, le=12)
    transfer_date:     date
    from_period_id:    str
    from_cost_center:  str
    from_account_code: str
    to_period_id:      str
    to_cost_center:    str
    to_account_code:   str
    amount:            float = Field(..., gt=0)
    reason:            Optional[str] = None


class BudgetSupplementCreate(BaseModel):
    entity_id:    str
    period_id:    str
    cost_center:  str
    account_code: str
    month:        int = Field(..., ge=1, le=12)
    amount:       float = Field(..., gt=0)
    reason:       Optional[str] = None


class ApprovalAction(BaseModel):
    action: str = Field(..., pattern="^(approved|rejected|returned)$")
    notes:  Optional[str] = None


class BudgetCheckReq(BaseModel):
    entity_id:    str
    cost_center:  str
    account_code: str
    year:         int
    month:        int
    requested:    float


# ── Budget Period CRUD ─────────────────────────────────────────────────────────

@router.post("/period", dependencies=_finance, summary="Buat periode anggaran baru")
def create_period(
    payload: BudgetPeriodCreate,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("finance")),
):
    existing = db.execute(
        text("""
            SELECT id FROM budget_period
            WHERE entity_id = :eid AND fiscal_year = :yr AND budget_version = :ver
        """),
        {"eid": payload.entity_id, "yr": payload.fiscal_year, "ver": payload.budget_version}
    ).fetchone()
    if existing:
        raise HTTPException(409, f"Budget {payload.fiscal_year}/{payload.budget_version} sudah ada")

    period_id = str(uuid4())
    db.execute(
        text("""
            INSERT INTO budget_period (id, entity_id, fiscal_year, budget_version, description, control_mode, created_by)
            VALUES (:id, :eid, :yr, :ver, :desc, :mode, :by)
        """),
        {
            "id": period_id, "eid": payload.entity_id, "yr": payload.fiscal_year,
            "ver": payload.budget_version, "desc": payload.description,
            "mode": payload.control_mode, "by": user.get("email", "system"),
        }
    )
    db.commit()
    return {"period_id": period_id, "fiscal_year": payload.fiscal_year,
            "version": payload.budget_version, "status": "draft"}


@router.get("/period", dependencies=_viewer, summary="List periode anggaran")
def list_periods(
    entity_id:   str  = Query(...),
    fiscal_year: Optional[int] = Query(None),
    db:          Session = Depends(get_db),
):
    params: dict = {"eid": entity_id}
    extra = ""
    if fiscal_year:
        extra = " AND fiscal_year = :yr"
        params["yr"] = fiscal_year
    rows = db.execute(
        text(f"""
            SELECT bp.*, COUNT(bl.id) AS line_count, COALESCE(SUM(bl.budgeted_amount), 0) AS total_budget
            FROM budget_period bp
            LEFT JOIN budget_line bl ON bl.period_id = bp.id
            WHERE bp.entity_id = :eid {extra}
            GROUP BY bp.id
            ORDER BY fiscal_year DESC, budget_version
        """),
        params
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/period/{period_id}", dependencies=_viewer, summary="Detail periode + summary")
def get_period(period_id: str, db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT * FROM budget_period WHERE id = :id"),
        {"id": period_id}
    ).fetchone()
    if not row:
        raise HTTPException(404, "Period tidak ditemukan")
    summary = db.execute(
        text("""
            SELECT cost_center,
                   SUM(budgeted_amount) AS total,
                   COUNT(DISTINCT account_code) AS accounts
            FROM budget_line WHERE period_id = :id
            GROUP BY cost_center ORDER BY cost_center
        """),
        {"id": period_id}
    ).fetchall()
    return {
        **dict(row._mapping),
        "cost_centers": [dict(r._mapping) for r in summary],
    }


# ── Budget Lines ──────────────────────────────────────────────────────────────

@router.post("/period/{period_id}/lines", dependencies=_finance,
             summary="Bulk input baris anggaran (replace per cost_center+account)")
def upsert_lines(
    period_id: str,
    payload:   BudgetLineBulk,
    db:        Session = Depends(get_db),
    user=      Depends(require_min_role("finance")),
):
    period = db.execute(
        text("SELECT id, fiscal_year, entity_id, status FROM budget_period WHERE id = :id"),
        {"id": period_id}
    ).fetchone()
    if not period:
        raise HTTPException(404, "Period tidak ditemukan")
    if period.status not in ("draft",):
        raise HTTPException(400, f"Budget status '{period.status}' — hanya draft yang bisa diedit")

    upserted = 0
    for line in payload.lines:
        budget_no = _gen_budget_no(db, period.fiscal_year)
        db.execute(
            text("""
                INSERT INTO budget_line (id, period_id, entity_id, budget_no, activity_description,
                    cost_center, account_code, year, month, budgeted_amount, notes, created_by, updated_at)
                VALUES (uuid_generate_v4(), :pid, :eid, :bno, :activity,
                    :cc, :acc, :yr, :mo, :amt, :notes, :by, NOW())
                ON CONFLICT (period_id, cost_center, account_code, year, month)
                DO UPDATE SET budgeted_amount      = EXCLUDED.budgeted_amount,
                              activity_description = EXCLUDED.activity_description,
                              notes                = EXCLUDED.notes,
                              updated_at           = NOW()
            """),
            {
                "pid": period_id, "eid": str(period.entity_id),
                "bno": budget_no, "activity": line.activity_description,
                "cc": line.cost_center, "acc": line.account_code,
                "yr": period.fiscal_year, "mo": line.month,
                "amt": line.budgeted_amount, "notes": line.notes,
                "by": user.get("email", "system"),
            }
        )
        upserted += 1

    db.commit()
    return {"upserted": upserted, "period_id": period_id}


@router.get("/period/{period_id}/lines", dependencies=_viewer,
            summary="Ambil semua baris anggaran")
def get_lines(
    period_id:   str,
    cost_center: Optional[str] = Query(None),
    month:       Optional[int] = Query(None),
    db:          Session = Depends(get_db),
):
    conditions = ["period_id = :pid"]
    params: dict = {"pid": period_id}
    if cost_center:
        conditions.append("cost_center = :cc")
        params["cc"] = cost_center
    if month:
        conditions.append("month = :month")
        params["month"] = month

    where = " AND ".join(conditions)
    rows = db.execute(
        text(f"SELECT * FROM budget_line WHERE {where} ORDER BY cost_center, account_code, month"),
        params
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/lines", dependencies=_viewer,
            summary="Cari baris anggaran (released) untuk dijadikan referensi dokumen lain (mis. PR)")
def search_referenceable_lines(
    entity_id:   str = Query(...),
    year:        int = Query(...),
    cost_center: Optional[str] = Query(None),
    month:       Optional[int] = Query(None),
    db:          Session = Depends(get_db),
):
    conditions = ["bu.entity_id = :eid", "bu.year = :year"]
    params: dict = {"eid": entity_id, "year": year}
    if cost_center:
        conditions.append("bu.cost_center = :cc")
        params["cc"] = cost_center
    if month:
        conditions.append("bu.month = :month")
        params["month"] = month

    where = " AND ".join(conditions)
    rows = db.execute(
        text(f"""
            SELECT bl.id AS budget_line_id, bl.budget_no, bl.activity_description,
                   bu.cost_center, bu.account_code, bu.year, bu.month,
                   bu.budgeted_amount, bu.actual_amount, bu.commitment_amount,
                   bu.budgeted_amount - bu.actual_amount - bu.commitment_amount AS available_amount
            FROM vw_budget_utilization bu
            JOIN budget_line bl
              ON bl.period_id    = bu.period_id
             AND bl.cost_center  = bu.cost_center
             AND bl.account_code = bu.account_code
             AND bl.year         = bu.year
             AND bl.month        = bu.month
            WHERE {where}
            ORDER BY bl.budget_no
        """),
        params
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Workflow Approval ─────────────────────────────────────────────────────────

@router.post("/period/{period_id}/submit", dependencies=_finance, summary="Submit budget ke approval")
def submit_period(
    period_id: str,
    db:        Session = Depends(get_db),
    user=      Depends(require_min_role("finance")),
):
    _check_and_transition(db, period_id, "draft", "submitted",
                          submitted_by=user.get("email"), submitted_at="NOW()")
    return {"period_id": period_id, "status": "submitted"}


@router.post("/period/{period_id}/approve", dependencies=_admin, summary="Approve budget")
def approve_period(
    period_id: str,
    payload:   ApprovalAction,
    db:        Session = Depends(get_db),
    user=      Depends(require_min_role("admin")),
):
    new_status = "approved" if payload.action == "approved" else "draft"
    _check_and_transition(db, period_id, "submitted", new_status,
                          approved_by=user.get("email"), approved_at="NOW()")
    _log_approval(db, period_id, 1, user.get("email", "admin"), payload.action, payload.notes)
    return {"period_id": period_id, "status": new_status}


@router.post("/period/{period_id}/release", dependencies=_admin,
             summary="Release budget — aktifkan Budget Control Engine")
def release_period(
    period_id: str,
    db:        Session = Depends(get_db),
    user=      Depends(require_min_role("admin")),
):
    _check_and_transition(db, period_id, "approved", "released",
                          released_by=user.get("email"), released_at="NOW()")
    return {"period_id": period_id, "status": "released",
            "message": "Budget Control Engine aktif. Semua transaksi akan dicek terhadap anggaran."}


@router.post("/period/{period_id}/control-mode", dependencies=_admin,
             summary="Ubah mode kontrol budget (hard/soft/off)")
def update_control_mode(
    period_id: str,
    mode:      str = Query(..., pattern="^(hard|soft|off)$"),
    db:        Session = Depends(get_db),
    user=      Depends(require_min_role("admin")),
):
    db.execute(
        text("UPDATE budget_period SET control_mode = :mode, updated_at = NOW() WHERE id = :id"),
        {"id": period_id, "mode": mode}
    )
    db.commit()
    return {"period_id": period_id, "control_mode": mode}


# ── Budget Transfer ────────────────────────────────────────────────────────────

@router.post("/transfer", dependencies=_finance, summary="Ajukan budget transfer antar cost center")
def create_transfer(
    payload: BudgetTransferCreate,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("finance")),
):
    tf_id = str(uuid4())
    tf_no = _gen_doc_no(db, "BT", payload.entity_id)
    db.execute(
        text("""
            INSERT INTO budget_transfer (
                id, entity_id, transfer_no, fiscal_year, month, transfer_date,
                from_period_id, from_cost_center, from_account_code,
                to_period_id,   to_cost_center,   to_account_code,
                amount, reason, status, submitted_by, submitted_at, created_by
            ) VALUES (
                :id, :eid, :no, :yr, :mo, :dt,
                :fpid, :fcc, :facc,
                :tpid, :tcc, :tacc,
                :amt, :reason, 'submitted', :by, NOW(), :by
            )
        """),
        {
            "id": tf_id, "eid": payload.entity_id, "no": tf_no,
            "yr": payload.fiscal_year, "mo": payload.month, "dt": payload.transfer_date,
            "fpid": payload.from_period_id, "fcc": payload.from_cost_center,
            "facc": payload.from_account_code,
            "tpid": payload.to_period_id, "tcc": payload.to_cost_center,
            "tacc": payload.to_account_code,
            "amt": payload.amount, "reason": payload.reason,
            "by": user.get("email", "system"),
        }
    )
    db.commit()
    return {"transfer_id": tf_id, "transfer_no": tf_no, "status": "submitted"}


@router.post("/transfer/{transfer_id}/approve", dependencies=_admin,
             summary="Approve/reject budget transfer")
def approve_transfer(
    transfer_id: str,
    payload:     ApprovalAction,
    db:          Session = Depends(get_db),
    user=        Depends(require_min_role("admin")),
):
    new_status = "approved" if payload.action == "approved" else "rejected"
    db.execute(
        text("""
            UPDATE budget_transfer SET
                status = :status,
                approved_by = :by,
                approved_at = NOW(),
                rejection_reason = :notes
            WHERE id = :id AND status = 'submitted'
        """),
        {"id": transfer_id, "status": new_status,
         "by": user.get("email"), "notes": payload.notes}
    )
    db.commit()

    if new_status == "approved":
        engine = BudgetEngine(db)
        engine.apply_transfer(transfer_id)

    return {"transfer_id": transfer_id, "status": new_status}


# ── Budget Supplement ─────────────────────────────────────────────────────────

@router.post("/supplement", dependencies=_finance, summary="Ajukan penambahan plafon budget")
def create_supplement(
    payload: BudgetSupplementCreate,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("finance")),
):
    sup_id = str(uuid4())
    sup_no = _gen_doc_no(db, "BS", payload.entity_id)
    db.execute(
        text("""
            INSERT INTO budget_supplement (
                id, entity_id, supplement_no, period_id, cost_center, account_code,
                month, amount, reason, status, submitted_by, created_by
            ) VALUES (
                :id, :eid, :no, :pid, :cc, :acc,
                :mo, :amt, :reason, 'submitted', :by, :by
            )
        """),
        {
            "id": sup_id, "eid": payload.entity_id, "no": sup_no,
            "pid": payload.period_id, "cc": payload.cost_center, "acc": payload.account_code,
            "mo": payload.month, "amt": payload.amount, "reason": payload.reason,
            "by": user.get("email", "system"),
        }
    )
    db.commit()
    return {"supplement_id": sup_id, "supplement_no": sup_no, "status": "submitted"}


@router.post("/supplement/{sup_id}/approve", dependencies=_admin,
             summary="Approve/reject supplement (harus CFO)")
def approve_supplement(
    sup_id:  str,
    payload: ApprovalAction,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("admin")),
):
    new_status = "approved" if payload.action == "approved" else "rejected"
    db.execute(
        text("""
            UPDATE budget_supplement SET status = :status, approved_by = :by, approved_at = NOW()
            WHERE id = :id AND status = 'submitted'
        """),
        {"id": sup_id, "status": new_status, "by": user.get("email")}
    )
    db.commit()

    if new_status == "approved":
        engine = BudgetEngine(db)
        engine.apply_supplement(sup_id)

    return {"supplement_id": sup_id, "status": new_status}


# ── Budget Check ──────────────────────────────────────────────────────────────

@router.post("/check", dependencies=_viewer, summary="Cek ketersediaan anggaran (simulasi)")
def check_budget(
    payload: BudgetCheckReq,
    db:      Session = Depends(get_db),
):
    engine = BudgetEngine(db)
    result = engine.check(
        entity_id    = payload.entity_id,
        cost_center  = payload.cost_center,
        account_code = payload.account_code,
        year         = payload.year,
        month        = payload.month,
        requested    = Decimal(str(payload.requested)),
    )
    return result.to_dict()


# ── Utilization & Variance Reports ────────────────────────────────────────────

@router.get("/utilization", dependencies=_viewer, summary="Laporan utilisasi anggaran per bulan")
def budget_utilization(
    entity_id:    str = Query(...),
    year:         int = Query(...),
    cost_center:  Optional[str] = Query(None),
    account_code: Optional[str] = Query(None),
    month:        Optional[int] = Query(None),
    db:           Session = Depends(get_db),
):
    engine = BudgetEngine(db)
    rows   = engine.get_utilization(entity_id, year, cost_center, account_code, month)
    return rows


@router.get("/variance", dependencies=_viewer, summary="Laporan Budget vs Actual tahunan")
def budget_variance(
    entity_id:   str = Query(...),
    year:        int = Query(...),
    cost_center: Optional[str] = Query(None),
    db:          Session = Depends(get_db),
):
    engine = BudgetEngine(db)
    return engine.get_variance_report(entity_id, year, cost_center)


@router.get("/commitment", dependencies=_viewer, summary="List encumbrance aktif")
def list_commitments(
    entity_id:   str  = Query(...),
    year:        int  = Query(...),
    cost_center: Optional[str] = Query(None),
    status:      Optional[str] = Query(None),
    db:          Session = Depends(get_db),
):
    conditions = ["entity_id = :eid", "year = :year"]
    params: dict = {"eid": entity_id, "year": year}
    if cost_center:
        conditions.append("cost_center = :cc")
        params["cc"] = cost_center
    if status:
        conditions.append("status = :status")
        params["status"] = status

    where = " AND ".join(conditions)
    rows = db.execute(
        text(f"""
            SELECT *, committed_amount - released_amount AS net_committed
            FROM budget_commitment WHERE {where}
            ORDER BY committed_at DESC
        """),
        params
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Cost Center master list ────────────────────────────────────────────────────

@router.get("/cost-centers", dependencies=_viewer, summary="Daftar cost center dari budget lines")
def list_cost_centers(
    entity_id: str  = Query(...),
    year:      Optional[int] = Query(None),
    db:        Session = Depends(get_db),
):
    params: dict = {"eid": entity_id}
    extra = ""
    if year:
        extra = " AND bl.year = :year"
        params["year"] = year

    rows = db.execute(
        text(f"""
            SELECT DISTINCT bl.cost_center,
                SUM(bl.budgeted_amount) AS total_budget
            FROM budget_line bl
            JOIN budget_period bp ON bp.id = bl.period_id
            WHERE bp.entity_id = :eid {extra}
            GROUP BY bl.cost_center ORDER BY bl.cost_center
        """),
        params
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _check_and_transition(db, period_id, expected_status, new_status, **set_fields):
    row = db.execute(
        text("SELECT id, status FROM budget_period WHERE id = :id"),
        {"id": period_id}
    ).fetchone()
    if not row:
        raise HTTPException(404, "Period tidak ditemukan")
    if row.status != expected_status:
        raise HTTPException(400, f"Status saat ini '{row.status}', dibutuhkan '{expected_status}'")

    sets = ["status = :new_status", "updated_at = NOW()"]
    params: dict = {"id": period_id, "new_status": new_status}
    for k, v in set_fields.items():
        if v == "NOW()":
            sets.append(f"{k} = NOW()")
        else:
            sets.append(f"{k} = :{k}")
            params[k] = v

    db.execute(
        text(f"UPDATE budget_period SET {', '.join(sets)} WHERE id = :id"),
        params
    )
    db.commit()


def _log_approval(db, period_id, seq, approver, action, notes):
    db.execute(
        text("""
            INSERT INTO budget_approval (id, period_id, sequence, approver, action, notes)
            VALUES (uuid_generate_v4(), :pid, :seq, :approver, :action, :notes)
        """),
        {"pid": period_id, "seq": seq, "approver": approver, "action": action, "notes": notes}
    )
    db.commit()


def _gen_budget_no(db, fiscal_year: int) -> str:
    pat   = f"BUD/{fiscal_year}"
    count = db.execute(
        text("SELECT COUNT(*) FROM budget_line WHERE budget_no LIKE :p"),
        {"p": f"{pat}/%"}
    ).scalar()
    return f"{pat}/{(count or 0) + 1:04d}"


def _gen_doc_no(db, prefix: str, entity_id: str) -> str:
    now = datetime.now()
    table_map = {"BT": "budget_transfer", "BS": "budget_supplement"}
    table = table_map.get(prefix, "budget_transfer")
    col   = {"BT": "transfer_no", "BS": "supplement_no"}.get(prefix, "transfer_no")
    pat   = f"{prefix}/{now.year}/{now.month:02d}"
    count = db.execute(
        text(f"SELECT COUNT(*) FROM {table} WHERE {col} LIKE :p"),
        {"p": f"{pat}/%"}
    ).scalar()
    return f"{pat}/{(count or 0) + 1:04d}"
