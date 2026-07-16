# modules/attendance_router.py
# REST endpoints untuk modul Time & Attendance

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text

from modules.attendance_engine import AttendanceEngine, calculate_overtime_cost, hourly_base_from_salary

router = APIRouter(prefix="/attendance", tags=["Attendance"])


# ── Dependency placeholder (injected from main.py) ───────────────────────────
def _get_db() -> Session:  # pragma: no cover
    raise NotImplementedError("Inject via app.dependency_overrides")


def _get_current_user():  # pragma: no cover
    raise NotImplementedError("Inject via app.dependency_overrides")


# ── Schemas ──────────────────────────────────────────────────────────────────

class AttendanceLogIn(BaseModel):
    employee_id:   str
    entity_id:     str
    log_timestamp: datetime
    log_type:      str = Field(..., pattern="^(IN|OUT)$")
    device_id:     Optional[str] = None
    device_name:   Optional[str] = None
    location:      Optional[str] = None
    latitude:      Optional[float] = None
    longitude:     Optional[float] = None
    source:        str = Field("device", pattern="^(device|mobile|manual|api)$")
    raw_data:      Optional[dict] = None


class BatchLogImport(BaseModel):
    logs: list[AttendanceLogIn]


class ProcessDailyReq(BaseModel):
    employee_id: str
    target_date: date
    force:       bool = False


class ProcessPeriodReq(BaseModel):
    entity_id: str
    year:      int
    month:     int = Field(..., ge=1, le=12)


class FreezePeriodReq(BaseModel):
    entity_id: str
    year:      int
    month:     int = Field(..., ge=1, le=12)


class LeaveRequestCreate(BaseModel):
    employee_id:   str
    entity_id:     str
    leave_type_id: str
    start_date:    date
    end_date:      date
    reason:        Optional[str] = None


class LeaveApproval(BaseModel):
    action: str = Field(..., pattern="^(approve|reject)$")
    notes:  Optional[str] = None


class OvertimeRequestCreate(BaseModel):
    employee_id:     str
    entity_id:       str
    ot_date:         date
    day_type:        str = Field("workday", pattern="^(workday|restday|national_holiday)$")
    estimated_hours: float = Field(..., gt=0)
    reason:          Optional[str] = None


class OvertimeComplete(BaseModel):
    actual_hours: float = Field(..., gt=0)


class OvertimeApproval(BaseModel):
    action: str = Field(..., pattern="^(approve|reject)$")
    notes:  Optional[str] = None


class WorkScheduleCreate(BaseModel):
    entity_id:          str
    schedule_name:      str
    schedule_type:      str = Field("fixed", pattern="^(fixed|flexible|shift)$")
    work_days:          list[int] = Field(..., description="ISO weekday 1=Mon, 7=Sun")
    work_days_per_week: int = Field(5, ge=5, le=6)
    start_time:         str = Field(..., description="HH:MM e.g. 08:00")
    end_time:           str = Field(..., description="HH:MM e.g. 17:00")
    break_minutes:      int = 60
    late_tolerance_min: int = 15
    early_leave_min:    int = 15


class AssignScheduleReq(BaseModel):
    employee_id:    str
    schedule_id:    str
    effective_date: date
    end_date:       Optional[date] = None


# ── Raw Log Ingestion ─────────────────────────────────────────────────────────

@router.post("/logs", summary="Ingest raw attendance log (single)")
def ingest_log(
    payload: AttendanceLogIn,
    db:      Session = Depends(_get_db),
    user:    dict    = Depends(_get_current_user),
):
    log_id = str(uuid4())
    db.execute(
        text("""
            INSERT INTO attendance_log (
                id, employee_id, entity_id, log_timestamp, log_type,
                device_id, device_name, location, latitude, longitude,
                source, raw_data, imported_at
            ) VALUES (
                :id, :eid, :entid, :ts, :type,
                :dev_id, :dev_name, :loc, :lat, :lon,
                :src, :raw, NOW()
            )
        """),
        {
            "id": log_id, "eid": payload.employee_id, "entid": payload.entity_id,
            "ts": payload.log_timestamp, "type": payload.log_type,
            "dev_id": payload.device_id, "dev_name": payload.device_name,
            "loc": payload.location, "lat": payload.latitude, "lon": payload.longitude,
            "src": payload.source,
            "raw": str(payload.raw_data) if payload.raw_data else None,
        }
    )
    db.commit()
    return {"log_id": log_id, "status": "ingested"}


