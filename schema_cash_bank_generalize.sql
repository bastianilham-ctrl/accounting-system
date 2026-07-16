-- ============================================================
-- MIGRATION: Generalize cash_transaction to support bank_account too
--
-- Sebelumnya cash_transaction.cash_account_id hanya bisa merujuk ke
-- cash_account (kas tunai/kas kecil) — tidak bisa untuk transaksi non-invoice
-- di sisi bank (angsuran bank, bunga, bank charge, WHT bunga, dll).
-- Disamakan dengan pola polymorphic yang sudah dipakai in_house_transfer
-- (account_type 'bank'|'cash' + account_id).
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_cash_bank_generalize.sql
-- ============================================================

ALTER TABLE cash_transaction ADD COLUMN IF NOT EXISTS account_type VARCHAR(10) NOT NULL DEFAULT 'cash'
    CHECK (account_type IN ('bank', 'cash'));
ALTER TABLE cash_transaction ADD COLUMN IF NOT EXISTS account_id UUID;

UPDATE cash_transaction SET account_id = cash_account_id WHERE account_id IS NULL;

ALTER TABLE cash_transaction ALTER COLUMN account_id SET NOT NULL;
ALTER TABLE cash_transaction DROP COLUMN cash_account_id;

CREATE INDEX IF NOT EXISTS idx_cash_txn_account ON cash_transaction(account_type, account_id);

SELECT 'Migration schema_cash_bank_generalize selesai' AS status;
