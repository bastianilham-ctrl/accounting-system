-- ============================================================
-- USER & AUTH SCHEMA
-- Jalankan: psql -U postgres -d accounting_db -f schema_users.sql
-- ============================================================

-- Role enum
CREATE TYPE user_role AS ENUM (
    'superadmin',   -- akses semua entity, bisa manage user
    'admin',        -- akses semua fitur dalam entity sendiri
    'finance',      -- posting jurnal, AP, AR, asset
    'viewer'        -- read-only semua report
);

CREATE TABLE IF NOT EXISTS app_user (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    username        VARCHAR(50) NOT NULL UNIQUE,
    email           VARCHAR(200) NOT NULL UNIQUE,
    full_name       VARCHAR(200) NOT NULL,
    hashed_password TEXT        NOT NULL,
    role            user_role   NOT NULL DEFAULT 'viewer',
    -- Scope akses: NULL = akses semua entity (hanya superadmin)
    entity_id       UUID        REFERENCES entity(id) ON DELETE SET NULL,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS refresh_token (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID        NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    token_hash      TEXT        NOT NULL UNIQUE,   -- SHA-256 dari token
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked         BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_app_user_username  ON app_user(username);
CREATE INDEX IF NOT EXISTS idx_app_user_email     ON app_user(email);
CREATE INDEX IF NOT EXISTS idx_app_user_entity    ON app_user(entity_id);
CREATE INDEX IF NOT EXISTS idx_refresh_token_user ON refresh_token(user_id);

-- Grant akses ke application user (sesuaikan nama jika berbeda)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'accounting_user') THEN
        GRANT ALL ON TABLE app_user      TO accounting_user;
        GRANT ALL ON TABLE refresh_token TO accounting_user;
    END IF;
END$$;

SELECT 'User schema created' AS status;
