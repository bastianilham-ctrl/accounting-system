-- ============================================================
-- SCHEMA: Budget Management (FICO / EPM)
-- Jalankan: psql -U postgres -d accounting_db -f schema_budget.sql
-- Dependensi: schema_journal_engine.sql (entity, chart_of_accounts, gl_journal)
-- ============================================================

-- ============================================================
-- 1. BUDGET PERIOD — rencana anggaran satu tahun fiskal
-- ============================================================

CREATE TABLE IF NOT EXISTS budget_period (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    fiscal_year     SMALLINT     NOT NULL,
    budget_version  VARCHAR(20)  NOT NULL DEFAULT 'ORIGINAL',  -- ORIGINAL, REV-1, REV-2, ...
    description     TEXT,

    -- Pengendalian budget
    control_mode    VARCHAR(10)  NOT NULL DEFAULT 'soft'
        CHECK (control_mode IN ('hard', 'soft', 'off')),
        -- hard : transaksi melebihi budget DITOLAK sistem
        -- soft : lolos dengan peringatan + notifikasi ke controller
        -- off  : tidak ada pengecekan budget

    -- Workflow
    status          VARCHAR(20)  NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'submitted', 'approved', 'released', 'closed')),
    submitted_by    VARCHAR(200),
    submitted_at    TIMESTAMPTZ,
    approved_by     VARCHAR(200),
    approved_at     TIMESTAMPTZ,
    released_by     VARCHAR(200),
    released_at     TIMESTAMPTZ,

    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (entity_id, fiscal_year, budget_version)
);
COMMENT ON TABLE budget_period IS
    'Header anggaran per tahun fiskal. Status harus "released" agar Budget Control Engine aktif.';
COMMENT ON COLUMN budget_period.control_mode IS
    '"hard" = tolak transaksi melebihi budget; "soft" = warning saja; "off" = tidak dicek.';


-- ============================================================
-- 2. BUDGET LINE — anggaran per cost center × account × bulan
-- ============================================================

CREATE TABLE IF NOT EXISTS budget_line (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    period_id       UUID         NOT NULL REFERENCES budget_period(id) ON DELETE CASCADE,
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    budget_no       VARCHAR(40)  UNIQUE,     -- identitas unik, mis. BUD/2026/0001 — dirujuk PR
    activity_description TEXT    NOT NULL DEFAULT '',  -- aktivitas yang dianggarkan
    cost_center     VARCHAR(100) NOT NULL,   -- Departemen: "IT", "Marketing", "GA", dll.
    account_code    VARCHAR(50)  NOT NULL,   -- GL Account (beban)
    year            SMALLINT     NOT NULL,
    month           SMALLINT     NOT NULL CHECK (month BETWEEN 1 AND 12),
    budgeted_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
    notes           TEXT,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (period_id, cost_center, account_code, year, month)
);
COMMENT ON TABLE budget_line IS
    'Satu record per kombinasi cost_center + account_code + bulan dalam satu periode anggaran. '
    'budget_no adalah identitas unik baris ini yang dirujuk dokumen lain (mis. PR/PO).';

-- Idempotent untuk database yang sudah punya tabel budget_line tanpa kolom ini
ALTER TABLE budget_line ADD COLUMN IF NOT EXISTS budget_no VARCHAR(40);
ALTER TABLE budget_line ADD COLUMN IF NOT EXISTS activity_description TEXT NOT NULL DEFAULT '';
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'budget_line_budget_no_key'
    ) THEN
        ALTER TABLE budget_line ADD CONSTRAINT budget_line_budget_no_key UNIQUE (budget_no);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_budline_period   ON budget_line(period_id);
CREATE INDEX IF NOT EXISTS idx_budline_cc_acc   ON budget_line(entity_id, cost_center, account_code, year, month);


-- ============================================================
-- 3. BUDGET APPROVAL — audit trail persetujuan anggaran
-- ============================================================

CREATE TABLE IF NOT EXISTS budget_approval (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    period_id   UUID         NOT NULL REFERENCES budget_period(id) ON DELETE CASCADE,
    sequence    SMALLINT     NOT NULL,
    approver    VARCHAR(200) NOT NULL,
    action      VARCHAR(20)  NOT NULL CHECK (action IN ('approved', 'rejected', 'returned')),
    notes       TEXT,
    acted_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);


