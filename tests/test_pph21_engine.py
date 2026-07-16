# tests/test_pph21_engine.py
# Unit tests untuk PPh 21 engine — pure functions, tidak butuh DB
# Jalankan: pytest tests/test_pph21_engine.py -v

import pytest
from decimal import Decimal
from modules.pph21_engine import calculate_pph21, is_tenaga_ahli, _progressive_tax


# ── Helper ────────────────────────────────────────────────────────────────────

def _pph(gross, has_npwp=True, is_ahli=True, ytd=0):
    return calculate_pph21(
        gross_amount     = Decimal(str(gross)),
        has_npwp         = has_npwp,
        is_tenaga_ahli   = is_ahli,
        ytd_gross_before = Decimal(str(ytd)),
    )


# ── Tenaga ahli — bracket 5% (PKP s/d 60 juta) ───────────────────────────────

class TestTenagaAhliSinglePayment:

    def test_pkp_dalam_bracket_5pct(self):
        # Gross 10 juta, PKP = 5 juta → PPh = 5% × 5 juta = 250.000
        r = _pph(10_000_000)
        assert r["pkp"]          == 5_000_000.0
        assert r["pph21_amount"] == 250_000.0

    def test_pkp_pas_batas_bracket_5pct(self):
        # Gross 120 juta, PKP = 60 juta → PPh = 5% × 60 juta = 3.000.000
        r = _pph(120_000_000)
        assert r["pkp"]          == 60_000_000.0
        assert r["pph21_amount"] == 3_000_000.0

    def test_pkp_melewati_bracket_15pct(self):
        # Gross 200 juta, PKP = 100 juta
        # PPh = 5%×60 + 15%×40 = 3.000.000 + 6.000.000 = 9.000.000
        r = _pph(200_000_000)
        assert r["pkp"]          == 100_000_000.0
        assert r["pph21_amount"] == 9_000_000.0

    def test_pkp_bracket_25pct(self):
        # Gross 700 juta, PKP = 350 juta
        # PPh = 5%×60 + 15%×190 + 25%×100 = 3M + 28.5M + 25M = 56.500.000
        r = _pph(700_000_000)
        assert r["pkp"]          == 350_000_000.0
        assert r["pph21_amount"] == 56_500_000.0

    def test_pkp_bracket_30pct(self):
        # Gross 2 milyar, PKP = 1 milyar
        # PPh = 5%×60 + 15%×190 + 25%×250 + 30%×500 = 3M + 28.5M + 62.5M + 150M = 244.000.000
        r = _pph(2_000_000_000)
        assert r["pkp"]          == 1_000_000_000.0
        assert r["pph21_amount"] == 244_000_000.0

    def test_pkp_bracket_35pct(self):
        # Gross 12 milyar, PKP = 6 milyar
        # PPh = 5%×60 + 15%×190 + 25%×250 + 30%×4500 + 35%×1000
        # = 3M + 28.5M + 62.5M + 1350M + 350M = 1.794.000.000
        r = _pph(12_000_000_000)
        assert r["pkp"]          == 6_000_000_000.0
        assert r["pph21_amount"] == 1_794_000_000.0


# ── Non-tenaga ahli (PKP = bruto) ─────────────────────────────────────────────

class TestNonTenagaAhli:

    def test_honorarium_kecil(self):
        # Gross 5 juta, PKP = 5 juta → PPh = 5% × 5 juta = 250.000
        r = _pph(5_000_000, is_ahli=False)
        assert r["pkp"]          == 5_000_000.0
        assert r["pph21_amount"] == 250_000.0

    def test_honorarium_medium(self):
        # Gross 100 juta, PKP = 100 juta
        # PPh = 5%×60 + 15%×40 = 3M + 6M = 9.000.000
        r = _pph(100_000_000, is_ahli=False)
        assert r["pkp"]          == 100_000_000.0
        assert r["pph21_amount"] == 9_000_000.0

    def test_pkp_factor_beda_antara_ahli_dan_non(self):
        # Gross sama, PPh ahli < non-ahli (karena PKP ahli = 50% gross)
        ahli    = _pph(100_000_000, is_ahli=True)
        non_ahli = _pph(100_000_000, is_ahli=False)
        assert ahli["pph21_amount"] < non_ahli["pph21_amount"]


# ── Tarif lebih tinggi tanpa NPWP ─────────────────────────────────────────────

