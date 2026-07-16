"""
test_vendor_tax_router.py — connect ke kode ASLI modules/vendor_tax_router.py
==============================================================================
Menggunakan FastAPI TestClient untuk hit endpoint langsung.
DB di-mock via dependency override → tidak butuh PostgreSQL nyata.

Endpoint yang di-cover:
    GET  /vendors/{id}/tax-profile
    PUT  /vendors/{id}/category
    PUT  /vendors/{id}/skb
    PUT  /vendors/{id}/umkm
    PUT  /vendors/{id}/pph-override
    POST /vendors/{id}/documents
    GET  /vendors/{id}/tax-log
    GET  /vendors/tax-review-queue/{entity_id}
"""
import sys
import os
import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

# ── Tambah root project ke path ──────────────────────────────────────────────
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.vendor_tax_router import router
from core.database import get_db

# ─────────────────────────────────────────────────────────────────────────────
# SETUP: FastAPI test app dengan DB override
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI()
app.include_router(router)

TODAY      = date.today()
FUTURE_90  = TODAY + timedelta(days=90)
FUTURE_20  = TODAY + timedelta(days=20)
EXPIRED_30 = TODAY - timedelta(days=30)

VENDOR_ID  = str(uuid4())
ENTITY_ID  = "ENT-001"


def make_mock_db(tax_profile=None, log_rows=None, review_queue=None):
    """
    Factory mock DB session.
    Setiap test bisa inject data yang berbeda.
    """
    db = MagicMock()

    # Default tax profile row
    default_profile = {
        "id": VENDOR_ID,
        "vendor_name": "PT Test Vendor",
        "vendor_code": "V-TEST",
        "npwp": "01.234.567.8-999.000",
        "vendor_category": "PT",
        "has_skb": False,
        "skb_number": None,
        "skb_expiry": None,
        "umkm_cert_number": None,
        "umkm_cert_expiry": None,
        "pph_override_type": None,
        "pph_override_rate": None,
        "pph_treatment": "pph23_normal",
        "skb_expired": False,
        "tax_reviewed_at": str(TODAY),
    }
    profile_data = tax_profile or default_profile

    # Mock row dengan _mapping
    mock_row = MagicMock()
    mock_row._mapping = profile_data

    # Mock rows untuk list (tax-log, review-queue)
    mock_rows = log_rows or []

    # execute().fetchone() → tax profile
    # execute().fetchall() → list (tax-log, review-queue)
    db.execute.return_value.fetchone.return_value = mock_row
    db.execute.return_value.fetchall.return_value = [
        MagicMock(_mapping=r) for r in (review_queue or [])
    ]
    db.commit = MagicMock()
    return db


@pytest.fixture
def client_with_db():
    """
    Helper: kembalikan fungsi yang bisa buat client dengan mock DB custom.
    """
    def _make(mock_db=None):
        db = mock_db or make_mock_db()
        app.dependency_overrides[get_db] = lambda: db
        return TestClient(app), db
    yield _make
    app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# GET /vendors/{id}/tax-profile
# ─────────────────────────────────────────────────────────────────────────────

