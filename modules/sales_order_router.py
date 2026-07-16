"""
Sales Order Router
Base prefix: /sales
"""

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from modules.sales_order_engine import SalesOrderEngine

router = APIRouter(prefix="/sales", tags=["Sales Order"])


# ── Pydantic Models ──────────────────────────────────────────────────────────

class QuotationLineIn(BaseModel):
    product_id:   UUID
    description:  Optional[str]     = None
    qty:          Decimal
    uom_id:       UUID
    unit_price:   Decimal
    discount_pct: Decimal            = Decimal("0")
    tax_rate:     Decimal            = Decimal("11")
    notes:        Optional[str]      = None


class CreateQuotationReq(BaseModel):
    entity_id:      UUID
    customer_id:    UUID
    quotation_date: date
    valid_until:    date
    lines:          list[QuotationLineIn]
    salesperson:    Optional[str]    = None
    currency:       str              = "IDR"
    exchange_rate:  Decimal          = Decimal("1")
    notes:          Optional[str]    = None
    created_by:     Optional[str]    = None


class ConfirmQuotationReq(BaseModel):
    confirmed_by:            str
    requested_delivery_date: Optional[date] = None
    warehouse_id:            Optional[UUID] = None


class SOLineIn(BaseModel):
    product_id:   UUID
    description:  Optional[str]  = None
    qty_ordered:  Decimal
    uom_id:       UUID
    unit_price:   Decimal
    discount_pct: Decimal         = Decimal("0")
    tax_rate:     Decimal         = Decimal("11")
    lot_id:       Optional[UUID] = None
    notes:        Optional[str]  = None


class CreateSODirectReq(BaseModel):
    entity_id:               UUID
    customer_id:             UUID
    so_date:                 date
    lines:                   list[SOLineIn]
    warehouse_id:            Optional[UUID] = None
    requested_delivery_date: Optional[date] = None
    salesperson:             Optional[str]  = None
    currency:                str            = "IDR"
    exchange_rate:           Decimal        = Decimal("1")
    notes:                   Optional[str]  = None
    created_by:              Optional[str]  = None


class ConfirmSOReq(BaseModel):
    confirmed_by: str


class ValidatePickingReq(BaseModel):
    picked_by:           str
    qty_picked_overrides: Optional[dict[str, float]] = None  # {picking_line_id: qty}


class CreateInvoiceReq(BaseModel):
    invoice_date: date
    created_by:   str
    invoice_type: str = Field(default="delivered", pattern="^(ordered|delivered)$")


class CheckAvailabilityReq(BaseModel):
    entity_id:    UUID
    lines:        list[dict]     # [{product_id, qty}]
    warehouse_id: Optional[UUID] = None


class CreateCustomerReq(BaseModel):
    entity_id:        UUID
    customer_code:    str
    customer_name:    str
    customer_type:    str     = "company"
    npwp:             Optional[str]     = None
    address:          Optional[str]     = None
    city:             Optional[str]     = None
    province:         Optional[str]     = None
    phone:            Optional[str]     = None
    email:            Optional[str]     = None
    contact_person:   Optional[str]     = None
    credit_limit:     Decimal           = Decimal("0")
    payment_term_days: int              = 30
    is_pkp:           bool              = False


# ── Customer ─────────────────────────────────────────────────────────────────

