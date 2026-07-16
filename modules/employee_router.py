# modules/employee_router.py
# Employee Registration + Payroll Calculation endpoints
#
# Workflow registrasi: draft → submitted → hr_review → approved → active
# Payroll: TER Jan-Nov, rekonsiliasi Desember

from uuid import UUID, uuid4
from decimal import Decimal
from datetime import date, datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger

from core.database import get_db
from modules.auth import require_min_role
from modules.payroll_engine import PayrollEngine, PayrollInput, calculate_monthly_ter
from modules.ter_tables import get_ter_category, ter_lookup, PTKP, TER_CATEGORY_MAP
from modules.salary_calculator import (
    calculate_bpjs, simulate_slip_gaji,
    BPJS_KES_CAP, BPJS_JP_CAP, BPJS_JKK_DEFAULT,
)

router = APIRouter(prefix="/employees", tags=["Employees & Payroll"])


# ── Request models ─────────────────────────────────────────────────────────────

class EmployeeRegistrationCreate(BaseModel):
    entity_id:          UUID
    full_name:          str
    nickname:           Optional[str]  = None
    nik_ktp:            Optional[str]  = None
    npwp:               Optional[str]  = None
    place_of_birth:     Optional[str]  = None
    date_of_birth:      Optional[date] = None
    gender:             Optional[str]  = None   # M / F
    marital_status:     Optional[str]  = None   # single / married / divorced
    phone:              Optional[str]  = None
    email:              str
    address:            Optional[str]  = None
    department:         Optional[str]  = None
    position:           Optional[str]  = None
    employment_type:    str            = "permanent"
    join_date:          Optional[date] = None
    ptkp_status:        str            = "TK/0"
    number_of_dependents: int          = 0
    bank_name:          Optional[str]  = None
    bank_account_no:    Optional[str]  = None
    bank_account_name:  Optional[str]  = None
    bpjs_kesehatan_no:  Optional[str]  = None
    bpjs_tk_no:         Optional[str]  = None
    gaji_pokok_proposed:   Optional[Decimal] = None
    tunjangan_proposed:    Optional[Decimal] = None


class PayrollComponentCreate(BaseModel):
    employee_id:                UUID
    effective_date:             date
    gaji_pokok:                 Decimal
    tunjangan_transport:        Decimal = Decimal("0")
    tunjangan_makan:            Decimal = Decimal("0")
    tunjangan_lain:             Decimal = Decimal("0")
    premi_bpjs_kesehatan_perusahaan: Decimal = Decimal("0")
    premi_bpjs_jkk:             Decimal = Decimal("0")
    premi_bpjs_jkm:             Decimal = Decimal("0")
    iuran_jht_karyawan_pct:     Decimal = Decimal("0.02")
    iuran_pensiun_karyawan:     Decimal = Decimal("0")
    notes:                      Optional[str] = None


class MonthlyPayrollRequest(BaseModel):
    employee_id:    UUID
    entity_id:      UUID
    year:           int
    month:          int
    lembur:         Decimal = Decimal("0")
    bonus_thr:      Decimal = Decimal("0")
    tunjangan_lain_extra: Decimal = Decimal("0")
    save_to_db:     bool    = True
    created_by:     str     = "system"


class PtkpUpdateRequest(BaseModel):
    new_ptkp_status:        str
    number_of_dependents:   int   = 0
    effective_year:         int
    reason:                 str
    changed_by:             str


# ── Registration endpoints ─────────────────────────────────────────────────────

