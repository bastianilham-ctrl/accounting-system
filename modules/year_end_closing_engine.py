"""
Year-End Closing Engine

Langkah tutup buku:
  1. setup_fiscal_year()       → init fiscal_year + 12 fiscal_period records
  2. run_pre_closing_checks()  → validasi semua prasyarat (10 checks)
  3. run_income_summary()      → jurnal penutup revenue & expense → Ikhtisar L/R
  4. run_re_transfer()         → pindahkan Ikhtisar L/R → Laba Ditahan (RE)
  5. lock_periods()            → lock semua fiscal_period tahun tsb
  6. close_fiscal_year()       → jalankan semua langkah sekaligus (orchestrator)
  7. reopen_period()           → buka kembali satu periode jika diperlukan (admin only)
  8. get_closing_status()      → ringkasan status penutupan
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


# Akun GL untuk closing — bisa dikonfigurasi per entity via COA
INCOME_SUMMARY_CODE = "3-0100"   # Ikhtisar Laba Rugi (clearing)
RETAINED_EARNINGS_CODE = "3-0200"  # Laba Ditahan / Retained Earnings


class YearEndClosingEngine:

    # ─────────────────────────────────────────────────────────────────────────
    # 1. SETUP FISCAL YEAR
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def setup_fiscal_year(
        db: Session,
        entity_id: UUID,
        fiscal_year: int,
        start_month: int = 1,
    ) -> dict:
        """
        Buat fiscal_year + 12 fiscal_period.
        start_month: 1 = Jan-Dec (default), 4 = Apr-Mar (beberapa perusahaan asing).
        """
        existing = db.execute(
            text("SELECT id FROM fiscal_year WHERE entity_id=:eid AND fiscal_year=:yr"),
            {"eid": str(entity_id), "yr": fiscal_year},
        ).fetchone()
        if existing:
            raise ValueError(f"Fiscal year {fiscal_year} sudah ada.")

        # Hitung start/end berdasarkan start_month
        if start_month == 1:
            start_date = date(fiscal_year, 1, 1)
            end_date   = date(fiscal_year, 12, 31)
        else:
            start_date = date(fiscal_year, start_month, 1)
            import calendar
            end_year  = fiscal_year + 1
            end_month = start_month - 1
            end_day   = calendar.monthrange(end_year, end_month)[1]
            end_date  = date(end_year, end_month, end_day)

        fy_row = db.execute(
            text("""
                INSERT INTO fiscal_year (entity_id, fiscal_year, start_date, end_date, status)
                VALUES (:eid, :yr, :sd, :ed, 'open')
                RETURNING id
            """),
            {"eid": str(entity_id), "yr": fiscal_year, "sd": start_date, "ed": end_date},
        ).fetchone()
        fy_id = str(fy_row.id)

        import calendar as cal
        created_periods = 0
        for i in range(12):
            mo    = ((start_month - 1 + i) % 12) + 1
            yr    = fiscal_year if mo >= start_month else fiscal_year + 1
            m_end = cal.monthrange(yr, mo)[1]

            existing_p = db.execute(
                text("SELECT id FROM fiscal_period WHERE entity_id=:eid AND period_year=:yr AND period_month=:mo"),
                {"eid": str(entity_id), "yr": yr, "mo": mo},
            ).fetchone()
            if existing_p:
                continue

            db.execute(
                text("""
                    INSERT INTO fiscal_period (
                        entity_id, fiscal_year_id, period_year, period_month,
                        start_date, end_date, status
                    ) VALUES (
                        :eid, :fyid, :yr, :mo, :sd, :ed, 'open'
                    )
                """),
                {
                    "eid":  str(entity_id), "fyid": fy_id,
                    "yr":   yr, "mo":  mo,
                    "sd":   date(yr, mo, 1),
                    "ed":   date(yr, mo, m_end),
                },
            )
            created_periods += 1

        db.commit()
        return {
            "fiscal_year_id":   fy_id,
            "fiscal_year":      fiscal_year,
            "start_date":       start_date.isoformat(),
            "end_date":         end_date.isoformat(),
            "periods_created":  created_periods,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 2. PRE-CLOSING CHECKS
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def run_pre_closing_checks(
        db: Session,
        entity_id: UUID,
        fiscal_year: int,
    ) -> dict:
        """
        Menjalankan 10 pre-closing checks. Setiap check menghasilkan pass/warn/fail.
        Semua harus pass/warn sebelum income_summary boleh dijalankan.
        """
        eid = str(entity_id)
        yr  = fiscal_year
        checks = []

        def _check(name: str, query: str, params: dict, pass_if_zero: bool = True, warn_threshold: int = 0) -> dict:
            count = db.execute(text(query), params).fetchone()[0]
            if pass_if_zero:
                result = "pass" if count == 0 else ("warn" if count <= warn_threshold else "fail")
            else:
                result = "pass" if count > 0 else "fail"
            return {"check_item": name, "count": count, "check_result": result}

        # 1. Draft journals in year
        checks.append(_check(
            "no_draft_journals",
            """SELECT COUNT(*) FROM gl_journal
               WHERE entity_id=:eid AND status='draft'
                 AND EXTRACT(YEAR FROM journal_date)=:yr""",
            {"eid": eid, "yr": yr},
        ))

        # 2. Unposted journals
        checks.append(_check(
            "no_unposted_journals",
            """SELECT COUNT(*) FROM gl_journal
               WHERE entity_id=:eid AND status NOT IN ('posted','reversed','void')
                 AND EXTRACT(YEAR FROM journal_date)=:yr""",
            {"eid": eid, "yr": yr},
        ))

        # 3. AP invoices belum lunas
        checks.append(_check(
            "ap_outstanding_check",
            """SELECT COUNT(*) FROM ap_invoice
               WHERE entity_id=:eid AND status NOT IN ('paid','cancelled')
                 AND EXTRACT(YEAR FROM invoice_date)=:yr""",
            {"eid": eid, "yr": yr},
            pass_if_zero=True, warn_threshold=5,
        ))

        # 4. AR invoices belum lunas
        checks.append(_check(
            "ar_outstanding_check",
            """SELECT COUNT(*) FROM ar_invoice
               WHERE entity_id=:eid AND status NOT IN ('paid','cancelled')
                 AND EXTRACT(YEAR FROM invoice_date)=:yr""",
            {"eid": eid, "yr": yr},
            pass_if_zero=True, warn_threshold=5,
        ))

        # 5. Depresiasi sudah diposting
        dep_row = db.execute(
            text("""
                SELECT COUNT(*) FROM asset_depreciation_schedule ads
                JOIN fixed_asset fa ON fa.id = ads.asset_id AND fa.entity_id=:eid
                WHERE EXTRACT(YEAR FROM ads.period_date)=:yr AND ads.is_posted=FALSE
            """),
            {"eid": eid, "yr": yr},
        ).fetchone()[0]
        checks.append({
            "check_item": "depreciation_posted",
            "count": dep_row,
            "check_result": "pass" if dep_row == 0 else "fail",
        })

        # 6. Bank reconciliation selesai bulan terakhir
        recon_row = db.execute(
            text("""
                SELECT COUNT(*) FROM bank_statement
                WHERE entity_id=:eid
                  AND statement_period_year=:yr
                  AND statement_period_month=12
                  AND status != 'locked'
            """),
            {"eid": eid, "yr": yr},
        ).fetchone()[0]
        checks.append({
            "check_item": "bank_recon_final",
            "count": recon_row,
            "check_result": "warn" if recon_row > 0 else "pass",
            "detail": "Bank statement Desember belum final" if recon_row > 0 else None,
        })

        # 7. Inventory adjustment belum diconfirm
        checks.append(_check(
            "inventory_adj_confirmed",
            """SELECT COUNT(*) FROM inventory_adjustment
               WHERE entity_id=:eid AND status='draft'
                 AND EXTRACT(YEAR FROM adjustment_date)=:yr""",
            {"eid": eid, "yr": yr},
        ))

        # 8. WHT SPT Masa bulan terakhir sudah disetor
        wht_row = db.execute(
            text("""
                SELECT COUNT(*) FROM wht_spt_masa
                WHERE entity_id=:eid AND period_year=:yr
                  AND status NOT IN ('paid','amended')
            """),
            {"eid": eid, "yr": yr},
        ).fetchone()[0]
        checks.append({
            "check_item": "wht_spt_paid",
            "count": wht_row,
            "check_result": "warn" if wht_row > 0 else "pass",
        })

        # 9. Income Summary account exists
        is_acc = db.execute(
            text("SELECT COUNT(*) FROM chart_of_accounts WHERE entity_id=:eid AND account_code=:code"),
            {"eid": eid, "code": INCOME_SUMMARY_CODE},
        ).fetchone()[0]
        checks.append({
            "check_item": "income_summary_account",
            "count": is_acc,
            "check_result": "pass" if is_acc > 0 else "fail",
            "detail": f"Akun {INCOME_SUMMARY_CODE} (Ikhtisar L/R) harus ada di CoA" if is_acc == 0 else None,
        })

        # 10. Retained Earnings account exists
        re_acc = db.execute(
            text("SELECT COUNT(*) FROM chart_of_accounts WHERE entity_id=:eid AND account_code=:code"),
            {"eid": eid, "code": RETAINED_EARNINGS_CODE},
        ).fetchone()[0]
        checks.append({
            "check_item": "retained_earnings_account",
            "count": re_acc,
            "check_result": "pass" if re_acc > 0 else "fail",
            "detail": f"Akun {RETAINED_EARNINGS_CODE} (Laba Ditahan) harus ada di CoA" if re_acc == 0 else None,
        })

        # Upsert ke closing_checklist
        for c in checks:
            db.execute(
                text("""
                    INSERT INTO closing_checklist
                        (entity_id, fiscal_year, check_item, check_result, detail, checked_at)
                    VALUES (:eid, :yr, :item, :result, :detail, NOW())
                    ON CONFLICT (entity_id, fiscal_year, check_item)
                    DO UPDATE SET check_result=:result, detail=:detail, checked_at=NOW()
                """),
                {
                    "eid":    eid, "yr":   yr,
                    "item":   c["check_item"],
                    "result": c["check_result"],
                    "detail": c.get("detail"),
                },
            )

        has_fail = any(c["check_result"] == "fail" for c in checks)
        db.execute(
            text("""
                INSERT INTO year_end_closing_log
                    (entity_id, fiscal_year, step, status, result_summary)
                VALUES (:eid, :yr, 'pre_check', :status, :summary::jsonb)
            """),
            {
                "eid":     eid, "yr": yr,
                "status":  "failed" if has_fail else "done",
                "summary": json.dumps({"checks": len(checks), "failed": sum(1 for c in checks if c["check_result"]=="fail")}),
            },
        )
        db.commit()

        return {
            "fiscal_year":  yr,
            "checks":       checks,
            "can_proceed":  not has_fail,
            "summary": {
                "pass":  sum(1 for c in checks if c["check_result"] == "pass"),
                "warn":  sum(1 for c in checks if c["check_result"] == "warn"),
                "fail":  sum(1 for c in checks if c["check_result"] == "fail"),
            },
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 3. INCOME SUMMARY — jurnal penutup
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def run_income_summary(
        db: Session,
        entity_id: UUID,
        fiscal_year: int,
        closing_date: date,
        closed_by: str,
        income_summary_account: Optional[str] = None,
    ) -> dict:
        """
        Menutup semua akun revenue & expense ke akun Ikhtisar Laba Rugi.
        Revenue normal credit → Dr. Revenue | Cr. Ikhtisar
        Expense normal debit  → Dr. Ikhtisar | Cr. Expense
        """
        eid      = str(entity_id)
        is_code  = income_summary_account or INCOME_SUMMARY_CODE

        fy = db.execute(
            text("SELECT id, status FROM fiscal_year WHERE entity_id=:eid AND fiscal_year=:yr"),
            {"eid": eid, "yr": fiscal_year},
        ).fetchone()
        if not fy:
            raise ValueError(f"Fiscal year {fiscal_year} belum di-setup. Jalankan setup_fiscal_year() dahulu.")
        if fy.status == "closed":
            raise ValueError(f"Fiscal year {fiscal_year} sudah ditutup.")

        # Ambil semua akun revenue & expense + saldo bersih
        balances = db.execute(
            text("""
                SELECT account_id, account_code, account_name, account_type,
                       normal_balance, net_balance
                FROM vw_income_expense_balance
                WHERE entity_id=:eid AND fiscal_year=:yr AND net_balance != 0
            """),
            {"eid": eid, "yr": fiscal_year},
        ).fetchall()

        if not balances:
            raise ValueError("Tidak ada saldo revenue/expense untuk ditutup.")

        def get_acc(code: str) -> str:
            r = db.execute(
                text("SELECT id FROM chart_of_accounts WHERE account_code=:c AND entity_id=:e"),
                {"c": code, "e": eid},
            ).fetchone()
            if not r:
                raise ValueError(f"Account '{code}' tidak ditemukan di CoA. Setup akun ini terlebih dahulu.")
            return str(r.id)

        is_acc_id = get_acc(is_code)
        net_to_is = Decimal("0")

        journal_row = db.execute(
            text("""
                INSERT INTO gl_journal (entity_id, journal_date, description,
                                        journal_type, reference_no, status, created_by)
                VALUES (:eid, :dt, :desc, 'closing', :ref, 'posted', :by)
                RETURNING id
            """),
            {
                "eid":  eid,
                "dt":   closing_date,
                "desc": f"Jurnal Penutup {fiscal_year} — Income Summary",
                "ref":  f"CLOSE-IS-{fiscal_year}",
                "by":   closed_by,
            },
        ).fetchone()
        journal_id = str(journal_row.id)

        for bal in balances:
            amt = abs(Decimal(str(bal.net_balance)))
            if amt == 0:
                continue

            acc_id = str(bal.account_id)

            if bal.account_type == "revenue":
                # Dr. Revenue | Cr. Ikhtisar
                db.execute(
                    text("""
                        INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr)
                        VALUES (:jid, :acc, :desc, :amt, 0)
                    """),
                    {"jid": journal_id, "acc": acc_id, "desc": f"Tutup {bal.account_name}", "amt": float(amt)},
                )
                net_to_is += amt   # net income ke Ikhtisar (credit)
            else:
                # Dr. Ikhtisar | Cr. Expense
                db.execute(
                    text("""
                        INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr)
                        VALUES (:jid, :acc, :desc, 0, :amt)
                    """),
                    {"jid": journal_id, "acc": acc_id, "desc": f"Tutup {bal.account_name}", "amt": float(amt)},
                )
                net_to_is -= amt   # expense mengurangi net income di Ikhtisar

        # Sisi lawan ke Ikhtisar L/R
        if net_to_is > 0:
            # Net income → Ikhtisar credit
            db.execute(
                text("""
                    INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr)
                    VALUES (:jid, :acc, :desc, 0, :amt)
                """),
                {"jid": journal_id, "acc": is_acc_id, "desc": "Ikhtisar Laba Rugi", "amt": float(net_to_is)},
            )
        elif net_to_is < 0:
            # Net loss → Ikhtisar debit
            db.execute(
                text("""
                    INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr)
                    VALUES (:jid, :acc, :desc, :amt, 0)
                """),
                {"jid": journal_id, "acc": is_acc_id, "desc": "Ikhtisar Laba Rugi (Rugi)", "amt": float(abs(net_to_is))},
            )

        # Update fiscal_year dengan closing_journal_id
        db.execute(
            text("UPDATE fiscal_year SET closing_journal_id=:jid, status='closing' WHERE id=:id"),
            {"jid": journal_id, "id": str(fy.id)},
        )
        db.execute(
            text("""
                INSERT INTO year_end_closing_log
                    (entity_id, fiscal_year, step, status, result_summary, executed_by)
                VALUES (:eid, :yr, 'income_summary', 'done', :summary::jsonb, :by)
            """),
            {
                "eid":     eid, "yr": fiscal_year,
                "summary": json.dumps({"journal_id": journal_id, "net_income": float(net_to_is), "accounts_closed": len(balances)}),
                "by":      closed_by,
            },
        )
        db.commit()
        return {
            "journal_id":     journal_id,
            "net_income":     float(net_to_is),
            "accounts_closed": len(balances),
            "label":          "LABA" if net_to_is >= 0 else "RUGI",
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 4. RETAINED EARNINGS TRANSFER
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def run_re_transfer(
        db: Session,
        entity_id: UUID,
        fiscal_year: int,
        closing_date: date,
        closed_by: str,
        income_summary_account: Optional[str] = None,
        retained_earnings_account: Optional[str] = None,
    ) -> dict:
        """
        Tutup akun Ikhtisar L/R ke Laba Ditahan.
        Jika net income: Dr. Ikhtisar | Cr. Laba Ditahan
        Jika net loss:   Dr. Laba Ditahan | Cr. Ikhtisar
        """
        eid     = str(entity_id)
        is_code = income_summary_account    or INCOME_SUMMARY_CODE
        re_code = retained_earnings_account or RETAINED_EARNINGS_CODE

        fy = db.execute(
            text("SELECT id, status FROM fiscal_year WHERE entity_id=:eid AND fiscal_year=:yr"),
            {"eid": eid, "yr": fiscal_year},
        ).fetchone()
        if not fy:
            raise ValueError(f"Fiscal year {fiscal_year} tidak ditemukan.")
        if fy.status == "closed":
            raise ValueError("Fiscal year sudah ditutup.")

        def get_acc(code: str) -> str:
            r = db.execute(
                text("SELECT id FROM chart_of_accounts WHERE account_code=:c AND entity_id=:e"),
                {"c": code, "e": eid},
            ).fetchone()
            if not r:
                raise ValueError(f"Account '{code}' tidak ditemukan di CoA.")
            return str(r.id)

        is_acc_id = get_acc(is_code)
        re_acc_id = get_acc(re_code)

        # Saldo Ikhtisar L/R setelah jurnal penutup income summary
        is_balance = db.execute(
            text("""
                SELECT COALESCE(SUM(gl.credit_idr), 0) - COALESCE(SUM(gl.debit_idr), 0) AS balance
                FROM gl_line gl
                JOIN gl_journal gj ON gj.id = gl.journal_id AND gj.status='posted'
                WHERE gl.account_id=:acc AND gj.entity_id=:eid
            """),
            {"acc": is_acc_id, "eid": eid},
        ).fetchone()
        net = Decimal(str(is_balance.balance or 0))

        if net == 0:
            raise ValueError(
                "Saldo Ikhtisar Laba Rugi = 0. Pastikan run_income_summary() sudah dijalankan."
            )

        journal_row = db.execute(
            text("""
                INSERT INTO gl_journal (entity_id, journal_date, description,
                                        journal_type, reference_no, status, created_by)
                VALUES (:eid, :dt, :desc, 'closing', :ref, 'posted', :by)
                RETURNING id
            """),
            {
                "eid":  eid, "dt":  closing_date,
                "desc": f"Transfer Laba Ditahan {fiscal_year}",
                "ref":  f"CLOSE-RE-{fiscal_year}",
                "by":   closed_by,
            },
        ).fetchone()
        journal_id = str(journal_row.id)

        if net > 0:
            # Laba: Dr. Ikhtisar | Cr. Laba Ditahan
            db.execute(
                text("""
                    INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr)
                    VALUES
                      (:jid, :is_acc, 'Tutup Ikhtisar Laba Rugi', :amt, 0),
                      (:jid, :re_acc, 'Tambah Laba Ditahan',       0, :amt)
                """),
                {"jid": journal_id, "is_acc": is_acc_id, "re_acc": re_acc_id, "amt": float(net)},
            )
        else:
            # Rugi: Dr. Laba Ditahan | Cr. Ikhtisar
            amt = abs(net)
            db.execute(
                text("""
                    INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr)
                    VALUES
                      (:jid, :re_acc, 'Kurangi Laba Ditahan (Rugi)', :amt, 0),
                      (:jid, :is_acc, 'Tutup Ikhtisar Laba Rugi',    0, :amt)
                """),
                {"jid": journal_id, "is_acc": is_acc_id, "re_acc": re_acc_id, "amt": float(amt)},
            )

        db.execute(
            text("UPDATE fiscal_year SET transfer_journal_id=:jid WHERE id=:id"),
            {"jid": journal_id, "id": str(fy.id)},
        )
        db.execute(
            text("""
                INSERT INTO year_end_closing_log
                    (entity_id, fiscal_year, step, status, result_summary, executed_by)
                VALUES (:eid, :yr, 're_transfer', 'done', :summary::jsonb, :by)
            """),
            {
                "eid":     eid, "yr": fiscal_year,
                "summary": json.dumps({"journal_id": journal_id, "net": float(net)}),
                "by":      closed_by,
            },
        )
        db.commit()
        return {
            "journal_id":      journal_id,
            "net_transferred": float(net),
            "direction":       "income" if net > 0 else "loss",
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 5. LOCK PERIODS
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def lock_periods(
        db: Session,
        entity_id: UUID,
        fiscal_year: int,
        locked_by: str,
    ) -> dict:
        """Lock semua fiscal_period dalam fiscal_year → tidak bisa post jurnal baru."""
        result = db.execute(
            text("""
                UPDATE fiscal_period
                SET status='locked', locked_by=:by, locked_at=NOW()
                WHERE entity_id=:eid AND period_year=:yr AND status='open'
            """),
            {"eid": str(entity_id), "yr": fiscal_year, "by": locked_by},
        )

        db.execute(
            text("UPDATE fiscal_year SET status='closed', closed_by=:by, closed_at=NOW() WHERE entity_id=:eid AND fiscal_year=:yr"),
            {"eid": str(entity_id), "yr": fiscal_year, "by": locked_by},
        )
        db.execute(
            text("""
                INSERT INTO year_end_closing_log
                    (entity_id, fiscal_year, step, status, result_summary, executed_by)
                VALUES (:eid, :yr, 'lock_periods', 'done', :summary::jsonb, :by)
            """),
            {
                "eid":     str(entity_id), "yr": fiscal_year,
                "summary": json.dumps({"locked_count": result.rowcount}),
                "by":      locked_by,
            },
        )
        db.commit()
        return {"locked_periods": result.rowcount, "fiscal_year_status": "closed"}

    # ─────────────────────────────────────────────────────────────────────────
    # 6. CLOSE FISCAL YEAR (orchestrator)
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def close_fiscal_year(
        db: Session,
        entity_id: UUID,
        fiscal_year: int,
        closing_date: date,
        closed_by: str,
        skip_checks: bool = False,
        income_summary_account: Optional[str] = None,
        retained_earnings_account: Optional[str] = None,
    ) -> dict:
        """
        Jalankan semua langkah tutup buku secara berurutan:
          1. Pre-closing checks (jika skip_checks=False)
          2. Income summary
          3. RE transfer
          4. Lock periods
        """
        results = {}

        if not skip_checks:
            check_result = YearEndClosingEngine.run_pre_closing_checks(db, entity_id, fiscal_year)
            results["pre_checks"] = check_result
            if not check_result["can_proceed"]:
                return {
                    "status":  "blocked",
                    "message": "Pre-closing checks gagal. Selesaikan semua item 'fail' sebelum tutup buku.",
                    "results": results,
                }

        is_result = YearEndClosingEngine.run_income_summary(
            db, entity_id, fiscal_year, closing_date, closed_by, income_summary_account
        )
        results["income_summary"] = is_result

        re_result = YearEndClosingEngine.run_re_transfer(
            db, entity_id, fiscal_year, closing_date, closed_by,
            income_summary_account, retained_earnings_account
        )
        results["re_transfer"] = re_result

        lock_result = YearEndClosingEngine.lock_periods(db, entity_id, fiscal_year, closed_by)
        results["lock_periods"] = lock_result

        return {
            "status":  "closed",
            "message": f"Fiscal year {fiscal_year} berhasil ditutup.",
            "results": results,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 7. REOPEN PERIOD
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def reopen_period(
        db: Session,
        entity_id: UUID,
        period_year: int,
        period_month: int,
        reopened_by: str,
        reason: str,
    ) -> dict:
        """Admin membuka kembali satu periode yang terkunci (untuk koreksi)."""
        result = db.execute(
            text("""
                UPDATE fiscal_period
                SET status='open', locked_by=NULL, locked_at=NULL
                WHERE entity_id=:eid AND period_year=:yr AND period_month=:mo
                  AND status='locked'
            """),
            {"eid": str(entity_id), "yr": period_year, "mo": period_month},
        )
        if result.rowcount == 0:
            raise ValueError("Periode tidak ditemukan atau sudah dalam status open.")

        db.execute(
            text("""
                INSERT INTO year_end_closing_log
                    (entity_id, fiscal_year, step, status, result_summary, executed_by)
                VALUES (:eid, :yr, 'reopen_period', 'done', :summary::jsonb, :by)
            """),
            {
                "eid":     str(entity_id), "yr": period_year,
                "summary": json.dumps({"month": period_month, "reason": reason}),
                "by":      reopened_by,
            },
        )
        db.commit()
        return {
            "status": "reopened",
            "period": f"{period_year}-{period_month:02d}",
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 8. GET CLOSING STATUS
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_closing_status(db: Session, entity_id: UUID, fiscal_year: int) -> dict:
        eid = str(entity_id)

        fy = db.execute(
            text("SELECT * FROM vw_fiscal_year_status WHERE entity_id=:eid AND fiscal_year=:yr"),
            {"eid": eid, "yr": fiscal_year},
        ).fetchone()

        checks = db.execute(
            text("""
                SELECT check_item, check_result, detail, checked_at
                FROM closing_checklist
                WHERE entity_id=:eid AND fiscal_year=:yr
                ORDER BY check_item
            """),
            {"eid": eid, "yr": fiscal_year},
        ).fetchall()

        logs = db.execute(
            text("""
                SELECT step, status, result_summary, executed_by, executed_at
                FROM year_end_closing_log
                WHERE entity_id=:eid AND fiscal_year=:yr
                ORDER BY executed_at DESC
            """),
            {"eid": eid, "yr": fiscal_year},
        ).fetchall()

        return {
            "fiscal_year": dict(fy._mapping) if fy else None,
            "checklist":   [dict(r._mapping) for r in checks],
            "logs":        [dict(r._mapping) for r in logs],
        }
