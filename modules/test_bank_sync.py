import requests
import json
import os

# --- KONFIGURASI ---
BASE_URL = "http://localhost:8000"
USERNAME = "superadmin"
PASSWORD = "Admin@12345!"
# ID ini harus valid di database Anda (cek tabel bank_account)
BANK_ACCOUNT_ID = "GANTI_DENGAN_UUID_BANK_ACCOUNT" 

def get_token():
    print("🔑 Melakukan login...")
    url = f"{BASE_URL}/auth/login"
    data = {"username": USERNAME, "password": PASSWORD}
    res = requests.post(url, data=data)
    if res.status_code == 200:
        token = res.json()["access_token"]
        print("✅ Login berhasil.")
        return token
    else:
        print(f"❌ Login gagal: {res.text}")
        return None

def create_sample_bca_csv():
    filename = "sample_bca.csv"
    content = """TANGGAL,KETERANGAN,CABANG,JUMLAH,SALDO
01/05,TRSF E-BANKING DB 0105/FWS99/99999,,1500000.00,10500000.00
02/05,TRANSFER CR KHR 0205/INV-2024-001,,2500000.00,13000000.00
03/05,BIAYA ADMIN,,15000.00,12985000.00
"""
    with open(filename, "w") as f:
        f.write(content)
    return filename

def create_sample_mandiri_csv():
    filename = "sample_mandiri.csv"
    # Mandiri biasanya pakai header yang lebih panjang
    content = """TANGGAL TRANSAKSI,TANGGAL VALUTA,DESKRIPSI,NOMINAL,JENIS,SALDO
2024-05-01,2024-05-01,PEMBAYARAN LISTRIK,500000.00,DEBET,5000000.00
2024-05-02,2024-05-02,TRANSFER MASUK DARI PT ABC,1000000.00,KREDIT,6000000.00
"""
    with open(filename, "w") as f:
        f.write(content)
    return filename

def test_import(token, filename, bank_type):
    print(f"🚀 Mengetes import mutasi {bank_type} ({filename})...")
    url = f"{BASE_URL}/bank/import/{BANK_ACCOUNT_ID}"
    headers = {"Authorization": f"Bearer {token}"}
    
    # BCA biasanya butuh skip_rows jika di-export langsung dari KlikBCA (biasanya 7 baris info rekening)
    # Namun di file contoh buatan kita di atas, skip_rows = 0
    params = {"skip_rows": 0}
    
    with open(filename, "rb") as f:
        files = {"file": (filename, f, "text/csv")}
        res = requests.post(url, headers=headers, params=params, files=files)

    if res.status_code == 200:
        data = res.json()
        print(f"✅ Berhasil import {bank_type}!")
        print(f"   Parsed: {data.get('total_parsed')} | Inserted: {data.get('inserted')} | Skipped: {data.get('skipped_duplicate')}")
    else:
        print(f"❌ Gagal import {bank_type}: {res.status_code}")
        print(f"   Detail: {res.text}")

def test_auto_reconcile(token):
    print(f"🔄 Menjalankan Auto-Reconciliation...")
    url = f"{BASE_URL}/bank/reconcile/{BANK_ACCOUNT_ID}/auto"
    headers = {"Authorization": f"Bearer {token}"}
    res = requests.post(url, headers=headers)
    
    if res.status_code == 200:
        data = res.json()
        print("✅ Auto-reconcile selesai.")
        print(f"   Matched: {data.get('matched')} | Partial: {data.get('partial')} | Still Unmatched: {data.get('still_unmatched')}")
    else:
        print(f"❌ Gagal reconcile: {res.text}")

if __name__ == "__main__":
    # 1. Ambil Token
    token = get_token()
    if not token:
        exit()

    # 2. Buat File Dummy
    bca_file = create_sample_bca_csv()
    mandiri_file = create_sample_mandiri_csv()

    # 3. Jalankan Test
    if "GANTI_DENGAN_UUID" in BANK_ACCOUNT_ID:
        print("\n⚠️  Peringatan: Kamu belum mengganti BANK_ACCOUNT_ID di dalam skrip.")
        print("   Silakan cek database kamu (tabel bank_account) untuk mendapatkan UUID rekening bank.")
    else:
        # Test Import BCA
        test_import(token, bca_file, "BCA")
        
        # Test Import Mandiri
        test_import(token, mandiri_file, "MANDIRI")
        
        # Test Auto Reconcile
        test_auto_reconcile(token)

    # Cleanup file dummy
    if os.path.exists(bca_file): os.remove(bca_file)
    if os.path.exists(mandiri_file): os.remove(mandiri_file)
    print("\n🏁 Test Selesai.")