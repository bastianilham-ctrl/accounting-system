-- ============================================================
-- SCHEMA: Unit Costing, Analytic Journal & Overhead Allocation
-- Perusahaan Jasa (Consulting) — FP&A / Management Accounting
--
-- Konsep:
--   Primary Ledger  → Laporan Keuangan Perusahaan (GL)
--   Analytic Ledger → Laporan Profitabilitas Per Proyek (secondary, tidak menyentuh GL riil)
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_costing.sql
-- Dependensi: schema_journal_engine.sql, schema_employee.sql, schema_ar.sql
-- ============================================================


-- ============================================================
-- 1. PROJECT — master data proyek/klien
-- ============================================================

CREATE TABLE IF NOT EXISTS project (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID          NOT NULL REFERENCES entity(id),
    project_code    VARCHAR(30)   NOT NULL,            -- PROJ-2026-001
    project_name    VARCHAR(200)  NOT NULL,
    client_name     VARCHAR(200),
    client_id       UUID          REFERENCES vendor(id),   -- opsional: link ke master vendor/klien
    project_type    VARCHAR(30)   NOT NULL DEFAULT 'time_and_material'
        CHECK (project_type IN ('time_and_material', 'fixed_price', 'retainer', 'milestone')),
    status          VARCHAR(20)   NOT NULL DEFAULT 'active'
        CHECK (status IN ('draft', 'active', 'on_hold', 'completed', 'cancelled')),
    start_date      DATE,
    end_date        DATE,
    contract_value  NUMERIC(18,2) NOT NULL DEFAULT 0,     -- nilai kontrak total
    billing_rate_per_hour NUMERIC(18,2),                  -- tarif T&M ke klien per jam
    project_manager VARCHAR(200),
    cost_center     VARCHAR(100),                          -- digunakan sebagai tag di GL & analytic
    notes           TEXT,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    UNIQUE (entity_id, project_code)
);
COMMENT ON TABLE project IS
    'Master proyek aktif. cost_center dipakai sebagai analytic tag di jurnal GL dan analytic journal.';

CREATE INDEX IF NOT EXISTS idx_project_entity  ON project(entity_id, status);
CREATE INDEX IF NOT EXISTS idx_project_client  ON project(client_id);


-- ============================================================
-- 2. EMPLOYEE COST RATE — tarif biaya riil per jam per karyawan
-- ============================================================
-- Rumus: Unit Cost/Jam = CTC Setahun ÷ Target Billable Hours

CREATE TABLE IF NOT EXISTS employee_cost_rate (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    employee_id     UUID          NOT NULL REFERENCES employee(id),
    entity_id       UUID          NOT NULL REFERENCES entity(id),
    fiscal_year     SMALLINT      NOT NULL,

    -- ── Komponen CTC Tahunan (semua angka dalam IDR) ────────────────
    annual_base_salary        NUMERIC(18,2) NOT NULL DEFAULT 0,
        -- gaji pokok + tunjangan tetap × 12 bulan
    annual_thr_bonus          NUMERIC(18,2) NOT NULL DEFAULT 0,
        -- THR + bonus performa tahunan
    annual_bpjs_employer      NUMERIC(18,2) NOT NULL DEFAULT 0,
        -- total BPJS beban perusahaan × 12 bulan
    annual_private_insurance  NUMERIC(18,2) NOT NULL DEFAULT 0,
        -- asuransi swasta (Manulife, Prudential, dll.)
    annual_asset_depreciation NUMERIC(18,2) NOT NULL DEFAULT 0,
        -- penyusutan laptop/aset kerja yang dipakai karyawan ini
    annual_training_budget    NUMERIC(18,2) NOT NULL DEFAULT 0,
        -- budget pelatihan & sertifikasi
    annual_other_benefits     NUMERIC(18,2) NOT NULL DEFAULT 0,
        -- pulsa, internet, lain-lain

    total_ctc       NUMERIC(18,2) NOT NULL DEFAULT 0,   -- sum semua komponen di atas

    -- ── Parameter Target Hours ───────────────────────────────────────
    national_holidays  SMALLINT      NOT NULL DEFAULT 16,   -- libur nasional + cuti bersama
    annual_leave_days  SMALLINT      NOT NULL DEFAULT 12,   -- hak cuti tahunan karyawan
    utilization_rate   NUMERIC(5,4)  NOT NULL DEFAULT 0.80  -- target utilisasi billable
        CHECK (utilization_rate BETWEEN 0.10 AND 1.00),

    -- ── Hasil Kalkulasi (snapshot saat disetujui) ──────────────────
    available_days          SMALLINT,         -- hari kerja tersedia
    available_hours         NUMERIC(10,2),    -- available_days × 8
    target_billable_hours   NUMERIC(10,2),    -- available_hours × utilization_rate
    unit_cost_per_hour      NUMERIC(18,4),    -- total_ctc ÷ target_billable_hours

    -- ── Kontrol ─────────────────────────────────────────────────────
    is_approved     BOOLEAN       NOT NULL DEFAULT FALSE,
    approved_by     VARCHAR(200),
    approved_at     TIMESTAMPTZ,

    notes           TEXT,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    UNIQUE (employee_id, fiscal_year)
);
COMMENT ON TABLE employee_cost_rate IS
    'Tarif biaya langsung per jam (Unit Cost/Hour) per karyawan per tahun fiskal. '
    'Digunakan untuk menilai beban labor allocation ke proyek.';

