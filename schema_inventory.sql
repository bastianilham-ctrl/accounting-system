-- ============================================================
-- SCHEMA: Inventory / Stock Management
-- Berlaku untuk: Dagang, Konstruksi, Manufaktur
--
-- Konsep Double-Entry Inventory:
--   Barang tidak pernah "hilang" — selalu berpindah dari satu lokasi ke lokasi lain.
--   Setiap pergerakan stok (stock_move) yang selesai (done) memicu jurnal GL otomatis.
--
-- Metode Valuasi:
--   average_cost (AVCO) — rata-rata bergerak (paling umum UKM)
--   fifo               — First In First Out via valuation layers
--   standard_cost      — harga standar ditetapkan manual
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_inventory.sql
-- Dependensi: schema_journal_engine.sql, schema_procurement.sql, schema_vendor_registration.sql
-- ============================================================


-- ============================================================
-- 1. UOM — Satuan Unit
-- ============================================================

CREATE TABLE IF NOT EXISTS product_uom (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    uom_code    VARCHAR(20)  NOT NULL UNIQUE,   -- PCS, KG, LITER, BOX, MTR
    uom_name    VARCHAR(50)  NOT NULL,
    uom_type    VARCHAR(20)  NOT NULL DEFAULT 'reference'
        CHECK (uom_type IN ('reference', 'bigger', 'smaller')),
    ratio       NUMERIC(14,6) NOT NULL DEFAULT 1,
        -- ratio terhadap UOM referensi untuk kategori yang sama
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE
);
INSERT INTO product_uom (id, uom_code, uom_name, uom_type, ratio) VALUES
    (uuid_generate_v4(), 'PCS',   'Pieces',       'reference', 1),
    (uuid_generate_v4(), 'BOX',   'Box',          'bigger',   12),
    (uuid_generate_v4(), 'KG',    'Kilogram',     'reference', 1),
    (uuid_generate_v4(), 'GR',    'Gram',         'smaller',   0.001),
    (uuid_generate_v4(), 'LITER', 'Liter',        'reference', 1),
    (uuid_generate_v4(), 'MTR',   'Meter',        'reference', 1),
    (uuid_generate_v4(), 'SET',   'Set',          'reference', 1),
    (uuid_generate_v4(), 'UNIT',  'Unit',         'reference', 1)
ON CONFLICT (uom_code) DO NOTHING;


-- ============================================================
-- 2. PRODUCT CATEGORY — hierarki kategori + akun GL + metode valuasi
-- ============================================================

CREATE TABLE IF NOT EXISTS product_category (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    category_code   VARCHAR(30)  NOT NULL,
    category_name   VARCHAR(100) NOT NULL,
    parent_id       UUID         REFERENCES product_category(id),

    -- Metode valuasi (dikunci setelah ada transaksi)
    cost_method     VARCHAR(20)  NOT NULL DEFAULT 'average_cost'
        CHECK (cost_method IN ('average_cost', 'fifo', 'standard_cost')),

    -- Akun GL (harus diisi sebelum ada transaksi inventory)
    inventory_account_code   VARCHAR(50),  -- Persediaan (asset)
    cogs_account_code        VARCHAR(50),  -- HPP / COGS (expense)
    grir_account_code        VARCHAR(50),  -- GR/IR clearing (interim liability)
    scrapped_account_code    VARCHAR(50),  -- Kerugian barang rusak (expense)
    adjustment_account_code  VARCHAR(50),  -- Selisih persediaan (expense/income)
    wip_account_code         VARCHAR(50),  -- Work In Progress (asset, untuk manufaktur)

    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (entity_id, category_code)
);
COMMENT ON TABLE product_category IS
    'Kategori produk sekaligus mendefinisikan metode valuasi dan akun GL. '
    'cost_method tidak boleh diubah setelah ada transaksi untuk menghindari inkonsistensi laporan pajak.';


