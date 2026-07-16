-- ============================================================
-- MIGRATION: Vendor Tax Profile
-- Tambah field status pajak vendor ke tabel vendor
-- Jalankan di psql: psql -U postgres -d accounting_db -f vendor_tax_migration.sql
-- ============================================================

-- 1. Tambah kolom baru ke tabel vendor
ALTER TABLE vendor
    ADD COLUMN IF NOT EXISTS vendor_category      VARCHAR(20) DEFAULT 'PT'
        CHECK (vendor_category IN ('PT', 'CV', 'Firma', 'Perorangan', 'UMKM', 'Asing', 'Lainnya')),
    ADD COLUMN IF NOT EXISTS has_skb              BOOLEAN     NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS skb_number           VARCHAR(50),
    ADD COLUMN IF NOT EXISTS skb_expiry           DATE,
    ADD COLUMN IF NOT EXISTS skb_doc_path         TEXT,
    ADD COLUMN IF NOT EXISTS umkm_cert_number     VARCHAR(50),
    ADD COLUMN IF NOT EXISTS umkm_cert_expiry     DATE,
    ADD COLUMN IF NOT EXISTS omzet_per_tahun      NUMERIC(18,2),
    ADD COLUMN IF NOT EXISTS is_pkp               BOOLEAN     NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS pph_override_type    VARCHAR(20),
    ADD COLUMN IF NOT EXISTS pph_override_rate    NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS pph_override_reason  TEXT,
    ADD COLUMN IF NOT EXISTS tax_reviewed_by      VARCHAR(100),
    ADD COLUMN IF NOT EXISTS tax_reviewed_at      TIMESTAMPTZ;

-- 2. Tabel log perubahan tax profile vendor (audit trail)
CREATE TABLE IF NOT EXISTS vendor_tax_log (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    vendor_id       UUID         NOT NULL REFERENCES vendor(id),
    changed_by      VARCHAR(100) NOT NULL,
    changed_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    field_changed   VARCHAR(50)  NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    reason          TEXT
);
COMMENT ON TABLE vendor_tax_log IS 'Audit trail setiap perubahan profil pajak vendor';

-- 3. Tabel dokumen pendukung pajak vendor (SKB, Surat UMKM, dll)
CREATE TABLE IF NOT EXISTS vendor_tax_document (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    vendor_id       UUID         NOT NULL REFERENCES vendor(id) ON DELETE CASCADE,
    doc_type        VARCHAR(30)  NOT NULL
        CHECK (doc_type IN ('SKB', 'UMKM_CERT', 'NPWP', 'NIB', 'PKP_CERT', 'OTHER')),
    doc_number      VARCHAR(100),
    doc_date        DATE,
    expiry_date     DATE,
    file_path       TEXT,
    notes           TEXT,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    uploaded_by     VARCHAR(100),
    uploaded_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE vendor_tax_document IS 'Dokumen pajak vendor: SKB, Surat UMKM, NPWP, dll';

-- 4. Update NIR VANA NET PT sebagai contoh
-- (sesuaikan setelah cek status vendor sebenarnya)
UPDATE vendor SET
    vendor_category = 'PT',
    is_pkp          = FALSE,
    has_skb         = FALSE,
    default_pph_type = 'PPh23',
    default_pph_rate = 2.00
WHERE vendor_name ILIKE '%NIR VANA NET%'
   OR vendor_name ILIKE '%NIRVANA NET%';

-- 5. View untuk cek status pajak vendor sekaligus
CREATE OR REPLACE VIEW vw_vendor_tax_status AS
SELECT
    v.id,
    v.vendor_code,
    v.vendor_name,
    v.npwp,
    v.vendor_category,
    v.tax_status,
    v.is_pkp,
    v.has_skb,
    v.skb_number,
    v.skb_expiry,
    CASE
        WHEN v.has_skb = TRUE AND (v.skb_expiry IS NULL OR v.skb_expiry >= CURRENT_DATE)
            THEN 'bebas_pph'
        WHEN v.vendor_category = 'UMKM'
            THEN 'pph_final_umkm'
        WHEN v.pph_override_type IS NOT NULL
            THEN 'override_manual'
        ELSE 'pph23_normal'
    END AS pph_treatment,
    CASE
        WHEN v.has_skb = TRUE AND (v.skb_expiry IS NULL OR v.skb_expiry >= CURRENT_DATE)
            THEN 0.00
        WHEN v.vendor_category = 'UMKM'
            THEN 0.50
        WHEN v.pph_override_rate IS NOT NULL
            THEN v.pph_override_rate
        ELSE COALESCE(v.default_pph_rate, 2.00)
    END AS effective_pph_rate,
    CASE
        WHEN v.has_skb = TRUE AND v.skb_expiry IS NOT NULL AND v.skb_expiry < CURRENT_DATE
            THEN TRUE
        ELSE FALSE
    END AS skb_expired,
    v.umkm_cert_number,
    v.umkm_cert_expiry,
    v.default_pph_type,
    v.default_pph_rate,
    v.pph_override_type,
    v.pph_override_rate,
    v.pph_override_reason,
    v.tax_reviewed_by,
    v.tax_reviewed_at
FROM vendor v;

COMMENT ON VIEW vw_vendor_tax_status IS
    'Status pajak efektif per vendor: bebas_pph / pph_final_umkm / override_manual / pph23_normal';

-- 6. Index
CREATE INDEX IF NOT EXISTS idx_vendor_category ON vendor(vendor_category);
CREATE INDEX IF NOT EXISTS idx_vendor_has_skb   ON vendor(has_skb);
CREATE INDEX IF NOT EXISTS idx_vendor_tax_doc   ON vendor_tax_document(vendor_id, doc_type, is_active);

SELECT 'Migration vendor tax profile selesai' AS status;