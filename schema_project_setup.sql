-- ============================================================
-- SCHEMA: Project Setup — General (PMBOK / Prince2 based)
-- Berlaku untuk: Jasa, Dagang, Konstruksi, Manufaktur
--
-- Komponen:
--   1. Cost Center Master
--   2. Project Charter & Scope of Work (SOW)
--   3. Team & RACI Matrix
--   4. WBS (Work Breakdown Structure)
--   5. Task + CPM fields (Early/Late Start-Finish, Float)
--   6. Task Dependencies
--   7. Milestones
--   8. Deliverables
--   9. Project Budget Lines
--  10. Risk Register
--  11. Communication Plan
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_project_setup.sql
-- Dependensi: schema_costing.sql (project, employee), schema_vendor_registration.sql (vendor/entity)
-- ============================================================


-- ============================================================
-- 1. COST CENTER — master hierarki pusat biaya
-- ============================================================

CREATE TABLE IF NOT EXISTS cost_center (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    cc_code         VARCHAR(30)  NOT NULL,
    cc_name         VARCHAR(100) NOT NULL,
    parent_id       UUID         REFERENCES cost_center(id),
    cc_type         VARCHAR(20)  NOT NULL DEFAULT 'department'
        CHECK (cc_type IN (
            'department',   -- divisi/departemen rutin
            'project',      -- cost center proyek (temporer)
            'overhead',     -- biaya umum yang dialokasikan
            'shared'        -- layanan bersama (IT, HR, Finance)
        )),
    manager_employee_id UUID     REFERENCES employee(id),
    gl_account_code    VARCHAR(50),  -- akun GL yang digunakan untuk akumulasi biaya CC ini
    is_active          BOOLEAN   NOT NULL DEFAULT TRUE,
    created_by         VARCHAR(200),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (entity_id, cc_code)
);
CREATE INDEX IF NOT EXISTS idx_cc_entity ON cost_center(entity_id, cc_type);
COMMENT ON TABLE cost_center IS
    'Master Cost Center hierarki. CC type=project biasanya dibuat per proyek, '
    'dihapus atau di-inactivate saat proyek selesai.';


-- Annual budget per cost center (opsional, untuk tracking budget CC)
CREATE TABLE IF NOT EXISTS cost_center_budget (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    cost_center_id  UUID         NOT NULL REFERENCES cost_center(id),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    fiscal_year     SMALLINT     NOT NULL,
    budget_amount   NUMERIC(18,2) NOT NULL DEFAULT 0,
    notes           TEXT,
    approved_by     VARCHAR(200),
    approved_at     TIMESTAMPTZ,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (cost_center_id, fiscal_year)
);


-- ALTER TABLE project — tambah FK ke cost_center master (existing column cost_center adalah VARCHAR)
-- Catatan: project.project_type SUDAH ADA dari schema_costing.sql dengan arti BERBEDA
-- (tipe billing: time_and_material/fixed_price/retainer/milestone) — dipakai modul Costing.
-- Project Setup butuh klasifikasi industri/domain (general/construction/software/...), jadi
-- dipakai kolom BARU `industry_type`, bukan menimpa/konflik dengan project_type milik Costing.
ALTER TABLE project ADD COLUMN IF NOT EXISTS cost_center_id UUID REFERENCES cost_center(id);
ALTER TABLE project ADD COLUMN IF NOT EXISTS industry_type VARCHAR(30) NOT NULL DEFAULT 'general'
    CHECK (industry_type IN ('general','construction','software','consulting','research','internal'));
ALTER TABLE project ADD COLUMN IF NOT EXISTS priority VARCHAR(10) DEFAULT 'medium'
    CHECK (priority IN ('low','medium','high','critical'));