CREATE INDEX IF NOT EXISTS idx_cost_rate_emp ON employee_cost_rate(employee_id, fiscal_year);


-- ============================================================
-- 3. PROJECT ASSIGNMENT — penugasan karyawan ke proyek
-- ============================================================

CREATE TABLE IF NOT EXISTS project_assignment (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id      UUID          NOT NULL REFERENCES project(id),
    employee_id     UUID          NOT NULL REFERENCES employee(id),
    entity_id       UUID          NOT NULL REFERENCES entity(id),
    role_in_project VARCHAR(100),                  -- "Lead Consultant", "Junior Analyst"
    assigned_from   DATE          NOT NULL,
    assigned_to     DATE,                          -- NULL = masih aktif
    planned_hours   NUMERIC(10,2),                -- estimasi jam yang direncanakan
    billing_rate_override NUMERIC(18,2),          -- override tarif T&M ke klien untuk konsultan ini
    is_active       BOOLEAN       NOT NULL DEFAULT TRUE,
    notes           TEXT,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, employee_id, assigned_from)
);
CREATE INDEX IF NOT EXISTS idx_assign_proj ON project_assignment(project_id);
CREATE INDEX IF NOT EXISTS idx_assign_emp  ON project_assignment(employee_id, is_active);


-- ============================================================
-- 4. PROJECT TIMESHEET — log jam kerja harian per proyek
-- ============================================================
-- Berbeda dengan attendance timesheet (hadir/tidak).
-- Ini mencatat: untuk proyek apa jam itu digunakan.

CREATE TABLE IF NOT EXISTS project_timesheet (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    employee_id     UUID          NOT NULL REFERENCES employee(id),
    project_id      UUID          REFERENCES project(id),   -- NULL = bench / internal
    entity_id       UUID          NOT NULL REFERENCES entity(id),
    timesheet_date  DATE          NOT NULL,
    hours           NUMERIC(5,2)  NOT NULL CHECK (hours > 0 AND hours <= 24),
    activity_type   VARCHAR(30)   NOT NULL DEFAULT 'billable'
        CHECK (activity_type IN ('billable', 'non_billable', 'bench', 'internal', 'training')),
    description     TEXT,

    -- Workflow
    status          VARCHAR(20)   NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'submitted', 'approved', 'rejected')),
    submitted_at    TIMESTAMPTZ,
    approved_by     VARCHAR(200),
    approved_at     TIMESTAMPTZ,
    rejection_reason TEXT,

    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE project_timesheet IS
    'Log jam harian konsultan per proyek. Status approved dibutuhkan agar dapat diproses oleh '
    'labor allocation engine.';

