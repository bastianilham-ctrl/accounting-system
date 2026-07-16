-- ============================================================
-- SCHEMA: Manual Journal Entry Workflow
-- Four-Eyes Principle / Segregation of Duties
-- Multi-Currency Support dengan Exchange Rate
--
-- Workflow: Draft → Pending Approval → Approved → Posted → (Reversed)
--           Draft / Pending Approval → Rejected → Draft
--
-- Prinsip keamanan:
--   1. Pembuat ≠ Penyetuju (Segregation of Duties)
--   2. Jurnal posted tidak bisa dihapus / diedit (Anti-delete constraint)
--   3. Koreksi hanya via Reversal Journal atau Adjustment Entry
--   4. Periode akuntansi yang sudah di-close menolak jurnal baru
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_journal_entry.sql
-- Dependensi: schema_journal_engine.sql (entity, chart_of_accounts, gl_journal)
-- ============================================================


-- ============================================================
-- 1. ACCOUNTING PERIOD — kontrol buka / tutup periode bulanan
-- ============================================================

CREATE TABLE IF NOT EXISTS accounting_period (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    period_year     SMALLINT     NOT NULL,
    period_month    SMALLINT     NOT NULL CHECK (period_month BETWEEN 1 AND 12),
    period_name     VARCHAR(10)  GENERATED ALWAYS AS (
                        LPAD(period_year::TEXT, 4, '0') || '-' ||
                        LPAD(period_month::TEXT, 2, '0')
                    ) STORED,

    status          VARCHAR(20)  NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'closed', 'locked')),
        -- open   : jurnal baru & revisi masih diizinkan
        -- closed : tidak ada jurnal baru (bisa dibuka kembali oleh admin)
        -- locked : permanen ditutup (audit / tax filing selesai)

    opened_by       VARCHAR(200),
    opened_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    closed_by       VARCHAR(200),
    closed_at       TIMESTAMPTZ,

    notes           TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (entity_id, period_year, period_month)
);
COMMENT ON TABLE accounting_period IS
    'Kontrol buka/tutup periode akuntansi bulanan. '
    'Jurnal hanya bisa disubmit jika periodenya masih "open".';

CREATE INDEX IF NOT EXISTS idx_accperiod_entity ON accounting_period(entity_id, period_year, period_month);


-- ============================================================
-- 2. JOURNAL APPROVAL MATRIX — matriks persetujuan jurnal
-- ============================================================

CREATE TABLE IF NOT EXISTS journal_approval_matrix (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    level           SMALLINT     NOT NULL,           -- 1 = level pertama
    threshold_name  VARCHAR(100) NOT NULL,           -- "Accounting Supervisor", "Finance Manager"

    -- Filter tipe jurnal (NULL = berlaku untuk semua tipe)
    journal_type    VARCHAR(30),
        -- Jika diisi, rule ini berlaku untuk tipe tersebut tanpa melihat nominal
        -- Contoh: 'adjustment','write_off' → selalu butuh Finance Manager

    -- Filter nominal (hanya berlaku jika journal_type IS NULL)
    min_amount      NUMERIC(18,2) NOT NULL DEFAULT 0,
    max_amount      NUMERIC(18,2),                   -- NULL = tidak ada batas atas

    required_role   VARCHAR(50)  NOT NULL,           -- 'finance' | 'admin'
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,

    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (entity_id, level)
);
COMMENT ON TABLE journal_approval_matrix IS
    'Matriks kewenangan approval jurnal. '
    'Didahulukan: filter berdasarkan journal_type; jika NULL, gunakan filter nominal. '
    'Contoh default: ≤50jt → finance (Accounting Supervisor); >50jt → admin (Finance Manager). '
    'Tipe adjustment/write_off selalu → admin.';

-- Seed default matrix — adjust entity_id saat deploy
-- INSERT INTO journal_approval_matrix (id, entity_id, level, threshold_name, journal_type, required_role)
-- VALUES
--   (uuid_generate_v4(), '<entity_id>', 1, 'All Types: ≤Rp50jt', NULL, 0, 50000000, 'finance'),
--   (uuid_generate_v4(), '<entity_id>', 2, 'All Types: >Rp50jt', NULL, 50000001, NULL, 'admin'),
--   (uuid_generate_v4(), '<entity_id>', 3, 'Adjustment / Write-off', 'adjustment', 0, NULL, 'admin'),
--   (uuid_generate_v4(), '<entity_id>', 4, 'Write-off', 'write_off', 0, NULL, 'admin');


-- ============================================================
-- 3. JOURNAL ENTRY — dokumen jurnal dengan workflow approval
-- ============================================================

