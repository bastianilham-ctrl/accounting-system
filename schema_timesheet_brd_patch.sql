-- Patch: Integrated Timesheet Management — link timesheet ke Task/Activity
-- (BRD user 2026-06-24, section 3-4: model data + trigger rollup ke project_task/milestone).
-- TIDAK membuat tabel baru `project_activities` — project_task yang sudah ada (dengan
-- kolom actual_hours yang SUDAH ADA dari sebelumnya) dipakai sebagai "Activity/Task".

-- 1. task_id: link timesheet ke project_task spesifik (opsional — NULL = jam proyek
--    level umum tanpa task spesifik, atau bench/internal kalau project_id juga NULL).
ALTER TABLE project_timesheet ADD COLUMN IF NOT EXISTS task_id UUID REFERENCES project_task(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_pts_task ON project_timesheet(task_id) WHERE task_id IS NOT NULL;

-- 2. Minimum hours per baris dinaikkan dari >0 ke >=0.5 (BRD: "Minimum: 0.5, Maximum: 24
--    hours per line item").
ALTER TABLE project_timesheet DROP CONSTRAINT IF EXISTS project_timesheet_hours_check;
ALTER TABLE project_timesheet ADD CONSTRAINT project_timesheet_hours_check
    CHECK (hours >= 0.5 AND hours <= 24);

SELECT 'Migration schema_timesheet_brd_patch selesai' AS status;
