-- ============================================================
-- SCHEMA: Financial Forecasting & FP&A Engine
--
-- Arsitektur Three-Way Forecast:
--   P&L Forecast ──► Cash Flow Forecast ──► Balance Sheet Forecast
--
-- Tiga pendekatan per jenis perusahaan:
--   jasa        : Driver manpower + contract backlog + DSO collection
--   dagang      : Inventory movement + sales trend + DSO/DPO/DIO
--   konstruksi  : Percentage of Completion (PoC) + milestone billing + RAB
--
-- Rolling Forecast: setiap bulan lock actuals, tambah 1 bulan baru
-- What-If: clone scenario → override assumptions → re-run
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_forecast.sql
-- ============================================================

-- ── 1. FORECAST SCENARIO ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS forecast_scenario (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    scenario_name   VARCHAR(200) NOT NULL,
    scenario_type   VARCHAR(20)  NOT NULL DEFAULT 'baseline'
        CHECK (scenario_type IN ('baseline','optimistic','pessimistic','what_if','rolling')),
    company_type    VARCHAR(20)  NOT NULL DEFAULT 'jasa'
        CHECK (company_type IN ('jasa','dagang','konstruksi','manufaktur')),
    as_of_date      DATE         NOT NULL,       -- tanggal mulai forecast (biasanya awal bulan ini)
    forecast_months SMALLINT     NOT NULL DEFAULT 12,
    base_scenario_id UUID        REFERENCES forecast_scenario(id),  -- untuk what-if clone
    status          VARCHAR(20)  NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','computing','computed','archived')),
    last_computed_at TIMESTAMPTZ,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (entity_id, scenario_name, as_of_date)
);
CREATE INDEX IF NOT EXISTS idx_fscen_entity ON forecast_scenario(entity_id, status);


-- ── 2. FORECAST ASSUMPTION — parameter kunci per scenario ────────────────────
-- Keys untuk jasa:     dso_days, revenue_growth_pct, labor_growth_pct, win_rate_pct,
--                      benefits_pct, income_tax_rate_pct, overhead_growth_pct
-- Keys untuk dagang:   dso_days, dpo_days, dio_days, gross_margin_pct,
--                      revenue_growth_pct, cogs_inflation_pct, income_tax_rate_pct
-- Keys untuk konstruksi: poc_method (cost_to_cost|physical), retention_release_months,
--                        cost_overrun_buffer_pct, subcon_dpo_days, income_tax_rate_pct
-- Shared:              capex_monthly_avg, discount_rate_pct, escalation_pct_pa
CREATE TABLE IF NOT EXISTS forecast_assumption (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    scenario_id     UUID         NOT NULL REFERENCES forecast_scenario(id) ON DELETE CASCADE,
    param_key       VARCHAR(60)  NOT NULL,
    param_value     NUMERIC(14,4) NOT NULL,
    param_unit      VARCHAR(20)  NOT NULL DEFAULT 'number'
        CHECK (param_unit IN ('number','pct','days','months','idr')),
    description     TEXT,
    UNIQUE (scenario_id, param_key)
);


-- ── 3. FORECAST LINE — output tiga laporan per periode ───────────────────────
-- statement_type: pl = Laba Rugi, cf = Arus Kas, bs = Neraca
-- line_code: kode baris standar (lihat mapping di engine)
CREATE TABLE IF NOT EXISTS forecast_line (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    scenario_id     UUID         NOT NULL REFERENCES forecast_scenario(id) ON DELETE CASCADE,
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    period_year     SMALLINT     NOT NULL,
    period_month    SMALLINT     NOT NULL CHECK (period_month BETWEEN 1 AND 12),
    statement_type  VARCHAR(5)   NOT NULL CHECK (statement_type IN ('pl','cf','bs')),
    line_code       VARCHAR(40)  NOT NULL,
    line_name       VARCHAR(200) NOT NULL,
    sort_order      SMALLINT     NOT NULL DEFAULT 0,
    line_category   VARCHAR(30),  -- revenue/cogs/gross_profit/opex/ebitda/ebit/tax/net_income/etc.
    amount          NUMERIC(18,2) NOT NULL DEFAULT 0,  -- forecast amount
    amount_actual   NUMERIC(18,2),                     -- filled from GL after month closes
    is_actual       BOOLEAN      NOT NULL DEFAULT FALSE,
    driver_source   VARCHAR(50),  -- contract/hr/inventory/trend/manual/construction_poc
    UNIQUE (scenario_id, period_year, period_month, statement_type, line_code)
);
CREATE INDEX IF NOT EXISTS idx_fline_scenario ON forecast_line(scenario_id, statement_type);
CREATE INDEX IF NOT EXISTS idx_fline_period   ON forecast_line(entity_id, period_year, period_month);


-- ── 4. FORECAST DRIVER OVERRIDE — manual override per baris ──────────────────
-- Untuk What-If: ubah satu angka spesifik tanpa re-run full forecast
CREATE TABLE IF NOT EXISTS forecast_driver_override (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    scenario_id     UUID         NOT NULL REFERENCES forecast_scenario(id) ON DELETE CASCADE,
    period_year     SMALLINT     NOT NULL,
    period_month    SMALLINT     NOT NULL,
    statement_type  VARCHAR(5)   NOT NULL,
    line_code       VARCHAR(40)  NOT NULL,
    override_amount NUMERIC(18,2) NOT NULL,
    override_reason TEXT,
    overridden_by   VARCHAR(200),
    overridden_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (scenario_id, period_year, period_month, statement_type, line_code)
);


-- ── 5. VIEWS ─────────────────────────────────────────────────────────────────

