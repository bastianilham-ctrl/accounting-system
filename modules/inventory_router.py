"""
Inventory Router — REST API untuk modul persediaan.
Prefix: /inventory
"""

from __future__ import annotations

from datetime import date
from typing import Any, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from modules.auth import get_current_user
from modules.inventory_engine import InventoryEngine

router = APIRouter(prefix="/inventory", tags=["inventory"])


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Schemas
# ─────────────────────────────────────────────────────────────────────────────

class ProductCategoryCreate(BaseModel):
    entity_id: str
    category_code: str
    category_name: str
    parent_id: Optional[str] = None
    cost_method: str = "average_cost"
    inventory_account_code: Optional[str] = None
    cogs_account_code: Optional[str] = None
    grir_account_code: Optional[str] = None
    scrapped_account_code: Optional[str] = None
    adjustment_account_code: Optional[str] = None
    wip_account_code: Optional[str] = None

    @validator("cost_method")
    def valid_method(cls, v):
        if v not in ("average_cost", "fifo", "standard_cost"):
            raise ValueError("cost_method harus: average_cost | fifo | standard_cost")
        return v


class ProductCreate(BaseModel):
    entity_id: str
    category_id: str
    sku: str
    barcode: Optional[str] = None
    product_name: str
    description: Optional[str] = None
    product_type: str = "storable"
    tracking_type: str = "none"
    uom_id: str
    uom_purchase_id: Optional[str] = None
    uom_sales_id: Optional[str] = None
    standard_price: float = 0.0
    sales_price: float = 0.0
    min_qty: float = 0.0
    max_qty: float = 0.0
    reorder_qty: float = 0.0
    notes: Optional[str] = None
    is_sellable: bool = True

    @validator("product_type")
    def valid_type(cls, v):
        if v not in ("storable", "consumable", "service"):
            raise ValueError("product_type harus: storable | consumable | service")
        return v

    @validator("tracking_type")
    def valid_tracking(cls, v):
        if v not in ("none", "lot", "serial"):
            raise ValueError("tracking_type harus: none | lot | serial")
        return v


class LocationCreate(BaseModel):
    entity_id: str
    location_code: str
    location_name: str
    location_type: str = "internal"
    parent_id: Optional[str] = None

    @validator("location_type")
    def valid_loc_type(cls, v):
        valid = ("internal", "supplier", "customer", "scrapped", "production", "transit", "virtual")
        if v not in valid:
            raise ValueError(f"location_type harus: {' | '.join(valid)}")
        return v


class LotCreate(BaseModel):
    product_id: str
    entity_id: str
    lot_number: str
    manufacture_date: Optional[date] = None
    expiry_date: Optional[date] = None
    initial_qty: float = 0.0
    notes: Optional[str] = None


class GoodsReceiptRequest(BaseModel):
    entity_id: str
    po_receipt_id: Optional[str] = None
    product_id: str
    destination_location_id: str
    qty: float = Field(..., gt=0)
    unit_cost: float = Field(..., ge=0)
    lot_id: Optional[str] = None
    reference_no: Optional[str] = None


class DOLineItem(BaseModel):
    product_id: str
    lot_id: Optional[str] = None
    qty: float = Field(..., gt=0)
    uom_id: Optional[str] = None
    unit_price: float = 0.0


class DeliveryOrderCreate(BaseModel):
    entity_id: str
    do_date: date
    customer_name: str
    customer_id: Optional[str] = None
    ar_invoice_id: Optional[str] = None
    source_location_id: str
    so_reference: Optional[str] = None
    delivery_address: Optional[str] = None
    lines: List[DOLineItem]


class TransferRequest(BaseModel):
    entity_id: str
    product_id: str
    source_location_id: str
    destination_location_id: str
    qty: float = Field(..., gt=0)
    lot_id: Optional[str] = None
    reference_no: Optional[str] = None


class ScrapRequest(BaseModel):
    entity_id: str
    product_id: str
    source_location_id: str
    qty: float = Field(..., gt=0)
    reason: Optional[str] = None
    lot_id: Optional[str] = None


