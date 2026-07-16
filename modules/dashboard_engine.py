"""
Dashboard Engine — Aggregasi KPI dari seluruh modul
Mendukung tiga jenis perusahaan: jasa / dagang / konstruksi
"""

from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


class DashboardEngine:

    # ── Summary KPI Cards ─────────────────────────────────────────────────────

    @staticmethod
    def get_kpi_cards(db: Session, entity_id: UUID, as_of_date) -> dict:
        yr  = as_of_date.year
        mo  = as_of_date.month

        def _q(sql, params=None):
            return db.execute(text(sql), params or {}).fetchone()

        # Revenue MTD
        rev = _q("""
            SELECT COALESCE(SUM(gl.credit_idr - gl.debit_idr), 0) AS val
            FROM gl_line gl
            JOIN gl_journal gj         ON gj.id = gl.journal_id AND gj.status='posted'
            JOIN chart_of_accounts coa ON coa.id = gl.account_id
            WHERE gj.entity_id=:eid AND coa.account_type='revenue'
              AND EXTRACT(YEAR FROM gj.journal_date)=:yr
              AND EXTRACT(MONTH FROM gj.journal_date)=:mo
        """, {"eid": str(entity_id), "yr": yr, "mo": mo})
        revenue_mtd = Decimal(str(rev.val)) if rev else Decimal("0")

        # Revenue prior month (MoM comparison)
        prior_mo = mo - 1 if mo > 1 else 12
        prior_yr = yr if mo > 1 else yr - 1
        rev_prior = _q("""
            SELECT COALESCE(SUM(gl.credit_idr - gl.debit_idr), 0) AS val
            FROM gl_line gl
            JOIN gl_journal gj         ON gj.id = gl.journal_id AND gj.status='posted'
            JOIN chart_of_accounts coa ON coa.id = gl.account_id
            WHERE gj.entity_id=:eid AND coa.account_type='revenue'
              AND EXTRACT(YEAR FROM gj.journal_date)=:yr
              AND EXTRACT(MONTH FROM gj.journal_date)=:mo
        """, {"eid": str(entity_id), "yr": prior_yr, "mo": prior_mo})
        revenue_prior = Decimal(str(rev_prior.val)) if rev_prior else Decimal("0")

        # COGS MTD
        cogs = _q("""
            SELECT COALESCE(SUM(gl.debit_idr - gl.credit_idr), 0) AS val
            FROM gl_line gl
            JOIN gl_journal gj         ON gj.id = gl.journal_id AND gj.status='posted'
            JOIN chart_of_accounts coa ON coa.id = gl.account_id
            WHERE gj.entity_id=:eid AND coa.account_type='expense'
              AND (coa.account_subtype ILIKE '%hpp%' OR coa.account_subtype ILIKE '%cogs%'
                   OR coa.account_code LIKE '5-%')
              AND EXTRACT(YEAR FROM gj.journal_date)=:yr
              AND EXTRACT(MONTH FROM gj.journal_date)=:mo
        """, {"eid": str(entity_id), "yr": yr, "mo": mo})
        cogs_mtd = Decimal(str(cogs.val)) if cogs else Decimal("0")

        # Total expense MTD
        exp = _q("""
            SELECT COALESCE(SUM(gl.debit_idr - gl.credit_idr), 0) AS val
            FROM gl_line gl
            JOIN gl_journal gj         ON gj.id = gl.journal_id AND gj.status='posted'
            JOIN chart_of_accounts coa ON coa.id = gl.account_id
            WHERE gj.entity_id=:eid AND coa.account_type='expense'
              AND EXTRACT(YEAR FROM gj.journal_date)=:yr
              AND EXTRACT(MONTH FROM gj.journal_date)=:mo
        """, {"eid": str(entity_id), "yr": yr, "mo": mo})
        expense_mtd = Decimal(str(exp.val)) if exp else Decimal("0")

        # Cash balance (from bank accounts)
        cash = _q("""
            SELECT COALESCE(SUM(ba.current_balance), 0) AS val
            FROM bank_account ba
            WHERE ba.entity_id=:eid AND ba.active=true
        """, {"eid": str(entity_id)})
        # Fallback to GL cash accounts if bank_account has no current_balance
        if not cash or not cash.val:
            cash = _q("""
                SELECT COALESCE(SUM(gl.debit_idr - gl.credit_idr), 0) AS val
                FROM gl_line gl
                JOIN gl_journal gj         ON gj.id = gl.journal_id AND gj.status='posted'
                JOIN chart_of_accounts coa ON coa.id = gl.account_id
                WHERE gj.entity_id=:eid
                  AND (coa.account_code LIKE '1-1%')
            """, {"eid": str(entity_id)})
        cash_balance = Decimal(str(cash.val)) if cash else Decimal("0")

        # AR outstanding
        ar = _q("""
            SELECT
                COALESCE(SUM(CASE WHEN ai.status NOT IN ('paid','cancelled') THEN ai.amount_remaining END), 0) AS total,
                COUNT(CASE WHEN ai.status NOT IN ('paid','cancelled') THEN 1 END) AS count,
                COALESCE(SUM(CASE WHEN ai.due_date < CURRENT_DATE
                                   AND ai.status NOT IN ('paid','cancelled')
                              THEN ai.amount_remaining END), 0) AS overdue
            FROM ar_invoice ai
            WHERE ai.entity_id=:eid
        """, {"eid": str(entity_id)})
        ar_total   = Decimal(str(ar.total))   if ar else Decimal("0")
        ar_overdue = Decimal(str(ar.overdue)) if ar else Decimal("0")
        ar_count   = int(ar.count) if ar else 0

        # AP outstanding
        ap = _q("""
            SELECT
                COALESCE(SUM(CASE WHEN api.status NOT IN ('paid','cancelled') THEN api.amount_remaining END), 0) AS total,
                COALESCE(SUM(CASE WHEN api.due_date < CURRENT_DATE
                                   AND api.status NOT IN ('paid','cancelled')
                              THEN api.amount_remaining END), 0) AS overdue
            FROM ap_invoice api
            WHERE api.entity_id=:eid
        """, {"eid": str(entity_id)})
        ap_total   = Decimal(str(ap.total))   if ap else Decimal("0")
        ap_overdue = Decimal(str(ap.overdue)) if ap else Decimal("0")

        # Gross profit & margins
        gross_profit = revenue_mtd - cogs_mtd
        net_income   = revenue_mtd - expense_mtd
        gm_pct = (gross_profit / revenue_mtd * 100) if revenue_mtd > 0 else Decimal("0")
        nm_pct = (net_income   / revenue_mtd * 100) if revenue_mtd > 0 else Decimal("0")
        rev_mom = (
            (revenue_mtd - revenue_prior) / revenue_prior * 100
            if revenue_prior > 0 else Decimal("0")
        )

        # DSO: AR / (Revenue last 3 months / 90)
        rev_90 = _q("""
            SELECT COALESCE(SUM(gl.credit_idr - gl.debit_idr), 0) AS val
            FROM gl_line gl
            JOIN gl_journal gj         ON gj.id = gl.journal_id AND gj.status='posted'
            JOIN chart_of_accounts coa ON coa.id = gl.account_id
            WHERE gj.entity_id=:eid AND coa.account_type='revenue'
              AND gj.journal_date >= CURRENT_DATE - INTERVAL '90 days'
        """, {"eid": str(entity_id)})
        rev_90_val = Decimal(str(rev_90.val)) if rev_90 and rev_90.val else Decimal("1")
        dso = ar_total / (rev_90_val / 90) if rev_90_val > 0 else Decimal("0")

        return {
            "period":          f"{yr}-{mo:02d}",
            "revenue_mtd":     float(revenue_mtd),
            "cogs_mtd":        float(cogs_mtd),
            "gross_profit_mtd": float(gross_profit),
            "gross_margin_pct": float(round(gm_pct, 1)),
            "expense_mtd":     float(expense_mtd),
            "net_income_mtd":  float(net_income),
            "net_margin_pct":  float(round(nm_pct, 1)),
            "revenue_mom_pct": float(round(rev_mom, 1)),
            "cash_balance":    float(cash_balance),
            "ar_outstanding":  float(ar_total),
            "ar_overdue":      float(ar_overdue),
            "ar_invoice_count": ar_count,
            "ap_outstanding":  float(ap_total),
            "ap_overdue":      float(ap_overdue),
            "dso_days":        float(round(dso, 1)),
        }

    # ── 12-Month P&L Trend ────────────────────────────────────────────────────

    @staticmethod
    def get_pl_monthly_trend(db: Session, entity_id: UUID, months: int = 12) -> list[dict]:
        rows = db.execute(
            text("""
                SELECT
                    EXTRACT(YEAR  FROM gj.journal_date)::int AS yr,
                    EXTRACT(MONTH FROM gj.journal_date)::int AS mo,
                    COALESCE(SUM(CASE WHEN coa.account_type='revenue'
                                 THEN gl.credit_idr - gl.debit_idr ELSE 0 END), 0) AS revenue,
                    COALESCE(SUM(CASE WHEN coa.account_type='expense'
                                      AND (coa.account_subtype ILIKE '%hpp%' OR coa.account_code LIKE '5-%')
                                 THEN gl.debit_idr - gl.credit_idr ELSE 0 END), 0) AS cogs,
                    COALESCE(SUM(CASE WHEN coa.account_type='expense'
                                 THEN gl.debit_idr - gl.credit_idr ELSE 0 END), 0) AS total_expense
                FROM gl_line gl
                JOIN gl_journal gj         ON gj.id = gl.journal_id AND gj.status='posted'
                JOIN chart_of_accounts coa ON coa.id = gl.account_id
                WHERE gj.entity_id=:eid
                  AND gj.journal_date >= CURRENT_DATE - INTERVAL ':months months'
                  AND coa.account_type IN ('revenue','expense')
                GROUP BY yr, mo
                ORDER BY yr, mo
            """),
            {"eid": str(entity_id), "months": months},
        ).fetchall()

        result = []
        for r in rows:
            rev    = Decimal(str(r.revenue))
            cogs   = Decimal(str(r.cogs))
            exp    = Decimal(str(r.total_expense))
            gp     = rev - cogs
            ni     = rev - exp
            result.append({
                "period":       f"{r.yr}-{r.mo:02d}",
                "year":         r.yr,
                "month":        r.mo,
                "revenue":      float(rev),
                "cogs":         float(cogs),
                "gross_profit": float(gp),
                "total_expense": float(exp),
                "net_income":   float(ni),
                "gross_margin_pct": float(round(gp / rev * 100, 1)) if rev > 0 else 0.0,
                "net_margin_pct":   float(round(ni / rev * 100, 1)) if rev > 0 else 0.0,
            })

        return result

    # ── AR Aging Dashboard ─────────────────────────────────────────────────────

    @staticmethod
    def get_ar_aging(db: Session, entity_id: UUID) -> dict:
        rows = db.execute(
            text("""
                SELECT
                    c.customer_name,
                    ai.invoice_number,
                    ai.invoice_date,
                    ai.due_date,
                    ai.total_amount,
                    ai.amount_remaining,
                    CURRENT_DATE - ai.due_date AS days_overdue
                FROM ar_invoice ai
                JOIN customer c ON c.id = ai.customer_id
                WHERE ai.entity_id=:eid
                  AND ai.status NOT IN ('paid','cancelled')
                  AND ai.amount_remaining > 0
                ORDER BY ai.due_date
            """),
            {"eid": str(entity_id)},
        ).fetchall()

        buckets = {"current": Decimal("0"), "1_30": Decimal("0"), "31_60": Decimal("0"),
                   "61_90": Decimal("0"), "over_90": Decimal("0")}
        detail = []

        for r in rows:
            over = int(r.days_overdue) if r.days_overdue else 0
            amt  = Decimal(str(r.amount_remaining))
            if over <= 0:
                buckets["current"] += amt
                bucket = "current"
            elif over <= 30:
                buckets["1_30"] += amt
                bucket = "1_30"
            elif over <= 60:
                buckets["31_60"] += amt
                bucket = "31_60"
            elif over <= 90:
                buckets["61_90"] += amt
                bucket = "61_90"
            else:
                buckets["over_90"] += amt
                bucket = "over_90"

            detail.append({
                "customer":        r.customer_name,
                "invoice_number":  r.invoice_number,
                "invoice_date":    str(r.invoice_date),
                "due_date":        str(r.due_date),
                "amount_remaining": float(amt),
                "days_overdue":    over,
                "bucket":          bucket,
            })

        return {
            "buckets":      {k: float(v) for k, v in buckets.items()},
            "total":        float(sum(buckets.values())),
            "invoice_count": len(detail),
            "detail":       detail,
        }

    # ── AP Aging Dashboard ─────────────────────────────────────────────────────

    @staticmethod
    def get_ap_aging(db: Session, entity_id: UUID) -> dict:
        rows = db.execute(
            text("""
                SELECT
                    v.vendor_name,
                    api.invoice_number,
                    api.invoice_date,
                    api.due_date,
                    api.total_amount,
                    api.amount_remaining,
                    CURRENT_DATE - api.due_date AS days_overdue
                FROM ap_invoice api
                JOIN vendor v ON v.id = api.vendor_id
                WHERE api.entity_id=:eid
                  AND api.status NOT IN ('paid','cancelled')
                  AND api.amount_remaining > 0
                ORDER BY api.due_date
            """),
            {"eid": str(entity_id)},
        ).fetchall()

        buckets = {"current": Decimal("0"), "1_30": Decimal("0"), "31_60": Decimal("0"),
                   "61_90": Decimal("0"), "over_90": Decimal("0")}
        detail = []

        for r in rows:
            over = int(r.days_overdue) if r.days_overdue else 0
            amt  = Decimal(str(r.amount_remaining))
            bucket = (
                "current" if over <= 0 else
                "1_30"    if over <= 30 else
                "31_60"   if over <= 60 else
                "61_90"   if over <= 90 else "over_90"
            )
            buckets[bucket] += amt
            detail.append({
                "vendor":          r.vendor_name,
                "invoice_number":  r.invoice_number,
                "invoice_date":    str(r.invoice_date),
                "due_date":        str(r.due_date),
                "amount_remaining": float(amt),
                "days_overdue":    over,
                "bucket":          bucket,
            })

        return {
            "buckets":      {k: float(v) for k, v in buckets.items()},
            "total":        float(sum(buckets.values())),
            "invoice_count": len(detail),
            "detail":       detail,
        }

    # ── Working Capital Metrics ───────────────────────────────────────────────

    @staticmethod
    def get_working_capital_metrics(db: Session, entity_id: UUID) -> dict:
        # Revenue last 90 days
        rev = db.execute(
            text("""
                SELECT COALESCE(SUM(gl.credit_idr - gl.debit_idr), 0) AS val
                FROM gl_line gl
                JOIN gl_journal gj         ON gj.id = gl.journal_id AND gj.status='posted'
                JOIN chart_of_accounts coa ON coa.id = gl.account_id
                WHERE gj.entity_id=:eid AND coa.account_type='revenue'
                  AND gj.journal_date >= CURRENT_DATE - INTERVAL '90 days'
            """),
            {"eid": str(entity_id)},
        ).fetchone()
        rev_90 = Decimal(str(rev.val)) if rev else Decimal("1")

        # COGS last 90 days
        cogs_r = db.execute(
            text("""
                SELECT COALESCE(SUM(gl.debit_idr - gl.credit_idr), 0) AS val
                FROM gl_line gl
                JOIN gl_journal gj         ON gj.id = gl.journal_id AND gj.status='posted'
                JOIN chart_of_accounts coa ON coa.id = gl.account_id
                WHERE gj.entity_id=:eid AND coa.account_type='expense'
                  AND (coa.account_subtype ILIKE '%hpp%' OR coa.account_code LIKE '5-%')
                  AND gj.journal_date >= CURRENT_DATE - INTERVAL '90 days'
            """),
            {"eid": str(entity_id)},
        ).fetchone()
        cogs_90 = Decimal(str(cogs_r.val)) if cogs_r else Decimal("1")

        # AR balance
        ar_row = db.execute(
            text("""
                SELECT COALESCE(SUM(amount_remaining), 0) AS val
                FROM ar_invoice
                WHERE entity_id=:eid AND status NOT IN ('paid','cancelled')
            """),
            {"eid": str(entity_id)},
        ).fetchone()
        ar_bal = Decimal(str(ar_row.val)) if ar_row else Decimal("0")

        # AP balance
        ap_row = db.execute(
            text("""
                SELECT COALESCE(SUM(amount_remaining), 0) AS val
                FROM ap_invoice
                WHERE entity_id=:eid AND status NOT IN ('paid','cancelled')
            """),
            {"eid": str(entity_id)},
        ).fetchone()
        ap_bal = Decimal(str(ap_row.val)) if ap_row else Decimal("0")

        # Inventory value
        inv_row = db.execute(
            text("""
                SELECT COALESCE(SUM(pp.current_avg_cost * sv.qty_on_hand), 0) AS val
                FROM product_product pp
                JOIN (
                    SELECT product_id,
                           SUM(CASE WHEN move_type='in' THEN quantity ELSE -quantity END) AS qty_on_hand
                    FROM stock_move
                    WHERE entity_id=:eid
                    GROUP BY product_id
                ) sv ON sv.product_id = pp.id
                WHERE pp.entity_id=:eid AND sv.qty_on_hand > 0
            """),
            {"eid": str(entity_id)},
        ).fetchone()
        inv_val = Decimal(str(inv_row.val)) if inv_row else Decimal("0")

        daily_rev  = rev_90  / 90 if rev_90  > 0 else Decimal("1")
        daily_cogs = cogs_90 / 90 if cogs_90 > 0 else Decimal("1")

        dso = ar_bal  / daily_rev  if daily_rev  > 0 else Decimal("0")
        dpo = ap_bal  / daily_cogs if daily_cogs > 0 else Decimal("0")
        dio = inv_val / daily_cogs if daily_cogs > 0 else Decimal("0")
        ccc = dso + dio - dpo  # Cash Conversion Cycle

        return {
            "dso_days":        float(round(dso, 1)),
            "dpo_days":        float(round(dpo, 1)),
            "dio_days":        float(round(dio, 1)),
            "ccc_days":        float(round(ccc, 1)),
            "ar_balance":      float(ar_bal),
            "ap_balance":      float(ap_bal),
            "inventory_value": float(inv_val),
            "working_capital": float(ar_bal + inv_val - ap_bal),
        }

    # ── Budget vs Actual ──────────────────────────────────────────────────────

    @staticmethod
    def get_budget_vs_actual(
        db: Session,
        entity_id: UUID,
        fiscal_year: int,
        scenario_id: Optional[UUID] = None,
    ) -> list[dict]:
        """Compare actual GL vs forecast scenario for full fiscal year."""
        if not scenario_id:
            # Auto-find baseline scenario for fiscal year
            scen = db.execute(
                text("""
                    SELECT id FROM forecast_scenario
                    WHERE entity_id=:eid AND scenario_type='baseline'
                      AND EXTRACT(YEAR FROM as_of_date)=:yr
                    ORDER BY created_at DESC LIMIT 1
                """),
                {"eid": str(entity_id), "yr": fiscal_year},
            ).fetchone()
            scenario_id = scen.id if scen else None

        rows = db.execute(
            text("""
                SELECT
                    fl.period_year,
                    fl.period_month,
                    fl.line_code,
                    fl.line_name,
                    fl.amount         AS forecast_amount,
                    COALESCE(
                        (SELECT SUM(CASE WHEN coa.account_type='revenue'
                                    THEN gl.credit_idr - gl.debit_idr
                                    ELSE gl.debit_idr - gl.credit_idr END)
                         FROM gl_line gl
                         JOIN gl_journal gj ON gj.id=gl.journal_id AND gj.status='posted'
                         JOIN chart_of_accounts coa ON coa.id=gl.account_id
                         WHERE gj.entity_id=:eid
                           AND EXTRACT(YEAR FROM gj.journal_date)=fl.period_year
                           AND EXTRACT(MONTH FROM gj.journal_date)=fl.period_month
                           AND coa.account_type IN ('revenue','expense')
                        ), 0
                    )                 AS actual_amount
                FROM forecast_line fl
                WHERE fl.scenario_id=:sid
                  AND fl.statement_type='pl'
                  AND fl.line_code IN ('REV_TOTAL','COGS_TOTAL','GROSS_PROFIT','EBITDA','NET_INCOME')
                ORDER BY fl.period_year, fl.period_month, fl.sort_order
            """),
            {"eid": str(entity_id), "sid": str(scenario_id) if scenario_id else None},
        ).fetchall()

        result = []
        for r in rows:
            fc  = Decimal(str(r.forecast_amount))
            act = Decimal(str(r.actual_amount))
            var = act - fc
            var_pct = (var / abs(fc) * 100) if fc != 0 else Decimal("0")
            result.append({
                "period":          f"{r.period_year}-{r.period_month:02d}",
                "line_code":       r.line_code,
                "line_name":       r.line_name,
                "forecast_amount": float(fc),
                "actual_amount":   float(act),
                "variance":        float(var),
                "variance_pct":    float(round(var_pct, 1)),
            })
        return result

    # ── Inventory Turnover (Dagang) ───────────────────────────────────────────

    @staticmethod
    def get_inventory_dashboard(db: Session, entity_id: UUID) -> dict:
        rows = db.execute(
            text("""
                SELECT
                    pp.product_code,
                    pp.product_name,
                    pp.current_avg_cost                                     AS unit_cost,
                    COALESCE(sq.qty_on_hand, 0)                             AS qty_on_hand,
                    pp.current_avg_cost * COALESCE(sq.qty_on_hand, 0)       AS inventory_value,
                    COALESCE(sq.avg_daily_out, 0)                           AS avg_daily_sales,
                    CASE WHEN COALESCE(sq.avg_daily_out, 0) > 0
                         THEN pp.current_avg_cost * COALESCE(sq.qty_on_hand, 0)
                              / (sq.avg_daily_out * pp.current_avg_cost * 30)
                         ELSE 0 END                                         AS inventory_turnover_monthly,
                    CASE WHEN COALESCE(sq.avg_daily_out, 0) > 0
                         THEN COALESCE(sq.qty_on_hand, 0) / sq.avg_daily_out
                         ELSE 9999 END                                      AS days_of_stock
                FROM product_product pp
                LEFT JOIN (
                    SELECT
                        product_id,
                        SUM(CASE WHEN move_type='in' THEN quantity ELSE -quantity END) AS qty_on_hand,
                        AVG(CASE WHEN move_type='out' THEN quantity ELSE 0 END)        AS avg_daily_out
                    FROM stock_move
                    WHERE entity_id=:eid
                    GROUP BY product_id
                ) sq ON sq.product_id = pp.id
                WHERE pp.entity_id=:eid AND pp.active=true
                ORDER BY inventory_value DESC
            """),
            {"eid": str(entity_id)},
        ).fetchall()

        low_stock = [r for r in rows if 0 < float(r.days_of_stock or 0) < 14]
        out_of_stock = [r for r in rows if float(r.qty_on_hand or 0) <= 0]

        total_value = sum(Decimal(str(r.inventory_value or "0")) for r in rows)

        return {
            "total_inventory_value": float(total_value),
            "total_sku":             len(rows),
            "low_stock_count":       len(low_stock),
            "out_of_stock_count":    len(out_of_stock),
            "products": [
                {
                    "product_code":          r.product_code,
                    "product_name":          r.product_name,
                    "qty_on_hand":           float(r.qty_on_hand or 0),
                    "unit_cost":             float(r.unit_cost or 0),
                    "inventory_value":       float(r.inventory_value or 0),
                    "days_of_stock":         float(r.days_of_stock or 9999),
                    "monthly_turnover":      float(r.inventory_turnover_monthly or 0),
                }
                for r in rows
            ],
        }

    # ── Cash Flow Position ────────────────────────────────────────────────────

    @staticmethod
    def get_cashflow_position(db: Session, entity_id: UUID, year: int, month: int) -> dict:
        """MTD actual cash inflows / outflows from bank statement lines."""
        rows = db.execute(
            text("""
                SELECT
                    COALESCE(SUM(bsl.credit_amount), 0) AS total_inflow,
                    COALESCE(SUM(bsl.debit_amount),  0) AS total_outflow
                FROM bank_statement_line bsl
                JOIN bank_statement bs ON bs.id = bsl.statement_id
                JOIN bank_account ba   ON ba.id = bs.bank_account_id
                WHERE ba.entity_id = :eid
                  AND bs.statement_period_year  = :yr
                  AND bs.statement_period_month = :mo
            """),
            {"eid": str(entity_id), "yr": year, "mo": month},
        ).fetchone()

        inflow  = Decimal(str(rows.total_inflow))  if rows else Decimal("0")
        outflow = Decimal(str(rows.total_outflow)) if rows else Decimal("0")

        return {
            "period":        f"{year}-{month:02d}",
            "total_inflow":  float(inflow),
            "total_outflow": float(outflow),
            "net_cashflow":  float(inflow - outflow),
        }

    # ── Revenue by Customer / Project ─────────────────────────────────────────

    @staticmethod
    def get_revenue_breakdown(
        db: Session,
        entity_id: UUID,
        year: int,
        month: int,
        group_by: str = "customer",   # customer | project | product
    ) -> list[dict]:
        if group_by == "customer":
            rows = db.execute(
                text("""
                    SELECT
                        COALESCE(c.customer_name, 'Unknown') AS label,
                        COALESCE(SUM(ail.subtotal), 0)       AS amount
                    FROM ar_invoice ai
                    JOIN ar_invoice_line ail ON ail.invoice_id = ai.id
                    LEFT JOIN customer c ON c.id = ai.customer_id
                    WHERE ai.entity_id=:eid
                      AND EXTRACT(YEAR  FROM ai.invoice_date)=:yr
                      AND EXTRACT(MONTH FROM ai.invoice_date)=:mo
                      AND ai.status NOT IN ('cancelled','draft')
                    GROUP BY c.customer_name
                    ORDER BY amount DESC
                    LIMIT 15
                """),
                {"eid": str(entity_id), "yr": year, "mo": month},
            ).fetchall()
        elif group_by == "project":
            rows = db.execute(
                text("""
                    SELECT
                        COALESCE(p.project_name, 'Tanpa Proyek') AS label,
                        COALESCE(SUM(gl.credit_idr - gl.debit_idr), 0) AS amount
                    FROM gl_line gl
                    JOIN gl_journal gj         ON gj.id = gl.journal_id AND gj.status='posted'
                    JOIN chart_of_accounts coa ON coa.id = gl.account_id
                    LEFT JOIN project p        ON p.id = gl.project_id
                    WHERE gj.entity_id=:eid AND coa.account_type='revenue'
                      AND EXTRACT(YEAR  FROM gj.journal_date)=:yr
                      AND EXTRACT(MONTH FROM gj.journal_date)=:mo
                    GROUP BY p.project_name
                    ORDER BY amount DESC LIMIT 15
                """),
                {"eid": str(entity_id), "yr": year, "mo": month},
            ).fetchall()
        else:
            rows = db.execute(
                text("""
                    SELECT
                        pp.product_name AS label,
                        COALESCE(SUM(ail.subtotal), 0) AS amount
                    FROM ar_invoice ai
                    JOIN ar_invoice_line ail ON ail.invoice_id = ai.id
                    JOIN product_product pp  ON pp.id = ail.product_id
                    WHERE ai.entity_id=:eid
                      AND EXTRACT(YEAR  FROM ai.invoice_date)=:yr
                      AND EXTRACT(MONTH FROM ai.invoice_date)=:mo
                      AND ai.status NOT IN ('cancelled','draft')
                    GROUP BY pp.product_name
                    ORDER BY amount DESC LIMIT 15
                """),
                {"eid": str(entity_id), "yr": year, "mo": month},
            ).fetchall()

        total = sum(Decimal(str(r.amount)) for r in rows)
        return [
            {
                "label":   r.label,
                "amount":  float(r.amount),
                "pct":     float(round(Decimal(str(r.amount)) / total * 100, 1)) if total > 0 else 0.0,
            }
            for r in rows
        ]

    # ── Expense Breakdown by Category ─────────────────────────────────────────

    @staticmethod
    def get_expense_breakdown(db: Session, entity_id: UUID, year: int, month: int) -> list[dict]:
        rows = db.execute(
            text("""
                SELECT
                    COALESCE(coa.account_subtype, coa.account_code) AS label,
                    COALESCE(SUM(gl.debit_idr - gl.credit_idr), 0) AS amount
                FROM gl_line gl
                JOIN gl_journal gj         ON gj.id = gl.journal_id AND gj.status='posted'
                JOIN chart_of_accounts coa ON coa.id = gl.account_id
                WHERE gj.entity_id=:eid AND coa.account_type='expense'
                  AND EXTRACT(YEAR  FROM gj.journal_date)=:yr
                  AND EXTRACT(MONTH FROM gj.journal_date)=:mo
                GROUP BY coa.account_subtype, coa.account_code
                ORDER BY amount DESC LIMIT 20
            """),
            {"eid": str(entity_id), "yr": year, "mo": month},
        ).fetchall()

        total = sum(Decimal(str(r.amount)) for r in rows)
        return [
            {
                "category": r.label,
                "amount":   float(r.amount),
                "pct":      float(round(Decimal(str(r.amount)) / total * 100, 1)) if total > 0 else 0.0,
            }
            for r in rows
        ]

    # ── Full Dashboard (single endpoint) ─────────────────────────────────────

    @staticmethod
    def get_full_dashboard(
        db: Session,
        entity_id: UUID,
        as_of_date,
        scenario_id: Optional[UUID] = None,
    ) -> dict:
        yr = as_of_date.year
        mo = as_of_date.month

        kpis     = DashboardEngine.get_kpi_cards(db, entity_id, as_of_date)
        trend    = DashboardEngine.get_pl_monthly_trend(db, entity_id, 12)
        ar_aging = DashboardEngine.get_ar_aging(db, entity_id)
        ap_aging = DashboardEngine.get_ap_aging(db, entity_id)
        wc       = DashboardEngine.get_working_capital_metrics(db, entity_id)
        cf_pos   = DashboardEngine.get_cashflow_position(db, entity_id, yr, mo)
        rev_by_cust = DashboardEngine.get_revenue_breakdown(db, entity_id, yr, mo, "customer")
        exp_brkdn   = DashboardEngine.get_expense_breakdown(db, entity_id, yr, mo)

        bva = []
        if scenario_id:
            bva = DashboardEngine.get_budget_vs_actual(db, entity_id, yr, scenario_id)

        return {
            "kpi_cards":         kpis,
            "pl_trend_12m":      trend,
            "ar_aging":          ar_aging,
            "ap_aging":          ap_aging,
            "working_capital":   wc,
            "cashflow_position": cf_pos,
            "revenue_by_customer": rev_by_cust,
            "expense_breakdown": exp_brkdn,
            "budget_vs_actual":  bva,
        }
