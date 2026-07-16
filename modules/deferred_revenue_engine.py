# modules/deferred_revenue_engine.py
# Deferred Revenue Engine — PSA gap #3 (BRD user 2026-06-28).
#
# Model: fixed-fee milestone billing dengan pengakuan revenue proporsional ke progress.
# Anchor ke project_milestone (PM module) + project.contract_value (Costing module) —
# BUKAN ke project_contract/contract_milestone (Contract module, tidak pernah dideploy).
#
# Akun GL:
#   2-1-010  Uang Muka Penjualan / Pelanggan   (liability — "Deferred Revenue")
#   4-1-001  Pendapatan Jasa / Pendapatan      (revenue, sama dengan billing automation)
#
# Alur:
#   1. record_payment()    : customer bayar termin -> Dr Bank, Cr Deferred Revenue (liability)
#   2. recognize_revenue() : progress_pct milestone naik -> Dr Deferred Revenue, Cr Revenue,
#                            sebesar (billing_amount x progress_pct/100) - sudah_direkognisi,
#                            DIBATASI tidak boleh melebihi saldo deferred yang tersedia
#                            (uang yang benar-benar sudah diterima) — kalau progress sudah
#                            lebih tinggi dari pembayaran yang diterima, sisanya TIDAK
#                            direkognisi sekarang (akan kebagian setelah pembayaran berikutnya
#                            masuk), dilaporkan transparan via "shortfall" di response,
#                            BUKAN diam-diam dipotong atau di-error.

from decimal import Decimal
from datetime import date
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import text

from modules.journal_engine import JournalEngine, JournalEntry, JournalLine

DEFERRED_REVENUE_ACCOUNT = "2-1-010"   # Uang Muka Penjualan / Pelanggan
REVENUE_ACCOUNT          = "4-1-001"   # Pendapatan Jasa


