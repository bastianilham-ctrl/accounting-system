-- ============================================================
-- SCHEMA: Intercompany Transactions
--
-- Konsep:
--   - Dua entity saling bertransaksi (holding ↔ anak perusahaan)
--   - Setiap transaksi membuat DUA jurnal GL: satu di setiap entity
--   - Akun "Due From" (asset) di sisi piutang
--   - Akun "Due To"   (liability) di sisi hutang
--   - Saat settlement: kedua sisi ditutup via jurnal pembayaran
--   - Untuk konsolidasi: Due From = Due To (eliminasi)
--
-- Tipe transaksi:
--   charge        - A tagih B (management fee, shared service, royalty)
--   cost_recharge - A recharge biaya ke B (shared cost allocation)
--   loan          - A pinjamkan uang ke B
--   loan_repayment- B kembalikan pinjaman ke A
--   equity_injection - holding inject modal ke anak
--   dividend      - anak bayar dividen ke holding
--   cash_transfer - transfer kas antar entity (tidak ada P&L)
--
-- GL default:
--   Due From (Piutang Interco)  : 1-9100
--   Due To   (Hutang Interco)   : 2-9100
--
-- Flow:
--   1. Initiator buat transaksi (draft)
--   2. Initiator submit (→ pending_approval)
--   3. Counterparty confirm (→ approved)
--   4. Post → jurnal di KEDUA entity (→ posted)
--   5. Settlement: bayar sebagian/penuh (→ partial_settled / settled)
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_intercompany.sql
-- ============================================================

-- ── 1. INTERCOMPANY TRANSACTION ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS intercompany_transaction (
    id                      UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Pihak-pihak
    initiator_entity_id     UUID         NOT NULL REFERENCES entity(id),
    counterparty_entity_id  UUID         NOT NULL REFERENCES entity(id),

    -- Tipe & info transaksi
    transaction_type        VARCHAR(30)  NOT NULL
        CHECK (transaction_type IN (
            'charge', 'cost_recharge', 'loan', 'loan_repayment',
            'equity_injection', 'dividend', 'cash_transfer'
        )),
    transaction_date        DATE         NOT NULL,
    reference_number        VARCHAR(100),           -- nomor referensi manual (opsional)
    description             TEXT         NOT NULL,

    -- Nilai
    currency                VARCHAR(5)   NOT NULL DEFAULT 'IDR',
    amount_fcy              NUMERIC(18,2) NOT NULL,   -- nilai dalam currency asli
    exchange_rate           NUMERIC(20,6) NOT NULL DEFAULT 1,
    amount_idr              NUMERIC(18,2) GENERATED ALWAYS AS
                            (amount_fcy * exchange_rate) STORED,

    -- GL accounts — bisa dioverride per transaksi
    initiator_debit_account     VARCHAR(20),  -- akun Dr di sisi initiator
    initiator_credit_account    VARCHAR(20),  -- akun Cr di sisi initiator
    counterparty_debit_account  VARCHAR(20),  -- akun Dr di sisi counterparty
    counterparty_credit_account VARCHAR(20),  -- akun Cr di sisi counterparty

    -- Jurnal yang dihasilkan
    initiator_journal_id        UUID         REFERENCES gl_journal(id),
    counterparty_journal_id     UUID         REFERENCES gl_journal(id),

    -- Settlement
    settled_amount_idr          NUMERIC(18,2) NOT NULL DEFAULT 0,
    outstanding_amount_idr      NUMERIC(18,2) GENERATED ALWAYS AS
                                (amount_idr - settled_amount_idr) STORED,

    -- Approval & status
    status                  VARCHAR(30)  NOT NULL DEFAULT 'draft'
        CHECK (status IN (
            'draft', 'pending_approval', 'approved', 'posted',
            'partial_settled', 'settled', 'cancelled'
        )),
    initiator_submitted_by  VARCHAR(200),
    initiator_submitted_at  TIMESTAMPTZ,
    counterparty_confirmed_by VARCHAR(200),
    counterparty_confirmed_at TIMESTAMPTZ,
    posted_by               VARCHAR(200),
    posted_at               TIMESTAMPTZ,
    cancelled_by            VARCHAR(200),
    cancelled_at            TIMESTAMPTZ,
    cancel_reason           TEXT,

    -- Metadata
    fiscal_year             SMALLINT,
    fiscal_month            SMALLINT,
    tags                    TEXT[],             -- label: 'management_fee', 'royalty', dll
    notes                   TEXT,
    created_by              VARCHAR(200),
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT ict_not_same_entity CHECK (initiator_entity_id != counterparty_entity_id)
);

CREATE INDEX IF NOT EXISTS idx_ict_initiator  ON intercompany_transaction(initiator_entity_id, status);
CREATE INDEX IF NOT EXISTS idx_ict_counterpty ON intercompany_transaction(counterparty_entity_id, status);
CREATE INDEX IF NOT EXISTS idx_ict_date       ON intercompany_transaction(transaction_date DESC);
CREATE INDEX IF NOT EXISTS idx_ict_status     ON intercompany_transaction(status);


