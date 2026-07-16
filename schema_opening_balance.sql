-- ============================================================
-- SCHEMA: Opening Balance Setup
--
-- Digunakan saat perusahaan pertama kali menggunakan sistem,
-- atau saat migrasi dari sistem lama.
--
-- Flow:
--   1. Buat session (entity + opening_date)
--   2. Input GL Trial Balance (wajib, harus balance)
--   3. Input detail subsidiary: AR, AP, Fixed Asset,
--      Inventory, Bank, Leave (opsional tapi direkomendasikan)
--   4. Validate: cek balance + cross-check subsidiary vs GL
--   5. Finalize: buat Opening Journal + populate semua modul
--
-- Setelah finalized:
--   - Session terkunci, tidak bisa diedit
--   - GL journal type='opening' terposting
--   - ar_invoice, ap_invoice, fixed_asset, stock_move, dll. terisi
--   - fiscal_period bulan opening dibuka otomatis
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_opening_balance.sql
-- ============================================================

-- ── 1. SESSION ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS opening_balance_session (
    id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    opening_date    DATE         NOT NULL,       -- tanggal saldo awal (biasanya 1 Jan atau 1 Juli)
    fiscal_year     SMALLINT     NOT NULL,
    is_mid_year     BOOLEAN      NOT NULL DEFAULT FALSE,  -- true = start mid-year, bawa saldo P&L YTD
    currency        VARCHAR(5)   NOT NULL DEFAULT 'IDR',

    -- Progress flags (false = belum diisi)
    gl_done         BOOLEAN NOT NULL DEFAULT FALSE,
    ar_done         BOOLEAN NOT NULL DEFAULT FALSE,
    ap_done         BOOLEAN NOT NULL DEFAULT FALSE,
    asset_done      BOOLEAN NOT NULL DEFAULT FALSE,
    inventory_done  BOOLEAN NOT NULL DEFAULT FALSE,
    bank_done       BOOLEAN NOT NULL DEFAULT FALSE,
    leave_done      BOOLEAN NOT NULL DEFAULT FALSE,

    -- Validation result
    last_validation JSONB,    -- hasil terakhir validate_session()
    is_valid        BOOLEAN,  -- null = belum divalidasi

    -- Status
    status          VARCHAR(20)  NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','in_progress','validated','finalized')),
    finalized_at    TIMESTAMPTZ,
    finalized_by    VARCHAR(200),
    opening_journal_id UUID REFERENCES gl_journal(id),

    notes           TEXT,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (entity_id)  -- satu entity hanya boleh satu opening balance session
);


-- ── 2. GL TRIAL BALANCE ───────────────────────────────────────────────────────
-- Input: saldo setiap akun per tanggal opening
CREATE TABLE IF NOT EXISTS opening_balance_gl (
    id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id      UUID         NOT NULL REFERENCES opening_balance_session(id) ON DELETE CASCADE,
    account_code    VARCHAR(20)  NOT NULL,
    account_name    VARCHAR(200),
    debit_balance   NUMERIC(18,2) NOT NULL DEFAULT 0,
    credit_balance  NUMERIC(18,2) NOT NULL DEFAULT 0,
    notes           TEXT,
    UNIQUE (session_id, account_code)
);
CREATE INDEX IF NOT EXISTS idx_obgl_session ON opening_balance_gl(session_id);


-- ── 3. AR DETAIL — Piutang Usaha Outstanding ─────────────────────────────────
CREATE TABLE IF NOT EXISTS opening_balance_ar (
    id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id      UUID         NOT NULL REFERENCES opening_balance_session(id) ON DELETE CASCADE,
    customer_name   VARCHAR(200) NOT NULL,
    customer_id     UUID,          -- opsional (jika customer sudah dibuat di master)
    invoice_number  VARCHAR(100) NOT NULL,
    invoice_date    DATE         NOT NULL,
    due_date        DATE,
    original_amount NUMERIC(18,2) NOT NULL,
    amount_remaining NUMERIC(18,2) NOT NULL,  -- jika ada cicilan sebelumnya
    currency        VARCHAR(5)   NOT NULL DEFAULT 'IDR',
    exchange_rate   NUMERIC(14,6) NOT NULL DEFAULT 1,
    description     TEXT,
    imported        BOOLEAN NOT NULL DEFAULT FALSE  -- sudah diproses ke ar_invoice
);
CREATE INDEX IF NOT EXISTS idx_obar_session ON opening_balance_ar(session_id);