@router.post("/register", dependencies=[Depends(require_min_role("viewer"))])
def register_employee(req: EmployeeRegistrationCreate, db: Session = Depends(get_db)):
    """Buat draft registrasi karyawan baru."""
    entity = db.execute(
        text("SELECT id FROM entity WHERE id = :id"), {"id": str(req.entity_id)}
    ).fetchone()
    if not entity:
        raise HTTPException(404, f"Entity tidak ditemukan")

    if req.ptkp_status not in TER_CATEGORY_MAP:
        raise HTTPException(400, f"PTKP status tidak valid. Valid: {', '.join(TER_CATEGORY_MAP)}")

    reg_no = _gen_reg_no(db)
    reg_id = uuid4()

    db.execute(
        text("""
            INSERT INTO employee_registration (
                id, entity_id, registration_no, status,
                full_name, nickname, nik_ktp, npwp,
                place_of_birth, date_of_birth, gender, marital_status,
                phone, email, address,
                department, position, employment_type, join_date,
                ptkp_status, number_of_dependents,
                bank_name, bank_account_no, bank_account_name,
                bpjs_kesehatan_no, bpjs_tk_no,
                gaji_pokok_proposed, tunjangan_proposed,
                created_at, updated_at
            ) VALUES (
                :id, :eid, :reg_no, 'draft',
                :full_name, :nickname, :nik, :npwp,
                :pob, :dob, :gender, :marital,
                :phone, :email, :address,
                :dept, :position, :emp_type, :join_date,
                :ptkp, :deps,
                :bank_name, :bank_no, :bank_holder,
                :bpjs_kes, :bpjs_tk,
                :gaji, :tunjangan,
                NOW(), NOW()
            )
        """),
        {
            "id": str(reg_id), "eid": str(req.entity_id), "reg_no": reg_no,
            "full_name": req.full_name, "nickname": req.nickname,
            "nik": req.nik_ktp, "npwp": req.npwp,
            "pob": req.place_of_birth, "dob": req.date_of_birth,
            "gender": req.gender, "marital": req.marital_status,
            "phone": req.phone, "email": req.email, "address": req.address,
            "dept": req.department, "position": req.position,
            "emp_type": req.employment_type, "join_date": req.join_date,
            "ptkp": req.ptkp_status, "deps": req.number_of_dependents,
            "bank_name": req.bank_name, "bank_no": req.bank_account_no,
            "bank_holder": req.bank_account_name,
            "bpjs_kes": req.bpjs_kesehatan_no, "bpjs_tk": req.bpjs_tk_no,
            "gaji": float(req.gaji_pokok_proposed) if req.gaji_pokok_proposed else None,
            "tunjangan": float(req.tunjangan_proposed) if req.tunjangan_proposed else None,
        }
    )
    db.commit()
    logger.info(f"Employee registration created: {reg_no} | {req.full_name}")
    return {
        "registration_id": str(reg_id),
        "registration_no": reg_no,
        "status":          "draft",
        "ter_category":    get_ter_category(req.ptkp_status),
    }


@router.post("/register/{reg_id}/submit", dependencies=[Depends(require_min_role("viewer"))])
def submit_registration(reg_id: str, submitted_by: str, db: Session = Depends(get_db)):
    """Submit ke HR untuk direview."""
    reg = _require_reg(db, reg_id, ["draft"])
    missing = []
    if not reg["doc_ktp_uploaded"]:  missing.append("KTP")
    if not reg["doc_npwp_uploaded"] and reg["npwp"]:  missing.append("NPWP")
    if not reg["doc_cv_uploaded"]:   missing.append("CV/Lamaran")
    if missing:
        raise HTTPException(400, f"Dokumen belum diupload: {', '.join(missing)}")

    db.execute(
        text("""
            UPDATE employee_registration SET
                status = 'submitted', submitted_by = :by, submitted_at = NOW(), updated_at = NOW()
            WHERE id = :id
        """),
        {"id": reg_id, "by": submitted_by}
    )
    db.commit()
    return {"status": "submitted", "next_step": "HR Review"}


@router.post("/register/{reg_id}/review", dependencies=[Depends(require_min_role("finance"))])
def hr_review(
    reg_id:     str,
    action:     str = Query(..., description="approve / reject"),
    reviewed_by: str = Query(...),
    notes:      Optional[str] = None,
    db: Session = Depends(get_db),
):
    """HR mereview kelengkapan data dan dokumen karyawan."""
    _require_reg(db, reg_id, ["submitted", "hr_review"])

    if action == "approve":
        db.execute(
            text("""
                UPDATE employee_registration SET
                    status = 'approved', reviewed_by = :by, reviewed_at = NOW(), updated_at = NOW()
                WHERE id = :id
            """),
            {"id": reg_id, "by": reviewed_by}
        )
        db.commit()
        return {"status": "approved", "next_step": "Activate employee"}

    db.execute(
        text("""
            UPDATE employee_registration SET
                status = 'rejected', reviewed_by = :by, reviewed_at = NOW(),
                rejection_reason = :notes, updated_at = NOW()
            WHERE id = :id
        """),
        {"id": reg_id, "by": reviewed_by, "notes": notes}
    )
    db.commit()
    return {"status": "rejected"}


