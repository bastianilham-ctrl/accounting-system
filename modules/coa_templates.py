# modules/coa_templates.py
# Standard Chart of Accounts (COA) templates untuk 7 jenis perusahaan Indonesia.
# Referensi: PSAK, ETAP, standar industri umum.
#
# Struktur kode akun:
#   1-x-xxx  Aset
#   2-x-xxx  Liabilitas
#   3-x-xxx  Ekuitas
#   4-x-xxx  Pendapatan
#   5-x-xxx  Harga Pokok / Biaya Langsung
#   6-x-xxx  Beban Operasional
#   7-x-xxx  Pendapatan / Beban Lain-lain

# Format setiap akun: (account_code, account_name, account_type, normal_balance, is_header)
# account_type: asset | liability | equity | revenue | expense |
#               prepaid | fixed_asset | accumulated_depreciation
# is_header: True = akun grup (tidak diposting langsung)

# ============================================================
# AKUN BERSAMA — sama untuk semua jenis perusahaan
# ============================================================

COMMON_ACCOUNTS = [
    # ── ASET LANCAR ──────────────────────────────────────────
    ("1-0-000", "ASET",                              "asset",    "debit",  True),
    ("1-1-000", "Aset Lancar",                       "asset",    "debit",  True),
    ("1-1-001", "Kas Kecil (Petty Cash)",            "asset",    "debit",  False),
    ("1-1-002", "Kas di Bank — BCA",                 "asset",    "debit",  False),
    ("1-1-003", "Kas di Bank — Mandiri",             "asset",    "debit",  False),
    ("1-1-004", "Kas di Bank — BNI",                 "asset",    "debit",  False),
    ("1-1-010", "Piutang Usaha",                     "asset",    "debit",  False),
    ("1-1-011", "Piutang Retensi",                   "asset",    "debit",  False),
    ("1-1-015", "Cadangan Kerugian Piutang",         "asset",    "credit", False),
    ("1-1-020", "Uang Muka Pembelian",               "asset",    "debit",  False),
    ("1-1-021", "Uang Muka Proyek",                  "asset",    "debit",  False),
    ("1-1-030", "PPN Masukan",                       "asset",    "debit",  False),
    ("1-1-031", "PPh 23 Dibayar Dimuka",             "asset",    "debit",  False),
    ("1-1-032", "PPh 25 Dibayar Dimuka",             "asset",    "debit",  False),
    ("1-1-040", "Biaya Dibayar Dimuka — Asuransi",   "prepaid",  "debit",  False),
    ("1-1-041", "Biaya Dibayar Dimuka — Sewa",       "prepaid",  "debit",  False),
    ("1-1-042", "Biaya Dibayar Dimuka — Lainnya",    "prepaid",  "debit",  False),
    # ── ASET TETAP ───────────────────────────────────────────
    ("1-6-000", "Aset Tetap",                        "fixed_asset", "debit", True),
    ("1-6-001", "Tanah",                             "fixed_asset", "debit", False),
    ("1-6-002", "Bangunan",                          "fixed_asset", "debit", False),
    ("1-6-003", "Kendaraan",                         "fixed_asset", "debit", False),
    ("1-6-004", "Peralatan Kantor",                  "fixed_asset", "debit", False),
    ("1-6-005", "Peralatan Komputer & IT",           "fixed_asset", "debit", False),
    ("1-6-006", "Furnitur & Perlengkapan",           "fixed_asset", "debit", False),
    ("1-6-007", "Mesin & Peralatan",                 "fixed_asset", "debit", False),
    ("1-7-001", "Akum. Penyusutan — Bangunan",       "accumulated_depreciation", "credit", False),
    ("1-7-002", "Akum. Penyusutan — Kendaraan",      "accumulated_depreciation", "credit", False),
    ("1-7-003", "Akum. Penyusutan — Peralatan",      "accumulated_depreciation", "credit", False),
    ("1-7-004", "Akum. Penyusutan — Komputer & IT",  "accumulated_depreciation", "credit", False),
    ("1-7-005", "Akum. Penyusutan — Mesin",          "accumulated_depreciation", "credit", False),
    # ── ASET LAIN-LAIN ───────────────────────────────────────
    ("1-8-001", "Deposit Sewa",                      "asset",    "debit",  False),
    ("1-8-002", "Aset Lain-lain",                    "asset",    "debit",  False),
    # ── LIABILITAS JANGKA PENDEK ─────────────────────────────
    ("2-0-000", "LIABILITAS",                        "liability","credit", True),
    ("2-1-000", "Liabilitas Jangka Pendek",          "liability","credit", True),
    ("2-1-001", "Hutang Usaha",                      "liability","credit", False),
    ("2-1-002", "Hutang PPh 21",                     "liability","credit", False),
    ("2-1-003", "Hutang PPh 23",                     "liability","credit", False),
    ("2-1-004", "Hutang PPh 4(2)",                   "liability","credit", False),
    ("2-1-005", "Hutang PPh 25/29",                  "liability","credit", False),
    ("2-1-006", "Hutang PPN",                        "liability","credit", False),
    ("2-1-007", "PPN Keluaran",                      "liability","credit", False),
    ("2-1-010", "Uang Muka Penjualan / Pelanggan",   "liability","credit", False),
    ("2-1-011", "Hutang Gaji & Upah",                "liability","credit", False),
    ("2-1-012", "Hutang BPJS Ketenagakerjaan",       "liability","credit", False),
    ("2-1-013", "Hutang BPJS Kesehatan",             "liability","credit", False),
    ("2-1-014", "Biaya yang Masih Harus Dibayar",    "liability","credit", False),
    ("2-1-015", "Hutang Jangka Pendek — Bank",       "liability","credit", False),
    ("2-1-016", "Hutang Pihak Berelasi (Related Party)", "liability","credit", False),
    # ── LIABILITAS JANGKA PANJANG ────────────────────────────
    ("2-2-000", "Liabilitas Jangka Panjang",         "liability","credit", True),
    ("2-2-001", "Hutang Bank Jangka Panjang",        "liability","credit", False),
    ("2-2-002", "Hutang Leasing",                    "liability","credit", False),
    ("2-2-003", "Liabilitas Imbalan Kerja (PSAK 24)","liability","credit", False),
    # ── EKUITAS ──────────────────────────────────────────────
    ("3-0-000", "EKUITAS",                           "equity",  "credit", True),
    ("3-1-001", "Modal Disetor",                     "equity",  "credit", False),
    ("3-1-002", "Tambahan Modal Disetor (Agio)",     "equity",  "credit", False),
    ("3-1-003", "Laba Ditahan",                      "equity",  "credit", False),
    ("3-1-004", "Laba / Rugi Tahun Berjalan",        "equity",  "credit", False),
    ("3-1-005", "Dividen",                           "equity",  "debit",  False),
    # ── PENDAPATAN LAIN-LAIN ─────────────────────────────────
    ("7-0-000", "PENDAPATAN & BEBAN LAIN-LAIN",      "revenue", "credit", True),
    ("7-1-001", "Pendapatan Bunga Bank",             "revenue", "credit", False),
    ("7-1-002", "Laba Penjualan Aset",               "revenue", "credit", False),
    ("7-1-003", "Pendapatan Lain-lain",              "revenue", "credit", False),
    ("7-2-001", "Beban Bunga Pinjaman",              "expense", "debit",  False),
    ("7-2-002", "Beban Administrasi Bank",           "expense", "debit",  False),
    ("7-2-003", "Rugi Penjualan Aset",               "expense", "debit",  False),
    ("7-2-004", "Beban Lain-lain",                   "expense", "debit",  False),
    # ── BEBAN UMUM & ADMINISTRASI (semua jenis) ──────────────
    ("6-0-000", "BEBAN OPERASIONAL",                 "expense", "debit",  True),
    ("6-9-000", "Beban Umum & Administrasi",         "expense", "debit",  True),
    ("6-9-001", "Gaji & Tunjangan Karyawan",         "expense", "debit",  False),
    ("6-9-002", "Upah Tenaga Kerja Harian",          "expense", "debit",  False),
    ("6-9-003", "THR & Bonus",                       "expense", "debit",  False),
    ("6-9-004", "BPJS Ketenagakerjaan — Perusahaan", "expense", "debit",  False),
    ("6-9-005", "BPJS Kesehatan — Perusahaan",       "expense", "debit",  False),
    ("6-9-010", "Beban Sewa Kantor",                 "expense", "debit",  False),
    ("6-9-011", "Beban Listrik & Air",               "expense", "debit",  False),
    ("6-9-012", "Beban Telepon & Internet",          "expense", "debit",  False),
    ("6-9-013", "Beban Alat Tulis Kantor",           "expense", "debit",  False),
    ("6-9-014", "Beban Fotokopi & Percetakan",       "expense", "debit",  False),
    ("6-9-015", "Beban Perjalanan Dinas",            "expense", "debit",  False),
    ("6-9-016", "Beban Transportasi & BBM",          "expense", "debit",  False),
    ("6-9-017", "Beban Parkir & Tol",                "expense", "debit",  False),
    ("6-9-018", "Beban Konsumsi & Makan",            "expense", "debit",  False),
    ("6-9-020", "Beban Asuransi",                    "expense", "debit",  False),
    ("6-9-021", "Beban Penyusutan Aset Tetap",       "expense", "debit",  False),
    ("6-9-022", "Beban Amortisasi Prepaid",          "expense", "debit",  False),
    ("6-9-025", "Beban Perawatan & Perbaikan Kantor","expense", "debit",  False),
    ("6-9-026", "Beban Keamanan & Kebersihan",       "expense", "debit",  False),
    ("6-9-030", "Beban Pemasaran & Iklan",           "expense", "debit",  False),
    ("6-9-031", "Beban Representasi & Entertainment","expense", "debit",  False),
    ("6-9-035", "Beban Jasa Profesional (Konsultan)","expense", "debit",  False),
    ("6-9-036", "Beban Jasa Akuntansi & Audit",      "expense", "debit",  False),
    ("6-9-037", "Beban Jasa Hukum & Notaris",        "expense", "debit",  False),
    ("6-9-040", "Beban Pajak & Perizinan",           "expense", "debit",  False),
    ("6-9-041", "Beban Denda & Sanksi Pajak",        "expense", "debit",  False),
    ("6-9-045", "Beban Pelatihan & Pengembangan SDM","expense", "debit",  False),
    ("6-9-050", "Beban Langganan Software & SaaS",   "expense", "debit",  False),
    ("6-9-051", "Beban Hosting & Domain",            "expense", "debit",  False),
    ("6-9-099", "Beban Lain-lain Umum",              "expense", "debit",  False),
]