class AdjustmentCreateRequest(BaseModel):
    entity_id: str
    location_id: str
    adjustment_date: date
    product_ids: Optional[List[str]] = None


class AdjustmentLineUpdate(BaseModel):
    line_id: str
    actual_qty: float = Field(..., ge=0)


class AdjustmentCountSubmit(BaseModel):
    lines: List[AdjustmentLineUpdate]


class ReorderRuleCreate(BaseModel):
    entity_id: str
    product_id: str
    location_id: str
    route: str = "buy"
    min_qty: float = 0.0
    max_qty: float = 0.0
    qty_multiple: float = 1.0
    vendor_id: Optional[str] = None
    lead_time_days: int = 0

    @validator("route")
    def valid_route(cls, v):
        if v not in ("buy", "manufacture"):
            raise ValueError("route harus: buy | manufacture")
        return v


class LocationInitRequest(BaseModel):
    entity_id: str


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _require_role(user: dict, *roles: str) -> None:
    # superadmin selalu boleh, konsisten dengan modules/auth.py:require_role()
    if user.get("role") not in roles and user.get("role") != "superadmin":
        raise HTTPException(status_code=403, detail=f"Butuh role: {', '.join(roles)}")


def _gen_do_no_router(db: Session) -> str:
    today = date.today()
    year, month = today.year, today.month
    row = db.execute(
        text("SELECT COUNT(*) AS cnt FROM delivery_order WHERE do_no LIKE :p"),
        {"p": f"DO/{year}/{month:02d}/%"},
    ).fetchone()
    seq = (row.cnt if row else 0) + 1
    return f"DO/{year}/{month:02d}/{seq:04d}"