-- Three-Way P&L Forecast (gabung semua baris P&L per scenario per periode)
CREATE OR REPLACE VIEW vw_forecast_pl AS
SELECT
    fl.scenario_id,
    fs.scenario_name,
    fs.company_type,
    fl.entity_id,
    fl.period_year,
    fl.period_month,
    fl.line_code,
    fl.line_name,
    fl.sort_order,
    fl.line_category,
    fl.amount              AS forecast_amount,
    fl.amount_actual       AS actual_amount,
    fl.is_actual,
    COALESCE(fl.amount_actual, fl.amount) AS effective_amount,
    CASE WHEN fl.amount_actual IS NOT NULL AND fl.amount != 0
         THEN ROUND((fl.amount_actual - fl.amount) / ABS(fl.amount) * 100, 1)
         ELSE NULL
    END AS variance_pct,
    fl.driver_source
FROM forecast_line fl
JOIN forecast_scenario fs ON fs.id = fl.scenario_id
WHERE fl.statement_type = 'pl'
ORDER BY fl.scenario_id, fl.period_year, fl.period_month, fl.sort_order;


-- Three-Way Cash Flow Forecast
CREATE OR REPLACE VIEW vw_forecast_cf AS
SELECT
    fl.scenario_id,
    fs.scenario_name,
    fl.entity_id,
    fl.period_year,
    fl.period_month,
    fl.line_code,
    fl.line_name,
    fl.sort_order,
    fl.line_category,
    fl.amount              AS forecast_amount,
    fl.amount_actual       AS actual_amount,
    COALESCE(fl.amount_actual, fl.amount) AS effective_amount,
    fl.is_actual,
    fl.driver_source
FROM forecast_line fl
JOIN forecast_scenario fs ON fs.id = fl.scenario_id
WHERE fl.statement_type = 'cf'
ORDER BY fl.scenario_id, fl.period_year, fl.period_month, fl.sort_order;


-- Balance Sheet Forecast
CREATE OR REPLACE VIEW vw_forecast_bs AS
SELECT
    fl.scenario_id,
    fs.scenario_name,
    fl.entity_id,
    fl.period_year,
    fl.period_month,
    fl.line_code,
    fl.line_name,
    fl.sort_order,
    fl.line_category,
    fl.amount              AS forecast_amount,
    fl.amount_actual       AS actual_amount,
    COALESCE(fl.amount_actual, fl.amount) AS effective_amount,
    fl.is_actual
FROM forecast_line fl
JOIN forecast_scenario fs ON fs.id = fl.scenario_id
WHERE fl.statement_type = 'bs'
ORDER BY fl.scenario_id, fl.period_year, fl.period_month, fl.sort_order;


-- Consolidated Pivot: revenue, gross_profit, ebitda, net_income per period
CREATE OR REPLACE VIEW vw_forecast_summary AS
SELECT
    fl.scenario_id,
    fs.scenario_name,
    fs.company_type,
    fl.entity_id,
    fl.period_year,
    fl.period_month,
    COALESCE(SUM(COALESCE(fl.amount_actual, fl.amount)) FILTER (WHERE fl.line_code = 'REV_TOTAL'),  0) AS revenue,
    COALESCE(SUM(COALESCE(fl.amount_actual, fl.amount)) FILTER (WHERE fl.line_code = 'COGS_TOTAL'), 0) AS cogs,
    COALESCE(SUM(COALESCE(fl.amount_actual, fl.amount)) FILTER (WHERE fl.line_code = 'GROSS_PROFIT'),0) AS gross_profit,
    COALESCE(SUM(COALESCE(fl.amount_actual, fl.amount)) FILTER (WHERE fl.line_code = 'EBITDA'),      0) AS ebitda,
    COALESCE(SUM(COALESCE(fl.amount_actual, fl.amount)) FILTER (WHERE fl.line_code = 'NET_INCOME'),  0) AS net_income,
    COALESCE(SUM(COALESCE(fl.amount_actual, fl.amount)) FILTER (WHERE fl.line_code = 'CF_CLOSE'),    0) AS cash_closing,
    fl.is_actual
FROM forecast_line fl
JOIN forecast_scenario fs ON fs.id = fl.scenario_id
WHERE fl.statement_type IN ('pl','cf')
GROUP BY fl.scenario_id, fs.scenario_name, fs.company_type, fl.entity_id,
         fl.period_year, fl.period_month, fl.is_actual;


-- What-If variance comparison (base vs what_if)
CREATE OR REPLACE VIEW vw_what_if_comparison AS
SELECT
    b.entity_id,
    b.period_year,
    b.period_month,
    b.statement_type,
    b.line_code,
    b.line_name,
    b.amount         AS base_amount,
    w.amount         AS what_if_amount,
    w.amount - b.amount AS delta,
    CASE WHEN b.amount != 0
         THEN ROUND((w.amount - b.amount) / ABS(b.amount) * 100, 1)
         ELSE NULL
    END AS delta_pct,
    b.scenario_id    AS base_scenario_id,
    w.scenario_id    AS what_if_scenario_id
FROM forecast_line b
JOIN forecast_scenario fs_b ON fs_b.id = b.scenario_id AND fs_b.scenario_type = 'baseline'
JOIN forecast_scenario fs_w ON fs_w.base_scenario_id = fs_b.id AND fs_w.scenario_type = 'what_if'
JOIN forecast_line w ON w.scenario_id     = fs_w.id
                    AND w.entity_id       = b.entity_id
                    AND w.period_year     = b.period_year
                    AND w.period_month    = b.period_month
                    AND w.statement_type  = b.statement_type
                    AND w.line_code       = b.line_code;


SELECT 'Migration schema_forecast selesai' AS status;
