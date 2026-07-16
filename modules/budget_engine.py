# modules/budget_engine.py
# Budget Control Engine — pengendalian anggaran real-time
#
# Formula:
#   Available = Budget − Actual − Commitment
#
#   Actual     : Realisasi dari GL journal posted (debit_amount − credit_amount)
#   Commitment : Encumbrance dari PO approved yang belum ditagihkan
#
# Control modes (budget_period.control_mode):
#   hard → tolak transaksi jika Available < 0
#   soft → lolos dengan peringatan, kirim flag ke caller
#   off  → tidak dicek

from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass, field
from typing import Optional
from uuid import uuid4

from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger


# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class BudgetCheckResult:
    entity_id:    str
    cost_center:  str
    account_code: str
    year:         int
    month:        int

    # Angka
    budgeted:     Decimal
    actual:       Decimal
    committed:    Decimal
    available:    Decimal
    requested:    Decimal

    # Keputusan
    is_over:      bool
    over_by:      Decimal        # positif = melebihi budget
    control_mode: str            # hard | soft | off
    action:       str            # allow | warn | block
    message:      str

    def to_dict(self) -> dict:
        return {
            "entity_id":    self.entity_id,
            "cost_center":  self.cost_center,
            "account_code": self.account_code,
            "year":         self.year,
            "month":        self.month,
            "budget":       float(self.budgeted),
            "actual":       float(self.actual),
            "committed":    float(self.committed),
            "available":    float(self.available),
            "requested":    float(self.requested),
            "is_over":      self.is_over,
            "over_by":      float(self.over_by),
            "control_mode": self.control_mode,
            "action":       self.action,
            "message":      self.message,
        }


# ── BudgetEngine ───────────────────────────────────────────────────────────────

