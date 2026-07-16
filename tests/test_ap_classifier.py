"""
test_ap_classifier.py  — connect ke kode ASLI modules/ap_classifier.py
=======================================================================
APClassifier adalah class dengan method .classify() dan ._determine_pph_treatment()
Test fokus ke _determine_pph_treatment() karena itulah inti logika PPh.

Skenario:
    1. SKB aktif            → treatment=bebas_pph,        rate=0,   needs_review=False
    2. SKB expired          → treatment=skb_expired,      rate=2%,  needs_review=True
    3. UMKM aktif           → treatment=pph_final_umkm,   rate=0.5, needs_review=False
    4. UMKM cert expired    → treatment=umkm_cert_expired, rate=2%, needs_review=True
    5. Override manual      → treatment=override_manual,  rate=sesuai override
    6. PPh23 normal         → treatment=pph23_normal,     rate=2%
    7. Vendor baru (belum review) → needs_review=True
    8. Edge cases: amount=0, SKB expiry=hari ini
"""
import sys
import os
import pytest
from decimal import Decimal
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

# ── Tambah root project ke path ──────────────────────────────────────────────
# Sesuaikan jika struktur folder berbeda
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ── Import kode asli ─────────────────────────────────────────────────────────
from modules.ap_classifier import APClassifier

# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

TODAY      = date.today()
FUTURE_90  = TODAY + timedelta(days=90)
FUTURE_20  = TODAY + timedelta(days=20)
EXPIRED_1  = TODAY - timedelta(days=1)
EXPIRED_30 = TODAY - timedelta(days=30)
AMOUNT     = Decimal("10000000")   # Rp 10 juta


@pytest.fixture
def classifier():
    """APClassifier dengan mock DB session — tidak butuh DB nyata."""
    mock_db = MagicMock()
    # Default: vendor_services kosong, tidak ada match DB
    mock_db.execute.return_value.fetchall.return_value = []
    mock_db.execute.return_value.fetchone.return_value = None
    return APClassifier(db=mock_db)


def make_profile(**kwargs) -> dict:
    """Helper buat tax profile dict, override field sesuai kebutuhan."""
    base = {
        "has_skb": False,
        "skb_number": None,
        "skb_expiry": None,
        "vendor_category": "PT",
        "umkm_cert_number": None,
        "umkm_cert_expiry": None,
        "pph_override_type": None,
        "pph_override_rate": None,
        "pph_override_reason": None,
        "default_pph_type": "PPh23",
        "default_pph_rate": 2.0,
        "tax_reviewed_at": TODAY,   # sudah pernah review
    }
    base.update(kwargs)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# SKENARIO 1: SKB aktif
# ─────────────────────────────────────────────────────────────────────────────

class TestSkenario1_SKBAktif:

    def test_treatment_bebas_pph(self, classifier):
        profile = make_profile(has_skb=True, skb_number="SKB-001", skb_expiry=FUTURE_90)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["treatment"] == "bebas_pph"

    def test_rate_nol(self, classifier):
        profile = make_profile(has_skb=True, skb_expiry=FUTURE_90)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["pph_rate"] == 0.0

    def test_pph_amount_nol(self, classifier):
        profile = make_profile(has_skb=True, skb_expiry=FUTURE_90)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["pph_amount"] == Decimal("0")

    def test_needs_review_false(self, classifier):
        profile = make_profile(has_skb=True, skb_expiry=FUTURE_90)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["needs_review"] is False

    def test_note_mengandung_nomor_skb(self, classifier):
        profile = make_profile(has_skb=True, skb_number="SKB-TEST-001", skb_expiry=FUTURE_90)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert "SKB-TEST-001" in result["note"]

    def test_skb_expiry_hari_ini_masih_berlaku(self, classifier):
        """skb_expiry == today → masih valid (>=)"""
        profile = make_profile(has_skb=True, skb_expiry=TODAY)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["treatment"] == "bebas_pph"

    def test_skb_tanpa_expiry_tetap_bebas(self, classifier):
        """has_skb=True tapi skb_expiry=None → bebas (tidak ada batas)"""
        profile = make_profile(has_skb=True, skb_expiry=None)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["treatment"] == "bebas_pph"


