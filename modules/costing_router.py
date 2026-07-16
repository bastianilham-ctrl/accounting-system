# modules/costing_router.py
# Unit Costing & Overhead Allocation REST API
#
# Endpoint groups:
#   /costing/projects         — master proyek
#   /costing/cost-rates       — tarif biaya per karyawan
#   /costing/assignments      — penugasan karyawan ke proyek
#   /costing/timesheets       — log jam kerja per proyek
#   /costing/allocation       — rules + execution engine
#   /costing/period           — kontrol lock periode analitik
#   /costing/reports          — project P&L, utilisasi, comparison
#   /costing/labor-reclass    — GL reclass payroll ke per-project cost_center
#   /costing/payroll-variance — variance analytic estimate vs aktual payroll

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
from modules.costing_engine import CostingEngine

router = APIRouter(prefix="/costing", tags=["Costing — Unit Cost & Project P&L"])

_viewer  = [Depends(require_min_role("viewer"))]
_finance = [Depends(require_min_role("finance"))]
_admin   = [Depends(require_min_role("admin"))]


# ── Request Schemas ────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    entity_id:            str
    project_code:         str
    project_name:         str
    client_name:          Optional[str] = None
    client_id:            Optional[str] = None
    project_type:         str = Field("time_and_material",
                              pattern="^(time_and_material|fixed_price|retainer|milestone)$")
    start_date:           Optional[date] = None
    end_date:             Optional[date] = None
    contract_value:       float = 0
    billing_rate_per_hour: Optional[float] = None
    project_manager:      Optional[str] = None
    cost_center:          Optional[str] = None
    notes:                Optional[str] = None


class ProjectStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(draft|active|on_hold|completed|cancelled)$")
    notes:  Optional[str] = None


class CostRateCreate(BaseModel):
    employee_id:               str
    entity_id:                 str
    fiscal_year:               int
    annual_base_salary:        float
    annual_thr_bonus:          float = 0
    annual_bpjs_employer:      float = 0
    annual_private_insurance:  float = 0
    annual_asset_depreciation: float = 0
    annual_training_budget:    float = 0
    annual_other_benefits:     float = 0
    national_holidays:         int   = 16
    annual_leave_days:         int   = 12
    utilization_rate:          float = Field(0.80, ge=0.10, le=1.00)
    notes:                     Optional[str] = None


class CostRateSimulate(BaseModel):
    annual_base_salary:        float
    annual_thr_bonus:          float = 0
    annual_bpjs_employer:      float = 0
    annual_private_insurance:  float = 0
    annual_asset_depreciation: float = 0
    annual_training_budget:    float = 0
    annual_other_benefits:     float = 0
    fiscal_year:               int   = datetime.now().year
    national_holidays:         int   = 16
    annual_leave_days:         int   = 12
    utilization_rate:          float = Field(0.80, ge=0.10, le=1.00)


class AssignmentCreate(BaseModel):
    project_id:            str
    employee_id:           str
    entity_id:             str
    role_in_project:       Optional[str] = None
    assigned_from:         date
    assigned_to:           Optional[date] = None
    planned_hours:         Optional[float] = None
    billing_rate_override: Optional[float] = None


class TimesheetCreate(BaseModel):
    employee_id:    str
    project_id:     Optional[str] = None
    task_id:        Optional[str] = None      # Activity/Task — dependent dropdown on project_id (BRD)
    entity_id:      str
    timesheet_date: date
    hours:          float = Field(..., ge=0.5, le=24)
    activity_type:  str = Field("billable",
                        pattern="^(billable|non_billable|bench|internal|training)$")
    description:    str = Field(..., min_length=10)

    @field_validator("timesheet_date")
    @classmethod
    def _date_not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("Tanggal timesheet tidak boleh di masa depan")
        return v


class TimesheetBatchCreate(BaseModel):
    entity_id:  str
    entries:    list[TimesheetCreate] = Field(..., min_length=1)


class AllocationRuleCreate(BaseModel):
    entity_id:           str
    rule_name:           str
    description:         Optional[str] = None
    sender_cost_center:  str
    allocation_basis:    str = Field("REVENUE",
                             pattern="^(REVENUE|TIMESHEET|HEADCOUNT|EQUAL)$")
    overhead_account_code: Optional[str] = None


class AllocationDestinationAdd(BaseModel):
    project_ids:  list[str]
    fixed_ratios: Optional[list[float]] = None  # harus sama panjang dengan project_ids jika diisi


class RunLaborPayload(BaseModel):
    entity_id: str
    year:      int
    month:     int = Field(..., ge=1, le=12)


class RunOverheadPayload(BaseModel):
    rule_id:  str
    year:     int
    month:    int = Field(..., ge=1, le=12)


class ReversePayload(BaseModel):
    entity_id:    str
    year:         int
    month:        int = Field(..., ge=1, le=12)
    journal_type: Optional[str] = Field(None,
                      pattern="^(labor_allocation|revenue_tagging|overhead_allocation)$")


class LockPeriodPayload(BaseModel):
    entity_id: str
    year:      int
    month:     int = Field(..., ge=1, le=12)
    force:     bool = False


# ── Project Endpoints ──────────────────────────────────────────────────────────

