-- ============================================================
-- SCHEMA: File Attachment Management
--
-- Konsep Polymorphic Linking:
--   Satu file (attachment) bisa di-link ke banyak entity
--   (mis. PDF kontrak di-link ke contract + ar_invoice)
--
-- ref_type: ar_invoice | ap_invoice | expense_claim | contract |
--           project | vendor | employee | journal | quotation |
--           sales_order | bank_statement | wht_transaction |
--           purchase_order | delivery_order | leave_request |
--           asset | payroll | other
--
-- Deduplication: SHA-256 hash — file identik hanya disimpan sekali
-- Soft delete: is_deleted flag; file fisik dihapus terpisah
--
-- Storage path: uploads/attachments/{entity_id[:8]}/{YYYY-MM}/{uuid}_{filename}
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_attachment.sql
-- ============================================================

-- ── 1. ATTACHMENT (file metadata) ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS attachment (
    id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    original_name   VARCHAR(500) NOT NULL,                  -- nama file asli dari user
    stored_name     VARCHAR(500) NOT NULL,                  -- nama file di disk (uuid-based)
    file_path       VARCHAR(1000) NOT NULL,                 -- relative path dari upload root
    file_size       BIGINT       NOT NULL,                  -- bytes
    mime_type       VARCHAR(200),
    file_extension  VARCHAR(20),
    sha256_hash     CHAR(64)     NOT NULL,                  -- untuk deduplication
    category        VARCHAR(50)  NOT NULL DEFAULT 'other'
        CHECK (category IN (
            'receipt','invoice','contract','report','po','do',
            'identity','photo','spreadsheet','presentation','other'
        )),
    description     TEXT,
    is_deleted      BOOLEAN      NOT NULL DEFAULT FALSE,
    deleted_by      VARCHAR(200),
    deleted_at      TIMESTAMPTZ,
    uploaded_by     VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (entity_id, sha256_hash)  -- dedup per entity
);
CREATE INDEX IF NOT EXISTS idx_attachment_entity   ON attachment(entity_id, is_deleted);
CREATE INDEX IF NOT EXISTS idx_attachment_hash     ON attachment(sha256_hash);
CREATE INDEX IF NOT EXISTS idx_attachment_created  ON attachment(entity_id, created_at DESC);


-- ── 2. ATTACHMENT LINK — polymorphic relation ─────────────────────────────────
CREATE TABLE IF NOT EXISTS attachment_link (
    id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    attachment_id   UUID         NOT NULL REFERENCES attachment(id) ON DELETE CASCADE,
    ref_type        VARCHAR(50)  NOT NULL
        CHECK (ref_type IN (
            'ar_invoice','ap_invoice','expense_claim','contract','project',
            'vendor','employee','journal','quotation','sales_order',
            'bank_statement','wht_transaction','purchase_order',
            'delivery_order','leave_request','asset','payroll','other'
        )),
    ref_id          UUID         NOT NULL,
    notes           TEXT,                                   -- catatan konteks attachment ini
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (attachment_id, ref_type, ref_id)
);
CREATE INDEX IF NOT EXISTS idx_attlink_ref   ON attachment_link(ref_type, ref_id);
CREATE INDEX IF NOT EXISTS idx_attlink_att   ON attachment_link(attachment_id);


-- ── 3. VIEWS ─────────────────────────────────────────────────────────────────

-- Daftar attachment dengan jumlah link
CREATE OR REPLACE VIEW vw_attachment_list AS
SELECT
    a.id,
    a.entity_id,
    a.original_name,
    a.file_size,
    a.mime_type,
    a.file_extension,
    a.category,
    a.description,
    a.sha256_hash,
    a.is_deleted,
    a.uploaded_by,
    a.created_at,
    COUNT(al.id)  AS link_count,
    ARRAY_AGG(al.ref_type || ':' || al.ref_id::text)
        FILTER (WHERE al.id IS NOT NULL) AS linked_to
FROM attachment a
LEFT JOIN attachment_link al ON al.attachment_id = a.id
GROUP BY a.id;


-- Attachment per entity reference (most common query)
CREATE OR REPLACE VIEW vw_entity_attachments AS
SELECT
    al.ref_type,
    al.ref_id,
    al.notes            AS link_notes,
    al.created_by       AS linked_by,
    al.created_at       AS linked_at,
    a.id                AS attachment_id,
    a.entity_id,
    a.original_name,
    a.file_size,
    a.mime_type,
    a.file_extension,
    a.category,
    a.description,
    a.file_path,
    a.uploaded_by,
    a.created_at        AS uploaded_at,
    a.is_deleted
FROM attachment_link al
JOIN attachment a ON a.id = al.attachment_id AND a.is_deleted = FALSE;


-- Summary: jumlah & ukuran file per entity
CREATE OR REPLACE VIEW vw_attachment_storage_summary AS
SELECT
    entity_id,
    COUNT(*)                                        AS total_files,
    COUNT(*) FILTER (WHERE is_deleted = FALSE)      AS active_files,
    COALESCE(SUM(file_size) FILTER (WHERE is_deleted = FALSE), 0) AS total_size_bytes,
    ROUND(
        COALESCE(SUM(file_size) FILTER (WHERE is_deleted = FALSE), 0)::numeric / 1048576,
        2
    )                                               AS total_size_mb,
    MAX(created_at)                                 AS last_upload_at
FROM attachment
GROUP BY entity_id;


SELECT 'Migration schema_attachment selesai' AS status;