@router.post("/logs/batch", summary="Batch import attendance logs")
def ingest_logs_batch(
    payload: BatchLogImport,
    db:      Session = Depends(_get_db),
    user:    dict    = Depends(_get_current_user),
):
    inserted = 0
    errors   = []
    for log in payload.logs:
        try:
            log_id = str(uuid4())
            db.execute(
                text("""
                    INSERT INTO attendance_log (
                        id, employee_id, entity_id, log_timestamp, log_type,
                        device_id, device_name, location, latitude, longitude, source, imported_at
                    ) VALUES (
                        :id, :eid, :entid, :ts, :type,
                        :dev_id, :dev_name, :loc, :lat, :lon, :src, NOW()
                    )
                    ON CONFLICT DO NOTHING
                """),
                {
                    "id": log_id, "eid": log.employee_id, "entid": log.entity_id,
                    "ts": log.log_timestamp, "type": log.log_type,
                    "dev_id": log.device_id, "dev_name": log.device_name,
                    "loc": log.location, "lat": log.latitude, "lon": log.longitude,
                    "src": log.source,
                }
            )
            inserted += 1
        except Exception as e:
            errors.append({"log_timestamp": str(log.log_timestamp), "error": str(e)})
    db.commit()
    return {"inserted": inserted, "errors": errors}


# ── Processing ────────────────────────────────────────────────────────────────

@router.post("/process/daily", summary="Proses absensi harian satu karyawan")
def process_daily(
    payload: ProcessDailyReq,
    db:      Session = Depends(_get_db),
    user:    dict    = Depends(_get_current_user),
):
    if user.get("role") not in ("finance", "admin"):
        raise HTTPException(403, "Butuh role finance atau admin")
    engine = AttendanceEngine(db)
    result = engine.process_daily(payload.employee_id, payload.target_date, payload.force)
    return result


@router.post("/process/period", summary="Proses seluruh karyawan satu periode (bulan)")
def process_period(
    payload: ProcessPeriodReq,
    db:      Session = Depends(_get_db),
    user:    dict    = Depends(_get_current_user),
):
    if user.get("role") not in ("finance", "admin"):
        raise HTTPException(403, "Butuh role finance atau admin")
    engine = AttendanceEngine(db)
    result = engine.process_period(payload.entity_id, payload.year, payload.month)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.post("/process/freeze", summary="Freeze (kunci) periode absensi")
def freeze_period(
    payload: FreezePeriodReq,
    db:      Session = Depends(_get_db),
    user:    dict    = Depends(_get_current_user),
):
    if user.get("role") != "admin":
        raise HTTPException(403, "Hanya admin yang dapat freeze periode")
    engine = AttendanceEngine(db)
    result = engine.freeze_period(payload.entity_id, payload.year, payload.month, user.get("email", "system"))
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


# ── Timesheet ─────────────────────────────────────────────────────────────────

@router.get("/timesheet/{employee_id}", summary="Ambil timesheet karyawan satu bulan")
def get_timesheet(
    employee_id: str,
    year:        int = Query(...),
    month:       int = Query(..., ge=1, le=12),
    db:          Session = Depends(_get_db),
    user:        dict    = Depends(_get_current_user),
):
    row = db.execute(
        text("""
            SELECT at.*, e.full_name, e.employee_no, e.ptkp_status
            FROM attendance_timesheet at
            JOIN employee e ON e.id = at.employee_id
            WHERE at.employee_id = :eid AND at.year = :year AND at.month = :month
        """),
        {"eid": employee_id, "year": year, "month": month}
    ).fetchone()

    if not row:
        raise HTTPException(404, "Timesheet belum dibuat — proses periode terlebih dahulu")
    return dict(row._mapping)