-- ── 2. INTERCOMPANY SETTLEMENT ────────────────────────────────────────────────
-- Setiap kali hutang/piutang interco dibayar, catat di sini
CREATE TABLE IF NOT EXISTS intercompany_settlement (
    id                          UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    transaction_id              UUID         NOT NULL REFERENCES intercompany_transaction(id),

    settlement_date             DATE         NOT NULL,
    amount_idr                  NUMERIC(18,2) NOT NULL,
    currency                    VARCHAR(5)   NOT NULL DEFAULT 'IDR',
    amount_fcy                  NUMERIC(18,2),
    exchange_rate               NUMERIC(20,6) DEFAULT 1,

    -- Cara pembayaran
    payment_method              VARCHAR(30)  DEFAULT 'bank_transfer'
        CHECK (payment_method IN ('bank_transfer', 'offset', 'netting')),
    initiator_bank_account_id   UUID         REFERENCES bank_account(id),
    counterparty_bank_account_id UUID        REFERENCES bank_account(id),
    bank_reference              VARCHAR(200),

    -- Jurnal settlement (dua sisi)
    initiator_journal_id        UUID         REFERENCES gl_journal(id),
    counterparty_journal_id     UUID         REFERENCES gl_journal(id),

    notes                       TEXT,
    created_by                  VARCHAR(200),
    created_at                  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ics_txn ON intercompany_settlement(transaction_id);


-- ── 3. INTERCOMPANY CONFIG PER ENTITY ─────────────────────────────────────────
-- Konfigurasi akun interco default per pasangan entity
CREATE TABLE IF NOT EXISTS intercompany_config (
    id                      UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id               UUID         NOT NULL REFERENCES entity(id),
    counterparty_entity_id  UUID         NOT NULL REFERENCES entity(id),
    due_from_account        VARCHAR(20)  NOT NULL DEFAULT '1-9100',
    due_to_account          VARCHAR(20)  NOT NULL DEFAULT '2-9100',
    default_charge_income_account VARCHAR(20),   -- akun pendapatan interco
    default_charge_expense_account VARCHAR(20),  -- akun beban interco
    is_active               BOOLEAN      NOT NULL DEFAULT TRUE,
    notes                   TEXT,
    UNIQUE (entity_id, counterparty_entity_id)
);


-- ── 4. VIEWS ─────────────────────────────────────────────────────────────────

-- Outstanding interco per entity (piutang yang belum lunas)
CREATE OR REPLACE VIEW vw_interco_outstanding AS
SELECT
    t.id,
    t.transaction_date,
    t.transaction_type,
    t.description,
    t.reference_number,

    ie.entity_name AS initiator_name,
    ce.entity_name AS counterparty_name,

    t.currency,
    t.amount_fcy,
    t.amount_idr,
    t.settled_amount_idr,
    t.outstanding_amount_idr,
    t.status,

    -- Pihak initiator: punya piutang (Due From)
    t.initiator_entity_id,
    -- Pihak counterparty: punya hutang (Due To)
    t.counterparty_entity_id

FROM intercompany_transaction t
JOIN entity ie ON ie.id = t.initiator_entity_id
JOIN entity ce ON ce.id = t.counterparty_entity_id
WHERE t.status IN ('posted', 'partial_settled')
  AND t.outstanding_amount_idr > 0;


-- Eliminasi interco: pasangkan Due From dengan Due To untuk konsolidasi
CREATE OR REPLACE VIEW vw_interco_elimination AS
SELECT
    t.initiator_entity_id   AS entity_a,
    t.counterparty_entity_id AS entity_b,
    ie.entity_name           AS entity_a_name,
    ce.entity_name           AS entity_b_name,
    t.transaction_type,
    SUM(t.amount_idr)        AS gross_amount,
    SUM(t.settled_amount_idr) AS settled,
    SUM(t.outstanding_amount_idr) AS outstanding,
    -- Untuk eliminasi: Dr 2-9100 (Due To di B) | Cr 1-9100 (Due From di A)
    COUNT(*)                 AS transaction_count
FROM intercompany_transaction t
JOIN entity ie ON ie.id = t.initiator_entity_id
JOIN entity ce ON ce.id = t.counterparty_entity_id
WHERE t.status IN ('posted', 'partial_settled', 'settled')
GROUP BY t.initiator_entity_id, t.counterparty_entity_id,
         ie.entity_name, ce.entity_name, t.transaction_type;


-- Aging piutang/hutang interco
CREATE OR REPLACE VIEW vw_interco_aging AS
SELECT
    t.initiator_entity_id,
    ie.entity_name AS creditor_name,
    t.counterparty_entity_id,
    ce.entity_name AS debtor_name,
    t.transaction_type,
    CURRENT_DATE - t.transaction_date AS age_days,
    CASE
        WHEN CURRENT_DATE - t.transaction_date <= 30  THEN '0-30'
        WHEN CURRENT_DATE - t.transaction_date <= 60  THEN '31-60'
        WHEN CURRENT_DATE - t.transaction_date <= 90  THEN '61-90'
        ELSE 'Over 90'
    END AS aging_bucket,
    t.outstanding_amount_idr
FROM intercompany_transaction t
JOIN entity ie ON ie.id = t.initiator_entity_id
JOIN entity ce ON ce.id = t.counterparty_entity_id
WHERE t.status IN ('posted', 'partial_settled')
  AND t.outstanding_amount_idr > 0;


SELECT 'Migration schema_intercompany selesai' AS status;
