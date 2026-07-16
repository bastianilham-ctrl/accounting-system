# modules/salary_calculator.py
# Kalkulasi slip gaji standar Indonesia — pendekatan Gross
#
# Regulasi:
#   PP No. 44 Tahun 2015   : BPJS TK (JHT, JP, JKK, JKM)
#   Perpres No. 64 Tahun 2020 : BPJS Kesehatan iuran
#   Perpres No. 109 Tahun 2013 : BPJS JP (plafon diupdate berkala)
#   PP No. 36 Tahun 2021   : JKK rate per kelompok risiko
#   PP No. 58 Tahun 2023   : TER PPh 21
#   PMK No. 168/PMK.010/2023 : Teknis pemotongan PPh 21
#
# RUMUS THP:
#   Bruto PPh21 = Gaji Pokok + Tunjangan Tetap + Tunjangan Variabel
#               + BPJS Kes Perusahaan + BPJS JKK Perusahaan + BPJS JKM Perusahaan
#               (JHT & JP perusahaan: bukan objek PPh 21 bulanan)
#   PPh 21      = Bruto PPh21 × TER rate
#   THP         = (Gaji Pokok + Tunjangan Tetap + Tunjangan Variabel)
#               - BPJS Karyawan (Kes + JHT + JP)
#               - PPh 21
#               - Potongan lain
#   CTC         = Gaji Gross + Total BPJS Perusahaan (semua)

from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass, field
from typing import Optional

# ── Konstanta Regulasi ─────────────────────────────────────────────────────────

# Plafon basis iuran (batas atas yang dijadikan dasar kalkulasi)
BPJS_KES_CAP = Decimal("12_000_000")    # Perpres 64/2020
BPJS_JP_CAP  = Decimal("10_641_400")    # Perpres 109/2013, diperbarui tiap Januari

# Tarif perusahaan (employer)
BPJS_KES_EMP_RATE  = Decimal("0.04")    # 4%
BPJS_JHT_EMP_RATE  = Decimal("0.037")   # 3.7%
BPJS_JP_EMP_RATE   = Decimal("0.020")   # 2.0%
BPJS_JKM_EMP_RATE  = Decimal("0.003")   # 0.3%
# JKK: 0.24% s.d. 1.74% tergantung kelompok risiko industri (PP 44/2015)
BPJS_JKK_DEFAULT   = Decimal("0.0024")  # risiko rendah (default)

# Tarif karyawan (employee)
BPJS_KES_EE_RATE   = Decimal("0.01")    # 1%
BPJS_JHT_EE_RATE   = Decimal("0.02")    # 2.0%
BPJS_JP_EE_RATE    = Decimal("0.01")    # 1.0%

_R = Decimal("1")   # rounding target — bulat ke rupiah


# ── BPJSComponents ─────────────────────────────────────────────────────────────