# ============================================================
# 1. JASA (Services) — konsultan, IT, profesional, dll
# ============================================================

JASA_ACCOUNTS = COMMON_ACCOUNTS + [
    # Pendapatan
    ("4-0-000", "PENDAPATAN",                          "revenue", "credit", True),
    ("4-1-001", "Pendapatan Jasa Konsultansi",         "revenue", "credit", False),
    ("4-1-002", "Pendapatan Jasa IT / Teknologi",      "revenue", "credit", False),
    ("4-1-003", "Pendapatan Jasa Manajemen",           "revenue", "credit", False),
    ("4-1-004", "Pendapatan Jasa Pelatihan",           "revenue", "credit", False),
    ("4-1-005", "Pendapatan Jasa Maintenance",         "revenue", "credit", False),
    ("4-1-006", "Pendapatan Project / Proyek",         "revenue", "credit", False),
    ("4-1-007", "Pendapatan Retainer Fee",             "revenue", "credit", False),
    ("4-1-009", "Pendapatan Jasa Lainnya",             "revenue", "credit", False),
    ("4-2-001", "Retur & Diskon Jasa",                 "revenue", "debit",  False),
    # Beban Langsung Jasa
    ("5-0-000", "BIAYA LANGSUNG JASA",                 "expense", "debit",  True),
    ("5-1-001", "Gaji Tenaga Ahli / Konsultan",        "expense", "debit",  False),
    ("5-1-002", "Honor Narasumber / Trainer",          "expense", "debit",  False),
    ("5-1-003", "Biaya Subkontraktor Jasa",            "expense", "debit",  False),
    ("5-1-004", "Biaya Lisensi & Tools Proyek",        "expense", "debit",  False),
    ("5-1-005", "Biaya Perjalanan Proyek",             "expense", "debit",  False),
    ("5-1-006", "Biaya Peralatan Proyek",              "expense", "debit",  False),
    ("5-1-007", "Biaya Langsung Lainnya",              "expense", "debit",  False),
    # Beban Penjualan Jasa
    ("6-1-000", "Beban Penjualan",                     "expense", "debit",  True),
    ("6-1-001", "Beban Komisi Penjualan",              "expense", "debit",  False),
    ("6-1-002", "Beban Proposal & Tender",             "expense", "debit",  False),
]