@router.post("/register/{reg_id}/activate", dependencies=[Depends(require_min_role("admin"))])
def activate_employee(reg_id: str, activated_by: str, db: Session = Depends(get_db)):
    """
    Aktivasi karyawan — buat record di tabel employee dari data registrasi.
    """
    reg = _require_reg(db, reg_id, ["approved"])
    emp_id = _create_employee_from_reg(db, reg, activated_by)
    db.commit()
    logger.info(f"Employee activated: {emp_id} from registration {reg_id}")
    return {
        "employee_id":     str(emp_id),
        "employee_no":     _get_employee_no(db, emp_id),
        "ter_category":    get_ter_category(reg["ptkp_status"]),
        "message":         "Karyawan aktif. Set komponen gaji via POST /employees/{id}/payroll-component",
    }


@router.patch("/register/{reg_id}/docs", dependencies=[Depends(require_min_role("viewer"))])
def update_doc_checklist(
    reg_id:       str,
    doc_ktp:      bool = False,
    doc_npwp:     bool = False,
    doc_ijazah:   bool = False,
    doc_cv:       bool = False,
    db: Session = Depends(get_db),
):
    db.execute(
        text("""
            UPDATE employee_registration SET
                doc_ktp_uploaded    = :ktp,
                doc_npwp_uploaded   = :npwp,
                doc_ijazah_uploaded = :ijazah,
                doc_cv_uploaded     = :cv,
                updated_at          = NOW()
            WHERE id = :id
        """),
        {"id": reg_id, "ktp": doc_ktp, "npwp": doc_npwp, "ijazah": doc_ijazah, "cv": doc_cv}
    )
    db.commit()
    return {"message": "Checklist dokumen diperbarui"}


# ── Employee master endpoints ──────────────────────────────────────────────────

@router.get("/{employee_id}", dependencies=[Depends(require_min_role("viewer"))])
def get_employee(employee_id: str, db: Session = Depends(get_db)):
    """Detail karyawan + komponen gaji aktif."""
    row = db.execute(
        text("SELECT * FROM vw_employee_payroll_summary WHERE employee_id = :id"),
        {"id": employee_id}
    ).fetchone()
    if not row:
        raise HTTPException(404, "Karyawan tidak ditemukan")
    return dict(row._mapping)


