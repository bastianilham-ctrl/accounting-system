-- ============================================================
-- SCHEMA: Employee Master & Payroll
-- Jalankan: psql -U postgres -d accounting_db -f schema_employee.sql
-- Dependensi: schema_journal_engine.sql (entity harus ada)
-- ============================================================

-- ============================================================
-- 1. EMPLOYEE MASTER
-- ============================================================

CREATE TABLE IF NOT EXISTS employee (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id           UUID         NOT NULL REFERENCES entity(id),
    employee_no         VARCHAR(30)  NOT NULL,        -- NIK Karyawan internal (auto: EMP-00001)
    full_name           VARCHAR(300) NOT NULL,
    nickname            VARCHAR(100),

    -- Identitas
    nik_ktp             VARCHAR(20),                   -- NIK KTP 16 digit
    npwp                VARCHAR(30),                   -- NPWP 15 digit
    place_of_birth      VARCHAR(100),
    date_of_birth       DATE,
    gender              CHAR(1) CHECK (gender IN ('M','F')),
    religion            VARCHAR(20),                   -- Islam, Kristen, Katolik, Hindu, Budha, Konghucu
    marital_status      VARCHAR(10) CHECK (marital_status IN ('single','married','divorced')),
    nationality         CHAR(2) DEFAULT 'ID',

    -- Alamat
    address             TEXT,
    city                VARCHAR(100),
    province            VARCHAR(100),
    postal_code         VARCHAR(10),
    phone               VARCHAR(30),
    email               VARCHAR(200),
    emergency_contact   VARCHAR(200),

    -- Data kepegawaian
    department          VARCHAR(100),
    position            VARCHAR(200),                  -- jabatan
    employment_type     VARCHAR(20) DEFAULT 'permanent'
        CHECK (employment_type IN ('permanent','contract','internship','outsource')),
    join_date           DATE,
    resign_date         DATE,
    status              VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','inactive','resigned','terminated')),

    -- Data pajak — menentukan TER category
    ptkp_status         VARCHAR(10) DEFAULT 'TK/0'
        CHECK (ptkp_status IN ('TK/0','TK/1','TK/2','TK/3','K/0','K/1','K/2','K/3')),
    ter_category        CHAR(1) GENERATED ALWAYS AS (
        CASE ptkp_status
            WHEN 'TK/0' THEN 'A' WHEN 'TK/1' THEN 'A' WHEN 'K/0'  THEN 'A'
            WHEN 'TK/2' THEN 'B' WHEN 'TK/3' THEN 'B' WHEN 'K/1'  THEN 'B' WHEN 'K/2' THEN 'B'
            WHEN 'K/3'  THEN 'C'
            ELSE 'A'
        END
    ) STORED,
    number_of_dependents SMALLINT DEFAULT 0 CHECK (number_of_dependents BETWEEN 0 AND 3),

    -- Rekening gaji
    bank_name           VARCHAR(100),
    bank_account_no     VARCHAR(50),
    bank_account_name   VARCHAR(300),

    -- BPJS
    bpjs_kesehatan_no   VARCHAR(30),
    bpjs_tk_no          VARCHAR(30),                   -- Tenaga Kerja

    -- Registration link
    registration_id     UUID,                          -- diisi dari employee_registration
    registration_status VARCHAR(30) DEFAULT 'active'
        CHECK (registration_status IN ('draft','submitted','hr_review','approved','active','inactive')),

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (entity_id, employee_no)
);
COMMENT ON TABLE employee IS 'Master data karyawan aktif, termasuk data PTKP untuk kalkulasi PPh 21 TER.';

CREATE INDEX IF NOT EXISTS idx_emp_entity   ON employee(entity_id, status);
CREATE INDEX IF NOT EXISTS idx_emp_npwp     ON employee(npwp);
CREATE INDEX IF NOT EXISTS idx_emp_ptkp     ON employee(ptkp_status, ter_category);


-- ============================================================
-- 2. KOMPONEN GAJI — salary component master per karyawan
-- ============================================================

