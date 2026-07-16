"""
FP&A Forecast Router
Base prefix: /forecast
"""

from datetime import date
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.database import get_db
from modules.forecast_engine import ForecastEngine

router = APIRouter(prefix="/forecast", tags=["FP&A Forecast"])


# ── Pydantic Models ──────────────────────────────────────────────────────────

class CreateScenarioReq(BaseModel):
    entity_id:       UUID
    scenario_name:   str
    company_type:    str               = "jasa"   # jasa | dagang | konstruksi
    as_of_date:      date
    forecast_months: int               = 12
    scenario_type:   str               = "baseline"
    created_by:      str               = ""
    assumptions:     dict[str, float]  = Field(default_factory=dict)


class SetAssumptionsReq(BaseModel):
    assumptions: dict[str, float]


class WhatIfReq(BaseModel):
    entity_id:            UUID
    base_scenario_id:     UUID
    what_if_name:         str
    override_assumptions: dict[str, float]
    created_by:           str = ""


class RefreshActualsReq(BaseModel):
    entity_id:    UUID
    period_year:  int
    period_month: int


class DriverOverrideReq(BaseModel):
    period_year:     int
    period_month:    int
    statement_type:  str
    line_code:       str
    override_amount: float
    override_reason: Optional[str] = None
    overridden_by:   str = ""


# ── Scenario Endpoints ────────────────────────────────────────────────────────

@router.post("/scenarios", summary="Buat forecast scenario baru")
def create_scenario(req: CreateScenarioReq, db: Session = Depends(get_db)):
    try:
        result = ForecastEngine.create_scenario(
            db,
            entity_id       = req.entity_id,
            scenario_name   = req.scenario_name,
            company_type    = req.company_type,
            as_of_date      = req.as_of_date,
            forecast_months = req.forecast_months,
            scenario_type   = req.scenario_type,
            created_by      = req.created_by,
        )
        # Set assumptions if provided
        if req.assumptions:
            ForecastEngine.set_assumptions(db, UUID(result["scenario_id"]), req.assumptions)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/scenarios", summary="List semua scenario per entity")
