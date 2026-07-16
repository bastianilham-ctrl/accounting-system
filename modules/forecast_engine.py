"""
FP&A Engine — Three-Way Financial Forecasting
Supports: Perusahaan Jasa / Dagang / Konstruksi

Flow:
  create_scenario() → set_assumptions() → run_forecast()
    ├── _pull_actuals()              : 12 bulan data GL nyata
    ├── _forecast_pl_jasa/dagang/konstruksi()  : proyeksi P&L per tipe
    ├── _build_cashflow()            : indirect method + DSO/DPO/DIO timing
    └── _build_balance_sheet()       : rolling BS dari opening + perubahan
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


# ── Defaults per Company Type ─────────────────────────────────────────────────

DEFAULTS_JASA = {
    "dso_days":             Decimal("45"),
    "revenue_growth_pct":   Decimal("5"),
    "labor_growth_pct":     Decimal("8"),
    "benefits_pct":         Decimal("15"),      # BPJS + tunjangan
    "win_rate_pct":         Decimal("35"),
    "income_tax_rate_pct":  Decimal("22"),
    "overhead_growth_pct":  Decimal("5"),
    "dpo_days":             Decimal("30"),
    "capex_monthly_avg":    Decimal("0"),
    "escalation_pct_pa":    Decimal("3"),
}

DEFAULTS_DAGANG = {
    "dso_days":             Decimal("30"),
    "dpo_days":             Decimal("45"),
    "dio_days":             Decimal("60"),
    "gross_margin_pct":     Decimal("30"),
    "revenue_growth_pct":   Decimal("5"),
    "cogs_inflation_pct":   Decimal("4"),
    "income_tax_rate_pct":  Decimal("22"),
    "overhead_growth_pct":  Decimal("5"),
    "safety_stock_days":    Decimal("14"),
    "capex_monthly_avg":    Decimal("0"),
}

DEFAULTS_KONSTRUKSI = {
    "retention_release_months":  Decimal("6"),
    "cost_overrun_buffer_pct":   Decimal("5"),
    "subcon_dpo_days":           Decimal("30"),
    "income_tax_rate_pct":       Decimal("22"),
    "overhead_growth_pct":       Decimal("5"),
    "dpo_days":                  Decimal("30"),
    "capex_monthly_avg":         Decimal("0"),
    "sinking_cash_alert_pct":    Decimal("10"),  # alert jika sisa CTC < 10% total RAB
}


# ── Line Code Catalogue ────────────────────────────────────────────────────────
# P&L
PL_LINES = [
    ("REV_CONTRACT",   "Pendapatan Kontrak Backlog",    10, "revenue"),
    ("REV_PIPELINE",   "Pendapatan Pipeline (Win-Rate)", 11, "revenue"),
    ("REV_RECURRING",  "Pendapatan Recurring / Retainer",12, "revenue"),
    ("REV_TOTAL",      "Total Pendapatan",               15, "revenue"),
    ("COGS_DIRECT",    "HPP / Biaya Langsung",           20, "cogs"),
    ("COGS_MATERIAL",  "Biaya Material / Barang",        21, "cogs"),
    ("COGS_TOTAL",     "Total HPP",                      25, "cogs"),
    ("GROSS_PROFIT",   "Laba Kotor",                     30, "gross_profit"),
    ("OPEX_LABOR",     "Biaya Tenaga Kerja",             40, "opex"),
    ("OPEX_RENT",      "Sewa & Utilitas",                41, "opex"),
    ("OPEX_DEPR",      "Penyusutan & Amortisasi",        42, "opex"),
    ("OPEX_MKTG",      "Biaya Pemasaran & Penjualan",    43, "opex"),
    ("OPEX_ADMIN",     "Biaya Umum & Administrasi",      44, "opex"),
    ("OPEX_OTHER",     "Biaya Operasional Lainnya",      45, "opex"),
    ("OPEX_TOTAL",     "Total Biaya Operasional",        49, "opex"),
    ("EBITDA",         "EBITDA",                         50, "ebitda"),
    ("EBIT",           "EBIT (Laba Operasi)",            55, "ebit"),
    ("INTEREST_INC",   "Pendapatan Bunga",               60, "financial"),
    ("INTEREST_EXP",   "Beban Bunga",                    61, "financial"),
    ("EBT",            "Laba Sebelum Pajak",             65, "ebt"),
    ("TAX_INCOME",     "Pajak Penghasilan",              70, "tax"),
    ("NET_INCOME",     "Laba Bersih",                    80, "net_income"),
]

# Cash Flow (indirect method)
CF_LINES = [
    ("CF_OP_NI",       "Laba Bersih",                    10, "operating"),
    ("CF_OP_DEPR",     "Penyusutan (ditambahkan kembali)",11, "operating"),
    ("CF_OP_AR",       "Perubahan Piutang Usaha (DSO)",   12, "operating"),
    ("CF_OP_INV",      "Perubahan Persediaan (DIO)",      13, "operating"),
    ("CF_OP_AP",       "Perubahan Hutang Usaha (DPO)",    14, "operating"),
    ("CF_OP_WC_OTHER", "Perubahan Modal Kerja Lainnya",   15, "operating"),
    ("CF_OP_TOTAL",    "Total Arus Kas Operasi",          19, "operating"),
    ("CF_IV_CAPEX",    "Belanja Modal (CAPEX)",           30, "investing"),
    ("CF_IV_OTHER",    "Investasi Lainnya",               31, "investing"),
    ("CF_IV_TOTAL",    "Total Arus Kas Investasi",        39, "investing"),
    ("CF_FIN_DEBT",    "Penerimaan / Pembayaran Hutang",  50, "financing"),
    ("CF_FIN_EQUITY",  "Setoran / Distribusi Modal",      51, "financing"),
    ("CF_FIN_TOTAL",   "Total Arus Kas Pendanaan",        59, "financing"),
    ("CF_NET",         "Kenaikan (Penurunan) Kas Neto",   60, "net"),
    ("CF_OPEN",        "Saldo Kas Awal",                  61, "balance"),
    ("CF_CLOSE",       "Saldo Kas Akhir",                 62, "balance"),
]

# Balance Sheet
BS_LINES = [
    # Aset Lancar
    ("BS_CASH",        "Kas & Setara Kas",               10, "current_asset"),
    ("BS_AR",          "Piutang Usaha",                  11, "current_asset"),
    ("BS_INVENTORY",   "Persediaan",                     12, "current_asset"),
    ("BS_PREPAID",     "Biaya Dibayar Dimuka",            13, "current_asset"),
    ("BS_OTHER_CA",    "Aset Lancar Lainnya",             14, "current_asset"),
    ("BS_TOTAL_CA",    "Total Aset Lancar",               19, "current_asset"),
    # Aset Tidak Lancar
    ("BS_FA_GROSS",    "Aset Tetap Bruto",               20, "fixed_asset"),
    ("BS_FA_ACCDEPR",  "Akumulasi Penyusutan",           21, "fixed_asset"),
    ("BS_FA_NET",      "Aset Tetap Neto",                22, "fixed_asset"),
    ("BS_INTANGIBLE",  "Aset Tidak Berwujud",            23, "fixed_asset"),
    ("BS_TOTAL_NCA",   "Total Aset Tidak Lancar",        29, "fixed_asset"),
    ("BS_TOTAL_ASSET", "Total Aset",                     30, "total_asset"),
    # Kewajiban Lancar
    ("BS_AP",          "Hutang Usaha",                   40, "current_liab"),
    ("BS_ACCRUED",     "Hutang Akrual",                  41, "current_liab"),
    ("BS_SHORT_DEBT",  "Hutang Bank Jangka Pendek",      42, "current_liab"),
    ("BS_TAX_PAYABLE", "Hutang Pajak",                   43, "current_liab"),
    ("BS_TOTAL_CL",    "Total Kewajiban Lancar",         49, "current_liab"),
    # Kewajiban Jangka Panjang
    ("BS_LONG_DEBT",   "Hutang Bank Jangka Panjang",     50, "long_liab"),
    ("BS_TOTAL_LTL",   "Total Kewajiban Jangka Panjang", 59, "long_liab"),
    ("BS_TOTAL_LIAB",  "Total Kewajiban",                60, "total_liab"),
    # Ekuitas
    ("BS_EQUITY_PAID", "Modal Disetor",                  70, "equity"),
    ("BS_RE",          "Laba Ditahan",                   71, "equity"),
    ("BS_NET_INCOME",  "Laba Bersih Tahun Berjalan",     72, "equity"),
    ("BS_TOTAL_EQ",    "Total Ekuitas",                  79, "equity"),
    ("BS_TOTAL_LE",    "Total Kewajiban + Ekuitas",      80, "check"),
]


# ── Helper dataclass ───────────────────────────────────────────────────────────
@dataclass
class ForecastPeriod:
    year: int
    month: int

    @property
    def label(self) -> str:
        return f"{self.year}-{self.month:02d}"


@dataclass
class PLSnapshot:
    period: ForecastPeriod
    revenue: Decimal = Decimal("0")
    cogs: Decimal = Decimal("0")
    labor: Decimal = Decimal("0")
    rent: Decimal = Decimal("0")
    depreciation: Decimal = Decimal("0")
    marketing: Decimal = Decimal("0")
    admin: Decimal = Decimal("0")
    other_opex: Decimal = Decimal("0")
    interest_income: Decimal = Decimal("0")
    interest_expense: Decimal = Decimal("0")
    tax: Decimal = Decimal("0")
    driver_source: str = "trend"

    @property
    def gross_profit(self) -> Decimal:
        return self.revenue - self.cogs

    @property
    def opex_total(self) -> Decimal:
        return self.labor + self.rent + self.depreciation + self.marketing + self.admin + self.other_opex

    @property
    def ebitda(self) -> Decimal:
        return self.gross_profit - (self.opex_total - self.depreciation)

    @property
    def ebit(self) -> Decimal:
        return self.gross_profit - self.opex_total

    @property
    def ebt(self) -> Decimal:
        return self.ebit + self.interest_income - self.interest_expense

    @property
    def net_income(self) -> Decimal:
        return self.ebt - self.tax


# ── Main Engine ────────────────────────────────────────────────────────────────
class ForecastEngine:

    # ── Scenario Management ───────────────────────────────────────────────────

    @staticmethod
    def create_scenario(
        db: Session,
        entity_id: UUID,
        scenario_name: str,
        company_type: str,
        as_of_date: date,
        forecast_months: int = 12,
        scenario_type: str = "baseline",
        base_scenario_id: Optional[UUID] = None,
        created_by: str = "",
    ) -> dict:
        existing = db.execute(
            text("SELECT id FROM forecast_scenario WHERE entity_id=:eid AND scenario_name=:nm AND as_of_date=:dt"),
            {"eid": str(entity_id), "nm": scenario_name, "dt": str(as_of_date)},
        ).fetchone()
        if existing:
            raise ValueError(f"Scenario '{scenario_name}' tanggal {as_of_date} sudah ada.")

        row = db.execute(
            text("""
                INSERT INTO forecast_scenario
                    (entity_id, scenario_name, scenario_type, company_type,
                     as_of_date, forecast_months, base_scenario_id, created_by)
                VALUES (:eid, :nm, :stype, :ctype, :dt, :months, :base, :by)
                RETURNING id
            """),
            {
                "eid":    str(entity_id),
                "nm":     scenario_name,
                "stype":  scenario_type,
                "ctype":  company_type,
                "dt":     str(as_of_date),
                "months": forecast_months,
                "base":   str(base_scenario_id) if base_scenario_id else None,
                "by":     created_by,
            },
        ).fetchone()
        db.commit()
        return {"scenario_id": str(row.id), "scenario_name": scenario_name, "status": "draft"}

    @staticmethod
    def set_assumptions(
        db: Session,
        scenario_id: UUID,
        assumptions: dict[str, float],
    ) -> dict:
        scenario = db.execute(
            text("SELECT company_type FROM forecast_scenario WHERE id=:sid"),
            {"sid": str(scenario_id)},
        ).fetchone()
        if not scenario:
            raise ValueError("Scenario tidak ditemukan.")

        # Merge with defaults
        defaults = {
            "jasa":       DEFAULTS_JASA,
            "dagang":     DEFAULTS_DAGANG,
            "konstruksi": DEFAULTS_KONSTRUKSI,
        }.get(scenario.company_type, DEFAULTS_JASA)

        merged = {k: float(v) for k, v in defaults.items()}
        merged.update(assumptions)

        for key, val in merged.items():
            db.execute(
                text("""
                    INSERT INTO forecast_assumption (scenario_id, param_key, param_value)
                    VALUES (:sid, :key, :val)
                    ON CONFLICT (scenario_id, param_key)
                    DO UPDATE SET param_value = EXCLUDED.param_value
                """),
                {"sid": str(scenario_id), "key": key, "val": val},
            )
        db.commit()
        return {"scenario_id": str(scenario_id), "assumptions_set": len(merged)}

    @staticmethod
    def _get_assumptions(db: Session, scenario_id: UUID) -> dict[str, Decimal]:
        rows = db.execute(
            text("SELECT param_key, param_value FROM forecast_assumption WHERE scenario_id=:sid"),
            {"sid": str(scenario_id)},
        ).fetchall()
        return {r.param_key: Decimal(str(r.param_value)) for r in rows}

    @staticmethod
    def _get_forecast_periods(as_of_date: date, n_months: int) -> list[ForecastPeriod]:
        periods = []
        y, m = as_of_date.year, as_of_date.month
        for _ in range(n_months):
            periods.append(ForecastPeriod(y, m))
            m += 1
            if m > 12:
                m = 1
                y += 1
        return periods

    # ── Actuals Pull ──────────────────────────────────────────────────────────

    @staticmethod
    def _pull_actuals(db: Session, entity_id: UUID, as_of_date: date, lookback: int = 12) -> dict:
        """Pull monthly P&L actuals for last N months from GL."""
        # Monthly revenue
        rev_rows = db.execute(
            text("""
                SELECT
                    EXTRACT(YEAR  FROM gj.journal_date)::int  AS yr,
                    EXTRACT(MONTH FROM gj.journal_date)::int  AS mo,
                    COALESCE(SUM(gl.credit_idr - gl.debit_idr), 0)  AS net_revenue
                FROM gl_line gl
                JOIN gl_journal gj         ON gj.id = gl.journal_id AND gj.status = 'posted'
                JOIN chart_of_accounts coa ON coa.id = gl.account_id
                WHERE gj.entity_id = :eid
                  AND coa.account_type = 'revenue'
                  AND gj.journal_date >= (DATE_TRUNC('month', :dt::date) - INTERVAL ':lookback months')
                  AND gj.journal_date <  DATE_TRUNC('month', :dt::date)
                GROUP BY yr, mo
                ORDER BY yr, mo
            """),
            {"eid": str(entity_id), "dt": str(as_of_date), "lookback": lookback},
        ).fetchall()

        # Monthly expense (by account_type breakdown)
        exp_rows = db.execute(
            text("""
                SELECT
                    EXTRACT(YEAR  FROM gj.journal_date)::int AS yr,
                    EXTRACT(MONTH FROM gj.journal_date)::int AS mo,
                    coa.account_subtype,
                    COALESCE(SUM(gl.debit_idr - gl.credit_idr), 0) AS net_expense
                FROM gl_line gl
                JOIN gl_journal gj         ON gj.id = gl.journal_id AND gj.status = 'posted'
                JOIN chart_of_accounts coa ON coa.id = gl.account_id
                WHERE gj.entity_id = :eid
                  AND coa.account_type = 'expense'
                  AND gj.journal_date >= (DATE_TRUNC('month', :dt::date) - INTERVAL ':lookback months')
                  AND gj.journal_date <  DATE_TRUNC('month', :dt::date)
                GROUP BY yr, mo, coa.account_subtype
                ORDER BY yr, mo
            """),
            {"eid": str(entity_id), "dt": str(as_of_date), "lookback": lookback},
        ).fetchall()

        monthly_rev: dict[tuple, Decimal] = {}
        for r in rev_rows:
            monthly_rev[(r.yr, r.mo)] = Decimal(str(r.net_revenue))

        monthly_exp: dict[tuple, dict] = {}
        for r in exp_rows:
            k = (r.yr, r.mo)
            if k not in monthly_exp:
                monthly_exp[k] = {}
            sub = r.account_subtype or "other"
            monthly_exp[k][sub] = Decimal(str(r.net_expense))

        # Compute averages
        rev_values = list(monthly_rev.values()) or [Decimal("0")]
        avg_revenue = sum(rev_values) / len(rev_values)

        return {
            "monthly_revenue":    monthly_rev,
            "monthly_expense":    monthly_exp,
            "avg_monthly_revenue": avg_revenue,
            "history_months":     len(rev_values),
        }

    # ── Perusahaan JASA — Forecast ────────────────────────────────────────────

    @staticmethod
    def _forecast_pl_jasa(
        db: Session,
        entity_id: UUID,
        assumptions: dict[str, Decimal],
        actuals: dict,
        periods: list[ForecastPeriod],
    ) -> list[PLSnapshot]:
        dso          = assumptions.get("dso_days",           Decimal("45"))
        rev_growth   = assumptions.get("revenue_growth_pct", Decimal("5")) / 100
        labor_growth = assumptions.get("labor_growth_pct",   Decimal("8")) / 100
        benefits_pct = assumptions.get("benefits_pct",       Decimal("15")) / 100
        win_rate     = assumptions.get("win_rate_pct",        Decimal("35")) / 100
        tax_rate     = assumptions.get("income_tax_rate_pct", Decimal("22")) / 100
        oh_growth    = assumptions.get("overhead_growth_pct", Decimal("5")) / 100

        # Pull contract backlog (uninvoiced milestones)
        backlog_rows = db.execute(
            text("""
                SELECT
                    cm.due_date,
                    cm.milestone_amount * (1 - COALESCE(cm.retention_pct, 0) / 100) AS net_amount
                FROM contract_milestone cm
                JOIN project_contract pc ON pc.id = cm.contract_id
                WHERE pc.entity_id = :eid
                  AND cm.billing_status IN ('PENDING', 'PARTIAL')
                  AND pc.status = 'ACTIVE'
                ORDER BY cm.due_date
            """),
            {"eid": str(entity_id)},
        ).fetchall()

        # Map backlog to period
        backlog_by_period: dict[tuple, Decimal] = {}
        for r in backlog_rows:
            if r.due_date:
                k = (r.due_date.year, r.due_date.month)
                backlog_by_period[k] = backlog_by_period.get(k, Decimal("0")) + Decimal(str(r.net_amount))

        # Pull pipeline projects (win_rate applied)
        pipeline_rows = db.execute(
            text("""
                SELECT
                    p.expected_revenue,
                    p.start_date
                FROM project p
                WHERE p.entity_id = :eid
                  AND p.status IN ('planning', 'on_hold')
                  AND p.expected_revenue IS NOT NULL
                  AND p.start_date IS NOT NULL
                ORDER BY p.start_date
            """),
            {"eid": str(entity_id)},
        ).fetchall()

        pipeline_by_period: dict[tuple, Decimal] = {}
        for r in pipeline_rows:
            if r.start_date:
                k = (r.start_date.year, r.start_date.month)
                pipeline_by_period[k] = (
                    pipeline_by_period.get(k, Decimal("0"))
                    + Decimal(str(r.expected_revenue or "0")) * win_rate
                )

        # Pull headcount & avg salary
        emp_row = db.execute(
            text("""
                SELECT
                    COUNT(*)               AS headcount,
                    COALESCE(AVG(basic_salary), 0) AS avg_salary
                FROM employee
                WHERE entity_id = :eid AND employment_status = 'active'
            """),
            {"eid": str(entity_id)},
        ).fetchone()
        headcount = int(emp_row.headcount) if emp_row else 0
        avg_salary = Decimal(str(emp_row.avg_salary)) if emp_row else Decimal("0")
        monthly_labor = avg_salary * headcount * (1 + benefits_pct)

        # Pull overhead from actuals (rent/depr/admin)
        avg_overhead = Decimal("0")
        if actuals["monthly_expense"]:
            oh_total = Decimal("0")
            n = 0
            for _, exp_dict in actuals["monthly_expense"].items():
                oh_total += exp_dict.get("overhead", Decimal("0")) + exp_dict.get("admin", Decimal("0"))
                n += 1
            if n:
                avg_overhead = oh_total / n

        base_revenue = actuals["avg_monthly_revenue"]

        snapshots = []
        for i, period in enumerate(periods):
            k = (period.year, period.month)
            month_factor = (1 + rev_growth) ** (i / 12)
            labor_factor = (1 + labor_growth) ** (i / 12)

            contracted = backlog_by_period.get(k, Decimal("0"))
            pipeline   = pipeline_by_period.get(k, Decimal("0"))
            recurring  = base_revenue * month_factor

            revenue = contracted + pipeline + recurring

            # COGS for jasa = direct labor on billable projects (50% of labor)
            direct_labor_cost = monthly_labor * labor_factor * Decimal("0.5")
            cogs = direct_labor_cost

            snap = PLSnapshot(
                period=period,
                revenue=revenue,
                cogs=cogs,
                labor=monthly_labor * labor_factor * Decimal("0.5"),   # indirect labor
                rent=avg_overhead * Decimal("0.4"),
                depreciation=avg_overhead * Decimal("0.2"),
                admin=avg_overhead * Decimal("0.4") * (1 + oh_growth) ** (i / 12),
                driver_source="contract_backlog+pipeline+trend",
            )
            ebt = snap.ebt
            snap.tax = max(Decimal("0"), ebt * tax_rate)
            snapshots.append(snap)

        return snapshots

    # ── Perusahaan DAGANG — Forecast ──────────────────────────────────────────

    @staticmethod
    def _forecast_pl_dagang(
        db: Session,
        entity_id: UUID,
        assumptions: dict[str, Decimal],
        actuals: dict,
        periods: list[ForecastPeriod],
    ) -> list[PLSnapshot]:
        rev_growth  = assumptions.get("revenue_growth_pct", Decimal("5")) / 100
        gm_pct      = assumptions.get("gross_margin_pct",   Decimal("30")) / 100
        cogs_infl   = assumptions.get("cogs_inflation_pct", Decimal("4")) / 100
        tax_rate    = assumptions.get("income_tax_rate_pct", Decimal("22")) / 100
        oh_growth   = assumptions.get("overhead_growth_pct", Decimal("5")) / 100

        base_revenue = actuals["avg_monthly_revenue"]
        # Seasonal index: use actual monthly revenue vs average
        seasonal: dict[int, Decimal] = {}
        if actuals["monthly_revenue"]:
            for (yr, mo), rev in actuals["monthly_revenue"].items():
                if base_revenue > 0:
                    idx = rev / base_revenue
                    if mo not in seasonal:
                        seasonal[mo] = []
                    seasonal[mo].append(idx)
            seasonal = {
                mo: sum(vals) / len(vals)
                for mo, vals in seasonal.items()
            }

        # Headcount cost
        emp_row = db.execute(
            text("""
                SELECT COUNT(*) AS headcount,
                       COALESCE(AVG(basic_salary), 0) AS avg_salary
                FROM employee
                WHERE entity_id = :eid AND employment_status = 'active'
            """),
            {"eid": str(entity_id)},
        ).fetchone()
        monthly_labor = (
            Decimal(str(emp_row.avg_salary)) * int(emp_row.headcount)
            if emp_row else Decimal("0")
        )

        # Historical overhead average
        avg_oh = _avg_expense_category(actuals["monthly_expense"], ["overhead", "admin", "rent"])

        snapshots = []
        for i, period in enumerate(periods):
            month_factor = (1 + rev_growth) ** (i / 12)
            cogs_factor  = (1 + cogs_infl)  ** (i / 12)
            oh_factor    = (1 + oh_growth)  ** (i / 12)
            seasonal_idx = seasonal.get(period.month, Decimal("1"))

            revenue = base_revenue * month_factor * seasonal_idx
            cogs    = revenue * (1 - gm_pct) * cogs_factor

            snap = PLSnapshot(
                period=period,
                revenue=revenue,
                cogs=cogs,
                labor=monthly_labor * oh_factor,
                rent=avg_oh * Decimal("0.3") * oh_factor,
                depreciation=avg_oh * Decimal("0.15"),
                marketing=revenue * Decimal("0.03"),
                admin=avg_oh * Decimal("0.55") * oh_factor,
                driver_source="trend+seasonality",
            )
            snap.tax = max(Decimal("0"), snap.ebt * tax_rate)
            snapshots.append(snap)

        return snapshots

    # ── Perusahaan KONSTRUKSI — Forecast ──────────────────────────────────────

    @staticmethod
    def _forecast_pl_konstruksi(
        db: Session,
        entity_id: UUID,
        assumptions: dict[str, Decimal],
        actuals: dict,
        periods: list[ForecastPeriod],
    ) -> list[PLSnapshot]:
        tax_rate   = assumptions.get("income_tax_rate_pct",   Decimal("22")) / 100
        oh_growth  = assumptions.get("overhead_growth_pct",   Decimal("5")) / 100
        buf_pct    = assumptions.get("cost_overrun_buffer_pct", Decimal("5")) / 100

        # Active construction projects
        proj_rows = db.execute(
            text("""
                SELECT
                    pc.id,
                    pc.contract_name,
                    pc.total_value,
                    pc.start_date,
                    pc.end_date,
                    COALESCE(pc.retention_pct, 0)          AS retention_pct,
                    COALESCE(
                        (SELECT SUM(wi.planned_cost)
                         FROM wbs_item wi
                         WHERE wi.project_id = pc.project_id), 0
                    )                                       AS rab_total,
                    COALESCE(
                        (SELECT SUM(
                            CASE WHEN gj.status='posted' THEN gl.debit_idr ELSE 0 END
                         )
                         FROM project_task pt
                         JOIN wbs_item wi     ON wi.id = pt.wbs_item_id
                         LEFT JOIN gl_line gl ON gl.cost_center_id = pt.id
                         LEFT JOIN gl_journal gj ON gj.id = gl.journal_id
                         WHERE wi.project_id = pc.project_id), 0
                    )                                       AS actual_cost_to_date
                FROM project_contract pc
                WHERE pc.entity_id = :eid
                  AND pc.status IN ('ACTIVE', 'DRAFT')
                ORDER BY pc.start_date
            """),
            {"eid": str(entity_id)},
        ).fetchall()

        # Milestone billing schedule
        milestone_rows = db.execute(
            text("""
                SELECT
                    cm.contract_id,
                    cm.due_date,
                    cm.milestone_amount,
                    cm.retention_pct,
                    cm.billing_status
                FROM contract_milestone cm
                JOIN project_contract pc ON pc.id = cm.contract_id
                WHERE pc.entity_id = :eid
                  AND cm.billing_status IN ('PENDING', 'PARTIAL')
                ORDER BY cm.due_date
            """),
            {"eid": str(entity_id)},
        ).fetchall()

        milestone_by_period: dict[tuple, Decimal] = {}
        for r in milestone_rows:
            if r.due_date:
                k = (r.due_date.year, r.due_date.month)
                ret = Decimal(str(r.retention_pct or "0")) / 100
                amt = Decimal(str(r.milestone_amount)) * (1 - ret)
                milestone_by_period[k] = milestone_by_period.get(k, Decimal("0")) + amt

        # Build PoC revenue schedule for each project
        # Revenue recognized = PoC% x Contract Value - cumulative recognized
        poc_rev_by_period: dict[tuple, Decimal] = {}
        poc_cost_by_period: dict[tuple, Decimal] = {}

        for proj in proj_rows:
            if not proj.start_date or not proj.end_date:
                continue
            total_val  = Decimal(str(proj.total_value))
            rab        = Decimal(str(proj.rab_total)) or total_val * Decimal("0.75")
            actual_cost = Decimal(str(proj.actual_cost_to_date))
            rab_with_buf = rab * (1 + buf_pct)

            # PoC to date (cost-to-cost)
            poc_to_date = min(actual_cost / rab_with_buf, Decimal("0.99")) if rab_with_buf > 0 else Decimal("0")
            rev_recognized = poc_to_date * total_val

            # Distribute remaining project over remaining months
            proj_end = proj.end_date
            remaining_months = []
            for p in periods:
                pdate = date(p.year, p.month, 1)
                if date(proj.start_date.year, proj.start_date.month, 1) <= pdate <= date(proj_end.year, proj_end.month, 1):
                    remaining_months.append(p)

            if not remaining_months:
                continue

            remaining_rev  = total_val - rev_recognized
            remaining_cost = max(Decimal("0"), rab_with_buf - actual_cost)
            monthly_rev_amt  = remaining_rev  / len(remaining_months)
            monthly_cost_amt = remaining_cost / len(remaining_months)

            for p in remaining_months:
                k = (p.year, p.month)
                poc_rev_by_period[k]  = poc_rev_by_period.get(k, Decimal("0"))  + monthly_rev_amt
                poc_cost_by_period[k] = poc_cost_by_period.get(k, Decimal("0")) + monthly_cost_amt

        # Overhead
        avg_oh = _avg_expense_category(actuals["monthly_expense"], ["overhead", "admin"])

        snapshots = []
        for i, period in enumerate(periods):
            k = (period.year, period.month)
            oh_factor = (1 + oh_growth) ** (i / 12)

            # Revenue: prioritize milestone billing, fallback to PoC-linear
            if k in milestone_by_period:
                revenue       = milestone_by_period[k]
                driver_source = "milestone_billing"
            else:
                revenue       = poc_rev_by_period.get(k, Decimal("0"))
                driver_source = "poc_linear"

            cogs = poc_cost_by_period.get(k, revenue * Decimal("0.65"))

            snap = PLSnapshot(
                period=period,
                revenue=revenue,
                cogs=cogs,
                rent=avg_oh * Decimal("0.2") * oh_factor,
                depreciation=avg_oh * Decimal("0.15"),
                admin=avg_oh * Decimal("0.65") * oh_factor,
                driver_source=driver_source,
            )
            snap.tax = max(Decimal("0"), snap.ebt * tax_rate)
            snapshots.append(snap)

        return snapshots

    # ── Cash Flow Builder — Indirect Method ──────────────────────────────────

    @staticmethod
    def _build_cashflow(
        db: Session,
        entity_id: UUID,
        pl_snapshots: list[PLSnapshot],
        assumptions: dict[str, Decimal],
        opening_cash: Decimal,
    ) -> list[dict]:
        """
        Indirect method CF:
        Net Income + Depreciation + ΔAR (DSO) + ΔInventory (DIO) + ΔAP (DPO) + CAPEX
        """
        dso     = assumptions.get("dso_days",          Decimal("45"))
        dpo     = assumptions.get("dpo_days",           Decimal("30"))
        dio     = assumptions.get("dio_days",           Decimal("0"))
        capex   = assumptions.get("capex_monthly_avg",  Decimal("0"))

        # DSO: fraction of revenue collected this month vs shifted to next
        dso_lag = dso / Decimal("30")            # lag in months
        dso_this_month = max(Decimal("0"), 1 - (dso_lag - 1)) if dso_lag >= 1 else Decimal("1")
        dso_next_month = Decimal("1") - dso_this_month

        # DPO: fraction of COGS paid this month vs next
        dpo_lag = dpo / Decimal("30")
        dpo_this_month = max(Decimal("0"), 1 - (dpo_lag - 1)) if dpo_lag >= 1 else Decimal("1")

        cf_lines = []
        running_cash = opening_cash
        prev_ar = Decimal("0")
        prev_inv = Decimal("0")
        prev_ap = Decimal("0")

        for i, snap in enumerate(pl_snapshots):
            # AR forecast: prior_AR + revenue - collections
            ar_balance = (
                prev_ar + snap.revenue
                - snap.revenue * dso_this_month
                - (pl_snapshots[i - 1].revenue * dso_next_month if i > 0 else Decimal("0"))
            )
            delta_ar = ar_balance - prev_ar     # positive = AR increased = cash used

            # Inventory (only for dagang)
            if dio > 0:
                inv_balance = snap.cogs * dio / Decimal("30")
                delta_inv = inv_balance - prev_inv
                prev_inv = inv_balance
            else:
                delta_inv = Decimal("0")

            # AP forecast
            ap_balance = snap.cogs * dpo / Decimal("30")
            delta_ap = ap_balance - prev_ap     # positive = AP increased = cash received
            prev_ap = ap_balance

            operating_cf = (
                snap.net_income
                + snap.depreciation        # add back non-cash
                - delta_ar                 # AR increase = outflow
                - delta_inv                # inventory increase = outflow
                + delta_ap                 # AP increase = inflow (delayed payment)
            )

            investing_cf = -capex
            financing_cf = Decimal("0")
            net_cf       = operating_cf + investing_cf + financing_cf
            closing_cash = running_cash + net_cf

            period_lines = [
                ("CF_OP_NI",       "Laba Bersih",                    snap.net_income,      10),
                ("CF_OP_DEPR",     "Penyusutan",                     snap.depreciation,    11),
                ("CF_OP_AR",       "Perubahan Piutang (DSO)",        -delta_ar,            12),
                ("CF_OP_INV",      "Perubahan Persediaan (DIO)",     -delta_inv,           13),
                ("CF_OP_AP",       "Perubahan Hutang (DPO)",          delta_ap,            14),
                ("CF_OP_WC_OTHER", "Modal Kerja Lainnya",            Decimal("0"),         15),
                ("CF_OP_TOTAL",    "Total Arus Kas Operasi",          operating_cf,        19),
                ("CF_IV_CAPEX",    "Belanja Modal",                  -capex,               30),
                ("CF_IV_OTHER",    "Investasi Lainnya",              Decimal("0"),         31),
                ("CF_IV_TOTAL",    "Total Arus Kas Investasi",        investing_cf,        39),
                ("CF_FIN_DEBT",    "Hutang Bank",                    Decimal("0"),         50),
                ("CF_FIN_EQUITY",  "Modal",                          Decimal("0"),         51),
                ("CF_FIN_TOTAL",   "Total Arus Kas Pendanaan",        financing_cf,        59),
                ("CF_NET",         "Kenaikan (Penurunan) Kas Neto",  net_cf,              60),
                ("CF_OPEN",        "Saldo Kas Awal",                  running_cash,        61),
                ("CF_CLOSE",       "Saldo Kas Akhir",                 closing_cash,        62),
            ]

            for lc, ln, amt, so in period_lines:
                cf_lines.append({
                    "period_year":   snap.period.year,
                    "period_month":  snap.period.month,
                    "statement_type": "cf",
                    "line_code":     lc,
                    "line_name":     ln,
                    "sort_order":    so,
                    "line_category": "operating" if so < 19 else
                                     "operating" if so == 19 else
                                     "investing" if so < 39 else
                                     "investing" if so == 39 else
                                     "financing" if so < 59 else
                                     "financing" if so == 59 else "balance",
                    "amount":        float(amt),
                    "driver_source": "indirect_method",
                })

            prev_ar = ar_balance
            running_cash = closing_cash

        return cf_lines

    # ── Balance Sheet Builder ─────────────────────────────────────────────────

    @staticmethod
    def _build_balance_sheet(
        db: Session,
        entity_id: UUID,
        pl_snapshots: list[PLSnapshot],
        cf_lines: list[dict],
        opening_bs: dict[str, Decimal],
        assumptions: dict[str, Decimal],
    ) -> list[dict]:
        """
        Rolling BS: opening + changes from P&L + CF.
        BS must balance: Total Asset = Total Liability + Equity
        """
        dso  = assumptions.get("dso_days",          Decimal("45"))
        dpo  = assumptions.get("dpo_days",           Decimal("30"))
        dio  = assumptions.get("dio_days",           Decimal("0"))
        capex = assumptions.get("capex_monthly_avg", Decimal("0"))

        # Starting values from opening_bs
        cash_bal    = opening_bs.get("BS_CASH",        Decimal("0"))
        ar_bal      = opening_bs.get("BS_AR",          Decimal("0"))
        inv_bal     = opening_bs.get("BS_INVENTORY",   Decimal("0"))
        prepaid_bal = opening_bs.get("BS_PREPAID",     Decimal("0"))
        fa_gross    = opening_bs.get("BS_FA_GROSS",    Decimal("0"))
        fa_accdepr  = opening_bs.get("BS_FA_ACCDEPR",  Decimal("0"))
        ap_bal      = opening_bs.get("BS_AP",          Decimal("0"))
        accrued_bal = opening_bs.get("BS_ACCRUED",     Decimal("0"))
        st_debt     = opening_bs.get("BS_SHORT_DEBT",  Decimal("0"))
        lt_debt     = opening_bs.get("BS_LONG_DEBT",   Decimal("0"))
        paid_cap    = opening_bs.get("BS_EQUITY_PAID", Decimal("0"))
        re_bal      = opening_bs.get("BS_RE",          Decimal("0"))
        tax_pay     = opening_bs.get("BS_TAX_PAYABLE", Decimal("0"))

        # Build period closing cash from CF lines
        cf_closing: dict[tuple, Decimal] = {}
        for row in cf_lines:
            if row["line_code"] == "CF_CLOSE":
                cf_closing[(row["period_year"], row["period_month"])] = Decimal(str(row["amount"]))

        bs_lines = []
        ytd_net_income = Decimal("0")

        for snap in pl_snapshots:
            k = (snap.period.year, snap.period.month)

            # Update balances
            cash_bal    = cf_closing.get(k, cash_bal)
            ar_bal      = ar_bal + snap.revenue - snap.revenue * Decimal("30") / dso
            ar_bal      = max(Decimal("0"), ar_bal)
            inv_bal     = snap.cogs * dio / Decimal("30") if dio > 0 else inv_bal
            fa_gross    = fa_gross + capex
            fa_accdepr  = fa_accdepr + snap.depreciation
            fa_net      = fa_gross - fa_accdepr
            ap_bal      = snap.cogs * dpo / Decimal("30")
            accrued_bal = snap.labor * Decimal("0.1")
            tax_pay     = snap.tax
            ytd_net_income += snap.net_income

            total_ca    = cash_bal + ar_bal + inv_bal + prepaid_bal
            total_nca   = fa_net
            total_asset = total_ca + total_nca
            total_cl    = ap_bal + accrued_bal + st_debt + tax_pay
            total_ltl   = lt_debt
            total_liab  = total_cl + total_ltl
            total_equity = paid_cap + re_bal + ytd_net_income
            total_le    = total_liab + total_equity

            period_bs = [
                ("BS_CASH",       "Kas & Setara Kas",           cash_bal,       10, "current_asset"),
                ("BS_AR",         "Piutang Usaha",              ar_bal,         11, "current_asset"),
                ("BS_INVENTORY",  "Persediaan",                 inv_bal,        12, "current_asset"),
                ("BS_PREPAID",    "Biaya Dibayar Dimuka",       prepaid_bal,    13, "current_asset"),
                ("BS_TOTAL_CA",   "Total Aset Lancar",          total_ca,       19, "current_asset"),
                ("BS_FA_GROSS",   "Aset Tetap Bruto",           fa_gross,       20, "fixed_asset"),
                ("BS_FA_ACCDEPR", "Akumulasi Penyusutan",       -fa_accdepr,    21, "fixed_asset"),
                ("BS_FA_NET",     "Aset Tetap Neto",            fa_net,         22, "fixed_asset"),
                ("BS_TOTAL_NCA",  "Total Aset Tidak Lancar",    total_nca,      29, "fixed_asset"),
                ("BS_TOTAL_ASSET","Total Aset",                  total_asset,    30, "total_asset"),
                ("BS_AP",         "Hutang Usaha",               ap_bal,         40, "current_liab"),
                ("BS_ACCRUED",    "Hutang Akrual",              accrued_bal,    41, "current_liab"),
                ("BS_SHORT_DEBT", "Hutang Bank Jangka Pendek",  st_debt,        42, "current_liab"),
                ("BS_TAX_PAYABLE","Hutang Pajak",               tax_pay,        43, "current_liab"),
                ("BS_TOTAL_CL",   "Total Kewajiban Lancar",     total_cl,       49, "current_liab"),
                ("BS_LONG_DEBT",  "Hutang Bank Jangka Panjang", lt_debt,        50, "long_liab"),
                ("BS_TOTAL_LTL",  "Total Kewajiban J. Panjang", total_ltl,      59, "long_liab"),
                ("BS_TOTAL_LIAB", "Total Kewajiban",            total_liab,     60, "total_liab"),
                ("BS_EQUITY_PAID","Modal Disetor",              paid_cap,       70, "equity"),
                ("BS_RE",         "Laba Ditahan",               re_bal,         71, "equity"),
                ("BS_NET_INCOME", "Laba Bersih Tahun Berjalan", ytd_net_income, 72, "equity"),
                ("BS_TOTAL_EQ",   "Total Ekuitas",              total_equity,   79, "equity"),
                ("BS_TOTAL_LE",   "Total Kewajiban + Ekuitas",  total_le,       80, "check"),
            ]

            for lc, ln, amt, so, cat in period_bs:
                bs_lines.append({
                    "period_year":    snap.period.year,
                    "period_month":   snap.period.month,
                    "statement_type": "bs",
                    "line_code":      lc,
                    "line_name":      ln,
                    "sort_order":     so,
                    "line_category":  cat,
                    "amount":         float(amt),
                    "driver_source":  "balance_sheet_projection",
                })

        return bs_lines

    # ── Pull Opening BS from GL ───────────────────────────────────────────────

    @staticmethod
    def _get_opening_bs(db: Session, entity_id: UUID, as_of_date: date) -> dict[str, Decimal]:
        rows = db.execute(
            text("""
                SELECT
                    coa.account_type,
                    coa.account_subtype,
                    CASE coa.normal_balance
                        WHEN 'debit'  THEN COALESCE(SUM(gl.debit_idr - gl.credit_idr), 0)
                        WHEN 'credit' THEN COALESCE(SUM(gl.credit_idr - gl.debit_idr), 0)
                    END AS balance
                FROM gl_line gl
                JOIN gl_journal gj         ON gj.id = gl.journal_id AND gj.status = 'posted'
                JOIN chart_of_accounts coa ON coa.id = gl.account_id
                WHERE gj.entity_id = :eid
                  AND gj.journal_date < :dt
                  AND coa.account_type IN ('asset','liability','equity')
                GROUP BY coa.account_type, coa.account_subtype, coa.normal_balance
            """),
            {"eid": str(entity_id), "dt": str(as_of_date)},
        ).fetchall()

        bs: dict[str, Decimal] = {}
        for r in rows:
            bal = Decimal(str(r.balance))
            sub = (r.account_subtype or "").lower()
            if r.account_type == "asset":
                if "kas" in sub or "bank" in sub or "cash" in sub:
                    bs["BS_CASH"] = bs.get("BS_CASH", Decimal("0")) + bal
                elif "piutang" in sub or "receivable" in sub:
                    bs["BS_AR"] = bs.get("BS_AR", Decimal("0")) + bal
                elif "persediaan" in sub or "inventory" in sub:
                    bs["BS_INVENTORY"] = bs.get("BS_INVENTORY", Decimal("0")) + bal
                elif "tetap" in sub or "fixed" in sub:
                    bs["BS_FA_GROSS"] = bs.get("BS_FA_GROSS", Decimal("0")) + bal
                elif "penyusutan" in sub or "depreciation" in sub:
                    bs["BS_FA_ACCDEPR"] = bs.get("BS_FA_ACCDEPR", Decimal("0")) + bal
            elif r.account_type == "liability":
                if "hutang usaha" in sub or "payable" in sub:
                    bs["BS_AP"] = bs.get("BS_AP", Decimal("0")) + bal
                elif "jangka pendek" in sub or "short" in sub:
                    bs["BS_SHORT_DEBT"] = bs.get("BS_SHORT_DEBT", Decimal("0")) + bal
                elif "jangka panjang" in sub or "long" in sub:
                    bs["BS_LONG_DEBT"] = bs.get("BS_LONG_DEBT", Decimal("0")) + bal
            elif r.account_type == "equity":
                if "modal" in sub or "capital" in sub:
                    bs["BS_EQUITY_PAID"] = bs.get("BS_EQUITY_PAID", Decimal("0")) + bal
                elif "ditahan" in sub or "retained" in sub:
                    bs["BS_RE"] = bs.get("BS_RE", Decimal("0")) + bal

        # Get cash from bank account actual balances if available
        bank_cash = db.execute(
            text("""
                SELECT COALESCE(SUM(
                    CASE WHEN bsl.credit_amount > 0 THEN bsl.credit_amount ELSE -bsl.debit_amount END
                ), 0) AS total_cash
                FROM bank_account ba
                JOIN bank_statement bs2 ON bs2.bank_account_id = ba.id AND bs2.statement_status = 'finalized'
                WHERE ba.entity_id = :eid
            """),
            {"eid": str(entity_id)},
        ).fetchone()
        if bank_cash and bank_cash.total_cash:
            bs["BS_CASH"] = Decimal(str(bank_cash.total_cash))

        return bs

    # ── Save to DB ────────────────────────────────────────────────────────────

    @staticmethod
    def _save_forecast_lines(
        db: Session,
        scenario_id: UUID,
        entity_id: UUID,
        all_lines: list[dict],
    ) -> None:
        db.execute(
            text("DELETE FROM forecast_line WHERE scenario_id=:sid"),
            {"sid": str(scenario_id)},
        )
        for row in all_lines:
            db.execute(
                text("""
                    INSERT INTO forecast_line
                        (scenario_id, entity_id, period_year, period_month,
                         statement_type, line_code, line_name, sort_order,
                         line_category, amount, driver_source)
                    VALUES (:sid, :eid, :yr, :mo, :stype, :lc, :ln, :so,
                            :cat, :amt, :drv)
                    ON CONFLICT (scenario_id, period_year, period_month, statement_type, line_code)
                    DO UPDATE SET amount=EXCLUDED.amount, driver_source=EXCLUDED.driver_source
                """),
                {
                    "sid":   str(scenario_id),
                    "eid":   str(entity_id),
                    "yr":    row["period_year"],
                    "mo":    row["period_month"],
                    "stype": row["statement_type"],
                    "lc":    row["line_code"],
                    "ln":    row["line_name"],
                    "so":    row.get("sort_order", 0),
                    "cat":   row.get("line_category"),
                    "amt":   row["amount"],
                    "drv":   row.get("driver_source"),
                },
            )

    # ── pl_snapshot → list[dict] ─────────────────────────────────────────────

    @staticmethod
    def _pl_snapshots_to_rows(snapshots: list[PLSnapshot]) -> list[dict]:
        rows = []
        for snap in snapshots:
            rev_contracted = snap.revenue * Decimal("0.6") if snap.driver_source.startswith("contract") else Decimal("0")
            rev_pipeline   = snap.revenue * Decimal("0.2") if snap.driver_source.startswith("contract") else Decimal("0")
            rev_recurring  = snap.revenue - rev_contracted - rev_pipeline

            lines = [
                ("REV_CONTRACT",  "Pendapatan Kontrak",             float(rev_contracted),    10, "revenue"),
                ("REV_PIPELINE",  "Pendapatan Pipeline",            float(rev_pipeline),       11, "revenue"),
                ("REV_RECURRING", "Pendapatan Recurring",           float(rev_recurring),      12, "revenue"),
                ("REV_TOTAL",     "Total Pendapatan",               float(snap.revenue),       15, "revenue"),
                ("COGS_DIRECT",   "HPP / Biaya Langsung",           float(snap.cogs),          20, "cogs"),
                ("COGS_TOTAL",    "Total HPP",                      float(snap.cogs),          25, "cogs"),
                ("GROSS_PROFIT",  "Laba Kotor",                     float(snap.gross_profit),  30, "gross_profit"),
                ("OPEX_LABOR",    "Biaya Tenaga Kerja",             float(snap.labor),         40, "opex"),
                ("OPEX_RENT",     "Sewa & Utilitas",                float(snap.rent),          41, "opex"),
                ("OPEX_DEPR",     "Penyusutan",                     float(snap.depreciation),  42, "opex"),
                ("OPEX_MKTG",     "Pemasaran & Penjualan",          float(snap.marketing),     43, "opex"),
                ("OPEX_ADMIN",    "Umum & Administrasi",            float(snap.admin),         44, "opex"),
                ("OPEX_OTHER",    "Biaya Operasional Lainnya",      float(snap.other_opex),    45, "opex"),
                ("OPEX_TOTAL",    "Total Biaya Operasional",        float(snap.opex_total),    49, "opex"),
                ("EBITDA",        "EBITDA",                         float(snap.ebitda),        50, "ebitda"),
                ("EBIT",          "EBIT",                           float(snap.ebit),          55, "ebit"),
                ("INTEREST_INC",  "Pendapatan Bunga",               float(snap.interest_income),60, "financial"),
                ("INTEREST_EXP",  "Beban Bunga",                    float(snap.interest_expense),61,"financial"),
                ("EBT",           "Laba Sebelum Pajak",             float(snap.ebt),           65, "ebt"),
                ("TAX_INCOME",    "Pajak Penghasilan",              float(snap.tax),           70, "tax"),
                ("NET_INCOME",    "Laba Bersih",                    float(snap.net_income),    80, "net_income"),
            ]
            for lc, ln, amt, so, cat in lines:
                rows.append({
                    "period_year":    snap.period.year,
                    "period_month":   snap.period.month,
                    "statement_type": "pl",
                    "line_code":      lc,
                    "line_name":      ln,
                    "sort_order":     so,
                    "line_category":  cat,
                    "amount":         amt,
                    "driver_source":  snap.driver_source,
                })
        return rows

    # ── Main Orchestrator ─────────────────────────────────────────────────────

    @staticmethod
    def run_forecast(db: Session, scenario_id: UUID, entity_id: UUID) -> dict:
        scenario = db.execute(
            text("""
                SELECT id, company_type, as_of_date, forecast_months
                FROM forecast_scenario WHERE id=:sid AND entity_id=:eid
            """),
            {"sid": str(scenario_id), "eid": str(entity_id)},
        ).fetchone()
        if not scenario:
            raise ValueError("Scenario tidak ditemukan.")

        db.execute(
            text("UPDATE forecast_scenario SET status='computing' WHERE id=:sid"),
            {"sid": str(scenario_id)},
        )
        db.commit()

        try:
            assumptions = ForecastEngine._get_assumptions(db, scenario_id)
            as_of_date  = scenario.as_of_date
            periods     = ForecastEngine._get_forecast_periods(as_of_date, scenario.forecast_months)
            actuals     = ForecastEngine._pull_actuals(db, entity_id, as_of_date)
            opening_bs  = ForecastEngine._get_opening_bs(db, entity_id, as_of_date)

            # P&L forecast
            if scenario.company_type == "jasa":
                pl_snaps = ForecastEngine._forecast_pl_jasa(db, entity_id, assumptions, actuals, periods)
            elif scenario.company_type == "dagang":
                pl_snaps = ForecastEngine._forecast_pl_dagang(db, entity_id, assumptions, actuals, periods)
            elif scenario.company_type == "konstruksi":
                pl_snaps = ForecastEngine._forecast_pl_konstruksi(db, entity_id, assumptions, actuals, periods)
            else:
                pl_snaps = ForecastEngine._forecast_pl_jasa(db, entity_id, assumptions, actuals, periods)

            pl_rows = ForecastEngine._pl_snapshots_to_rows(pl_snaps)

            opening_cash = opening_bs.get("BS_CASH", Decimal("0"))
            cf_rows = ForecastEngine._build_cashflow(db, entity_id, pl_snaps, assumptions, opening_cash)
            bs_rows = ForecastEngine._build_balance_sheet(db, entity_id, pl_snaps, cf_rows, opening_bs, assumptions)

            all_rows = pl_rows + cf_rows + bs_rows
            ForecastEngine._save_forecast_lines(db, scenario_id, entity_id, all_rows)

            db.execute(
                text("""
                    UPDATE forecast_scenario
                    SET status='computed', last_computed_at=NOW()
                    WHERE id=:sid
                """),
                {"sid": str(scenario_id)},
            )
            db.commit()

            return {
                "scenario_id":  str(scenario_id),
                "status":       "computed",
                "periods":      len(periods),
                "pl_lines":     len(pl_rows),
                "cf_lines":     len(cf_rows),
                "bs_lines":     len(bs_rows),
                "company_type": scenario.company_type,
            }

        except Exception as exc:
            db.execute(
                text("UPDATE forecast_scenario SET status='draft' WHERE id=:sid"),
                {"sid": str(scenario_id)},
            )
            db.commit()
            raise exc

    # ── Get Three-Way Output ─────────────────────────────────────────────────

    @staticmethod
    def get_three_way_forecast(db: Session, scenario_id: UUID) -> dict:
        scenario = db.execute(
            text("SELECT scenario_name, company_type, status FROM forecast_scenario WHERE id=:sid"),
            {"sid": str(scenario_id)},
        ).fetchone()
        if not scenario:
            raise ValueError("Scenario tidak ditemukan.")
        if scenario.status != "computed":
            raise ValueError(f"Forecast belum dihitung (status: {scenario.status}). Jalankan run_forecast() dulu.")

        def fetch(stype):
            rows = db.execute(
                text("""
                    SELECT period_year, period_month, line_code, line_name,
                           sort_order, line_category, amount, amount_actual,
                           is_actual, driver_source
                    FROM forecast_line
                    WHERE scenario_id=:sid AND statement_type=:stype
                    ORDER BY period_year, period_month, sort_order
                """),
                {"sid": str(scenario_id), "stype": stype},
            ).fetchall()
            return [dict(r._mapping) for r in rows]

        pl = fetch("pl")
        cf = fetch("cf")
        bs = fetch("bs")

        # Summary pivot
        summary_rows = db.execute(
            text("""
                SELECT period_year, period_month, revenue, gross_profit,
                       ebitda, net_income, cash_closing
                FROM vw_forecast_summary
                WHERE scenario_id=:sid
                ORDER BY period_year, period_month
            """),
            {"sid": str(scenario_id)},
        ).fetchall()

        return {
            "scenario_id":   str(scenario_id),
            "scenario_name": scenario.scenario_name,
            "company_type":  scenario.company_type,
            "pl":            pl,
            "cf":            cf,
            "bs":            bs,
            "summary":       [dict(r._mapping) for r in summary_rows],
        }

    # ── Refresh Actuals (lock period) ─────────────────────────────────────────

    @staticmethod
    def refresh_actuals(db: Session, scenario_id: UUID, entity_id: UUID, period_year: int, period_month: int) -> dict:
        """Pull real GL data for a closed period and stamp it into forecast_line.amount_actual."""
        # Revenue actual
        rev = db.execute(
            text("""
                SELECT COALESCE(SUM(gl.credit_idr - gl.debit_idr), 0) AS net_rev
                FROM gl_line gl
                JOIN gl_journal gj ON gj.id = gl.journal_id AND gj.status = 'posted'
                JOIN chart_of_accounts coa ON coa.id = gl.account_id
                WHERE gj.entity_id=:eid AND coa.account_type='revenue'
                  AND EXTRACT(YEAR FROM gj.journal_date)=:yr
                  AND EXTRACT(MONTH FROM gj.journal_date)=:mo
            """),
            {"eid": str(entity_id), "yr": period_year, "mo": period_month},
        ).fetchone()

        net_rev = Decimal(str(rev.net_rev)) if rev else Decimal("0")

        # Update REV_TOTAL, NET_INCOME with actual
        db.execute(
            text("""
                UPDATE forecast_line
                SET amount_actual=:amt, is_actual=true
                WHERE scenario_id=:sid AND period_year=:yr AND period_month=:mo
                  AND statement_type='pl' AND line_code='REV_TOTAL'
            """),
            {"sid": str(scenario_id), "amt": float(net_rev), "yr": period_year, "mo": period_month},
        )
        db.commit()
        return {"period": f"{period_year}-{period_month:02d}", "actual_revenue": float(net_rev), "stamped": True}

    # ── What-If Analysis ──────────────────────────────────────────────────────

    @staticmethod
    def run_what_if(
        db: Session,
        base_scenario_id: UUID,
        entity_id: UUID,
        what_if_name: str,
        override_assumptions: dict[str, float],
        created_by: str = "",
    ) -> dict:
        base = db.execute(
            text("""
                SELECT scenario_name, company_type, as_of_date, forecast_months
                FROM forecast_scenario WHERE id=:sid
            """),
            {"sid": str(base_scenario_id)},
        ).fetchone()
        if not base:
            raise ValueError("Base scenario tidak ditemukan.")

        try:
            scenario_result = ForecastEngine.create_scenario(
                db,
                entity_id       = entity_id,
                scenario_name   = what_if_name,
                company_type    = base.company_type,
                as_of_date      = base.as_of_date,
                forecast_months = base.forecast_months,
                scenario_type   = "what_if",
                base_scenario_id = base_scenario_id,
                created_by      = created_by,
            )
        except ValueError:
            row = db.execute(
                text("""
                    SELECT id FROM forecast_scenario
                    WHERE entity_id=:eid AND scenario_name=:nm AND as_of_date=:dt
                """),
                {"eid": str(entity_id), "nm": what_if_name, "dt": str(base.as_of_date)},
            ).fetchone()
            scenario_result = {"scenario_id": str(row.id)}

        what_if_id = UUID(scenario_result["scenario_id"])

        # Copy base assumptions then override
        base_assump = ForecastEngine._get_assumptions(db, base_scenario_id)
        merged = {k: float(v) for k, v in base_assump.items()}
        merged.update(override_assumptions)

        ForecastEngine.set_assumptions(db, what_if_id, merged)
        result = ForecastEngine.run_forecast(db, what_if_id, entity_id)
        result["base_scenario_id"] = str(base_scenario_id)
        result["overrides_applied"] = list(override_assumptions.keys())
        return result

    # ── Get Variance: Actual vs Forecast ─────────────────────────────────────

    @staticmethod
    def get_variance_report(
        db: Session,
        scenario_id: UUID,
        statement_type: str = "pl",
        period_year: Optional[int] = None,
        period_month: Optional[int] = None,
    ) -> list[dict]:
        filters = ["fl.scenario_id=:sid", "fl.statement_type=:stype", "fl.amount_actual IS NOT NULL"]
        params: dict = {"sid": str(scenario_id), "stype": statement_type}
        if period_year:
            filters.append("fl.period_year=:yr")
            params["yr"] = period_year
        if period_month:
            filters.append("fl.period_month=:mo")
            params["mo"] = period_month

        rows = db.execute(
            text(f"""
                SELECT
                    fl.period_year, fl.period_month, fl.line_code, fl.line_name,
                    fl.amount AS forecast, fl.amount_actual AS actual,
                    fl.amount_actual - fl.amount AS variance,
                    CASE WHEN fl.amount != 0
                         THEN ROUND((fl.amount_actual - fl.amount) / ABS(fl.amount) * 100, 1)
                         ELSE NULL END AS variance_pct
                FROM forecast_line fl
                WHERE {' AND '.join(filters)}
                ORDER BY fl.period_year, fl.period_month, fl.sort_order
            """),
            params,
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    # ── Construction-Specific ─────────────────────────────────────────────────

    @staticmethod
    def get_construction_dashboard(db: Session, entity_id: UUID) -> list[dict]:
        """
        Per-project: PoC%, Revenue Recognized, CTC, Under/Over-billing, Sinking Cash Alert.
        """
        rows = db.execute(
            text("""
                SELECT
                    pc.id          AS contract_id,
                    pc.contract_name,
                    pc.total_value AS contract_value,
                    COALESCE(
                        (SELECT SUM(wi.planned_cost) FROM wbs_item wi WHERE wi.project_id = pc.project_id), 0
                    )              AS rab_total,
                    COALESCE(
                        (SELECT SUM(gl.debit_idr)
                         FROM gl_line gl
                         JOIN gl_journal gj ON gj.id = gl.journal_id AND gj.status = 'posted'
                         JOIN chart_of_accounts coa ON coa.id = gl.account_id
                         WHERE coa.account_type = 'expense'
                           AND gl.project_id = pc.project_id
                        ), 0
                    )              AS actual_cost,
                    COALESCE(
                        (SELECT SUM(gl.credit_idr - gl.debit_idr)
                         FROM gl_line gl
                         JOIN gl_journal gj ON gj.id = gl.journal_id AND gj.status = 'posted'
                         JOIN chart_of_accounts coa ON coa.id = gl.account_id
                         WHERE coa.account_type = 'revenue'
                           AND gl.project_id = pc.project_id
                        ), 0
                    )              AS revenue_recognized,
                    COALESCE(
                        (SELECT SUM(cm.milestone_amount)
                         FROM contract_milestone cm
                         WHERE cm.contract_id = pc.id AND cm.billing_status = 'INVOICED'
                        ), 0
                    )              AS billed_to_date,
                    pc.status
                FROM project_contract pc
                WHERE pc.entity_id = :eid AND pc.status IN ('ACTIVE','DRAFT')
                ORDER BY pc.start_date
            """),
            {"eid": str(entity_id)},
        ).fetchall()

        result = []
        for r in rows:
            contract_val  = Decimal(str(r.contract_value))
            rab           = Decimal(str(r.rab_total)) or contract_val * Decimal("0.75")
            actual_cost   = Decimal(str(r.actual_cost))
            rev_recog     = Decimal(str(r.revenue_recognized))
            billed        = Decimal(str(r.billed_to_date))

            poc_pct = min(actual_cost / rab, Decimal("1")) if rab > 0 else Decimal("0")
            earned_value  = poc_pct * contract_val
            ctc           = max(Decimal("0"), rab - actual_cost)

            # Under-billing: earned revenue > billed (perusahaan undercharge)
            # Over-billing: billed > earned revenue (perusahaan overbilled)
            billing_diff  = billed - earned_value
            billing_status = "over_billed" if billing_diff > 0 else "under_billed" if billing_diff < 0 else "on_track"

            # Sinking cash alert: saldo CTC < 10% RAB
            sinking_alert = ctc / rab < Decimal("0.10") if rab > 0 else False

            result.append({
                "contract_id":       str(r.contract_id),
                "contract_name":     r.contract_name,
                "contract_value":    float(contract_val),
                "rab_total":         float(rab),
                "actual_cost":       float(actual_cost),
                "poc_pct":           float(round(poc_pct * 100, 1)),
                "revenue_recognized": float(rev_recog),
                "earned_value":      float(earned_value),
                "billed_to_date":    float(billed),
                "billing_difference": float(billing_diff),
                "billing_status":    billing_status,
                "cost_to_complete":  float(ctc),
                "sinking_cash_alert": sinking_alert,
                "project_status":    r.status,
            })

        return result

    # ── Trading-Specific: MRP / Procurement Forecast ─────────────────────────

    @staticmethod
    def get_procurement_forecast(
        db: Session,
        entity_id: UUID,
        scenario_id: UUID,
        warehouse_id: Optional[UUID] = None,
    ) -> list[dict]:
        """
        Untuk dagang: hitung kebutuhan pembelian per produk berdasarkan
        forecast penjualan + safety stock - on-hand.
        Purchase = (Forecast Sales Qty + Safety Stock - On-Hand) × Purchase Price
        """
        # Get forecast revenue from scenario → back-calculate qty
        assumptions = ForecastEngine._get_assumptions(db, scenario_id)
        gm_pct      = assumptions.get("gross_margin_pct",  Decimal("30")) / 100
        safety_days = assumptions.get("safety_stock_days", Decimal("14"))

        # Product-level on-hand
        products = db.execute(
            text("""
                SELECT
                    pp.id,
                    pp.product_name,
                    pp.product_code,
                    pp.current_avg_cost     AS unit_cost,
                    pp.sales_price          AS unit_price,
                    COALESCE(
                        (SELECT SUM(CASE WHEN sm.move_type='in' THEN sm.quantity ELSE -sm.quantity END)
                         FROM stock_move sm
                         WHERE sm.product_id = pp.id
                           AND (:wh IS NULL OR sm.destination_warehouse_id=:wh OR sm.source_warehouse_id=:wh)
                        ), 0
                    ) AS on_hand_qty,
                    COALESCE(
                        (SELECT AVG(sm.quantity) FROM stock_move sm
                         WHERE sm.product_id = pp.id AND sm.move_type = 'out'
                           AND sm.move_date >= NOW() - INTERVAL '90 days'
                        ), 0
                    ) / 90 AS avg_daily_sales
                FROM product_product pp
                WHERE pp.entity_id = :eid AND pp.active = true
                ORDER BY pp.product_name
            """),
            {"eid": str(entity_id), "wh": str(warehouse_id) if warehouse_id else None},
        ).fetchall()

        result = []
        for p in products:
            daily_sales = Decimal(str(p.avg_daily_sales))
            on_hand     = Decimal(str(p.on_hand_qty))
            unit_cost   = Decimal(str(p.unit_cost or "0"))
            safety_qty  = daily_sales * safety_days
            monthly_qty = daily_sales * 30
            reorder_qty = max(Decimal("0"), monthly_qty + safety_qty - on_hand)
            purchase_cost = reorder_qty * unit_cost

            result.append({
                "product_id":       str(p.id),
                "product_code":     p.product_code,
                "product_name":     p.product_name,
                "on_hand_qty":      float(on_hand),
                "avg_daily_sales":  float(daily_sales),
                "monthly_forecast_qty": float(monthly_qty),
                "safety_stock_qty": float(safety_qty),
                "reorder_qty":      float(reorder_qty),
                "unit_cost":        float(unit_cost),
                "purchase_value":   float(purchase_cost),
                "days_of_stock":    float(on_hand / daily_sales) if daily_sales > 0 else 9999,
            })

        return sorted(result, key=lambda x: x["purchase_value"], reverse=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _avg_expense_category(monthly_expense: dict, categories: list[str]) -> Decimal:
    if not monthly_expense:
        return Decimal("0")
    total = Decimal("0")
    n = 0
    for _, exp_dict in monthly_expense.items():
        for cat in categories:
            total += exp_dict.get(cat, Decimal("0"))
        n += 1
    return total / n if n else Decimal("0")
