-- ============================================================
-- CUSTOM JOURNAL ENGINE & VENDOR ENRICHMENT SYSTEM
-- Schema PostgreSQL v1.0
-- Author  : Ahmad Jaenal Ilham
-- Created : 2026-05-30
-- ============================================================

-- ============================================================
-- 0. EXTENSIONS
-- ============================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "unaccent";


-- ============================================================
-- 1. MASTER DATA — ENTITY & PERIOD
-- ============================================================

CREATE TABLE entity (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code            VARCHAR(20)  NOT NULL UNIQUE,
    name            VARCHAR(200) NOT NULL,
    npwp            VARCHAR(30),
    currency        CHAR(3)      NOT NULL DEFAULT 'IDR',
    country         CHAR(2)      NOT NULL DEFAULT 'ID',
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE entity IS 'Multi-entity support: setiap perusahaan/entitas dalam group';

CREATE TABLE fiscal_period (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    period_name     VARCHAR(20)  NOT NULL,          -- e.g. '2026-01'
    year            SMALLINT     NOT NULL,
    month           SMALLINT     NOT NULL CHECK (month BETWEEN 1 AND 12),
    date_start      DATE         NOT NULL,
    date_end        DATE         NOT NULL,
    is_locked       BOOLEAN      NOT NULL DEFAULT FALSE,
    locked_by       VARCHAR(100),
    locked_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (entity_id, year, month)
);
COMMENT ON TABLE fiscal_period IS 'Period lock: jurnal tidak bisa diposting ke period yang sudah dikunci';


-- ============================================================
-- 2. CHART OF ACCOUNTS (COA)
-- ============================================================

CREATE TYPE account_type AS ENUM (
    'asset', 'liability', 'equity', 'revenue', 'expense',
    'prepaid', 'fixed_asset', 'accumulated_depreciation'
);

CREATE TYPE account_normal_balance AS ENUM ('debit', 'credit');

CREATE TABLE chart_of_accounts (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    account_code    VARCHAR(20)  NOT NULL,
    account_name    VARCHAR(200) NOT NULL,
    account_type    account_type NOT NULL,
    normal_balance  account_normal_balance NOT NULL,
    parent_id       UUID         REFERENCES chart_of_accounts(id),
    level           SMALLINT     NOT NULL DEFAULT 1,
    is_header       BOOLEAN      NOT NULL DEFAULT FALSE,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    tax_object      VARCHAR(50),   -- e.g. 'PPh23', 'PPh4(2)', 'PPN_PM', 'non_taxable'
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (entity_id, account_code)
);
COMMENT ON TABLE chart_of_accounts IS 'COA multi-level dengan flagging objek pajak per akun';


-- ============================================================
-- 3. FX RATE
-- ============================================================

CREATE TABLE fx_rate (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    from_currency   CHAR(3)      NOT NULL,
    to_currency     CHAR(3)      NOT NULL DEFAULT 'IDR',
    rate_date       DATE         NOT NULL,
    rate            NUMERIC(18,6) NOT NULL,
    source          VARCHAR(50)  DEFAULT 'BI_KURS_TENGAH',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (from_currency, to_currency, rate_date)
);
COMMENT ON TABLE fx_rate IS 'Kurs BI tengah per tanggal untuk konversi multi-currency';


-- ============================================================
-- 4. VENDOR MASTER & SCRAPER ENRICHMENT
-- ============================================================

CREATE TYPE vendor_tax_status AS ENUM ('PKP', 'non_PKP', 'unknown');
CREATE TYPE vendor_scrape_status AS ENUM ('pending', 'success', 'failed', 'manual');

CREATE TABLE vendor (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    vendor_code     VARCHAR(30)  NOT NULL,
    vendor_name     VARCHAR(300) NOT NULL,
    npwp            VARCHAR(30),
    nib             VARCHAR(30),                    -- OSS NIB
    kbli            VARCHAR(10),                    -- Klasifikasi Bidang Usaha
    klu             VARCHAR(10),                    -- Klasifikasi Lapangan Usaha DJP
    tax_status      vendor_tax_status NOT NULL DEFAULT 'unknown',
    is_foreign      BOOLEAN      NOT NULL DEFAULT FALSE,
    country         CHAR(2)      DEFAULT 'ID',
    website         VARCHAR(300),
    email           VARCHAR(200),
    phone           VARCHAR(50),
    address         TEXT,
    -- Enrichment metadata
    scrape_status   vendor_scrape_status NOT NULL DEFAULT 'pending',
    last_scraped_at TIMESTAMPTZ,
    scrape_notes    TEXT,
    -- Default tax mapping (di-override oleh tax_advice_engine jika perlu)
    default_pph_type    VARCHAR(20),               -- e.g. 'PPh23', 'PPh4(2)', 'PPh21'
    default_pph_rate    NUMERIC(5,2),              -- e.g. 2.00 untuk PPh23 2%
    default_ppn_eligible BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (entity_id, vendor_code)
);
COMMENT ON TABLE vendor IS 'Master vendor dengan enrichment data dari DJP, OSS, dan website scraping';

CREATE TABLE vendor_services (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    vendor_id       UUID         NOT NULL REFERENCES vendor(id) ON DELETE CASCADE,
    service_name    VARCHAR(300) NOT NULL,
    service_category VARCHAR(100),                 -- e.g. 'IT Services', 'Sewa', 'Jasa Konsultansi'
    source          VARCHAR(50)  NOT NULL,          -- 'website', 'OSS', 'DJP', 'manual', 'AI'
    pph_object      VARCHAR(30),                   -- 'PPh23_jasa_teknik', 'PPh4(2)_sewa'
    pph_rate        NUMERIC(5,2),
    coa_suggestion  VARCHAR(20),                   -- account_code default untuk jasa ini
    confidence      NUMERIC(4,2) DEFAULT 1.00,     -- 0-1: confidence score dari AI
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE vendor_services IS 'Daftar layanan vendor hasil scraping untuk auto-klasifikasi akun dan pajak';

CREATE TABLE vendor_scrape_log (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    vendor_id       UUID         NOT NULL REFERENCES vendor(id),
    scrape_source   VARCHAR(50)  NOT NULL,          -- 'DJP', 'OSS', 'website', 'AI'
    scrape_url      TEXT,
    status          VARCHAR(20)  NOT NULL,
    raw_data        JSONB,
    error_message   TEXT,
    scraped_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE vendor_scrape_log IS 'Log setiap proses scraping vendor untuk audit trail';


-- ============================================================
-- 5. JOURNAL ENGINE — HEADER & LINE
-- ============================================================

CREATE TYPE journal_status AS ENUM ('draft', 'posted', 'reversed', 'cancelled');
CREATE TYPE journal_type   AS ENUM ('AP', 'AR', 'GL', 'BANK', 'ASSET', 'PREPAID', 'PAYROLL', 'MANUAL');

CREATE TABLE gl_journal (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    period_id       UUID         NOT NULL REFERENCES fiscal_period(id),
    journal_no      VARCHAR(50)  NOT NULL UNIQUE,   -- auto-generated: AP/2026/05/0001
    journal_type    journal_type NOT NULL,
    journal_date    DATE         NOT NULL,
    description     TEXT         NOT NULL,
    reference_no    VARCHAR(100),                   -- nomor invoice/dokumen sumber
    currency        CHAR(3)      NOT NULL DEFAULT 'IDR',
    fx_rate         NUMERIC(18,6) DEFAULT 1.000000,
    status          journal_status NOT NULL DEFAULT 'draft',
    source          VARCHAR(50)  DEFAULT 'manual',  -- 'OCR', 'bank_sync', 'auto', 'manual'
    reverse_of      UUID         REFERENCES gl_journal(id),
    posted_by       VARCHAR(100),
    posted_at       TIMESTAMPTZ,
    created_by      VARCHAR(100),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE gl_journal IS 'Header jurnal — satu record per transaksi, bisa multi-line';

CREATE TABLE gl_line (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    journal_id      UUID         NOT NULL REFERENCES gl_journal(id) ON DELETE CASCADE,
    line_no         SMALLINT     NOT NULL,
    account_id      UUID         NOT NULL REFERENCES chart_of_accounts(id),
    description     TEXT,
    debit_idr       NUMERIC(18,2) NOT NULL DEFAULT 0,
    credit_idr      NUMERIC(18,2) NOT NULL DEFAULT 0,
    debit_fc        NUMERIC(18,2) DEFAULT 0,        -- foreign currency amount
    credit_fc       NUMERIC(18,2) DEFAULT 0,
    vendor_id       UUID         REFERENCES vendor(id),
    cost_center     VARCHAR(50),
    project_code    VARCHAR(50),
    tax_code        VARCHAR(30),                    -- e.g. 'PPh23_2pct', 'PPN_11pct'
    tax_amount      NUMERIC(18,2) DEFAULT 0,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_debit_credit CHECK (
        NOT (debit_idr > 0 AND credit_idr > 0)     -- satu baris hanya debit atau credit
    ),
    CONSTRAINT chk_non_negative CHECK (
        debit_idr >= 0 AND credit_idr >= 0
    )
);
COMMENT ON TABLE gl_line IS 'Baris jurnal double entry — debit dan credit tidak boleh isi bersamaan';

-- Constraint double entry: total debit = total credit per journal
-- Divalidasi di application layer (Python) sebelum INSERT


-- ============================================================
-- 6. AP MODULE
-- ============================================================

CREATE TYPE invoice_status AS ENUM ('draft', 'approved', 'partial', 'paid', 'cancelled');

CREATE TABLE ap_invoice (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    vendor_id       UUID         NOT NULL REFERENCES vendor(id),
    invoice_no      VARCHAR(100) NOT NULL,          -- nomor invoice dari vendor
    invoice_date    DATE         NOT NULL,
    due_date        DATE,
    currency        CHAR(3)      NOT NULL DEFAULT 'IDR',
    subtotal        NUMERIC(18,2) NOT NULL DEFAULT 0,
    ppn_amount      NUMERIC(18,2) NOT NULL DEFAULT 0,
    pph_amount      NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_amount    NUMERIC(18,2) NOT NULL DEFAULT 0,
    paid_amount     NUMERIC(18,2) NOT NULL DEFAULT 0,
    status          invoice_status NOT NULL DEFAULT 'draft',
    journal_id      UUID         REFERENCES gl_journal(id),
    -- OCR metadata
    ocr_source      VARCHAR(50),                    -- 'email', 'upload', 'scan'
    ocr_file_path   TEXT,
    ocr_confidence  NUMERIC(4,2),
    -- Tax
    faktur_pajak_no VARCHAR(30),
    pph_type        VARCHAR(20),
    pph_rate        NUMERIC(5,2),
    -- Classification
    coa_expense     VARCHAR(20),                    -- akun beban/prepaid/aset hasil klasifikasi
    classification  VARCHAR(20),                    -- 'expense', 'prepaid', 'fixed_asset'
    classification_confidence NUMERIC(4,2),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE ap_payment (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    invoice_id      UUID         NOT NULL REFERENCES ap_invoice(id),
    payment_date    DATE         NOT NULL,
    amount          NUMERIC(18,2) NOT NULL,
    currency        CHAR(3)      NOT NULL DEFAULT 'IDR',
    bank_account    VARCHAR(50),
    reference_no    VARCHAR(100),
    journal_id      UUID         REFERENCES gl_journal(id),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);


-- ============================================================
-- 7. AR MODULE
-- ============================================================

CREATE TABLE ar_invoice (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    customer_name   VARCHAR(300) NOT NULL,
    customer_npwp   VARCHAR(30),
    invoice_no      VARCHAR(100) NOT NULL,
    invoice_date    DATE         NOT NULL,
    due_date        DATE,
    currency        CHAR(3)      NOT NULL DEFAULT 'IDR',
    subtotal        NUMERIC(18,2) NOT NULL DEFAULT 0,
    ppn_amount      NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_amount    NUMERIC(18,2) NOT NULL DEFAULT 0,
    paid_amount     NUMERIC(18,2) NOT NULL DEFAULT 0,
    status          invoice_status NOT NULL DEFAULT 'draft',
    journal_id      UUID         REFERENCES gl_journal(id),
    -- Auto-invoicing metadata
    contract_ref    VARCHAR(100),
    generated_by    VARCHAR(20)  DEFAULT 'manual',  -- 'auto', 'manual'
    sent_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE ar_receipt (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    invoice_id      UUID         NOT NULL REFERENCES ar_invoice(id),
    receipt_date    DATE         NOT NULL,
    amount          NUMERIC(18,2) NOT NULL,
    currency        CHAR(3)      NOT NULL DEFAULT 'IDR',
    bank_account    VARCHAR(50),
    reference_no    VARCHAR(100),
    bank_statement_id UUID,                         -- link ke bank_statement_line
    journal_id      UUID         REFERENCES gl_journal(id),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);


-- ============================================================
-- 8. BANK SYNC MODULE
-- ============================================================

CREATE TABLE bank_account (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    bank_name       VARCHAR(100) NOT NULL,
    account_no      VARCHAR(50)  NOT NULL,
    account_name    VARCHAR(200),
    currency        CHAR(3)      NOT NULL DEFAULT 'IDR',
    coa_id          UUID         REFERENCES chart_of_accounts(id),
    last_sync_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TYPE match_status AS ENUM ('matched', 'partial', 'unmatched', 'ignored');

CREATE TABLE bank_statement_line (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    bank_account_id UUID         NOT NULL REFERENCES bank_account(id),
    transaction_date DATE        NOT NULL,
    value_date      DATE,
    description     TEXT,
    reference_no    VARCHAR(200),
    debit_amount    NUMERIC(18,2) DEFAULT 0,
    credit_amount   NUMERIC(18,2) DEFAULT 0,
    balance         NUMERIC(18,2),
    match_status    match_status NOT NULL DEFAULT 'unmatched',
    matched_invoice_id UUID,                        -- AR atau AP invoice yang dicocokkan
    journal_id      UUID         REFERENCES gl_journal(id),
    raw_data        JSONB,                          -- data mentah dari bank API/CSV
    imported_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE bank_statement_line IS 'Mutasi bank hasil import API/MT940/CSV untuk auto-rekonsiliasi';


-- ============================================================
-- 9. ASSET & PREPAID — AMORTISASI & DEPRESIASI
-- ============================================================

CREATE TYPE asset_status   AS ENUM ('active', 'disposed', 'fully_depreciated');
CREATE TYPE asset_method   AS ENUM ('straight_line', 'declining_balance', 'sum_of_years');
CREATE TYPE asset_category AS ENUM (
    'kelompok_1', 'kelompok_2', 'kelompok_3', 'kelompok_4',
    'bangunan_permanen', 'bangunan_tidak_permanen', 'prepaid', 'intangible'
);

CREATE TABLE fixed_asset (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    asset_code      VARCHAR(30)  NOT NULL UNIQUE,
    asset_name      VARCHAR(300) NOT NULL,
    category        asset_category NOT NULL,
    acquisition_date DATE        NOT NULL,
    acquisition_cost NUMERIC(18,2) NOT NULL,
    salvage_value   NUMERIC(18,2) NOT NULL DEFAULT 0,
    useful_life_months SMALLINT  NOT NULL,          -- masa manfaat komersial
    fiscal_life_months SMALLINT,                    -- masa manfaat fiskal (PMK 96/2009)
    method          asset_method NOT NULL DEFAULT 'straight_line',
    status          asset_status NOT NULL DEFAULT 'active',
    coa_asset       VARCHAR(20),
    coa_accum_dep   VARCHAR(20),
    coa_dep_expense VARCHAR(20),
    ap_invoice_id   UUID         REFERENCES ap_invoice(id),
    -- Tax
    fiscal_group    VARCHAR(20),                    -- 'Kelompok I', 'II', 'III', 'IV', 'Bangunan'
    fiscal_method   asset_method DEFAULT 'straight_line',
    fiscal_rate     NUMERIC(5,2),                   -- tarif penyusutan fiskal %
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE fixed_asset IS 'Aset tetap dengan pembedaan metode komersial vs fiskal (PMK 96/2009)';

CREATE TABLE asset_depreciation_schedule (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    asset_id        UUID         NOT NULL REFERENCES fixed_asset(id) ON DELETE CASCADE,
    period_id       UUID         NOT NULL REFERENCES fiscal_period(id),
    period_date     DATE         NOT NULL,
    commercial_dep  NUMERIC(18,2) NOT NULL DEFAULT 0,  -- depresiasi komersial
    fiscal_dep      NUMERIC(18,2) NOT NULL DEFAULT 0,  -- depresiasi fiskal
    temp_diff       NUMERIC(18,2) GENERATED ALWAYS AS (commercial_dep - fiscal_dep) STORED,
    book_value_end  NUMERIC(18,2) NOT NULL,
    fiscal_value_end NUMERIC(18,2),
    journal_id      UUID         REFERENCES gl_journal(id),
    is_posted       BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (asset_id, period_id)
);
COMMENT ON TABLE asset_depreciation_schedule IS 'Jadwal depresiasi bulanan: komersial vs fiskal untuk koreksi fiskal SPT Badan';

CREATE TABLE prepaid_expense (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    prepaid_code    VARCHAR(30)  NOT NULL UNIQUE,
    description     VARCHAR(300) NOT NULL,
    start_date      DATE         NOT NULL,
    end_date        DATE         NOT NULL,
    total_amount    NUMERIC(18,2) NOT NULL,
    monthly_amount  NUMERIC(18,2) NOT NULL,
    coa_prepaid     VARCHAR(20),
    coa_expense     VARCHAR(20),
    ap_invoice_id   UUID         REFERENCES ap_invoice(id),
    status          VARCHAR(20)  NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE prepaid_amortization_schedule (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    prepaid_id      UUID         NOT NULL REFERENCES prepaid_expense(id) ON DELETE CASCADE,
    period_id       UUID         NOT NULL REFERENCES fiscal_period(id),
    period_date     DATE         NOT NULL,
    amortize_amount NUMERIC(18,2) NOT NULL,
    remaining_amount NUMERIC(18,2) NOT NULL,
    journal_id      UUID         REFERENCES gl_journal(id),
    is_posted       BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (prepaid_id, period_id)
);


-- ============================================================
-- 10. TAX ADVICE ENGINE
-- ============================================================

CREATE TABLE tax_advice_log (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    source_type     VARCHAR(30)  NOT NULL,          -- 'ap_invoice', 'asset', 'prepaid'
    source_id       UUID         NOT NULL,
    vendor_id       UUID         REFERENCES vendor(id),
    advice_type     VARCHAR(30)  NOT NULL,          -- 'PPh23', 'PPh4(2)', 'PPN', 'koreksi_fiskal'
    advice_detail   TEXT         NOT NULL,
    tax_rate        NUMERIC(5,2),
    tax_base        NUMERIC(18,2),
    tax_amount      NUMERIC(18,2),
    regulation_ref  VARCHAR(200),                   -- e.g. 'PMK-141/PMK.03/2015 Pasal 3 ayat 1'
    confidence      NUMERIC(4,2) DEFAULT 1.00,
    is_acknowledged BOOLEAN      NOT NULL DEFAULT FALSE,
    acknowledged_by VARCHAR(100),
    acknowledged_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE tax_advice_log IS 'Log rekomendasi pajak otomatis per transaksi lengkap dengan referensi regulasi';


-- ============================================================
-- 11. INDEXES
-- ============================================================

CREATE INDEX idx_gl_journal_entity_period ON gl_journal(entity_id, period_id);
CREATE INDEX idx_gl_journal_date          ON gl_journal(journal_date);
CREATE INDEX idx_gl_journal_status        ON gl_journal(status);
CREATE INDEX idx_gl_line_journal          ON gl_line(journal_id);
CREATE INDEX idx_gl_line_account          ON gl_line(account_id);
CREATE INDEX idx_ap_invoice_vendor        ON ap_invoice(vendor_id);
CREATE INDEX idx_ap_invoice_status        ON ap_invoice(status);
CREATE INDEX idx_ar_invoice_status        ON ar_invoice(status);
CREATE INDEX idx_bank_stmt_match          ON bank_statement_line(match_status);
CREATE INDEX idx_vendor_npwp              ON vendor(npwp);
CREATE INDEX idx_asset_dep_schedule       ON asset_depreciation_schedule(asset_id, is_posted);
CREATE INDEX idx_prepaid_schedule         ON prepaid_amortization_schedule(prepaid_id, is_posted);
CREATE INDEX idx_tax_advice_source        ON tax_advice_log(source_type, source_id);


-- ============================================================
-- 12. VIEWS — TRIAL BALANCE & GL SUMMARY
-- ============================================================

CREATE VIEW vw_trial_balance AS
SELECT
    coa.entity_id,
    coa.account_code,
    coa.account_name,
    coa.account_type,
    coa.normal_balance,
    COALESCE(SUM(gl.debit_idr),  0) AS total_debit,
    COALESCE(SUM(gl.credit_idr), 0) AS total_credit,
    CASE coa.normal_balance
        WHEN 'debit'  THEN COALESCE(SUM(gl.debit_idr), 0) - COALESCE(SUM(gl.credit_idr), 0)
        WHEN 'credit' THEN COALESCE(SUM(gl.credit_idr), 0) - COALESCE(SUM(gl.debit_idr),  0)
    END AS balance
FROM chart_of_accounts coa
LEFT JOIN gl_line gl        ON gl.account_id = coa.id
LEFT JOIN gl_journal j      ON j.id = gl.journal_id AND j.status = 'posted'
GROUP BY coa.entity_id, coa.account_code, coa.account_name,
         coa.account_type, coa.normal_balance;

COMMENT ON VIEW vw_trial_balance IS 'Trial balance otomatis dari semua jurnal berstatus posted';

CREATE VIEW vw_ap_aging AS
SELECT
    v.vendor_name,
    ai.invoice_no,
    ai.invoice_date,
    ai.due_date,
    ai.total_amount,
    ai.paid_amount,
    (ai.total_amount - ai.paid_amount) AS outstanding,
    (CURRENT_DATE - ai.due_date)       AS days_overdue,
    CASE
        WHEN (CURRENT_DATE - ai.due_date) <= 0   THEN 'current'
        WHEN (CURRENT_DATE - ai.due_date) <= 30  THEN '1-30 days'
        WHEN (CURRENT_DATE - ai.due_date) <= 60  THEN '31-60 days'
        WHEN (CURRENT_DATE - ai.due_date) <= 90  THEN '61-90 days'
        ELSE '>90 days'
    END AS aging_bucket
FROM ap_invoice ai
JOIN vendor v ON v.id = ai.vendor_id
WHERE ai.status NOT IN ('paid', 'cancelled');

COMMENT ON VIEW vw_ap_aging IS 'AP aging otomatis untuk monitoring hutang jatuh tempo';


-- ============================================================
-- END OF SCHEMA
-- ============================================================