# ============================================================
# 2. DAGANG (Trading) — distributor, retail, grosir
# ============================================================

DAGANG_ACCOUNTS = COMMON_ACCOUNTS + [
    # Persediaan
    ("1-3-000", "Persediaan",                          "asset",   "debit",  True),
    ("1-3-001", "Persediaan Barang Dagangan",          "asset",   "debit",  False),
    ("1-3-002", "Barang Dalam Perjalanan",             "asset",   "debit",  False),
    ("1-3-003", "Penyisihan Penurunan Nilai Persediaan","asset",  "credit", False),
    # Pendapatan
    ("4-0-000", "PENDAPATAN",                          "revenue", "credit", True),
    ("4-1-001", "Penjualan — Tunai",                   "revenue", "credit", False),
    ("4-1-002", "Penjualan — Kredit",                  "revenue", "credit", False),
    ("4-1-003", "Penjualan — Ekspor",                  "revenue", "credit", False),
    ("4-2-001", "Retur Penjualan",                     "revenue", "debit",  False),
    ("4-2-002", "Diskon Penjualan",                    "revenue", "debit",  False),
    ("4-2-003", "Potongan Harga / Rabat",              "revenue", "debit",  False),
    # HPP
    ("5-0-000", "HARGA POKOK PENJUALAN",               "expense", "debit",  True),
    ("5-1-001", "Harga Pokok Penjualan (HPP)",         "expense", "debit",  False),
    ("5-1-002", "Biaya Pembelian Barang",              "expense", "debit",  False),
    ("5-1-003", "Biaya Angkut Pembelian",              "expense", "debit",  False),
    ("5-1-004", "Retur Pembelian",                     "expense", "credit", False),
    ("5-1-005", "Diskon Pembelian",                    "expense", "credit", False),
    # Beban Penjualan
    ("6-1-000", "Beban Penjualan",                     "expense", "debit",  True),
    ("6-1-001", "Beban Pengiriman Barang",             "expense", "debit",  False),
    ("6-1-002", "Beban Gudang & Penyimpanan",          "expense", "debit",  False),
    ("6-1-003", "Beban Pengemasan",                    "expense", "debit",  False),
    ("6-1-004", "Beban Komisi Penjualan",              "expense", "debit",  False),
    ("6-1-005", "Beban Promosi & Iklan",               "expense", "debit",  False),
    ("6-1-006", "Beban Pameran & Event",               "expense", "debit",  False),
    ("6-1-007", "Beban Retur & Klaim Pelanggan",       "expense", "debit",  False),
]