@dataclass
class BPJSComponents:
    """Seluruh komponen BPJS dari satu karyawan satu bulan."""

    # Basis kalkulasi
    basis_upah:  Decimal     # gaji_pokok + tunjangan_tetap (sebelum cap)
    basis_kes:   Decimal     # min(basis_upah, BPJS_KES_CAP)
    basis_jp:    Decimal     # min(basis_upah, BPJS_JP_CAP)

    # ── Perusahaan ──────────────────────────────────────────────────────────
    kes_employer:   Decimal  # 4%  × basis_kes   — objek PPh 21
    jht_employer:   Decimal  # 3.7% × basis_upah  — BUKAN objek PPh 21 bulanan
    jp_employer:    Decimal  # 2%  × basis_jp    — BUKAN objek PPh 21 bulanan
    jkk_employer:   Decimal  # jkk_rate × basis_upah — objek PPh 21
    jkm_employer:   Decimal  # 0.3% × basis_upah   — objek PPh 21

    # ── Karyawan ────────────────────────────────────────────────────────────
    kes_employee:   Decimal  # 1%  × basis_kes
    jht_employee:   Decimal  # 2%  × basis_upah
    jp_employee:    Decimal  # 1%  × basis_jp

    @property
    def total_employer(self) -> Decimal:
        return (self.kes_employer + self.jht_employer + self.jp_employer
                + self.jkk_employer + self.jkm_employer)

    @property
    def total_employer_obj_pajak(self) -> Decimal:
        """Kes + JKK + JKM — masuk bruto PPh 21 bulanan."""
        return self.kes_employer + self.jkk_employer + self.jkm_employer

    @property
    def total_employer_non_obj_pajak(self) -> Decimal:
        """JHT + JP perusahaan — bukan objek PPh 21 bulanan."""
        return self.jht_employer + self.jp_employer

    @property
    def total_employee(self) -> Decimal:
        return self.kes_employee + self.jht_employee + self.jp_employee

    def to_dict(self) -> dict:
        return {
            "basis_upah":   float(self.basis_upah),
            "basis_kes":    float(self.basis_kes),
            "basis_jp":     float(self.basis_jp),
            "employer": {
                "kes":    float(self.kes_employer),
                "jht":    float(self.jht_employer),
                "jp":     float(self.jp_employer),
                "jkk":    float(self.jkk_employer),
                "jkm":    float(self.jkm_employer),
                "total":  float(self.total_employer),
                "obj_pajak":     float(self.total_employer_obj_pajak),
                "non_obj_pajak": float(self.total_employer_non_obj_pajak),
            },
            "employee": {
                "kes":    float(self.kes_employee),
                "jht":    float(self.jht_employee),
                "jp":     float(self.jp_employee),
                "total":  float(self.total_employee),
            },
        }


def calculate_bpjs(
    gaji_pokok:     Decimal,
    tunjangan_tetap: Decimal,
    jkk_rate:       Decimal = BPJS_JKK_DEFAULT,
) -> BPJSComponents:
    """
    Hitung semua komponen BPJS dari basis upah.

    Basis = gaji_pokok + tunjangan_tetap (jabatan, keluarga)
    TIDAK termasuk: tunjangan makan/transport/lembur (variabel).
    """
    basis = gaji_pokok + tunjangan_tetap
    basis_kes = min(basis, BPJS_KES_CAP)
    basis_jp  = min(basis, BPJS_JP_CAP)

    def r(x: Decimal) -> Decimal:
        return x.quantize(_R, ROUND_HALF_UP)

    return BPJSComponents(
        basis_upah     = basis,
        basis_kes      = basis_kes,
        basis_jp       = basis_jp,
        # Employer
        kes_employer   = r(basis_kes * BPJS_KES_EMP_RATE),
        jht_employer   = r(basis     * BPJS_JHT_EMP_RATE),
        jp_employer    = r(basis_jp  * BPJS_JP_EMP_RATE),
        jkk_employer   = r(basis     * jkk_rate),
        jkm_employer   = r(basis     * BPJS_JKM_EMP_RATE),
        # Employee
        kes_employee   = r(basis_kes * BPJS_KES_EE_RATE),
        jht_employee   = r(basis     * BPJS_JHT_EE_RATE),
        jp_employee    = r(basis_jp  * BPJS_JP_EE_RATE),
    )


# ── SlipGajiResult ─────────────────────────────────────────────────────────────