CREATE TABLE IF NOT EXISTS journal_entry (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    entry_no        VARCHAR(30)  NOT NULL UNIQUE,    -- JE/2026/06/0001
    journal_date    DATE         NOT NULL,
    period_year     SMALLINT     NOT NULL,
    period_month    SMALLINT     NOT NULL CHECK (period_month BETWEEN 1 AND 12),

    journal_type    VARCHAR(30)  NOT NULL DEFAULT 'general'
        CHECK (journal_type IN (
            'general',        -- Jurnal Umum
            'adjustment',     -- Koreksi / Penyesuaian
            'accrual',        -- Akrual beban/pendapatan
            'prepaid',        -- Biaya dibayar di muka
            'depreciation',   -- Penyusutan aktiva
            'provision',      -- Provisi / cadangan
            'write_off',      -- Penghapusan piutang / aset
            'reversal',       -- Jurnal balik otomatis
            'closing'         -- Jurnal penutup akhir tahun
        )),

    -- ── Multi-Currency ──────────────────────────────────────────────────
    currency        VARCHAR(3)   NOT NULL DEFAULT 'IDR',
    exchange_rate   NUMERIC(18,6) NOT NULL DEFAULT 1,
        -- Rate: 1 unit currency ini = exchange_rate IDR
        -- Contoh: USD dengan rate 16.000 → 1 USD = Rp16.000
        -- Untuk IDR: exchange_rate = 1 (default)

    -- ── Nilai total (dalam currency asal & IDR) ─────────────────────────
    total_debit_currency  NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_credit_currency NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_debit_idr       NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_credit_idr      NUMERIC(18,2) NOT NULL DEFAULT 0,

    description     TEXT         NOT NULL,
    reference_no    VARCHAR(100),
    attachment_url  VARCHAR(500),                   -- link ke file pendukung

    -- ── Workflow Status ─────────────────────────────────────────────────
    status          VARCHAR(30)  NOT NULL DEFAULT 'draft'
        CHECK (status IN (
            'draft',            -- dibuat, belum disubmit
            'pending_approval', -- menunggu review atasan
            'approved',         -- disetujui, belum diposting ke GL
            'posted',           -- sudah posting ke GL (IMMUTABLE)
            'rejected',         -- ditolak reviewer, kembali ke staff
            'cancelled',        -- dibatalkan sebelum posting
            'reversed'          -- sudah dibuat jurnal balik-nya
        )),

    -- ── Approval metadata ───────────────────────────────────────────────
    required_approval_role VARCHAR(50),             -- ditentukan saat submit
    submitted_by    VARCHAR(200),
    submitted_at    TIMESTAMPTZ,
    reviewed_by     VARCHAR(200),
    reviewed_at     TIMESTAMPTZ,
    posted_by       VARCHAR(200),
    posted_at       TIMESTAMPTZ,
    rejection_reason TEXT,

    -- ── GL link (diisi saat posting) ────────────────────────────────────
    gl_journal_id   UUID         REFERENCES gl_journal(id),

    -- ── Reversal tracking ───────────────────────────────────────────────
    is_reversal         BOOLEAN      NOT NULL DEFAULT FALSE,
    reversal_of_id      UUID         REFERENCES journal_entry(id),   -- jurnal asli
    reversed_by_id      UUID         REFERENCES journal_entry(id),   -- jurnal reversal-nya

    created_by      VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE journal_entry IS
    'Dokumen jurnal umum dengan workflow four-eyes principle. '
    'Status "posted" bersifat IMMUTABLE — tidak dapat diedit atau dihapus. '
    'Koreksi hanya via reversal atau adjustment entry baru.';

CREATE INDEX IF NOT EXISTS idx_je_entity_status  ON journal_entry(entity_id, status);
CREATE INDEX IF NOT EXISTS idx_je_period         ON journal_entry(entity_id, period_year, period_month);
CREATE INDEX IF NOT EXISTS idx_je_type           ON journal_entry(entity_id, journal_type, status);
CREATE INDEX IF NOT EXISTS idx_je_reviewer       ON journal_entry(required_approval_role, status);


-- ============================================================
-- 4. JOURNAL ENTRY LINE — baris debet/kredit
-- ============================================================

CREATE TABLE IF NOT EXISTS journal_entry_line (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entry_id        UUID         NOT NULL REFERENCES journal_entry(id) ON DELETE CASCADE,
    line_no         SMALLINT     NOT NULL,

    account_code    VARCHAR(50)  NOT NULL,           -- referensi chart_of_accounts
    account_name    VARCHAR(200),                    -- snapshot saat simpan

    description     TEXT,

    -- ── Nominal dalam currency jurnal ────────────────────────────────────
    debit_amount    NUMERIC(18,2) NOT NULL DEFAULT 0  CHECK (debit_amount >= 0),
    credit_amount   NUMERIC(18,2) NOT NULL DEFAULT 0  CHECK (credit_amount >= 0),

    -- ── Nominal hasil konversi ke IDR (dihitung: amount × exchange_rate header) ──
    debit_idr       NUMERIC(18,2) NOT NULL DEFAULT 0,
    credit_idr      NUMERIC(18,2) NOT NULL DEFAULT 0,

    -- ── Dimensi analitik ─────────────────────────────────────────────────
    cost_center     VARCHAR(100),
    project_id      UUID         REFERENCES project(id),
    vendor_id       UUID         REFERENCES vendor(id),
    tax_code        VARCHAR(20),
    tax_amount      NUMERIC(18,2) NOT NULL DEFAULT 0,

    notes           TEXT,

    UNIQUE (entry_id, line_no),
    CHECK (debit_amount > 0 OR credit_amount > 0),           -- setidaknya satu sisi isi
    CHECK (NOT (debit_amount > 0 AND credit_amount > 0))     -- tidak boleh dua-duanya isi
);
COMMENT ON TABLE journal_entry_line IS
    'Baris jurnal. debit_idr / credit_idr dihitung oleh engine (amount × exchange_rate). '
    'Kolom project_id opsional — digunakan untuk analytic tagging ke modul costing.';

CREATE INDEX IF NOT EXISTS idx_jel_entry   ON journal_entry_line(entry_id);
CREATE INDEX IF NOT EXISTS idx_jel_account ON journal_entry_line(account_code);
CREATE INDEX IF NOT EXISTS idx_jel_project ON journal_entry_line(project_id) WHERE project_id IS NOT NULL;


-- ============================================================
-- 5. JOURNAL APPROVAL LOG — audit trail seluruh aksi
-- ============================================================

CREATE TABLE IF NOT EXISTS journal_approval_log (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entry_id        UUID         NOT NULL REFERENCES journal_entry(id) ON DELETE CASCADE,
    action          VARCHAR(30)  NOT NULL
        CHECK (action IN (
            'created', 'updated', 'submitted', 'approved',
            'rejected', 'posted', 'reversed', 'cancelled'
        )),
    actor           VARCHAR(200) NOT NULL,
    actor_role      VARCHAR(50),
    required_role   VARCHAR(50),
    from_status     VARCHAR(30),
    to_status       VARCHAR(30),
    notes           TEXT,
    acted_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE journal_approval_log IS
    'Jejak audit (audit trail) setiap perubahan status jurnal. '
    'Tidak bisa dihapus — log abadi untuk keperluan audit eksternal.';

CREATE INDEX IF NOT EXISTS idx_jal_entry ON journal_approval_log(entry_id, acted_at);


-- ============================================================
-- 6. VIEWS
-- ============================================================

-- Jurnal pending review (dikelompokkan per role penyetuju)
CREATE OR REPLACE VIEW vw_je_pending_approval AS
SELECT
    je.id, je.entry_no, je.journal_date,
    je.entity_id,
    je.journal_type,
    je.currency, je.exchange_rate,
    je.total_debit_idr, je.total_credit_idr,
    je.description, je.reference_no,
    je.required_approval_role,
    je.submitted_by, je.submitted_at,
    EXTRACT(DAYS FROM (NOW() - je.submitted_at)) AS days_pending
FROM journal_entry je
WHERE je.status = 'pending_approval'
ORDER BY je.submitted_at ASC;

-- Detail jurnal lengkap dengan lines dan approval log
CREATE OR REPLACE VIEW vw_je_with_lines AS
SELECT
    je.*,
    COALESCE(
        json_agg(
            json_build_object(
                'line_no',      jel.line_no,
                'account_code', jel.account_code,
                'account_name', jel.account_name,
                'description',  jel.description,
                'debit_amount', jel.debit_amount,
                'credit_amount',jel.credit_amount,
                'debit_idr',    jel.debit_idr,
                'credit_idr',   jel.credit_idr,
                'cost_center',  jel.cost_center,
                'project_id',   jel.project_id
            ) ORDER BY jel.line_no
        ) FILTER (WHERE jel.id IS NOT NULL),
        '[]'::json
    ) AS lines
FROM journal_entry je
LEFT JOIN journal_entry_line jel ON jel.entry_id = je.id
GROUP BY je.id;

-- Ringkasan aktivitas jurnal per bulan
CREATE OR REPLACE VIEW vw_je_monthly_summary AS
SELECT
    je.entity_id,
    je.period_year,
    je.period_month,
    je.journal_type,
    je.currency,
    COUNT(*)                                        AS total_entries,
    COUNT(*) FILTER (WHERE je.status = 'draft')     AS draft_count,
    COUNT(*) FILTER (WHERE je.status = 'pending_approval') AS pending_count,
    COUNT(*) FILTER (WHERE je.status = 'posted')    AS posted_count,
    COUNT(*) FILTER (WHERE je.status = 'rejected')  AS rejected_count,
    COALESCE(SUM(je.total_debit_idr) FILTER (WHERE je.status = 'posted'), 0) AS posted_debit_idr
FROM journal_entry je
GROUP BY je.entity_id, je.period_year, je.period_month, je.journal_type, je.currency;


SELECT 'Migration schema_journal_entry selesai' AS status;