# ============================================================
# 3. KONSTRUKSI (Construction)
# ============================================================

KONSTRUKSI_ACCOUNTS = COMMON_ACCOUNTS + [
    # Aset khusus konstruksi
    ("1-3-001", "Persediaan Material Bangunan",        "asset",   "debit",  False),
    ("1-3-002", "Perlengkapan Proyek",                 "asset",   "debit",  False),
    ("1-4-001", "Tagihan atas Pekerjaan Dalam Proses", "asset",   "debit",  False),
    ("1-4-002", "Retensi Proyek yang Belum Diterima",  "asset",   "debit",  False),
    ("1-4-003", "Biaya Proyek Tangguhan",              "asset",   "debit",  False),
    ("1-6-008", "Alat Berat & Excavator",              "fixed_asset","debit",False),
    ("1-6-009", "Scaffolding & Bekisting",             "fixed_asset","debit",False),
    ("1-7-006", "Akum. Penyusutan — Alat Berat",       "accumulated_depreciation","credit",False),
    # Pendapatan
    ("4-0-000", "PENDAPATAN",                          "revenue", "credit", True),
    ("4-1-001", "Pendapatan Kontrak — Sipil",          "revenue", "credit", False),
    ("4-1-002", "Pendapatan Kontrak — Gedung",         "revenue", "credit", False),
    ("4-1-003", "Pendapatan Kontrak — Mekanikal",      "revenue", "credit", False),
    ("4-1-004", "Pendapatan Kontrak — Elektrikal",     "revenue", "credit", False),
    ("4-1-005", "Pendapatan Kontrak — Interior",       "revenue", "credit", False),
    ("4-1-006", "Pendapatan Kontrak — EPC",            "revenue", "credit", False),
    ("4-1-007", "Pendapatan Variation Order (VO)",     "revenue", "credit", False),
    ("4-2-001", "Retensi yang Diakui",                 "revenue", "credit", False),
    # Biaya Langsung Proyek (HPP Konstruksi)
    ("5-0-000", "BIAYA LANGSUNG PROYEK",               "expense", "debit",  True),
    ("5-1-001", "Biaya Material Bangunan",             "expense", "debit",  False),
    ("5-1-002", "Biaya Upah Tukang & Mandor",          "expense", "debit",  False),
    ("5-1-003", "Biaya Subkontraktor",                 "expense", "debit",  False),
    ("5-1-004", "Biaya Sewa Alat Berat",               "expense", "debit",  False),
    ("5-1-005", "Biaya Penyusutan Alat Proyek",        "expense", "debit",  False),
    ("5-1-006", "Biaya Transportasi & Mobilisasi",     "expense", "debit",  False),
    ("5-1-007", "Biaya K3 (Keselamatan Kerja)",        "expense", "debit",  False),
    ("5-1-008", "Biaya Site Overhead",                 "expense", "debit",  False),
    ("5-1-009", "Biaya Asuransi CAR (Contractor AR)",  "expense", "debit",  False),
    ("5-1-010", "Biaya Perancangan & Engineering",     "expense", "debit",  False),
    ("5-1-011", "Biaya Pengujian & Commissioning",     "expense", "debit",  False),
    ("5-1-099", "Biaya Proyek Lainnya",                "expense", "debit",  False),
    # Overhead Konstruksi
    ("6-1-000", "Biaya Overhead Konstruksi",           "expense", "debit",  True),
    ("6-1-001", "Biaya Tender & Estimasi",             "expense", "debit",  False),
    ("6-1-002", "Biaya Jaminan Penawaran",             "expense", "debit",  False),
    ("6-1-003", "Biaya Jaminan Pelaksanaan",           "expense", "debit",  False),
]

