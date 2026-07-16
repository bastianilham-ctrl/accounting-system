# main.py
# FastAPI entry point — Accounting System

import os
os.environ["PATH"] = r"C:\poppler-26.02.0\Library\bin" + os.pathsep + os.environ.get("PATH", "")
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from uuid import UUID
from decimal import Decimal
from datetime import date
from typing import List, Optional
import shutil

from sqlalchemy import text

from core.database import get_db, check_connection
from modules.journal_engine import JournalEngine, JournalEntry, JournalLine
from modules.vendor_scraper import VendorScraper
from modules.ap_processor import APProcessor
from modules.ap_classifier import APClassifier
from modules.ocr_router import router as ocr_router
from modules.vendor_tax_router import router as vendor_tax_router
from modules.ar_router import router as ar_router
from modules.auth_router import router as auth_router
from modules.auth import get_current_active_user, require_min_role
from modules.coa_templates import TEMPLATES, get_template, list_templates
from modules.asset_router import router as asset_router
from modules.ap_payment_router import router as ap_payment_router
from modules.bank_router import router as bank_router
from modules.pph21_router import router as pph21_router
from modules.ppn_router import router as ppn_router
from modules.vendor_registration_router import router as vendor_registration_router
from modules.employee_router import router as employee_router
from modules.attendance_router import router as attendance_router
from modules.budget_router import router as budget_router
from modules.procurement_router import router as procurement_router
from modules.costing_router import router as costing_router
from modules.journal_entry_router import router as journal_entry_router
from modules.inventory_router import router as inventory_router
from modules.project_setup_router import router as project_setup_router
from modules.contract_router import router as contract_router
from modules.deferred_revenue_router import router as deferred_revenue_router
from modules.bank_recon_router import router as bank_recon_router
from modules.expense_claim_router import router as expense_claim_router
from modules.leave_router import router as leave_router
from modules.withholding_tax_router import router as withholding_tax_router
from modules.sales_order_router import router as sales_order_router
from modules.year_end_closing_router import router as year_end_closing_router
from modules.forecast_router import router as forecast_router
from modules.dashboard_router import router as dashboard_router
from modules.attachment_router import router as attachment_router
from modules.audit_router import router as audit_router
from modules.permission_router import router as permission_router
from modules.opening_balance_router import router as opening_balance_router
from modules.notification_router import router as notification_router
from modules.multicurrency_router import router as multicurrency_router
from modules.intercompany_router import router as intercompany_router
from modules.payroll_disbursement_router import router as payroll_disbursement_router
from modules.invoice_template_router import router as invoice_template_router
from modules.financial_report_router import router as financial_report_router
from modules.coa_router import router as coa_router
from modules.cash_bank_router import router as cash_bank_router
from modules.entity_router import router as entity_router
from modules.contact_router import router as contact_router, person_router as contact_person_router
from modules.email_marketing_router import router as email_marketing_router, tracking_router as email_tracking_router
from modules.scheduler import start_scheduler
from config.settings import settings
from loguru import logger

# ============================================================
# LIFESPAN (Startup & Shutdown)
# ============================================================

@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not check_connection():
        raise RuntimeError("Database tidak bisa diakses. Cek konfigurasi .env")

    # Start Background Scheduler
    scheduler = start_scheduler()

    logger.info("Accounting System API started")
    yield
    # Shutdown Scheduler saat aplikasi berhenti
    scheduler.shutdown()