class TestGetTaxProfile:

    def test_200_vendor_ditemukan(self, client_with_db):
        client, _ = client_with_db()
        res = client.get(f"/vendors/{VENDOR_ID}/tax-profile")
        assert res.status_code == 200

    def test_response_mengandung_vendor_name(self, client_with_db):
        client, _ = client_with_db()
        res = client.get(f"/vendors/{VENDOR_ID}/tax-profile")
        assert "vendor_name" in res.json()

    def test_404_vendor_tidak_ada(self, client_with_db):
        db = make_mock_db()
        # fetchone → None = vendor tidak ditemukan
        db.execute.return_value.fetchone.return_value = None
        client, _ = client_with_db(db)
        res = client.get(f"/vendors/{uuid4()}/tax-profile")
        assert res.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# PUT /vendors/{id}/skb
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateSKB:

    def test_200_set_skb_aktif(self, client_with_db):
        client, _ = client_with_db()
        res = client.put(f"/vendors/{VENDOR_ID}/skb", json={
            "has_skb": True,
            "skb_number": "SKB-2024-001",
            "skb_expiry": str(FUTURE_90),
            "reviewed_by": "finance_team",
        })
        assert res.status_code == 200

    def test_response_success_true(self, client_with_db):
        client, _ = client_with_db()
        res = client.put(f"/vendors/{VENDOR_ID}/skb", json={
            "has_skb": True,
            "skb_number": "SKB-TEST",
            "skb_expiry": str(FUTURE_90),
        })
        assert res.json()["success"] is True

    def test_db_execute_dipanggil(self, client_with_db):
        client, db = client_with_db()
        client.put(f"/vendors/{VENDOR_ID}/skb", json={
            "has_skb": True,
            "skb_number": "SKB-DB-TEST",
            "skb_expiry": str(FUTURE_90),
        })
        assert db.execute.called, "db.execute harus dipanggil saat update SKB"

    def test_db_commit_dipanggil(self, client_with_db):
        client, db = client_with_db()
        client.put(f"/vendors/{VENDOR_ID}/skb", json={
            "has_skb": True,
            "skb_number": "SKB-COMMIT",
            "skb_expiry": str(FUTURE_90),
        })
        assert db.commit.called, "db.commit harus dipanggil setelah update"

    def test_200_set_skb_false(self, client_with_db):
        """Boleh set has_skb=False untuk cabut SKB"""
        client, _ = client_with_db()
        res = client.put(f"/vendors/{VENDOR_ID}/skb", json={
            "has_skb": False,
            "reason": "SKB tidak diperpanjang",
        })
        assert res.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# PUT /vendors/{id}/umkm
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateUMKM:

    def test_200_set_umkm(self, client_with_db):
        client, _ = client_with_db()
        res = client.put(f"/vendors/{VENDOR_ID}/umkm", json={
            "umkm_cert_number": "UMKM-2024-999",
            "umkm_cert_expiry": str(FUTURE_90),
            "omzet_per_tahun": 500000000,
            "reviewed_by": "finance_team",
        })
        assert res.status_code == 200

    def test_response_success_true(self, client_with_db):
        client, _ = client_with_db()
        res = client.put(f"/vendors/{VENDOR_ID}/umkm", json={
            "umkm_cert_number": "UMKM-TEST",
            "umkm_cert_expiry": str(FUTURE_90),
        })
        assert res.json()["success"] is True

    def test_db_commit_dipanggil(self, client_with_db):
        client, db = client_with_db()
        client.put(f"/vendors/{VENDOR_ID}/umkm", json={
            "umkm_cert_number": "UMKM-COMMIT",
            "umkm_cert_expiry": str(FUTURE_90),
        })
        assert db.commit.called

    def test_200_tanpa_omzet(self, client_with_db):
        """omzet_per_tahun optional"""
        client, _ = client_with_db()
        res = client.put(f"/vendors/{VENDOR_ID}/umkm", json={
            "umkm_cert_number": "UMKM-NO-OMZET",
            "umkm_cert_expiry": str(FUTURE_90),
        })
        assert res.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# PUT /vendors/{id}/category
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateCategory:

    @pytest.mark.parametrize("category", ["PT", "CV", "UMKM", "Perorangan", "Asing"])
    def test_200_kategori_valid(self, client_with_db, category):
        client, _ = client_with_db()
        res = client.put(f"/vendors/{VENDOR_ID}/category", json={
            "vendor_category": category,
            "is_pkp": True,
        })
        assert res.status_code == 200, f"Kategori '{category}' harus diterima"

    def test_400_kategori_tidak_valid(self, client_with_db):
        client, _ = client_with_db()
        res = client.put(f"/vendors/{VENDOR_ID}/category", json={
            "vendor_category": "INVALID_CAT",
            "is_pkp": True,
        })
        assert res.status_code == 400

    def test_response_success_true(self, client_with_db):
        client, _ = client_with_db()
        res = client.put(f"/vendors/{VENDOR_ID}/category", json={
            "vendor_category": "PT",
            "is_pkp": True,
        })
        assert res.json()["success"] is True


# ─────────────────────────────────────────────────────────────────────────────
# PUT /vendors/{id}/pph-override
# ─────────────────────────────────────────────────────────────────────────────

class TestPPhOverride:

    def test_200_override_valid(self, client_with_db):
        client, _ = client_with_db()
        res = client.put(f"/vendors/{VENDOR_ID}/pph-override", json={
            "pph_override_type": "PPh26",
            "pph_override_rate": 20.0,
            "pph_override_reason": "Vendor asing — tarif P3B",
            "reviewed_by": "finance_manager",
        })
        assert res.status_code == 200

    def test_400_tanpa_reason(self, client_with_db):
        client, _ = client_with_db()
        res = client.put(f"/vendors/{VENDOR_ID}/pph-override", json={
            "pph_override_type": "PPh26",
            "pph_override_rate": 20.0,
            "pph_override_reason": "",   # kosong → harus ditolak
        })
        assert res.status_code == 400, "Reason wajib untuk audit trail"

    def test_db_commit_dipanggil(self, client_with_db):
        client, db = client_with_db()
        client.put(f"/vendors/{VENDOR_ID}/pph-override", json={
            "pph_override_type": "bebas",
            "pph_override_rate": 0.0,
            "pph_override_reason": "Kontrak khusus",
        })
        assert db.commit.called

    def test_response_success_true(self, client_with_db):
        client, _ = client_with_db()
        res = client.put(f"/vendors/{VENDOR_ID}/pph-override", json={
            "pph_override_type": "PPh4(2)",
            "pph_override_rate": 10.0,
            "pph_override_reason": "Sewa gedung",
        })
        assert res.json()["success"] is True


# ─────────────────────────────────────────────────────────────────────────────
# GET /vendors/{id}/tax-log
# ─────────────────────────────────────────────────────────────────────────────

class TestGetTaxLog:

    def test_200_tax_log(self, client_with_db):
        client, _ = client_with_db()
        res = client.get(f"/vendors/{VENDOR_ID}/tax-log")
        assert res.status_code == 200

    def test_response_adalah_list(self, client_with_db):
        client, _ = client_with_db()
        res = client.get(f"/vendors/{VENDOR_ID}/tax-log")
        assert isinstance(res.json(), list)


# ─────────────────────────────────────────────────────────────────────────────
# GET /vendors/tax-review-queue/{entity_id}
# ─────────────────────────────────────────────────────────────────────────────

class TestReviewQueue:

    def test_200_review_queue(self, client_with_db):
        client, _ = client_with_db()
        res = client.get(f"/vendors/tax-review-queue/{ENTITY_ID}")
        assert res.status_code == 200

    def test_response_adalah_list(self, client_with_db):
        client, _ = client_with_db()
        res = client.get(f"/vendors/tax-review-queue/{ENTITY_ID}")
        assert isinstance(res.json(), list)