@router.post("/projects", dependencies=_finance, summary="Buat proyek baru")
def create_project(
    payload: ProjectCreate,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("finance")),
):
    pid = str(uuid4())
    # Default cost_center = project_code jika tidak diisi
    cc = payload.cost_center or payload.project_code

    db.execute(
        text("""
            INSERT INTO project (
                id, entity_id, project_code, project_name, client_name, client_id,
                project_type, status, start_date, end_date, contract_value,
                billing_rate_per_hour, project_manager, cost_center, notes,
                created_by, created_at, updated_at
            ) VALUES (
                :id, :eid, :code, :name, :client, :cid,
                :ptype, 'active', :start, :end, :cv,
                :brate, :pm, :cc, :notes,
                :by, NOW(), NOW()
            )
        """),
        {
            "id": pid, "eid": payload.entity_id, "code": payload.project_code,
            "name": payload.project_name, "client": payload.client_name,
            "cid": payload.client_id, "ptype": payload.project_type,
            "start": payload.start_date, "end": payload.end_date,
            "cv": payload.contract_value,
            "brate": payload.billing_rate_per_hour, "pm": payload.project_manager,
            "cc": cc, "notes": payload.notes, "by": user.get("email", "system"),
        }
    )
    db.commit()
    return {"project_id": pid, "project_code": payload.project_code, "cost_center": cc}


@router.get("/projects", dependencies=_viewer, summary="List proyek")
def list_projects(
    entity_id: str = Query(...),
    status:    Optional[str] = Query(None),
    db:        Session = Depends(get_db),
):
    cond = "entity_id = :eid"
    params: dict = {"eid": entity_id}
    if status:
        cond += " AND status = :status"
        params["status"] = status

    rows = db.execute(
        text(f"SELECT * FROM project WHERE {cond} ORDER BY project_code"),
        params
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/projects/{project_id}", dependencies=_viewer, summary="Detail proyek")
def get_project(project_id: str, db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT * FROM project WHERE id = :id"),
        {"id": project_id}
    ).fetchone()
    if not row:
        raise HTTPException(404, "Proyek tidak ditemukan")
    return dict(row._mapping)


@router.put("/projects/{project_id}/status", dependencies=_finance, summary="Update status proyek")
def update_project_status(
    project_id: str,
    payload:    ProjectStatusUpdate,
    db:         Session = Depends(get_db),
):
    db.execute(
        text("UPDATE project SET status = :s, notes = :n, updated_at = NOW() WHERE id = :id"),
        {"id": project_id, "s": payload.status, "n": payload.notes}
    )
    db.commit()
    return {"project_id": project_id, "status": payload.status}


# ── Employee Cost Rate Endpoints ───────────────────────────────────────────────

@router.post("/cost-rates/simulate",
             summary="Simulasi unit cost per jam (tanpa login)")
def simulate_unit_cost(payload: CostRateSimulate):
    """Hitung unit_cost_per_hour dari input CTC + parameter jam. Tidak perlu autentikasi."""
    from modules.costing_engine import CostingEngine, _count_weekends
    from calendar import isleap

    total_ctc = sum([
        payload.annual_base_salary,
        payload.annual_thr_bonus,
        payload.annual_bpjs_employer,
        payload.annual_private_insurance,
        payload.annual_asset_depreciation,
        payload.annual_training_budget,
        payload.annual_other_benefits,
    ])
    total_days  = 366 if isleap(payload.fiscal_year) else 365
    weekends    = _count_weekends(payload.fiscal_year)
    avail_days  = total_days - weekends - payload.national_holidays - payload.annual_leave_days
    avail_hours = avail_days * 8
    target      = avail_hours * payload.utilization_rate
    unit_cost   = round(total_ctc / target, 2) if target > 0 else 0

    return {
        "fiscal_year":          payload.fiscal_year,
        "total_ctc":            total_ctc,
        "total_days":           total_days,
        "weekends":             weekends,
        "national_holidays":    payload.national_holidays,
        "annual_leave":         payload.annual_leave_days,
        "available_days":       avail_days,
        "available_hours":      avail_hours,
        "utilization_rate":     payload.utilization_rate,
        "target_billable_hours": round(target, 2),
        "unit_cost_per_hour":   unit_cost,
        "ctc_breakdown": {
            "annual_base_salary":        payload.annual_base_salary,
            "annual_thr_bonus":          payload.annual_thr_bonus,
            "annual_bpjs_employer":      payload.annual_bpjs_employer,
            "annual_private_insurance":  payload.annual_private_insurance,
            "annual_asset_depreciation": payload.annual_asset_depreciation,
            "annual_training_budget":    payload.annual_training_budget,
            "annual_other_benefits":     payload.annual_other_benefits,
        }
    }


@router.get("/cost-rates/reference/target-hours",
            summary="Referensi perhitungan target billable hours")
def reference_target_hours(
    fiscal_year:       int   = Query(datetime.now().year),
    national_holidays: int   = Query(16, ge=0, le=30),
    annual_leave:      int   = Query(12, ge=0, le=30),
    utilization_rate:  float = Query(0.80, ge=0.10, le=1.00),
):
    engine = CostingEngine(None)
    result = engine.calculate_target_hours(fiscal_year, national_holidays, annual_leave, utilization_rate)
    return result.to_dict()


@router.post("/cost-rates", dependencies=_finance, summary="Input cost rate tahunan karyawan")
def create_cost_rate(
    payload: CostRateCreate,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("finance")),
):
    total_ctc = sum([
        payload.annual_base_salary, payload.annual_thr_bonus,
        payload.annual_bpjs_employer, payload.annual_private_insurance,
        payload.annual_asset_depreciation, payload.annual_training_budget,
        payload.annual_other_benefits,
    ])
    db.execute(
        text("""
            INSERT INTO employee_cost_rate (
                id, employee_id, entity_id, fiscal_year,
                annual_base_salary, annual_thr_bonus, annual_bpjs_employer,
                annual_private_insurance, annual_asset_depreciation,
                annual_training_budget, annual_other_benefits,
                total_ctc, national_holidays, annual_leave_days, utilization_rate,
                notes, created_by, created_at, updated_at
            ) VALUES (
                uuid_generate_v4(), :emp, :eid, :yr,
                :bas, :thr, :bpjs, :ins, :dep, :trn, :oth,
                :ctc, :nh, :al, :util, :notes, :by, NOW(), NOW()
            )
            ON CONFLICT (employee_id, fiscal_year) DO UPDATE SET
                annual_base_salary        = EXCLUDED.annual_base_salary,
                annual_thr_bonus          = EXCLUDED.annual_thr_bonus,
                annual_bpjs_employer      = EXCLUDED.annual_bpjs_employer,
                annual_private_insurance  = EXCLUDED.annual_private_insurance,
                annual_asset_depreciation = EXCLUDED.annual_asset_depreciation,
                annual_training_budget    = EXCLUDED.annual_training_budget,
                annual_other_benefits     = EXCLUDED.annual_other_benefits,
                total_ctc                 = EXCLUDED.total_ctc,
                national_holidays         = EXCLUDED.national_holidays,
                annual_leave_days         = EXCLUDED.annual_leave_days,
                utilization_rate          = EXCLUDED.utilization_rate,
                is_approved               = FALSE,
                updated_at                = NOW()
        """),
        {
            "emp": payload.employee_id, "eid": payload.entity_id, "yr": payload.fiscal_year,
            "bas": payload.annual_base_salary, "thr": payload.annual_thr_bonus,
            "bpjs": payload.annual_bpjs_employer, "ins": payload.annual_private_insurance,
            "dep": payload.annual_asset_depreciation, "trn": payload.annual_training_budget,
            "oth": payload.annual_other_benefits, "ctc": total_ctc,
            "nh": payload.national_holidays, "al": payload.annual_leave_days,
            "util": payload.utilization_rate,
            "notes": payload.notes, "by": user.get("email", "system"),
        }
    )
    db.commit()
    return {"status": "saved", "total_ctc": total_ctc, "employee_id": payload.employee_id,
            "fiscal_year": payload.fiscal_year}


