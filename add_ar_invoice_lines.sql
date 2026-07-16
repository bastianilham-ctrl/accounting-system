-- Multi-line revenue allocation for AR invoices (mirrors ap_invoice_line)
CREATE TABLE IF NOT EXISTS ar_invoice_line (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ar_invoice_id UUID NOT NULL REFERENCES ar_invoice(id) ON DELETE CASCADE,
    line_no       SMALLINT NOT NULL,
    account_code  VARCHAR(20) NOT NULL,
    description   TEXT,
    amount        NUMERIC(18,2) NOT NULL DEFAULT 0,
    cost_center   VARCHAR(100),
    project_id    UUID REFERENCES project(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ar_invoice_id, line_no)
);

CREATE INDEX IF NOT EXISTS idx_ar_invoice_line_invoice ON ar_invoice_line(ar_invoice_id);
