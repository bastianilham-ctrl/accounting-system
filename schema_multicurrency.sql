-- ============================================================
-- SCHEMA: Multi-Currency
--
-- Konsep:
--   - Semua GL SELALU disimpan dalam IDR (debit_idr/credit_idr)
--   - Transaksi FCY juga menyimpan amount_fcy + exchange_rate
--   - Revaluation: setiap akhir bulan/tahun, hitung selisih kurs
--     antara kurs buku vs kurs tanggal revaluasi
--   - Realized G/L: terjadi saat AR/AP FCY dibayar pada kurs berbeda
--   - Unrealized G/L: dari revaluation periodik (belum settling)
--
-- Akun default:
--   Keuntungan Selisih Kurs  : 7-1000
--   Kerugian Selisih Kurs    : 8-1000
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_multicurrency.sql
-- ============================================================

-- ── 1. CURRENCY MASTER ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS currency (
    id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    currency_code   VARCHAR(5)   NOT NULL UNIQUE,   -- ISO 4217: USD, SGD, EUR
    currency_name   VARCHAR(100) NOT NULL,
    symbol          VARCHAR(10),
    decimal_places  SMALLINT     NOT NULL DEFAULT 2,
    is_base_currency BOOLEAN     NOT NULL DEFAULT FALSE,  -- TRUE hanya untuk IDR
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE
);

-- Seed: mata uang yang umum di Indonesia
INSERT INTO currency (currency_code, currency_name, symbol, decimal_places, is_base_currency)
VALUES
    ('IDR', 'Rupiah',              'Rp',  0, TRUE),
    ('USD', 'US Dollar',           '$',   2, FALSE),
    ('SGD', 'Singapore Dollar',    'S$',  2, FALSE),
    ('EUR', 'Euro',                '€',   2, FALSE),
    ('JPY', 'Japanese Yen',        '¥',   0, FALSE),
    ('CNY', 'Chinese Yuan',        '¥',   2, FALSE),
    ('MYR', 'Malaysian Ringgit',   'RM',  2, FALSE),
    ('AUD', 'Australian Dollar',   'A$',  2, FALSE),
    ('GBP', 'British Pound',       '£',   2, FALSE),
    ('HKD', 'Hong Kong Dollar',    'HK$', 2, FALSE),
    ('SAR', 'Saudi Riyal',         'SR',  2, FALSE)
ON CONFLICT (currency_code) DO NOTHING;


-- ── 2. EXCHANGE RATE ──────────────────────────────────────────────────────────
-- Semua rate ke IDR (base currency)
-- rate_type: middle (tengah), buying (beli), selling (jual)
CREATE TABLE IF NOT EXISTS exchange_rate (
    id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    from_currency   VARCHAR(5)   NOT NULL REFERENCES currency(currency_code),
    to_currency     VARCHAR(5)   NOT NULL DEFAULT 'IDR',
    rate_date       DATE         NOT NULL,
    rate            NUMERIC(20,6) NOT NULL,          -- 1 FCY = rate IDR
    rate_type       VARCHAR(10)  NOT NULL DEFAULT 'middle'
        CHECK (rate_type IN ('middle','buying','selling')),
    source          VARCHAR(50)  DEFAULT 'manual',   -- manual | bi_rate | market
    notes           TEXT,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (from_currency, to_currency, rate_date, rate_type)
);
CREATE INDEX IF NOT EXISTS idx_exrate_lookup ON exchange_rate(from_currency, to_currency, rate_date DESC);
CREATE INDEX IF NOT EXISTS idx_exrate_date   ON exchange_rate(rate_date DESC);


-- ── 3. ALTER TABLE — tambah kolom FCY ke tabel yang sudah ada ─────────────────

-- gl_line: tambah kolom mata uang dan jumlah original (opsional — null = IDR)
ALTER TABLE gl_line
    ADD COLUMN IF NOT EXISTS currency      VARCHAR(5)    DEFAULT 'IDR',
    ADD COLUMN IF NOT EXISTS amount_fcy    NUMERIC(18,2),   -- jumlah dalam mata uang asli
    ADD COLUMN IF NOT EXISTS exchange_rate NUMERIC(20,6);   -- kurs yang dipakai saat posting

-- ar_invoice: pastikan kolom FCY ada
ALTER TABLE ar_invoice
    ADD COLUMN IF NOT EXISTS currency      VARCHAR(5)    DEFAULT 'IDR',
    ADD COLUMN IF NOT EXISTS exchange_rate NUMERIC(20,6) DEFAULT 1,
    ADD COLUMN IF NOT EXISTS amount_fcy    NUMERIC(18,2),   -- nominal dalam FCY
    ADD COLUMN IF NOT EXISTS realized_gl_journal_id UUID REFERENCES gl_journal(id);

-- ap_invoice: pastikan kolom FCY ada
ALTER TABLE ap_invoice
    ADD COLUMN IF NOT EXISTS currency      VARCHAR(5)    DEFAULT 'IDR',
    ADD COLUMN IF NOT EXISTS exchange_rate NUMERIC(20,6) DEFAULT 1,
    ADD COLUMN IF NOT EXISTS amount_fcy    NUMERIC(18,2),
    ADD COLUMN IF NOT EXISTS realized_gl_journal_id UUID REFERENCES gl_journal(id);

-- bank_account: pastikan ada currency
ALTER TABLE bank_account
    ADD COLUMN IF NOT EXISTS currency      VARCHAR(5) DEFAULT 'IDR';

