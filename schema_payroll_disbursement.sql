-- ============================================================
-- SCHEMA: Payroll Disbursement
--
-- Payroll engine sudah menghitung gaji. Schema ini menangani:
--   - Batch disbursement per payroll run
--   - Tracking status transfer per karyawan
--   - GL posting: Dr Hutang Gaji | Cr Bank
--   - Export file transfer untuk bank (format CSV)
--
-- Akun GL default:
--   Beban Gaji          : 6-1000
--   Hutang Gaji         : 2-2000
--   Hutang PPh 21       : 2-1200
--   Hutang BPJS TK      : 2-1300
--   Hutang BPJS Kes     : 2-1400
--   Kas/Bank Gaji       : dikonfigurasi per disbursement
--
-- Flow:
--   1. Buat disbursement dari payroll_run yang sudah diapprove
--   2. Approve disbursement (cek limit, cek saldo bank)
--   3. Post accrual journal (jika belum ada): Dr Beban | Cr Hutang
--   4. Post disbursement journal: Dr Hutang Gaji | Cr Bank
--   5. Mark per-karyawan: transferred / failed
--   6. Export bank transfer file
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_payroll_disbursement.sql
-- ============================================================

-- ── 1. PAYROLL DISBURSEMENT BATCH ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS payroll_disbursement (
    id                      UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id               UUID         NOT NULL REFERENCES entity(id),
    payroll_run_id          UUID         NOT NULL,   -- FK ke payroll_run
    fiscal_year             SMALLINT     NOT NULL,
    fiscal_month            SMALLINT     NOT NULL,
    disbursement_date       DATE         NOT NULL,

    -- Rekening perusahaan sumber dana
    bank_account_id         UUID         NOT NULL REFERENCES bank_account(id),

    -- Totals (di-denormalisasi untuk performa)
    total_gross             NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_deductions        NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_net               NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_pph21             NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_bpjs_tk_employee  NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_bpjs_kes_employee NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_bpjs_employer     NUMERIC(18,2) NOT NULL DEFAULT 0,

    -- Count karyawan
    employee_count          INTEGER      NOT NULL DEFAULT 0,
    transferred_count       INTEGER      NOT NULL DEFAULT 0,
    failed_count            INTEGER      NOT NULL DEFAULT 0,
    skipped_count           INTEGER      NOT NULL DEFAULT 0,

    -- GL accounts (bisa dioverride per disbursement)
    gl_salary_payable       VARCHAR(20)  NOT NULL DEFAULT '2-2000',
    gl_pph21_payable        VARCHAR(20)  NOT NULL DEFAULT '2-1200',
    gl_bpjs_tk_payable      VARCHAR(20)  NOT NULL DEFAULT '2-1300',
    gl_bpjs_kes_payable     VARCHAR(20)  NOT NULL DEFAULT '2-1400',
    gl_salary_expense       VARCHAR(20)  NOT NULL DEFAULT '6-1000',
    gl_bpjs_employer_expense VARCHAR(20) NOT NULL DEFAULT '6-1100',

    -- Jurnal GL
    accrual_journal_id      UUID         REFERENCES gl_journal(id),  -- jurnal beban gaji
    disbursement_journal_id UUID         REFERENCES gl_journal(id),  -- jurnal bayar ke karyawan

    -- Status & approval
    status                  VARCHAR(30)  NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'pending_approval', 'approved', 'disbursed', 'cancelled')),
    submitted_by            VARCHAR(200),
    submitted_at            TIMESTAMPTZ,
    approved_by             VARCHAR(200),
    approved_at             TIMESTAMPTZ,
    disbursed_by            VARCHAR(200),
    disbursed_at            TIMESTAMPTZ,
    cancelled_by            VARCHAR(200),
    cancelled_at            TIMESTAMPTZ,
    cancel_reason           TEXT,

    notes                   TEXT,
    created_by              VARCHAR(200),
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Satu payroll_run hanya boleh satu disbursement aktif
    UNIQUE (payroll_run_id)
);

