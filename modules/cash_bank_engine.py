"""
Cash & Bank Engine — Kas/Bank Transactions (non-AP/AR), Petty Cash (Imprest), In-house Transfer

Alur:
  1. create_cash_account()         -> Daftarkan kas tunai / kas kecil + akun COA terkait
  2. create_cash_transaction()     -> Draft transaksi kas/bank masuk-keluar non-AP/AR
                                       (account_type 'cash' atau 'bank' — mis. angsuran
                                       bank, bunga, bank charge, WHT bunga di sisi bank;
                                       setoran tunai, dll di sisi cash)
     post_cash_transaction()       -> Posting GL (Dr/Cr akun Kas/Bank vs baris akun lawan)
  3. create_petty_cash_expense()   -> Draft pengeluaran kas kecil (sistem imprest)
     post_petty_cash_expense()     -> Posting GL (Dr Expense | Cr Kas Kecil)
  4. create_in_house_transfer()    -> Draft transfer antar bank_account/cash_account
     post_in_house_transfer()      -> Posting GL (Dr Tujuan | Cr Asal), opsional
                                       langsung tandai petty cash expense 'replenished'
                                       kalau purpose='petty_cash_topup'
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from modules.journal_engine import JournalEngine, JournalEntry, JournalLine
from modules.exchange_rate_engine import ExchangeRateEngine


class CashBankEngine:

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _coa_code_for_cash_account(db: Session, cash_account_id: UUID) -> dict:
        row = db.execute(
            text("""
                SELECT ca.entity_id, ca.account_name, coa.account_code
                FROM cash_account ca
                JOIN chart_of_accounts coa ON coa.id = ca.coa_id
                WHERE ca.id = :id
            """),
            {"id": str(cash_account_id)},
        ).fetchone()
        if not row:
            raise ValueError(f"Cash account {cash_account_id} tidak ditemukan.")
        return {"entity_id": row.entity_id, "name": row.account_name, "account_code": row.account_code}

    @staticmethod
    def _coa_code_for_bank_account(db: Session, bank_account_id: UUID) -> dict:
        row = db.execute(
            text("""
                SELECT ba.entity_id, ba.account_name, coa.account_code
                FROM bank_account ba
                JOIN chart_of_accounts coa ON coa.id = ba.coa_id
                WHERE ba.id = :id
            """),
            {"id": str(bank_account_id)},
        ).fetchone()
        if not row:
            raise ValueError(f"Bank account {bank_account_id} tidak ditemukan atau belum punya COA.")
        return {"entity_id": row.entity_id, "name": row.account_name, "account_code": row.account_code}

    @staticmethod
    def _resolve_account(db: Session, acc_type: str, acc_id: UUID) -> dict:
        if acc_type == "bank":
            return CashBankEngine._coa_code_for_bank_account(db, acc_id)
        if acc_type == "cash":
            return CashBankEngine._coa_code_for_cash_account(db, acc_id)
        raise ValueError(f"source_type/dest_type tidak valid: {acc_type}")

    @staticmethod
    def _project_code(db: Session, project_id: Optional[UUID]) -> Optional[str]:
        if not project_id:
            return None
        row = db.execute(
            text("SELECT project_code FROM project WHERE id = :id"),
            {"id": str(project_id)},
        ).fetchone()
        return row.project_code if row else None

    @staticmethod
    def _next_doc_no(db: Session, table: str, prefix: str, entity_id: UUID, doc_date: date, no_column: str = "transaction_no") -> str:
        seq_row = db.execute(
            text(f"""
                SELECT COUNT(*) + 1 AS seq FROM {table}
                WHERE entity_id = :eid
                  AND {no_column} LIKE :pat
            """),
            {"eid": str(entity_id), "pat": f"{prefix}-{doc_date.strftime('%Y%m')}-%"},
        ).fetchone()
        return f"{prefix}-{doc_date.strftime('%Y%m')}-{seq_row.seq:04d}"

    # ─────────────────────────────────────────────────────────────────────────
    # 1. CASH ACCOUNT MASTER
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def create_cash_account(
        db: Session,
        entity_id: UUID,
        account_name: str,
        coa_code: str,
        account_type: str = "cash",
        custodian_name: Optional[str] = None,
        float_amount: Decimal = Decimal("0"),
    ) -> dict:
        if account_type not in ("cash", "petty_cash"):
            raise ValueError("account_type harus 'cash' atau 'petty_cash'.")

        coa = db.execute(
            text("""
                SELECT id FROM chart_of_accounts
                WHERE entity_id = :eid AND account_code = :code AND is_active = TRUE
            """),
            {"eid": str(entity_id), "code": coa_code},
        ).fetchone()
        if not coa:
            raise ValueError(f"COA '{coa_code}' tidak ditemukan. Pastikan COA sudah dibuat.")

        acc_id = uuid4()
        db.execute(
            text("""
                INSERT INTO cash_account
                    (id, entity_id, account_name, account_type, coa_id, custodian_name, float_amount)
                VALUES
                    (:id, :eid, :name, :type, :coa, :custodian, :float)
            """),
            {
                "id": str(acc_id), "eid": str(entity_id), "name": account_name,
                "type": account_type, "coa": str(coa.id), "custodian": custodian_name,
                "float": float(float_amount),
            },
        )
        db.commit()
        return {"cash_account_id": str(acc_id), "account_name": account_name, "account_type": account_type}

    @staticmethod
    def list_cash_accounts(db: Session, entity_id: UUID, account_type: Optional[str] = None) -> list[dict]:
        filters = ["ca.entity_id = :eid"]
        params: dict = {"eid": str(entity_id)}
        if account_type:
            filters.append("ca.account_type = :type")
            params["type"] = account_type

        rows = db.execute(
            text(f"""
                SELECT ca.id, ca.account_name, ca.account_type, ca.custodian_name,
                       ca.float_amount, ca.is_active,
                       coa.account_code, coa.account_name AS coa_name
                FROM cash_account ca
                JOIN chart_of_accounts coa ON coa.id = ca.coa_id
                WHERE {" AND ".join(filters)}
                ORDER BY ca.account_type, ca.account_name
            """),
            params,
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def get_cash_account_balance(db: Session, cash_account_id: UUID) -> dict:
        """Saldo kas berdasarkan posisi GL aktual (debit - credit) pada akun COA terkait."""
        acc = db.execute(
            text("""
                SELECT ca.id, ca.account_name, ca.entity_id, coa.id AS coa_id, coa.account_code
                FROM cash_account ca
                JOIN chart_of_accounts coa ON coa.id = ca.coa_id
                WHERE ca.id = :id
            """),
            {"id": str(cash_account_id)},
        ).fetchone()
        if not acc:
            raise ValueError(f"Cash account {cash_account_id} tidak ditemukan.")

        bal = db.execute(
            text("""
                SELECT COALESCE(SUM(gl.debit_idr), 0) AS total_debit,
                       COALESCE(SUM(gl.credit_idr), 0) AS total_credit
                FROM gl_line gl
                JOIN gl_journal gj ON gj.id = gl.journal_id AND gj.status = 'posted'
                WHERE gl.account_id = :coa_id
            """),
            {"coa_id": str(acc.coa_id)},
        ).fetchone()

        balance = float(bal.total_debit) - float(bal.total_credit)
        return {
            "cash_account_id": str(cash_account_id),
            "account_name": acc.account_name,
            "account_code": acc.account_code,
            "balance": balance,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 2. CASH TRANSACTION (kas masuk/keluar non-AP/AR)
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def create_cash_transaction(
        db: Session,
        entity_id: UUID,
        account_type: str,
        account_id: UUID,
        transaction_date: date,
        direction: str,
        description: str,
        lines: list[dict],
        currency: str = "IDR",
        created_by: str = "system",
    ) -> dict:
        """
        lines: [{account_code, description, amount, cost_center, project_id}]
        amount pada setiap baris diisi dalam `currency`. Kalau currency != IDR,
        dikonversi ke IDR (kurs tanggal transaction_date) untuk posting GL —
        nilai asli disimpan sebagai amount_fcy untuk tracking FCY.
        """
        if account_type not in ("bank", "cash"):
            raise ValueError("account_type harus 'bank' atau 'cash'.")
        if direction not in ("in", "out"):
            raise ValueError("direction harus 'in' atau 'out'.")
        if not lines:
            raise ValueError("Minimal 1 baris transaksi diperlukan.")

        currency = currency.upper()
        rate = ExchangeRateEngine.get_rate_or_raise(db, currency, "IDR", transaction_date) \
            if currency != "IDR" else Decimal("1")

        total_fcy = sum(Decimal(str(l["amount"])) for l in lines)
        if total_fcy <= 0:
            raise ValueError("Total transaksi harus lebih dari 0.")
        total_idr = (total_fcy * rate).quantize(Decimal("1")) if currency != "IDR" else total_fcy

        # Validasi akun ada dan satu entity
        CashBankEngine._resolve_account(db, account_type, account_id)

        txn_no = CashBankEngine._next_doc_no(db, "cash_transaction", "CTX", entity_id, transaction_date)

        txn_id = uuid4()
        db.execute(
            text("""
                INSERT INTO cash_transaction
                    (id, entity_id, account_type, account_id, transaction_no, transaction_date,
                     direction, description, amount, currency, exchange_rate, amount_fcy, status, created_by)
                VALUES
                    (:id, :eid, :atype, :aid, :no, :dt, :dir, :desc, :amt, :cur, :rate, :fcy, 'draft', :by)
            """),
            {
                "id": str(txn_id), "eid": str(entity_id), "atype": account_type, "aid": str(account_id),
                "no": txn_no, "dt": transaction_date, "dir": direction,
                "desc": description, "amt": float(total_idr), "by": created_by,
                "cur": currency, "rate": float(rate),
                "fcy": float(total_fcy) if currency != "IDR" else None,
            },
        )

        for i, l in enumerate(lines, start=1):
            line_fcy = Decimal(str(l["amount"]))
            line_idr = (line_fcy * rate).quantize(Decimal("1")) if currency != "IDR" else line_fcy
            db.execute(
                text("""
                    INSERT INTO cash_transaction_line
                        (id, cash_transaction_id, line_no, account_code, description,
                         amount, amount_fcy, cost_center, project_id)
                    VALUES
                        (:id, :tid, :lno, :code, :desc, :amt, :fcy, :cc, :proj)
                """),
                {
                    "id": str(uuid4()), "tid": str(txn_id), "lno": i,
                    "fcy": float(line_fcy) if currency != "IDR" else None,
                    "code": l["account_code"], "desc": l.get("description"),
                    "amt": float(line_idr), "cc": l.get("cost_center"),
                    "proj": str(l["project_id"]) if l.get("project_id") else None,
                },
            )

        db.commit()
        return {
            "cash_transaction_id": str(txn_id), "transaction_no": txn_no, "status": "draft",
            "total": float(total_idr), "currency": currency,
            "total_fcy": float(total_fcy) if currency != "IDR" else None,
        }

    @staticmethod
    def post_cash_transaction(db: Session, transaction_id: UUID, posted_by: str = "system") -> dict:
        txn = db.execute(
            text("SELECT * FROM cash_transaction WHERE id = :id"),
            {"id": str(transaction_id)},
        ).fetchone()
        if not txn:
            raise ValueError("Transaksi kas tidak ditemukan.")
        if txn.status != "draft":
            raise ValueError(f"Transaksi berstatus '{txn.status}', hanya 'draft' yang bisa diposting.")

        acc = CashBankEngine._resolve_account(db, txn.account_type, txn.account_id)
        is_fcy = txn.currency and txn.currency != "IDR"

        lines = db.execute(
            text("""
                SELECT account_code, description, amount, amount_fcy, cost_center, project_id
                FROM cash_transaction_line
                WHERE cash_transaction_id = :tid
                ORDER BY line_no
            """),
            {"tid": str(transaction_id)},
        ).fetchall()

        journal_lines: list[JournalLine] = []
        for l in lines:
            proj_code = CashBankEngine._project_code(db, l.project_id)
            fcy_kwargs = dict(
                currency=txn.currency if is_fcy else None,
                amount_fcy=Decimal(str(l.amount_fcy)) if is_fcy and l.amount_fcy is not None else None,
                exchange_rate=Decimal(str(txn.exchange_rate)) if is_fcy else None,
            )
            if txn.direction == "in":
                journal_lines.append(JournalLine(
                    account_code=l.account_code, description=l.description or txn.description,
                    credit_idr=Decimal(str(l.amount)), cost_center=l.cost_center, project_code=proj_code,
                    **fcy_kwargs,
                ))
            else:
                journal_lines.append(JournalLine(
                    account_code=l.account_code, description=l.description or txn.description,
                    debit_idr=Decimal(str(l.amount)), cost_center=l.cost_center, project_code=proj_code,
                    **fcy_kwargs,
                ))

        cash_side = JournalLine(
            account_code=acc["account_code"], description=txn.description,
            debit_idr=Decimal(str(txn.amount)) if txn.direction == "in" else Decimal("0"),
            credit_idr=Decimal(str(txn.amount)) if txn.direction == "out" else Decimal("0"),
            currency=txn.currency if is_fcy else None,
            amount_fcy=Decimal(str(txn.amount_fcy)) if is_fcy and txn.amount_fcy is not None else None,
            exchange_rate=Decimal(str(txn.exchange_rate)) if is_fcy else None,
        )

        entry = JournalEntry(
            entity_id=txn.entity_id,
            journal_type="CASH",
            journal_date=txn.transaction_date,
            description=f"{txn.transaction_no} — {txn.description}",
            lines=[cash_side] + journal_lines,
            reference_no=txn.transaction_no,
            currency=txn.currency,
            fx_rate=Decimal(str(txn.exchange_rate)),
            source="cash_bank",
            created_by=posted_by,
        )

        result = JournalEngine(db).post_journal(entry)
        if not result["success"]:
            raise ValueError(result["error"])

        db.execute(
            text("UPDATE cash_transaction SET status = 'posted', journal_id = :jid WHERE id = :id"),
            {"jid": result["journal_id"], "id": str(transaction_id)},
        )
        db.commit()
        return {"cash_transaction_id": str(transaction_id), "status": "posted", "journal_id": result["journal_id"], "journal_no": result["journal_no"]}

    @staticmethod
    def list_cash_transactions(
        db: Session,
        entity_id: UUID,
        account_type: Optional[str] = None,
        account_id: Optional[UUID] = None,
        status: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> list[dict]:
        filters = ["ct.entity_id = :eid"]
        params: dict = {"eid": str(entity_id)}
        if account_type:
            filters.append("ct.account_type = :atype")
            params["atype"] = account_type
        if account_id:
            filters.append("ct.account_id = :aid")
            params["aid"] = str(account_id)
        if status:
            filters.append("ct.status = :status")
            params["status"] = status
        if date_from:
            filters.append("ct.transaction_date >= :df")
            params["df"] = date_from
        if date_to:
            filters.append("ct.transaction_date <= :dt")
            params["dt"] = date_to

        rows = db.execute(
            text(f"""
                SELECT ct.id, ct.transaction_no, ct.transaction_date, ct.direction,
                       ct.description, ct.amount, ct.currency, ct.amount_fcy, ct.status, ct.journal_id,
                       ct.account_type, ct.account_id,
                       COALESCE(ca.account_name, ba.bank_name || ' — ' || ba.account_no) AS account_name
                FROM cash_transaction ct
                LEFT JOIN cash_account ca ON ca.id = ct.account_id AND ct.account_type = 'cash'
                LEFT JOIN bank_account ba ON ba.id = ct.account_id AND ct.account_type = 'bank'
                WHERE {" AND ".join(filters)}
                ORDER BY ct.transaction_date DESC, ct.created_at DESC
            """),
            params,
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def get_cash_transaction(db: Session, transaction_id: UUID) -> dict:
        txn = db.execute(
            text("""
                SELECT ct.*,
                       COALESCE(ca.account_name, ba.bank_name || ' — ' || ba.account_no) AS account_name
                FROM cash_transaction ct
                LEFT JOIN cash_account ca ON ca.id = ct.account_id AND ct.account_type = 'cash'
                LEFT JOIN bank_account ba ON ba.id = ct.account_id AND ct.account_type = 'bank'
                WHERE ct.id = :id
            """),
            {"id": str(transaction_id)},
        ).fetchone()
        if not txn:
            raise ValueError("Transaksi kas tidak ditemukan.")

        lines = db.execute(
            text("""
                SELECT line_no, account_code, description, amount, cost_center, project_id
                FROM cash_transaction_line
                WHERE cash_transaction_id = :tid
                ORDER BY line_no
            """),
            {"tid": str(transaction_id)},
        ).fetchall()

        result = dict(txn._mapping)
        result["lines"] = [dict(l._mapping) for l in lines]
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # 3. PETTY CASH EXPENSE (sistem imprest)
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def create_petty_cash_expense(
        db: Session,
        entity_id: UUID,
        cash_account_id: UUID,
        expense_date: date,
        account_code: str,
        amount: Decimal,
        description: Optional[str] = None,
        cost_center: Optional[str] = None,
        project_id: Optional[UUID] = None,
        receipt_ref: Optional[str] = None,
        currency: str = "IDR",
        created_by: str = "system",
    ) -> dict:
        if amount <= 0:
            raise ValueError("Amount harus lebih dari 0.")

        currency = currency.upper()
        rate = ExchangeRateEngine.get_rate_or_raise(db, currency, "IDR", expense_date) \
            if currency != "IDR" else Decimal("1")
        amount_fcy = Decimal(str(amount))
        amount_idr = (amount_fcy * rate).quantize(Decimal("1")) if currency != "IDR" else amount_fcy

        exp_id = uuid4()
        db.execute(
            text("""
                INSERT INTO petty_cash_expense
                    (id, entity_id, cash_account_id, expense_date, account_code, description,
                     amount, currency, exchange_rate, amount_fcy, cost_center, project_id, receipt_ref, status, created_by)
                VALUES
                    (:id, :eid, :cid, :dt, :code, :desc, :amt, :cur, :rate, :fcy, :cc, :proj, :receipt, 'draft', :by)
            """),
            {
                "id": str(exp_id), "eid": str(entity_id), "cid": str(cash_account_id),
                "dt": expense_date, "code": account_code, "desc": description,
                "amt": float(amount_idr), "cur": currency, "rate": float(rate),
                "fcy": float(amount_fcy) if currency != "IDR" else None, "cc": cost_center,
                "proj": str(project_id) if project_id else None,
                "receipt": receipt_ref, "by": created_by,
            },
        )
        db.commit()
        return {"petty_cash_expense_id": str(exp_id), "status": "draft", "amount": float(amount_idr), "currency": currency}

    @staticmethod
    def post_petty_cash_expense(db: Session, expense_id: UUID, posted_by: str = "system") -> dict:
        exp = db.execute(
            text("SELECT * FROM petty_cash_expense WHERE id = :id"),
            {"id": str(expense_id)},
        ).fetchone()
        if not exp:
            raise ValueError("Petty cash expense tidak ditemukan.")
        if exp.status != "draft":
            raise ValueError(f"Expense berstatus '{exp.status}', hanya 'draft' yang bisa diposting.")

        cash_acc = CashBankEngine._coa_code_for_cash_account(db, exp.cash_account_id)
        proj_code = CashBankEngine._project_code(db, exp.project_id)
        is_fcy = exp.currency and exp.currency != "IDR"
        fcy_kwargs = dict(
            currency=exp.currency if is_fcy else None,
            amount_fcy=Decimal(str(exp.amount_fcy)) if is_fcy and exp.amount_fcy is not None else None,
            exchange_rate=Decimal(str(exp.exchange_rate)) if is_fcy else None,
        )

        entry = JournalEntry(
            entity_id=exp.entity_id,
            journal_type="CASH",
            journal_date=exp.expense_date,
            description=f"Kas Kecil — {exp.description or exp.account_code}",
            lines=[
                JournalLine(
                    account_code=exp.account_code, description=exp.description or "Pengeluaran kas kecil",
                    debit_idr=Decimal(str(exp.amount)), cost_center=exp.cost_center, project_code=proj_code,
                    **fcy_kwargs,
                ),
                JournalLine(
                    account_code=cash_acc["account_code"], description=f"Kas kecil — {exp.receipt_ref or exp.description or ''}",
                    credit_idr=Decimal(str(exp.amount)),
                    **fcy_kwargs,
                ),
            ],
            reference_no=exp.receipt_ref,
            currency=exp.currency, fx_rate=Decimal(str(exp.exchange_rate)),
            source="cash_bank",
            created_by=posted_by,
        )

        result = JournalEngine(db).post_journal(entry)
        if not result["success"]:
            raise ValueError(result["error"])

        db.execute(
            text("UPDATE petty_cash_expense SET status = 'posted', journal_id = :jid WHERE id = :id"),
            {"jid": result["journal_id"], "id": str(expense_id)},
        )
        db.commit()
        return {"petty_cash_expense_id": str(expense_id), "status": "posted", "journal_id": result["journal_id"], "journal_no": result["journal_no"]}

    @staticmethod
    def list_petty_cash_expenses(
        db: Session,
        entity_id: UUID,
        cash_account_id: Optional[UUID] = None,
        status: Optional[str] = None,
        replenished: Optional[bool] = None,
    ) -> list[dict]:
        filters = ["pce.entity_id = :eid"]
        params: dict = {"eid": str(entity_id)}
        if cash_account_id:
            filters.append("pce.cash_account_id = :cid")
            params["cid"] = str(cash_account_id)
        if status:
            filters.append("pce.status = :status")
            params["status"] = status
        if replenished is not None:
            filters.append("pce.replenished = :repl")
            params["repl"] = replenished

        rows = db.execute(
            text(f"""
                SELECT pce.id, pce.expense_date, pce.account_code, pce.description,
                       pce.amount, pce.currency, pce.amount_fcy, pce.cost_center, pce.receipt_ref, pce.status,
                       pce.replenished, pce.journal_id,
                       ca.account_name AS cash_account_name
                FROM petty_cash_expense pce
                JOIN cash_account ca ON ca.id = pce.cash_account_id
                WHERE {" AND ".join(filters)}
                ORDER BY pce.expense_date DESC
            """),
            params,
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def get_outstanding_petty_cash(db: Session, cash_account_id: UUID) -> dict:
        """Total expense yang sudah posted tapi belum direplenish — dasar untuk top-up."""
        row = db.execute(
            text("""
                SELECT COALESCE(SUM(amount), 0) AS total_outstanding, COUNT(*) AS count_items
                FROM petty_cash_expense
                WHERE cash_account_id = :cid AND status = 'posted' AND replenished = FALSE
            """),
            {"cid": str(cash_account_id)},
        ).fetchone()
        return {"cash_account_id": str(cash_account_id), "total_outstanding": float(row.total_outstanding), "count_items": row.count_items}

    # ─────────────────────────────────────────────────────────────────────────
    # 4. IN-HOUSE TRANSFER
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def create_in_house_transfer(
        db: Session,
        entity_id: UUID,
        transfer_date: date,
        source_type: str,
        source_id: UUID,
        dest_type: str,
        dest_id: UUID,
        amount: Decimal,
        purpose: str = "transfer",
        description: Optional[str] = None,
        currency: str = "IDR",
        created_by: str = "system",
    ) -> dict:
        if source_type not in ("bank", "cash") or dest_type not in ("bank", "cash"):
            raise ValueError("source_type/dest_type harus 'bank' atau 'cash'.")
        if purpose not in ("transfer", "petty_cash_topup"):
            raise ValueError("purpose harus 'transfer' atau 'petty_cash_topup'.")
        if amount <= 0:
            raise ValueError("Amount harus lebih dari 0.")
        if source_type == dest_type and str(source_id) == str(dest_id):
            raise ValueError("Sumber dan tujuan transfer tidak boleh sama.")

        # Validasi kedua akun ada dan satu entity
        src = CashBankEngine._resolve_account(db, source_type, source_id)
        dst = CashBankEngine._resolve_account(db, dest_type, dest_id)
        if str(src["entity_id"]) != str(entity_id) or str(dst["entity_id"]) != str(entity_id):
            raise ValueError("Sumber/tujuan transfer harus berada pada entity yang sama dengan entity_id.")

        currency = currency.upper()
        rate = ExchangeRateEngine.get_rate_or_raise(db, currency, "IDR", transfer_date) \
            if currency != "IDR" else Decimal("1")
        amount_fcy = Decimal(str(amount))
        amount_idr = (amount_fcy * rate).quantize(Decimal("1")) if currency != "IDR" else amount_fcy

        transfer_no = CashBankEngine._next_doc_no(db, "in_house_transfer", "TRF", entity_id, transfer_date, no_column="transfer_no")

        tid = uuid4()
        db.execute(
            text("""
                INSERT INTO in_house_transfer
                    (id, entity_id, transfer_no, transfer_date, source_type, source_id,
                     dest_type, dest_id, amount, currency, exchange_rate, amount_fcy, purpose, description, status, created_by)
                VALUES
                    (:id, :eid, :no, :dt, :stype, :sid, :dtype, :did, :amt, :cur, :rate, :fcy, :purpose, :desc, 'draft', :by)
            """),
            {
                "id": str(tid), "eid": str(entity_id), "no": transfer_no, "dt": transfer_date,
                "stype": source_type, "sid": str(source_id), "dtype": dest_type, "did": str(dest_id),
                "amt": float(amount_idr), "cur": currency, "rate": float(rate),
                "fcy": float(amount_fcy) if currency != "IDR" else None,
                "purpose": purpose, "desc": description, "by": created_by,
            },
        )
        db.commit()
        return {
            "in_house_transfer_id": str(tid), "transfer_no": transfer_no, "status": "draft",
            "source": src["name"], "dest": dst["name"], "amount": float(amount_idr), "currency": currency,
        }

    @staticmethod
    def post_in_house_transfer(
        db: Session,
        transfer_id: UUID,
        posted_by: str = "system",
        replenish_expense_ids: Optional[list[UUID]] = None,
    ) -> dict:
        trf = db.execute(
            text("SELECT * FROM in_house_transfer WHERE id = :id"),
            {"id": str(transfer_id)},
        ).fetchone()
        if not trf:
            raise ValueError("In-house transfer tidak ditemukan.")
        if trf.status != "draft":
            raise ValueError(f"Transfer berstatus '{trf.status}', hanya 'draft' yang bisa diposting.")

        src = CashBankEngine._resolve_account(db, trf.source_type, trf.source_id)
        dst = CashBankEngine._resolve_account(db, trf.dest_type, trf.dest_id)
        is_fcy = trf.currency and trf.currency != "IDR"
        fcy_kwargs = dict(
            currency=trf.currency if is_fcy else None,
            amount_fcy=Decimal(str(trf.amount_fcy)) if is_fcy and trf.amount_fcy is not None else None,
            exchange_rate=Decimal(str(trf.exchange_rate)) if is_fcy else None,
        )

        desc = trf.description or f"Transfer {src['name']} -> {dst['name']}"
        entry = JournalEntry(
            entity_id=trf.entity_id,
            journal_type="CASH",
            journal_date=trf.transfer_date,
            description=f"{trf.transfer_no} — {desc}",
            lines=[
                JournalLine(account_code=dst["account_code"], description=desc, debit_idr=Decimal(str(trf.amount)), **fcy_kwargs),
                JournalLine(account_code=src["account_code"], description=desc, credit_idr=Decimal(str(trf.amount)), **fcy_kwargs),
            ],
            reference_no=trf.transfer_no,
            currency=trf.currency, fx_rate=Decimal(str(trf.exchange_rate)),
            source="cash_bank",
            created_by=posted_by,
        )

        result = JournalEngine(db).post_journal(entry)
        if not result["success"]:
            raise ValueError(result["error"])

        db.execute(
            text("UPDATE in_house_transfer SET status = 'posted', journal_id = :jid WHERE id = :id"),
            {"jid": result["journal_id"], "id": str(transfer_id)},
        )

        replenished_count = 0
        if trf.purpose == "petty_cash_topup" and replenish_expense_ids:
            for exp_id in replenish_expense_ids:
                res = db.execute(
                    text("""
                        UPDATE petty_cash_expense
                        SET replenished = TRUE
                        WHERE id = :id AND cash_account_id = :cid AND status = 'posted' AND replenished = FALSE
                    """),
                    {"id": str(exp_id), "cid": str(trf.dest_id)},
                )
                replenished_count += res.rowcount

        db.commit()
        return {
            "in_house_transfer_id": str(transfer_id), "status": "posted",
            "journal_id": result["journal_id"], "journal_no": result["journal_no"],
            "replenished_expenses": replenished_count,
        }

    @staticmethod
    def list_in_house_transfers(
        db: Session,
        entity_id: UUID,
        source_type: Optional[str] = None,
        dest_type: Optional[str] = None,
        status: Optional[str] = None,
        purpose: Optional[str] = None,
    ) -> list[dict]:
        filters = ["it.entity_id = :eid"]
        params: dict = {"eid": str(entity_id)}
        if source_type:
            filters.append("it.source_type = :stype")
            params["stype"] = source_type
        if dest_type:
            filters.append("it.dest_type = :dtype")
            params["dtype"] = dest_type
        if status:
            filters.append("it.status = :status")
            params["status"] = status
        if purpose:
            filters.append("it.purpose = :purpose")
            params["purpose"] = purpose

        rows = db.execute(
            text(f"""
                SELECT it.id, it.transfer_no, it.transfer_date, it.source_type, it.source_id,
                       it.dest_type, it.dest_id, it.amount, it.currency, it.amount_fcy, it.purpose, it.description,
                       it.status, it.journal_id
                FROM in_house_transfer it
                WHERE {" AND ".join(filters)}
                ORDER BY it.transfer_date DESC, it.created_at DESC
            """),
            params,
        ).fetchall()

        results = []
        for r in rows:
            row = dict(r._mapping)
            try:
                row["source_name"] = CashBankEngine._resolve_account(db, row["source_type"], row["source_id"])["name"]
                row["dest_name"] = CashBankEngine._resolve_account(db, row["dest_type"], row["dest_id"])["name"]
            except ValueError:
                row["source_name"] = row["dest_name"] = None
            results.append(row)
        return results