ALTER TABLE project ADD COLUMN IF NOT EXISTS project_manager_id UUID REFERENCES employee(id);
ALTER TABLE project ADD COLUMN IF NOT EXISTS sponsor_id UUID REFERENCES employee(id);
ALTER TABLE project ADD COLUMN IF NOT EXISTS currency VARCHAR(3) DEFAULT 'IDR';
ALTER TABLE project ADD COLUMN IF NOT EXISTS contingency_pct NUMERIC(5,2) DEFAULT 5.00;
ALTER TABLE project ADD COLUMN IF NOT EXISTS charter_status VARCHAR(20) DEFAULT 'draft'
    CHECK (charter_status IN ('draft','pending_approval','approved','rejected','on_hold','closed'));
-- budget_amount: anggaran proyek dari sisi PM (beda dari contract_value milik Costing yang
-- merepresentasikan nilai kontrak billing ke klien) — dipakai EVM (BAC) dan project_budget_line rollup.
ALTER TABLE project ADD COLUMN IF NOT EXISTS budget_amount NUMERIC(18,2) NOT NULL DEFAULT 0;
-- status existing (dari schema_costing.sql) cuma punya draft/active/on_hold/completed/cancelled —
-- Project Setup butuh tahap 'planning' (charter belum berjalan, masih setup WBS/Gantt).
ALTER TABLE project DROP CONSTRAINT IF EXISTS project_status_check;
ALTER TABLE project ADD CONSTRAINT project_status_check
    CHECK (status IN ('draft','planning','active','on_hold','completed','cancelled'));


-- ============================================================
-- 2. PROJECT SCOPE OF WORK (SOW) — dokumen ruang lingkup
-- ============================================================

CREATE TABLE IF NOT EXISTS project_scope (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id      UUID         NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    -- Tujuan proyek SMART
    objective       TEXT         NOT NULL,
    -- Ringkasan in-scope dan out-of-scope (detail per baris di project_scope_item)
    in_scope_summary  TEXT,
    out_scope_summary TEXT,
    -- Asumsi & Kendala
    assumptions     TEXT,
    constraints     TEXT,
    -- Acceptance criteria: syarat proyek dinyatakan selesai
    acceptance_criteria TEXT,
    -- Version control scope
    version         SMALLINT     NOT NULL DEFAULT 1,
    is_current      BOOLEAN      NOT NULL DEFAULT TRUE,
    approved_by     VARCHAR(200),
    approved_at     TIMESTAMPTZ,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pscope_project ON project_scope(project_id, is_current);
COMMENT ON TABLE project_scope IS
    'Satu proyek bisa punya beberapa versi scope (change request). '
    'is_current=TRUE menunjukkan versi yang aktif berlaku.';


CREATE TABLE IF NOT EXISTS project_scope_item (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id      UUID         NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    scope_id        UUID         NOT NULL REFERENCES project_scope(id) ON DELETE CASCADE,
    item_type       VARCHAR(20)  NOT NULL
        CHECK (item_type IN ('in_scope','out_of_scope','assumption','constraint','acceptance')),
    sequence        SMALLINT     NOT NULL DEFAULT 1,
    description     TEXT         NOT NULL,
    UNIQUE (scope_id, item_type, sequence)
);


-- ============================================================
-- 3. PROJECT TEAM & RACI MATRIX
-- ============================================================

CREATE TABLE IF NOT EXISTS project_team_member (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id      UUID         NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    employee_id     UUID         NOT NULL REFERENCES employee(id),
    role_in_project VARCHAR(50)  NOT NULL
        CHECK (role_in_project IN (
            'sponsor',          -- Project Sponsor (jajaran direksi)
            'project_manager',  -- PM
            'team_lead',        -- Tech Lead / Lead Konsultan
            'member',           -- anggota tim
            'consultant',       -- konsultan eksternal
            'reviewer',         -- reviewer / QA
            'stakeholder'       -- pemangku kepentingan non-teknis
        )),
    allocation_pct  NUMERIC(5,2) NOT NULL DEFAULT 100.00  -- % waktu dedikasi ke proyek ini
        CHECK (allocation_pct > 0 AND allocation_pct <= 100),
    join_date       DATE,
    end_date        DATE,
    notes           TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, employee_id)
);
CREATE INDEX IF NOT EXISTS idx_ptm_project  ON project_team_member(project_id);
CREATE INDEX IF NOT EXISTS idx_ptm_employee ON project_team_member(employee_id);

