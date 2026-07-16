"""
Bank Reconciliation Engine

Alur:
  1. create_statement()       → buat header rekening koran
  2. import_lines()           → bulk insert baris transaksi
  3. run_auto_match()         → cocokkan otomatis by amount + date ± N days + ref
  4. confirm_suggestion()     → konfirmasi satu suggested match
  5. create_manual_match()    → cocokkan manual antara satu bank line ↔ satu GL line
  6. create_adjustment()      → catat item hanya-bank atau hanya-GL + buat jurnal penyesuaian
  7. finalize_reconciliation()→ validasi completion, lock statement
  8. get_report()             → laporan rekonsiliasi lengkap
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


class BankReconEngine:

    # ─── konstanta toleransi ─────────────────────────────────────────────────
    AMOUNT_TOLERANCE   = Decimal("0.05")   # toleransi selisih pembulatan Rp 0,05
    DATE_WINDOW_DAYS   = 5                  # window pencocokan tanggal ± 5 hari

    # ─────────────────────────────────────────────────────────────────────────
    # 1. CREATE STATEMENT
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def create_statement(
        db: Session,
        entity_id: UUID,
        bank_account_id: UUID,
        statement_period_year: int,
        statement_period_month: int,
        statement_date: date,
        opening_balance: Decimal,
        closing_balance: Decimal,
        source: str = "manual",
        imported_by: str = None,
    ) -> dict:
        """Buat header bank statement (rekening koran)."""
        existing = db.execute(
            text("""
                SELECT id FROM bank_statement
                WHERE bank_account_id = :ba_id
                  AND statement_period_year  = :yr
                  AND statement_period_month = :mo
            """),
            {"ba_id": str(bank_account_id), "yr": statement_period_year, "mo": statement_period_month},
        ).fetchone()
        if existing:
            raise ValueError(
                f"Statement untuk periode {statement_period_year}-{statement_period_month:02d} "
                "sudah ada."
            )

        row = db.execute(
            text("""
                INSERT INTO bank_statement (
                    entity_id, bank_account_id,
                    statement_period_year, statement_period_month,
                    statement_date, opening_balance, closing_balance,
                    source, status, imported_by
                ) VALUES (
                    :eid, :ba_id,
                    :yr, :mo,
                    :stmt_date, :ob, :cb,
                    :source, 'draft', :imported_by
                ) RETURNING id, status
            """),
            {
                "eid": str(entity_id),
                "ba_id": str(bank_account_id),
                "yr": statement_period_year,
                "mo": statement_period_month,
                "stmt_date": statement_date,
                "ob": float(opening_balance),
                "cb": float(closing_balance),
                "source": source,
                "imported_by": imported_by,
            },
        ).fetchone()
        db.commit()
        return {"statement_id": str(row.id), "status": row.status}

    # ─────────────────────────────────────────────────────────────────────────
    # 2. IMPORT LINES
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def import_lines(
        db: Session,
        statement_id: UUID,
        lines: list[dict],
    ) -> dict:
        """
        Bulk insert baris bank statement.
        lines: list of {transaction_date, description, reference_no, debit_amount, credit_amount, running_balance}
        """
        stmt_row = db.execute(
            text("SELECT entity_id, bank_account_id, status FROM bank_statement WHERE id = :sid"),
            {"sid": str(statement_id)},
        ).fetchone()
        if not stmt_row:
            raise ValueError("Statement tidak ditemukan.")
        if stmt_row.status == "locked":
            raise ValueError("Statement sudah terkunci, tidak bisa menambah baris.")

        # hapus baris lama jika re-import
        db.execute(
            text("DELETE FROM bank_statement_line WHERE statement_id = :sid"),
            {"sid": str(statement_id)},
        )

        inserted = 0
        total_debit  = Decimal("0")
        total_credit = Decimal("0")

        for i, line in enumerate(lines, start=1):
            debit  = Decimal(str(line.get("debit_amount",  0) or 0))
            credit = Decimal(str(line.get("credit_amount", 0) or 0))
            total_debit  += debit
            total_credit += credit

            db.execute(
                text("""
                    INSERT INTO bank_statement_line (
                        statement_id, entity_id, bank_account_id,
                        line_no, transaction_date, value_date,
                        description, reference_no,
                        debit_amount, credit_amount, running_balance,
                        match_status
                    ) VALUES (
                        :sid, :eid, :ba_id,
                        :line_no, :txn_date, :val_date,
                        :desc, :ref,
                        :debit, :credit, :balance,
                        'unmatched'
                    )
                """),
                {
                    "sid":      str(statement_id),
                    "eid":      str(stmt_row.entity_id),
                    "ba_id":    str(stmt_row.bank_account_id),
                    "line_no":  i,
                    "txn_date": line["transaction_date"],
                    "val_date": line.get("value_date"),
                    "desc":     line["description"],
                    "ref":      line.get("reference_no"),
                    "debit":    float(debit),
                    "credit":   float(credit),
                    "balance":  float(line.get("running_balance") or 0),
                },
            )
            inserted += 1

        # update total di header
        db.execute(
            text("""
                UPDATE bank_statement
                SET total_debit  = :td,
                    total_credit = :tc,
                    status       = 'in_progress'
                WHERE id = :sid
            """),
            {"td": float(total_debit), "tc": float(total_credit), "sid": str(statement_id)},
        )
        db.commit()
        return {"inserted": inserted, "total_debit": float(total_debit), "total_credit": float(total_credit)}

    # ─────────────────────────────────────────────────────────────────────────
    # 3. AUTO-MATCH
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def run_auto_match(db: Session, statement_id: UUID) -> dict:
        """
        Algoritma auto-matching:
          Untuk setiap bank line 'unmatched':
            1. Cari GL entries pada akun bank yang sama dengan:
               - amount sama (debit/credit sisi berlawanan, toleransi ±AMOUNT_TOLERANCE)
               - journal_date dalam window ± DATE_WINDOW_DAYS
               - (opsional) reference_no match
            2. Jika tepat 1 kandidat → status='matched', insert recon_match
            3. Jika >1 kandidat → status='suggested' (user pilih mana)
        """
        stmt_row = db.execute(
            text("""
                SELECT bs.entity_id, bs.bank_account_id, ba.gl_account_code
                FROM bank_statement bs
                JOIN bank_account ba ON ba.id = bs.bank_account_id
                WHERE bs.id = :sid
            """),
            {"sid": str(statement_id)},
        ).fetchone()
        if not stmt_row:
            raise ValueError("Statement tidak ditemukan.")
        if not stmt_row.gl_account_code:
            raise ValueError(
                "GL account code belum dikonfigurasi pada bank account. "
                "Set bank_account.gl_account_code terlebih dahulu."
            )

        entity_id       = str(stmt_row.entity_id)
        bank_account_id = str(stmt_row.bank_account_id)
        gl_account_code = stmt_row.gl_account_code

        bank_lines = db.execute(
            text("""
                SELECT id, transaction_date, debit_amount, credit_amount, reference_no
                FROM bank_statement_line
                WHERE statement_id = :sid AND match_status = 'unmatched'
                ORDER BY line_no
            """),
            {"sid": str(statement_id)},
        ).fetchall()

        matched_count   = 0
        suggested_count = 0
        tol = float(BankReconEngine.AMOUNT_TOLERANCE)
        window = BankReconEngine.DATE_WINDOW_DAYS

        for bl in bank_lines:
            # sisi bank: debit bank = uang keluar → GL credit pada akun bank
            #            credit bank = uang masuk  → GL debit  pada akun bank
            if bl.credit_amount > 0:
                gl_side_col = "gl.debit_idr"
                bank_amount = float(bl.credit_amount)
            else:
                gl_side_col = "gl.credit_idr"
                bank_amount = float(bl.debit_amount)

            date_from = bl.transaction_date - timedelta(days=window)
            date_to   = bl.transaction_date + timedelta(days=window)

            candidates = db.execute(
                text(f"""
                    SELECT gl.id AS gl_line_id, {gl_side_col} AS amount,
                           gj.reference_no, gj.journal_date
                    FROM gl_line gl
                    JOIN gl_journal gj    ON gj.id = gl.journal_id
                                        AND gj.status = 'posted'
                                        AND gj.entity_id = :eid
                    JOIN chart_of_accounts coa ON coa.id = gl.account_id
                                              AND coa.account_code = :acc_code
                    LEFT JOIN gl_recon_status grs ON grs.gl_line_id = gl.id
                    WHERE gj.journal_date BETWEEN :d_from AND :d_to
                      AND {gl_side_col} BETWEEN :amt_low AND :amt_high
                      AND COALESCE(grs.match_status, 'unmatched') = 'unmatched'
                    ORDER BY
                        ABS({gl_side_col} - :amt) ASC,
                        ABS(gj.journal_date - :txn_date) ASC
                    LIMIT 5
                """),
                {
                    "eid":      entity_id,
                    "acc_code": gl_account_code,
                    "d_from":   date_from,
                    "d_to":     date_to,
                    "amt":      bank_amount,
                    "amt_low":  bank_amount - tol,
                    "amt_high": bank_amount + tol,
                    "txn_date": bl.transaction_date,
                },
            ).fetchall()

            if not candidates:
                continue

            # Cek apakah reference_no match untuk prefer exact reference
            ref_matched = [
                c for c in candidates
                if bl.reference_no and c.reference_no and bl.reference_no in c.reference_no
            ]
            best_candidates = ref_matched if ref_matched else candidates

            if len(best_candidates) == 1:
                # Match langsung
                cand = best_candidates[0]
                diff = abs(bank_amount - float(cand.amount))
                db.execute(
                    text("""
                        INSERT INTO recon_match (statement_id, bank_line_id, gl_line_id,
                                                  match_type, amount, difference, matched_by)
                        VALUES (:sid, :bl_id, :gl_id, 'auto', :amt, :diff, 'system-auto')
                        ON CONFLICT (bank_line_id, gl_line_id) DO NOTHING
                    """),
                    {
                        "sid":   str(statement_id),
                        "bl_id": str(bl.id),
                        "gl_id": str(cand.gl_line_id),
                        "amt":   bank_amount,
                        "diff":  diff,
                    },
                )
                db.execute(
                    text("UPDATE bank_statement_line SET match_status='matched' WHERE id=:id"),
                    {"id": str(bl.id)},
                )
                # Upsert gl_recon_status
                BankReconEngine._upsert_gl_recon(
                    db, entity_id, str(cand.gl_line_id), bank_account_id,
                    str(statement_id), "matched"
                )
                matched_count += 1
            else:
                # Beberapa kandidat — tandai suggested
                db.execute(
                    text("UPDATE bank_statement_line SET match_status='suggested' WHERE id=:id"),
                    {"id": str(bl.id)},
                )
                suggested_count += 1

        db.commit()
        return {
            "auto_matched":       matched_count,
            "needs_confirmation": suggested_count,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 4. CONFIRM SUGGESTION
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def confirm_suggestion(
        db: Session,
        statement_id: UUID,
        bank_line_id: UUID,
        gl_line_id: UUID,
        confirmed_by: str,
    ) -> dict:
        """User memilih salah satu kandidat suggested untuk dikonfirmasi."""
        bl = db.execute(
            text("""
                SELECT bsl.*, bs.bank_account_id
                FROM bank_statement_line bsl
                JOIN bank_statement bs ON bs.id = bsl.statement_id
                WHERE bsl.id = :bl_id AND bsl.statement_id = :sid
            """),
            {"bl_id": str(bank_line_id), "sid": str(statement_id)},
        ).fetchone()
        if not bl:
            raise ValueError("Bank line tidak ditemukan.")
        if bl.match_status not in ("suggested", "unmatched"):
            raise ValueError(f"Bank line sudah berstatus {bl.match_status}.")

        gl = db.execute(
            text("SELECT debit_idr, credit_idr FROM gl_line WHERE id = :id"),
            {"id": str(gl_line_id)},
        ).fetchone()
        if not gl:
            raise ValueError("GL line tidak ditemukan.")

        bank_amount = float(bl.credit_amount or bl.debit_amount)
        gl_amount   = float(gl.debit_idr or gl.credit_idr)
        diff        = abs(bank_amount - gl_amount)

        db.execute(
            text("""
                INSERT INTO recon_match (statement_id, bank_line_id, gl_line_id,
                                         match_type, amount, difference, matched_by)
                VALUES (:sid, :bl_id, :gl_id, 'suggested_confirmed', :amt, :diff, :by)
                ON CONFLICT (bank_line_id, gl_line_id) DO NOTHING
            """),
            {
                "sid":   str(statement_id), "bl_id": str(bank_line_id),
                "gl_id": str(gl_line_id),   "amt":   bank_amount,
                "diff":  diff,              "by":    confirmed_by,
            },
        )
        db.execute(
            text("UPDATE bank_statement_line SET match_status='matched' WHERE id=:id"),
            {"id": str(bank_line_id)},
        )
        BankReconEngine._upsert_gl_recon(
            db, str(bl.entity_id), str(gl_line_id), str(bl.bank_account_id),
            str(statement_id), "matched"
        )
        db.commit()
        return {"status": "matched", "difference": diff}

    # ─────────────────────────────────────────────────────────────────────────
    # 5. MANUAL MATCH
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def create_manual_match(
        db: Session,
        statement_id: UUID,
        bank_line_id: UUID,
        gl_line_id: UUID,
        matched_by: str,
    ) -> dict:
        """Cocokkan satu bank line ke satu GL line secara manual."""
        bl = db.execute(
            text("""
                SELECT bsl.*, bs.bank_account_id, bs.entity_id
                FROM bank_statement_line bsl
                JOIN bank_statement bs ON bs.id = bsl.statement_id
                WHERE bsl.id = :bl_id AND bsl.statement_id = :sid
            """),
            {"bl_id": str(bank_line_id), "sid": str(statement_id)},
        ).fetchone()
        if not bl:
            raise ValueError("Bank line tidak ditemukan dalam statement ini.")
        if bl.match_status == "matched":
            raise ValueError("Bank line sudah di-match.")

        # cek GL line belum di-match
        gl_status = db.execute(
            text("""
                SELECT match_status FROM gl_recon_status WHERE gl_line_id = :id
            """),
            {"id": str(gl_line_id)},
        ).fetchone()
        if gl_status and gl_status.match_status == "matched":
            raise ValueError("GL line sudah di-match dengan transaksi lain.")

        gl = db.execute(
            text("SELECT debit_idr, credit_idr FROM gl_line WHERE id = :id"),
            {"id": str(gl_line_id)},
        ).fetchone()
        if not gl:
            raise ValueError("GL line tidak ditemukan.")

        bank_amount = float((bl.credit_amount or 0) + (bl.debit_amount or 0))
        gl_amount   = float((gl.debit_idr or 0) + (gl.credit_idr or 0))
        diff        = abs(bank_amount - gl_amount)

        db.execute(
            text("""
                INSERT INTO recon_match (statement_id, bank_line_id, gl_line_id,
                                         match_type, amount, difference, matched_by)
                VALUES (:sid, :bl_id, :gl_id, 'manual', :amt, :diff, :by)
                ON CONFLICT (bank_line_id, gl_line_id) DO NOTHING
            """),
            {
                "sid":   str(statement_id), "bl_id": str(bank_line_id),
                "gl_id": str(gl_line_id),   "amt":   bank_amount,
                "diff":  diff,              "by":    matched_by,
            },
        )
        db.execute(
            text("UPDATE bank_statement_line SET match_status='matched' WHERE id=:id"),
            {"id": str(bank_line_id)},
        )
        BankReconEngine._upsert_gl_recon(
            db, str(bl.entity_id), str(gl_line_id), str(bl.bank_account_id),
            str(statement_id), "matched"
        )
        db.commit()
        return {"status": "matched", "difference": diff}

    # ─────────────────────────────────────────────────────────────────────────
    # 6. CREATE ADJUSTMENT
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def create_adjustment(
        db: Session,
        statement_id: UUID,
        adjustment_type: str,
        source: str,
        description: str,
        amount: Decimal,
        bank_line_id: Optional[UUID] = None,
        gl_line_id: Optional[UUID] = None,
        # Untuk bank_only: buat jurnal penyesuaian otomatis
        debit_account_code: Optional[str] = None,
        credit_account_code: Optional[str] = None,
        created_by: str = None,
    ) -> dict:
        """
        Catat item yang tidak bisa di-match:
          - bank_only : ada di bank tapi belum di GL (biaya admin bank, bunga)
            → buat GL journal otomatis (Dr. expense / Cr. akun bank)
          - gl_only   : ada di GL tapi belum di bank (cek beredar, transfer in-transit)
            → cukup catat sebagai outstanding item
          - timing_diff: beda waktu
        """
        stmt_row = db.execute(
            text("""
                SELECT entity_id, bank_account_id, ba.gl_account_code, status
                FROM bank_statement bs
                JOIN bank_account ba ON ba.id = bs.bank_account_id
                WHERE bs.id = :sid
            """),
            {"sid": str(statement_id)},
        ).fetchone()
        if not stmt_row:
            raise ValueError("Statement tidak ditemukan.")
        if stmt_row.status == "locked":
            raise ValueError("Statement sudah terkunci.")

        adj_journal_id = None

        if adjustment_type == "bank_only" and debit_account_code and credit_account_code:
            # Buat jurnal penyesuaian
            journal_row = db.execute(
                text("""
                    INSERT INTO gl_journal (entity_id, journal_date, description,
                                            journal_type, status, created_by)
                    VALUES (:eid, CURRENT_DATE, :desc, 'general', 'posted', :by)
                    RETURNING id
                """),
                {
                    "eid":  str(stmt_row.entity_id),
                    "desc": f"Bank adj: {description}",
                    "by":   created_by,
                },
            ).fetchone()
            adj_journal_id = str(journal_row.id)

            # Ambil account ids
            debit_acc = db.execute(
                text("SELECT id FROM chart_of_accounts WHERE account_code=:c AND entity_id=:e"),
                {"c": debit_account_code, "e": str(stmt_row.entity_id)},
            ).fetchone()
            credit_acc = db.execute(
                text("SELECT id FROM chart_of_accounts WHERE account_code=:c AND entity_id=:e"),
                {"c": credit_account_code, "e": str(stmt_row.entity_id)},
            ).fetchone()
            if not debit_acc or not credit_acc:
                raise ValueError("Account code tidak ditemukan dalam CoA entity ini.")

            db.execute(
                text("""
                    INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr)
                    VALUES
                      (:jid, :d_acc, :desc, :amt, 0),
                      (:jid, :c_acc, :desc, 0, :amt)
                """),
                {
                    "jid":   adj_journal_id,
                    "d_acc": str(debit_acc.id),
                    "c_acc": str(credit_acc.id),
                    "desc":  description,
                    "amt":   float(amount),
                },
            )

        # Insert adjustment record
        adj_row = db.execute(
            text("""
                INSERT INTO recon_adjustment (
                    statement_id, adjustment_type, source,
                    bank_line_id, gl_line_id,
                    description, amount, adjustment_journal_id, created_by
                ) VALUES (
                    :sid, :adj_type, :source,
                    :bl_id, :gl_id,
                    :desc, :amt, :jid, :by
                ) RETURNING id
            """),
            {
                "sid":     str(statement_id),
                "adj_type": adjustment_type,
                "source":  source,
                "bl_id":   str(bank_line_id) if bank_line_id else None,
                "gl_id":   str(gl_line_id)   if gl_line_id   else None,
                "desc":    description,
                "amt":     float(amount),
                "jid":     adj_journal_id,
                "by":      created_by,
            },
        ).fetchone()

        # Update bank line status jika ada
        if bank_line_id:
            db.execute(
                text("UPDATE bank_statement_line SET match_status='adjusted' WHERE id=:id"),
                {"id": str(bank_line_id)},
            )
        if gl_line_id:
            BankReconEngine._upsert_gl_recon(
                db, str(stmt_row.entity_id), str(gl_line_id), str(stmt_row.bank_account_id),
                str(statement_id), "adjusted"
            )

        db.commit()
        return {
            "adjustment_id":  str(adj_row.id),
            "journal_created": adj_journal_id is not None,
            "journal_id":     adj_journal_id,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 7. FINALIZE RECONCILIATION
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def finalize_reconciliation(
        db: Session,
        statement_id: UUID,
        finalized_by: str,
    ) -> dict:
        """
        Validasi dan lock statement rekonsiliasi.
        Pengecekan:
          - Semua baris harus matched atau adjusted (tidak boleh ada 'unmatched')
          - GL balance check: opening + total GL movements = closing (bank statement)
        """
        stmt_row = db.execute(
            text("""
                SELECT bs.*, ba.gl_account_code, ba.account_name
                FROM bank_statement bs
                JOIN bank_account ba ON ba.id = bs.bank_account_id
                WHERE bs.id = :sid
            """),
            {"sid": str(statement_id)},
        ).fetchone()
        if not stmt_row:
            raise ValueError("Statement tidak ditemukan.")
        if stmt_row.status == "locked":
            raise ValueError("Statement sudah dikunci sebelumnya.")

        # Cek sisa unmatched/suggested
        pending = db.execute(
            text("""
                SELECT COUNT(*) AS cnt FROM bank_statement_line
                WHERE statement_id = :sid
                  AND match_status IN ('unmatched','suggested')
            """),
            {"sid": str(statement_id)},
        ).fetchone()
        if pending.cnt > 0:
            raise ValueError(
                f"Masih ada {pending.cnt} baris bank statement yang belum dicocokkan. "
                "Selesaikan semua sebelum finalisasi."
            )

        # Kalkulasi rekonsiliasi
        matched_total_credit = db.execute(
            text("""
                SELECT COALESCE(SUM(credit_amount), 0) AS total
                FROM bank_statement_line
                WHERE statement_id = :sid AND match_status = 'matched'
            """),
            {"sid": str(statement_id)},
        ).fetchone().total

        matched_total_debit = db.execute(
            text("""
                SELECT COALESCE(SUM(debit_amount), 0) AS total
                FROM bank_statement_line
                WHERE statement_id = :sid AND match_status = 'matched'
            """),
            {"sid": str(statement_id)},
        ).fetchone().total

        adj_amount = db.execute(
            text("""
                SELECT COALESCE(SUM(amount), 0) AS total
                FROM recon_adjustment
                WHERE statement_id = :sid AND adjustment_type = 'bank_only'
            """),
            {"sid": str(statement_id)},
        ).fetchone().total

        computed_closing = (
            Decimal(str(stmt_row.opening_balance))
            + Decimal(str(matched_total_credit))
            - Decimal(str(matched_total_debit))
            + Decimal(str(adj_amount))
        )
        difference = abs(computed_closing - Decimal(str(stmt_row.closing_balance)))

        # Toleransi Rp 1 untuk pembulatan
        if difference > Decimal("1.00"):
            return {
                "status":           "validation_failed",
                "message":          f"Selisih saldo rekonsiliasi = {difference:,.2f}. Periksa kembali.",
                "computed_closing": float(computed_closing),
                "statement_closing": float(stmt_row.closing_balance),
                "difference":       float(difference),
            }

        # Lock statement
        db.execute(
            text("""
                UPDATE bank_statement
                SET status = 'locked', imported_by = :by
                WHERE id = :sid
            """),
            {"by": finalized_by, "sid": str(statement_id)},
        )
        db.commit()

        return {
            "status":           "locked",
            "computed_closing": float(computed_closing),
            "statement_closing": float(stmt_row.closing_balance),
            "difference":       float(difference),
            "message":          "Rekonsiliasi berhasil dikunci.",
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 8. GET REPORT
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_report(db: Session, statement_id: UUID) -> dict:
        """
        Laporan rekonsiliasi lengkap — format standar bank recon:
          Saldo per Bank Statement
          (+) Deposit in transit (GL dr, belum muncul di bank)
          (-) Outstanding checks (GL cr, belum muncul di bank)
          = Adjusted Bank Balance

          Saldo per GL (akun bank)
          (+) Note collections / Bunga kredit (bank_only credit)
          (-) Bank charges / Admin (bank_only debit)
          = Adjusted GL Balance

          Selisih (harus = 0)
        """
        stmt = db.execute(
            text("""
                SELECT bs.*, ba.account_name, ba.account_number, ba.gl_account_code
                FROM bank_statement bs
                JOIN bank_account ba ON ba.id = bs.bank_account_id
                WHERE bs.id = :sid
            """),
            {"sid": str(statement_id)},
        ).fetchone()
        if not stmt:
            raise ValueError("Statement tidak ditemukan.")

        # Ringkasan match
        summary = db.execute(
            text("SELECT * FROM vw_recon_summary WHERE statement_id = :sid"),
            {"sid": str(statement_id)},
        ).fetchone()

        # Items bank_only (bunga/biaya yg belum di GL → sudah ada adj journal)
        bank_only = db.execute(
            text("""
                SELECT ra.description, ra.amount, ra.source,
                       ra.adjustment_journal_id IS NOT NULL AS has_journal
                FROM recon_adjustment ra
                WHERE ra.statement_id = :sid AND ra.adjustment_type = 'bank_only'
                ORDER BY ra.created_at
            """),
            {"sid": str(statement_id)},
        ).fetchall()

        # Items gl_only (outstanding checks, deposit in transit)
        gl_only = db.execute(
            text("""
                SELECT ra.description, ra.amount, ra.source
                FROM recon_adjustment ra
                WHERE ra.statement_id = :sid AND ra.adjustment_type IN ('gl_only','timing_diff')
                ORDER BY ra.created_at
            """),
            {"sid": str(statement_id)},
        ).fetchall()

        # Unmatched bank lines (jika ada)
        unmatched_bank = db.execute(
            text("""
                SELECT line_no, transaction_date, description, reference_no,
                       debit_amount, credit_amount, match_status
                FROM bank_statement_line
                WHERE statement_id = :sid AND match_status IN ('unmatched','suggested')
                ORDER BY line_no
            """),
            {"sid": str(statement_id)},
        ).fetchall()

        # GL saldo per akun bank
        gl_balance = db.execute(
            text("""
                SELECT
                    COALESCE(SUM(gl.debit_idr), 0) - COALESCE(SUM(gl.credit_idr), 0) AS net_balance
                FROM gl_line gl
                JOIN gl_journal gj          ON gj.id = gl.journal_id AND gj.status = 'posted'
                JOIN chart_of_accounts coa  ON coa.id = gl.account_id
                                           AND coa.account_code = :acc_code
                WHERE gj.entity_id = :eid
                  AND EXTRACT(YEAR  FROM gj.journal_date) <= :yr
                  AND (EXTRACT(YEAR  FROM gj.journal_date) < :yr
                       OR EXTRACT(MONTH FROM gj.journal_date) <= :mo)
            """),
            {
                "acc_code": stmt.gl_account_code,
                "eid":      str(stmt.entity_id),
                "yr":       stmt.statement_period_year,
                "mo":       stmt.statement_period_month,
            },
        ).fetchone()

        return {
            "statement": {
                "id":             str(stmt.id),
                "bank_account":   stmt.account_name,
                "account_number": stmt.account_number,
                "period":         f"{stmt.statement_period_year}-{stmt.statement_period_month:02d}",
                "statement_date": stmt.statement_date.isoformat(),
                "status":         stmt.status,
                "opening_balance": float(stmt.opening_balance),
                "closing_balance": float(stmt.closing_balance),
            },
            "summary": {
                "total_lines":     int(summary.total_lines    or 0),
                "matched_lines":   int(summary.matched_lines  or 0),
                "unmatched_lines": int(summary.unmatched_lines or 0),
                "adjusted_lines":  int(summary.adjusted_lines or 0),
                "completion_pct":  float(summary.completion_pct or 0),
            },
            "bank_only_adjustments": [
                {
                    "description": r.description,
                    "amount":      float(r.amount),
                    "has_journal": r.has_journal,
                }
                for r in bank_only
            ],
            "outstanding_items": [
                {
                    "description": r.description,
                    "amount":      float(r.amount),
                    "source":      r.source,
                }
                for r in gl_only
            ],
            "unmatched_bank_lines": [
                {
                    "line_no":          r.line_no,
                    "transaction_date": r.transaction_date.isoformat(),
                    "description":      r.description,
                    "reference_no":     r.reference_no,
                    "debit_amount":     float(r.debit_amount),
                    "credit_amount":    float(r.credit_amount),
                    "match_status":     r.match_status,
                }
                for r in unmatched_bank
            ],
            "gl_balance_ytd": float(gl_balance.net_balance or 0),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # GET SUGGESTIONS — kandidat GL untuk satu bank line
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_suggestions(
        db: Session,
        statement_id: UUID,
        bank_line_id: UUID,
    ) -> list[dict]:
        """Kembalikan daftar GL candidates untuk bank line ber-status 'suggested'."""
        bl = db.execute(
            text("""
                SELECT bsl.*, bs.bank_account_id, ba.gl_account_code, bs.entity_id
                FROM bank_statement_line bsl
                JOIN bank_statement bs ON bs.id = bsl.statement_id
                JOIN bank_account ba   ON ba.id = bs.bank_account_id
                WHERE bsl.id = :bl_id AND bsl.statement_id = :sid
            """),
            {"bl_id": str(bank_line_id), "sid": str(statement_id)},
        ).fetchone()
        if not bl:
            raise ValueError("Bank line tidak ditemukan.")

        bank_amount = float(bl.credit_amount or bl.debit_amount)
        gl_side_col = "gl.debit_idr" if bl.credit_amount > 0 else "gl.credit_idr"
        tol         = float(BankReconEngine.AMOUNT_TOLERANCE)
        window      = BankReconEngine.DATE_WINDOW_DAYS
        date_from   = bl.transaction_date - timedelta(days=window)
        date_to     = bl.transaction_date + timedelta(days=window)

        rows = db.execute(
            text(f"""
                SELECT gl.id AS gl_line_id, gj.journal_date, gj.reference_no,
                       gj.description, {gl_side_col} AS amount
                FROM gl_line gl
                JOIN gl_journal gj    ON gj.id = gl.journal_id
                                    AND gj.status = 'posted'
                                    AND gj.entity_id = :eid
                JOIN chart_of_accounts coa ON coa.id = gl.account_id
                                          AND coa.account_code = :acc_code
                LEFT JOIN gl_recon_status grs ON grs.gl_line_id = gl.id
                WHERE gj.journal_date BETWEEN :d_from AND :d_to
                  AND {gl_side_col} BETWEEN :amt_low AND :amt_high
                  AND COALESCE(grs.match_status, 'unmatched') = 'unmatched'
                ORDER BY ABS({gl_side_col} - :amt) ASC
                LIMIT 10
            """),
            {
                "eid":      str(bl.entity_id),
                "acc_code": bl.gl_account_code,
                "d_from":   date_from,
                "d_to":     date_to,
                "amt":      bank_amount,
                "amt_low":  bank_amount - tol,
                "amt_high": bank_amount + tol,
            },
        ).fetchall()

        return [
            {
                "gl_line_id":    str(r.gl_line_id),
                "journal_date":  r.journal_date.isoformat(),
                "reference_no":  r.reference_no,
                "description":   r.description,
                "amount":        float(r.amount),
                "difference":    abs(bank_amount - float(r.amount)),
            }
            for r in rows
        ]

    # ─────────────────────────────────────────────────────────────────────────
    # UNMATCH — batalkan match yang sudah ada
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def unmatch(
        db: Session,
        statement_id: UUID,
        bank_line_id: UUID,
    ) -> dict:
        """Batalkan match untuk bank line tertentu (kecuali statement sudah locked)."""
        stmt = db.execute(
            text("SELECT status FROM bank_statement WHERE id = :sid"),
            {"sid": str(statement_id)},
        ).fetchone()
        if not stmt:
            raise ValueError("Statement tidak ditemukan.")
        if stmt.status == "locked":
            raise ValueError("Statement sudah dikunci, tidak bisa unmatch.")

        match_rows = db.execute(
            text("""
                SELECT gl_line_id FROM recon_match
                WHERE statement_id = :sid AND bank_line_id = :bl_id
            """),
            {"sid": str(statement_id), "bl_id": str(bank_line_id)},
        ).fetchall()

        for mr in match_rows:
            db.execute(
                text("""
                    UPDATE gl_recon_status SET match_status = 'unmatched'
                    WHERE gl_line_id = :gl_id
                """),
                {"gl_id": str(mr.gl_line_id)},
            )

        db.execute(
            text("DELETE FROM recon_match WHERE statement_id=:sid AND bank_line_id=:bl_id"),
            {"sid": str(statement_id), "bl_id": str(bank_line_id)},
        )
        db.execute(
            text("UPDATE bank_statement_line SET match_status='unmatched' WHERE id=:id"),
            {"id": str(bank_line_id)},
        )
        db.commit()
        return {"unmatched": len(match_rows)}

    # ─────────────────────────────────────────────────────────────────────────
    # HELPER INTERNAL
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _upsert_gl_recon(
        db: Session,
        entity_id: str,
        gl_line_id: str,
        bank_account_id: str,
        statement_id: str,
        match_status: str,
    ) -> None:
        db.execute(
            text("""
                INSERT INTO gl_recon_status
                    (entity_id, gl_line_id, bank_account_id, statement_id, match_status)
                VALUES (:eid, :gl_id, :ba_id, :sid, :status)
                ON CONFLICT (gl_line_id)
                DO UPDATE SET match_status = :status, statement_id = :sid
            """),
            {
                "eid":    entity_id,
                "gl_id":  gl_line_id,
                "ba_id":  bank_account_id,
                "sid":    statement_id,
                "status": match_status,
            },
        )
