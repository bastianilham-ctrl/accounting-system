import requests
import os

# Konfigurasi
BASE_URL = "http://localhost:8000"
ENTITY_ID = "GANTI_DENGAN_UUID_ENTITY_DI_DATABASE" # Ambil dari tabel entity
USER_ID = "bastian_admin"
SAMPLE_PDF = "contoh_invoice.pdf" # Pastikan file ini ada

def test_full_workflow():
    print(f"--- Memulai Test Workflow AP ---")
    
    if not os.path.exists(SAMPLE_PDF):
        print(f"Error: File {SAMPLE_PDF} tidak ditemukan untuk testing.")
        return

    url = f"{BASE_URL}/ap/upload"
    params = {
        "entity_id": ENTITY_ID,
        "user_id": USER_ID
    }
    
    with open(SAMPLE_PDF, "rb") as f:
        files = {"file": (SAMPLE_PDF, f, "application/pdf")}
        response = requests.post(url, params=params, files=files)

    if response.status_code == 200:
        data = response.json()
        print("✅ Berhasil!")
        print(f"Nomor Invoice : {data.get('invoice_no')}")
        print(f"Vendor        : {data.get('vendor')}")
        print(f"Pajak         : {data.get('tax_treatment')}")
        print(f"Nomor Jurnal  : {data.get('journal_no')}")
    else:
        print(f"❌ Gagal: {response.status_code}")
        print(response.text)

def check_health():
    res = requests.get(f"{BASE_URL}/health")
    print(f"Status API: {res.json()}")

if __name__ == "__main__":
    # 1. Cek koneksi
    check_health()
    # 2. Jalankan upload (uncomment jika sudah ada file PDF)
    # test_full_workflow()