-- ============================================================
-- 3. PRODUCT MASTER
-- ============================================================

CREATE TABLE IF NOT EXISTS product_product (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    category_id     UUID         NOT NULL REFERENCES product_category(id),
    sku             VARCHAR(100) NOT NULL,
    barcode         VARCHAR(100),
    product_name    VARCHAR(200) NOT NULL,
    description     TEXT,

    product_type    VARCHAR(20)  NOT NULL DEFAULT 'storable'
        CHECK (product_type IN (
            'storable',       -- barang fisik, ditrack di gudang
            'consumable',     -- habis pakai, tidak ditrack (selalu tersedia)
            'service'         -- jasa, tidak ada pergerakan fisik
        )),

    tracking_type   VARCHAR(20)  NOT NULL DEFAULT 'none'
        CHECK (tracking_type IN (
            'none',   -- tidak perlu lot/serial
            'lot',    -- tracking per batch/lot (makanan, farmasi, dll)
            'serial'  -- tracking per unit (elektronik, aset bernilai tinggi)
        )),

    -- UOM
    uom_id          UUID         NOT NULL REFERENCES product_uom(id),
    uom_purchase_id UUID         REFERENCES product_uom(id),  -- UOM saat beli
    uom_sales_id    UUID         REFERENCES product_uom(id),  -- UOM saat jual

    -- Harga / Biaya
    standard_price  NUMERIC(18,4) NOT NULL DEFAULT 0,    -- untuk standard_cost
    current_avg_cost NUMERIC(18,4) NOT NULL DEFAULT 0,   -- diupdate engine setiap GR (AVCO)
    sales_price     NUMERIC(18,2) NOT NULL DEFAULT 0,    -- harga jual default

    -- Reorder threshold (diisi di sini untuk referensi cepat)
    min_qty         NUMERIC(12,4) NOT NULL DEFAULT 0,    -- safety stock
    max_qty         NUMERIC(12,4) NOT NULL DEFAULT 0,    -- target max stock
    reorder_qty     NUMERIC(12,4) NOT NULL DEFAULT 0,    -- qty yang di-order saat reorder

    notes           TEXT,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (entity_id, sku)
);
CREATE INDEX IF NOT EXISTS idx_product_category ON product_product(category_id);
CREATE INDEX IF NOT EXISTS idx_product_barcode  ON product_product(barcode) WHERE barcode IS NOT NULL;
COMMENT ON TABLE product_product IS
    'Master produk. Hanya product_type=storable yang ditrack di gudang. '
    'Metode valuasi diwarisi dari product_category.cost_method.';


-- ============================================================
-- 4. INVENTORY LOCATION — lokasi fisik + virtual
-- ============================================================

CREATE TABLE IF NOT EXISTS inventory_location (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    location_code   VARCHAR(30)  NOT NULL,
    location_name   VARCHAR(100) NOT NULL,
    location_type   VARCHAR(20)  NOT NULL DEFAULT 'internal'
        CHECK (location_type IN (
            'internal',    -- gudang fisik perusahaan
            'supplier',    -- virtual: asal barang dari vendor
            'customer',    -- virtual: tujuan barang ke pelanggan
            'scrapped',    -- virtual: barang rusak/dibuang
            'production',  -- virtual: WIP / area produksi manufaktur
            'transit',     -- dalam perjalanan (inter-warehouse)
            'virtual'      -- penyesuaian / initial stock
        )),
    parent_id       UUID         REFERENCES inventory_location(id),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (entity_id, location_code)
);
CREATE INDEX IF NOT EXISTS idx_location_type ON inventory_location(entity_id, location_type);
COMMENT ON TABLE inventory_location IS
    'Lokasi gudang (fisik & virtual). Semua mutasi bergerak dari satu lokasi ke lokasi lain. '
    'Virtual locations (supplier, customer, scrapped, production) wajib ada per entity.';


