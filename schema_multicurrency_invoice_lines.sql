-- ============================================================
-- SCHEMA: Multi-Currency — kolom tambahan untuk invoice line & payment/receipt
--
-- Lanjutan dari schema_multicurrency.sql. ap_invoice/ar_invoice/gl_line
-- sudah punya currency/exchange_rate/amount_fcy dari migrasi sebelumnya.
-- Ini menambah kolom yang sama untuk:
--   - ap_invoice_line / ar_invoice_line (FCY amount per baris, untuk audit/display)
--   - ap_payment / ar_receipt (FCY amount, kurs pembayaran, realized G/L per transaksi)
--
-- Jalankan: psql -U postgres -d accounting_db -f schema_multicurrency_invoice_lines.sql
-- ============================================================

ALTER TABLE ap_invoice_line
    ADD COLUMN IF NOT EXISTS amount_fcy NUMERIC(18,2);

ALTER TABLE ar_invoice_line
    ADD COLUMN IF NOT EXISTS amount_fcy NUMERIC(18,2);

ALTER TABLE ap_payment
    ADD COLUMN IF NOT EXISTS amount_fcy   NUMERIC(18,2),
    ADD COLUMN IF NOT EXISTS payment_rate NUMERIC(20,6),
    ADD COLUMN IF NOT EXISTS realized_gl  NUMERIC(18,2);

ALTER TABLE ar_receipt
    ADD COLUMN IF NOT EXISTS amount_fcy   NUMERIC(18,2),
    ADD COLUMN IF NOT EXISTS payment_rate NUMERIC(20,6),
    ADD COLUMN IF NOT EXISTS realized_gl  NUMERIC(18,2);

SELECT 'Migration schema_multicurrency_invoice_lines selesai' AS status;