@dataclass
class SlipGajiResult:
    """
    Hasil kalkulasi slip gaji satu bulan, lengkap dengan THP dan CTC.
    """

    # ── Identitas ───────────────────────────────────────────────────────────
    employee_id:   str
    employee_name: str
    employee_no:   str
    npwp:          str
    ptkp_status:   str
    ter_category:  str
    year:          int
    month:         int

    # ── A. Pendapatan Karyawan (Gaji Gross) ─────────────────────────────────
    gaji_pokok:         Decimal
    tunjangan_tetap:    Decimal   # jabatan + keluarga + tunjangan tetap lainnya
    tunjangan_variabel: Decimal   # makan + transport + lembur
    bonus_thr:          Decimal

    # ── B. BPJS Components ──────────────────────────────────────────────────
    bpjs: BPJSComponents

    # ── C. PPh 21 ────────────────────────────────────────────────────────────
    ter_rate_pct:   Decimal
    pph21:          Decimal
    has_npwp:       bool
    npwp_penalty:   bool

    # ── D. Potongan Lain ────────────────────────────────────────────────────
    potongan_alpha:   Decimal = Decimal("0")  # potongan absensi alpha
    potongan_kasbon:  Decimal = Decimal("0")
    potongan_lainnya: Decimal = Decimal("0")

    # ── Derived properties ──────────────────────────────────────────────────

    @property
    def gaji_gross(self) -> Decimal:
        """Total gaji karyawan sebelum potongan (tidak termasuk BPJS perusahaan)."""
        return self.gaji_pokok + self.tunjangan_tetap + self.tunjangan_variabel + self.bonus_thr

    @property
    def bruto_pph21(self) -> Decimal:
        """
        Basis PPh 21 = gaji_gross + BPJS Kes + JKK + JKM perusahaan.
        JHT dan JP perusahaan tidak termasuk (PMK 168/2023).
        """
        return self.gaji_gross + self.bpjs.total_employer_obj_pajak

    @property
    def total_potongan_bpjs(self) -> Decimal:
        return self.bpjs.total_employee

    @property
    def total_potongan_lain(self) -> Decimal:
        return self.potongan_alpha + self.potongan_kasbon + self.potongan_lainnya

    @property
    def total_potongan(self) -> Decimal:
        return self.total_potongan_bpjs + self.pph21 + self.total_potongan_lain

    @property
    def thp(self) -> Decimal:
        """Take Home Pay = Gaji Gross − BPJS Karyawan − PPh 21 − Potongan Lain."""
        return self.gaji_gross - self.total_potongan

    @property
    def ctc(self) -> Decimal:
        """Cost to Company = Gaji Gross + Total BPJS Perusahaan."""
        return self.gaji_gross + self.bpjs.total_employer

    def to_dict(self) -> dict:
        return {
            "employee_id":   self.employee_id,
            "employee_name": self.employee_name,
            "employee_no":   self.employee_no,
            "npwp":          self.npwp,
            "ptkp_status":   self.ptkp_status,
            "ter_category":  self.ter_category,
            "year":          self.year,
            "month":         self.month,

            # Pendapatan
            "pendapatan": {
                "gaji_pokok":         float(self.gaji_pokok),
                "tunjangan_tetap":    float(self.tunjangan_tetap),
                "tunjangan_variabel": float(self.tunjangan_variabel),
                "bonus_thr":          float(self.bonus_thr),
                "gaji_gross":         float(self.gaji_gross),
            },

            # BPJS
            "bpjs": self.bpjs.to_dict(),

            # PPh 21
            "pph21": {
                "bruto_pph21":    float(self.bruto_pph21),
                "ter_rate_pct":   float(self.ter_rate_pct),
                "pph21_amount":   float(self.pph21),
                "has_npwp":       self.has_npwp,
                "npwp_penalty":   self.npwp_penalty,
            },

            # Potongan
            "potongan": {
                "bpjs_karyawan":     float(self.total_potongan_bpjs),
                "bpjs_kes":          float(self.bpjs.kes_employee),
                "bpjs_jht":          float(self.bpjs.jht_employee),
                "bpjs_jp":           float(self.bpjs.jp_employee),
                "pph21":             float(self.pph21),
                "alpha":             float(self.potongan_alpha),
                "kasbon":            float(self.potongan_kasbon),
                "lainnya":           float(self.potongan_lainnya),
                "total":             float(self.total_potongan),
            },

            # Summary
            "summary": {
                "gaji_gross":        float(self.gaji_gross),
                "total_potongan":    float(self.total_potongan),
                "thp":               float(self.thp),
                "ctc":               float(self.ctc),
                "bpjs_perusahaan":   float(self.bpjs.total_employer),
            },

            "regulation": "PP 44/2015, Perpres 64/2020, PP 58/2023, PMK 168/2023",
        }


# ── Main entry point ───────────────────────────────────────────────────────────

