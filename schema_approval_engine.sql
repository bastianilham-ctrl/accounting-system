-- ============================================================
-- SCHEMA: Generic Approval Engine (plug-and-play)
-- Jalankan: psql -U postgres -d accounting_db -f schema_approval_engine.sql
-- Dependensi: schema_journal_engine.sql (entity), schema_employee.sql (employee),
--             schema_users.sql (app_user), schema_procurement.sql (po_approval_matrix,
--             dipakai oleh strategi 'amount_matrix' untuk document_type='purchase_order')
--
-- Engine ini tidak tahu apa-apa soal modul spesifik (PR/PO/dll) — beroperasi murni
-- atas (document_type, document_id) yang didaftarkan modul pemanggil lewat
-- modules/approval_engine.py::ApprovalEngine. Untuk plug modul baru: cukup tambah
-- 1 baris approval_policy (atau biarkan fallback 'single_role'), tidak perlu ubah
-- skema atau kode engine.
-- ============================================================

-- ============================================================
-- 1. Hirarki organisasi — kolom minimal yang dibutuhkan strategi 'hierarchy'
-- ============================================================

ALTER TABLE employee ADD COLUMN IF NOT EXISTS manager_id UUID REFERENCES employee(id);
COMMENT ON COLUMN employee.manager_id IS
    'Atasan langsung (self-FK). Dipakai ApprovalEngine strategi hierarchy untuk membangun rantai approval berjenjang.';


-- ============================================================
-- 2. APPROVAL POLICY — master konfigurasi strategi approval per document_type
-- ============================================================

CREATE TABLE IF NOT EXISTS approval_policy (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         REFERENCES entity(id),   -- NULL = berlaku semua entity (default global)
    document_type   VARCHAR(50)  NOT NULL,                -- 'purchase_requisition', 'purchase_order', dst (extensible)
    strategy        VARCHAR(20)  NOT NULL DEFAULT 'single_role'
        CHECK (strategy IN ('hierarchy', 'amount_matrix', 'single_role')),
    fallback_role   VARCHAR(50)  NOT NULL DEFAULT 'finance',
    terminal_roles  VARCHAR(200) NOT NULL DEFAULT 'finance,admin,superadmin',
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (entity_id, document_type)
);
COMMENT ON TABLE approval_policy IS
    'Master: strategi approval per document_type (per entity, atau global kalau entity_id NULL). '
    'hierarchy = naik rantai manager_id; amount_matrix = baca po_approval_matrix berdasar nominal; '
    'single_role = 1 step, role minimum = fallback_role.';

INSERT INTO approval_policy (entity_id, document_type, strategy, fallback_role, terminal_roles)
VALUES
    (NULL, 'purchase_requisition', 'hierarchy',     'finance', 'finance,admin,superadmin'),
    (NULL, 'purchase_order',       'amount_matrix', 'finance', 'finance,admin,superadmin')
ON CONFLICT (entity_id, document_type) DO NOTHING;


-- ============================================================
-- 3. APPROVAL REQUEST — satu instance approval per dokumen
-- ============================================================

CREATE TABLE IF NOT EXISTS approval_request (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    document_type   VARCHAR(50)  NOT NULL,
    document_id     UUID         NOT NULL,
    document_ref    VARCHAR(50),                  -- nomor dokumen untuk display (PR/2026/.../PO/2026/...)
    document_amount NUMERIC(18,2),
    requested_by    VARCHAR(200),
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,

    UNIQUE (document_type, document_id)
);
CREATE INDEX IF NOT EXISTS idx_approval_request_doc ON approval_request(document_type, document_id);


-- ============================================================
-- 4. APPROVAL STEP — satu baris per level dalam satu request
-- ============================================================

CREATE TABLE IF NOT EXISTS approval_step (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    request_id           UUID         NOT NULL REFERENCES approval_request(id) ON DELETE CASCADE,
    level                SMALLINT     NOT NULL,
    approver_employee_id UUID         REFERENCES employee(id),  -- diisi kalau strategi hierarchy
    required_role        VARCHAR(50),                            -- diisi kalau amount_matrix / single_role / fallback
    approver_label       VARCHAR(300) NOT NULL,
    status               VARCHAR(20)  NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'skipped')),
    acted_by             VARCHAR(200),
    acted_at             TIMESTAMPTZ,
    notes                TEXT,

    UNIQUE (request_id, level)
);
CREATE INDEX IF NOT EXISTS idx_approval_step_request ON approval_step(request_id, status);

SELECT 'Migration schema_approval_engine selesai' AS status;
