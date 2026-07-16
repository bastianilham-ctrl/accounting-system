-- ============================================================
-- SCHEMA: Bank Reconciliation
--
-- Konsep:
--   Bank statement (rekening koran) diimport per baris.
--   Setiap baris dicocokkan (match) ke GL entry pada akun bank yang sama.
--   Selisih yang tidak bisa di-match (biaya bank, bunga, cek beredar)
--   dicatat sebagai adjustment entry.
--
-- Alur:
--   1. Import/input bank statement lines
--   2. Jalankan auto-match (by amount + date + reference)
--   3. Manual match untuk sisa yang belum cocok
--   4. Buat adjustment entry untuk selisih
--   5. Finalisasi → lock session → laporan rekonsiliasi
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_bank_reconciliation.sql
-- Dependensi: schema_journal_engine.sql (gl_journal, gl_line, chart_of_accounts)
-- ============================================================

-- ── 1. BANK ACCOUNT MASTER ──────────────────────────────────────────────────
-- (sudah ada di schema bank sebelumnya, cukup tambahkan kolom jika belum ada)
ALTER TABLE bank_account ADD COLUMN IF NOT EXISTS gl_account_code VARCHAR(50);
ALTER TABLE bank_account ADD COLUMN IF NOT EXISTS opening_balance NUMERIC(18,2) NOT NULL DEFAULT 0;
ALTER TABLE bank_account ADD COLUMN IF NOT EXISTS opening_balance_date DATE;


-- ── 2. BANK STATEMENT IMPORT ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bank_statement (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    bank_account_id UUID         NOT NULL REFERENCES bank_account(id),
    statement_period_year  SMALLINT NOT NULL,
    statement_period_month SMALLINT NOT NULL CHECK (statement_period_month BETWEEN 1 AND 12),
    statement_date  DATE         NOT NULL,          -- tanggal cetak rekening koran
    opening_balance NUMERIC(18,2) NOT NULL DEFAULT 0,
    closing_balance NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_debit     NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_credit    NUMERIC(18,2) NOT NULL DEFAULT 0,
    source          VARCHAR(20)   NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual','csv_import','api')),
    status          VARCHAR(20)   NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','in_progress','reconciled','locked')),
    imported_by     VARCHAR(200),
    imported_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    UNIQUE (bank_account_id, statement_period_year, statement_period_month)
);
CREATE INDEX IF NOT EXISTS idx_bstmt_account ON bank_statement(bank_account_id, status);


-- ── 3. BANK STATEMENT LINE ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bank_statement_line (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    statement_id    UUID         NOT NULL REFERENCES bank_statement(id) ON DELETE CASCADE,
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    bank_account_id UUID         NOT NULL REFERENCES bank_account(id),
    line_no         SMALLINT     NOT NULL,
    transaction_date DATE        NOT NULL,
    value_date      DATE,
    description     TEXT         NOT NULL,
    reference_no    VARCHAR(200),
    debit_amount    NUMERIC(18,2) NOT NULL DEFAULT 0,  -- uang keluar dari rekening
    credit_amount   NUMERIC(18,2) NOT NULL DEFAULT 0,  -- uang masuk ke rekening
    running_balance NUMERIC(18,2),
    match_status    VARCHAR(20)   NOT NULL DEFAULT 'unmatched'
        CHECK (match_status IN (
            'unmatched',    -- belum dicocokkan
            'matched',      -- sudah cocok dengan GL
            'suggested',    -- ada kandidat auto-match, menunggu konfirmasi
            'adjusted'      -- dicatat sebagai adjustment (biaya bank, bunga, dll)
        )),
    UNIQUE (statement_id, line_no)
);
CREATE INDEX IF NOT EXISTS idx_bsl_statement ON bank_statement_line(statement_id, match_status);
CREATE INDEX IF NOT EXISTS idx_bsl_date      ON bank_statement_line(entity_id, transaction_date);


-- ── 4. GL RECONCILIATION ENTRY — GL entries yang bisa direkonsiliasi ─────────
-- Ini adalah VIEW terhadap gl_line (tidak perlu tabel baru)
-- Tapi kita butuh tabel untuk menandai gl_line mana yang sudah di-match
CREATE TABLE IF NOT EXISTS gl_recon_status (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    gl_line_id      UUID         NOT NULL REFERENCES gl_line(id),
    bank_account_id UUID         NOT NULL REFERENCES bank_account(id),
    match_status    VARCHAR(20)   NOT NULL DEFAULT 'unmatched'
        CHECK (match_status IN ('unmatched','matched','adjusted')),
    statement_id    UUID         REFERENCES bank_statement(id),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (gl_line_id)
);
CREATE INDEX IF NOT EXISTS idx_glrecon_account ON gl_recon_status(bank_account_id, match_status);


