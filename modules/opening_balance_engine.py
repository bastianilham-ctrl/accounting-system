"""
Opening Balance Engine
Onboarding perusahaan baru atau migrasi dari sistem lama.

Flow:
  1. create_session()           → buat session, tentukan opening_date
  2. save_gl_balances()         → input Trial Balance (wajib)
  3. save_ar_items()            → input AR outstanding (opsional)
  4. save_ap_items()            → input AP outstanding (opsional)
  5. save_asset_register()      → input daftar aset tetap (opsional)
  6. save_inventory()           → input stok awal (opsional)
  7. save_bank_balances()       → input saldo bank (opsional)
  8. save_leave_balances()      → input saldo cuti (opsional)
  9. validate_session()         → validasi: GL balance + cross-check subsidiary
  10. finalize()                → posting Opening Journal + populate semua modul

Setelah finalize:
  - gl_journal type='opening' terbuat dengan semua entri GL
  - ar_invoice records dibuat (is_opening_balance=True, status='open')
  - ap_invoice records dibuat (is_opening_balance=True, status='open')
  - fixed_asset records dibuat dengan akumulasi penyusutan existing
  - stock_move records dibuat (move_type='opening')
  - bank_account.opening_balance diupdate
  - leave_entitlement records dibuat/diupdate
  - fiscal_year dan fiscal_period disiapkan
  - session dikunci (status='finalized')
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from modules.audit_engine import AuditEngine

# Tolerance untuk cross-check subsidiary vs GL (rupiah)
BALANCE_TOLERANCE = Decimal("1.00")


# ── Session Management ─────────────────────────────────────────────────────────
class OpeningBalanceEngine:

    @staticmethod
    def create_session(
        db: Session,
        entity_id: UUID,
        opening_date: date,
        fiscal_year: int,
        is_mid_year: bool = False,
        notes: Optional[str] = None,
        created_by: str = "",
    ) -> dict:
        existing = db.execute(
            text("SELECT id, status FROM opening_balance_session WHERE entity_id=:eid"),
            {"eid": str(entity_id)},
        ).fetchone()

        if existing:
            if existing.status == "finalized":
                raise ValueError("Opening balance sudah difinalisasi dan tidak bisa diubah.")
            # Return existing session
            return {
                "session_id": str(existing.id),
                "status":     existing.status,
                "message":    "Session yang ada dikembalikan.",
            }

        row = db.execute(
            text("""
                INSERT INTO opening_balance_session
                    (entity_id, opening_date, fiscal_year, is_mid_year, notes, created_by)
                VALUES (:eid, :dt, :yr, :mid, :notes, :by)
                RETURNING id
            """),
            {
                "eid":   str(entity_id),
                "dt":    str(opening_date),
                "yr":    fiscal_year,
                "mid":   is_mid_year,
                "notes": notes,
                "by":    created_by,
            },
        ).fetchone()
        db.commit()

        return {
            "session_id": str(row.id),
            "status":     "draft",
            "opening_date": str(opening_date),
            "fiscal_year":  fiscal_year,
        }

    @staticmethod
    def _get_session(db: Session, session_id: UUID):
        sess = db.execute(
            text("SELECT * FROM opening_balance_session WHERE id=:sid"),
            {"sid": str(session_id)},
        ).fetchone()
        if not sess:
            raise ValueError("Opening balance session tidak ditemukan.")
        if sess.status == "finalized":
            raise ValueError("Session sudah difinalisasi dan tidak bisa diedit.")
        return sess

    # ── GL Trial Balance ──────────────────────────────────────────────────────

    @staticmethod
    def save_gl_balances(
        db: Session,
        session_id: UUID,
        balances: list[dict],
        replace_all: bool = True,
    ) -> dict:
        """
        balances: [{"account_code": "1-1100", "debit_balance": 50000000, "credit_balance": 0, "account_name": "Kas"}, ...]
        replace_all=True → hapus semua baris GL yang ada lalu insert ulang (default)
        replace_all=False → upsert individual baris
        """
        OpeningBalanceEngine._get_session(db, session_id)

        if replace_all:
            db.execute(
                text("DELETE FROM opening_balance_gl WHERE session_id=:sid"),
                {"sid": str(session_id)},
            )

        inserted = 0
        for b in balances:
            if not b.get("account_code"):
                continue
            db.execute(
                text("""
                    INSERT INTO opening_balance_gl
                        (session_id, account_code, account_name, debit_balance, credit_balance, notes)
                    VALUES (:sid, :code, :name, :dr, :cr, :notes)
                    ON CONFLICT (session_id, account_code)
                    DO UPDATE SET
                        debit_balance  = EXCLUDED.debit_balance,
                        credit_balance = EXCLUDED.credit_balance,
                        account_name   = EXCLUDED.account_name,
                        notes          = EXCLUDED.notes
                """),
                {
                    "sid":   str(session_id),
                    "code":  b["account_code"],
                    "name":  b.get("account_name"),
                    "dr":    Decimal(str(b.get("debit_balance",  0))),
                    "cr":    Decimal(str(b.get("credit_balance", 0))),
                    "notes": b.get("notes"),
                },
            )
            inserted += 1

        db.execute(
            text("UPDATE opening_balance_session SET gl_done=TRUE, status='in_progress', is_valid=NULL WHERE id=:sid"),
            {"sid": str(session_id)},
        )
        db.commit()

        totals = db.execute(
            text("SELECT SUM(debit_balance) AS dr, SUM(credit_balance) AS cr FROM opening_balance_gl WHERE session_id=:sid"),
            {"sid": str(session_id)},
        ).fetchone()

        return {
            "session_id": str(session_id),
            "accounts_saved": inserted,
            "total_debit":   float(totals.dr or 0),
            "total_credit":  float(totals.cr or 0),
            "is_balanced":   abs((totals.dr or 0) - (totals.cr or 0)) <= 1,
        }

    # ── AR Items ──────────────────────────────────────────────────────────────

    @staticmethod
    def save_ar_items(
        db: Session,
        session_id: UUID,
        items: list[dict],
        replace_all: bool = True,
    ) -> dict:
        """
        items: [{"customer_name": "PT ABC", "invoice_number": "INV-001",
                 "invoice_date": "2024-12-31", "due_date": "2025-01-31",
                 "original_amount": 10000000, "amount_remaining": 10000000}]
        """
        OpeningBalanceEngine._get_session(db, session_id)
        if replace_all:
            db.execute(text("DELETE FROM opening_balance_ar WHERE session_id=:sid"), {"sid": str(session_id)})

        for item in items:
            db.execute(
                text("""
                    INSERT INTO opening_balance_ar
                        (session_id, customer_name, customer_id, invoice_number, invoice_date,
                         due_date, original_amount, amount_remaining, currency, exchange_rate, description)
                    VALUES (:sid, :cname, :cid, :inv, :idate, :ddate,
                            :orig, :rem, :cur, :fx, :desc)
                    ON CONFLICT DO NOTHING
                """),
                {
                    "sid":   str(session_id),
                    "cname": item["customer_name"],
                    "cid":   str(item["customer_id"]) if item.get("customer_id") else None,
                    "inv":   item["invoice_number"],
                    "idate": str(item["invoice_date"]),
                    "ddate": str(item["due_date"]) if item.get("due_date") else None,
                    "orig":  Decimal(str(item["original_amount"])),
                    "rem":   Decimal(str(item.get("amount_remaining", item["original_amount"]))),
                    "cur":   item.get("currency", "IDR"),
                    "fx":    Decimal(str(item.get("exchange_rate", 1))),
                    "desc":  item.get("description"),
                },
            )

        db.execute(
            text("UPDATE opening_balance_session SET ar_done=TRUE, is_valid=NULL WHERE id=:sid"),
            {"sid": str(session_id)},
        )
        db.commit()

        total = db.execute(
            text("SELECT COUNT(*) AS cnt, SUM(amount_remaining) AS total FROM opening_balance_ar WHERE session_id=:sid"),
            {"sid": str(session_id)},
        ).fetchone()
        return {"items_saved": int(total.cnt), "total_amount": float(total.total or 0)}

    # ── AP Items ──────────────────────────────────────────────────────────────

    @staticmethod
    def save_ap_items(
        db: Session,
        session_id: UUID,
        items: list[dict],
        replace_all: bool = True,
    ) -> dict:
        OpeningBalanceEngine._get_session(db, session_id)
        if replace_all:
            db.execute(text("DELETE FROM opening_balance_ap WHERE session_id=:sid"), {"sid": str(session_id)})

        for item in items:
            db.execute(
                text("""
                    INSERT INTO opening_balance_ap
                        (session_id, vendor_name, vendor_id, invoice_number, invoice_date,
                         due_date, original_amount, amount_remaining, currency, exchange_rate, description)
                    VALUES (:sid, :vname, :vid, :inv, :idate, :ddate,
                            :orig, :rem, :cur, :fx, :desc)
                    ON CONFLICT DO NOTHING
                """),
                {
                    "sid":   str(session_id),
                    "vname": item["vendor_name"],
                    "vid":   str(item["vendor_id"]) if item.get("vendor_id") else None,
                    "inv":   item["invoice_number"],
                    "idate": str(item["invoice_date"]),
                    "ddate": str(item["due_date"]) if item.get("due_date") else None,
                    "orig":  Decimal(str(item["original_amount"])),
                    "rem":   Decimal(str(item.get("amount_remaining", item["original_amount"]))),
                    "cur":   item.get("currency", "IDR"),
                    "fx":    Decimal(str(item.get("exchange_rate", 1))),
                    "desc":  item.get("description"),
                },
            )

        db.execute(
            text("UPDATE opening_balance_session SET ap_done=TRUE, is_valid=NULL WHERE id=:sid"),
            {"sid": str(session_id)},
        )
        db.commit()

        total = db.execute(
            text("SELECT COUNT(*) AS cnt, SUM(amount_remaining) AS total FROM opening_balance_ap WHERE session_id=:sid"),
            {"sid": str(session_id)},
        ).fetchone()
        return {"items_saved": int(total.cnt), "total_amount": float(total.total or 0)}

    # ── Fixed Asset Register ──────────────────────────────────────────────────

    @staticmethod
    def save_asset_register(
        db: Session,
        session_id: UUID,
        assets: list[dict],
        replace_all: bool = True,
    ) -> dict:
        OpeningBalanceEngine._get_session(db, session_id)
        if replace_all:
            db.execute(text("DELETE FROM opening_balance_asset WHERE session_id=:sid"), {"sid": str(session_id)})

        for a in assets:
            db.execute(
                text("""
                    INSERT INTO opening_balance_asset
                        (session_id, asset_code, asset_name, category, location,
                         acquisition_date, acquisition_cost, accumulated_depreciation,
                         useful_life_months, depreciation_method, salvage_value,
                         gl_asset_account, gl_depr_account, gl_expense_account,
                         serial_number, notes)
                    VALUES (:sid, :code, :name, :cat, :loc,
                            :aqdt, :aqcost, :accdepr,
                            :life, :method, :salvage,
                            :gl_asset, :gl_depr, :gl_exp,
                            :serial, :notes)
                """),
                {
                    "sid":     str(session_id),
                    "code":    a.get("asset_code"),
                    "name":    a["asset_name"],
                    "cat":     a.get("category"),
                    "loc":     a.get("location"),
                    "aqdt":    str(a["acquisition_date"]),
                    "aqcost":  Decimal(str(a["acquisition_cost"])),
                    "accdepr": Decimal(str(a.get("accumulated_depreciation", 0))),
                    "life":    int(a.get("useful_life_months", 60)),
                    "method":  a.get("depreciation_method", "straight_line"),
                    "salvage": Decimal(str(a.get("salvage_value", 0))),
                    "gl_asset": a.get("gl_asset_account"),
                    "gl_depr":  a.get("gl_depr_account"),
                    "gl_exp":   a.get("gl_expense_account"),
                    "serial":  a.get("serial_number"),
                    "notes":   a.get("notes"),
                },
            )

        db.execute(
            text("UPDATE opening_balance_session SET asset_done=TRUE, is_valid=NULL WHERE id=:sid"),
            {"sid": str(session_id)},
        )
        db.commit()

        total = db.execute(
            text("SELECT COUNT(*) AS cnt, SUM(net_book_value) AS nbv FROM opening_balance_asset WHERE session_id=:sid"),
            {"sid": str(session_id)},
        ).fetchone()
        return {"assets_saved": int(total.cnt), "total_nbv": float(total.nbv or 0)}

    # ── Inventory ─────────────────────────────────────────────────────────────

    @staticmethod
    def save_inventory(
        db: Session,
        session_id: UUID,
        items: list[dict],
        replace_all: bool = True,
    ) -> dict:
        OpeningBalanceEngine._get_session(db, session_id)
        if replace_all:
            db.execute(text("DELETE FROM opening_balance_inventory WHERE session_id=:sid"), {"sid": str(session_id)})

        for item in items:
            db.execute(
                text("""
                    INSERT INTO opening_balance_inventory
                        (session_id, product_code, product_name, product_id,
                         warehouse_code, warehouse_id, quantity, unit_cost,
                         unit_of_measure, notes)
                    VALUES (:sid, :code, :name, :pid, :wcode, :wid, :qty, :cost, :uom, :notes)
                    ON CONFLICT (session_id, product_code, warehouse_code)
                    DO UPDATE SET
                        quantity     = EXCLUDED.quantity,
                        unit_cost    = EXCLUDED.unit_cost,
                        product_name = EXCLUDED.product_name
                """),
                {
                    "sid":   str(session_id),
                    "code":  item["product_code"],
                    "name":  item.get("product_name"),
                    "pid":   str(item["product_id"]) if item.get("product_id") else None,
                    "wcode": item.get("warehouse_code", "DEFAULT"),
                    "wid":   str(item["warehouse_id"]) if item.get("warehouse_id") else None,
                    "qty":   Decimal(str(item["quantity"])),
                    "cost":  Decimal(str(item["unit_cost"])),
                    "uom":   item.get("unit_of_measure"),
                    "notes": item.get("notes"),
                },
            )

        db.execute(
            text("UPDATE opening_balance_session SET inventory_done=TRUE, is_valid=NULL WHERE id=:sid"),
            {"sid": str(session_id)},
        )
        db.commit()

        total = db.execute(
            text("SELECT COUNT(*) AS cnt, SUM(total_value) AS tv FROM opening_balance_inventory WHERE session_id=:sid"),
            {"sid": str(session_id)},
        ).fetchone()
        return {"items_saved": int(total.cnt), "total_value": float(total.tv or 0)}

    # ── Bank Balances ─────────────────────────────────────────────────────────

    @staticmethod
    def save_bank_balances(
        db: Session,
        session_id: UUID,
        banks: list[dict],
        replace_all: bool = True,
    ) -> dict:
        OpeningBalanceEngine._get_session(db, session_id)
        if replace_all:
            db.execute(text("DELETE FROM opening_balance_bank WHERE session_id=:sid"), {"sid": str(session_id)})

        for b in banks:
            db.execute(
                text("""
                    INSERT INTO opening_balance_bank
                        (session_id, bank_account_id, bank_name, account_number,
                         account_holder, currency, opening_balance, gl_account_code, notes)
                    VALUES (:sid, :baid, :bname, :anum, :holder, :cur, :bal, :gl, :notes)
                    ON CONFLICT (session_id, bank_name, account_number)
                    DO UPDATE SET opening_balance=EXCLUDED.opening_balance, gl_account_code=EXCLUDED.gl_account_code
                """),
                {
                    "sid":    str(session_id),
                    "baid":   str(b["bank_account_id"]) if b.get("bank_account_id") else None,
                    "bname":  b["bank_name"],
                    "anum":   b.get("account_number", ""),
                    "holder": b.get("account_holder"),
                    "cur":    b.get("currency", "IDR"),
                    "bal":    Decimal(str(b["opening_balance"])),
                    "gl":     b.get("gl_account_code"),
                    "notes":  b.get("notes"),
                },
            )

        db.execute(
            text("UPDATE opening_balance_session SET bank_done=TRUE, is_valid=NULL WHERE id=:sid"),
            {"sid": str(session_id)},
        )
        db.commit()

        total = db.execute(
            text("SELECT COUNT(*) AS cnt, SUM(opening_balance) AS total FROM opening_balance_bank WHERE session_id=:sid"),
            {"sid": str(session_id)},
        ).fetchone()
        return {"banks_saved": int(total.cnt), "total_balance": float(total.total or 0)}

    # ── Leave Balances ────────────────────────────────────────────────────────

    @staticmethod
    def save_leave_balances(
        db: Session,
        session_id: UUID,
        leaves: list[dict],
        replace_all: bool = True,
    ) -> dict:
        OpeningBalanceEngine._get_session(db, session_id)
        if replace_all:
            db.execute(text("DELETE FROM opening_balance_leave WHERE session_id=:sid"), {"sid": str(session_id)})

        for lv in leaves:
            db.execute(
                text("""
                    INSERT INTO opening_balance_leave
                        (session_id, employee_id, employee_code, employee_name,
                         leave_type_code, fiscal_year,
                         entitled_days, used_days, carry_forward, notes)
                    VALUES (:sid, :eid, :ecode, :ename, :ltype, :yr, :ent, :used, :cf, :notes)
                    ON CONFLICT (session_id, employee_code, leave_type_code, fiscal_year)
                    DO UPDATE SET
                        entitled_days = EXCLUDED.entitled_days,
                        used_days     = EXCLUDED.used_days,
                        carry_forward = EXCLUDED.carry_forward
                """),
                {
                    "sid":   str(session_id),
                    "eid":   str(lv["employee_id"]) if lv.get("employee_id") else None,
                    "ecode": lv.get("employee_code", ""),
                    "ename": lv.get("employee_name"),
                    "ltype": lv["leave_type_code"],
                    "yr":    int(lv.get("fiscal_year", date.today().year)),
                    "ent":   Decimal(str(lv.get("entitled_days", 0))),
                    "used":  Decimal(str(lv.get("used_days", 0))),
                    "cf":    Decimal(str(lv.get("carry_forward", 0))),
                    "notes": lv.get("notes"),
                },
            )

        db.execute(
            text("UPDATE opening_balance_session SET leave_done=TRUE, is_valid=NULL WHERE id=:sid"),
            {"sid": str(session_id)},
        )
        db.commit()

        total = db.execute(
            text("SELECT COUNT(*) AS cnt FROM opening_balance_leave WHERE session_id=:sid"),
            {"sid": str(session_id)},
        ).fetchone()
        return {"records_saved": int(total.cnt)}

    # ── Validate ──────────────────────────────────────────────────────────────

    @staticmethod
    def validate_session(db: Session, session_id: UUID) -> dict:
        sess = db.execute(
            text("SELECT * FROM opening_balance_session WHERE id=:sid"),
            {"sid": str(session_id)},
        ).fetchone()
        if not sess:
            raise ValueError("Session tidak ditemukan.")

        checks = []
        errors = []
        warnings = []

        # ── Check 1: GL harus ada ─────────────────────────────────────────────
        gl_count = db.execute(
            text("SELECT COUNT(*) AS cnt FROM opening_balance_gl WHERE session_id=:sid"),
            {"sid": str(session_id)},
        ).fetchone().cnt

        if gl_count == 0:
            errors.append("GL Trial Balance belum diisi. Ini wajib diisi sebelum finalisasi.")
        else:
            checks.append({"check": "GL entries exist", "result": "pass", "detail": f"{gl_count} accounts"})

        # ── Check 2: GL harus balance ─────────────────────────────────────────
        gl_totals = db.execute(
            text("SELECT SUM(debit_balance) AS dr, SUM(credit_balance) AS cr FROM opening_balance_gl WHERE session_id=:sid"),
            {"sid": str(session_id)},
        ).fetchone()
        total_dr = Decimal(str(gl_totals.dr or 0))
        total_cr = Decimal(str(gl_totals.cr or 0))
        diff = abs(total_dr - total_cr)

        if diff > BALANCE_TOLERANCE:
            errors.append(f"GL tidak balance: Debit {total_dr:,.0f} vs Credit {total_cr:,.0f} (selisih {diff:,.0f})")
        else:
            checks.append({"check": "GL balanced", "result": "pass",
                           "detail": f"Debit = Credit = {total_dr:,.0f}"})

        # ── Check 3: Validasi akun ada di CoA ────────────────────────────────
        unknown = db.execute(
            text("""
                SELECT og.account_code
                FROM opening_balance_gl og
                WHERE og.session_id=:sid
                  AND NOT EXISTS (
                      SELECT 1 FROM chart_of_accounts coa
                      WHERE coa.account_code = og.account_code
                        AND coa.entity_id = :eid
                  )
            """),
            {"sid": str(session_id), "eid": str(sess.entity_id)},
        ).fetchall()
        if unknown:
            codes = [r.account_code for r in unknown]
            warnings.append(f"Akun berikut tidak ada di CoA: {', '.join(codes[:10])}. Akan dibuat otomatis saat finalisasi.")
        else:
            checks.append({"check": "All GL accounts in CoA", "result": "pass"})

        # ── Check 4: Cross-check AR vs GL ─────────────────────────────────────
        ar_total = db.execute(
            text("SELECT COALESCE(SUM(amount_remaining), 0) AS total FROM opening_balance_ar WHERE session_id=:sid"),
            {"sid": str(session_id)},
        ).fetchone()
        ar_subsidiary = Decimal(str(ar_total.total))

        if sess.ar_done and ar_subsidiary > 0:
            # Find AR GL account (account_code starts with 1-12 or contains 'piutang')
            ar_gl = db.execute(
                text("""
                    SELECT COALESCE(SUM(debit_balance - credit_balance), 0) AS bal
                    FROM opening_balance_gl og
                    JOIN chart_of_accounts coa ON coa.account_code = og.account_code
                        AND coa.entity_id = :eid
                    WHERE og.session_id=:sid
                      AND coa.account_type='asset'
                      AND (coa.account_code LIKE '1-12%' OR coa.account_name ILIKE '%piutang%')
                """),
                {"sid": str(session_id), "eid": str(sess.entity_id)},
            ).fetchone()
            ar_gl_bal = Decimal(str(ar_gl.bal)) if ar_gl else Decimal("0")
            ar_diff = abs(ar_subsidiary - ar_gl_bal)

            if ar_diff > BALANCE_TOLERANCE:
                warnings.append(
                    f"AR subsidiary ({ar_subsidiary:,.0f}) tidak cocok dengan GL Piutang ({ar_gl_bal:,.0f}), "
                    f"selisih {ar_diff:,.0f}. Periksa kembali."
                )
            else:
                checks.append({"check": "AR subsidiary matches GL", "result": "pass",
                               "detail": f"AR = {ar_subsidiary:,.0f}"})

        # ── Check 5: Cross-check AP vs GL ─────────────────────────────────────
        ap_total = db.execute(
            text("SELECT COALESCE(SUM(amount_remaining), 0) AS total FROM opening_balance_ap WHERE session_id=:sid"),
            {"sid": str(session_id)},
        ).fetchone()
        ap_subsidiary = Decimal(str(ap_total.total))

        if sess.ap_done and ap_subsidiary > 0:
            ap_gl = db.execute(
                text("""
                    SELECT COALESCE(SUM(credit_balance - debit_balance), 0) AS bal
                    FROM opening_balance_gl og
                    JOIN chart_of_accounts coa ON coa.account_code = og.account_code
                        AND coa.entity_id = :eid
                    WHERE og.session_id=:sid
                      AND coa.account_type='liability'
                      AND (coa.account_code LIKE '2-1%' OR coa.account_name ILIKE '%hutang usaha%')
                """),
                {"sid": str(session_id), "eid": str(sess.entity_id)},
            ).fetchone()
            ap_gl_bal = Decimal(str(ap_gl.bal)) if ap_gl else Decimal("0")
            ap_diff = abs(ap_subsidiary - ap_gl_bal)
            if ap_diff > BALANCE_TOLERANCE:
                warnings.append(
                    f"AP subsidiary ({ap_subsidiary:,.0f}) tidak cocok dengan GL Hutang Usaha ({ap_gl_bal:,.0f}), "
                    f"selisih {ap_diff:,.0f}."
                )
            else:
                checks.append({"check": "AP subsidiary matches GL", "result": "pass"})

        # ── Check 6: Cross-check Inventory vs GL ─────────────────────────────
        inv_total = db.execute(
            text("SELECT COALESCE(SUM(total_value), 0) AS tv FROM opening_balance_inventory WHERE session_id=:sid"),
            {"sid": str(session_id)},
        ).fetchone()
        inv_subsidiary = Decimal(str(inv_total.tv))

        if sess.inventory_done and inv_subsidiary > 0:
            inv_gl = db.execute(
                text("""
                    SELECT COALESCE(SUM(debit_balance - credit_balance), 0) AS bal
                    FROM opening_balance_gl og
                    JOIN chart_of_accounts coa ON coa.account_code = og.account_code
                        AND coa.entity_id = :eid
                    WHERE og.session_id=:sid
                      AND (coa.account_code LIKE '1-14%' OR coa.account_name ILIKE '%persediaan%')
                """),
                {"sid": str(session_id), "eid": str(sess.entity_id)},
            ).fetchone()
            inv_gl_bal = Decimal(str(inv_gl.bal)) if inv_gl else Decimal("0")
            inv_diff = abs(inv_subsidiary - inv_gl_bal)
            if inv_diff > BALANCE_TOLERANCE:
                warnings.append(
                    f"Inventory subsidiary ({inv_subsidiary:,.0f}) tidak cocok dengan GL Persediaan ({inv_gl_bal:,.0f}), "
                    f"selisih {inv_diff:,.0f}."
                )
            else:
                checks.append({"check": "Inventory subsidiary matches GL", "result": "pass"})

        # ── Check 7: Cross-check Fixed Asset NBV vs GL ────────────────────────
        fa_total = db.execute(
            text("SELECT COALESCE(SUM(net_book_value), 0) AS nbv FROM opening_balance_asset WHERE session_id=:sid"),
            {"sid": str(session_id)},
        ).fetchone()
        fa_subsidiary = Decimal(str(fa_total.nbv))

        if sess.asset_done and fa_subsidiary > 0:
            fa_gl = db.execute(
                text("""
                    SELECT COALESCE(SUM(debit_balance - credit_balance), 0) AS bal
                    FROM opening_balance_gl og
                    JOIN chart_of_accounts coa ON coa.account_code = og.account_code
                        AND coa.entity_id = :eid
                    WHERE og.session_id=:sid
                      AND (coa.account_code LIKE '1-5%'
                           OR coa.account_name ILIKE '%aset tetap%')
                """),
                {"sid": str(session_id), "eid": str(sess.entity_id)},
            ).fetchone()
            fa_gl_bal = Decimal(str(fa_gl.bal)) if fa_gl else Decimal("0")
            fa_diff = abs(fa_subsidiary - fa_gl_bal)
            if fa_diff > BALANCE_TOLERANCE:
                warnings.append(
                    f"Fixed Asset NBV ({fa_subsidiary:,.0f}) tidak cocok dengan GL Aset Tetap Neto ({fa_gl_bal:,.0f}), "
                    f"selisih {fa_diff:,.0f}."
                )
            else:
                checks.append({"check": "Fixed Asset NBV matches GL", "result": "pass"})

        is_valid = len(errors) == 0
        result = {
            "session_id": str(session_id),
            "is_valid":   is_valid,
            "can_finalize": is_valid,
            "checks":     checks,
            "warnings":   warnings,
            "errors":     errors,
            "summary": {
                "total_debit":  float(total_dr),
                "total_credit": float(total_cr),
                "balance_diff": float(diff),
            }
        }

        db.execute(
            text("UPDATE opening_balance_session SET last_validation=:res::jsonb, is_valid=:valid WHERE id=:sid"),
            {"sid": str(session_id), "res": json.dumps(result), "valid": is_valid},
        )
        db.commit()
        return result

    # ── Finalize ──────────────────────────────────────────────────────────────

    @staticmethod
    def finalize(db: Session, session_id: UUID, finalized_by: str) -> dict:
        sess = db.execute(
            text("SELECT * FROM opening_balance_session WHERE id=:sid"),
            {"sid": str(session_id)},
        ).fetchone()
        if not sess:
            raise ValueError("Session tidak ditemukan.")
        if sess.status == "finalized":
            raise ValueError("Session sudah difinalisasi.")
        if not sess.is_valid:
            raise ValueError("Session belum divalidasi atau validasi gagal. Jalankan validate_session() terlebih dahulu.")

        steps = []

        try:
            # Step 1: Post Opening Balance GL Journal
            journal_id = OpeningBalanceEngine._post_opening_journal(db, sess)
            steps.append({"step": "opening_journal", "status": "done", "journal_id": str(journal_id)})

            # Step 2: Import AR invoices
            ar_count = OpeningBalanceEngine._import_ar(db, sess)
            steps.append({"step": "ar_import", "status": "done", "records": ar_count})

            # Step 3: Import AP invoices
            ap_count = OpeningBalanceEngine._import_ap(db, sess)
            steps.append({"step": "ap_import", "status": "done", "records": ap_count})

            # Step 4: Import Fixed Assets
            asset_count = OpeningBalanceEngine._import_assets(db, sess)
            steps.append({"step": "asset_import", "status": "done", "records": asset_count})

            # Step 5: Import Inventory
            inv_count = OpeningBalanceEngine._import_inventory(db, sess)
            steps.append({"step": "inventory_import", "status": "done", "records": inv_count})

            # Step 6: Update Bank Balances
            bank_count = OpeningBalanceEngine._update_bank_balances(db, sess)
            steps.append({"step": "bank_update", "status": "done", "records": bank_count})

            # Step 7: Setup Leave Entitlements
            leave_count = OpeningBalanceEngine._setup_leave(db, sess)
            steps.append({"step": "leave_setup", "status": "done", "records": leave_count})

            # Step 8: Setup Fiscal Year & Period if not exists
            OpeningBalanceEngine._ensure_fiscal_period(db, sess)
            steps.append({"step": "fiscal_period", "status": "done"})

            # Lock session
            db.execute(
                text("""
                    UPDATE opening_balance_session
                    SET status='finalized', finalized_at=NOW(),
                        finalized_by=:by, opening_journal_id=:jid
                    WHERE id=:sid
                """),
                {"sid": str(session_id), "by": finalized_by, "jid": str(journal_id)},
            )

            AuditEngine.log(
                db,
                entity_id   = sess.entity_id,
                username    = finalized_by,
                action      = "FINALIZE",
                module      = "opening_balance",
                ref_type    = "other",
                description = f"Opening Balance difinalisasi per {sess.opening_date}",
                severity    = "critical",
            )

            db.commit()

            return {
                "status":      "finalized",
                "session_id":  str(session_id),
                "opening_date": str(sess.opening_date),
                "journal_id":  str(journal_id),
                "steps":       steps,
            }

        except Exception as e:
            db.rollback()
            raise ValueError(f"Finalisasi gagal pada step '{steps[-1]['step'] if steps else 'init'}': {str(e)}")

    # ── Private: Post Opening GL Journal ─────────────────────────────────────

    @staticmethod
    def _post_opening_journal(db: Session, sess) -> UUID:
        gl_rows = db.execute(
            text("""
                SELECT og.account_code, og.debit_balance, og.credit_balance,
                       coa.id AS account_id, coa.account_name
                FROM opening_balance_gl og
                LEFT JOIN chart_of_accounts coa ON coa.account_code=og.account_code
                    AND coa.entity_id=:eid
                WHERE og.session_id=:sid
                  AND (og.debit_balance > 0 OR og.credit_balance > 0)
            """),
            {"sid": str(sess.id), "eid": str(sess.entity_id)},
        ).fetchall()

        ref_no = f"OPB-{sess.opening_date.year}"
        journal = db.execute(
            text("""
                INSERT INTO gl_journal
                    (entity_id, journal_date, reference_number, description,
                     journal_type, status, created_by)
                VALUES (:eid, :dt, :ref, :desc, 'opening', 'posted', 'system')
                RETURNING id
            """),
            {
                "eid":  str(sess.entity_id),
                "dt":   str(sess.opening_date),
                "ref":  ref_no,
                "desc": f"Opening Balance per {sess.opening_date}",
            },
        ).fetchone()
        journal_id = journal.id

        for row in gl_rows:
            # Get or fallback to a placeholder account_id
            acc_id = row.account_id
            if not acc_id:
                # Auto-create the account if not found
                new_acc = db.execute(
                    text("""
                        INSERT INTO chart_of_accounts
                            (entity_id, account_code, account_name, account_type, normal_balance)
                        VALUES (:eid, :code, :name, 'asset', 'debit')
                        ON CONFLICT (entity_id, account_code) DO UPDATE SET account_name=EXCLUDED.account_name
                        RETURNING id
                    """),
                    {"eid": str(sess.entity_id), "code": row.account_code, "name": row.account_code},
                ).fetchone()
                acc_id = new_acc.id

            if Decimal(str(row.debit_balance)) > 0:
                db.execute(
                    text("""
                        INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr, entity_id)
                        VALUES (:jid, :aid, :desc, :dr, 0, :eid)
                    """),
                    {
                        "jid":  str(journal_id), "aid": str(acc_id),
                        "desc": f"Opening Balance {row.account_code}",
                        "dr":   Decimal(str(row.debit_balance)),
                        "eid":  str(sess.entity_id),
                    },
                )
            if Decimal(str(row.credit_balance)) > 0:
                db.execute(
                    text("""
                        INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr, entity_id)
                        VALUES (:jid, :aid, :desc, 0, :cr, :eid)
                    """),
                    {
                        "jid":  str(journal_id), "aid": str(acc_id),
                        "desc": f"Opening Balance {row.account_code}",
                        "cr":   Decimal(str(row.credit_balance)),
                        "eid":  str(sess.entity_id),
                    },
                )

        return journal_id

    # ── Private: Import AR ────────────────────────────────────────────────────

    @staticmethod
    def _import_ar(db: Session, sess) -> int:
        items = db.execute(
            text("SELECT * FROM opening_balance_ar WHERE session_id=:sid AND imported=FALSE"),
            {"sid": str(sess.id)},
        ).fetchall()

        for item in items:
            # Find or create customer
            customer_id = item.customer_id
            if not customer_id:
                cust = db.execute(
                    text("""
                        INSERT INTO customer (entity_id, customer_name, customer_code, is_active)
                        VALUES (:eid, :name, :code, TRUE)
                        ON CONFLICT (entity_id, customer_code) DO UPDATE SET customer_name=EXCLUDED.customer_name
                        RETURNING id
                    """),
                    {
                        "eid":  str(sess.entity_id),
                        "name": item.customer_name,
                        "code": f"CUST-{item.customer_name[:10].upper().replace(' ', '-')}",
                    },
                ).fetchone()
                customer_id = cust.id

            # Find AR account
            ar_acc = db.execute(
                text("""
                    SELECT id FROM chart_of_accounts
                    WHERE entity_id=:eid AND account_type='asset'
                      AND (account_code LIKE '1-12%' OR account_name ILIKE '%piutang usaha%')
                    ORDER BY account_code LIMIT 1
                """),
                {"eid": str(sess.entity_id)},
            ).fetchone()
            ar_acc_id = ar_acc.id if ar_acc else None

            # Create AR invoice with opening_balance flag
            db.execute(
                text("""
                    INSERT INTO ar_invoice
                        (entity_id, customer_id, invoice_number, invoice_date, due_date,
                         total_amount, amount_remaining, currency, exchange_rate,
                         status, description, is_opening_balance, ar_account_id)
                    VALUES (:eid, :cid, :inv, :idate, :ddate,
                            :orig, :rem, :cur, :fx,
                            'open', :desc, TRUE, :ar_acc)
                    ON CONFLICT (entity_id, invoice_number) DO NOTHING
                """),
                {
                    "eid":    str(sess.entity_id),
                    "cid":    str(customer_id),
                    "inv":    item.invoice_number,
                    "idate":  str(item.invoice_date),
                    "ddate":  str(item.due_date) if item.due_date else None,
                    "orig":   item.original_amount,
                    "rem":    item.amount_remaining,
                    "cur":    item.currency,
                    "fx":     item.exchange_rate,
                    "desc":   item.description or f"Opening Balance - {item.invoice_number}",
                    "ar_acc": str(ar_acc_id) if ar_acc_id else None,
                },
            )

            db.execute(
                text("UPDATE opening_balance_ar SET imported=TRUE WHERE id=:id"),
                {"id": str(item.id)},
            )

        return len(items)

    # ── Private: Import AP ────────────────────────────────────────────────────

    @staticmethod
    def _import_ap(db: Session, sess) -> int:
        items = db.execute(
            text("SELECT * FROM opening_balance_ap WHERE session_id=:sid AND imported=FALSE"),
            {"sid": str(sess.id)},
        ).fetchall()

        for item in items:
            vendor_id = item.vendor_id
            if not vendor_id:
                vend = db.execute(
                    text("""
                        INSERT INTO vendor (entity_id, vendor_name, vendor_code, is_active)
                        VALUES (:eid, :name, :code, TRUE)
                        ON CONFLICT (entity_id, vendor_code) DO UPDATE SET vendor_name=EXCLUDED.vendor_name
                        RETURNING id
                    """),
                    {
                        "eid":  str(sess.entity_id),
                        "name": item.vendor_name,
                        "code": f"VND-{item.vendor_name[:10].upper().replace(' ', '-')}",
                    },
                ).fetchone()
                vendor_id = vend.id

            db.execute(
                text("""
                    INSERT INTO ap_invoice
                        (entity_id, vendor_id, invoice_number, invoice_date, due_date,
                         total_amount, amount_remaining, currency, exchange_rate,
                         status, description, is_opening_balance)
                    VALUES (:eid, :vid, :inv, :idate, :ddate,
                            :orig, :rem, :cur, :fx, 'open', :desc, TRUE)
                    ON CONFLICT (entity_id, invoice_number) DO NOTHING
                """),
                {
                    "eid":   str(sess.entity_id), "vid": str(vendor_id),
                    "inv":   item.invoice_number,
                    "idate": str(item.invoice_date),
                    "ddate": str(item.due_date) if item.due_date else None,
                    "orig":  item.original_amount, "rem": item.amount_remaining,
                    "cur":   item.currency, "fx": item.exchange_rate,
                    "desc":  item.description or f"Opening Balance - {item.invoice_number}",
                },
            )
            db.execute(text("UPDATE opening_balance_ap SET imported=TRUE WHERE id=:id"), {"id": str(item.id)})

        return len(items)

    # ── Private: Import Fixed Assets ──────────────────────────────────────────

    @staticmethod
    def _import_assets(db: Session, sess) -> int:
        items = db.execute(
            text("SELECT * FROM opening_balance_asset WHERE session_id=:sid AND imported=FALSE"),
            {"sid": str(sess.id)},
        ).fetchall()

        for a in items:
            db.execute(
                text("""
                    INSERT INTO fixed_asset
                        (entity_id, asset_code, asset_name, category, location,
                         acquisition_date, acquisition_cost, accumulated_depreciation,
                         net_book_value, useful_life_months, depreciation_method,
                         salvage_value, gl_asset_account_code, gl_acc_depr_account_code,
                         gl_depr_expense_account_code, serial_number, status,
                         is_opening_balance)
                    VALUES (:eid, :code, :name, :cat, :loc,
                            :aqdt, :aqcost, :accdepr,
                            :nbv, :life, :method, :salvage,
                            :gl_asset, :gl_depr, :gl_exp,
                            :serial, 'active', TRUE)
                    ON CONFLICT DO NOTHING
                """),
                {
                    "eid":     str(sess.entity_id),
                    "code":    a.asset_code or f"AST-{str(a.id)[:8]}",
                    "name":    a.asset_name,
                    "cat":     a.category,
                    "loc":     a.location,
                    "aqdt":    str(a.acquisition_date),
                    "aqcost":  a.acquisition_cost,
                    "accdepr": a.accumulated_depreciation,
                    "nbv":     a.net_book_value,
                    "life":    a.useful_life_months,
                    "method":  a.depreciation_method,
                    "salvage": a.salvage_value,
                    "gl_asset": a.gl_asset_account,
                    "gl_depr":  a.gl_depr_account,
                    "gl_exp":   a.gl_expense_account,
                    "serial":   a.serial_number,
                },
            )
            db.execute(text("UPDATE opening_balance_asset SET imported=TRUE WHERE id=:id"), {"id": str(a.id)})

        return len(items)

    # ── Private: Import Inventory ─────────────────────────────────────────────

    @staticmethod
    def _import_inventory(db: Session, sess) -> int:
        items = db.execute(
            text("SELECT * FROM opening_balance_inventory WHERE session_id=:sid AND imported=FALSE"),
            {"sid": str(sess.id)},
        ).fetchall()

        for item in items:
            product_id = item.product_id
            if not product_id:
                prod = db.execute(
                    text("""
                        INSERT INTO product_product
                            (entity_id, product_code, product_name, current_avg_cost, active)
                        VALUES (:eid, :code, :name, :cost, TRUE)
                        ON CONFLICT (entity_id, product_code)
                        DO UPDATE SET current_avg_cost=EXCLUDED.current_avg_cost
                        RETURNING id
                    """),
                    {
                        "eid":  str(sess.entity_id),
                        "code": item.product_code,
                        "name": item.product_name or item.product_code,
                        "cost": item.unit_cost,
                    },
                ).fetchone()
                product_id = prod.id

            warehouse_id = item.warehouse_id
            if not warehouse_id:
                wh = db.execute(
                    text("""
                        SELECT id FROM warehouse
                        WHERE entity_id=:eid AND warehouse_code=:code
                        LIMIT 1
                    """),
                    {"eid": str(sess.entity_id), "code": item.warehouse_code or "DEFAULT"},
                ).fetchone()
                warehouse_id = wh.id if wh else None

            # Create opening stock_move
            db.execute(
                text("""
                    INSERT INTO stock_move
                        (entity_id, product_id, move_type, quantity, unit_cost,
                         destination_warehouse_id, move_date, reference, is_opening_balance)
                    VALUES (:eid, :pid, 'opening', :qty, :cost,
                            :wid, :dt, 'Opening Balance', TRUE)
                """),
                {
                    "eid":  str(sess.entity_id),
                    "pid":  str(product_id),
                    "qty":  item.quantity,
                    "cost": item.unit_cost,
                    "wid":  str(warehouse_id) if warehouse_id else None,
                    "dt":   str(sess.opening_date),
                },
            )

            # Update avg cost
            db.execute(
                text("UPDATE product_product SET current_avg_cost=:cost WHERE id=:pid"),
                {"cost": item.unit_cost, "pid": str(product_id)},
            )

            db.execute(text("UPDATE opening_balance_inventory SET imported=TRUE WHERE id=:id"), {"id": str(item.id)})

        return len(items)

    # ── Private: Update Bank Balances ─────────────────────────────────────────

    @staticmethod
    def _update_bank_balances(db: Session, sess) -> int:
        items = db.execute(
            text("SELECT * FROM opening_balance_bank WHERE session_id=:sid AND imported=FALSE"),
            {"sid": str(sess.id)},
        ).fetchall()

        for b in items:
            if b.bank_account_id:
                db.execute(
                    text("""
                        UPDATE bank_account
                        SET opening_balance=:bal, opening_balance_date=:dt
                        WHERE id=:id
                    """),
                    {"bal": b.opening_balance, "dt": str(sess.opening_date), "id": str(b.bank_account_id)},
                )
            else:
                db.execute(
                    text("""
                        INSERT INTO bank_account
                            (entity_id, bank_name, account_number, account_holder,
                             currency, opening_balance, opening_balance_date, gl_account_code, active)
                        VALUES (:eid, :bname, :anum, :holder, :cur, :bal, :dt, :gl, TRUE)
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "eid":    str(sess.entity_id),
                        "bname":  b.bank_name,
                        "anum":   b.account_number or "",
                        "holder": b.account_holder,
                        "cur":    b.currency,
                        "bal":    b.opening_balance,
                        "dt":     str(sess.opening_date),
                        "gl":     b.gl_account_code,
                    },
                )
            db.execute(text("UPDATE opening_balance_bank SET imported=TRUE WHERE id=:id"), {"id": str(b.id)})

        return len(items)

    # ── Private: Setup Leave ──────────────────────────────────────────────────

    @staticmethod
    def _setup_leave(db: Session, sess) -> int:
        items = db.execute(
            text("SELECT * FROM opening_balance_leave WHERE session_id=:sid AND imported=FALSE"),
            {"sid": str(sess.id)},
        ).fetchall()

        for lv in items:
            employee_id = lv.employee_id
            if not employee_id and lv.employee_code:
                emp = db.execute(
                    text("SELECT id FROM employee WHERE entity_id=:eid AND employee_code=:code"),
                    {"eid": str(sess.entity_id), "code": lv.employee_code},
                ).fetchone()
                if emp:
                    employee_id = emp.id

            if not employee_id:
                db.execute(text("UPDATE opening_balance_leave SET imported=TRUE WHERE id=:id"), {"id": str(lv.id)})
                continue

            # Find leave_type by code
            lt = db.execute(
                text("SELECT id FROM leave_type WHERE entity_id=:eid AND leave_code=:code"),
                {"eid": str(sess.entity_id), "code": lv.leave_type_code},
            ).fetchone()
            if not lt:
                db.execute(text("UPDATE opening_balance_leave SET imported=TRUE WHERE id=:id"), {"id": str(lv.id)})
                continue

            db.execute(
                text("""
                    INSERT INTO leave_entitlement
                        (entity_id, employee_id, leave_type_id, fiscal_year,
                         entitled_days, carry_forward, used_days)
                    VALUES (:eid, :empid, :ltid, :yr, :ent, :cf, :used)
                    ON CONFLICT (entity_id, employee_id, leave_type_id, fiscal_year)
                    DO UPDATE SET
                        entitled_days = EXCLUDED.entitled_days,
                        carry_forward = EXCLUDED.carry_forward,
                        used_days     = EXCLUDED.used_days
                """),
                {
                    "eid":   str(sess.entity_id),
                    "empid": str(employee_id),
                    "ltid":  str(lt.id),
                    "yr":    lv.fiscal_year,
                    "ent":   lv.entitled_days,
                    "cf":    lv.carry_forward,
                    "used":  lv.used_days,
                },
            )
            db.execute(text("UPDATE opening_balance_leave SET imported=TRUE WHERE id=:id"), {"id": str(lv.id)})

        return len(items)

    # ── Private: Ensure Fiscal Year/Period ────────────────────────────────────

    @staticmethod
    def _ensure_fiscal_period(db: Session, sess) -> None:
        from modules.year_end_closing_engine import YearEndClosingEngine
        try:
            YearEndClosingEngine.setup_fiscal_year(db, sess.entity_id, sess.fiscal_year)
        except Exception:
            pass  # Already exists is fine

    # ── Get Session Status ────────────────────────────────────────────────────

    @staticmethod
    def get_status(db: Session, entity_id: UUID) -> dict:
        row = db.execute(
            text("SELECT * FROM vw_opening_balance_status WHERE entity_id=:eid"),
            {"eid": str(entity_id)},
        ).fetchone()
        if not row:
            return {"has_session": False}
        result = dict(row._mapping)
        result["has_session"] = True

        # Completion percentage
        flags = [row.gl_done, row.ar_done, row.ap_done,
                 row.asset_done, row.inventory_done, row.bank_done, row.leave_done]
        result["completion_pct"] = round(sum(1 for f in flags if f) / len(flags) * 100)
        return result
