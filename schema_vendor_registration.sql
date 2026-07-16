-- ============================================================
-- MIGRATION: Vendor Registration & Approval Workflow
-- Jalankan: psql -U postgres -d accounting_db -f schema_vendor_registration.sql
-- Dependensi: schema_journal_engine.sql (tabel vendor, entity harus sudah ada)
-- ============================================================

-- ============================================================
-- 1. EXTEND vendor TABLE — ERP General + Accounting + Purchasing Data
-- ============================================================

ALTER TABLE vendor
    -- General Data
    ADD COLUMN IF NOT EXISTS legal_name             VARCHAR(300),     -- nama sesuai akta/NPWP
    ADD COLUMN IF NOT EXISTS trading_name           VARCHAR(300),     -- nama dagang
    ADD COLUMN IF NOT EXISTS legal_entity_type      VARCHAR(20)
        CHECK (legal_entity_type IN ('PT','CV','Firma','Perorangan','UMKM','Koperasi','Yayasan','Asing','Lainnya')),
    ADD COLUMN IF NOT EXISTS head_office_address    TEXT,
    ADD COLUMN IF NOT EXISTS warehouse_address      TEXT,
    ADD COLUMN IF NOT EXISTS gps_lat                NUMERIC(10,7),
    ADD COLUMN IF NOT EXISTS gps_lon                NUMERIC(10,7),
    ADD COLUMN IF NOT EXISTS contact_person         VARCHAR(200),
    ADD COLUMN IF NOT EXISTS contact_title          VARCHAR(100),
    ADD COLUMN IF NOT EXISTS contact_phone          VARCHAR(50),
    ADD COLUMN IF NOT EXISTS contact_email          VARCHAR(200),
    -- Legalitas
    ADD COLUMN IF NOT EXISTS nib_expiry             DATE,
    ADD COLUMN IF NOT EXISTS nib_doc_path           TEXT,
    ADD COLUMN IF NOT EXISTS nppkp                  VARCHAR(30),      -- Nomor Pengukuhan PKP
    ADD COLUMN IF NOT EXISTS is_pkp                 BOOLEAN NOT NULL DEFAULT FALSE,
    -- Accounting Data (COA mapping)
    ADD COLUMN IF NOT EXISTS ap_control_account     VARCHAR(20) DEFAULT '2-1-001',
    ADD COLUMN IF NOT EXISTS advance_payment_account VARCHAR(20) DEFAULT '1-3-001',
    -- Purchasing Data
    ADD COLUMN IF NOT EXISTS payment_terms          VARCHAR(20) DEFAULT 'NET30',
        -- NET30, NET45, NET60, COD, PREPAID, CIA
    ADD COLUMN IF NOT EXISTS incoterms              VARCHAR(10),      -- FOB, CIF, EXW, DDP, DAP
    ADD COLUMN IF NOT EXISTS order_currency         CHAR(3) DEFAULT 'IDR',
    ADD COLUMN IF NOT EXISTS min_order_value        NUMERIC(18,2),
    ADD COLUMN IF NOT EXISTS lead_time_days         SMALLINT,
    ADD COLUMN IF NOT EXISTS vendor_rating          NUMERIC(3,1),     -- 0.0–10.0, auto-updated
    -- Registration status
    ADD COLUMN IF NOT EXISTS registration_status    VARCHAR(30) DEFAULT 'active'
        CHECK (registration_status IN ('draft','submitted','internal_review',
               'banking_validation','approval_l1','approval_l2','active','rejected','suspended')),
    ADD COLUMN IF NOT EXISTS is_bank_locked         BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS activated_at           TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS activated_by           VARCHAR(100);


-- ============================================================
-- 2. vendor_bank_account — rekening dengan mekanisme locking
-- ============================================================

