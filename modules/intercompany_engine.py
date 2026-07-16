"""
Intercompany Transaction Engine
================================
Mengelola transaksi antar entity dalam satu grup perusahaan.

Dua hal yang selalu terjadi saat posting:
  1. Jurnal di entity INITIATOR  (pihak yang menagih / meminjam)
  2. Jurnal di entity COUNTERPARTY (pihak yang ditagih / dipinjami)

Akun default per tipe transaksi:
  charge / cost_recharge:
    Initiator  : Dr 1-9100 (Due From) | Cr 4-5000 (Pendapatan Interco)
    Counterparty: Dr 6-5000 (Beban Interco) | Cr 2-9100 (Due To)

  loan:
    Initiator  : Dr 1-9200 (Pinjaman Diberikan) | Cr 1-1110 (Bank)
    Counterparty: Dr 1-1110 (Bank) | Cr 2-9200 (Pinjaman Diterima)

  equity_injection:
    Initiator  : Dr 1-9300 (Investasi pada Anak) | Cr 1-1110 (Bank)
    Counterparty: Dr 1-1110 (Bank) | Cr 3-1100 (Modal Disetor)

  dividend:
    Initiator  : Dr 1-9100 (Due From) | Cr 4-6000 (Pendapatan Dividen)
    Counterparty: Dr 3-3000 (Laba Ditahan) | Cr 2-9100 (Due To)

  cash_transfer:
    Initiator  : Dr 1-9100 (Due From B) | Cr 1-1110 (Bank A)
    Counterparty: Dr 1-1110 (Bank B) | Cr 2-9100 (Due To A)

Settlement (saat bayar):
  Initiator  : Dr 1-1110 (Bank diterima) | Cr 1-9100 (Due From)
  Counterparty: Dr 2-9100 (Due To) | Cr 1-1110 (Bank dibayar)
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from sqlalchemy import text
from sqlalchemy.orm import Session


# ── Defaults per tipe transaksi ───────────────────────────────────────────────

_DEFAULTS: dict[str, dict] = {
    "charge": {
        "initiator_debit":      "1-9100",   # Due From
        "initiator_credit":     "4-5000",   # Pendapatan Jasa Interco
        "counterparty_debit":   "6-5000",   # Beban Jasa Interco
        "counterparty_credit":  "2-9100",   # Due To
    },
    "cost_recharge": {
        "initiator_debit":      "1-9100",
        "initiator_credit":     "4-5100",   # Pemulihan Biaya
        "counterparty_debit":   "6-5100",   # Biaya Bersama
        "counterparty_credit":  "2-9100",
    },
    "loan": {
        "initiator_debit":      "1-9200",   # Pinjaman Diberikan ke Anak
        "initiator_credit":     "1-1110",   # Bank
        "counterparty_debit":   "1-1110",   # Bank
        "counterparty_credit":  "2-9200",   # Pinjaman Diterima
    },
    "loan_repayment": {
        "initiator_debit":      "1-1110",   # Bank (terima cicilan)
        "initiator_credit":     "1-9200",   # Kurangi Pinjaman Diberikan
        "counterparty_debit":   "2-9200",   # Kurangi Pinjaman Diterima
        "counterparty_credit":  "1-1110",   # Bank (bayar cicilan)
    },
    "equity_injection": {
        "initiator_debit":      "1-9300",   # Investasi pada Anak Perusahaan
        "initiator_credit":     "1-1110",   # Bank
        "counterparty_debit":   "1-1110",   # Bank
        "counterparty_credit":  "3-1100",   # Modal Disetor
    },
    "dividend": {
        "initiator_debit":      "1-9100",   # Due From (piutang dividen)
        "initiator_credit":     "4-6000",   # Pendapatan Dividen
        "counterparty_debit":   "3-3000",   # Laba Ditahan
        "counterparty_credit":  "2-9100",   # Due To (hutang dividen)
    },
    "cash_transfer": {
        "initiator_debit":      "1-9100",   # Due From B
        "initiator_credit":     "1-1110",   # Bank A keluar
        "counterparty_debit":   "1-1110",   # Bank B masuk
        "counterparty_credit":  "2-9100",   # Due To A
    },
}


def _to_d(val) -> Decimal:
    return Decimal(str(val)) if val is not None else Decimal("0")


class IntercompanyEngine:

    # ── Create ────────────────────────────────────────────────────────────────

    @staticmethod
    def create(
        db: Session,
        initiator_entity_id: str,
        counterparty_entity_id: str,
        transaction_type: str,
        transaction_date: date,
        description: str,
        amount: Decimal,
        currency: str = "IDR",
        exchange_rate: Decimal = Decimal("1"),
        initiator_debit_account: Optional[str] = None,
        initiator_credit_account: Optional[str] = None,
        counterparty_debit_account: Optional[str] = None,
        counterparty_credit_account: Optional[str] = None,
        reference_number: Optional[str] = None,
        fiscal_year: Optional[int] = None,
        fiscal_month: Optional[int] = None,
        tags: Optional[list[str]] = None,
        notes: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> dict:
        """
        Buat transaksi intercompany baru dalam status 'draft'.
        Belum memposting ke GL.
        """
        if initiator_entity_id == counterparty_entity_id:
            raise ValueError("Initiator dan counterparty tidak boleh entity yang sama.")

        if transaction_type not in _DEFAULTS:
            raise ValueError(f"Tipe transaksi '{transaction_type}' tidak valid.")

        defaults = _DEFAULTS[transaction_type]

        # Gunakan default akun jika tidak dioverride
        i_dr  = initiator_debit_account   or defaults["initiator_debit"]
        i_cr  = initiator_credit_account  or defaults["initiator_credit"]
        cp_dr = counterparty_debit_account  or defaults["counterparty_debit"]
        cp_cr = counterparty_credit_account or defaults["counterparty_credit"]

        fy = fiscal_year  or transaction_date.year
        fm = fiscal_month or transaction_date.month

        row = db.execute(text("""
            INSERT INTO intercompany_transaction
                (initiator_entity_id, counterparty_entity_id,
                 transaction_type, transaction_date, description,
                 currency, amount_fcy, exchange_rate,
                 initiator_debit_account, initiator_credit_account,
                 counterparty_debit_account, counterparty_credit_account,
                 reference_number, fiscal_year, fiscal_month, tags, notes,
                 created_by)
            VALUES
                (:ieid, :ceid,
                 :ttype, :tdate, :desc,
                 :cur, :amt, :rate,
                 :i_dr, :i_cr, :cp_dr, :cp_cr,
                 :ref, :fy, :fm, :tags, :notes, :cb)
            RETURNING id, amount_idr
        """), {
            "ieid": initiator_entity_id, "ceid": counterparty_entity_id,
            "ttype": transaction_type, "tdate": transaction_date, "desc": description,
            "cur": currency.upper(), "amt": str(amount), "rate": str(exchange_rate),
            "i_dr": i_dr, "i_cr": i_cr, "cp_dr": cp_dr, "cp_cr": cp_cr,
            "ref": reference_number, "fy": fy, "fm": fm,
            "tags": tags, "notes": notes, "cb": created_by,
        }).first()
        db.commit()
        return {
            "id": str(row.id),
            "status": "draft",
            "amount_idr": str(row.amount_idr),
            "transaction_type": transaction_type,
            "initiator_accounts": {"debit": i_dr, "credit": i_cr},
            "counterparty_accounts": {"debit": cp_dr, "credit": cp_cr},
        }

    # ── Submit (draft → pending_approval) ─────────────────────────────────────

    @staticmethod
    def submit(db: Session, transaction_id: str, submitted_by: str) -> dict:
        txn = IntercompanyEngine._get_or_raise(db, transaction_id, expected_status="draft")
        db.execute(text("""
            UPDATE intercompany_transaction
               SET status = 'pending_approval',
                   initiator_submitted_by = :sb,
                   initiator_submitted_at = NOW(),
                   updated_at = NOW()
            WHERE id = :id
        """), {"sb": submitted_by, "id": transaction_id})
        db.commit()
        return {"id": transaction_id, "status": "pending_approval", "submitted_by": submitted_by}

    # ── Confirm oleh counterparty (pending_approval → approved) ───────────────

    @staticmethod
    def confirm(db: Session, transaction_id: str, confirmed_by: str) -> dict:
        txn = IntercompanyEngine._get_or_raise(db, transaction_id, expected_status="pending_approval")
        db.execute(text("""
            UPDATE intercompany_transaction
               SET status = 'approved',
                   counterparty_confirmed_by = :cb,
                   counterparty_confirmed_at = NOW(),
                   updated_at = NOW()
            WHERE id = :id
        """), {"cb": confirmed_by, "id": transaction_id})
        db.commit()
        return {"id": transaction_id, "status": "approved", "confirmed_by": confirmed_by}

    # ── Post ke GL (approved → posted) ────────────────────────────────────────

    @staticmethod
    def post(db: Session, transaction_id: str, posted_by: str) -> dict:
        """
        Posting jurnal ke DUA entity sekaligus.
        Setelah posted, tidak bisa diedit.
        """
        txn = IntercompanyEngine._get_or_raise(db, transaction_id, expected_status="approved")

        amount_idr = _to_d(txn.amount_idr)
        description = txn.description

        # Buat jurnal di entity INITIATOR
        i_journal_id, i_jnum = IntercompanyEngine._post_single_entity(
            db=db,
            entity_id=str(txn.initiator_entity_id),
            journal_date=txn.transaction_date,
            description=f"Interco {txn.transaction_type} - {description}",
            reference=f"IC-{str(txn.id)[:8].upper()}",
            debit_account=txn.initiator_debit_account,
            credit_account=txn.initiator_credit_account,
            amount_idr=amount_idr,
            currency=txn.currency,
            amount_fcy=_to_d(txn.amount_fcy),
            exchange_rate=_to_d(txn.exchange_rate),
            created_by=posted_by,
            journal_type="intercompany",
        )

        # Buat jurnal di entity COUNTERPARTY
        cp_journal_id, cp_jnum = IntercompanyEngine._post_single_entity(
            db=db,
            entity_id=str(txn.counterparty_entity_id),
            journal_date=txn.transaction_date,
            description=f"Interco {txn.transaction_type} (dari {str(txn.initiator_entity_id)[:8]}) - {description}",
            reference=f"IC-{str(txn.id)[:8].upper()}",
            debit_account=txn.counterparty_debit_account,
            credit_account=txn.counterparty_credit_account,
            amount_idr=amount_idr,
            currency=txn.currency,
            amount_fcy=_to_d(txn.amount_fcy),
            exchange_rate=_to_d(txn.exchange_rate),
            created_by=posted_by,
            journal_type="intercompany",
        )

        db.execute(text("""
            UPDATE intercompany_transaction
               SET status = 'posted',
                   initiator_journal_id    = :ijid,
                   counterparty_journal_id = :cpjid,
                   posted_by  = :pb,
                   posted_at  = NOW(),
                   updated_at = NOW()
            WHERE id = :id
        """), {
            "ijid": i_journal_id, "cpjid": cp_journal_id,
            "pb": posted_by, "id": transaction_id,
        })
        db.commit()

        return {
            "id": transaction_id,
            "status": "posted",
            "initiator_journal_id": i_journal_id,
            "initiator_journal_number": i_jnum,
            "counterparty_journal_id": cp_journal_id,
            "counterparty_journal_number": cp_jnum,
        }

    @staticmethod
    def _post_single_entity(
        db: Session,
        entity_id: str,
        journal_date: date,
        description: str,
        reference: str,
        debit_account: str,
        credit_account: str,
        amount_idr: Decimal,
        currency: str,
        amount_fcy: Decimal,
        exchange_rate: Decimal,
        created_by: str,
        journal_type: str = "intercompany",
    ) -> tuple[str, str]:
        """
        Post satu jurnal Dr/Cr ke entity tertentu.
        Return (journal_id, journal_number).
        """
        # Cari account_id dari CoA entity
        dr_acct = db.execute(text("""
            SELECT id FROM chart_of_accounts
            WHERE account_code = :code AND entity_id = :eid
        """), {"code": debit_account, "eid": entity_id}).first()

        cr_acct = db.execute(text("""
            SELECT id FROM chart_of_accounts
            WHERE account_code = :code AND entity_id = :eid
        """), {"code": credit_account, "eid": entity_id}).first()

        if dr_acct is None:
            raise ValueError(f"Akun '{debit_account}' tidak ada di CoA entity {entity_id[:8]}.")
        if cr_acct is None:
            raise ValueError(f"Akun '{credit_account}' tidak ada di CoA entity {entity_id[:8]}.")

        is_fcy = (currency != "IDR")

        journal = db.execute(text("""
            INSERT INTO gl_journal
                (entity_id, journal_date, journal_type, description,
                 reference_number, status, has_fcy, created_by, created_at)
            VALUES
                (:eid, :jdt, :jtype, :desc,
                 :ref, 'posted', :has_fcy, :cb, NOW())
            RETURNING id, journal_number
        """), {
            "eid": entity_id, "jdt": journal_date, "jtype": journal_type,
            "desc": description, "ref": reference,
            "has_fcy": is_fcy, "cb": created_by,
        }).first()
        journal_id = str(journal.id)

        fcy_params = {
            "cur": currency,
            "amt_fcy": str(amount_fcy) if is_fcy else None,
            "rate": str(exchange_rate) if is_fcy else None,
        }

        # Debit line
        db.execute(text("""
            INSERT INTO gl_line
                (journal_id, account_id, description,
                 debit_idr, credit_idr,
                 currency, amount_fcy, exchange_rate)
            VALUES
                (:jid, :acct, :desc,
                 :amt, 0,
                 :cur, :amt_fcy, :rate)
        """), {
            "jid": journal_id, "acct": str(dr_acct.id),
            "desc": description, "amt": str(amount_idr),
            **fcy_params,
        })

        # Credit line
        db.execute(text("""
            INSERT INTO gl_line
                (journal_id, account_id, description,
                 debit_idr, credit_idr,
                 currency, amount_fcy, exchange_rate)
            VALUES
                (:jid, :acct, :desc,
                 0, :amt,
                 :cur, :amt_fcy, :rate)
        """), {
            "jid": journal_id, "acct": str(cr_acct.id),
            "desc": description, "amt": str(amount_idr),
            **fcy_params,
        })

        return journal_id, journal.journal_number

    # ── Cancel ────────────────────────────────────────────────────────────────

    @staticmethod
    def cancel(
        db: Session,
        transaction_id: str,
        cancelled_by: str,
        reason: str,
    ) -> dict:
        txn = IntercompanyEngine._get_or_raise(db, transaction_id)
        if txn.status == "posted":
            # Harus reverse jurnal dulu
            raise ValueError(
                "Transaksi sudah diposting. Gunakan reverse() untuk membatalkan "
                "dan membalik jurnal GL yang sudah ada."
            )
        if txn.status in ("settled", "partial_settled"):
            raise ValueError("Transaksi sudah (sebagian) diselesaikan, tidak bisa dibatalkan.")

        db.execute(text("""
            UPDATE intercompany_transaction
               SET status = 'cancelled',
                   cancelled_by = :cb, cancelled_at = NOW(),
                   cancel_reason = :reason, updated_at = NOW()
            WHERE id = :id
        """), {"cb": cancelled_by, "reason": reason, "id": transaction_id})
        db.commit()
        return {"id": transaction_id, "status": "cancelled"}

    # ── Reverse posting ───────────────────────────────────────────────────────

    @staticmethod
    def reverse(
        db: Session,
        transaction_id: str,
        reversed_by: str,
        reason: str,
    ) -> dict:
        """
        Balik jurnal yang sudah diposting di kedua entity.
        Membuat jurnal reversal dengan Dr/Cr terbalik.
        Status kembali ke cancelled.
        """
        txn = IntercompanyEngine._get_or_raise(db, transaction_id, expected_status="posted")

        if _to_d(txn.settled_amount_idr) > 0:
            raise ValueError(
                "Tidak bisa reverse: sudah ada sebagian yang diselesaikan (settled). "
                "Reverse settlement dulu."
            )

        rev_ref = f"REV-IC-{str(txn.id)[:8].upper()}"
        rev_desc = f"Balik: {txn.description} ({reason})"

        # Reverse jurnal initiator (balik Dr↔Cr)
        i_rev_id, i_rev_num = IntercompanyEngine._reverse_journal(
            db, str(txn.initiator_journal_id), rev_desc, rev_ref, reversed_by
        )

        # Reverse jurnal counterparty
        cp_rev_id, cp_rev_num = IntercompanyEngine._reverse_journal(
            db, str(txn.counterparty_journal_id), rev_desc, rev_ref, reversed_by
        )

        db.execute(text("""
            UPDATE intercompany_transaction
               SET status = 'cancelled',
                   cancelled_by = :cb, cancelled_at = NOW(),
                   cancel_reason = :reason, updated_at = NOW()
            WHERE id = :id
        """), {"cb": reversed_by, "reason": reason, "id": transaction_id})
        db.commit()

        return {
            "id": transaction_id,
            "status": "cancelled",
            "initiator_reversal_journal": i_rev_id,
            "counterparty_reversal_journal": cp_rev_id,
        }

    @staticmethod
    def _reverse_journal(
        db: Session,
        original_journal_id: str,
        description: str,
        reference: str,
        created_by: str,
    ) -> tuple[str, str]:
        orig = db.execute(text("""
            SELECT entity_id, journal_date, journal_type FROM gl_journal
            WHERE id = :id
        """), {"id": original_journal_id}).first()

        rev = db.execute(text("""
            INSERT INTO gl_journal
                (entity_id, journal_date, journal_type, description,
                 reference_number, status, has_fcy, created_by, created_at)
            VALUES
                (:eid, CURRENT_DATE, :jtype, :desc,
                 :ref, 'posted', FALSE, :cb, NOW())
            RETURNING id, journal_number
        """), {
            "eid": str(orig.entity_id),
            "jtype": orig.journal_type + "_reversal",
            "desc": description, "ref": reference, "cb": created_by,
        }).first()
        rev_id = str(rev.id)

        lines = db.execute(text("""
            SELECT account_id, description, debit_idr, credit_idr
            FROM gl_line WHERE journal_id = :jid
        """), {"jid": original_journal_id}).fetchall()

        for line in lines:
            db.execute(text("""
                INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr)
                VALUES (:jid, :acct, :desc, :dr, :cr)
            """), {
                "jid": rev_id, "acct": str(line.account_id),
                "desc": "Balik: " + (line.description or ""),
                "dr": str(_to_d(line.credit_idr)),
                "cr": str(_to_d(line.debit_idr)),
            })

        return rev_id, rev.journal_number

    # ── Settlement ────────────────────────────────────────────────────────────

    @staticmethod
    def settle(
        db: Session,
        transaction_id: str,
        settlement_date: date,
        amount_idr: Decimal,
        payment_method: str,
        initiator_bank_account_id: Optional[str],
        counterparty_bank_account_id: Optional[str],
        bank_reference: Optional[str],
        created_by: str,
        currency: str = "IDR",
        amount_fcy: Optional[Decimal] = None,
        exchange_rate: Optional[Decimal] = None,
        notes: Optional[str] = None,
    ) -> dict:
        """
        Catat pembayaran interco.
        Posting jurnal pelunasan di kedua entity:
          Initiator  : Dr Cash/Bank | Cr Due From
          Counterparty: Dr Due To | Cr Cash/Bank
        """
        txn = IntercompanyEngine._get_or_raise(db, transaction_id)
        if txn.status not in ("posted", "partial_settled"):
            raise ValueError(f"Status '{txn.status}' tidak bisa diselesaikan.")

        outstanding = _to_d(txn.outstanding_amount_idr)
        settle_amt = _to_d(amount_idr)
        if settle_amt <= 0:
            raise ValueError("Jumlah settlement harus > 0.")
        if settle_amt > outstanding + Decimal("1"):
            raise ValueError(
                f"Jumlah settlement (Rp {settle_amt:,.0f}) melebihi outstanding "
                f"(Rp {outstanding:,.0f})."
            )
        settle_amt = min(settle_amt, outstanding)

        # Ambil GL accounts dari konfigurasi / default
        due_from_acct = txn.initiator_debit_account   # akun Due From di initiator
        due_to_acct   = txn.counterparty_credit_account  # akun Due To di counterparty

        # Cari akun kas/bank initiator
        i_bank_gl = None
        if initiator_bank_account_id:
            i_bank_row = db.execute(text("""
                SELECT gl_account_code FROM bank_account WHERE id = :id
            """), {"id": initiator_bank_account_id}).first()
            i_bank_gl = i_bank_row.gl_account_code if i_bank_row else "1-1110"
        else:
            i_bank_gl = "1-1110"

        # Cari akun kas/bank counterparty
        cp_bank_gl = None
        if counterparty_bank_account_id:
            cp_bank_row = db.execute(text("""
                SELECT gl_account_code FROM bank_account WHERE id = :id
            """), {"id": counterparty_bank_account_id}).first()
            cp_bank_gl = cp_bank_row.gl_account_code if cp_bank_row else "1-1110"
        else:
            cp_bank_gl = "1-1110"

        ref = f"ICSETTLE-{str(txn.id)[:8].upper()}"
        desc = f"Settlement interco - {txn.description}"

        # Jurnal di INITIATOR: Dr Bank | Cr Due From
        i_jid, i_jnum = IntercompanyEngine._post_single_entity(
            db=db,
            entity_id=str(txn.initiator_entity_id),
            journal_date=settlement_date,
            description=desc,
            reference=ref,
            debit_account=i_bank_gl,
            credit_account=due_from_acct,
            amount_idr=settle_amt,
            currency=currency,
            amount_fcy=_to_d(amount_fcy) if amount_fcy else settle_amt,
            exchange_rate=_to_d(exchange_rate) if exchange_rate else Decimal("1"),
            created_by=created_by,
            journal_type="intercompany_settlement",
        )

        # Jurnal di COUNTERPARTY: Dr Due To | Cr Bank
        cp_jid, cp_jnum = IntercompanyEngine._post_single_entity(
            db=db,
            entity_id=str(txn.counterparty_entity_id),
            journal_date=settlement_date,
            description=desc,
            reference=ref,
            debit_account=due_to_acct,
            credit_account=cp_bank_gl,
            amount_idr=settle_amt,
            currency=currency,
            amount_fcy=_to_d(amount_fcy) if amount_fcy else settle_amt,
            exchange_rate=_to_d(exchange_rate) if exchange_rate else Decimal("1"),
            created_by=created_by,
            journal_type="intercompany_settlement",
        )

        # Catat settlement
        settle_row = db.execute(text("""
            INSERT INTO intercompany_settlement
                (transaction_id, settlement_date, amount_idr,
                 currency, amount_fcy, exchange_rate,
                 payment_method,
                 initiator_bank_account_id, counterparty_bank_account_id,
                 bank_reference,
                 initiator_journal_id, counterparty_journal_id,
                 notes, created_by)
            VALUES
                (:tid, :dt, :amt,
                 :cur, :fcy, :rate,
                 :method,
                 :ibid, :cpbid,
                 :ref,
                 :ijid, :cpjid,
                 :notes, :cb)
            RETURNING id
        """), {
            "tid": transaction_id, "dt": settlement_date, "amt": str(settle_amt),
            "cur": currency, "fcy": str(amount_fcy) if amount_fcy else None,
            "rate": str(exchange_rate) if exchange_rate else None,
            "method": payment_method,
            "ibid": initiator_bank_account_id,
            "cpbid": counterparty_bank_account_id,
            "ref": bank_reference,
            "ijid": i_jid, "cpjid": cp_jid,
            "notes": notes, "cb": created_by,
        }).first()

        # Update settled_amount dan status
        new_settled = _to_d(txn.settled_amount_idr) + settle_amt
        new_outstanding = _to_d(txn.amount_idr) - new_settled
        new_status = "settled" if new_outstanding <= Decimal("1") else "partial_settled"

        db.execute(text("""
            UPDATE intercompany_transaction
               SET settled_amount_idr = :sa,
                   status = :st,
                   updated_at = NOW()
            WHERE id = :id
        """), {"sa": str(new_settled), "st": new_status, "id": transaction_id})
        db.commit()

        return {
            "settlement_id": str(settle_row.id),
            "transaction_id": transaction_id,
            "settled_amount": str(settle_amt),
            "new_outstanding": str(max(new_outstanding, Decimal("0"))),
            "new_status": new_status,
            "initiator_journal_id": i_jid,
            "counterparty_journal_id": cp_jid,
        }

    # ── Query ─────────────────────────────────────────────────────────────────

    @staticmethod
    def list_transactions(
        db: Session,
        entity_id: str,
        side: str = "both",           # both | initiator | counterparty
        status: Optional[str] = None,
        transaction_type: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        page: int = 1,
        size: int = 50,
    ) -> dict:
        conditions = []
        params: dict = {"eid": entity_id, "offset": (page - 1) * size, "size": size}

        if side == "initiator":
            conditions.append("t.initiator_entity_id = :eid")
        elif side == "counterparty":
            conditions.append("t.counterparty_entity_id = :eid")
        else:
            conditions.append("(t.initiator_entity_id = :eid OR t.counterparty_entity_id = :eid)")

        if status:
            conditions.append("t.status = :status")
            params["status"] = status
        if transaction_type:
            conditions.append("t.transaction_type = :ttype")
            params["ttype"] = transaction_type
        if date_from:
            conditions.append("t.transaction_date >= :df")
            params["df"] = date_from
        if date_to:
            conditions.append("t.transaction_date <= :dt")
            params["dt"] = date_to

        where = " AND ".join(conditions) if conditions else "1=1"

        total = db.execute(text(f"""
            SELECT COUNT(*) FROM intercompany_transaction t WHERE {where}
        """), params).scalar()

        rows = db.execute(text(f"""
            SELECT
                t.*,
                ie.entity_name AS initiator_name,
                ce.entity_name AS counterparty_name
            FROM intercompany_transaction t
            JOIN entity ie ON ie.id = t.initiator_entity_id
            JOIN entity ce ON ce.id = t.counterparty_entity_id
            WHERE {where}
            ORDER BY t.transaction_date DESC, t.created_at DESC
            LIMIT :size OFFSET :offset
        """), params).fetchall()

        return {
            "total": total,
            "page": page,
            "size": size,
            "items": [dict(r._mapping) for r in rows],
        }

    @staticmethod
    def get_detail(db: Session, transaction_id: str) -> dict:
        txn = db.execute(text("""
            SELECT t.*,
                   ie.entity_name AS initiator_name,
                   ce.entity_name AS counterparty_name,
                   ij.journal_number AS initiator_journal_number,
                   cj.journal_number AS counterparty_journal_number
            FROM intercompany_transaction t
            JOIN entity ie   ON ie.id = t.initiator_entity_id
            JOIN entity ce   ON ce.id = t.counterparty_entity_id
            LEFT JOIN gl_journal ij  ON ij.id = t.initiator_journal_id
            LEFT JOIN gl_journal cj  ON cj.id = t.counterparty_journal_id
            WHERE t.id = :id
        """), {"id": transaction_id}).first()

        if txn is None:
            raise ValueError("Transaksi tidak ditemukan.")

        settlements = db.execute(text("""
            SELECT s.*,
                   ij.journal_number AS initiator_journal_number,
                   cj.journal_number AS counterparty_journal_number
            FROM intercompany_settlement s
            LEFT JOIN gl_journal ij ON ij.id = s.initiator_journal_id
            LEFT JOIN gl_journal cj ON cj.id = s.counterparty_journal_id
            WHERE s.transaction_id = :tid
            ORDER BY s.settlement_date
        """), {"tid": transaction_id}).fetchall()

        return {
            **dict(txn._mapping),
            "settlements": [dict(s._mapping) for s in settlements],
        }

    @staticmethod
    def get_outstanding_balances(db: Session, entity_id: str) -> dict:
        """
        Ringkasan piutang dan hutang interco untuk satu entity.
        """
        receivables = db.execute(text("""
            SELECT ce.entity_name AS counterparty_name,
                   ce.id          AS counterparty_id,
                   t.transaction_type,
                   SUM(t.amount_idr) AS total_amount,
                   SUM(t.outstanding_amount_idr) AS outstanding
            FROM intercompany_transaction t
            JOIN entity ce ON ce.id = t.counterparty_entity_id
            WHERE t.initiator_entity_id = :eid
              AND t.status IN ('posted', 'partial_settled')
            GROUP BY ce.entity_name, ce.id, t.transaction_type
            ORDER BY outstanding DESC
        """), {"eid": entity_id}).fetchall()

        payables = db.execute(text("""
            SELECT ie.entity_name AS creditor_name,
                   ie.id          AS creditor_id,
                   t.transaction_type,
                   SUM(t.amount_idr) AS total_amount,
                   SUM(t.outstanding_amount_idr) AS outstanding
            FROM intercompany_transaction t
            JOIN entity ie ON ie.id = t.initiator_entity_id
            WHERE t.counterparty_entity_id = :eid
              AND t.status IN ('posted', 'partial_settled')
            GROUP BY ie.entity_name, ie.id, t.transaction_type
            ORDER BY outstanding DESC
        """), {"eid": entity_id}).fetchall()

        return {
            "receivables": [dict(r._mapping) for r in receivables],
            "payables":    [dict(r._mapping) for r in payables],
            "total_receivable": sum(_to_d(r.outstanding) for r in receivables),
            "total_payable":    sum(_to_d(r.outstanding) for r in payables),
        }

    @staticmethod
    def get_elimination_schedule(db: Session, entity_ids: list[str]) -> list[dict]:
        """
        Jadwal eliminasi untuk konsolidasi.
        Pasangkan Due From di A dengan Due To di B.
        """
        rows = db.execute(text("""
            SELECT * FROM vw_interco_elimination
            WHERE initiator_entity_id = ANY(:eids)
               OR counterparty_entity_id = ANY(:eids)
            ORDER BY outstanding DESC
        """), {"eids": entity_ids}).fetchall()
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def get_aging_report(db: Session, entity_id: str) -> dict:
        receivable_aging = db.execute(text("""
            SELECT aging_bucket, SUM(outstanding_amount_idr) AS amount
            FROM vw_interco_aging
            WHERE initiator_entity_id = :eid
            GROUP BY aging_bucket
            ORDER BY aging_bucket
        """), {"eid": entity_id}).fetchall()

        payable_aging = db.execute(text("""
            SELECT aging_bucket, SUM(outstanding_amount_idr) AS amount
            FROM vw_interco_aging
            WHERE counterparty_entity_id = :eid
            GROUP BY aging_bucket
            ORDER BY aging_bucket
        """), {"eid": entity_id}).fetchall()

        return {
            "receivable_aging": [dict(r._mapping) for r in receivable_aging],
            "payable_aging":    [dict(r._mapping) for r in payable_aging],
        }

    # ── Config ────────────────────────────────────────────────────────────────

    @staticmethod
    def set_config(
        db: Session,
        entity_id: str,
        counterparty_entity_id: str,
        due_from_account: str = "1-9100",
        due_to_account: str = "2-9100",
        default_charge_income_account: Optional[str] = None,
        default_charge_expense_account: Optional[str] = None,
    ) -> dict:
        row = db.execute(text("""
            INSERT INTO intercompany_config
                (entity_id, counterparty_entity_id,
                 due_from_account, due_to_account,
                 default_charge_income_account, default_charge_expense_account)
            VALUES (:eid, :ceid, :dfa, :dta, :cia, :cea)
            ON CONFLICT (entity_id, counterparty_entity_id) DO UPDATE SET
                due_from_account = EXCLUDED.due_from_account,
                due_to_account   = EXCLUDED.due_to_account,
                default_charge_income_account  = EXCLUDED.default_charge_income_account,
                default_charge_expense_account = EXCLUDED.default_charge_expense_account
            RETURNING id
        """), {
            "eid": entity_id, "ceid": counterparty_entity_id,
            "dfa": due_from_account, "dta": due_to_account,
            "cia": default_charge_income_account,
            "cea": default_charge_expense_account,
        }).first()
        db.commit()
        return {"id": str(row.id), "entity_id": entity_id}

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _get_or_raise(
        db: Session,
        transaction_id: str,
        expected_status: Optional[str] = None,
    ):
        txn = db.execute(text("""
            SELECT * FROM intercompany_transaction WHERE id = :id
        """), {"id": transaction_id}).first()

        if txn is None:
            raise ValueError(f"Transaksi intercompany '{transaction_id}' tidak ditemukan.")
        if expected_status and txn.status != expected_status:
            raise ValueError(
                f"Status saat ini '{txn.status}', dibutuhkan '{expected_status}'."
            )
        return txn

    @staticmethod
    def get_default_accounts(transaction_type: str) -> dict:
        """Return akun default untuk tipe transaksi tertentu."""
        if transaction_type not in _DEFAULTS:
            raise ValueError(f"Tipe '{transaction_type}' tidak valid.")
        return _DEFAULTS[transaction_type]
