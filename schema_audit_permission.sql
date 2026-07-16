-- ============================================================
-- SCHEMA: Audit Trail + Multi-Entity User Permission
--
-- AUDIT TRAIL
--   Setiap transaksi bisnis dilog: siapa, kapan, aksi apa,
--   record mana, before/after data (JSONB diff).
--   AuditEngine.log() dipanggil dari tiap engine method.
--   Severity: info | warning | critical
--
-- MULTI-ENTITY PERMISSION
--   Satu user bisa punya role berbeda di entity berbeda.
--   user_entity_permission: user_id + entity_id → role
--   Super-admin (users.role='admin') melewati semua cek entity.
--   Role hierarchy: viewer < finance < approver < admin
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_audit_permission.sql
-- ============================================================


-- ════════════════════════════════════════════════════════════
-- BAGIAN 1: AUDIT TRAIL
-- ════════════════════════════════════════════════════════════

-- ── 1a. AUDIT LOG ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         REFERENCES entity(id),         -- nullable (login event tidak punya entity)
    user_id         UUID,                                        -- nullable (system action)
    username        VARCHAR(200),
    user_ip         INET,
    user_agent      TEXT,

    -- Apa yang terjadi
    action          VARCHAR(50)  NOT NULL
        CHECK (action IN (
            -- User / Auth
            'LOGIN','LOGOUT','PASSWORD_CHANGE','TOKEN_REFRESH',
            -- Data mutations
            'CREATE','UPDATE','DELETE','RESTORE',
            -- Workflow
            'SUBMIT','APPROVE','REJECT','CANCEL','VOID','REVERSE',
            -- Financial
            'POST','LOCK','CLOSE','REOPEN','FINALIZE',
            -- Access control
            'GRANT_ACCESS','REVOKE_ACCESS','ROLE_CHANGE',
            -- System / Scheduler
            'IMPORT','EXPORT','SCHEDULER_RUN',
            -- Generic
            'OTHER'
        )),
    module          VARCHAR(100),                                -- journal/ar_invoice/employee/contract/etc.
    ref_type        VARCHAR(50),                                 -- sama dgn attachment ref_type
    ref_id          UUID,                                        -- PK record yang terpengaruh
    ref_number      VARCHAR(200),                               -- nomor human-readable (no.invoice, no.jurnal, dll.)

    -- Deskripsi & Data
    description     TEXT         NOT NULL,                      -- kalimat ringkas: "Invoice INV-2024-001 diapprove"
    before_data     JSONB,                                       -- state sebelum perubahan
    after_data      JSONB,                                       -- state sesudah perubahan
    diff_data       JSONB,                                       -- hanya field yang berubah {field: {before,after}}
    metadata        JSONB,                                       -- context tambahan (IP, browser, dll.)

    -- Klasifikasi
    severity        VARCHAR(20)  NOT NULL DEFAULT 'info'
        CHECK (severity IN ('info','warning','critical')),
    is_system       BOOLEAN      NOT NULL DEFAULT FALSE,        -- true jika dijalankan oleh scheduler/background job

    -- Grouping (satu HTTP request bisa punya beberapa log)
    request_id      UUID,

    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Index untuk query umum
