"""
conftest.py — Shared fixtures untuk ACCOUNTING_SYSTEM test suite
Covers: vendor tax profiles, AP transactions, document states
"""
import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, AsyncMock


# ──────────────────────────────────────────
# Helper: tanggal relatif terhadap hari ini
# ──────────────────────────────────────────
TODAY = date.today()
FUTURE_30  = TODAY + timedelta(days=30)
FUTURE_90  = TODAY + timedelta(days=90)
EXPIRED_1  = TODAY - timedelta(days=1)
EXPIRED_30 = TODAY - timedelta(days=30)


# ──────────────────────────────────────────
# Vendor fixtures — 1 per skenario PPh
# ──────────────────────────────────────────

@pytest.fixture
def vendor_skb_aktif():
    """Skenario 1: Vendor memiliki SKB aktif → PPh = 0%"""
    return {
        "id": "V001",
        "name": "PT Maju Bersama",
        "vendor_category": "badan",
        "has_skb": True,
        "skb_number": "SKB-2024-001",
        "skb_expiry": FUTURE_90.isoformat(),
        "umkm_cert_number": None,
        "pph_override_type": None,
    }


@pytest.fixture
def vendor_umkm_aktif():
    """Skenario 2: Vendor UMKM aktif → PPh Final 0.5%"""
    return {
        "id": "V002",
        "name": "CV Karya Mandiri",
        "vendor_category": "umkm",
        "has_skb": False,
        "skb_number": None,
        "skb_expiry": None,
        "umkm_cert_number": "UMKM-2024-999",
        "umkm_cert_expiry": FUTURE_90.isoformat(),
        "pph_override_type": None,
    }


@pytest.fixture
def vendor_skb_expired():
    """Skenario 3a: SKB expired → needs_tax_review = True"""
    return {
        "id": "V003",
        "name": "PT Dokumen Lama",
        "vendor_category": "badan",
        "has_skb": True,
        "skb_number": "SKB-2023-OLD",
        "skb_expiry": EXPIRED_30.isoformat(),
        "umkm_cert_number": None,
        "pph_override_type": None,
    }


@pytest.fixture
def vendor_umkm_expired():
    """Skenario 3b: Surat UMKM expired → needs_tax_review = True"""
    return {
        "id": "V004",
        "name": "CV Surat Lama",
        "vendor_category": "umkm",
        "has_skb": False,
        "skb_number": None,
        "skb_expiry": None,
        "umkm_cert_number": "UMKM-2023-OLD",
        "umkm_cert_expiry": EXPIRED_1.isoformat(),
        "pph_override_type": None,
    }


@pytest.fixture
def vendor_normal_badan():
    """Skenario 4a: Vendor Badan biasa → PPh 23 = 2%"""
    return {
        "id": "V005",
        "name": "PT Solusi Digital",
        "vendor_category": "badan",
        "has_skb": False,
        "skb_number": None,
        "skb_expiry": None,
        "umkm_cert_number": None,
        "pph_override_type": None,
    }


@pytest.fixture
def vendor_normal_op():
    """Skenario 4b: Orang Pribadi non-UMKM → PPh 23 = 2%"""
    return {
        "id": "V006",
        "name": "Budi Santoso",
        "vendor_category": "orang_pribadi",
        "has_skb": False,
        "skb_number": None,
        "skb_expiry": None,
        "umkm_cert_number": None,
        "pph_override_type": None,
    }


@pytest.fixture
def vendor_override_manual():
    """Skenario 5: Override manual oleh finance → pakai rate override"""
    return {
        "id": "V007",
        "name": "PT Khusus Override",
        "vendor_category": "badan",
        "has_skb": False,
        "skb_number": None,
        "skb_expiry": None,
        "umkm_cert_number": None,
        "pph_override_type": "exempt",   # atau "rate_1_5", dll
        "pph_override_rate": 0.0,
        "pph_override_reason": "Kontrak khusus 2024",
    }


# ──────────────────────────────────────────
# AP Transaction fixtures
# ──────────────────────────────────────────

@pytest.fixture
def ap_transaction_base():
    """Template transaksi AP dasar"""
    return {
        "id": "TRX-001",
        "vendor_id": "V001",
        "invoice_number": "INV/2024/001",
        "invoice_date": TODAY.isoformat(),
        "gross_amount": 10_000_000,   # Rp 10 juta
        "service_type": "jasa_manajemen",
        "currency": "IDR",
    }


# ──────────────────────────────────────────
# Mock DB fixture
# ──────────────────────────────────────────

@pytest.fixture
def mock_db():
    """Mock database session"""
    db = MagicMock()
    db.execute = MagicMock()
    db.fetchone = MagicMock()
    db.fetchall = MagicMock(return_value=[])
    db.commit = MagicMock()
    return db


@pytest.fixture
def mock_db_async():
    """Mock async database session"""
    db = AsyncMock()
    return db
