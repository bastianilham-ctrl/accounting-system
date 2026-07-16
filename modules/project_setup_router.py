"""
Project Setup Router — REST API (PMBOK / Prince2 based)
Prefix: /projects

Modul ini mencakup:
  - Cost Center (master + budget tahunan)
  - Project initialization (Charter + SOW)
  - Team & RACI Matrix
  - WBS (Work Breakdown Structure)
  - Task management + CPM trigger
  - Task Dependencies
  - Milestones
  - Deliverables
  - Budget Lines
  - Risk Register
  - Communication Plan
  - Change Requests
  - Reports: Gantt, EVM, Health Dashboard, Resource Loading, Risk Matrix
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
from modules.project_setup_engine import ProjectSetupEngine, _gen_cr_no, _next_task_code, _next_risk_code

router = APIRouter(prefix="/projects", tags=["project-setup"])


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _require_role(user: dict, *roles: str) -> None:
    # superadmin selalu boleh, konsisten dengan modules/auth.py:require_role()
    if user.get("role") not in roles and user.get("role") != "superadmin":
        raise HTTPException(403, detail=f"Butuh role: {', '.join(roles)}")


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Schemas
# ─────────────────────────────────────────────────────────────────────────────

class CostCenterCreate(BaseModel):
    entity_id: str
    cc_code: str
    cc_name: str
    parent_id: Optional[str] = None
    cc_type: str = "department"
    manager_employee_id: Optional[str] = None
    gl_account_code: Optional[str] = None

    @validator("cc_type")
    def valid_type(cls, v):
        if v not in ("department", "project", "overhead", "shared"):
            raise ValueError("cc_type harus: department | project | overhead | shared")
        return v


class CostCenterBudgetCreate(BaseModel):
    cost_center_id: str
    entity_id: str
    fiscal_year: int
    budget_amount: float = Field(..., gt=0)
    notes: Optional[str] = None


class ProjectInitRequest(BaseModel):
    entity_id: str
    project_code: str
    project_name: str
    industry_type: str = "general"
    objective: str
    start_date: date
    end_date: date
    budget_amount: float = Field(..., ge=0)
    currency: str = "IDR"
    priority: str = "medium"
    cost_center_id: Optional[str] = None
    project_manager_id: Optional[str] = None
    sponsor_id: Optional[str] = None
    client_id: Optional[str] = None
    contingency_pct: float = 5.0
    in_scope_items: Optional[List[str]] = None
    out_scope_items: Optional[List[str]] = None
    assumptions: Optional[List[str]] = None
    constraints: Optional[List[str]] = None

    @validator("industry_type")
    def valid_ptype(cls, v):
        valid = ("general", "construction", "software", "consulting", "research", "internal")
        if v not in valid:
            raise ValueError(f"industry_type: {' | '.join(valid)}")
        return v

    @validator("priority")
    def valid_priority(cls, v):
        if v not in ("low", "medium", "high", "critical"):
            raise ValueError("priority: low | medium | high | critical")
        return v


class ProjectUpdateRequest(BaseModel):
    project_name: Optional[str] = None
    industry_type: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    budget_amount: Optional[float] = None
    priority: Optional[str] = None
    project_manager_id: Optional[str] = None
    sponsor_id: Optional[str] = None
    cost_center_id: Optional[str] = None
    charter_status: Optional[str] = None

    @validator("industry_type")
    def valid_industry(cls, v):
        if v is None:
            return v
        valid = ("general", "construction", "software", "consulting", "research", "internal")
        if v not in valid:
            raise ValueError(f"industry_type: {' | '.join(valid)}")
        return v


class ScopeUpdateRequest(BaseModel):
    objective: Optional[str] = None
    in_scope_summary: Optional[str] = None
    out_scope_summary: Optional[str] = None
    assumptions: Optional[str] = None
    constraints: Optional[str] = None
    acceptance_criteria: Optional[str] = None


class ScopeItemCreate(BaseModel):
    item_type: str
    description: str
    sequence: int = 1

    @validator("item_type")
    def valid_itype(cls, v):
        valid = ("in_scope", "out_of_scope", "assumption", "constraint", "acceptance")
        if v not in valid:
            raise ValueError(f"item_type: {' | '.join(valid)}")
        return v


class TeamMemberAdd(BaseModel):
    employee_id: str
    role_in_project: str = "member"
    wbs_item_id: Optional[str] = None
    allocation_pct: float = Field(100.0, gt=0, le=100)
    join_date: Optional[date] = None
    end_date: Optional[date] = None
    notes: Optional[str] = None

    @validator("role_in_project")
    def valid_role(cls, v):
        valid = ("project_director", "sponsor", "project_manager", "work_package_manager",
                 "team_lead", "member", "consultant", "reviewer", "stakeholder")
        if v not in valid:
            raise ValueError(f"role: {' | '.join(valid)}")
        return v


class RACIEntryCreate(BaseModel):
    activity_name: str
    wbs_item_id: Optional[str] = None
    entries: List[RACILine]


class RACILine(BaseModel):
    employee_id: str
    raci_role: str

    @validator("raci_role")
    def valid_raci(cls, v):
        if v not in ("R", "A", "C", "I"):
            raise ValueError("raci_role harus: R | A | C | I")
        return v


class RACIEntryCreate(BaseModel):
    activity_name: str
    wbs_item_id: Optional[str] = None
    entries: List[RACILine]


class WBSItemCreate(BaseModel):
    parent_id: Optional[str] = None
    wbs_code: str
    wbs_name: str
    description: Optional[str] = None
    is_work_package: bool = False
    planned_hours: float = 0.0
    planned_cost: float = 0.0
    sequence: int = 1


class TaskCreate(BaseModel):
    wbs_item_id: Optional[str] = None
    milestone_id: Optional[str] = None
    task_name: str
    description: Optional[str] = None
    assigned_to_id: Optional[str] = None
    reviewer_id: Optional[str] = None
    planned_start: date
    planned_end: date
    planned_hours: float = 0.0
    planned_cost: float = 0.0
    notes: Optional[str] = None


class TaskUpdate(BaseModel):
    task_name: Optional[str] = None
    assigned_to_id: Optional[str] = None
    milestone_id: Optional[str] = None
    planned_start: Optional[date] = None
    planned_end: Optional[date] = None
    planned_hours: Optional[float] = None
    planned_cost: Optional[float] = None
    actual_start: Optional[date] = None
    actual_end: Optional[date] = None
    actual_hours: Optional[float] = None
    actual_cost: Optional[float] = None
    progress_pct: Optional[int] = Field(None, ge=0, le=100)
    status: Optional[str] = None
    notes: Optional[str] = None


class DependencyCreate(BaseModel):
    predecessor_id: str
    successor_id: str
    dependency_type: str = "FS"
    lag_days: int = 0

    @validator("dependency_type")
    def valid_dep(cls, v):
        if v not in ("FS", "SS", "FF", "SF"):
            raise ValueError("dependency_type: FS | SS | FF | SF")
        return v


class MilestoneCreate(BaseModel):
    milestone_name: str
    description: Optional[str] = None
    target_start_date: Optional[date] = None
    target_date: date
    linked_task_id: Optional[str] = None
    sequence: int = 1
    billing_amount: Optional[float] = None


class MilestoneUpdate(BaseModel):
    milestone_name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    actual_date: Optional[date] = None
    progress_pct: Optional[int] = Field(None, ge=0, le=100)
    target_start_date: Optional[date] = None
    target_date: Optional[date] = None
    progress_status: Optional[str] = None
    issue_notes: Optional[str] = None
    billing_amount: Optional[float] = None

    @validator("progress_status")
    def valid_progress_status(cls, v):
        if v is None:
            return v
        if v not in ("not_started", "in_progress", "completed"):
            raise ValueError("progress_status: not_started | in_progress | completed")
        return v


class DeliverableCreate(BaseModel):
    phase: str = "execution"
    deliverable_name: str
    description: Optional[str] = None
    deliverable_type: str = "technical"
    due_date: date
    responsible_id: Optional[str] = None

    @validator("phase")
    def valid_phase(cls, v):
        valid = ("initiation", "planning", "execution", "monitoring", "closing")
        if v not in valid:
            raise ValueError(f"phase: {' | '.join(valid)}")
        return v


class BudgetLineCreate(BaseModel):
    cost_type: str
    description: str
    wbs_item_id: Optional[str] = None
    quantity: float = Field(1.0, gt=0)
    unit_price: float = Field(..., ge=0)
    gl_account_code: Optional[str] = None
    notes: Optional[str] = None

    @validator("cost_type")
    def valid_cost(cls, v):
        valid = ("direct_labor", "direct_material", "software", "hardware",
                 "travel", "subcontractor", "indirect", "contingency", "other")
        if v not in valid:
            raise ValueError(f"cost_type: {' | '.join(valid)}")
        return v


class RiskCreate(BaseModel):
    risk_title: str
    description: Optional[str] = None
    category: str = "technical"
    probability: int = Field(3, ge=1, le=5)
    impact: int = Field(3, ge=1, le=5)
    mitigation_plan: Optional[str] = None
    contingency_plan: Optional[str] = None
    risk_owner_id: Optional[str] = None
    financial_impact: float = 0.0

    @validator("category")
    def valid_cat(cls, v):
        valid = ("technical", "financial", "resource", "schedule", "scope", "external", "quality")
        if v not in valid:
            raise ValueError(f"category: {' | '.join(valid)}")
        return v


class CommunicationPlanCreate(BaseModel):
    meeting_type: str
    frequency: str = "weekly"
    day_of_week: Optional[str] = None
    day_of_month: Optional[int] = None
    duration_minutes: int = 60
    participants: Optional[str] = None
    facilitator_id: Optional[str] = None
    agenda_template: Optional[str] = None
    communication_channel: str = "meeting"
    output_document: Optional[str] = None
    notes: Optional[str] = None


class ChangeRequestCreate(BaseModel):
    cr_title: str
    description: str
    change_type: str = "scope"
    impact_scope: Optional[str] = None
    impact_schedule_days: int = 0
    impact_budget: float = 0.0
    requested_by: Optional[str] = None

    @validator("change_type")
    def valid_cr_type(cls, v):
        if v not in ("scope", "schedule", "budget", "resource", "quality"):
            raise ValueError("change_type: scope | schedule | budget | resource | quality")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# COST CENTER
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/cost-centers", status_code=201, summary="Buat Cost Center baru")
def create_cost_center(
    req: CostCenterCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "admin")
    existing = db.execute(
        text("SELECT id FROM cost_center WHERE entity_id = :eid AND cc_code = :c"),
        {"eid": req.entity_id, "c": req.cc_code},
    ).fetchone()
    if existing:
        raise HTTPException(400, f"cc_code '{req.cc_code}' sudah ada")

    cc_id = str(uuid4())
    db.execute(
        text(
            "INSERT INTO cost_center "
            "(id, entity_id, cc_code, cc_name, parent_id, cc_type, "
            " manager_employee_id, gl_account_code, created_by) "
            "VALUES (:id, :eid, :c, :n, :pid, :ct, :mid, :gl, :cby)"
        ),
        {
            "id": cc_id, "eid": req.entity_id, "c": req.cc_code, "n": req.cc_name,
            "pid": req.parent_id, "ct": req.cc_type,
            "mid": req.manager_employee_id, "gl": req.gl_account_code,
            "cby": current_user["username"],
        },
    )
    db.commit()
    return {"cost_center_id": cc_id, "cc_code": req.cc_code}


@router.get("/cost-centers", summary="Daftar Cost Center")
def list_cost_centers(
    entity_id: str = Query(...),
    cc_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    q = "SELECT cc.*, e2.full_name AS manager_name FROM cost_center cc LEFT JOIN employee e2 ON e2.id = cc.manager_employee_id WHERE cc.entity_id = :eid AND cc.is_active = TRUE"
    params: dict[str, Any] = {"eid": entity_id}
    if cc_type:
        q += " AND cc.cc_type = :ct"
        params["ct"] = cc_type
    rows = db.execute(text(q + " ORDER BY cc.cc_code"), params).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/cost-centers/{cc_id}/budget", status_code=201, summary="Set anggaran tahunan Cost Center")
def set_cc_budget(
    cc_id: str,
    req: CostCenterBudgetCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "admin")
    db.execute(
        text(
            "INSERT INTO cost_center_budget "
            "(id, cost_center_id, entity_id, fiscal_year, budget_amount, notes, "
            " approved_by, approved_at) "
            "VALUES (:id, :cid, :eid, :fy, :ba, :notes, :aby, NOW()) "
            "ON CONFLICT (cost_center_id, fiscal_year) DO UPDATE "
            "SET budget_amount = EXCLUDED.budget_amount, notes = EXCLUDED.notes, "
            "    approved_by = EXCLUDED.approved_by, approved_at = NOW()"
        ),
        {
            "id": str(uuid4()), "cid": cc_id, "eid": req.entity_id,
            "fy": req.fiscal_year, "ba": req.budget_amount, "notes": req.notes,
            "aby": current_user["username"],
        },
    )
    db.commit()
    return {"cost_center_id": cc_id, "fiscal_year": req.fiscal_year, "budget_amount": req.budget_amount}


@router.get("/cost-centers/performance", summary="Budget vs Realisasi per Cost Center")
def cc_performance(
    entity_id: str = Query(...),
    fiscal_year: int = Query(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return ProjectSetupEngine.get_cost_center_performance(db, entity_id, fiscal_year)


# ─────────────────────────────────────────────────────────────────────────────
# PROJECT — Initialization & CRUD
# ─────────────────────────────────────────────────────────────────────────────

@router.post("", status_code=201, summary="Inisialisasi proyek baru (Charter + SOW)")
def create_project(
    req: ProjectInitRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Satu endpoint untuk setup proyek dari nol:
    - Buat project record
    - Buat Cost Center project (jika cost_center_id tidak diisi)
    - Buat Project Scope v1 (SOW)
    - Insert in-scope, out-of-scope, assumption, constraint items
    """
    _require_role(current_user, "admin")
    try:
        result = ProjectSetupEngine.initialize_project(
            db=db,
            entity_id=req.entity_id,
            project_code=req.project_code,
            project_name=req.project_name,
            industry_type=req.industry_type,
            objective=req.objective,
            start_date=req.start_date,
            end_date=req.end_date,
            budget_amount=req.budget_amount,
            currency=req.currency,
            priority=req.priority,
            cost_center_id=req.cost_center_id,
            project_manager_id=req.project_manager_id,
            sponsor_id=req.sponsor_id,
            client_id=req.client_id,
            contingency_pct=req.contingency_pct,
            in_scope_items=req.in_scope_items,
            out_scope_items=req.out_scope_items,
            assumptions=req.assumptions,
            constraints=req.constraints,
            created_by=current_user["username"],
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("", summary="Daftar proyek")
def list_projects(
    entity_id: str = Query(...),
    charter_status: Optional[str] = Query(None),
    industry_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    filters = ["entity_id = :eid"]
    params: dict[str, Any] = {"eid": entity_id}
    if charter_status:
        filters.append("charter_status = :cs")
        params["cs"] = charter_status
    if industry_type:
        filters.append("industry_type = :pt")
        params["pt"] = industry_type
    if search:
        filters.append("(project_code ILIKE :s OR project_name ILIKE :s)")
        params["s"] = f"%{search}%"
    where = " AND ".join(filters)
    total = db.execute(text(f"SELECT COUNT(*) FROM vw_project_summary WHERE {where}"), params).scalar()
    params["offset"] = (page - 1) * size
    params["limit"]  = size
    rows = db.execute(
        text(f"SELECT * FROM vw_project_summary WHERE {where} ORDER BY start_date DESC LIMIT :limit OFFSET :offset"),
        params,
    ).fetchall()
    return {"total": total, "page": page, "size": size, "items": [dict(r._mapping) for r in rows]}


@router.get("/{project_id}", summary="Detail proyek")
def get_project(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    row = db.execute(
        text("SELECT * FROM vw_project_summary WHERE id = :pid"), {"pid": project_id}
    ).fetchone()
    if not row:
        raise HTTPException(404, "Proyek tidak ditemukan")
    return dict(row._mapping)


@router.put("/{project_id}", summary="Update header proyek")
def update_project(
    project_id: str,
    req: ProjectUpdateRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "admin")
    updates: list[str] = ["updated_at = NOW()"]
    params: dict[str, Any] = {"pid": project_id}

    if req.project_name    is not None: updates.append("project_name = :pn");    params["pn"]   = req.project_name
    if req.industry_type   is not None: updates.append("industry_type = :it");   params["it"]   = req.industry_type
    if req.start_date      is not None: updates.append("start_date = :sd");       params["sd"]   = req.start_date
    if req.end_date        is not None: updates.append("end_date = :ed");         params["ed"]   = req.end_date
    if req.budget_amount   is not None: updates.append("budget_amount = :ba");    params["ba"]   = req.budget_amount
    if req.priority        is not None: updates.append("priority = :pri");        params["pri"]  = req.priority
    if req.project_manager_id is not None: updates.append("project_manager_id = :pmid"); params["pmid"] = req.project_manager_id
    if req.sponsor_id      is not None: updates.append("sponsor_id = :spid");     params["spid"] = req.sponsor_id
    if req.cost_center_id  is not None: updates.append("cost_center_id = :ccid"); params["ccid"] = req.cost_center_id
    if req.charter_status  is not None: updates.append("charter_status = :cs");   params["cs"]   = req.charter_status

    db.execute(text(f"UPDATE project SET {', '.join(updates)} WHERE id = :pid"), params)
    db.commit()
    return {"project_id": project_id, "updated": True}


@router.post("/{project_id}/approve-charter", summary="Approve / Reject project charter")
def approve_charter(
    project_id: str,
    action: str = Query(..., description="approved | rejected"),
    notes: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "admin")
    if action not in ("approved", "rejected"):
        raise HTTPException(400, "action harus: approved | rejected")
    db.execute(
        text(
            "UPDATE project SET charter_status = :s, updated_at = NOW() "
            "WHERE id = :pid"
        ),
        {"s": action, "pid": project_id},
    )
    db.commit()
    return {"project_id": project_id, "charter_status": action}


# ─────────────────────────────────────────────────────────────────────────────
# SCOPE OF WORK
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{project_id}/scope", summary="Baca Scope of Work aktif")
def get_scope(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    scope = db.execute(
        text(
            "SELECT * FROM project_scope "
            "WHERE project_id = :pid AND is_current = TRUE "
            "ORDER BY version DESC LIMIT 1"
        ),
        {"pid": project_id},
    ).fetchone()
    if not scope:
        raise HTTPException(404, "Scope of Work belum dibuat")

    items = db.execute(
        text(
            "SELECT * FROM project_scope_item "
            "WHERE scope_id = :sid ORDER BY item_type, sequence"
        ),
        {"sid": str(scope.id)},
    ).fetchall()

    grouped: dict[str, list] = {}
    for item in items:
        t = item.item_type
        grouped.setdefault(t, []).append({"sequence": item.sequence, "description": item.description})

    return {**dict(scope._mapping), "items": grouped}


@router.put("/{project_id}/scope", summary="Update Scope of Work aktif (objective, assumptions, dst)")
def update_scope(
    project_id: str,
    req: ScopeUpdateRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "admin")
    scope = db.execute(
        text("SELECT id FROM project_scope WHERE project_id = :pid AND is_current = TRUE LIMIT 1"),
        {"pid": project_id},
    ).fetchone()
    if not scope:
        raise HTTPException(404, "Scope belum ada — inisialisasi proyek dulu")

    updates: list[str] = []
    params: dict[str, Any] = {"sid": str(scope.id)}
    for field in ("objective", "in_scope_summary", "out_scope_summary",
                  "assumptions", "constraints", "acceptance_criteria"):
        val = getattr(req, field)
        if val is not None:
            updates.append(f"{field} = :{field}")
            params[field] = val
    if not updates:
        return {"scope_id": str(scope.id), "updated": False}

    db.execute(text(f"UPDATE project_scope SET {', '.join(updates)} WHERE id = :sid"), params)
    db.commit()
    return {"scope_id": str(scope.id), "updated": True}


@router.post("/{project_id}/scope/items", status_code=201, summary="Tambah item scope (in/out/assumption/constraint)")
def add_scope_item(
    project_id: str,
    req: ScopeItemCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    scope = db.execute(
        text("SELECT id FROM project_scope WHERE project_id = :pid AND is_current = TRUE LIMIT 1"),
        {"pid": project_id},
    ).fetchone()
    if not scope:
        raise HTTPException(404, "Scope belum ada — inisialisasi proyek dulu")

    db.execute(
        text(
            "INSERT INTO project_scope_item (id, project_id, scope_id, item_type, sequence, description) "
            "VALUES (:id, :pid, :sid, :it, :seq, :desc)"
        ),
        {
            "id": str(uuid4()), "pid": project_id, "sid": str(scope.id),
            "it": req.item_type, "seq": req.sequence, "desc": req.description,
        },
    )
    db.commit()
    return {"created": True, "item_type": req.item_type}


# ─────────────────────────────────────────────────────────────────────────────
# TEAM & RACI
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{project_id}/team", status_code=201, summary="Tambah anggota tim")
def add_team_member(
    project_id: str,
    req: TeamMemberAdd,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "admin")
    existing = db.execute(
        text("SELECT id FROM project_team_member WHERE project_id = :pid AND employee_id = :eid"),
        {"pid": project_id, "eid": req.employee_id},
    ).fetchone()
    if existing:
        raise HTTPException(400, "Karyawan sudah menjadi anggota tim proyek ini")

    db.execute(
        text(
            "INSERT INTO project_team_member "
            "(id, project_id, employee_id, role_in_project, wbs_item_id, allocation_pct, join_date, end_date, notes) "
            "VALUES (:id, :pid, :eid, :role, :wid, :alloc, :jd, :ed, :notes)"
        ),
        {
            "id": str(uuid4()), "pid": project_id, "eid": req.employee_id,
            "role": req.role_in_project, "wid": req.wbs_item_id, "alloc": req.allocation_pct,
            "jd": req.join_date, "ed": req.end_date, "notes": req.notes,
        },
    )
    db.commit()
    return {"project_id": project_id, "employee_id": req.employee_id, "role": req.role_in_project}


@router.get("/{project_id}/team", summary="Daftar tim proyek")
def list_team(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    rows = db.execute(
        text(
            "SELECT ptm.*, e.full_name, e.employee_no, e.position, "
            "       wi.wbs_code, wi.wbs_name "
            "FROM project_team_member ptm "
            "JOIN employee e ON e.id = ptm.employee_id "
            "LEFT JOIN wbs_item wi ON wi.id = ptm.wbs_item_id "
            "WHERE ptm.project_id = :pid ORDER BY ptm.role_in_project, e.full_name"
        ),
        {"pid": project_id},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.delete("/{project_id}/team/{employee_id}", summary="Hapus anggota tim")
def remove_team_member(
    project_id: str,
    employee_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "admin")
    db.execute(
        text("DELETE FROM project_team_member WHERE project_id = :pid AND employee_id = :eid"),
        {"pid": project_id, "eid": employee_id},
    )
    db.commit()
    return {"deleted": True}


@router.post("/{project_id}/raci", status_code=201, summary="Set entri RACI untuk satu aktivitas")
def set_raci(
    project_id: str,
    req: RACIEntryCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "admin")
    # Validasi: tepat 1 Accountable per aktivitas
    accountables = [e for e in req.entries if e.raci_role == "A"]
    if len(accountables) != 1:
        raise HTTPException(400, "Setiap aktivitas harus punya tepat 1 Accountable (A)")

    for entry in req.entries:
        db.execute(
            text(
                "INSERT INTO raci_entry "
                "(id, project_id, activity_name, wbs_item_id, employee_id, raci_role) "
                "VALUES (:id, :pid, :act, :wid, :eid, :role) "
                "ON CONFLICT (project_id, activity_name, employee_id) "
                "DO UPDATE SET raci_role = EXCLUDED.raci_role"
            ),
            {
                "id": str(uuid4()), "pid": project_id, "act": req.activity_name,
                "wid": req.wbs_item_id, "eid": entry.employee_id, "role": entry.raci_role,
            },
        )
    db.commit()
    return {"activity_name": req.activity_name, "entries_saved": len(req.entries)}


@router.get("/{project_id}/raci", summary="RACI Matrix proyek")
def get_raci(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    rows = db.execute(
        text("SELECT * FROM vw_raci_matrix WHERE project_id = :pid ORDER BY activity_name"),
        {"pid": project_id},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# WBS
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{project_id}/wbs", status_code=201, summary="Tambah WBS item")
def create_wbs_item(
    project_id: str,
    req: WBSItemCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    existing = db.execute(
        text("SELECT id FROM wbs_item WHERE project_id = :pid AND wbs_code = :c"),
        {"pid": project_id, "c": req.wbs_code},
    ).fetchone()
    if existing:
        raise HTTPException(400, f"wbs_code '{req.wbs_code}' sudah ada")

    level = req.wbs_code.count(".") + 1
    wbs_id = str(uuid4())
    db.execute(
        text(
            "INSERT INTO wbs_item "
            "(id, project_id, parent_id, wbs_code, wbs_name, description, "
            " level, is_work_package, planned_hours, planned_cost, sequence) "
            "VALUES (:id, :pid, :par, :c, :n, :desc, :lvl, :iwp, :ph, :pc, :seq)"
        ),
        {
            "id": wbs_id, "pid": project_id, "par": req.parent_id,
            "c": req.wbs_code, "n": req.wbs_name, "desc": req.description,
            "lvl": level, "iwp": req.is_work_package,
            "ph": req.planned_hours, "pc": req.planned_cost, "seq": req.sequence,
        },
    )
    db.commit()
    return {"wbs_id": wbs_id, "wbs_code": req.wbs_code, "level": level}


@router.get("/{project_id}/wbs", summary="WBS hierarchy + cost rollup")
def get_wbs(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return ProjectSetupEngine.get_wbs_cost_rollup(db, project_id)


# ─────────────────────────────────────────────────────────────────────────────
# TASKS
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{project_id}/tasks", status_code=201, summary="Tambah task")
def create_task(
    project_id: str,
    req: TaskCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "superadmin")
    task_id   = str(uuid4())
    task_code = _next_task_code(db, project_id)
    duration  = (req.planned_end - req.planned_start).days + 1

    db.execute(
        text(
            "INSERT INTO project_task "
            "(id, project_id, wbs_item_id, milestone_id, task_code, task_name, description, "
            " assigned_to_id, reviewer_id, planned_start, planned_end, "
            " duration_days, planned_hours, planned_cost, notes, created_by) "
            "VALUES (:id, :pid, :wid, :mid, :tc, :tn, :desc, "
            "        :aid, :rid, :ps, :pe, :dur, :ph, :pc, :notes, :cby)"
        ),
        {
            "id": task_id, "pid": project_id, "wid": req.wbs_item_id, "mid": req.milestone_id,
            "tc": task_code, "tn": req.task_name, "desc": req.description,
            "aid": req.assigned_to_id, "rid": req.reviewer_id,
            "ps": req.planned_start, "pe": req.planned_end,
            "dur": duration, "ph": req.planned_hours, "pc": req.planned_cost,
            "notes": req.notes, "cby": current_user["username"],
        },
    )
    db.commit()
    if req.milestone_id:
        ProjectSetupEngine.recompute_milestone_progress(db, req.milestone_id)
        db.commit()
    return {"task_id": task_id, "task_code": task_code, "duration_days": duration}


@router.get("/{project_id}/tasks", summary="Daftar tasks")
def list_tasks(
    project_id: str,
    status: Optional[str] = Query(None),
    assigned_to_id: Optional[str] = Query(None),
    is_critical: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    q = "SELECT * FROM vw_gantt_chart WHERE project_id = :pid"
    params: dict[str, Any] = {"pid": project_id}
    if status:
        q += " AND status = :st"; params["st"] = status
    if is_critical is not None:
        q += " AND is_critical = :ic"; params["ic"] = is_critical
    rows = db.execute(text(q + " ORDER BY planned_start, wbs_code"), params).fetchall()
    return [dict(r._mapping) for r in rows]


@router.put("/{project_id}/tasks/{task_id}", summary="Update task (progress, actual, status)")
def update_task(
    project_id: str,
    task_id: str,
    req: TaskUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    # Edit field Plan (nama/assigned/jadwal rencana/biaya rencana/link milestone) = perubahan
    # struktural task, dibatasi superadmin saja (request user 2026-06-24) — beda dari field
    # tracking harian (actual_*/progress_pct/status/notes) yang tetap terbuka utk PM/team
    # supaya day-to-day monitoring di kotak Actual tidak terhambat.
    PLAN_FIELDS = ("task_name", "assigned_to_id", "milestone_id", "planned_start",
                   "planned_end", "planned_hours", "planned_cost")
    if any(getattr(req, f) is not None for f in PLAN_FIELDS):
        _require_role(current_user, "superadmin")

    old_row = db.execute(
        text("SELECT milestone_id FROM project_task WHERE id = :tid"), {"tid": task_id}
    ).fetchone()
    old_milestone_id = str(old_row.milestone_id) if old_row and old_row.milestone_id else None

    updates = ["updated_at = NOW()"]
    params: dict[str, Any] = {"tid": task_id, "pid": project_id}

    fields_map = {
        "task_name":       ("task_name", req.task_name),
        "assigned_to_id":  ("assigned_to_id", req.assigned_to_id),
        "planned_start":   ("planned_start", req.planned_start),
        "planned_end":     ("planned_end", req.planned_end),
        "planned_hours":   ("planned_hours", req.planned_hours),
        "planned_cost":    ("planned_cost", req.planned_cost),
        "actual_start":    ("actual_start", req.actual_start),
        "actual_end":      ("actual_end", req.actual_end),
        "actual_hours":    ("actual_hours", req.actual_hours),
        "actual_cost":     ("actual_cost", req.actual_cost),
        "progress_pct":    ("progress_pct", req.progress_pct),
        "status":          ("status", req.status),
        "notes":           ("notes", req.notes),
    }
    for param_key, (col, val) in fields_map.items():
        if val is not None:
            updates.append(f"{col} = :{param_key}")
            params[param_key] = val

    # milestone_id: None = jangan diubah, "" = lepas link (set NULL), string = relink
    if req.milestone_id is not None:
        updates.append("milestone_id = :mid")
        params["mid"] = req.milestone_id or None

    # Recalculate duration if dates changed
    if req.planned_start and req.planned_end:
        dur = (req.planned_end - req.planned_start).days + 1
        updates.append("duration_days = :dur")
        params["dur"] = dur

    db.execute(
        text(f"UPDATE project_task SET {', '.join(updates)} WHERE id = :tid AND project_id = :pid"),
        params,
    )
    db.commit()

    new_milestone_id = (req.milestone_id or None) if req.milestone_id is not None else old_milestone_id
    if new_milestone_id:
        ProjectSetupEngine.recompute_milestone_progress(db, new_milestone_id)
    if old_milestone_id and old_milestone_id != new_milestone_id:
        ProjectSetupEngine.recompute_milestone_progress(db, old_milestone_id)
    db.commit()
    return {"task_id": task_id, "updated": True}


# ─────────────────────────────────────────────────────────────────────────────
# DEPENDENCIES
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{project_id}/dependencies", status_code=201, summary="Tambah dependency antar task")
def add_dependency(
    project_id: str,
    req: DependencyCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if req.predecessor_id == req.successor_id:
        raise HTTPException(400, "predecessor dan successor tidak boleh sama")

    db.execute(
        text(
            "INSERT INTO task_dependency "
            "(id, project_id, predecessor_id, successor_id, dependency_type, lag_days) "
            "VALUES (:id, :pid, :pred, :succ, :dt, :lag) "
            "ON CONFLICT (predecessor_id, successor_id) DO UPDATE "
            "SET dependency_type = EXCLUDED.dependency_type, lag_days = EXCLUDED.lag_days"
        ),
        {
            "id": str(uuid4()), "pid": project_id,
            "pred": req.predecessor_id, "succ": req.successor_id,
            "dt": req.dependency_type, "lag": req.lag_days,
        },
    )
    db.commit()
    return {"created": True, "dependency_type": req.dependency_type}


@router.delete("/{project_id}/dependencies", summary="Hapus dependency")
def delete_dependency(
    project_id: str,
    predecessor_id: str = Query(...),
    successor_id: str = Query(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    db.execute(
        text(
            "DELETE FROM task_dependency "
            "WHERE project_id = :pid AND predecessor_id = :pred AND successor_id = :succ"
        ),
        {"pid": project_id, "pred": predecessor_id, "succ": successor_id},
    )
    db.commit()
    return {"deleted": True}


# ─────────────────────────────────────────────────────────────────────────────
# MILESTONES
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{project_id}/milestones", status_code=201, summary="Tambah milestone")
def create_milestone(
    project_id: str,
    req: MilestoneCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "superadmin")
    ms_id = str(uuid4())
    db.execute(
        text(
            "INSERT INTO project_milestone "
            "(id, project_id, milestone_name, description, target_start_date, target_date, linked_task_id, sequence, billing_amount) "
            "VALUES (:id, :pid, :n, :desc, :tsd, :td, :tid, :seq, :bamt)"
        ),
        {
            "id": ms_id, "pid": project_id, "n": req.milestone_name,
            "desc": req.description, "tsd": req.target_start_date, "td": req.target_date,
            "tid": req.linked_task_id, "seq": req.sequence, "bamt": req.billing_amount or 0,
        },
    )
    db.commit()
    return {"milestone_id": ms_id}


@router.get("/{project_id}/milestones", summary="Daftar milestones")
def list_milestones(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    rows = db.execute(
        text(
            "SELECT pm.*, COUNT(pt.id) AS task_count, "
            "COALESCE(SUM(pt.planned_hours), 0) AS total_planned_hours, "
            "COALESCE(SUM(pt.planned_cost), 0) AS total_planned_cost "
            "FROM project_milestone pm "
            "LEFT JOIN project_task pt ON pt.milestone_id = pm.id AND pt.status != 'cancelled' "
            "WHERE pm.project_id = :pid GROUP BY pm.id ORDER BY pm.target_date"
        ),
        {"pid": project_id},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.put("/{project_id}/milestones/{ms_id}", summary="Update status / actual date / progress milestone")
def update_milestone(
    project_id: str,
    ms_id: str,
    req: MilestoneUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if req.status is not None and req.status not in ("pending", "achieved", "missed", "at_risk"):
        raise HTTPException(400, "status: pending | achieved | missed | at_risk")

    # Edit field Plan (nama/deskripsi/target date) = perubahan struktural milestone,
    # dibatasi superadmin saja (request user 2026-06-24) — beda dari field tracking
    # harian (status/progress_pct/progress_status/issue_notes/actual_date) yang tetap
    # terbuka untuk PM/team supaya day-to-day monitoring di kotak Actual tidak terhambat.
    PLAN_FIELDS = ("milestone_name", "description", "target_start_date", "target_date", "billing_amount")
    if any(getattr(req, f) is not None for f in PLAN_FIELDS):
        _require_role(current_user, "superadmin")

    # progress_status (day-to-day tracking PM) terpisah dari status (outcome vs Target Date,
    # dipakai Health Dashboard utk overdue_milestones) — tapi saat PM tandai 'completed',
    # auto-sync status='achieved'+actual_date=hari ini kalau caller belum set itu manual,
    # supaya 2 sistem tidak bisa saling kontradiksi (progress_status completed tapi status pending).
    if req.progress_status == "completed" and req.status is None and req.actual_date is None:
        req.status = "achieved"
        req.actual_date = date.today()

    updates: list[str] = []
    params: dict[str, Any] = {"mid": ms_id, "pid": project_id}
    for field in ("milestone_name", "description", "status", "actual_date", "progress_pct",
                  "target_start_date", "target_date", "progress_status", "issue_notes", "billing_amount"):
        val = getattr(req, field)
        if val is not None:
            updates.append(f"{field} = :{field}")
            params[field] = val
    if not updates:
        return {"milestone_id": ms_id, "updated": False}

    db.execute(
        text(f"UPDATE project_milestone SET {', '.join(updates)} WHERE id = :mid AND project_id = :pid"),
        params,
    )
    db.commit()
    return {"milestone_id": ms_id, "updated": True}


# ─────────────────────────────────────────────────────────────────────────────
# DELIVERABLES
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{project_id}/deliverables", status_code=201, summary="Tambah deliverable")
def create_deliverable(
    project_id: str,
    req: DeliverableCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    del_id = str(uuid4())
    db.execute(
        text(
            "INSERT INTO project_deliverable "
            "(id, project_id, phase, deliverable_name, description, deliverable_type, "
            " due_date, responsible_id) "
            "VALUES (:id, :pid, :phase, :n, :desc, :dt, :dd, :rid)"
        ),
        {
            "id": del_id, "pid": project_id, "phase": req.phase,
            "n": req.deliverable_name, "desc": req.description, "dt": req.deliverable_type,
            "dd": req.due_date, "rid": req.responsible_id,
        },
    )
    db.commit()
    return {"deliverable_id": del_id}


@router.get("/{project_id}/deliverables", summary="Daftar deliverables per fase")
def list_deliverables(
    project_id: str,
    phase: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    q = "SELECT pd.*, e.full_name AS responsible_name FROM project_deliverable pd LEFT JOIN employee e ON e.id = pd.responsible_id WHERE pd.project_id = :pid"
    params: dict[str, Any] = {"pid": project_id}
    if phase:
        q += " AND pd.phase = :ph"; params["ph"] = phase
    rows = db.execute(text(q + " ORDER BY pd.due_date"), params).fetchall()
    return [dict(r._mapping) for r in rows]


@router.put("/{project_id}/deliverables/{del_id}/deliver", summary="Mark deliverable sebagai delivered")
def deliver(
    project_id: str,
    del_id: str,
    acceptance_notes: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    db.execute(
        text(
            "UPDATE project_deliverable "
            "SET status = 'delivered', delivered_date = CURRENT_DATE, acceptance_notes = :notes "
            "WHERE id = :did AND project_id = :pid"
        ),
        {"notes": acceptance_notes, "did": del_id, "pid": project_id},
    )
    db.commit()
    return {"deliverable_id": del_id, "status": "delivered"}


# ─────────────────────────────────────────────────────────────────────────────
# BUDGET
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{project_id}/budget", status_code=201, summary="Tambah baris anggaran proyek")
def add_budget_line(
    project_id: str,
    req: BudgetLineCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "finance", "admin")
    bl_id = str(uuid4())
    db.execute(
        text(
            "INSERT INTO project_budget_line "
            "(id, project_id, cost_type, description, wbs_item_id, "
            " quantity, unit_price, gl_account_code, notes, created_by) "
            "VALUES (:id, :pid, :ct, :desc, :wid, :qty, :up, :gl, :notes, :cby)"
        ),
        {
            "id": bl_id, "pid": project_id, "ct": req.cost_type,
            "desc": req.description, "wid": req.wbs_item_id,
            "qty": req.quantity, "up": req.unit_price,
            "gl": req.gl_account_code, "notes": req.notes,
            "cby": current_user["username"],
        },
    )
    db.commit()
    return {
        "budget_line_id": bl_id,
        "planned_amount": round(req.quantity * req.unit_price, 2),
    }


@router.get("/{project_id}/budget", summary="Budget vs Actual per cost_type")
def get_budget(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return ProjectSetupEngine.get_budget_summary(db, project_id)


@router.get("/{project_id}/budget/lines", summary="Daftar baris anggaran proyek (mentah, dengan id)")
def list_budget_lines(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    rows = db.execute(
        text(
            "SELECT * FROM project_budget_line WHERE project_id = :pid "
            "ORDER BY cost_type, created_at"
        ),
        {"pid": project_id},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.put("/{project_id}/budget/{line_id}/actual", summary="Update actual_amount baris anggaran")
def update_actual(
    project_id: str,
    line_id: str,
    actual_amount: float = Query(..., ge=0),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "finance", "admin")
    db.execute(
        text(
            "UPDATE project_budget_line "
            "SET actual_amount = :aa "
            "WHERE id = :lid AND project_id = :pid"
        ),
        {"aa": actual_amount, "lid": line_id, "pid": project_id},
    )
    db.commit()
    return {"line_id": line_id, "actual_amount": actual_amount}


# ─────────────────────────────────────────────────────────────────────────────
# RISK REGISTER
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{project_id}/risks", status_code=201, summary="Tambah risiko ke risk register")
def create_risk(
    project_id: str,
    req: RiskCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    risk_id   = str(uuid4())
    risk_code = _next_risk_code(db, project_id)
    db.execute(
        text(
            "INSERT INTO project_risk "
            "(id, project_id, risk_code, risk_title, description, category, "
            " probability, impact, mitigation_plan, contingency_plan, "
            " risk_owner_id, financial_impact, identified_by) "
            "VALUES (:id, :pid, :rc, :rt, :desc, :cat, "
            "        :prob, :imp, :mit, :con, "
            "        :oid, :fi, :iby)"
        ),
        {
            "id": risk_id, "pid": project_id, "rc": risk_code,
            "rt": req.risk_title, "desc": req.description, "cat": req.category,
            "prob": req.probability, "imp": req.impact,
            "mit": req.mitigation_plan, "con": req.contingency_plan,
            "oid": req.risk_owner_id, "fi": req.financial_impact,
            "iby": current_user["username"],
        },
    )
    db.commit()
    score = req.probability * req.impact
    return {
        "risk_id":   risk_id,
        "risk_code": risk_code,
        "risk_score": score,
        "risk_level": "critical" if score >= 15 else "high" if score >= 9 else "medium" if score >= 4 else "low",
    }


@router.get("/{project_id}/risks", summary="Risk register + heatmap")
def get_risks(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return ProjectSetupEngine.get_risk_analysis(db, project_id)


@router.put("/{project_id}/risks/{risk_id}/status", summary="Update status risiko")
def update_risk_status(
    project_id: str,
    risk_id: str,
    status: str = Query(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    valid = ("identified", "mitigating", "resolved", "accepted", "occurred", "closed")
    if status not in valid:
        raise HTTPException(400, f"status: {' | '.join(valid)}")
    db.execute(
        text(
            "UPDATE project_risk SET status = :st, updated_at = NOW() "
            "WHERE id = :rid AND project_id = :pid"
        ),
        {"st": status, "rid": risk_id, "pid": project_id},
    )
    db.commit()
    return {"risk_id": risk_id, "status": status}


# ─────────────────────────────────────────────────────────────────────────────
# COMMUNICATION PLAN
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{project_id}/communication-plan", status_code=201, summary="Tambah entri communication plan")
def add_comm_plan(
    project_id: str,
    req: CommunicationPlanCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    cp_id = str(uuid4())
    db.execute(
        text(
            "INSERT INTO communication_plan "
            "(id, project_id, meeting_type, frequency, day_of_week, day_of_month, "
            " duration_minutes, participants, facilitator_id, agenda_template, "
            " communication_channel, output_document, notes) "
            "VALUES (:id, :pid, :mt, :freq, :dow, :dom, "
            "        :dur, :part, :fid, :agenda, :chan, :out, :notes)"
        ),
        {
            "id": cp_id, "pid": project_id, "mt": req.meeting_type, "freq": req.frequency,
            "dow": req.day_of_week, "dom": req.day_of_month,
            "dur": req.duration_minutes, "part": req.participants,
            "fid": req.facilitator_id, "agenda": req.agenda_template,
            "chan": req.communication_channel, "out": req.output_document, "notes": req.notes,
        },
    )
    db.commit()
    return {"comm_plan_id": cp_id}


@router.get("/{project_id}/communication-plan", summary="Communication Matrix proyek")
def get_comm_plan(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    rows = db.execute(
        text(
            "SELECT cp.*, e.full_name AS facilitator_name "
            "FROM communication_plan cp "
            "LEFT JOIN employee e ON e.id = cp.facilitator_id "
            "WHERE cp.project_id = :pid ORDER BY cp.meeting_type"
        ),
        {"pid": project_id},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# CHANGE REQUESTS
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{project_id}/change-requests", status_code=201, summary="Submit Change Request")
def create_cr(
    project_id: str,
    req: ChangeRequestCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    cr_no = _gen_cr_no(db)
    cr_id = str(uuid4())
    db.execute(
        text(
            "INSERT INTO project_change_request "
            "(id, project_id, cr_no, cr_title, description, change_type, "
            " impact_scope, impact_schedule_days, impact_budget, requested_by) "
            "VALUES (:id, :pid, :cno, :ct, :desc, :ctype, "
            "        :iscope, :isched, :ibud, :rby)"
        ),
        {
            "id": cr_id, "pid": project_id, "cno": cr_no, "ct": req.cr_title,
            "desc": req.description, "ctype": req.change_type,
            "iscope": req.impact_scope, "isched": req.impact_schedule_days,
            "ibud": req.impact_budget,
            "rby": req.requested_by or current_user["username"],
        },
    )
    db.commit()
    return {"cr_id": cr_id, "cr_no": cr_no}


@router.get("/{project_id}/change-requests", summary="Daftar Change Requests")
def list_crs(
    project_id: str,
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    q = "SELECT * FROM project_change_request WHERE project_id = :pid"
    params: dict[str, Any] = {"pid": project_id}
    if status:
        q += " AND status = :st"; params["st"] = status
    rows = db.execute(text(q + " ORDER BY created_at DESC"), params).fetchall()
    return [dict(r._mapping) for r in rows]


@router.put("/{project_id}/change-requests/{cr_id}/review", summary="Approve / Reject Change Request")
def review_cr(
    project_id: str,
    cr_id: str,
    action: str = Query(...),
    notes: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "admin")
    valid = ("approved", "rejected", "deferred", "implemented")
    if action not in valid:
        raise HTTPException(400, f"action: {' | '.join(valid)}")
    db.execute(
        text(
            "UPDATE project_change_request "
            "SET status = :st, reviewed_by = :rby, reviewed_at = NOW(), notes = :notes "
            "WHERE id = :crid AND project_id = :pid"
        ),
        {"st": action, "rby": current_user["username"], "notes": notes, "crid": cr_id, "pid": project_id},
    )
    db.commit()
    return {"cr_id": cr_id, "status": action}


# ─────────────────────────────────────────────────────────────────────────────
# REPORTS & ANALYTICS
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{project_id}/compute-cpm", summary="Hitung Critical Path Method (CPM)")
def compute_cpm(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Jalankan algoritma CPM:
    - Forward pass (Early Start/Finish)
    - Backward pass (Late Start/Finish)
    - Hitung Total Float dan Free Float
    - Tandai is_critical=TRUE untuk task di jalur kritis

    Wajib dijalankan ulang setiap ada perubahan task atau dependency.
    """
    result = ProjectSetupEngine.compute_cpm(db, project_id)
    return result


@router.get("/{project_id}/gantt", summary="Data Gantt Chart (tasks + milestones + dependencies)")
def get_gantt(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return ProjectSetupEngine.get_gantt_data(db, project_id)


@router.get("/{project_id}/evm", summary="Earned Value Management (EVM) analysis")
def get_evm(
    project_id: str,
    status_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    EVM Indicators:
    - BAC, PV, EV, AC
    - SPI (schedule), CPI (cost)
    - EAC, ETC, VAC
    - RAG status (on_track / at_risk / behind / over_budget)
    """
    try:
        return ProjectSetupEngine.get_evm(db, project_id, status_date)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/{project_id}/resource-loading", summary="Utilization rate per anggota tim")
def resource_loading(
    project_id: str,
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return ProjectSetupEngine.get_resource_loading(db, project_id, date_from, date_to)


@router.get("/{project_id}/mandays", summary="Mandays Plan vs Actual (terintegrasi project_timesheet)")
def mandays_summary(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return ProjectSetupEngine.get_mandays_summary(db, project_id)


@router.get("/{project_id}/health", summary="Project Health Dashboard (RAG, EVM, Risk, Milestone)")
def project_health(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    One-stop health dashboard:
    - RAG status (Green / Amber / Red)
    - Progress % (weighted by planned cost)
    - Task stats (total, completed, overdue, critical)
    - Milestone stats
    - Budget KPIs (CPI, SPI, EAC, VAC)
    - Risk summary (by level + financial exposure)
    """
    try:
        return ProjectSetupEngine.get_project_health(db, project_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