-- ============================================================
-- 4. BUDGET TRANSFER — pindah anggaran antar cost center
-- ============================================================

CREATE TABLE IF NOT EXISTS budget_transfer (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    transfer_no     VARCHAR(30)  NOT NULL UNIQUE,  -- BT/2026/06/0001
    fiscal_year     SMALLINT     NOT NULL,
    month           SMALLINT     NOT NULL CHECK (month BETWEEN 1 AND 12),
    transfer_date   DATE         NOT NULL,

    -- Dari mana
    from_period_id  UUID         REFERENCES budget_period(id),
    from_cost_center VARCHAR(100) NOT NULL,
    from_account_code VARCHAR(50) NOT NULL,

    -- Ke mana
    to_period_id    UUID         REFERENCES budget_period(id),
    to_cost_center  VARCHAR(100) NOT NULL,
    to_account_code VARCHAR(50)  NOT NULL,

    amount          NUMERIC(18,2) NOT NULL CHECK (amount > 0),
    reason          TEXT,

    status          VARCHAR(20)  NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'submitted', 'approved', 'rejected')),
    submitted_by    VARCHAR(200),
    submitted_at    TIMESTAMPTZ,
    approved_by     VARCHAR(200),
    approved_at     TIMESTAMPTZ,
    rejection_reason TEXT,

    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE budget_transfer IS
    'Perpindahan (geser) anggaran dari satu cost center ke cost center lain dalam tahun fiskal yang sama.';

CREATE INDEX IF NOT EXISTS idx_budtransfer_entity ON budget_transfer(entity_id, fiscal_year, status);


-- ============================================================
-- 5. BUDGET SUPPLEMENT — tambah plafon (sumber: dana cadangan)
-- ============================================================

CREATE TABLE IF NOT EXISTS budget_supplement (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    supplement_no   VARCHAR(30)  NOT NULL UNIQUE,  -- BS/2026/06/0001
    period_id       UUID         NOT NULL REFERENCES budget_period(id),
    cost_center     VARCHAR(100) NOT NULL,
    account_code    VARCHAR(50)  NOT NULL,
    month           SMALLINT     NOT NULL CHECK (month BETWEEN 1 AND 12),
    amount          NUMERIC(18,2) NOT NULL CHECK (amount > 0),
    reason          TEXT,

    status          VARCHAR(20)  NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'submitted', 'approved', 'rejected')),
    submitted_by    VARCHAR(200),
    approved_by     VARCHAR(200),  -- harus CFO / admin
    approved_at     TIMESTAMPTZ,
    rejection_reason TEXT,

    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE budget_supplement IS
    'Penambahan plafon anggaran dari dana cadangan — harus approved CFO.';


-- ============================================================
-- 6. BUDGET COMMITMENT — encumbrance (komitmen aktif dari PO/PR)
-- ============================================================

CREATE TABLE IF NOT EXISTS budget_commitment (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    cost_center     VARCHAR(100) NOT NULL,
    account_code    VARCHAR(50)  NOT NULL,
    year            SMALLINT     NOT NULL,
    month           SMALLINT     NOT NULL CHECK (month BETWEEN 1 AND 12),

    source_type     VARCHAR(30)  NOT NULL
        CHECK (source_type IN ('purchase_order', 'payroll', 'project', 'other')),
    source_id       UUID,          -- FK ke PO / payroll_run / project ID
    source_ref      VARCHAR(100),  -- Nomor PO untuk display (PO/2026/06/0001)

    committed_amount  NUMERIC(18,2) NOT NULL DEFAULT 0,   -- total commitment dibuat
    released_amount   NUMERIC(18,2) NOT NULL DEFAULT 0,   -- sudah dirilis (ditagihkan)

    status          VARCHAR(20)  NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'partial', 'released', 'cancelled')),

    committed_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    released_at     TIMESTAMPTZ,
    notes           TEXT
);
COMMENT ON TABLE budget_commitment IS
    'Encumbrance (dana yang sudah "dipesan" oleh PO). '
    'Mengurangi saldo anggaran tersedia sebelum invoice masuk.';