# ─────────────────────────────────────────────────────────────────────────────
# Setup — Initial Locations
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/setup/locations", summary="Inisialisasi virtual locations wajib per entity")
def init_virtual_locations(
    req: LocationInitRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Buat virtual locations yang wajib ada sebelum transaksi inventory:
    supplier, customer, scrapped, production, virtual (adjustment).
    """
    _require_role(current_user, "admin")
    required = [
        ("VIRT-SUPPLIER",   "Virtual: Supplier",    "supplier"),
        ("VIRT-CUSTOMER",   "Virtual: Customer",    "customer"),
        ("VIRT-SCRAPPED",   "Virtual: Scrapped",    "scrapped"),
        ("VIRT-PRODUCTION", "Virtual: Production",  "production"),
        ("VIRT-VIRTUAL",    "Virtual: Penyesuaian", "virtual"),
    ]
    created = []
    for code, name, loc_type in required:
        existing = db.execute(
            text("SELECT id FROM inventory_location WHERE entity_id = :eid AND location_code = :c"),
            {"eid": req.entity_id, "c": code},
        ).fetchone()
        if not existing:
            db.execute(
                text(
                    "INSERT INTO inventory_location "
                    "(id, entity_id, location_code, location_name, location_type) "
                    "VALUES (:id, :eid, :c, :n, :lt)"
                ),
                {"id": str(uuid4()), "eid": req.entity_id, "c": code, "n": name, "lt": loc_type},
            )
            created.append(code)
    db.commit()
    return {"entity_id": req.entity_id, "created": created}


# ─────────────────────────────────────────────────────────────────────────────
# Product Category
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/categories", status_code=201, summary="Buat kategori produk")
def create_category(
    req: ProductCategoryCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "admin")
    existing = db.execute(
        text(
            "SELECT id FROM product_category "
            "WHERE entity_id = :eid AND category_code = :c"
        ),
        {"eid": req.entity_id, "c": req.category_code},
    ).fetchone()
    if existing:
        raise HTTPException(400, f"category_code '{req.category_code}' sudah ada")

    cat_id = str(uuid4())
    db.execute(
        text(
            "INSERT INTO product_category "
            "(id, entity_id, category_code, category_name, parent_id, cost_method, "
            " inventory_account_code, cogs_account_code, grir_account_code, "
            " scrapped_account_code, adjustment_account_code, wip_account_code, "
            " created_by) "
            "VALUES (:id, :eid, :c, :n, :pid, :cm, :ia, :ca, :ga, :sa, :aa, :wa, :cby)"
        ),
        {
            "id": cat_id, "eid": req.entity_id, "c": req.category_code,
            "n": req.category_name, "pid": req.parent_id, "cm": req.cost_method,
            "ia": req.inventory_account_code, "ca": req.cogs_account_code,
            "ga": req.grir_account_code, "sa": req.scrapped_account_code,
            "aa": req.adjustment_account_code, "wa": req.wip_account_code,
            "cby": current_user["username"],
        },
    )
    db.commit()
    return {"category_id": cat_id, "category_code": req.category_code}


@router.get("/categories", summary="Daftar kategori produk")
def list_categories(
    entity_id: str = Query(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    rows = db.execute(
        text("SELECT * FROM product_category WHERE entity_id = :eid ORDER BY category_code"),
        {"eid": entity_id},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Product
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/products", status_code=201, summary="Buat produk baru")
def create_product(
    req: ProductCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "admin")
    existing = db.execute(
        text("SELECT id FROM product_product WHERE entity_id = :eid AND sku = :sku"),
        {"eid": req.entity_id, "sku": req.sku},
    ).fetchone()
    if existing:
        raise HTTPException(400, f"SKU '{req.sku}' sudah ada")

    prod_id = str(uuid4())
    db.execute(
        text(
            "INSERT INTO product_product "
            "(id, entity_id, category_id, sku, barcode, product_name, description, "
            " product_type, tracking_type, uom_id, uom_purchase_id, uom_sales_id, "
            " standard_price, current_avg_cost, sales_price, "
            " min_qty, max_qty, reorder_qty, notes, is_sellable, created_by) "
            "VALUES (:id, :eid, :cid, :sku, :bc, :pn, :desc, "
            "        :pt, :tt, :uid, :upid, :usid, "
            "        :sp, :sp, :slp, "
            "        :minq, :maxq, :rqty, :notes, :sellable, :cby)"
        ),
        {
            "id": prod_id, "eid": req.entity_id, "cid": req.category_id,
            "sku": req.sku, "bc": req.barcode, "pn": req.product_name, "desc": req.description,
            "pt": req.product_type, "tt": req.tracking_type,
            "uid": req.uom_id, "upid": req.uom_purchase_id, "usid": req.uom_sales_id,
            "sp": req.standard_price, "slp": req.sales_price,
            "minq": req.min_qty, "maxq": req.max_qty, "rqty": req.reorder_qty,
            "notes": req.notes, "sellable": req.is_sellable, "cby": current_user["username"],
        },
    )
    db.commit()
    return {"product_id": prod_id, "sku": req.sku}


@router.get("/products", summary="Daftar produk")
def list_products(
    entity_id: str = Query(...),
    category_id: Optional[str] = Query(None),
    product_type: Optional[str] = Query(None),
    is_sellable: Optional[bool] = Query(None, description="Filter is_sellable=TRUE — dipakai picker item Sales Order/Invoice (REQ-03)"),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    filters = ["p.entity_id = :eid", "p.is_active = TRUE"]
    params: dict[str, Any] = {"eid": entity_id}

    if category_id:
        filters.append("p.category_id = :cid")
        params["cid"] = category_id
    if product_type:
        filters.append("p.product_type = :pt")
        params["pt"] = product_type
    if is_sellable is not None:
        filters.append("p.is_sellable = :sellable")
        params["sellable"] = is_sellable
    if search:
        filters.append("(p.sku ILIKE :s OR p.product_name ILIKE :s OR p.barcode ILIKE :s)")
        params["s"] = f"%{search}%"

    where = " AND ".join(filters)
    total = db.execute(
        text(f"SELECT COUNT(*) FROM product_product p WHERE {where}"), params
    ).scalar()

    params["offset"] = (page - 1) * size
    params["limit"]  = size
    rows = db.execute(
        text(
            f"SELECT p.*, pc.category_name, pc.cost_method, u.uom_code "
            f"FROM product_product p "
            f"JOIN product_category pc ON pc.id = p.category_id "
            f"JOIN product_uom u ON u.id = p.uom_id "
            f"WHERE {where} "
            f"ORDER BY p.sku "
            f"LIMIT :limit OFFSET :offset"
        ),
        params,
    ).fetchall()

    return {
        "total": total, "page": page, "size": size,
        "items": [dict(r._mapping) for r in rows],
    }


@router.get("/products/{product_id}", summary="Detail produk")
def get_product(
    product_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    row = db.execute(
        text(
            "SELECT p.*, pc.category_name, pc.cost_method, "
            "       pc.inventory_account_code, pc.cogs_account_code, "
            "       pc.grir_account_code, u.uom_code "
            "FROM product_product p "
            "JOIN product_category pc ON pc.id = p.category_id "
            "JOIN product_uom u ON u.id = p.uom_id "
            "WHERE p.id = :pid"
        ),
        {"pid": product_id},
    ).fetchone()
    if not row:
        raise HTTPException(404, "Produk tidak ditemukan")
    return dict(row._mapping)


# ─────────────────────────────────────────────────────────────────────────────
# Location
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/locations", status_code=201, summary="Buat lokasi gudang")
def create_location(
    req: LocationCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "admin")
    existing = db.execute(
        text(
            "SELECT id FROM inventory_location "
            "WHERE entity_id = :eid AND location_code = :c"
        ),
        {"eid": req.entity_id, "c": req.location_code},
    ).fetchone()
    if existing:
        raise HTTPException(400, f"location_code '{req.location_code}' sudah ada")

    loc_id = str(uuid4())
    db.execute(
        text(
            "INSERT INTO inventory_location "
            "(id, entity_id, location_code, location_name, location_type, parent_id) "
            "VALUES (:id, :eid, :c, :n, :lt, :pid)"
        ),
        {
            "id": loc_id, "eid": req.entity_id, "c": req.location_code,
            "n": req.location_name, "lt": req.location_type, "pid": req.parent_id,
        },
    )
    db.commit()
    return {"location_id": loc_id, "location_code": req.location_code}


@router.get("/locations", summary="Daftar lokasi")
def list_locations(
    entity_id: str = Query(...),
    location_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    query = "SELECT * FROM inventory_location WHERE entity_id = :eid AND is_active = TRUE"
    params: dict[str, Any] = {"eid": entity_id}
    if location_type:
        query += " AND location_type = :lt"
        params["lt"] = location_type
    rows = db.execute(text(query + " ORDER BY location_code"), params).fetchall()
    return [dict(r._mapping) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Lot / Serial Number
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/lots", status_code=201, summary="Daftarkan lot/batch baru")
def create_lot(
    req: LotCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    lot_id = str(uuid4())
    db.execute(
        text(
            "INSERT INTO stock_lot "
            "(id, product_id, entity_id, lot_number, manufacture_date, "
            " expiry_date, initial_qty, notes, created_by) "
            "VALUES (:id, :pid, :eid, :ln, :mfg, :exp, :iqty, :notes, :cby)"
        ),
        {
            "id": lot_id, "pid": req.product_id, "eid": req.entity_id,
            "ln": req.lot_number, "mfg": req.manufacture_date, "exp": req.expiry_date,
            "iqty": req.initial_qty, "notes": req.notes, "cby": current_user["username"],
        },
    )
    db.commit()
    return {"lot_id": lot_id, "lot_number": req.lot_number}


@router.get("/lots", summary="Daftar lot produk")
def list_lots(
    product_id: str = Query(...),
    entity_id: str = Query(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    rows = db.execute(
        text(
            "SELECT * FROM stock_lot "
            "WHERE product_id = :pid AND entity_id = :eid "
            "ORDER BY expiry_date ASC NULLS LAST, lot_number ASC"
        ),
        {"pid": product_id, "eid": entity_id},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Goods Receipt
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/goods-receipts", status_code=201, summary="Penerimaan barang dari vendor")
def goods_receipt(
    req: GoodsReceiptRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Proses penerimaan barang (GR). Role: finance atau admin.
    GL otomatis: Dr. Persediaan | Cr. GR/IR Clearing.
    """
    _require_role(current_user, "finance", "admin")
    try:
        result = InventoryEngine.receive_goods(
            db=db,
            entity_id=req.entity_id,
            po_receipt_id=req.po_receipt_id,
            product_id=req.product_id,
            destination_location_id=req.destination_location_id,
            qty=req.qty,
            unit_cost=req.unit_cost,
            lot_id=req.lot_id,
            reference_no=req.reference_no,
            created_by=current_user["username"],
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Delivery Order
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/delivery-orders", status_code=201, summary="Buat Delivery Order")
def create_do(
    req: DeliveryOrderCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    do_no = _gen_do_no_router(db)
    do_id = str(uuid4())
    db.execute(
        text(
            "INSERT INTO delivery_order "
            "(id, entity_id, do_no, do_date, customer_name, customer_id, ar_invoice_id, "
            " source_location_id, so_reference, delivery_address, status, created_by) "
            "VALUES (:id, :eid, :dno, :dt, :cn, :cid, :arid, "
            "        :sloc, :soref, :daddr, 'draft', :cby)"
        ),
        {
            "id": do_id, "eid": req.entity_id, "dno": do_no, "dt": req.do_date,
            "cn": req.customer_name, "cid": req.customer_id, "arid": req.ar_invoice_id,
            "sloc": req.source_location_id, "soref": req.so_reference,
            "daddr": req.delivery_address, "cby": current_user["username"],
        },
    )
    for i, line in enumerate(req.lines, start=1):
        db.execute(
            text(
                "INSERT INTO delivery_order_line "
                "(id, do_id, line_no, product_id, lot_id, qty, uom_id, unit_price) "
                "VALUES (:id, :did, :ln, :pid, :lot, :qty, :uid, :up)"
            ),
            {
                "id": str(uuid4()), "did": do_id, "ln": i,
                "pid": line.product_id, "lot": line.lot_id,
                "qty": line.qty, "uid": line.uom_id, "up": line.unit_price,
            },
        )
    db.commit()
    return {"do_id": do_id, "do_no": do_no, "status": "draft"}


@router.post("/delivery-orders/{do_id}/validate", summary="Validasi DO — keluar stok & posting GL")
def validate_do(
    do_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "finance", "admin")
    try:
        result = InventoryEngine.validate_delivery_order(
            db=db,
            do_id=do_id,
            validated_by=current_user["username"],
            validated_by_role=current_user["role"],
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/delivery-orders", summary="Daftar Delivery Order")
def list_dos(
    entity_id: str = Query(...),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    filters = ["entity_id = :eid"]
    params: dict[str, Any] = {"eid": entity_id}
    if status:
        filters.append("status = :st")
        params["st"] = status
    where = " AND ".join(filters)
    total = db.execute(text(f"SELECT COUNT(*) FROM delivery_order WHERE {where}"), params).scalar()
    params["offset"] = (page - 1) * size
    params["limit"]  = size
    rows = db.execute(
        text(
            f"SELECT * FROM delivery_order WHERE {where} "
            f"ORDER BY do_date DESC LIMIT :limit OFFSET :offset"
        ),
        params,
    ).fetchall()
    return {"total": total, "page": page, "size": size, "items": [dict(r._mapping) for r in rows]}


@router.get("/delivery-orders/{do_id}", summary="Detail Delivery Order")
def get_do(
    do_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    do_row = db.execute(
        text("SELECT * FROM delivery_order WHERE id = :did"), {"did": do_id}
    ).fetchone()
    if not do_row:
        raise HTTPException(404, "DO tidak ditemukan")
    lines = db.execute(
        text(
            "SELECT dol.*, p.sku, p.product_name "
            "FROM delivery_order_line dol "
            "JOIN product_product p ON p.id = dol.product_id "
            "WHERE dol.do_id = :did ORDER BY dol.line_no"
        ),
        {"did": do_id},
    ).fetchall()
    return {**dict(do_row._mapping), "lines": [dict(l._mapping) for l in lines]}


# ─────────────────────────────────────────────────────────────────────────────
# Internal Transfer
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/transfers", status_code=201, summary="Transfer antar gudang internal")
def transfer(
    req: TransferRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        result = InventoryEngine.transfer_stock(
            db=db,
            entity_id=req.entity_id,
            product_id=req.product_id,
            source_location_id=req.source_location_id,
            destination_location_id=req.destination_location_id,
            qty=req.qty,
            lot_id=req.lot_id,
            reference_no=req.reference_no,
            created_by=current_user["username"],
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Scrap
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/scraps", status_code=201, summary="Pencatatan barang rusak / dibuang")
def scrap(
    req: ScrapRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "finance", "admin")
    try:
        result = InventoryEngine.scrap_goods(
            db=db,
            entity_id=req.entity_id,
            product_id=req.product_id,
            source_location_id=req.source_location_id,
            qty=req.qty,
            reason=req.reason,
            lot_id=req.lot_id,
            created_by=current_user["username"],
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Inventory Adjustment (Stock Opname)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/adjustments", status_code=201, summary="Buat sesi stock opname")
def create_adjustment(
    req: AdjustmentCreateRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "finance", "admin")
    try:
        result = InventoryEngine.create_adjustment(
            db=db,
            entity_id=req.entity_id,
            location_id=req.location_id,
            adjustment_date=req.adjustment_date,
            product_ids=req.product_ids,
            created_by=current_user["username"],
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/adjustments/{adj_id}/start", summary="Mulai counting — set status in_progress")
def start_adjustment(
    adj_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "finance", "admin")
    row = db.execute(
        text("SELECT status FROM inventory_adjustment WHERE id = :aid"), {"aid": adj_id}
    ).fetchone()
    if not row:
        raise HTTPException(404, "Adjustment tidak ditemukan")
    if row.status != "draft":
        raise HTTPException(400, f"Status harus 'draft' (saat ini: {row.status})")
    db.execute(
        text("UPDATE inventory_adjustment SET status = 'in_progress' WHERE id = :aid"),
        {"aid": adj_id},
    )
    db.commit()
    return {"adjustment_id": adj_id, "status": "in_progress"}


@router.put("/adjustments/{adj_id}/count", summary="Input hasil hitungan fisik")
def submit_count(
    adj_id: str,
    req: AdjustmentCountSubmit,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    for item in req.lines:
        db.execute(
            text(
                "UPDATE inventory_adjustment_line "
                "SET actual_qty = :aq "
                "WHERE id = :lid AND adjustment_id = :aid"
            ),
            {"aq": item.actual_qty, "lid": item.line_id, "aid": adj_id},
        )
    db.commit()
    return {"updated": len(req.lines)}


@router.post("/adjustments/{adj_id}/confirm", summary="Konfirmasi opname — buat stock_move untuk selisih")
def confirm_adjustment(
    adj_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "finance", "admin")
    try:
        result = InventoryEngine.confirm_adjustment(
            db=db,
            adjustment_id=adj_id,
            confirmed_by=current_user["username"],
            confirmed_by_role=current_user["role"],
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/adjustments/{adj_id}", summary="Detail sesi opname + selisih")
def get_adjustment(
    adj_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    adj = db.execute(
        text("SELECT * FROM inventory_adjustment WHERE id = :aid"), {"aid": adj_id}
    ).fetchone()
    if not adj:
        raise HTTPException(404, "Adjustment tidak ditemukan")
    lines = db.execute(
        text(
            "SELECT al.*, p.sku, p.product_name "
            "FROM inventory_adjustment_line al "
            "JOIN product_product p ON p.id = al.product_id "
            "WHERE al.adjustment_id = :aid ORDER BY p.sku"
        ),
        {"aid": adj_id},
    ).fetchall()
    return {**dict(adj._mapping), "lines": [dict(l._mapping) for l in lines]}


# ─────────────────────────────────────────────────────────────────────────────
# Reorder Rules
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/reorder-rules", status_code=201, summary="Buat reorder rule")
def create_reorder_rule(
    req: ReorderRuleCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "admin")
    rr_id = str(uuid4())
    db.execute(
        text(
            "INSERT INTO reorder_rule "
            "(id, entity_id, product_id, location_id, route, "
            " min_qty, max_qty, qty_multiple, vendor_id, lead_time_days, created_by) "
            "VALUES (:id, :eid, :pid, :loc, :route, "
            "        :minq, :maxq, :qm, :vid, :lt, :cby) "
            "ON CONFLICT (entity_id, product_id, location_id) DO UPDATE "
            "SET route=EXCLUDED.route, min_qty=EXCLUDED.min_qty, max_qty=EXCLUDED.max_qty, "
            "    qty_multiple=EXCLUDED.qty_multiple, vendor_id=EXCLUDED.vendor_id, "
            "    lead_time_days=EXCLUDED.lead_time_days"
        ),
        {
            "id": rr_id, "eid": req.entity_id, "pid": req.product_id, "loc": req.location_id,
            "route": req.route, "minq": req.min_qty, "maxq": req.max_qty,
            "qm": req.qty_multiple, "vid": req.vendor_id, "lt": req.lead_time_days,
            "cby": current_user["username"],
        },
    )
    db.commit()
    return {"reorder_rule_id": rr_id}


@router.get("/reorder-rules", summary="Daftar reorder rules")
def list_reorder_rules(
    entity_id: str = Query(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    rows = db.execute(
        text(
            "SELECT rr.*, p.sku, p.product_name, loc.location_name "
            "FROM reorder_rule rr "
            "JOIN product_product p ON p.id = rr.product_id "
            "JOIN inventory_location loc ON loc.id = rr.location_id "
            "WHERE rr.entity_id = :eid AND rr.is_active = TRUE "
            "ORDER BY p.sku"
        ),
        {"eid": entity_id},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/reorder-rules/run", summary="Jalankan evaluasi reorder — buat draft PR otomatis")
def run_reorder(
    entity_id: str = Query(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_role(current_user, "finance", "admin")
    triggered = InventoryEngine.run_reorder_check(db=db, entity_id=entity_id, created_by=current_user["username"])
    return {"triggered_count": len(triggered), "items": triggered}


# ─────────────────────────────────────────────────────────────────────────────
# Stock Queries & Reports
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/stock/summary", summary="Ringkasan stok on-hand per produk per lokasi")
def stock_summary(
    entity_id: str = Query(...),
    location_id: Optional[str] = Query(None),
    product_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return InventoryEngine.get_stock_summary(
        db=db,
        entity_id=entity_id,
        location_id=location_id,
        product_id=product_id,
    )


@router.get("/stock/low-stock", summary="Produk di bawah safety stock")
def low_stock_alert(
    entity_id: str = Query(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    rows = db.execute(
        text("SELECT * FROM vw_low_stock_alert WHERE entity_id = :eid ORDER BY shortage_qty DESC"),
        {"eid": entity_id},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/stock/moves", summary="Riwayat mutasi stok")
def stock_moves(
    entity_id: str = Query(...),
    product_id: Optional[str] = Query(None),
    move_type: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return InventoryEngine.get_stock_moves(
        db=db,
        entity_id=entity_id,
        product_id=product_id,
        move_type=move_type,
        date_from=date_from,
        date_to=date_to,
        page=page,
        size=size,
    )


@router.get("/stock/fifo-layers/{product_id}", summary="Lapisan biaya FIFO (untuk audit FIFO)")
def fifo_layers(
    product_id: str,
    entity_id: str = Query(...),
    location_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return InventoryEngine.get_fifo_layers(
        db=db,
        entity_id=entity_id,
        product_id=product_id,
        location_id=location_id,
    )


@router.get("/stock/valuation", summary="Valuasi total persediaan per produk")
def stock_valuation(
    entity_id: str = Query(...),
    location_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    query = "SELECT * FROM vw_stock_summary WHERE entity_id = :eid"
    params: dict[str, Any] = {"eid": entity_id}
    if location_id:
        query += " AND location_id = :loc"
        params["loc"] = location_id
    rows = db.execute(text(query + " ORDER BY product_name"), params).fetchall()
    items = [dict(r._mapping) for r in rows]
    total_value = sum(float(r.get("stock_value") or 0) for r in items)
    return {"total_stock_value_idr": total_value, "items": items}


@router.get("/uom", summary="Daftar satuan unit (UOM)")
def list_uom(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    rows = db.execute(
        text("SELECT * FROM product_uom WHERE is_active = TRUE ORDER BY uom_code")
    ).fetchall()
    return [dict(r._mapping) for r in rows]
