-- ============================================================
-- SCHEMA: Expense Claim & Reimbursement
--
-- Konsep:
--   Karyawan mengajukan klaim biaya operasional (perjalanan, entertain, supplies, dll).
--   Manager menyetujui / reject.
--   Finance memverifikasi receipt + menetapkan akun GL.
--   Pembayaran reimbursement (ke karyawan via payroll/transfer langsung).
--   Jika biaya bisa ditagihkan ke proyek/klien → passthrough billable.
--   Setiap approve/pay → posting GL otomatis.
--
-- Alur:
--   Draft → Submitted → Approved/Rejected → Verified → Paid
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_expense_claim.sql
-- Dependensi: schema_hr.sql (employee), schema_journal_engine.sql (gl_journal, gl_line)
--             schema_project_setup.sql (project)
-- ============================================================

-- ── 1. EXPENSE CATEGORY ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS expense_category (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    category_code   VARCHAR(20)  NOT NULL,
    category_name   VARCHAR(200) NOT NULL,
    expense_type    VARCHAR(30)  NOT NULL DEFAULT 'operational'
        CHECK (expense_type IN (
            'travel',           -- perjalanan dinas
            'accommodation',    -- hotel/penginapan
            'meal',             -- makan/entertainment
            'transport',        -- transport lokal
            'communication',    -- pulsa/internet
            'office_supplies',  -- ATK
            'training',         -- pelatihan/seminar
            'medical',          -- pengobatan
            'representation',   -- biaya representasi
            'operational'       -- lainnya
        )),
    gl_account_code VARCHAR(50),   -- akun biaya default untuk kategori ini
    max_amount      NUMERIC(18,2), -- batas maksimal per klaim (NULL = tidak ada batas)
    requires_receipt BOOLEAN NOT NULL DEFAULT TRUE,
    is_billable_default BOOLEAN NOT NULL DEFAULT FALSE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (entity_id, category_code)
);

-- Seed default categories
INSERT INTO expense_category (entity_id, category_code, category_name, expense_type, requires_receipt, is_billable_default)
SELECT e.id, 'TRAVEL',    'Perjalanan Dinas',    'travel',         TRUE,  TRUE
FROM entity e WHERE NOT EXISTS (SELECT 1 FROM expense_category ec WHERE ec.entity_id=e.id AND ec.category_code='TRAVEL')
UNION ALL
SELECT e.id, 'HOTEL',     'Akomodasi/Hotel',     'accommodation',  TRUE,  TRUE
FROM entity e WHERE NOT EXISTS (SELECT 1 FROM expense_category ec WHERE ec.entity_id=e.id AND ec.category_code='HOTEL')
UNION ALL
SELECT e.id, 'MEAL',      'Makan/Entertainment', 'meal',           TRUE,  FALSE
FROM entity e WHERE NOT EXISTS (SELECT 1 FROM expense_category ec WHERE ec.entity_id=e.id AND ec.category_code='MEAL')
UNION ALL
SELECT e.id, 'TRANSPORT', 'Transportasi Lokal',  'transport',      FALSE, FALSE
FROM entity e WHERE NOT EXISTS (SELECT 1 FROM expense_category ec WHERE ec.entity_id=e.id AND ec.category_code='TRANSPORT')
UNION ALL
SELECT e.id, 'SUPPLIES',  'Alat Tulis Kantor',   'office_supplies',TRUE,  FALSE
FROM entity e WHERE NOT EXISTS (SELECT 1 FROM expense_category ec WHERE ec.entity_id=e.id AND ec.category_code='SUPPLIES')
UNION ALL
SELECT e.id, 'TRAINING',  'Pelatihan/Seminar',   'training',       TRUE,  FALSE
FROM entity e WHERE NOT EXISTS (SELECT 1 FROM expense_category ec WHERE ec.entity_id=e.id AND ec.category_code='TRAINING');