CREATE TABLE IF NOT EXISTS vendor_bank_account (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    vendor_id           UUID         REFERENCES vendor(id) ON DELETE CASCADE,
    registration_id     UUID,        -- diisi saat registration, nullable setelah aktivasi

    -- Bank details
    bank_country        CHAR(2)      NOT NULL DEFAULT 'ID',
    bank_name           VARCHAR(200) NOT NULL,
    bank_code           VARCHAR(10),              -- kode bank BI (014=BCA, 008=Mandiri, 009=BNI, 002=BRI)
    swift_code          VARCHAR(20),              -- BIC/SWIFT untuk transfer internasional
    branch_name         VARCHAR(200),
    account_no          VARCHAR(50)  NOT NULL,
    account_holder_name VARCHAR(300) NOT NULL,    -- HARUS sama persis dengan nama legal vendor
    currency            CHAR(3)      NOT NULL DEFAULT 'IDR',

    -- Validasi rekening
    is_verified         BOOLEAN      NOT NULL DEFAULT FALSE,
    verified_by         VARCHAR(100),
    verified_at         TIMESTAMPTZ,
    verification_method VARCHAR(30),              -- 'manual', 'api_inquiry', 'dokumen'
    verification_notes  TEXT,

    -- Lock — setelah vendor aktif, rekening dikunci (tidak bisa di-edit langsung)
    is_locked           BOOLEAN      NOT NULL DEFAULT FALSE,
    locked_at           TIMESTAMPTZ,
    locked_by           VARCHAR(100),

    -- Dokumen pendukung
    bank_book_path      TEXT,        -- path buku tabungan / koran yang diupload vendor

    is_primary          BOOLEAN      NOT NULL DEFAULT TRUE,
    is_active           BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE vendor_bank_account IS
    'Rekening bank vendor — dikunci otomatis saat vendor aktif. Perubahan hanya via vendor_bank_change_request.';

CREATE INDEX IF NOT EXISTS idx_vba_vendor   ON vendor_bank_account(vendor_id);
CREATE INDEX IF NOT EXISTS idx_vba_locked   ON vendor_bank_account(is_locked, is_active);


-- ============================================================
-- 3. vendor_registration — formulir pendaftaran vendor baru
-- ============================================================

CREATE TABLE IF NOT EXISTS vendor_registration (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id           UUID         NOT NULL REFERENCES entity(id),
    registration_no     VARCHAR(30)  NOT NULL UNIQUE,  -- auto: VR/2026/06/0001

    -- Status machine (mengikuti workflow 5-step)
    status              VARCHAR(30)  NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','submitted','internal_review','banking_validation',
                          'approval_l1','approval_l2','active','rejected')),

    -- General Data (diisi vendor saat registrasi)
    legal_name          VARCHAR(300) NOT NULL,
    trading_name        VARCHAR(300),
    legal_entity_type   VARCHAR(20)
        CHECK (legal_entity_type IN ('PT','CV','Firma','Perorangan','UMKM','Koperasi','Yayasan','Asing','Lainnya')),
    head_office_address TEXT,
    warehouse_address   TEXT,
    gps_lat             NUMERIC(10,7),
    gps_lon             NUMERIC(10,7),
    contact_person      VARCHAR(200),
    contact_title       VARCHAR(100),
    contact_phone       VARCHAR(50),
    contact_email       VARCHAR(200) NOT NULL,

    -- Legalitas
    npwp                VARCHAR(30),
    nib                 VARCHAR(30),
    nib_expiry          DATE,
    nppkp               VARCHAR(30),
    is_pkp              BOOLEAN      NOT NULL DEFAULT FALSE,
    kbli                VARCHAR(10),

    -- Accounting & Tax (diisi tim Finance saat review)
    ap_control_account  VARCHAR(20)  DEFAULT '2-1-001',
    advance_payment_account VARCHAR(20) DEFAULT '1-3-001',
    vendor_category     VARCHAR(20)
        CHECK (vendor_category IN ('PT','CV','Firma','Perorangan','UMKM','Koperasi','Yayasan','Asing','Lainnya')),
    default_pph_type    VARCHAR(20),
    default_pph_rate    NUMERIC(5,2),
    is_pkp_confirmed    BOOLEAN,     -- konfirmasi dari Finance

    -- Purchasing Data (diisi tim Procurement)
    payment_terms       VARCHAR(20)  DEFAULT 'NET30',
    incoterms           VARCHAR(10),
    order_currency      CHAR(3)      DEFAULT 'IDR',
    min_order_value     NUMERIC(18,2),
    lead_time_days      SMALLINT,

    -- Checklist dokumen (wajib sebelum submit)
    doc_npwp_uploaded   BOOLEAN      NOT NULL DEFAULT FALSE,
    doc_nib_uploaded    BOOLEAN      NOT NULL DEFAULT FALSE,
    doc_bank_book_uploaded BOOLEAN   NOT NULL DEFAULT FALSE,  -- buku tabungan/koran
    doc_deed_uploaded   BOOLEAN      NOT NULL DEFAULT FALSE,  -- akta pendirian / KTP

    -- Workflow metadata
    submitted_by        VARCHAR(100),
    submitted_at        TIMESTAMPTZ,
    rejection_reason    TEXT,

    -- Link ke vendor yang sudah aktif (diisi saat aktivasi)
    vendor_id           UUID         REFERENCES vendor(id),

    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE vendor_registration IS
    'Form pendaftaran vendor baru, mengikuti workflow 5-step approval. Setelah approve L2 → vendor aktif dibuat otomatis.';

CREATE INDEX IF NOT EXISTS idx_vreg_entity ON vendor_registration(entity_id, status);
CREATE INDEX IF NOT EXISTS idx_vreg_status ON vendor_registration(status);


-- ============================================================
-- 4. vendor_registration_approval — audit trail per step
-- ============================================================

