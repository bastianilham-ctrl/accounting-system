-- ============================================================
-- SCHEMA: PO Bidding / Vendor Quote Comparison (tender)
-- Jalankan: psql -U postgres -d accounting_db -f schema_po_bidding.sql
-- Dependensi: schema_procurement.sql (purchase_order, vendor)
--
-- Mengubah alur PR -> PO: begitu PR final approved, sistem otomatis bikin
-- 1 record purchase_order status 'open' (TANPA vendor) sebagai wadah tender.
-- Tim procurement input quote dari beberapa vendor (lumpsum per vendor),
-- lalu pilih 1 sebagai pemenang -> PO pindah ke status 'draft' dengan
-- vendor_id & total_amount terisi dari quote terpilih, lanjut flow
-- submit/approve seperti biasa.
-- ============================================================

-- ============================================================
-- 1. Status baru 'open' di purchase_order — sebelum 'draft' di alur
-- ============================================================

ALTER TYPE po_status ADD VALUE IF NOT EXISTS 'open' BEFORE 'draft';

-- PO status 'open' belum punya vendor terpilih (masih tahap tender)
ALTER TABLE purchase_order ALTER COLUMN vendor_id DROP NOT NULL;

COMMENT ON COLUMN purchase_order.vendor_id IS
  'NULL selama PO status open (tahap tender/banding vendor) — diisi otomatis saat select-vendor dipanggil';

-- ============================================================
-- 2. Vendor quote — banding harga lumpsum per vendor untuk 1 PO open
-- ============================================================

CREATE TABLE IF NOT EXISTS po_vendor_quote (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    po_id           UUID NOT NULL REFERENCES purchase_order(id) ON DELETE CASCADE,
    vendor_id       UUID NOT NULL REFERENCES vendor(id),
    quoted_amount   NUMERIC(18,2) NOT NULL CHECK (quoted_amount >= 0),
    quote_date      DATE,
    payment_terms   VARCHAR(100),
    notes           TEXT,
    is_selected     BOOLEAN NOT NULL DEFAULT FALSE,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (po_id, vendor_id)
);

COMMENT ON TABLE po_vendor_quote IS
  'Banding harga (lumpsum) dari beberapa vendor untuk 1 PO yang masih status open (tender). 1 vendor = 1 quote (re-submit akan update via UPSERT). is_selected menandai pemenang.';

CREATE INDEX IF NOT EXISTS idx_povq_po ON po_vendor_quote(po_id);

-- ============================================================
-- 3. Fix vw_po_open — INNER JOIN vendor menyembunyikan PO status 'open'
--    (belum ada vendor terpilih, masih tahap tender)
-- ============================================================

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
LEFT JOIN vendor v ON v.id = po.vendor_id
LEFT JOIN po_item pi ON pi.po_id = po.id
WHERE po.status NOT IN ('closed', 'cancelled')
GROUP BY po.id, v.vendor_name;