app = FastAPI(
    title="Accounting System API",
    description=(
        "Journal Engine · Vendor Scraper · AP/AR · Fixed Assets · "
        "Auto Tax Classification · PPh 21 TER · PPN Reconcile · "
        "Vendor Registration · Employee & Payroll · Attendance · "
        "Budget FICO · Procurement PR/PO · "
        "Unit Costing · Project P&L · Overhead Allocation · "
        "Journal Entry Workflow · Multi-Currency · "
        "Inventory (AVCO/FIFO/Standard) · Perpetual GL · Reorder Rules · "
        "Project Setup (Charter, SOW, WBS, CPM, EVM, Risk, RACI) · Cost Center · "
        "Contract Tracker (Legal×PM×Finance, BAST Gate, AR Aging, Dashboard) · "
        "Bank Reconciliation (Import, Auto-Match, Manual Match, Adjustment, Finalize) · "
        "Expense Claim & Reimbursement (Advance, Approval Flow, GL Posting, Billable Passthrough) · "
        "Leave Management (Entitlement, Carry-Forward, Approval Flow, LWOP Payroll Integration) · "
        "PPh 23 & PPh 4(2) (Bukti Potong, SPT Masa, GL Posting) · "
        "Sales Order (Quotation→SO→Picking→DO→AR Invoice, Stock Check) · "
        "Year-End Closing (Pre-Check, Income Summary, RE Transfer, Period Lock) · "
        "Dashboard (KPI Cards, P&L Trend, AR/AP Aging, Working Capital, DSO/DPO/DIO) · "
        "FP&A Engine (Driver-Based Forecast, Rolling 12-Month, Three-Way P&L+CF+BS, "
        "What-If Analysis, Jasa/Dagang/Konstruksi, PoC, MRP, Procurement Schedule) · "
        "File Attachments (Upload, Dedup SHA-256, Polymorphic Link, Download, Search) · "
        "Audit Trail (per-transaksi, before/after diff, severity, export CSV) · "
        "Multi-Entity Permission (viewer/finance/approver/admin per entity, module restriction) · "
        "Opening Balance (GL Trial Balance, AR/AP Detail, Fixed Asset Register, "
        "Inventory, Bank, Leave — validate + finalize → populate semua modul) · "
        "Multi-Currency Engine (Currency Master, Exchange Rate Harian, Konversi FCY→IDR, "
        "Revaluasi Periodik Unrealized G/L, Auto-Reverse, Realized G/L saat Settlement, "
        "FCY Exposure Report, GL dengan amount_fcy + exchange_rate) · "
        "Intercompany Transactions (Charge/Recharge/Loan/Equity/Dividend/Cash Transfer, "
        "Dual-Entity GL Posting, Settlement, Reversal, Aging, Elimination Schedule) · "
        "Payroll Disbursement (Batch dari Payroll Run, Accrual Journal, "
        "Dr Hutang Gaji|Cr Bank, Per-Karyawan Transfer Status, Bank File Export CSV) · "
        "Invoice Template & Email (Custom HTML Jinja2 Template, Logo Upload, "
        "PDF via WeasyPrint, Email Template Subject+Body, SMTP Config per Entity, "
        "Draft Preview HTML/PDF, Post+Auto-Send Email, Email Log) · "
        "Laporan Keuangan Formal (Trial Balance, Neraca, Laba Rugi, Buku Besar, "
        "Arus Kas Metode Tidak Langsung — PDF + Excel Export, Comparative Period) · "
        "Cash & Bank (Kas Tunai Non-AP/AR, Petty Cash Imprest + Top-up, "
        "In-house Transfer Antar Rekening/Kas, Saldo Kas Real-time dari GL)"
    ),
    version="3.4.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth router — publik (login, refresh tidak butuh token)
app.include_router(auth_router)

# Protected routers — semua butuh Bearer token
_auth_dep = [Depends(get_current_active_user)]
app.include_router(ocr_router,          dependencies=_auth_dep)
app.include_router(vendor_tax_router,   dependencies=_auth_dep)
app.include_router(ar_router,           dependencies=_auth_dep)
app.include_router(asset_router,        dependencies=_auth_dep)
app.include_router(ap_payment_router,   dependencies=_auth_dep)
app.include_router(bank_router,         dependencies=_auth_dep)
app.include_router(pph21_router,               dependencies=_auth_dep)
app.include_router(ppn_router,                 dependencies=_auth_dep)
app.include_router(vendor_registration_router, dependencies=_auth_dep)
app.include_router(employee_router,            dependencies=_auth_dep)
app.include_router(attendance_router,          dependencies=_auth_dep)
app.include_router(budget_router,              dependencies=_auth_dep)
app.include_router(procurement_router,         dependencies=_auth_dep)
app.include_router(costing_router,             dependencies=_auth_dep)
app.include_router(journal_entry_router,       dependencies=_auth_dep)
app.include_router(inventory_router,           dependencies=_auth_dep)
app.include_router(project_setup_router,      dependencies=_auth_dep)
app.include_router(contract_router,           dependencies=_auth_dep)
app.include_router(deferred_revenue_router,   dependencies=_auth_dep)
app.include_router(bank_recon_router,         dependencies=_auth_dep)
app.include_router(expense_claim_router,      dependencies=_auth_dep)
app.include_router(leave_router,              dependencies=_auth_dep)
app.include_router(withholding_tax_router,    dependencies=_auth_dep)
app.include_router(sales_order_router,        dependencies=_auth_dep)
app.include_router(year_end_closing_router,   dependencies=_auth_dep)
app.include_router(forecast_router,           dependencies=_auth_dep)
app.include_router(dashboard_router,          dependencies=_auth_dep)
app.include_router(notification_router,       dependencies=_auth_dep)
app.include_router(attachment_router,         dependencies=_auth_dep)
app.include_router(audit_router,              dependencies=_auth_dep)
app.include_router(permission_router,         dependencies=_auth_dep)
app.include_router(opening_balance_router,    dependencies=_auth_dep)
app.include_router(multicurrency_router,          dependencies=_auth_dep)
app.include_router(intercompany_router,           dependencies=_auth_dep)
app.include_router(payroll_disbursement_router,   dependencies=_auth_dep)
app.include_router(invoice_template_router,       dependencies=_auth_dep)
app.include_router(financial_report_router,       dependencies=_auth_dep)
app.include_router(coa_router,                    dependencies=_auth_dep)
app.include_router(cash_bank_router,              dependencies=_auth_dep)
app.include_router(entity_router,                 dependencies=_auth_dep)
app.include_router(contact_router,                dependencies=_auth_dep)
app.include_router(contact_person_router,         dependencies=_auth_dep)
app.include_router(email_marketing_router,        dependencies=_auth_dep)
app.include_router(email_tracking_router)   # public — no auth, for tracking pixel


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/")
def root():
    return {"status": "running", "system": "Accounting System v1.2"}

@app.get("/health")
def health(db=Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}


# ============================================================
# JOURNAL ENDPOINTS
# ============================================================

class JournalLineRequest(BaseModel):
    account_code: str
    description: str
    debit_idr: Decimal = Decimal("0")
    credit_idr: Decimal = Decimal("0")
    vendor_id: Optional[UUID] = None
    cost_center: Optional[str] = None
    tax_code: Optional[str] = None
    tax_amount: Decimal = Decimal("0")

class JournalRequest(BaseModel):
    entity_id: UUID
    journal_type: str
    journal_date: date
    description: str
    lines: List[JournalLineRequest]
    reference_no: Optional[str] = None
    currency: str = "IDR"
    source: str = "manual"
    created_by: str = "system"


@app.post("/journals/post", dependencies=[Depends(require_min_role("finance"))])
def post_journal(req: JournalRequest, db=Depends(get_db)):
    """Posting jurnal baru dengan validasi double entry."""
    engine = JournalEngine(db)
    entry = JournalEntry(
        entity_id=req.entity_id,
        journal_type=req.journal_type,
        journal_date=req.journal_date,
        description=req.description,
        lines=[JournalLine(**l.model_dump()) for l in req.lines],
        reference_no=req.reference_no,
        currency=req.currency,
        source=req.source,
        created_by=req.created_by,
    )
    result = engine.post_journal(entry)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/journals/{journal_id}/reverse", dependencies=[Depends(require_min_role("finance"))])
def reverse_journal(journal_id: UUID, reason: str, reversed_by: str, db=Depends(get_db)):
    """Buat jurnal balik dari jurnal yang sudah diposting."""
    engine = JournalEngine(db)
    result = engine.reverse_journal(journal_id, reason, reversed_by)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ============================================================
# VENDOR ENDPOINTS
# ============================================================

@app.get("/vendors", dependencies=[Depends(require_min_role("viewer"))])
def search_vendors(
    search: str = "",
    entity_id: Optional[UUID] = None,
    limit: int = 20,
    db=Depends(get_db),
):
    """Cari vendor by nama / kode / NPWP."""
    filters = []
    params  = {"limit": limit}

    if search:
        filters.append("(v.vendor_name ILIKE :q OR v.vendor_code ILIKE :q OR v.npwp ILIKE :q)")
        params["q"] = f"%{search}%"

    if entity_id:
        filters.append("v.entity_id = :eid")
        params["eid"] = str(entity_id)

    where_clause = ("WHERE " + " AND ".join(filters)) if filters else ""

    rows = db.execute(
        text(f"""
            SELECT
                v.id, v.vendor_code, v.vendor_name, v.npwp,
                v.vendor_category, v.has_skb, v.skb_expiry,
                v.umkm_cert_expiry, v.default_pph_type, v.default_pph_rate,
                v.default_payment_term_days
            FROM vendor v
            {where_clause}
            ORDER BY v.vendor_name ASC
            LIMIT :limit
        """),
        params
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@app.post("/vendors/{vendor_id}/enrich", dependencies=[Depends(require_min_role("admin"))])
async def enrich_vendor(vendor_id: UUID, db=Depends(get_db)):
    """Jalankan scraping dan enrichment data vendor."""
    scraper = VendorScraper(db)
    result  = await scraper.enrich_vendor(vendor_id)
    return result


# ============================================================
# AP WORKFLOW ENDPOINT (OCR + TAX + JOURNAL)
# ============================================================

@app.post("/ap/upload", dependencies=[Depends(require_min_role("finance"))])
async def upload_invoice_workflow(
    entity_id: UUID,
    user_id: str,
    file: UploadFile = File(...),
    db=Depends(get_db)
):
    """Workflow Otomatis: Upload PDF -> OCR -> Validasi Pajak -> Posting Jurnal."""
    temp_path = os.path.join(settings.UPLOAD_DIR, file.filename)
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    try:
        processor = APProcessor(db)
        result    = processor.process_invoice_upload(temp_path, entity_id, user_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# AP CLASSIFICATION ENDPOINT
# ============================================================

class ClassifyRequest(BaseModel):
    vendor_id: UUID
    description: str
    amount: Decimal
    service_period_months: int = 1

@app.post("/ap/classify", dependencies=[Depends(require_min_role("finance"))])
def classify_invoice(req: ClassifyRequest, db=Depends(get_db)):
    """Klasifikasikan invoice AP: expense / prepaid / fixed_asset."""
    classifier = APClassifier(db)
    result = classifier.classify(
        vendor_id=req.vendor_id,
        description=req.description,
        amount=req.amount,
        service_period_months=req.service_period_months,
    )
    return result


# ============================================================
# REPORTS
# ============================================================

@app.get("/reports/trial-balance/{entity_id}", dependencies=[Depends(require_min_role("viewer"))])
def trial_balance(entity_id: UUID, db=Depends(get_db)):
    """Trial balance dari semua jurnal posted."""
    rows = db.execute(
        text("SELECT * FROM vw_trial_balance WHERE entity_id = :eid ORDER BY account_code"),
        {"eid": str(entity_id)}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@app.get("/reports/ap-aging/{entity_id}", dependencies=[Depends(require_min_role("viewer"))])
def ap_aging(entity_id: UUID, db=Depends(get_db)):
    """AP aging dari view vw_ap_aging."""
    rows = db.execute(
        text("""
            SELECT v.* FROM vw_ap_aging v
            JOIN ap_invoice ai ON ai.invoice_no = v.invoice_no
            WHERE ai.entity_id = :eid
        """),
        {"eid": str(entity_id)}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@app.get("/reports/profit-loss/{entity_id}", dependencies=[Depends(require_min_role("viewer"))])
def profit_loss(
    entity_id: UUID,
    year: int,
    from_month: int = 1,
    to_month: int = 12,
    db=Depends(get_db),
):
    """
    Laporan Laba Rugi per akun untuk rentang bulan dalam satu tahun.
    Response mencakup:
    - Baris revenue + expense per akun
    - Summary: total revenue, total expense, net income/loss
    """
    if not (1 <= from_month <= 12 and 1 <= to_month <= 12 and from_month <= to_month):
        raise HTTPException(400, "from_month/to_month harus antara 1-12 dan from <= to")

    rows = db.execute(
        text("""
            SELECT
                account_code, account_name, account_type,
                SUM(amount)       AS amount,
                SUM(total_debit)  AS total_debit,
                SUM(total_credit) AS total_credit
            FROM vw_profit_loss
            WHERE entity_id  = :eid
              AND year        = :year
              AND month BETWEEN :from_m AND :to_m
            GROUP BY account_code, account_name, account_type
            ORDER BY account_type, account_code
        """),
        {
            "eid": str(entity_id), "year": year,
            "from_m": from_month, "to_m": to_month,
        }
    ).fetchall()

    lines        = [dict(r._mapping) for r in rows]
    total_rev    = sum(float(r["amount"]) for r in lines if r["account_type"] == "revenue")
    total_exp    = sum(float(r["amount"]) for r in lines if r["account_type"] == "expense")
    net_income   = total_rev - total_exp

    return {
        "entity_id":     str(entity_id),
        "year":          year,
        "period":        f"{year}-{from_month:02d} s/d {year}-{to_month:02d}",
        "lines":         lines,
        "summary": {
            "total_revenue":  total_rev,
            "total_expense":  total_exp,
            "net_income":     net_income,
            "net_income_label": "LABA" if net_income >= 0 else "RUGI",
        },
    }


@app.get("/reports/balance-sheet/{entity_id}", dependencies=[Depends(require_min_role("viewer"))])
def balance_sheet(entity_id: UUID, as_of_date: Optional[date] = None, db=Depends(get_db)):
    """
    Neraca (Balance Sheet) kumulatif.
    as_of_date: filter sampai tanggal tertentu (opsional, default semua posted jurnal).
    Response mencakup: asset, liabilities, equity + summary check (asset = liab + equity).
    """
    date_filter = ""
    params: dict = {"eid": str(entity_id)}

    if as_of_date:
        # Filter hanya jurnal sampai as_of_date
        date_filter = "AND j.journal_date <= :aod"
        params["aod"] = as_of_date

        rows = db.execute(
            text(f"""
                SELECT
                    coa.account_code, coa.account_name, coa.account_type,
                    coa.normal_balance, coa.level,
                    COALESCE(SUM(gl.debit_idr),  0) AS total_debit,
                    COALESCE(SUM(gl.credit_idr), 0) AS total_credit,
                    CASE coa.normal_balance
                        WHEN 'debit'  THEN COALESCE(SUM(gl.debit_idr),  0) - COALESCE(SUM(gl.credit_idr), 0)
                        WHEN 'credit' THEN COALESCE(SUM(gl.credit_idr), 0) - COALESCE(SUM(gl.debit_idr),  0)
                    END AS balance
                FROM chart_of_accounts coa
                LEFT JOIN gl_line    gl  ON gl.account_id  = coa.id
                LEFT JOIN gl_journal j   ON j.id = gl.journal_id AND j.status = 'posted'
                                         {date_filter}
                WHERE coa.entity_id   = :eid
                  AND coa.account_type IN (
                      'asset','liability','equity',
                      'prepaid','fixed_asset','accumulated_depreciation'
                  )
                GROUP BY coa.account_code, coa.account_name, coa.account_type,
                         coa.normal_balance, coa.level
                ORDER BY coa.account_type, coa.account_code
            """),
            params
        ).fetchall()
    else:
        rows = db.execute(
            text("""
                SELECT account_code, account_name, account_type, normal_balance,
                       level, total_debit, total_credit, balance
                FROM vw_balance_sheet
                WHERE entity_id = :eid
                ORDER BY account_type, account_code
            """),
            params
        ).fetchall()

    lines = [dict(r._mapping) for r in rows]

    total_asset = sum(
        float(r["balance"]) for r in lines
        if r["account_type"] in ("asset", "prepaid", "fixed_asset", "accumulated_depreciation")
    )
    total_liab  = sum(float(r["balance"]) for r in lines if r["account_type"] == "liability")
    total_equity = sum(float(r["balance"]) for r in lines if r["account_type"] == "equity")

    return {
        "entity_id":  str(entity_id),
        "as_of_date": str(as_of_date) if as_of_date else "all-time",
        "lines":      lines,
        "summary": {
            "total_asset":   total_asset,
            "total_liability": total_liab,
            "total_equity":  total_equity,
            "balanced":      abs(total_asset - (total_liab + total_equity)) < 1.0,
        },
    }


@app.get("/reports/cash-flow/{entity_id}", dependencies=[Depends(require_min_role("viewer"))])
def cash_flow(entity_id: UUID, year: int, db=Depends(get_db)):
    """
    Laporan Arus Kas simplified (indirect method).
    Operating: Net income + penyesuaian depresiasi + perubahan modal kerja
    Investing: Pembelian/pelepasan aset tetap
    Financing: Perubahan equity / pinjaman bank
    """
    # Net income dari P&L
    pl_rows = db.execute(
        text("""
            SELECT account_type, SUM(amount) AS total
            FROM vw_profit_loss
            WHERE entity_id = :eid AND year = :year
            GROUP BY account_type
        """),
        {"eid": str(entity_id), "year": year}
    ).fetchall()

    pl_map     = {r.account_type: float(r.total) for r in pl_rows}
    net_income = pl_map.get("revenue", 0) - pl_map.get("expense", 0)

    # Depresiasi (non-kas — tambahkan kembali ke operating)
    dep_row = db.execute(
        text("""
            SELECT COALESCE(SUM(ads.commercial_dep), 0) AS total_dep
            FROM asset_depreciation_schedule ads
            JOIN fixed_asset fa ON fa.id = ads.asset_id
            WHERE fa.entity_id = :eid
              AND EXTRACT(YEAR FROM ads.period_date) = :year
              AND ads.is_posted = TRUE
        """),
        {"eid": str(entity_id), "year": year}
    ).fetchone()
    total_dep = float(dep_row.total_dep or 0)

    # Amortisasi prepaid (non-kas)
    amort_row = db.execute(
        text("""
            SELECT COALESCE(SUM(pas.amortize_amount), 0) AS total_amort
            FROM prepaid_amortization_schedule pas
            JOIN prepaid_expense pe ON pe.id = pas.prepaid_id
            WHERE pe.entity_id = :eid
              AND EXTRACT(YEAR FROM pas.period_date) = :year
              AND pas.is_posted = TRUE
        """),
        {"eid": str(entity_id), "year": year}
    ).fetchone()
    total_amort = float(amort_row.total_amort or 0)

    # Perubahan AR (piutang naik = kas turun)
    ar_row = db.execute(
        text("""
            SELECT
                COALESCE(SUM(total_amount - paid_amount), 0) AS ar_outstanding
            FROM ar_invoice
            WHERE entity_id = :eid
              AND EXTRACT(YEAR FROM invoice_date) = :year
              AND status NOT IN ('cancelled')
        """),
        {"eid": str(entity_id), "year": year}
    ).fetchone()

    # Perubahan AP (hutang naik = kas naik)
    ap_row = db.execute(
        text("""
            SELECT
                COALESCE(SUM(total_amount - pph_amount - paid_amount), 0) AS ap_outstanding
            FROM ap_invoice
            WHERE entity_id = :eid
              AND EXTRACT(YEAR FROM invoice_date) = :year
              AND status NOT IN ('cancelled')
        """),
        {"eid": str(entity_id), "year": year}
    ).fetchone()

    # Investing: pembelian aset tahun ini
    asset_row = db.execute(
        text("""
            SELECT COALESCE(SUM(acquisition_cost), 0) AS total_capex
            FROM fixed_asset
            WHERE entity_id = :eid
              AND EXTRACT(YEAR FROM acquisition_date) = :year
        """),
        {"eid": str(entity_id), "year": year}
    ).fetchone()

    ar_change      = -float(ar_row.ar_outstanding or 0)    # AR naik = kas turun
    ap_change      = float(ap_row.ap_outstanding or 0)     # AP naik = kas naik
    total_capex    = float(asset_row.total_capex or 0)

    operating_cf = net_income + total_dep + total_amort + ar_change + ap_change
    investing_cf = -total_capex
    net_cf       = operating_cf + investing_cf

    return {
        "entity_id": str(entity_id),
        "year":      year,
        "operating": {
            "net_income":          net_income,
            "add_depreciation":    total_dep,
            "add_amortization":    total_amort,
            "change_ar":           ar_change,
            "change_ap":           ap_change,
            "total_operating_cf":  operating_cf,
        },
        "investing": {
            "capex":               -total_capex,
            "total_investing_cf":  investing_cf,
        },
        "financing": {
            "note": "Financing activities perlu input manual (pinjaman, setoran modal, dividen)",
            "total_financing_cf": 0,
        },
        "net_cash_flow": net_cf,
    }


# ============================================================
# SETUP — COA Template
# ============================================================

class ApplyCOARequest(BaseModel):
    entity_id: UUID
    business_type: str   # jasa | dagang | konstruksi | manufaktur | rental | properti | logistik
    overwrite: bool = False  # True = hapus COA existing lalu insert ulang

@app.get("/setup/coa-templates", dependencies=[Depends(require_min_role("viewer"))])
def coa_template_list():
    """Daftar template COA yang tersedia beserta jumlah akun."""
    return list_templates()

@app.post("/setup/apply-coa-template", dependencies=[Depends(require_min_role("admin"))])
def apply_coa_template(req: ApplyCOARequest, db=Depends(get_db)):
    """
    Terapkan template COA standard ke sebuah entity.
    - business_type: jasa / dagang / konstruksi / manufaktur / rental / properti / logistik
    - overwrite=false (default): skip akun yang sudah ada
    - overwrite=true: hapus semua COA entity lalu insert ulang dari template
    """
    # Validasi entity ada
    entity = db.execute(
        text("SELECT id, name FROM entity WHERE id = :id"),
        {"id": str(req.entity_id)}
    ).fetchone()
    if not entity:
        raise HTTPException(404, f"Entity {req.entity_id} tidak ditemukan")

    # Validasi business_type
    try:
        accounts = get_template(req.business_type)
    except ValueError as e:
        raise HTTPException(400, str(e))

    if req.overwrite:
        db.execute(
            text("DELETE FROM chart_of_accounts WHERE entity_id = :eid"),
            {"eid": str(req.entity_id)}
        )

    from uuid import uuid4 as _uuid4
    inserted = 0
    skipped  = 0

    for (code, name, acc_type, normal_bal, is_header) in accounts:
        existing = db.execute(
            text("""
                SELECT id FROM chart_of_accounts
                WHERE entity_id = :eid AND account_code = :code
            """),
            {"eid": str(req.entity_id), "code": code}
        ).fetchone()

        if existing and not req.overwrite:
            skipped += 1
            continue

        db.execute(
            text("""
                INSERT INTO chart_of_accounts
                    (id, entity_id, account_code, account_name,
                     account_type, normal_balance, is_header, is_active, level)
                VALUES
                    (:id, :eid, :code, :name,
                     :atype, :nbal, :header, TRUE, :level)
                ON CONFLICT (entity_id, account_code) DO NOTHING
            """),
            {
                "id":     str(_uuid4()),
                "eid":    str(req.entity_id),
                "code":   code,
                "name":   name,
                "atype":  acc_type,
                "nbal":   normal_bal,
                "header": is_header,
                "level":  len(code.split("-")),
            }
        )
        inserted += 1

    db.commit()

    tpl_info = TEMPLATES[req.business_type.lower()]
    return {
        "success":       True,
        "entity_id":     str(req.entity_id),
        "entity_name":   entity.name,
        "business_type": req.business_type,
        "template_name": tpl_info["name"],
        "total_template": len(accounts),
        "inserted":      inserted,
        "skipped":       skipped,
    }


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.API_DEBUG,
    )