@router.get("/cost-rates/{employee_id}/{fiscal_year}",
            dependencies=_viewer, summary="Ambil cost rate karyawan")
def get_cost_rate(employee_id: str, fiscal_year: int, db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT * FROM employee_cost_rate WHERE employee_id = :eid AND fiscal_year = :yr"),
        {"eid": employee_id, "yr": fiscal_year}
    ).fetchone()
    if not row:
        raise HTTPException(404, "Cost rate tidak ditemukan")
    return dict(row._mapping)


@router.post("/cost-rates/{employee_id}/{fiscal_year}/calculate",
             dependencies=_finance, summary="Hitung ulang unit_cost_per_hour")
def calculate_cost_rate(
    employee_id: str,
    fiscal_year: int,
    db:          Session = Depends(get_db),
):
    engine = CostingEngine(db)
    try:
        result = engine.refresh_unit_cost(employee_id, fiscal_year)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result.to_dict()


@router.post("/cost-rates/{employee_id}/{fiscal_year}/approve",
             dependencies=_admin, summary="Approve cost rate karyawan")
def approve_cost_rate(
    employee_id: str,
    fiscal_year: int,
    db:          Session = Depends(get_db),
    user=        Depends(require_min_role("admin")),
):
    # Auto-calculate sebelum approve
    engine = CostingEngine(db)
    try:
        engine.refresh_unit_cost(employee_id, fiscal_year)
    except ValueError as e:
        raise HTTPException(400, str(e))

    db.execute(
        text("""
            UPDATE employee_cost_rate SET
                is_approved = TRUE, approved_by = :by, approved_at = NOW(), updated_at = NOW()
            WHERE employee_id = :eid AND fiscal_year = :yr
        """),
        {"by": user.get("email"), "eid": employee_id, "yr": fiscal_year}
    )
    db.commit()
    return {"status": "approved", "employee_id": employee_id, "fiscal_year": fiscal_year}


@router.get("/cost-rates", dependencies=_viewer, summary="List cost rates per entity/tahun")
def list_cost_rates(
    entity_id:   str = Query(...),
    fiscal_year: int = Query(...),
    db:          Session = Depends(get_db),
):
    rows = db.execute(
        text("""
            SELECT ecr.*, e.full_name, e.job_title
            FROM employee_cost_rate ecr
            JOIN employee e ON e.id = ecr.employee_id
            WHERE ecr.entity_id = :eid AND ecr.fiscal_year = :yr
            ORDER BY e.full_name
        """),
        {"eid": entity_id, "yr": fiscal_year}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Project Assignment Endpoints ───────────────────────────────────────────────

@router.post("/assignments", dependencies=_finance, summary="Tugaskan konsultan ke proyek")
def create_assignment(
    payload: AssignmentCreate,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("finance")),
):
    aid = str(uuid4())
    db.execute(
        text("""
            INSERT INTO project_assignment (
                id, project_id, employee_id, entity_id,
                role_in_project, assigned_from, assigned_to,
                planned_hours, billing_rate_override, created_by
            ) VALUES (
                :id, :pid, :eid_emp, :eid,
                :role, :from, :to, :hrs, :brate, :by
            )
            ON CONFLICT (project_id, employee_id, assigned_from) DO NOTHING
        """),
        {
            "id": aid, "pid": payload.project_id,
            "eid_emp": payload.employee_id, "eid": payload.entity_id,
            "role": payload.role_in_project,
            "from": payload.assigned_from, "to": payload.assigned_to,
            "hrs": payload.planned_hours, "brate": payload.billing_rate_override,
            "by": user.get("email", "system"),
        }
    )
    db.commit()
    return {"assignment_id": aid}