CREATE INDEX IF NOT EXISTS idx_pts_emp_date    ON project_timesheet(employee_id, timesheet_date);
CREATE INDEX IF NOT EXISTS idx_pts_proj        ON project_timesheet(project_id, timesheet_date);
CREATE INDEX IF NOT EXISTS idx_pts_entity_stat ON project_timesheet(entity_id, status, timesheet_date);


-- ============================================================
-- 5. ALLOCATION RULES — aturan alokasi overhead ke proyek
-- ============================================================

CREATE TABLE IF NOT EXISTS allocation_rules (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id            UUID         NOT NULL REFERENCES entity(id),
    rule_name            VARCHAR(100) NOT NULL,
    description          TEXT,
    sender_cost_center   VARCHAR(100) NOT NULL,  -- pool G&A (contoh: 'GA', 'OVERHEAD', 'SHARED')
    allocation_basis     VARCHAR(30)  NOT NULL DEFAULT 'REVENUE'
        CHECK (allocation_basis IN ('REVENUE', 'TIMESHEET', 'HEADCOUNT', 'EQUAL')),
    overhead_account_code VARCHAR(50),           -- akun beban alokasi overhead di analytic
    is_active            BOOLEAN      NOT NULL DEFAULT TRUE,
    created_by           VARCHAR(200),
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE allocation_rules IS
    'Mendefinisikan dari mana overhead diambil (sender_cost_center) dan '
    'cara pembaginya (allocation_basis). Dieksekusi saat month-end closing.';

CREATE TABLE IF NOT EXISTS allocation_destinations (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    rule_id         UUID          NOT NULL REFERENCES allocation_rules(id) ON DELETE CASCADE,
    project_id      UUID          NOT NULL REFERENCES project(id),
    fixed_ratio     NUMERIC(10,8),  -- NULL = dinamis dari basis; non-NULL = rasio tetap (total harus = 1)
    UNIQUE (rule_id, project_id)
);
COMMENT ON TABLE allocation_destinations IS
    'Daftar proyek yang berhak menerima distribusi overhead dari sebuah rule.';


-- ============================================================
-- 6. ANALYTIC JOURNAL — secondary ledger (tidak menyentuh GL)
-- ============================================================
-- Governance (BAB V): murni analitik, tidak mengubah saldo Kas/Bank/Piutang/Utang.

CREATE TABLE IF NOT EXISTS analytic_journal (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID          NOT NULL REFERENCES entity(id),
    journal_no      VARCHAR(30)   NOT NULL UNIQUE,   -- AJ/2026/06/0001
    journal_date    DATE          NOT NULL,
    year            SMALLINT      NOT NULL,
    month           SMALLINT      NOT NULL CHECK (month BETWEEN 1 AND 12),
    journal_type    VARCHAR(30)   NOT NULL
        CHECK (journal_type IN ('labor_allocation', 'revenue_tagging', 'overhead_allocation', 'manual')),
    rule_id         UUID          REFERENCES allocation_rules(id),  -- overhead_allocation saja
    source_ref      VARCHAR(200),   -- periode timesheet, ar_invoice_id, dll.
    description     TEXT,

    status          VARCHAR(20)   NOT NULL DEFAULT 'posted'
        CHECK (status IN ('posted', 'reversed')),
    reversed_by_id  UUID          REFERENCES analytic_journal(id),  -- jurnal reversal-nya

    total_debit     NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_credit    NUMERIC(18,2) NOT NULL DEFAULT 0,

    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_aj_period ON analytic_journal(entity_id, year, month, status);
CREATE INDEX IF NOT EXISTS idx_aj_type   ON analytic_journal(entity_id, journal_type, year, month);

-- Baris detail analytic journal
CREATE TABLE IF NOT EXISTS analytic_journal_line (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    journal_id      UUID          NOT NULL REFERENCES analytic_journal(id) ON DELETE CASCADE,
    line_no         SMALLINT      NOT NULL,

    project_id      UUID          REFERENCES project(id),   -- NULL = bench / pool clearing
    cost_center     VARCHAR(100),                           -- project_code atau 'BENCH' atau pool code
    account_code    VARCHAR(50),                            -- referensi GL account untuk kategori
    account_type    VARCHAR(30)   NOT NULL
        CHECK (account_type IN (
            'revenue',          -- Pendapatan proyek
            'direct_labor',     -- HPP tenaga kerja langsung (billable)
            'idle_labor',       -- Biaya tenaga kerja bench/idle
            'overhead',         -- Overhead dialokasikan ke proyek
            'pool_clearing',    -- Pembersih pool overhead (kredit)
            'other'
        )),

    debit_amount    NUMERIC(18,2) NOT NULL DEFAULT 0,   -- sisi beban / cost
    credit_amount   NUMERIC(18,2) NOT NULL DEFAULT 0,   -- sisi pendapatan / clearing

    -- Labor allocation metadata
    employee_id         UUID          REFERENCES employee(id),
    billable_hours      NUMERIC(10,4),
    unit_cost_per_hour  NUMERIC(18,4),

    -- Overhead allocation metadata
    rule_id             UUID          REFERENCES allocation_rules(id),
    allocation_ratio    NUMERIC(10,8),

    source_ref          VARCHAR(200),
    description         TEXT,

    UNIQUE (journal_id, line_no)
);
CREATE INDEX IF NOT EXISTS idx_ajl_project  ON analytic_journal_line(project_id);
CREATE INDEX IF NOT EXISTS idx_ajl_employee ON analytic_journal_line(employee_id);


-- ============================================================
-- 7. ANALYTIC PERIOD — kontrol lock periode analitik
-- ============================================================
-- Governance (BAB V): setelah divalidasi oleh Financial Controller,
-- status di-lock (Read-Only) untuk mencegah manipulasi retroaktif.

CREATE TABLE IF NOT EXISTS analytic_period (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID          NOT NULL REFERENCES entity(id),
    year            SMALLINT      NOT NULL,
    month           SMALLINT      NOT NULL CHECK (month BETWEEN 1 AND 12),

    status          VARCHAR(20)   NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'processing', 'locked')),

    -- Flag step-step yang sudah diselesaikan
    labor_allocation_posted    BOOLEAN NOT NULL DEFAULT FALSE,
    revenue_tagging_posted     BOOLEAN NOT NULL DEFAULT FALSE,
    overhead_allocation_posted BOOLEAN NOT NULL DEFAULT FALSE,

    locked_by       VARCHAR(200),
    locked_at       TIMESTAMPTZ,
    notes           TEXT,

    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    UNIQUE (entity_id, year, month)
);
COMMENT ON TABLE analytic_period IS
    'Satu record per bulan per entity. Setelah locked, tidak ada entri analitik baru yang '
    'dapat dibuat untuk periode tersebut.';


