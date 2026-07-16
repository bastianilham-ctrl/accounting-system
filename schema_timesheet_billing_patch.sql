-- Patch: Timesheet-to-AR billing automation (PSA gap #2, user 2026-06-24).
-- Menandai timesheet yang sudah ke-tarik ke sebuah AR invoice, supaya batch
-- billing berikutnya tidak menagih ulang jam yang sama (double-billing).

ALTER TABLE project_timesheet ADD COLUMN IF NOT EXISTS is_invoiced BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE project_timesheet ADD COLUMN IF NOT EXISTS ar_invoice_id UUID REFERENCES ar_invoice(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_pts_invoiced ON project_timesheet(project_id, is_invoiced) WHERE is_invoiced = FALSE;

SELECT 'Migration schema_timesheet_billing_patch selesai' AS status;
