"""
Withholding Tax Engine — PPh 23 & PPh 4(2)

Fungsi utama:
  1. create_from_invoice()     → buat wht_transaction otomatis dari AP invoice
  2. create_manual()           → buat wht_transaction manual
  3. confirm_transaction()     → draft → confirmed
  4. issue_bukti_potong()      → confirmed → bukti_potong (generate nomor)
  5. create_spt_masa()         → kumpulkan semua confirmed/bukti_potong dalam satu SPT
  6. submit_spt()              → submit + record payment (NTPN) + posting GL
  7. void_transaction()        → void satu transaksi
  8. get_summary()             → ringkasan per periode
  9. get_rate()                → lookup tarif berdasarkan income_type_code + has_npwp
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


# Akun GL default untuk WHT — bisa di-override dari settings/parameter
DEFAULT_GL = {
    "PPh23":  {"payable": "2-2300", "expense": "6-1500"},   # Hutang PPh 23, Beban PPh 23
    "PPh4_2": {"payable": "2-2310", "expense": "6-1510"},   # Hutang PPh 4(2), Beban PPh 4(2)
}


class WithholdingTaxEngine:

    # ─────────────────────────────────────────────────────────────────────────
    # 1. CREATE FROM AP INVOICE
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def create_from_invoice(
        db: Session,
        ap_invoice_id: UUID,
        tax_type: str,
        income_type_code: str,
        dpp: Decimal,
        created_by: str = None,
    ) -> dict:
        """
        Buat wht_transaction dari AP invoice.
        DPP biasanya = nilai jasa (bisa berbeda dari total invoice jika ada material/PPN).
        """
        inv = db.execute(
            text("""
                SELECT ai.entity_id, ai.vendor_id, ai.invoice_date,
                       v.npwp, v.has_skb
                FROM ap_invoice ai
                JOIN vendor v ON v.id = ai.vendor_id
                WHERE ai.id = :id
            """),
            {"id": str(ap_invoice_id)},
        ).fetchone()
        if not inv:
            raise ValueError("AP Invoice tidak ditemukan.")

        has_npwp = bool(inv.npwp and len(inv.npwp.strip()) >= 15)
        rate = WithholdingTaxEngine._get_rate(db, tax_type, income_type_code, has_npwp)

        row = db.execute(
            text("""
                INSERT INTO wht_transaction (
                    entity_id, tax_type, income_type_code,
                    ap_invoice_id, vendor_id, transaction_date,
                    dpp, rate_pct, has_npwp, description,
                    period_year, period_month, status
                ) VALUES (
                    :eid, :tt, :itc,
                    :inv, :vid, :dt,
                    :dpp, :rate, :npwp, :desc,
                    :yr, :mo, 'draft'
                ) RETURNING id, tax_amount
            """),
            {
                "eid":  str(inv.entity_id),
                "tt":   tax_type,
                "itc":  income_type_code,
                "inv":  str(ap_invoice_id),
                "vid":  str(inv.vendor_id),
                "dt":   inv.invoice_date,
                "dpp":  float(dpp),
                "rate": float(rate),
                "npwp": has_npwp,
                "desc": f"WHT dari invoice AP",
                "yr":   inv.invoice_date.year,
                "mo":   inv.invoice_date.month,
            },
        ).fetchone()
        db.commit()
        return {
            "wht_id":     str(row.id),
            "tax_type":   tax_type,
            "dpp":        float(dpp),
            "rate_pct":   float(rate),
            "tax_amount": float(row.tax_amount),
            "has_npwp":   has_npwp,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 2. CREATE MANUAL
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def create_manual(
        db: Session,
        entity_id: UUID,
        vendor_id: UUID,
        tax_type: str,
        income_type_code: str,
        transaction_date: date,
        dpp: Decimal,
        description: str = None,
        has_npwp: bool = True,
    ) -> dict:
        rate = WithholdingTaxEngine._get_rate(db, tax_type, income_type_code, has_npwp)

        row = db.execute(
            text("""
                INSERT INTO wht_transaction (
                    entity_id, tax_type, income_type_code,
                    vendor_id, transaction_date, dpp, rate_pct,
                    has_npwp, description, period_year, period_month, status
                ) VALUES (
                    :eid, :tt, :itc,
                    :vid, :dt, :dpp, :rate,
                    :npwp, :desc, :yr, :mo, 'draft'
                ) RETURNING id, tax_amount
            """),
            {
                "eid":  str(entity_id), "tt":   tax_type,  "itc":  income_type_code,
                "vid":  str(vendor_id), "dt":   transaction_date,
                "dpp":  float(dpp),    "rate":  float(rate),
                "npwp": has_npwp,       "desc":  description,
                "yr":   transaction_date.year, "mo": transaction_date.month,
            },
        ).fetchone()
        db.commit()
        return {
            "wht_id":     str(row.id),
            "tax_amount": float(row.tax_amount),
            "rate_pct":   float(rate),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 3. CONFIRM
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def confirm_transaction(
        db: Session,
        wht_id: UUID,
        confirmed_by: str,
        gl_account_payable: Optional[str] = None,
        gl_account_expense: Optional[str] = None,
    ) -> dict:
        """
        Konfirmasi + posting GL:
          Dr. Beban PPh 23/4(2)  (expense)
          Cr. Hutang PPh 23/4(2) (payable)
        """
        wht = db.execute(
            text("SELECT * FROM wht_transaction WHERE id=:id"),
            {"id": str(wht_id)},
        ).fetchone()
        if not wht:
            raise ValueError("WHT transaction tidak ditemukan.")
        if wht.status != "draft":
            raise ValueError(f"Transaksi berstatus {wht.status}, hanya 'draft' yang bisa dikonfirmasi.")

        entity_id = str(wht.entity_id)
        defaults  = DEFAULT_GL[wht.tax_type]
        exp_code  = gl_account_expense or defaults["expense"]
        pay_code  = gl_account_payable or defaults["payable"]

        def get_acc(code: str) -> str:
            r = db.execute(
                text("SELECT id FROM chart_of_accounts WHERE account_code=:c AND entity_id=:e"),
                {"c": code, "e": entity_id},
            ).fetchone()
            if not r:
                raise ValueError(f"Account code '{code}' tidak ditemukan dalam CoA.")
            return str(r.id)

        exp_acc = get_acc(exp_code)
        pay_acc = get_acc(pay_code)
        amt     = float(wht.tax_amount)

        journal_row = db.execute(
            text("""
                INSERT INTO gl_journal (entity_id, journal_date, description,
                                        journal_type, reference_no, status, created_by)
                VALUES (:eid, :dt, :desc, 'tax', :ref, 'posted', :by)
                RETURNING id
            """),
            {
                "eid":  entity_id,
                "dt":   wht.transaction_date,
                "desc": f"{wht.tax_type} - {wht.income_type_code}",
                "ref":  str(wht_id)[:8],
                "by":   confirmed_by,
            },
        ).fetchone()
        journal_id = str(journal_row.id)

        db.execute(
            text("""
                INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr)
                VALUES
                  (:jid, :exp, :desc, :amt, 0),
                  (:jid, :pay, :desc, 0, :amt)
            """),
            {
                "jid": journal_id, "exp": exp_acc, "pay": pay_acc,
                "desc": f"{wht.tax_type} {wht.income_type_code}", "amt": amt,
            },
        )

        db.execute(
            text("""
                UPDATE wht_transaction
                SET status='confirmed', gl_journal_id=:jid
                WHERE id=:id
            """),
            {"jid": journal_id, "id": str(wht_id)},
        )
        db.commit()
        return {"status": "confirmed", "journal_id": journal_id, "tax_amount": amt}

    # ─────────────────────────────────────────────────────────────────────────
    # 4. ISSUE BUKTI POTONG
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def issue_bukti_potong(
        db: Session,
        wht_id: UUID,
        bukti_potong_date: date,
        issued_by: str,
    ) -> dict:
        wht = db.execute(
            text("SELECT * FROM wht_transaction WHERE id=:id"),
            {"id": str(wht_id)},
        ).fetchone()
        if not wht:
            raise ValueError("WHT transaction tidak ditemukan.")
        if wht.status != "confirmed":
            raise ValueError("Hanya transaksi 'confirmed' yang bisa diterbitkan bukti potongnya.")

        # Generate nomor bukti potong: BP-PPh23-YYYYMM-XXXX
        seq_row = db.execute(
            text("""
                SELECT COUNT(*) + 1 AS seq FROM wht_transaction
                WHERE entity_id = :eid AND tax_type = :tt
                  AND period_year  = :yr AND period_month = :mo
                  AND status IN ('bukti_potong','spt_included')
            """),
            {
                "eid": str(wht.entity_id), "tt": wht.tax_type,
                "yr":  wht.period_year,    "mo": wht.period_month,
            },
        ).fetchone()
        code    = "23" if wht.tax_type == "PPh23" else "42"
        bp_no   = f"BP-{code}-{wht.period_year}{wht.period_month:02d}-{seq_row.seq:04d}"

        db.execute(
            text("""
                UPDATE wht_transaction
                SET status='bukti_potong', bukti_potong_no=:bp, bukti_potong_date=:dt
                WHERE id=:id
            """),
            {"bp": bp_no, "dt": bukti_potong_date, "id": str(wht_id)},
        )
        db.commit()
        return {
            "status":           "bukti_potong",
            "bukti_potong_no":  bp_no,
            "bukti_potong_date": bukti_potong_date.isoformat(),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 5. CREATE SPT MASA
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def create_spt_masa(
        db: Session,
        entity_id: UUID,
        tax_type: str,
        period_year: int,
        period_month: int,
        created_by: str,
    ) -> dict:
        """
        Rekap semua wht_transaction status confirmed/bukti_potong dalam periode ke SPT Masa.
        Semua transaksi yang masuk → status → 'spt_included'.
        """
        existing = db.execute(
            text("""
                SELECT id FROM wht_spt_masa
                WHERE entity_id=:eid AND tax_type=:tt
                  AND period_year=:yr AND period_month=:mo
            """),
            {"eid": str(entity_id), "tt": tax_type, "yr": period_year, "mo": period_month},
        ).fetchone()
        if existing:
            raise ValueError(
                f"SPT Masa {tax_type} {period_year}-{period_month:02d} sudah ada."
            )

        rows = db.execute(
            text("""
                SELECT id, dpp, tax_amount FROM wht_transaction
                WHERE entity_id=:eid AND tax_type=:tt
                  AND period_year=:yr AND period_month=:mo
                  AND status IN ('confirmed','bukti_potong')
            """),
            {"eid": str(entity_id), "tt": tax_type, "yr": period_year, "mo": period_month},
        ).fetchall()

        if not rows:
            raise ValueError(
                f"Tidak ada transaksi {tax_type} berstatus confirmed/bukti_potong "
                f"untuk periode {period_year}-{period_month:02d}."
            )

        total_dpp = sum(Decimal(str(r.dpp))        for r in rows)
        total_tax = sum(Decimal(str(r.tax_amount))  for r in rows)
        count     = len(rows)

        spt_row = db.execute(
            text("""
                INSERT INTO wht_spt_masa (
                    entity_id, tax_type, period_year, period_month,
                    total_dpp, total_tax, total_bukti_potong,
                    status, created_by
                ) VALUES (
                    :eid, :tt, :yr, :mo,
                    :dpp, :tax, :cnt,
                    'draft', :by
                ) RETURNING id
            """),
            {
                "eid": str(entity_id), "tt": tax_type,
                "yr":  period_year,    "mo": period_month,
                "dpp": float(total_dpp), "tax": float(total_tax), "cnt": count,
                "by":  created_by,
            },
        ).fetchone()

        # Update status semua transaksi → spt_included
        for r in rows:
            db.execute(
                text("UPDATE wht_transaction SET status='spt_included' WHERE id=:id"),
                {"id": str(r.id)},
            )

        db.commit()
        return {
            "spt_id":              str(spt_row.id),
            "tax_type":            tax_type,
            "period":              f"{period_year}-{period_month:02d}",
            "total_dpp":           float(total_dpp),
            "total_tax":           float(total_tax),
            "total_bukti_potong":  count,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 6. SUBMIT SPT + SETOR
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def submit_spt(
        db: Session,
        spt_id: UUID,
        payment_date: date,
        ntpn: str,
        submitted_by: str,
        bank_account_id: UUID,
        gl_account_payable: Optional[str] = None,
    ) -> dict:
        """
        Tandai SPT Masa sebagai sudah disetor ke DJP.
        Posting GL: Dr. Hutang PPh | Cr. Kas/Bank
        """
        spt = db.execute(
            text("SELECT * FROM wht_spt_masa WHERE id=:id"),
            {"id": str(spt_id)},
        ).fetchone()
        if not spt:
            raise ValueError("SPT Masa tidak ditemukan.")
        if spt.status not in ("draft", "submitted"):
            raise ValueError(f"SPT berstatus {spt.status}.")

        entity_id = str(spt.entity_id)
        defaults  = DEFAULT_GL[spt.tax_type]
        pay_code  = gl_account_payable or defaults["payable"]

        ba = db.execute(
            text("SELECT gl_account_code FROM bank_account WHERE id=:id"),
            {"id": str(bank_account_id)},
        ).fetchone()
        if not ba or not ba.gl_account_code:
            raise ValueError("Bank account tidak memiliki gl_account_code.")

        def get_acc(code: str) -> str:
            r = db.execute(
                text("SELECT id FROM chart_of_accounts WHERE account_code=:c AND entity_id=:e"),
                {"c": code, "e": entity_id},
            ).fetchone()
            if not r:
                raise ValueError(f"Account code '{code}' tidak ditemukan.")
            return str(r.id)

        pay_acc  = get_acc(pay_code)
        bank_acc = get_acc(ba.gl_account_code)
        amt      = float(spt.total_tax)

        journal_row = db.execute(
            text("""
                INSERT INTO gl_journal (entity_id, journal_date, description,
                                        journal_type, reference_no, status, created_by)
                VALUES (:eid, :dt, :desc, 'tax', :ref, 'posted', :by)
                RETURNING id
            """),
            {
                "eid":  entity_id,
                "dt":   payment_date,
                "desc": f"Setor {spt.tax_type} Masa {spt.period_year}-{spt.period_month:02d}",
                "ref":  ntpn,
                "by":   submitted_by,
            },
        ).fetchone()
        journal_id = str(journal_row.id)

        db.execute(
            text("""
                INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr)
                VALUES
                  (:jid, :pay, :desc, :amt, 0),
                  (:jid, :bank,:desc, 0, :amt)
            """),
            {
                "jid": journal_id, "pay": pay_acc, "bank": bank_acc,
                "desc": f"Setor {spt.tax_type} {spt.period_year}-{spt.period_month:02d}",
                "amt": amt,
            },
        )

        db.execute(
            text("""
                UPDATE wht_spt_masa
                SET status='paid', payment_date=:dt, payment_ntpn=:ntpn,
                    payment_journal_id=:jid
                WHERE id=:id
            """),
            {"dt": payment_date, "ntpn": ntpn, "jid": journal_id, "id": str(spt_id)},
        )
        db.commit()
        return {
            "status":     "paid",
            "journal_id": journal_id,
            "total_paid": amt,
            "ntpn":       ntpn,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 7. VOID
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def void_transaction(db: Session, wht_id: UUID, voided_by: str, reason: str) -> dict:
        wht = db.execute(
            text("SELECT * FROM wht_transaction WHERE id=:id"),
            {"id": str(wht_id)},
        ).fetchone()
        if not wht:
            raise ValueError("WHT transaction tidak ditemukan.")
        if wht.status == "spt_included":
            raise ValueError("Transaksi yang sudah masuk SPT tidak bisa di-void.")

        # Reverse GL jika ada
        if wht.gl_journal_id:
            db.execute(
                text("""
                    INSERT INTO gl_journal (entity_id, journal_date, description,
                                            journal_type, reference_no, status, created_by)
                    SELECT entity_id, CURRENT_DATE,
                           'Void ' || description, journal_type,
                           'VOID-' || reference_no, 'posted', :by
                    FROM gl_journal WHERE id=:jid
                    RETURNING id
                """),
                {"by": voided_by, "jid": str(wht.gl_journal_id)},
            )
            # reverse lines (swap debit/credit)
            rev_journal = db.execute(
                text("SELECT id FROM gl_journal WHERE reference_no=:ref ORDER BY created_at DESC LIMIT 1"),
                {"ref": f"VOID-{str(wht_id)[:8]}"},
            ).fetchone()
            if rev_journal:
                db.execute(
                    text("""
                        INSERT INTO gl_line (journal_id, account_id, description, debit_idr, credit_idr)
                        SELECT :rev_jid, account_id, description, credit_idr, debit_idr
                        FROM gl_line WHERE journal_id=:orig_jid
                    """),
                    {"rev_jid": str(rev_journal.id), "orig_jid": str(wht.gl_journal_id)},
                )

        db.execute(
            text("UPDATE wht_transaction SET status='void' WHERE id=:id"),
            {"id": str(wht_id)},
        )
        db.commit()
        return {"status": "void"}

    # ─────────────────────────────────────────────────────────────────────────
    # 8. GET SUMMARY
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_summary(
        db: Session,
        entity_id: UUID,
        tax_type: str,
        period_year: int,
        period_month: int,
    ) -> dict:
        """Ringkasan transaksi WHT per periode + status SPT."""
        rows = db.execute(
            text("""
                SELECT * FROM vw_wht_by_vendor
                WHERE entity_id=:eid AND tax_type=:tt
                  AND period_year=:yr AND period_month=:mo
                ORDER BY vendor_name
            """),
            {"eid": str(entity_id), "tt": tax_type, "yr": period_year, "mo": period_month},
        ).fetchall()

        spt = db.execute(
            text("""
                SELECT id, status, total_dpp, total_tax, total_bukti_potong,
                       payment_date, payment_ntpn
                FROM wht_spt_masa
                WHERE entity_id=:eid AND tax_type=:tt
                  AND period_year=:yr AND period_month=:mo
            """),
            {"eid": str(entity_id), "tt": tax_type, "yr": period_year, "mo": period_month},
        ).fetchone()

        total_dpp = sum(float(r.total_dpp) for r in rows)
        total_tax = sum(float(r.total_tax) for r in rows)

        return {
            "period":       f"{period_year}-{period_month:02d}",
            "tax_type":     tax_type,
            "total_dpp":    total_dpp,
            "total_tax":    total_tax,
            "by_vendor":    [dict(r._mapping) for r in rows],
            "spt_masa":     dict(spt._mapping) if spt else None,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # HELPER — GET RATE
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _get_rate(db: Session, tax_type: str, income_type_code: str, has_npwp: bool) -> Decimal:
        row = db.execute(
            text("""
                SELECT rate_pct, rate_npwp_pct FROM wht_rate
                WHERE tax_type = :tt AND income_type_code = :itc
                ORDER BY effective_date DESC LIMIT 1
            """),
            {"tt": tax_type, "itc": income_type_code},
        ).fetchone()
        if not row:
            raise ValueError(
                f"Tarif untuk {tax_type} / {income_type_code} tidak ditemukan. "
                "Cek tabel wht_rate."
            )
        if has_npwp and row.rate_npwp_pct is not None:
            return Decimal(str(row.rate_npwp_pct))
        # Jika tidak ber-NPWP untuk PPh 23 → tarif 2x
        if not has_npwp and tax_type == "PPh23":
            return Decimal(str(row.rate_pct)) * 2
        return Decimal(str(row.rate_pct))