-- ============================================================
-- 5. LOT / BATCH TRACKING — penelusuran per lot/kadaluarsa
-- ============================================================

CREATE TABLE IF NOT EXISTS stock_lot (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id      UUID         NOT NULL REFERENCES product_product(id),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    lot_number      VARCHAR(100) NOT NULL,
    manufacture_date DATE,
    expiry_date     DATE,         -- untuk FEFO sorting
    initial_qty     NUMERIC(12,4) NOT NULL DEFAULT 0,
    notes           TEXT,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (product_id, lot_number)
);
CREATE INDEX IF NOT EXISTS idx_lot_expiry ON stock_lot(product_id, expiry_date) WHERE expiry_date IS NOT NULL;


-- ============================================================
-- 6. STOCK MOVE — jantung sistem inventory
-- ============================================================
-- Jangan pernah memodifikasi qty langsung di product_product.
-- Selalu buat stock_move dan validate untuk mengubah stok.

CREATE TABLE IF NOT EXISTS stock_move (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id               UUID         NOT NULL REFERENCES entity(id),
    product_id              UUID         NOT NULL REFERENCES product_product(id),
    uom_id                  UUID         REFERENCES product_uom(id),
    source_location_id      UUID         NOT NULL REFERENCES inventory_location(id),
    destination_location_id UUID         NOT NULL REFERENCES inventory_location(id),

    move_type               VARCHAR(30)  NOT NULL
        CHECK (move_type IN (
            'receipt',          -- barang masuk dari vendor (GR)
            'delivery',         -- barang keluar ke pelanggan (DO)
            'transfer',         -- pindah antar gudang internal
            'adjustment_in',    -- stok opname — koreksi positif
            'adjustment_out',   -- stok opname — koreksi negatif
            'scrap',            -- barang rusak/dibuang
            'production_in',    -- hasil produksi masuk ke gudang
            'production_out'    -- bahan baku keluar ke produksi
        )),

    qty_done        NUMERIC(12,4) NOT NULL CHECK (qty_done > 0),
    unit_cost       NUMERIC(18,4) NOT NULL DEFAULT 0,    -- HPP per unit saat mutasi
    total_cost      NUMERIC(18,2) NOT NULL DEFAULT 0,    -- qty_done × unit_cost

    -- Referensi dokumen
    po_receipt_id   UUID         REFERENCES po_receipt(id),
    do_id           UUID,        -- FK ke delivery_order.id (tabel di bawah)
    adjustment_id   UUID,        -- FK ke inventory_adjustment.id
    scrap_id        UUID,        -- FK ke stock_scrap.id
    lot_id          UUID         REFERENCES stock_lot(id),
    reference_no    VARCHAR(200),

    -- Workflow
    status          VARCHAR(20)  NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'assigned', 'done', 'cancelled')),
    done_at         TIMESTAMPTZ,
    done_by         VARCHAR(200),

    -- GL link (diisi setelah posting)
    gl_journal_id   UUID         REFERENCES gl_journal(id),

    notes           TEXT,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_smove_product    ON stock_move(product_id, status);
CREATE INDEX IF NOT EXISTS idx_smove_location_d ON stock_move(destination_location_id, status);
CREATE INDEX IF NOT EXISTS idx_smove_location_s ON stock_move(source_location_id, status);
CREATE INDEX IF NOT EXISTS idx_smove_entity     ON stock_move(entity_id, move_type, status);
CREATE INDEX IF NOT EXISTS idx_smove_receipt    ON stock_move(po_receipt_id) WHERE po_receipt_id IS NOT NULL;

COMMENT ON TABLE stock_move IS
    'Setiap baris = satu pergerakan stok. Stok on_hand = SUM(qty masuk) - SUM(qty keluar). '
    'Status done memicu update valuasi dan posting jurnal GL otomatis.';


-- ============================================================
-- 7. STOCK VALUATION LAYER — lapisan biaya untuk FIFO
-- ============================================================

