"""
Leave Management Engine

Alur:
  1. initialize_entitlements()  → buat jatah cuti awal tahun (bulk per entity)
  2. carry_forward_balances()   → rollover sisa cuti ke tahun berikutnya
  3. create_request()           → ajukan cuti (validasi balance + overlap + notice days)
  4. submit_request()           → Draft → Submitted
  5. approve_request()          → Submitted → Approved (kurangi balance)
  6. reject_request()           → Submitted → Rejected
  7. cancel_request()           → kembalikan balance jika sebelumnya Approved
  8. adjust_balance()           → koreksi manual balance
  9. get_leave_calendar()       → lihat jadwal cuti per periode
 10. get_unpaid_deductions()    → list LWOP untuk integrasi payroll
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


class LeaveEngine:

    # ─────────────────────────────────────────────────────────────────────────
    # 1. INITIALIZE ENTITLEMENTS
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def initialize_entitlements(
        db: Session,
        entity_id: UUID,
        fiscal_year: int,
        created_by: str = None,
    ) -> dict:
        """
        Buat entitlement untuk semua karyawan aktif × semua leave type aktif.
        Skip jika sudah ada. Hitung carry_forward dari tahun sebelumnya.
        """
        employees = db.execute(
            text("""
                SELECT id FROM employee
                WHERE entity_id = :eid AND employment_status = 'active'
            """),
            {"eid": str(entity_id)},
        ).fetchall()

        leave_types = db.execute(
            text("""
                SELECT id, default_days_per_year, max_carry_forward
                FROM leave_type
                WHERE entity_id = :eid AND is_active = TRUE
            """),
            {"eid": str(entity_id)},
        ).fetchall()

        created = 0
        skipped = 0

        for emp in employees:
            for lt in leave_types:
                existing = db.execute(
                    text("""
                        SELECT id FROM leave_entitlement
                        WHERE employee_id=:emp AND leave_type_id=:lt AND fiscal_year=:yr
                    """),
                    {"emp": str(emp.id), "lt": str(lt.id), "yr": fiscal_year},
                ).fetchone()
                if existing:
                    skipped += 1
                    continue

                # Carry forward dari tahun sebelumnya
                carry = Decimal("0")
                if lt.max_carry_forward > 0:
                    prev = db.execute(
                        text("""
                            SELECT balance FROM leave_entitlement
                            WHERE employee_id=:emp AND leave_type_id=:lt AND fiscal_year=:yr
                        """),
                        {"emp": str(emp.id), "lt": str(lt.id), "yr": fiscal_year - 1},
                    ).fetchone()
                    if prev and prev.balance > 0:
                        carry = min(
                            Decimal(str(prev.balance)),
                            Decimal(str(lt.max_carry_forward)),
                        )

                db.execute(
                    text("""
                        INSERT INTO leave_entitlement (
                            entity_id, employee_id, leave_type_id,
                            fiscal_year, entitled_days, carry_forward
                        ) VALUES (:eid, :emp, :lt, :yr, :days, :carry)
                    """),
                    {
                        "eid":   str(entity_id),
                        "emp":   str(emp.id),
                        "lt":    str(lt.id),
                        "yr":    fiscal_year,
                        "days":  float(lt.default_days_per_year),
                        "carry": float(carry),
                    },
                )
                created += 1

        db.commit()
        return {"fiscal_year": fiscal_year, "created": created, "skipped": skipped}

    # ─────────────────────────────────────────────────────────────────────────
    # 2. CARRY FORWARD
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def carry_forward_balances(
        db: Session,
        entity_id: UUID,
        from_year: int,
    ) -> dict:
        """Update carry_forward pada entitlement tahun berikutnya berdasarkan saldo from_year."""
        to_year = from_year + 1
        rows = db.execute(
            text("""
                SELECT le.employee_id, le.leave_type_id, le.balance,
                       lt.max_carry_forward
                FROM leave_entitlement le
                JOIN leave_type lt ON lt.id = le.leave_type_id
                WHERE le.entity_id = :eid AND le.fiscal_year = :yr
                  AND le.balance > 0 AND lt.max_carry_forward > 0
            """),
            {"eid": str(entity_id), "yr": from_year},
        ).fetchall()

        updated = 0
        for r in rows:
            carry = min(Decimal(str(r.balance)), Decimal(str(r.max_carry_forward)))
            result = db.execute(
                text("""
                    UPDATE leave_entitlement
                    SET carry_forward = :carry
                    WHERE employee_id=:emp AND leave_type_id=:lt AND fiscal_year=:yr
                      AND entity_id=:eid
                """),
                {
                    "carry": float(carry),
                    "emp":   str(r.employee_id),
                    "lt":    str(r.leave_type_id),
                    "yr":    to_year,
                    "eid":   str(entity_id),
                },
            )
            if result.rowcount:
                updated += 1

        db.commit()
        return {"from_year": from_year, "to_year": to_year, "updated": updated}

    # ─────────────────────────────────────────────────────────────────────────
    # 3. CREATE REQUEST (Draft)
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def create_request(
        db: Session,
        entity_id: UUID,
        employee_id: UUID,
        leave_type_id: UUID,
        date_from: date,
        date_to: date,
        reason: str = None,
        document_url: str = None,
    ) -> dict:
        """
        Buat pengajuan cuti baru.
        Validasi:
          - date_from <= date_to
          - Hitung working days (kecuali weekend + public holiday)
          - Cek notice_days_required
          - Cek overlap dengan cuti lain yang aktif
          - Cek balance cukup (untuk paid leave)
        """
        if date_from > date_to:
            raise ValueError("date_from tidak boleh setelah date_to.")

        lt = db.execute(
            text("""
                SELECT lt.*, e.id AS eid
                FROM leave_type lt, entity e
                WHERE lt.id = :id AND lt.entity_id = e.id
            """),
            {"id": str(leave_type_id)},
        ).fetchone()
        if not lt:
            raise ValueError("Leave type tidak ditemukan.")

        # Hitung working days
        total_days = LeaveEngine._count_working_days(db, str(entity_id), date_from, date_to)
        if total_days <= 0:
            raise ValueError("Tidak ada hari kerja dalam rentang tanggal tersebut.")

        # Cek notice days
        today = date.today()
        if (date_from - today).days < lt.notice_days_required:
            raise ValueError(
                f"Pengajuan cuti minimal H-{lt.notice_days_required} sebelum tanggal mulai."
            )

        # Cek overlap
        overlap = db.execute(
            text("""
                SELECT COUNT(*) FROM leave_request
                WHERE employee_id = :emp
                  AND status IN ('submitted','approved')
                  AND date_from <= :d_to AND date_to >= :d_from
            """),
            {"emp": str(employee_id), "d_from": date_from, "d_to": date_to},
        ).fetchone()[0]
        if overlap > 0:
            raise ValueError("Terdapat cuti yang sudah diajukan/disetujui pada periode yang sama.")

        # Cek balance (hanya untuk paid leave yang punya entitlement)
        if lt.is_paid and lt.default_days_per_year > 0:
            fiscal_year = date_from.year
            ent = db.execute(
                text("""
                    SELECT balance, pending_days FROM leave_entitlement
                    WHERE employee_id=:emp AND leave_type_id=:lt AND fiscal_year=:yr
                """),
                {"emp": str(employee_id), "lt": str(leave_type_id), "yr": fiscal_year},
            ).fetchone()
            if not ent:
                raise ValueError(
                    f"Entitlement cuti {lt.type_code} tahun {fiscal_year} belum diinisialisasi."
                )
            available = Decimal(str(ent.balance)) - Decimal(str(ent.pending_days))
            if available < Decimal(str(total_days)):
                raise ValueError(
                    f"Saldo cuti tidak cukup. Tersedia: {float(available):.1f} hari, "
                    f"dibutuhkan: {total_days:.1f} hari."
                )

        # Generate nomor
        seq_row = db.execute(
            text("""
                SELECT COUNT(*) + 1 AS seq FROM leave_request
                WHERE entity_id = :eid
                  AND EXTRACT(YEAR FROM date_from) = :yr
            """),
            {"eid": str(entity_id), "yr": date_from.year},
        ).fetchone()
        request_no = f"LVR-{date_from.year}-{seq_row.seq:04d}"

        is_unpaid = not lt.is_paid

        row = db.execute(
            text("""
                INSERT INTO leave_request (
                    entity_id, request_no, employee_id, leave_type_id,
                    date_from, date_to, total_days, reason, document_url,
                    status, is_unpaid_deduction
                ) VALUES (
                    :eid, :no, :emp, :lt,
                    :d_from, :d_to, :days, :reason, :doc,
                    'draft', :unpaid
                ) RETURNING id
            """),
            {
                "eid":    str(entity_id),
                "no":     request_no,
                "emp":    str(employee_id),
                "lt":     str(leave_type_id),
                "d_from": date_from,
                "d_to":   date_to,
                "days":   float(total_days),
                "reason": reason,
                "doc":    document_url,
                "unpaid": is_unpaid,
            },
        ).fetchone()

        # Update pending_days di entitlement jika ada
        if lt.is_paid and lt.default_days_per_year > 0:
            db.execute(
                text("""
                    UPDATE leave_entitlement
                    SET pending_days = pending_days + :days
                    WHERE employee_id=:emp AND leave_type_id=:lt AND fiscal_year=:yr
                """),
                {
                    "days": float(total_days),
                    "emp":  str(employee_id),
                    "lt":   str(leave_type_id),
                    "yr":   date_from.year,
                },
            )

        db.commit()
        return {
            "request_id":  str(row.id),
            "request_no":  request_no,
            "total_days":  float(total_days),
            "is_unpaid":   is_unpaid,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 4. SUBMIT
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def submit_request(db: Session, request_id: UUID, submitted_by: str) -> dict:
        req = LeaveEngine._get_request(db, request_id)
        if req.status != "draft":
            raise ValueError(f"Pengajuan berstatus {req.status}, tidak bisa disubmit.")

        db.execute(
            text("""
                UPDATE leave_request
                SET status='submitted', submitted_by=:by, submitted_at=NOW()
                WHERE id=:id
            """),
            {"by": submitted_by, "id": str(request_id)},
        )
        db.commit()
        return {"status": "submitted", "request_no": req.request_no}

    # ─────────────────────────────────────────────────────────────────────────
    # 5. APPROVE
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def approve_request(
        db: Session,
        request_id: UUID,
        approved_by: str,
        notes: str = None,
    ) -> dict:
        req = LeaveEngine._get_request(db, request_id)
        if req.status != "submitted":
            raise ValueError(f"Pengajuan berstatus {req.status}, hanya 'submitted' yang bisa diapprove.")

        lt = db.execute(
            text("SELECT is_paid, default_days_per_year FROM leave_type WHERE id=:id"),
            {"id": str(req.leave_type_id)},
        ).fetchone()

        db.execute(
            text("""
                UPDATE leave_request
                SET status='approved', approved_by=:by, approved_at=NOW(), approval_notes=:notes
                WHERE id=:id
            """),
            {"by": approved_by, "notes": notes, "id": str(request_id)},
        )

        # Kurangi balance, hapus pending
        if lt.is_paid and lt.default_days_per_year > 0:
            db.execute(
                text("""
                    UPDATE leave_entitlement
                    SET used_days    = used_days    + :days,
                        pending_days = GREATEST(pending_days - :days, 0)
                    WHERE employee_id=:emp AND leave_type_id=:lt AND fiscal_year=:yr
                """),
                {
                    "days": float(req.total_days),
                    "emp":  str(req.employee_id),
                    "lt":   str(req.leave_type_id),
                    "yr":   req.date_from.year,
                },
            )

        db.commit()
        return {"status": "approved", "total_days_deducted": float(req.total_days)}

    # ─────────────────────────────────────────────────────────────────────────
    # 6. REJECT
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def reject_request(
        db: Session,
        request_id: UUID,
        rejected_by: str,
        reason: str,
    ) -> dict:
        req = LeaveEngine._get_request(db, request_id)
        if req.status not in ("submitted", "approved"):
            raise ValueError(f"Pengajuan berstatus {req.status}, tidak bisa direject.")

        lt = db.execute(
            text("SELECT is_paid, default_days_per_year FROM leave_type WHERE id=:id"),
            {"id": str(req.leave_type_id)},
        ).fetchone()

        db.execute(
            text("""
                UPDATE leave_request
                SET status='rejected', approved_by=:by, approved_at=NOW(), approval_notes=:reason
                WHERE id=:id
            """),
            {"by": rejected_by, "reason": reason, "id": str(request_id)},
        )

        # Kembalikan pending (jika masih submitted) atau used_days (jika approved)
        if lt.is_paid and lt.default_days_per_year > 0:
            if req.status == "submitted":
                db.execute(
                    text("""
                        UPDATE leave_entitlement
                        SET pending_days = GREATEST(pending_days - :days, 0)
                        WHERE employee_id=:emp AND leave_type_id=:lt AND fiscal_year=:yr
                    """),
                    {"days": float(req.total_days), "emp": str(req.employee_id),
                     "lt": str(req.leave_type_id), "yr": req.date_from.year},
                )
            else:  # was approved
                db.execute(
                    text("""
                        UPDATE leave_entitlement
                        SET used_days = GREATEST(used_days - :days, 0)
                        WHERE employee_id=:emp AND leave_type_id=:lt AND fiscal_year=:yr
                    """),
                    {"days": float(req.total_days), "emp": str(req.employee_id),
                     "lt": str(req.leave_type_id), "yr": req.date_from.year},
                )

        db.commit()
        return {"status": "rejected"}

    # ─────────────────────────────────────────────────────────────────────────
    # 7. CANCEL
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def cancel_request(
        db: Session,
        request_id: UUID,
        cancelled_by: str,
    ) -> dict:
        req = LeaveEngine._get_request(db, request_id)
        if req.status not in ("draft", "submitted", "approved"):
            raise ValueError(f"Pengajuan berstatus {req.status}, tidak bisa dibatalkan.")

        lt = db.execute(
            text("SELECT is_paid, default_days_per_year FROM leave_type WHERE id=:id"),
            {"id": str(req.leave_type_id)},
        ).fetchone()

        if req.status == "approved" and date.today() >= req.date_from:
            raise ValueError("Cuti yang sudah berjalan tidak bisa dibatalkan.")

        db.execute(
            text("""
                UPDATE leave_request
                SET status='cancelled', approved_by=:by, approved_at=NOW()
                WHERE id=:id
            """),
            {"by": cancelled_by, "id": str(request_id)},
        )

        if lt.is_paid and lt.default_days_per_year > 0:
            if req.status == "submitted":
                db.execute(
                    text("""
                        UPDATE leave_entitlement
                        SET pending_days = GREATEST(pending_days - :days, 0)
                        WHERE employee_id=:emp AND leave_type_id=:lt AND fiscal_year=:yr
                    """),
                    {"days": float(req.total_days), "emp": str(req.employee_id),
                     "lt": str(req.leave_type_id), "yr": req.date_from.year},
                )
            elif req.status == "approved":
                db.execute(
                    text("""
                        UPDATE leave_entitlement
                        SET used_days = GREATEST(used_days - :days, 0)
                        WHERE employee_id=:emp AND leave_type_id=:lt AND fiscal_year=:yr
                    """),
                    {"days": float(req.total_days), "emp": str(req.employee_id),
                     "lt": str(req.leave_type_id), "yr": req.date_from.year},
                )

        db.commit()
        return {"status": "cancelled"}

    # ─────────────────────────────────────────────────────────────────────────
    # 8. ADJUST BALANCE (koreksi manual)
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def adjust_balance(
        db: Session,
        entity_id: UUID,
        employee_id: UUID,
        leave_type_id: UUID,
        fiscal_year: int,
        adjustment_days: Decimal,
        reason: str,
        adjusted_by: str,
    ) -> dict:
        ent = db.execute(
            text("""
                SELECT id, balance FROM leave_entitlement
                WHERE employee_id=:emp AND leave_type_id=:lt AND fiscal_year=:yr AND entity_id=:eid
            """),
            {"emp": str(employee_id), "lt": str(leave_type_id), "yr": fiscal_year, "eid": str(entity_id)},
        ).fetchone()
        if not ent:
            raise ValueError("Entitlement tidak ditemukan.")

        new_balance = Decimal(str(ent.balance)) + Decimal(str(adjustment_days))
        if new_balance < 0:
            raise ValueError(f"Penyesuaian akan membuat saldo menjadi negatif ({float(new_balance):.1f}).")

        # Adjust entitled_days (bukan used_days) untuk koreksi positif
        # Adjust used_days untuk koreksi negatif
        if adjustment_days > 0:
            db.execute(
                text("""
                    UPDATE leave_entitlement
                    SET entitled_days = entitled_days + :adj
                    WHERE id = :id
                """),
                {"adj": float(adjustment_days), "id": str(ent.id)},
            )
        else:
            db.execute(
                text("""
                    UPDATE leave_entitlement
                    SET used_days = used_days + :adj
                    WHERE id = :id
                """),
                {"adj": float(-adjustment_days), "id": str(ent.id)},
            )

        db.execute(
            text("""
                INSERT INTO leave_balance_adjustment (
                    entity_id, employee_id, leave_type_id, fiscal_year,
                    adjustment_days, reason, adjusted_by
                ) VALUES (:eid, :emp, :lt, :yr, :adj, :reason, :by)
            """),
            {
                "eid": str(entity_id), "emp": str(employee_id),
                "lt":  str(leave_type_id), "yr": fiscal_year,
                "adj": float(adjustment_days), "reason": reason, "by": adjusted_by,
            },
        )
        db.commit()
        return {"new_balance": float(new_balance)}

    # ─────────────────────────────────────────────────────────────────────────
    # 9. GET LEAVE CALENDAR
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_leave_calendar(
        db: Session,
        entity_id: UUID,
        date_from: date,
        date_to: date,
        department: Optional[str] = None,
    ) -> list[dict]:
        filters = ["lc.entity_id = :eid", "lc.date_to >= :d_from", "lc.date_from <= :d_to"]
        params: dict = {"eid": str(entity_id), "d_from": date_from, "d_to": date_to}
        if department:
            filters.append("lc.department = :dept")
            params["dept"] = department

        rows = db.execute(
            text(f"""
                SELECT * FROM vw_leave_calendar
                WHERE {" AND ".join(filters)}
                ORDER BY date_from, employee_name
            """),
            params,
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    # ─────────────────────────────────────────────────────────────────────────
    # 10. GET UNPAID DEDUCTIONS (untuk payroll)
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_unpaid_deductions(
        db: Session,
        entity_id: UUID,
        payroll_period_year: int,
        payroll_period_month: int,
    ) -> list[dict]:
        """
        Kembalikan list karyawan dengan LWOP dalam bulan payroll tertentu.
        Digunakan oleh modul payroll untuk memotong gaji.
        """
        month_start = date(payroll_period_year, payroll_period_month, 1)
        if payroll_period_month == 12:
            month_end = date(payroll_period_year + 1, 1, 1) - timedelta(days=1)
        else:
            month_end = date(payroll_period_year, payroll_period_month + 1, 1) - timedelta(days=1)

        rows = db.execute(
            text("""
                SELECT lr.id AS request_id, lr.request_no,
                       lr.employee_id, e.full_name AS employee_name, e.employee_code,
                       lr.date_from, lr.date_to, lr.total_days,
                       lt.type_name, lr.payroll_period_id
                FROM leave_request lr
                JOIN employee   e  ON e.id  = lr.employee_id
                JOIN leave_type lt ON lt.id = lr.leave_type_id
                WHERE lr.entity_id = :eid
                  AND lr.status = 'approved'
                  AND lr.is_unpaid_deduction = TRUE
                  AND lr.date_from <= :m_end
                  AND lr.date_to   >= :m_start
                ORDER BY lr.date_from
            """),
            {"eid": str(entity_id), "m_start": month_start, "m_end": month_end},
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _count_working_days(db: Session, entity_id: str, date_from: date, date_to: date) -> Decimal:
        """Hitung hari kerja antara date_from dan date_to (senin-jumat, tidak termasuk public holiday)."""
        holidays = db.execute(
            text("""
                SELECT holiday_date FROM public_holiday
                WHERE entity_id = :eid
                  AND holiday_date BETWEEN :d_from AND :d_to
            """),
            {"eid": entity_id, "d_from": date_from, "d_to": date_to},
        ).fetchall()
        holiday_set = {r.holiday_date for r in holidays}

        count = 0
        current = date_from
        while current <= date_to:
            if current.weekday() < 5 and current not in holiday_set:  # Mon-Fri
                count += 1
            current += timedelta(days=1)
        return Decimal(str(count))

    @staticmethod
    def _get_request(db: Session, request_id: UUID):
        row = db.execute(
            text("SELECT * FROM leave_request WHERE id = :id"),
            {"id": str(request_id)},
        ).fetchone()
        if not row:
            raise ValueError("Leave request tidak ditemukan.")
        return row