class DeferredRevenueEngine:

    def __init__(self, db: Session):
        self.db = db

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _get_milestone_and_project(self, milestone_id: str):
        row = self.db.execute(
            text(
                "SELECT pm.id AS milestone_id, pm.milestone_name, pm.billing_amount, "
                "       pm.progress_pct, pm.project_id, "
                "       p.entity_id, p.cost_center, p.project_code, p.contract_value "
                "FROM project_milestone pm "
                "JOIN project p ON p.id = pm.project_id "
                "WHERE pm.id = :mid"
            ),
            {"mid": milestone_id},
        ).fetchone()
        if not row:
            raise ValueError(f"Milestone {milestone_id} tidak ditemukan")
        return row

    def _ledger_totals(self, milestone_id: str) -> dict:
        row = self.db.execute(
            text(
                "SELECT "
                "  COALESCE(SUM(amount) FILTER (WHERE event_type = 'payment_received'), 0) AS total_paid, "
                "  COALESCE(SUM(amount) FILTER (WHERE event_type = 'revenue_recognized'), 0) AS total_recognized "
                "FROM deferred_revenue_ledger WHERE milestone_id = :mid"
            ),
            {"mid": milestone_id},
        ).fetchone()
        return {"total_paid": Decimal(str(row.total_paid)), "total_recognized": Decimal(str(row.total_recognized))}

    # ── Public API ───────────────────────────────────────────────────────────

    def record_payment(
        self,
        milestone_id: str,
        amount: Decimal,
        payment_date: date,
        created_by: str,
        bank_account_code: str = "1-1-001",
        notes: Optional[str] = None,
    ) -> dict:
        """
        Dr Bank, Cr Deferred Revenue (liability) — uang termin yang diterima dari klien
        untuk milestone fixed-fee ini. BELUM diakui sebagai revenue (IFRS15/PSAK72:
        revenue diakui saat performance obligation terpenuhi, bukan saat kas diterima).
        """
        ms = self._get_milestone_and_project(milestone_id)
        if ms.billing_amount <= 0:
            raise ValueError(
                f"Milestone '{ms.milestone_name}' belum punya billing_amount (porsi contract "
                f"value untuk termin ini) — set billing_amount dulu sebelum mencatat pembayaran"
            )

        totals = self._ledger_totals(milestone_id)
        new_total_paid = totals["total_paid"] + amount
        if new_total_paid > Decimal(str(ms.billing_amount)):
            raise ValueError(
                f"Total pembayaran ({new_total_paid:,.2f}) akan melebihi billing_amount milestone "
                f"({Decimal(str(ms.billing_amount)):,.2f}) — sudah diterima {totals['total_paid']:,.2f} sebelumnya"
            )

        engine = JournalEngine(self.db)
        entry = JournalEntry(
            entity_id=ms.entity_id,
            journal_type="AR",
            journal_date=payment_date,
            description=f"Penerimaan termin — {ms.milestone_name} ({ms.project_code})",
            reference_no=f"DRV-PAY/{milestone_id[:8]}",
            created_by=created_by,
            lines=[
                JournalLine(
                    account_code=bank_account_code,
                    description=f"Penerimaan termin {ms.milestone_name}",
                    debit_idr=amount,
                    cost_center=ms.cost_center,
                    project_code=ms.project_code,
                ),
                JournalLine(
                    account_code=DEFERRED_REVENUE_ACCOUNT,
                    description=f"Uang muka — {ms.milestone_name}",
                    credit_idr=amount,
                    cost_center=ms.cost_center,
                    project_code=ms.project_code,
                ),
            ],
        )
        result = engine.post_journal(entry)
        if not result["success"]:
            raise ValueError(result["error"])

        ledger_id = str(self.db.execute(
            text(
                "INSERT INTO deferred_revenue_ledger "
                "(project_id, milestone_id, event_type, amount, progress_pct_at_event, "
                " journal_id, notes, created_by) "
                "VALUES (:pid, :mid, 'payment_received', :amt, :pct, :jid, :notes, :by) "
                "RETURNING id"
            ),
            {
                "pid": ms.project_id, "mid": milestone_id, "amt": float(amount),
                "pct": ms.progress_pct, "jid": result["journal_id"], "notes": notes, "by": created_by,
            },
        ).scalar())
        self.db.commit()

        return {
            "status": "posted",
            "ledger_id": ledger_id,
            "journal_no": result["journal_no"],
            "journal_id": result["journal_id"],
            "amount": float(amount),
            "total_paid": float(new_total_paid),
            "billing_amount": float(ms.billing_amount),
        }

    def recognize_revenue(self, milestone_id: str, created_by: str, recognition_date: Optional[date] = None) -> dict:
        """
        Dr Deferred Revenue, Cr Revenue — release proporsional ke progress_pct milestone
        saat ini. Release = (billing_amount x progress_pct/100) - sudah_direkognisi,
        DIBATASI ke saldo deferred yang tersedia (total_paid - total_recognized) supaya
        liability tidak pernah negatif. Kalau progress > pembayaran yang diterima, sisanya
        dilaporkan sebagai `shortfall` (Contract Asset / unbilled situation) — TIDAK
        direkognisi sekarang, bukan error, bukan silently dropped.
        """
        ms = self._get_milestone_and_project(milestone_id)
        if recognition_date is None:
            recognition_date = date.today()

        totals = self._ledger_totals(milestone_id)
        billing_amount = Decimal(str(ms.billing_amount))
        recognizable_total = (billing_amount * Decimal(str(ms.progress_pct)) / 100).quantize(Decimal("0.01"))
        to_release = recognizable_total - totals["total_recognized"]

        if to_release <= 0:
            return {
                "status": "skipped",
                "reason": "Tidak ada kenaikan progress yang perlu direkognisi sejak rekognisi terakhir",
            }

        deferred_balance = totals["total_paid"] - totals["total_recognized"]
        release = min(to_release, deferred_balance)
        shortfall = to_release - release

        if release <= 0:
            return {
                "status": "skipped",
                "reason": (
                    "Progress sudah naik tapi belum ada saldo deferred revenue (pembayaran) "
                    "untuk direkognisi — catat payment dulu"
                ),
                "shortfall": float(shortfall),
            }

        engine = JournalEngine(self.db)
        entry = JournalEntry(
            entity_id=ms.entity_id,
            journal_type="GL",
            journal_date=recognition_date,
            description=f"Pengakuan revenue — {ms.milestone_name} ({ms.project_code}) @ {ms.progress_pct}%",
            reference_no=f"DRV-REC/{milestone_id[:8]}",
            created_by=created_by,
            lines=[
                JournalLine(
                    account_code=DEFERRED_REVENUE_ACCOUNT,
                    description=f"Release uang muka — {ms.milestone_name}",
                    debit_idr=release,
                    cost_center=ms.cost_center,
                    project_code=ms.project_code,
                ),
                JournalLine(
                    account_code=REVENUE_ACCOUNT,
                    description=f"Pendapatan diakui — {ms.milestone_name} @ {ms.progress_pct}%",
                    credit_idr=release,
                    cost_center=ms.cost_center,
                    project_code=ms.project_code,
                ),
            ],
        )
        result = engine.post_journal(entry)
        if not result["success"]:
            raise ValueError(result["error"])

        ledger_id = str(self.db.execute(
            text(
                "INSERT INTO deferred_revenue_ledger "
                "(project_id, milestone_id, event_type, amount, progress_pct_at_event, journal_id, created_by) "
                "VALUES (:pid, :mid, 'revenue_recognized', :amt, :pct, :jid, :by) "
                "RETURNING id"
            ),
            {
                "pid": ms.project_id, "mid": milestone_id, "amt": float(release),
                "pct": ms.progress_pct, "jid": result["journal_id"], "by": created_by,
            },
        ).scalar())
        self.db.commit()

        return {
            "status": "posted",
            "ledger_id": ledger_id,
            "journal_no": result["journal_no"],
            "journal_id": result["journal_id"],
            "released": float(release),
            "shortfall": float(shortfall),
            "total_recognized": float(totals["total_recognized"] + release),
            "billing_amount": float(billing_amount),
            "progress_pct": ms.progress_pct,
        }

    def get_summary(self, project_id: str) -> list[dict]:
        """Ringkasan per-milestone: billing_amount, total_paid, total_recognized, deferred_balance."""
        rows = self.db.execute(
            text(
                "SELECT pm.id AS milestone_id, pm.milestone_name, pm.billing_amount, pm.progress_pct, "
                "  COALESCE(d.total_paid, 0) AS total_paid, "
                "  COALESCE(d.total_recognized, 0) AS total_recognized "
                "FROM project_milestone pm "
                "LEFT JOIN ( "
                "  SELECT milestone_id, "
                "    SUM(amount) FILTER (WHERE event_type = 'payment_received') AS total_paid, "
                "    SUM(amount) FILTER (WHERE event_type = 'revenue_recognized') AS total_recognized "
                "  FROM deferred_revenue_ledger GROUP BY milestone_id "
                ") d ON d.milestone_id = pm.id "
                "WHERE pm.project_id = :pid AND pm.billing_amount > 0 "
                "ORDER BY pm.target_date"
            ),
            {"pid": project_id},
        ).fetchall()
        return [
            {
                "milestone_id":      str(r.milestone_id),
                "milestone_name":    r.milestone_name,
                "billing_amount":    float(r.billing_amount),
                "progress_pct":      r.progress_pct,
                "total_paid":        float(r.total_paid),
                "total_recognized":  float(r.total_recognized),
                "deferred_balance":  float(r.total_paid) - float(r.total_recognized),
            }
            for r in rows
        ]
