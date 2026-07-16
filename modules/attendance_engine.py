# modules/attendance_engine.py
# Time & Attendance processing engine
#
# Tahapan:
#   1. process_daily()     — raw log → attendance_daily (clock-in/out, late, OT)
#   2. generate_timesheet() — aggregate daily records → attendance_timesheet
#   3. freeze_period()      — lock timesheet (tidak bisa diubah setelah freeze)
#
# Regulasi lembur (PP 35/2021 turunan UU Cipta Kerja):
#   Hari kerja : jam 1 × 1.5, jam 2+ × 2.0
#   Hari libur/minggu (5-day/week) : jam 1-8 × 2.0, jam 9 × 3.0, jam 10+ × 4.0
#   Hari libur/minggu (6-day/week) : jam 1-7 × 2.0, jam 8 × 3.0, jam 9+ × 4.0
#   Upah per jam = (gaji_pokok + tunjangan_tetap) / 173

from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime, timedelta, time
from typing import Optional
from uuid import uuid4

from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger


# ── Overtime multiplier tables (PP 35/2021) ────────────────────────────────────

# (jam_ke, multiplier) — jam_ke adalah ORDINAL (1-based)
OT_WORKDAY: list[tuple[int, float]] = [
    (1, 1.5),   # jam pertama
    (2, 2.0),   # jam ke-2 dst (batas atas tidak terbatas)
]

OT_RESTDAY_5DAY: list[tuple[int, float]] = [
    (8, 2.0),   # jam 1–8
    (9, 3.0),   # jam ke-9
    (10, 4.0),  # jam ke-10 dst
]

OT_RESTDAY_6DAY: list[tuple[int, float]] = [
    (7, 2.0),   # jam 1–7
    (8, 3.0),   # jam ke-8
    (9, 4.0),   # jam ke-9 dst
]

UPAH_DIVISOR = Decimal("173")    # PP 35/2021 standar


# ── OT cost calculation ───────────────────────────────────────────────────────

def calculate_overtime_cost(
    actual_hours:      float,
    day_type:          str,    # "workday" | "restday" | "national_holiday"
    work_days_per_week: int,   # 5 or 6
    hourly_base:       Decimal,
) -> dict:
    """
    Hitung biaya lembur total berdasarkan PP 35/2021.
    Mengembalikan breakdown per layer.
    """
    if actual_hours <= 0:
        return {"total_cost": Decimal("0"), "hourly_base": float(hourly_base), "breakdown": []}

    if day_type == "workday":
        table = OT_WORKDAY
    else:
        table = OT_RESTDAY_5DAY if work_days_per_week == 5 else OT_RESTDAY_6DAY

    total      = Decimal("0")
    breakdown  = []
    remaining  = Decimal(str(actual_hours))
    prev_layer = 0

    for i, (threshold, multiplier) in enumerate(table):
        is_last = (i == len(table) - 1)
        layer_hours = (
            remaining if is_last
            else min(remaining, Decimal(str(threshold - prev_layer)))
        )
        if layer_hours <= 0:
            break

        cost = (hourly_base * Decimal(str(multiplier)) * layer_hours).quantize(
            Decimal("1"), ROUND_HALF_UP
        )
        breakdown.append({
            "jam_ke":      f"{prev_layer + 1}–{int(threshold)}" if not is_last else f"{prev_layer + 1}+",
            "hours":       float(layer_hours),
            "multiplier":  multiplier,
            "cost":        float(cost),
        })
        total     += cost
        remaining -= layer_hours
        prev_layer = threshold
        if remaining <= 0:
            break

    return {
        "actual_hours": actual_hours,
        "hourly_base":  float(hourly_base),
        "total_cost":   total,
        "breakdown":    breakdown,
        "regulation":   "PP 35/2021 Pasal 31",
    }


