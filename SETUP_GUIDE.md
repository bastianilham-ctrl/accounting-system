# Panduan Setup — Accounting System
## Step-by-Step dari Nol sampai Running

---

## STEP 1 — Buat Folder Project

Buka Terminal (Windows: PowerShell atau CMD), jalankan:

```bash
mkdir accounting_system
cd accounting_system
```

Salin semua file yang sudah dibuat ke folder ini:
```
accounting_system/
├── main.py
├── requirements.txt
├── .env.example
├── config/
│   └── settings.py
├── core/
│   └── database.py
├── modules/
│   ├── journal_engine.py
│   ├── vendor_scraper.py
│   └── ap_classifier.py
├── uploads/
└── temp/
    └── ocr/
```

---

## STEP 2 — Buat Virtual Environment Python

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Mac / Linux
python3 -m venv venv
source venv/bin/activate
```

Setelah aktif, terminal akan menampilkan `(venv)` di depan prompt.

---

## STEP 3 — Install Dependencies

```bash
pip install -r requirements.txt
```

Khusus Playwright (scraping), jalankan juga:
```bash
playwright install chromium
```

Khusus Tesseract OCR (Windows), download installer dari:
https://github.com/UB-Mannheim/tesseract/wiki
Lalu tambahkan path ke environment variable.

---

## STEP 4 — Buat Database PostgreSQL

Buka pgAdmin atau psql, jalankan:

```sql
CREATE DATABASE accounting_db;
CREATE USER accounting_user WITH PASSWORD 'password_kamu';
GRANT ALL PRIVILEGES ON DATABASE accounting_db TO accounting_user;
```

Lalu jalankan file schema:
```bash
psql -U postgres -d accounting_db -f schema_journal_engine.sql
```

---

## STEP 5 — Konfigurasi Environment Variables

```bash
# Salin file contoh
copy .env.example .env      # Windows
cp .env.example .env        # Mac/Linux
```

Edit file `.env`, isi minimal:
```env
DATABASE_URL=postgresql://accounting_user:password_kamu@localhost:5432/accounting_db
API_SECRET_KEY=isi_random_string_panjang_disini
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxx
```

---

## STEP 6 — Buat Folder Tambahan

```bash
# Windows
mkdir uploads
mkdir temp\ocr

# Mac/Linux
mkdir -p uploads temp/ocr
```

---

## STEP 7 — Isi Data Master (COA dan Entity)

Jalankan SQL berikut di pgAdmin untuk data awal:

```sql
-- Insert entity perusahaan
INSERT INTO entity (id, code, name, npwp, currency)
VALUES (
    uuid_generate_v4(),
    'DRBC',
    'PT Digital Realty Bersama Consultancy',
    '12.345.678.9-012.345',
    'IDR'
);

-- Contoh beberapa akun COA (sesuaikan dengan COA perusahaan kamu)
INSERT INTO chart_of_accounts (id, entity_id, account_code, account_name, account_type, normal_balance)
SELECT
    uuid_generate_v4(),
    e.id,
    a.code,
    a.name,
    a.type::account_type,
    a.nb::account_normal_balance
