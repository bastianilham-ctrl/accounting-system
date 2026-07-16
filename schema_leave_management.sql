-- ============================================================
-- SCHEMA: Leave Management
--
-- Konsep:
--   Karyawan memiliki jatah cuti per tahun (entitlement).
--   Pengajuan cuti (request) → approval manager → balance berkurang.
--   Cuti tidak dibayar (unpaid) → flag ke payroll untuk potongan gaji.
--   Carry-forward sisa cuti tahun lalu (opsional, configurable per leave type).
--
-- Alur:
--   LeaveType → LeaveEntitlement (per karyawan per tahun) →
--   LeaveRequest (Draft → Submitted → Approved/Rejected → Cancelled)
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_leave_management.sql
-- Dependensi: schema_hr.sql (employee)
-- ============================================================

-- ── 1. LEAVE TYPE ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS leave_type (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id           UUID         NOT NULL REFERENCES entity(id),
    type_code           VARCHAR(20)  NOT NULL,
    type_name           VARCHAR(200) NOT NULL,
    leave_category      VARCHAR(30)  NOT NULL DEFAULT 'annual'
        CHECK (leave_category IN (
            'annual',           -- cuti tahunan (wajib UU)
            'sick',             -- sakit (dengan surat dokter)
            'maternity',        -- melahirkan (90 hari)
            'paternity',        -- suami istri melahirkan
            'bereavement',      -- kematian keluarga
            'marriage',         -- menikah (3 hari UU)
            'emergency',        -- darurat keluarga
            'unpaid',           -- cuti tanpa bayaran (LWOP)
            'compensatory',     -- cuti pengganti lembur
            'study',            -- izin belajar/ujian
            'other'
        )),
    is_paid             BOOLEAN NOT NULL DEFAULT TRUE,
    default_days_per_year NUMERIC(5,1) NOT NULL DEFAULT 12,
    max_carry_forward   NUMERIC(5,1) NOT NULL DEFAULT 0,  -- 0 = tidak bisa carry
    max_consecutive_days SMALLINT,                         -- NULL = tidak ada batas
    requires_document   BOOLEAN NOT NULL DEFAULT FALSE,    -- wajib lampirkan surat
    notice_days_required SMALLINT NOT NULL DEFAULT 1,      -- minimal H-N pengajuan
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (entity_id, type_code)
);

-- Seed default leave types
INSERT INTO leave_type (entity_id, type_code, type_name, leave_category, is_paid,
                         default_days_per_year, max_carry_forward, requires_document)
SELECT e.id, 'ANNUAL',      'Cuti Tahunan',           'annual',      TRUE,  12, 5,  FALSE FROM entity e
WHERE NOT EXISTS (SELECT 1 FROM leave_type lt WHERE lt.entity_id=e.id AND lt.type_code='ANNUAL')
UNION ALL
SELECT e.id, 'SICK',        'Cuti Sakit',             'sick',        TRUE,  0,  0,  TRUE  FROM entity e
WHERE NOT EXISTS (SELECT 1 FROM leave_type lt WHERE lt.entity_id=e.id AND lt.type_code='SICK')
UNION ALL
SELECT e.id, 'MATERNITY',   'Cuti Melahirkan',        'maternity',   TRUE,  90, 0,  TRUE  FROM entity e
WHERE NOT EXISTS (SELECT 1 FROM leave_type lt WHERE lt.entity_id=e.id AND lt.type_code='MATERNITY')
UNION ALL
SELECT e.id, 'PATERNITY',   'Cuti Ayah',              'paternity',   TRUE,  2,  0,  TRUE  FROM entity e
WHERE NOT EXISTS (SELECT 1 FROM leave_type lt WHERE lt.entity_id=e.id AND lt.type_code='PATERNITY')
UNION ALL
SELECT e.id, 'BEREAVEMENT', 'Cuti Duka',              'bereavement', TRUE,  3,  0,  FALSE FROM entity e
WHERE NOT EXISTS (SELECT 1 FROM leave_type lt WHERE lt.entity_id=e.id AND lt.type_code='BEREAVEMENT')
UNION ALL
SELECT e.id, 'UNPAID',      'Cuti Tanpa Bayaran',     'unpaid',      FALSE, 0,  0,  FALSE FROM entity e
WHERE NOT EXISTS (SELECT 1 FROM leave_type lt WHERE lt.entity_id=e.id AND lt.type_code='UNPAID')
UNION ALL
SELECT e.id, 'COMP',        'Cuti Pengganti (Kompensasi)', 'compensatory', TRUE, 0, 30, FALSE FROM entity e
WHERE NOT EXISTS (SELECT 1 FROM leave_type lt WHERE lt.entity_id=e.id AND lt.type_code='COMP');


-- ── 2. LEAVE ENTITLEMENT (jatah per karyawan per tahun) ──────────────────────
CREATE TABLE IF NOT EXISTS leave_entitlement (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    employee_id     UUID         NOT NULL REFERENCES employee(id),
    leave_type_id   UUID         NOT NULL REFERENCES leave_type(id),
    fiscal_year     SMALLINT     NOT NULL,
    entitled_days   NUMERIC(5,1) NOT NULL,              -- jatah awal
    carry_forward   NUMERIC(5,1) NOT NULL DEFAULT 0,    -- sisa dari tahun lalu
    total_entitled  NUMERIC(5,1) GENERATED ALWAYS AS (entitled_days + carry_forward) STORED,
    used_days       NUMERIC(5,1) NOT NULL DEFAULT 0,
    pending_days    NUMERIC(5,1) NOT NULL DEFAULT 0,    -- diajukan, belum approved
    balance         NUMERIC(5,1) GENERATED ALWAYS AS
                        (entitled_days + carry_forward - used_days) STORED,
    notes           TEXT,
    UNIQUE (employee_id, leave_type_id, fiscal_year)
);
CREATE INDEX IF NOT EXISTS idx_lent_employee ON leave_entitlement(employee_id, fiscal_year);