class BudgetEngine:
    """
    Engine pengendalian anggaran.
    Semua operasi tulis (commitment) menggunakan DB session yang di-commit manual.
    """

    def __init__(self, db: Session):
        self.db = db

    # ── Check availability ─────────────────────────────────────────────────────

    def check(
        self,
        entity_id:    str,
        cost_center:  str,
        account_code: str,
        year:         int,
        month:        int,
        requested:    Decimal,
        control_mode: Optional[str] = None,
    ) -> BudgetCheckResult:
        """
        Cek ketersediaan anggaran untuk satu transaksi.

        1. Ambil budget dari budget_line (periode released)
        2. Ambil actual dari GL (journal posted)
        3. Ambil commitment dari budget_commitment (active/partial)
        4. Hitung available dan putuskan action
        """
        # Ambil budget
        budgeted = self._get_budgeted(entity_id, cost_center, account_code, year, month)

        # Ambil actual
        actual = self._get_actual(entity_id, cost_center, account_code, year, month)

        # Ambil commitment
        committed = self._get_committed(entity_id, cost_center, account_code, year, month)

        available = budgeted - actual - committed
        is_over   = (available - requested) < Decimal("0")
        over_by   = max(Decimal("0"), requested - available)

        # Ambil control_mode dari budget_period jika tidak di-override
        if control_mode is None:
            control_mode = self._get_control_mode(entity_id, year) or "soft"

        # Tentukan action
        if not is_over or control_mode == "off":
            action  = "allow"
            message = f"Budget tersedia Rp {available:,.0f}"
        elif control_mode == "hard":
            action  = "block"
            message = (
                f"Budget HABIS: tersedia Rp {available:,.0f}, "
                f"diminta Rp {requested:,.0f}, kurang Rp {over_by:,.0f}. "
                f"Transaksi ditolak (hard control)."
            )
        else:  # soft
            action  = "warn"
            message = (
                f"PERINGATAN: budget melebihi plafon sebesar Rp {over_by:,.0f}. "
                f"Tersedia Rp {available:,.0f}, diminta Rp {requested:,.0f}. "
                f"Transaksi diloloskan dengan notifikasi (soft control)."
            )

        return BudgetCheckResult(
            entity_id    = entity_id,
            cost_center  = cost_center,
            account_code = account_code,
            year         = year,
            month        = month,
            budgeted     = budgeted,
            actual       = actual,
            committed    = committed,
            available    = available,
            requested    = requested,
            is_over      = is_over,
            over_by      = over_by,
            control_mode = control_mode,
            action       = action,
            message      = message,
        )

    def check_pr_items(
        self,
        entity_id:   str,
        cost_center: str,
        items:       list[dict],   # [{"account_code": ..., "total_amount": ...}]
        year:        int,
        month:       int,
    ) -> dict:
        """
        Budget check untuk semua item PR sekaligus.
        Mengembalikan: overall_action, item_results.
        """
        results  = []
        blocked  = 0
        warnings = 0

        for item in items:
            r = self.check(
                entity_id    = entity_id,
                cost_center  = cost_center,
                account_code = item.get("account_code", ""),
                year         = year,
                month        = month,
                requested    = Decimal(str(item.get("total_amount", 0))),
            )
            results.append(r.to_dict())
            if r.action == "block":
                blocked += 1
            elif r.action == "warn":
                warnings += 1

        overall = "block" if blocked > 0 else ("warn" if warnings > 0 else "allow")
        status  = "blocked" if blocked > 0 else ("warning" if warnings > 0 else "ok")

        return {
            "overall_action": overall,
            "budget_check_status": status,
            "blocked_count":  blocked,
            "warning_count":  warnings,
            "items":          results,
        }

    # ── Commitment management ─────────────────────────────────────────────────

    def create_po_commitment(
        self,
        po_id:      str,
        po_no:      str,
        entity_id:  str,
        items:      list[dict],   # po_item records
        year:       int,
        month:      int,
    ) -> str:
        """
        Buat encumbrance (commitment) saat PO di-approve.
        Mengurangi available budget sampai invoice masuk.
        Mengembalikan commitment_id.
        """
        # Aggregate amount per cost_center + account_code
        grouped: dict[tuple, Decimal] = {}
        for item in items:
            key = (
                item.get("cost_center", ""),
                item.get("account_code", ""),
            )
            grouped[key] = grouped.get(key, Decimal("0")) + Decimal(str(item.get("total_amount", 0)))

        first_id = None
        for (cc, acc), amount in grouped.items():
            if amount <= 0:
                continue
            commit_id = str(uuid4())
            self.db.execute(
                text("""
                    INSERT INTO budget_commitment (
                        id, entity_id, cost_center, account_code,
                        year, month, source_type, source_id, source_ref,
                        committed_amount, released_amount, status, committed_at
                    ) VALUES (
                        :id, :eid, :cc, :acc,
                        :yr, :mo, 'purchase_order', :src_id, :src_ref,
                        :amount, 0, 'active', NOW()
                    )
                """),
                {
                    "id": commit_id, "eid": entity_id, "cc": cc, "acc": acc,
                    "yr": year, "mo": month, "src_id": po_id, "src_ref": po_no,
                    "amount": float(amount),
                }
            )
            if first_id is None:
                first_id = commit_id

        self.db.commit()
        logger.info(f"PO commitment created: PO {po_no}, {len(grouped)} lines, year={year} month={month}")
        return first_id or ""

    def release_commitment(
        self,
        po_id:          str,
        invoiced_amount: Decimal,
    ) -> bool:
        """
        Rilis sebagian commitment saat invoice masuk untuk PO ini.
        Dipanggil dari AP processor saat invoice PO diverifikasi.
        """
        rows = self.db.execute(
            text("""
                SELECT id, committed_amount, released_amount
                FROM budget_commitment
                WHERE source_type = 'purchase_order' AND source_id = :po_id
                  AND status IN ('active', 'partial')
                ORDER BY committed_at
            """),
            {"po_id": po_id}
        ).fetchall()

        remaining = invoiced_amount
        for row in rows:
            if remaining <= 0:
                break
            releasable = Decimal(str(row.committed_amount)) - Decimal(str(row.released_amount))
            release    = min(remaining, releasable)
            new_released = Decimal(str(row.released_amount)) + release
            new_status   = (
                "released" if new_released >= Decimal(str(row.committed_amount))
                else "partial"
            )
            self.db.execute(
                text("""
                    UPDATE budget_commitment SET
                        released_amount = :released,
                        status          = :status,
                        released_at     = NOW()
                    WHERE id = :id
                """),
                {"id": row.id, "released": float(new_released), "status": new_status}
            )
            remaining -= release

        self.db.commit()
        return True

    def cancel_commitment(self, po_id: str) -> bool:
        """Batalkan semua commitment saat PO di-cancel."""
        self.db.execute(
            text("""
                UPDATE budget_commitment SET status = 'cancelled', released_at = NOW()
                WHERE source_type = 'purchase_order' AND source_id = :po_id
                  AND status IN ('active', 'partial')
            """),
            {"po_id": po_id}
        )
        self.db.commit()
        logger.info(f"PO commitment cancelled: PO ID {po_id}")
        return True

    # ── Hard mitigation: reservasi saat PR submit ───────────────────────────────

    def reserve_for_pr(
        self,
        pr_id:      str,
        pr_no:      str,
        entity_id:  str,
        cost_center: str,
        items:      list[dict],   # [{"account_code": ..., "total_amount": ...}]
        year:       int,
        month:      int,
    ) -> dict:
        """
        Dipanggil saat PR submit. Mengunci baris budget_line yang relevan
        (SELECT ... FOR UPDATE) supaya 2 submit bersamaan untuk cost_center+account_code
        yang sama tidak bisa lolos dobel — transaksi kedua menunggu transaksi pertama
        commit/rollback sebelum membaca ulang sisa budget yang sudah ter-update.

        Mengembalikan {"action": "allow"|"warn"|"block", "results": [...]}.
        Kalau action == "block", TIDAK ADA commitment yang dibuat dan caller harus
        rollback (jangan lanjut transisi status PR ke submitted).
        """
        grouped: dict[tuple, Decimal] = {}
        for item in items:
            key = (cost_center, item.get("account_code", ""))
            grouped[key] = grouped.get(key, Decimal("0")) + Decimal(str(item.get("total_amount", 0)))

        results = []
        blocked = 0
        warnings = 0
        for (cc, acc), amount in grouped.items():
            if amount <= 0:
                continue
            # Kunci baris budget_line yang relevan — resource yang diperebutkan.
            self.db.execute(
                text("""
                    SELECT id FROM budget_line
                    WHERE entity_id = :eid AND cost_center = :cc AND account_code = :acc
                      AND year = :yr AND month = :mo
                    FOR UPDATE
                """),
                {"eid": entity_id, "cc": cc, "acc": acc, "yr": year, "mo": month}
            )
            r = self.check(
                entity_id=entity_id, cost_center=cc, account_code=acc,
                year=year, month=month, requested=amount,
            )
            results.append(r.to_dict())
            if r.action == "block":
                blocked += 1
            elif r.action == "warn":
                warnings += 1

        overall = "block" if blocked > 0 else ("warn" if warnings > 0 else "allow")
        if overall == "block":
            return {"action": "block", "results": results}

        for (cc, acc), amount in grouped.items():
            if amount <= 0:
                continue
            self.db.execute(
                text("""
                    INSERT INTO budget_commitment (
                        id, entity_id, cost_center, account_code,
                        year, month, source_type, source_id, source_ref,
                        committed_amount, released_amount, status, committed_at
                    ) VALUES (
                        uuid_generate_v4(), :eid, :cc, :acc,
                        :yr, :mo, 'purchase_requisition', :src_id, :src_ref,
                        :amount, 0, 'active', NOW()
                    )
                """),
                {"eid": entity_id, "cc": cc, "acc": acc, "yr": year, "mo": month,
                 "src_id": pr_id, "src_ref": pr_no, "amount": float(amount)}
            )

        self.db.commit()
        logger.info(f"PR commitment reserved: PR {pr_no}, {len(grouped)} lines, year={year} month={month}")
        return {"action": overall, "results": results}

    def cancel_pr_commitment(self, pr_id: str) -> bool:
        """
        Lepas reservasi budget dari PR — dipanggil saat PR ditolak, ATAU saat PO
        turunannya sudah final approved (reservasi PR digantikan commitment PO yang
        bernilai nyata hasil tender) atau PO-nya ditolak.
        """
        self.db.execute(
            text("""
                UPDATE budget_commitment SET status = 'cancelled', released_at = NOW()
                WHERE source_type = 'purchase_requisition' AND source_id = :pr_id
                  AND status IN ('active', 'partial')
            """),
            {"pr_id": pr_id}
        )
        self.db.commit()
        logger.info(f"PR commitment released: PR ID {pr_id}")
        return True

    # ── Budget transfer ────────────────────────────────────────────────────────

    def apply_transfer(self, transfer_id: str) -> bool:
        """
        Terapkan budget transfer yang sudah approved:
        kurangi dari_line, tambahkan ke_line.
        """
        tf = self.db.execute(
            text("SELECT * FROM budget_transfer WHERE id = :id AND status = 'approved'"),
            {"id": transfer_id}
        ).fetchone()
        if not tf:
            return False

        # Kurangi from_line
        self.db.execute(
            text("""
                UPDATE budget_line SET
                    budgeted_amount = budgeted_amount - :amt,
                    updated_at      = NOW()
                WHERE period_id   = :pid
                  AND cost_center  = :cc
                  AND account_code = :acc
                  AND year         = :yr
                  AND month        = :mo
            """),
            {
                "amt": float(tf.amount), "pid": str(tf.from_period_id),
                "cc": tf.from_cost_center, "acc": tf.from_account_code,
                "yr": tf.fiscal_year, "mo": tf.month,
            }
        )
        # Tambah to_line (upsert)
        self.db.execute(
            text("""
                INSERT INTO budget_line (id, period_id, entity_id, cost_center, account_code, year, month, budgeted_amount)
                VALUES (uuid_generate_v4(), :pid, :eid, :cc, :acc, :yr, :mo, :amt)
                ON CONFLICT (period_id, cost_center, account_code, year, month)
                DO UPDATE SET budgeted_amount = budget_line.budgeted_amount + EXCLUDED.budgeted_amount,
                              updated_at = NOW()
            """),
            {
                "pid": str(tf.to_period_id), "eid": str(tf.entity_id),
                "cc": tf.to_cost_center, "acc": tf.to_account_code,
                "yr": tf.fiscal_year, "mo": tf.month, "amt": float(tf.amount),
            }
        )
        self.db.commit()
        return True

    def apply_supplement(self, supplement_id: str) -> bool:
        """Tambah plafon budget dari supplement yang sudah approved."""
        sup = self.db.execute(
            text("SELECT * FROM budget_supplement WHERE id = :id AND status = 'approved'"),
            {"id": supplement_id}
        ).fetchone()
        if not sup:
            return False

        self.db.execute(
            text("""
                INSERT INTO budget_line (id, period_id, entity_id, cost_center, account_code, year, month, budgeted_amount)
                VALUES (uuid_generate_v4(), :pid, :eid, :cc, :acc, :yr, :mo, :amt)
                ON CONFLICT (period_id, cost_center, account_code, year, month)
                DO UPDATE SET budgeted_amount = budget_line.budgeted_amount + EXCLUDED.budgeted_amount,
                              updated_at = NOW()
            """),
            {
                "pid": str(sup.period_id), "eid": str(sup.entity_id),
                "cc": sup.cost_center, "acc": sup.account_code,
                "yr": _get_year_from_period(self.db, str(sup.period_id)),
                "mo": sup.month, "amt": float(sup.amount),
            }
        )
        self.db.commit()
        return True

    # ── Utilization report ─────────────────────────────────────────────────────

    def get_utilization(
        self,
        entity_id:    str,
        year:         int,
        cost_center:  Optional[str] = None,
        account_code: Optional[str] = None,
        month:        Optional[int] = None,
    ) -> list[dict]:
        """
        Laporan utilisasi anggaran: budget vs actual vs commitment.
        """
        conditions = [
            "bu.entity_id = :eid",
            "bu.year = :year",
        ]
        params: dict = {"eid": entity_id, "year": year}

        if cost_center:
            conditions.append("bu.cost_center = :cc")
            params["cc"] = cost_center
        if account_code:
            conditions.append("bu.account_code = :acc")
            params["acc"] = account_code
        if month:
            conditions.append("bu.month = :month")
            params["month"] = month

        where = " AND ".join(conditions)
        rows = self.db.execute(
            text(f"""
                SELECT *,
                    budgeted_amount - actual_amount - commitment_amount AS available_amount,
                    CASE WHEN budgeted_amount > 0
                         THEN ROUND((actual_amount + commitment_amount) * 100.0 / budgeted_amount, 2)
                         ELSE NULL END AS utilization_pct
                FROM vw_budget_utilization bu
                WHERE {where}
                ORDER BY cost_center, account_code, month
            """),
            params
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    def get_variance_report(
        self,
        entity_id:   str,
        year:        int,
        cost_center: Optional[str] = None,
    ) -> dict:
        """
        Laporan Budget vs Actual tahunan — ringkasan per cost center dan akun.
        """
        conditions = ["entity_id = :eid", "year = :year"]
        params: dict = {"eid": entity_id, "year": year}

        if cost_center:
            conditions.append("cost_center = :cc")
            params["cc"] = cost_center

        where = " AND ".join(conditions)
        rows = self.db.execute(
            text(f"""
                SELECT * FROM vw_budget_variance_annual
                WHERE {where}
                ORDER BY cost_center, account_code
            """),
            params
        ).fetchall()

        lines        = [dict(r._mapping) for r in rows]
        total_budget = sum(float(r["budget_annual"] or 0) for r in lines)
        total_actual = sum(float(r["actual_annual"] or 0) for r in lines)
        total_var    = total_budget - total_actual
        overall_util = (total_actual / total_budget * 100) if total_budget > 0 else 0

        return {
            "entity_id":       entity_id,
            "year":            year,
            "cost_center":     cost_center or "ALL",
            "lines":           lines,
            "summary": {
                "total_budget":  total_budget,
                "total_actual":  total_actual,
                "total_variance": total_var,
                "utilization_pct": round(overall_util, 2),
                "status": "over" if total_actual > total_budget else "under",
            },
        }

    # ── DB helpers ─────────────────────────────────────────────────────────────

    def _get_budgeted(self, entity_id, cost_center, account_code, year, month) -> Decimal:
        row = self.db.execute(
            text("""
                SELECT COALESCE(SUM(bl.budgeted_amount), 0) AS total
                FROM budget_line bl
                JOIN budget_period bp ON bp.id = bl.period_id
                WHERE bl.entity_id    = :eid
                  AND bl.cost_center  = :cc
                  AND bl.account_code = :acc
                  AND bl.year         = :year
                  AND bl.month        = :month
                  AND bp.status       IN ('released', 'closed')
            """),
            {"eid": entity_id, "cc": cost_center, "acc": account_code,
             "year": year, "month": month}
        ).fetchone()
        return Decimal(str(row.total or 0))

    def _get_actual(self, entity_id, cost_center, account_code, year, month) -> Decimal:
        row = self.db.execute(
            text("""
                SELECT COALESCE(SUM(gl.debit_idr - gl.credit_idr), 0) AS total
                FROM gl_line gl
                JOIN gl_journal j           ON j.id = gl.journal_id
                JOIN chart_of_accounts coa  ON coa.id = gl.account_id
                WHERE j.entity_id       = :eid
                  AND gl.cost_center    = :cc
                  AND coa.account_code  = :acc
                  AND j.status          = 'posted'
                  AND EXTRACT(YEAR  FROM j.journal_date) = :year
                  AND EXTRACT(MONTH FROM j.journal_date) = :month
            """),
            {"eid": entity_id, "cc": cost_center, "acc": account_code,
             "year": year, "month": month}
        ).fetchone()
        return Decimal(str(row.total or 0))

    def _get_committed(self, entity_id, cost_center, account_code, year, month) -> Decimal:
        row = self.db.execute(
            text("""
                SELECT COALESCE(SUM(committed_amount - released_amount), 0) AS total
                FROM budget_commitment
                WHERE entity_id    = :eid
                  AND cost_center  = :cc
                  AND account_code = :acc
                  AND year         = :year
                  AND month        = :month
                  AND status       IN ('active', 'partial')
            """),
            {"eid": entity_id, "cc": cost_center, "acc": account_code,
             "year": year, "month": month}
        ).fetchone()
        return Decimal(str(row.total or 0))

    def _get_control_mode(self, entity_id: str, year: int) -> Optional[str]:
        row = self.db.execute(
            text("""
                SELECT control_mode FROM budget_period
                WHERE entity_id = :eid AND fiscal_year = :year
                  AND status IN ('released', 'closed')
                ORDER BY budget_version DESC LIMIT 1
            """),
            {"eid": entity_id, "year": year}
        ).fetchone()
        return row.control_mode if row else None


# ── Utility ────────────────────────────────────────────────────────────────────

def _get_year_from_period(db: Session, period_id: str) -> int:
    row = db.execute(
        text("SELECT fiscal_year FROM budget_period WHERE id = :id"),
        {"id": period_id}
    ).fetchone()
    return int(row.fiscal_year) if row else 0
