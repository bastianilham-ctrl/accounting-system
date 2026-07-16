# modules/ter_tables.py
# Tarif Efektif Rata-rata (TER) PPh Pasal 21 — PP No. 58 Tahun 2023 + PMK 168/2023
#
# PENTING:
#   TER digunakan untuk PEGAWAI TETAP masa Januari s/d November.
#   Bulan Desember → rekonsiliasi tarif Pasal 17 (lihat payroll_engine.py).
#   Untuk BUKAN PEGAWAI (tenaga ahli, honorarium) → lihat pph21_engine.py.
#
# Struktur tabel: list[ (batas_bawah_bruto_bulanan, tarif_persen: Decimal) ]
# Batas bawah INKLUSIF. Lookup: ambil tarif dari batas_bawah TERTINGGI yang ≤ gross.

from decimal import Decimal
from typing import Optional


# ── PTKP Tahunan (PMK 101/PMK.010/2016 — belum ada perubahan s/d 2026) ────────

PTKP = {
    "TK/0": Decimal("54_000_000"),
    "TK/1": Decimal("58_500_000"),
    "TK/2": Decimal("63_000_000"),
    "TK/3": Decimal("67_500_000"),
    "K/0":  Decimal("58_500_000"),
    "K/1":  Decimal("63_000_000"),
    "K/2":  Decimal("67_500_000"),
    "K/3":  Decimal("72_000_000"),
}

# Kategori TER berdasarkan status PTKP (PP 58/2023 Pasal 2)
TER_CATEGORY_MAP = {
    "TK/0": "A",
    "TK/1": "A",
    "K/0":  "A",
    "TK/2": "B",
    "TK/3": "B",
    "K/1":  "B",
    "K/2":  "B",
    "K/3":  "C",
}

# Biaya jabatan: 5% bruto, max Rp 6.000.000/tahun (PMK 250/2008)
BIAYA_JABATAN_RATE  = Decimal("0.05")
BIAYA_JABATAN_MAX   = Decimal("6_000_000")   # per tahun
BIAYA_JABATAN_MAX_M = Decimal("500_000")      # per bulan (6M / 12)


# ── TER Kategori A (TK/0, TK/1, K/0) — PP 58/2023 Lampiran I ─────────────────
# (batas_bawah_inklusif, tarif_persen)

TER_A: list[tuple[int, Decimal]] = [
    (0,               Decimal("0")),
    (5_400_001,       Decimal("0.25")),
    (5_650_001,       Decimal("0.5")),
    (5_950_001,       Decimal("0.75")),
    (6_300_001,       Decimal("1")),
    (6_750_001,       Decimal("1.25")),
    (7_500_001,       Decimal("1.5")),
    (8_550_001,       Decimal("1.75")),
    (9_650_001,       Decimal("2")),
    (10_050_001,      Decimal("2.25")),
    (10_350_001,      Decimal("2.5")),
    (10_700_001,      Decimal("3")),
    (11_050_001,      Decimal("3.5")),
    (11_600_001,      Decimal("4")),
    (12_500_001,      Decimal("5")),
    (13_750_001,      Decimal("6")),
    (15_100_001,      Decimal("7")),
    (16_950_001,      Decimal("8")),
    (19_750_001,      Decimal("9")),
    (24_150_001,      Decimal("10")),
    (26_450_001,      Decimal("11")),
    (28_000_001,      Decimal("12")),
    (30_050_001,      Decimal("13")),
    (32_400_001,      Decimal("14")),
    (35_400_001,      Decimal("15")),
    (39_100_001,      Decimal("16")),
    (43_850_001,      Decimal("17")),
    (47_800_001,      Decimal("18")),
    (51_400_001,      Decimal("19")),
    (56_300_001,      Decimal("20")),
    (62_200_001,      Decimal("21")),
    (68_600_001,      Decimal("22")),
    (77_500_001,      Decimal("23")),
    (89_000_001,      Decimal("24")),
    (103_000_001,     Decimal("25")),
    (125_000_001,     Decimal("26")),
    (157_000_001,     Decimal("27")),
    (206_000_001,     Decimal("28")),
    (337_000_001,     Decimal("29")),
    (454_000_001,     Decimal("30")),
    (550_000_001,     Decimal("31")),
    (695_000_001,     Decimal("32")),
    (910_000_001,     Decimal("33")),
    (1_400_000_001,   Decimal("34")),
]


# ── TER Kategori B (TK/2, TK/3, K/1, K/2) — PP 58/2023 Lampiran II ───────────

