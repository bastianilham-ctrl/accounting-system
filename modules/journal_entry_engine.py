# modules/journal_entry_engine.py
# Manual Journal Entry Workflow Engine
#
# Prinsip Four-Eyes (Segregation of Duties):
#   Pembuat (Finance Staff) ≠ Penyetuju (Accounting Supervisor / Finance Manager)
#
# State machine:
#   draft ──submit──► pending_approval ──approve──► approved ──post──► posted
#                               │                                        │
#                            reject                                   reverse
#                               │                                        │
#                            rejected ──(edit)──► draft            reversed
#   draft / rejected ──cancel──► cancelled
#
# Keamanan pasca-posting (BAB III §3):
#   1. Anti-delete — posted journal tidak bisa dihapus / diedit
#   2. Reversal only — koreksi hanya via jurnal balik otomatis
#   3. Audit trail — setiap aksi tersimpan di journal_approval_log

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import uuid4

from sqlalchemy.orm import Session
from sqlalchemy import text

# Toleransi selisih pembulatan: selisih ≤ Rp1 masih diterima
BALANCE_TOLERANCE = Decimal("1.00")


# ── Data Classes ───────────────────────────────────────────────────────────────

@dataclass
class JELineInput:
    account_code:  str
    debit_amount:  Decimal = Decimal("0")
    credit_amount: Decimal = Decimal("0")
    description:   Optional[str] = None
    cost_center:   Optional[str] = None
    project_id:    Optional[str] = None
    vendor_id:     Optional[str] = None
    tax_code:      Optional[str] = None
    tax_amount:    Decimal = Decimal("0")

    def validate(self):
        if self.debit_amount < 0 or self.credit_amount < 0:
            raise ValueError(f"Nominal negatif tidak diizinkan — akun {self.account_code}")
        if self.debit_amount > 0 and self.credit_amount > 0:
            raise ValueError(f"Satu baris tidak boleh memiliki debet DAN kredit sekaligus — akun {self.account_code}")
        if self.debit_amount == 0 and self.credit_amount == 0:
            raise ValueError(f"Baris tidak boleh bernilai nol — akun {self.account_code}")


@dataclass
class ValidationResult:
    is_valid:    bool
    errors:      list[str] = field(default_factory=list)

    def add(self, msg: str):
        self.errors.append(msg)
        self.is_valid = False


# ── Core Engine ────────────────────────────────────────────────────────────────

