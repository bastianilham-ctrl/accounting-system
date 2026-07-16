# modules/payroll_engine.py
# Mesin perhitungan PPh 21 Pegawai Tetap
#
# Referensi regulasi:
#   PP No. 58 Tahun 2023        : TER (Tarif Efektif Rata-rata) untuk masa Jan-Nov
#   PMK No. 168/PMK.010/2023    : Teknis pemotongan PPh 21
#   UU HPP 2021 Pasal 17        : Tarif progresif untuk rekonsiliasi Desember
#   PMK 250/PMK.03/2008         : Biaya jabatan (5%, maks Rp 6 juta/tahun)
#
# ALUR:
#   Masa Jan-Nov : PPh 21 = bruto_bulanan × tarif_TER (kategori A/B/C)
#   Masa Des     : PPh 21 = PPh_terutang_setahun − Σ PPh_TER_Jan_Nov
#                  PPh_terutang_setahun = progresif(PKP)
#                  PKP = bruto_setahun − biaya_jabatan − iuran_jht − PTKP

from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass, field
from typing import Optional

from modules.ter_tables import (
    ter_lookup, get_ptkp, get_ter_category,
    BIAYA_JABATAN_RATE, BIAYA_JABATAN_MAX, BIAYA_JABATAN_MAX_M,
)
from modules.pph21_engine import _progressive_tax  # re-use progressive calc untuk Desember
from modules.salary_calculator import (
    calculate_bpjs, build_slip_gaji, BPJSComponents,
    BPJS_JKK_DEFAULT,
)


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class PayrollInput:
    """
    Komponen penghasilan bruto satu bulan untuk satu karyawan.

    Pemisahan tunjangan penting untuk BPJS basis:
      Basis BPJS = gaji_pokok + tunjangan_tetap (jabatan, keluarga, dll.)
      Bruto PPh21 = basis BPJS + tunjangan_variabel + bonus_thr
                  + BPJS Kes + JKK + JKM perusahaan
    """
    gaji_pokok:         Decimal
    # Tunjangan TETAP (masuk basis BPJS)
    tunjangan_tetap:    Decimal = Decimal("0")   # jabatan, keluarga, dll.
    # Tunjangan VARIABEL (tidak masuk basis BPJS, tapi masuk bruto PPh21)
    tunjangan_variabel: Decimal = Decimal("0")   # makan + transport + lembur (gabungan)
    lembur:             Decimal = Decimal("0")   # diperhitungkan terpisah untuk kejelasan
    bonus_thr:          Decimal = Decimal("0")

    # BPJS perusahaan — jika None, auto-kalkulasi dari gaji_pokok + tunjangan_tetap
    jkk_rate:           Decimal = BPJS_JKK_DEFAULT
    _bpjs:              Optional[BPJSComponents] = field(default=None, repr=False)

    # Potongan karyawan (hanya dipakai saat rekonsiliasi Desember)
    iuran_jht_karyawan:     Decimal = Decimal("0")
    iuran_pensiun_karyawan: Decimal = Decimal("0")

    # ── Backward-compat: legacy callers yang pass BPJS sebagai amounts ──────
    premi_bpjs_kesehatan: Optional[Decimal] = None  # override kes_employer
    premi_bpjs_jkk:       Optional[Decimal] = None  # override jkk_employer
    premi_bpjs_jkm:       Optional[Decimal] = None  # override jkm_employer

    def get_bpjs(self) -> BPJSComponents:
        """Kembalikan BPJSComponents — auto-calc jika belum di-set."""
        if self._bpjs is not None:
            return self._bpjs
        bpjs = calculate_bpjs(self.gaji_pokok, self.tunjangan_tetap, self.jkk_rate)
        # Override dengan nilai manual jika disupply
        if self.premi_bpjs_kesehatan is not None:
            object.__setattr__(bpjs, "kes_employer", self.premi_bpjs_kesehatan)
        if self.premi_bpjs_jkk is not None:
            object.__setattr__(bpjs, "jkk_employer", self.premi_bpjs_jkk)
        if self.premi_bpjs_jkm is not None:
            object.__setattr__(bpjs, "jkm_employer", self.premi_bpjs_jkm)
        return bpjs

    @property
    def total_tunjangan_variabel(self) -> Decimal:
        return self.tunjangan_variabel + self.lembur

    @property
    def bruto_bulanan(self) -> Decimal:
        """
        Bruto untuk basis PPh 21 TER:
          = gaji_pokok + tunjangan_tetap + tunjangan_variabel + lembur + bonus_thr
          + BPJS Kes + JKK + JKM perusahaan (objek PPh 21)
        JHT dan JP perusahaan tidak termasuk (PMK 168/2023).
        """
        bpjs = self.get_bpjs()
        return (
            self.gaji_pokok
            + self.tunjangan_tetap
            + self.total_tunjangan_variabel
            + self.bonus_thr
            + bpjs.total_employer_obj_pajak   # Kes + JKK + JKM employer
        )

    @property
    def pengurang_karyawan(self) -> Decimal:
        """Iuran karyawan — hanya dipakai saat rekonsiliasi Desember."""
        return self.iuran_jht_karyawan + self.iuran_pensiun_karyawan