def hourly_base_from_salary(gaji_pokok: Decimal, tunjangan_tetap: Decimal = Decimal("0")) -> Decimal:
    """Upah per jam = (gaji_pokok + tunjangan_tetap) / 173 (PP 35/2021)."""
    return ((gaji_pokok + tunjangan_tetap) / UPAH_DIVISOR).quantize(Decimal("1"), ROUND_HALF_UP)


# ── AttendanceEngine (DB-aware) ────────────────────────────────────────────────

class AttendanceEngine:

    def __init__(self, db: Session):
        self.db = db

    # ── Process daily attendance ───────────────────────────────────────────────

    def process_daily(
        self,
        employee_id: str,
        target_date: date,
        force:       bool = False,
    ) -> dict:
        """
        Proses raw logs untuk satu karyawan pada satu tanggal.
        1. Ambil semua IN/OUT log pada tanggal tersebut
        2. Cocokkan dengan jadwal kerja karyawan
        3. Tentukan status: hadir / terlambat / alpha / libur
        4. Catat lembur jika ada SPL approved
        5. Upsert ke attendance_daily
        """
        # Cek apakah sudah diproses dan period belum frozen
        if not force:
            existing = self.db.execute(
                text("""
                    SELECT id, daily_status FROM attendance_daily
                    WHERE employee_id = :eid AND attendance_date = :dt
                """),
                {"eid": employee_id, "dt": target_date}
            ).fetchone()
            if existing and not force:
                return {"skipped": True, "reason": "already_processed", "date": str(target_date)}

        # Ambil jadwal karyawan
        schedule = self._get_schedule(employee_id, target_date)
        if not schedule:
            logger.warning(f"No schedule for employee {employee_id} on {target_date}")
            return {"skipped": True, "reason": "no_schedule", "date": str(target_date)}

        # Cek apakah hari kerja
        weekday_iso = target_date.isoweekday()  # 1=Mon, 7=Sun
        is_workday  = weekday_iso in schedule["work_days"]

        if not is_workday:
            self._upsert_daily(employee_id, schedule["entity_id"], target_date, {
                "daily_status": "libur",
                "is_paid":      True,
                "work_hours":   0,
            })
            return {"date": str(target_date), "status": "libur"}

        # Ambil leave request yang disetujui pada tanggal ini
        leave = self._get_approved_leave(employee_id, target_date)
        if leave:
            leave_code = leave["leave_code"]
            status_map = {"SAKIT": "sakit", "DINAS_LUAR": "dinas_luar", "WFH": "wfh"}
            daily_status = status_map.get(leave_code, "cuti")
            self._upsert_daily(employee_id, schedule["entity_id"], target_date, {
                "daily_status":   daily_status,
                "is_paid":        leave["is_paid"],
                "leave_request_id": leave["id"],
                "work_hours":     0,
            })
            return {"date": str(target_date), "status": daily_status}

        # Ambil raw logs hari ini
        logs = self._get_logs(employee_id, target_date)

        if not logs:
            # Tidak ada log → alpha
            self._upsert_daily(employee_id, schedule["entity_id"], target_date, {
                "daily_status": "alpha",
                "is_paid":      False,
                "work_hours":   0,
            })
            return {"date": str(target_date), "status": "alpha"}

        # Hitung clock-in/out
        in_logs  = [l for l in logs if l["log_type"] == "IN"]
        out_logs = [l for l in logs if l["log_type"] == "OUT"]

        clock_in  = min(l["log_timestamp"] for l in in_logs)  if in_logs  else None
        clock_out = max(l["log_timestamp"] for l in out_logs) if out_logs else None

        # Hitung late minutes
        sched_start = datetime.combine(target_date, schedule["start_time"])
        late_min = 0
        if clock_in:
            delta = (clock_in - sched_start).total_seconds() / 60
            late_min = max(0, int(delta) - schedule["late_tolerance_min"])

        # Hitung early leave minutes
        sched_end      = datetime.combine(target_date, schedule["end_time"])
        early_leave_min = 0
        if clock_out:
            delta = (sched_end - clock_out).total_seconds() / 60
            early_leave_min = max(0, int(delta) - schedule["early_leave_min"])

        # Hitung jam kerja efektif (dikurangi break)
        work_hours = 0.0
        if clock_in and clock_out and clock_out > clock_in:
            gross_hours = (clock_out - clock_in).total_seconds() / 3600
            work_hours  = max(0, gross_hours - schedule["break_minutes"] / 60)

        # Status
        daily_status = "hadir_terlambat" if late_min > 0 else "hadir"

        # Cek lembur (harus ada SPL approved)
        ot = self._get_approved_ot(employee_id, target_date)
        ot_hours    = 0.0
        ot_request_id = None
        ot_day_type = "workday"

        if ot and clock_out:
            actual_ot = ot["actual_hours"] or ot["estimated_hours"]
            ot_hours  = float(actual_ot)
            ot_request_id = ot["id"]
            ot_day_type   = ot["day_type"]
            if ot_hours > 0:
                daily_status = "lembur"

        record_data = {
            "clock_in":           clock_in,
            "clock_out":          clock_out,
            "work_hours":         round(work_hours, 2),
            "late_minutes":       late_min,
            "early_leave_minutes": early_leave_min,
            "overtime_hours":     round(ot_hours, 2),
            "overtime_request_id": ot_request_id,
            "ot_day_type":        ot_day_type,
            "daily_status":       daily_status,
            "is_paid":            True,
        }

        rec_id = self._upsert_daily(employee_id, schedule["entity_id"], target_date, record_data)
        self._mark_logs_processed(employee_id, target_date)

        return {
            "date":          str(target_date),
            "status":        daily_status,
            "clock_in":      str(clock_in) if clock_in else None,
            "clock_out":     str(clock_out) if clock_out else None,
            "work_hours":    round(work_hours, 2),
            "late_minutes":  late_min,
            "overtime_hours": round(ot_hours, 2),
        }

    def process_period(self, entity_id: str, year: int, month: int) -> dict:
        """
        Proses semua karyawan entitas ini untuk satu periode (bulan).
        Iterasi setiap hari dalam bulan, proses setiap karyawan.
        """
        period = self._get_or_create_period(entity_id, year, month)
        if period["is_frozen"]:
            return {"error": "Period sudah di-freeze — tidak bisa diproses ulang"}

        start_date = date(year, month, 1)
        # Gunakan cutoff_date sebagai batas akhir (bukan akhir bulan penuh)
        end_date   = period["cutoff_date"]

        employees = self.db.execute(
            text("SELECT id FROM employee WHERE entity_id = :eid AND status = 'active'"),
            {"eid": entity_id}
        ).fetchall()

        processed = 0
        errors    = []
        current   = start_date
        while current <= end_date:
            for emp in employees:
                try:
                    self.process_daily(str(emp.id), current, force=True)
                    processed += 1
                except Exception as e:
                    errors.append({"employee_id": str(emp.id), "date": str(current), "error": str(e)})
            current += timedelta(days=1)

        # Generate timesheets setelah daily selesai
        ts_results = []
        for emp in employees:
            ts = self.generate_timesheet(str(emp.id), entity_id, year, month)
            ts_results.append(ts)

        self.db.execute(
            text("UPDATE attendance_period SET processed_at = NOW() WHERE id = :id"),
            {"id": period["id"]}
        )
        self.db.commit()

        logger.info(
            f"Period {month:02d}/{year} entity {entity_id}: "
            f"{processed} daily records, {len(ts_results)} timesheets"
        )
        return {
            "period_id": period["id"],
            "processed_daily": processed,
            "timesheets": len(ts_results),
            "errors": errors,
        }

    # ── Generate timesheet ────────────────────────────────────────────────────

    def generate_timesheet(
        self, employee_id: str, entity_id: str, year: int, month: int
    ) -> dict:
        """Aggregate attendance_daily → attendance_timesheet untuk satu bulan."""
        period = self._get_or_create_period(entity_id, year, month)

        # Hitung hari kerja seharusnya (berdasarkan jadwal)
        schedule = self.db.execute(
            text("""
                SELECT ws.work_days, ws.work_days_per_week
                FROM employee_schedule es
                JOIN work_schedule ws ON ws.id = es.schedule_id
                WHERE es.employee_id = :eid
                  AND es.effective_date <= :end_date
                  AND (es.end_date IS NULL OR es.end_date >= :start_date)
                ORDER BY es.effective_date DESC LIMIT 1
            """),
            {
                "eid": employee_id,
                "start_date": date(year, month, 1),
                "end_date": period["cutoff_date"],
            }
        ).fetchone()

        scheduled_days = 0
        if schedule:
            wd_list = schedule.work_days  # list of int
            current = date(year, month, 1)
            while current <= period["cutoff_date"]:
                if current.isoweekday() in wd_list:
                    scheduled_days += 1
                current += timedelta(days=1)

        # Aggregate dari attendance_daily
        agg = self.db.execute(
            text("""
                SELECT
                    COUNT(*) FILTER (WHERE daily_status IN ('hadir','hadir_terlambat','lembur')) AS present_days,
                    COUNT(*) FILTER (WHERE daily_status = 'alpha')    AS alpha_days,
                    COUNT(*) FILTER (WHERE daily_status = 'cuti')     AS leave_days,
                    COUNT(*) FILTER (WHERE daily_status IN ('sakit','dinas_luar')) AS sick_days,
                    COALESCE(SUM(work_hours),  0)                     AS total_work_hours,
                    COALESCE(SUM(late_minutes),0)                     AS total_late_min,
                    COALESCE(SUM(overtime_hours) FILTER (WHERE ot_day_type = 'workday'), 0) AS ot_workday,
                    COALESCE(SUM(overtime_hours) FILTER (WHERE ot_day_type IN ('restday','national_holiday')), 0) AS ot_restday
                FROM attendance_daily
                WHERE employee_id = :eid
                  AND attendance_date BETWEEN :start_date AND :end_date
            """),
            {
                "eid": employee_id,
                "start_date": date(year, month, 1),
                "end_date":   period["cutoff_date"],
            }
        ).fetchone()

        self.db.execute(
            text("""
                INSERT INTO attendance_timesheet (
                    id, employee_id, entity_id, period_id, year, month,
                    scheduled_days, present_days, alpha_days, leave_days, sick_days,
                    total_work_hours, total_late_minutes,
                    overtime_workday_h, overtime_restday_h,
                    created_at, updated_at
                ) VALUES (
                    :id, :eid, :entid, :pid, :year, :month,
                    :sched, :present, :alpha, :leave, :sick,
                    :work_h, :late_min,
                    :ot_wd, :ot_rd,
                    NOW(), NOW()
                )
                ON CONFLICT (employee_id, year, month) DO UPDATE SET
                    scheduled_days     = EXCLUDED.scheduled_days,
                    present_days       = EXCLUDED.present_days,
                    alpha_days         = EXCLUDED.alpha_days,
                    leave_days         = EXCLUDED.leave_days,
                    sick_days          = EXCLUDED.sick_days,
                    total_work_hours   = EXCLUDED.total_work_hours,
                    total_late_minutes = EXCLUDED.total_late_minutes,
                    overtime_workday_h = EXCLUDED.overtime_workday_h,
                    overtime_restday_h = EXCLUDED.overtime_restday_h,
                    updated_at         = NOW()
            """),
            {
                "id": str(uuid4()), "eid": employee_id, "entid": entity_id,
                "pid": period["id"], "year": year, "month": month,
                "sched":    scheduled_days,
                "present":  int(agg.present_days  or 0),
                "alpha":    int(agg.alpha_days     or 0),
                "leave":    int(agg.leave_days     or 0),
                "sick":     int(agg.sick_days      or 0),
                "work_h":   float(agg.total_work_hours or 0),
                "late_min": int(agg.total_late_min or 0),
                "ot_wd":    float(agg.ot_workday   or 0),
                "ot_rd":    float(agg.ot_restday   or 0),
            }
        )
        self.db.commit()
        return {
            "employee_id":         employee_id,
            "year":                year,
            "month":               month,
            "scheduled_days":      scheduled_days,
            "present_days":        int(agg.present_days  or 0),
            "alpha_days":          int(agg.alpha_days     or 0),
            "overtime_workday_h":  float(agg.ot_workday   or 0),
            "overtime_restday_h":  float(agg.ot_restday   or 0),
        }

    # ── Freeze period ─────────────────────────────────────────────────────────

    def freeze_period(self, entity_id: str, year: int, month: int, frozen_by: str) -> dict:
        """
        Kunci data absensi periode ini.
        Setelah freeze: daily records & timesheets tidak bisa diubah.
        """
        period = self._get_or_create_period(entity_id, year, month)
        if period["is_frozen"]:
            return {"error": "Period sudah di-freeze sebelumnya", "frozen_at": str(period.get("frozen_at"))}

        self.db.execute(
            text("""
                UPDATE attendance_period SET
                    is_frozen = TRUE, frozen_by = :by, frozen_at = NOW()
                WHERE id = :id
            """),
            {"id": period["id"], "by": frozen_by}
        )
        # Freeze semua timesheet di periode ini
        self.db.execute(
            text("""
                UPDATE attendance_timesheet SET
                    is_frozen = TRUE, frozen_at = NOW()
                WHERE period_id = :pid
            """),
            {"pid": period["id"]}
        )
        self.db.commit()
        logger.info(f"Period {month:02d}/{year} entity {entity_id} frozen by {frozen_by}")
        return {
            "period_id": period["id"],
            "year": year, "month": month,
            "is_frozen": True,
            "frozen_by": frozen_by,
        }

    # ── Variable payroll component from timesheet ─────────────────────────────

    def get_payroll_variables(
        self, employee_id: str, entity_id: str, year: int, month: int
    ) -> dict:
        """
        Ambil nilai variabel dari timesheet yang siap dikonsumsi payroll engine:
        - tunjangan_makan & transport (× hari_hadir)
        - biaya_lembur (× tarif_per_jam × multiplier PP 35/2021)
        - potongan_alpha (× gaji_pokok / hari_kerja)
        """
        ts = self.db.execute(
            text("""
                SELECT at.*, pc.gaji_pokok, pc.tunjangan_transport, pc.tunjangan_makan,
                       pc.premi_bpjs_kesehatan_perusahaan, pc.premi_bpjs_jkk, pc.premi_bpjs_jkm,
                       pc.iuran_jht_karyawan_pct, pc.iuran_pensiun_karyawan,
                       ws.work_days_per_week
                FROM attendance_timesheet at
                JOIN employee_payroll_component pc ON pc.employee_id = at.employee_id AND pc.is_active = TRUE
                LEFT JOIN employee_schedule es     ON es.employee_id = at.employee_id
                                                   AND es.effective_date <= :month_end
                                                   AND (es.end_date IS NULL OR es.end_date >= :month_start)
                LEFT JOIN work_schedule ws         ON ws.id = es.schedule_id
                WHERE at.employee_id = :eid AND at.year = :year AND at.month = :month
                ORDER BY es.effective_date DESC LIMIT 1
            """),
            {
                "eid": employee_id, "year": year, "month": month,
                "month_start": date(year, month, 1),
                "month_end":   date(year, month, 28),  # safe approximation
            }
        ).fetchone()

        if not ts:
            return {"error": "Timesheet belum dibuat atau komponen gaji belum diset"}

        gaji_pokok     = Decimal(str(ts.gaji_pokok or 0))
        tunjangan_trans = Decimal(str(ts.tunjangan_transport or 0))
        tunjangan_makan = Decimal(str(ts.tunjangan_makan or 0))

        present_days   = ts.present_days or 0
        alpha_days     = ts.alpha_days   or 0
        scheduled_days = ts.scheduled_days or 1  # hindari div zero

        # Tunjangan variabel (hanya hari hadir actual)
        trans_var  = tunjangan_trans / scheduled_days * present_days if tunjangan_trans > 0 else Decimal("0")
        makan_var  = tunjangan_makan / scheduled_days * present_days if tunjangan_makan > 0 else Decimal("0")

        # Biaya lembur
        hourly = hourly_base_from_salary(gaji_pokok)
        ot_wd_cost = Decimal("0")
        ot_rd_cost = Decimal("0")
        ot_days_pw = ts.work_days_per_week or 5

        if (ts.overtime_workday_h or 0) > 0:
            result = calculate_overtime_cost(ts.overtime_workday_h, "workday", ot_days_pw, hourly)
            ot_wd_cost = Decimal(str(result["total_cost"]))

        if (ts.overtime_restday_h or 0) > 0:
            result = calculate_overtime_cost(ts.overtime_restday_h, "restday", ot_days_pw, hourly)
            ot_rd_cost = Decimal(str(result["total_cost"]))

        # Potongan alpha (proporsional dari gaji pokok)
        potongan_alpha = Decimal("0")
        if alpha_days > 0 and scheduled_days > 0:
            potongan_alpha = (gaji_pokok / scheduled_days * alpha_days).quantize(
                Decimal("1"), ROUND_HALF_UP
            )

        return {
            "employee_id":      employee_id,
            "present_days":     present_days,
            "alpha_days":       alpha_days,
            "scheduled_days":   scheduled_days,
            "tunjangan_transport_var": float(trans_var),
            "tunjangan_makan_var":     float(makan_var),
            "overtime_workday_cost":   float(ot_wd_cost),
            "overtime_restday_cost":   float(ot_rd_cost),
            "total_overtime_cost":     float(ot_wd_cost + ot_rd_cost),
            "potongan_alpha":          float(potongan_alpha),
            "overtime_workday_h":      float(ts.overtime_workday_h or 0),
            "overtime_restday_h":      float(ts.overtime_restday_h or 0),
            "hourly_base":             float(hourly),
        }

    # ── DB helpers ─────────────────────────────────────────────────────────────

    def _get_schedule(self, employee_id: str, for_date: date) -> Optional[dict]:
        row = self.db.execute(
            text("""
                SELECT ws.*, e.entity_id
                FROM employee_schedule es
                JOIN work_schedule ws ON ws.id = es.schedule_id
                JOIN employee e       ON e.id  = es.employee_id
                WHERE es.employee_id  = :eid
                  AND es.effective_date <= :dt
                  AND (es.end_date IS NULL OR es.end_date >= :dt)
                ORDER BY es.effective_date DESC LIMIT 1
            """),
            {"eid": employee_id, "dt": for_date}
        ).fetchone()
        return dict(row._mapping) if row else None

    def _get_logs(self, employee_id: str, target_date: date) -> list:
        rows = self.db.execute(
            text("""
                SELECT id, log_timestamp, log_type
                FROM attendance_log
                WHERE employee_id = :eid
                  AND log_timestamp::date = :dt
                ORDER BY log_timestamp
            """),
            {"eid": employee_id, "dt": target_date}
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    def _get_approved_leave(self, employee_id: str, target_date: date) -> Optional[dict]:
        row = self.db.execute(
            text("""
                SELECT lr.id, lt.leave_code, lt.is_paid
                FROM leave_request lr
                JOIN leave_type lt ON lt.id = lr.leave_type_id
                WHERE lr.employee_id = :eid
                  AND lr.status      = 'approved'
                  AND :dt BETWEEN lr.start_date AND lr.end_date
                LIMIT 1
            """),
            {"eid": employee_id, "dt": target_date}
        ).fetchone()
        return dict(row._mapping) if row else None

    def _get_approved_ot(self, employee_id: str, target_date: date) -> Optional[dict]:
        row = self.db.execute(
            text("""
                SELECT id, day_type, estimated_hours, actual_hours
                FROM overtime_request
                WHERE employee_id = :eid
                  AND ot_date     = :dt
                  AND status      IN ('approved','completed')
                LIMIT 1
            """),
            {"eid": employee_id, "dt": target_date}
        ).fetchone()
        return dict(row._mapping) if row else None

    def _get_or_create_period(self, entity_id: str, year: int, month: int) -> dict:
        row = self.db.execute(
            text("""
                SELECT * FROM attendance_period
                WHERE entity_id = :eid AND year = :year AND month = :month
            """),
            {"eid": entity_id, "year": year, "month": month}
        ).fetchone()
        if row:
            return dict(row._mapping)

        # Default cutoff = tanggal 20 bulan tersebut (bisa dikonfigurasi)
        from calendar import monthrange
        last_day    = monthrange(year, month)[1]
        cutoff_date = date(year, month, min(20, last_day))
        period_id   = uuid4()

        self.db.execute(
            text("""
                INSERT INTO attendance_period (id, entity_id, year, month, cutoff_date)
                VALUES (:id, :eid, :year, :month, :cutoff)
            """),
            {"id": str(period_id), "eid": entity_id, "year": year, "month": month,
             "cutoff": cutoff_date}
        )
        self.db.commit()
        return {"id": str(period_id), "is_frozen": False, "cutoff_date": cutoff_date,
                "year": year, "month": month}

    def _upsert_daily(self, employee_id: str, entity_id: str, att_date: date, data: dict) -> str:
        rec_id = str(uuid4())
        self.db.execute(
            text("""
                INSERT INTO attendance_daily (
                    id, employee_id, entity_id, attendance_date,
                    clock_in, clock_out, work_hours, late_minutes, early_leave_minutes,
                    overtime_hours, overtime_request_id, ot_day_type,
                    daily_status, leave_request_id, is_paid, processed_at,
                    created_at, updated_at
                ) VALUES (
                    :id, :eid, :entid, :dt,
                    :ci, :co, :wh, :late, :early,
                    :ot_h, :ot_req, :ot_type,
                    :status, :leave_id, :is_paid, NOW(),
                    NOW(), NOW()
                )
                ON CONFLICT (employee_id, attendance_date) DO UPDATE SET
                    clock_in              = EXCLUDED.clock_in,
                    clock_out             = EXCLUDED.clock_out,
                    work_hours            = EXCLUDED.work_hours,
                    late_minutes          = EXCLUDED.late_minutes,
                    early_leave_minutes   = EXCLUDED.early_leave_minutes,
                    overtime_hours        = EXCLUDED.overtime_hours,
                    overtime_request_id   = EXCLUDED.overtime_request_id,
                    ot_day_type           = EXCLUDED.ot_day_type,
                    daily_status          = EXCLUDED.daily_status,
                    leave_request_id      = EXCLUDED.leave_request_id,
                    is_paid               = EXCLUDED.is_paid,
                    processed_at          = NOW(),
                    updated_at            = NOW()
            """),
            {
                "id": rec_id, "eid": employee_id, "entid": entity_id, "dt": att_date,
                "ci":      data.get("clock_in"),
                "co":      data.get("clock_out"),
                "wh":      data.get("work_hours", 0),
                "late":    data.get("late_minutes", 0),
                "early":   data.get("early_leave_minutes", 0),
                "ot_h":    data.get("overtime_hours", 0),
                "ot_req":  str(data["overtime_request_id"]) if data.get("overtime_request_id") else None,
                "ot_type": data.get("ot_day_type", "workday"),
                "status":  data.get("daily_status", "hadir"),
                "leave_id": str(data["leave_request_id"]) if data.get("leave_request_id") else None,
                "is_paid": data.get("is_paid", True),
            }
        )
        return rec_id

    def _mark_logs_processed(self, employee_id: str, target_date: date):
        self.db.execute(
            text("""
                UPDATE attendance_log SET is_processed = TRUE
                WHERE employee_id = :eid AND log_timestamp::date = :dt
            """),
            {"eid": employee_id, "dt": target_date}
        )