# ============================================================
# 4. MANUFAKTUR (Manufacturing)
# ============================================================

MANUFAKTUR_ACCOUNTS = COMMON_ACCOUNTS + [
    # Persediaan multi-level
    ("1-3-000", "Persediaan",                          "asset",   "debit",  True),
    ("1-3-001", "Persediaan Bahan Baku",               "asset",   "debit",  False),
    ("1-3-002", "Persediaan Bahan Pembantu (Supplies)","asset",   "debit",  False),
    ("1-3-003", "Barang Dalam Proses (WIP)",           "asset",   "debit",  False),
    ("1-3-004", "Barang Jadi",                         "asset",   "debit",  False),
    ("1-3-005", "Suku Cadang & Spare Part",            "asset",   "debit",  False),
    ("1-3-006", "Penyisihan Penurunan Nilai Persediaan","asset",  "credit", False),
    ("1-6-008", "Mesin Produksi",                      "fixed_asset","debit",False),
    ("1-6-009", "Peralatan Pabrik",                    "fixed_asset","debit",False),
    ("1-6-010", "Gedung Pabrik",                       "fixed_asset","debit",False),
    ("1-7-006", "Akum. Penyusutan — Mesin Produksi",   "accumulated_depreciation","credit",False),
    ("1-7-007", "Akum. Penyusutan — Gedung Pabrik",    "accumulated_depreciation","credit",False),
    # Pendapatan
    ("4-0-000", "PENDAPATAN",                          "revenue", "credit", True),
    ("4-1-001", "Penjualan Barang Jadi — Lokal",       "revenue", "credit", False),
    ("4-1-002", "Penjualan Barang Jadi — Ekspor",      "revenue", "credit", False),
    ("4-1-003", "Penjualan Produk Sampingan",          "revenue", "credit", False),
    ("4-1-004", "Penjualan Scrap / Limbah",            "revenue", "credit", False),
    ("4-2-001", "Retur Penjualan",                     "revenue", "debit",  False),
    ("4-2-002", "Diskon & Potongan Penjualan",         "revenue", "debit",  False),
    # Harga Pokok Produksi
    ("5-0-000", "HARGA POKOK PRODUKSI",                "expense", "debit",  True),
    ("5-1-001", "Biaya Bahan Baku Langsung",           "expense", "debit",  False),
    ("5-1-002", "Biaya Tenaga Kerja Langsung",         "expense", "debit",  False),
    ("5-1-003", "Biaya Overhead Pabrik — Tetap",       "expense", "debit",  False),
    ("5-1-004", "Biaya Overhead Pabrik — Variabel",    "expense", "debit",  False),
    ("5-1-005", "Biaya Bahan Pembantu",                "expense", "debit",  False),
    ("5-1-006", "Biaya Penyusutan Mesin & Pabrik",     "expense", "debit",  False),
    ("5-1-007", "Biaya Energi Pabrik (Listrik, Gas)",  "expense", "debit",  False),
    ("5-1-008", "Biaya Maintenance Mesin",             "expense", "debit",  False),
    ("5-1-009", "Biaya Quality Control",               "expense", "debit",  False),
    ("5-1-010", "Biaya Pengemasan",                    "expense", "debit",  False),
    ("5-2-001", "Variansi Bahan Baku",                 "expense", "debit",  False),
    ("5-2-002", "Variansi Tenaga Kerja",               "expense", "debit",  False),
    # Beban Penjualan & Distribusi
    ("6-1-000", "Beban Penjualan & Distribusi",        "expense", "debit",  True),
    ("6-1-001", "Beban Pengiriman & Ekspedisi",        "expense", "debit",  False),
    ("6-1-002", "Beban Gudang Barang Jadi",            "expense", "debit",  False),
    ("6-1-003", "Beban Komisi & Agen",                 "expense", "debit",  False),
    ("6-1-004", "Beban Promosi & Pameran",             "expense", "debit",  False),
    ("6-1-005", "Beban Riset & Pengembangan Produk",   "expense", "debit",  False),
]