-- RACI per aktivitas / WBS item
CREATE TABLE IF NOT EXISTS raci_entry (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id      UUID         NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    activity_name   VARCHAR(200) NOT NULL,      -- nama aktivitas / fase
    wbs_item_id     UUID,                        -- FK ke wbs_item (opsional, diisi setelah WBS dibuat)
    employee_id     UUID         NOT NULL REFERENCES employee(id),
    raci_role       CHAR(1)      NOT NULL CHECK (raci_role IN ('R','A','C','I')),
        -- R = Responsible (yang mengerjakan)
        -- A = Accountable (penanggung jawab — hanya 1 per aktivitas)
        -- C = Consulted (dimintai pendapat)
        -- I = Informed (dikabari)
    UNIQUE (project_id, activity_name, employee_id)
);
COMMENT ON TABLE raci_entry IS
    'Setiap aktivitas harus punya tepat 1 Accountable (A). '
    'Bisa punya banyak Responsible, Consulted, dan Informed.';


-- ============================================================
-- 4. WBS — Work Breakdown Structure (hierarki paket kerja)
-- ============================================================

CREATE TABLE IF NOT EXISTS wbs_item (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id      UUID         NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    parent_id       UUID         REFERENCES wbs_item(id),
    wbs_code        VARCHAR(30)  NOT NULL,   -- e.g. "1", "1.1", "1.1.2"
    wbs_name        VARCHAR(200) NOT NULL,
    description     TEXT,
    level           SMALLINT     NOT NULL DEFAULT 1,  -- kedalaman hierarki
    is_work_package BOOLEAN      NOT NULL DEFAULT FALSE,
        -- TRUE = leaf node, bisa dikaitkan ke task
    planned_hours   NUMERIC(10,2) NOT NULL DEFAULT 0,
    planned_cost    NUMERIC(18,2) NOT NULL DEFAULT 0,
    sequence        SMALLINT     NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, wbs_code)
);
CREATE INDEX IF NOT EXISTS idx_wbs_project ON wbs_item(project_id, parent_id);


-- ============================================================
-- 5. PROJECT TASK — dengan field CPM (ES/EF/LS/LF/Float)
-- ============================================================

