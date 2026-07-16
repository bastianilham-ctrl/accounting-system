-- ============================================================
-- SCHEMA: Procurement — Purchase Requisition (PR) & Purchase Order (PO)
-- Jalankan: psql -U postgres -d accounting_db -f schema_procurement.sql
-- Dependensi: schema_journal_engine.sql, schema_budget.sql, schema_vendor_registration.sql
-- ============================================================

-- ============================================================
-- 1. PO APPROVAL MATRIX — limit kewenangan persetujuan PO
-- ============================================================

CREATE TABLE IF NOT EXISTS po_approval_matrix (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    level           SMALLINT     NOT NULL,            -- 1 = level pertama, 2 = level kedua, dst.
    threshold_name  VARCHAR(100) NOT NULL,            -- "Purchasing Manager", "CFO"
    min_amount      NUMERIC(18,2) NOT NULL DEFAULT 0,
    max_amount      NUMERIC(18,2),                    -- NULL = tanpa batas atas
    approver_role   VARCHAR(50)  NOT NULL,            -- finance | admin (sesuai JWT role)
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (entity_id, level)
);
COMMENT ON TABLE po_approval_matrix IS
    'Matriks kewenangan approval PO berdasarkan nominal. '
    'Contoh: PO < 50jt → finance; PO > 50jt → admin (CFO/Direktur).';

-- Default matrix — caller harus insert dengan entity_id yang sesuai
-- Level 1: semua PO, approver: finance (Purchasing Manager)
-- Level 2: PO > 50jt, approver: admin (Direktur Keuangan)


-- ============================================================
-- 2. PURCHASE REQUISITION (PR) — permintaan pengadaan internal
-- ============================================================

CREATE TYPE pr_status AS ENUM (
    'draft',       -- dibuat, belum disubmit
    'submitted',   -- disubmit ke atasan
    'approved',    -- disetujui, siap dikonversi ke PO
    'rejected',    -- ditolak
    'converted',   -- sudah dikonversi ke PO
    'cancelled'    -- dibatalkan
);

