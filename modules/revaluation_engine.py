"""
Revaluation Engine
==================
Menghitung dan memposting jurnal revaluasi mata uang asing (FCY).

Dua jenis G/L selisih kurs:
  A. UNREALIZED (dari revaluation periodik):
     - Akhir bulan/tahun, nilai IDR buku vs kurs baru
     - Akun: Keuntungan/Kerugian Selisih Kurs Belum Terealisasi
     - Dapat di-reverse awal bulan berikutnya (opsional)

  B. REALIZED (saat AR/AP FCY dibayar):
     - Beda kurs invoice vs kurs saat pembayaran
     - Akun: Keuntungan/Kerugian Selisih Kurs Terealisasi
     - Tidak dapat di-reverse

Flow revaluation:
  1. run_revaluation() → identifikasi semua akun FCY
  2. Hitung adjustment per akun per currency
  3. Buat draft revaluation_run + revaluation_entry
  4. post_revaluation() → posting ke GL
  5. (opsional) auto_reverse → buat jurnal balik awal bulan berikut

GL Accounts default:
  7-1000 : Keuntungan Selisih Kurs (untuk gain)
  8-1000 : Kerugian Selisih Kurs   (untuk loss)
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from sqlalchemy import text
from sqlalchemy.orm import Session

from .exchange_rate_engine import ExchangeRateEngine


# ── Helper ────────────────────────────────────────────────────────────────────

def _to_d(val) -> Decimal:
    return Decimal(str(val)) if val is not None else Decimal("0")


class RevaluationEngine:

    # ── Preview (sebelum posting) ─────────────────────────────────────────────

    @staticmethod
    def preview_revaluation(
        db: Session,
        entity_id: str,
        revaluation_date: date,
        rate_type: str = "middle",
        gl_gain_account: str = "7-1000",
        gl_loss_account: str = "8-1000",
    ) -> dict:
        """
        Hitung adjustment tanpa memposting apapun.
        Berguna untuk review sebelum eksekusi.
        """
        entries = RevaluationEngine._compute_entries(
            db, entity_id, revaluation_date, rate_type
        )

        total_gain = sum(_to_d(e["adjustment"]) for e in entries if e["is_gain"])
        total_loss = sum(abs(_to_d(e["adjustment"])) for e in entries if not e["is_gain"] and e["adjustment"] != 0)

        return {
            "entity_id": entity_id,
            "revaluation_date": str(revaluation_date),
            "rate_type": rate_type,
            "gl_gain_account": gl_gain_account,
            "gl_loss_account": gl_loss_account,
            "total_gain": str(total_gain),
            "total_loss": str(total_loss),
            "net_adjustment": str(total_gain - total_loss),
            "entries": entries,
        }

    @staticmethod
    def _compute_entries(
        db: Session,
        entity_id: str,
        revaluation_date: date,
        rate_type: str = "middle",
    ) -> list[dict]:
        """
        Untuk setiap akun monetary dengan saldo FCY, hitung:
          - FCY net balance
          - Current IDR book value
          - New IDR value (FCY × rate_baru)
          - Adjustment = new - old
        """
        # Ambil semua akun asset/liability dengan transaksi FCY
        rows = db.execute(text("""
            SELECT
                coa.id              AS account_id,
                coa.account_code,
                coa.account_name,
                gl.currency,
                SUM(
                    gl.amount_fcy * CASE
                        WHEN gl.debit_idr > 0 THEN 1
                        WHEN gl.credit_idr > 0 THEN -1
                        ELSE 0
                    END
                )                   AS fcy_net_balance,
                SUM(gl.debit_idr - gl.credit_idr) AS idr_book_value
            FROM gl_line gl
            JOIN gl_journal gj         ON gj.id = gl.journal_id
                                       AND gj.status = 'posted'
                                       AND gj.entity_id = :entity_id
                                       AND gj.journal_date <= :rev_date
            JOIN chart_of_accounts coa ON coa.id = gl.account_id
                                       AND coa.account_type IN ('asset','liability')
            WHERE gl.currency IS NOT NULL
              AND gl.currency != 'IDR'
              AND gl.amount_fcy IS NOT NULL
              AND gl.amount_fcy != 0
            GROUP BY coa.id, coa.account_code, coa.account_name, gl.currency
            HAVING ABS(SUM(
                gl.amount_fcy * CASE
                    WHEN gl.debit_idr > 0 THEN 1
                    WHEN gl.credit_idr > 0 THEN -1
                    ELSE 0
                END
            )) > 0.001
        """), {"entity_id": entity_id, "rev_date": revaluation_date}).fetchall()

        entries = []
        for row in rows:
            new_rate = ExchangeRateEngine.get_rate(
                db, row.currency, "IDR", revaluation_date, rate_type
            )
            if new_rate is None:
                entries.append({
                    "account_id": str(row.account_id),
                    "account_code": row.account_code,
                    "account_name": row.account_name,
                    "currency": row.currency,
                    "fcy_net_balance": str(_to_d(row.fcy_net_balance)),
                    "book_idr_value": str(_to_d(row.idr_book_value)),
                    "new_rate": None,
                    "new_idr_value": None,
                    "adjustment": "0",
                    "is_gain": False,
                    "warning": f"Kurs {row.currency}/IDR tidak tersedia pada {revaluation_date}",
                })
                continue

            fcy_balance = _to_d(row.fcy_net_balance)
            book_idr = _to_d(row.idr_book_value)
            new_idr = (fcy_balance * new_rate).quantize(Decimal("1"), ROUND_HALF_UP)
            adjustment = new_idr - book_idr

            if abs(adjustment) < Decimal("1"):
                continue  # selisih < Rp 1, skip

            entries.append({
                "account_id": str(row.account_id),
                "account_code": row.account_code,
                "account_name": row.account_name,
                "currency": row.currency,
                "fcy_net_balance": str(fcy_balance),
                "book_idr_value": str(book_idr),
                "new_rate": str(new_rate),
                "new_idr_value": str(new_idr),
                "adjustment": str(adjustment),
                "is_gain": adjustment > 0,
            })
        return entries

    # ── Create Draft Run ──────────────────────────────────────────────────────

    @staticmethod
    def create_run(
        db: Session,
        entity_id: str,
        revaluation_date: date,
        run_by: str,
        auto_reverse: bool = False,
        gl_gain_account: str = "7-1000",
        gl_loss_account: str = "8-1000",
        rate_type: str = "middle",
        notes: str = None,
    ) -> dict:
        """
        Buat revaluation_run dalam status 'draft' + isi revaluation_entry.
        Belum memposting ke GL.
        """
        fiscal_year = revaluation_date.year
        fiscal_month = revaluation_date.month

        # Cek apakah sudah ada run untuk periode ini
        existing = db.execute(text("""
            SELECT id, status FROM revaluation_run
            WHERE entity_id = :eid AND fiscal_year = :fy AND fiscal_month = :fm
        """), {"eid": entity_id, "fy": fiscal_year, "fm": fiscal_month}).first()

        if existing and existing.status == "posted":
            raise ValueError(
                f"Revaluasi untuk periode {fiscal_year}-{fiscal_month:02d} sudah diposting. "
                "Lakukan reverse terlebih dahulu."
            )

        if existing and existing.status == "reversed":
            # Hapus run lama yang sudah reversed → buat baru
            db.execute(text("DELETE FROM revaluation_run WHERE id = :id"), {"id": str(existing.id)})

        # Hitung entries
        entries = RevaluationEngine._compute_entries(db, entity_id, revaluation_date, rate_type)

        if not entries:
            raise ValueError("Tidak ada saldo FCY yang perlu direvaluasi.")

        total_gain = sum(_to_d(e["adjustment"]) for e in entries if e["is_gain"])
        total_loss = sum(abs(_to_d(e["adjustment"])) for e in entries if not e.get("is_gain", False) and _to_d(e["adjustment"]) < 0)

        # Insert run
        run_row = db.execute(text("""
            INSERT INTO revaluation_run
                (entity_id, run_date, fiscal_year, fiscal_month, status,
                 auto_reverse, gl_gain_account, gl_loss_account,
                 total_gain, total_loss, run_by, notes)
            VALUES
                (:eid, :dt, :fy, :fm, 'draft',
                 :ar, :gain_acc, :loss_acc,
                 :tg, :tl, :run_by, :notes)
            ON CONFLICT (entity_id, fiscal_year, fiscal_month)
            DO UPDATE SET
                run_date = EXCLUDED.run_date,
                status = 'draft',
                auto_reverse = EXCLUDED.auto_reverse,
                gl_gain_account = EXCLUDED.gl_gain_account,
                gl_loss_account = EXCLUDED.gl_loss_account,
                total_gain = EXCLUDED.total_gain,
                total_loss = EXCLUDED.total_loss,
                run_by = EXCLUDED.run_by,
                notes = EXCLUDED.notes
            RETURNING id
        """), {
            "eid": entity_id, "dt": revaluation_date,
            "fy": fiscal_year, "fm": fiscal_month,
            "ar": auto_reverse, "gain_acc": gl_gain_account, "loss_acc": gl_loss_account,
            "tg": str(total_gain), "tl": str(total_loss),
            "run_by": run_by, "notes": notes,
        }).first()
        run_id = str(run_row.id)

        # Hapus entries lama (kalau ada)
        db.execute(text("DELETE FROM revaluation_entry WHERE run_id = :rid"), {"rid": run_id})

        # Insert entries baru
        for e in entries:
            if e.get("warning"):
                continue  # skip akun yang tidak ada kursnya
            db.execute(text("""
                INSERT INTO revaluation_entry
                    (run_id, account_id, account_code, account_name,
                     currency, fcy_balance, book_idr_value, new_rate,
                     new_idr_value, adjustment, is_gain)
                VALUES
                    (:rid, :acct, :code, :name,
                     :cur, :fcy, :book, :rate,
                     :new_idr, :adj, :gain)
            """), {
                "rid": run_id,
                "acct": e["account_id"],
                "code": e["account_code"],
                "name": e["account_name"],
                "cur": e["currency"],
                "fcy": e["fcy_net_balance"],
                "book": e["book_idr_value"],
                "rate": e["new_rate"],
                "new_idr": e["new_idr_value"],
                "adj": e["adjustment"],
                "gain": e["is_gain"],
            })

        db.commit()
        return {
            "run_id": run_id,
            "status": "draft",
            "total_gain": str(total_gain),
            "total_loss": str(total_loss),
            "net_adjustment": str(total_gain - total_loss),
            "entry_count": len([e for e in entries if not e.get("warning")]),
            "warnings": [e for e in entries if e.get("warning")],
        }

    # ── Post ke GL ────────────────────────────────────────────────────────────

    @staticmethod
    def post_revaluation(
        db: Session,
        run_id: str,
        entity_id: str,
        posted_by: str,
    ) -> dict:
        """
        Posting jurnal revaluasi ke GL.
        Satu jurnal dengan multiple line: untuk setiap entry,
          GAIN: Dr FCY Account | Cr Keuntungan Selisih Kurs
          LOSS: Dr Kerugian Selisih Kurs | Cr FCY Account
        """
        run = db.execute(text("""
            SELECT * FROM revaluation_run WHERE id = :id AND entity_id = :eid
        """), {"id": run_id, "eid": entity_id}).first()

        if run is None:
            raise ValueError("Revaluation run tidak ditemukan.")
        if run.status == "posted":
            raise ValueError("Sudah diposting.")
        if run.status == "reversed":
            raise ValueError("Sudah di-reverse, buat run baru.")

        entries = db.execute(text("""
            SELECT * FROM revaluation_entry WHERE run_id = :rid
        """), {"rid": run_id}).fetchall()

        if not entries:
            raise ValueError("Tidak ada entry revaluasi.")

        # Pastikan akun gain/loss ada di CoA
        gain_acct = db.execute(text("""
            SELECT id FROM chart_of_accounts
            WHERE account_code = :code AND entity_id = :eid
        """), {"code": run.gl_gain_account, "eid": entity_id}).first()

        loss_acct = db.execute(text("""
            SELECT id FROM chart_of_accounts
            WHERE account_code = :code AND entity_id = :eid
        """), {"code": run.gl_loss_account, "eid": entity_id}).first()

        if gain_acct is None:
            raise ValueError(f"Akun gain '{run.gl_gain_account}' tidak ditemukan di CoA entity ini.")
        if loss_acct is None:
            raise ValueError(f"Akun loss '{run.gl_loss_account}' tidak ditemukan di CoA entity ini.")

        # Buat jurnal GL
        journal_row = db.execute(text("""
            INSERT INTO gl_journal
                (entity_id, journal_date, journal_type, description,
                 reference_number, status, has_fcy, created_by, created_at)
            VALUES
                (:eid, :jdt, 'revaluation',
                 :desc, :ref, 'posted', TRUE, :by, NOW())
            RETURNING id, journal_number
        """), {
            "eid": entity_id,
            "jdt": run.run_date,
            "desc": f"Revaluasi Kurs FCY - {run.run_date.strftime('%B %Y')}",
            "ref": f"REVAL-{run.fiscal_year}{run.fiscal_month:02d}",
            "by": posted_by,
        }).first()
        journal_id = str(journal_row.id)
        journal_number = journal_row.journal_number

        # Buat GL lines
        total_gain = Decimal("0")
        total_loss = Decimal("0")
        gain_agg = Decimal("0")
        loss_agg = Decimal("0")
        line_updates = []

        for entry in entries:
            adj = _to_d(entry.adjustment)
            if adj == 0:
                continue
            adj_abs = abs(adj)

            if entry.is_gain:
                # Dr: FCY Account (asset naik)
                dr_line = db.execute(text("""
                    INSERT INTO gl_line
                        (journal_id, account_id, description,
                         debit_idr, credit_idr, currency, amount_fcy, exchange_rate)
                    VALUES
                        (:jid, :acct, :desc,
                         :dr, 0, 'IDR', NULL, NULL)
                    RETURNING id
                """), {
                    "jid": journal_id,
                    "acct": str(entry.account_id),
                    "desc": f"Revaluasi {entry.currency} - {entry.account_code}",
                    "dr": str(adj_abs),
                }).first()

                # Cr: Keuntungan Selisih Kurs
                cr_line = db.execute(text("""
                    INSERT INTO gl_line
                        (journal_id, account_id, description,
                         debit_idr, credit_idr, currency, amount_fcy, exchange_rate)
                    VALUES
                        (:jid, :acct, :desc,
                         0, :cr, 'IDR', NULL, NULL)
                    RETURNING id
                """), {
                    "jid": journal_id,
                    "acct": str(gain_acct.id),
                    "desc": f"Keuntungan kurs {entry.currency} - {entry.account_code}",
                    "cr": str(adj_abs),
                }).first()
                gain_agg += adj_abs
                line_updates.append((str(entry.id), str(dr_line.id), str(cr_line.id)))
            else:
                # Loss: Dr Kerugian | Cr FCY Account
                dr_line = db.execute(text("""
                    INSERT INTO gl_line
                        (journal_id, account_id, description,
                         debit_idr, credit_idr, currency, amount_fcy, exchange_rate)
                    VALUES
                        (:jid, :acct, :desc,
                         :dr, 0, 'IDR', NULL, NULL)
                    RETURNING id
                """), {
                    "jid": journal_id,
                    "acct": str(loss_acct.id),
                    "desc": f"Kerugian kurs {entry.currency} - {entry.account_code}",
                    "dr": str(adj_abs),
                }).first()

                cr_line = db.execute(text("""
                    INSERT INTO gl_line
                        (journal_id, account_id, description,
                         debit_idr, credit_idr, currency, amount_fcy, exchange_rate)
                    VALUES
                        (:jid, :acct, :desc,
                         0, :cr, 'IDR', NULL, NULL)
                    RETURNING id
                """), {
                    "jid": journal_id,
                    "acct": str(entry.account_id),
                    "desc": f"Revaluasi {entry.currency} - {entry.account_code}",
                    "cr": str(adj_abs),
                }).first()
                loss_agg += adj_abs
                line_updates.append((str(entry.id), str(dr_line.id), str(cr_line.id)))

        # Update entry dengan GL line IDs
        for (entry_id, dr_id, cr_id) in line_updates:
            db.execute(text("""
                UPDATE revaluation_entry
                   SET gl_line_debit = :dr, gl_line_credit = :cr
                WHERE id = :id
            """), {"dr": dr_id, "cr": cr_id, "id": entry_id})

        # Update run status
        db.execute(text("""
            UPDATE revaluation_run
               SET status = 'posted',
                   journal_id = :jid,
                   total_gain = :tg,
                   total_loss = :tl
            WHERE id = :rid
        """), {
            "jid": journal_id,
            "tg": str(gain_agg),
            "tl": str(loss_agg),
            "rid": run_id,
        })

        # Auto-reverse jika diminta
        reversal_journal_id = None
        if run.auto_reverse:
            reversal_journal_id = RevaluationEngine._create_reversal_journal(
                db, entity_id, run_id, journal_id, run.run_date, posted_by
            )

        db.commit()
        return {
            "run_id": run_id,
            "journal_id": journal_id,
            "journal_number": journal_number,
            "status": "posted",
            "total_gain": str(gain_agg),
            "total_loss": str(loss_agg),
            "net_adjustment": str(gain_agg - loss_agg),
            "reversal_journal_id": reversal_journal_id,
        }

    @staticmethod
    def _create_reversal_journal(
        db: Session,
        entity_id: str,
        run_id: str,
        original_journal_id: str,
        run_date: date,
        created_by: str,
    ) -> str:
        """
        Buat jurnal balik (reversal) pada tanggal 1 bulan berikutnya.
        Membalik semua Dr/Cr dari jurnal revaluasi original.
        """
        first_of_next_month = (run_date.replace(day=1) + timedelta(days=32)).replace(day=1)

        # Ambil semua lines dari jurnal original
        lines = db.execute(text("""
            SELECT account_id, description, debit_idr, credit_idr
            FROM gl_line
            WHERE journal_id = :jid
        """), {"jid": original_journal_id}).fetchall()

        rev_journal = db.execute(text("""
            INSERT INTO gl_journal
                (entity_id, journal_date, journal_type, description,
                 reference_number, status, has_fcy, created_by, created_at)
            VALUES
                (:eid, :jdt, 'revaluation_reversal',
                 :desc, :ref, 'posted', FALSE, :by, NOW())
            RETURNING id
        """), {
            "eid": entity_id,
            "jdt": first_of_next_month,
            "desc": f"Balik Revaluasi Kurs - {run_date.strftime('%B %Y')}",
            "ref": f"REVAL-REV-{run_date.year}{run_date.month:02d}",
            "by": created_by,
        }).first()
        rev_journal_id = str(rev_journal.id)

        for line in lines:
            # Balik Dr ↔ Cr
            db.execute(text("""
                INSERT INTO gl_line
                    (journal_id, account_id, description, debit_idr, credit_idr)
                VALUES
                    (:jid, :acct, :desc, :dr, :cr)
            """), {
                "jid": rev_journal_id,
                "acct": str(line.account_id),
                "desc": "Balik: " + (line.description or ""),
                "dr": str(_to_d(line.credit_idr)),   # balik
                "cr": str(_to_d(line.debit_idr)),    # balik
            })

        # Simpan link ke reversal journal
        db.execute(text("""
            UPDATE revaluation_run SET reversal_journal_id = :rjid WHERE id = :rid
        """), {"rjid": rev_journal_id, "rid": run_id})

        return rev_journal_id

    # ── Reverse ───────────────────────────────────────────────────────────────

    @staticmethod
    def reverse_revaluation(
        db: Session,
        run_id: str,
        entity_id: str,
        reversed_by: str,
        reason: str = None,
    ) -> dict:
        """
        Manual reverse untuk revaluation yang sudah diposting.
        Berguna jika salah input kurs atau terjadi kesalahan.
        """
        from datetime import datetime

        run = db.execute(text("""
            SELECT * FROM revaluation_run WHERE id = :id AND entity_id = :eid
        """), {"id": run_id, "eid": entity_id}).first()

        if run is None:
            raise ValueError("Revaluation run tidak ditemukan.")
        if run.status != "posted":
            raise ValueError(f"Status saat ini '{run.status}', hanya bisa reverse yang 'posted'.")
        if run.reversal_journal_id:
            raise ValueError("Sudah ada auto-reversal journal. Tidak perlu reverse manual.")

        # Buat jurnal reverse
        rev_journal_id = RevaluationEngine._create_reversal_journal(
            db, entity_id, run_id, str(run.journal_id), run.run_date, reversed_by
        )

        db.execute(text("""
            UPDATE revaluation_run
               SET status = 'reversed',
                   reversed_by = :rb,
                   reversed_at = NOW()
            WHERE id = :id
        """), {"rb": reversed_by, "id": run_id})

        db.commit()
        return {
            "run_id": run_id,
            "status": "reversed",
            "reversal_journal_id": rev_journal_id,
            "reversed_by": reversed_by,
        }

    # ── Realized G/L saat AR/AP Dibayar ──────────────────────────────────────

    @staticmethod
    def compute_realized_gain_loss(
        db: Session,
        entity_id: str,
        invoice_type: str,             # 'ar' | 'ap'
        invoice_currency: str,
        invoice_amount_fcy: Decimal,
        invoice_rate: Decimal,         # kurs saat faktur dibuat (booking rate)
        payment_rate: Decimal,         # kurs saat dibayar
        gl_gain_account: str = "7-1000",
        gl_loss_account: str = "8-1000",
    ) -> Optional[dict]:
        """
        Hitung realized gain/loss saat AR/AP FCY diselesaikan pada kurs berbeda
        dari kurs pembukuan. TIDAK melakukan posting — hanya kalkulasi murni,
        supaya line gain/loss bisa disisipkan ke journal pembayaran utama
        SEBELUM di-post (jurnal harus balance dalam satu kali post_journal()).

        Rumus dasar:
          invoice_idr = amount_fcy × invoice_rate   (nilai liability/receivable yang dibukukan)
          payment_idr = amount_fcy × payment_rate   (kas riil yang berpindah)

        Untuk AR: gain kalau kas yang DITERIMA lebih besar dari nilai dibukukan
          → realized_gl = payment_idr - invoice_idr
        Untuk AP: gain kalau kas yang DIKELUARKAN lebih kecil dari nilai liability
          yang dibukukan (kebalikan dari AR, karena payment_idr di sisi AP adalah
          kas keluar bukan kas masuk)
          → realized_gl = invoice_idr - payment_idr

        realized_gl positif = gain (favorable buat entity) di kedua kasus.

        Return None kalau currency IDR atau selisihnya < Rp1 (tidak perlu jurnal tambahan).
        """
        if invoice_currency == "IDR":
            return None

        invoice_idr = (_to_d(invoice_amount_fcy) * _to_d(invoice_rate)).quantize(Decimal("1"), ROUND_HALF_UP)
        payment_idr = (_to_d(invoice_amount_fcy) * _to_d(payment_rate)).quantize(Decimal("1"), ROUND_HALF_UP)

        if invoice_type == "ar":
            realized_gl = payment_idr - invoice_idr
        else:
            realized_gl = invoice_idr - payment_idr

        if abs(realized_gl) < Decimal("1"):
            return None  # selisih terlalu kecil, skip

        is_gain = realized_gl > 0
        abs_amount = abs(realized_gl)

        gain_row = db.execute(text("""
            SELECT id FROM chart_of_accounts
            WHERE account_code = :code AND entity_id = :eid
        """), {"code": gl_gain_account, "eid": entity_id}).first()

        loss_row = db.execute(text("""
            SELECT id FROM chart_of_accounts
            WHERE account_code = :code AND entity_id = :eid
        """), {"code": gl_loss_account, "eid": entity_id}).first()

        if gain_row is None or loss_row is None:
            raise ValueError("Akun G/L selisih kurs (7-1000/8-1000) belum dikonfigurasi di CoA entity ini.")

        account_code = gl_gain_account if is_gain else gl_loss_account
        desc = (
            f"Realized {'gain' if is_gain else 'loss'} kurs {invoice_currency} "
            f"- {'Piutang' if invoice_type == 'ar' else 'Hutang'} "
            f"@ invoice rate {invoice_rate} vs payment rate {payment_rate}"
        )

        return {
            "realized_gl": realized_gl,
            "is_gain": is_gain,
            "abs_amount": abs_amount,
            "account_code": account_code,
            "description": desc,
            "invoice_rate": invoice_rate,
            "payment_rate": payment_rate,
            "invoice_currency": invoice_currency,
        }

    @staticmethod
    def mark_realized_gl_journal(db: Session, invoice_type: str, invoice_id: str, journal_id: str) -> None:
        """Catat jurnal pembayaran (yang sudah memuat line realized G/L) di invoice terkait."""
        acct_table = "ar_invoice" if invoice_type == "ar" else "ap_invoice"
        db.execute(text(f"""
            UPDATE {acct_table} SET realized_gl_journal_id = :jid WHERE id = :inv_id
        """), {"jid": journal_id, "inv_id": invoice_id})

    # ── Reports ───────────────────────────────────────────────────────────────

    @staticmethod
    def get_run_history(
        db: Session,
        entity_id: str,
        limit: int = 24,
    ) -> list[dict]:
        rows = db.execute(text("""
            SELECT
                r.id, r.run_date, r.fiscal_year, r.fiscal_month,
                r.status, r.total_gain, r.total_loss,
                (r.total_gain - r.total_loss) AS net_adjustment,
                r.auto_reverse, r.run_by, r.created_at,
                j.journal_number, rj.journal_number AS reversal_journal_number,
                COUNT(e.id) AS entry_count
            FROM revaluation_run r
            LEFT JOIN gl_journal j  ON j.id = r.journal_id
            LEFT JOIN gl_journal rj ON rj.id = r.reversal_journal_id
            LEFT JOIN revaluation_entry e ON e.run_id = r.id
            WHERE r.entity_id = :eid
            GROUP BY r.id, j.journal_number, rj.journal_number
            ORDER BY r.run_date DESC
            LIMIT :lim
        """), {"eid": entity_id, "lim": limit}).fetchall()
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def get_run_detail(
        db: Session,
        run_id: str,
        entity_id: str,
    ) -> dict:
        run = db.execute(text("""
            SELECT r.*, j.journal_number, rj.journal_number AS reversal_number
            FROM revaluation_run r
            LEFT JOIN gl_journal j  ON j.id = r.journal_id
            LEFT JOIN gl_journal rj ON rj.id = r.reversal_journal_id
            WHERE r.id = :id AND r.entity_id = :eid
        """), {"id": run_id, "eid": entity_id}).first()

        if run is None:
            raise ValueError("Revaluation run tidak ditemukan.")

        entries = db.execute(text("""
            SELECT * FROM revaluation_entry WHERE run_id = :rid ORDER BY currency, account_code
        """), {"rid": run_id}).fetchall()

        return {
            **dict(run._mapping),
            "entries": [dict(e._mapping) for e in entries],
        }

    @staticmethod
    def get_fcy_exposure(
        db: Session,
        entity_id: str,
    ) -> list[dict]:
        """
        FCY exposure per currency: total saldo FCY + nilai IDR saat ini + unrealized G/L.
        """
        rows = db.execute(text("""
            SELECT
                currency,
                SUM(fcy_net_balance)  AS total_fcy,
                SUM(idr_book_value)   AS idr_book,
                MAX(latest_rate)      AS latest_rate,
                SUM(idr_at_latest_rate) AS idr_at_latest_rate,
                SUM(unrealized_position) AS unrealized_position
            FROM vw_fcy_exposure
            WHERE entity_id = :eid
            GROUP BY currency
            ORDER BY currency
        """), {"eid": entity_id}).fetchall()
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def get_gl_fcy_report(
        db: Session,
        entity_id: str,
        currency: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> list[dict]:
        """
        Daftar semua transaksi GL yang melibatkan FCY.
        """
        conditions = ["gj.entity_id = :eid", "gj.status = 'posted'",
                      "gl.currency IS NOT NULL", "gl.currency != 'IDR'"]
        params: dict = {"eid": entity_id}

        if currency:
            conditions.append("gl.currency = :cur")
            params["cur"] = currency
        if date_from:
            conditions.append("gj.journal_date >= :df")
            params["df"] = date_from
        if date_to:
            conditions.append("gj.journal_date <= :dt")
            params["dt"] = date_to

        where = " AND ".join(conditions)
        rows = db.execute(text(f"""
            SELECT
                gj.journal_date,
                gj.journal_number,
                gj.description AS journal_desc,
                coa.account_code,
                coa.account_name,
                gl.currency,
                gl.amount_fcy,
                gl.exchange_rate,
                gl.debit_idr,
                gl.credit_idr,
                gl.description AS line_desc
            FROM gl_line gl
            JOIN gl_journal gj         ON gj.id = gl.journal_id
            JOIN chart_of_accounts coa ON coa.id = gl.account_id
            WHERE {where}
            ORDER BY gj.journal_date DESC, gj.journal_number, coa.account_code
        """), params).fetchall()
        return [dict(r._mapping) for r in rows]
