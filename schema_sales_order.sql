-- ============================================================
-- SCHEMA: Sales Order (SO) Module
--
-- Alur: Quotation → Sales Order → Picking (reserve stock) → DO → AR Invoice
--
-- Integrasi:
--   - inventory: stock_move untuk pengurangan stok
--   - ar_invoice: dibuat otomatis dari DO
--   - contract_tracker: bisa di-link ke contract_milestone
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_sales_order.sql
-- Dependensi: schema_ar.sql, schema_inventory.sql, schema_vendor.sql (customer)
-- ============================================================

-- ── 1. CUSTOMER MASTER (extend dari vendor / buat sendiri) ───────────────────
-- Gunakan tabel customer yang ada jika sudah ada, atau buat alias
CREATE TABLE IF NOT EXISTS customer (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    customer_code   VARCHAR(30)  NOT NULL,
    customer_name   VARCHAR(300) NOT NULL,
    customer_type   VARCHAR(20)  NOT NULL DEFAULT 'company'
        CHECK (customer_type IN ('company','individual','government')),
    npwp            VARCHAR(30),
    address         TEXT,
    city            VARCHAR(100),
    province        VARCHAR(100),
    phone           VARCHAR(50),
    email           VARCHAR(200),
    contact_person  VARCHAR(200),
    credit_limit    NUMERIC(18,2) NOT NULL DEFAULT 0,
    payment_term_days SMALLINT   NOT NULL DEFAULT 30,
    is_pkp          BOOLEAN NOT NULL DEFAULT FALSE,   -- Pengusaha Kena Pajak
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (entity_id, customer_code)
);
CREATE INDEX IF NOT EXISTS idx_customer_entity ON customer(entity_id, is_active);


