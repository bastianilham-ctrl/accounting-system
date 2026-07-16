-- Kategori hutang AP: trade (Hutang Usaha) / related_party / bank_loan / other
-- payable_coa = akun COA yang dikredit saat posting jurnal & didebit saat pembayaran

ALTER TABLE ap_invoice ADD COLUMN IF NOT EXISTS payable_category VARCHAR(20) NOT NULL DEFAULT 'trade';
ALTER TABLE ap_invoice ADD COLUMN IF NOT EXISTS payable_coa VARCHAR(20) NOT NULL DEFAULT '2-1-001';

-- Akun baru "Hutang Pihak Berelasi" untuk semua entity yang sudah ada
INSERT INTO chart_of_accounts (entity_id, account_code, account_name, account_type, normal_balance, is_header)
SELECT e.id, '2-1-016', 'Hutang Pihak Berelasi (Related Party)', 'liability', 'credit', FALSE
FROM entity e
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts c
    WHERE c.entity_id = e.id AND c.account_code = '2-1-016'
);
