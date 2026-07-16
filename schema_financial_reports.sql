-- ============================================================
-- FINANCIAL REPORTS — Views tambahan
-- Jalankan: psql -U postgres -d accounting_db -f schema_financial_reports.sql
-- ============================================================


-- ============================================================
-- 1. VIEW P&L — Laba Rugi per Bulan
-- ============================================================
-- Grouping: per entity + per periode (tahun + bulan) + per akun
-- Gunakan dari API dengan filter entity_id + year + from_month + to_month

CREATE OR REPLACE VIEW vw_profit_loss AS
SELECT
    j.entity_id,
    fp.year,
    fp.month,
    fp.period_name,
    coa.account_code,
    coa.account_name,
    coa.account_type,
    COALESCE(SUM(gl.debit_idr),  0)   AS total_debit,
    COALESCE(SUM(gl.credit_idr), 0)   AS total_credit,
    CASE coa.account_type
        WHEN 'revenue' THEN COALESCE(SUM(gl.credit_idr), 0) - COALESCE(SUM(gl.debit_idr), 0)
        WHEN 'expense' THEN COALESCE(SUM(gl.debit_idr),  0) - COALESCE(SUM(gl.credit_idr), 0)
        ELSE 0
    END                               AS amount
FROM chart_of_accounts coa
JOIN gl_line    gl ON gl.account_id = coa.id
JOIN gl_journal j  ON j.id = gl.journal_id AND j.status = 'posted'
JOIN fiscal_period fp ON fp.id = j.period_id
WHERE coa.account_type IN ('revenue', 'expense')
GROUP BY
    j.entity_id, fp.year, fp.month, fp.period_name,
    coa.account_code, coa.account_name, coa.account_type;

COMMENT ON VIEW vw_profit_loss IS
    'P&L per periode: revenue dan expense per akun. Filter by entity_id + year + month dari API.';


-- ============================================================
-- 2. VIEW BALANCE SHEET — Neraca Kumulatif
-- ============================================================
-- Saldo kumulatif semua posted journal per akun neraca.
-- Gunakan bersama filter entity_id dari API.
-- Untuk balance sheet per tanggal tertentu, filter gl_journal.journal_date di query API.

CREATE OR REPLACE VIEW vw_balance_sheet AS
SELECT
    coa.entity_id,
    coa.account_code,
    coa.account_name,
    coa.account_type,
    coa.normal_balance,
    coa.parent_id,
    coa.level,
    COALESCE(SUM(gl.debit_idr),  0)   AS total_debit,
    COALESCE(SUM(gl.credit_idr), 0)   AS total_credit,
    CASE coa.normal_balance
        WHEN 'debit'  THEN COALESCE(SUM(gl.debit_idr),  0) - COALESCE(SUM(gl.credit_idr), 0)
        WHEN 'credit' THEN COALESCE(SUM(gl.credit_idr), 0) - COALESCE(SUM(gl.debit_idr),  0)
    END                               AS balance
FROM chart_of_accounts coa
LEFT JOIN gl_line    gl ON gl.account_id = coa.id
LEFT JOIN gl_journal j  ON j.id = gl.journal_id AND j.status = 'posted'
WHERE coa.account_type IN (
    'asset', 'liability', 'equity',
    'prepaid', 'fixed_asset', 'accumulated_depreciation'
)
GROUP BY
    coa.entity_id, coa.account_code, coa.account_name,
    coa.account_type, coa.normal_balance, coa.parent_id, coa.level;

COMMENT ON VIEW vw_balance_sheet IS
    'Neraca kumulatif per akun. Filter by entity_id dari API.';


-- ============================================================
-- 3. VIEW AR AGING DETAIL — Per Invoice
-- ============================================================

