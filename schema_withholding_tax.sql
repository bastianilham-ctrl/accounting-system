-- ============================================================
-- SCHEMA: PPh 23 & PPh 4(2) — Withholding Tax
--
-- Konsep:
--   PPh 23  : Dipotong saat pembayaran jasa/dividen/royalti dari Badan.
--             Tarif: 2% (jasa), 15% (dividen/royalti/bunga), 100% jika NPWP tidak ada.
--   PPh 4(2): Final — sewa tanah/bangunan (10%), jasa konstruksi (2-4%),
--             hadiah/bonus (25%), obligasi (10-15%).
--
--   Alur:
--     1. Saat AP invoice dibuat → wht_transaction dibuat otomatis
--     2. Finance konfirmasi setiap akhir bulan → generate bukti potong
--     3. Buat SPT Masa (ringkasan bulan) → setor ke DJP
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_withholding_tax.sql
-- Dependensi: schema_ap.sql (ap_invoice, vendor), schema_journal_engine.sql
-- ============================================================

-- ── 1. WHT RATE TABLE ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS wht_rate (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tax_type        VARCHAR(10)  NOT NULL CHECK (tax_type IN ('PPh23','PPh4_2')),
    income_type     VARCHAR(50)  NOT NULL,  -- jasa, dividen, sewa_bangunan, konstruksi, dll
    income_type_code VARCHAR(20) NOT NULL,
    rate_pct        NUMERIC(5,2) NOT NULL,
    rate_npwp_pct   NUMERIC(5,2),           -- tarif jika NPWP ada (kadang beda)
    effective_date  DATE         NOT NULL DEFAULT '2023-01-01',
    notes           TEXT,
    UNIQUE (tax_type, income_type_code, effective_date)
);

-- Seed tarif standar 2024
INSERT INTO wht_rate (tax_type, income_type, income_type_code, rate_pct, rate_npwp_pct, notes) VALUES
-- PPh 23
('PPh23', 'Jasa (umum)',               'JASA',          2.00,  2.00,  'Tarif 4% jika tidak ber-NPWP'),
('PPh23', 'Jasa Teknik',               'JASA_TEKNIK',   2.00,  2.00,  NULL),
('PPh23', 'Jasa Konsultan',            'JASA_KONSULTAN',2.00,  2.00,  NULL),
('PPh23', 'Jasa Manajemen',            'JASA_MGMT',     2.00,  2.00,  NULL),
('PPh23', 'Jasa Desain',               'JASA_DESAIN',   2.00,  2.00,  NULL),
('PPh23', 'Dividen (Badan)',           'DIVIDEN',      15.00, 15.00,  NULL),
('PPh23', 'Bunga',                     'BUNGA',        15.00, 15.00,  NULL),
('PPh23', 'Royalti',                   'ROYALTI',      15.00, 15.00,  NULL),
('PPh23', 'Hadiah & Penghargaan',      'HADIAH',       15.00, 15.00,  NULL),
-- PPh 4(2)
('PPh4_2','Sewa Tanah/Bangunan',       'SEWA_BANGUN',  10.00, 10.00,  'Bersifat final'),
('PPh4_2','Jasa Konstruksi (kecil)',   'KONS_KECIL',    2.00,  2.00,  'Kualifikasi kecil'),
('PPh4_2','Jasa Konstruksi (menengah)','KONS_MENENGAH', 3.00,  3.00,  'Kualifikasi menengah/besar'),
('PPh4_2','Jasa Konstruksi (tanpa)',   'KONS_TANPA',    4.00,  4.00,  'Tanpa kualifikasi'),
('PPh4_2','Perencanaan Konstruksi',    'KONS_RENCANA',  4.00,  4.00,  NULL),
('PPh4_2','Pengawasan Konstruksi',     'KONS_AWAS',     4.00,  4.00,  NULL),
('PPh4_2','Hadiah Undian',             'UNDIAN',       25.00, 25.00,  NULL),
('PPh4_2','Obligasi/Bunga Obligasi',   'OBLIGASI',     10.00, 10.00,  NULL)
ON CONFLICT DO NOTHING;


