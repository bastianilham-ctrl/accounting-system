-- ============================================================
-- SCHEMA: Contract Tracker — Multi-Modul Terintegrasi
-- Legal (Kontrak) × PM (Milestone BAST) × Finance (AR/Billing)
--
-- Arsitektur:
--   project (1) → project_contract (N)
--   project_contract (1) → contract_milestone (N)
--   project_contract (1) → ar_invoice (N)    [via ALTER TABLE]
--   contract_milestone (1) → ar_invoice (N)  [via ALTER TABLE]
--
-- Logika Otomasi:
--   A. Daily cron: update status invoice OVERDUE setelah lewat TOP
--   B. Validation gate: invoice hanya bisa dibuat jika milestone COMPLETED & kontrak ACTIVE
--   C. Dashboard query: total_invoiced / outstanding / collected / overdue per kontrak
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_contract_tracker.sql
-- Dependensi: schema_project_setup.sql (project), schema_ar.sql (ar_invoice), schema_vendor_registration.sql (vendor)
-- ============================================================


-- ============================================================
-- 1. PROJECT CONTRACT — tabel induk kontrak
-- ============================================================

CREATE TABLE IF NOT EXISTS project_contract (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    project_id      UUID         NOT NULL REFERENCES project(id),
    client_id       UUID         REFERENCES vendor(id),   -- link ke master klien

    contract_number VARCHAR(100) NOT NULL UNIQUE,
    contract_title  VARCHAR(200) NOT NULL,

    -- Nilai & Mata Uang
    total_value     NUMERIC(18,2) NOT NULL CHECK (total_value > 0),
    currency        VARCHAR(3)    NOT NULL DEFAULT 'IDR',
    exchange_rate   NUMERIC(18,6) NOT NULL DEFAULT 1,     -- ke IDR jika non-IDR

    -- Term of Payment
    term_of_payment_days INT NOT NULL DEFAULT 30,         -- TOP dalam hari kalender
    retention_pct   NUMERIC(5,2) NOT NULL DEFAULT 0,      -- % retensi (umum di konstruksi)
    retention_amount NUMERIC(18,2) GENERATED ALWAYS AS (total_value * retention_pct / 100) STORED,
    retention_released_at TIMESTAMPTZ,

    -- Status Kontrak (alur legal)
    contract_status VARCHAR(20)   NOT NULL DEFAULT 'IN_REVIEW'
        CHECK (contract_status IN (
            'IN_REVIEW',    -- Draft / masih negosiasi
            'ACTIVE',       -- Ditandatangani resmi — billing diizinkan
            'AMENDED',      -- Ada addendum aktif (kontrak induk tetap jalan)
            'COMPLETED',    -- Seluruh kewajiban selesai
            'TERMINATED'    -- Dihentikan sebelum selesai
        )),

    -- Tanggal
    start_date      DATE         NOT NULL,
    end_date        DATE         NOT NULL,
    signing_date    DATE,                        -- tanggal tanda tangan kontrak
    original_end_date DATE,                      -- disimpan saat ada extension

    -- Referensi dokumen
    po_number       VARCHAR(100),                -- nomor PO dari klien
    scope_summary   TEXT,
    notes           TEXT,
    legal_reviewed_by VARCHAR(200),
    legal_reviewed_at TIMESTAMPTZ,

    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_contract_project ON project_contract(project_id);
CREATE INDEX IF NOT EXISTS idx_contract_client  ON project_contract(client_id);
CREATE INDEX IF NOT EXISTS idx_contract_status  ON project_contract(entity_id, contract_status);
COMMENT ON TABLE project_contract IS
    'Satu proyek bisa punya lebih dari satu kontrak (multi-kontrak). '
    'Billing hanya diizinkan jika contract_status = ACTIVE. '
    'retention_amount = total_value × retention_pct / 100 (computed).';


-- ============================================================
-- 2. CONTRACT MILESTONE — jadwal penagihan per termin
-- ============================================================

CREATE TABLE IF NOT EXISTS contract_milestone (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contract_id     UUID         NOT NULL REFERENCES project_contract(id) ON DELETE CASCADE,
    pm_milestone_id UUID         REFERENCES project_milestone(id),  -- link ke PM milestone (opsional)

    sequence        SMALLINT     NOT NULL DEFAULT 1,
    milestone_name  VARCHAR(150) NOT NULL,        -- "DP 30%", "Termin 1", "Final Payment"
    description     TEXT,

    -- Porsi tagihan
    percentage      NUMERIC(5,2) NOT NULL CHECK (percentage > 0 AND percentage <= 100),
    amount_target   NUMERIC(18,2) NOT NULL,       -- nominal; disimpan eksplisit (dihitung engine)
        -- = contract.total_value × percentage / 100 (dikurangi retensi jika berlaku)
    retention_held  NUMERIC(18,2) NOT NULL DEFAULT 0,  -- porsi retensi ditahan di termin ini

    -- Syarat penagihan (trigger condition)
    trigger_condition TEXT,                       -- deskripsi: "Setelah BAST ditandatangani klien"

    -- Status pekerjaan (dari PM / lapangan)
    work_status     VARCHAR(20)  NOT NULL DEFAULT 'NOT_STARTED'
        CHECK (work_status IN (
            'NOT_STARTED',   -- belum dikerjakan
            'IN_PROGRESS',   -- sedang berjalan
            'COMPLETED'      -- BAST sudah terbit — billing boleh dibuat
        )),

    -- Berita Acara Serah Terima
    bast_number     VARCHAR(100),
    bast_date       DATE,
    bast_signed_by  VARCHAR(200),

    -- Status billing (dari Finance)
    billing_status  VARCHAR(20)  NOT NULL DEFAULT 'UNINVOICED'
        CHECK (billing_status IN (
            'UNINVOICED',       -- belum bisa ditagih (work belum selesai)
            'READY_TO_BILL',    -- pekerjaan selesai, belum dibuat invoice
            'INVOICED',         -- invoice sudah dibuat
            'PAID'              -- invoice sudah lunas
        )),

    -- Invoice link (diisi saat invoice dibuat)
    invoice_id      UUID,                         -- FK ke ar_invoice.id (ditambahkan setelah tabel ar_invoice)
    invoiced_at     TIMESTAMPTZ,

    due_date        DATE,                         -- estimasi jatuh tempo termin ini
    notes           TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cms_contract    ON contract_milestone(contract_id, sequence);
CREATE INDEX IF NOT EXISTS idx_cms_billing     ON contract_milestone(billing_status);
CREATE INDEX IF NOT EXISTS idx_cms_work_status ON contract_milestone(work_status);
COMMENT ON TABLE contract_milestone IS
    'Jadwal termin penagihan. Setiap termin harus punya work_status=COMPLETED (BAST) '
    'sebelum Finance boleh membuat invoice. Jumlah percentage semua termin = 100%.';


-- ============================================================
-- 3. CONTRACT AMENDMENT — addendum / perubahan kontrak
-- ============================================================

CREATE TABLE IF NOT EXISTS contract_amendment (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contract_id     UUID         NOT NULL REFERENCES project_contract(id),
    amendment_no    VARCHAR(50)  NOT NULL UNIQUE,   -- ADD/2026/001
    amendment_title VARCHAR(200) NOT NULL,
    amendment_type  VARCHAR(20)  NOT NULL DEFAULT 'scope_change'
        CHECK (amendment_type IN (
            'value_increase',    -- nilai kontrak naik (extra work)
            'value_decrease',    -- nilai kontrak turun
            'extension',         -- perpanjangan waktu
            'scope_change',      -- perubahan lingkup kerja
            'termination'        -- penghentian kontrak
        )),

    -- Sebelum vs Sesudah
    original_value  NUMERIC(18,2),
    new_value       NUMERIC(18,2),
    original_end_date DATE,
    new_end_date    DATE,
    value_delta     NUMERIC(18,2) GENERATED ALWAYS AS (COALESCE(new_value, 0) - COALESCE(original_value, 0)) STORED,

    reason          TEXT         NOT NULL,
    impact_description TEXT,

    status          VARCHAR(20)  NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'SIGNED', 'REJECTED')),
    signing_date    DATE,
    signed_by       VARCHAR(200),

    notes           TEXT,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_amend_contract ON contract_amendment(contract_id, status);


-- ============================================================
-- 4. CONTRACT DOCUMENT — lampiran dokumen legal
-- ============================================================

CREATE TABLE IF NOT EXISTS contract_document (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contract_id     UUID         NOT NULL REFERENCES project_contract(id) ON DELETE CASCADE,
    amendment_id    UUID         REFERENCES contract_amendment(id),
    document_type   VARCHAR(30)  NOT NULL DEFAULT 'other'
        CHECK (document_type IN (
            'contract_signed',   -- kontrak yang sudah ditandatangani
            'amendment',         -- dokumen addendum
            'bast',              -- Berita Acara Serah Terima
            'nda',               -- Non-Disclosure Agreement
            'po',                -- Purchase Order dari klien
            'warranty',          -- dokumen garansi
            'other'
        )),
    document_name   VARCHAR(200) NOT NULL,
    file_url        VARCHAR(500),
    file_size_kb    INT,
    uploaded_by     VARCHAR(200),
    uploaded_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cdoc_contract ON contract_document(contract_id, document_type);


-- ============================================================
-- 5. CONTRACT PAYMENT — catatan pembayaran aktual dari klien
-- ============================================================

CREATE TABLE IF NOT EXISTS contract_payment (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contract_id     UUID         NOT NULL REFERENCES project_contract(id),
    milestone_id    UUID         REFERENCES contract_milestone(id),
    invoice_id      UUID,                         -- FK ke ar_invoice (FK constraint ditambah setelah ALTER)
    entity_id       UUID         NOT NULL REFERENCES entity(id),

    payment_date    DATE         NOT NULL,
    amount_received NUMERIC(18,2) NOT NULL CHECK (amount_received > 0),
    currency        VARCHAR(3)   NOT NULL DEFAULT 'IDR',
    exchange_rate   NUMERIC(18,6) NOT NULL DEFAULT 1,
    amount_idr      NUMERIC(18,2) GENERATED ALWAYS AS (amount_received * exchange_rate) STORED,

    payment_method  VARCHAR(30)  DEFAULT 'bank_transfer'
        CHECK (payment_method IN ('bank_transfer','cheque','giro','cash','other')),
    bank_reference  VARCHAR(100),   -- nomor referensi transfer/giro
    notes           TEXT,
    recorded_by     VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cpay_contract ON contract_payment(contract_id);
CREATE INDEX IF NOT EXISTS idx_cpay_invoice  ON contract_payment(invoice_id) WHERE invoice_id IS NOT NULL;


-- ============================================================
-- 6. ALTER TABLE ar_invoice — tambah link ke contract & milestone
-- ============================================================

ALTER TABLE ar_invoice ADD COLUMN IF NOT EXISTS contract_id   UUID REFERENCES project_contract(id);
ALTER TABLE ar_invoice ADD COLUMN IF NOT EXISTS milestone_id  UUID REFERENCES contract_milestone(id);
ALTER TABLE ar_invoice ADD COLUMN IF NOT EXISTS payment_status VARCHAR(20) DEFAULT 'DRAFT'
    CHECK (payment_status IN ('DRAFT','SENT','PAID','OVERDUE','CANCELLED'));

-- Backfill: sinkronkan payment_status dari status yang sudah ada
UPDATE ar_invoice
SET payment_status =
    CASE status
        WHEN 'draft'     THEN 'DRAFT'
        WHEN 'sent'      THEN 'SENT'
        WHEN 'paid'      THEN 'PAID'
        WHEN 'overdue'   THEN 'OVERDUE'
        WHEN 'cancelled' THEN 'CANCELLED'
        ELSE 'DRAFT'
    END
WHERE payment_status IS NULL;

CREATE INDEX IF NOT EXISTS idx_ar_contract  ON ar_invoice(contract_id)  WHERE contract_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ar_milestone ON ar_invoice(milestone_id) WHERE milestone_id IS NOT NULL;

-- FK dari contract_milestone.invoice_id ke ar_invoice
ALTER TABLE contract_milestone ADD CONSTRAINT fk_cms_invoice
    FOREIGN KEY (invoice_id) REFERENCES ar_invoice(id) DEFERRABLE INITIALLY DEFERRED;

-- FK dari contract_payment.invoice_id ke ar_invoice
ALTER TABLE contract_payment ADD CONSTRAINT fk_cpay_invoice
    FOREIGN KEY (invoice_id) REFERENCES ar_invoice(id) DEFERRABLE INITIALLY DEFERRED;


-- ============================================================
-- 7. VIEWS
-- ============================================================

-- ── 7.1  Dashboard per kontrak (Logika C) ─────────────────────────────────
CREATE OR REPLACE VIEW vw_contract_dashboard AS
SELECT
    p.id                AS project_id,
    p.project_code,
    p.project_name,
    c.id                AS contract_id,
    c.contract_number,
    c.contract_title,
    c.contract_status,
    c.total_value       AS total_contract_value,
    c.currency,
    c.term_of_payment_days,
    c.retention_pct,
    c.retention_amount,
    c.start_date        AS contract_start,
    c.end_date          AS contract_end,
    c.signing_date,

    -- Milestone stats
    COUNT(DISTINCT cm.id)                                                      AS total_milestones,
    COUNT(DISTINCT cm.id) FILTER (WHERE cm.work_status = 'COMPLETED')          AS completed_milestones,
    COUNT(DISTINCT cm.id) FILTER (WHERE cm.billing_status = 'READY_TO_BILL')   AS ready_to_bill,
    COUNT(DISTINCT cm.id) FILTER (WHERE cm.billing_status = 'INVOICED')        AS invoiced_milestones,
    COUNT(DISTINCT cm.id) FILTER (WHERE cm.billing_status = 'PAID')            AS paid_milestones,

    -- Invoice financials (Logika C)
    COALESCE(SUM(ai.total_amount)
        FILTER (WHERE ai.payment_status IN ('SENT','OVERDUE','PAID')), 0)       AS total_invoiced,

    c.total_value - COALESCE(SUM(ai.total_amount)
        FILTER (WHERE ai.payment_status IN ('SENT','OVERDUE','PAID')), 0)       AS total_uninvoiced,

    COALESCE(SUM(ai.paid_amount)
        FILTER (WHERE ai.payment_status = 'PAID'), 0)                           AS total_collected_cash,

    COALESCE(SUM(ai.total_amount - COALESCE(ai.paid_amount,0))
        FILTER (WHERE ai.payment_status IN ('SENT','OVERDUE')), 0)              AS total_outstanding,

    COALESCE(SUM(ai.total_amount - COALESCE(ai.paid_amount,0))
        FILTER (WHERE ai.payment_status = 'OVERDUE'), 0)                        AS total_overdue,

    -- Realisasi penerimaan pembayaran
    COALESCE((
        SELECT SUM(cp.amount_idr)
        FROM contract_payment cp
        WHERE cp.contract_id = c.id
    ), 0)                                                                       AS total_cash_received,

    -- Progress billing %
    CASE
        WHEN c.total_value = 0 THEN 0
        ELSE ROUND(
            100.0 * COALESCE(SUM(ai.total_amount)
                FILTER (WHERE ai.payment_status IN ('SENT','OVERDUE','PAID')), 0)
            / c.total_value, 1)
    END                                                                         AS billing_progress_pct

FROM project p
JOIN project_contract c ON c.project_id = p.id
LEFT JOIN contract_milestone cm ON cm.contract_id = c.id
LEFT JOIN ar_invoice ai ON ai.contract_id = c.id AND ai.payment_status != 'CANCELLED'
GROUP BY p.id, p.project_code, p.project_name,
         c.id, c.contract_number, c.contract_title, c.contract_status,
         c.total_value, c.currency, c.term_of_payment_days,
         c.retention_pct, c.retention_amount, c.start_date, c.end_date, c.signing_date;

COMMENT ON VIEW vw_contract_dashboard IS
    'Dashboard utama satu baris per kontrak. '
    'total_uninvoiced = nilai kontrak yang belum diinvoice (milestone belum selesai atau belum dibuat invoice). '
    'total_outstanding = invoice terkirim + overdue yang belum lunas.';


-- ── 7.2  Status billing per milestone ─────────────────────────────────────
CREATE OR REPLACE VIEW vw_milestone_billing AS
SELECT
    cm.id,
    cm.contract_id,
    c.contract_number,
    c.contract_status,
    cm.sequence,
    cm.milestone_name,
    cm.percentage,
    cm.amount_target,
    cm.retention_held,
    cm.amount_target - cm.retention_held AS net_amount_billed,
    cm.trigger_condition,
    cm.work_status,
    cm.bast_number,
    cm.bast_date,
    cm.billing_status,
    cm.due_date,
    ai.id           AS invoice_id,
    ai.invoice_no,
    ai.invoice_date,
    ai.due_date     AS invoice_due_date,
    ai.total_amount AS invoice_amount,
    ai.paid_amount,
    ai.payment_status,
    CASE
        WHEN ai.payment_status = 'OVERDUE'
        THEN (CURRENT_DATE - ai.due_date)
        ELSE 0
    END             AS days_overdue
FROM contract_milestone cm
JOIN project_contract c ON c.id = cm.contract_id
LEFT JOIN ar_invoice ai ON ai.id = cm.invoice_id;


-- ── 7.3  AR Outstanding aging per kontrak ─────────────────────────────────
CREATE OR REPLACE VIEW vw_contract_ar_aging AS
SELECT
    c.entity_id,
    c.id            AS contract_id,
    c.contract_number,
    c.project_id,
    p.project_name,
    v.vendor_name   AS client_name,
    ai.id           AS invoice_id,
    ai.invoice_no,
    ai.invoice_date,
    ai.due_date,
    ai.total_amount,
    ai.paid_amount,
    ai.total_amount - COALESCE(ai.paid_amount, 0) AS outstanding_amount,
    ai.payment_status,
    CURRENT_DATE - ai.due_date AS days_overdue,
    CASE
        WHEN ai.payment_status = 'PAID'    THEN 'PAID'
        WHEN CURRENT_DATE <= ai.due_date   THEN 'CURRENT'
        WHEN CURRENT_DATE - ai.due_date <= 30  THEN '1-30 HARI'
        WHEN CURRENT_DATE - ai.due_date <= 60  THEN '31-60 HARI'
        WHEN CURRENT_DATE - ai.due_date <= 90  THEN '61-90 HARI'
        ELSE '>90 HARI'
    END             AS aging_bucket
FROM ar_invoice ai
JOIN project_contract c ON c.id = ai.contract_id
JOIN project p          ON p.id = c.project_id
LEFT JOIN vendor v      ON v.id = c.client_id
WHERE ai.payment_status NOT IN ('CANCELLED','DRAFT');


-- ── 7.4  Ringkasan outstanding per klien/entity ──────────────────────────
CREATE OR REPLACE VIEW vw_client_outstanding_summary AS
SELECT
    c.entity_id,
    c.client_id,
    v.vendor_name   AS client_name,
    COUNT(DISTINCT c.id) AS total_contracts,
    SUM(
        COALESCE(
            (SELECT SUM(ai2.total_amount - COALESCE(ai2.paid_amount,0))
             FROM ar_invoice ai2
             WHERE ai2.contract_id = c.id
               AND ai2.payment_status IN ('SENT','OVERDUE')
            ), 0)
    )               AS total_outstanding_idr,
    SUM(
        COALESCE(
            (SELECT SUM(ai2.total_amount - COALESCE(ai2.paid_amount,0))
             FROM ar_invoice ai2
             WHERE ai2.contract_id = c.id
               AND ai2.payment_status = 'OVERDUE'
            ), 0)
    )               AS total_overdue_idr
FROM project_contract c
LEFT JOIN vendor v ON v.id = c.client_id
WHERE c.contract_status = 'ACTIVE'
GROUP BY c.entity_id, c.client_id, v.vendor_name;


-- ── 7.5  Kontrak mendekati batas waktu ──────────────────────────────────
CREATE OR REPLACE VIEW vw_contract_expiry_alert AS
SELECT
    c.entity_id,
    c.id            AS contract_id,
    c.contract_number,
    c.contract_title,
    p.project_name,
    v.vendor_name   AS client_name,
    c.end_date,
    c.end_date - CURRENT_DATE AS days_to_expiry,
    c.contract_status,
    CASE
        WHEN c.end_date < CURRENT_DATE           THEN 'EXPIRED'
        WHEN c.end_date - CURRENT_DATE <= 30     THEN 'CRITICAL (≤30 hari)'
        WHEN c.end_date - CURRENT_DATE <= 60     THEN 'WARNING (≤60 hari)'
        ELSE 'OK'
    END             AS expiry_status
FROM project_contract c
JOIN project p   ON p.id = c.project_id
LEFT JOIN vendor v ON v.id = c.client_id
WHERE c.contract_status IN ('ACTIVE','AMENDED')
  AND c.end_date - CURRENT_DATE <= 60;  -- hanya tampil jika ≤60 hari lagi


SELECT 'Migration schema_contract_tracker selesai' AS status;
