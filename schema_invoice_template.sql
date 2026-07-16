-- ============================================================
-- SCHEMA: Invoice Template & Email
--
-- Fitur:
--   - Template HTML invoice yang bisa dikustomisasi per entity
--   - Template email (subject + body) yang bisa dikustomisasi
--   - Field email address + CC di ar_invoice
--   - Tracking email sent (kapan, sukses/gagal)
--   - Email log per invoice
--
-- Template engine: Jinja2
-- PDF engine: WeasyPrint (HTML → PDF)
--
-- Variable yang tersedia di template invoice:
--   {{ company.name }}, {{ company.address }}, {{ company.logo_base64 }}
--   {{ invoice.number }}, {{ invoice.date }}, {{ invoice.due_date }}
--   {{ customer.name }}, {{ customer.address }}
--   {{ items }}  ← list of line items
--   {{ summary.subtotal }}, {{ summary.tax_total }}, {{ summary.total }}
--   {{ payment.bank_name }}, {{ payment.account_number }}
--
-- Variable email template:
--   {{ customer_name }}, {{ invoice_number }}, {{ invoice_date }}
--   {{ due_date }}, {{ total_amount }}, {{ company_name }}
--   {{ sender_name }}, {{ days_until_due }}
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_invoice_template.sql
-- ============================================================

