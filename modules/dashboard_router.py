"""
Dashboard Router
Base prefix: /dashboard
"""

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.database import get_db
from modules.dashboard_engine import DashboardEngine

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


# ── Full Dashboard ─────────────────────────────────────────────────────────────

@router.get("/", summary="Full dashboard: KPI + trend + aging + WC + CF (single call)")
def full_dashboard(
    entity_id:   UUID,
    as_of_date:  date  = Query(default=date.today()),
    scenario_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
):
    return DashboardEngine.get_full_dashboard(db, entity_id, as_of_date, scenario_id)


# ── KPI Cards ─────────────────────────────────────────────────────────────────

@router.get("/kpis", summary="KPI cards untuk periode berjalan")
def kpi_cards(
    entity_id:  UUID,
    as_of_date: date = Query(default=date.today()),
    db: Session = Depends(get_db),
):
    return DashboardEngine.get_kpi_cards(db, entity_id, as_of_date)


# ── P&L Trend ─────────────────────────────────────────────────────────────────

@router.get("/pl-trend", summary="Trend P&L 12 bulan terakhir")
def pl_trend(
    entity_id: UUID,
    months:    int = Query(default=12, ge=3, le=36),
    db: Session = Depends(get_db),
):
    return DashboardEngine.get_pl_monthly_trend(db, entity_id, months)


# ── AR / AP Aging ─────────────────────────────────────────────────────────────

@router.get("/ar-aging", summary="AR aging: current / 1-30 / 31-60 / 61-90 / 90+")
def ar_aging(entity_id: UUID, db: Session = Depends(get_db)):
    return DashboardEngine.get_ar_aging(db, entity_id)


@router.get("/ap-aging", summary="AP aging: current / 1-30 / 31-60 / 61-90 / 90+")
def ap_aging(entity_id: UUID, db: Session = Depends(get_db)):
    return DashboardEngine.get_ap_aging(db, entity_id)


# ── Working Capital ───────────────────────────────────────────────────────────

@router.get("/working-capital", summary="DSO / DPO / DIO / CCC metrics")
def working_capital(entity_id: UUID, db: Session = Depends(get_db)):
    return DashboardEngine.get_working_capital_metrics(db, entity_id)


# ── Cash Flow Position ─────────────────────────────────────────────────────────

@router.get("/cashflow-position", summary="Posisi arus kas periode berjalan (dari bank statement)")
def cashflow_position(
    entity_id: UUID,
    year:  int = Query(default=date.today().year),
    month: int = Query(default=date.today().month, ge=1, le=12),
    db: Session = Depends(get_db),
):
    return DashboardEngine.get_cashflow_position(db, entity_id, year, month)


# ── Revenue Breakdown ─────────────────────────────────────────────────────────

@router.get("/revenue-breakdown", summary="Breakdown pendapatan per customer / project / product")
def revenue_breakdown(
    entity_id: UUID,
    year:     int  = Query(default=date.today().year),
    month:    int  = Query(default=date.today().month, ge=1, le=12),
    group_by: str  = Query(default="customer", regex="^(customer|project|product)$"),
    db: Session = Depends(get_db),
):
    return DashboardEngine.get_revenue_breakdown(db, entity_id, year, month, group_by)


# ── Expense Breakdown ──────────────────────────────────────────────────────────

@router.get("/expense-breakdown", summary="Breakdown biaya per kategori akun")
def expense_breakdown(
    entity_id: UUID,
    year:  int = Query(default=date.today().year),
    month: int = Query(default=date.today().month, ge=1, le=12),
    db: Session = Depends(get_db),
):
    return DashboardEngine.get_expense_breakdown(db, entity_id, year, month)


# ── Inventory Dashboard (Dagang) ──────────────────────────────────────────────

@router.get("/inventory", summary="Inventory summary: nilai, turnover, low-stock alert (dagang)")
def inventory_dashboard(entity_id: UUID, db: Session = Depends(get_db)):
    return DashboardEngine.get_inventory_dashboard(db, entity_id)


# ── Budget vs Actual ──────────────────────────────────────────────────────────

@router.get("/budget-vs-actual", summary="Budget vs Actual: variance analisis per bulan")
def budget_vs_actual(
    entity_id:   UUID,
    fiscal_year: int  = Query(default=date.today().year),
    scenario_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
):
    return DashboardEngine.get_budget_vs_actual(db, entity_id, fiscal_year, scenario_id)


# ── Construction Dashboard ────────────────────────────────────────────────────

@router.get("/construction", summary="PoC%, CTC, billing gap, sinking cash alert per proyek")
def construction_dashboard(entity_id: UUID, db: Session = Depends(get_db)):
    from modules.forecast_engine import ForecastEngine
    return ForecastEngine.get_construction_dashboard(db, entity_id)
