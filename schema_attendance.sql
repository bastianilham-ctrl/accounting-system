-- ============================================================
-- SCHEMA: Time & Attendance Module
-- Jalankan: psql -U postgres -d accounting_db -f schema_attendance.sql
-- Dependensi: schema_journal_engine.sql, schema_employee.sql
-- ============================================================

-- ============================================================
-- 1. WORK SCHEDULE — master shift / jadwal kerja
-- ============================================================

CREATE TABLE IF NOT EXISTS work_schedule (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id           UUID         NOT NULL REFERENCES entity(id),
    schedule_name       VARCHAR(100) NOT NULL,         -- "Shift Pagi", "Office Hours", "Flex"
    schedule_type       VARCHAR(20)  NOT NULL DEFAULT 'fixed'
        CHECK (schedule_type IN ('fixed','flexible','shift')),
    work_days           SMALLINT[]   NOT NULL,          -- [1,2,3,4,5] = Senin-Jumat (ISO: 1=Mon)
    work_days_per_week  SMALLINT     NOT NULL DEFAULT 5 CHECK (work_days_per_week IN (5,6)),
    start_time          TIME         NOT NULL,
    end_time            TIME         NOT NULL,
    break_minutes       SMALLINT     NOT NULL DEFAULT 60,
    late_tolerance_min  SMALLINT     NOT NULL DEFAULT 15, -- toleransi terlambat (menit)
    early_leave_min     SMALLINT     NOT NULL DEFAULT 15, -- toleransi pulang cepat
    work_hours_per_day  NUMERIC(4,2) GENERATED ALWAYS AS (
        EXTRACT(EPOCH FROM (end_time - start_time)) / 3600.0 - break_minutes / 60.0
    ) STORED,
    is_active           BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE work_schedule IS 'Master jadwal kerja/shift — menjadi acuan perhitungan jam hadir dan lembur.';

-- Assign jadwal ke karyawan (bisa berubah seiring waktu)
CREATE TABLE IF NOT EXISTS employee_schedule (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    employee_id     UUID NOT NULL REFERENCES employee(id) ON DELETE CASCADE,
    schedule_id     UUID NOT NULL REFERENCES work_schedule(id),
    effective_date  DATE NOT NULL,
    end_date        DATE,                       -- NULL = berlaku sampai diganti
    created_by      VARCHAR(100),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (employee_id, effective_date)
);


-- ============================================================
-- 2. ATTENDANCE PERIOD — cut-off absensi bulanan
-- ============================================================

CREATE TABLE IF NOT EXISTS attendance_period (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    year            SMALLINT     NOT NULL,
    month           SMALLINT     NOT NULL CHECK (month BETWEEN 1 AND 12),
    cutoff_date     DATE         NOT NULL,    -- tgl cut-off absensi (misal tgl 20)
    is_frozen       BOOLEAN      NOT NULL DEFAULT FALSE,
    frozen_by       VARCHAR(100),
    frozen_at       TIMESTAMPTZ,
    processed_at    TIMESTAMPTZ,             -- kapan timesheet di-generate
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (entity_id, year, month)
);
COMMENT ON TABLE attendance_period IS 'Periode cut-off absensi. Setelah di-freeze, data tidak bisa diubah.';


-- ============================================================
-- 3. ATTENDANCE LOG — raw data dari mesin absensi
-- ============================================================

CREATE TYPE log_type AS ENUM ('IN','OUT');

CREATE TABLE IF NOT EXISTS attendance_log (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    employee_id     UUID         NOT NULL REFERENCES employee(id),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    log_timestamp   TIMESTAMPTZ  NOT NULL,
    log_type        log_type     NOT NULL,   -- IN / OUT
    device_id       VARCHAR(50),             -- ID mesin fingerprint/face
    device_name     VARCHAR(100),
    location        VARCHAR(200),            -- GPS atau nama lokasi
    latitude        NUMERIC(10,7),
    longitude       NUMERIC(10,7),
    source          VARCHAR(20)  NOT NULL DEFAULT 'device'
        CHECK (source IN ('device','mobile','manual','api')),
    is_processed    BOOLEAN      NOT NULL DEFAULT FALSE,
    raw_data        JSONB,
    imported_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE attendance_log IS 'Raw log absensi dari mesin biometrik atau mobile app.';

CREATE INDEX IF NOT EXISTS idx_attlog_emp_ts    ON attendance_log(employee_id, log_timestamp);
CREATE INDEX IF NOT EXISTS idx_attlog_processed ON attendance_log(is_processed, entity_id);


-- ============================================================
-- 4. LEAVE TYPE — master jenis cuti
-- ============================================================

CREATE TABLE IF NOT EXISTS leave_type (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    leave_code      VARCHAR(20)  NOT NULL,   -- CUTI_TAHUNAN, SAKIT, DINAS, ALPHA, IZIN
    leave_name      VARCHAR(100) NOT NULL,
    is_paid         BOOLEAN      NOT NULL DEFAULT TRUE,   -- FALSE = alpha = potong gaji
    requires_doc    BOOLEAN      NOT NULL DEFAULT FALSE,  -- surat dokter, dll
    max_days_year   SMALLINT,                -- NULL = tidak dibatasi
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    UNIQUE (entity_id, leave_code)
);
COMMENT ON TABLE leave_type IS 'Master jenis cuti/izin/alpha — is_paid=FALSE akan trigger potongan gaji.';

-- Insert default leave types (idempotent via ON CONFLICT DO NOTHING)
-- Caller harus insert dengan entity_id yang sesuai setelah setup


-- ============================================================
-- 5. LEAVE REQUEST — permohonan cuti/izin
-- ============================================================

CREATE TYPE leave_status AS ENUM ('draft','submitted','approved','rejected','cancelled');

CREATE TABLE IF NOT EXISTS leave_request (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    employee_id     UUID         NOT NULL REFERENCES employee(id),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    leave_type_id   UUID         NOT NULL REFERENCES leave_type(id),
    start_date      DATE         NOT NULL,
    end_date        DATE         NOT NULL,
    total_days      SMALLINT     NOT NULL,
    reason          TEXT,
    doc_path        TEXT,                    -- bukti dokumen (surat dokter, dll)
    status          leave_status NOT NULL DEFAULT 'draft',
    submitted_by    VARCHAR(100),
    submitted_at    TIMESTAMPTZ,
    approved_by     VARCHAR(100),
    approved_at     TIMESTAMPTZ,
    rejection_reason TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_leave_dates CHECK (end_date >= start_date)
);
COMMENT ON TABLE leave_request IS 'Permohonan cuti/izin — hanya leave_request approved yang dikecualikan dari potongan alpha.';

CREATE INDEX IF NOT EXISTS idx_leave_emp_date  ON leave_request(employee_id, start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_leave_status    ON leave_request(status, entity_id);


-- ============================================================
-- 6. OVERTIME REQUEST — Surat Perintah Lembur (SPL)
-- ============================================================

CREATE TYPE ot_status AS ENUM ('draft','submitted','approved','rejected','completed');
CREATE TYPE day_type  AS ENUM ('workday','restday','national_holiday');

CREATE TABLE IF NOT EXISTS overtime_request (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    employee_id         UUID         NOT NULL REFERENCES employee(id),
    entity_id           UUID         NOT NULL REFERENCES entity(id),
    ot_date             DATE         NOT NULL,
    day_type            day_type     NOT NULL DEFAULT 'workday',
    estimated_hours     NUMERIC(4,2) NOT NULL,
    actual_hours        NUMERIC(4,2),        -- diisi saat completed
    reason              TEXT,
    status              ot_status    NOT NULL DEFAULT 'draft',
    submitted_by        VARCHAR(100),
    submitted_at        TIMESTAMPTZ,
    approved_by         VARCHAR(100),
    approved_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (employee_id, ot_date)
);
COMMENT ON TABLE overtime_request IS
    'Lembur hanya dihitung jika ada SPL approved yang cocok. Actual hours diisi setelah selesai.';

CREATE INDEX IF NOT EXISTS idx_ot_emp_date  ON overtime_request(employee_id, ot_date);
CREATE INDEX IF NOT EXISTS idx_ot_status    ON overtime_request(status, entity_id);


-- ============================================================
-- 7. ATTENDANCE DAILY — hasil pemrosesan per hari per karyawan
-- ============================================================

CREATE TYPE daily_status AS ENUM (
    'hadir',         -- hadir normal
    'hadir_terlambat', -- hadir tapi terlambat
    'lembur',        -- ada lembur di hari ini
    'cuti',          -- approved leave (paid)
    'sakit',         -- approved sick leave
    'dinas_luar',    -- off-site work
    'alpha',         -- tidak hadir tanpa keterangan (unpaid)
    'libur',         -- hari libur nasional atau mingguan
    'wfh',           -- work from home
    'holiday'        -- company holiday
);

CREATE TABLE IF NOT EXISTS attendance_daily (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    employee_id         UUID         NOT NULL REFERENCES employee(id),
    entity_id           UUID         NOT NULL REFERENCES entity(id),
    attendance_date     DATE         NOT NULL,

    -- Clock in/out setelah proses
    clock_in            TIMESTAMPTZ,
    clock_out           TIMESTAMPTZ,
    work_hours          NUMERIC(5,2) DEFAULT 0,    -- jam kerja efektif

    -- Toleransi & deviasi
    late_minutes        SMALLINT     DEFAULT 0,
    early_leave_minutes SMALLINT     DEFAULT 0,

    -- Lembur (hanya jika ada SPL approved)
    overtime_hours      NUMERIC(4,2) DEFAULT 0,
    overtime_request_id UUID REFERENCES overtime_request(id),
    ot_day_type         day_type     DEFAULT 'workday',

    -- Status hari ini
    daily_status        daily_status NOT NULL DEFAULT 'hadir',
    leave_request_id    UUID REFERENCES leave_request(id),

    -- Flag untuk payroll
    is_paid             BOOLEAN      NOT NULL DEFAULT TRUE,  -- FALSE = alpha
    deduction_amount    NUMERIC(18,2) DEFAULT 0,  -- potongan alpha (diisi saat run payroll)

    source_log_ids      UUID[],       -- referensi ke attendance_log yang diproses
    is_manual           BOOLEAN      NOT NULL DEFAULT FALSE,
    manual_reason       TEXT,
    processed_at        TIMESTAMPTZ,

    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (employee_id, attendance_date)
);
COMMENT ON TABLE attendance_daily IS
    'Satu record per karyawan per hari — hasil pemrosesan raw log vs schedule.';

CREATE INDEX IF NOT EXISTS idx_attdaily_emp_date  ON attendance_daily(employee_id, attendance_date);
CREATE INDEX IF NOT EXISTS idx_attdaily_entity    ON attendance_daily(entity_id, attendance_date);
CREATE INDEX IF NOT EXISTS idx_attdaily_status    ON attendance_daily(daily_status, attendance_date);


-- ============================================================
-- 8. ATTENDANCE TIMESHEET — ringkasan bulanan siap payroll
-- ============================================================

CREATE TABLE IF NOT EXISTS attendance_timesheet (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    employee_id         UUID         NOT NULL REFERENCES employee(id),
    entity_id           UUID         NOT NULL REFERENCES entity(id),
    period_id           UUID         NOT NULL REFERENCES attendance_period(id),
    year                SMALLINT     NOT NULL,
    month               SMALLINT     NOT NULL,

    -- Hari kerja
    scheduled_days      SMALLINT     NOT NULL DEFAULT 0,  -- hari kerja seharusnya
    present_days        SMALLINT     NOT NULL DEFAULT 0,  -- hadir actual
    alpha_days          SMALLINT     NOT NULL DEFAULT 0,  -- alpha (unpaid)
    leave_days          SMALLINT     NOT NULL DEFAULT 0,  -- cuti (paid)
    sick_days           SMALLINT     NOT NULL DEFAULT 0,  -- sakit

    -- Jam & lembur
    total_work_hours    NUMERIC(6,2) NOT NULL DEFAULT 0,
    total_late_minutes  SMALLINT     NOT NULL DEFAULT 0,
    overtime_workday_h  NUMERIC(5,2) NOT NULL DEFAULT 0,  -- lembur hari kerja
    overtime_restday_h  NUMERIC(5,2) NOT NULL DEFAULT 0,  -- lembur hari libur

    -- Status
    is_frozen           BOOLEAN      NOT NULL DEFAULT FALSE,
    frozen_at           TIMESTAMPTZ,
    payroll_run         BOOLEAN      NOT NULL DEFAULT FALSE,  -- sudah di-run payroll
    payroll_run_at      TIMESTAMPTZ,

    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (employee_id, year, month)
);
COMMENT ON TABLE attendance_timesheet IS
    'Ringkasan bulanan absensi per karyawan — dikonsumsi oleh payroll engine saat run payroll.';

CREATE INDEX IF NOT EXISTS idx_ts_period  ON attendance_timesheet(period_id, is_frozen);
CREATE INDEX IF NOT EXISTS idx_ts_emp_yr  ON attendance_timesheet(employee_id, year, month);


-- ============================================================
-- 9. PAYROLL RUN LOG — log eksekusi payroll + jurnal
-- ============================================================

CREATE TABLE IF NOT EXISTS payroll_run_log (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    year            SMALLINT     NOT NULL,
    month           SMALLINT     NOT NULL,
    run_by          VARCHAR(100) NOT NULL,
    run_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    employees_count SMALLINT     NOT NULL DEFAULT 0,
    total_bruto     NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_pph21     NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_bpjs      NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_net       NUMERIC(18,2) NOT NULL DEFAULT 0,
    journal_id      UUID         REFERENCES gl_journal(id),
    status          VARCHAR(20)  NOT NULL DEFAULT 'success'
        CHECK (status IN ('success','partial','failed')),
    notes           TEXT
);
COMMENT ON TABLE payroll_run_log IS 'Log setiap eksekusi run payroll dan jurnal yang diposting ke GL.';


-- ============================================================
-- 10. VIEWS
-- ============================================================

CREATE OR REPLACE VIEW vw_attendance_monthly AS
SELECT
    at.employee_id,
    at.entity_id,
    at.year,
    at.month,
    e.employee_no,
    e.full_name,
    e.department,
    at.scheduled_days,
    at.present_days,
    at.alpha_days,
    at.leave_days,
    at.sick_days,
    at.total_work_hours,
    at.total_late_minutes,
    at.overtime_workday_h,
    at.overtime_restday_h,
    at.is_frozen,
    at.payroll_run
FROM attendance_timesheet at
JOIN employee e ON e.id = at.employee_id;


CREATE OR REPLACE VIEW vw_overtime_summary AS
SELECT
    ot.employee_id,
    ot.entity_id,
    ot.ot_date,
    e.full_name,
    e.department,
    ot.day_type,
    ot.estimated_hours,
    ot.actual_hours,
    ot.status,
    ot.approved_by
FROM overtime_request ot
JOIN employee e ON e.id = ot.employee_id
WHERE ot.status = 'approved';


CREATE OR REPLACE VIEW vw_leave_balance AS
SELECT
    lr.employee_id,
    e.full_name,
    lt.leave_code,
    lt.leave_name,
    lt.max_days_year,
    EXTRACT(YEAR FROM lr.start_date)::int AS tahun,
    SUM(lr.total_days) FILTER (WHERE lr.status = 'approved') AS dipakai,
    COALESCE(lt.max_days_year, 0) - COALESCE(SUM(lr.total_days) FILTER (WHERE lr.status = 'approved'), 0) AS sisa
FROM leave_request lr
JOIN employee e  ON e.id  = lr.employee_id
JOIN leave_type lt ON lt.id = lr.leave_type_id
GROUP BY lr.employee_id, e.full_name, lt.leave_code, lt.leave_name, lt.max_days_year,
         EXTRACT(YEAR FROM lr.start_date);


SELECT 'Migration schema_attendance selesai' AS status;