-- ── 2. EXPENSE CLAIM (header) ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS expense_claim (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    claim_no        VARCHAR(50)  NOT NULL UNIQUE,
    employee_id     UUID         NOT NULL REFERENCES employee(id),
    project_id      UUID         REFERENCES project(id),         -- opsional, untuk billable
    cost_center_id  UUID         REFERENCES cost_center(id),
    claim_date      DATE         NOT NULL,
    period_from     DATE         NOT NULL,
    period_to       DATE         NOT NULL,
    purpose         TEXT         NOT NULL,
    total_amount    NUMERIC(18,2) NOT NULL DEFAULT 0,            -- dihitung dari lines
    approved_amount NUMERIC(18,2) NOT NULL DEFAULT 0,            -- setelah approval
    status          VARCHAR(30)  NOT NULL DEFAULT 'draft'
        CHECK (status IN (
            'draft',        -- sedang diisi
            'submitted',    -- sudah diajukan ke manager
            'approved',     -- disetujui manager
            'rejected',     -- ditolak manager
            'verified',     -- diverifikasi finance (siap bayar)
            'paid',         -- sudah dibayarkan
            'cancelled'
        )),
    -- Approval workflow
    submitted_by    VARCHAR(200),
    submitted_at    TIMESTAMPTZ,
    approved_by     VARCHAR(200),
    approved_at     TIMESTAMPTZ,
    approval_notes  TEXT,
    verified_by     VARCHAR(200),
    verified_at     TIMESTAMPTZ,
    -- Payment
    payment_method  VARCHAR(20)  CHECK (payment_method IN ('bank_transfer','cash','payroll_deduction')),
    bank_account_id UUID         REFERENCES bank_account(id),    -- akun bank pembayaran
    paid_by         VARCHAR(200),
    paid_at         TIMESTAMPTZ,
    payment_journal_id UUID      REFERENCES gl_journal(id),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_eclaim_employee ON expense_claim(employee_id, status);
CREATE INDEX IF NOT EXISTS idx_eclaim_entity   ON expense_claim(entity_id, claim_date DESC);


-- ── 3. EXPENSE CLAIM LINE (item per baris) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS expense_claim_line (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    claim_id        UUID         NOT NULL REFERENCES expense_claim(id) ON DELETE CASCADE,
    line_no         SMALLINT     NOT NULL,
    category_id     UUID         NOT NULL REFERENCES expense_category(id),
    expense_date    DATE         NOT NULL,
    description     TEXT         NOT NULL,
    quantity        NUMERIC(10,2) NOT NULL DEFAULT 1,
    unit_amount     NUMERIC(18,2) NOT NULL,
    total_amount    NUMERIC(18,2) GENERATED ALWAYS AS (quantity * unit_amount) STORED,
    approved_amount NUMERIC(18,2),      -- bisa diubah reviewer
    gl_account_code VARCHAR(50),        -- override per baris (finance bisa adjust)
    receipt_filename VARCHAR(500),
    receipt_url     TEXT,
    is_billable     BOOLEAN NOT NULL DEFAULT FALSE,
    notes           TEXT,
    UNIQUE (claim_id, line_no)
);
CREATE INDEX IF NOT EXISTS idx_ecline_claim ON expense_claim_line(claim_id);


-- ── 4. EXPENSE ADVANCE (uang muka perjalanan) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS expense_advance (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID         NOT NULL REFERENCES entity(id),
    advance_no      VARCHAR(50)  NOT NULL UNIQUE,
    employee_id     UUID         NOT NULL REFERENCES employee(id),
    project_id      UUID         REFERENCES project(id),
    advance_date    DATE         NOT NULL,
    purpose         TEXT         NOT NULL,
    amount_requested NUMERIC(18,2) NOT NULL,
    amount_approved  NUMERIC(18,2),
    amount_disbursed NUMERIC(18,2) NOT NULL DEFAULT 0,
    amount_settled   NUMERIC(18,2) NOT NULL DEFAULT 0,       -- jumlah yang sudah dipertanggungjawabkan
    balance_due      NUMERIC(18,2) GENERATED ALWAYS AS (amount_disbursed - amount_settled) STORED,
    status          VARCHAR(30)  NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','approved','disbursed','settled','cancelled')),
    linked_claim_id  UUID        REFERENCES expense_claim(id),
    disburse_journal_id UUID     REFERENCES gl_journal(id),
    settle_journal_id   UUID     REFERENCES gl_journal(id),
    approved_by     VARCHAR(200),
    disbursed_by    VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_eadv_employee ON expense_advance(employee_id, status);


-- ── 5. VIEWS ─────────────────────────────────────────────────────────────────

-- Ringkasan klaim per karyawan
CREATE OR REPLACE VIEW vw_expense_claim_summary AS
SELECT
    ec.id AS claim_id,
    ec.entity_id,
    ec.claim_no,
    e.full_name         AS employee_name,
    e.employee_code,
    ec.claim_date,
    ec.period_from,
    ec.period_to,
    ec.purpose,
    ec.total_amount,
    ec.approved_amount,
    ec.status,
    ec.project_id,
    p.project_name,
    COUNT(ecl.id)       AS total_lines,
    COUNT(ecl.id) FILTER (WHERE ecl.is_billable) AS billable_lines,
    SUM(COALESCE(ecl.approved_amount, ecl.total_amount)) FILTER (WHERE ecl.is_billable) AS billable_amount
FROM expense_claim ec
JOIN employee e ON e.id = ec.employee_id
LEFT JOIN project p ON p.id = ec.project_id
LEFT JOIN expense_claim_line ecl ON ecl.claim_id = ec.id
GROUP BY ec.id, e.full_name, e.employee_code, p.project_name;


-- Outstanding advance per karyawan
CREATE OR REPLACE VIEW vw_advance_outstanding AS
SELECT
    ea.id AS advance_id,
    ea.entity_id,
    ea.advance_no,
    e.full_name     AS employee_name,
    e.employee_code,
    ea.advance_date,
    ea.purpose,
    ea.amount_disbursed,
    ea.amount_settled,
    ea.balance_due,
    ea.status,
    ea.project_id
FROM expense_advance ea
JOIN employee e ON e.id = ea.employee_id
WHERE ea.balance_due > 0 AND ea.status = 'disbursed';


-- Ringkasan biaya per proyek (billable passthrough)
CREATE OR REPLACE VIEW vw_project_expense_passthrough AS
SELECT
    ecl.claim_id,
    ec.entity_id,
    ec.project_id,
    p.project_name,
    p.project_code,
    cat.category_name,
    cat.expense_type,
    ecl.expense_date,
    ecl.description,
    COALESCE(ecl.approved_amount, ecl.total_amount) AS amount,
    ec.status AS claim_status
FROM expense_claim_line ecl
JOIN expense_claim ec    ON ec.id  = ecl.claim_id
JOIN expense_category cat ON cat.id = ecl.category_id
LEFT JOIN project p       ON p.id  = ec.project_id
WHERE ecl.is_billable = TRUE
  AND ec.project_id IS NOT NULL
  AND ec.status IN ('approved','verified','paid');


SELECT 'Migration schema_expense_claim selesai' AS status;
