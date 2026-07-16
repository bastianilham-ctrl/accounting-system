-- Termin pembayaran (payment term) untuk otomasi due_date AP invoice
-- default_payment_term_days di vendor = termin default vendor tersebut (mis. NET 30)
-- payment_term_days di ap_invoice = termin yang dipakai invoice ini (bisa override per invoice)

ALTER TABLE vendor ADD COLUMN IF NOT EXISTS default_payment_term_days SMALLINT;
ALTER TABLE ap_invoice ADD COLUMN IF NOT EXISTS payment_term_days SMALLINT NOT NULL DEFAULT 30;