-- ── 2. WHT TRANSACTION — per invoice / per baris ─────────────────────────────
CREATE TABLE IF NOT EXISTS wht_transaction (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    tax_type        VARCHAR(10)  NOT NULL CHECK (tax_type IN ('PPh23','PPh4_2')),
    income_type_code VARCHAR(20) NOT NULL,
    ap_invoice_id   UUID         REFERENCES ap_invoice(id),   -- bisa null jika manual
    vendor_id       UUID         NOT NULL REFERENCES vendor(id),
    transaction_date DATE        NOT NULL,
    dpp             NUMERIC(18,2) NOT NULL,   -- Dasar Pengenaan Pajak
    rate_pct        NUMERIC(5,2) NOT NULL,
    tax_amount      NUMERIC(18,2) GENERATED ALWAYS AS (dpp * rate_pct / 100) STORED,
    has_npwp        BOOLEAN NOT NULL DEFAULT TRUE,
    description     TEXT,
    period_year     SMALLINT     NOT NULL,
    period_month    SMALLINT     NOT NULL CHECK (period_month BETWEEN 1 AND 12),
    status          VARCHAR(20)  NOT NULL DEFAULT 'draft'
        CHECK (status IN (
            'draft',        -- terbuat dari AP, belum dikonfirmasi
            'confirmed',    -- dikonfirmasi finance
            'bukti_potong', -- bukti potong sudah diterbitkan
            'spt_included', -- sudah masuk SPT Masa
            'void'
        )),
    bukti_potong_no  VARCHAR(50),
    bukti_potong_date DATE,
    gl_journal_id   UUID         REFERENCES gl_journal(id),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wht_entity   ON wht_transaction(entity_id, tax_type, period_year, period_month);
CREATE INDEX IF NOT EXISTS idx_wht_vendor   ON wht_transaction(vendor_id, status);
CREATE INDEX IF NOT EXISTS idx_wht_invoice  ON wht_transaction(ap_invoice_id);


-- ── 3. SPT MASA — rekapitulasi per bulan ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS wht_spt_masa (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    tax_type        VARCHAR(10)  NOT NULL CHECK (tax_type IN ('PPh23','PPh4_2')),
    period_year     SMALLINT     NOT NULL,
    period_month    SMALLINT     NOT NULL CHECK (period_month BETWEEN 1 AND 12),
    total_dpp       NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_tax       NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_bukti_potong SMALLINT  NOT NULL DEFAULT 0,
    status          VARCHAR(20)  NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','submitted','paid','amended')),
    payment_date    DATE,
    payment_ntpn    VARCHAR(50),   -- Nomor Transaksi Penerimaan Negara
    payment_journal_id UUID       REFERENCES gl_journal(id),
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (entity_id, tax_type, period_year, period_month)
);


-- ── 4. VIEWS ─────────────────────────────────────────────────────────────────

-- Transaksi per vendor per bulan
CREATE OR REPLACE VIEW vw_wht_by_vendor AS
SELECT
    wt.entity_id,
    wt.tax_type,
    wt.period_year,
    wt.period_month,
    wt.vendor_id,
    v.vendor_name,
    v.npwp,
    wt.income_type_code,
    wr.income_type,
    COUNT(wt.id)         AS transaction_count,
    SUM(wt.dpp)          AS total_dpp,
    SUM(wt.tax_amount)   AS total_tax,
    MAX(wt.status)       AS latest_status
FROM wht_transaction wt
JOIN vendor v  ON v.id  = wt.vendor_id
JOIN wht_rate wr ON wr.income_type_code = wt.income_type_code
               AND wr.tax_type          = wt.tax_type
GROUP BY wt.entity_id, wt.tax_type, wt.period_year, wt.period_month,
         wt.vendor_id, v.vendor_name, v.npwp, wt.income_type_code, wr.income_type;


-- Ringkasan SPT Masa (lampiran)
CREATE OR REPLACE VIEW vw_spt_masa_detail AS
SELECT
    spt.id AS spt_id,
    spt.entity_id,
    spt.tax_type,
    spt.period_year,
    spt.period_month,
    spt.total_dpp,
    spt.total_tax,
    spt.total_bukti_potong,
    spt.status,
    spt.payment_date,
    spt.payment_ntpn,
    wt.id             AS transaction_id,
    wt.bukti_potong_no,
    wt.bukti_potong_date,
    wt.vendor_id,
    v.vendor_name,
    v.npwp,
    wt.income_type_code,
    wt.dpp,
    wt.rate_pct,
    wt.tax_amount
FROM wht_spt_masa spt
JOIN wht_transaction wt ON wt.entity_id   = spt.entity_id
                        AND wt.tax_type    = spt.tax_type
                        AND wt.period_year  = spt.period_year
                        AND wt.period_month = spt.period_month
                        AND wt.status       = 'spt_included'
JOIN vendor v ON v.id = wt.vendor_id;


-- Transaksi yang belum memiliki bukti potong
CREATE OR REPLACE VIEW vw_wht_pending_bukti_potong AS
SELECT
    wt.id, wt.entity_id, wt.tax_type, wt.income_type_code,
    wt.transaction_date, wt.period_year, wt.period_month,
    v.vendor_name, v.npwp, wt.has_npwp,
    wt.dpp, wt.rate_pct, wt.tax_amount, wt.description,
    ai.invoice_no
FROM wht_transaction wt
JOIN vendor v ON v.id = wt.vendor_id
LEFT JOIN ap_invoice ai ON ai.id = wt.ap_invoice_id
WHERE wt.status = 'confirmed';


SELECT 'Migration schema_withholding_tax selesai' AS status;
