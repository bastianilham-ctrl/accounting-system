-- ============================================================
-- SCHEMA: Year-End Closing
--
-- Konsep:
--   1. Pre-closing checks: validasi sebelum tutup buku
--      - Semua jurnal bulan terakhir sudah diposting
--      - Tidak ada AP/AR outstanding yang aneh
--      - Depresiasi sudah dijalankan
--      - Bank recon sudah final
--   2. Income Summary Entry: menutup akun revenue & expense ke akun
--      "Ikhtisar Laba Rugi" (clearing account)
--   3. Retained Earnings Transfer: pindahkan net income dari Ikhtisar
--      ke akun Laba Ditahan / Retained Earnings
--   4. Period Lock: lock semua periode dalam fiscal year yang ditutup
--      (tidak bisa post jurnal baru ke periode terkunci)
--   5. Opening Balance Next Year: carry forward balance sheet ke tahun baru
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_year_end_closing.sql
-- ============================================================

-- ── 1. FISCAL YEAR ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS fiscal_year (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    fiscal_year     SMALLINT     NOT NULL,
    start_date      DATE         NOT NULL,
    end_date        DATE         NOT NULL,
    status          VARCHAR(20)  NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','closing','closed')),
    closed_by       VARCHAR(200),
    closed_at       TIMESTAMPTZ,
    closing_journal_id   UUID    REFERENCES gl_journal(id),  -- Income Summary journal
    transfer_journal_id  UUID    REFERENCES gl_journal(id),  -- RE Transfer journal
    UNIQUE (entity_id, fiscal_year)
);


-- ── 2. FISCAL PERIOD (per bulan dalam tahun fiskal) ──────────────────────────
CREATE TABLE IF NOT EXISTS fiscal_period (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    fiscal_year_id  UUID         NOT NULL REFERENCES fiscal_year(id),
    period_year     SMALLINT     NOT NULL,
    period_month    SMALLINT     NOT NULL CHECK (period_month BETWEEN 1 AND 12),
    start_date      DATE         NOT NULL,
    end_date        DATE         NOT NULL,
    status          VARCHAR(20)  NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','locked')),
    locked_by       VARCHAR(200),
    locked_at       TIMESTAMPTZ,
    UNIQUE (entity_id, period_year, period_month)
);
CREATE INDEX IF NOT EXISTS idx_fperiod_entity ON fiscal_period(entity_id, status);


-- ── 3. YEAR-END CLOSING LOG ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS year_end_closing_log (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    fiscal_year     SMALLINT     NOT NULL,
    step            VARCHAR(50)  NOT NULL,   -- pre_check, income_summary, re_transfer, lock_periods
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','running','done','failed')),
    result_summary  JSONB,
    error_message   TEXT,
    executed_by     VARCHAR(200),
    executed_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_yec_entity ON year_end_closing_log(entity_id, fiscal_year);


-- ── 4. PRE-CLOSING CHECKLIST ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS closing_checklist (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    fiscal_year     SMALLINT     NOT NULL,
    check_item      VARCHAR(100) NOT NULL,
    check_result    VARCHAR(20)  NOT NULL DEFAULT 'pending'
        CHECK (check_result IN ('pending','pass','warn','fail')),
    detail          TEXT,
    checked_at      TIMESTAMPTZ,
    UNIQUE (entity_id, fiscal_year, check_item)
);


-- ── 5. VIEWS ─────────────────────────────────────────────────────────────────

-- Status fiscal year + progres penutupan
CREATE OR REPLACE VIEW vw_fiscal_year_status AS
SELECT
    fy.id AS fiscal_year_id,
    fy.entity_id,
    fy.fiscal_year,
    fy.start_date,
    fy.end_date,
    fy.status,
    fy.closed_at,
    COUNT(fp.id)                                             AS total_periods,
    COUNT(fp.id) FILTER (WHERE fp.status = 'locked')        AS locked_periods,
    COUNT(fp.id) FILTER (WHERE fp.status = 'open')          AS open_periods
FROM fiscal_year fy
LEFT JOIN fiscal_period fp ON fp.fiscal_year_id = fy.id
GROUP BY fy.id;


-- Revenue & Expense accounts balance for closing
CREATE OR REPLACE VIEW vw_income_expense_balance AS
SELECT
    gj.entity_id,
    EXTRACT(YEAR FROM gj.journal_date)::SMALLINT AS fiscal_year,
    coa.id AS account_id,
    coa.account_code,
    coa.account_name,
    coa.account_type,
    coa.normal_balance,
    COALESCE(SUM(gl.debit_idr),  0) AS total_debit,
    COALESCE(SUM(gl.credit_idr), 0) AS total_credit,
    CASE coa.normal_balance
        WHEN 'credit' THEN COALESCE(SUM(gl.credit_idr),0) - COALESCE(SUM(gl.debit_idr),0)
        WHEN 'debit'  THEN COALESCE(SUM(gl.debit_idr),0)  - COALESCE(SUM(gl.credit_idr),0)
    END AS net_balance
FROM gl_line gl
JOIN gl_journal gj         ON gj.id = gl.journal_id AND gj.status = 'posted'
JOIN chart_of_accounts coa ON coa.id = gl.account_id
WHERE coa.account_type IN ('revenue','expense')
GROUP BY gj.entity_id, EXTRACT(YEAR FROM gj.journal_date), coa.id,
         coa.account_code, coa.account_name, coa.account_type, coa.normal_balance;


SELECT 'Migration schema_year_end_closing selesai' AS status;