CREATE INDEX IF NOT EXISTS idx_audit_entity     ON audit_log(entity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_ref        ON audit_log(ref_type, ref_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user       ON audit_log(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action     ON audit_log(entity_id, action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_severity   ON audit_log(entity_id, severity, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_module     ON audit_log(entity_id, module, created_at DESC);

-- Partitioning-ready: created_at index akan digunakan untuk pruning log lama
-- (tambahkan pg_partman atau cron DELETE jika volume besar)


-- ── 1b. VIEWS ─────────────────────────────────────────────────────────────────

-- History satu record (timeline view)
CREATE OR REPLACE VIEW vw_record_audit_history AS
SELECT
    al.id,
    al.entity_id,
    al.user_id,
    al.username,
    al.action,
    al.module,
    al.ref_type,
    al.ref_id,
    al.ref_number,
    al.description,
    al.diff_data,
    al.severity,
    al.is_system,
    al.created_at
FROM audit_log al
ORDER BY al.ref_type, al.ref_id, al.created_at DESC;


-- Activity summary per entity (count per action per day)
CREATE OR REPLACE VIEW vw_audit_entity_summary AS
SELECT
    entity_id,
    DATE_TRUNC('day', created_at)::DATE AS activity_date,
    module,
    action,
    severity,
    COUNT(*)          AS event_count,
    COUNT(DISTINCT user_id) FILTER (WHERE user_id IS NOT NULL) AS unique_users
FROM audit_log
WHERE entity_id IS NOT NULL
GROUP BY entity_id, DATE_TRUNC('day', created_at), module, action, severity;


-- Critical events (approval, void, delete, permission changes)
CREATE OR REPLACE VIEW vw_audit_critical_events AS
SELECT
    al.id,
    al.entity_id,
    al.username,
    al.action,
    al.module,
    al.ref_type,
    al.ref_id,
    al.ref_number,
    al.description,
    al.diff_data,
    al.user_ip,
    al.created_at
FROM audit_log al
WHERE al.severity IN ('warning','critical')
ORDER BY al.created_at DESC;


-- ════════════════════════════════════════════════════════════
-- BAGIAN 2: MULTI-ENTITY USER PERMISSION
-- ════════════════════════════════════════════════════════════

-- ── 2a. USER ENTITY PERMISSION ────────────────────────────────────────────────
-- Satu row = satu user punya satu role di satu entity
-- Super-admin (users.role='admin') bypass tabel ini (akses semua entity)
CREATE TABLE IF NOT EXISTS user_entity_permission (
    id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID         NOT NULL,                       -- FK ke users.id
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    role            VARCHAR(20)  NOT NULL DEFAULT 'viewer'
        CHECK (role IN ('viewer','finance','approver','admin')),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,

    -- Module-level restrictions (null = akses semua modul dalam role)
    -- JSONB array: ["journal","ar_invoice","ap_invoice"] — null = semua
    allowed_modules JSONB,

    -- Validity period
    valid_from      DATE,
    valid_until     DATE,                                        -- null = tidak expire

    granted_by      VARCHAR(200),
    granted_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    revoked_by      VARCHAR(200),
    revoked_at      TIMESTAMPTZ,
    notes           TEXT,

    UNIQUE (user_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_uep_user   ON user_entity_permission(user_id, is_active);
CREATE INDEX IF NOT EXISTS idx_uep_entity ON user_entity_permission(entity_id, is_active);


-- ── 2b. PERMISSION AUDIT (setiap grant/revoke dicatat) ───────────────────────
-- Note: grant/revoke juga masuk ke audit_log dengan action GRANT_ACCESS/REVOKE_ACCESS
-- Tabel ini khusus untuk history permission secara lengkap
CREATE TABLE IF NOT EXISTS permission_history (
    id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID         NOT NULL,
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    action          VARCHAR(20)  NOT NULL
        CHECK (action IN ('granted','revoked','role_changed','reactivated','expired')),
    old_role        VARCHAR(20),
    new_role        VARCHAR(20),
    performed_by    VARCHAR(200),
    reason          TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_permhist_user   ON permission_history(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_permhist_entity ON permission_history(entity_id, created_at DESC);


-- ── 2c. VIEWS ─────────────────────────────────────────────────────────────────

-- Effective permissions (only active, not expired)
CREATE OR REPLACE VIEW vw_active_permissions AS
SELECT
    uep.id,
    uep.user_id,
    uep.entity_id,
    e.entity_name,
    uep.role,
    uep.is_active,
    uep.allowed_modules,
    uep.valid_from,
    uep.valid_until,
    uep.granted_by,
    uep.granted_at,
    CASE
        WHEN uep.valid_until IS NOT NULL AND uep.valid_until < CURRENT_DATE THEN TRUE
        ELSE FALSE
    END AS is_expired
FROM user_entity_permission uep
JOIN entity e ON e.id = uep.entity_id
WHERE uep.is_active = TRUE;


-- User access matrix (semua entity yang bisa diakses user)
CREATE OR REPLACE VIEW vw_user_access_matrix AS
SELECT
    uep.user_id,
    uep.entity_id,
    e.entity_name,
    uep.role,
    uep.allowed_modules,
    uep.valid_until,
    (uep.valid_until IS NULL OR uep.valid_until >= CURRENT_DATE) AS is_valid
FROM user_entity_permission uep
JOIN entity e ON e.id = uep.entity_id
WHERE uep.is_active = TRUE;


SELECT 'Migration schema_audit_permission selesai' AS status;