@router.get("/", dependencies=[Depends(require_min_role("viewer"))])
def list_employees(
    entity_id:  Optional[str] = None,
    department: Optional[str] = None,
    status:     Optional[str] = None,
    limit:      int           = Query(default=50, le=200),
    db: Session = Depends(get_db),
):
    filters = []
    params: dict = {"limit": limit}
    if entity_id:  filters.append("entity_id = :eid");  params["eid"] = entity_id
    if department: filters.append("department ILIKE :dept"); params["dept"] = f"%{department}%"
    if status:     filters.append("status = :status");   params["status"] = status
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    rows = db.execute(
        text(f"SELECT * FROM vw_employee_payroll_summary {where} ORDER BY full_name LIMIT :limit"),
        params
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Payroll component ──────────────────────────────────────────────────────────

@router.post("/{employee_id}/payroll-component",
             dependencies=[Depends(require_min_role("finance"))])
def set_payroll_component(req: PayrollComponentCreate, db: Session = Depends(get_db)):
    """Set/update komponen gaji karyawan. Record lama dinonaktifkan."""
    emp = db.execute(
        text("SELECT id, full_name FROM employee WHERE id = :id AND status = 'active'"),
        {"id": str(req.employee_id)}
    ).fetchone()
    if not emp:
        raise HTTPException(404, "Karyawan tidak ditemukan atau tidak aktif")

    # Nonaktifkan komponen sebelumnya
    db.execute(
        text("UPDATE employee_payroll_component SET is_active = FALSE WHERE employee_id = :id"),
        {"id": str(req.employee_id)}
    )

    comp_id = uuid4()
    db.execute(
        text("""
            INSERT INTO employee_payroll_component (
                id, employee_id, effective_date, is_active,
                gaji_pokok, tunjangan_transport, tunjangan_makan, tunjangan_lain,
                premi_bpjs_kesehatan_perusahaan, premi_bpjs_jkk, premi_bpjs_jkm,
                iuran_jht_karyawan_pct, iuran_pensiun_karyawan, notes
            ) VALUES (
                :id, :eid, :eff_date, TRUE,
                :gp, :tt, :tm, :tl,
                :bpjs_kes, :bpjs_jkk, :bpjs_jkm,
                :jht_pct, :pensiun, :notes
            )
        """),
        {
            "id": str(comp_id), "eid": str(req.employee_id),
            "eff_date": req.effective_date,
            "gp":  float(req.gaji_pokok),
            "tt":  float(req.tunjangan_transport),
            "tm":  float(req.tunjangan_makan),
            "tl":  float(req.tunjangan_lain),
            "bpjs_kes": float(req.premi_bpjs_kesehatan_perusahaan),
            "bpjs_jkk": float(req.premi_bpjs_jkk),
            "bpjs_jkm": float(req.premi_bpjs_jkm),
            "jht_pct":  float(req.iuran_jht_karyawan_pct),
            "pensiun":  float(req.iuran_pensiun_karyawan),
            "notes":    req.notes,
        }
    )
    db.commit()

    bruto_normal = (
        req.gaji_pokok + req.tunjangan_transport + req.tunjangan_makan + req.tunjangan_lain
        + req.premi_bpjs_kesehatan_perusahaan + req.premi_bpjs_jkk + req.premi_bpjs_jkm
    )
    return {
        "component_id": str(comp_id),
        "bruto_normal": float(bruto_normal),
        "message":      f"Komponen gaji {emp.full_name} diperbarui efektif {req.effective_date}",
    }


# ── Payroll calculation ────────────────────────────────────────────────────────

@router.post("/payroll/calculate", dependencies=[Depends(require_min_role("finance"))])
def calculate_payroll(req: MonthlyPayrollRequest, db: Session = Depends(get_db)):
    """
    Hitung PPh 21 bulan tertentu untuk satu karyawan.

    - Bulan 1–11 : TER (PP 58/2023) — PPh = bruto × tarif TER
    - Bulan 12   : Rekonsiliasi Pasal 17 — PPh = terutang setahun − Σ TER Jan-Nov

    Jika save_to_db=True, hasilnya disimpan ke employee_payroll_history.
    """
    if not (1 <= req.month <= 12):
        raise HTTPException(400, "month harus 1–12")

    engine = PayrollEngine(db)
    result = engine.calculate_monthly(
        employee_id           = str(req.employee_id),
        year                  = req.year,
        month                 = req.month,
        lembur                = req.lembur,
        bonus_thr             = req.bonus_thr,
        tunjangan_lain_extra  = req.tunjangan_lain_extra,
    )

    if "error" in result:
        raise HTTPException(404, result["error"])

    if req.save_to_db:
        engine.save_payroll_record(
            employee_id = str(req.employee_id),
            entity_id   = str(req.entity_id),
            year        = req.year,
            month       = req.month,
            result      = result,
            created_by  = req.created_by,
        )
        result["saved"] = True

    return result


@router.post("/payroll/batch", dependencies=[Depends(require_min_role("finance"))])
def batch_payroll(
    entity_id:  str,
    year:       int  = Query(...),
    month:      int  = Query(..., ge=1, le=12),
    created_by: str  = Query(default="system"),
    db: Session = Depends(get_db),
):
    """
    Hitung PPh 21 semua karyawan aktif entity ini untuk bulan tertentu.
    Hasilnya disimpan ke employee_payroll_history.
    """
    employees = db.execute(
        text("SELECT id FROM employee WHERE entity_id = :eid AND status = 'active'"),
        {"eid": entity_id}
    ).fetchall()

    engine  = PayrollEngine(db)
    results = []
    errors  = []

    for emp in employees:
        try:
            r = engine.calculate_monthly(str(emp.id), year, month)
            if "error" not in r:
                engine.save_payroll_record(str(emp.id), entity_id, year, month, r, created_by)
                results.append({"employee_id": str(emp.id), "pph21": r["pph21_amount"]})
            else:
                errors.append({"employee_id": str(emp.id), "error": r["error"]})
        except Exception as e:
            errors.append({"employee_id": str(emp.id), "error": str(e)})

    logger.info(f"Batch payroll {month:02d}/{year} entity {entity_id}: {len(results)} OK, {len(errors)} error")
    return {
        "entity_id":     entity_id,
        "year":          year,
        "month":         month,
        "processed":     len(results),
        "errors":        len(errors),
        "results":       results,
        "error_details": errors,
    }


@router.get("/{employee_id}/payroll/ytd", dependencies=[Depends(require_min_role("viewer"))])
def payroll_ytd(
    employee_id: str,
    year: int = Query(default=date.today().year),
    db: Session = Depends(get_db),
):
    """YTD summary PPh 21 karyawan: total bruto, total PPh, breakdown per bulan."""
    engine = PayrollEngine(db)
    return engine.get_ytd_summary(employee_id, year)


@router.get("/payroll/summary/{entity_id}", dependencies=[Depends(require_min_role("viewer"))])
def payroll_entity_summary(
    entity_id: str,
    year: int  = Query(...),
    month: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
):
    """Ringkasan PPh 21 seluruh karyawan entity ini untuk bulan tertentu."""
    rows = db.execute(
        text("""
            SELECT
                e.employee_no, e.full_name, e.department,
                e.ptkp_status, e.ter_category,
                eph.bruto_bulanan, eph.ter_rate_pct, eph.pph21_amount, eph.method
            FROM employee_payroll_history eph
            JOIN employee e ON e.id = eph.employee_id
            WHERE eph.entity_id = :eid AND eph.year = :year AND eph.month = :month
            ORDER BY e.department, e.full_name
        """),
        {"eid": entity_id, "year": year, "month": month}
    ).fetchall()

    lines        = [dict(r._mapping) for r in rows]
    total_bruto  = sum(float(r["bruto_bulanan"] or 0) for r in lines)
    total_pph    = sum(float(r["pph21_amount"]  or 0) for r in lines)

    return {
        "entity_id":    entity_id,
        "year":         year,
        "month":        month,
        "total_karyawan": len(lines),
        "total_bruto":  total_bruto,
        "total_pph21":  total_pph,
        "lines":        lines,
    }


# ── PTKP update ────────────────────────────────────────────────────────────────

@router.patch("/{employee_id}/ptkp", dependencies=[Depends(require_min_role("finance"))])
def update_ptkp(employee_id: str, req: PtkpUpdateRequest, db: Session = Depends(get_db)):
    """
    Update status PTKP karyawan — berlaku mulai tahun berikutnya (atau sesuai effective_year).
    Menyimpan history perubahan untuk SPT tahunan.
    """
    if req.new_ptkp_status not in TER_CATEGORY_MAP:
        raise HTTPException(400, f"PTKP tidak valid. Valid: {', '.join(TER_CATEGORY_MAP)}")

    emp = db.execute(
        text("SELECT ptkp_status FROM employee WHERE id = :id"), {"id": employee_id}
    ).fetchone()
    if not emp:
        raise HTTPException(404, "Karyawan tidak ditemukan")

    old_ptkp = emp.ptkp_status
    db.execute(
        text("""
            UPDATE employee SET
                ptkp_status          = :ptkp,
                number_of_dependents = :deps,
                updated_at           = NOW()
            WHERE id = :id
        """),
        {"id": employee_id, "ptkp": req.new_ptkp_status, "deps": req.number_of_dependents}
    )
    db.execute(
        text("""
            INSERT INTO employee_ptkp_history
                (id, employee_id, old_ptkp, new_ptkp, effective_year, reason, changed_by)
            VALUES (:id, :eid, :old, :new, :year, :reason, :by)
        """),
        {
            "id": str(uuid4()), "eid": employee_id,
            "old": old_ptkp, "new": req.new_ptkp_status,
            "year": req.effective_year, "reason": req.reason, "by": req.changed_by,
        }
    )
    db.commit()
    return {
        "employee_id":  employee_id,
        "old_ptkp":     old_ptkp,
        "new_ptkp":     req.new_ptkp_status,
        "ter_category": get_ter_category(req.new_ptkp_status),
        "effective_year": req.effective_year,
    }


# ── Reference: TER rates untuk simulasi ──────────────────────────────────────

@router.get("/reference/ter-rate")
def ter_rate_lookup(
    gross:       float = Query(..., description="Penghasilan bruto bulanan (Rp)"),
    ptkp_status: str   = Query(default="TK/0"),
):
    """Lookup tarif TER untuk simulasi tanpa harus punya data karyawan di DB."""
    try:
        rate = ter_lookup(Decimal(str(gross)), ptkp_status)
        return {
            "gross":        gross,
            "ptkp_status":  ptkp_status,
            "ter_category": get_ter_category(ptkp_status),
            "ter_rate_pct": float(rate),
            "pph21_amount": float((Decimal(str(gross)) * rate / 100).quantize(Decimal("1"))),
            "ptkp_tahunan": float(PTKP.get(ptkp_status.upper(), 0)),
            "regulation":   "PP 58/2023 Lampiran",
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/reference/ptkp-categories")
def ptkp_categories():
    """Daftar PTKP status, nilai, dan kategori TER masing-masing."""
    return [
        {
            "ptkp_status":  k,
            "ptkp_tahunan": float(v),
            "ter_category": TER_CATEGORY_MAP[k],
        }
        for k, v in PTKP.items()
    ]


# ── BPJS Calculator (simulasi, tanpa auth) ────────────────────────────────────

@router.get("/reference/bpjs-rates", summary="Daftar tarif BPJS regulasi")
def bpjs_rates():
    """Informasi tarif BPJS sesuai regulasi terkini (PP 44/2015, Perpres 64/2020)."""
    return {
        "bpjs_kesehatan": {
            "employer_pct": 4.0, "employee_pct": 1.0,
            "basis": "Gaji Pokok + Tunjangan Tetap",
            "cap_basis": float(BPJS_KES_CAP),
            "regulation": "Perpres 64/2020",
        },
        "bpjs_jht": {
            "employer_pct": 3.7, "employee_pct": 2.0,
            "basis": "Gaji Pokok + Tunjangan Tetap (tanpa plafon)",
            "regulation": "PP 46/2015",
        },
        "bpjs_jp": {
            "employer_pct": 2.0, "employee_pct": 1.0,
            "basis": "Gaji Pokok + Tunjangan Tetap",
            "cap_basis": float(BPJS_JP_CAP),
            "regulation": "Perpres 109/2013 (plafon diperbarui tiap Januari)",
        },
        "bpjs_jkk": {
            "employer_pct": "0.24–1.74 (tergantung risiko industri)", "employee_pct": 0,
            "default": float(BPJS_JKK_DEFAULT) * 100,
            "basis": "Gaji Pokok + Tunjangan Tetap",
            "regulation": "PP 44/2015 Lampiran I",
        },
        "bpjs_jkm": {
            "employer_pct": 0.3, "employee_pct": 0,
            "basis": "Gaji Pokok + Tunjangan Tetap",
            "regulation": "PP 44/2015",
        },
    }


@router.get("/reference/simulate-bpjs", summary="Simulasi BPJS dari basis upah")
def simulate_bpjs(
    gaji_pokok:      float = Query(..., description="Gaji pokok"),
    tunjangan_tetap: float = Query(0,   description="Tunjangan tetap (jabatan, keluarga)"),
    jkk_rate:        float = Query(0.0024, description="Tarif JKK (default 0.24%)"),
):
    """Kalkulasi semua komponen BPJS dari basis upah. Tidak butuh login."""
    bpjs = calculate_bpjs(
        Decimal(str(gaji_pokok)),
        Decimal(str(tunjangan_tetap)),
        Decimal(str(jkk_rate)),
    )
    return bpjs.to_dict()


@router.get("/reference/simulate-slip-gaji", summary="Simulasi slip gaji lengkap (THP & CTC)")
def simulate_slip(
    gaji_pokok:         float = Query(..., description="Gaji pokok"),
    tunjangan_tetap:    float = Query(0,  description="Tunjangan tetap (jabatan, keluarga, dll.)"),
    tunjangan_variabel: float = Query(0,  description="Tunjangan variabel (makan, transport, lembur)"),
    ptkp_status:        str   = Query("TK/0"),
    jkk_rate:           float = Query(0.0024),
    bonus_thr:          float = Query(0),
    has_npwp:           bool  = Query(True),
    potongan_lain:      float = Query(0, description="Potongan kasbon / alpha / lainnya"),
):
    """
    Simulasi slip gaji lengkap tanpa data karyawan di DB.
    Menampilkan: BPJS employer & employee, PPh 21 TER, THP, Cost to Company.
    Endpoint ini tidak memerlukan autentikasi.
    """
    return simulate_slip_gaji(
        gaji_pokok         = gaji_pokok,
        tunjangan_tetap    = tunjangan_tetap,
        tunjangan_variabel = tunjangan_variabel,
        ptkp_status        = ptkp_status,
        jkk_rate           = jkk_rate,
        bonus_thr          = bonus_thr,
        has_npwp           = has_npwp,
        potongan_lain      = potongan_lain,
    )


# ── Slip Gaji (karyawan aktif di DB) ─────────────────────────────────────────

@router.get("/{employee_id}/slip-gaji/{year}/{month}",
            summary="Ambil slip gaji karyawan dari history",
            dependencies=[Depends(require_min_role("viewer"))])
def get_slip_gaji(
    employee_id: str,
    year:        int,
    month:       int,
    db:          Session = Depends(get_db),
):
    """
    Ambil slip gaji yang sudah di-run dari employee_payroll_history.
    Gunakan POST /employees/payroll/calculate untuk men-generate slip baru.
    """
    row = db.execute(
        text("""
            SELECT * FROM vw_slip_gaji
            WHERE employee_id = :eid AND year = :year AND month = :month
        """),
        {"eid": employee_id, "year": year, "month": month}
    ).fetchone()
    if not row:
        raise HTTPException(404, "Slip gaji belum dibuat — run payroll terlebih dahulu")
    return dict(row._mapping)


@router.get("/slip-gaji/{entity_id}/{year}/{month}",
            summary="Rekap slip gaji semua karyawan satu periode",
            dependencies=[Depends(require_min_role("finance"))])
def get_slip_gaji_rekap(
    entity_id: str,
    year:      int,
    month:     int,
    db:        Session = Depends(get_db),
):
    rows = db.execute(
        text("""
            SELECT * FROM vw_slip_gaji
            WHERE entity_id = :entid AND year = :year AND month = :month
            ORDER BY department, full_name
        """),
        {"entid": entity_id, "year": year, "month": month}
    ).fetchall()
    slips   = [dict(r._mapping) for r in rows]
    summary = {
        "total_karyawan": len(slips),
        "total_bruto":    sum(float(s.get("bruto_pph21") or 0) for s in slips),
        "total_pph21":    sum(float(s.get("pph21_amount") or 0) for s in slips),
        "total_thp":      sum(float(s.get("thp") or 0) for s in slips),
        "total_ctc":      sum(float(s.get("ctc") or 0) for s in slips),
        "total_bpjs_perusahaan": sum(float(s.get("total_bpjs_employer") or 0) for s in slips),
    }
    return {"period": f"{month:02d}/{year}", "summary": summary, "slips": slips}


@router.get("/bpjs-rekap/{entity_id}/{year}/{month}",
            summary="Rekap setoran BPJS bulanan",
            dependencies=[Depends(require_min_role("finance"))])
def get_bpjs_rekap(
    entity_id: str,
    year:      int,
    month:     int,
    db:        Session = Depends(get_db),
):
    """Rekap setoran BPJS untuk upload ke e-Dabu dan SIPP."""
    row = db.execute(
        text("""
            SELECT * FROM vw_bpjs_bulanan
            WHERE entity_id = :entid AND year = :year AND month = :month
        """),
        {"entid": entity_id, "year": year, "month": month}
    ).fetchone()
    if not row:
        raise HTTPException(404, "Belum ada data payroll periode ini")
    return dict(row._mapping)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _gen_reg_no(db: Session) -> str:
    now    = datetime.now()
    prefix = f"ER/{now.year}/{now.month:02d}"
    count  = db.execute(
        text("SELECT COUNT(*) FROM employee_registration WHERE registration_no LIKE :p"),
        {"p": f"{prefix}/%"}
    ).scalar()
    return f"{prefix}/{(count or 0) + 1:04d}"


def _require_reg(db: Session, reg_id: str, allowed: list) -> dict:
    row = db.execute(
        text("SELECT * FROM employee_registration WHERE id = :id"), {"id": reg_id}
    ).fetchone()
    if not row:
        raise HTTPException(404, "Registration tidak ditemukan")
    d = dict(row._mapping)
    if d["status"] not in allowed:
        raise HTTPException(400,
            f"Status '{d['status']}' tidak valid. Harus: {', '.join(allowed)}")
    return d


def _create_employee_from_reg(db: Session, reg: dict, activated_by: str) -> UUID:
    entity_id = reg["entity_id"]
    count = db.execute(
        text("SELECT COUNT(*) FROM employee WHERE entity_id = :eid"), {"eid": entity_id}
    ).scalar()
    emp_no  = f"EMP-{(count or 0) + 1:05d}"
    emp_id  = uuid4()

    db.execute(
        text("""
            INSERT INTO employee (
                id, entity_id, employee_no, full_name, nickname,
                nik_ktp, npwp, place_of_birth, date_of_birth,
                gender, marital_status, phone, email, address,
                department, position, employment_type, join_date,
                ptkp_status, number_of_dependents,
                bank_name, bank_account_no, bank_account_name,
                bpjs_kesehatan_no, bpjs_tk_no,
                registration_id, registration_status, status,
                created_at, updated_at
            ) VALUES (
                :id, :eid, :emp_no, :name, :nick,
                :nik, :npwp, :pob, :dob,
                :gender, :marital, :phone, :email, :addr,
                :dept, :pos, :emp_type, :join_date,
                :ptkp, :deps,
                :bank_name, :bank_no, :bank_holder,
                :bpjs_kes, :bpjs_tk,
                :reg_id, 'active', 'active',
                NOW(), NOW()
            )
        """),
        {
            "id": str(emp_id), "eid": entity_id, "emp_no": emp_no,
            "name": reg["full_name"], "nick": reg["nickname"],
            "nik": reg["nik_ktp"], "npwp": reg["npwp"],
            "pob": reg["place_of_birth"], "dob": reg["date_of_birth"],
            "gender": reg["gender"], "marital": reg["marital_status"],
            "phone": reg["phone"], "email": reg["email"], "addr": reg["address"],
            "dept": reg["department"], "pos": reg["position"],
            "emp_type": reg["employment_type"], "join_date": reg["join_date"],
            "ptkp": reg["ptkp_status"], "deps": reg["number_of_dependents"],
            "bank_name": reg["bank_name"], "bank_no": reg["bank_account_no"],
            "bank_holder": reg["bank_account_name"],
            "bpjs_kes": reg["bpjs_kesehatan_no"], "bpjs_tk": reg["bpjs_tk_no"],
            "reg_id": reg["id"],
        }
    )

    db.execute(
        text("""
            UPDATE employee_registration SET
                status = 'active', employee_id = :eid,
                approved_by = :by, approved_at = NOW(), updated_at = NOW()
            WHERE id = :id
        """),
        {"id": reg["id"], "eid": str(emp_id), "by": activated_by}
    )

    if reg.get("gaji_pokok_proposed"):
        comp_id = uuid4()
        db.execute(
            text("""
                INSERT INTO employee_payroll_component
                    (id, employee_id, effective_date, is_active, gaji_pokok, tunjangan_lain)
                VALUES (:id, :eid, :eff, TRUE, :gp, :tl)
            """),
            {
                "id": str(comp_id), "eid": str(emp_id),
                "eff": reg.get("join_date") or date.today(),
                "gp": float(reg["gaji_pokok_proposed"]),
                "tl": float(reg.get("tunjangan_proposed") or 0),
            }
        )

    return emp_id


def _get_employee_no(db: Session, employee_id: UUID) -> str:
    row = db.execute(
        text("SELECT employee_no FROM employee WHERE id = :id"), {"id": str(employee_id)}
    ).fetchone()
    return row.employee_no if row else ""
