-- ============================================================
-- SCHEMA: Procurement Item Master & Account Determination Engine
-- Jalankan: psql -U postgres -d accounting_db -f schema_procurement_item_master.sql
-- Dependensi: schema_journal_engine.sql (entity, chart_of_accounts),
--             schema_procurement.sql (pr_item, po_item),
--             schema_budget.sql (budget_line, budget_commitment)
--
-- Filosofi (3 dimensi terpisah, supaya COA tidak meledak jumlahnya):
--   - procurement_item : identitas fisik/jasa barang (SKU) + material_group, TIDAK tahu COA/cost center
--   - account_expense_mapping : material_group -> 1 COA global (dikelola Finance)
--   - cost_center (sudah ada di pr_item/po_item/budget_line) : siapa yang belanja
-- PR/PO menjahit 3 dimensi ini per baris item secara dinamis.
--
-- Catatan desain: Item Master ini SENGAJA dipisah dari product_product/product_category
-- (schema_inventory.sql) yang berat untuk valuasi stok (FIFO/avg cost/lokasi gudang).
-- procurement_item mendukung goods/services/asset/expense (termasuk yang non-stok).
-- Kalau modul Inventory nanti dikerjakan, product_product bisa REFERENCE procurement_item.id
-- (1:1 extension) untuk barang yang stock-managed — bukan dua katalog independen.
-- ============================================================

-- ============================================================
-- 1. Material Group — pengelompokan kasar item untuk account determination
-- ============================================================

CREATE TABLE IF NOT EXISTS material_group (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    group_code      VARCHAR(30)  NOT NULL,
    group_name      VARCHAR(100) NOT NULL,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (entity_id, group_code)
);
COMMENT ON TABLE material_group IS
    'Pengelompokan kasar item (mis. ATK, Elektronik, Jasa Konsultasi) — dipakai untuk account determination, bukan untuk valuasi stok.';

-- ============================================================
-- 2. Procurement Item Master (SKU) — identitas item lintas PR/PO
-- ============================================================

CREATE TABLE IF NOT EXISTS procurement_item (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id           UUID         NOT NULL REFERENCES entity(id),
    sku_code            VARCHAR(50)  NOT NULL,
    item_name           VARCHAR(255) NOT NULL,
    item_type           VARCHAR(20)  NOT NULL DEFAULT 'expense'
        CHECK (item_type IN ('goods', 'services', 'asset', 'expense')),
    material_group_id   UUID         NOT NULL REFERENCES material_group(id),
    uom                 VARCHAR(30)  NOT NULL DEFAULT 'unit',
    is_stock_managed    BOOLEAN      NOT NULL DEFAULT FALSE,
    is_active           BOOLEAN      NOT NULL DEFAULT TRUE,
    created_by          VARCHAR(200),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (entity_id, sku_code)
);
CREATE INDEX IF NOT EXISTS idx_procurement_item_group ON procurement_item(material_group_id);
COMMENT ON TABLE procurement_item IS
    'Item master untuk PR/PO (goods/services/asset/expense). is_stock_managed=TRUE menandai item yang akan dilacak stoknya kalau modul Inventory aktif (referensi 1:1 dari product_product.item_id di masa depan, bukan katalog independen).';

-- ============================================================
-- 3. Account Determination Mapping — material_group -> 1 COA global
-- ============================================================

CREATE TABLE IF NOT EXISTS account_expense_mapping (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id           UUID         NOT NULL REFERENCES entity(id),
    material_group_id   UUID         NOT NULL REFERENCES material_group(id),
    account_code        VARCHAR(50)  NOT NULL,  -- cocok dgn chart_of_accounts.account_code (scoped per entity_id, tidak FK langsung — pola sama dgn budget_line.account_code)
    created_by          VARCHAR(200),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (entity_id, material_group_id)
);
COMMENT ON TABLE account_expense_mapping IS
    'Dikelola Finance/Accounting Manager. 1 material_group hanya boleh mapping ke 1 COA per entity, supaya account determination selalu deterministik.';

-- ============================================================
-- 4. Link item master ke baris PR/PO (opsional — manual free-text tetap didukung)
-- ============================================================

ALTER TABLE pr_item ADD COLUMN IF NOT EXISTS item_id UUID REFERENCES procurement_item(id);
ALTER TABLE po_item ADD COLUMN IF NOT EXISTS item_id UUID REFERENCES procurement_item(id);
COMMENT ON COLUMN pr_item.item_id IS
    'Kalau diisi, account_code di-derive otomatis via material_group -> account_expense_mapping (mengabaikan input manual), dan budget_line_id di-resolve otomatis dari cost_center+account_code periode aktif.';

-- ============================================================
-- 5. Budget commitment — tambah source_type 'purchase_requisition'
--    (encumbrance sekarang juga terjadi saat PR submit, bukan cuma saat PO approved)
-- ============================================================

ALTER TABLE budget_commitment DROP CONSTRAINT IF EXISTS budget_commitment_source_type_check;
ALTER TABLE budget_commitment ADD CONSTRAINT budget_commitment_source_type_check
    CHECK (source_type IN ('purchase_order', 'purchase_requisition', 'payroll', 'project', 'other'));