CREATE OR REPLACE VIEW vw_ar_aging AS
SELECT
    ai.entity_id,
    ai.customer_name,
    ai.customer_npwp,
    ai.invoice_no,
    ai.invoice_date,
    ai.due_date,
    ai.total_amount,
    ai.paid_amount,
    (ai.total_amount - ai.paid_amount)       AS outstanding,
    (CURRENT_DATE - ai.due_date)             AS days_overdue,
    CASE
        WHEN (CURRENT_DATE - ai.due_date) <= 0   THEN 'current'
        WHEN (CURRENT_DATE - ai.due_date) <= 30  THEN '1-30 days'
        WHEN (CURRENT_DATE - ai.due_date) <= 60  THEN '31-60 days'
        WHEN (CURRENT_DATE - ai.due_date) <= 90  THEN '61-90 days'
        ELSE '>90 days'
    END                                      AS aging_bucket
FROM ar_invoice ai
WHERE ai.status NOT IN ('paid', 'cancelled');

COMMENT ON VIEW vw_ar_aging IS
    'AR aging detail per invoice. Pasangan dari vw_ap_aging untuk sisi piutang.';


-- ============================================================
-- 4. VIEW CASH FLOW SIMPLIFIED — Indirect Method
-- ============================================================
-- Simplified: operating cash berdasarkan net income + penyesuaian non-kas.
-- Perubahan modal kerja (AR/AP) dihitung dari saldo awal vs akhir periode.

CREATE OR REPLACE VIEW vw_cash_flow_components AS
SELECT
    j.entity_id,
    fp.year,
    fp.month,
    fp.period_name,
    j.journal_type,
    coa.account_type,
    CASE
        -- Operating: P&L accounts + perubahan AR/AP/prepaid
        WHEN coa.account_type IN ('revenue', 'expense')                    THEN 'operating'
        WHEN coa.account_code LIKE '1-1-%' AND j.journal_type = 'AR'      THEN 'operating'  -- piutang
        WHEN coa.account_code LIKE '2-1-%' AND j.journal_type = 'AP'      THEN 'operating'  -- hutang
        WHEN coa.account_type = 'prepaid'                                  THEN 'operating'
        -- Investing: aset tetap
        WHEN coa.account_type IN ('fixed_asset', 'accumulated_depreciation') THEN 'investing'
        -- Financing: equity changes, bank loans
        WHEN coa.account_type = 'equity'                                   THEN 'financing'
        ELSE 'other'
    END                                                                    AS cash_flow_category,
    COALESCE(SUM(gl.debit_idr),  0)   AS total_debit,
    COALESCE(SUM(gl.credit_idr), 0)   AS total_credit
FROM chart_of_accounts coa
JOIN gl_line    gl ON gl.account_id = coa.id
JOIN gl_journal j  ON j.id = gl.journal_id AND j.status = 'posted'
JOIN fiscal_period fp ON fp.id = j.period_id
GROUP BY
    j.entity_id, fp.year, fp.month, fp.period_name,
    j.journal_type, coa.account_type, coa.account_code;

COMMENT ON VIEW vw_cash_flow_components IS
    'Komponen cash flow per kategori (operating/investing/financing). Digunakan API untuk laporan arus kas.';


-- ============================================================
-- 5. INDEX TAMBAHAN (untuk performance laporan)
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_gl_journal_type    ON gl_journal(journal_type);
CREATE INDEX IF NOT EXISTS idx_gl_line_account_j  ON gl_line(account_id, journal_id);
CREATE INDEX IF NOT EXISTS idx_ar_invoice_entity  ON ar_invoice(entity_id, status);
CREATE INDEX IF NOT EXISTS idx_ar_receipt_entity  ON ar_receipt(entity_id);
CREATE INDEX IF NOT EXISTS idx_fixed_asset_entity ON fixed_asset(entity_id, status);
CREATE INDEX IF NOT EXISTS idx_prepaid_entity     ON prepaid_expense(entity_id, status);
CREATE INDEX IF NOT EXISTS idx_asset_dep_posted   ON asset_depreciation_schedule(asset_id, is_posted, period_date);
CREATE INDEX IF NOT EXISTS idx_prepaid_amort      ON prepaid_amortization_schedule(prepaid_id, is_posted, period_date);

SELECT 'Financial reports views created successfully' AS status;