# ============================================================
# 5. RENTAL (Equipment / Vehicle Rental)
# ============================================================

RENTAL_ACCOUNTS = COMMON_ACCOUNTS + [
    # Aset yang disewakan
    ("1-5-000", "Aset Sewa (Rental Fleet)",            "fixed_asset","debit",True),
    ("1-5-001", "Kendaraan untuk Disewakan",           "fixed_asset","debit",False),
    ("1-5-002", "Alat Berat untuk Disewakan",          "fixed_asset","debit",False),
    ("1-5-003", "Peralatan untuk Disewakan",           "fixed_asset","debit",False),
    ("1-5-004", "Gedung / Ruang untuk Disewakan",      "fixed_asset","debit",False),
    ("1-5-010", "Akum. Penyusutan — Kendaraan Sewa",   "accumulated_depreciation","credit",False),
    ("1-5-011", "Akum. Penyusutan — Alat Berat Sewa",  "accumulated_depreciation","credit",False),
    ("1-5-012", "Akum. Penyusutan — Peralatan Sewa",   "accumulated_depreciation","credit",False),
    ("1-3-001", "Suku Cadang & Spare Part",            "asset",   "debit",  False),
    # Pendapatan
    ("4-0-000", "PENDAPATAN",                          "revenue", "credit", True),
    ("4-1-001", "Pendapatan Sewa Harian",              "revenue", "credit", False),
    ("4-1-002", "Pendapatan Sewa Bulanan",             "revenue", "credit", False),
    ("4-1-003", "Pendapatan Sewa Jangka Panjang",      "revenue", "credit", False),
    ("4-1-004", "Pendapatan Operator / Driver",        "revenue", "credit", False),
    ("4-1-005", "Pendapatan Asuransi Klaim",           "revenue", "credit", False),
    ("4-1-006", "Pendapatan Denda Keterlambatan",      "revenue", "credit", False),
    ("4-2-001", "Retur & Diskon Sewa",                 "revenue", "debit",  False),
    # Biaya Langsung Sewa
    ("5-0-000", "BIAYA LANGSUNG SEWA",                 "expense", "debit",  True),
    ("5-1-001", "Biaya BBM Armada",                    "expense", "debit",  False),
    ("5-1-002", "Biaya Servis & Perawatan Rutin",      "expense", "debit",  False),
    ("5-1-003", "Biaya Perbaikan & Overhaul",          "expense", "debit",  False),
    ("5-1-004", "Biaya Asuransi Armada",               "expense", "debit",  False),
    ("5-1-005", "Biaya Pajak Kendaraan (PKB/STNK)",    "expense", "debit",  False),
    ("5-1-006", "Biaya Operator / Driver",             "expense", "debit",  False),
    ("5-1-007", "Biaya Penyusutan Aset Sewa",          "expense", "debit",  False),
    ("5-1-008", "Biaya Mobilisasi & Demobilisasi",     "expense", "debit",  False),
    ("5-1-009", "Biaya Penggantian Suku Cadang",       "expense", "debit",  False),
    # Beban Operasional
    ("6-1-000", "Beban Operasional Rental",            "expense", "debit",  True),
    ("6-1-001", "Beban Administrasi Kontrak",          "expense", "debit",  False),
    ("6-1-002", "Beban Inspeksi & Sertifikasi",        "expense", "debit",  False),
    ("6-1-003", "Beban GPS & Monitoring",              "expense", "debit",  False),
]