class JournalEntryEngine:
    def __init__(self, db: Session):
        self.db = db

    # ── 1. Create Draft ───────────────────────────────────────────────────────

    def create_draft(
        self,
        entity_id:    str,
        journal_date: date,
        journal_type: str,
        description:  str,
        lines:        list[JELineInput],
        currency:     str = "IDR",
        exchange_rate: float = 1.0,
        reference_no: Optional[str] = None,
        attachment_url: Optional[str] = None,
        created_by:   str = "system",
    ) -> dict:
        """
        Buat jurnal dengan status Draft.
        Belum ada dampak ke GL — angka belum masuk ke Laporan Keuangan.
        """
        if not lines:
            raise ValueError("Jurnal harus memiliki minimal 2 baris")

        for ln in lines:
            ln.validate()

        er    = Decimal(str(exchange_rate))
        entry_no = _gen_entry_no(self.db)
        entry_id = str(uuid4())

        # Hitung totals
        total_dr_cur = sum(ln.debit_amount  for ln in lines)
        total_cr_cur = sum(ln.credit_amount for ln in lines)
        total_dr_idr = (total_dr_cur * er).quantize(Decimal("0.01"), ROUND_HALF_UP)
        total_cr_idr = (total_cr_cur * er).quantize(Decimal("0.01"), ROUND_HALF_UP)

        period_year  = journal_date.year
        period_month = journal_date.month

        self.db.execute(
            text("""
                INSERT INTO journal_entry (
                    id, entity_id, entry_no, journal_date,
                    period_year, period_month, journal_type,
                    currency, exchange_rate,
                    total_debit_currency, total_credit_currency,
                    total_debit_idr, total_credit_idr,
                    description, reference_no, attachment_url,
                    status, created_by, created_at, updated_at
                ) VALUES (
                    :id, :eid, :no, :dt,
                    :yr, :mo, :jtype,
                    :cur, :er,
                    :tdc, :tcc, :tdi, :tci,
                    :desc, :ref, :att,
                    'draft', :by, NOW(), NOW()
                )
            """),
            {
                "id": entry_id, "eid": entity_id, "no": entry_no,
                "dt": journal_date, "yr": period_year, "mo": period_month,
                "jtype": journal_type,
                "cur": currency, "er": float(er),
                "tdc": float(total_dr_cur), "tcc": float(total_cr_cur),
                "tdi": float(total_dr_idr), "tci": float(total_cr_idr),
                "desc": description, "ref": reference_no, "att": attachment_url,
                "by": created_by,
            }
        )

        # Insert lines
        for i, ln in enumerate(lines, 1):
            dr_idr = (ln.debit_amount  * er).quantize(Decimal("0.01"), ROUND_HALF_UP)
            cr_idr = (ln.credit_amount * er).quantize(Decimal("0.01"), ROUND_HALF_UP)

            # Snapshot nama akun
            acc_name = self.db.execute(
                text("SELECT account_name FROM chart_of_accounts WHERE account_code = :c AND entity_id = :eid"),
                {"c": ln.account_code, "eid": entity_id}
            ).scalar()

            self.db.execute(
                text("""
                    INSERT INTO journal_entry_line (
                        id, entry_id, line_no,
                        account_code, account_name, description,
                        debit_amount, credit_amount, debit_idr, credit_idr,
                        cost_center, project_id, vendor_id,
                        tax_code, tax_amount
                    ) VALUES (
                        uuid_generate_v4(), :eid, :no,
                        :acc, :aname, :desc,
                        :dr, :cr, :dri, :cri,
                        :cc, :pid, :vid, :tc, :ta
                    )
                """),
                {
                    "eid": entry_id, "no": i,
                    "acc": ln.account_code, "aname": acc_name,
                    "desc": ln.description,
                    "dr": float(ln.debit_amount), "cr": float(ln.credit_amount),
                    "dri": float(dr_idr), "cri": float(cr_idr),
                    "cc": ln.cost_center, "pid": ln.project_id,
                    "vid": ln.vendor_id, "tc": ln.tax_code, "ta": float(ln.tax_amount),
                }
            )

        _log(self.db, entry_id, "created", created_by, None, None, "draft", "draft", "Jurnal dibuat sebagai draft")
        self.db.commit()

        return {
            "entry_id":   entry_id,
            "entry_no":   entry_no,
            "status":     "draft",
            "currency":   currency,
            "exchange_rate": float(er),
            "total_debit_idr":  float(total_dr_idr),
            "total_credit_idr": float(total_cr_idr),
            "line_count": len(lines),
        }

    # ── 2. Update Draft Lines ─────────────────────────────────────────────────

    def update_draft(
        self,
        entry_id:     str,
        journal_date: Optional[date] = None,
        description:  Optional[str]  = None,
        currency:     Optional[str]   = None,
        exchange_rate: Optional[float] = None,
        lines:        Optional[list[JELineInput]] = None,
        updated_by:   str = "system",
    ) -> dict:
        """Update draft — hanya boleh dilakukan saat status = draft atau rejected."""
        entry = self._get_entry_or_raise(entry_id)
        if entry.status not in ("draft", "rejected"):
            raise ValueError(f"Jurnal dengan status '{entry.status}' tidak bisa diedit. "
                             "Hanya draft atau rejected yang bisa diubah.")

        fields_to_update: dict = {"updated_at": "NOW()", "status": "draft"}
        params: dict = {"id": entry_id}

        if journal_date:
            fields_to_update["journal_date"] = ":jdt"
            fields_to_update["period_year"]  = ":pyr"
            fields_to_update["period_month"] = ":pmo"
            params["jdt"] = journal_date
            params["pyr"] = journal_date.year
            params["pmo"] = journal_date.month
        if description:
            fields_to_update["description"] = ":desc"
            params["desc"] = description
        if currency:
            fields_to_update["currency"] = ":cur"
            params["cur"] = currency
        if exchange_rate:
            fields_to_update["exchange_rate"] = ":er"
            params["er"] = exchange_rate

        if lines is not None:
            for ln in lines:
                ln.validate()

            er = Decimal(str(exchange_rate or float(entry.exchange_rate)))
            total_dr_cur = sum(ln.debit_amount  for ln in lines)
            total_cr_cur = sum(ln.credit_amount for ln in lines)
            total_dr_idr = (total_dr_cur * er).quantize(Decimal("0.01"), ROUND_HALF_UP)
            total_cr_idr = (total_cr_cur * er).quantize(Decimal("0.01"), ROUND_HALF_UP)

            fields_to_update.update({
                "total_debit_currency":  ":tdc",
                "total_credit_currency": ":tcc",
                "total_debit_idr":  ":tdi",
                "total_credit_idr": ":tci",
            })
            params.update({"tdc": float(total_dr_cur), "tcc": float(total_cr_cur),
                           "tdi": float(total_dr_idr), "tci": float(total_cr_idr)})

            # Hapus baris lama, insert baru
            self.db.execute(text("DELETE FROM journal_entry_line WHERE entry_id = :id"), {"id": entry_id})
            for i, ln in enumerate(lines, 1):
                dr_idr = (ln.debit_amount  * er).quantize(Decimal("0.01"), ROUND_HALF_UP)
                cr_idr = (ln.credit_amount * er).quantize(Decimal("0.01"), ROUND_HALF_UP)
                acc_name = self.db.execute(
                    text("SELECT account_name FROM chart_of_accounts WHERE account_code = :c AND entity_id = :eid"),
                    {"c": ln.account_code, "eid": str(entry.entity_id)}
                ).scalar()
                self.db.execute(
                    text("""
                        INSERT INTO journal_entry_line (
                            id, entry_id, line_no, account_code, account_name, description,
                            debit_amount, credit_amount, debit_idr, credit_idr,
                            cost_center, project_id, vendor_id, tax_code, tax_amount
                        ) VALUES (
                            uuid_generate_v4(), :eid, :no, :acc, :aname, :desc,
                            :dr, :cr, :dri, :cri, :cc, :pid, :vid, :tc, :ta
                        )
                    """),
                    {
                        "eid": entry_id, "no": i, "acc": ln.account_code, "aname": acc_name,
                        "desc": ln.description, "dr": float(ln.debit_amount), "cr": float(ln.credit_amount),
                        "dri": float(dr_idr), "cri": float(cr_idr), "cc": ln.cost_center,
                        "pid": ln.project_id, "vid": ln.vendor_id, "tc": ln.tax_code, "ta": float(ln.tax_amount),
                    }
                )

        # Build SET clause
        set_parts = []
        for col, val in fields_to_update.items():
            if val == "NOW()":
                set_parts.append(f"{col} = NOW()")
            else:
                set_parts.append(f"{col} = {val}")

        self.db.execute(
            text(f"UPDATE journal_entry SET {', '.join(set_parts)} WHERE id = :id"),
            params
        )
        _log(self.db, entry_id, "updated", updated_by, None, None, entry.status, "draft", "Draft diperbarui")
        self.db.commit()
        return {"entry_id": entry_id, "status": "draft"}

    # ── 3. Submit for Approval ────────────────────────────────────────────────

    def submit(self, entry_id: str, submitted_by: str) -> dict:
        """
        Submit jurnal untuk review.

        Sistem menjalankan dua validasi otomatis (Tahap 2):
          a. Balance Check : total debet = total kredit (toleransi Rp1)
          b. Period Check  : tanggal jurnal harus berada di periode yang masih Open
        """
        entry = self._get_entry_or_raise(entry_id)
        if entry.status not in ("draft", "rejected"):
            raise ValueError(f"Hanya jurnal draft/rejected yang bisa disubmit. Status saat ini: '{entry.status}'")

        # ── Validasi otomatis (hardcoded) ──────────────────────────────────
        vr = self._validate(entry)
        if not vr.is_valid:
            raise ValueError("Validasi gagal:\n• " + "\n• ".join(vr.errors))

        # Tentukan required role dari approval matrix
        required_role = _get_required_role(
            self.db,
            str(entry.entity_id),
            entry.journal_type,
            float(entry.total_debit_idr),
        )

        self.db.execute(
            text("""
                UPDATE journal_entry SET
                    status                = 'pending_approval',
                    required_approval_role = :role,
                    submitted_by          = :by,
                    submitted_at          = NOW(),
                    rejection_reason      = NULL,
                    updated_at            = NOW()
                WHERE id = :id
            """),
            {"id": entry_id, "role": required_role, "by": submitted_by}
        )
        _log(self.db, entry_id, "submitted", submitted_by, None, required_role,
             entry.status, "pending_approval",
             f"Membutuhkan approval dari role: {required_role}")
        self.db.commit()

        return {
            "entry_id":             entry_id,
            "entry_no":             entry.entry_no,
            "status":               "pending_approval",
            "required_approval_role": required_role,
            "message":              f"Jurnal menunggu persetujuan dari {required_role}",
        }

    # ── 4. Approve ────────────────────────────────────────────────────────────

    def approve(self, entry_id: str, reviewer: str, reviewer_role: str, notes: Optional[str] = None) -> dict:
        """
        Setujui jurnal. Status → approved.
        Reviewer HARUS memiliki role yang sesuai dengan required_approval_role.
        """
        entry = self._get_entry_or_raise(entry_id)
        if entry.status != "pending_approval":
            raise ValueError(f"Hanya jurnal 'pending_approval' yang bisa di-approve. Status: '{entry.status}'")

        _check_reviewer_role(reviewer_role, str(entry.required_approval_role))

        self.db.execute(
            text("""
                UPDATE journal_entry SET
                    status      = 'approved',
                    reviewed_by = :by,
                    reviewed_at = NOW(),
                    updated_at  = NOW()
                WHERE id = :id
            """),
            {"id": entry_id, "by": reviewer}
        )
        _log(self.db, entry_id, "approved", reviewer, reviewer_role,
             str(entry.required_approval_role),
             "pending_approval", "approved", notes or "Disetujui")
        self.db.commit()

        return {"entry_id": entry_id, "status": "approved", "reviewed_by": reviewer}

    # ── 5. Reject ─────────────────────────────────────────────────────────────

    def reject(self, entry_id: str, reviewer: str, reviewer_role: str, reason: str) -> dict:
        """
        Tolak jurnal. Status → rejected. Jurnal kembali ke drafter untuk direvisi.
        """
        entry = self._get_entry_or_raise(entry_id)
        if entry.status != "pending_approval":
            raise ValueError(f"Hanya jurnal 'pending_approval' yang bisa ditolak. Status: '{entry.status}'")

        _check_reviewer_role(reviewer_role, str(entry.required_approval_role))
        if not reason or not reason.strip():
            raise ValueError("Alasan penolakan wajib diisi")

        self.db.execute(
            text("""
                UPDATE journal_entry SET
                    status           = 'rejected',
                    reviewed_by      = :by,
                    reviewed_at      = NOW(),
                    rejection_reason = :reason,
                    updated_at       = NOW()
                WHERE id = :id
            """),
            {"id": entry_id, "by": reviewer, "reason": reason}
        )
        _log(self.db, entry_id, "rejected", reviewer, reviewer_role,
             str(entry.required_approval_role),
             "pending_approval", "rejected", reason)
        self.db.commit()

        return {
            "entry_id":         entry_id,
            "status":           "rejected",
            "rejection_reason": reason,
            "message":          "Jurnal dikembalikan ke staff untuk direvisi",
        }

    # ── 6. Post to General Ledger ─────────────────────────────────────────────

    def post(self, entry_id: str, posted_by: str, posted_by_role: str) -> dict:
        """
        Posting jurnal yang sudah approved ke General Ledger.

        Setelah posted:
          - Status = 'posted' (IMMUTABLE)
          - Angka mengalir ke GL, memperbarui Trial Balance & Laporan Keuangan
          - Tidak bisa diedit atau dihapus — hanya bisa dikoreksi via reversal
        """
        entry = self._get_entry_or_raise(entry_id)
        if entry.status != "approved":
            raise ValueError(f"Hanya jurnal 'approved' yang bisa diposting. Status: '{entry.status}'")

        # Hak posting: minimal sama dengan required_approval_role
        _check_reviewer_role(posted_by_role, str(entry.required_approval_role))

        # Ambil lines
        lines = self.db.execute(
            text("SELECT * FROM journal_entry_line WHERE entry_id = :id ORDER BY line_no"),
            {"id": entry_id}
        ).fetchall()

        # Integrasikan ke GL via JournalEngine
        from modules.journal_engine import JournalEngine, JournalEntry, JournalLine
        from decimal import Decimal as D

        je_lines = [
            JournalLine(
                account_code = ln.account_code,
                description  = ln.description or "",
                debit_idr    = D(str(ln.debit_idr)),
                credit_idr   = D(str(ln.credit_idr)),
                vendor_id    = ln.vendor_id,
                cost_center  = ln.cost_center,
                tax_code     = ln.tax_code,
                tax_amount   = D(str(ln.tax_amount)),
            )
            for ln in lines
        ]

        je = JournalEntry(
            entity_id    = entry.entity_id,
            journal_type = entry.journal_type.upper(),
            journal_date = entry.journal_date,
            description  = entry.description,
            lines        = je_lines,
            reference_no = entry.entry_no,
            currency     = entry.currency,
            source       = "journal_entry",
            created_by   = posted_by,
        )

        gl_engine = JournalEngine(self.db)
        result    = gl_engine.post_journal(je)

        if not result.get("success"):
            raise ValueError(f"GL posting gagal: {result.get('error')}")

        gl_journal_id = result.get("journal_id")

        self.db.execute(
            text("""
                UPDATE journal_entry SET
                    status        = 'posted',
                    gl_journal_id = :glid,
                    posted_by     = :by,
                    posted_at     = NOW(),
                    updated_at    = NOW()
                WHERE id = :id
            """),
            {"id": entry_id, "glid": gl_journal_id, "by": posted_by}
        )
        _log(self.db, entry_id, "posted", posted_by, posted_by_role,
             str(entry.required_approval_role),
             "approved", "posted",
             f"Diposting ke GL — gl_journal_id: {gl_journal_id}")
        self.db.commit()

        return {
            "entry_id":     entry_id,
            "entry_no":     entry.entry_no,
            "status":       "posted",
            "gl_journal_id": gl_journal_id,
            "message":      "Jurnal berhasil diposting ke General Ledger",
        }

    # ── 7. Full Reversal ──────────────────────────────────────────────────────

    def full_reversal(
        self,
        entry_id:       str,
        reversal_date:  date,
        reversed_by:    str,
        reversed_by_role: str,
        reason:         str,
        fast_track:     bool = False,
    ) -> dict:
        """
        Buat jurnal balik otomatis (debet↔kredit swap).

        Fast_track=True: jurnal reversal langsung masuk ke status 'approved',
        tinggal di-post. Khusus admin untuk koreksi cepat.

        Prinsip (BAB III §3): original journal tetap 'posted',
        hanya statusnya berubah ke 'reversed'. Tidak ada record yang dihapus.
        """
        entry = self._get_entry_or_raise(entry_id)
        if entry.status != "posted":
            raise ValueError(f"Hanya jurnal 'posted' yang bisa di-reverse. Status: '{entry.status}'")
        if entry.status == "reversed":
            raise ValueError("Jurnal ini sudah pernah di-reverse sebelumnya")

        # Cek periode reversal terbuka
        _check_period_open(self.db, str(entry.entity_id), reversal_date.year, reversal_date.month)

        orig_lines = self.db.execute(
            text("SELECT * FROM journal_entry_line WHERE entry_id = :id ORDER BY line_no"),
            {"id": entry_id}
        ).fetchall()

        # Buat reversal entry
        rev_entry_no = _gen_entry_no(self.db)
        rev_entry_id = str(uuid4())

        rev_status = "approved" if fast_track else "draft"

        self.db.execute(
            text("""
                INSERT INTO journal_entry (
                    id, entity_id, entry_no, journal_date,
                    period_year, period_month, journal_type,
                    currency, exchange_rate,
                    total_debit_currency, total_credit_currency,
                    total_debit_idr, total_credit_idr,
                    description, reference_no,
                    status, is_reversal, reversal_of_id,
                    required_approval_role,
                    submitted_by, submitted_at, reviewed_by, reviewed_at,
                    created_by, created_at, updated_at
                ) VALUES (
                    :id, :eid, :no, :dt,
                    :yr, :mo, 'reversal',
                    :cur, :er,
                    :tdc, :tcc, :tdi, :tci,
                    :desc, :ref,
                    :status, TRUE, :orig_id,
                    :role,
                    :by, NOW(), :reviewer, :reviewed_at_val,
                    :by, NOW(), NOW()
                )
            """),
            {
                "id": rev_entry_id, "eid": str(entry.entity_id),
                "no": rev_entry_no, "dt": reversal_date,
                "yr": reversal_date.year, "mo": reversal_date.month,
                "cur": entry.currency, "er": float(entry.exchange_rate),
                # Swap debit/credit totals
                "tdc": float(entry.total_credit_currency),
                "tcc": float(entry.total_debit_currency),
                "tdi": float(entry.total_credit_idr),
                "tci": float(entry.total_debit_idr),
                "desc": f"[REVERSAL] {entry.description} — {reason}",
                "ref":  entry.entry_no,
                "status": rev_status,
                "orig_id": entry_id,
                "role": str(entry.required_approval_role),
                "by": reversed_by,
                # Jika fast_track, auto-set reviewed
                "reviewer": reversed_by if fast_track else None,
                "reviewed_at_val": "NOW()" if fast_track else None,
            }
        )

        # Copy lines — swap debit↔credit
        for ln in orig_lines:
            self.db.execute(
                text("""
                    INSERT INTO journal_entry_line (
                        id, entry_id, line_no,
                        account_code, account_name, description,
                        debit_amount, credit_amount, debit_idr, credit_idr,
                        cost_center, project_id, vendor_id, tax_code, tax_amount
                    ) VALUES (
                        uuid_generate_v4(), :eid, :no,
                        :acc, :aname, :desc,
                        :dr, :cr, :dri, :cri,
                        :cc, :pid, :vid, :tc, :ta
                    )
                """),
                {
                    "eid": rev_entry_id, "no": ln.line_no,
                    "acc": ln.account_code, "aname": ln.account_name,
                    "desc": f"[REV] {ln.description or ''}",
                    "dr":  float(ln.credit_amount), "cr":  float(ln.debit_amount),   # swap
                    "dri": float(ln.credit_idr),    "cri": float(ln.debit_idr),       # swap
                    "cc": ln.cost_center, "pid": ln.project_id,
                    "vid": ln.vendor_id, "tc": ln.tax_code, "ta": float(ln.tax_amount),
                }
            )

        # Tandai original sebagai reversed
        self.db.execute(
            text("""
                UPDATE journal_entry SET
                    status = 'reversed', reversed_by_id = :revid, updated_at = NOW()
                WHERE id = :id
            """),
            {"id": entry_id, "revid": rev_entry_id}
        )

        _log(self.db, entry_id, "reversed", reversed_by, reversed_by_role, None,
             "posted", "reversed", f"Reversal dibuat: {rev_entry_no} — {reason}")
        _log(self.db, rev_entry_id, "created", reversed_by, reversed_by_role, None,
             None, rev_status,
             f"Reversal dari {entry.entry_no}" + (" (fast-track approved)" if fast_track else ""))
        self.db.commit()

        return {
            "original_entry_id": entry_id,
            "reversal_entry_id": rev_entry_id,
            "reversal_entry_no": rev_entry_no,
            "reversal_status":   rev_status,
            "message": (
                "Jurnal reversal dibuat dan siap diposting."
                if fast_track else
                "Jurnal reversal dibuat dalam status draft. Submit untuk approval sebelum posting."
            ),
        }

    # ── 8. Cancel ─────────────────────────────────────────────────────────────

    def cancel(self, entry_id: str, cancelled_by: str, reason: str) -> dict:
        """Batalkan jurnal — hanya boleh saat draft atau rejected."""
        entry = self._get_entry_or_raise(entry_id)
        if entry.status not in ("draft", "rejected"):
            raise ValueError(f"Hanya jurnal draft/rejected yang bisa dibatalkan. Status: '{entry.status}'")

        self.db.execute(
            text("""
                UPDATE journal_entry SET status = 'cancelled', updated_at = NOW() WHERE id = :id
            """),
            {"id": entry_id}
        )
        _log(self.db, entry_id, "cancelled", cancelled_by, None, None,
             entry.status, "cancelled", reason)
        self.db.commit()
        return {"entry_id": entry_id, "status": "cancelled"}

    # ── 9. Validation ─────────────────────────────────────────────────────────

    def _validate(self, entry) -> ValidationResult:
        """
        Validasi otomatis sebelum submit (Tahap 2):
          1. Balance Check — debit = kredit
          2. Period Check  — tanggal di periode yang masih open
          3. Minimal 2 baris
        """
        vr = ValidationResult(is_valid=True)

        # Balance Check
        diff = abs(Decimal(str(entry.total_debit_idr)) - Decimal(str(entry.total_credit_idr)))
        if diff > BALANCE_TOLERANCE:
            vr.add(
                f"BALANCE ERROR: Total Debet Rp{float(entry.total_debit_idr):,.2f} ≠ "
                f"Total Kredit Rp{float(entry.total_credit_idr):,.2f} "
                f"(selisih Rp{float(diff):,.2f})"
            )

        # Period Check
        try:
            _check_period_open(
                self.db, str(entry.entity_id),
                entry.period_year, entry.period_month
            )
        except ValueError as e:
            vr.add(str(e))

        # Minimal lines check
        line_count = self.db.execute(
            text("SELECT COUNT(*) FROM journal_entry_line WHERE entry_id = :id"),
            {"id": str(entry.id)}
        ).scalar()
        if (line_count or 0) < 2:
            vr.add("Jurnal harus memiliki minimal 2 baris")

        return vr

    # ── Internal Helpers ──────────────────────────────────────────────────────

    def _get_entry_or_raise(self, entry_id: str):
        row = self.db.execute(
            text("SELECT * FROM journal_entry WHERE id = :id"),
            {"id": entry_id}
        ).fetchone()
        if not row:
            raise ValueError(f"Journal entry '{entry_id}' tidak ditemukan")
        return row

    # ── Accounting Period Management ──────────────────────────────────────────

    def ensure_period(self, entity_id: str, year: int, month: int) -> dict:
        """Buat periode jika belum ada, default open."""
        row = self.db.execute(
            text("SELECT * FROM accounting_period WHERE entity_id = :eid AND period_year = :yr AND period_month = :mo"),
            {"eid": entity_id, "yr": year, "mo": month}
        ).fetchone()
        if row:
            return dict(row._mapping)

        self.db.execute(
            text("""
                INSERT INTO accounting_period (id, entity_id, period_year, period_month, status)
                VALUES (uuid_generate_v4(), :eid, :yr, :mo, 'open')
                ON CONFLICT (entity_id, period_year, period_month) DO NOTHING
            """),
            {"eid": entity_id, "yr": year, "mo": month}
        )
        self.db.commit()
        row = self.db.execute(
            text("SELECT * FROM accounting_period WHERE entity_id = :eid AND period_year = :yr AND period_month = :mo"),
            {"eid": entity_id, "yr": year, "mo": month}
        ).fetchone()
        return dict(row._mapping)

    def close_period(self, entity_id: str, year: int, month: int, closed_by: str) -> dict:
        """Tutup periode — tidak ada jurnal baru setelah ini kecuali dibuka kembali."""
        self.ensure_period(entity_id, year, month)
        row = self.db.execute(
            text("SELECT status FROM accounting_period WHERE entity_id = :eid AND period_year = :yr AND period_month = :mo"),
            {"eid": entity_id, "yr": year, "mo": month}
        ).fetchone()
        if row and row.status == "locked":
            raise ValueError("Periode sudah dalam status locked — tidak bisa diubah")

        self.db.execute(
            text("""
                UPDATE accounting_period SET
                    status = 'closed', closed_by = :by, closed_at = NOW(), updated_at = NOW()
                WHERE entity_id = :eid AND period_year = :yr AND period_month = :mo
            """),
            {"eid": entity_id, "yr": year, "mo": month, "by": closed_by}
        )
        self.db.commit()
        return {"status": "closed", "period": f"{year}-{month:02d}"}

    def lock_period(self, entity_id: str, year: int, month: int, locked_by: str) -> dict:
        """Lock permanen — dipakai setelah filing pajak / audit selesai."""
        self.ensure_period(entity_id, year, month)
        self.db.execute(
            text("""
                UPDATE accounting_period SET
                    status = 'locked', closed_by = :by, closed_at = NOW(), updated_at = NOW()
                WHERE entity_id = :eid AND period_year = :yr AND period_month = :mo
            """),
            {"eid": entity_id, "yr": year, "mo": month, "by": locked_by}
        )
        self.db.commit()
        return {"status": "locked", "period": f"{year}-{month:02d}"}

    def reopen_period(self, entity_id: str, year: int, month: int, reopened_by: str) -> dict:
        """Buka kembali periode closed (tidak bisa untuk locked)."""
        row = self.db.execute(
            text("SELECT status FROM accounting_period WHERE entity_id = :eid AND period_year = :yr AND period_month = :mo"),
            {"eid": entity_id, "yr": year, "mo": month}
        ).fetchone()
        if not row:
            return self.ensure_period(entity_id, year, month)
        if row.status == "locked":
            raise ValueError("Periode locked tidak bisa dibuka kembali. Hubungi CFO / Auditor.")

        self.db.execute(
            text("""
                UPDATE accounting_period SET
                    status = 'open', opened_by = :by, opened_at = NOW(), updated_at = NOW()
                WHERE entity_id = :eid AND period_year = :yr AND period_month = :mo
            """),
            {"eid": entity_id, "yr": year, "mo": month, "by": reopened_by}
        )
        self.db.commit()
        return {"status": "open", "period": f"{year}-{month:02d}"}