CREATE TABLE IF NOT EXISTS vendor_registration_approval (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    registration_id     UUID         NOT NULL REFERENCES vendor_registration(id) ON DELETE CASCADE,

    approval_level      VARCHAR(30)  NOT NULL
        CHECK (approval_level IN ('internal_review','banking_validation','approval_l1','approval_l2')),
    -- Level:
    --   internal_review    : Procurement & Tax — verifikasi dokumen & NPWP/NIB
    --   banking_validation : Finance — verifikasi nomor rekening (manual atau API)
    --   approval_l1        : Purchasing Manager — validasi kapabilitas vendor
    --   approval_l2        : Finance Controller — validasi rekening & perpajakan final

    status              VARCHAR(20)  NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','approved','rejected','revision_needed')),

    approved_by         VARCHAR(100),
    approved_at         TIMESTAMPTZ,
    notes               TEXT,         -- catatan reviewer

    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE vendor_registration_approval IS
    'Audit trail setiap langkah approval pendaftaran vendor.';

CREATE INDEX IF NOT EXISTS idx_vra_reg ON vendor_registration_approval(registration_id, approval_level);


-- ============================================================
-- 5. vendor_bank_change_request — perubahan rekening setelah aktif
-- ============================================================

CREATE TABLE IF NOT EXISTS vendor_bank_change_request (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    vendor_id           UUID         NOT NULL REFERENCES vendor(id),
    current_bank_id     UUID         NOT NULL REFERENCES vendor_bank_account(id),

    -- Data rekening baru yang diajukan
    new_bank_country    CHAR(2)      NOT NULL DEFAULT 'ID',
    new_bank_name       VARCHAR(200) NOT NULL,
    new_bank_code       VARCHAR(10),
    new_swift_code      VARCHAR(20),
    new_branch_name     VARCHAR(200),
    new_account_no      VARCHAR(50)  NOT NULL,
    new_account_holder  VARCHAR(300) NOT NULL,
    new_currency        CHAR(3)      NOT NULL DEFAULT 'IDR',
    new_bank_book_path  TEXT,        -- dokumen buku tabungan baru (WAJIB)

    reason              TEXT         NOT NULL,  -- alasan perubahan (wajib isi)

    status              VARCHAR(20)  NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','approved','rejected')),

    -- Workflow
    requested_by        VARCHAR(100) NOT NULL,
    requested_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    reviewed_by         VARCHAR(100),
    reviewed_at         TIMESTAMPTZ,
    review_notes        TEXT
);
COMMENT ON TABLE vendor_bank_change_request IS
    'Workflow perubahan rekening vendor yang sudah aktif. Rekening lama tetap aktif sampai request disetujui.';

CREATE INDEX IF NOT EXISTS idx_vbcr_vendor ON vendor_bank_change_request(vendor_id, status);


-- ============================================================
-- 6. VIEW — vendor_registration_summary
-- ============================================================

CREATE OR REPLACE VIEW vw_vendor_registration_summary AS
SELECT
    vr.id,
    vr.registration_no,
    vr.entity_id,
    vr.status,
    vr.legal_name,
    vr.trading_name,
    vr.legal_entity_type,
    vr.contact_email,
    vr.npwp,
    vr.nib,
    vr.submitted_at,
    vr.vendor_id,
    -- Checklist dokumen
    (vr.doc_npwp_uploaded AND vr.doc_nib_uploaded AND
     vr.doc_bank_book_uploaded AND vr.doc_deed_uploaded) AS all_docs_complete,
    -- Status approval terkini per level
    l_ir.status   AS internal_review_status,
    l_bv.status   AS banking_validation_status,
    l_l1.status   AS approval_l1_status,
    l_l2.status   AS approval_l2_status,
    -- Bank account primary
    vba.bank_name,
    vba.account_no,
    vba.account_holder_name,
    vba.is_verified AS bank_verified,
    vba.is_locked   AS bank_locked,
    vr.created_at,
    vr.updated_at
FROM vendor_registration vr
LEFT JOIN vendor_registration_approval l_ir ON l_ir.registration_id = vr.id AND l_ir.approval_level = 'internal_review'
LEFT JOIN vendor_registration_approval l_bv ON l_bv.registration_id = vr.id AND l_bv.approval_level = 'banking_validation'
LEFT JOIN vendor_registration_approval l_l1 ON l_l1.registration_id = vr.id AND l_l1.approval_level = 'approval_l1'
LEFT JOIN vendor_registration_approval l_l2 ON l_l2.registration_id = vr.id AND l_l2.approval_level = 'approval_l2'
LEFT JOIN vendor_bank_account vba ON vba.registration_id = vr.id AND vba.is_primary = TRUE;

COMMENT ON VIEW vw_vendor_registration_summary IS
    'Ringkasan pendaftaran vendor: status workflow + checklist dokumen + data rekening primary.';


-- ============================================================
-- 7. INDEXES TAMBAHAN
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_vendor_registration_status ON vendor(registration_status);
CREATE INDEX IF NOT EXISTS idx_vendor_bank_locked         ON vendor(is_bank_locked);

SELECT 'Migration vendor registration selesai' AS status;