CREATE TABLE IF NOT EXISTS purchase_requisition (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    req_no          VARCHAR(30)  NOT NULL UNIQUE,  -- PR/2026/06/0001
    department      VARCHAR(100),
    cost_center     VARCHAR(100),                  -- untuk budget check
    requested_by    VARCHAR(200) NOT NULL,
    required_date   DATE,
    purpose         TEXT,

    -- Budget check hasil
    budget_check_status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (budget_check_status IN ('pending', 'ok', 'warning', 'blocked')),
    budget_available    NUMERIC(18,2),
    budget_total        NUMERIC(18,2),

    -- Status workflow
    status          pr_status    NOT NULL DEFAULT 'draft',
    submitted_by    VARCHAR(200),
    submitted_at    TIMESTAMPTZ,
    approved_by     VARCHAR(200),
    approved_at     TIMESTAMPTZ,
    rejected_by     VARCHAR(200),
    rejection_reason TEXT,

    -- Konversi ke PO
    converted_to_po UUID,          -- diisi saat convert_to_po()
    converted_at    TIMESTAMPTZ,
    converted_by    VARCHAR(200),

    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE purchase_requisition IS
    'Dokumen permintaan pengadaan internal. Status "approved" diperlukan sebelum bisa dikonversi ke PO.';

CREATE INDEX IF NOT EXISTS idx_pr_entity     ON purchase_requisition(entity_id, status);
CREATE INDEX IF NOT EXISTS idx_pr_dept       ON purchase_requisition(department, cost_center);


-- ============================================================
-- 3. PR ITEM — rincian barang/jasa yang diminta
-- ============================================================

CREATE TABLE IF NOT EXISTS pr_item (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pr_id           UUID         NOT NULL REFERENCES purchase_requisition(id) ON DELETE CASCADE,
    item_no         SMALLINT     NOT NULL,
    description     TEXT         NOT NULL,
    category        VARCHAR(30)  NOT NULL DEFAULT 'services'
        CHECK (category IN ('goods', 'services', 'asset')),
    unit            VARCHAR(30),
    qty             NUMERIC(10,4) NOT NULL DEFAULT 1   CHECK (qty > 0),
    unit_price      NUMERIC(18,2) NOT NULL DEFAULT 0   CHECK (unit_price >= 0),
    total_amount    NUMERIC(18,2) GENERATED ALWAYS AS (qty * unit_price) STORED,
    account_code    VARCHAR(50),   -- GL account beban
    budget_line_id  UUID         REFERENCES budget_line(id),  -- referensi nomor budget — wajib diisi sebelum PR disubmit
    notes           TEXT,
    UNIQUE (pr_id, item_no)
);
COMMENT ON TABLE pr_item IS
    'Rincian item PR. total_amount = qty × unit_price (auto-computed). '
    'budget_line_id wajib terisi sebelum PR bisa disubmit (lihat submit_pr di procurement_router.py).';


-- ============================================================
-- 4. PR APPROVAL — audit trail persetujuan PR
-- ============================================================

CREATE TABLE IF NOT EXISTS pr_approval (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pr_id       UUID         NOT NULL REFERENCES purchase_requisition(id) ON DELETE CASCADE,
    level       SMALLINT     NOT NULL,
    approver    VARCHAR(200) NOT NULL,
    action      VARCHAR(20)  NOT NULL CHECK (action IN ('approved', 'rejected', 'returned')),
    notes       TEXT,
    acted_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);


-- ============================================================
-- 5. PURCHASE ORDER (PO) — komitmen pengadaan ke vendor
-- ============================================================

CREATE TYPE po_status AS ENUM (
    'draft',            -- dibuat dari PR atau langsung
    'submitted',        -- disubmit untuk approval
    'approved',         -- disetujui, encumbrance dibuat
    'sent',             -- PDF dikirim ke vendor
    'partial_received', -- sebagian barang/jasa sudah diterima
    'received',         -- seluruh item diterima
    'closed',           -- invoice sudah match & dibayar
    'cancelled'         -- dibatalkan (commitment dilepas)
);

CREATE TABLE IF NOT EXISTS purchase_order (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    po_no           VARCHAR(30)  NOT NULL UNIQUE,  -- PO/2026/06/0001
    pr_id           UUID         REFERENCES purchase_requisition(id),
    vendor_id       UUID         NOT NULL REFERENCES vendor(id),

    po_date         DATE         NOT NULL,
    required_date   DATE,
    delivery_address TEXT,
    payment_terms   VARCHAR(100),                   -- "Net 30", "COD", dll.
    currency        VARCHAR(3)   NOT NULL DEFAULT 'IDR',

    -- Nilai
    subtotal        NUMERIC(18,2) NOT NULL DEFAULT 0,
    tax_amount      NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_amount    NUMERIC(18,2) NOT NULL DEFAULT 0,

    -- Status
    status          po_status    NOT NULL DEFAULT 'draft',

    -- Commitment (encumbrance)
    commitment_id   UUID         REFERENCES budget_commitment(id),

    -- Approval
    submitted_by    VARCHAR(200),
    submitted_at    TIMESTAMPTZ,
    approved_by     VARCHAR(200),
    approved_at     TIMESTAMPTZ,
    rejected_by     VARCHAR(200),
    rejection_reason TEXT,

    -- Pengiriman
    sent_at         TIMESTAMPTZ,
    sent_to_email   VARCHAR(200),

    notes           TEXT,
    internal_notes  TEXT,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE purchase_order IS
    'PO adalah komitmen hukum dengan vendor. Setelah approved, budget_commitment dibuat otomatis.';

CREATE INDEX IF NOT EXISTS idx_po_entity     ON purchase_order(entity_id, status);
CREATE INDEX IF NOT EXISTS idx_po_vendor     ON purchase_order(vendor_id);
CREATE INDEX IF NOT EXISTS idx_po_pr         ON purchase_order(pr_id);


-- ============================================================
-- 6. PO ITEM — rincian barang/jasa dalam PO
-- ============================================================

CREATE TABLE IF NOT EXISTS po_item (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    po_id           UUID         NOT NULL REFERENCES purchase_order(id) ON DELETE CASCADE,
    pr_item_id      UUID         REFERENCES pr_item(id),   -- NULL jika PO langsung (tanpa PR)
    item_no         SMALLINT     NOT NULL,
    description     TEXT         NOT NULL,
    category        VARCHAR(30)  NOT NULL DEFAULT 'services'
        CHECK (category IN ('goods', 'services', 'asset')),
    unit            VARCHAR(30),
    qty             NUMERIC(10,4) NOT NULL CHECK (qty > 0),
    unit_price      NUMERIC(18,2) NOT NULL CHECK (unit_price >= 0),
    total_amount    NUMERIC(18,2) GENERATED ALWAYS AS (qty * unit_price) STORED,
    account_code    VARCHAR(50),
    cost_center     VARCHAR(100),

    -- Penerimaan
    received_qty    NUMERIC(10,4) NOT NULL DEFAULT 0,
    invoiced_qty    NUMERIC(10,4) NOT NULL DEFAULT 0,

    UNIQUE (po_id, item_no)
);
COMMENT ON TABLE po_item IS
    'Item PO — bisa berasal dari pr_item (konversi) atau input manual.';

CREATE INDEX IF NOT EXISTS idx_poitem_po ON po_item(po_id);
CREATE INDEX IF NOT EXISTS idx_poitem_cc ON po_item(cost_center, account_code);


-- ============================================================
-- 7. PO APPROVAL — audit trail persetujuan PO
-- ============================================================

CREATE TABLE IF NOT EXISTS po_approval (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    po_id           UUID         NOT NULL REFERENCES purchase_order(id) ON DELETE CASCADE,
    level           SMALLINT     NOT NULL,
    required_role   VARCHAR(50),   -- role yang dibutuhkan untuk level ini
    approver        VARCHAR(200)  NOT NULL,
    action          VARCHAR(20)   NOT NULL CHECK (action IN ('approved', 'rejected', 'returned')),
    notes           TEXT,
    acted_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);


-- ============================================================
-- 8. PO RECEIPT — penerimaan barang/jasa (Goods Receipt)
-- ============================================================

CREATE TABLE IF NOT EXISTS po_receipt (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    po_id           UUID         NOT NULL REFERENCES purchase_order(id),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    receipt_no      VARCHAR(30)  NOT NULL UNIQUE,   -- GR/2026/06/0001
    receipt_date    DATE         NOT NULL,
    received_by     VARCHAR(200),
    notes           TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS po_receipt_item (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    receipt_id      UUID         NOT NULL REFERENCES po_receipt(id) ON DELETE CASCADE,
    po_item_id      UUID         NOT NULL REFERENCES po_item(id),
    received_qty    NUMERIC(10,4) NOT NULL CHECK (received_qty > 0),
    notes           TEXT
);
COMMENT ON TABLE po_receipt IS 'Goods Receipt — pencatatan penerimaan item PO. Update po_item.received_qty.';


-- ============================================================
-- 9. VIEWS
-- ============================================================

-- Status PR open (belum converted)
CREATE OR REPLACE VIEW vw_pr_open AS
SELECT
    pr.id, pr.req_no, pr.entity_id, pr.department, pr.cost_center,
    pr.requested_by, pr.required_date, pr.purpose,
    pr.budget_check_status, pr.budget_available,
    pr.status, pr.submitted_at, pr.approved_at,
    COUNT(pi.id)                AS item_count,
    COALESCE(SUM(pi.total_amount), 0) AS total_estimated
FROM purchase_requisition pr
LEFT JOIN pr_item pi ON pi.pr_id = pr.id
WHERE pr.status NOT IN ('converted', 'cancelled', 'rejected')
GROUP BY pr.id;

-- Open PO (tidak closed/cancelled) + ringkasan penerimaan
CREATE OR REPLACE VIEW vw_po_open AS
SELECT
    po.id, po.po_no, po.entity_id, po.pr_id,
    po.vendor_id, v.vendor_name,
    po.po_date, po.required_date,
    po.total_amount, po.status, po.approved_at, po.sent_at,
    COUNT(pi.id)                           AS item_count,
    COALESCE(SUM(pi.total_amount),    0)   AS po_value,
    COALESCE(SUM(pi.received_qty * pi.unit_price), 0) AS received_value,
    COALESCE(SUM(pi.invoiced_qty  * pi.unit_price), 0) AS invoiced_value,
    COALESCE(SUM(pi.total_amount), 0)
        - COALESCE(SUM(pi.invoiced_qty * pi.unit_price), 0) AS outstanding_value
FROM purchase_order po
JOIN vendor v ON v.id = po.vendor_id
LEFT JOIN po_item pi ON pi.po_id = po.id
WHERE po.status NOT IN ('closed', 'cancelled')
GROUP BY po.id, v.vendor_name;

-- Traceability PR → PO
CREATE OR REPLACE VIEW vw_pr_po_traceability AS
SELECT
    pr.req_no, pr.department, pr.cost_center, pr.requested_by,
    pr.status AS pr_status, pr.approved_at AS pr_approved_at,
    po.po_no, po.vendor_id, v.vendor_name,
    po.total_amount AS po_value, po.status AS po_status,
    po.sent_at
FROM purchase_requisition pr
LEFT JOIN purchase_order po ON po.pr_id = pr.id
LEFT JOIN vendor v ON v.id = po.vendor_id;


SELECT 'Migration schema_procurement selesai' AS status;