-- ============================================================
-- 8. ALTER ar_invoice — tambah project_id untuk revenue tagging
-- ============================================================

ALTER TABLE ar_invoice ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES project(id);
CREATE INDEX IF NOT EXISTS idx_ar_invoice_project ON ar_invoice(project_id) WHERE project_id IS NOT NULL;


-- ============================================================
-- 9. VIEWS
-- ============================================================

-- Project P&L per bulan (dari analytic journal)
CREATE OR REPLACE VIEW vw_project_pnl AS
SELECT
    aj.entity_id,
    aj.year,
    aj.month,
    COALESCE(p.project_code, 'BENCH')     AS project_code,
    COALESCE(p.project_name, 'Non-Project (Bench/Internal)') AS project_name,
    p.client_name,
    p.project_type,

    -- Revenue
    COALESCE(SUM(ajl.credit_amount) FILTER (WHERE ajl.account_type = 'revenue'), 0)
        AS revenue,

    -- Direct Labor HPP
    COALESCE(SUM(ajl.debit_amount) FILTER (WHERE ajl.account_type = 'direct_labor'), 0)
        AS direct_labor_cost,

    -- Idle Labor (bench)
    COALESCE(SUM(ajl.debit_amount) FILTER (WHERE ajl.account_type = 'idle_labor'), 0)
        AS idle_labor_cost,

    -- Overhead teralokasi
    COALESCE(SUM(ajl.debit_amount) FILTER (WHERE ajl.account_type = 'overhead'), 0)
        AS overhead_allocated,

    -- Total cost proyek
    COALESCE(SUM(ajl.debit_amount) FILTER (WHERE ajl.account_type IN ('direct_labor', 'overhead', 'other')), 0)
        AS total_direct_cost,

    -- Gross Profit proyek
    COALESCE(SUM(ajl.credit_amount) FILTER (WHERE ajl.account_type = 'revenue'), 0)
    - COALESCE(SUM(ajl.debit_amount) FILTER (WHERE ajl.account_type IN ('direct_labor', 'overhead')), 0)
        AS gross_profit,

    -- Jam billable
    COALESCE(SUM(ajl.billable_hours) FILTER (WHERE ajl.account_type = 'direct_labor'), 0)
        AS billable_hours