@dataclass
class MonthlyPayrollResult:
    """Hasil kalkulasi PPh 21 satu bulan."""
    employee_id:    str
    month:          int
    year:           int
    ptkp_status:    str
    ter_category:   str

    bruto_bulanan:  Decimal
    ter_rate_pct:   Decimal
    pph21_amount:   Decimal

    has_npwp:       bool
    npwp_penalty:   bool

    method:         str   # "TER" | "Desember_Rekonsiliasi"
    notes:          str   = ""


@dataclass
class DecemberReconciliation:
    """Hasil rekonsiliasi PPh 21 bulan Desember."""
    employee_id:          str
    year:                 int
    ptkp_status:          str

    bruto_jan_nov:        Decimal  # total bruto Jan-Nov
    bruto_desember:       Decimal  # bruto bulan Desember
    bruto_setahun:        Decimal  # total bruto setahun

    biaya_jabatan:        Decimal  # min(5%×bruto, 6 juta)
    iuran_jht_pensiun:    Decimal  # dibayar karyawan, sepanjang tahun
    penghasilan_netto:    Decimal  # bruto - biaya_jabatan - iuran
    ptkp:                 Decimal
    pkp:                  Decimal  # max(netto - ptkp, 0)

    pph_terutang_setahun: Decimal  # progresif Pasal 17
    pph_ter_jan_nov:      Decimal  # sudah dipotong Jan-Nov
    pph_desember:         Decimal  # = terutang - jan_nov (bisa negatif = lebih potong)

    has_npwp:             bool
    npwp_penalty:         bool
    tax_breakdown:        list = field(default_factory=list)


# ── Core calculation functions ─────────────────────────────────────────────────

def calculate_monthly_ter(
    payroll:      PayrollInput,
    ptkp_status:  str,
    month:        int,
    has_npwp:     bool = True,
    employee_id:  str  = "",
) -> MonthlyPayrollResult:
    """
    Hitung PPh 21 bulan Januari s/d November menggunakan metode TER.
    TER: tarif flat dari tabel lookup, bukan progresif.

    Args:
        payroll     : komponen gaji bulan ini
        ptkp_status : status PTKP karyawan ("TK/0", "K/1", dll.)
        month       : 1–11 (Desember gunakan calculate_december_reconciliation)
        has_npwp    : False → PPh × 1.20 (PMK 168/2023 Pasal 24)
    """
    if not (1 <= month <= 11):
        raise ValueError("Metode TER hanya untuk bulan 1–11. Bulan 12 gunakan rekonsiliasi Desember.")

    bruto  = payroll.bruto_bulanan
    rate   = ter_lookup(bruto, ptkp_status)         # tarif dalam persen
    pph    = (bruto * rate / 100).quantize(Decimal("1"), ROUND_HALF_UP)

    if not has_npwp:
        pph = (pph * Decimal("1.20")).quantize(Decimal("1"), ROUND_HALF_UP)

    return MonthlyPayrollResult(
        employee_id  = employee_id,
        month        = month,
        year         = 0,  # diisi oleh caller
        ptkp_status  = ptkp_status,
        ter_category = get_ter_category(ptkp_status),
        bruto_bulanan = bruto,
        ter_rate_pct  = rate,
        pph21_amount  = pph,
        has_npwp      = has_npwp,
        npwp_penalty  = not has_npwp,
        method        = "TER",
        notes = (
            f"TER Kategori {get_ter_category(ptkp_status)} | "
            f"Bruto Rp {bruto:,.0f} × {rate}% = Rp {pph:,.0f}"
            + (" | Tanpa NPWP ×1.20" if not has_npwp else "")
        ),
    )


