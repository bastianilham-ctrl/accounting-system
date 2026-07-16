"""
Financial Report Router
========================
Endpoint:
  GET  /financial-reports/trial-balance
  GET  /financial-reports/balance-sheet
  GET  /financial-reports/profit-loss
  GET  /financial-reports/general-ledger
  GET  /financial-reports/cash-flow

Export (tambahkan ?format=pdf atau ?format=excel ke endpoint di atas):
  GET  /financial-reports/trial-balance?format=pdf&...
  GET  /financial-reports/balance-sheet?format=excel&...
  dst.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from core.database import get_db
from modules.auth import get_current_active_user
from .financial_report_engine import FinancialReportEngine

router = APIRouter(prefix="/financial-reports", tags=["Financial Reports"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pdf_response(data: bytes, filename: str) -> Response:
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _excel_response(data: bytes, filename: str) -> Response:
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── 1. TRIAL BALANCE ─────────────────────────────────────────────────────────

@router.get("/trial-balance")
async def get_trial_balance(
    entity_id:             str,
    fiscal_year:           int,
    fiscal_month:          int,
    include_zero_balance:  bool = False,
    compare_year:          Optional[int] = None,
    compare_month:         Optional[int] = None,
    format:                Optional[str] = Query(None, description="pdf | excel"),
    db: Session = Depends(get_db),
    _user=Depends(get_current_active_user),
):
    """
    Neraca Saldo (Trial Balance).

    - **fiscal_year** + **fiscal_month**: periode laporan
    - **include_zero_balance**: sertakan akun bersaldo nol (default false)
    - **compare_year** + **compare_month**: periode pembanding opsional
    - **format**: kosong → JSON, `pdf` → PDF, `excel` → Excel
    """
    try:
        data = FinancialReportEngine.get_trial_balance(
            db, entity_id, fiscal_year, fiscal_month,
            include_zero_balance=include_zero_balance,
            compare_year=compare_year,
            compare_month=compare_month,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if format == "pdf":
        pdf = FinancialReportEngine.export_pdf(data)
        return _pdf_response(pdf, f"trial_balance_{fiscal_year}{fiscal_month:02d}.pdf")
    if format == "excel":
        xlsx = FinancialReportEngine.export_excel(data)
        return _excel_response(xlsx, f"trial_balance_{fiscal_year}{fiscal_month:02d}.xlsx")
    return data


# ── 2. BALANCE SHEET ─────────────────────────────────────────────────────────

@router.get("/balance-sheet")
async def get_balance_sheet(
    entity_id:    str,
    as_of_date:   date,
    compare_date: Optional[date] = None,
    format:       Optional[str] = Query(None, description="pdf | excel"),
    db: Session = Depends(get_db),
    _user=Depends(get_current_active_user),
):
    """
    Neraca (Balance Sheet) per tanggal.

    - **as_of_date**: tanggal laporan (misal 2024-05-31)
    - **compare_date**: tanggal pembanding opsional (misal 2023-12-31)
    """
    try:
        data = FinancialReportEngine.get_balance_sheet(db, entity_id, as_of_date, compare_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if format == "pdf":
        pdf = FinancialReportEngine.export_pdf(data)
        return _pdf_response(pdf, f"neraca_{as_of_date}.pdf")
    if format == "excel":
        xlsx = FinancialReportEngine.export_excel(data)
        return _excel_response(xlsx, f"neraca_{as_of_date}.xlsx")
    return data


# ── 3. PROFIT & LOSS ─────────────────────────────────────────────────────────

@router.get("/profit-loss")
async def get_profit_loss(
    entity_id:   str,
    from_date:   date,
    to_date:     date,
    compare_from: Optional[date] = None,
    compare_to:   Optional[date] = None,
    format:       Optional[str] = Query(None, description="pdf | excel"),
    db: Session = Depends(get_db),
    _user=Depends(get_current_active_user),
):
    """
    Laporan Laba Rugi untuk rentang tanggal.

    - **from_date** / **to_date**: periode laporan
    - **compare_from** / **compare_to**: periode pembanding opsional
    - **format**: kosong → JSON, `pdf` → PDF, `excel` → Excel

    Tips:
    - Satu bulan: `from_date=2024-05-01&to_date=2024-05-31`
    - YTD: `from_date=2024-01-01&to_date=2024-05-31`
    - Full year: `from_date=2024-01-01&to_date=2024-12-31`
    """
    try:
        data = FinancialReportEngine.get_profit_loss(
            db, entity_id, from_date, to_date, compare_from, compare_to
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if format == "pdf":
        pdf = FinancialReportEngine.export_pdf(data)
        return _pdf_response(pdf, f"laba_rugi_{from_date}_{to_date}.pdf")
    if format == "excel":
        xlsx = FinancialReportEngine.export_excel(data)
        return _excel_response(xlsx, f"laba_rugi_{from_date}_{to_date}.xlsx")
    return data


# ── 4. GENERAL LEDGER ────────────────────────────────────────────────────────

@router.get("/general-ledger")
async def get_general_ledger(
    entity_id:    str,
    from_date:    date,
    to_date:      date,
    account_code: Optional[str] = Query(None, description="Filter akun tertentu, misal '1-1110'"),
    account_type: Optional[str] = Query(None, description="Filter tipe: asset | liability | equity | revenue | expense"),
    journal_type: Optional[str] = Query(None, description="Filter tipe jurnal: JU | JV | AR | AP | dst"),
    page:         int = Query(1, ge=1),
    size:         int = Query(100, ge=1, le=500),
    format:       Optional[str] = Query(None, description="pdf | excel"),
    db: Session = Depends(get_db),
    _user=Depends(get_current_active_user),
):
    """
    Buku Besar (General Ledger) — semua transaksi per akun dengan running balance.

    - **account_code**: jika diisi, tampilkan satu akun saja
    - **account_type**: filter berdasarkan tipe akun
    - **journal_type**: filter tipe jurnal (JU, AR, AP, JV, dll)
    - **page** / **size**: pagination untuk akun dengan banyak transaksi
    """
    try:
        data = FinancialReportEngine.get_general_ledger(
            db, entity_id, from_date, to_date,
            account_code=account_code,
            account_type=account_type,
            journal_type=journal_type,
            page=page,
            size=size,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if format == "pdf":
        pdf = FinancialReportEngine.export_pdf(data)
        fname = f"buku_besar_{account_code or 'all'}_{from_date}_{to_date}.pdf"
        return _pdf_response(pdf, fname)
    if format == "excel":
        xlsx = FinancialReportEngine.export_excel(data)
        fname = f"buku_besar_{account_code or 'all'}_{from_date}_{to_date}.xlsx"
        return _excel_response(xlsx, fname)
    return data


# ── 5. CASH FLOW ─────────────────────────────────────────────────────────────

@router.get("/cash-flow")
async def get_cash_flow(
    entity_id:   str,
    from_date:   date,
    to_date:     date,
    compare_from: Optional[date] = None,
    compare_to:   Optional[date] = None,
    format:       Optional[str] = Query(None, description="pdf | excel"),
    db: Session = Depends(get_db),
    _user=Depends(get_current_active_user),
):
    """
    Laporan Arus Kas (Cash Flow Statement) — Metode Tidak Langsung.

    Struktur:
    - **I. Arus Kas Operasi**: Laba Bersih + Depresiasi ± Perubahan Modal Kerja
    - **II. Arus Kas Investasi**: Pembelian/Penjualan Aset Tetap
    - **III. Arus Kas Pendanaan**: Pinjaman, Setoran Modal, Dividen

    Klasifikasi akun berdasarkan prefix kode akun:
    - 1-5xxx, 1-6xxx, 1-7xxx, 1-8xxx → Investasi
    - 2-3xxx, 2-4xxx, 3-xxx → Pendanaan
    - Lainnya → Operasi
    """
    try:
        data = FinancialReportEngine.get_cash_flow(
            db, entity_id, from_date, to_date, compare_from, compare_to
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if format == "pdf":
        pdf = FinancialReportEngine.export_pdf(data)
        return _pdf_response(pdf, f"arus_kas_{from_date}_{to_date}.pdf")
    if format == "excel":
        xlsx = FinancialReportEngine.export_excel(data)
        return _excel_response(xlsx, f"arus_kas_{from_date}_{to_date}.xlsx")
    return data


# ── Quick reference endpoints ─────────────────────────────────────────────────

@router.get("/available-reports")
async def list_available_reports():
    """Daftar laporan keuangan yang tersedia beserta parameter yang dibutuhkan."""
    return {
        "reports": [
            {
                "name":        "Trial Balance (Neraca Saldo)",
                "endpoint":    "/financial-reports/trial-balance",
                "required":    ["entity_id", "fiscal_year", "fiscal_month"],
                "optional":    ["include_zero_balance", "compare_year", "compare_month", "format"],
                "description": "Saldo semua akun per periode dengan opening, mutasi, dan closing balance",
            },
            {
                "name":        "Balance Sheet (Neraca)",
                "endpoint":    "/financial-reports/balance-sheet",
                "required":    ["entity_id", "as_of_date"],
                "optional":    ["compare_date", "format"],
                "description": "Neraca posisi keuangan per tanggal tertentu (Aktiva = Kewajiban + Ekuitas)",
            },
            {
                "name":        "Profit & Loss (Laba Rugi)",
                "endpoint":    "/financial-reports/profit-loss",
                "required":    ["entity_id", "from_date", "to_date"],
                "optional":    ["compare_from", "compare_to", "format"],
                "description": "Laporan laba rugi: Pendapatan - HPP - Beban = Laba Bersih",
            },
            {
                "name":        "General Ledger (Buku Besar)",
                "endpoint":    "/financial-reports/general-ledger",
                "required":    ["entity_id", "from_date", "to_date"],
                "optional":    ["account_code", "account_type", "journal_type", "page", "size", "format"],
                "description": "Detail transaksi per akun dengan running balance",
            },
            {
                "name":        "Cash Flow (Arus Kas)",
                "endpoint":    "/financial-reports/cash-flow",
                "required":    ["entity_id", "from_date", "to_date"],
                "optional":    ["compare_from", "compare_to", "format"],
                "description": "Arus kas metode tidak langsung (Operasi, Investasi, Pendanaan)",
            },
        ],
        "export_formats": ["pdf", "excel"],
        "note": "Tambahkan ?format=pdf atau ?format=excel ke endpoint mana pun untuk ekspor",
    }