FROM analytic_journal aj
JOIN analytic_journal_line ajl ON ajl.journal_id = aj.id AND aj.status = 'posted'
LEFT JOIN project p ON p.id = ajl.project_id
GROUP BY aj.entity_id, aj.year, aj.month, p.id, p.project_code, p.project_name,
         p.client_name, p.project_type;


-- Utilisasi jam kerja konsultan per bulan
CREATE OR REPLACE VIEW vw_labor_utilization AS
SELECT
    ajl.employee_id,
    e.full_name                                                 AS employee_name,
    e.job_title,
    aj.entity_id,
    aj.year,
    aj.month,

    COALESCE(SUM(ajl.billable_hours) FILTER (WHERE ajl.account_type = 'direct_labor'), 0)
        AS billable_hours,
    COALESCE(SUM(ajl.billable_hours) FILTER (WHERE ajl.account_type = 'idle_labor'), 0)
        AS bench_hours,
    COALESCE(SUM(ajl.billable_hours), 0)
        AS total_hours_allocated,

    CASE WHEN COALESCE(SUM(ajl.billable_hours), 0) > 0 THEN
        ROUND(
            COALESCE(SUM(ajl.billable_hours) FILTER (WHERE ajl.account_type = 'direct_labor'), 0)
            / COALESCE(SUM(ajl.billable_hours), 0) * 100,
        2)
    ELSE 0 END                                                  AS utilization_pct,

    COALESCE(AVG(ajl.unit_cost_per_hour) FILTER (WHERE ajl.unit_cost_per_hour IS NOT NULL), 0)
        AS unit_cost_per_hour,

    -- Total cost absorption
    COALESCE(SUM(ajl.debit_amount), 0) AS total_cost_absorbed

FROM analytic_journal aj
JOIN analytic_journal_line ajl ON ajl.journal_id = aj.id AND aj.status = 'posted'
JOIN employee e ON e.id = ajl.employee_id
WHERE aj.journal_type = 'labor_allocation'
  AND ajl.employee_id IS NOT NULL
GROUP BY ajl.employee_id, e.full_name, e.job_title, aj.entity_id, aj.year, aj.month;


-- Ringkasan overhead allocation per rule per bulan
CREATE OR REPLACE VIEW vw_overhead_allocation_detail AS
SELECT
    aj.entity_id,
    aj.year,
    aj.month,
    ar.rule_name,
    ar.sender_cost_center,
    ar.allocation_basis,
    p.project_code,
    p.project_name,
    ajl.allocation_ratio,
    ajl.debit_amount    AS allocated_amount,
    ajl.description
FROM analytic_journal aj
JOIN analytic_journal_line ajl ON ajl.journal_id = aj.id AND aj.status = 'posted'
LEFT JOIN allocation_rules ar ON ar.id = ajl.rule_id
LEFT JOIN project p ON p.id = ajl.project_id
WHERE aj.journal_type = 'overhead_allocation'
  AND ajl.account_type = 'overhead';


SELECT 'Migration schema_costing selesai' AS status;