@router.get("/assignments", dependencies=_viewer, summary="List penugasan")
def list_assignments(
    project_id:  Optional[str] = Query(None),
    employee_id: Optional[str] = Query(None),
    entity_id:   Optional[str] = Query(None),
    active_only: bool = Query(True),
    db:          Session = Depends(get_db),
):
    conds = []
    params: dict = {}
    if project_id:
        conds.append("pa.project_id = :pid")
        params["pid"] = project_id
    if employee_id:
        conds.append("pa.employee_id = :empid")
        params["empid"] = employee_id
    if entity_id:
        conds.append("pa.entity_id = :eid")
        params["eid"] = entity_id
    if active_only:
        conds.append("pa.is_active = TRUE")

    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    rows = db.execute(
        text(f"""
            SELECT pa.*, p.project_name, p.client_name, e.full_name
            FROM project_assignment pa
            JOIN project p ON p.id = pa.project_id
            JOIN employee e ON e.id = pa.employee_id
            {where}
            ORDER BY pa.assigned_from DESC
        """),
        params
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Timesheet Endpoints ────────────────────────────────────────────────────────

def _check_active_assignment(db: Session, employee_id: str, project_id: str, timesheet_date: date) -> None:
    """
    Tolak timesheet kalau employee tidak punya project_assignment aktif untuk project itu
    pada tanggal yang di-log (data governance — siapapun seharusnya tidak bisa log jam ke
    proyek yang dia tidak ditugaskan). project_id NULL (bench/internal) tidak perlu dicek.
    """
    row = db.execute(
        text("""
            SELECT 1 FROM project_assignment
            WHERE employee_id = :emp AND project_id = :pid AND is_active = TRUE
              AND assigned_from <= :dt
              AND (assigned_to IS NULL OR assigned_to >= :dt)
            LIMIT 1
        """),
        {"emp": employee_id, "pid": project_id, "dt": timesheet_date}
    ).fetchone()
    if not row:
        raise HTTPException(
            400,
            f"Employee {employee_id} tidak punya assignment aktif untuk project {project_id} "
            f"pada tanggal {timesheet_date}"
        )


def _check_project_billable(db: Session, project_id: str) -> None:
    """
    BRD: dropdown Project Code cuma boleh nampilin proyek 'Approved dan active' —
    map ke charter_status='approved' (workflow approval PM module) DAN
    status='active' (status operasional Costing module) di tabel `project` yang sama.
    """
    row = db.execute(
        text("SELECT charter_status, status FROM project WHERE id = :pid"),
        {"pid": project_id}
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Project {project_id} tidak ditemukan")
    if row.charter_status != "approved" or row.status != "active":
        raise HTTPException(
            400,
            f"Project {project_id} harus charter_status='approved' dan status='active' "
            f"(saat ini: charter_status='{row.charter_status}', status='{row.status}')"
        )


def _check_task_in_project(db: Session, task_id: str, project_id: Optional[str]) -> None:
    """Activity/Task dropdown dependent ke Project Code — task harus milik project itu."""
    row = db.execute(
        text("SELECT project_id FROM project_task WHERE id = :tid"),
        {"tid": task_id}
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Task {task_id} tidak ditemukan")
    if not project_id or str(row.project_id) != str(project_id):
        raise HTTPException(400, f"Task {task_id} bukan milik project {project_id}")


def _check_daily_cap(db: Session, employee_id: str, timesheet_date: date, new_hours: float) -> bool:
    """
    BRD: total jam employee lintas semua proyek pada 1 tanggal tidak boleh >24 jam.
    Return True kalau total (termasuk entry baru ini) >8 jam — flag overtime, bukan blocking.
    """
    existing = db.execute(
        text("""
            SELECT COALESCE(SUM(hours), 0) AS total
            FROM project_timesheet
            WHERE employee_id = :emp AND timesheet_date = :dt AND status != 'rejected'
        """),
        {"emp": employee_id, "dt": timesheet_date}
    ).scalar()
    total = float(existing or 0) + new_hours
    if total > 24:
        raise HTTPException(
            400,
            f"Total jam employee {employee_id} pada {timesheet_date} akan jadi {total}j "
            f"(melebihi 24j/hari) — sudah ada {existing}j tercatat"
        )
    return total > 8


def _validate_timesheet_entry(db: Session, entry: TimesheetCreate) -> bool:
    if entry.project_id:
        _check_project_billable(db, entry.project_id)
        _check_active_assignment(db, entry.employee_id, entry.project_id, entry.timesheet_date)
    if entry.task_id:
        _check_task_in_project(db, entry.task_id, entry.project_id)
    return _check_daily_cap(db, entry.employee_id, entry.timesheet_date, entry.hours)


@router.post("/timesheets", summary="Log jam kerja harian per proyek/task")
def create_timesheet(
    payload: TimesheetCreate,
    db:      Session = Depends(get_db),
    user=    Depends(get_current_active_user),
):
    overtime_flag = _validate_timesheet_entry(db, payload)
    tid = str(uuid4())
    db.execute(
        text("""
            INSERT INTO project_timesheet (
                id, employee_id, project_id, task_id, entity_id,
                timesheet_date, hours, activity_type, description,
                status, created_by, created_at, updated_at
            ) VALUES (
                :id, :emp, :pid, :tid, :eid,
                :dt, :hrs, :atype, :desc,
                'draft', :by, NOW(), NOW()
            )
        """),
        {
            "id": tid, "emp": payload.employee_id, "pid": payload.project_id, "tid": payload.task_id,
            "eid": payload.entity_id, "dt": payload.timesheet_date,
            "hrs": payload.hours, "atype": payload.activity_type,
            "desc": payload.description, "by": user.get("email", "system"),
        }
    )
    db.commit()
    return {"timesheet_id": tid, "status": "draft", "overtime_flag": overtime_flag}


@router.post("/timesheets/batch", summary="Bulk input timesheet (satu minggu / satu bulan)")
def create_timesheets_batch(
    payload: TimesheetBatchCreate,
    db:      Session = Depends(get_db),
    user=    Depends(get_current_active_user),
):
    count = 0
    overtime_dates = []
    for entry in payload.entries:
        if _validate_timesheet_entry(db, entry):
            overtime_dates.append(str(entry.timesheet_date))
        db.execute(
            text("""
                INSERT INTO project_timesheet (
                    id, employee_id, project_id, task_id, entity_id,
                    timesheet_date, hours, activity_type, description,
                    status, created_by, created_at, updated_at
                ) VALUES (
                    uuid_generate_v4(), :emp, :pid, :tid, :eid,
                    :dt, :hrs, :atype, :desc, 'draft', :by, NOW(), NOW()
                )
            """),
            {
                "emp": entry.employee_id, "pid": entry.project_id, "tid": entry.task_id,
                "eid": payload.entity_id, "dt": entry.timesheet_date,
                "hrs": entry.hours, "atype": entry.activity_type,
                "desc": entry.description, "by": user.get("email", "system"),
            }
        )
        count += 1
    db.commit()
    return {"inserted": count, "overtime_dates": overtime_dates}


@router.get("/timesheets", dependencies=_viewer, summary="List timesheet dengan filter")
def list_timesheets(
    entity_id:   str = Query(...),
    employee_id: Optional[str] = Query(None),
    project_id:  Optional[str] = Query(None),
    status:      Optional[str] = Query(None),
    year:        Optional[int] = Query(None),
    month:       Optional[int] = Query(None),
    db:          Session = Depends(get_db),
):
    conds = ["pt.entity_id = :eid"]
    params: dict = {"eid": entity_id}
    if employee_id:
        conds.append("pt.employee_id = :empid")
        params["empid"] = employee_id
    if project_id:
        conds.append("pt.project_id = :pid")
        params["pid"] = project_id
    if status:
        conds.append("pt.status = :status")
        params["status"] = status
    if year:
        conds.append("EXTRACT(YEAR FROM pt.timesheet_date) = :yr")
        params["yr"] = year
    if month:
        conds.append("EXTRACT(MONTH FROM pt.timesheet_date) = :mo")
        params["mo"] = month

    where = " AND ".join(conds)
    rows = db.execute(
        text(f"""
            SELECT pt.*, p.project_name, p.client_name, e.full_name AS employee_name
            FROM project_timesheet pt
            LEFT JOIN project p ON p.id = pt.project_id
            JOIN employee e ON e.id = pt.employee_id
            WHERE {where}
            ORDER BY pt.timesheet_date DESC, e.full_name
        """),
        params
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/timesheets/summary", dependencies=_viewer, summary="Ringkasan jam per proyek per karyawan")
def timesheet_summary(
    entity_id: str = Query(...),
    year:      int = Query(...),
    month:     int = Query(..., ge=1, le=12),
    db:        Session = Depends(get_db),
):
    from datetime import date as _date
    start = _date(year, month, 1)
    from modules.costing_engine import _last_day
    end = _last_day(year, month)

    rows = db.execute(
        text("""
            SELECT
                pt.employee_id, e.full_name,
                pt.project_id, COALESCE(p.project_name, 'Bench/Internal') AS project_name,
                pt.activity_type,
                SUM(pt.hours) AS total_hours,
                COUNT(*) AS entry_count,
                MIN(pt.status) AS min_status
            FROM project_timesheet pt
            JOIN employee e ON e.id = pt.employee_id
            LEFT JOIN project p ON p.id = pt.project_id
            WHERE pt.entity_id = :eid
              AND pt.timesheet_date BETWEEN :start AND :end
            GROUP BY pt.employee_id, e.full_name, pt.project_id, p.project_name, pt.activity_type
            ORDER BY e.full_name, p.project_name
        """),
        {"eid": entity_id, "start": start, "end": end}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/timesheets/{ts_id}/submit", summary="Submit timesheet untuk approval")
def submit_timesheet(
    ts_id: str,
    db:    Session = Depends(get_db),
    user=  Depends(get_current_active_user),
):
    row = db.execute(
        text("SELECT id, status FROM project_timesheet WHERE id = :id"),
        {"id": ts_id}
    ).fetchone()
    if not row:
        raise HTTPException(404, "Timesheet tidak ditemukan")
    if row.status != "draft":
        raise HTTPException(400, f"Timesheet status '{row.status}', harus 'draft'")

    db.execute(
        text("""
            UPDATE project_timesheet SET
                status = 'submitted', submitted_at = NOW(), updated_at = NOW()
            WHERE id = :id
        """),
        {"id": ts_id}
    )
    db.commit()
    return {"timesheet_id": ts_id, "status": "submitted"}


def _apply_timesheet_to_task(db: Session, task_id: str, hours: float) -> None:
    """
    BRD section 4: saat timesheet approved/posted, roll up jam ke project_task.actual_hours.
    Murni informational (basis Burn Rate/utilization report) — TIDAK menggerakkan
    weighted progress milestone, karena progress_pct task tetap assessment manual PM lewat
    modal Update Aktual (keputusan user 2026-06-24: jam kerja besar belum tentu = progress
    besar, mis. stuck debugging 8 jam tapi progress masih 0%).
    """
    db.execute(
        text("UPDATE project_task SET actual_hours = actual_hours + :hrs, updated_at = NOW() WHERE id = :tid"),
        {"hrs": hours, "tid": task_id}
    )


@router.post("/timesheets/{ts_id}/approve", dependencies=_finance,
             summary="Approve / reject timesheet")
def approve_timesheet(
    ts_id:  str,
    action: str = Query(..., pattern="^(approved|rejected)$"),
    reason: Optional[str] = Query(None),
    db:     Session = Depends(get_db),
    user=   Depends(require_min_role("finance")),
):
    row = db.execute(
        text("SELECT id, status, task_id, hours FROM project_timesheet WHERE id = :id"),
        {"id": ts_id}
    ).fetchone()
    if not row or row.status != "submitted":
        raise HTTPException(400, "Timesheet harus dalam status 'submitted'")

    db.execute(
        text("""
            UPDATE project_timesheet SET
                status = :s, approved_by = :by, approved_at = NOW(),
                rejection_reason = :reason, updated_at = NOW()
            WHERE id = :id
        """),
        {"id": ts_id, "s": action, "by": user.get("email"), "reason": reason}
    )
    if action == "approved" and row.task_id:
        _apply_timesheet_to_task(db, str(row.task_id), float(row.hours))
    db.commit()
    return {"timesheet_id": ts_id, "status": action}


@router.post("/timesheets/bulk-approve", dependencies=_finance,
             summary="Approve semua timesheet submitted dalam satu periode")
def bulk_approve_timesheets(
    entity_id: str = Query(...),
    year:      int = Query(...),
    month:     int = Query(..., ge=1, le=12),
    db:        Session = Depends(get_db),
    user=      Depends(require_min_role("finance")),
):
    from datetime import date as _date
    from modules.costing_engine import _last_day
    start = _date(year, month, 1)
    end   = _last_day(year, month)

    # Kumpulkan task_id+hours SEBELUM bulk UPDATE — dibutuhkan untuk rollup actual_hours
    # per task (BRD section 4), karena bulk UPDATE di bawah tidak per-row di Python.
    pending = db.execute(
        text("""
            SELECT task_id, SUM(hours) AS total_hours
            FROM project_timesheet
            WHERE entity_id = :eid AND status = 'submitted'
              AND timesheet_date BETWEEN :start AND :end AND task_id IS NOT NULL
            GROUP BY task_id
        """),
        {"eid": entity_id, "start": start, "end": end}
    ).fetchall()

    result = db.execute(
        text("""
            UPDATE project_timesheet SET
                status = 'approved', approved_by = :by, approved_at = NOW(), updated_at = NOW()
            WHERE entity_id = :eid
              AND status = 'submitted'
              AND timesheet_date BETWEEN :start AND :end
        """),
        {"eid": entity_id, "by": user.get("email"), "start": start, "end": end}
    )
    for p in pending:
        _apply_timesheet_to_task(db, str(p.task_id), float(p.total_hours))
    db.commit()
    return {"approved": result.rowcount, "period": f"{year}-{month:02d}"}


class GenerateInvoicePayload(BaseModel):
    project_id:           str
    invoice_date:          date
    ppn_rate:              int = Field(11, ge=0, le=100)
    revenue_account_code:  str = "4-1-001"


@router.post("/timesheets/generate-invoice", dependencies=_finance,
             summary="Generate draft AR invoice dari timesheet approved+billable yang belum ditagih")
def generate_invoice_from_timesheets(
    payload: GenerateInvoicePayload,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("finance")),
):
    engine = CostingEngine(db)
    try:
        result = engine.generate_invoice_from_timesheets(
            project_id=payload.project_id,
            invoice_date=payload.invoice_date,
            created_by=user.get("email", "system"),
            ppn_rate=payload.ppn_rate,
            revenue_account_code=payload.revenue_account_code,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


# ── Allocation Rules Endpoints ─────────────────────────────────────────────────

@router.post("/allocation/rules", dependencies=_admin, summary="Buat aturan alokasi overhead")
def create_allocation_rule(
    payload: AllocationRuleCreate,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("admin")),
):
    rid = str(uuid4())
    db.execute(
        text("""
            INSERT INTO allocation_rules (
                id, entity_id, rule_name, description,
                sender_cost_center, allocation_basis, overhead_account_code,
                is_active, created_by
            ) VALUES (
                :id, :eid, :name, :desc,
                :cc, :basis, :acc, TRUE, :by
            )
        """),
        {
            "id": rid, "eid": payload.entity_id, "name": payload.rule_name,
            "desc": payload.description, "cc": payload.sender_cost_center,
            "basis": payload.allocation_basis, "acc": payload.overhead_account_code,
            "by": user.get("email", "system"),
        }
    )
    db.commit()
    return {"rule_id": rid, "rule_name": payload.rule_name}


@router.post("/allocation/rules/{rule_id}/destinations", dependencies=_admin,
             summary="Tambah proyek penerima ke rule")
def add_destinations(
    rule_id: str,
    payload: AllocationDestinationAdd,
    db:      Session = Depends(get_db),
):
    if payload.fixed_ratios and len(payload.fixed_ratios) != len(payload.project_ids):
        raise HTTPException(400, "Jumlah fixed_ratios harus sama dengan jumlah project_ids")

    for i, pid in enumerate(payload.project_ids):
        ratio = payload.fixed_ratios[i] if payload.fixed_ratios else None
        db.execute(
            text("""
                INSERT INTO allocation_destinations (id, rule_id, project_id, fixed_ratio)
                VALUES (uuid_generate_v4(), :rid, :pid, :ratio)
                ON CONFLICT (rule_id, project_id) DO UPDATE SET fixed_ratio = EXCLUDED.fixed_ratio
            """),
            {"rid": rule_id, "pid": pid, "ratio": ratio}
        )
    db.commit()
    return {"rule_id": rule_id, "destinations_added": len(payload.project_ids)}


@router.get("/allocation/rules", dependencies=_viewer, summary="List allocation rules")
def list_allocation_rules(entity_id: str = Query(...), db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT ar.*, COUNT(ad.id) AS destination_count
            FROM allocation_rules ar
            LEFT JOIN allocation_destinations ad ON ad.rule_id = ar.id
            WHERE ar.entity_id = :eid
            GROUP BY ar.id
            ORDER BY ar.rule_name
        """),
        {"eid": entity_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/allocation/rules/{rule_id}", dependencies=_viewer,
            summary="Detail rule + destinations")
def get_allocation_rule(rule_id: str, db: Session = Depends(get_db)):
    rule = db.execute(
        text("SELECT * FROM allocation_rules WHERE id = :id"),
        {"id": rule_id}
    ).fetchone()
    if not rule:
        raise HTTPException(404, "Rule tidak ditemukan")

    destinations = db.execute(
        text("""
            SELECT ad.*, p.project_code, p.project_name, p.client_name
            FROM allocation_destinations ad
            JOIN project p ON p.id = ad.project_id
            WHERE ad.rule_id = :id
        """),
        {"id": rule_id}
    ).fetchall()

    return {
        **dict(rule._mapping),
        "destinations": [dict(r._mapping) for r in destinations],
    }


# ── Allocation Execution Endpoints ─────────────────────────────────────────────

@router.post("/allocation/run/labor", dependencies=_finance,
             summary="Jalankan Labor Allocation (timesheet → analytic journal)")
def run_labor_allocation(
    payload: RunLaborPayload,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("finance")),
):
    engine = CostingEngine(db)
    try:
        result = engine.post_labor_allocation(
            payload.entity_id, payload.year, payload.month,
            created_by=user.get("email", "system")
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.post("/allocation/run/revenue", dependencies=_finance,
             summary="Jalankan Revenue Tagging (AR invoice → analytic journal)")
def run_revenue_tagging(
    payload: RunLaborPayload,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("finance")),
):
    engine = CostingEngine(db)
    try:
        result = engine.tag_revenue_to_analytic(
            payload.entity_id, payload.year, payload.month,
            created_by=user.get("email", "system")
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.post("/allocation/run/overhead", dependencies=_finance,
             summary="Jalankan Overhead Allocation Engine (G&A Pool → proyek)")
def run_overhead_allocation(
    payload: RunOverheadPayload,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("finance")),
):
    engine = CostingEngine(db)
    try:
        result = engine.execute_overhead_allocation(
            payload.rule_id, payload.year, payload.month,
            created_by=user.get("email", "system")
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.post("/allocation/reverse", dependencies=_finance,
             summary="Rollback jurnal analitik sebelum lock (BAB V §2)")
def reverse_allocation(
    payload: ReversePayload,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("finance")),
):
    engine = CostingEngine(db)
    try:
        result = engine.reverse_analytic_period(
            payload.entity_id, payload.year, payload.month,
            journal_type=payload.journal_type,
            created_by=user.get("email", "system")
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


# ── Period Control Endpoints ───────────────────────────────────────────────────

@router.get("/period/{entity_id}/{year}/{month}", dependencies=_viewer,
            summary="Status periode analitik")
def get_period_status(entity_id: str, year: int, month: int, db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT * FROM analytic_period WHERE entity_id = :eid AND year = :yr AND month = :mo"),
        {"eid": entity_id, "yr": year, "mo": month}
    ).fetchone()
    if not row:
        return {
            "entity_id": entity_id, "year": year, "month": month,
            "status": "not_started",
            "labor_allocation_posted": False,
            "revenue_tagging_posted": False,
            "overhead_allocation_posted": False,
        }
    return dict(row._mapping)


@router.post("/period/lock", dependencies=_admin,
             summary="Lock periode analitik setelah validasi (BAB V §3)")
def lock_period(
    payload: LockPeriodPayload,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("admin")),
):
    engine = CostingEngine(db)
    try:
        if payload.force:
            result = engine.lock_analytic_period_force(
                payload.entity_id, payload.year, payload.month, user.get("email", "admin"))
        else:
            result = engine.lock_analytic_period(
                payload.entity_id, payload.year, payload.month, user.get("email", "admin"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


# ── Report Endpoints ───────────────────────────────────────────────────────────

@router.get("/reports/project-pnl", dependencies=_viewer,
            summary="Laporan Laba Rugi Per Proyek (analytic)")
def report_project_pnl(
    entity_id: str = Query(...),
    year:      int = Query(...),
    month:     Optional[int] = Query(None, ge=1, le=12),
    db:        Session = Depends(get_db),
):
    engine = CostingEngine(db)
    rows = engine.get_project_pnl(entity_id, year, month)
    # Tambah total baris
    total = {
        "project_code":    "TOTAL",
        "project_name":    "Total Semua Proyek",
        "revenue":         sum(r["revenue"] for r in rows),
        "direct_labor":    sum(r["direct_labor"] for r in rows),
        "idle_labor":      sum(r["idle_labor"] for r in rows),
        "overhead":        sum(r["overhead"] for r in rows),
        "gross_profit":    sum(r["gross_profit"] for r in rows),
        "billable_hours":  sum(r["billable_hours"] for r in rows),
    }
    total_rev = total["revenue"]
    total["gross_margin_pct"] = round(total["gross_profit"] / total_rev * 100, 2) if total_rev > 0 else None

    return {"projects": rows, "total": total, "entity_id": entity_id, "year": year, "month": month}


@router.get("/reports/corporate-comparison", dependencies=_viewer,
            summary="Perbandingan Corporate P&L vs Project P&L (BAB II §3)")
def report_corporate_comparison(
    entity_id: str = Query(...),
    year:      int = Query(...),
    month:     int = Query(..., ge=1, le=12),
    db:        Session = Depends(get_db),
):
    engine = CostingEngine(db)
    return engine.get_corporate_comparison(entity_id, year, month)


@router.get("/reports/labor-utilization", dependencies=_viewer,
            summary="Laporan Utilisasi Jam Konsultan (billable vs bench)")
def report_labor_utilization(
    entity_id: str = Query(...),
    year:      int = Query(...),
    month:     Optional[int] = Query(None, ge=1, le=12),
    db:        Session = Depends(get_db),
):
    engine = CostingEngine(db)
    rows = engine.get_labor_utilization(entity_id, year, month)
    total_billable = sum(r["billable_hours"] for r in rows)
    total_bench    = sum(r["bench_hours"] for r in rows)
    total_all      = total_billable + total_bench
    return {
        "employees":       rows,
        "summary": {
            "total_billable_hours": total_billable,
            "total_bench_hours":    total_bench,
            "total_hours":          total_all,
            "avg_utilization_pct":  round(total_billable / total_all * 100, 2) if total_all > 0 else 0,
        }
    }


@router.get("/reports/overhead-detail", dependencies=_viewer,
            summary="Rincian distribusi overhead per proyek")
def report_overhead_detail(
    entity_id: str = Query(...),
    year:      int = Query(...),
    month:     int = Query(..., ge=1, le=12),
    db:        Session = Depends(get_db),
):
    rows = db.execute(
        text("""
            SELECT * FROM vw_overhead_allocation_detail
            WHERE entity_id = :eid AND year = :yr AND month = :mo
            ORDER BY rule_name, project_code
        """),
        {"eid": entity_id, "yr": year, "mo": month}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/reports/analytic-journals", dependencies=_viewer,
            summary="List analytic journals per periode")
def list_analytic_journals(
    entity_id: str = Query(...),
    year:      int = Query(...),
    month:     int = Query(..., ge=1, le=12),
    db:        Session = Depends(get_db),
):
    rows = db.execute(
        text("""
            SELECT aj.*,
                   COUNT(ajl.id) AS line_count
            FROM analytic_journal aj
            LEFT JOIN analytic_journal_line ajl ON ajl.journal_id = aj.id
            WHERE aj.entity_id = :eid AND aj.year = :yr AND aj.month = :mo
            GROUP BY aj.id
            ORDER BY aj.created_at
        """),
        {"eid": entity_id, "yr": year, "mo": month}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Labor Reclass & Payroll Variance ─────────────────────────────────────────

class LaborReclassPayload(BaseModel):
    entity_id:       str
    year:            int = Field(..., ge=2020, le=2099)
    month:           int = Field(..., ge=1, le=12)
    beban_gaji_code: str = Field(..., description="Account code untuk Beban Gaji (Dr + Cr offset)")


@router.post(
    "/labor-reclass",
    dependencies=[Depends(require_min_role("finance"))],
    summary="Distribusikan beban gaji aktual ke GL per-project/cost_center (Dr Beban Gaji per project, Cr Beban Gaji pool)",
)
def post_labor_reclass(
    payload: LaborReclassPayload,
    db:      Session = Depends(get_db),
    user=    Depends(require_min_role("finance")),
):
    try:
        return CostingEngine(db).post_labor_reclass(
            entity_id=payload.entity_id,
            year=payload.year,
            month=payload.month,
            beban_gaji_code=payload.beban_gaji_code,
            created_by=user.get("email", "system"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get(
    "/payroll-variance",
    dependencies=[Depends(require_min_role("viewer"))],
    summary="Variance analytic estimate (timesheet × unit_cost) vs aktual payroll per karyawan",
)
def get_payroll_variance(
    entity_id: str = Query(...),
    year:      int = Query(...),
    month:     int = Query(..., ge=1, le=12),
    db:        Session = Depends(get_db),
):
    return CostingEngine(db).get_payroll_variance(entity_id, year, month)