-- gl_journal: tambah flag multi-currency
ALTER TABLE gl_journal
    ADD COLUMN IF NOT EXISTS has_fcy BOOLEAN NOT NULL DEFAULT FALSE;


-- ── 4. REVALUATION RUN ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS revaluation_run (
    id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    run_date        DATE         NOT NULL,           -- tanggal revaluasi (biasanya akhir bulan)
    fiscal_year     SMALLINT     NOT NULL,
    fiscal_month    SMALLINT     NOT NULL,
    status          VARCHAR(20)  NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','posted','reversed')),
    journal_id      UUID         REFERENCES gl_journal(id),  -- jurnal revaluasi
    reversal_journal_id UUID     REFERENCES gl_journal(id),  -- auto-reversal (opsional)
    auto_reverse    BOOLEAN      NOT NULL DEFAULT FALSE,      -- buat reversal di awal bulan berikut
    gl_gain_account VARCHAR(20)  NOT NULL DEFAULT '7-1000',
    gl_loss_account VARCHAR(20)  NOT NULL DEFAULT '8-1000',
    total_gain      NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_loss      NUMERIC(18,2) NOT NULL DEFAULT 0,
    run_by          VARCHAR(200),
    notes           TEXT,
    reversed_by     VARCHAR(200),
    reversed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (entity_id, fiscal_year, fiscal_month)
);
CREATE INDEX IF NOT EXISTS idx_reval_entity ON revaluation_run(entity_id, status);


-- ── 5. REVALUATION ENTRY ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS revaluation_entry (
    id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id          UUID         NOT NULL REFERENCES revaluation_run(id) ON DELETE CASCADE,
    account_id      UUID         NOT NULL,
    account_code    VARCHAR(20)  NOT NULL,
    account_name    VARCHAR(200),
    currency        VARCHAR(5)   NOT NULL,
    fcy_balance     NUMERIC(18,2) NOT NULL,   -- total saldo dalam FCY
    book_idr_value  NUMERIC(18,2) NOT NULL,   -- nilai IDR yang ada di buku saat ini
    new_rate        NUMERIC(20,6) NOT NULL,   -- kurs revaluasi
    new_idr_value   NUMERIC(18,2) NOT NULL,   -- FCY × new_rate
    adjustment      NUMERIC(18,2) NOT NULL,   -- new_idr_value - book_idr_value
    is_gain         BOOLEAN      NOT NULL,    -- adjustment > 0 = gain
    gl_line_debit   UUID         REFERENCES gl_line(id),
    gl_line_credit  UUID         REFERENCES gl_line(id)
);
CREATE INDEX IF NOT EXISTS idx_revalentry_run ON revaluation_entry(run_id);


-- ── 6. VIEWS ─────────────────────────────────────────────────────────────────

-- Kurs terbaru per mata uang (paling mudah untuk UI)
CREATE OR REPLACE VIEW vw_exchange_rate_latest AS
SELECT DISTINCT ON (from_currency, to_currency, rate_type)
    from_currency,
    to_currency,
    rate_type,
    rate_date,
    rate,
    source
FROM exchange_rate
ORDER BY from_currency, to_currency, rate_type, rate_date DESC;


-- FCY Exposure per entity: per currency, berapa saldo FCY di GL
-- (hanya akun monetary: asset/liability dengan FCY)
CREATE OR REPLACE VIEW vw_fcy_exposure AS
SELECT
    gj.entity_id,
    gl.currency,
    coa.account_type,
    coa.account_code,
    coa.account_name,
    COALESCE(SUM(gl.amount_fcy * CASE WHEN gl.debit_idr > 0 THEN 1 ELSE -1 END), 0) AS fcy_net_balance,
    COALESCE(SUM(gl.debit_idr - gl.credit_idr), 0)                                   AS idr_book_value,
    er.rate                                                                            AS latest_rate,
    COALESCE(
        SUM(gl.amount_fcy * CASE WHEN gl.debit_idr > 0 THEN 1 ELSE -1 END), 0
    ) * COALESCE(er.rate, 1)                                                          AS idr_at_latest_rate,
    COALESCE(
        SUM(gl.debit_idr - gl.credit_idr), 0
    ) - COALESCE(
        SUM(gl.amount_fcy * CASE WHEN gl.debit_idr > 0 THEN 1 ELSE -1 END), 0
    ) * COALESCE(er.rate, 1)                                                          AS unrealized_position
FROM gl_line gl
JOIN gl_journal gj         ON gj.id = gl.journal_id AND gj.status = 'posted'
JOIN chart_of_accounts coa ON coa.id = gl.account_id
                           AND coa.account_type IN ('asset','liability')
LEFT JOIN vw_exchange_rate_latest er ON er.from_currency = gl.currency
                                    AND er.to_currency   = 'IDR'
                                    AND er.rate_type     = 'middle'
WHERE gl.currency IS NOT NULL
  AND gl.currency != 'IDR'
  AND gl.amount_fcy IS NOT NULL
GROUP BY gj.entity_id, gl.currency, coa.account_type, coa.account_code,
         coa.account_name, er.rate;


-- Unrealized G/L summary per currency per entity
CREATE OR REPLACE VIEW vw_unrealized_gl AS
SELECT
    entity_id,
    currency,
    SUM(unrealized_position) FILTER (WHERE unrealized_position > 0) AS unrealized_gain,
    SUM(unrealized_position) FILTER (WHERE unrealized_position < 0) AS unrealized_loss,
    SUM(unrealized_position)                                          AS net_unrealized
FROM vw_fcy_exposure
GROUP BY entity_id, currency;


SELECT 'Migration schema_multicurrency selesai' AS status;