def calculate_december_reconciliation(
    bruto_per_bulan:    list[Decimal],      # bruto Jan-Des (12 elemen)
    iuran_jht_per_bulan: list[Decimal],     # iuran JHT karyawan Jan-Des
    iuran_pensiun_per_bulan: list[Decimal], # iuran pensiun karyawan Jan-Des
    pph_ter_jan_nov:    Decimal,            # total PPh yang sudah dipotong Jan-Nov
    ptkp_status:        str,
    has_npwp:           bool = True,
    employee_id:        str  = "",
    year:               int  = 0,
) -> DecemberReconciliation:
    """
    Rekonsiliasi PPh 21 bulan Desember (Masa pajak terakhir).

    Alur:
    1. Hitung total bruto setahun (Jan-Des)
    2. Hitung biaya jabatan = min(5% × bruto_setahun, 6.000.000)
    3. Hitung penghasilan netto = bruto - biaya_jabatan - iuran_jht_pensiun
    4. PKP = max(netto - PTKP, 0)
    5. PPh terutang setahun = progresif Pasal 17 pada PKP
    6. PPh Desember = PPh terutang - PPh TER Jan-Nov
    """
    if len(bruto_per_bulan) != 12:
        raise ValueError("bruto_per_bulan harus 12 elemen (Jan-Des)")

    bruto_jan_nov = sum(bruto_per_bulan[:11], Decimal("0"))
    bruto_des     = bruto_per_bulan[11]
    bruto_setahun = bruto_jan_nov + bruto_des

    total_iuran = (
        sum(iuran_jht_per_bulan,    Decimal("0"))
        + sum(iuran_pensiun_per_bulan, Decimal("0"))
    )

    biaya_jabatan = min(
        (bruto_setahun * BIAYA_JABATAN_RATE).quantize(Decimal("1"), ROUND_HALF_UP),
        BIAYA_JABATAN_MAX,
    )

    penghasilan_netto = bruto_setahun - biaya_jabatan - total_iuran
    ptkp_val          = get_ptkp(ptkp_status)
    pkp               = max(penghasilan_netto - ptkp_val, Decimal("0"))

    pph_terutang, breakdown = _progressive_tax(pkp)

    if not has_npwp:
        pph_terutang = (pph_terutang * Decimal("1.20")).quantize(Decimal("1"), ROUND_HALF_UP)

    pph_des = pph_terutang - pph_ter_jan_nov

    return DecemberReconciliation(
        employee_id          = employee_id,
        year                 = year,
        ptkp_status          = ptkp_status,
        bruto_jan_nov        = bruto_jan_nov,
        bruto_desember       = bruto_des,
        bruto_setahun        = bruto_setahun,
        biaya_jabatan        = biaya_jabatan,
        iuran_jht_pensiun    = total_iuran,
        penghasilan_netto    = penghasilan_netto,
        ptkp                 = ptkp_val,
        pkp                  = pkp,
        pph_terutang_setahun = pph_terutang,
        pph_ter_jan_nov      = pph_ter_jan_nov,
        pph_desember         = pph_des,
        has_npwp             = has_npwp,
        npwp_penalty         = not has_npwp,
        tax_breakdown        = breakdown,
    )


# ── DB-aware engine ────────────────────────────────────────────────────────────