CREATE INDEX IF NOT EXISTS idx_commit_entity    ON budget_commitment(entity_id, cost_center, account_code, year, month);
CREATE INDEX IF NOT EXISTS idx_commit_source    ON budget_commitment(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_commit_status    ON budget_commitment(status);


-- ============================================================
-- 7. VIEWS — Budget vs Actual vs Commitment
-- ============================================================

-- Ringkasan utilisasi anggaran per cost center per bulan
CREATE OR REPLACE VIEW vw_budget_utilization AS
SELECT
    bl.entity_id,
    bl.period_id,
    bl.cost_center,
    bl.account_code,
    bl.year,
    bl.month,
    bl.budgeted_amount,

    -- Realisasi (actual) dari GL
    COALESCE((
        SELECT SUM(gl.debit_idr - gl.credit_idr)
        FROM gl_line gl
        JOIN gl_journal j           ON j.id = gl.journal_id
        JOIN chart_of_accounts coa  ON coa.id = gl.account_id
        WHERE coa.account_code = bl.account_code
          AND gl.cost_center   = bl.cost_center
          AND j.entity_id      = bl.entity_id
          AND j.status         = 'posted'
          AND EXTRACT(YEAR  FROM j.journal_date) = bl.year
          AND EXTRACT(MONTH FROM j.journal_date) = bl.month
    ), 0) AS actual_amount,

    -- Komitmen aktif (dari PO yang belum ditagihkan)
    COALESCE((
        SELECT SUM(bc.committed_amount - bc.released_amount)
        FROM budget_commitment bc
        WHERE bc.entity_id    = bl.entity_id
          AND bc.cost_center  = bl.cost_center
          AND bc.account_code = bl.account_code
          AND bc.year         = bl.year
          AND bc.month        = bl.month
          AND bc.status       IN ('active', 'partial')
    ), 0) AS commitment_amount

FROM budget_line bl
JOIN budget_period bp ON bp.id = bl.period_id
WHERE bp.status IN ('released', 'closed');

-- Kalkulasi available di view (kolom computed)
CREATE OR REPLACE VIEW vw_budget_available AS
SELECT
    *,
    budgeted_amount - actual_amount - commitment_amount AS available_amount,
    CASE WHEN budgeted_amount > 0
         THEN ROUND((actual_amount + commitment_amount) * 100.0 / budgeted_amount, 2)
         ELSE NULL
    END AS utilization_pct
FROM vw_budget_utilization;


-- Variance report tahunan — semua bulan dalam satu baris per akun
CREATE OR REPLACE VIEW vw_budget_variance_annual AS
SELECT
    bl.entity_id,
    bl.period_id,
    bl.cost_center,
    bl.account_code,
    bl.year,
    SUM(bl.budgeted_amount)  AS budget_annual,
    COALESCE(SUM(act.actual_month), 0) AS actual_annual,
    SUM(bl.budgeted_amount) - COALESCE(SUM(act.actual_month), 0) AS variance,
    CASE WHEN SUM(bl.budgeted_amount) > 0
         THEN ROUND(COALESCE(SUM(act.actual_month), 0) * 100.0 / SUM(bl.budgeted_amount), 2)
         ELSE NULL
    END AS utilization_pct
FROM budget_line bl
LEFT JOIN (
    SELECT
        coa.account_code,
        gl.cost_center,
        j.entity_id,
        EXTRACT(YEAR FROM j.journal_date)::int AS year,
        SUM(gl.debit_idr - gl.credit_idr) AS actual_month
    FROM gl_line gl
    JOIN gl_journal j           ON j.id = gl.journal_id AND j.status = 'posted'
    JOIN chart_of_accounts coa  ON coa.id = gl.account_id
    GROUP BY coa.account_code, gl.cost_center, j.entity_id, EXTRACT(YEAR FROM j.journal_date)
) act ON act.account_code = bl.account_code
      AND act.cost_center  = bl.cost_center
      AND act.entity_id    = bl.entity_id
      AND act.year         = bl.year
GROUP BY bl.entity_id, bl.period_id, bl.cost_center, bl.account_code, bl.year;


SELECT 'Migration schema_budget selesai' AS status;