CREATE TABLE IF NOT EXISTS stock_valuation_layer (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id      UUID         NOT NULL REFERENCES product_product(id),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    location_id     UUID         NOT NULL REFERENCES inventory_location(id),
    move_id         UUID         NOT NULL REFERENCES stock_move(id),
    qty             NUMERIC(12,4) NOT NULL,          -- qty awal di layer ini
    unit_cost       NUMERIC(18,4) NOT NULL,          -- biaya per unit layer ini
    remaining_qty   NUMERIC(12,4) NOT NULL,          -- sisa yang belum dikonsumsi
    remaining_value NUMERIC(18,2) GENERATED ALWAYS AS (remaining_qty * unit_cost) STORED,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_svl_product ON stock_valuation_layer(product_id, entity_id, remaining_qty);
CREATE INDEX IF NOT EXISTS idx_svl_fifo    ON stock_valuation_layer(product_id, entity_id, location_id, created_at)
    WHERE remaining_qty > 0;
COMMENT ON TABLE stock_valuation_layer IS
    'Digunakan untuk metode FIFO. Setiap GR membuat layer baru. '
    'DO mengonsumsi layer tertua (created_at ASC) terlebih dahulu.';


-- ============================================================
-- 8. DELIVERY ORDER — dokumen pengiriman barang ke pelanggan
-- ============================================================

CREATE TABLE IF NOT EXISTS delivery_order (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    do_no           VARCHAR(30)  NOT NULL UNIQUE,    -- DO/2026/06/0001
    do_date         DATE         NOT NULL,
    customer_name   VARCHAR(200) NOT NULL,
    customer_id     UUID,  -- link ke master klien; FK ke customer(id) ditambahkan di schema_sales_order.sql
                            -- (customer table dibuat di sana, dideploy setelah schema ini)
    ar_invoice_id   UUID         REFERENCES ar_invoice(id),
    source_location_id UUID      NOT NULL REFERENCES inventory_location(id),
    so_reference    VARCHAR(100),  -- nomor SO (manual / integrasi SO module kelak)
    delivery_address TEXT,

    status          VARCHAR(20)  NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'ready', 'done', 'cancelled')),
    validated_by    VARCHAR(200),
    validated_at    TIMESTAMPTZ,
    notes           TEXT,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_do_entity ON delivery_order(entity_id, status);

CREATE TABLE IF NOT EXISTS delivery_order_line (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    do_id       UUID         NOT NULL REFERENCES delivery_order(id) ON DELETE CASCADE,
    line_no     SMALLINT     NOT NULL,
    product_id  UUID         NOT NULL REFERENCES product_product(id),
    lot_id      UUID         REFERENCES stock_lot(id),
    qty         NUMERIC(12,4) NOT NULL CHECK (qty > 0),
    uom_id      UUID         REFERENCES product_uom(id),
    unit_price  NUMERIC(18,2) NOT NULL DEFAULT 0,   -- harga jual ke pelanggan
    move_id     UUID         REFERENCES stock_move(id),  -- diisi saat validate
    UNIQUE (do_id, line_no)
);


-- ============================================================
-- 9. STOCK SCRAP — pencatatan barang rusak/dibuang
-- ============================================================

CREATE TABLE IF NOT EXISTS stock_scrap (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    scrap_no        VARCHAR(30)  NOT NULL UNIQUE,    -- SCR/2026/06/0001
    scrap_date      DATE         NOT NULL,
    product_id      UUID         NOT NULL REFERENCES product_product(id),
    lot_id          UUID         REFERENCES stock_lot(id),
    source_location_id UUID      NOT NULL REFERENCES inventory_location(id),
    qty             NUMERIC(12,4) NOT NULL CHECK (qty > 0),
    uom_id          UUID         REFERENCES product_uom(id),
    reason          TEXT,
    move_id         UUID         REFERENCES stock_move(id),
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);


