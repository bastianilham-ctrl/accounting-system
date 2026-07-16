"""
Contract Engine — Contract Tracker terintegrasi AR

Logika A: Daily cron — update status invoice OVERDUE setelah lewat TOP
Logika B: Validation gate — invoice hanya bisa dibuat jika:
          (1) contract_status = ACTIVE
          (2) milestone.work_status = COMPLETED (BAST sudah terbit)
          (3) milestone.billing_status = READY_TO_BILL (belum diinvoice)
Logika C: Dashboard query — total_invoiced / outstanding / collected / overdue per kontrak
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session
from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _gen_invoice_no(db: Session, entity_id: str) -> str:
    today = date.today()
    year, month = today.year, today.month
    row = db.execute(
        text(
            "SELECT COUNT(*) AS cnt FROM ar_invoice "
            "WHERE entity_id = :eid "
            "  AND invoice_no LIKE :p"
        ),
        {"eid": entity_id, "p": f"INV/{year}/{month:02d}/%"},
    ).fetchone()
    seq = (row.cnt if row else 0) + 1
    return f"INV/{year}/{month:02d}/{seq:04d}"


def _gen_amendment_no(db: Session, contract_id: str) -> str:
    row = db.execute(
        text(
            "SELECT COUNT(*) AS cnt FROM contract_amendment "
            "WHERE contract_id = :cid"
        ),
        {"cid": contract_id},
    ).fetchone()
    seq = (row.cnt if row else 0) + 1
    today = date.today()
    return f"ADD/{today.year}/{seq:03d}"


# ─────────────────────────────────────────────────────────────────────────────
# ContractEngine
# ─────────────────────────────────────────────────────────────────────────────

class ContractEngine:

    # ─── Logika A: Daily Aging Cron ───────────────────────────────────────────

    @staticmethod
    def run_invoice_aging(db: Session) -> dict:
        """
        Logika A — dijalankan harian oleh scheduler (jam 00:00).

        Algoritma:
          1. Ambil semua invoice payment_status IN ('SENT', 'OVERDUE')
          2. Ambil TOP dari project_contract induknya
          3. Hitung calculated_due_date = date_issued + TOP hari
          4. Jika today > calculated_due_date → set payment_status = 'OVERDUE'
          5. Sinkronkan ke ar_invoice.status (lowercase) untuk konsistensi

        Return: jumlah invoice yang diubah ke OVERDUE hari ini.
        """
        today = date.today()

        active_invoices = db.execute(
            text(
                "SELECT ai.id, ai.invoice_date, ai.contract_id, ai.payment_status "
                "FROM ar_invoice ai "
                "WHERE ai.payment_status IN ('SENT', 'OVERDUE') "
                "  AND ai.contract_id IS NOT NULL"
            )
        ).fetchall()

        updated_overdue = 0
        updated_due_date = 0

        for inv in active_invoices:
            if not inv.contract_id:
                continue

            contract = db.execute(
                text(
                    "SELECT term_of_payment_days FROM project_contract "
                    "WHERE id = :cid"
                ),
                {"cid": str(inv.contract_id)},
            ).fetchone()

            if not contract:
                continue

            top_days = int(contract.term_of_payment_days or 30)
            calculated_due = inv.invoice_date + timedelta(days=top_days)

            if today > calculated_due and inv.payment_status != 'OVERDUE':
                # Lewat jatuh tempo — ubah ke OVERDUE
                db.execute(
                    text(
                        "UPDATE ar_invoice "
                        "SET due_date = :dd, payment_status = 'OVERDUE', status = 'overdue' "
                        "WHERE id = :iid"
                    ),
                    {"dd": calculated_due, "iid": str(inv.id)},
                )
                updated_overdue += 1
            else:
                # Sinkronkan due_date saja
                db.execute(
                    text("UPDATE ar_invoice SET due_date = :dd WHERE id = :iid"),
                    {"dd": calculated_due, "iid": str(inv.id)},
                )
                updated_due_date += 1

        if updated_overdue > 0 or updated_due_date > 0:
            db.commit()

        logger.info(
            f"Contract aging: {updated_overdue} invoice → OVERDUE, "
            f"{updated_due_date} due_date synced"
        )
        return {
            "run_date": str(today),
            "invoices_set_overdue": updated_overdue,
            "due_dates_synced": updated_due_date,
        }

    # ─── Logika B: Validation Gate ────────────────────────────────────────────

    @staticmethod
    def validate_invoice_trigger(db: Session, milestone_id: str) -> None:
        """
        Logika B — gerbang pengunci finansial.
        Melempar ValueError jika kondisi tidak terpenuhi.

        Gate 1: milestone.work_status == 'COMPLETED' (BAST sudah terbit)
        Gate 2: contract.contract_status == 'ACTIVE' (kontrak sudah ditandatangani)
        Gate 3: milestone.billing_status == 'READY_TO_BILL' (belum diinvoice)
        """
        milestone = db.execute(
            text(
                "SELECT cm.*, c.contract_status, c.contract_number "
                "FROM contract_milestone cm "
                "JOIN project_contract c ON c.id = cm.contract_id "
                "WHERE cm.id = :mid"
            ),
            {"mid": milestone_id},
        ).fetchone()

        if not milestone:
            raise ValueError(f"Milestone {milestone_id} tidak ditemukan")

        # Gate 1: pekerjaan harus selesai (BAST)
        if milestone.work_status != 'COMPLETED':
            raise ValueError(
                f"Gate 1 GAGAL: Invoice tidak dapat dibuat — pekerjaan milestone "
                f"'{milestone.milestone_name}' belum berstatus COMPLETED "
                f"(BAST belum diterbitkan). Status saat ini: {milestone.work_status}."
            )

        # Gate 2: kontrak harus ACTIVE
        if milestone.contract_status != 'ACTIVE':
            raise ValueError(
                f"Gate 2 GAGAL: Kontrak '{milestone.contract_number}' "
                f"belum ditandatangani resmi. "
                f"Status: {milestone.contract_status}. "
                f"Proses billing diblokir hingga kontrak berstatus ACTIVE."
            )

        # Gate 3: belum pernah diinvoice
        if milestone.billing_status == 'INVOICED':
            raise ValueError(
                f"Gate 3 GAGAL: Milestone '{milestone.milestone_name}' "
                f"sudah memiliki invoice (billing_status: INVOICED). "
                f"Tidak boleh membuat invoice duplikat."
            )
        if milestone.billing_status == 'PAID':
            raise ValueError(
                f"Gate 3 GAGAL: Milestone '{milestone.milestone_name}' sudah lunas (PAID)."
            )
        if milestone.billing_status not in ('READY_TO_BILL', 'UNINVOICED'):
            raise ValueError(
                f"Gate 3 GAGAL: billing_status '{milestone.billing_status}' tidak valid untuk pembuatan invoice."
            )

    # ─── Create Invoice from Milestone ───────────────────────────────────────

    @staticmethod
    def create_invoice_from_milestone(
        db: Session,
        milestone_id: str,
        invoice_date: date,
        created_by: str,
        notes: Optional[str] = None,
        tax_rate: float = 0.11,   # PPN 11%
    ) -> dict:
        """
        Buat draft AR invoice dari satu milestone kontrak.

        Proses:
        1. validate_invoice_trigger (Logika B)
        2. Hitung tax amount (PPN)
        3. due_date = invoice_date + term_of_payment_days
        4. Generate invoice_no
        5. INSERT ar_invoice dengan contract_id + milestone_id
        6. UPDATE contract_milestone SET billing_status = 'INVOICED'
        7. Return invoice detail
        """
        ContractEngine.validate_invoice_trigger(db, milestone_id)

        milestone = db.execute(
            text(
                "SELECT cm.*, "
                "       c.id AS contract_id, c.contract_number, c.term_of_payment_days, "
                "       c.entity_id, c.client_id, c.currency, c.exchange_rate, "
                "       p.id AS project_id, p.project_name, "
                "       v.vendor_name AS client_name "
                "FROM contract_milestone cm "
                "JOIN project_contract c ON c.id = cm.contract_id "
                "JOIN project p          ON p.id = c.project_id "
                "LEFT JOIN vendor v      ON v.id = c.client_id "
                "WHERE cm.id = :mid"
            ),
            {"mid": milestone_id},
        ).fetchone()

        entity_id   = str(milestone.entity_id)
        invoice_no  = _gen_invoice_no(db, entity_id)
        top_days    = int(milestone.term_of_payment_days or 30)
        due_date    = invoice_date + timedelta(days=top_days)

        # Jumlah tagih = amount_target dikurangi retensi yang ditahan
        taxable_amount  = Decimal(str(milestone.amount_target)) - Decimal(str(milestone.retention_held or 0))
        tax_amount      = (taxable_amount * Decimal(str(tax_rate))).quantize(Decimal("1"))
        total_amount    = taxable_amount + tax_amount

        invoice_id = str(uuid.uuid4())
        db.execute(
            text(
                "INSERT INTO ar_invoice "
                "(id, entity_id, project_id, contract_id, milestone_id, "
                " invoice_no, customer_name, customer_id, "
                " invoice_date, due_date, "
                " subtotal, tax_amount, total_amount, paid_amount, "
                " currency, description, "
                " status, payment_status, "
                " created_by) "
                "VALUES (:id, :eid, :pid, :cid, :mid, "
                "        :ino, :cname, :cust_id, "
                "        :idate, :ddate, "
                "        :sub, :tax, :total, 0, "
                "        :cur, :desc, "
                "        'draft', 'DRAFT', "
                "        :cby)"
            ),
            {
                "id": invoice_id, "eid": entity_id,
                "pid": str(milestone.project_id),
                "cid": str(milestone.contract_id),
                "mid": milestone_id,
                "ino": invoice_no,
                "cname": milestone.client_name or "",
                "cust_id": str(milestone.client_id) if milestone.client_id else None,
                "idate": invoice_date, "ddate": due_date,
                "sub": float(taxable_amount), "tax": float(tax_amount),
                "total": float(total_amount),
                "cur": milestone.currency,
                "desc": f"Tagihan {milestone.milestone_name} - {milestone.client_name or 'Klien'} - {str(milestone.contract_number)}",
                "cby": created_by,
            },
        )

        # Update milestone status
        db.execute(
            text(
                "UPDATE contract_milestone "
                "SET billing_status = 'INVOICED', invoice_id = :iid, invoiced_at = NOW(), "
                "    due_date = :dd, updated_at = NOW() "
                "WHERE id = :mid"
            ),
            {"iid": invoice_id, "dd": due_date, "mid": milestone_id},
        )

        db.commit()
        return {
            "invoice_id":    invoice_id,
            "invoice_no":    invoice_no,
            "invoice_date":  str(invoice_date),
            "due_date":      str(due_date),
            "subtotal":      float(taxable_amount),
            "tax_amount":    float(tax_amount),
            "total_amount":  float(total_amount),
            "currency":      milestone.currency,
            "payment_status": "DRAFT",
            "contract_number": str(milestone.contract_number),
            "milestone_name":  milestone.milestone_name,
        }

    # ─── BAST Completion (Logika Operasional PM) ──────────────────────────────

    @staticmethod
    def complete_milestone_bast(
        db: Session,
        milestone_id: str,
        bast_number: str,
        bast_date: date,
        bast_signed_by: str,
        completed_by: str,
    ) -> dict:
        """
        Tim lapangan melaporkan pekerjaan selesai dan BAST diterbitkan.
        Efek: work_status → COMPLETED, billing_status → READY_TO_BILL.
        Finance kemudian bisa membuat invoice menggunakan create_invoice_from_milestone().
        """
        milestone = db.execute(
            text("SELECT * FROM contract_milestone WHERE id = :mid"),
            {"mid": milestone_id},
        ).fetchone()
        if not milestone:
            raise ValueError(f"Milestone {milestone_id} tidak ditemukan")

        if milestone.work_status == 'COMPLETED':
            raise ValueError("Milestone sudah berstatus COMPLETED")
        if milestone.billing_status == 'INVOICED':
            raise ValueError("Milestone sudah diinvoice, tidak bisa mengubah work status")

        db.execute(
            text(
                "UPDATE contract_milestone "
                "SET work_status = 'COMPLETED', "
                "    billing_status = 'READY_TO_BILL', "
                "    bast_number = :bn, bast_date = :bd, bast_signed_by = :bs, "
                "    updated_at = NOW() "
                "WHERE id = :mid"
            ),
            {
                "bn": bast_number, "bd": bast_date,
                "bs": bast_signed_by, "mid": milestone_id,
            },
        )

        # Sinkronkan PM milestone jika ada link
        if milestone.pm_milestone_id:
            db.execute(
                text(
                    "UPDATE project_milestone "
                    "SET status = 'achieved', actual_date = :ad "
                    "WHERE id = :pmid"
                ),
                {"ad": bast_date, "pmid": str(milestone.pm_milestone_id)},
            )

        db.commit()
        return {
            "milestone_id":   milestone_id,
            "work_status":    "COMPLETED",
            "billing_status": "READY_TO_BILL",
            "bast_number":    bast_number,
            "bast_date":      str(bast_date),
            "message":        "Milestone selesai. Finance dapat membuat invoice.",
        }

    # ─── Record Payment ───────────────────────────────────────────────────────

    @staticmethod
    def record_payment(
        db: Session,
        invoice_id: str,
        payment_date: date,
        amount_received: float,
        bank_reference: Optional[str] = None,
        payment_method: str = "bank_transfer",
        recorded_by: str = "system",
        notes: Optional[str] = None,
    ) -> dict:
        """
        Catat pembayaran aktual dari klien untuk satu invoice.
        Update paid_amount + payment_status di ar_invoice.
        Jika lunas: update milestone billing_status → PAID.
        """
        invoice = db.execute(
            text(
                "SELECT ai.*, c.id AS cid "
                "FROM ar_invoice ai "
                "LEFT JOIN project_contract c ON c.id = ai.contract_id "
                "WHERE ai.id = :iid"
            ),
            {"iid": invoice_id},
        ).fetchone()
        if not invoice:
            raise ValueError(f"Invoice {invoice_id} tidak ditemukan")
        if invoice.payment_status == 'PAID':
            raise ValueError("Invoice sudah lunas")
        if invoice.payment_status == 'CANCELLED':
            raise ValueError("Invoice sudah di-cancel")

        new_paid = Decimal(str(invoice.paid_amount or 0)) + Decimal(str(amount_received))
        total    = Decimal(str(invoice.total_amount))
        is_paid  = new_paid >= total

        new_status = 'PAID' if is_paid else 'SENT'

        db.execute(
            text(
                "UPDATE ar_invoice "
                "SET paid_amount = :pa, payment_status = :ps, status = :st "
                "WHERE id = :iid"
            ),
            {
                "pa": float(new_paid),
                "ps": new_status,
                "st": "paid" if is_paid else "partial",
                "iid": invoice_id,
            },
        )

        # Cari contract_id dan milestone_id dari invoice
        contract_id = str(invoice.contract_id) if invoice.contract_id else None
        milestone_id_inv = str(invoice.milestone_id) if invoice.milestone_id else None

        payment_id = str(uuid.uuid4())
        db.execute(
            text(
                "INSERT INTO contract_payment "
                "(id, contract_id, milestone_id, invoice_id, entity_id, "
                " payment_date, amount_received, payment_method, bank_reference, notes, recorded_by) "
                "VALUES (:id, :cid, :mid, :iid, :eid, "
                "        :pd, :ar, :pm, :br, :notes, :rby)"
            ),
            {
                "id": str(uuid.uuid4()), "cid": contract_id,
                "mid": milestone_id_inv, "iid": invoice_id,
                "eid": str(invoice.entity_id),
                "pd": payment_date, "ar": amount_received,
                "pm": payment_method, "br": bank_reference,
                "notes": notes, "rby": recorded_by,
            },
        )

        if is_paid and milestone_id_inv:
            db.execute(
                text(
                    "UPDATE contract_milestone "
                    "SET billing_status = 'PAID', updated_at = NOW() "
                    "WHERE id = :mid"
                ),
                {"mid": milestone_id_inv},
            )

        db.commit()
        return {
            "payment_id":     payment_id,
            "invoice_id":     invoice_id,
            "amount_received": amount_received,
            "new_paid_total":  float(new_paid),
            "payment_status":  new_status,
            "is_fully_paid":   is_paid,
        }

    # ─── Contract Amendment ───────────────────────────────────────────────────

    @staticmethod
    def create_amendment(
        db: Session,
        contract_id: str,
        amendment_type: str,
        amendment_title: str,
        reason: str,
        new_value: Optional[float] = None,
        new_end_date: Optional[date] = None,
        impact_description: Optional[str] = None,
        created_by: str = "system",
    ) -> dict:
        """
        Buat draft amendment. Efek pada kontrak diterapkan saat amendment di-sign.
        """
        contract = db.execute(
            text("SELECT * FROM project_contract WHERE id = :cid"),
            {"cid": contract_id},
        ).fetchone()
        if not contract:
            raise ValueError(f"Contract {contract_id} tidak ditemukan")
        if contract.contract_status not in ('ACTIVE', 'AMENDED'):
            raise ValueError(f"Hanya kontrak ACTIVE/AMENDED yang bisa diamendemen (status: {contract.contract_status})")

        amendment_no = _gen_amendment_no(db, contract_id)
        amend_id = str(uuid.uuid4())

        db.execute(
            text(
                "INSERT INTO contract_amendment "
                "(id, contract_id, amendment_no, amendment_title, amendment_type, "
                " original_value, new_value, original_end_date, new_end_date, "
                " reason, impact_description, created_by) "
                "VALUES (:id, :cid, :ano, :at, :atype, "
                "        :ov, :nv, :oed, :ned, "
                "        :rsn, :imp, :cby)"
            ),
            {
                "id": amend_id, "cid": contract_id,
                "ano": amendment_no, "at": amendment_title, "atype": amendment_type,
                "ov": float(contract.total_value), "nv": new_value,
                "oed": contract.end_date, "ned": new_end_date,
                "rsn": reason, "imp": impact_description, "cby": created_by,
            },
        )
        db.commit()
        return {
            "amendment_id": amend_id,
            "amendment_no": amendment_no,
            "status": "DRAFT",
        }

    @staticmethod
    def sign_amendment(
        db: Session,
        amendment_id: str,
        signing_date: date,
        signed_by: str,
    ) -> dict:
        """
        Tanda tangan amendment → terapkan perubahan ke contract master.
        Jika nilai berubah: recalculate amount_target semua milestone yang belum diinvoice.
        """
        amendment = db.execute(
            text("SELECT * FROM contract_amendment WHERE id = :aid"),
            {"aid": amendment_id},
        ).fetchone()
        if not amendment:
            raise ValueError(f"Amendment {amendment_id} tidak ditemukan")
        if amendment.status != 'DRAFT':
            raise ValueError(f"Amendment sudah berstatus {amendment.status}")

        contract_id = str(amendment.contract_id)
        updates: list[str] = ["contract_status = 'AMENDED'", "updated_at = NOW()"]
        params: dict[str, Any] = {"cid": contract_id}

        if amendment.new_value is not None:
            updates.append("total_value = :nv")
            params["nv"] = float(amendment.new_value)

        if amendment.new_end_date is not None:
            updates.append("end_date = :ned")
            params["ned"] = amendment.new_end_date

        db.execute(text(f"UPDATE project_contract SET {', '.join(updates)} WHERE id = :cid"), params)

        # Recalculate milestone amounts jika nilai berubah
        if amendment.new_value and amendment.original_value and float(amendment.new_value) != float(amendment.original_value):
            new_val = Decimal(str(amendment.new_value))
            uninvoiced_milestones = db.execute(
                text(
                    "SELECT id, percentage, retention_held "
                    "FROM contract_milestone "
                    "WHERE contract_id = :cid "
                    "  AND billing_status IN ('UNINVOICED','READY_TO_BILL')"
                ),
                {"cid": contract_id},
            ).fetchall()

            for ms in uninvoiced_milestones:
                new_target = (new_val * Decimal(str(ms.percentage)) / 100).quantize(Decimal("1"))
                db.execute(
                    text(
                        "UPDATE contract_milestone "
                        "SET amount_target = :nt, updated_at = NOW() "
                        "WHERE id = :mid"
                    ),
                    {"nt": float(new_target), "mid": str(ms.id)},
                )

        # Mark amendment as SIGNED
        db.execute(
            text(
                "UPDATE contract_amendment "
                "SET status = 'SIGNED', signing_date = :sd, signed_by = :sb "
                "WHERE id = :aid"
            ),
            {"sd": signing_date, "sb": signed_by, "aid": amendment_id},
        )
        db.commit()
        return {
            "amendment_id": amendment_id,
            "status": "SIGNED",
            "contract_id": contract_id,
            "message": "Amendment diterapkan ke kontrak.",
        }

    # ─── Logika C: Contract Dashboard ────────────────────────────────────────

    @staticmethod
    def get_contract_dashboard(db: Session, project_id: str) -> list[dict]:
        """Logika C — ambil semua kontrak + financials per proyek."""
        rows = db.execute(
            text(
                "SELECT * FROM vw_contract_dashboard "
                "WHERE project_id = :pid "
                "ORDER BY contract_number"
            ),
            {"pid": project_id},
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def get_contract_detail(db: Session, contract_id: str) -> dict:
        """Detail satu kontrak + milestones + invoices."""
        contract = db.execute(
            text("SELECT * FROM vw_contract_dashboard WHERE contract_id = :cid"),
            {"cid": contract_id},
        ).fetchone()
        if not contract:
            raise ValueError(f"Contract {contract_id} tidak ditemukan")

        milestones = db.execute(
            text(
                "SELECT * FROM vw_milestone_billing "
                "WHERE contract_id = :cid ORDER BY sequence"
            ),
            {"cid": contract_id},
        ).fetchall()

        amendments = db.execute(
            text(
                "SELECT * FROM contract_amendment "
                "WHERE contract_id = :cid ORDER BY created_at DESC"
            ),
            {"cid": contract_id},
        ).fetchall()

        documents = db.execute(
            text(
                "SELECT * FROM contract_document "
                "WHERE contract_id = :cid ORDER BY uploaded_at DESC"
            ),
            {"cid": contract_id},
        ).fetchall()

        payments = db.execute(
            text(
                "SELECT cp.*, ai.invoice_no "
                "FROM contract_payment cp "
                "LEFT JOIN ar_invoice ai ON ai.id = cp.invoice_id "
                "WHERE cp.contract_id = :cid ORDER BY cp.payment_date DESC"
            ),
            {"cid": contract_id},
        ).fetchall()

        return {
            **dict(contract._mapping),
            "milestones":  [dict(r._mapping) for r in milestones],
            "amendments":  [dict(r._mapping) for r in amendments],
            "documents":   [dict(r._mapping) for r in documents],
            "payments":    [dict(r._mapping) for r in payments],
        }

    # ─── AR Aging / Outstanding ───────────────────────────────────────────────

    @staticmethod
    def get_ar_aging(
        db: Session,
        entity_id: str,
        client_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> dict:
        """
        AR aging buckets: CURRENT / 1-30 / 31-60 / 61-90 / >90 hari.
        """
        q = "SELECT * FROM vw_contract_ar_aging WHERE entity_id = :eid"
        params: dict[str, Any] = {"eid": entity_id}
        if client_id:
            q += " AND client_id = :cid"; params["cid"] = client_id
        if project_id:
            q += " AND project_id = :pid"; params["pid"] = project_id

        rows = db.execute(text(q + " ORDER BY days_overdue DESC"), params).fetchall()
        items = [dict(r._mapping) for r in rows]

        # Summarize by bucket
        buckets: dict[str, dict] = {}
        for row in items:
            bucket = row.get("aging_bucket", "CURRENT")
            if bucket not in buckets:
                buckets[bucket] = {"count": 0, "total_outstanding": 0.0}
            buckets[bucket]["count"] += 1
            buckets[bucket]["total_outstanding"] += float(row.get("outstanding_amount") or 0)

        total_outstanding = sum(b["total_outstanding"] for b in buckets.values())
        total_overdue     = sum(
            b["total_outstanding"]
            for k, b in buckets.items() if k != "CURRENT" and k != "PAID"
        )

        return {
            "entity_id":        entity_id,
            "total_outstanding": round(total_outstanding, 2),
            "total_overdue":     round(total_overdue, 2),
            "by_bucket":         buckets,
            "invoices":          items,
        }

    @staticmethod
    def get_client_outstanding(db: Session, entity_id: str) -> list[dict]:
        rows = db.execute(
            text(
                "SELECT * FROM vw_client_outstanding_summary "
                "WHERE entity_id = :eid "
                "ORDER BY total_outstanding_idr DESC"
            ),
            {"eid": entity_id},
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def get_expiry_alerts(db: Session, entity_id: str) -> list[dict]:
        rows = db.execute(
            text(
                "SELECT * FROM vw_contract_expiry_alert "
                "WHERE entity_id = :eid "
                "ORDER BY days_to_expiry ASC"
            ),
            {"eid": entity_id},
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    # ─── Milestone Percentage Validation ─────────────────────────────────────

    @staticmethod
    def validate_milestone_total(db: Session, contract_id: str) -> dict:
        """
        Validasi bahwa total percentage semua milestone = 100%.
        Dipanggil sebelum mengaktifkan kontrak.
        """
        row = db.execute(
            text(
                "SELECT COALESCE(SUM(percentage), 0) AS total_pct, "
                "       COUNT(*) AS count "
                "FROM contract_milestone WHERE contract_id = :cid"
            ),
            {"cid": contract_id},
        ).fetchone()

        total_pct = float(row.total_pct or 0)
        is_valid  = abs(total_pct - 100.0) < 0.01  # toleransi pembulatan

        return {
            "contract_id":    contract_id,
            "total_percentage": total_pct,
            "milestone_count":  int(row.count or 0),
            "is_valid":         is_valid,
            "message": "OK" if is_valid else f"Total persentase {total_pct}% ≠ 100%. Periksa milestone.",
        }