-- ── 2. PRICE LIST ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS price_list (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    product_id      UUID         NOT NULL REFERENCES product_product(id),
    customer_id     UUID         REFERENCES customer(id),   -- NULL = berlaku untuk semua
    currency        VARCHAR(3)   NOT NULL DEFAULT 'IDR',
    unit_price      NUMERIC(18,2) NOT NULL,
    valid_from      DATE         NOT NULL,
    valid_to        DATE,
    min_qty         NUMERIC(14,4) NOT NULL DEFAULT 1,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_pricelist_product ON price_list(product_id, is_active);


-- ── 3. QUOTATION ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS quotation (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    quotation_no    VARCHAR(50)  NOT NULL UNIQUE,
    customer_id     UUID         NOT NULL REFERENCES customer(id),
    quotation_date  DATE         NOT NULL,
    valid_until     DATE         NOT NULL,
    salesperson     VARCHAR(200),
    currency        VARCHAR(3)   NOT NULL DEFAULT 'IDR',
    exchange_rate   NUMERIC(14,4) NOT NULL DEFAULT 1,
    subtotal        NUMERIC(18,2) NOT NULL DEFAULT 0,
    discount_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
    tax_amount      NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_amount    NUMERIC(18,2) NOT NULL DEFAULT 0,
    notes           TEXT,
    status          VARCHAR(20)  NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','sent','confirmed','expired','cancelled')),
    so_id           UUID,        -- FK ke sales_order setelah dikonfirmasi (set saat confirm)
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_quot_entity   ON quotation(entity_id, status);
CREATE INDEX IF NOT EXISTS idx_quot_customer ON quotation(customer_id, quotation_date DESC);


CREATE TABLE IF NOT EXISTS quotation_line (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    quotation_id    UUID         NOT NULL REFERENCES quotation(id) ON DELETE CASCADE,
    line_no         SMALLINT     NOT NULL,
    product_id      UUID         NOT NULL REFERENCES product_product(id),
    description     TEXT,
    qty             NUMERIC(14,4) NOT NULL,
    uom_id          UUID         NOT NULL REFERENCES product_uom(id),
    unit_price      NUMERIC(18,2) NOT NULL,
    discount_pct    NUMERIC(5,2) NOT NULL DEFAULT 0,
    subtotal        NUMERIC(18,2) GENERATED ALWAYS AS
                        (qty * unit_price * (1 - discount_pct / 100)) STORED,
    tax_rate        NUMERIC(5,2) NOT NULL DEFAULT 11,   -- PPN 11%
    notes           TEXT,
    UNIQUE (quotation_id, line_no)
);


-- ── 4. SALES ORDER ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sales_order (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    so_no           VARCHAR(50)  NOT NULL UNIQUE,
    customer_id     UUID         NOT NULL REFERENCES customer(id),
    quotation_id    UUID         REFERENCES quotation(id),
    so_date         DATE         NOT NULL,
    requested_delivery_date DATE,
    salesperson     VARCHAR(200),
    currency        VARCHAR(3)   NOT NULL DEFAULT 'IDR',
    exchange_rate   NUMERIC(14,4) NOT NULL DEFAULT 1,
    subtotal        NUMERIC(18,2) NOT NULL DEFAULT 0,
    discount_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
    tax_amount      NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_amount    NUMERIC(18,2) NOT NULL DEFAULT 0,
    payment_term_days SMALLINT   NOT NULL DEFAULT 30,
    warehouse_id    UUID         REFERENCES inventory_location(id),
    notes           TEXT,
    status          VARCHAR(20)  NOT NULL DEFAULT 'draft'
        CHECK (status IN (
            'draft',        -- sedang dibuat
            'confirmed',    -- dikonfirmasi, siap proses
            'picking',      -- sedang disiapkan (stok direservasi)
            'ready',        -- stok sudah disiapkan, siap kirim
            'delivered',    -- sudah dikirim (DO dibuat)
            'invoiced',     -- AR Invoice sudah dibuat
            'done',         -- selesai
            'cancelled'
        )),
    contract_id     UUID,        -- opsional link ke contract
    milestone_id    UUID,        -- opsional link ke contract_milestone
    project_id      UUID         REFERENCES project(id),
    confirmed_by    VARCHAR(200),
    confirmed_at    TIMESTAMPTZ,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_so_entity   ON sales_order(entity_id, status);
CREATE INDEX IF NOT EXISTS idx_so_customer ON sales_order(customer_id, so_date DESC);


CREATE TABLE IF NOT EXISTS sales_order_line (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    so_id           UUID         NOT NULL REFERENCES sales_order(id) ON DELETE CASCADE,
    line_no         SMALLINT     NOT NULL,
    product_id      UUID         NOT NULL REFERENCES product_product(id),
    description     TEXT,
    qty_ordered     NUMERIC(14,4) NOT NULL,
    qty_delivered   NUMERIC(14,4) NOT NULL DEFAULT 0,
    qty_invoiced    NUMERIC(14,4) NOT NULL DEFAULT 0,
    uom_id          UUID         NOT NULL REFERENCES product_uom(id),
    unit_price      NUMERIC(18,2) NOT NULL,
    discount_pct    NUMERIC(5,2) NOT NULL DEFAULT 0,
    subtotal        NUMERIC(18,2) GENERATED ALWAYS AS
                        (qty_ordered * unit_price * (1 - discount_pct / 100)) STORED,
    tax_rate        NUMERIC(5,2) NOT NULL DEFAULT 11,
    lot_id          UUID         REFERENCES stock_lot(id),
    notes           TEXT,
    UNIQUE (so_id, line_no)
);
CREATE INDEX IF NOT EXISTS idx_sol_so ON sales_order_line(so_id);


-- ── 5. PICKING ORDER (reservasi stok) ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS picking_order (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    picking_no      VARCHAR(50)  NOT NULL UNIQUE,
    so_id           UUID         NOT NULL REFERENCES sales_order(id),
    warehouse_id    UUID         NOT NULL REFERENCES inventory_location(id),
    picking_date    DATE         NOT NULL,
    status          VARCHAR(20)  NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','in_progress','done','cancelled')),
    picked_by       VARCHAR(200),
    picked_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS picking_order_line (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    picking_id      UUID         NOT NULL REFERENCES picking_order(id) ON DELETE CASCADE,
    so_line_id      UUID         NOT NULL REFERENCES sales_order_line(id),
    product_id      UUID         NOT NULL REFERENCES product_product(id),
    lot_id          UUID         REFERENCES stock_lot(id),
    qty_to_pick     NUMERIC(14,4) NOT NULL,
    qty_picked      NUMERIC(14,4) NOT NULL DEFAULT 0,
    uom_id          UUID         NOT NULL REFERENCES product_uom(id),
    UNIQUE (picking_id, so_line_id)
);


-- ── 6. VIEWS ─────────────────────────────────────────────────────────────────

-- SO dengan status pengiriman
CREATE OR REPLACE VIEW vw_so_fulfillment AS
SELECT
    so.id AS so_id,
    so.entity_id,
    so.so_no,
    c.customer_name,
    so.so_date,
    so.requested_delivery_date,
    so.total_amount,
    so.status,
    COUNT(sol.id)                                       AS total_lines,
    SUM(sol.qty_ordered)                                AS total_qty_ordered,
    SUM(sol.qty_delivered)                              AS total_qty_delivered,
    SUM(sol.qty_invoiced)                               AS total_qty_invoiced,
    ROUND(100.0 * SUM(sol.qty_delivered)
          / NULLIF(SUM(sol.qty_ordered), 0), 1)         AS delivery_pct,
    ROUND(100.0 * SUM(sol.qty_invoiced)
          / NULLIF(SUM(sol.qty_ordered), 0), 1)         AS invoice_pct
FROM sales_order so
JOIN customer c   ON c.id  = so.customer_id
LEFT JOIN sales_order_line sol ON sol.so_id = so.id
GROUP BY so.id, c.customer_name;


-- Stok available per produk (dari vw_stock_current jika ada)
CREATE OR REPLACE VIEW vw_so_product_availability AS
SELECT
    p.id AS product_id,
    p.product_code,
    p.product_name,
    p.tracking_type,
    COALESCE(sc.qty_available, 0) AS qty_available,
    COALESCE(sc.qty_reserved,  0) AS qty_reserved,
    COALESCE(sc.qty_on_hand,   0) AS qty_on_hand,
    p.current_avg_cost
FROM product_product p
LEFT JOIN vw_stock_summary sc ON sc.product_id = p.id
WHERE p.is_active = TRUE;


-- Pipeline penjualan (quotation + SO)
CREATE OR REPLACE VIEW vw_sales_pipeline AS
SELECT
    'quotation'  AS doc_type,
    q.id         AS doc_id,
    q.entity_id,
    q.quotation_no AS doc_no,
    c.customer_name,
    q.quotation_date AS doc_date,
    q.valid_until    AS expiry_date,
    q.total_amount,
    q.status,
    q.salesperson
FROM quotation q JOIN customer c ON c.id = q.customer_id
UNION ALL
SELECT
    'sales_order' AS doc_type,
    so.id,
    so.entity_id,
    so.so_no,
    c.customer_name,
    so.so_date,
    so.requested_delivery_date,
    so.total_amount,
    so.status,
    so.salesperson
FROM sales_order so JOIN customer c ON c.id = so.customer_id;

-- delivery_order.customer_id dibuat tanpa FK di schema_inventory.sql (customer table belum ada
-- saat itu) — tabel customer baru ada di schema ini, jadi FK-nya ditambahkan di sini.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'delivery_order_customer_id_fkey'
    ) THEN
        ALTER TABLE delivery_order
            ADD CONSTRAINT delivery_order_customer_id_fkey
            FOREIGN KEY (customer_id) REFERENCES customer(id);
    END IF;
END $$;

SELECT 'Migration schema_sales_order selesai' AS status;
