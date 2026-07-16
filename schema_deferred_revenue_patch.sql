-- Patch: Deferred Revenue Engine (PSA gap #3, BRD user 2026-06-28).
-- Anchor ke project_milestone (PM module, sudah live + sudah punya weighted progress_pct
-- dari sesi sebelumnya) — BUKAN ke project_contract/contract_milestone (Contract module)
-- yang TIDAK PERNAH dideploy ke DB (\dt tidak menemukan tabel-tabel itu sama sekali) dan
-- contract_engine.py-nya juga referensi kolom ar_invoice yang tidak match skema live
-- (tax_amount/contract_id/milestone_id) — pola "kode ada, skema tidak sinkron" yang
-- berulang di project ini. TIDAK direvive di sini, di luar scope.

-- 1. Porsi contract value yang dialokasikan ke milestone ini (basis billing schedule).
ALTER TABLE project_milestone ADD COLUMN IF NOT EXISTS billing_amount NUMERIC(18,2) NOT NULL DEFAULT 0;

-- 2. Audit trail wajib (BRD: "Maintain audit trail of progress updates and revenue
--    recognition") — setiap event payment_received / revenue_recognized tercatat permanen,
--    plus referensi ke gl_journal yang di-posting via JournalEngine (double-entry tervalidasi,
--    period-lock-aware — bukan raw INSERT seperti pola lama payroll_engine.py/contract_engine.py
--    yang ternyata salah nama tabel/kolom).
CREATE TABLE IF NOT EXISTS deferred_revenue_ledger (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id            UUID NOT NULL REFERENCES project(id),
    milestone_id          UUID NOT NULL REFERENCES project_milestone(id),
    event_type            VARCHAR(20) NOT NULL CHECK (event_type IN ('payment_received', 'revenue_recognized')),
    amount                NUMERIC(18,2) NOT NULL CHECK (amount > 0),
    progress_pct_at_event SMALLINT,
    journal_id            UUID REFERENCES gl_journal(id),
    notes                 TEXT,
    created_by            VARCHAR(200),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_drl_milestone ON deferred_revenue_ledger(milestone_id);
CREATE INDEX IF NOT EXISTS idx_drl_project   ON deferred_revenue_ledger(project_id);

SELECT 'Migration schema_deferred_revenue_patch selesai' AS status;
