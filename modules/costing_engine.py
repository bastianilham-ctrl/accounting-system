# modules/costing_engine.py
# Unit Costing, Labor Allocation & Overhead Allocation Engine
#
# Arsitektur "Satu Input, Dua Output" (BAB II):
#   Primary Ledger  → GL journal (saldo riil — tidak disentuh engine ini)
#   Analytic Ledger → analytic_journal (distribusi biaya ke proyek)
#
# Tiga pilar governance (BAB V):
#   1. Secondary-ledger only   — tidak mengubah Kas/Bank/Piutang/Utang di GL
#   2. Rollback feature        — reverse_analytic_period() sebelum lock
#   3. Period lock constraint  — lock setelah Financial Controller validasi

from calendar import isleap
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from sqlalchemy.orm import Session
from sqlalchemy import text

from modules.contract_engine import _gen_invoice_no

WORK_HOURS_PER_DAY      = 8
DEFAULT_UTILIZATION      = Decimal("0.80")
DEFAULT_NATIONAL_HOLIDAYS = 16
DEFAULT_ANNUAL_LEAVE      = 12


# ── Data Classes ───────────────────────────────────────────────────────────────

@dataclass
class BillableHoursResult:
    fiscal_year:           int
    total_days:            int
    weekends:              int
    national_holidays:     int
    annual_leave:          int
    available_days:        int
    available_hours:       Decimal
    utilization_rate:      Decimal
    target_billable_hours: Decimal

    def to_dict(self) -> dict:
        return {
            "fiscal_year":           self.fiscal_year,
            "total_days":            self.total_days,
            "weekends":              self.weekends,
            "national_holidays":     self.national_holidays,
            "annual_leave":          self.annual_leave,
            "available_days":        self.available_days,
            "available_hours":       float(self.available_hours),
            "utilization_rate":      float(self.utilization_rate),
            "target_billable_hours": float(self.target_billable_hours),
        }


@dataclass
class UnitCostResult:
    employee_id:            str
    fiscal_year:            int
    total_ctc:              Decimal
    available_days:         int
    target_billable_hours:  Decimal
    unit_cost_per_hour:     Decimal

    def to_dict(self) -> dict:
        return {
            "employee_id":           self.employee_id,
            "fiscal_year":           self.fiscal_year,
            "total_ctc":             float(self.total_ctc),
            "available_days":        self.available_days,
            "target_billable_hours": float(self.target_billable_hours),
            "unit_cost_per_hour":    float(self.unit_cost_per_hour),
        }


# ── Core Engine ────────────────────────────────────────────────────────────────

