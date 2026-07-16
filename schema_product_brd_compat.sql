-- Compatibility layer: BRD "Global Sales Product & Inventory Master"
-- Existing rich product_product/product_category tetap dipakai (FIFO/AVCO, multi-warehouse, dst),
-- kolom-kolom ini cuma alias/penanda dengan nama persis sesuai BRD supaya modul Sales Order &
-- Invoice bisa filter/baca dengan nama field yang BRD minta, tanpa duplikasi katalog produk.

-- product_code: alias dari sku (BRD pakai nama ini), generated supaya selalu sinkron, tidak perlu sync manual
ALTER TABLE product_product ADD COLUMN IF NOT EXISTS product_code VARCHAR(100) GENERATED ALWAYS AS (sku) STORED;

-- is_stock_item: true kalau product_type='storable' (satu-satunya tipe yang ditrack stok di gudang)
ALTER TABLE product_product ADD COLUMN IF NOT EXISTS is_stock_item BOOLEAN GENERATED ALWAYS AS (product_type = 'storable') STORED;

-- is_sellable: flag baru (bukan derived) — produk raw material/komponen produksi mungkin is_active tapi tidak dijual ke customer
ALTER TABLE product_product ADD COLUMN IF NOT EXISTS is_sellable BOOLEAN NOT NULL DEFAULT TRUE;

CREATE INDEX IF NOT EXISTS idx_product_product_code ON product_product(product_code);
CREATE INDEX IF NOT EXISTS idx_product_is_sellable ON product_product(is_sellable) WHERE is_sellable = TRUE;

SELECT 'Migration schema_product_brd_compat selesai' AS status;