@router.get("/timesheet/entity/{entity_id}", summary="Ringkasan timesheet semua karyawan satu periode")
def get_entity_timesheet(
    entity_id: str,
    year:      int = Query(...),
    month:     int = Query(..., ge=1, le=12),
    db:        Session = Depends(_get_db),
    user:      dict    = Depends(_get_current_user),
):
    rows = db.execute(
        text("""
            SELECT at.*, e.full_name, e.employee_no, e.department, e.ptkp_status
            FROM attendance_timesheet at
            JOIN employee e ON e.id = at.employee_id
            WHERE at.entity_id = :entid AND at.year = :year AND at.month = :month
            ORDER BY e.department, e.full_name
        """),
        {"entid": entity_id, "year": year, "month": month}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/timesheet/{employee_id}/payroll-variables", summary="Variabel payroll dari timesheet")
def get_payroll_variables(
    employee_id: str,
    entity_id:   str = Query(...),
    year:        int = Query(...),
    month:       int = Query(..., ge=1, le=12),
    db:          Session = Depends(_get_db),
    user:        dict    = Depends(_get_current_user),
):
    engine = AttendanceEngine(db)
    result = engine.get_payroll_variables(employee_id, entity_id, year, month)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


# ── Daily records ─────────────────────────────────────────────────────────────

@router.get("/daily/{employee_id}", summary="Riwayat absensi harian karyawan")
def get_daily(
    employee_id: str,
    year:        int  = Query(...),
    month:       int  = Query(..., ge=1, le=12),
    db:          Session = Depends(_get_db),
    user:        dict    = Depends(_get_current_user),
):
    rows = db.execute(
        text("""
            SELECT ad.*, lt.leave_code
            FROM attendance_daily ad
            LEFT JOIN leave_request lr ON lr.id = ad.leave_request_id
            LEFT JOIN leave_type lt    ON lt.id = lr.leave_type_id
            WHERE ad.employee_id = :eid
              AND EXTRACT(YEAR  FROM ad.attendance_date) = :year
              AND EXTRACT(MONTH FROM ad.attendance_date) = :month
            ORDER BY ad.attendance_date
        """),
        {"eid": employee_id, "year": year, "month": month}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Leave Request ─────────────────────────────────────────────────────────────

@router.post("/leave", summary="Buat permohonan cuti/izin")
def create_leave(
    payload: LeaveRequestCreate,
    db:      Session = Depends(_get_db),
    user:    dict    = Depends(_get_current_user),
):
    if payload.end_date < payload.start_date:
        raise HTTPException(400, "end_date tidak boleh sebelum start_date")

    total_days = (payload.end_date - payload.start_date).days + 1
    req_id = str(uuid4())
    db.execute(
        text("""
            INSERT INTO leave_request (
                id, employee_id, entity_id, leave_type_id,
                start_date, end_date, total_days, reason,
                status, submitted_by, submitted_at, created_at, updated_at
            ) VALUES (
                :id, :eid, :entid, :ltid,
                :start, :end, :days, :reason,
                'draft', :by, NOW(), NOW(), NOW()
            )
        """),
        {
            "id": req_id, "eid": payload.employee_id, "entid": payload.entity_id,
            "ltid": payload.leave_type_id, "start": payload.start_date, "end": payload.end_date,
            "days": total_days, "reason": payload.reason, "by": user.get("email", "system"),
        }
    )
    db.commit()
    return {"request_id": req_id, "total_days": total_days, "status": "draft"}


@router.post("/leave/{request_id}/submit", summary="Submit permohonan cuti")
def submit_leave(
    request_id: str,
    db:         Session = Depends(_get_db),
    user:       dict    = Depends(_get_current_user),
):
    row = db.execute(
        text("SELECT id, status FROM leave_request WHERE id = :id"),
        {"id": request_id}
    ).fetchone()
    if not row:
        raise HTTPException(404, "Leave request tidak ditemukan")
    if row.status != "draft":
        raise HTTPException(400, f"Status saat ini '{row.status}', hanya draft yang bisa disubmit")

    db.execute(
        text("""
            UPDATE leave_request SET status='submitted', submitted_at=NOW(), updated_at=NOW()
            WHERE id = :id
        """),
        {"id": request_id}
    )
    db.commit()
    return {"request_id": request_id, "status": "submitted"}


@router.post("/leave/{request_id}/approve", summary="Approve/reject permohonan cuti")
def approve_leave(
    request_id: str,
    payload:    LeaveApproval,
    db:         Session = Depends(_get_db),
    user:       dict    = Depends(_get_current_user),
):
    if user.get("role") not in ("finance", "admin"):
        raise HTTPException(403, "Butuh role finance atau admin untuk approve cuti")

    row = db.execute(
        text("SELECT id, status FROM leave_request WHERE id = :id"),
        {"id": request_id}
    ).fetchone()
    if not row:
        raise HTTPException(404, "Leave request tidak ditemukan")
    if row.status != "submitted":
        raise HTTPException(400, f"Status saat ini '{row.status}', hanya submitted yang bisa di-approve")

    new_status = "approved" if payload.action == "approve" else "rejected"
    db.execute(
        text("""
            UPDATE leave_request SET
                status = :status,
                approved_by = :by,
                approved_at = NOW(),
                rejection_reason = :notes,
                updated_at = NOW()
            WHERE id = :id
        """),
        {"id": request_id, "status": new_status, "by": user.get("email"), "notes": payload.notes}
    )
    db.commit()
    return {"request_id": request_id, "status": new_status}


@router.get("/leave", summary="List permohonan cuti")
def list_leaves(
    entity_id:   str  = Query(...),
    employee_id: Optional[str] = Query(None),
    status:      Optional[str] = Query(None),
    year:        Optional[int] = Query(None),
    db:          Session = Depends(_get_db),
    user:        dict    = Depends(_get_current_user),
):
    conditions = ["lr.entity_id = :entid"]
    params: dict = {"entid": entity_id}

    if employee_id:
        conditions.append("lr.employee_id = :eid")
        params["eid"] = employee_id
    if status:
        conditions.append("lr.status = :status")
        params["status"] = status
    if year:
        conditions.append("EXTRACT(YEAR FROM lr.start_date) = :year")
        params["year"] = year

    where = " AND ".join(conditions)
    rows = db.execute(
        text(f"""
            SELECT lr.*, e.full_name, lt.leave_name, lt.is_paid
            FROM leave_request lr
            JOIN employee  e  ON e.id  = lr.employee_id
            JOIN leave_type lt ON lt.id = lr.leave_type_id
            WHERE {where}
            ORDER BY lr.start_date DESC
        """),
        params
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Overtime Request ──────────────────────────────────────────────────────────

@router.post("/overtime", summary="Buat SPL (Surat Perintah Lembur)")
def create_overtime(
    payload: OvertimeRequestCreate,
    db:      Session = Depends(_get_db),
    user:    dict    = Depends(_get_current_user),
):
    existing = db.execute(
        text("""
            SELECT id FROM overtime_request
            WHERE employee_id = :eid AND ot_date = :dt
              AND status NOT IN ('rejected','cancelled')
        """),
        {"eid": payload.employee_id, "dt": payload.ot_date}
    ).fetchone()
    if existing:
        raise HTTPException(409, "SPL untuk karyawan ini pada tanggal tersebut sudah ada")

    req_id = str(uuid4())
    db.execute(
        text("""
            INSERT INTO overtime_request (
                id, employee_id, entity_id, ot_date, day_type,
                estimated_hours, reason, status, submitted_by, created_at, updated_at
            ) VALUES (
                :id, :eid, :entid, :dt, :dtype,
                :est, :reason, 'draft', :by, NOW(), NOW()
            )
        """),
        {
            "id": req_id, "eid": payload.employee_id, "entid": payload.entity_id,
            "dt": payload.ot_date, "dtype": payload.day_type,
            "est": payload.estimated_hours, "reason": payload.reason,
            "by": user.get("email", "system"),
        }
    )
    db.commit()
    return {"request_id": req_id, "status": "draft"}


@router.post("/overtime/{request_id}/submit", summary="Submit SPL untuk approval")
def submit_overtime(
    request_id: str,
    db:         Session = Depends(_get_db),
    user:       dict    = Depends(_get_current_user),
):
    row = db.execute(
        text("SELECT id, status FROM overtime_request WHERE id = :id"),
        {"id": request_id}
    ).fetchone()
    if not row:
        raise HTTPException(404, "OT request tidak ditemukan")
    if row.status != "draft":
        raise HTTPException(400, f"Status '{row.status}' tidak bisa disubmit")

    db.execute(
        text("""
            UPDATE overtime_request SET
                status='submitted', submitted_at=NOW(), updated_at=NOW()
            WHERE id = :id
        """),
        {"id": request_id}
    )
    db.commit()
    return {"request_id": request_id, "status": "submitted"}


@router.post("/overtime/{request_id}/approve", summary="Approve/reject SPL")
def approve_overtime(
    request_id: str,
    payload:    OvertimeApproval,
    db:         Session = Depends(_get_db),
    user:       dict    = Depends(_get_current_user),
):
    if user.get("role") not in ("finance", "admin"):
        raise HTTPException(403, "Butuh role finance atau admin")

    row = db.execute(
        text("SELECT id, status FROM overtime_request WHERE id = :id"),
        {"id": request_id}
    ).fetchone()
    if not row:
        raise HTTPException(404, "OT request tidak ditemukan")
    if row.status != "submitted":
        raise HTTPException(400, f"Status '{row.status}' tidak bisa di-approve")

    new_status = "approved" if payload.action == "approve" else "rejected"
    db.execute(
        text("""
            UPDATE overtime_request SET
                status = :status, approved_by = :by, approved_at = NOW(), updated_at = NOW()
            WHERE id = :id
        """),
        {"id": request_id, "status": new_status, "by": user.get("email")}
    )
    db.commit()
    return {"request_id": request_id, "status": new_status}


@router.post("/overtime/{request_id}/complete", summary="Isi actual hours lembur setelah selesai")
def complete_overtime(
    request_id: str,
    payload:    OvertimeComplete,
    db:         Session = Depends(_get_db),
    user:       dict    = Depends(_get_current_user),
):
    row = db.execute(
        text("SELECT id, status FROM overtime_request WHERE id = :id"),
        {"id": request_id}
    ).fetchone()
    if not row:
        raise HTTPException(404, "OT request tidak ditemukan")
    if row.status != "approved":
        raise HTTPException(400, "Hanya SPL approved yang bisa di-complete")

    db.execute(
        text("""
            UPDATE overtime_request SET
                status='completed', actual_hours=:hours, updated_at=NOW()
            WHERE id = :id
        """),
        {"id": request_id, "hours": payload.actual_hours}
    )
    db.commit()
    return {"request_id": request_id, "status": "completed", "actual_hours": payload.actual_hours}


@router.get("/overtime", summary="List SPL")
def list_overtime(
    entity_id:   str  = Query(...),
    employee_id: Optional[str] = Query(None),
    status:      Optional[str] = Query(None),
    year:        Optional[int] = Query(None),
    month:       Optional[int] = Query(None),
    db:          Session = Depends(_get_db),
    user:        dict    = Depends(_get_current_user),
):
    conditions = ["ot.entity_id = :entid"]
    params: dict = {"entid": entity_id}

    if employee_id:
        conditions.append("ot.employee_id = :eid")
        params["eid"] = employee_id
    if status:
        conditions.append("ot.status = :status")
        params["status"] = status
    if year:
        conditions.append("EXTRACT(YEAR FROM ot.ot_date) = :year")
        params["year"] = year
    if month:
        conditions.append("EXTRACT(MONTH FROM ot.ot_date) = :month")
        params["month"] = month

    where = " AND ".join(conditions)
    rows = db.execute(
        text(f"""
            SELECT ot.*, e.full_name, e.department
            FROM overtime_request ot
            JOIN employee e ON e.id = ot.employee_id
            WHERE {where}
            ORDER BY ot.ot_date DESC
        """),
        params
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Overtime Calculator (simulasi, tanpa auth) ────────────────────────────────

@router.get("/overtime/calculate", summary="Simulasi biaya lembur PP 35/2021")
def calculate_overtime(
    actual_hours:       float = Query(..., description="Jam lembur actual"),
    day_type:           str   = Query("workday", description="workday|restday|national_holiday"),
    work_days_per_week: int   = Query(5, ge=5, le=6),
    gaji_pokok:         float = Query(..., description="Gaji pokok bulanan"),
    tunjangan_tetap:    float = Query(0, description="Tunjangan tetap (optional)"),
):
    hourly = hourly_base_from_salary(Decimal(str(gaji_pokok)), Decimal(str(tunjangan_tetap)))
    result = calculate_overtime_cost(actual_hours, day_type, work_days_per_week, hourly)
    return result


# ── Work Schedule CRUD ────────────────────────────────────────────────────────

@router.post("/schedule", summary="Buat jadwal kerja baru")
def create_schedule(
    payload: WorkScheduleCreate,
    db:      Session = Depends(_get_db),
    user:    dict    = Depends(_get_current_user),
):
    if user.get("role") not in ("finance", "admin"):
        raise HTTPException(403, "Butuh role finance atau admin")

    sched_id = str(uuid4())
    db.execute(
        text("""
            INSERT INTO work_schedule (
                id, entity_id, schedule_name, schedule_type,
                work_days, work_days_per_week,
                start_time, end_time, break_minutes,
                late_tolerance_min, early_leave_min, created_at
            ) VALUES (
                :id, :entid, :name, :type,
                :wdays, :wpw,
                :start, :end, :break,
                :late, :early, NOW()
            )
        """),
        {
            "id": sched_id, "entid": payload.entity_id, "name": payload.schedule_name,
            "type": payload.schedule_type, "wdays": payload.work_days,
            "wpw": payload.work_days_per_week,
            "start": payload.start_time, "end": payload.end_time,
            "break": payload.break_minutes,
            "late": payload.late_tolerance_min, "early": payload.early_leave_min,
        }
    )
    db.commit()
    return {"schedule_id": sched_id, "schedule_name": payload.schedule_name}


@router.get("/schedule", summary="List jadwal kerja per entitas")
def list_schedules(
    entity_id: str = Query(...),
    db:        Session = Depends(_get_db),
    user:      dict    = Depends(_get_current_user),
):
    rows = db.execute(
        text("""
            SELECT * FROM work_schedule
            WHERE entity_id = :entid AND is_active = TRUE
            ORDER BY schedule_name
        """),
        {"entid": entity_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/schedule/assign", summary="Assign jadwal ke karyawan")
def assign_schedule(
    payload: AssignScheduleReq,
    db:      Session = Depends(_get_db),
    user:    dict    = Depends(_get_current_user),
):
    if user.get("role") not in ("finance", "admin"):
        raise HTTPException(403, "Butuh role finance atau admin")

    # Tutup assignment sebelumnya jika ada
    db.execute(
        text("""
            UPDATE employee_schedule SET end_date = :eff_date - INTERVAL '1 day'
            WHERE employee_id = :eid AND end_date IS NULL AND effective_date < :eff_date
        """),
        {"eid": payload.employee_id, "eff_date": payload.effective_date}
    )

    assign_id = str(uuid4())
    db.execute(
        text("""
            INSERT INTO employee_schedule (id, employee_id, schedule_id, effective_date, end_date, created_by)
            VALUES (:id, :eid, :sid, :eff, :end_date, :by)
            ON CONFLICT (employee_id, effective_date) DO UPDATE SET
                schedule_id = EXCLUDED.schedule_id,
                end_date    = EXCLUDED.end_date
        """),
        {
            "id": assign_id, "eid": payload.employee_id, "sid": payload.schedule_id,
            "eff": payload.effective_date, "end_date": payload.end_date,
            "by": user.get("email", "system"),
        }
    )
    db.commit()
    return {"assignment_id": assign_id, "effective_date": str(payload.effective_date)}


# ── Period management ─────────────────────────────────────────────────────────

@router.get("/period", summary="List attendance period")
def list_periods(
    entity_id: str = Query(...),
    year:      Optional[int] = Query(None),
    db:        Session = Depends(_get_db),
    user:      dict    = Depends(_get_current_user),
):
    params: dict = {"entid": entity_id}
    extra = ""
    if year:
        extra = " AND year = :year"
        params["year"] = year

    rows = db.execute(
        text(f"""
            SELECT * FROM attendance_period
            WHERE entity_id = :entid {extra}
            ORDER BY year DESC, month DESC
        """),
        params
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Leave Type master ─────────────────────────────────────────────────────────

@router.get("/leave-type", summary="List jenis cuti/izin")
def list_leave_types(
    entity_id: str = Query(...),
    db:        Session = Depends(_get_db),
    user:      dict    = Depends(_get_current_user),
):
    rows = db.execute(
        text("""
            SELECT * FROM leave_type
            WHERE entity_id = :entid AND is_active = TRUE
            ORDER BY leave_code
        """),
        {"entid": entity_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]