class PayrollEngine:
    """
    Mesin payroll yang membaca data karyawan dari DB dan menulis hasilnya
    ke tabel employee_payroll_history.
    """

    def __init__(self, db):
        from sqlalchemy import text
        self.db   = db
        self.text = text

    def get_employee(self, employee_id: str) -> Optional[dict]:
        row = self.db.execute(
            self.text("""
                SELECT e.*, ea.gaji_pokok, ea.tunjangan_transport, ea.tunjangan_makan,
                       ea.tunjangan_lain, ea.premi_bpjs_kesehatan_perusahaan,
                       ea.premi_bpjs_jkk, ea.premi_bpjs_jkm,
                       ea.iuran_jht_karyawan_pct, ea.iuran_pensiun_karyawan
                FROM employee e
                LEFT JOIN employee_payroll_component ea ON ea.employee_id = e.id AND ea.is_active = TRUE
                WHERE e.id = :id AND e.status = 'active'
            """),
            {"id": employee_id}
        ).fetchone()
        return dict(row._mapping) if row else None

    def calculate_monthly(
        self,
        employee_id:     str,
        year:            int,
        month:           int,
        lembur:          Decimal = Decimal("0"),
        bonus_thr:       Decimal = Decimal("0"),
        tunjangan_extra: Decimal = Decimal("0"),   # tunjangan variabel tambahan
        potongan_alpha:  Decimal = Decimal("0"),
        potongan_kasbon: Decimal = Decimal("0"),
    ) -> dict:
        """
        Hitung slip gaji + PPh 21 bulan tertentu untuk satu karyawan.
        Otomatis pilih metode TER (Jan-Nov) atau rekonsiliasi Desember.
        """
        emp = self.get_employee(employee_id)
        if not emp:
            return {"error": "Karyawan tidak ditemukan atau tidak aktif"}

        gaji_pokok    = Decimal(str(emp.get("gaji_pokok") or 0))
        tunj_tetap    = Decimal(str(emp.get("tunjangan_lain") or 0))   # jabatan, keluarga, dll.
        tunj_variabel = (
            Decimal(str(emp.get("tunjangan_transport") or 0))
            + Decimal(str(emp.get("tunjangan_makan") or 0))
            + tunjangan_extra
            - potongan_alpha   # alpha mengurangi gaji variabel
        )
        jkk_rate  = Decimal(str(emp.get("bpjs_jkk_rate") or "0.0024"))
        iuran_jht = gaji_pokok * Decimal(str(emp.get("iuran_jht_karyawan_pct") or "0.02"))
        iuran_pen = Decimal(str(emp.get("iuran_pensiun_karyawan") or 0))

        payroll = PayrollInput(
            gaji_pokok             = gaji_pokok,
            tunjangan_tetap        = tunj_tetap,
            tunjangan_variabel     = tunj_variabel,
            lembur                 = lembur,
            bonus_thr              = bonus_thr,
            jkk_rate               = jkk_rate,
            iuran_jht_karyawan     = iuran_jht,
            iuran_pensiun_karyawan = iuran_pen,
        )

        ptkp_status = emp.get("ptkp_status", "TK/0")
        has_npwp    = bool(emp.get("npwp"))

        if month <= 11:
            result = calculate_monthly_ter(payroll, ptkp_status, month, has_npwp, employee_id)
            result.year = year
            return _ter_result_to_dict(result, payroll, emp, potongan_alpha, potongan_kasbon)
        else:
            return self.calculate_december(employee_id, year, payroll, ptkp_status, has_npwp,
                                           potongan_alpha=potongan_alpha,
                                           potongan_kasbon=potongan_kasbon)

    def calculate_december(
        self,
        employee_id:     str,
        year:            int,
        december_payroll: PayrollInput,
        ptkp_status:     str,
        has_npwp:        bool,
        potongan_alpha:  Decimal = Decimal("0"),
        potongan_kasbon: Decimal = Decimal("0"),
    ) -> dict:
        """
        Rekonsiliasi Desember: ambil data Jan-Nov dari DB, hitung selisih PPh.
        """
        history = self.db.execute(
            self.text("""
                SELECT month, bruto_bulanan, pph21_amount,
                       iuran_jht_karyawan, iuran_pensiun_karyawan
                FROM employee_payroll_history
                WHERE employee_id = :eid AND year = :year AND month BETWEEN 1 AND 11
                ORDER BY month
            """),
            {"eid": employee_id, "year": year}
        ).fetchall()

        # Pad dengan 0 jika bulan-bulan sebelumnya belum ada (misal karyawan baru)
        bruto_map    = {r.month: Decimal(str(r.bruto_bulanan))       for r in history}
        iuran_jht_m  = {r.month: Decimal(str(r.iuran_jht_karyawan))  for r in history}
        iuran_pen_m  = {r.month: Decimal(str(r.iuran_pensiun_karyawan)) for r in history}
        pph_jan_nov  = sum(Decimal(str(r.pph21_amount)) for r in history)

        bruto_list   = [bruto_map.get(m,   Decimal("0")) for m in range(1, 12)]
        iuran_jht_l  = [iuran_jht_m.get(m, Decimal("0")) for m in range(1, 12)]
        iuran_pen_l  = [iuran_pen_m.get(m, Decimal("0")) for m in range(1, 12)]

        # Tambahkan Desember
        bruto_list.append(december_payroll.bruto_bulanan)
        iuran_jht_l.append(december_payroll.iuran_jht_karyawan)
        iuran_pen_l.append(december_payroll.iuran_pensiun_karyawan)

        emp = self.get_employee(employee_id)
        rec = calculate_december_reconciliation(
            bruto_per_bulan        = bruto_list,
            iuran_jht_per_bulan    = iuran_jht_l,
            iuran_pensiun_per_bulan = iuran_pen_l,
            pph_ter_jan_nov        = pph_jan_nov,
            ptkp_status            = ptkp_status,
            has_npwp               = has_npwp,
            employee_id            = employee_id,
            year                   = year,
        )
        return _dec_result_to_dict(rec, december_payroll, emp or {},
                                   potongan_alpha, potongan_kasbon)

    def save_payroll_record(
        self,
        employee_id: str,
        entity_id:   str,
        year:        int,
        month:       int,
        result:      dict,
        created_by:  str = "system",
    ) -> str:
        """Simpan hasil perhitungan payroll ke employee_payroll_history."""
        from uuid import uuid4
        rec_id = str(uuid4())
        bpjs_emp  = result.get("bpjs", {}).get("employer", {})
        bpjs_ee   = result.get("bpjs", {}).get("employee", {})
        summary   = result.get("summary", {})
        potongan  = result.get("potongan", {})
        pendapatan = result.get("pendapatan", {})

        self.db.execute(
            self.text("""
                INSERT INTO employee_payroll_history (
                    id, employee_id, entity_id, year, month,
                    bruto_bulanan, tunjangan_tetap, tunjangan_variabel,
                    lembur, bonus_thr,
                    ter_rate_pct, pph21_amount,
                    iuran_jht_karyawan, iuran_pensiun_karyawan,
                    bpjs_kes_employer, bpjs_jht_employer, bpjs_jp_employer,
                    bpjs_jkk_employer, bpjs_jkm_employer,
                    bpjs_kes_employee, bpjs_jp_employee,
                    thp, ctc,
                    potongan_alpha, potongan_kasbon,
                    method, notes, created_by, created_at
                ) VALUES (
                    :id, :eid, :entid, :year, :month,
                    :bruto, :tt, :tv,
                    :lembur, :bonus,
                    :rate, :pph,
                    :jht, :pen,
                    :bkes_em, :bjht_em, :bjp_em,
                    :bjkk_em, :bjkm_em,
                    :bkes_ee, :bjp_ee,
                    :thp, :ctc,
                    :alpha, :kasbon,
                    :method, :notes, :by, NOW()
                )
                ON CONFLICT (employee_id, year, month)
                DO UPDATE SET
                    bruto_bulanan      = EXCLUDED.bruto_bulanan,
                    tunjangan_tetap    = EXCLUDED.tunjangan_tetap,
                    tunjangan_variabel = EXCLUDED.tunjangan_variabel,
                    lembur             = EXCLUDED.lembur,
                    bonus_thr          = EXCLUDED.bonus_thr,
                    ter_rate_pct       = EXCLUDED.ter_rate_pct,
                    pph21_amount       = EXCLUDED.pph21_amount,
                    bpjs_kes_employer  = EXCLUDED.bpjs_kes_employer,
                    bpjs_jht_employer  = EXCLUDED.bpjs_jht_employer,
                    bpjs_jp_employer   = EXCLUDED.bpjs_jp_employer,
                    bpjs_jkk_employer  = EXCLUDED.bpjs_jkk_employer,
                    bpjs_jkm_employer  = EXCLUDED.bpjs_jkm_employer,
                    bpjs_kes_employee  = EXCLUDED.bpjs_kes_employee,
                    bpjs_jp_employee   = EXCLUDED.bpjs_jp_employee,
                    thp                = EXCLUDED.thp,
                    ctc                = EXCLUDED.ctc,
                    potongan_alpha     = EXCLUDED.potongan_alpha,
                    potongan_kasbon    = EXCLUDED.potongan_kasbon,
                    method             = EXCLUDED.method,
                    notes              = EXCLUDED.notes,
                    updated_at         = NOW()
            """),
            {
                "id":    rec_id, "eid": employee_id, "entid": entity_id,
                "year":  year, "month": month,
                "bruto": float(result.get("bruto_pph21", result.get("bruto_bulanan", 0))),
                "tt":    float(pendapatan.get("tunjangan_tetap", 0)),
                "tv":    float(pendapatan.get("tunjangan_variabel", 0)),
                "lembur": float(result.get("lembur", 0)),
                "bonus": float(result.get("bonus_thr", 0)),
                "rate":  float(result.get("ter_rate_pct", 0)),
                "pph":   float(result.get("pph21_amount", 0)),
                "jht":   float(result.get("iuran_jht_karyawan", 0)),
                "pen":   float(result.get("iuran_pensiun_karyawan", 0)),
                "bkes_em": float(bpjs_emp.get("kes", 0)),
                "bjht_em": float(bpjs_emp.get("jht", 0)),
                "bjp_em":  float(bpjs_emp.get("jp",  0)),
                "bjkk_em": float(bpjs_emp.get("jkk", 0)),
                "bjkm_em": float(bpjs_emp.get("jkm", 0)),
                "bkes_ee": float(bpjs_ee.get("kes", 0)),
                "bjp_ee":  float(bpjs_ee.get("jp",  0)),
                "thp":     float(summary.get("thp", 0)),
                "ctc":     float(summary.get("ctc", 0)),
                "alpha":   float(potongan.get("alpha", 0)),
                "kasbon":  float(potongan.get("kasbon", 0)),
                "method":  result.get("method", "TER"),
                "notes":   result.get("notes", ""),
                "by":      created_by,
            }
        )
        self.db.commit()
        return rec_id

    def get_ytd_summary(self, employee_id: str, year: int) -> dict:
        """YTD summary PPh 21 karyawan: total bruto, total PPh dipotong."""
        rows = self.db.execute(
            self.text("""
                SELECT month, bruto_bulanan, ter_rate_pct, pph21_amount, method
                FROM employee_payroll_history
                WHERE employee_id = :eid AND year = :year
                ORDER BY month
            """),
            {"eid": employee_id, "year": year}
        ).fetchall()

        records     = [dict(r._mapping) for r in rows]
        total_bruto = sum(float(r["bruto_bulanan"] or 0) for r in records)
        total_pph   = sum(float(r["pph21_amount"]  or 0) for r in records)
        return {
            "employee_id":  employee_id,
            "year":         year,
            "months":       records,
            "total_bruto":  total_bruto,
            "total_pph21":  total_pph,
            "months_filled": len(records),
        }

    def calculate_from_timesheet(
        self,
        employee_id: str,
        entity_id:   str,
        year:        int,
        month:       int,
        bonus_thr:   Decimal = Decimal("0"),
    ) -> dict:
        """
        Hitung payroll bulan ini dengan data variabel dari timesheet absensi.
        Ambil tunjangan_makan/transport proporsional hari hadir,
        biaya lembur (PP 35/2021), dan potongan alpha dari AttendanceEngine.
        """
        from modules.attendance_engine import AttendanceEngine
        att = AttendanceEngine(self.db)
        variables = att.get_payroll_variables(employee_id, entity_id, year, month)
        if "error" in variables:
            return {"error": variables["error"]}

        emp = self.get_employee(employee_id)
        if not emp:
            return {"error": "Karyawan tidak ditemukan atau tidak aktif"}

        lembur_cost  = Decimal(str(variables["total_overtime_cost"]))
        alpha_deduct = Decimal(str(variables["potongan_alpha"]))
        trans_var    = Decimal(str(variables["tunjangan_transport_var"]))
        makan_var    = Decimal(str(variables["tunjangan_makan_var"]))

        gaji_pokok = Decimal(str(emp.get("gaji_pokok") or 0))
        jkk_rate   = Decimal(str(emp.get("bpjs_jkk_rate") or "0.0024"))
        iuran_jht  = gaji_pokok * Decimal(str(emp.get("iuran_jht_karyawan_pct") or "0.02"))
        iuran_pen  = Decimal(str(emp.get("iuran_pensiun_karyawan") or 0))

        payroll = PayrollInput(
            gaji_pokok             = gaji_pokok,
            tunjangan_tetap        = Decimal(str(emp.get("tunjangan_lain") or 0)),
            tunjangan_variabel     = trans_var + makan_var - alpha_deduct,
            lembur                 = lembur_cost,
            bonus_thr              = bonus_thr,
            jkk_rate               = jkk_rate,
            iuran_jht_karyawan     = iuran_jht,
            iuran_pensiun_karyawan = iuran_pen,
        )

        ptkp_status = emp.get("ptkp_status", "TK/0")
        has_npwp    = bool(emp.get("npwp"))

        if month <= 11:
            result = calculate_monthly_ter(payroll, ptkp_status, month, has_npwp, employee_id)
            result.year = year
            base = _ter_result_to_dict(result, payroll, emp, alpha_deduct)
        else:
            base = self.calculate_december(employee_id, year, payroll, ptkp_status, has_npwp,
                                           potongan_alpha=alpha_deduct)

        base["attendance"] = {
            "present_days":        variables["present_days"],
            "alpha_days":          variables["alpha_days"],
            "scheduled_days":      variables["scheduled_days"],
            "potongan_alpha":      float(alpha_deduct),
            "overtime_workday_h":  variables["overtime_workday_h"],
            "overtime_restday_h":  variables["overtime_restday_h"],
            "overtime_cost":       float(lembur_cost),
        }
        return base

    def post_payroll_journal(
        self,
        entity_id:   str,
        year:        int,
        month:       int,
        results:     list[dict],
        coa_map:     dict,
        created_by:  str = "system",
    ) -> dict:
        """
        Posting jurnal akuntansi untuk run payroll satu periode.

        Struktur jurnal (per karyawan, digabung jadi 1 batch journal):
            Dr. Beban Gaji          (coa_map["beban_gaji"])
            Dr. Beban Lembur        (coa_map["beban_lembur"])
            Dr. Beban BPJS Perusahaan (coa_map["beban_bpjs"])
            Cr. Hutang Gaji         (coa_map["hutang_gaji"])
            Cr. Hutang PPh 21       (coa_map["hutang_pph21"])
            Cr. Hutang BPJS Karyawan (coa_map["hutang_bpjs"])

        Args:
            results  : list hasil calculate_monthly / calculate_from_timesheet
            coa_map  : dict mapping nama akun → kode COA (harus ada di chart_of_accounts)
        """
        from uuid import uuid4
        from datetime import date

        required_keys = ["beban_gaji", "beban_lembur", "beban_bpjs",
                         "hutang_gaji", "hutang_pph21", "hutang_bpjs"]
        for k in required_keys:
            if k not in coa_map:
                return {"error": f"coa_map kurang key '{k}'"}

        journal_date = date(year, month, 28)  # akhir bulan — approximation

        # Hitung total
        total_bruto       = Decimal("0")
        total_lembur      = Decimal("0")
        total_bpjs_perus  = Decimal("0")
        total_pph21       = Decimal("0")
        total_bpjs_karyw  = Decimal("0")

        for r in results:
            comp = r.get("komponen", {})
            total_bruto      += Decimal(str(r.get("bruto_bulanan", 0)))
            total_lembur     += Decimal(str(comp.get("lembur", 0)))
            total_bpjs_perus += (
                Decimal(str(comp.get("premi_bpjs_kesehatan", 0)))
                + Decimal(str(comp.get("premi_bpjs_jkk", 0)))
                + Decimal(str(comp.get("premi_bpjs_jkm", 0)))
            )
            total_pph21      += Decimal(str(r.get("pph21_amount", 0)))
            total_bpjs_karyw += (
                Decimal(str(r.get("iuran_jht_karyawan", 0)))
                + Decimal(str(r.get("iuran_pensiun_karyawan", 0)))
            )

        # Gaji netto = bruto - pph21 - iuran karyawan
        total_hutang_gaji = total_bruto - total_pph21 - total_bpjs_karyw

        # Buat GL journal header
        journal_id = str(uuid4())
        period_row = self.db.execute(
            self.text("""
                SELECT fp.id FROM fiscal_period fp
                WHERE fp.entity_id = :entid
                  AND fp.year = :year AND fp.period_no = :month
                  AND fp.is_closed = FALSE
                LIMIT 1
            """),
            {"entid": entity_id, "year": year, "month": month}
        ).fetchone()

        if not period_row:
            return {"error": f"Fiscal period {month:02d}/{year} tidak ditemukan atau sudah ditutup"}

        period_id = period_row.id
        self.db.execute(
            self.text("""
                INSERT INTO gl_journal (id, entity_id, journal_date, journal_type,
                    reference_no, description, period_id, status, created_by, created_at)
                VALUES (:id, :entid, :dt, 'PY', :ref, :desc, :pid, 'posted', :by, NOW())
            """),
            {
                "id":    journal_id,
                "entid": entity_id,
                "dt":    journal_date,
                "ref":   f"PAYROLL/{year}/{month:02d}",
                "desc":  f"Run Payroll {month:02d}/{year} — {len(results)} karyawan",
                "pid":   period_id,
                "by":    created_by,
            }
        )

        # Buat journal lines
        lines = [
            # Debit
            (coa_map["beban_gaji"],   total_bruto - total_lembur - total_bpjs_perus, Decimal("0"), "Beban Gaji"),
            (coa_map["beban_lembur"], total_lembur,    Decimal("0"), "Beban Lembur"),
            (coa_map["beban_bpjs"],   total_bpjs_perus, Decimal("0"), "Beban BPJS Perusahaan"),
            # Credit
            (coa_map["hutang_gaji"],  Decimal("0"), total_hutang_gaji, "Hutang Gaji"),
            (coa_map["hutang_pph21"], Decimal("0"), total_pph21,       "Hutang PPh 21"),
            (coa_map["hutang_bpjs"],  Decimal("0"), total_bpjs_karyw,  "Hutang BPJS Karyawan"),
        ]

        for line_no, (coa_code, debit, credit, desc) in enumerate(lines, start=1):
            if (debit + credit) == 0:
                continue
            self.db.execute(
                self.text("""
                    INSERT INTO gl_journal_line (id, journal_id, line_no, account_code,
                        debit_amount, credit_amount, description, created_at)
                    VALUES (:id, :jid, :ln, :coa, :dr, :cr, :desc, NOW())
                """),
                {
                    "id":   str(uuid4()),
                    "jid":  journal_id,
                    "ln":   line_no,
                    "coa":  coa_code,
                    "dr":   float(debit),
                    "cr":   float(credit),
                    "desc": desc,
                }
            )

        # Log payroll run
        self.db.execute(
            self.text("""
                INSERT INTO payroll_run_log (id, entity_id, year, month, run_by,
                    employees_count, total_bruto, total_pph21,
                    total_bpjs, total_net, journal_id, status)
                VALUES (:id, :entid, :year, :month, :by,
                    :cnt, :bruto, :pph, :bpjs, :net, :jid, 'success')
            """),
            {
                "id":    str(uuid4()),
                "entid": entity_id,
                "year":  year,
                "month": month,
                "by":    created_by,
                "cnt":   len(results),
                "bruto": float(total_bruto),
                "pph":   float(total_pph21),
                "bpjs":  float(total_bpjs_perus + total_bpjs_karyw),
                "net":   float(total_hutang_gaji),
                "jid":   journal_id,
            }
        )

        self.db.commit()
        return {
            "journal_id":     journal_id,
            "period":         f"{month:02d}/{year}",
            "employees_count": len(results),
            "total_bruto":    float(total_bruto),
            "total_pph21":    float(total_pph21),
            "total_bpjs":     float(total_bpjs_perus + total_bpjs_karyw),
            "total_net_gaji": float(total_hutang_gaji),
            "journal_lines":  len(lines),
            "status":         "posted",
        }