def list_scenarios(
    entity_id:    UUID,
    company_type: Optional[str] = None,
    status:       Optional[str] = None,
    db: Session = Depends(get_db),
):
    from sqlalchemy import text
    filters = ["entity_id=:eid"]
    params: dict = {"eid": str(entity_id)}
    if company_type:
        filters.append("company_type=:ctype")
        params["ctype"] = company_type
    if status:
        filters.append("status=:status")
        params["status"] = status

    rows = db.execute(
        text(f"""
            SELECT id, scenario_name, scenario_type, company_type, as_of_date,
                   forecast_months, status, last_computed_at, created_at, created_by
            FROM forecast_scenario
            WHERE {' AND '.join(filters)}
            ORDER BY created_at DESC
        """),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/scenarios/{scenario_id}", summary="Detail scenario + assumptions")
def get_scenario(scenario_id: UUID, db: Session = Depends(get_db)):
    from sqlalchemy import text
    scen = db.execute(
        text("SELECT * FROM forecast_scenario WHERE id=:sid"),
        {"sid": str(scenario_id)},
    ).fetchone()
    if not scen:
        raise HTTPException(status_code=404, detail="Scenario tidak ditemukan.")

    assump = db.execute(
        text("SELECT param_key, param_value, param_unit, description FROM forecast_assumption WHERE scenario_id=:sid"),
        {"sid": str(scenario_id)},
    ).fetchall()

    return {
        **dict(scen._mapping),
        "assumptions": [dict(r._mapping) for r in assump],
    }


@router.put("/scenarios/{scenario_id}/assumptions", summary="Update assumptions scenario")
def set_assumptions(scenario_id: UUID, req: SetAssumptionsReq, db: Session = Depends(get_db)):
    try:
        return ForecastEngine.set_assumptions(db, scenario_id, req.assumptions)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Run Forecast ──────────────────────────────────────────────────────────────

@router.post("/scenarios/{scenario_id}/run", summary="Hitung forecast (run engine)")
def run_forecast(
    scenario_id: UUID,
    entity_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    try:
        return ForecastEngine.run_forecast(db, scenario_id, entity_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Forecast gagal: {str(e)}")


@router.post("/scenarios/{scenario_id}/create-and-run", summary="Buat scenario + langsung run")
def create_and_run(req: CreateScenarioReq, db: Session = Depends(get_db)):
    """Shortcut: create scenario → set assumptions → run forecast → return three-way output."""
    try:
        result = ForecastEngine.create_scenario(
            db,
            entity_id       = req.entity_id,
            scenario_name   = req.scenario_name,
            company_type    = req.company_type,
            as_of_date      = req.as_of_date,
            forecast_months = req.forecast_months,
            scenario_type   = req.scenario_type,
            created_by      = req.created_by,
        )
        sid = UUID(result["scenario_id"])
        ForecastEngine.set_assumptions(db, sid, req.assumptions)
        run_result = ForecastEngine.run_forecast(db, sid, req.entity_id)
        return {**result, **run_result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Three-Way Output ──────────────────────────────────────────────────────────

@router.get("/scenarios/{scenario_id}/three-way", summary="Output Three-Way Forecast (P&L + CF + BS)")
def three_way_forecast(scenario_id: UUID, db: Session = Depends(get_db)):
    try:
        return ForecastEngine.get_three_way_forecast(db, scenario_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/scenarios/{scenario_id}/pl", summary="P&L Forecast per periode")
def get_pl_forecast(
    scenario_id: UUID,
    period_year:  Optional[int] = None,
    period_month: Optional[int] = None,
    db: Session = Depends(get_db),
):
    from sqlalchemy import text
    filters = ["scenario_id=:sid", "statement_type='pl'"]
    params: dict = {"sid": str(scenario_id)}
    if period_year:
        filters.append("period_year=:yr"); params["yr"] = period_year
    if period_month:
        filters.append("period_month=:mo"); params["mo"] = period_month

    rows = db.execute(
        text(f"""
            SELECT period_year, period_month, line_code, line_name, sort_order,
                   line_category, amount, amount_actual, is_actual, driver_source
            FROM forecast_line WHERE {' AND '.join(filters)}
            ORDER BY period_year, period_month, sort_order
        """),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/scenarios/{scenario_id}/cashflow", summary="Cash Flow Forecast per periode")
def get_cf_forecast(
    scenario_id: UUID,
    period_year:  Optional[int] = None,
    db: Session = Depends(get_db),
):
    from sqlalchemy import text
    filters = ["scenario_id=:sid", "statement_type='cf'"]
    params: dict = {"sid": str(scenario_id)}
    if period_year:
        filters.append("period_year=:yr"); params["yr"] = period_year

    rows = db.execute(
        text(f"""
            SELECT period_year, period_month, line_code, line_name, sort_order,
                   line_category, amount, amount_actual, is_actual, driver_source
            FROM forecast_line WHERE {' AND '.join(filters)}
            ORDER BY period_year, period_month, sort_order
        """),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/scenarios/{scenario_id}/balance-sheet", summary="Balance Sheet Forecast per periode")
def get_bs_forecast(
    scenario_id: UUID,
    period_year:  Optional[int] = None,
    db: Session = Depends(get_db),
):
    from sqlalchemy import text
    filters = ["scenario_id=:sid", "statement_type='bs'"]
    params: dict = {"sid": str(scenario_id)}
    if period_year:
        filters.append("period_year=:yr"); params["yr"] = period_year

    rows = db.execute(
        text(f"""
            SELECT period_year, period_month, line_code, line_name, sort_order,
                   line_category, amount, amount_actual, is_actual, driver_source
            FROM forecast_line WHERE {' AND '.join(filters)}
            ORDER BY period_year, period_month, sort_order
        """),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/scenarios/{scenario_id}/summary", summary="Ringkasan per periode (revenue/GP/EBITDA/NI/cash)")
def get_forecast_summary(scenario_id: UUID, db: Session = Depends(get_db)):
    from sqlalchemy import text
    rows = db.execute(
        text("""
            SELECT period_year, period_month, revenue, cogs, gross_profit,
                   ebitda, net_income, cash_closing
            FROM vw_forecast_summary
            WHERE scenario_id=:sid
            ORDER BY period_year, period_month
        """),
        {"sid": str(scenario_id)},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Variance Report ───────────────────────────────────────────────────────────

@router.get("/scenarios/{scenario_id}/variance", summary="Variance: actual vs forecast")
def variance_report(
    scenario_id:   UUID,
    statement_type: str = Query("pl"),
    period_year:   Optional[int] = None,
    period_month:  Optional[int] = None,
    db: Session = Depends(get_db),
):
    return ForecastEngine.get_variance_report(
        db, scenario_id, statement_type, period_year, period_month
    )


# ── Refresh Actuals ───────────────────────────────────────────────────────────

@router.post("/scenarios/{scenario_id}/refresh-actuals", summary="Stamp actual GL ke forecast_line")
def refresh_actuals(scenario_id: UUID, req: RefreshActualsReq, db: Session = Depends(get_db)):
    try:
        return ForecastEngine.refresh_actuals(
            db, scenario_id, req.entity_id, req.period_year, req.period_month
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Driver Override ───────────────────────────────────────────────────────────

@router.post("/scenarios/{scenario_id}/overrides", summary="Override satu baris forecast")
def add_override(scenario_id: UUID, req: DriverOverrideReq, db: Session = Depends(get_db)):
    from sqlalchemy import text
    db.execute(
        text("""
            INSERT INTO forecast_driver_override
                (scenario_id, period_year, period_month, statement_type,
                 line_code, override_amount, override_reason, overridden_by)
            VALUES (:sid, :yr, :mo, :stype, :lc, :amt, :reason, :by)
            ON CONFLICT (scenario_id, period_year, period_month, statement_type, line_code)
            DO UPDATE SET override_amount=EXCLUDED.override_amount,
                          override_reason=EXCLUDED.override_reason,
                          overridden_by=EXCLUDED.overridden_by,
                          overridden_at=NOW()
        """),
        {
            "sid":    str(scenario_id),
            "yr":     req.period_year,
            "mo":     req.period_month,
            "stype":  req.statement_type,
            "lc":     req.line_code,
            "amt":    req.override_amount,
            "reason": req.override_reason,
            "by":     req.overridden_by,
        },
    )
    # Apply override to forecast_line
    db.execute(
        text("""
            UPDATE forecast_line
            SET amount=:amt
            WHERE scenario_id=:sid AND period_year=:yr AND period_month=:mo
              AND statement_type=:stype AND line_code=:lc
        """),
        {
            "sid":  str(scenario_id), "yr": req.period_year, "mo": req.period_month,
            "stype": req.statement_type, "lc": req.line_code, "amt": req.override_amount,
        },
    )
    db.commit()
    return {"status": "overridden", "line_code": req.line_code, "new_amount": req.override_amount}


# ── What-If Analysis ──────────────────────────────────────────────────────────

@router.post("/what-if", summary="What-If: clone scenario + override assumptions + re-run")
def run_what_if(req: WhatIfReq, db: Session = Depends(get_db)):
    try:
        return ForecastEngine.run_what_if(
            db,
            base_scenario_id      = req.base_scenario_id,
            entity_id             = req.entity_id,
            what_if_name          = req.what_if_name,
            override_assumptions  = req.override_assumptions,
            created_by            = req.created_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/what-if/comparison", summary="Compare base vs what-if scenario")
def what_if_comparison(
    base_scenario_id:    UUID,
    what_if_scenario_id: UUID,
    statement_type: str = Query("pl"),
    db: Session = Depends(get_db),
):
    from sqlalchemy import text
    rows = db.execute(
        text("""
            SELECT entity_id, period_year, period_month, statement_type,
                   line_code, line_name, base_amount, what_if_amount, delta, delta_pct
            FROM vw_what_if_comparison
            WHERE base_scenario_id=:base AND what_if_scenario_id=:wi
              AND statement_type=:stype
            ORDER BY period_year, period_month, line_code
        """),
        {"base": str(base_scenario_id), "wi": str(what_if_scenario_id), "stype": statement_type},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Construction-Specific ─────────────────────────────────────────────────────

@router.get("/construction/project-dashboard", summary="PoC%, CTC, Under/Over-billing per proyek")
def construction_dashboard(entity_id: UUID, db: Session = Depends(get_db)):
    return ForecastEngine.get_construction_dashboard(db, entity_id)


# ── Trading-Specific: Procurement Forecast ───────────────────────────────────

@router.get("/trading/procurement-forecast", summary="MRP: kebutuhan pembelian per produk")
def procurement_forecast(
    entity_id:    UUID,
    scenario_id:  UUID,
    warehouse_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
):
    try:
        return ForecastEngine.get_procurement_forecast(
            db, entity_id, scenario_id, warehouse_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Rolling Forecast ──────────────────────────────────────────────────────────

@router.post("/rolling/advance-month", summary="Rolling forecast: lock bulan lalu + tambah bulan baru")
def advance_rolling_forecast(
    scenario_id:  UUID,
    entity_id:    UUID,
    close_year:   int,
    close_month:  int,
    db: Session = Depends(get_db),
):
    """
    1. Refresh actuals untuk bulan yang baru tutup
    2. Re-run forecast (horizon tetap 12 bulan, bergeser satu bulan ke depan)
    """
    from sqlalchemy import text
    from datetime import date

    try:
        # Stamp actuals untuk bulan ditutup
        ForecastEngine.refresh_actuals(db, scenario_id, entity_id, close_year, close_month)

        # Advance as_of_date satu bulan
        new_month = close_month + 1 if close_month < 12 else 1
        new_year  = close_year if close_month < 12 else close_year + 1
        new_as_of = date(new_year, new_month, 1)

        db.execute(
            text("UPDATE forecast_scenario SET as_of_date=:dt WHERE id=:sid"),
            {"dt": str(new_as_of), "sid": str(scenario_id)},
        )
        db.commit()

        return ForecastEngine.run_forecast(db, scenario_id, entity_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