@router.post("/customers", summary="Tambah customer baru")
def create_customer(req: CreateCustomerReq, db: Session = Depends(get_db)):
    try:
        row = db.execute(
            text("""
                INSERT INTO customer (
                    entity_id, customer_code, customer_name, customer_type,
                    npwp, address, city, province, phone, email,
                    contact_person, credit_limit, payment_term_days, is_pkp
                ) VALUES (
                    :eid, :code, :name, :type,
                    :npwp, :addr, :city, :prov, :phone, :email,
                    :cp, :cl, :top, :pkp
                ) RETURNING id
            """),
            {
                "eid":  str(req.entity_id), "code": req.customer_code, "name": req.customer_name,
                "type": req.customer_type,  "npwp": req.npwp,
                "addr": req.address,        "city": req.city,          "prov": req.province,
                "phone": req.phone,         "email": req.email,        "cp":   req.contact_person,
                "cl":   float(req.credit_limit), "top": req.payment_term_days, "pkp": req.is_pkp,
            },
        ).fetchone()
        db.commit()
        return {"customer_id": str(row.id)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/customers", summary="List customer")
def list_customers(
    entity_id:   UUID,
    search:      Optional[str]  = None,
    is_active:   bool           = True,
    page:        int = Query(default=1, ge=1),
    size:        int = Query(default=30, ge=1, le=200),
    db: Session = Depends(get_db),
):
    filters = ["entity_id=:eid", "is_active=:active"]
    params: dict = {"eid": str(entity_id), "active": is_active,
                    "offset": (page - 1) * size, "limit": size}
    if search:
        filters.append("(customer_name ILIKE :q OR customer_code ILIKE :q)")
        params["q"] = f"%{search}%"

    where = " AND ".join(filters)
    rows = db.execute(
        text(f"""
            SELECT id, customer_code, customer_name, customer_type,
                   npwp, city, phone, email, credit_limit, payment_term_days, is_pkp
            FROM customer
            WHERE {where}
            ORDER BY customer_name
            OFFSET :offset LIMIT :limit
        """),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Quotation ─────────────────────────────────────────────────────────────────

@router.post("/quotations", summary="Buat quotation baru")
def create_quotation(req: CreateQuotationReq, db: Session = Depends(get_db)):
    try:
        return SalesOrderEngine.create_quotation(
            db,
            entity_id      = req.entity_id,
            customer_id    = req.customer_id,
            quotation_date = req.quotation_date,
            valid_until    = req.valid_until,
            lines          = [l.model_dump() for l in req.lines],
            salesperson    = req.salesperson,
            currency       = req.currency,
            exchange_rate  = req.exchange_rate,
            notes          = req.notes,
            created_by     = req.created_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/quotations", summary="List quotation")
def list_quotations(
    entity_id:   UUID,
    customer_id: Optional[UUID] = None,
    status:      Optional[str]  = None,
    date_from:   Optional[date] = None,
    date_to:     Optional[date] = None,
    page:        int = Query(default=1, ge=1),
    size:        int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    filters = ["q.entity_id=:eid"]
    params: dict = {"eid": str(entity_id), "offset": (page - 1) * size, "limit": size}
    if customer_id:
        filters.append("q.customer_id=:cust")
        params["cust"] = str(customer_id)
    if status:
        filters.append("q.status=:status")
        params["status"] = status
    if date_from:
        filters.append("q.quotation_date>=:d_from")
        params["d_from"] = date_from
    if date_to:
        filters.append("q.quotation_date<=:d_to")
        params["d_to"] = date_to

    rows = db.execute(
        text(f"""
            SELECT q.id, q.quotation_no, c.customer_name,
                   q.quotation_date, q.valid_until, q.total_amount, q.status, q.salesperson
            FROM quotation q JOIN customer c ON c.id=q.customer_id
            WHERE {" AND ".join(filters)}
            ORDER BY q.quotation_date DESC
            OFFSET :offset LIMIT :limit
        """),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/quotations/{quotation_id}/confirm", summary="Konfirmasi quotation → SO")
def confirm_quotation(quotation_id: UUID, req: ConfirmQuotationReq, db: Session = Depends(get_db)):
    try:
        return SalesOrderEngine.confirm_quotation(
            db, quotation_id,
            confirmed_by             = req.confirmed_by,
            requested_delivery_date  = req.requested_delivery_date,
            warehouse_id             = req.warehouse_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Sales Orders ─────────────────────────────────────────────────────────────

@router.post("/orders", summary="Buat SO langsung (tanpa quotation)")
def create_so(req: CreateSODirectReq, db: Session = Depends(get_db)):
    try:
        return SalesOrderEngine.create_so_direct(
            db,
            entity_id               = req.entity_id,
            customer_id             = req.customer_id,
            so_date                 = req.so_date,
            lines                   = [l.model_dump() for l in req.lines],
            warehouse_id            = req.warehouse_id,
            requested_delivery_date = req.requested_delivery_date,
            salesperson             = req.salesperson,
            currency                = req.currency,
            exchange_rate           = req.exchange_rate,
            notes                   = req.notes,
            created_by              = req.created_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/orders", summary="List Sales Order")
def list_so(
    entity_id:   UUID,
    customer_id: Optional[UUID] = None,
    status:      Optional[str]  = None,
    date_from:   Optional[date] = None,
    date_to:     Optional[date] = None,
    page:        int = Query(default=1, ge=1),
    size:        int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        text("""
            SELECT * FROM vw_so_fulfillment
            WHERE entity_id=:eid
              AND (:cust IS NULL OR customer_name ILIKE '%' || :cust || '%')
              AND (:status IS NULL OR status=:status)
              AND (:d_from IS NULL OR so_date>=:d_from)
              AND (:d_to   IS NULL OR so_date<=:d_to)
            ORDER BY so_date DESC
            OFFSET :offset LIMIT :limit
        """),
        {
            "eid":    str(entity_id),
            "cust":   None,
            "status": status,
            "d_from": date_from,
            "d_to":   date_to,
            "offset": (page - 1) * size,
            "limit":  size,
        },
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/orders/{so_id}", summary="Detail Sales Order")
def get_so(so_id: UUID, db: Session = Depends(get_db)):
    try:
        return SalesOrderEngine.get_so_detail(db, so_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/orders/{so_id}/confirm", summary="Konfirmasi SO + buat picking order")
def confirm_so(so_id: UUID, req: ConfirmSOReq, db: Session = Depends(get_db)):
    try:
        return SalesOrderEngine.confirm_so(db, so_id, req.confirmed_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/orders/{so_id}/invoice", summary="Buat AR Invoice dari SO")
def create_invoice(so_id: UUID, req: CreateInvoiceReq, db: Session = Depends(get_db)):
    try:
        return SalesOrderEngine.create_invoice_from_so(
            db, so_id,
            invoice_date = req.invoice_date,
            created_by   = req.created_by,
            invoice_type = req.invoice_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Picking ───────────────────────────────────────────────────────────────────

@router.post("/pickings/{picking_id}/validate", summary="Selesaikan picking → buat DO")
def validate_picking(picking_id: UUID, req: ValidatePickingReq, db: Session = Depends(get_db)):
    try:
        return SalesOrderEngine.validate_picking(
            db, picking_id,
            picked_by            = req.picked_by,
            qty_picked_overrides = req.qty_picked_overrides,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/pickings", summary="List picking orders")
def list_pickings(
    entity_id: UUID,
    status:    Optional[str] = None,
    db: Session = Depends(get_db),
):
    params: dict = {"eid": str(entity_id)}
    extra = ""
    if status:
        extra = "AND po.status=:status"
        params["status"] = status

    rows = db.execute(
        text(f"""
            SELECT po.id, po.picking_no, po.so_id, so.so_no,
                   po.picking_date, po.status, po.picked_by
            FROM picking_order po
            JOIN sales_order so ON so.id=po.so_id
            WHERE po.entity_id=:eid {extra}
            ORDER BY po.picking_date DESC
        """),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Availability Check & Pipeline ────────────────────────────────────────────

@router.post("/check-availability", summary="Cek ketersediaan stok sebelum SO")
def check_availability(req: CheckAvailabilityReq, db: Session = Depends(get_db)):
    return SalesOrderEngine.check_availability(db, req.entity_id, req.lines, req.warehouse_id)


@router.get("/pipeline", summary="Sales pipeline (quotation + SO)")
def sales_pipeline(
    entity_id:   UUID,
    salesperson: Optional[str]  = None,
    date_from:   Optional[date] = None,
    db: Session = Depends(get_db),
):
    params: dict = {"eid": str(entity_id)}
    extra = "WHERE entity_id=:eid"
    if salesperson:
        extra += " AND salesperson ILIKE :sales"
        params["sales"] = f"%{salesperson}%"
    if date_from:
        extra += " AND doc_date>=:d_from"
        params["d_from"] = date_from

    rows = db.execute(
        text(f"SELECT * FROM vw_sales_pipeline {extra} ORDER BY doc_date DESC"),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/reports/product-availability", summary="Ketersediaan stok per produk")
def product_availability(entity_id: UUID, db: Session = Depends(get_db)):
    rows = db.execute(
        text("SELECT * FROM vw_so_product_availability ORDER BY product_name"),
        {},
    ).fetchall()
    return [dict(r._mapping) for r in rows]