# ============================================================
# 6. PROPERTI (Real Estate — developer & investasi)
# ============================================================

PROPERTI_ACCOUNTS = COMMON_ACCOUNTS + [
    # Persediaan & Aset Properti
    ("1-3-001", "Persediaan Tanah untuk Dijual",       "asset",   "debit",  False),
    ("1-3-002", "Persediaan Bangunan untuk Dijual",    "asset",   "debit",  False),
    ("1-3-003", "Properti dalam Pembangunan (WIP)",    "asset",   "debit",  False),
    ("1-3-004", "Biaya Pengembangan Tangguhan",        "asset",   "debit",  False),
    ("1-4-001", "Properti Investasi — Tanah",          "fixed_asset","debit",False),
    ("1-4-002", "Properti Investasi — Bangunan",       "fixed_asset","debit",False),
    ("1-4-010", "Akum. Penyusutan — Properti Investasi","accumulated_depreciation","credit",False),
    # Pendapatan
    ("4-0-000", "PENDAPATAN",                          "revenue", "credit", True),
    ("4-1-001", "Pendapatan Penjualan Kavling",        "revenue", "credit", False),
    ("4-1-002", "Pendapatan Penjualan Rumah/Unit",     "revenue", "credit", False),
    ("4-1-003", "Pendapatan Penjualan Ruko",           "revenue", "credit", False),
    ("4-1-004", "Pendapatan Sewa Properti",            "revenue", "credit", False),
    ("4-1-005", "Pendapatan Service Charge",           "revenue", "credit", False),
    ("4-1-006", "Pendapatan IPL (Iuran Pengelolaan)",  "revenue", "credit", False),
    ("4-1-007", "Pendapatan Uang Muka Penjualan",      "revenue", "credit", False),
    ("4-2-001", "Retur & Pembatalan Penjualan",        "revenue", "debit",  False),
    # HPP Properti
    ("5-0-000", "HARGA POKOK PENJUALAN PROPERTI",      "expense", "debit",  True),
    ("5-1-001", "Biaya Tanah (HPP)",                   "expense", "debit",  False),
    ("5-1-002", "Biaya Konstruksi (HPP)",              "expense", "debit",  False),
    ("5-1-003", "Biaya Perizinan & Sertifikasi",       "expense", "debit",  False),
    ("5-1-004", "Biaya Infrastructure & Fasilitas",    "expense", "debit",  False),
    ("5-1-005", "Biaya Marketing & Penjualan",         "expense", "debit",  False),
    # Beban Operasional Properti
    ("6-1-000", "Beban Operasional Properti",          "expense", "debit",  True),
    ("6-1-001", "Biaya Pengelolaan Properti",          "expense", "debit",  False),
    ("6-1-002", "Biaya Perawatan Fasilitas Umum",      "expense", "debit",  False),
    ("6-1-003", "Biaya Keamanan Komplek",              "expense", "debit",  False),
    ("6-1-004", "Biaya PBB & Retribusi",               "expense", "debit",  False),
    ("6-1-005", "Biaya Arsitek & Desainer",            "expense", "debit",  False),
    ("6-1-006", "Biaya PPJB & Notaris",                "expense", "debit",  False),
    ("6-1-007", "Biaya Pameran Properti",              "expense", "debit",  False),
]

# ============================================================
# 7. LOGISTIK (Freight, Warehouse, Supply Chain)
# ============================================================