-- ============================================================
-- 10. INVENTORY ADJUSTMENT — stock opname / physical count
-- ============================================================

CREATE TABLE IF NOT EXISTS inventory_adjustment (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    adjustment_no   VARCHAR(30)  NOT NULL UNIQUE,    -- SA/2026/06/0001
    location_id     UUID         NOT NULL REFERENCES inventory_location(id),
    adjustment_date DATE         NOT NULL,
    status          VARCHAR(20)  NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'in_progress', 'done', 'cancelled')),
    notes           TEXT,
    created_by      VARCHAR(200),
    confirmed_by    VARCHAR(200),
    confirmed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS inventory_adjustment_line (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    adjustment_id   UUID         NOT NULL REFERENCES inventory_adjustment(id) ON DELETE CASCADE,
    product_id      UUID         NOT NULL REFERENCES product_product(id),
    lot_id          UUID         REFERENCES stock_lot(id),
    theoretical_qty NUMERIC(12,4) NOT NULL DEFAULT 0,  -- qty menurut sistem
    actual_qty      NUMERIC(12,4),                     -- qty hasil hitung fisik (diisi tim gudang)
    unit_cost       NUMERIC(18,4) NOT NULL DEFAULT 0,  -- snapshot harga saat opname
    difference_qty  NUMERIC(12,4) GENERATED ALWAYS AS
                        (COALESCE(actual_qty, theoretical_qty) - theoretical_qty) STORED,
    move_id         UUID         REFERENCES stock_move(id),  -- diisi saat confirm
    UNIQUE (adjustment_id, product_id, lot_id)
);
COMMENT ON TABLE inventory_adjustment IS
    'Header stock opname. Konfirmasi akan membuat stock_move untuk setiap selisih.';


-- ============================================================
-- 11. REORDER RULES — kontrol min-max stok otomatis
-- ============================================================

CREATE TABLE IF NOT EXISTS reorder_rule (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    product_id      UUID         NOT NULL REFERENCES product_product(id),
    location_id     UUID         NOT NULL REFERENCES inventory_location(id),
    route           VARCHAR(20)  NOT NULL DEFAULT 'buy'
        CHECK (route IN ('buy', 'manufacture')),
    min_qty         NUMERIC(12,4) NOT NULL DEFAULT 0,   -- safety stock — trigger reorder
    max_qty         NUMERIC(12,4) NOT NULL DEFAULT 0,   -- target stok setelah reorder
    qty_multiple    NUMERIC(12,4) NOT NULL DEFAULT 1,   -- kelipatan pemesanan
    vendor_id       UUID         REFERENCES vendor(id), -- default vendor untuk auto-PR
    lead_time_days  SMALLINT     NOT NULL DEFAULT 0,    -- hari pengiriman dari vendor
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (entity_id, product_id, location_id)
);
COMMENT ON TABLE reorder_rule IS
    'Jika qty_on_hand ≤ min_qty, engine otomatis membuat draft PR ke modul procurement.';


-- ============================================================
-- 12. ALTER TABLE — tambah product_id ke po_item (untuk link ke inventory)
-- ============================================================

ALTER TABLE po_item ADD COLUMN IF NOT EXISTS product_id UUID REFERENCES product_product(id);
ALTER TABLE po_item ADD COLUMN IF NOT EXISTS lot_id     UUID REFERENCES stock_lot(id);
CREATE INDEX IF NOT EXISTS idx_poitem_product ON po_item(product_id) WHERE product_id IS NOT NULL;


-- ============================================================
-- 13. VIEWS
-- ============================================================

-- Stok on-hand per produk per lokasi (real-time dari mutasi)
CREATE OR REPLACE VIEW vw_stock_incoming AS
SELECT
    sm.product_id, sm.entity_id, sm.destination_location_id AS location_id,
    SUM(sm.qty_done)   AS qty_in,
    SUM(sm.total_cost) AS value_in
