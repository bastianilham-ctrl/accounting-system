-- ============================================================
-- SCHEMA: Cash & Bank — Cash Transactions, Petty Cash, In-house Transfer
--
-- Catatan: bank account master, bank statement import, dan bank
-- reconciliation SUDAH ADA (schema_journal_engine.sql + schema_bank_reconciliation.sql).
-- File ini HANYA menambah 3 hal yang belum ada: kas tunai non-AP/AR,
-- kas kecil (petty cash, sistem imprest), dan transfer antar rekening internal.
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_cash_bank.sql
-- Dependensi: schema_journal_engine.sql (gl_journal, bank_account, chart_of_accounts),
--             schema_costing.sql (project)
-- ============================================================

ALTER TYPE journal_type ADD VALUE IF NOT EXISTS 'CASH';

-- ── 1. CASH ACCOUNT MASTER (kas tunai & kas kecil, di luar bank_account) ───────
CREATE TABLE IF NOT EXISTS cash_account (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID NOT NULL REFERENCES entity(id),
    account_name    VARCHAR(150) NOT NULL,
    account_type    VARCHAR(20)  NOT NULL DEFAULT 'cash' CHECK (account_type IN ('cash','petty_cash')),
    coa_id          UUID NOT NULL REFERENCES chart_of_accounts(id),
    custodian_name  VARCHAR(150),
    float_amount    NUMERIC(18,2) NOT NULL DEFAULT 0,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── 2. CASH TRANSACTION (kas/bank masuk/keluar umum, bukan AP/AR) ─────────────
-- account_type/account_id: polymorphic, sama seperti in_house_transfer — bisa
-- merujuk ke cash_account ATAU bank_account (mis. angsuran bank, bunga,
-- bank charge, WHT bunga — yang tidak lewat vendor invoice).
CREATE TABLE IF NOT EXISTS cash_transaction (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id        UUID NOT NULL REFERENCES entity(id),
    account_type     VARCHAR(10) NOT NULL DEFAULT 'cash' CHECK (account_type IN ('bank','cash')),
    account_id       UUID NOT NULL,
    transaction_no   VARCHAR(50) NOT NULL,
    transaction_date DATE NOT NULL,
    direction        VARCHAR(10) NOT NULL CHECK (direction IN ('in','out')),
    description      TEXT,
    amount           NUMERIC(18,2) NOT NULL DEFAULT 0,
    currency         VARCHAR(5)  NOT NULL DEFAULT 'IDR',
    exchange_rate    NUMERIC(20,6) NOT NULL DEFAULT 1,
    amount_fcy       NUMERIC(18,2),
    status           VARCHAR(20) NOT NULL DEFAULT 'draft',
    journal_id       UUID REFERENCES gl_journal(id),
    created_by       VARCHAR(100) NOT NULL DEFAULT 'system',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (entity_id, transaction_no)
);

CREATE TABLE IF NOT EXISTS cash_transaction_line (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    cash_transaction_id  UUID NOT NULL REFERENCES cash_transaction(id) ON DELETE CASCADE,
    line_no              SMALLINT NOT NULL,
    account_code         VARCHAR(20) NOT NULL,
    description          TEXT,
    amount               NUMERIC(18,2) NOT NULL DEFAULT 0,
    amount_fcy           NUMERIC(18,2),
    cost_center          VARCHAR(100),
    project_id           UUID REFERENCES project(id),
    UNIQUE (cash_transaction_id, line_no)
);

-- ── 3. PETTY CASH EXPENSE (sistem imprest) ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS petty_cash_expense (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID NOT NULL REFERENCES entity(id),
    cash_account_id UUID NOT NULL REFERENCES cash_account(id),
    expense_date    DATE NOT NULL,
    account_code    VARCHAR(20) NOT NULL,
    description     TEXT,
    amount          NUMERIC(18,2) NOT NULL DEFAULT 0,
    currency        VARCHAR(5)  NOT NULL DEFAULT 'IDR',
    exchange_rate   NUMERIC(20,6) NOT NULL DEFAULT 1,
    amount_fcy      NUMERIC(18,2),
    cost_center     VARCHAR(100),
    project_id      UUID REFERENCES project(id),
    receipt_ref     VARCHAR(100),
    status          VARCHAR(20) NOT NULL DEFAULT 'draft',
    journal_id      UUID REFERENCES gl_journal(id),
    replenished     BOOLEAN NOT NULL DEFAULT FALSE,
    created_by      VARCHAR(100) NOT NULL DEFAULT 'system',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── 4. IN-HOUSE TRANSFER (antar bank_account dan/atau cash_account) ────────────
CREATE TABLE IF NOT EXISTS in_house_transfer (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id     UUID NOT NULL REFERENCES entity(id),
    transfer_no   VARCHAR(50) NOT NULL,
    transfer_date DATE NOT NULL,
    source_type   VARCHAR(10) NOT NULL CHECK (source_type IN ('bank','cash')),
    source_id     UUID NOT NULL,
    dest_type     VARCHAR(10) NOT NULL CHECK (dest_type IN ('bank','cash')),
    dest_id       UUID NOT NULL,
    amount        NUMERIC(18,2) NOT NULL DEFAULT 0,
    currency      VARCHAR(5)  NOT NULL DEFAULT 'IDR',
    exchange_rate NUMERIC(20,6) NOT NULL DEFAULT 1,
    amount_fcy    NUMERIC(18,2),
    purpose       VARCHAR(30) NOT NULL DEFAULT 'transfer' CHECK (purpose IN ('transfer','petty_cash_topup')),
    description   TEXT,
    status        VARCHAR(20) NOT NULL DEFAULT 'draft',
    journal_id    UUID REFERENCES gl_journal(id),
    created_by    VARCHAR(100) NOT NULL DEFAULT 'system',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (entity_id, transfer_no)
);

CREATE INDEX IF NOT EXISTS idx_cash_txn_account ON cash_transaction(account_type, account_id);
CREATE INDEX IF NOT EXISTS idx_cash_txn_line_txn ON cash_transaction_line(cash_transaction_id);
CREATE INDEX IF NOT EXISTS idx_petty_cash_expense_account ON petty_cash_expense(cash_account_id);
CREATE INDEX IF NOT EXISTS idx_in_house_transfer_source ON in_house_transfer(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_in_house_transfer_dest ON in_house_transfer(dest_type, dest_id);