LOGISTIK_ACCOUNTS = COMMON_ACCOUNTS + [
    # Aset armada & gudang
    ("1-3-001", "Persediaan Bahan Bakar (BBM)",        "asset",   "debit",  False),
    ("1-3-002", "Persediaan Suku Cadang Kendaraan",    "asset",   "debit",  False),
    ("1-3-003", "Perlengkapan Operasional Gudang",     "asset",   "debit",  False),
    ("1-6-008", "Truk & Kendaraan Angkutan",           "fixed_asset","debit",False),
    ("1-6-009", "Forklift & Alat Gudang",              "fixed_asset","debit",False),
    ("1-6-010", "Gedung Gudang",                       "fixed_asset","debit",False),
    ("1-6-011", "Sistem WMS & Tracking",               "fixed_asset","debit",False),
    ("1-7-006", "Akum. Penyusutan — Armada",           "accumulated_depreciation","credit",False),
    ("1-7-007", "Akum. Penyusutan — Alat Gudang",      "accumulated_depreciation","credit",False),
    ("1-7-008", "Akum. Penyusutan — Gedung Gudang",    "accumulated_depreciation","credit",False),
    # Pendapatan
    ("4-0-000", "PENDAPATAN",                          "revenue", "credit", True),
    ("4-1-001", "Pendapatan Jasa Pengiriman Darat",    "revenue", "credit", False),
    ("4-1-002", "Pendapatan Jasa Pengiriman Laut",     "revenue", "credit", False),
    ("4-1-003", "Pendapatan Jasa Pengiriman Udara",    "revenue", "credit", False),
    ("4-1-004", "Pendapatan Jasa Pergudangan",         "revenue", "credit", False),
    ("4-1-005", "Pendapatan Jasa Bongkar Muat",        "revenue", "credit", False),
    ("4-1-006", "Pendapatan Jasa Custom Clearance",    "revenue", "credit", False),
    ("4-1-007", "Pendapatan Jasa Freight Forwarding",  "revenue", "credit", False),
    ("4-1-008", "Pendapatan Last Mile Delivery",       "revenue", "credit", False),
    ("4-2-001", "Diskon & Potongan Tarif",             "revenue", "debit",  False),
    # Biaya Langsung Logistik
    ("5-0-000", "BIAYA LANGSUNG OPERASIONAL",          "expense", "debit",  True),
    ("5-1-001", "Biaya BBM & Energi Armada",           "expense", "debit",  False),
    ("5-1-002", "Biaya Pengemudi & Helper",            "expense", "debit",  False),
    ("5-1-003", "Biaya Servis & Perawatan Armada",     "expense", "debit",  False),
    ("5-1-004", "Biaya Penyusutan Armada & Alat",      "expense", "debit",  False),
    ("5-1-005", "Biaya Asuransi Kargo",                "expense", "debit",  False),
    ("5-1-006", "Biaya Asuransi Armada",               "expense", "debit",  False),
    ("5-1-007", "Biaya Operasional Gudang",            "expense", "debit",  False),
    ("5-1-008", "Biaya Bongkar Muat",                  "expense", "debit",  False),
    ("5-1-009", "Biaya Freight (Subkon Ekspedisi)",    "expense", "debit",  False),
    ("5-1-010", "Biaya Tol & Parkir Operasional",      "expense", "debit",  False),
    ("5-1-011", "Biaya TKBM (Buruh)",                  "expense", "debit",  False),
    ("5-1-012", "Biaya Packing & Labeling",            "expense", "debit",  False),
    # Beban Operasional
    ("6-1-000", "Beban Operasional Logistik",          "expense", "debit",  True),
    ("6-1-001", "Beban Pajak Kendaraan (PKB/STNK)",    "expense", "debit",  False),
    ("6-1-002", "Beban GPS & Fleet Management",        "expense", "debit",  False),
    ("6-1-003", "Beban Sistem WMS & TMS",              "expense", "debit",  False),
    ("6-1-004", "Beban KIR & Sertifikasi Kendaraan",   "expense", "debit",  False),
]

# ============================================================
# REGISTRY — mapping nama ke template list
# ============================================================

TEMPLATES: dict = {
    "jasa":        {"name": "Perusahaan Jasa",              "accounts": JASA_ACCOUNTS},
    "dagang":      {"name": "Perusahaan Dagang / Distribusi","accounts": DAGANG_ACCOUNTS},
    "konstruksi":  {"name": "Perusahaan Konstruksi",        "accounts": KONSTRUKSI_ACCOUNTS},
    "manufaktur":  {"name": "Perusahaan Manufaktur",        "accounts": MANUFAKTUR_ACCOUNTS},
    "rental":      {"name": "Perusahaan Rental",            "accounts": RENTAL_ACCOUNTS},
    "properti":    {"name": "Perusahaan Properti",          "accounts": PROPERTI_ACCOUNTS},
    "logistik":    {"name": "Perusahaan Logistik",          "accounts": LOGISTIK_ACCOUNTS},
}


def get_template(business_type: str) -> list:
    """Ambil daftar akun untuk jenis usaha tertentu."""
    t = TEMPLATES.get(business_type.lower())
    if not t:
        raise ValueError(
            f"Jenis usaha '{business_type}' tidak dikenal. "
            f"Pilih: {list(TEMPLATES.keys())}"
        )
    return t["accounts"]


def list_templates() -> dict:
    """Daftar semua template yang tersedia."""
    return {k: {"name": v["name"], "account_count": len(v["accounts"])}
            for k, v in TEMPLATES.items()}