-- ── 4. AP DETAIL — Hutang Usaha Outstanding ──────────────────────────────────
CREATE TABLE IF NOT EXISTS opening_balance_ap (
    id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id      UUID         NOT NULL REFERENCES opening_balance_session(id) ON DELETE CASCADE,
    vendor_name     VARCHAR(200) NOT NULL,
    vendor_id       UUID,
    invoice_number  VARCHAR(100) NOT NULL,
    invoice_date    DATE         NOT NULL,
    due_date        DATE,
    original_amount NUMERIC(18,2) NOT NULL,
    amount_remaining NUMERIC(18,2) NOT NULL,
    currency        VARCHAR(5)   NOT NULL DEFAULT 'IDR',
    exchange_rate   NUMERIC(14,6) NOT NULL DEFAULT 1,
    description     TEXT,
    imported        BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_obap_session ON opening_balance_ap(session_id);


-- ── 5. FIXED ASSET REGISTER ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS opening_balance_asset (
    id                      UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id              UUID         NOT NULL REFERENCES opening_balance_session(id) ON DELETE CASCADE,
    asset_code              VARCHAR(50),
    asset_name              VARCHAR(200) NOT NULL,
    category                VARCHAR(100),
    location                VARCHAR(200),
    acquisition_date        DATE         NOT NULL,
    acquisition_cost        NUMERIC(18,2) NOT NULL,
    accumulated_depreciation NUMERIC(18,2) NOT NULL DEFAULT 0,
    net_book_value          NUMERIC(18,2) GENERATED ALWAYS AS (acquisition_cost - accumulated_depreciation) STORED,
    useful_life_months      SMALLINT     NOT NULL DEFAULT 60,
    depreciation_method     VARCHAR(20)  NOT NULL DEFAULT 'straight_line'
        CHECK (depreciation_method IN ('straight_line','declining_balance','units_of_production')),
    salvage_value           NUMERIC(18,2) NOT NULL DEFAULT 0,
    gl_asset_account        VARCHAR(20),   -- account code aset tetap (mis. 1-5100)
    gl_depr_account         VARCHAR(20),   -- account code akum. penyusutan (mis. 1-5200)
    gl_expense_account      VARCHAR(20),   -- account code biaya penyusutan (mis. 6-1100)
    serial_number           VARCHAR(200),
    notes                   TEXT,
    imported                BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_obasset_session ON opening_balance_asset(session_id);


-- ── 6. INVENTORY — Persediaan Awal ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS opening_balance_inventory (
    id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id      UUID         NOT NULL REFERENCES opening_balance_session(id) ON DELETE CASCADE,
    product_code    VARCHAR(50)  NOT NULL,
    product_name    VARCHAR(200),
    product_id      UUID,          -- opsional jika produk sudah dibuat di master
    warehouse_code  VARCHAR(50),
    warehouse_id    UUID,
    quantity        NUMERIC(14,4) NOT NULL,
    unit_cost       NUMERIC(14,4) NOT NULL,
    total_value     NUMERIC(18,2) GENERATED ALWAYS AS (quantity * unit_cost) STORED,
    unit_of_measure VARCHAR(20),
    notes           TEXT,
    imported        BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (session_id, product_code, warehouse_code)
);
CREATE INDEX IF NOT EXISTS idx_obinv_session ON opening_balance_inventory(session_id);


-- ── 7. BANK BALANCES ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS opening_balance_bank (
    id                  UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id          UUID         NOT NULL REFERENCES opening_balance_session(id) ON DELETE CASCADE,
    bank_account_id     UUID,          -- FK ke bank_account (opsional)
    bank_name           VARCHAR(200) NOT NULL,
    account_number      VARCHAR(100),
    account_holder      VARCHAR(200),
    currency            VARCHAR(5)   NOT NULL DEFAULT 'IDR',
    opening_balance     NUMERIC(18,2) NOT NULL,
    gl_account_code     VARCHAR(20),   -- akun kas/bank yang sesuai
    notes               TEXT,
    imported            BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (session_id, bank_name, account_number)
);
CREATE INDEX IF NOT EXISTS idx_obbank_session ON opening_balance_bank(session_id);


-- ── 8. LEAVE BALANCES ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS opening_balance_leave (
    id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id      UUID         NOT NULL REFERENCES opening_balance_session(id) ON DELETE CASCADE,
    employee_id     UUID,
    employee_code   VARCHAR(50),
    employee_name   VARCHAR(200),
    leave_type_code VARCHAR(50)  NOT NULL,
    fiscal_year     SMALLINT     NOT NULL,
    entitled_days   NUMERIC(5,1) NOT NULL DEFAULT 0,
    used_days       NUMERIC(5,1) NOT NULL DEFAULT 0,
    carry_forward   NUMERIC(5,1) NOT NULL DEFAULT 0,
    balance_days    NUMERIC(5,1) GENERATED ALWAYS AS (entitled_days + carry_forward - used_days) STORED,
    notes           TEXT,
    imported        BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (session_id, employee_code, leave_type_code, fiscal_year)
);
CREATE INDEX IF NOT EXISTS idx_obleave_session ON opening_balance_leave(session_id);


-- ── 9. VIEWS ─────────────────────────────────────────────────────────────────

-- Progress & summary per session
CREATE OR REPLACE VIEW vw_opening_balance_status AS
SELECT
    obs.id AS session_id,
    obs.entity_id,
    obs.opening_date,
    obs.fiscal_year,
    obs.is_mid_year,
    obs.status,
    obs.is_valid,
    obs.gl_done, obs.ar_done, obs.ap_done,
    obs.asset_done, obs.inventory_done, obs.bank_done, obs.leave_done,

    -- Counts dari setiap tabel subsidiary
    COALESCE((SELECT COUNT(*) FROM opening_balance_gl    WHERE session_id=obs.id), 0) AS gl_account_count,
    COALESCE((SELECT SUM(debit_balance)  FROM opening_balance_gl WHERE session_id=obs.id), 0) AS gl_total_debit,
    COALESCE((SELECT SUM(credit_balance) FROM opening_balance_gl WHERE session_id=obs.id), 0) AS gl_total_credit,
    COALESCE((SELECT COUNT(*) FROM opening_balance_ar    WHERE session_id=obs.id), 0) AS ar_item_count,
    COALESCE((SELECT SUM(amount_remaining) FROM opening_balance_ar WHERE session_id=obs.id), 0) AS ar_total,
    COALESCE((SELECT COUNT(*) FROM opening_balance_ap    WHERE session_id=obs.id), 0) AS ap_item_count,
    COALESCE((SELECT SUM(amount_remaining) FROM opening_balance_ap WHERE session_id=obs.id), 0) AS ap_total,
    COALESCE((SELECT COUNT(*) FROM opening_balance_asset WHERE session_id=obs.id), 0) AS asset_count,
    COALESCE((SELECT SUM(net_book_value) FROM opening_balance_asset WHERE session_id=obs.id), 0) AS asset_nbv_total,
    COALESCE((SELECT COUNT(*) FROM opening_balance_inventory WHERE session_id=obs.id), 0) AS inventory_sku_count,
    COALESCE((SELECT SUM(total_value) FROM opening_balance_inventory WHERE session_id=obs.id), 0) AS inventory_total,
    COALESCE((SELECT COUNT(*) FROM opening_balance_bank  WHERE session_id=obs.id), 0) AS bank_account_count,
    COALESCE((SELECT SUM(opening_balance) FROM opening_balance_bank WHERE session_id=obs.id), 0) AS bank_total,

    obs.finalized_at,
    obs.finalized_by,
    obs.opening_journal_id

FROM opening_balance_session obs;


SELECT 'Migration schema_opening_balance selesai' AS status;