-- ── 5. RECONCILIATION MATCH — pasangan bank line ↔ GL entry ─────────────────
CREATE TABLE IF NOT EXISTS recon_match (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    statement_id        UUID         NOT NULL REFERENCES bank_statement(id) ON DELETE CASCADE,
    bank_line_id        UUID         NOT NULL REFERENCES bank_statement_line(id),
    gl_line_id          UUID         NOT NULL REFERENCES gl_line(id),
    match_type          VARCHAR(20)  NOT NULL DEFAULT 'manual'
        CHECK (match_type IN ('auto','manual','suggested_confirmed')),
    amount              NUMERIC(18,2) NOT NULL,
    difference          NUMERIC(18,2) NOT NULL DEFAULT 0,  -- selisih kecil (pembulatan)
    matched_by          VARCHAR(200),
    matched_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (bank_line_id, gl_line_id)
);


-- ── 6. RECONCILIATION ADJUSTMENT — item hanya di bank / hanya di GL ─────────
CREATE TABLE IF NOT EXISTS recon_adjustment (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    statement_id    UUID         NOT NULL REFERENCES bank_statement(id) ON DELETE CASCADE,
    adjustment_type VARCHAR(20)  NOT NULL
        CHECK (adjustment_type IN (
            'bank_only',    -- ada di bank tapi belum di GL (biaya admin, bunga kredit)
            'gl_only',      -- ada di GL tapi belum di bank (cek beredar, transfer in-transit)
            'timing_diff'   -- beda waktu (sudah dicatat GL belum muncul di bank)
        )),
    source          VARCHAR(20)  NOT NULL DEFAULT 'bank_line'
        CHECK (source IN ('bank_line','gl_line')),
    bank_line_id    UUID         REFERENCES bank_statement_line(id),
    gl_line_id      UUID         REFERENCES gl_line(id),
    description     TEXT         NOT NULL,
    amount          NUMERIC(18,2) NOT NULL,
    -- Jurnal penyesuaian yang dibuat untuk bank_only items
    adjustment_journal_id UUID   REFERENCES gl_journal(id),
    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);


-- ── 7. VIEWS ─────────────────────────────────────────────────────────────────

-- Baris bank statement yang belum dicocokkan
CREATE OR REPLACE VIEW vw_unmatched_bank_lines AS
SELECT
    bsl.id, bsl.statement_id, bsl.entity_id, bsl.bank_account_id,
    bsl.line_no, bsl.transaction_date, bsl.description, bsl.reference_no,
    bsl.debit_amount, bsl.credit_amount, bsl.running_balance,
    bsl.match_status,
    bs.statement_period_year, bs.statement_period_month,
    ba.account_name AS bank_account_name, ba.account_number
FROM bank_statement_line bsl
JOIN bank_statement bs ON bs.id = bsl.statement_id
JOIN bank_account ba   ON ba.id = bsl.bank_account_id
WHERE bsl.match_status IN ('unmatched','suggested');


-- GL entries yang belum direkonsiliasi (akun bank)
CREATE OR REPLACE VIEW vw_unmatched_gl_entries AS
SELECT
    gl.id AS gl_line_id,
    gj.entity_id, gj.journal_date, gj.description AS journal_desc,
    gj.reference_no, gj.journal_type,
    coa.account_code, coa.account_name,
    gl.debit_idr, gl.credit_idr,
    COALESCE(grs.match_status, 'unmatched') AS match_status
FROM gl_line gl
JOIN gl_journal gj          ON gj.id = gl.journal_id AND gj.status = 'posted'
JOIN chart_of_accounts coa  ON coa.id = gl.account_id
LEFT JOIN gl_recon_status grs ON grs.gl_line_id = gl.id
WHERE coa.account_type = 'asset'
  AND coa.account_name ILIKE '%bank%'
  AND COALESCE(grs.match_status, 'unmatched') = 'unmatched';


-- Ringkasan rekonsiliasi per statement
CREATE OR REPLACE VIEW vw_recon_summary AS
SELECT
    bs.id AS statement_id,
    bs.entity_id,
    bs.bank_account_id,
    ba.account_name AS bank_account_name,
    ba.account_number,
    bs.statement_period_year,
    bs.statement_period_month,
    bs.opening_balance,
    bs.closing_balance,
    bs.status,
    COUNT(bsl.id)                                                    AS total_lines,
    COUNT(bsl.id) FILTER (WHERE bsl.match_status = 'matched')        AS matched_lines,
    COUNT(bsl.id) FILTER (WHERE bsl.match_status = 'unmatched')      AS unmatched_lines,
    COUNT(bsl.id) FILTER (WHERE bsl.match_status = 'adjusted')       AS adjusted_lines,
    COALESCE(SUM(bsl.credit_amount) FILTER (WHERE bsl.match_status = 'matched'), 0) AS matched_credits,
    COALESCE(SUM(bsl.debit_amount)  FILTER (WHERE bsl.match_status = 'matched'), 0) AS matched_debits,
    ROUND(
        100.0 * COUNT(bsl.id) FILTER (WHERE bsl.match_status IN ('matched','adjusted'))
        / NULLIF(COUNT(bsl.id), 0), 1
    )                                                                AS completion_pct
FROM bank_statement bs
JOIN bank_account ba ON ba.id = bs.bank_account_id
LEFT JOIN bank_statement_line bsl ON bsl.statement_id = bs.id
GROUP BY bs.id, ba.account_name, ba.account_number;


SELECT 'Migration schema_bank_reconciliation selesai' AS status;
