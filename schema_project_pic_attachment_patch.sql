-- Patch: PIC roles (Project Director, Work Package Manager) + BAST attachment support
-- untuk modul Project Management (gap report user 2026-06-23).

-- 1. Perluas role_in_project: tambah project_director (governance, di atas sponsor/PM)
--    dan work_package_manager (PIC per WBS item/paket kerja, bukan PIC seluruh proyek).
ALTER TABLE project_team_member DROP CONSTRAINT IF EXISTS project_team_member_role_in_project_check;
ALTER TABLE project_team_member ADD CONSTRAINT project_team_member_role_in_project_check
    CHECK (role_in_project IN (
        'project_director', 'sponsor', 'project_manager', 'work_package_manager',
        'team_lead', 'member', 'consultant', 'reviewer', 'stakeholder'
    ));

-- wbs_item_id: scope opsional supaya satu Work Package Manager bisa di-assign spesifik ke
-- satu WBS item/paket kerja (pola sama seperti raci_entry.wbs_item_id), bukan ke seluruh proyek.
ALTER TABLE project_team_member ADD COLUMN IF NOT EXISTS wbs_item_id UUID REFERENCES wbs_item(id);

-- 2. BAST (Berita Acara Serah Terima) attachment untuk task & milestone yang sudah selesai.
ALTER TABLE attachment_link DROP CONSTRAINT IF EXISTS attachment_link_ref_type_check;
ALTER TABLE attachment_link ADD CONSTRAINT attachment_link_ref_type_check
    CHECK (ref_type IN (
        'ar_invoice','ap_invoice','expense_claim','contract','project',
        'project_task','project_milestone',
        'vendor','employee','journal','quotation','sales_order',
        'bank_statement','wht_transaction','purchase_order',
        'delivery_order','leave_request','asset','payroll','other'
    ));

-- 3. Milestone: Target Start Date (target_date existing = Target End Date) +
--    progress_pct sebagai dasar revenue recognition (terpisah dari status pending/achieved/dll,
--    karena milestone besar bisa progress parsial sebelum status berubah jadi 'achieved').
ALTER TABLE project_milestone ADD COLUMN IF NOT EXISTS target_start_date DATE;
ALTER TABLE project_milestone ADD COLUMN IF NOT EXISTS progress_pct SMALLINT NOT NULL DEFAULT 0
    CHECK (progress_pct BETWEEN 0 AND 100);

-- 4. Day-to-day progress tracking (TERPISAH dari `status` pending/achieved/missed/at_risk
--    yang dipakai Health Dashboard untuk overdue_milestones — jangan disatukan).
--    progress_status untuk dipantau PM harian; saat diset 'completed', router otomatis
--    set status='achieved' + actual_date=hari ini supaya 2 sistem tetap sinkron.
ALTER TABLE project_milestone ADD COLUMN IF NOT EXISTS progress_status VARCHAR(20) NOT NULL DEFAULT 'not_started'
    CHECK (progress_status IN ('not_started','in_progress','completed'));
ALTER TABLE project_milestone ADD COLUMN IF NOT EXISTS issue_notes TEXT;

-- 5. Parent-child Task -> Milestone (Milestone = Parent/besaran, Task = Child/detail) —
--    request user 2026-06-24. Progress milestone otomatis di-rollup dari rata-rata
--    progress_pct task anaknya (lihat ProjectSetupEngine.recompute_milestone_progress);
--    milestone TANPA task anak tetap pakai input manual seperti sebelumnya (backward compatible).
ALTER TABLE project_task ADD COLUMN IF NOT EXISTS milestone_id UUID REFERENCES project_milestone(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_ptask_milestone ON project_task(milestone_id) WHERE milestone_id IS NOT NULL;

-- vw_gantt_chart: tambah milestone_id+milestone_name di PALING AKHIR select list
-- (Postgres CREATE OR REPLACE VIEW menolak kalau kolom baru disisipkan di tengah).
CREATE OR REPLACE VIEW vw_gantt_chart AS
SELECT
    pt.id,
    pt.project_id,
    pt.task_code,
    pt.task_name,
    pt.wbs_item_id,
    wi.wbs_code,
    wi.wbs_name,
    e.full_name                  AS assigned_to,
    pt.planned_start,
    pt.planned_end,
    pt.duration_days,
    pt.actual_start,
    pt.actual_end,
    pt.progress_pct,
    pt.status,
    pt.early_start,
    pt.early_finish,
    pt.late_start,
    pt.late_finish,
    pt.total_float,
    pt.is_critical,
    pt.planned_cost,
    pt.actual_cost,
    COALESCE(
        string_agg(
            DISTINCT td.predecessor_id::TEXT || ':' || td.dependency_type,
            ',' ORDER BY td.predecessor_id::TEXT || ':' || td.dependency_type
        ),
        ''
    )                            AS predecessors,
    pt.milestone_id,
    pm.milestone_name             AS milestone_name,
    pt.planned_hours
FROM project_task pt
LEFT JOIN wbs_item wi       ON wi.id  = pt.wbs_item_id
LEFT JOIN employee e        ON e.id   = pt.assigned_to_id
LEFT JOIN project_milestone pm ON pm.id = pt.milestone_id
LEFT JOIN task_dependency td ON td.successor_id = pt.id
WHERE pt.status != 'cancelled'
GROUP BY pt.id, wi.wbs_code, wi.wbs_name, e.full_name, pm.milestone_name;

-- 6. Weighted Progress (Rata-rata Terbobot) — request user 2026-06-24 (lanjutan).
--    vw_gantt_chart perlu expose pt.planned_hours (bobot man-days) supaya frontend bisa
--    nampilin "Weight Indicator" per task — kolom ditambah di PALING AKHIR select list lagi
--    (gotcha CREATE OR REPLACE VIEW yang sama). Sudah termasuk di definisi view di atas;
--    blok ini cuma marker urutan migrasi untuk dokumentasi.

SELECT 'Migration schema_project_pic_attachment_patch selesai' AS status;
