"""
Payroll Disbursement Engine
============================
Mengelola proses pembayaran gaji setelah payroll engine menghitung.

Dua jurnal terpisah:
  A. Accrual Journal (jika belum ada dari payroll_run):
       Dr Beban Gaji           Total Gross
       Dr Beban BPJS Employer
         Cr Hutang Gaji        Total Net
         Cr Hutang PPh 21
         Cr Hutang BPJS TK Employee
         Cr Hutang BPJS Kes Employee
         Cr Hutang BPJS Employer

  B. Disbursement Journal (saat transfer ke karyawan):
       Dr Hutang Gaji          Total Net
         Cr Bank/Kas Gaji      Total Net

Flow:
  1. create_disbursement(payroll_run_id) → load data dari payroll_run
  2. submit() → pending_approval
  3. approve() → approved
  4. post_accrual() → posting jurnal beban (opsional jika payroll_run belum posting)
  5. disburse() → posting jurnal bayar + mark per karyawan
  6. mark_transferred(employee_id, reference) → update status per karyawan
  7. export_bank_file() → CSV untuk bank transfer

Notes:
  - payroll_run berisi data gaji yang sudah final dari payroll engine
  - Disbursement line dibuat dari payroll_detail (per karyawan)
  - Bank file format: sesuaikan dengan format bank masing-masing
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from sqlalchemy import text
from sqlalchemy.orm import Session


def _to_d(val) -> Decimal:
    return Decimal(str(val)) if val is not None else Decimal("0")


class PayrollDisbursementEngine:

    # ── Create dari payroll_run ───────────────────────────────────────────────

    @staticmethod
    def create_disbursement(
        db: Session,
        entity_id: str,
        payroll_run_id: str,
        disbursement_date: date,
        bank_account_id: str,
        gl_salary_payable: str = "2-2000",
        gl_pph21_payable: str = "2-1200",
        gl_bpjs_tk_payable: str = "2-1300",
        gl_bpjs_kes_payable: str = "2-1400",
        gl_salary_expense: str = "6-1000",
        gl_bpjs_employer_expense: str = "6-1100",
        notes: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> dict:
        """
        Buat disbursement batch dari payroll_run.
        Load semua data karyawan dari payroll_detail.
        """
        # Cek payroll_run
        run = db.execute(text("""
            SELECT id, entity_id, fiscal_year, fiscal_month, status,
                   total_gross_salary, total_net_salary,
                   total_pph21, total_bpjs_tk_employee, total_bpjs_kes_employee,
                   total_bpjs_employer
            FROM payroll_run
            WHERE id = :id AND entity_id = :eid
        """), {"id": payroll_run_id, "eid": entity_id}).first()

        if run is None:
            raise ValueError("Payroll run tidak ditemukan atau bukan milik entity ini.")
        if run.status not in ("approved", "posted"):
            raise ValueError(
                f"Payroll run status '{run.status}'. Harus 'approved' atau 'posted' "
                "sebelum bisa dibuat disbursement."
            )

        # Cek tidak ada disbursement aktif untuk run ini
        existing = db.execute(text("""
            SELECT id, status FROM payroll_disbursement WHERE payroll_run_id = :rid
        """), {"rid": payroll_run_id}).first()

        if existing and existing.status not in ("cancelled",):
            raise ValueError(
                f"Disbursement untuk payroll run ini sudah ada (status: {existing.status})."
            )

        # Ambil detail per karyawan dari payroll_detail
        employees = db.execute(text("""
            SELECT
                pd.employee_id,
                e.employee_code,
                e.full_name AS employee_name,
                e.department,
                e.bank_name,
                e.bank_account_number,
                e.bank_account_holder,
                e.bank_branch,
                pd.gross_salary,
                COALESCE(pd.total_allowances, 0)     AS total_allowances,
                COALESCE(pd.pph21_amount, 0)          AS pph21_amount,
                COALESCE(pd.bpjs_tk_employee, 0)      AS bpjs_tk_employee,
                COALESCE(pd.bpjs_kes_employee, 0)     AS bpjs_kes_employee,
                COALESCE(pd.other_deductions, 0)      AS other_deductions,
                COALESCE(pd.total_deductions, 0)      AS total_deductions,
                pd.net_salary
            FROM payroll_detail pd
            JOIN employee e ON e.id = pd.employee_id
            WHERE pd.payroll_run_id = :rid
            ORDER BY e.department, e.employee_code
        """), {"rid": payroll_run_id}).fetchall()

        if not employees:
            raise ValueError("Tidak ada data karyawan di payroll run ini.")

        # Hitung totals (pakai dari payroll_run jika ada, fallback ke sum)
        total_gross = _to_d(run.total_gross_salary)
        total_net   = _to_d(run.total_net_salary)
        total_pph21 = _to_d(run.total_pph21)
        total_bpjs_tk  = _to_d(run.total_bpjs_tk_employee)
        total_bpjs_kes = _to_d(run.total_bpjs_kes_employee)
        total_bpjs_employer = _to_d(run.total_bpjs_employer)
        total_deductions = total_gross - total_net

        disb = db.execute(text("""
            INSERT INTO payroll_disbursement
                (entity_id, payroll_run_id, fiscal_year, fiscal_month,
                 disbursement_date, bank_account_id,
                 total_gross, total_deductions, total_net,
                 total_pph21, total_bpjs_tk_employee, total_bpjs_kes_employee,
                 total_bpjs_employer, employee_count,
                 gl_salary_payable, gl_pph21_payable,
                 gl_bpjs_tk_payable, gl_bpjs_kes_payable,
                 gl_salary_expense, gl_bpjs_employer_expense,
                 notes, created_by)
            VALUES
                (:eid, :rid, :fy, :fm,
                 :ddate, :bank,
                 :tg, :td, :tn,
                 :pph21, :bpjstk, :bpjskes,
                 :bpjsemp, :ecount,
                 :gsp, :gpph, :gtk, :gkes, :gexp, :gbexp,
                 :notes, :cb)
            RETURNING id
        """), {
            "eid": entity_id, "rid": payroll_run_id,
            "fy": run.fiscal_year, "fm": run.fiscal_month,
            "ddate": disbursement_date, "bank": bank_account_id,
            "tg": str(total_gross), "td": str(total_deductions), "tn": str(total_net),
            "pph21": str(total_pph21), "bpjstk": str(total_bpjs_tk),
            "bpjskes": str(total_bpjs_kes), "bpjsemp": str(total_bpjs_employer),
            "ecount": len(employees),
            "gsp": gl_salary_payable, "gpph": gl_pph21_payable,
            "gtk": gl_bpjs_tk_payable, "gkes": gl_bpjs_kes_payable,
            "gexp": gl_salary_expense, "gbexp": gl_bpjs_employer_expense,
            "notes": notes, "cb": created_by,
        }).first()
        disb_id = str(disb.id)

        # Insert lines per karyawan
        for emp in employees:
            db.execute(text("""
                INSERT INTO payroll_disbursement_line
                    (disbursement_id, employee_id, employee_code, employee_name, department,
                     bank_name, bank_account_number, bank_account_holder, bank_branch,
                     gross_salary, total_allowances,
                     pph21_amount, bpjs_tk_employee, bpjs_kes_employee,
                     other_deductions, total_deductions, net_salary)
                VALUES
                    (:did, :eid, :ecode, :ename, :dept,
                     :bname, :bacct, :bholder, :bbranch,
                     :gross, :allow,
                     :pph21, :bpjstk, :bpjskes,
                     :other, :tded, :net)
            """), {
                "did": disb_id,
                "eid": str(emp.employee_id),
                "ecode": emp.employee_code,
                "ename": emp.employee_name,
                "dept": emp.department,
                "bname": emp.bank_name,
                "bacct": emp.bank_account_number,
                "bholder": emp.bank_account_holder,
                "bbranch": getattr(emp, "bank_branch", None),
                "gross": str(_to_d(emp.gross_salary)),
                "allow": str(_to_d(emp.total_allowances)),
                "pph21": str(_to_d(emp.pph21_amount)),
                "bpjstk": str(_to_d(emp.bpjs_tk_employee)),
                "bpjskes": str(_to_d(emp.bpjs_kes_employee)),
                "other": str(_to_d(emp.other_deductions)),
                "tded": str(_to_d(emp.total_deductions)),
                "net": str(_to_d(emp.net_salary)),
            })

        db.commit()
        return {
            "disbursement_id": disb_id,
            "status": "draft",
            "employee_count": len(employees),
            "total_gross": str(total_gross),
            "total_net": str(total_net),
            "fiscal_year": run.fiscal_year,
            "fiscal_month": run.fiscal_month,
        }

    # ── Submit & Approve ──────────────────────────────────────────────────────

    @staticmethod
    def submit(db: Session, disbursement_id: str, submitted_by: str) -> dict:
        disb = PayrollDisbursementEngine._get_or_raise(db, disbursement_id, "draft")
        db.execute(text("""
            UPDATE payroll_disbursement
               SET status = 'pending_approval',
                   submitted_by = :sb, submitted_at = NOW(), updated_at = NOW()
            WHERE id = :id
        """), {"sb": submitted_by, "id": disbursement_id})
        db.commit()
        return {"disbursement_id": disbursement_id, "status": "pending_approval"}

    @staticmethod
    def approve(db: Session, disbursement_id: str, approved_by: str) -> dict:
        disb = PayrollDisbursementEngine._get_or_raise(db, disbursement_id, "pending_approval")
        db.execute(text("""
            UPDATE payroll_disbursement
               SET status = 'approved',
                   approved_by = :ab, approved_at = NOW(), updated_at = NOW()
            WHERE id = :id
        """), {"ab": approved_by, "id": disbursement_id})
        db.commit()
        return {"disbursement_id": disbursement_id, "status": "approved"}

    # ── Post Accrual Journal ─────────────────────────────────────────────────

    @staticmethod
    def post_accrual(
        db: Session,
        disbursement_id: str,
        entity_id: str,
        journal_date: date,
        posted_by: str,
    ) -> dict:
        """
        Posting jurnal beban gaji (accrual):
          Dr Beban Gaji              gross_salary
          Dr Beban BPJS Employer     bpjs_employer
            Cr Hutang Gaji           net_salary
            Cr Hutang PPh 21         pph21
            Cr Hutang BPJS TK        bpjs_tk_employee
            Cr Hutang BPJS Kes       bpjs_kes_employee
            Cr Hutang BPJS Employer  bpjs_employer

        Opsional: jika payroll_run sudah punya jurnal, lewati langkah ini.
        """
        disb = PayrollDisbursementEngine._get_or_raise(db, disbursement_id)
        if str(disb.entity_id) != entity_id:
            raise ValueError("Entity tidak sesuai.")
        if disb.accrual_journal_id:
            raise ValueError("Accrual journal sudah ada.")
        if disb.status not in ("approved", "pending_approval", "draft"):
            raise ValueError(f"Status '{disb.status}' tidak bisa posting accrual.")

        # Resolve GL account IDs
        def _get_acct(code: str):
            row = db.execute(text("""
                SELECT id FROM chart_of_accounts
                WHERE account_code = :code AND entity_id = :eid
            """), {"code": code, "eid": entity_id}).first()
            if row is None:
                raise ValueError(f"Akun '{code}' tidak ditemukan di CoA.")
            return str(row.id)

        acct_expense    = _get_acct(disb.gl_salary_expense)
        acct_bpjs_exp   = _get_acct(disb.gl_bpjs_employer_expense)
        acct_sal_payable = _get_acct(disb.gl_salary_payable)
        acct_pph21      = _get_acct(disb.gl_pph21_payable)
        acct_bpjs_tk    = _get_acct(disb.gl_bpjs_tk_payable)
        acct_bpjs_kes   = _get_acct(disb.gl_bpjs_kes_payable)

        total_gross  = _to_d(disb.total_gross)
        total_net    = _to_d(disb.total_net)
        total_pph21  = _to_d(disb.total_pph21)
        total_bpjs_tk  = _to_d(disb.total_bpjs_tk_employee)
        total_bpjs_kes = _to_d(disb.total_bpjs_kes_employee)
        total_bpjs_employer = _to_d(disb.total_bpjs_employer)
        fiscal_str = f"{disb.fiscal_year}/{disb.fiscal_month:02d}"

        journal = db.execute(text("""
            INSERT INTO gl_journal
                (entity_id, journal_date, journal_type, description,
                 reference_number, status, created_by, created_at)
            VALUES
                (:eid, :jdt, 'payroll_accrual',
                 :desc, :ref, 'posted', :cb, NOW())
            RETURNING id, journal_number
        """), {
            "eid": entity_id,
            "jdt": journal_date,
            "desc": f"Akrual Gaji {fiscal_str}",
            "ref": f"PAYROLL-ACCR-{disb.fiscal_year}{disb.fiscal_month:02d}",
            "cb": posted_by,
        }).first()
        jid = str(journal.id)

        def _dr(acct_id, amount, desc):
            if amount <= 0:
                return
            db.execute(text("""
                INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr)
                VALUES (:jid, :acct, :desc, :amt, 0)
            """), {"jid": jid, "acct": acct_id, "desc": desc, "amt": str(amount)})

        def _cr(acct_id, amount, desc):
            if amount <= 0:
                return
            db.execute(text("""
                INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr)
                VALUES (:jid, :acct, :desc, 0, :amt)
            """), {"jid": jid, "acct": acct_id, "desc": desc, "amt": str(amount)})

        _dr(acct_expense,  total_gross,         f"Beban Gaji {fiscal_str}")
        _dr(acct_bpjs_exp, total_bpjs_employer, f"Beban BPJS Employer {fiscal_str}")
        _cr(acct_sal_payable, total_net,         f"Hutang Gaji {fiscal_str}")
        _cr(acct_pph21,    total_pph21,          f"Hutang PPh 21 {fiscal_str}")
        _cr(acct_bpjs_tk,  total_bpjs_tk,        f"Hutang BPJS TK (Karyawan) {fiscal_str}")
        _cr(acct_bpjs_kes, total_bpjs_kes,       f"Hutang BPJS Kes (Karyawan) {fiscal_str}")

        # BPJS employer: hutang ke BPJS tapi expense sudah di-Dr di atas
        # Jika ada akun terpisah untuk hutang BPJS employer, tambahkan di sini
        # Untuk simplifikasi: gunakan akun yang sama dengan BPJS TK
        if total_bpjs_employer > 0:
            _cr(acct_bpjs_tk, total_bpjs_employer, f"Hutang BPJS Employer {fiscal_str}")

        db.execute(text("""
            UPDATE payroll_disbursement
               SET accrual_journal_id = :jid, updated_at = NOW()
            WHERE id = :did
        """), {"jid": jid, "did": disbursement_id})
        db.commit()

        return {
            "disbursement_id": disbursement_id,
            "accrual_journal_id": jid,
            "journal_number": journal.journal_number,
        }

    # ── Disburse (post pembayaran ke bank) ────────────────────────────────────

    @staticmethod
    def disburse(
        db: Session,
        disbursement_id: str,
        entity_id: str,
        posted_by: str,
        disbursement_date: Optional[date] = None,
    ) -> dict:
        """
        Posting jurnal pembayaran gaji ke bank:
          Dr Hutang Gaji    total_net
            Cr Bank/Kas     total_net

        Setelah ini, semua line karyawan berubah ke 'transferred'
        (kecuali yang tidak punya rekening bank → 'skipped').
        Gunakan mark_transferred() untuk update satu per satu dari konfirmasi bank.
        """
        disb = PayrollDisbursementEngine._get_or_raise(db, disbursement_id, "approved")
        if str(disb.entity_id) != entity_id:
            raise ValueError("Entity tidak sesuai.")

        actual_date = disbursement_date or disb.disbursement_date

        # Cari GL akun bank dari bank_account
        bank = db.execute(text("""
            SELECT bank_name, account_number, gl_account_code
            FROM bank_account WHERE id = :id
        """), {"id": str(disb.bank_account_id)}).first()

        if bank is None:
            raise ValueError("Bank account tidak ditemukan.")

        bank_gl_code = bank.gl_account_code or "1-1110"
        bank_acct = db.execute(text("""
            SELECT id FROM chart_of_accounts
            WHERE account_code = :code AND entity_id = :eid
        """), {"code": bank_gl_code, "eid": entity_id}).first()

        sal_acct = db.execute(text("""
            SELECT id FROM chart_of_accounts
            WHERE account_code = :code AND entity_id = :eid
        """), {"code": disb.gl_salary_payable, "eid": entity_id}).first()

        if bank_acct is None:
            raise ValueError(f"Akun bank '{bank_gl_code}' tidak ada di CoA.")
        if sal_acct is None:
            raise ValueError(f"Akun hutang gaji '{disb.gl_salary_payable}' tidak ada di CoA.")

        fiscal_str = f"{disb.fiscal_year}/{disb.fiscal_month:02d}"
        total_net = _to_d(disb.total_net)

        journal = db.execute(text("""
            INSERT INTO gl_journal
                (entity_id, journal_date, journal_type, description,
                 reference_number, status, created_by, created_at)
            VALUES
                (:eid, :jdt, 'payroll_disbursement',
                 :desc, :ref, 'posted', :cb, NOW())
            RETURNING id, journal_number
        """), {
            "eid": entity_id,
            "jdt": actual_date,
            "desc": f"Pembayaran Gaji {fiscal_str} via {bank.bank_name} {bank.account_number}",
            "ref": f"PAYROLL-DISB-{disb.fiscal_year}{disb.fiscal_month:02d}",
            "cb": posted_by,
        }).first()
        jid = str(journal.id)

        # Dr Hutang Gaji
        dr_line = db.execute(text("""
            INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr)
            VALUES (:jid, :acct, :desc, :amt, 0)
            RETURNING id
        """), {
            "jid": jid, "acct": str(sal_acct.id),
            "desc": f"Pembayaran hutang gaji {fiscal_str}",
            "amt": str(total_net),
        }).first()

        # Cr Bank
        db.execute(text("""
            INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr)
            VALUES (:jid, :acct, :desc, 0, :amt)
        """), {
            "jid": jid, "acct": str(bank_acct.id),
            "desc": f"Keluar via {bank.bank_name} - gaji {fiscal_str}",
            "amt": str(total_net),
        })

        # Update disbursement
        db.execute(text("""
            UPDATE payroll_disbursement
               SET status = 'disbursed',
                   disbursement_journal_id = :jid,
                   disbursed_by = :db, disbursed_at = NOW(),
                   disbursement_date = :ddate,
                   updated_at = NOW()
            WHERE id = :did
        """), {"jid": jid, "db": posted_by, "ddate": actual_date, "did": disbursement_id})

        # Mark semua karyawan yang punya rekening bank → 'transferred'
        # Yang tidak punya rekening → 'skipped'
        db.execute(text("""
            UPDATE payroll_disbursement_line
               SET status = CASE
                   WHEN bank_account_number IS NOT NULL AND bank_account_number != ''
                   THEN 'transferred'
                   ELSE 'skipped'
               END,
               transferred_at = CASE
                   WHEN bank_account_number IS NOT NULL AND bank_account_number != ''
                   THEN NOW()
                   ELSE NULL
               END
            WHERE disbursement_id = :did
        """), {"did": disbursement_id})

        # Hitung count
        counts = db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'transferred') AS transferred,
                COUNT(*) FILTER (WHERE status = 'skipped')     AS skipped,
                COUNT(*) FILTER (WHERE status = 'pending')     AS pending
            FROM payroll_disbursement_line WHERE disbursement_id = :did
        """), {"did": disbursement_id}).first()

        db.execute(text("""
            UPDATE payroll_disbursement
               SET transferred_count = :tc, skipped_count = :sc
            WHERE id = :did
        """), {"tc": counts.transferred, "sc": counts.skipped, "did": disbursement_id})

        db.commit()

        return {
            "disbursement_id": disbursement_id,
            "status": "disbursed",
            "journal_id": jid,
            "journal_number": journal.journal_number,
            "total_net": str(total_net),
            "transferred_count": counts.transferred,
            "skipped_count": counts.skipped,
        }

    # ── Mark per-karyawan ─────────────────────────────────────────────────────

    @staticmethod
    def mark_transferred(
        db: Session,
        disbursement_id: str,
        employee_id: str,
        transfer_reference: str,
    ) -> dict:
        """Update status transfer untuk satu karyawan (dari konfirmasi bank)."""
        db.execute(text("""
            UPDATE payroll_disbursement_line
               SET status = 'transferred',
                   transfer_reference = :ref,
                   transferred_at = NOW()
            WHERE disbursement_id = :did AND employee_id = :eid
        """), {"ref": transfer_reference, "did": disbursement_id, "eid": employee_id})
        db.commit()
        return {"employee_id": employee_id, "status": "transferred", "reference": transfer_reference}

    @staticmethod
    def mark_failed(
        db: Session,
        disbursement_id: str,
        employee_id: str,
        reason: str,
    ) -> dict:
        """Mark transfer gagal untuk satu karyawan."""
        db.execute(text("""
            UPDATE payroll_disbursement_line
               SET status = 'failed',
                   failure_reason = :reason
            WHERE disbursement_id = :did AND employee_id = :eid
        """), {"reason": reason, "did": disbursement_id, "eid": employee_id})

        db.execute(text("""
            UPDATE payroll_disbursement
               SET failed_count = (
                   SELECT COUNT(*) FROM payroll_disbursement_line
                   WHERE disbursement_id = :did AND status = 'failed'
               )
            WHERE id = :did
        """), {"did": disbursement_id})
        db.commit()
        return {"employee_id": employee_id, "status": "failed", "reason": reason}

    @staticmethod
    def bulk_mark_transferred(
        db: Session,
        disbursement_id: str,
        transfers: list[dict],  # [{employee_id, transfer_reference}]
    ) -> dict:
        """Bulk update status transfer dari file konfirmasi bank."""
        success = 0
        errors = []
        for t in transfers:
            try:
                PayrollDisbursementEngine.mark_transferred(
                    db, disbursement_id, t["employee_id"], t.get("transfer_reference", "")
                )
                success += 1
            except Exception as e:
                errors.append({"employee_id": t.get("employee_id"), "error": str(e)})
        return {"success": success, "errors": errors}

    # ── Cancel ────────────────────────────────────────────────────────────────

    @staticmethod
    def cancel(
        db: Session,
        disbursement_id: str,
        cancelled_by: str,
        reason: str,
    ) -> dict:
        disb = PayrollDisbursementEngine._get_or_raise(db, disbursement_id)
        if disb.status == "disbursed":
            raise ValueError(
                "Disbursement sudah dieksekusi. Tidak bisa dibatalkan. "
                "Buat jurnal koreksi manual jika ada kesalahan."
            )
        db.execute(text("""
            UPDATE payroll_disbursement
               SET status = 'cancelled',
                   cancelled_by = :cb, cancelled_at = NOW(),
                   cancel_reason = :reason, updated_at = NOW()
            WHERE id = :id
        """), {"cb": cancelled_by, "reason": reason, "id": disbursement_id})
        db.commit()
        return {"disbursement_id": disbursement_id, "status": "cancelled"}

    # ── Export bank file (CSV) ────────────────────────────────────────────────

    @staticmethod
    def export_bank_file(
        db: Session,
        disbursement_id: str,
        format: str = "standard",  # standard | bca | mandiri | bni | bri
    ) -> tuple[str, str]:
        """
        Export file transfer untuk diserahkan ke bank.
        Return: (csv_content: str, filename: str)

        Format standard:
          bank_name, account_number, account_holder, amount, reference
        """
        disb = db.execute(text("""
            SELECT pd.*, ba.bank_name AS source_bank, ba.account_number AS source_account
            FROM payroll_disbursement pd
            JOIN bank_account ba ON ba.id = pd.bank_account_id
            WHERE pd.id = :id
        """), {"id": disbursement_id}).first()

        if disb is None:
            raise ValueError("Disbursement tidak ditemukan.")

        lines = db.execute(text("""
            SELECT employee_code, employee_name, department,
                   bank_name, bank_account_number, bank_account_holder, bank_branch,
                   net_salary, status
            FROM payroll_disbursement_line
            WHERE disbursement_id = :id
              AND status IN ('pending', 'transferred')
            ORDER BY department, employee_code
        """), {"id": disbursement_id}).fetchall()

        output = io.StringIO()
        writer = csv.writer(output)

        if format == "bca":
            # Format BCA KlikBCA Bisnis
            writer.writerow(["Rekening Tujuan", "Nama Penerima", "Nominal", "Berita", "Keterangan"])
            for line in lines:
                writer.writerow([
                    line.bank_account_number,
                    line.bank_account_holder or line.employee_name,
                    int(_to_d(line.net_salary)),
                    f"GAJI {disb.fiscal_year}/{disb.fiscal_month:02d}",
                    line.employee_code,
                ])
        elif format == "mandiri":
            # Format Mandiri Cash Management
            writer.writerow(["Account", "Name", "Amount", "Description", "Ref"])
            for line in lines:
                writer.writerow([
                    line.bank_account_number,
                    line.bank_account_holder or line.employee_name,
                    f"{_to_d(line.net_salary):.2f}",
                    f"SAL {disb.fiscal_year}{disb.fiscal_month:02d}",
                    line.employee_code,
                ])
        else:
            # Format standard
            writer.writerow([
                "No", "Kode Karyawan", "Nama Karyawan", "Departemen",
                "Nama Bank", "No. Rekening", "Nama Pemegang",
                "Nominal (IDR)", "Referensi",
            ])
            for i, line in enumerate(lines, 1):
                writer.writerow([
                    i, line.employee_code, line.employee_name, line.department,
                    line.bank_name, line.bank_account_number, line.bank_account_holder,
                    int(_to_d(line.net_salary)),
                    f"GAJI-{disb.fiscal_year}{disb.fiscal_month:02d}-{line.employee_code}",
                ])

        filename = f"payroll_transfer_{disb.fiscal_year}{disb.fiscal_month:02d}_{format}.csv"
        return output.getvalue(), filename

    # ── Query ─────────────────────────────────────────────────────────────────

    @staticmethod
    def list_disbursements(
        db: Session,
        entity_id: str,
        fiscal_year: Optional[int] = None,
        status: Optional[str] = None,
        page: int = 1,
        size: int = 24,
    ) -> dict:
        conditions = ["pd.entity_id = :eid"]
        params: dict = {"eid": entity_id, "offset": (page - 1) * size, "size": size}

        if fiscal_year:
            conditions.append("pd.fiscal_year = :fy")
            params["fy"] = fiscal_year
        if status:
            conditions.append("pd.status = :status")
            params["status"] = status

        where = " AND ".join(conditions)

        total = db.execute(text(f"""
            SELECT COUNT(*) FROM payroll_disbursement pd WHERE {where}
        """), params).scalar()

        rows = db.execute(text(f"""
            SELECT * FROM vw_payroll_disbursement_summary
            WHERE entity_id = :eid
              {'AND fiscal_year = :fy' if fiscal_year else ''}
              {'AND status = :status' if status else ''}
            ORDER BY fiscal_year DESC, fiscal_month DESC
            LIMIT :size OFFSET :offset
        """), params).fetchall()

        return {"total": total, "page": page, "size": size,
                "items": [dict(r._mapping) for r in rows]}

    @staticmethod
    def get_detail(db: Session, disbursement_id: str) -> dict:
        disb = db.execute(text("""
            SELECT * FROM vw_payroll_disbursement_summary WHERE id = :id
        """), {"id": disbursement_id}).first()
        if disb is None:
            raise ValueError("Disbursement tidak ditemukan.")

        lines = db.execute(text("""
            SELECT * FROM payroll_disbursement_line
            WHERE disbursement_id = :id
            ORDER BY department, employee_code
        """), {"id": disbursement_id}).fetchall()

        return {
            **dict(disb._mapping),
            "lines": [dict(l._mapping) for l in lines],
        }

    @staticmethod
    def get_pending_transfers(db: Session, disbursement_id: str) -> list[dict]:
        """Karyawan yang transfer belum terkonfirmasi / gagal."""
        rows = db.execute(text("""
            SELECT * FROM vw_payroll_pending_transfer WHERE disbursement_id = :id
        """), {"id": disbursement_id}).fetchall()
        return [dict(r._mapping) for r in rows]

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _get_or_raise(
        db: Session,
        disbursement_id: str,
        expected_status: Optional[str] = None,
    ):
        disb = db.execute(text("""
            SELECT * FROM payroll_disbursement WHERE id = :id
        """), {"id": disbursement_id}).first()
        if disb is None:
            raise ValueError(f"Disbursement '{disbursement_id}' tidak ditemukan.")
        if expected_status and disb.status != expected_status:
            raise ValueError(
                f"Status saat ini '{disb.status}', dibutuhkan '{expected_status}'."
            )
        return disb