class CostingEngine:
    def __init__(self, db: Session):
        self.db = db

    # ── 1. Target Billable Hours Calculation ──────────────────────────────────

    def calculate_target_hours(
        self,
        fiscal_year:       int,
        national_holidays: int = DEFAULT_NATIONAL_HOLIDAYS,
        annual_leave:      int = DEFAULT_ANNUAL_LEAVE,
        utilization:       float = 0.80,
    ) -> BillableHoursResult:
        """
        Menghitung target jam billable neto per tahun.

        Rumus (BAB I §3):
            Available Days = Total Days − Weekends − National Holidays − Annual Leave
            Target Billable Hours = Available Days × 8 × Utilization Rate
        """
        total_days  = 366 if isleap(fiscal_year) else 365
        weekends    = _count_weekends(fiscal_year)
        avail_days  = max(0, total_days - weekends - national_holidays - annual_leave)
        avail_hours = Decimal(avail_days) * WORK_HOURS_PER_DAY
        util        = Decimal(str(utilization))
        target      = (avail_hours * util).quantize(Decimal("0.01"))

        return BillableHoursResult(
            fiscal_year           = fiscal_year,
            total_days            = total_days,
            weekends              = weekends,
            national_holidays     = national_holidays,
            annual_leave          = annual_leave,
            available_days        = avail_days,
            available_hours       = avail_hours,
            utilization_rate      = util,
            target_billable_hours = target,
        )

    # ── 2. Unit Cost per Hour Calculation ─────────────────────────────────────

    def refresh_unit_cost(self, employee_id: str, fiscal_year: int) -> UnitCostResult:
        """
        Hitung ulang unit_cost_per_hour dari employee_cost_rate dan simpan snapshot.
        Rumus: Unit Cost/Jam = CTC Setahun ÷ Target Billable Hours
        """
        rate = self.db.execute(
            text("SELECT * FROM employee_cost_rate WHERE employee_id = :eid AND fiscal_year = :yr"),
            {"eid": employee_id, "yr": fiscal_year}
        ).fetchone()
        if not rate:
            raise ValueError(f"Cost rate tidak ditemukan untuk employee {employee_id} tahun {fiscal_year}")

        bh = self.calculate_target_hours(
            fiscal_year       = fiscal_year,
            national_holidays = rate.national_holidays,
            annual_leave      = rate.annual_leave_days,
            utilization       = float(rate.utilization_rate),
        )
        total_ctc = Decimal(str(rate.total_ctc))
        if bh.target_billable_hours <= 0:
            raise ValueError("Target billable hours = 0, tidak bisa menghitung unit cost")

        unit_cost = (total_ctc / bh.target_billable_hours).quantize(Decimal("0.01"))

        self.db.execute(
            text("""
                UPDATE employee_cost_rate SET
                    available_days        = :ad,
                    available_hours       = :ah,
                    target_billable_hours = :tbh,
                    unit_cost_per_hour    = :ucp,
                    updated_at            = NOW()
                WHERE employee_id = :eid AND fiscal_year = :yr
            """),
            {
                "ad":  bh.available_days,
                "ah":  float(bh.available_hours),
                "tbh": float(bh.target_billable_hours),
                "ucp": float(unit_cost),
                "eid": employee_id,
                "yr":  fiscal_year,
            }
        )
        self.db.commit()

        return UnitCostResult(
            employee_id           = employee_id,
            fiscal_year           = fiscal_year,
            total_ctc             = total_ctc,
            available_days        = bh.available_days,
            target_billable_hours = bh.target_billable_hours,
            unit_cost_per_hour    = unit_cost,
        )

    # ── 3. Labor Allocation — timesheet → analytic journal ───────────────────

    def post_labor_allocation(
        self,
        entity_id:  str,
        year:       int,
        month:      int,
        created_by: str = "system",
    ) -> dict:
        """
        Distribusikan beban gaji karyawan ke proyek berdasarkan timesheet approved.

        Jurnal Analitik (BAB II §2):
            Dr. Beban Gaji Langsung  (tag: Project X)  = jam_X × unit_cost
            Dr. Beban Gaji Bench     (tag: Internal)   = jam_bench × unit_cost
        Tidak ada entri di Primary Ledger — murni secondary analytic.
        """
        _check_period_not_locked(self.db, entity_id, year, month)
        period = _get_or_create_analytic_period(self.db, entity_id, year, month)
        if period["labor_allocation_posted"]:
            raise ValueError(f"Labor allocation untuk {year}-{month:02d} sudah diposting. "
                             "Gunakan reverse_analytic_period() terlebih dahulu.")

        start_date = date(year, month, 1)
        end_date   = _last_day(year, month)

        # Ambil semua approved timesheet untuk periode ini
        timesheets = self.db.execute(
            text("""
                SELECT
                    pt.employee_id, pt.project_id, pt.activity_type,
                    SUM(pt.hours) AS total_hours
                FROM project_timesheet pt
                WHERE pt.entity_id     = :eid
                  AND pt.status        = 'approved'
                  AND pt.timesheet_date BETWEEN :start AND :end
                GROUP BY pt.employee_id, pt.project_id, pt.activity_type
            """),
            {"eid": entity_id, "start": start_date, "end": end_date}
        ).fetchall()

        if not timesheets:
            return {"status": "skipped", "reason": "Tidak ada timesheet approved untuk periode ini"}

        # Kumpulkan employee_id unik
        emp_ids = list({str(r.employee_id) for r in timesheets})

        # Baca unit_cost_per_hour untuk semua karyawan di tahun ini
        cost_rates = self.db.execute(
            text("""
                SELECT employee_id, unit_cost_per_hour
                FROM employee_cost_rate
                WHERE employee_id = ANY(:ids) AND fiscal_year = :yr
                  AND is_approved = TRUE
            """),
            {"ids": emp_ids, "yr": year}
        ).fetchall()
        rate_map = {str(r.employee_id): Decimal(str(r.unit_cost_per_hour)) for r in cost_rates}

        # Buat analytic journal
        journal_no  = _gen_analytic_no(self.db)
        journal_id  = str(uuid4())
        lines: list[dict] = []
        line_no = 1
        total_debit = Decimal("0")

        for ts in timesheets:
            emp_id = str(ts.employee_id)
            ucp    = rate_map.get(emp_id)
            if ucp is None:
                continue  # skip jika belum ada approved cost rate

            hours  = Decimal(str(ts.total_hours))
            amount = (hours * ucp).quantize(Decimal("0.01"))

            # activity_type → account_type analytic
            is_billable = ts.activity_type in ("billable",)
            acc_type    = "direct_labor" if is_billable else "idle_labor"

            lines.append({
                "line_no":            line_no,
                "project_id":         str(ts.project_id) if ts.project_id else None,
                "cost_center":        None,  # akan diisi dari project.cost_center saat insert
                "account_type":       acc_type,
                "debit_amount":       amount,
                "credit_amount":      Decimal("0"),
                "employee_id":        emp_id,
                "billable_hours":     hours,
                "unit_cost_per_hour": ucp,
                "source_ref":         f"timesheet/{year}/{month:02d}",
                "description":        f"Labor allocation {ts.activity_type} — {year}-{month:02d}",
            })
            total_debit += amount
            line_no += 1

        if not lines:
            return {"status": "skipped", "reason": "Tidak ada karyawan dengan approved cost rate"}

        # Insert journal header
        self.db.execute(
            text("""
                INSERT INTO analytic_journal (
                    id, entity_id, journal_no, journal_date, year, month,
                    journal_type, source_ref, description,
                    status, total_debit, total_credit, created_by
                ) VALUES (
                    :id, :eid, :no, :dt, :yr, :mo,
                    'labor_allocation', :src, :desc,
                    'posted', :td, 0, :by
                )
            """),
            {
                "id": journal_id, "eid": entity_id, "no": journal_no,
                "dt": end_date, "yr": year, "mo": month,
                "src": f"timesheet/{year}/{month:02d}",
                "desc": f"Labor Cost Allocation — {year}-{month:02d}",
                "td": float(total_debit), "by": created_by,
            }
        )

        # Insert lines
        for ln in lines:
            # Ambil cost_center dari project
            if ln["project_id"]:
                proj_cc = self.db.execute(
                    text("SELECT cost_center FROM project WHERE id = :id"),
                    {"id": ln["project_id"]}
                ).scalar()
            else:
                proj_cc = "BENCH"

            self.db.execute(
                text("""
                    INSERT INTO analytic_journal_line (
                        id, journal_id, line_no, project_id, cost_center,
                        account_type, debit_amount, credit_amount,
                        employee_id, billable_hours, unit_cost_per_hour,
                        source_ref, description
                    ) VALUES (
                        uuid_generate_v4(), :jid, :no, :pid, :cc,
                        :atype, :dr, :cr,
                        :emp, :hours, :ucp,
                        :src, :desc
                    )
                """),
                {
                    "jid": journal_id, "no": ln["line_no"],
                    "pid": ln["project_id"], "cc": proj_cc,
                    "atype": ln["account_type"],
                    "dr": float(ln["debit_amount"]), "cr": float(ln["credit_amount"]),
                    "emp": ln["employee_id"],
                    "hours": float(ln["billable_hours"]),
                    "ucp": float(ln["unit_cost_per_hour"]),
                    "src": ln["source_ref"], "desc": ln["description"],
                }
            )

        # Update analytic_period flag
        self.db.execute(
            text("""
                UPDATE analytic_period
                SET labor_allocation_posted = TRUE, status = 'processing', updated_at = NOW()
                WHERE entity_id = :eid AND year = :yr AND month = :mo
            """),
            {"eid": entity_id, "yr": year, "mo": month}
        )
        self.db.commit()

        return {
            "journal_id":   journal_id,
            "journal_no":   journal_no,
            "journal_type": "labor_allocation",
            "total_lines":  len(lines),
            "total_debit":  float(total_debit),
            "period":       f"{year}-{month:02d}",
        }

    # ── 4. Revenue Tagging — AR invoices → analytic journal ──────────────────

    def tag_revenue_to_analytic(
        self,
        entity_id:  str,
        year:       int,
        month:      int,
        created_by: str = "system",
    ) -> dict:
        """
        Tag pendapatan AR invoice yang sudah dikaitkan ke project_id
        ke dalam analytic journal sebagai sisi revenue.
        """
        _check_period_not_locked(self.db, entity_id, year, month)
        period = _get_or_create_analytic_period(self.db, entity_id, year, month)
        if period["revenue_tagging_posted"]:
            raise ValueError("Revenue tagging sudah diposting. Gunakan reverse_analytic_period() terlebih dahulu.")

        start_date = date(year, month, 1)
        end_date   = _last_day(year, month)

        # AR invoices dengan project_id di periode ini
        invoices = self.db.execute(
            text("""
                SELECT id, project_id, invoice_no, total_amount
                FROM ar_invoice
                WHERE entity_id    = :eid
                  AND project_id   IS NOT NULL
                  AND status       IN ('posted', 'paid', 'partially_paid')
                  AND invoice_date BETWEEN :start AND :end
            """),
            {"eid": entity_id, "start": start_date, "end": end_date}
        ).fetchall()

        if not invoices:
            return {"status": "skipped", "reason": "Tidak ada AR invoice dengan project_id di periode ini"}

        journal_no    = _gen_analytic_no(self.db)
        journal_id    = str(uuid4())
        total_credit  = Decimal("0")

        self.db.execute(
            text("""
                INSERT INTO analytic_journal (
                    id, entity_id, journal_no, journal_date, year, month,
                    journal_type, source_ref, description,
                    status, total_debit, total_credit, created_by
                ) VALUES (
                    :id, :eid, :no, :dt, :yr, :mo,
                    'revenue_tagging', :src, :desc,
                    'posted', 0, :tc, :by
                )
            """),
            {
                "id": journal_id, "eid": entity_id, "no": journal_no,
                "dt": end_date, "yr": year, "mo": month,
                "src": f"ar_invoice/{year}/{month:02d}",
                "desc": f"Revenue Tagging — {year}-{month:02d}",
                "tc": 0, "by": created_by,
            }
        )

        for i, inv in enumerate(invoices, 1):
            amount = Decimal(str(inv.total_amount))
            total_credit += amount

            proj_cc = self.db.execute(
                text("SELECT cost_center FROM project WHERE id = :id"),
                {"id": str(inv.project_id)}
            ).scalar()

            self.db.execute(
                text("""
                    INSERT INTO analytic_journal_line (
                        id, journal_id, line_no, project_id, cost_center,
                        account_type, debit_amount, credit_amount,
                        source_ref, description
                    ) VALUES (
                        uuid_generate_v4(), :jid, :no, :pid, :cc,
                        'revenue', 0, :cr,
                        :src, :desc
                    )
                """),
                {
                    "jid": journal_id, "no": i,
                    "pid": str(inv.project_id), "cc": proj_cc,
                    "cr": float(amount),
                    "src": str(inv.id),
                    "desc": f"Revenue — {inv.invoice_no}",
                }
            )

        # Update total_credit di header
        self.db.execute(
            text("UPDATE analytic_journal SET total_credit = :tc WHERE id = :id"),
            {"tc": float(total_credit), "id": journal_id}
        )
        self.db.execute(
            text("""
                UPDATE analytic_period SET
                    revenue_tagging_posted = TRUE, status = 'processing', updated_at = NOW()
                WHERE entity_id = :eid AND year = :yr AND month = :mo
            """),
            {"eid": entity_id, "yr": year, "mo": month}
        )
        self.db.commit()

        return {
            "journal_id":    journal_id,
            "journal_no":    journal_no,
            "journal_type":  "revenue_tagging",
            "invoices_tagged": len(invoices),
            "total_revenue": float(total_credit),
            "period":        f"{year}-{month:02d}",
        }

    # ── 5. Overhead Allocation Engine (Assessment Method) ────────────────────

    def execute_overhead_allocation(
        self,
        rule_id:    str,
        year:       int,
        month:      int,
        created_by: str = "system",
    ) -> dict:
        """
        Distribusikan beban overhead dari G&A Pool ke proyek-proyek penerima.

        Alur (BAB III §1):
            1. Baca total overhead pool dari GL (berdasarkan sender_cost_center)
            2. Hitung basis alokasi per proyek (Revenue / Timesheet / Headcount / Equal)
            3. Hitung rasio = basis_proyek / total_basis
            4. Posting analytic journal lines (tidak menyentuh GL)
            5. Drain pool analytically (pool_clearing)
        """
        rule = self.db.execute(
            text("SELECT * FROM allocation_rules WHERE id = :id AND is_active = TRUE"),
            {"id": rule_id}
        ).fetchone()
        if not rule:
            raise ValueError(f"Allocation rule {rule_id} tidak ditemukan atau tidak aktif")

        entity_id = str(rule.entity_id)
        _check_period_not_locked(self.db, entity_id, year, month)
        _get_or_create_analytic_period(self.db, entity_id, year, month)

        start_date = date(year, month, 1)
        end_date   = _last_day(year, month)

        # ── Step 1: Total overhead pool dari GL ─────────────────────────────
        total_overhead = self.db.execute(
            text("""
                SELECT COALESCE(SUM(jl.debit_amount - jl.credit_amount), 0)
                FROM gl_journal_line jl
                JOIN gl_journal j ON j.id = jl.journal_id
                WHERE jl.cost_center  = :cc
                  AND j.entity_id     = :eid
                  AND j.status        = 'posted'
                  AND j.journal_date  BETWEEN :start AND :end
            """),
            {"cc": rule.sender_cost_center, "eid": entity_id,
             "start": start_date, "end": end_date}
        ).scalar() or Decimal("0")

        total_overhead = Decimal(str(total_overhead))
        if total_overhead <= 0:
            return {"status": "skipped", "reason": "Saldo overhead pool kosong atau nol"}

        # ── Step 2: Ambil destinations ────────────────────────────────────
        destinations = self.db.execute(
            text("""
                SELECT ad.project_id, ad.fixed_ratio
                FROM allocation_destinations ad
                WHERE ad.rule_id = :rid
            """),
            {"rid": rule_id}
        ).fetchall()

        if not destinations:
            raise ValueError(f"Rule {rule_id} tidak memiliki destination projects")

        # ── Step 3: Hitung basis per proyek ────────────────────────────────
        proj_ids = [str(d.project_id) for d in destinations]

        if rule.allocation_basis == "REVENUE":
            basis_rows = self.db.execute(
                text("""
                    SELECT project_id, COALESCE(SUM(total_amount), 0) AS basis
                    FROM ar_invoice
                    WHERE project_id   = ANY(:pids)
                      AND entity_id    = :eid
                      AND status       IN ('posted', 'paid', 'partially_paid')
                      AND invoice_date BETWEEN :start AND :end
                    GROUP BY project_id
                """),
                {"pids": proj_ids, "eid": entity_id, "start": start_date, "end": end_date}
            ).fetchall()

        elif rule.allocation_basis == "TIMESHEET":
            basis_rows = self.db.execute(
                text("""
                    SELECT project_id, COALESCE(SUM(hours), 0) AS basis
                    FROM project_timesheet
                    WHERE project_id   = ANY(:pids)
                      AND entity_id    = :eid
                      AND status       = 'approved'
                      AND activity_type = 'billable'
                      AND timesheet_date BETWEEN :start AND :end
                    GROUP BY project_id
                """),
                {"pids": proj_ids, "eid": entity_id, "start": start_date, "end": end_date}
            ).fetchall()

        elif rule.allocation_basis == "HEADCOUNT":
            basis_rows = self.db.execute(
                text("""
                    SELECT project_id, COUNT(DISTINCT employee_id) AS basis
                    FROM project_assignment
                    WHERE project_id  = ANY(:pids)
                      AND is_active   = TRUE
                      AND assigned_from <= :end
                      AND (assigned_to IS NULL OR assigned_to >= :start)
                    GROUP BY project_id
                """),
                {"pids": proj_ids, "start": start_date, "end": end_date}
            ).fetchall()

        else:  # EQUAL
            basis_rows = [
                type("Row", (), {"project_id": pid, "basis": 1})()
                for pid in proj_ids
            ]

        basis_map = {str(r.project_id): Decimal(str(r.basis)) for r in basis_rows}
        total_basis = sum(basis_map.values())

        if total_basis <= 0:
            return {
                "status": "skipped",
                "reason": (
                    f"Total basis ({rule.allocation_basis}) = 0 untuk bulan ini. "
                    "Tidak ada proyek yang bisa menerima alokasi."
                ),
            }

        # ── Step 4: Buat analytic journal ─────────────────────────────────
        journal_no  = _gen_analytic_no(self.db)
        journal_id  = str(uuid4())
        lines: list[dict] = []
        total_allocated = Decimal("0")

        for dest in destinations:
            pid       = str(dest.project_id)
            # Gunakan fixed_ratio jika tersedia, else hitung dari basis
            if dest.fixed_ratio is not None:
                ratio = Decimal(str(dest.fixed_ratio))
            else:
                proj_basis = basis_map.get(pid, Decimal("0"))
                ratio      = (proj_basis / total_basis).quantize(Decimal("0.00000001")) if total_basis > 0 else Decimal("0")

            allocated = (total_overhead * ratio).quantize(Decimal("0.01"))
            if allocated <= 0:
                continue

            proj_cc = self.db.execute(
                text("SELECT cost_center, project_name FROM project WHERE id = :id"),
                {"id": pid}
            ).fetchone()

            lines.append({
                "project_id":       pid,
                "cost_center":      proj_cc.cost_center if proj_cc else pid,
                "account_type":     "overhead",
                "debit_amount":     allocated,
                "credit_amount":    Decimal("0"),
                "rule_id":          rule_id,
                "allocation_ratio": ratio,
                "description":      f"Alokasi overhead {rule.rule_name} — rasio {float(ratio)*100:.2f}% — {year}-{month:02d}",
            })
            total_allocated += allocated

        # Pool clearing line (kredit analytic)
        lines.append({
            "project_id":       None,
            "cost_center":      rule.sender_cost_center,
            "account_type":     "pool_clearing",
            "debit_amount":     Decimal("0"),
            "credit_amount":    total_allocated,
            "rule_id":          rule_id,
            "allocation_ratio": Decimal("1"),
            "description":      f"Pool clearing — {rule.sender_cost_center} — {year}-{month:02d}",
        })

        # Insert header
        self.db.execute(
            text("""
                INSERT INTO analytic_journal (
                    id, entity_id, journal_no, journal_date, year, month,
                    journal_type, rule_id, source_ref, description,
                    status, total_debit, total_credit, created_by
                ) VALUES (
                    :id, :eid, :no, :dt, :yr, :mo,
                    'overhead_allocation', :rid, :src, :desc,
                    'posted', :td, :tc, :by
                )
            """),
            {
                "id": journal_id, "eid": entity_id, "no": journal_no,
                "dt": end_date, "yr": year, "mo": month,
                "rid": rule_id,
                "src": f"rule/{rule_id}/{year}/{month:02d}",
                "desc": f"Overhead Allocation — {rule.rule_name} — {year}-{month:02d}",
                "td": float(total_allocated), "tc": float(total_allocated),
                "by": created_by,
            }
        )

        for i, ln in enumerate(lines, 1):
            self.db.execute(
                text("""
                    INSERT INTO analytic_journal_line (
                        id, journal_id, line_no, project_id, cost_center,
                        account_type, debit_amount, credit_amount,
                        rule_id, allocation_ratio, description
                    ) VALUES (
                        uuid_generate_v4(), :jid, :no, :pid, :cc,
                        :atype, :dr, :cr,
                        :rid, :ratio, :desc
                    )
                """),
                {
                    "jid": journal_id, "no": i,
                    "pid": ln["project_id"], "cc": ln["cost_center"],
                    "atype": ln["account_type"],
                    "dr": float(ln["debit_amount"]), "cr": float(ln["credit_amount"]),
                    "rid": ln.get("rule_id"), "ratio": float(ln["allocation_ratio"]),
                    "desc": ln["description"],
                }
            )

        self.db.execute(
            text("""
                UPDATE analytic_period SET
                    overhead_allocation_posted = TRUE, status = 'processing', updated_at = NOW()
                WHERE entity_id = :eid AND year = :yr AND month = :mo
            """),
            {"eid": entity_id, "yr": year, "mo": month}
        )
        self.db.commit()

        return {
            "journal_id":        journal_id,
            "journal_no":        journal_no,
            "journal_type":      "overhead_allocation",
            "rule_name":         rule.rule_name,
            "total_overhead":    float(total_overhead),
            "total_allocated":   float(total_allocated),
            "projects_receiving": len([l for l in lines if l["account_type"] == "overhead"]),
            "period":            f"{year}-{month:02d}",
        }

    # ── 6. Reversal — rollback semua / per tipe ───────────────────────────────

    def reverse_analytic_period(
        self,
        entity_id:    str,
        year:         int,
        month:        int,
        journal_type: Optional[str] = None,  # None = semua tipe
        created_by:   str = "system",
    ) -> dict:
        """
        Governance (BAB V §2): batalkan jurnal analitik sebelum lock.
        Membuat jurnal reversal (debit↔credit swap) dan menandai original sebagai 'reversed'.
        """
        _check_period_not_locked(self.db, entity_id, year, month)

        cond = "entity_id = :eid AND year = :yr AND month = :mo AND status = 'posted'"
        params: dict = {"eid": entity_id, "yr": year, "mo": month}
        if journal_type:
            cond += " AND journal_type = :jtype"
            params["jtype"] = journal_type

        journals = self.db.execute(
            text(f"SELECT * FROM analytic_journal WHERE {cond}"),
            params
        ).fetchall()

        if not journals:
            return {"status": "skipped", "reason": "Tidak ada jurnal analitik yang perlu di-reverse"}

        reversed_count = 0
        for j in journals:
            rev_id  = str(uuid4())
            rev_no  = _gen_analytic_no(self.db)

            # Insert reversal header
            self.db.execute(
                text("""
                    INSERT INTO analytic_journal (
                        id, entity_id, journal_no, journal_date, year, month,
                        journal_type, rule_id, source_ref, description,
                        status, reversed_by_id, total_debit, total_credit, created_by
                    ) VALUES (
                        :id, :eid, :no, :dt, :yr, :mo,
                        :jtype, :rid, :src, :desc,
                        'posted', :orig_id, :td, :tc, :by
                    )
                """),
                {
                    "id": rev_id, "eid": entity_id, "no": rev_no,
                    "dt": _last_day(year, month), "yr": year, "mo": month,
                    "jtype": j.journal_type, "rid": j.rule_id,
                    "src": f"reversal/{j.journal_no}",
                    "desc": f"[REVERSAL] {j.description}",
                    "orig_id": str(j.id),
                    # swap debit/credit totals
                    "td": float(j.total_credit), "tc": float(j.total_debit),
                    "by": created_by,
                }
            )

            # Copy & swap lines
            orig_lines = self.db.execute(
                text("SELECT * FROM analytic_journal_line WHERE journal_id = :jid ORDER BY line_no"),
                {"jid": str(j.id)}
            ).fetchall()

            for ln in orig_lines:
                self.db.execute(
                    text("""
                        INSERT INTO analytic_journal_line (
                            id, journal_id, line_no, project_id, cost_center,
                            account_type, debit_amount, credit_amount,
                            employee_id, billable_hours, unit_cost_per_hour,
                            rule_id, allocation_ratio, source_ref, description
                        ) VALUES (
                            uuid_generate_v4(), :jid, :no, :pid, :cc,
                            :atype, :dr, :cr,
                            :emp, :hrs, :ucp,
                            :rid, :ratio, :src, :desc
                        )
                    """),
                    {
                        "jid": rev_id, "no": ln.line_no,
                        "pid": str(ln.project_id) if ln.project_id else None,
                        "cc": ln.cost_center, "atype": ln.account_type,
                        "dr": float(ln.credit_amount),   # swap
                        "cr": float(ln.debit_amount),    # swap
                        "emp": str(ln.employee_id) if ln.employee_id else None,
                        "hrs": float(ln.billable_hours) if ln.billable_hours else None,
                        "ucp": float(ln.unit_cost_per_hour) if ln.unit_cost_per_hour else None,
                        "rid": str(ln.rule_id) if ln.rule_id else None,
                        "ratio": float(ln.allocation_ratio) if ln.allocation_ratio else None,
                        "src": ln.source_ref, "desc": f"[REV] {ln.description}",
                    }
                )

            # Tandai original sebagai reversed
            self.db.execute(
                text("UPDATE analytic_journal SET status = 'reversed', reversed_by_id = :rid WHERE id = :id"),
                {"rid": rev_id, "id": str(j.id)}
            )
            reversed_count += 1

        # Reset flags di analytic_period
        reset_fields = []
        if not journal_type or journal_type == "labor_allocation":
            reset_fields.append("labor_allocation_posted = FALSE")
        if not journal_type or journal_type == "revenue_tagging":
            reset_fields.append("revenue_tagging_posted = FALSE")
        if not journal_type or journal_type == "overhead_allocation":
            reset_fields.append("overhead_allocation_posted = FALSE")

        if reset_fields:
            self.db.execute(
                text(f"""
                    UPDATE analytic_period SET
                        {', '.join(reset_fields)},
                        status = 'open', updated_at = NOW()
                    WHERE entity_id = :eid AND year = :yr AND month = :mo
                """),
                {"eid": entity_id, "yr": year, "mo": month}
            )
        self.db.commit()

        return {
            "reversed":     reversed_count,
            "journal_type": journal_type or "all",
            "period":       f"{year}-{month:02d}",
        }

    # ── 7. Period Lock ────────────────────────────────────────────────────────

    def lock_analytic_period(self, entity_id: str, year: int, month: int, locked_by: str) -> dict:
        """
        Governance (BAB V §3): lock periode analitik setelah divalidasi.
        Membutuhkan ke-3 step sudah selesai: labor + revenue + overhead.
        """
        period = self.db.execute(
            text("SELECT * FROM analytic_period WHERE entity_id = :eid AND year = :yr AND month = :mo"),
            {"eid": entity_id, "yr": year, "mo": month}
        ).fetchone()

        if not period:
            raise ValueError(f"Periode {year}-{month:02d} belum ada. Jalankan alokasi terlebih dahulu.")
        if period.status == "locked":
            raise ValueError(f"Periode {year}-{month:02d} sudah dalam status locked.")

        missing = []
        if not period.labor_allocation_posted:
            missing.append("Labor Allocation")
        if not period.revenue_tagging_posted:
            missing.append("Revenue Tagging")
        if not period.overhead_allocation_posted:
            missing.append("Overhead Allocation")

        if missing:
            raise ValueError(
                f"Lock dibatalkan — step berikut belum selesai: {', '.join(missing)}. "
                "Selesaikan atau skip dengan parameter force=True jika memang tidak ada transaksi."
            )

        self.db.execute(
            text("""
                UPDATE analytic_period SET
                    status = 'locked', locked_by = :by, locked_at = NOW(), updated_at = NOW()
                WHERE entity_id = :eid AND year = :yr AND month = :mo
            """),
            {"by": locked_by, "eid": entity_id, "yr": year, "mo": month}
        )
        self.db.commit()
        return {"status": "locked", "period": f"{year}-{month:02d}", "locked_by": locked_by}

    def lock_analytic_period_force(self, entity_id: str, year: int, month: int, locked_by: str) -> dict:
        """Lock paksa meski ada step yang belum diposting (untuk periode tanpa transaksi)."""
        _check_period_not_locked(self.db, entity_id, year, month)
        _get_or_create_analytic_period(self.db, entity_id, year, month)
        self.db.execute(
            text("""
                UPDATE analytic_period SET
                    status = 'locked', locked_by = :by, locked_at = NOW(), updated_at = NOW()
                WHERE entity_id = :eid AND year = :yr AND month = :mo
            """),
            {"by": locked_by, "eid": entity_id, "yr": year, "mo": month}
        )
        self.db.commit()
        return {"status": "locked", "period": f"{year}-{month:02d}", "note": "force-locked"}

    # ── 8. Labor Cost GL Reclass ──────────────────────────────────────────────

    def post_labor_reclass(
        self,
        entity_id:       str,
        year:            int,
        month:           int,
        beban_gaji_code: str,
        created_by:      str = "system",
    ) -> dict:
        """
        Distribusikan beban gaji aktual (dari payroll) ke GL per-project/cost_center
        berdasarkan rasio jam timesheet bulan itu.

        Prasyarat:
          1. analytic_period.labor_allocation_posted = TRUE (timesheet sudah ke analytic ledger)
          2. employee_payroll_history sudah ada untuk bulan ini (payroll sudah dihitung)
          3. labor_reclass_gl_posted = FALSE (belum pernah diposting, mencegah double-posting)

        Jurnal reclass (satu batch per periode):
          Dr  Beban Gaji — <project_code> (cost_center tagged)  =  aktual_payroll × rasio_project
          Dr  Beban Gaji — Bench/Internal                       =  aktual_payroll × rasio_bench
          Cr  Beban Gaji (pool, no cost_center)                 =  total aktual_payroll (offset)

        Sumber rasio: analytic_journal_line (timesheet × cost_rate per karyawan per project).
        Sumber aktual: employee_payroll_history.bruto_bulanan.
        """
        from modules.journal_engine import JournalEngine, JournalEntry, JournalLine

        _check_period_not_locked(self.db, entity_id, year, month)
        period = _get_or_create_analytic_period(self.db, entity_id, year, month)
        if not period.get("labor_allocation_posted"):
            raise ValueError(
                f"Labor allocation analytic untuk {year}-{month:02d} belum diposting. "
                "Jalankan post_labor_allocation() terlebih dahulu."
            )
        if period.get("labor_reclass_gl_posted"):
            raise ValueError(
                f"Labor reclass GL untuk {year}-{month:02d} sudah pernah diposting. "
                "Gunakan reverse_analytic_period() untuk membatalkan sebelum posting ulang."
            )

        start_date = date(year, month, 1)
        end_date   = _last_day(year, month)

        # Ambil aktual payroll per karyawan bulan ini
        payroll_rows = self.db.execute(
            text("""
                SELECT employee_id, bruto_bulanan
                FROM employee_payroll_history
                WHERE entity_id = :eid AND year = :yr AND month = :mo
                  AND bruto_bulanan > 0
            """),
            {"eid": entity_id, "yr": year, "mo": month}
        ).fetchall()

        if not payroll_rows:
            return {"status": "skipped", "reason": "Tidak ada data payroll untuk periode ini"}

        payroll_map = {str(r.employee_id): Decimal(str(r.bruto_bulanan)) for r in payroll_rows}

        # Ambil distribusi jam per karyawan per project dari analytic journal
        analytic_rows = self.db.execute(
            text("""
                SELECT
                    ajl.employee_id,
                    ajl.project_id,
                    ajl.cost_center,
                    SUM(ajl.debit_amount) AS allocated_cost
                FROM analytic_journal aj
                JOIN analytic_journal_line ajl ON ajl.journal_id = aj.id
                WHERE aj.entity_id = :eid AND aj.year = :yr AND aj.month = :mo
                  AND aj.status = 'posted'
                  AND ajl.account_type IN ('direct_labor', 'idle_labor')
                  AND ajl.employee_id IS NOT NULL
                  AND ajl.debit_amount > 0
                GROUP BY ajl.employee_id, ajl.project_id, ajl.cost_center
            """),
            {"eid": entity_id, "yr": year, "mo": month}
        ).fetchall()

        if not analytic_rows:
            return {"status": "skipped", "reason": "Tidak ada analytic labor allocation untuk periode ini"}

        # Hitung rasio per karyawan → distribusi aktual payroll
        from collections import defaultdict
        emp_total_analytic: dict[str, Decimal] = defaultdict(Decimal)
        for r in analytic_rows:
            emp_total_analytic[str(r.employee_id)] += Decimal(str(r.allocated_cost))

        # Ambil project_code untuk tagging
        project_ids = list({str(r.project_id) for r in analytic_rows if r.project_id})
        proj_code_map: dict[str, str] = {}
        if project_ids:
            proj_rows = self.db.execute(
                text("SELECT id, project_code, cost_center FROM project WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": project_ids}
            ).fetchall()
            proj_code_map = {str(r.id): r.project_code for r in proj_rows}

        # Build GL lines: distribute each employee's actual payroll proportionally
        je_lines: list[JournalLine] = []
        total_reclassed = Decimal("0")

        # Group Dr lines by (project_id, cost_center) to batch them
        bucket: dict[tuple, Decimal] = defaultdict(Decimal)
        bucket_label: dict[tuple, str] = {}

        for r in analytic_rows:
            emp_id   = str(r.employee_id)
            actual   = payroll_map.get(emp_id, Decimal("0"))
            analytic_total = emp_total_analytic.get(emp_id, Decimal("0"))
            if analytic_total == 0 or actual == 0:
                continue

            ratio   = Decimal(str(r.allocated_cost)) / analytic_total
            alloc   = (actual * ratio).quantize(Decimal("0.01"))
            if alloc <= 0:
                continue

            proj_id   = str(r.project_id) if r.project_id else None
            cc        = r.cost_center or "BENCH"
            proj_code = proj_code_map.get(proj_id, "BENCH") if proj_id else "BENCH"
            key = (proj_id, cc)
            bucket[key] += alloc
            bucket_label[key] = f"Reclass Beban Gaji — {proj_code} ({cc})"

        for (proj_id, cc), alloc in bucket.items():
            je_lines.append(JournalLine(
                account_code=beban_gaji_code,
                description=bucket_label[(proj_id, cc)],
                debit_idr=alloc,
                cost_center=cc if cc != "BENCH" else None,
                project_code=proj_code_map.get(proj_id) if proj_id else None,
            ))
            total_reclassed += alloc

        if not je_lines or total_reclassed <= 0:
            return {"status": "skipped", "reason": "Tidak ada alokasi payroll yang bisa dihitung"}

        # Cr: offset pool (no cost_center, same beban_gaji account)
        je_lines.append(JournalLine(
            account_code=beban_gaji_code,
            description=f"Offset pool reclass payroll {month:02d}/{year}",
            credit_idr=total_reclassed,
        ))

        je_result = JournalEngine(self.db).post_journal(JournalEntry(
            entity_id=entity_id,
            journal_type="GL",
            journal_date=end_date,
            description=f"Labor cost reclass GL — {month:02d}/{year}",
            reference_no=f"RECLASS/{year}/{month:02d}",
            created_by=created_by,
            lines=je_lines,
        ))
        if not je_result["success"]:
            raise ValueError(je_result.get("error", "JournalEngine gagal"))

        # Mark period as reclassed + insert audit row
        self.db.execute(
            text("UPDATE analytic_period SET labor_reclass_gl_posted = TRUE WHERE entity_id = :eid AND year = :yr AND month = :mo"),
            {"eid": entity_id, "yr": year, "mo": month}
        )
        self.db.execute(
            text("""
                INSERT INTO payroll_labor_reclass
                  (entity_id, year, month, journal_id, employees_count, total_reclassed, created_by)
                VALUES (:eid, :yr, :mo, :jid, :cnt, :total, :by)
                ON CONFLICT (entity_id, year, month) DO UPDATE
                  SET journal_id=EXCLUDED.journal_id, total_reclassed=EXCLUDED.total_reclassed,
                      created_at=NOW()
            """),
            {"eid": entity_id, "yr": year, "mo": month, "jid": je_result["journal_id"],
             "cnt": len(payroll_rows), "total": float(total_reclassed), "by": created_by}
        )
        self.db.commit()

        return {
            "status":          "posted",
            "journal_id":      je_result["journal_id"],
            "journal_no":      je_result["journal_no"],
            "period":          f"{year}-{month:02d}",
            "total_reclassed": float(total_reclassed),
            "employees_count": len(payroll_rows),
            "gl_lines":        len(je_lines),
        }

    def get_payroll_variance(self, entity_id: str, year: int, month: int) -> list[dict]:
        """
        Variance report: analytic estimate (timesheet × unit_cost_per_hour)
        vs actual payroll (bruto_bulanan dari employee_payroll_history).

        Positive variance = actual lebih mahal dari estimate (under-estimated cost).
        Negative variance = actual lebih murah (over-estimated, efficient).

        Return: per-employee, plus per-project distribution dari rasio timesheet.
        """
        payroll_rows = self.db.execute(
            text("""
                SELECT eph.employee_id, e.full_name AS employee_name,
                       eph.bruto_bulanan AS actual_payroll
                FROM employee_payroll_history eph
                JOIN employee e ON e.id = eph.employee_id
                WHERE eph.entity_id = :eid AND eph.year = :yr AND eph.month = :mo
                  AND eph.bruto_bulanan > 0
                ORDER BY e.full_name
            """),
            {"eid": entity_id, "yr": year, "mo": month}
        ).fetchall()

        if not payroll_rows:
            return []

        emp_ids = [str(r.employee_id) for r in payroll_rows]

        analytic_rows = self.db.execute(
            text("""
                SELECT
                    ajl.employee_id,
                    ajl.project_id,
                    p.project_code,
                    p.project_name,
                    ajl.cost_center,
                    SUM(ajl.billable_hours)     AS hours,
                    SUM(ajl.debit_amount)       AS estimate_cost
                FROM analytic_journal aj
                JOIN analytic_journal_line ajl ON ajl.journal_id = aj.id
                LEFT JOIN project p ON p.id = ajl.project_id
                WHERE aj.entity_id = :eid AND aj.year = :yr AND aj.month = :mo
                  AND aj.status = 'posted'
                  AND ajl.account_type IN ('direct_labor', 'idle_labor')
                  AND ajl.employee_id = ANY(CAST(:eids AS uuid[]))
                GROUP BY ajl.employee_id, ajl.project_id, p.project_code, p.project_name, ajl.cost_center
            """),
            {"eid": entity_id, "yr": year, "mo": month, "eids": emp_ids}
        ).fetchall()

        from collections import defaultdict
        analytic_by_emp: dict[str, list] = defaultdict(list)
        for r in analytic_rows:
            analytic_by_emp[str(r.employee_id)].append(r)

        result = []
        for pr in payroll_rows:
            emp_id       = str(pr.employee_id)
            actual       = Decimal(str(pr.actual_payroll))
            lines        = analytic_by_emp.get(emp_id, [])
            total_est    = sum(Decimal(str(l.estimate_cost)) for l in lines)
            variance     = actual - total_est
            total_hours  = sum(Decimal(str(l.hours or 0)) for l in lines)

            by_project = []
            for l in lines:
                est = Decimal(str(l.estimate_cost))
                ratio = est / total_est if total_est else Decimal("0")
                actual_alloc = (actual * ratio).quantize(Decimal("0.01"))
                by_project.append({
                    "project_code":  l.project_code or "BENCH",
                    "project_name":  l.project_name or "Bench/Internal",
                    "cost_center":   l.cost_center,
                    "hours":         float(l.hours or 0),
                    "estimate_cost": float(est),
                    "actual_cost":   float(actual_alloc),
                    "variance":      float(actual_alloc - est),
                })

            result.append({
                "employee_id":    emp_id,
                "employee_name":  pr.employee_name,
                "actual_payroll": float(actual),
                "estimate_cost":  float(total_est),
                "variance":       float(variance),
                "variance_pct":   round(float(variance / total_est * 100), 2) if total_est else None,
                "total_hours":    float(total_hours),
                "by_project":     by_project,
            })

        return result

    # ── 9. Reporting ──────────────────────────────────────────────────────────

    def get_project_pnl(self, entity_id: str, year: int, month: Optional[int] = None) -> list:
        """Laporan Laba Rugi Per Proyek dari analytic journal."""
        cond = "aj.entity_id = :eid AND aj.year = :yr AND aj.status = 'posted'"
        params: dict = {"eid": entity_id, "yr": year}
        if month:
            cond += " AND aj.month = :mo"
            params["mo"] = month

        rows = self.db.execute(
            text(f"""
                SELECT
                    aj.year, aj.month,
                    COALESCE(p.project_code, 'BENCH')  AS project_code,
                    COALESCE(p.project_name, 'Bench/Internal') AS project_name,
                    p.client_name,
                    COALESCE(SUM(ajl.credit_amount) FILTER (WHERE ajl.account_type = 'revenue'), 0) AS revenue,
                    COALESCE(SUM(ajl.debit_amount)  FILTER (WHERE ajl.account_type = 'direct_labor'), 0) AS direct_labor,
                    COALESCE(SUM(ajl.debit_amount)  FILTER (WHERE ajl.account_type = 'idle_labor'), 0) AS idle_labor,
                    COALESCE(SUM(ajl.debit_amount)  FILTER (WHERE ajl.account_type = 'overhead'), 0) AS overhead,
                    COALESCE(SUM(ajl.billable_hours) FILTER (WHERE ajl.account_type = 'direct_labor'), 0) AS billable_hours
                FROM analytic_journal aj
                JOIN analytic_journal_line ajl ON ajl.journal_id = aj.id
                LEFT JOIN project p ON p.id = ajl.project_id
                WHERE {cond}
                GROUP BY aj.year, aj.month, p.id, p.project_code, p.project_name, p.client_name
                ORDER BY aj.year, aj.month, p.project_code
            """),
            params
        ).fetchall()

        result = []
        for r in rows:
            rev  = float(r.revenue)
            dl   = float(r.direct_labor)
            oh   = float(r.overhead)
            il   = float(r.idle_labor)
            gp   = rev - dl - oh
            gp_pct = round(gp / rev * 100, 2) if rev > 0 else None
            result.append({
                "year":         r.year,
                "month":        r.month,
                "project_code": r.project_code,
                "project_name": r.project_name,
                "client_name":  r.client_name,
                "revenue":      rev,
                "direct_labor": dl,
                "idle_labor":   il,
                "overhead":     oh,
                "gross_profit": gp,
                "gross_margin_pct": gp_pct,
                "billable_hours": float(r.billable_hours),
            })
        return result

    def generate_invoice_from_timesheets(
        self,
        project_id: str,
        invoice_date: date,
        created_by: str,
        ppn_rate: int = 11,
        revenue_account_code: str = "4-1-001",
    ) -> dict:
        """
        PSA gap #2 (BRD timesheet 2026-06-24): tarik timesheet approved+billable+belum
        di-invoice jadi 1 draft AR invoice, 1 baris per karyawan (qty = SUM jam, rate dari
        project_assignment.billing_rate_override kalau ada, fallback project.billing_rate_per_hour).
        Timesheet TANPA rate (keduanya kosong) TIDAK ditagih dan TIDAK ditandai is_invoiced —
        supaya jam itu tetap "kelihatan" sebagai unbilled di batch berikutnya setelah rate diisi,
        bukan diam-diam hilang. Status invoice awal selalu 'draft' (belum posting jurnal) —
        konsisten dengan pola create_ar_invoice() di ar_router.py.
        """
        db = self.db
        project = db.execute(
            text("SELECT * FROM project WHERE id = :pid"), {"pid": project_id}
        ).fetchone()
        if not project:
            raise ValueError(f"Project {project_id} tidak ditemukan")
        if project.charter_status != "approved" or project.status != "active":
            raise ValueError(
                f"Project harus charter_status='approved' dan status='active' "
                f"(saat ini: charter_status='{project.charter_status}', status='{project.status}')"
            )

        rows = db.execute(
            text(
                "SELECT pts.id, pts.employee_id, pts.hours, e.full_name, "
                "  COALESCE(pa.billing_rate_override, p.billing_rate_per_hour) AS rate "
                "FROM project_timesheet pts "
                "JOIN employee e ON e.id = pts.employee_id "
                "JOIN project p  ON p.id = pts.project_id "
                "LEFT JOIN project_assignment pa "
                "  ON pa.project_id = pts.project_id AND pa.employee_id = pts.employee_id AND pa.is_active = TRUE "
                "WHERE pts.project_id = :pid AND pts.status = 'approved' "
                "  AND pts.activity_type = 'billable' AND pts.is_invoiced = FALSE"
            ),
            {"pid": project_id},
        ).fetchall()

        if not rows:
            return {"status": "skipped", "reason": "Tidak ada timesheet approved+billable yang belum di-invoice"}

        by_employee: dict[str, dict] = {}
        unrated_employee_names: set[str] = set()
        for r in rows:
            if r.rate is None:
                unrated_employee_names.add(r.full_name)
                continue
            key = str(r.employee_id)
            entry = by_employee.setdefault(key, {
                "full_name": r.full_name, "hours": Decimal("0"),
                "rate": Decimal(str(r.rate)), "ts_ids": [],
            })
            entry["hours"] += Decimal(str(r.hours))
            entry["ts_ids"].append(str(r.id))

        if not by_employee:
            return {
                "status": "skipped",
                "reason": "Semua timesheet unbilled milik karyawan tanpa billing rate",
                "unrated_employees": sorted(unrated_employee_names),
            }

        entity_id  = str(project.entity_id)
        invoice_no = _gen_invoice_no(db, entity_id)

        lines: list[dict] = []
        ts_ids_to_mark: list[str] = []
        subtotal = Decimal("0")
        for info in by_employee.values():
            amount = (info["hours"] * info["rate"]).quantize(Decimal("0.01"))
            subtotal += amount
            ts_ids_to_mark.extend(info["ts_ids"])
            lines.append({
                "description": f"Jasa konsultansi — {info['full_name']} ({info['hours']}j x Rp{info['rate']:,.0f})",
                "amount": amount,
            })

        ppn_amount   = (subtotal * Decimal(str(ppn_rate)) / 100).quantize(Decimal("1"))
        total_amount = subtotal + ppn_amount

        invoice_id = str(uuid4())
        db.execute(
            text(
                "INSERT INTO ar_invoice "
                "(id, entity_id, customer_name, invoice_no, invoice_date, "
                " subtotal, ppn_amount, total_amount, paid_amount, status, "
                " generated_by, cost_center, project_id, currency, exchange_rate) "
                "VALUES (:id, :eid, :cname, :ino, :idate, "
                "        :sub, :ppn, :total, 0, 'draft', "
                "        'timesheet_billing', :cc, :pid, 'IDR', 1)"
            ),
            {
                "id": invoice_id, "eid": entity_id,
                "cname": project.client_name or project.project_name,
                "ino": invoice_no, "idate": invoice_date,
                "sub": float(subtotal), "ppn": float(ppn_amount), "total": float(total_amount),
                "cc": project.cost_center, "pid": project_id,
            },
        )
        for i, line in enumerate(lines, start=1):
            db.execute(
                text(
                    "INSERT INTO ar_invoice_line "
                    "(ar_invoice_id, line_no, account_code, description, amount, cost_center, project_id) "
                    "VALUES (:iid, :ln, :acc, :desc, :amt, :cc, :pid)"
                ),
                {
                    "iid": invoice_id, "ln": i, "acc": revenue_account_code,
                    "desc": line["description"], "amt": float(line["amount"]),
                    "cc": project.cost_center, "pid": project_id,
                },
            )

        db.execute(
            text("UPDATE project_timesheet SET is_invoiced = TRUE, ar_invoice_id = :iid WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"iid": invoice_id, "ids": ts_ids_to_mark},
        )
        db.commit()

        return {
            "status":           "created",
            "invoice_id":       invoice_id,
            "invoice_no":       invoice_no,
            "subtotal":         float(subtotal),
            "ppn_amount":       float(ppn_amount),
            "total_amount":     float(total_amount),
            "line_count":       len(lines),
            "timesheets_billed": len(ts_ids_to_mark),
            "unrated_employees": sorted(unrated_employee_names),
        }

    def get_corporate_comparison(self, entity_id: str, year: int, month: int) -> dict:
        """
        Perbandingan side-by-side (BAB II §3):
            Luaran A: Corporate P&L (dari GL)
            Luaran B: Project P&L per segmen (dari analytic)
        """
        start_date = date(year, month, 1)
        end_date   = _last_day(year, month)

        # Corporate P&L dari GL
        gl_rows = self.db.execute(
            text("""
                SELECT
                    coa.account_type,
                    coa.account_name,
                    COALESCE(SUM(jl.credit_amount - jl.debit_amount), 0) AS amount
                FROM gl_journal_line jl
                JOIN gl_journal j    ON j.id = jl.journal_id
                JOIN chart_of_accounts coa ON coa.account_code = jl.account_code
                    AND coa.entity_id = j.entity_id
                WHERE j.entity_id  = :eid
                  AND j.status     = 'posted'
                  AND j.journal_date BETWEEN :start AND :end
                  AND coa.account_type IN ('revenue', 'expense')
                GROUP BY coa.account_type, coa.account_name
                ORDER BY coa.account_type, coa.account_name
            """),
            {"eid": entity_id, "start": start_date, "end": end_date}
        ).fetchall()

        corporate = {"revenue_lines": [], "expense_lines": []}
        total_rev_corp = Decimal("0")
        total_exp_corp = Decimal("0")
        for r in gl_rows:
            amt = float(r.amount)
            if r.account_type == "revenue":
                corporate["revenue_lines"].append({"account": r.account_name, "amount": amt})
                total_rev_corp += Decimal(str(amt))
            else:
                corporate["expense_lines"].append({"account": r.account_name, "amount": abs(amt)})
                total_exp_corp += abs(Decimal(str(amt)))

        corporate["total_revenue"] = float(total_rev_corp)
        corporate["total_expense"] = float(total_exp_corp)
        corporate["net_profit"]    = float(total_rev_corp - total_exp_corp)

        # Project breakdown dari analytic
        project_rows = self.get_project_pnl(entity_id, year, month)
        total_rev_proj  = sum(r["revenue"] for r in project_rows)
        total_dl_proj   = sum(r["direct_labor"] for r in project_rows)
        total_il_proj   = sum(r["idle_labor"] for r in project_rows)
        total_oh_proj   = sum(r["overhead"] for r in project_rows)
        total_gp_proj   = sum(r["gross_profit"] for r in project_rows)

        return {
            "period":           f"{year}-{month:02d}",
            "entity_id":        entity_id,
            "corporate_pnl":    corporate,
            "project_breakdown": project_rows,
            "project_summary": {
                "total_revenue":    total_rev_proj,
                "total_direct_labor": total_dl_proj,
                "total_idle_labor": total_il_proj,
                "total_overhead":   total_oh_proj,
                "total_gross_profit": total_gp_proj,
                "gross_margin_pct": round(total_gp_proj / total_rev_proj * 100, 2) if total_rev_proj > 0 else None,
            },
            "reconciliation": {
                "corporate_net_profit":        float(total_rev_corp - total_exp_corp),
                "analytic_net_profit":          total_gp_proj,
                "note": "Perbedaan kecil wajar karena analytic menggunakan unit cost (CTC-based), "
                        "bukan actual payroll. Selisih adalah labor cost variance."
            }
        }

    def get_labor_utilization(self, entity_id: str, year: int, month: Optional[int] = None) -> list:
        """Laporan utilisasi jam konsultan (billable vs bench)."""
        cond = "aj.entity_id = :eid AND aj.year = :yr AND aj.status = 'posted' AND aj.journal_type = 'labor_allocation'"
        params: dict = {"eid": entity_id, "yr": year}
        if month:
            cond += " AND aj.month = :mo"
            params["mo"] = month

        rows = self.db.execute(
            text(f"""
                SELECT
                    aj.year, aj.month,
                    ajl.employee_id,
                    e.full_name AS employee_name,
                    e.job_title,
                    COALESCE(SUM(ajl.billable_hours) FILTER (WHERE ajl.account_type = 'direct_labor'), 0) AS billable_hours,
                    COALESCE(SUM(ajl.billable_hours) FILTER (WHERE ajl.account_type = 'idle_labor'), 0) AS bench_hours,
                    COALESCE(SUM(ajl.billable_hours), 0) AS total_hours,
                    COALESCE(SUM(ajl.debit_amount), 0) AS total_cost,
                    AVG(ajl.unit_cost_per_hour) AS unit_cost_per_hour
                FROM analytic_journal aj
                JOIN analytic_journal_line ajl ON ajl.journal_id = aj.id
                JOIN employee e ON e.id = ajl.employee_id
                WHERE {cond} AND ajl.employee_id IS NOT NULL
                GROUP BY aj.year, aj.month, ajl.employee_id, e.full_name, e.job_title
                ORDER BY aj.year, aj.month, e.full_name
            """),
            params
        ).fetchall()

        return [
            {
                "year":            r.year,
                "month":           r.month,
                "employee_id":     str(r.employee_id),
                "employee_name":   r.employee_name,
                "job_title":       r.job_title,
                "billable_hours":  float(r.billable_hours),
                "bench_hours":     float(r.bench_hours),
                "total_hours":     float(r.total_hours),
                "utilization_pct": round(float(r.billable_hours) / float(r.total_hours) * 100, 2)
                                   if float(r.total_hours) > 0 else 0,
                "total_cost":      float(r.total_cost),
                "unit_cost_per_hour": float(r.unit_cost_per_hour) if r.unit_cost_per_hour else None,
            }
            for r in rows
        ]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _count_weekends(year: int) -> int:
    start  = date(year, 1, 1)
    total  = 366 if isleap(year) else 365
    fw     = total // 7
    extra  = total % 7
    wkends = fw * 2
    wd0    = start.weekday()  # 0=Monday
    for i in range(extra):
        if (wd0 + i) % 7 >= 5:
            wkends += 1
    return wkends


def _last_day(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _gen_analytic_no(db) -> str:
    now    = datetime.now()
    prefix = f"AJ/{now.year}/{now.month:02d}"
    count  = db.execute(
        text("SELECT COUNT(*) FROM analytic_journal WHERE journal_no LIKE :p"),
        {"p": f"{prefix}/%"}
    ).scalar()
    return f"{prefix}/{(count or 0) + 1:04d}"


def _get_or_create_analytic_period(db, entity_id: str, year: int, month: int) -> dict:
    row = db.execute(
        text("SELECT * FROM analytic_period WHERE entity_id = :eid AND year = :yr AND month = :mo"),
        {"eid": entity_id, "yr": year, "mo": month}
    ).fetchone()
    if row:
        return dict(row._mapping)

    db.execute(
        text("""
            INSERT INTO analytic_period (id, entity_id, year, month, status)
            VALUES (uuid_generate_v4(), :eid, :yr, :mo, 'open')
            ON CONFLICT (entity_id, year, month) DO NOTHING
        """),
        {"eid": entity_id, "yr": year, "mo": month}
    )
    db.commit()
    row = db.execute(
        text("SELECT * FROM analytic_period WHERE entity_id = :eid AND year = :yr AND month = :mo"),
        {"eid": entity_id, "yr": year, "mo": month}
    ).fetchone()
    return dict(row._mapping)


def _check_period_not_locked(db, entity_id: str, year: int, month: int):
    row = db.execute(
        text("SELECT status FROM analytic_period WHERE entity_id = :eid AND year = :yr AND month = :mo"),
        {"eid": entity_id, "yr": year, "mo": month}
    ).fetchone()
    if row and row.status == "locked":
        raise ValueError(
            f"Periode analitik {year}-{month:02d} sudah LOCKED. "
            "Hubungi Financial Controller untuk membuka kunci."
        )