# ── Serialization helpers ──────────────────────────────────────────────────────

def _build_slip_section(payroll: PayrollInput, pph21: Decimal, emp: dict,
                        potongan_alpha: Decimal = Decimal("0"),
                        potongan_kasbon: Decimal = Decimal("0")) -> dict:
    """Bangun section slip gaji (BPJS, THP, CTC) dari PayrollInput."""
    bpjs = payroll.get_bpjs()
    gaji_gross = (
        payroll.gaji_pokok
        + payroll.tunjangan_tetap
        + payroll.total_tunjangan_variabel
        + payroll.bonus_thr
    )
    total_potongan_bpjs = bpjs.total_employee
    total_potongan_lain = potongan_alpha + potongan_kasbon
    total_potongan      = total_potongan_bpjs + pph21 + total_potongan_lain
    thp = gaji_gross - total_potongan
    ctc = gaji_gross + bpjs.total_employer

    return {
        "pendapatan": {
            "gaji_pokok":         float(payroll.gaji_pokok),
            "tunjangan_tetap":    float(payroll.tunjangan_tetap),
            "tunjangan_variabel": float(payroll.total_tunjangan_variabel),
            "bonus_thr":          float(payroll.bonus_thr),
            "gaji_gross":         float(gaji_gross),
        },
        "bpjs": bpjs.to_dict(),
        "potongan": {
            "bpjs_karyawan":  float(total_potongan_bpjs),
            "bpjs_kes":       float(bpjs.kes_employee),
            "bpjs_jht":       float(bpjs.jht_employee),
            "bpjs_jp":        float(bpjs.jp_employee),
            "pph21":          float(pph21),
            "alpha":          float(potongan_alpha),
            "kasbon":         float(potongan_kasbon),
            "total":          float(total_potongan),
        },
        "summary": {
            "gaji_gross":      float(gaji_gross),
            "total_potongan":  float(total_potongan),
            "thp":             float(thp),
            "ctc":             float(ctc),
            "bpjs_perusahaan": float(bpjs.total_employer),
        },
    }


