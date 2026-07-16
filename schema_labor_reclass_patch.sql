-- Patch: Labor Cost Reclass GL flag + payroll_labor_reclass audit table
-- (PSA gap #4, BRD user 2026-06-28)

-- Track per-period GL reclass posting state (parallel to labor_allocation_posted)
ALTER TABLE analytic_period
    ADD COLUMN IF NOT EXISTS labor_reclass_gl_posted BOOLEAN NOT NULL DEFAULT FALSE;

-- Audit trail for each reclass GL journal batch
CREATE TABLE IF NOT EXISTS payroll_labor_reclass (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id         UUID NOT NULL REFERENCES entity(id),
    year              SMALLINT NOT NULL,
    month             SMALLINT NOT NULL,
    journal_id        UUID REFERENCES gl_journal(id),
    employees_count   SMALLINT NOT NULL DEFAULT 0,
    total_reclassed   NUMERIC(18,2) NOT NULL DEFAULT 0,
    created_by        VARCHAR(200),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (entity_id, year, month)
);

SELECT 'Migration schema_labor_reclass_patch selesai' AS status;