def build_slip_gaji(
    employee_id:        str,
    employee_name:      str,
    employee_no:        str,
    npwp:               str,
    ptkp_status:        str,
    ter_category:       str,
    year:               int,
    month:              int,
    gaji_pokok:         Decimal,
    tunjangan_tetap:    Decimal,        # jabatan + keluarga + lain (tetap)
    tunjangan_variabel: Decimal,        # makan + transport + lembur
    bonus_thr:          Decimal,
    jkk_rate:           Decimal,
    ter_rate_pct:       Decimal,        # dari tabel TER (salary_calculator tidak menghitung TER)
    pph21:              Decimal,        # hasil dari payroll_engine / TER lookup
    has_npwp:           bool = True,
    npwp_penalty:       bool = False,
    potongan_alpha:     Decimal = Decimal("0"),
    potongan_kasbon:    Decimal = Decimal("0"),
    potongan_lainnya:   Decimal = Decimal("0"),
) -> SlipGajiResult:
    """
    Bangun SlipGajiResult dari komponen yang diberikan.
    PPh 21 dihitung di luar (oleh payroll_engine) karena memerlukan logika TER/Desember.
    """
    bpjs = calculate_bpjs(gaji_pokok, tunjangan_tetap, jkk_rate)

    return SlipGajiResult(
        employee_id        = employee_id,
        employee_name      = employee_name,
        employee_no        = employee_no,
        npwp               = npwp or "",
        ptkp_status        = ptkp_status,
        ter_category       = ter_category,
        year               = year,
        month              = month,
        gaji_pokok         = gaji_pokok,
        tunjangan_tetap    = tunjangan_tetap,
        tunjangan_variabel = tunjangan_variabel,
        bonus_thr          = bonus_thr,
        bpjs               = bpjs,
        ter_rate_pct       = ter_rate_pct,
        pph21              = pph21,
        has_npwp           = has_npwp,
        npwp_penalty       = npwp_penalty,
        potongan_alpha     = potongan_alpha,
        potongan_kasbon    = potongan_kasbon,
        potongan_lainnya   = potongan_lainnya,
    )


def simulate_slip_gaji(
    gaji_pokok:         float,
    tunjangan_tetap:    float,
    tunjangan_variabel: float,
    ptkp_status:        str,
    jkk_rate:           float = 0.0024,
    bonus_thr:          float = 0,
    has_npwp:           bool  = True,
    potongan_lain:      float = 0,
) -> dict:
    """
    Hitung simulasi slip gaji tanpa data karyawan di DB.
    Dipakai untuk endpoint kalkulator publik.
    """
    from modules.ter_tables import ter_lookup, get_ter_category

    gp   = Decimal(str(gaji_pokok))
    tt   = Decimal(str(tunjangan_tetap))
    tv   = Decimal(str(tunjangan_variabel))
    thr  = Decimal(str(bonus_thr))
    jkk  = Decimal(str(jkk_rate))
    pot  = Decimal(str(potongan_lain))

    bpjs = calculate_bpjs(gp, tt, jkk)

    # Bruto PPh21
    bruto_pph21 = gp + tt + tv + thr + bpjs.total_employer_obj_pajak

    ter_rate = ter_lookup(bruto_pph21, ptkp_status)
    pph21    = (bruto_pph21 * ter_rate / 100).quantize(_R, ROUND_HALF_UP)
    if not has_npwp:
        pph21 = (pph21 * Decimal("1.20")).quantize(_R, ROUND_HALF_UP)

    result = build_slip_gaji(
        employee_id        = "SIMULASI",
        employee_name      = "Simulasi",
        employee_no        = "-",
        npwp               = "" if not has_npwp else "XX.XXX.XXX.X-XXX.XXX",
        ptkp_status        = ptkp_status,
        ter_category       = get_ter_category(ptkp_status),
        year               = 0,
        month              = 0,
        gaji_pokok         = gp,
        tunjangan_tetap    = tt,
        tunjangan_variabel = tv,
        bonus_thr          = thr,
        jkk_rate           = jkk,
        ter_rate_pct       = ter_rate,
        pph21              = pph21,
        has_npwp           = has_npwp,
        npwp_penalty       = not has_npwp,
        potongan_lainnya   = pot,
    )
    return result.to_dict()