# ─────────────────────────────────────────────────────────────────────────────
# SKENARIO 2: SKB expired
# ─────────────────────────────────────────────────────────────────────────────

class TestSkenario2_SKBExpired:

    def test_treatment_skb_expired(self, classifier):
        profile = make_profile(has_skb=True, skb_expiry=EXPIRED_30)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["treatment"] == "skb_expired"

    def test_needs_review_true(self, classifier):
        profile = make_profile(has_skb=True, skb_expiry=EXPIRED_30)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["needs_review"] is True

    def test_fallback_rate_2pct(self, classifier):
        profile = make_profile(has_skb=True, skb_expiry=EXPIRED_30)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["pph_rate"] == 2.0

    def test_pph_amount_fallback_benar(self, classifier):
        """Rp 10juta × 2% = Rp 200.000"""
        profile = make_profile(has_skb=True, skb_expiry=EXPIRED_1)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["pph_amount"] == pytest.approx(Decimal("200000"), rel=Decimal("1e-6"))

    def test_note_mengandung_tanggal_expired(self, classifier):
        profile = make_profile(has_skb=True, skb_expiry=EXPIRED_30)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert str(EXPIRED_30) in result["note"]


# ─────────────────────────────────────────────────────────────────────────────
# SKENARIO 3: UMKM aktif
# ─────────────────────────────────────────────────────────────────────────────

class TestSkenario3_UMKMAktif:

    def test_treatment_pph_final_umkm(self, classifier):
        profile = make_profile(
            vendor_category="UMKM",
            umkm_cert_number="UMKM-001",
            umkm_cert_expiry=FUTURE_90,
        )
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["treatment"] == "pph_final_umkm"

    def test_rate_0_5_persen(self, classifier):
        profile = make_profile(vendor_category="UMKM", umkm_cert_expiry=FUTURE_90)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["pph_rate"] == 0.5

    def test_pph_amount_benar(self, classifier):
        """Rp 10juta × 0.5% = Rp 50.000"""
        profile = make_profile(vendor_category="UMKM", umkm_cert_expiry=FUTURE_90)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["pph_amount"] == pytest.approx(Decimal("50000"), rel=Decimal("1e-6"))

    def test_needs_review_false(self, classifier):
        profile = make_profile(vendor_category="UMKM", umkm_cert_expiry=FUTURE_90)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["needs_review"] is False

    def test_pph_type_pph_final_umkm(self, classifier):
        profile = make_profile(vendor_category="UMKM", umkm_cert_expiry=FUTURE_90)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["pph_type"] == "PPh_Final_UMKM"


# ─────────────────────────────────────────────────────────────────────────────
# SKENARIO 4: UMKM cert expired
# ─────────────────────────────────────────────────────────────────────────────

class TestSkenario4_UMKMExpired:

    def test_treatment_umkm_cert_expired(self, classifier):
        profile = make_profile(
            vendor_category="UMKM",
            umkm_cert_number="UMKM-OLD",
            umkm_cert_expiry=EXPIRED_30,
        )
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["treatment"] == "umkm_cert_expired"

    def test_needs_review_true(self, classifier):
        profile = make_profile(vendor_category="UMKM", umkm_cert_expiry=EXPIRED_1)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["needs_review"] is True

    def test_fallback_rate_2pct(self, classifier):
        profile = make_profile(vendor_category="UMKM", umkm_cert_expiry=EXPIRED_1)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["pph_rate"] == 2.0

    def test_note_mengandung_tanggal_expired(self, classifier):
        profile = make_profile(vendor_category="UMKM", umkm_cert_expiry=EXPIRED_30)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert str(EXPIRED_30) in result["note"]


# ─────────────────────────────────────────────────────────────────────────────
# SKENARIO 5: Override manual
# ─────────────────────────────────────────────────────────────────────────────