FROM entity e, (VALUES
    ('1-1-001', 'Kas',                         'asset',       'debit'),
    ('1-1-002', 'Bank BCA',                    'asset',       'debit'),
    ('1-2-001', 'Piutang Usaha',               'asset',       'debit'),
    ('1-5-001', 'Biaya Dibayar Dimuka',        'prepaid',     'debit'),
    ('1-6-001', 'Aset Tetap',                  'fixed_asset', 'debit'),
    ('1-6-002', 'Akum. Penyusutan Aset Tetap', 'accumulated_depreciation', 'credit'),
    ('2-1-001', 'Hutang Usaha (AP)',            'liability',   'credit'),
    ('2-1-002', 'Hutang PPh 23',               'liability',   'credit'),
    ('2-1-003', 'Hutang PPN',                  'liability',   'credit'),
    ('3-1-001', 'Modal Disetor',               'equity',      'credit'),
    ('4-1-001', 'Pendapatan Jasa',             'revenue',     'credit'),
    ('6-1-001', 'Beban Umum & Administrasi',   'expense',     'debit'),
    ('6-1-002', 'Beban Listrik & Utilitas',    'expense',     'debit'),
    ('6-1-003', 'Beban Sewa',                  'expense',     'debit'),
    ('6-1-004', 'Beban IT & Software',         'expense',     'debit'),
    ('6-1-005', 'Beban Gaji & THR',            'expense',     'debit'),
    ('6-1-006', 'Beban Penyusutan',            'expense',     'debit'),
    ('6-1-007', 'Beban Amortisasi',            'expense',     'debit')
) AS a(code, name, type, nb)
WHERE e.code = 'DRBC';
```

---

## STEP 8 — Jalankan API Server

```bash
# Pastikan virtual environment aktif
python main.py
```

Atau dengan uvicorn langsung:
```bash
uvicorn main:app --reload --port 8000
```

Buka browser: **http://localhost:8000/docs**
Ini adalah Swagger UI — kamu bisa test semua endpoint dari sini.

---

## STEP 9 — Test Endpoint Pertama

### Test health check:
```bash
curl http://localhost:8000/health
```

### Test posting jurnal manual:
```bash
curl -X POST http://localhost:8000/journals/post \
  -H "Content-Type: application/json" \
  -d '{
    "entity_id": "UUID_ENTITY_KAMU",
    "journal_type": "GL",
    "journal_date": "2026-05-30",
    "description": "Test jurnal pertama",
    "lines": [
      {
        "account_code": "1-1-002",
        "description": "Debit Bank BCA",
        "debit_idr": 1000000,
        "credit_idr": 0
      },
      {
        "account_code": "4-1-001",
        "description": "Kredit Pendapatan",
        "debit_idr": 0,
        "credit_idr": 1000000
      }
    ],
    "source": "manual",
    "created_by": "ilham"
  }'
```

### Test klasifikasi AP:
```bash
curl -X POST http://localhost:8000/ap/classify \
  -H "Content-Type: application/json" \
  -d '{
    "vendor_id": "UUID_VENDOR_KAMU",
    "description": "Sewa server AWS bulan Juni 2026",
    "amount": 15000000,
    "service_period_months": 1
  }'
```

---

## STEP 11 — Roadmap UI & Integrasi Frontend

Setelah API berjalan, fokuslah pada pembuatan screen berikut di Frontend (React/Vue/Next.js):

1.  **Dashboard**: Konsumsi endpoint `/reports/ap-aging/{id}`.
2.  **OCR Workbench**: Integrasikan dengan `APProcessor.process_invoice_upload`.
3.  **Vendor Tax Manager**: Kelola field `has_skb` dan `vendor_category`.
4.  **Tax Export**: Integrasi UI untuk menjalankan `coretax_export.py`.
5.  **Fixed Asset Registry**: (Next) Modul untuk mencatat aset tetap dari invoice.

## STEP 12 — Verifikasi Manual (Post-Setup)

1. **Cek Jurnal**: `SELECT * FROM gl_journal ORDER BY created_at DESC;`
2. **Cek Hutang**: `SELECT * FROM vw_ap_aging;`
3. **Cek Dokumen Pajak**: Jalankan `python expiry_alert.py`.

---

## Troubleshooting

**Error: `ModuleNotFoundError`**
→ Pastikan virtual environment aktif (`venv\Scripts\activate`)

**Error: `psycopg2 connection refused`**
→ Pastikan PostgreSQL service berjalan
→ Cek DATABASE_URL di file `.env`

**Error: `tesseract not found`**
→ Install Tesseract dan tambahkan ke PATH Windows

**Error: `playwright browser not found`**
→ Jalankan: `playwright install chromium`

---

*Generated: 2026-05-30 | Accounting System v1.0*
