"""
Expense Claim & Reimbursement Engine

Alur:
  1. create_claim()       → Draft klaim + lines
  2. submit_claim()       → Draft → Submitted (email manager, tapi di sini cukup status update)
  3. approve_claim()      → Submitted → Approved (manager)
  4. reject_claim()       → Submitted → Rejected
  5. verify_claim()       → Approved → Verified (finance cek receipt + set GL account)
  6. pay_claim()          → Verified → Paid + POST GL Journal
  7. create_advance()     → Buat uang muka
  8. disburse_advance()   → Cair uang muka + POST GL (Dr.Kas Bon | Cr.Kas/Bank)
  9. settle_advance()     → Pertanggungjawaban uang muka (link ke expense_claim)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session


class ExpenseClaimEngine:

    # ─────────────────────────────────────────────────────────────────────────
    # 1. CREATE CLAIM
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def create_claim(
        db: Session,
        entity_id: UUID,
        employee_id: UUID,
        claim_date: date,
        period_from: date,
        period_to: date,
        purpose: str,
        lines: list[dict],
        project_id: Optional[UUID] = None,
        cost_center_id: Optional[UUID] = None,
        created_by: str = None,
    ) -> dict:
        """
        Buat klaim baru (status: draft).
        lines: [{category_id, expense_date, description, quantity, unit_amount,
                  is_billable, receipt_filename, notes}]
        """
        if period_from > period_to:
            raise ValueError("period_from tidak boleh setelah period_to.")
        if not lines:
            raise ValueError("Minimal 1 baris klaim diperlukan.")

        # Nomor klaim: ECL-YYYYMM-XXXX
        seq_row = db.execute(
            text("""
                SELECT COUNT(*) + 1 AS seq FROM expense_claim
                WHERE entity_id = :eid
                  AND EXTRACT(YEAR  FROM claim_date) = :yr
                  AND EXTRACT(MONTH FROM claim_date) = :mo
            """),
            {"eid": str(entity_id), "yr": claim_date.year, "mo": claim_date.month},
        ).fetchone()
        claim_no = f"ECL-{claim_date.strftime('%Y%m')}-{seq_row.seq:04d}"

        claim_row = db.execute(
            text("""
                INSERT INTO expense_claim (
                    entity_id, claim_no, employee_id, project_id, cost_center_id,
                    claim_date, period_from, period_to, purpose, status
                ) VALUES (
                    :eid, :no, :emp, :proj, :cc,
                    :dt, :pfrom, :pto, :purpose, 'draft'
                ) RETURNING id
            """),
            {
                "eid":     str(entity_id),
                "no":      claim_no,
                "emp":     str(employee_id),
                "proj":    str(project_id) if project_id else None,
                "cc":      str(cost_center_id) if cost_center_id else None,
                "dt":      claim_date,
                "pfrom":   period_from,
                "pto":     period_to,
                "purpose": purpose,
            },
        ).fetchone()
        claim_id = str(claim_row.id)

        total = Decimal("0")
        for i, ln in enumerate(lines, start=1):
            qty    = Decimal(str(ln.get("quantity", 1) or 1))
            unit   = Decimal(str(ln["unit_amount"]))
            line_total = qty * unit
            total += line_total

            db.execute(
                text("""
                    INSERT INTO expense_claim_line (
                        claim_id, line_no, category_id,
                        expense_date, description, quantity, unit_amount,
                        gl_account_code, receipt_filename,
                        is_billable, notes
                    ) VALUES (
                        :cid, :lno, :cat,
                        :dt, :desc, :qty, :unit,
                        :gl, :receipt,
                        :billable, :notes
                    )
                """),
                {
                    "cid":     claim_id,
                    "lno":     i,
                    "cat":     str(ln["category_id"]),
                    "dt":      ln["expense_date"],
                    "desc":    ln["description"],
                    "qty":     float(qty),
                    "unit":    float(unit),
                    "gl":      ln.get("gl_account_code"),
                    "receipt": ln.get("receipt_filename"),
                    "billable": ln.get("is_billable", False),
                    "notes":   ln.get("notes"),
                },
            )

        db.execute(
            text("UPDATE expense_claim SET total_amount=:total WHERE id=:id"),
            {"total": float(total), "id": claim_id},
        )
        db.commit()
        return {"claim_id": claim_id, "claim_no": claim_no, "total_amount": float(total)}

    # ─────────────────────────────────────────────────────────────────────────
    # 2. SUBMIT
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def submit_claim(db: Session, claim_id: UUID, submitted_by: str) -> dict:
        claim = ExpenseClaimEngine._get_claim(db, claim_id)
        if claim.status != "draft":
            raise ValueError(f"Klaim sudah berstatus {claim.status}, tidak bisa disubmit.")

        db.execute(
            text("""
                UPDATE expense_claim
                SET status='submitted', submitted_by=:by, submitted_at=NOW()
                WHERE id=:id
            """),
            {"by": submitted_by, "id": str(claim_id)},
        )
        db.commit()
        return {"status": "submitted", "claim_no": claim.claim_no}

    # ─────────────────────────────────────────────────────────────────────────
    # 3. APPROVE
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def approve_claim(
        db: Session,
        claim_id: UUID,
        approved_by: str,
        approved_amount: Optional[Decimal] = None,
        notes: str = None,
    ) -> dict:
        """
        Manager approve. Bisa sesuaikan approved_amount per header,
        atau approved_amount per baris via line_overrides.
        """
        claim = ExpenseClaimEngine._get_claim(db, claim_id)
        if claim.status != "submitted":
            raise ValueError(f"Klaim berstatus {claim.status}, hanya 'submitted' yang bisa diapprove.")

        # Default approved_amount = total_amount
        amt = float(approved_amount) if approved_amount is not None else float(claim.total_amount)

        db.execute(
            text("""
                UPDATE expense_claim
                SET status='approved', approved_by=:by, approved_at=NOW(),
                    approved_amount=:amt, approval_notes=:notes
                WHERE id=:id
            """),
            {"by": approved_by, "amt": amt, "notes": notes, "id": str(claim_id)},
        )
        db.commit()
        return {"status": "approved", "approved_amount": amt}

    # ─────────────────────────────────────────────────────────────────────────
    # 4. REJECT
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def reject_claim(
        db: Session,
        claim_id: UUID,
        rejected_by: str,
        reason: str,
    ) -> dict:
        claim = ExpenseClaimEngine._get_claim(db, claim_id)
        if claim.status not in ("submitted", "approved"):
            raise ValueError(f"Klaim berstatus {claim.status}, tidak bisa direject.")

        db.execute(
            text("""
                UPDATE expense_claim
                SET status='rejected', approved_by=:by, approved_at=NOW(), approval_notes=:reason
                WHERE id=:id
            """),
            {"by": rejected_by, "reason": reason, "id": str(claim_id)},
        )
        db.commit()
        return {"status": "rejected", "reason": reason}

    # ─────────────────────────────────────────────────────────────────────────
    # 5. VERIFY (Finance)
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def verify_claim(
        db: Session,
        claim_id: UUID,
        verified_by: str,
        line_updates: Optional[list[dict]] = None,
    ) -> dict:
        """
        Finance memverifikasi receipt + bisa update gl_account_code / approved_amount per baris.
        line_updates: [{line_id, gl_account_code, approved_amount}]
        """
        claim = ExpenseClaimEngine._get_claim(db, claim_id)
        if claim.status != "approved":
            raise ValueError(f"Klaim berstatus {claim.status}, hanya 'approved' yang bisa diverifikasi.")

        if line_updates:
            for lu in line_updates:
                db.execute(
                    text("""
                        UPDATE expense_claim_line
                        SET gl_account_code  = COALESCE(:gl,  gl_account_code),
                            approved_amount  = COALESCE(:amt, approved_amount)
                        WHERE id = :lid AND claim_id = :cid
                    """),
                    {
                        "gl":  lu.get("gl_account_code"),
                        "amt": lu.get("approved_amount"),
                        "lid": str(lu["line_id"]),
                        "cid": str(claim_id),
                    },
                )

        db.execute(
            text("""
                UPDATE expense_claim
                SET status='verified', verified_by=:by, verified_at=NOW()
                WHERE id=:id
            """),
            {"by": verified_by, "id": str(claim_id)},
        )
        db.commit()
        return {"status": "verified"}

    # ─────────────────────────────────────────────────────────────────────────
    # 6. PAY CLAIM  — posting GL otomatis
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def pay_claim(
        db: Session,
        claim_id: UUID,
        payment_method: str,
        paid_by: str,
        payment_date: date,
        bank_account_id: Optional[UUID] = None,
        employee_bank_account_no: Optional[str] = None,
    ) -> dict:
        """
        Posting GL reimbursement:
          Untuk setiap baris → Dr. Expense Account (dari category.gl_account_code)
          Total → Cr. Kas/Bank (dari bank_account.gl_account_code)

          Jika ada uang muka yang terkait → offset terlebih dulu.
        """
        claim = ExpenseClaimEngine._get_claim(db, claim_id)
        if claim.status != "verified":
            raise ValueError(f"Klaim berstatus {claim.status}, hanya 'verified' yang bisa dibayar.")

        lines = db.execute(
            text("""
                SELECT ecl.id, ecl.description,
                       COALESCE(ecl.approved_amount, ecl.total_amount) AS amount,
                       COALESCE(ecl.gl_account_code, cat.gl_account_code)  AS gl_code,
                       ecl.is_billable
                FROM expense_claim_line ecl
                JOIN expense_category cat ON cat.id = ecl.category_id
                WHERE ecl.claim_id = :cid
                ORDER BY ecl.line_no
            """),
            {"cid": str(claim_id)},
        ).fetchall()

        if not lines:
            raise ValueError("Tidak ada baris klaim.")

        # Tentukan akun kredit (Kas/Bank)
        if bank_account_id:
            ba = db.execute(
                text("SELECT gl_account_code FROM bank_account WHERE id=:id"),
                {"id": str(bank_account_id)},
            ).fetchone()
            if not ba or not ba.gl_account_code:
                raise ValueError("Bank account tidak memiliki gl_account_code.")
            credit_code = ba.gl_account_code
        elif payment_method == "cash":
            credit_code = None  # harus dari parameter caller
        else:
            credit_code = None

        if not credit_code:
            raise ValueError(
                "credit_account_code tidak ditemukan. Set gl_account_code pada bank_account "
                "atau gunakan payment_method='cash' dengan bank_account_id."
            )

        # Resolve account IDs dari CoA
        entity_id = str(claim.entity_id)

        def get_account_id(code: str) -> str:
            row = db.execute(
                text("SELECT id FROM chart_of_accounts WHERE account_code=:c AND entity_id=:e"),
                {"c": code, "e": entity_id},
            ).fetchone()
            if not row:
                raise ValueError(f"Account code '{code}' tidak ditemukan dalam CoA entity.")
            return str(row.id)

        credit_acc_id = get_account_id(credit_code)
        total_pay = sum(Decimal(str(ln.amount)) for ln in lines)

        # Buat GL Journal
        journal_row = db.execute(
            text("""
                INSERT INTO gl_journal (
                    entity_id, journal_date, description,
                    journal_type, reference_no, status, created_by
                ) VALUES (
                    :eid, :dt, :desc,
                    'expense', :ref, 'posted', :by
                ) RETURNING id
            """),
            {
                "eid":  entity_id,
                "dt":   payment_date,
                "desc": f"Reimbursement {claim.claim_no}",
                "ref":  claim.claim_no,
                "by":   paid_by,
            },
        ).fetchone()
        journal_id = str(journal_row.id)

        # Debit per baris expense
        for ln in lines:
            if not ln.gl_code:
                raise ValueError(
                    f"GL account code tidak ditemukan untuk baris '{ln.description}'. "
                    "Set di expense_category.gl_account_code atau verifikasi per baris."
                )
            debit_acc_id = get_account_id(ln.gl_code)
            db.execute(
                text("""
                    INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr)
                    VALUES (:jid, :acc, :desc, :amt, 0)
                """),
                {"jid": journal_id, "acc": debit_acc_id, "desc": ln.description, "amt": float(ln.amount)},
            )

        # Credit total ke kas/bank
        db.execute(
            text("""
                INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr)
                VALUES (:jid, :acc, :desc, 0, :amt)
            """),
            {
                "jid":  journal_id,
                "acc":  credit_acc_id,
                "desc": f"Pembayaran reimbursement {claim.claim_no}",
                "amt":  float(total_pay),
            },
        )

        db.execute(
            text("""
                UPDATE expense_claim
                SET status='paid', paid_by=:by, paid_at=NOW(),
                    payment_method=:method, bank_account_id=:ba,
                    payment_journal_id=:jid
                WHERE id=:id
            """),
            {
                "by":     paid_by,
                "method": payment_method,
                "ba":     str(bank_account_id) if bank_account_id else None,
                "jid":    journal_id,
                "id":     str(claim_id),
            },
        )
        db.commit()
        return {
            "status":         "paid",
            "journal_id":     journal_id,
            "total_paid":     float(total_pay),
            "claim_no":       claim.claim_no,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 7. CREATE ADVANCE
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def create_advance(
        db: Session,
        entity_id: UUID,
        employee_id: UUID,
        advance_date: date,
        purpose: str,
        amount_requested: Decimal,
        project_id: Optional[UUID] = None,
        approved_by: str = None,
    ) -> dict:
        seq_row = db.execute(
            text("""
                SELECT COUNT(*) + 1 AS seq FROM expense_advance
                WHERE entity_id = :eid
                  AND EXTRACT(YEAR FROM advance_date) = :yr
            """),
            {"eid": str(entity_id), "yr": advance_date.year},
        ).fetchone()
        adv_no = f"ADV-{advance_date.year}-{seq_row.seq:04d}"

        row = db.execute(
            text("""
                INSERT INTO expense_advance (
                    entity_id, advance_no, employee_id, project_id,
                    advance_date, purpose, amount_requested,
                    amount_approved, status
                ) VALUES (
                    :eid, :no, :emp, :proj,
                    :dt, :purpose, :req,
                    :appr, 'approved'
                ) RETURNING id
            """),
            {
                "eid":    str(entity_id),
                "no":     adv_no,
                "emp":    str(employee_id),
                "proj":   str(project_id) if project_id else None,
                "dt":     advance_date,
                "purpose": purpose,
                "req":    float(amount_requested),
                "appr":   float(amount_requested) if approved_by else None,
            },
        ).fetchone()
        db.commit()
        return {"advance_id": str(row.id), "advance_no": adv_no}

    # ─────────────────────────────────────────────────────────────────────────
    # 8. DISBURSE ADVANCE — cair uang muka + GL
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def disburse_advance(
        db: Session,
        advance_id: UUID,
        disbursed_by: str,
        disburse_date: date,
        bank_account_id: UUID,
        kas_bon_account_code: str = "1-1300",  # Akun Kas Bon / Uang Muka Karyawan
    ) -> dict:
        """
        Posting GL: Dr. Kas Bon Karyawan | Cr. Kas/Bank
        """
        adv = db.execute(
            text("""
                SELECT ea.*, ba.gl_account_code AS bank_gl
                FROM expense_advance ea
                JOIN bank_account ba ON ba.id = :ba_id
                WHERE ea.id = :id
            """),
            {"id": str(advance_id), "ba_id": str(bank_account_id)},
        ).fetchone()
        if not adv:
            raise ValueError("Advance tidak ditemukan.")
        if adv.status != "approved":
            raise ValueError(f"Advance berstatus {adv.status}, hanya 'approved' yang bisa dicairkan.")
        if not adv.bank_gl:
            raise ValueError("Bank account tidak memiliki gl_account_code.")

        entity_id = str(adv.entity_id)
        amount    = float(adv.amount_approved or adv.amount_requested)

        def get_account_id(code: str) -> str:
            row = db.execute(
                text("SELECT id FROM chart_of_accounts WHERE account_code=:c AND entity_id=:e"),
                {"c": code, "e": entity_id},
            ).fetchone()
            if not row:
                raise ValueError(f"Account code '{code}' tidak ditemukan.")
            return str(row.id)

        debit_acc_id  = get_account_id(kas_bon_account_code)
        credit_acc_id = get_account_id(adv.bank_gl)

        journal_row = db.execute(
            text("""
                INSERT INTO gl_journal (entity_id, journal_date, description,
                                        journal_type, reference_no, status, created_by)
                VALUES (:eid, :dt, :desc, 'expense', :ref, 'posted', :by)
                RETURNING id
            """),
            {
                "eid":  entity_id,
                "dt":   disburse_date,
                "desc": f"Pencairan uang muka {adv.advance_no}",
                "ref":  adv.advance_no,
                "by":   disbursed_by,
            },
        ).fetchone()
        journal_id = str(journal_row.id)

        db.execute(
            text("""
                INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr)
                VALUES
                  (:jid, :d_acc, :desc, :amt, 0),
                  (:jid, :c_acc, :desc, 0, :amt)
            """),
            {
                "jid":   journal_id,
                "d_acc": debit_acc_id,
                "c_acc": credit_acc_id,
                "desc":  f"Uang muka {adv.advance_no}",
                "amt":   amount,
            },
        )

        db.execute(
            text("""
                UPDATE expense_advance
                SET status='disbursed', amount_disbursed=:amt,
                    disbursed_by=:by, disburse_journal_id=:jid
                WHERE id=:id
            """),
            {"amt": amount, "by": disbursed_by, "jid": journal_id, "id": str(advance_id)},
        )
        db.commit()
        return {"status": "disbursed", "journal_id": journal_id, "amount_disbursed": amount}

    # ─────────────────────────────────────────────────────────────────────────
    # 9. SETTLE ADVANCE — pertanggungjawaban uang muka
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def settle_advance(
        db: Session,
        advance_id: UUID,
        claim_id: UUID,
        settled_by: str,
        settle_date: date,
        kas_bon_account_code: str = "1-1300",
    ) -> dict:
        """
        Pertanggungjawaban: link advance ke expense_claim.
        GL Settle:
          - Jika claim > advance: Dr.Kas Bon (full), Cr.Beban (claim lines) → selisih Dr.Beban | Cr.Kas/Bank
          - Jika claim < advance: Dr.Kas Bon (claim), Dr.Kas/Bank (kembalian), Cr.Beban
          Implementasi sederhana: Cr. Kas Bon sebesar advance, Dr/Cr selisih ke beban/kas
        """
        adv = db.execute(
            text("SELECT * FROM expense_advance WHERE id=:id"),
            {"id": str(advance_id)},
        ).fetchone()
        if not adv:
            raise ValueError("Advance tidak ditemukan.")
        if adv.status != "disbursed":
            raise ValueError("Advance belum dicairkan, tidak bisa di-settle.")

        claim = ExpenseClaimEngine._get_claim(db, claim_id)
        if claim.status != "verified":
            raise ValueError("Claim harus dalam status 'verified' untuk settlement.")

        entity_id    = str(adv.entity_id)
        advance_amt  = Decimal(str(adv.amount_disbursed))
        claim_amt    = Decimal(str(claim.approved_amount or claim.total_amount))
        diff         = claim_amt - advance_amt  # positif = kurang bayar, negatif = kembalian

        def get_account_id(code: str) -> str:
            row = db.execute(
                text("SELECT id FROM chart_of_accounts WHERE account_code=:c AND entity_id=:e"),
                {"c": code, "e": entity_id},
            ).fetchone()
            if not row:
                raise ValueError(f"Account code '{code}' tidak ditemukan.")
            return str(row.id)

        kas_bon_id = get_account_id(kas_bon_account_code)

        journal_row = db.execute(
            text("""
                INSERT INTO gl_journal (entity_id, journal_date, description,
                                        journal_type, reference_no, status, created_by)
                VALUES (:eid, :dt, :desc, 'expense', :ref, 'posted', :by)
                RETURNING id
            """),
            {
                "eid":  entity_id,
                "dt":   settle_date,
                "desc": f"Settlement uang muka {adv.advance_no} → {claim.claim_no}",
                "ref":  adv.advance_no,
                "by":   settled_by,
            },
        ).fetchone()
        journal_id = str(journal_row.id)

        # Kredit Kas Bon (hapus piutang uang muka)
        db.execute(
            text("""
                INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr)
                VALUES (:jid, :acc, :desc, 0, :amt)
            """),
            {"jid": journal_id, "acc": kas_bon_id, "desc": "Settlement kas bon", "amt": float(advance_amt)},
        )

        # Debit beban sesuai klaim lines
        lines = db.execute(
            text("""
                SELECT ecl.description,
                       COALESCE(ecl.approved_amount, ecl.total_amount) AS amount,
                       COALESCE(ecl.gl_account_code, cat.gl_account_code) AS gl_code
                FROM expense_claim_line ecl
                JOIN expense_category cat ON cat.id = ecl.category_id
                WHERE ecl.claim_id = :cid
            """),
            {"cid": str(claim_id)},
        ).fetchall()

        for ln in lines:
            if not ln.gl_code:
                raise ValueError(f"GL account tidak ditemukan untuk baris '{ln.description}'.")
            acc_id = get_account_id(ln.gl_code)
            db.execute(
                text("""
                    INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr)
                    VALUES (:jid, :acc, :desc, :amt, 0)
                """),
                {"jid": journal_id, "acc": acc_id, "desc": ln.description, "amt": float(ln.amount)},
            )

        # Update advance + claim
        db.execute(
            text("""
                UPDATE expense_advance
                SET status='settled', amount_settled=:amt,
                    linked_claim_id=:cid, settle_journal_id=:jid
                WHERE id=:id
            """),
            {"amt": float(claim_amt), "cid": str(claim_id), "jid": journal_id, "id": str(advance_id)},
        )
        db.execute(
            text("UPDATE expense_claim SET status='paid', paid_by=:by, paid_at=NOW(), payment_journal_id=:jid WHERE id=:cid"),
            {"by": settled_by, "jid": journal_id, "cid": str(claim_id)},
        )
        db.commit()
        return {
            "status":      "settled",
            "journal_id":  journal_id,
            "advance_amount": float(advance_amt),
            "claim_amount":   float(claim_amt),
            "difference":     float(diff),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # GET CLAIM DETAIL
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_claim_detail(db: Session, claim_id: UUID) -> dict:
        claim = ExpenseClaimEngine._get_claim(db, claim_id)
        lines = db.execute(
            text("""
                SELECT ecl.id, ecl.line_no, ecl.expense_date, ecl.description,
                       ecl.quantity, ecl.unit_amount, ecl.total_amount,
                       ecl.approved_amount, ecl.gl_account_code,
                       ecl.receipt_filename, ecl.is_billable, ecl.notes,
                       cat.category_name, cat.expense_type
                FROM expense_claim_line ecl
                JOIN expense_category cat ON cat.id = ecl.category_id
                WHERE ecl.claim_id = :cid
                ORDER BY ecl.line_no
            """),
            {"cid": str(claim_id)},
        ).fetchall()

        return {
            "claim": dict(claim._mapping),
            "lines": [dict(r._mapping) for r in lines],
        }

    # ─────────────────────────────────────────────────────────────────────────
    # LIST CLAIMS
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def list_claims(
        db: Session,
        entity_id: UUID,
        employee_id: Optional[UUID] = None,
        status: Optional[str] = None,
        project_id: Optional[UUID] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        page: int = 1,
        size: int = 20,
    ) -> dict:
        filters = ["entity_id = :eid"]
        params: dict = {"eid": str(entity_id), "offset": (page - 1) * size, "limit": size}

        if employee_id:
            filters.append("employee_id = :emp")
            params["emp"] = str(employee_id)
        if status:
            filters.append("status = :status")
            params["status"] = status
        if project_id:
            filters.append("project_id = :proj")
            params["proj"] = str(project_id)
        if date_from:
            filters.append("claim_date >= :d_from")
            params["d_from"] = date_from
        if date_to:
            filters.append("claim_date <= :d_to")
            params["d_to"] = date_to

        where = " AND ".join(filters)
        rows = db.execute(
            text(f"""
                SELECT * FROM vw_expense_claim_summary
                WHERE {where}
                ORDER BY claim_date DESC
                OFFSET :offset LIMIT :limit
            """),
            params,
        ).fetchall()
        total = db.execute(
            text(f"SELECT COUNT(*) FROM vw_expense_claim_summary WHERE {where}"),
            {k: v for k, v in params.items() if k not in ("offset", "limit")},
        ).fetchone()[0]

        return {
            "total": total,
            "page":  page,
            "size":  size,
            "items": [dict(r._mapping) for r in rows],
        }

    # ─────────────────────────────────────────────────────────────────────────
    # INTERNAL HELPER
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _get_claim(db: Session, claim_id: UUID):
        row = db.execute(
            text("SELECT * FROM expense_claim WHERE id = :id"),
            {"id": str(claim_id)},
        ).fetchone()
        if not row:
            raise ValueError("Expense claim tidak ditemukan.")
        return row