def _ter_result_to_dict(r: MonthlyPayrollResult, payroll: PayrollInput, emp: dict,
                        potongan_alpha: Decimal = Decimal("0"),
                        potongan_kasbon: Decimal = Decimal("0")) -> dict:
    slip = _build_slip_section(payroll, r.pph21_amount, emp, potongan_alpha, potongan_kasbon)
    return {
        "employee_id":    r.employee_id,
        "employee_name":  emp.get("full_name", ""),
        "employee_no":    emp.get("employee_no", ""),
        "npwp":           emp.get("npwp", ""),
        "month":          r.month,
        "year":           r.year,
        "ptkp_status":    r.ptkp_status,
        "ter_category":   r.ter_category,
        "method":         "TER (PP 58/2023)",
        "bruto_pph21":    float(payroll.bruto_bulanan),
        "ter_rate_pct":   float(r.ter_rate_pct),
        "pph21_amount":   float(r.pph21_amount),
        "has_npwp":       r.has_npwp,
        "npwp_penalty":   r.npwp_penalty,
        "iuran_jht_karyawan":      float(payroll.iuran_jht_karyawan),
        "iuran_pensiun_karyawan":  float(payroll.iuran_pensiun_karyawan),
        "notes":          r.notes,
        "regulation":     "PP 58/2023, PMK 168/2023",
        **slip,
    }