# ── Module-level Helpers ───────────────────────────────────────────────────────

def _gen_entry_no(db) -> str:
    now    = datetime.now()
    prefix = f"JE/{now.year}/{now.month:02d}"
    count  = db.execute(
        text("SELECT COUNT(*) FROM journal_entry WHERE entry_no LIKE :p"),
        {"p": f"{prefix}/%"}
    ).scalar()
    return f"{prefix}/{(count or 0) + 1:04d}"


def _check_period_open(db, entity_id: str, year: int, month: int):
    """Raise ValueError jika periode sudah closed atau locked."""
    row = db.execute(
        text("""
            SELECT status FROM accounting_period
            WHERE entity_id = :eid AND period_year = :yr AND period_month = :mo
        """),
        {"eid": entity_id, "yr": year, "mo": month}
    ).fetchone()

    if not row:
        # Belum ada record → auto-create sebagai 'open'
        db.execute(
            text("""
                INSERT INTO accounting_period (id, entity_id, period_year, period_month, status)
                VALUES (uuid_generate_v4(), :eid, :yr, :mo, 'open')
                ON CONFLICT (entity_id, period_year, period_month) DO NOTHING
            """),
            {"eid": entity_id, "yr": year, "mo": month}
        )
        db.commit()
        return  # Periode baru = open

    if row.status == "closed":
        raise ValueError(
            f"Periode {year}-{month:02d} sudah DITUTUP (closed). "
            "Minta Finance Manager untuk membuka kembali periode tersebut."
        )
    if row.status == "locked":
        raise ValueError(
            f"Periode {year}-{month:02d} sudah DIKUNCI (locked) — permanen. "
            "Tidak bisa membuat atau merevisi jurnal untuk periode ini."
        )


