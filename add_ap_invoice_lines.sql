-- Multi-line expense allocation untuk AP invoice (Odoo-style invoice lines)
-- Satu invoice bisa punya banyak baris beban, masing-masing dengan account/cost
-- center/project sendiri. Backward compatible: kolom lama di ap_invoice
-- (coa_expense, cost_center, project_id) tetap dipakai sebagai fallback
-- single-line untuk invoice lama / hasil OCR otomatis.

CREATE TABLE IF NOT EXISTS ap_invoice_line (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ap_invoice_id UUID NOT NULL REFERENCES ap_invoice(id) ON DELETE CASCADE,
    line_no       SMALLINT NOT NULL,
    account_code  VARCHAR(20) NOT NULL,
    description   TEXT,
    amount        NUMERIC(18,2) NOT NULL DEFAULT 0,
    cost_center   VARCHAR(100),
    project_id    UUID REFERENCES project(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ap_invoice_id, line_no)
);
CREATE INDEX IF NOT EXISTS idx_ap_invoice_line_invoice ON ap_invoice_line(ap_invoice_id);