CREATE TABLE IF NOT EXISTS project_task (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id      UUID         NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    wbs_item_id     UUID         REFERENCES wbs_item(id),
    task_code       VARCHAR(30),    -- T-001, T-002, ...
    task_name       VARCHAR(200) NOT NULL,
    description     TEXT,

    -- ── Penugasan ────────────────────────────────────────────────────────────
    assigned_to_id  UUID         REFERENCES employee(id),
    reviewer_id     UUID         REFERENCES employee(id),

    -- ── Waktu rencana (input manual / CPM output) ─────────────────────────
    planned_start   DATE         NOT NULL,
    planned_end     DATE         NOT NULL,
    duration_days   SMALLINT     NOT NULL DEFAULT 1 CHECK (duration_days >= 0),
    planned_hours   NUMERIC(10,2) NOT NULL DEFAULT 0,

    -- ── Aktualisasi ────────────────────────────────────────────────────────
    actual_start    DATE,
    actual_end      DATE,
    actual_hours    NUMERIC(10,2) NOT NULL DEFAULT 0,
    progress_pct    SMALLINT     NOT NULL DEFAULT 0 CHECK (progress_pct BETWEEN 0 AND 100),

    -- ── Status ─────────────────────────────────────────────────────────────
    status          VARCHAR(20)  NOT NULL DEFAULT 'not_started'
        CHECK (status IN (
            'not_started','in_progress','completed','blocked','on_hold','cancelled'
        )),

    -- ── CPM Fields (dihitung oleh engine, disimpan untuk caching) ──────────
    early_start     DATE,        -- ES: earliest possible start
    early_finish    DATE,        -- EF: earliest possible finish
    late_start      DATE,        -- LS: latest allowable start
    late_finish     DATE,        -- LF: latest allowable finish
    total_float     SMALLINT,    -- LF - EF (hari); 0 = critical path
    free_float      SMALLINT,    -- float bebas (tidak mempengaruhi successor)
    is_critical     BOOLEAN      NOT NULL DEFAULT FALSE,

    -- ── Biaya ──────────────────────────────────────────────────────────────
    planned_cost    NUMERIC(18,2) NOT NULL DEFAULT 0,
    actual_cost     NUMERIC(18,2) NOT NULL DEFAULT 0,

    notes           TEXT,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ptask_project  ON project_task(project_id, status);
CREATE INDEX IF NOT EXISTS idx_ptask_assigned ON project_task(assigned_to_id);
CREATE INDEX IF NOT EXISTS idx_ptask_wbs      ON project_task(wbs_item_id);
COMMENT ON TABLE project_task IS
    'early_start/early_finish/late_start/late_finish/total_float/is_critical '
    'diisi oleh ProjectSetupEngine.compute_cpm(). Jangan edit manual.';


-- ============================================================
-- 6. TASK DEPENDENCY — ketergantungan antar task
-- ============================================================

CREATE TABLE IF NOT EXISTS task_dependency (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id      UUID         NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    predecessor_id  UUID         NOT NULL REFERENCES project_task(id) ON DELETE CASCADE,
    successor_id    UUID         NOT NULL REFERENCES project_task(id) ON DELETE CASCADE,
    dependency_type VARCHAR(5)   NOT NULL DEFAULT 'FS'
        CHECK (dependency_type IN (
            'FS',   -- Finish-to-Start  : B mulai setelah A selesai (paling umum)
            'SS',   -- Start-to-Start   : B mulai setelah A mulai
            'FF',   -- Finish-to-Finish : B selesai setelah A selesai
            'SF'    -- Start-to-Finish  : B selesai setelah A mulai
        )),
    lag_days        SMALLINT     NOT NULL DEFAULT 0,  -- jeda tambahan (positif) atau overlap (negatif)
    UNIQUE (predecessor_id, successor_id)
);
COMMENT ON TABLE task_dependency IS
    'FS (Finish-to-Start) adalah default dan paling umum. '
    'lag_days=-2 artinya successor bisa mulai 2 hari sebelum predecessor selesai (overlap).';


-- ============================================================
-- 7. MILESTONES — titik capai utama (durasi nol)
-- ============================================================

CREATE TABLE IF NOT EXISTS project_milestone (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id      UUID         NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    milestone_name  VARCHAR(200) NOT NULL,
    description     TEXT,
    target_date     DATE         NOT NULL,
    actual_date     DATE,
    linked_task_id  UUID         REFERENCES project_task(id),
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','achieved','missed','at_risk')),
    sequence        SMALLINT     NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ms_project ON project_milestone(project_id, status);


-- ============================================================
-- 8. DELIVERABLES — produk luaran proyek
-- ============================================================

CREATE TABLE IF NOT EXISTS project_deliverable (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id      UUID         NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    phase           VARCHAR(30)  NOT NULL DEFAULT 'execution'
        CHECK (phase IN (
            'initiation',   -- Kick-off, SOW approval
            'planning',     -- Blueprint, WBS, Gantt
            'execution',    -- Deliverable utama (produk/sistem)
            'monitoring',   -- Laporan progres, laporan UAT
            'closing'       -- BAST, dokumentasi final
        )),
    deliverable_name VARCHAR(200) NOT NULL,
    description      TEXT,
    deliverable_type VARCHAR(20)  NOT NULL DEFAULT 'technical'
        CHECK (deliverable_type IN ('technical','documentation','approval','report','training')),
    due_date         DATE         NOT NULL,
    delivered_date   DATE,
    responsible_id   UUID         REFERENCES employee(id),
    status           VARCHAR(20)  NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','in_progress','delivered','accepted','rejected')),
    acceptance_notes TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_deliv_project ON project_deliverable(project_id, phase, status);


-- ============================================================
-- 9. PROJECT BUDGET LINES — rencana anggaran proyek
-- ============================================================

CREATE TABLE IF NOT EXISTS project_budget_line (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id      UUID         NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    cost_type       VARCHAR(30)  NOT NULL
        CHECK (cost_type IN (
            'direct_labor',     -- gaji/honor manpower proyek (dari unit cost × jam)
            'direct_material',  -- material/bahan langsung
            'software',         -- lisensi, tools, SaaS
            'hardware',         -- peralatan, infrastruktur
            'travel',           -- perjalanan dinas
            'subcontractor',    -- pihak ketiga / subkon
            'indirect',         -- porsi overhead dialokasikan
            'contingency',      -- cadangan risiko
            'other'             -- lain-lain
        )),
    description     VARCHAR(200) NOT NULL,
    wbs_item_id     UUID         REFERENCES wbs_item(id),
    quantity        NUMERIC(12,4) NOT NULL DEFAULT 1,
    unit_price      NUMERIC(18,2) NOT NULL DEFAULT 0,
    planned_amount  NUMERIC(18,2) GENERATED ALWAYS AS (quantity * unit_price) STORED,
    actual_amount   NUMERIC(18,2) NOT NULL DEFAULT 0,  -- diupdate dari timesheet/AP
    gl_account_code VARCHAR(50),
    notes           TEXT,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pbl_project ON project_budget_line(project_id, cost_type);
COMMENT ON TABLE project_budget_line IS
    'planned_amount = quantity × unit_price (computed). '
    'actual_amount diisi dari timesheet (direct_labor) atau AP invoice (material/subkon).';


-- ============================================================
-- 10. RISK REGISTER
-- ============================================================

CREATE TABLE IF NOT EXISTS project_risk (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id      UUID         NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    risk_code       VARCHAR(20),   -- R-001, R-002
    risk_title      VARCHAR(200) NOT NULL,
    description     TEXT,
    category        VARCHAR(20)  NOT NULL DEFAULT 'technical'
        CHECK (category IN (
            'technical',    -- risiko teknis / teknologi
            'financial',    -- fluktuasi biaya, kurs
            'resource',     -- ketersediaan SDM
            'schedule',     -- keterlambatan jadwal
            'scope',        -- perluasan scope
            'external',     -- regulasi, vendor, force majeure
            'quality'       -- standar kualitas tidak terpenuhi
        )),

    -- Skala 1-5: 1=Sangat Rendah, 5=Sangat Tinggi
    probability     SMALLINT     NOT NULL DEFAULT 3 CHECK (probability BETWEEN 1 AND 5),
    impact          SMALLINT     NOT NULL DEFAULT 3 CHECK (impact BETWEEN 1 AND 5),
    risk_score      SMALLINT     GENERATED ALWAYS AS (probability * impact) STORED,
    risk_level      VARCHAR(10)  GENERATED ALWAYS AS (
        CASE
            WHEN (probability * impact) >= 15 THEN 'critical'
            WHEN (probability * impact) >= 9  THEN 'high'
            WHEN (probability * impact) >= 4  THEN 'medium'
            ELSE 'low'
        END
    ) STORED,

    mitigation_plan   TEXT,        -- rencana pencegahan sebelum terjadi
    contingency_plan  TEXT,        -- rencana respons jika risiko terjadi
    risk_owner_id     UUID         REFERENCES employee(id),
    financial_impact  NUMERIC(18,2) NOT NULL DEFAULT 0,  -- estimasi kerugian finansial

    status          VARCHAR(20)  NOT NULL DEFAULT 'identified'
        CHECK (status IN (
            'identified',   -- baru diidentifikasi
            'mitigating',   -- sedang dimitigasi
            'resolved',     -- risiko berhasil diatasi
            'accepted',     -- risiko diterima, tidak dimitigasi
            'occurred',     -- risiko sudah terjadi
            'closed'        -- sudah selesai
        )),

    identified_by   VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_risk_project ON project_risk(project_id, risk_level, status);
COMMENT ON TABLE project_risk IS
    'risk_score = probability × impact (1-25). '
    'risk_level: critical(≥15), high(≥9), medium(≥4), low(<4). Semua computed.';


-- ============================================================
-- 11. COMMUNICATION PLAN — matriks komunikasi & jadwal meeting
-- ============================================================

CREATE TABLE IF NOT EXISTS communication_plan (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id      UUID         NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    meeting_type    VARCHAR(30)  NOT NULL
        CHECK (meeting_type IN (
            'daily_standup',        -- 15 menit, tim internal
            'weekly_progress',      -- laporan mingguan PM + owner
            'monthly_steering',     -- steering committee + direksi
            'kickoff',              -- kick-off meeting (satu kali)
            'review_session',       -- review teknis / UAT session
            'ad_hoc'                -- insidental
        )),
    frequency       VARCHAR(20)  NOT NULL DEFAULT 'weekly'
        CHECK (frequency IN ('daily','weekly','biweekly','monthly','once','as_needed')),
    day_of_week     VARCHAR(10),   -- 'monday','tuesday', dst (untuk weekly/daily)
    day_of_month    SMALLINT,      -- 1-31 (untuk monthly)
    duration_minutes SMALLINT     NOT NULL DEFAULT 60,
    participants    TEXT,          -- deskripsi/daftar peserta (bisa JSON array)
    facilitator_id  UUID          REFERENCES employee(id),
    agenda_template TEXT,
    communication_channel VARCHAR(30) DEFAULT 'meeting'
        CHECK (communication_channel IN ('meeting','email','slack','whatsapp','report','call')),
    output_document VARCHAR(100),  -- misal: "Minutes of Meeting", "Weekly Report"
    notes           TEXT,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_comm_project ON communication_plan(project_id, meeting_type);


-- ============================================================
-- 12. PROJECT CHANGE REQUEST — kontrol scope creep
-- ============================================================

CREATE TABLE IF NOT EXISTS project_change_request (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id      UUID         NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    cr_no           VARCHAR(30)  NOT NULL UNIQUE,   -- CR/2026/06/0001
    cr_title        VARCHAR(200) NOT NULL,
    description     TEXT         NOT NULL,
    change_type     VARCHAR(20)  NOT NULL DEFAULT 'scope'
        CHECK (change_type IN ('scope','schedule','budget','resource','quality')),
    impact_scope    TEXT,
    impact_schedule_days SMALLINT NOT NULL DEFAULT 0,
    impact_budget   NUMERIC(18,2) NOT NULL DEFAULT 0,
    requested_by    VARCHAR(200),
    requested_at    DATE         NOT NULL DEFAULT CURRENT_DATE,
    status          VARCHAR(20)  NOT NULL DEFAULT 'submitted'
        CHECK (status IN ('submitted','reviewing','approved','rejected','deferred','implemented')),
    reviewed_by     VARCHAR(200),
    reviewed_at     TIMESTAMPTZ,
    notes           TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);


-- ============================================================
-- 13. PROJECT TIMESHEET LINK — lihat project_timesheet di schema_costing
--     (sudah ada, tidak perlu duplikasi)
-- ============================================================


-- ============================================================
-- 14. VIEWS
-- ============================================================

-- Ringkasan progres proyek
-- DROP dulu (bukan cuma OR REPLACE) karena kolom cost_center_id/cost_center_code
-- disisipkan di tengah daftar SELECT — Postgres menolak OR REPLACE yang mengubah
-- urutan/nama kolom existing view, hanya boleh menambah kolom di paling akhir.
DROP VIEW IF EXISTS vw_project_summary;
CREATE VIEW vw_project_summary AS
SELECT
    p.id,
    p.entity_id,
    p.project_code,
    p.project_name,
    p.industry_type,
    p.priority,
    p.charter_status,
    p.start_date,
    p.end_date,
    p.budget_amount,
    p.cost_center_id,
    cc.cc_code                                          AS cost_center_code,
    cc.cc_name                                          AS cost_center_name,
    pm_emp.full_name                                    AS project_manager,
    sp_emp.full_name                                    AS sponsor,
    COUNT(DISTINCT pt.id)                               AS total_tasks,
    COUNT(DISTINCT pt.id) FILTER (WHERE pt.status = 'completed')  AS completed_tasks,
    COUNT(DISTINCT pt.id) FILTER (WHERE pt.is_critical = TRUE)    AS critical_tasks,
    COUNT(DISTINCT pm2.id)                              AS team_size,
    COUNT(DISTINCT pms.id) FILTER (WHERE pms.status = 'pending')  AS pending_milestones,
    COUNT(DISTINCT pms.id) FILTER (WHERE pms.status = 'achieved') AS achieved_milestones,
    COALESCE(SUM(pbl.planned_amount), 0)                AS total_planned_budget,
    COALESCE(SUM(pbl.actual_amount), 0)                 AS total_actual_cost,
    CASE
        WHEN COUNT(DISTINCT pt.id) = 0 THEN 0
        ELSE ROUND(
            100.0 * COUNT(DISTINCT pt.id) FILTER (WHERE pt.status = 'completed')
            / NULLIF(COUNT(DISTINCT pt.id), 0)
        )
    END                                                 AS completion_pct
FROM project p
LEFT JOIN cost_center cc              ON cc.id  = p.cost_center_id
LEFT JOIN employee pm_emp             ON pm_emp.id = p.project_manager_id
LEFT JOIN employee sp_emp             ON sp_emp.id = p.sponsor_id
LEFT JOIN project_task pt             ON pt.project_id = p.id AND pt.status != 'cancelled'
LEFT JOIN project_team_member pm2     ON pm2.project_id = p.id
LEFT JOIN project_milestone pms       ON pms.project_id = p.id
LEFT JOIN project_budget_line pbl     ON pbl.project_id = p.id
GROUP BY p.id, cc.cc_name, pm_emp.full_name, sp_emp.full_name;


-- Gantt chart data (task + CPM)
CREATE OR REPLACE VIEW vw_gantt_chart AS
SELECT
    pt.id,
    pt.project_id,
    pt.task_code,
    pt.task_name,
    pt.wbs_item_id,
    wi.wbs_code,
    wi.wbs_name,
    e.full_name                  AS assigned_to,
    pt.planned_start,
    pt.planned_end,
    pt.duration_days,
    pt.actual_start,
    pt.actual_end,
    pt.progress_pct,
    pt.status,
    pt.early_start,
    pt.early_finish,
    pt.late_start,
    pt.late_finish,
    pt.total_float,
    pt.is_critical,
    pt.planned_cost,
    pt.actual_cost,
    -- Daftar predecessor (aggregasi)
    COALESCE(
        string_agg(
            DISTINCT td.predecessor_id::TEXT || ':' || td.dependency_type,
            ',' ORDER BY td.predecessor_id::TEXT || ':' || td.dependency_type
        ),
        ''
    )                            AS predecessors
FROM project_task pt
LEFT JOIN wbs_item wi       ON wi.id  = pt.wbs_item_id
LEFT JOIN employee e        ON e.id   = pt.assigned_to_id
LEFT JOIN task_dependency td ON td.successor_id = pt.id
WHERE pt.status != 'cancelled'
GROUP BY pt.id, wi.wbs_code, wi.wbs_name, e.full_name;


-- Risk matrix heatmap
CREATE OR REPLACE VIEW vw_risk_matrix AS
SELECT
    r.project_id,
    r.id,
    r.risk_code,
    r.risk_title,
    r.category,
    r.probability,
    r.impact,
    r.risk_score,
    r.risk_level,
    r.status,
    r.financial_impact,
    e.full_name AS risk_owner
FROM project_risk r
LEFT JOIN employee e ON e.id = r.risk_owner_id
WHERE r.status NOT IN ('resolved','closed');


-- Budget vs Actual per cost_type
CREATE OR REPLACE VIEW vw_project_budget_vs_actual AS
SELECT
    pbl.project_id,
    pbl.cost_type,
    SUM(pbl.planned_amount)     AS planned,
    SUM(pbl.actual_amount)      AS actual,
    SUM(pbl.planned_amount) - SUM(pbl.actual_amount) AS variance,
    CASE
        WHEN SUM(pbl.planned_amount) = 0 THEN NULL
        ELSE ROUND(100.0 * SUM(pbl.actual_amount) / SUM(pbl.planned_amount), 1)
    END                          AS burn_pct
FROM project_budget_line pbl
GROUP BY pbl.project_id, pbl.cost_type;


-- Resource loading — jam alokasi tim
CREATE OR REPLACE VIEW vw_resource_loading AS
SELECT
    ptm.project_id,
    ptm.employee_id,
    e.full_name,
    ptm.role_in_project,
    ptm.allocation_pct,
    p.start_date,
    p.end_date,
    COUNT(DISTINCT pt.id) FILTER (WHERE pt.assigned_to_id = ptm.employee_id) AS assigned_tasks,
    SUM(pt.planned_hours) FILTER (WHERE pt.assigned_to_id = ptm.employee_id) AS planned_hours_total,
    SUM(pt.actual_hours)  FILTER (WHERE pt.assigned_to_id = ptm.employee_id) AS actual_hours_total
FROM project_team_member ptm
JOIN project p     ON p.id   = ptm.project_id
JOIN employee e    ON e.id   = ptm.employee_id
LEFT JOIN project_task pt ON pt.project_id = ptm.project_id
GROUP BY ptm.project_id, ptm.employee_id, e.full_name, ptm.role_in_project,
         ptm.allocation_pct, p.start_date, p.end_date;


-- RACI summary per aktivitas
CREATE OR REPLACE VIEW vw_raci_matrix AS
SELECT
    r.project_id,
    r.activity_name,
    r.wbs_item_id,
    wi.wbs_code,
    MAX(e.full_name) FILTER (WHERE r.raci_role = 'A') AS accountable,
    string_agg(DISTINCT e.full_name, ', ') FILTER (WHERE r.raci_role = 'R') AS responsible,
    string_agg(DISTINCT e.full_name, ', ') FILTER (WHERE r.raci_role = 'C') AS consulted,
    string_agg(DISTINCT e.full_name, ', ') FILTER (WHERE r.raci_role = 'I') AS informed
FROM raci_entry r
JOIN employee e   ON e.id  = r.employee_id
LEFT JOIN wbs_item wi ON wi.id = r.wbs_item_id
GROUP BY r.project_id, r.activity_name, r.wbs_item_id, wi.wbs_code;


-- Cost center budget vs realisasi (dari GL journal)
CREATE OR REPLACE VIEW vw_cost_center_performance AS
SELECT
    cc.entity_id,
    cc.id          AS cost_center_id,
    cc.cc_code,
    cc.cc_name,
    cc.cc_type,
    ccb.fiscal_year,
    COALESCE(ccb.budget_amount, 0) AS approved_budget,
    -- Realisasi dari GL (akun expense yang terkait CC)
    COALESCE((
        SELECT SUM(gl.debit_idr - gl.credit_idr)
        FROM gl_line gl
        JOIN gl_journal j ON j.id = gl.journal_id AND j.status = 'posted'
        JOIN chart_of_accounts coa ON coa.id = gl.account_id
            AND coa.account_type = 'expense'
        WHERE gl.cost_center = cc.cc_code
          AND EXTRACT(YEAR FROM j.journal_date) = ccb.fiscal_year
    ), 0)          AS actual_expense,
    COALESCE(ccb.budget_amount, 0) -
    COALESCE((
        SELECT SUM(gl.debit_idr - gl.credit_idr)
        FROM gl_line gl
        JOIN gl_journal j ON j.id = gl.journal_id AND j.status = 'posted'
        JOIN chart_of_accounts coa ON coa.id = gl.account_id
            AND coa.account_type = 'expense'
        WHERE gl.cost_center = cc.cc_code
          AND EXTRACT(YEAR FROM j.journal_date) = ccb.fiscal_year
    ), 0)          AS remaining_budget
FROM cost_center cc
LEFT JOIN cost_center_budget ccb ON ccb.cost_center_id = cc.id
WHERE cc.is_active = TRUE;


SELECT 'Migration schema_project_setup selesai' AS status;