CREATE TABLE IF NOT EXISTS employee_payroll_component (
    id                          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    employee_id                 UUID NOT NULL REFERENCES employee(id) ON DELETE CASCADE,
    effective_date              DATE NOT NULL,
    is_active                   BOOLEAN NOT NULL DEFAULT TRUE,

    -- Komponen tetap (masuk bruto TER)
    gaji_pokok                  NUMERIC(18,2) NOT NULL DEFAULT 0,
    tunjangan_transport         NUMERIC(18,2) NOT NULL DEFAULT 0,
    tunjangan_makan             NUMERIC(18,2) NOT NULL DEFAULT 0,
    tunjangan_lain              NUMERIC(18,2) NOT NULL DEFAULT 0,   -- tunjangan jabatan, dll.

    -- BPJS dibayar perusahaan (masuk bruto TER)
    premi_bpjs_kesehatan_perusahaan NUMERIC(18,2) NOT NULL DEFAULT 0,  -- 4% gaji pokok
    premi_bpjs_jkk              NUMERIC(18,2) NOT NULL DEFAULT 0,   -- 0.24%–1.74% sesuai risiko
    premi_bpjs_jkm              NUMERIC(18,2) NOT NULL DEFAULT 0,   -- 0.3% gaji pokok

    -- Iuran karyawan (TIDAK masuk bruto TER, dipakai saat rekonsiliasi Desember)
    iuran_jht_karyawan_pct      NUMERIC(5,4) NOT NULL DEFAULT 0.02, -- 2% gaji pokok
    iuran_pensiun_karyawan      NUMERIC(18,2) NOT NULL DEFAULT 0,   -- flat atau % sesuai PKP

    -- Catatan
    notes                       TEXT,
    created_by                  VARCHAR(100),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE employee_payroll_component IS
    'Komponen gaji tetap per karyawan. Buat record baru saat ada kenaikan gaji (history tracking).';

CREATE INDEX IF NOT EXISTS idx_epc_employee ON employee_payroll_component(employee_id, is_active);


-- ============================================================
-- 3. RIWAYAT PAYROLL BULANAN
-- ============================================================

CREATE TABLE IF NOT EXISTS employee_payroll_history (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    employee_id         UUID         NOT NULL REFERENCES employee(id),
    entity_id           UUID         NOT NULL REFERENCES entity(id),
    year                SMALLINT     NOT NULL,
    month               SMALLINT     NOT NULL CHECK (month BETWEEN 1 AND 12),

    -- Penghasilan
    bruto_bulanan       NUMERIC(18,2) NOT NULL DEFAULT 0,
    bonus_thr           NUMERIC(18,2) NOT NULL DEFAULT 0,
    lembur              NUMERIC(18,2) NOT NULL DEFAULT 0,

    -- Iuran karyawan bulan ini (untuk rekonsiliasi Desember)
    iuran_jht_karyawan  NUMERIC(18,2) NOT NULL DEFAULT 0,
    iuran_pensiun_karyawan NUMERIC(18,2) NOT NULL DEFAULT 0,

    -- Hasil PPh 21
    ter_rate_pct        NUMERIC(5,2),                   -- tarif TER (%), null untuk Desember
    pph21_amount        NUMERIC(18,2) NOT NULL DEFAULT 0,

    -- Untuk rekonsiliasi Desember
    pph_terutang_setahun NUMERIC(18,2),
    pph_ter_jan_nov      NUMERIC(18,2),
    pkp_setahun          NUMERIC(18,2),

    method              VARCHAR(30) DEFAULT 'TER',      -- 'TER' | 'Desember_Rekonsiliasi'
    notes               TEXT,

    -- Jurnal akuntansi
    journal_id          UUID REFERENCES gl_journal(id),
    is_posted           BOOLEAN NOT NULL DEFAULT FALSE,

    created_by          VARCHAR(100),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ,

    UNIQUE (employee_id, year, month)
);
COMMENT ON TABLE employee_payroll_history IS
    'Rekord PPh 21 bulanan per karyawan — TER Jan-Nov, rekonsiliasi Desember.';

CREATE INDEX IF NOT EXISTS idx_eph_employee_year ON employee_payroll_history(employee_id, year);
CREATE INDEX IF NOT EXISTS idx_eph_entity_year   ON employee_payroll_history(entity_id, year, month);
CREATE INDEX IF NOT EXISTS idx_eph_posted        ON employee_payroll_history(is_posted);


-- ============================================================
-- 4. EMPLOYEE REGISTRATION — onboarding workflow
-- ============================================================

CREATE TABLE IF NOT EXISTS employee_registration (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id           UUID         NOT NULL REFERENCES entity(id),
    registration_no     VARCHAR(30)  NOT NULL UNIQUE,  -- ER/2026/06/0001

    status              VARCHAR(30)  NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','submitted','hr_review','approved','active','rejected')),

    -- Data pribadi
    full_name           VARCHAR(300) NOT NULL,
    nickname            VARCHAR(100),
    nik_ktp             VARCHAR(20),
    npwp                VARCHAR(30),
    place_of_birth      VARCHAR(100),
    date_of_birth       DATE,
    gender              CHAR(1) CHECK (gender IN ('M','F')),
    marital_status      VARCHAR(10),
    nationality         CHAR(2) DEFAULT 'ID',
    phone               VARCHAR(30),
    email               VARCHAR(200) NOT NULL,
    address             TEXT,

    -- Kepegawaian
    department          VARCHAR(100),
    position            VARCHAR(200),
    employment_type     VARCHAR(20) DEFAULT 'permanent',
    join_date           DATE,

    -- Pajak
    ptkp_status         VARCHAR(10) DEFAULT 'TK/0',
    number_of_dependents SMALLINT DEFAULT 0,

    -- Rekening gaji
    bank_name           VARCHAR(100),
    bank_account_no     VARCHAR(50),
    bank_account_name   VARCHAR(300),

    -- BPJS
    bpjs_kesehatan_no   VARCHAR(30),
    bpjs_tk_no          VARCHAR(30),

    -- Checklist dokumen
    doc_ktp_uploaded    BOOLEAN NOT NULL DEFAULT FALSE,
    doc_npwp_uploaded   BOOLEAN NOT NULL DEFAULT FALSE,
    doc_ijazah_uploaded BOOLEAN NOT NULL DEFAULT FALSE,
    doc_cv_uploaded     BOOLEAN NOT NULL DEFAULT FALSE,

    -- Komponen gaji yang diajukan
    gaji_pokok_proposed NUMERIC(18,2),
    tunjangan_proposed  NUMERIC(18,2),

    -- Workflow
    submitted_by        VARCHAR(100),
    submitted_at        TIMESTAMPTZ,
    reviewed_by         VARCHAR(100),
    reviewed_at         TIMESTAMPTZ,
    approved_by         VARCHAR(100),
    approved_at         TIMESTAMPTZ,
    rejection_reason    TEXT,

    -- Link ke employee aktif
    employee_id         UUID REFERENCES employee(id),

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE employee_registration IS
    'Onboarding karyawan baru: draft → submitted → hr_review → approved → active (employee record dibuat).';

CREATE INDEX IF NOT EXISTS idx_ereg_entity  ON employee_registration(entity_id, status);


-- ============================================================
-- 5. PTKP CHANGE HISTORY — riwayat perubahan status PTKP
-- ============================================================

CREATE TABLE IF NOT EXISTS employee_ptkp_history (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    employee_id     UUID         NOT NULL REFERENCES employee(id),
    old_ptkp        VARCHAR(10),
    new_ptkp        VARCHAR(10),
    effective_year  SMALLINT     NOT NULL,   -- berlaku mulai tahun ini
    reason          TEXT,                    -- misal: menikah, punya anak
    changed_by      VARCHAR(100),
    changed_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE employee_ptkp_history IS
    'Riwayat perubahan status PTKP karyawan — penting untuk SPT tahunan PPh 21.';


-- ============================================================
-- 6. VIEWS
-- ============================================================

CREATE OR REPLACE VIEW vw_employee_payroll_summary AS
SELECT
    e.id            AS employee_id,
    e.entity_id,
    e.employee_no,
    e.full_name,
    e.npwp,
    e.ptkp_status,
    e.ter_category,
    e.department,
    e.position,
    e.status,
    -- Komponen gaji terkini
    pc.gaji_pokok,
    pc.tunjangan_transport,
    pc.tunjangan_makan,
    pc.tunjangan_lain,
    pc.premi_bpjs_kesehatan_perusahaan,
    (
        pc.gaji_pokok + pc.tunjangan_transport + pc.tunjangan_makan + pc.tunjangan_lain
        + pc.premi_bpjs_kesehatan_perusahaan + pc.premi_bpjs_jkk + pc.premi_bpjs_jkm
    ) AS bruto_bulanan_normal
FROM employee e
LEFT JOIN employee_payroll_component pc ON pc.employee_id = e.id AND pc.is_active = TRUE;

COMMENT ON VIEW vw_employee_payroll_summary IS
    'Ringkasan karyawan aktif + komponen gaji standar (tanpa lembur/bonus).';


CREATE OR REPLACE VIEW vw_payroll_pph21_ytd AS
SELECT
    eph.employee_id,
    eph.entity_id,
    eph.year,
    e.employee_no,
    e.full_name,
    e.ptkp_status,
    COUNT(*)                  AS bulan_processed,
    SUM(eph.bruto_bulanan)    AS total_bruto,
    SUM(eph.pph21_amount)     AS total_pph21,
    MAX(eph.pph_terutang_setahun) AS pph_terutang_setahun,
    MAX(eph.pkp_setahun)      AS pkp_setahun
FROM employee_payroll_history eph
JOIN employee e ON e.id = eph.employee_id
GROUP BY eph.employee_id, eph.entity_id, eph.year, e.employee_no, e.full_name, e.ptkp_status;

COMMENT ON VIEW vw_payroll_pph21_ytd IS
    'YTD PPh 21 per karyawan per tahun — untuk rekap bupot 1721-A1.';


SELECT 'Migration schema_employee selesai' AS status;
