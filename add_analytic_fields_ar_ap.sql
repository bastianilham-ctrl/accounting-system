-- Analytic accounting fields (cost center / project) on AR & AP invoices
-- Mirrors journal_entry_line.cost_center (text code) / project_id (UUID FK to project)

ALTER TABLE ar_invoice ADD COLUMN IF NOT EXISTS cost_center VARCHAR(100);
ALTER TABLE ar_invoice ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES project(id);

ALTER TABLE ap_invoice ADD COLUMN IF NOT EXISTS cost_center VARCHAR(100);
ALTER TABLE ap_invoice ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES project(id);