-- ── 3. LEAVE REQUEST ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS leave_request (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    request_no      VARCHAR(50)  NOT NULL UNIQUE,
    employee_id     UUID         NOT NULL REFERENCES employee(id),
    leave_type_id   UUID         NOT NULL REFERENCES leave_type(id),
    date_from       DATE         NOT NULL,
    date_to         DATE         NOT NULL,
    total_days      NUMERIC(5,1) NOT NULL,               -- hari kerja (tidak hitung weekend/libur)
    reason          TEXT,
    document_url    TEXT,                                 -- surat dokter / surat lainnya
    status          VARCHAR(20)  NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','submitted','approved','rejected','cancelled')),
    -- Approval
    submitted_by    VARCHAR(200),
    submitted_at    TIMESTAMPTZ,
    approved_by     VARCHAR(200),
    approved_at     TIMESTAMPTZ,
    approval_notes  TEXT,
    -- Payroll impact
    is_unpaid_deduction BOOLEAN NOT NULL DEFAULT FALSE,  -- TRUE jika LWOP yang perlu potong gaji
    payroll_period_id   UUID,                            -- link ke payroll jika sudah diproses
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lreq_employee ON leave_request(employee_id, status);
CREATE INDEX IF NOT EXISTS idx_lreq_date     ON leave_request(entity_id, date_from, date_to);


-- ── 4. PUBLIC HOLIDAY ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public_holiday (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id   UUID         NOT NULL REFERENCES entity(id),
    holiday_date DATE        NOT NULL,
    description VARCHAR(200) NOT NULL,
    UNIQUE (entity_id, holiday_date)
);


-- ── 5. LEAVE BALANCE ADJUSTMENT (manual koreksi) ─────────────────────────────
CREATE TABLE IF NOT EXISTS leave_balance_adjustment (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    employee_id     UUID         NOT NULL REFERENCES employee(id),
    leave_type_id   UUID         NOT NULL REFERENCES leave_type(id),
    fiscal_year     SMALLINT     NOT NULL,
    adjustment_days NUMERIC(5,1) NOT NULL,    -- positif = tambah, negatif = kurang
    reason          TEXT         NOT NULL,
    adjusted_by     VARCHAR(200),
    adjusted_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);


-- ── 6. VIEWS ─────────────────────────────────────────────────────────────────

-- Saldo cuti per karyawan
CREATE OR REPLACE VIEW vw_leave_balance AS
SELECT
    le.id,
    le.entity_id,
    le.employee_id,
    e.employee_code,
    e.full_name         AS employee_name,
    e.department,
    lt.type_code,
    lt.type_name,
    lt.leave_category,
    lt.is_paid,
    le.fiscal_year,
    le.entitled_days,
    le.carry_forward,
    le.total_entitled,
    le.used_days,
    le.pending_days,
    le.balance
FROM leave_entitlement le
JOIN employee   e  ON e.id  = le.employee_id
JOIN leave_type lt ON lt.id = le.leave_type_id;


-- Pengajuan cuti aktif (submitted atau approved)
CREATE OR REPLACE VIEW vw_pending_leave_requests AS
SELECT
    lr.id AS request_id,
    lr.entity_id,
    lr.request_no,
    e.employee_code,
    e.full_name         AS employee_name,
    e.department,
    lt.type_name,
    lt.leave_category,
    lr.date_from,
    lr.date_to,
    lr.total_days,
    lr.reason,
    lr.status,
    lr.submitted_at,
    lr.is_unpaid_deduction
FROM leave_request lr
JOIN employee   e  ON e.id  = lr.employee_id
JOIN leave_type lt ON lt.id = lr.leave_type_id
WHERE lr.status IN ('submitted','approved');


-- Ringkasan cuti karyawan per tahun (untuk HR dashboard)
CREATE OR REPLACE VIEW vw_leave_summary_by_employee AS
SELECT
    le.entity_id,
    le.employee_id,
    e.full_name         AS employee_name,
    e.department,
    le.fiscal_year,
    SUM(le.entitled_days)   AS total_entitled,
    SUM(le.used_days)       AS total_used,
    SUM(le.balance)         AS total_balance,
    SUM(le.carry_forward)   AS total_carry_forward,
    COUNT(DISTINCT le.leave_type_id) AS leave_types_count
FROM leave_entitlement le
JOIN employee e ON e.id = le.employee_id
GROUP BY le.entity_id, le.employee_id, e.full_name, e.department, le.fiscal_year;


-- Jadwal cuti (calendar view) — untuk deteksi overlap
CREATE OR REPLACE VIEW vw_leave_calendar AS
SELECT
    lr.entity_id,
    lr.employee_id,
    e.full_name       AS employee_name,
    e.department,
    lt.type_name,
    lr.date_from,
    lr.date_to,
    lr.total_days,
    lr.status
FROM leave_request lr
JOIN employee   e  ON e.id  = lr.employee_id
JOIN leave_type lt ON lt.id = lr.leave_type_id
WHERE lr.status IN ('approved', 'submitted');


SELECT 'Migration schema_leave_management selesai' AS status;