def _get_required_role(db, entity_id: str, journal_type: str, total_amount: float) -> str:
    """
    Tentukan required role dari approval matrix.
    Prioritas: (1) rule berdasarkan journal_type → (2) rule berdasarkan nominal.
    Default jika tidak ada rule: 'finance'.
    """
    # Cek rule spesifik tipe
    type_rule = db.execute(
        text("""
            SELECT required_role FROM journal_approval_matrix
            WHERE entity_id  = :eid
              AND journal_type = :jtype
              AND is_active   = TRUE
            ORDER BY level ASC
            LIMIT 1
        """),
        {"eid": entity_id, "jtype": journal_type}
    ).fetchone()
    if type_rule:
        return type_rule.required_role

    # Cek rule nominal
    amount_rule = db.execute(
        text("""
            SELECT required_role FROM journal_approval_matrix
            WHERE entity_id   = :eid
              AND journal_type IS NULL
              AND is_active    = TRUE
              AND min_amount  <= :amt
              AND (max_amount IS NULL OR max_amount >= :amt)
            ORDER BY level DESC
            LIMIT 1
        """),
        {"eid": entity_id, "amt": total_amount}
    ).fetchone()
    return amount_rule.required_role if amount_rule else "finance"


def _check_reviewer_role(reviewer_role: str, required_role: str):
    """Pastikan reviewer memiliki role yang cukup."""
    role_rank = {"viewer": 0, "finance": 1, "admin": 2}
    if role_rank.get(reviewer_role, 0) < role_rank.get(required_role, 1):
        raise ValueError(
            f"Aksi ini membutuhkan role '{required_role}'. "
            f"Role Anda: '{reviewer_role}'."
        )


def _log(
    db, entry_id: str, action: str, actor: str,
    actor_role: Optional[str], required_role: Optional[str],
    from_status: Optional[str], to_status: Optional[str],
    notes: Optional[str],
):
    db.execute(
        text("""
            INSERT INTO journal_approval_log (
                id, entry_id, action, actor, actor_role, required_role,
                from_status, to_status, notes
            ) VALUES (
                uuid_generate_v4(), :eid, :action, :actor, :arole, :rrole,
                :from_s, :to_s, :notes
            )
        """),
        {
            "eid": entry_id, "action": action, "actor": actor,
            "arole": actor_role, "rrole": required_role,
            "from_s": from_status, "to_s": to_status, "notes": notes,
        }
    )