-- ── 1. INVOICE TEMPLATE ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS invoice_template (
    id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    template_name   VARCHAR(200) NOT NULL,
    is_default      BOOLEAN      NOT NULL DEFAULT FALSE,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,

    -- Layout & branding
    logo_base64     TEXT,           -- base64 encoded logo image
    logo_url        VARCHAR(500),   -- atau URL logo
    primary_color   VARCHAR(10)     DEFAULT '#1a56db',
    secondary_color VARCHAR(10)     DEFAULT '#374151',
    font_family     VARCHAR(100)    DEFAULT 'Inter, Arial, sans-serif',

    -- Company info yang tampil di invoice
    company_display_name   VARCHAR(200),
    company_address        TEXT,
    company_phone          VARCHAR(100),
    company_email          VARCHAR(200),
    company_website        VARCHAR(200),
    company_tax_number     VARCHAR(100),   -- NPWP
    company_pkp_number     VARCHAR(100),   -- Nomor PKP
    company_bank_name      VARCHAR(200),
    company_bank_account   VARCHAR(100),
    company_bank_holder    VARCHAR(200),
    company_bank_branch    VARCHAR(200),
    company_swift_code     VARCHAR(20),

    -- HTML template body (Jinja2)
    template_html   TEXT NOT NULL,

    -- Footer text
    footer_text     TEXT,
    payment_terms_text TEXT,        -- mis. "Pembayaran dalam 30 hari sejak tanggal invoice"
    notes_default   TEXT,           -- catatan default yang selalu muncul

    -- Paper settings
    paper_size      VARCHAR(10)     DEFAULT 'A4',  -- A4 | Letter
    orientation     VARCHAR(10)     DEFAULT 'portrait',
    margin_top      SMALLINT        DEFAULT 20,    -- mm
    margin_bottom   SMALLINT        DEFAULT 20,
    margin_left     SMALLINT        DEFAULT 15,
    margin_right    SMALLINT        DEFAULT 15,

    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_invtmpl_entity ON invoice_template(entity_id, is_default);

-- Pastikan hanya satu default template per entity
CREATE UNIQUE INDEX IF NOT EXISTS uidx_invtmpl_default
    ON invoice_template(entity_id)
    WHERE is_default = TRUE AND is_active = TRUE;


-- ── 2. EMAIL TEMPLATE ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS invoice_email_template (
    id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    template_name   VARCHAR(200) NOT NULL,
    is_default      BOOLEAN      NOT NULL DEFAULT FALSE,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,

    -- Subject bisa pakai variable Jinja2
    subject_template    VARCHAR(500) NOT NULL
        DEFAULT 'Invoice {{ invoice_number }} dari {{ company_name }}',

    -- Body HTML template (Jinja2)
    body_template   TEXT NOT NULL,

    -- Reply-to email
    reply_to        VARCHAR(200),

    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_emailtmpl_entity ON invoice_email_template(entity_id, is_default);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_emailtmpl_default
    ON invoice_email_template(entity_id)
    WHERE is_default = TRUE AND is_active = TRUE;


-- ── 3. ALTER ar_invoice — tambah field email ──────────────────────────────────
ALTER TABLE ar_invoice
    ADD COLUMN IF NOT EXISTS customer_email   VARCHAR(500),
    ADD COLUMN IF NOT EXISTS email_cc         TEXT,           -- koma-separated
    ADD COLUMN IF NOT EXISTS invoice_template_id UUID REFERENCES invoice_template(id),
    ADD COLUMN IF NOT EXISTS email_template_id   UUID REFERENCES invoice_email_template(id),
    ADD COLUMN IF NOT EXISTS email_sent       BOOLEAN      NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS email_sent_at    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS email_error      TEXT,          -- error terakhir jika gagal
    ADD COLUMN IF NOT EXISTS email_send_count SMALLINT     NOT NULL DEFAULT 0;


-- ── 4. EMAIL LOG ──────────────────────────────────────────────────────────────
-- Catat setiap attempt pengiriman email
CREATE TABLE IF NOT EXISTS invoice_email_log (
    id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    ar_invoice_id   UUID         NOT NULL REFERENCES ar_invoice(id),
    entity_id       UUID         NOT NULL,
    sent_to         TEXT         NOT NULL,   -- recipient email
    sent_cc         TEXT,
    subject         TEXT,
    body_html       TEXT,
    status          VARCHAR(20)  NOT NULL    -- success | failed
        CHECK (status IN ('success', 'failed')),
    error_message   TEXT,
    sent_by         VARCHAR(200),
    sent_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    smtp_response   TEXT
);

CREATE INDEX IF NOT EXISTS idx_emaillog_invoice ON invoice_email_log(ar_invoice_id);
CREATE INDEX IF NOT EXISTS idx_emaillog_entity  ON invoice_email_log(entity_id, sent_at DESC);


-- ── 5. ENTITY EMAIL CONFIG ────────────────────────────────────────────────────
-- SMTP config per entity (override dari .env global)
CREATE TABLE IF NOT EXISTS entity_email_config (
    id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL UNIQUE REFERENCES entity(id),
    smtp_host       VARCHAR(200),
    smtp_port       SMALLINT     DEFAULT 587,
    smtp_username   VARCHAR(200),
    smtp_password   TEXT,           -- sebaiknya dienkripsi
    use_tls         BOOLEAN      NOT NULL DEFAULT TRUE,
    use_ssl         BOOLEAN      NOT NULL DEFAULT FALSE,
    from_name       VARCHAR(200),
    from_address    VARCHAR(200),
    reply_to        VARCHAR(200),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    -- Test result
    last_test_at    TIMESTAMPTZ,
    last_test_ok    BOOLEAN,
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ── 6. VIEWS ─────────────────────────────────────────────────────────────────

-- Status email semua invoice
CREATE OR REPLACE VIEW vw_invoice_email_status AS
SELECT
    ai.id           AS invoice_id,
    ai.invoice_number,
    ai.invoice_date,
    ai.due_date,
    ai.status       AS invoice_status,
    ai.total_amount,
    ai.customer_email,
    ai.email_cc,
    ai.email_sent,
    ai.email_sent_at,
    ai.email_send_count,
    ai.email_error,
    e.entity_name,
    -- Last email attempt
    (SELECT el.status FROM invoice_email_log el
     WHERE el.ar_invoice_id = ai.id
     ORDER BY el.sent_at DESC LIMIT 1)  AS last_email_status,
    (SELECT el.sent_at FROM invoice_email_log el
     WHERE el.ar_invoice_id = ai.id
     ORDER BY el.sent_at DESC LIMIT 1)  AS last_email_at
FROM ar_invoice ai
JOIN entity e ON e.id = ai.entity_id;


SELECT 'Migration schema_invoice_template selesai' AS status;