CREATE INDEX IF NOT EXISTS idx_pdisb_entity  ON payroll_disbursement(entity_id, fiscal_year, fiscal_month);
CREATE INDEX IF NOT EXISTS idx_pdisb_status  ON payroll_disbursement(status);
CREATE INDEX IF NOT EXISTS idx_pdisb_bank    ON payroll_disbursement(bank_account_id);


-- ── 2. DISBURSEMENT LINE — per karyawan ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS payroll_disbursement_line (
    id                      UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    disbursement_id         UUID         NOT NULL REFERENCES payroll_disbursement(id) ON DELETE CASCADE,
    employee_id             UUID         NOT NULL,
    employee_code           VARCHAR(50),
    employee_name           VARCHAR(200) NOT NULL,
    department              VARCHAR(200),

    -- Info rekening bank karyawan
    bank_name               VARCHAR(200),
    bank_account_number     VARCHAR(100),
    bank_account_holder     VARCHAR(200),
    bank_branch             VARCHAR(200),

    -- Komponen gaji
    gross_salary            NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_allowances        NUMERIC(18,2) NOT NULL DEFAULT 0,
    pph21_amount            NUMERIC(18,2) NOT NULL DEFAULT 0,
    bpjs_tk_employee        NUMERIC(18,2) NOT NULL DEFAULT 0,
    bpjs_kes_employee       NUMERIC(18,2) NOT NULL DEFAULT 0,
    other_deductions        NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_deductions        NUMERIC(18,2) NOT NULL DEFAULT 0,
    net_salary              NUMERIC(18,2) NOT NULL DEFAULT 0,  -- yang ditransfer ke karyawan

    -- Transfer info
    transfer_reference      VARCHAR(200),   -- nomor referensi dari bank
    status                  VARCHAR(20)  NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'transferred', 'failed', 'skipped')),
    failure_reason          TEXT,
    transferred_at          TIMESTAMPTZ,

    -- GL line yang dibuat saat disbursement
    gl_line_id              UUID         REFERENCES gl_line(id),

    notes                   TEXT
);

CREATE INDEX IF NOT EXISTS idx_pdline_disb  ON payroll_disbursement_line(disbursement_id);
CREATE INDEX IF NOT EXISTS idx_pdline_emp   ON payroll_disbursement_line(employee_id);
CREATE INDEX IF NOT EXISTS idx_pdline_status ON payroll_disbursement_line(status);


-- ── 3. VIEWS ─────────────────────────────────────────────────────────────────

-- Summary per disbursement batch
CREATE OR REPLACE VIEW vw_payroll_disbursement_summary AS
SELECT
    pd.id,
    pd.entity_id,
    e.entity_name,
    pd.fiscal_year,
    pd.fiscal_month,
    pd.disbursement_date,
    pd.status,
    pd.employee_count,
    pd.transferred_count,
    pd.failed_count,
    pd.skipped_count,
    pd.total_gross,
    pd.total_net,
    pd.total_pph21,
    pd.total_bpjs_tk_employee + pd.total_bpjs_kes_employee AS total_employee_deductions,
    pd.total_bpjs_employer AS employer_bpjs_cost,
    ba.bank_name,
    ba.account_number,
    pd.approved_by,
    pd.disbursed_by,
    pd.disbursed_at
FROM payroll_disbursement pd
JOIN entity e      ON e.id  = pd.entity_id
JOIN bank_account ba ON ba.id = pd.bank_account_id;


-- Detail karyawan yang belum ditransfer
CREATE OR REPLACE VIEW vw_payroll_pending_transfer AS
SELECT
    pdl.disbursement_id,
    pdl.employee_id,
    pdl.employee_code,
    pdl.employee_name,
    pdl.department,
    pdl.bank_name,
    pdl.bank_account_number,
    pdl.bank_account_holder,
    pdl.net_salary,
    pdl.status,
    pdl.failure_reason
FROM payroll_disbursement_line pdl
WHERE pdl.status IN ('pending', 'failed');


SELECT 'Migration schema_payroll_disbursement selesai' AS status;