class TestSkenario5_OverrideManual:

    def test_treatment_override_manual(self, classifier):
        profile = make_profile(
            pph_override_type="PPh26",
            pph_override_rate=20.0,
            pph_override_reason="Vendor asing — tarif P3B Indonesia-Singapura",
        )
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["treatment"] == "override_manual"

    def test_rate_sesuai_override(self, classifier):
        profile = make_profile(
            pph_override_type="PPh26",
            pph_override_rate=15.0,
            pph_override_reason="P3B rate",
        )
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["pph_rate"] == 15.0

    def test_pph_amount_sesuai_rate_override(self, classifier):
        """Rp 10juta × 15% = Rp 1.500.000"""
        profile = make_profile(
            pph_override_type="PPh26",
            pph_override_rate=15.0,
            pph_override_reason="P3B",
        )
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["pph_amount"] == pytest.approx(Decimal("1500000"), rel=Decimal("1e-6"))

    def test_override_exempt_rate_nol(self, classifier):
        profile = make_profile(
            pph_override_type="bebas",
            pph_override_rate=0.0,
            pph_override_reason="Kontrak khusus",
        )
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["pph_rate"] == 0.0

    def test_note_mengandung_alasan(self, classifier):
        profile = make_profile(
            pph_override_type="PPh26",
            pph_override_rate=15.0,
            pph_override_reason="Tarif P3B Indonesia-Singapura",
        )
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert "Tarif P3B Indonesia-Singapura" in result["note"]


# ─────────────────────────────────────────────────────────────────────────────
# SKENARIO 6: PPh23 normal
# ─────────────────────────────────────────────────────────────────────────────

class TestSkenario6_PPh23Normal:

    def test_treatment_pph23_normal(self, classifier):
        profile = make_profile()   # default: PT, no SKB, no UMKM, no override
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["treatment"] == "pph23_normal"

    def test_rate_2pct(self, classifier):
        profile = make_profile()
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["pph_rate"] == 2.0

    def test_pph_amount_benar(self, classifier):
        """Rp 10juta × 2% = Rp 200.000"""
        profile = make_profile()
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["pph_amount"] == pytest.approx(Decimal("200000"), rel=Decimal("1e-6"))

    def test_needs_review_false_jika_sudah_direviewed(self, classifier):
        profile = make_profile(tax_reviewed_at=TODAY)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["needs_review"] is False


# ─────────────────────────────────────────────────────────────────────────────
# SKENARIO 7: Vendor baru (belum pernah di-review)
# ─────────────────────────────────────────────────────────────────────────────

class TestSkenario7_VendorBaru:

    def test_needs_review_true_jika_belum_direviewed(self, classifier):
        profile = make_profile(tax_reviewed_at=None)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["needs_review"] is True

    def test_rate_tetap_2pct_meski_belum_review(self, classifier):
        profile = make_profile(tax_reviewed_at=None)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["pph_rate"] == 2.0

    def test_note_mengandung_peringatan_konfirmasi(self, classifier):
        profile = make_profile(tax_reviewed_at=None)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert "konfirmasi" in result["note"].lower() or "review" in result["note"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# EDGE CASES
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_amount_nol_tidak_error(self, classifier):
        profile = make_profile()
        result = classifier._determine_pph_treatment(profile, Decimal("0"))
        assert result["pph_amount"] == Decimal("0")

    def test_amount_sangat_besar(self, classifier):
        """Rp 1 Miliar × 2% = Rp 20 juta"""
        profile = make_profile()
        result = classifier._determine_pph_treatment(profile, Decimal("1000000000"))
        assert result["pph_amount"] == pytest.approx(Decimal("20000000"), rel=Decimal("1e-6"))

    def test_umkm_tanpa_expiry_tetap_aktif(self, classifier):
        """UMKM dengan umkm_cert_expiry=None → tidak dianggap expired"""
        profile = make_profile(vendor_category="UMKM", umkm_cert_expiry=None)
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["treatment"] == "pph_final_umkm"

    def test_prioritas_skb_lebih_tinggi_dari_umkm(self, classifier):
        """Jika vendor UMKM sekaligus punya SKB aktif → SKB yang menang"""
        profile = make_profile(
            has_skb=True, skb_expiry=FUTURE_90,
            vendor_category="UMKM", umkm_cert_expiry=FUTURE_90,
        )
        result = classifier._determine_pph_treatment(profile, AMOUNT)
        assert result["treatment"] == "bebas_pph", \
            "SKB harus lebih prioritas dari UMKM"