TER_B: list[tuple[int, Decimal]] = [
    (0,               Decimal("0")),
    (6_200_001,       Decimal("0.25")),
    (6_500_001,       Decimal("0.5")),
    (6_850_001,       Decimal("0.75")),
    (7_300_001,       Decimal("1")),
    (9_200_001,       Decimal("1.5")),
    (10_750_001,      Decimal("1.75")),
    (11_250_001,      Decimal("2")),
    (11_600_001,      Decimal("3")),
    (12_600_001,      Decimal("4")),
    (13_600_001,      Decimal("5")),
    (14_950_001,      Decimal("6")),
    (16_400_001,      Decimal("7")),
    (18_450_001,      Decimal("8")),
    (21_850_001,      Decimal("9")),
    (26_000_001,      Decimal("10")),
    (27_700_001,      Decimal("11")),
    (29_350_001,      Decimal("12")),
    (31_450_001,      Decimal("13")),
    (33_950_001,      Decimal("14")),
    (37_100_001,      Decimal("15")),
    (41_100_001,      Decimal("16")),
    (45_800_001,      Decimal("17")),
    (49_500_001,      Decimal("18")),
    (53_800_001,      Decimal("19")),
    (58_500_001,      Decimal("20")),
    (64_000_001,      Decimal("21")),
    (71_000_001,      Decimal("22")),
    (80_000_001,      Decimal("23")),
    (93_000_001,      Decimal("24")),
    (109_000_001,     Decimal("25")),
    (129_000_001,     Decimal("26")),
    (163_000_001,     Decimal("27")),
    (211_000_001,     Decimal("28")),
    (374_000_001,     Decimal("29")),
    (459_000_001,     Decimal("30")),
    (555_000_001,     Decimal("31")),
    (704_000_001,     Decimal("32")),
    (957_000_001,     Decimal("33")),
    (1_405_000_001,   Decimal("34")),
]


# ── TER Kategori C (K/3) — PP 58/2023 Lampiran III ────────────────────────────

TER_C: list[tuple[int, Decimal]] = [
    (0,               Decimal("0")),
    (6_600_001,       Decimal("0.25")),
    (6_950_001,       Decimal("0.5")),
    (7_350_001,       Decimal("0.75")),
    (7_800_001,       Decimal("1")),
    (8_850_001,       Decimal("1.25")),
    (9_800_001,       Decimal("1.5")),
    (10_950_001,      Decimal("1.75")),
    (11_200_001,      Decimal("2")),
    (12_050_001,      Decimal("3")),
    (12_950_001,      Decimal("4")),
    (14_150_001,      Decimal("5")),
    (15_550_001,      Decimal("6")),
    (17_050_001,      Decimal("7")),
    (19_500_001,      Decimal("8")),
    (22_700_001,      Decimal("9")),
    (26_600_001,      Decimal("10")),
    (28_100_001,      Decimal("11")),
    (30_100_001,      Decimal("12")),
    (32_600_001,      Decimal("13")),
    (35_400_001,      Decimal("14")),
    (38_900_001,      Decimal("15")),
    (43_000_001,      Decimal("16")),
    (47_400_001,      Decimal("17")),
    (51_200_001,      Decimal("18")),
    (55_800_001,      Decimal("19")),
    (60_400_001,      Decimal("20")),
    (66_700_001,      Decimal("21")),
    (74_500_001,      Decimal("22")),
    (83_200_001,      Decimal("23")),
    (95_600_001,      Decimal("24")),
    (110_000_001,     Decimal("25")),
    (134_000_001,     Decimal("26")),
    (169_000_001,     Decimal("27")),
    (221_000_001,     Decimal("28")),
    (390_000_001,     Decimal("29")),
    (474_000_001,     Decimal("30")),
    (572_000_001,     Decimal("31")),
    (721_000_001,     Decimal("32")),
    (973_000_001,     Decimal("33")),
    (1_009_000_001,   Decimal("34")),
]

_TER_TABLES = {"A": TER_A, "B": TER_B, "C": TER_C}


# ── Public API ─────────────────────────────────────────────────────────────────

def get_ter_category(ptkp_status: str) -> str:
    """
    Mapping status PTKP → Kategori TER (A / B / C).
    Raise ValueError jika status tidak dikenal.
    """
    cat = TER_CATEGORY_MAP.get(ptkp_status.upper())
    if cat is None:
        raise ValueError(
            f"Status PTKP '{ptkp_status}' tidak dikenal. "
            f"Valid: {', '.join(TER_CATEGORY_MAP)}"
        )
    return cat


def ter_lookup(monthly_gross: Decimal, ptkp_status: str) -> Decimal:
    """
    Cari tarif TER (%) dari tabel berdasarkan penghasilan bruto bulanan.

    Return: tarif dalam persen (misal: Decimal("2") berarti 2%).
    """
    cat   = get_ter_category(ptkp_status)
    table = _TER_TABLES[cat]
    gross_int = int(monthly_gross)

    rate = Decimal("0")
    for lb, r in table:
        if gross_int >= lb:
            rate = r
        else:
            break
    return rate


def get_ptkp(ptkp_status: str) -> Decimal:
    """Ambil nilai PTKP tahunan berdasarkan status."""
    val = PTKP.get(ptkp_status.upper())
    if val is None:
        raise ValueError(f"Status PTKP '{ptkp_status}' tidak dikenal.")
    return val