FROM stock_move sm
WHERE sm.status = 'done'
GROUP BY sm.product_id, sm.entity_id, sm.destination_location_id;

CREATE OR REPLACE VIEW vw_stock_outgoing AS
SELECT
    sm.product_id, sm.entity_id, sm.source_location_id AS location_id,
    SUM(sm.qty_done)   AS qty_out,
    SUM(sm.total_cost) AS value_out
FROM stock_move sm
WHERE sm.status = 'done'
GROUP BY sm.product_id, sm.entity_id, sm.source_location_id;

CREATE OR REPLACE VIEW vw_stock_current AS
SELECT
    COALESCE(i.product_id, o.product_id)     AS product_id,
    COALESCE(i.entity_id,  o.entity_id)      AS entity_id,
    COALESCE(i.location_id, o.location_id)   AS location_id,
    p.sku, p.product_name, p.current_avg_cost,
    pc.cost_method,
    loc.location_name, loc.location_type,
    COALESCE(i.qty_in,   0) - COALESCE(o.qty_out,   0) AS qty_on_hand,
    COALESCE(i.value_in, 0) - COALESCE(o.value_out, 0) AS stock_value
FROM vw_stock_incoming i
FULL OUTER JOIN vw_stock_outgoing o
    ON i.product_id = o.product_id
    AND i.entity_id  = o.entity_id
    AND i.location_id = o.location_id
JOIN product_product p   ON p.id  = COALESCE(i.product_id, o.product_id)
JOIN product_category pc ON pc.id = p.category_id
JOIN inventory_location loc ON loc.id = COALESCE(i.location_id, o.location_id)
WHERE loc.location_type = 'internal'
  AND (COALESCE(i.qty_in, 0) - COALESCE(o.qty_out, 0)) > 0;

-- Stok reserved (dari DO yang belum di-validate)
CREATE OR REPLACE VIEW vw_stock_reserved AS
SELECT
    sm.product_id, sm.entity_id, sm.source_location_id AS location_id,
    SUM(sm.qty_done) AS qty_reserved
FROM stock_move sm
WHERE sm.status = 'assigned'
  AND sm.move_type = 'delivery'
GROUP BY sm.product_id, sm.entity_id, sm.source_location_id;

-- Ringkasan stok + reserved + available
CREATE OR REPLACE VIEW vw_stock_summary AS
SELECT
    sc.entity_id, sc.product_id, sc.location_id,
    sc.sku, sc.product_name, sc.cost_method,
    sc.location_name, sc.current_avg_cost,
    sc.qty_on_hand,
    COALESCE(sr.qty_reserved, 0)                           AS qty_reserved,
    sc.qty_on_hand - COALESCE(sr.qty_reserved, 0)          AS qty_available,
    sc.stock_value
FROM vw_stock_current sc
LEFT JOIN vw_stock_reserved sr
    ON sr.product_id  = sc.product_id
    AND sr.entity_id  = sc.entity_id
    AND sr.location_id = sc.location_id;

-- Alert produk di bawah safety stock
CREATE OR REPLACE VIEW vw_low_stock_alert AS
SELECT
    ss.entity_id, ss.product_id, ss.sku, ss.product_name,
    ss.location_id, ss.location_name,
    ss.qty_on_hand, ss.qty_available,
    rr.min_qty AS safety_stock,
    rr.max_qty AS target_qty,
    rr.min_qty - ss.qty_available  AS shortage_qty,
    rr.route, rr.vendor_id, rr.lead_time_days
FROM vw_stock_summary ss
JOIN reorder_rule rr
    ON rr.product_id  = ss.product_id
    AND rr.location_id = ss.location_id
    AND rr.entity_id  = ss.entity_id
    AND rr.is_active  = TRUE
WHERE ss.qty_available <= rr.min_qty;


SELECT 'Migration schema_inventory selesai' AS status;
