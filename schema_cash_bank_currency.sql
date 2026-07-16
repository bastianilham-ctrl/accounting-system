-- ============================================================
-- MIGRATION: Currency / FCY fields untuk Cash & Bank module
--
-- Konsisten dengan desain schema_multicurrency.sql: GL tetap selalu
-- dicatat dalam IDR (debit_idr/credit_idr). Kolom `amount`/`amount_fcy`
-- di modul ini: `amount` = nilai IDR (sudah dikonversi, dipakai semua
-- kode lama tanpa perubahan), `amount_fcy` = nilai asli dalam mata uang
-- transaksi (kalau currency != IDR), `exchange_rate` = kurs yang dipakai.
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_cash_bank_currency.sql
-- ============================================================

ALTER TABLE cash_transaction
    ADD COLUMN IF NOT EXISTS currency      VARCHAR(5)    NOT NULL DEFAULT 'IDR',
    ADD COLUMN IF NOT EXISTS exchange_rate NUMERIC(20,6) NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS amount_fcy    NUMERIC(18,2);

ALTER TABLE cash_transaction_line
    ADD COLUMN IF NOT EXISTS amount_fcy NUMERIC(18,2);

ALTER TABLE petty_cash_expense
    ADD COLUMN IF NOT EXISTS currency      VARCHAR(5)    NOT NULL DEFAULT 'IDR',
    ADD COLUMN IF NOT EXISTS exchange_rate NUMERIC(20,6) NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS amount_fcy    NUMERIC(18,2);

ALTER TABLE in_house_transfer
    ADD COLUMN IF NOT EXISTS currency      VARCHAR(5)    NOT NULL DEFAULT 'IDR',
    ADD COLUMN IF NOT EXISTS exchange_rate NUMERIC(20,6) NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS amount_fcy    NUMERIC(18,2);

SELECT 'Migration schema_cash_bank_currency selesai' AS status;
