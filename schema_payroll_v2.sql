-- ============================================================
-- MIGRATION: Payroll v2 — BPJS rates & tunjangan tetap
-- Jalankan: psql -U postgres -d accounting_db -f schema_payroll_v2.sql
-- Dependensi: schema_employee.sql harus sudah dijalankan
-- ============================================================

-- ============================================================
-- 1. employee_payroll_component — tambah kolom BPJS rates & tunjangan
-- ============================================================

-- Tunjangan tetap (jabatan, keluarga) — terpisah dari makan/transport
ALTER TABLE employee_payroll_component
    ADD COLUMN IF NOT EXISTS tunjangan_jabatan     NUMERIC(18,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tunjangan_keluarga    NUMERIC(18,2) NOT NULL DEFAULT 0;

-- BPJS JKK rate per risiko industri (PP 44/2015 Lampiran I)
-- Risiko sangat rendah : 0.24%   (kantor umum, jasa keuangan, software)
-- Risiko rendah        : 0.54%
-- Risiko menengah      : 0.89%
-- Risiko tinggi        : 1.27%
-- Risiko sangat tinggi : 1.74%   (pertambangan, konstruksi berat)
ALTER TABLE employee_payroll_component
    ADD COLUMN IF NOT EXISTS bpjs_jkk_rate         NUMERIC(6,4) NOT NULL DEFAULT 0.0024;

-- BPJS JP rates (bisa berbeda jika PKWT mengikuti perjanjian kerja)
ALTER TABLE employee_payroll_component
    ADD COLUMN IF NOT EXISTS bpjs_jp_employer_pct  NUMERIC(5,4) NOT NULL DEFAULT 0.02,
    ADD COLUMN IF NOT EXISTS bpjs_jp_employee_pct  NUMERIC(5,4) NOT NULL DEFAULT 0.01;

-- Tunjangan tidak tetap harian (basis tarif, dikalikan hari hadir di attendance engine)
ALTER TABLE employee_payroll_component
    ADD COLUMN IF NOT EXISTS tarif_makan_harian     NUMERIC(18,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tarif_transport_harian NUMERIC(18,2) NOT NULL DEFAULT 0;

COMMENT ON COLUMN employee_payroll_component.tunjangan_jabatan     IS 'Tunjangan jabatan tetap per bulan (masuk basis BPJS)';
COMMENT ON COLUMN employee_payroll_component.tunjangan_keluarga    IS 'Tunjangan keluarga tetap per bulan (masuk basis BPJS)';
COMMENT ON COLUMN employee_payroll_component.bpjs_jkk_rate         IS 'Tarif JKK: 0.0024–0.0174 sesuai risiko industri (PP 44/2015)';
COMMENT ON COLUMN employee_payroll_component.bpjs_jp_employer_pct  IS 'Tarif JP perusahaan, default 2% (Perpres 109/2013)';
COMMENT ON COLUMN employee_payroll_component.bpjs_jp_employee_pct  IS 'Tarif JP karyawan, default 1% (Perpres 109/2013)';
COMMENT ON COLUMN employee_payroll_component.tarif_makan_harian    IS 'Uang makan per hari hadir (dikalikan present_days di payroll engine)';
COMMENT ON COLUMN employee_payroll_component.tarif_transport_harian IS 'Uang transport per hari hadir';


-- ============================================================
-- 2. employee_payroll_history — tambah kolom BPJS & THP
-- ============================================================

ALTER TABLE employee_payroll_history
    ADD COLUMN IF NOT EXISTS tunjangan_tetap        NUMERIC(18,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tunjangan_variabel     NUMERIC(18,2) NOT NULL DEFAULT 0,

    -- BPJS perusahaan bulan ini (untuk rekap CTC)
    ADD COLUMN IF NOT EXISTS bpjs_kes_employer      NUMERIC(18,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS bpjs_jht_employer      NUMERIC(18,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS bpjs_jp_employer       NUMERIC(18,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS bpjs_jkk_employer      NUMERIC(18,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS bpjs_jkm_employer      NUMERIC(18,2) NOT NULL DEFAULT 0,

    -- BPJS karyawan bulan ini (untuk rekap slip)
    ADD COLUMN IF NOT EXISTS bpjs_kes_employee      NUMERIC(18,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS bpjs_jp_employee       NUMERIC(18,2) NOT NULL DEFAULT 0,

    -- THP dan CTC
    ADD COLUMN IF NOT EXISTS thp                    NUMERIC(18,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS ctc                    NUMERIC(18,2) NOT NULL DEFAULT 0,

    -- Potongan tambahan
    ADD COLUMN IF NOT EXISTS potongan_alpha         NUMERIC(18,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS potongan_kasbon        NUMERIC(18,2) NOT NULL DEFAULT 0;

COMMENT ON COLUMN employee_payroll_history.thp IS 'Take Home Pay = Gaji Gross − BPJS Karyawan − PPh 21 − Potongan Lain';
COMMENT ON COLUMN employee_payroll_history.ctc IS 'Cost to Company = Gaji Gross + Total BPJS Perusahaan';


-- ============================================================
-- 3. Update save_payroll_record() parameter — handled in Python
--    but we expose a view for rekap slip gaji
-- ============================================================

CREATE OR REPLACE VIEW vw_slip_gaji AS
SELECT
    eph.id,
    eph.employee_id,
    eph.entity_id,
    eph.year,
    eph.month,
    e.employee_no,
    e.full_name,
    e.npwp,
    e.ptkp_status,
    e.ter_category,
    e.department,
    e.position,
    e.bank_name,
    e.bank_account_no,
    e.bank_account_name,

    -- Pendapatan
    eph.bruto_bulanan              AS bruto_pph21,
    eph.tunjangan_tetap,
    eph.tunjangan_variabel,
    eph.lembur,
    eph.bonus_thr,

    -- BPJS Perusahaan
    eph.bpjs_kes_employer,
    eph.bpjs_jht_employer,
    eph.bpjs_jp_employer,
    eph.bpjs_jkk_employer,
    eph.bpjs_jkm_employer,
    (eph.bpjs_kes_employer + eph.bpjs_jht_employer + eph.bpjs_jp_employer
     + eph.bpjs_jkk_employer + eph.bpjs_jkm_employer) AS total_bpjs_employer,

    -- BPJS Karyawan
    eph.bpjs_kes_employee,
    eph.iuran_jht_karyawan,
    eph.bpjs_jp_employee,
    (eph.bpjs_kes_employee + eph.iuran_jht_karyawan + eph.bpjs_jp_employee) AS total_bpjs_employee,

    -- Pajak
    eph.ter_rate_pct,
    eph.pph21_amount,

    -- Potongan
    eph.potongan_alpha,
    eph.potongan_kasbon,

    -- Hasil
    eph.thp,
    eph.ctc,

    -- Audit
    eph.method,
    eph.is_posted,
    eph.journal_id,
    eph.created_at
FROM employee_payroll_history eph
JOIN employee e ON e.id = eph.employee_id;

COMMENT ON VIEW vw_slip_gaji IS 'Slip gaji lengkap per karyawan per bulan — join dengan employee master.';


-- ============================================================
-- 4. Rekap BPJS bulanan — untuk setoran ke BPJS
-- ============================================================

CREATE OR REPLACE VIEW vw_bpjs_bulanan AS
SELECT
    eph.entity_id,
    eph.year,
    eph.month,
    COUNT(*)                              AS jumlah_karyawan,
    -- Iuran Kes
    SUM(eph.bpjs_kes_employer)            AS kes_employer,
    SUM(eph.bpjs_kes_employee)            AS kes_employee,
    SUM(eph.bpjs_kes_employer + eph.bpjs_kes_employee) AS kes_total,
    -- Iuran JHT
    SUM(eph.bpjs_jht_employer)            AS jht_employer,
    SUM(eph.iuran_jht_karyawan)           AS jht_employee,
    SUM(eph.bpjs_jht_employer + eph.iuran_jht_karyawan) AS jht_total,
    -- Iuran JP
    SUM(eph.bpjs_jp_employer)             AS jp_employer,
    SUM(eph.bpjs_jp_employee)             AS jp_employee,
    SUM(eph.bpjs_jp_employer + eph.bpjs_jp_employee) AS jp_total,
    -- Iuran JKK + JKM (employer only)
    SUM(eph.bpjs_jkk_employer)            AS jkk_employer,
    SUM(eph.bpjs_jkm_employer)            AS jkm_employer,
    -- Total setoran
    SUM(eph.bpjs_kes_employer + eph.bpjs_kes_employee
      + eph.bpjs_jht_employer + eph.iuran_jht_karyawan
      + eph.bpjs_jp_employer  + eph.bpjs_jp_employee
      + eph.bpjs_jkk_employer + eph.bpjs_jkm_employer) AS grand_total_bpjs,
    -- PPh 21
    SUM(eph.pph21_amount)                 AS total_pph21,
    -- Payroll
    SUM(eph.bruto_bulanan)                AS total_bruto,
    SUM(eph.thp)                          AS total_thp,
    SUM(eph.ctc)                          AS total_ctc
FROM employee_payroll_history eph
GROUP BY eph.entity_id, eph.year, eph.month;

COMMENT ON VIEW vw_bpjs_bulanan IS 'Rekap setoran BPJS per bulan — dasar untuk upload e-Dabu dan SIPP.';


SELECT 'Migration schema_payroll_v2 selesai' AS status;