class TestTanpaNPWP:

    def test_tanpa_npwp_lebih_tinggi(self):
        dengan_npwp  = _pph(10_000_000, has_npwp=True)
        tanpa_npwp   = _pph(10_000_000, has_npwp=False)
        # Tanpa NPWP = ×1.20
        assert round(tanpa_npwp["pph21_amount"]) == round(dengan_npwp["pph21_amount"] * 1.2)

    def test_tanpa_npwp_flag_di_response(self):
        r = _pph(10_000_000, has_npwp=False)
        assert r["has_npwp"]      is False
        assert r["npwp_penalty"]  is True

    def test_dengan_npwp_flag_di_response(self):
        r = _pph(10_000_000, has_npwp=True)
        assert r["has_npwp"]      is True
        assert r["npwp_penalty"]  is False


# ── YTD cumulative — skenario dua pembayaran ke orang sama ───────────────────

class TestYTDCumulative:

    def test_pembayaran_pertama_masuk_bracket_5pct(self):
        # Gross 40 juta, YTD before 0 → PKP = 20 juta → 5% = 1.000.000
        r = _pph(40_000_000, ytd=0)
        assert r["pph21_amount"] == 1_000_000.0

    def test_pembayaran_kedua_sudah_di_bracket_15pct(self):
        # YTD before = 160 juta, gross = 40 juta
        # PKP before  = 80 juta → PPh before  = 5%×60 + 15%×20 = 6.000.000
        # PKP after   = 100 juta → PPh after  = 5%×60 + 15%×40 = 9.000.000
        # PPh potong  = 9.000.000 - 6.000.000 = 3.000.000
        r = _pph(40_000_000, ytd=160_000_000)
        assert r["pph21_amount"] == 3_000_000.0

    def test_ytd_gross_after_terakumulasi(self):
        r = _pph(50_000_000, ytd=100_000_000)
        assert r["ytd_gross_after"] == 150_000_000.0

    def test_tidak_ada_double_potong_bracket_sama(self):
        # Jika YTD before dan after masih di bracket yang sama,
        # PPh incremental harus benar
        r = _pph(10_000_000, ytd=10_000_000)
        # PKP before = 5 juta, PKP after = 10 juta
        # PPh after = 5%×10M = 500K; PPh before = 5%×5M = 250K; delta = 250K
        assert r["pph21_amount"] == 250_000.0


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_gross_nol(self):
        r = _pph(0)
        assert r["pph21_amount"]   == 0.0
        assert r["effective_rate"] == 0.0

    def test_gross_sangat_kecil(self):
        # Gross 1.000 → PKP 500 → PPh 5%×500 = 25 (dibulatkan)
        r = _pph(1_000)
        assert r["pph21_amount"] == 25.0

    def test_effective_rate_dalam_range(self):
        r = _pph(100_000_000)
        assert 0 < r["effective_rate"] < 100

    def test_response_memuat_semua_key(self):
        r = _pph(50_000_000)
        for key in ("gross_amount", "pkp", "pph21_amount", "effective_rate",
                    "has_npwp", "npwp_penalty", "tax_breakdown",
                    "ytd_gross_after", "regulation"):
            assert key in r, f"Missing key: {key}"

    def test_tax_breakdown_tidak_kosong(self):
        r = _pph(100_000_000)
        assert len(r["tax_breakdown"]) >= 1

    def test_regulation_ref_ada(self):
        r = _pph(50_000_000)
        assert "PMK 168" in r["regulation"] or "UU HPP" in r["regulation"]


# ── is_tenaga_ahli detector ───────────────────────────────────────────────────

class TestIsAhliDetector:

    @pytest.mark.parametrize("desc,expected", [
        ("Jasa Konsultansi IT",          True),
        ("Honorarium Notaris",           True),
        ("Professional Fee Pengacara",   True),
        ("Jasa Dokter Perusahaan",       True),
        ("Audit Fee Akuntansi",          True),
        ("Advisory Services",            True),
        ("Pembelian ATK",                False),
        ("Sewa Kantor",                  False),
        ("Transport",                    False),
        ("",                             False),
    ])
    def test_detection(self, desc, expected):
        assert is_tenaga_ahli(desc) is expected


# ── _progressive_tax pure function ───────────────────────────────────────────

class TestProgressiveTax:

    def test_pkp_nol(self):
        total, breakdown = _progressive_tax(Decimal("0"))
        assert total == 0
        assert breakdown == []

    def test_pkp_negatif(self):
        total, _ = _progressive_tax(Decimal("-1000"))
        assert total == 0

    def test_breakdown_akurat(self):
        # PKP = 100 juta: 60M×5% + 40M×15%
        total, breakdown = _progressive_tax(Decimal("100_000_000"))
        assert len(breakdown) == 2
        assert breakdown[0]["rate_pct"]      == 5.0
        assert breakdown[0]["taxable_amount"] == 60_000_000.0
        assert breakdown[1]["rate_pct"]      == 15.0
        assert breakdown[1]["taxable_amount"] == 40_000_000.0
        assert float(total) == 9_000_000.0