def _dec_result_to_dict(r: DecemberReconciliation, payroll: PayrollInput, emp: dict,
                        potongan_alpha: Decimal = Decimal("0"),
                        potongan_kasbon: Decimal = Decimal("0")) -> dict:
    slip = _build_slip_section(payroll, r.pph_desember, emp, potongan_alpha, potongan_kasbon)
    return {
        "employee_id":          r.employee_id,
        "employee_name":        emp.get("full_name", ""),
        "employee_no":          emp.get("employee_no", ""),
        "npwp":                 emp.get("npwp", ""),
        "year":                 r.year,
        "month":                12,
        "ptkp_status":          r.ptkp_status,
        "method":               "Rekonsiliasi Desember (UU HPP 2021 Pasal 17)",
        "bruto_desember":       float(r.bruto_desember),
        "bruto_jan_nov":        float(r.bruto_jan_nov),
        "bruto_setahun":        float(r.bruto_setahun),
        "biaya_jabatan":        float(r.biaya_jabatan),
        "iuran_jht_pensiun":    float(r.iuran_jht_pensiun),
        "penghasilan_netto":    float(r.penghasilan_netto),
        "ptkp":                 float(r.ptkp),
        "pkp":                  float(r.pkp),
        "pph_terutang_setahun": float(r.pph_terutang_setahun),
        "pph_ter_jan_nov":      float(r.pph_ter_jan_nov),
        "pph_desember":         float(r.pph_desember),
        "pph21_amount":         float(r.pph_desember),
        "ter_rate_pct":         0,
        "has_npwp":             r.has_npwp,
        "npwp_penalty":         r.npwp_penalty,
        "tax_breakdown":        r.tax_breakdown,
        "iuran_jht_karyawan":   float(payroll.iuran_jht_karyawan),
        "iuran_pensiun_karyawan": float(payroll.iuran_pensiun_karyawan),
        "notes": (
            f"Rekonsiliasi: PPh terutang setahun Rp {r.pph_terutang_setahun:,.0f} "
            f"− PPh TER Jan-Nov Rp {r.pph_ter_jan_nov:,.0f} "
            f"= PPh Desember Rp {r.pph_desember:,.0f}"
        ),
        "regulation": "PP 58/2023, PMK 168/2023, UU HPP 2021 Pasal 17",
        **slip,
    }
