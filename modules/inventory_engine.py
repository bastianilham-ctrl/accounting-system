"""
Inventory Engine
Handles valuation (AVCO / FIFO / Standard), GL posting, GR, DO, scrap,
internal transfer, stock adjustment, and reorder rule evaluation.

Prinsip double-entry inventory:
  Barang tidak pernah hilang — selalu berpindah dari satu lokasi ke lokasi lain.
  Setiap stock_move status='done' memicu GL journal secara otomatis.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from modules.journal_engine import JournalEngine, JournalEntry, JournalLine  # existing GL engine


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _gen_doc_no(db: Session, prefix: str) -> str:
    """Generate sequential document number: PREFIX/YYYY/MM/NNNN."""
    today = date.today()
    year, month = today.year, today.month
    pattern = f"{prefix}/{year}/{month:02d}/%"
    row = db.execute(
        text(
            "SELECT COUNT(*) AS cnt FROM stock_move "
            "WHERE reference_no LIKE :p"
        ),
        {"p": pattern},
    ).fetchone()
    seq = (row.cnt if row else 0) + 1
    return f"{prefix}/{year}/{month:02d}/{seq:04d}"


def _gen_do_no(db: Session) -> str:
    today = date.today()
    year, month = today.year, today.month
    row = db.execute(
        text(
            "SELECT COUNT(*) AS cnt FROM delivery_order "
            "WHERE do_no LIKE :p"
        ),
        {"p": f"DO/{year}/{month:02d}/%"},
    ).fetchone()
    seq = (row.cnt if row else 0) + 1
    return f"DO/{year}/{month:02d}/{seq:04d}"


def _gen_scrap_no(db: Session) -> str:
    today = date.today()
    year, month = today.year, today.month
    row = db.execute(
        text(
            "SELECT COUNT(*) AS cnt FROM stock_scrap "
            "WHERE scrap_no LIKE :p"
        ),
        {"p": f"SCR/{year}/{month:02d}/%"},
    ).fetchone()
    seq = (row.cnt if row else 0) + 1
    return f"SCR/{year}/{month:02d}/{seq:04d}"


def _gen_adj_no(db: Session) -> str:
    today = date.today()
    year, month = today.year, today.month
    row = db.execute(
        text(
            "SELECT COUNT(*) AS cnt FROM inventory_adjustment "
            "WHERE adjustment_no LIKE :p"
        ),
        {"p": f"SA/{year}/{month:02d}/%"},
    ).fetchone()
    seq = (row.cnt if row else 0) + 1
    return f"SA/{year}/{month:02d}/{seq:04d}"


def _get_product(db: Session, product_id: str) -> dict:
    row = db.execute(
        text(
            "SELECT p.*, pc.cost_method, "
            "       pc.inventory_account_code, pc.cogs_account_code, "
            "       pc.grir_account_code, pc.scrapped_account_code, "
            "       pc.adjustment_account_code, pc.wip_account_code "
            "FROM product_product p "
            "JOIN product_category pc ON pc.id = p.category_id "
            "WHERE p.id = :pid"
        ),
        {"pid": product_id},
    ).fetchone()
    if not row:
        raise ValueError(f"Product {product_id} tidak ditemukan")
    return dict(row._mapping)


def _get_location(db: Session, loc_id: str) -> dict:
    row = db.execute(
        text("SELECT * FROM inventory_location WHERE id = :lid"),
        {"lid": loc_id},
    ).fetchone()
    if not row:
        raise ValueError(f"Location {loc_id} tidak ditemukan")
    return dict(row._mapping)


def _get_qty_on_hand(db: Session, product_id: str, location_id: str) -> Decimal:
    """Hitung qty on-hand dari mutasi stock_move status='done'."""
    row = db.execute(
        text(
            "SELECT "
            "  COALESCE(SUM(CASE WHEN destination_location_id = :loc THEN qty_done ELSE 0 END), 0) "
            "- COALESCE(SUM(CASE WHEN source_location_id = :loc THEN qty_done ELSE 0 END), 0) "
            "  AS qty_on_hand "
            "FROM stock_move "
            "WHERE product_id = :pid "
            "  AND status = 'done' "
            "  AND (:loc IN (source_location_id, destination_location_id))"
        ),
        {"pid": product_id, "loc": location_id},
    ).fetchone()
    return Decimal(str(row.qty_on_hand or 0))


# ─────────────────────────────────────────────────────────────────────────────
# Valuation Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_unit_cost_for_delivery(
    db: Session,
    product: dict,
    location_id: str,
    qty: Decimal,
) -> Decimal:
    """
    Kembalikan unit_cost untuk digunakan pada outgoing move (DO / scrap / production_out).
    Untuk FIFO: rata-rata dari FIFO layers yang akan dikonsumsi.
    Untuk AVCO / standard_cost: ambil dari product_product.
    """
    method = product["cost_method"]
    if method == "standard_cost":
        return Decimal(str(product["standard_price"]))

    if method == "average_cost":
        return Decimal(str(product["current_avg_cost"]))

    # FIFO — baca layers tertua dulu
    if method == "fifo":
        rows = db.execute(
            text(
                "SELECT unit_cost, remaining_qty "
                "FROM stock_valuation_layer "
                "WHERE product_id = :pid "
                "  AND entity_id  = :eid "
                "  AND location_id = :loc "
                "  AND remaining_qty > 0 "
                "ORDER BY created_at ASC"
            ),
            {
                "pid": product["id"],
                "eid": product["entity_id"],
                "loc": location_id,
            },
        ).fetchall()

        total_cost = Decimal("0")
        remaining = qty
        for layer in rows:
            take = min(Decimal(str(layer.remaining_qty)), remaining)
            total_cost += take * Decimal(str(layer.unit_cost))
            remaining -= take
            if remaining <= 0:
                break

        if remaining > Decimal("0.0001"):
            raise ValueError(
                f"Stok tidak mencukupi (kekurangan {remaining} unit) "
                f"untuk produk {product['product_name']}"
            )
        return (total_cost / qty).quantize(Decimal("0.0001"))

    raise ValueError(f"cost_method tidak dikenal: {method}")


def _consume_fifo_layers(
    db: Session,
    product: dict,
    location_id: str,
    qty: Decimal,
    move_id: str,
) -> None:
    """Kurangi FIFO valuation layers (oldest first) setelah outgoing move."""
    rows = db.execute(
        text(
            "SELECT id, remaining_qty, unit_cost "
            "FROM stock_valuation_layer "
            "WHERE product_id  = :pid "
            "  AND entity_id   = :eid "
            "  AND location_id = :loc "
            "  AND remaining_qty > 0 "
            "ORDER BY created_at ASC "
            "FOR UPDATE"
        ),
        {
            "pid": product["id"],
            "eid": product["entity_id"],
            "loc": location_id,
        },
    ).fetchall()

    remaining = qty
    for layer in rows:
        if remaining <= 0:
            break
        take = min(Decimal(str(layer.remaining_qty)), remaining)
        new_qty = Decimal(str(layer.remaining_qty)) - take
        db.execute(
            text(
                "UPDATE stock_valuation_layer "
                "SET remaining_qty = :nq "
                "WHERE id = :lid"
            ),
            {"nq": float(new_qty), "lid": str(layer.id)},
        )
        remaining -= take


def _update_avco(
    db: Session,
    product: dict,
    location_id: str,
    incoming_qty: Decimal,
    incoming_unit_cost: Decimal,
) -> Decimal:
    """
    Hitung ulang average cost setelah goods receipt.
    new_avg = (existing_value + incoming_value) / (existing_qty + incoming_qty)
    """
    existing_qty = _get_qty_on_hand(db, product["id"], location_id)
    existing_value = existing_qty * Decimal(str(product["current_avg_cost"]))
    new_value = existing_value + (incoming_qty * incoming_unit_cost)
    new_qty = existing_qty + incoming_qty
    new_avg = (new_value / new_qty).quantize(Decimal("0.0001")) if new_qty > 0 else incoming_unit_cost

    db.execute(
        text(
            "UPDATE product_product "
            "SET current_avg_cost = :avg, updated_at = NOW() "
            "WHERE id = :pid"
        ),
        {"avg": float(new_avg), "pid": product["id"]},
    )
    return new_avg


# ─────────────────────────────────────────────────────────────────────────────
# GL Posting per move_type
# ─────────────────────────────────────────────────────────────────────────────

_GL_DESCRIPTIONS: dict[str, str] = {
    "receipt":        "Goods Receipt — penerimaan barang dari vendor",
    "delivery":       "Delivery — pengiriman barang ke pelanggan",
    "scrap":          "Scrap — barang rusak/dibuang",
    "adjustment_in":  "Inventory Adjustment — koreksi positif stok opname",
    "adjustment_out": "Inventory Adjustment — koreksi negatif stok opname",
    "production_out": "Production — bahan baku keluar ke WIP",
    "production_in":  "Production — barang jadi masuk dari WIP",
}


def _post_gl_for_move(
    db: Session,
    move: dict,
    product: dict,
    entity_id: str,
    created_by: str,
) -> Optional[str]:
    """
    Buat GL journal untuk satu stock_move berdasarkan move_type.
    Return gl_journal_id atau None jika tidak ada GL (internal transfer).
    """
    move_type = move["move_type"]
    total_cost = Decimal(str(move["total_cost"]))
    ref_no     = move["reference_no"] or str(move["id"])

    inv_acc  = product["inventory_account_code"]
    cogs_acc = product["cogs_account_code"]
    grir_acc = product["grir_account_code"]
    scr_acc  = product["scrapped_account_code"]
    adj_acc  = product["adjustment_account_code"]
    wip_acc  = product["wip_account_code"]

    if move_type == "transfer":
        return None  # internal transfer — tidak ada GL

    description = _GL_DESCRIPTIONS.get(move_type, f"Inventory move: {move_type}")
    description += f" | {ref_no}"

    if move_type == "receipt":
        # Dr. Persediaan | Cr. GR/IR (Hutang Belum Diinvoice)
        if not inv_acc or not grir_acc:
            raise ValueError("inventory_account_code dan grir_account_code wajib diisi di product_category")
        lines = [
            {"account_code": inv_acc,  "debit": float(total_cost),  "credit": 0},
            {"account_code": grir_acc, "debit": 0, "credit": float(total_cost)},
        ]

    elif move_type == "delivery":
        # Dr. HPP/COGS | Cr. Persediaan
        if not cogs_acc or not inv_acc:
            raise ValueError("cogs_account_code dan inventory_account_code wajib diisi di product_category")
        lines = [
            {"account_code": cogs_acc, "debit": float(total_cost),  "credit": 0},
            {"account_code": inv_acc,  "debit": 0, "credit": float(total_cost)},
        ]

    elif move_type == "scrap":
        # Dr. Kerugian Persediaan | Cr. Persediaan
        if not scr_acc or not inv_acc:
            raise ValueError("scrapped_account_code dan inventory_account_code wajib diisi di product_category")
        lines = [
            {"account_code": scr_acc, "debit": float(total_cost),  "credit": 0},
            {"account_code": inv_acc, "debit": 0, "credit": float(total_cost)},
        ]

    elif move_type == "adjustment_in":
        # Dr. Persediaan | Cr. Selisih Persediaan
        if not inv_acc or not adj_acc:
            raise ValueError("inventory_account_code dan adjustment_account_code wajib diisi di product_category")
        lines = [
            {"account_code": inv_acc, "debit": float(total_cost),  "credit": 0},
            {"account_code": adj_acc, "debit": 0, "credit": float(total_cost)},
        ]

    elif move_type == "adjustment_out":
        # Dr. Selisih Persediaan | Cr. Persediaan
        if not inv_acc or not adj_acc:
            raise ValueError("inventory_account_code dan adjustment_account_code wajib diisi di product_category")
        lines = [
            {"account_code": adj_acc, "debit": float(total_cost),  "credit": 0},
            {"account_code": inv_acc, "debit": 0, "credit": float(total_cost)},
        ]

    elif move_type == "production_out":
        # Dr. WIP | Cr. Persediaan Bahan Baku
        if not wip_acc or not inv_acc:
            raise ValueError("wip_account_code dan inventory_account_code wajib diisi di product_category")
        lines = [
            {"account_code": wip_acc, "debit": float(total_cost),  "credit": 0},
            {"account_code": inv_acc, "debit": 0, "credit": float(total_cost)},
        ]

    elif move_type == "production_in":
        # Dr. Persediaan Barang Jadi | Cr. WIP
        if not inv_acc or not wip_acc:
            raise ValueError("inventory_account_code dan wip_account_code wajib diisi di product_category")
        lines = [
            {"account_code": inv_acc, "debit": float(total_cost),  "credit": 0},
            {"account_code": wip_acc, "debit": 0, "credit": float(total_cost)},
        ]

    else:
        raise ValueError(f"move_type tidak dikenal: {move_type}")

    entry = JournalEntry(
        entity_id=entity_id,
        journal_type="GL",
        journal_date=move["done_at"].date() if move.get("done_at") else date.today(),
        description=description,
        reference_no=ref_no,
        created_by=created_by,
        lines=[
            JournalLine(
                account_code=ln["account_code"],
                description=description,
                debit_idr=Decimal(str(ln["debit"])),
                credit_idr=Decimal(str(ln["credit"])),
            )
            for ln in lines
        ],
    )
    result = JournalEngine(db).post_journal(entry)
    if not result.get("success"):
        raise ValueError(f"Gagal posting GL inventory: {result.get('error')}")
    return result["journal_id"]


# ─────────────────────────────────────────────────────────────────────────────
# InventoryEngine — kelas utama
# ─────────────────────────────────────────────────────────────────────────────

class InventoryEngine:

    # ─── Goods Receipt ────────────────────────────────────────────────────────

    @staticmethod
    def receive_goods(
        db: Session,
        entity_id: str,
        po_receipt_id: str,
        product_id: str,
        destination_location_id: str,
        qty: float,
        unit_cost: float,
        lot_id: Optional[str] = None,
        reference_no: Optional[str] = None,
        created_by: str = "system",
    ) -> dict:
        """
        Proses penerimaan barang dari vendor (Goods Receipt).
        Arah: supplier_location → internal_location
        GL: Dr. Persediaan | Cr. GR/IR Clearing
        """
        qty_d      = Decimal(str(qty))
        unit_cost_d = Decimal(str(unit_cost))
        total_cost = qty_d * unit_cost_d
        product    = _get_product(db, product_id)
        dest_loc   = _get_location(db, destination_location_id)

        if dest_loc["location_type"] != "internal":
            raise ValueError("destination harus lokasi internal (gudang)")

        supplier_loc = db.execute(
            text(
                "SELECT id FROM inventory_location "
                "WHERE entity_id = :eid AND location_type = 'supplier' "
                "LIMIT 1"
            ),
            {"eid": entity_id},
        ).fetchone()
        if not supplier_loc:
            raise ValueError("Virtual location 'supplier' belum dibuat untuk entity ini")

        ref = reference_no or _gen_doc_no(db, "GR")
        done_at = _now()

        move_id = str(uuid.uuid4())
        db.execute(
            text(
                "INSERT INTO stock_move "
                "(id, entity_id, product_id, source_location_id, destination_location_id, "
                " move_type, qty_done, unit_cost, total_cost, "
                " po_receipt_id, lot_id, reference_no, status, done_at, done_by, created_by, created_at) "
                "VALUES (:id, :eid, :pid, :src, :dst, 'receipt', :qty, :uc, :tc, "
                "        :recid, :lot, :ref, 'done', :done_at, :done_by, :cby, NOW())"
            ),
            {
                "id": move_id, "eid": entity_id, "pid": product_id,
                "src": str(supplier_loc.id), "dst": destination_location_id,
                "qty": float(qty_d), "uc": float(unit_cost_d), "tc": float(total_cost),
                "recid": po_receipt_id, "lot": lot_id,
                "ref": ref, "done_at": done_at, "done_by": created_by, "cby": created_by,
            },
        )

        # Valuasi
        if product["cost_method"] == "average_cost":
            new_avg = _update_avco(db, product, destination_location_id, qty_d, unit_cost_d)

        elif product["cost_method"] == "fifo":
            new_avg = unit_cost_d
            layer_id = str(uuid.uuid4())
            db.execute(
                text(
                    "INSERT INTO stock_valuation_layer "
                    "(id, product_id, entity_id, location_id, move_id, qty, unit_cost, remaining_qty) "
                    "VALUES (:id, :pid, :eid, :loc, :mid, :qty, :uc, :qty)"
                ),
                {
                    "id": layer_id, "pid": product_id, "eid": entity_id,
                    "loc": destination_location_id, "mid": move_id,
                    "qty": float(qty_d), "uc": float(unit_cost_d),
                },
            )

        else:  # standard_cost
            new_avg = Decimal(str(product["standard_price"]))

        # GL posting
        move_row = {
            "id": move_id, "move_type": "receipt",
            "total_cost": float(total_cost), "reference_no": ref,
            "done_at": done_at,
        }
        gl_id = _post_gl_for_move(db, move_row, product, entity_id, created_by)

        if gl_id:
            db.execute(
                text("UPDATE stock_move SET gl_journal_id = :gl WHERE id = :mid"),
                {"gl": gl_id, "mid": move_id},
            )

        db.commit()
        return {
            "move_id": move_id,
            "reference_no": ref,
            "qty": float(qty_d),
            "unit_cost": float(unit_cost_d),
            "total_cost": float(total_cost),
            "new_unit_cost": float(new_avg),
            "gl_journal_id": gl_id,
        }

    # ─── Delivery Order ───────────────────────────────────────────────────────

    @staticmethod
    def validate_delivery_order(
        db: Session,
        do_id: str,
        validated_by: str,
        validated_by_role: str,
    ) -> dict:
        """
        Validasi DO — update stok, tutup DO, buat GL.
        GL: Dr. HPP/COGS | Cr. Persediaan
        """
        do_row = db.execute(
            text("SELECT * FROM delivery_order WHERE id = :did"),
            {"did": do_id},
        ).fetchone()
        if not do_row:
            raise ValueError(f"Delivery Order {do_id} tidak ditemukan")
        if do_row.status not in ("draft", "ready"):
            raise ValueError(f"DO status '{do_row.status}' tidak bisa di-validate")

        lines = db.execute(
            text("SELECT * FROM delivery_order_line WHERE do_id = :did ORDER BY line_no"),
            {"did": do_id},
        ).fetchall()

        done_at = _now()
        move_ids = []

        for line in lines:
            product = _get_product(db, str(line.product_id))

            if product["product_type"] != "storable":
                # Service/consumable tidak ditrack di gudang — tidak ada stock_move/GL inventory.
                # Revenue & COGS untuk item ini diakui sepenuhnya lewat posting AR Invoice (REQ-03).
                continue

            # cek stok cukup
            qty_on_hand = _get_qty_on_hand(db, str(line.product_id), str(do_row.source_location_id))
            if qty_on_hand < Decimal(str(line.qty)):
                raise ValueError(
                    f"Stok tidak mencukupi untuk {product['product_name']}: "
                    f"tersedia {qty_on_hand}, dibutuhkan {line.qty}"
                )

            qty_d = Decimal(str(line.qty))
            unit_cost_d = _get_unit_cost_for_delivery(
                db, product, str(do_row.source_location_id), qty_d
            )
            total_cost = qty_d * unit_cost_d

            # customer virtual location
            customer_loc = db.execute(
                text(
                    "SELECT id FROM inventory_location "
                    "WHERE entity_id = :eid AND location_type = 'customer' "
                    "LIMIT 1"
                ),
                {"eid": str(do_row.entity_id)},
            ).fetchone()
            if not customer_loc:
                raise ValueError("Virtual location 'customer' belum dibuat untuk entity ini")

            move_id = str(uuid.uuid4())
            db.execute(
                text(
                    "INSERT INTO stock_move "
                    "(id, entity_id, product_id, source_location_id, destination_location_id, "
                    " move_type, qty_done, unit_cost, total_cost, "
                    " do_id, lot_id, reference_no, status, done_at, done_by, created_by, created_at) "
                    "VALUES (:id, :eid, :pid, :src, :dst, 'delivery', :qty, :uc, :tc, "
                    "        :doid, :lot, :ref, 'done', :done_at, :done_by, :cby, NOW())"
                ),
                {
                    "id": move_id, "eid": str(do_row.entity_id), "pid": str(line.product_id),
                    "src": str(do_row.source_location_id), "dst": str(customer_loc.id),
                    "qty": float(qty_d), "uc": float(unit_cost_d), "tc": float(total_cost),
                    "doid": do_id, "lot": str(line.lot_id) if line.lot_id else None,
                    "ref": str(do_row.do_no), "done_at": done_at,
                    "done_by": validated_by, "cby": validated_by,
                },
            )

            # FIFO: consume layers
            if product["cost_method"] == "fifo":
                _consume_fifo_layers(db, product, str(do_row.source_location_id), qty_d, move_id)

            # GL
            move_row = {
                "id": move_id, "move_type": "delivery",
                "total_cost": float(total_cost), "reference_no": str(do_row.do_no),
                "done_at": done_at,
            }
            gl_id = _post_gl_for_move(db, move_row, product, str(do_row.entity_id), validated_by)
            if gl_id:
                db.execute(
                    text("UPDATE stock_move SET gl_journal_id = :gl WHERE id = :mid"),
                    {"gl": gl_id, "mid": move_id},
                )

            # update DO line with move_id
            db.execute(
                text("UPDATE delivery_order_line SET move_id = :mid WHERE id = :lid"),
                {"mid": move_id, "lid": str(line.id)},
            )
            move_ids.append(move_id)

        # close DO
        db.execute(
            text(
                "UPDATE delivery_order "
                "SET status = 'done', validated_by = :vb, validated_at = :vat, updated_at = NOW() "
                "WHERE id = :did"
            ),
            {"vb": validated_by, "vat": done_at, "did": do_id},
        )
        db.commit()
        return {
            "do_id": do_id,
            "do_no": str(do_row.do_no),
            "status": "done",
            "move_ids": move_ids,
            "validated_by": validated_by,
        }

    # ─── Internal Transfer ────────────────────────────────────────────────────

    @staticmethod
    def transfer_stock(
        db: Session,
        entity_id: str,
        product_id: str,
        source_location_id: str,
        destination_location_id: str,
        qty: float,
        lot_id: Optional[str] = None,
        reference_no: Optional[str] = None,
        created_by: str = "system",
    ) -> dict:
        """
        Transfer internal antar gudang.
        Tidak ada GL posting (tidak ada perubahan nilai neraca).
        """
        qty_d   = Decimal(str(qty))
        product = _get_product(db, product_id)

        qty_on_hand = _get_qty_on_hand(db, product_id, source_location_id)
        if qty_on_hand < qty_d:
            raise ValueError(
                f"Stok di lokasi sumber tidak cukup: tersedia {qty_on_hand}, diminta {qty_d}"
            )

        unit_cost_d = _get_unit_cost_for_delivery(db, product, source_location_id, qty_d)
        total_cost  = qty_d * unit_cost_d
        ref = reference_no or _gen_doc_no(db, "TRF")
        done_at = _now()

        move_id = str(uuid.uuid4())
        db.execute(
            text(
                "INSERT INTO stock_move "
                "(id, entity_id, product_id, source_location_id, destination_location_id, "
                " move_type, qty_done, unit_cost, total_cost, "
                " lot_id, reference_no, status, done_at, done_by, created_by, created_at) "
                "VALUES (:id, :eid, :pid, :src, :dst, 'transfer', :qty, :uc, :tc, "
                "        :lot, :ref, 'done', :done_at, :done_by, :cby, NOW())"
            ),
            {
                "id": move_id, "eid": entity_id, "pid": product_id,
                "src": source_location_id, "dst": destination_location_id,
                "qty": float(qty_d), "uc": float(unit_cost_d), "tc": float(total_cost),
                "lot": lot_id, "ref": ref, "done_at": done_at,
                "done_by": created_by, "cby": created_by,
            },
        )

        # FIFO: transfer layers ke lokasi tujuan
        if product["cost_method"] == "fifo":
            _consume_fifo_layers(db, product, source_location_id, qty_d, move_id)
            layer_id = str(uuid.uuid4())
            db.execute(
                text(
                    "INSERT INTO stock_valuation_layer "
                    "(id, product_id, entity_id, location_id, move_id, qty, unit_cost, remaining_qty) "
                    "VALUES (:id, :pid, :eid, :loc, :mid, :qty, :uc, :qty)"
                ),
                {
                    "id": layer_id, "pid": product_id, "eid": entity_id,
                    "loc": destination_location_id, "mid": move_id,
                    "qty": float(qty_d), "uc": float(unit_cost_d),
                },
            )

        db.commit()
        return {
            "move_id": move_id,
            "reference_no": ref,
            "qty": float(qty_d),
            "unit_cost": float(unit_cost_d),
            "total_cost": float(total_cost),
            "gl_journal_id": None,
        }

    # ─── Scrap ────────────────────────────────────────────────────────────────

    @staticmethod
    def scrap_goods(
        db: Session,
        entity_id: str,
        product_id: str,
        source_location_id: str,
        qty: float,
        reason: Optional[str] = None,
        lot_id: Optional[str] = None,
        created_by: str = "system",
    ) -> dict:
        """
        Penghapusan barang rusak.
        GL: Dr. Kerugian Persediaan | Cr. Persediaan
        """
        qty_d   = Decimal(str(qty))
        product = _get_product(db, product_id)

        qty_on_hand = _get_qty_on_hand(db, product_id, source_location_id)
        if qty_on_hand < qty_d:
            raise ValueError(f"Stok tidak mencukupi: tersedia {qty_on_hand}")

        scrap_loc = db.execute(
            text(
                "SELECT id FROM inventory_location "
                "WHERE entity_id = :eid AND location_type = 'scrapped' "
                "LIMIT 1"
            ),
            {"eid": entity_id},
        ).fetchone()
        if not scrap_loc:
            raise ValueError("Virtual location 'scrapped' belum dibuat untuk entity ini")

        unit_cost_d = _get_unit_cost_for_delivery(db, product, source_location_id, qty_d)
        total_cost  = qty_d * unit_cost_d
        scrap_no    = _gen_scrap_no(db)
        done_at     = _now()

        scrap_id = str(uuid.uuid4())
        move_id  = str(uuid.uuid4())

        db.execute(
            text(
                "INSERT INTO stock_scrap "
                "(id, entity_id, scrap_no, scrap_date, product_id, lot_id, "
                " source_location_id, qty, reason, created_by, created_at) "
                "VALUES (:id, :eid, :sno, :dt, :pid, :lot, :src, :qty, :rsn, :cby, NOW())"
            ),
            {
                "id": scrap_id, "eid": entity_id, "sno": scrap_no,
                "dt": done_at.date(), "pid": product_id, "lot": lot_id,
                "src": source_location_id, "qty": float(qty_d),
                "rsn": reason, "cby": created_by,
            },
        )
        db.execute(
            text(
                "INSERT INTO stock_move "
                "(id, entity_id, product_id, source_location_id, destination_location_id, "
                " move_type, qty_done, unit_cost, total_cost, "
                " scrap_id, reference_no, status, done_at, done_by, created_by, created_at) "
                "VALUES (:id, :eid, :pid, :src, :dst, 'scrap', :qty, :uc, :tc, "
                "        :scrid, :ref, 'done', :done_at, :done_by, :cby, NOW())"
            ),
            {
                "id": move_id, "eid": entity_id, "pid": product_id,
                "src": source_location_id, "dst": str(scrap_loc.id),
                "qty": float(qty_d), "uc": float(unit_cost_d), "tc": float(total_cost),
                "scrid": scrap_id, "ref": scrap_no, "done_at": done_at,
                "done_by": created_by, "cby": created_by,
            },
        )

        if product["cost_method"] == "fifo":
            _consume_fifo_layers(db, product, source_location_id, qty_d, move_id)

        move_row = {
            "id": move_id, "move_type": "scrap",
            "total_cost": float(total_cost), "reference_no": scrap_no,
            "done_at": done_at,
        }
        gl_id = _post_gl_for_move(db, move_row, product, entity_id, created_by)
        if gl_id:
            db.execute(
                text("UPDATE stock_move SET gl_journal_id = :gl WHERE id = :mid"),
                {"gl": gl_id, "mid": move_id},
            )
            db.execute(
                text("UPDATE stock_scrap SET move_id = :mid WHERE id = :sid"),
                {"mid": move_id, "sid": scrap_id},
            )

        db.commit()
        return {
            "scrap_id": scrap_id,
            "scrap_no": scrap_no,
            "move_id": move_id,
            "qty": float(qty_d),
            "unit_cost": float(unit_cost_d),
            "total_cost": float(total_cost),
            "gl_journal_id": gl_id,
        }

    # ─── Inventory Adjustment (Stock Opname) ─────────────────────────────────

    @staticmethod
    def create_adjustment(
        db: Session,
        entity_id: str,
        location_id: str,
        adjustment_date: date,
        product_ids: Optional[list[str]] = None,
        created_by: str = "system",
    ) -> dict:
        """
        Buat header stock adjustment dan ambil snapshot theoretical_qty dari sistem.
        product_ids=None → ambil semua produk di lokasi tersebut.
        """
        adj_no = _gen_adj_no(db)
        adj_id = str(uuid.uuid4())

        db.execute(
            text(
                "INSERT INTO inventory_adjustment "
                "(id, entity_id, adjustment_no, location_id, adjustment_date, "
                " status, created_by, created_at) "
                "VALUES (:id, :eid, :ano, :loc, :dt, 'draft', :cby, NOW())"
            ),
            {
                "id": adj_id, "eid": entity_id, "ano": adj_no,
                "loc": location_id, "dt": adjustment_date, "cby": created_by,
            },
        )

        # Ambil semua produk + qty on-hand di lokasi ini
        if product_ids:
            product_filter = "AND p.id = ANY(:pids)"
            params: dict[str, Any] = {"loc": location_id, "eid": entity_id, "pids": product_ids}
        else:
            product_filter = ""
            params = {"loc": location_id, "eid": entity_id}

        products = db.execute(
            text(
                f"SELECT DISTINCT sm.product_id, p.current_avg_cost "
                f"FROM stock_move sm "
                f"JOIN product_product p ON p.id = sm.product_id "
                f"WHERE sm.entity_id = :eid "
                f"  AND :loc IN (sm.source_location_id, sm.destination_location_id) "
                f"  AND sm.status = 'done' "
                f"  {product_filter}"
            ),
            params,
        ).fetchall()

        for prod in products:
            theoretical = _get_qty_on_hand(db, str(prod.product_id), location_id)
            db.execute(
                text(
                    "INSERT INTO inventory_adjustment_line "
                    "(id, adjustment_id, product_id, theoretical_qty, actual_qty, unit_cost) "
                    "VALUES (:id, :aid, :pid, :tqty, NULL, :uc)"
                ),
                {
                    "id": str(uuid.uuid4()), "aid": adj_id,
                    "pid": str(prod.product_id),
                    "tqty": float(theoretical),
                    "uc": float(prod.current_avg_cost or 0),
                },
            )

        db.commit()
        return {
            "adjustment_id": adj_id,
            "adjustment_no": adj_no,
            "status": "draft",
            "line_count": len(products),
        }

    @staticmethod
    def confirm_adjustment(
        db: Session,
        adjustment_id: str,
        confirmed_by: str,
        confirmed_by_role: str,
    ) -> dict:
        """
        Konfirmasi stock opname — buat stock_move untuk setiap selisih.
        Selisih > 0 → adjustment_in; selisih < 0 → adjustment_out.
        """
        if confirmed_by_role not in ("finance", "admin") and confirmed_by_role != "superadmin":
            raise ValueError("Konfirmasi stock adjustment butuh role finance atau admin")

        adj = db.execute(
            text("SELECT * FROM inventory_adjustment WHERE id = :aid"),
            {"aid": adjustment_id},
        ).fetchone()
        if not adj:
            raise ValueError(f"Adjustment {adjustment_id} tidak ditemukan")
        if adj.status != "in_progress":
            raise ValueError(f"Status harus 'in_progress' sebelum confirm (saat ini: {adj.status})")

        lines = db.execute(
            text(
                "SELECT * FROM inventory_adjustment_line "
                "WHERE adjustment_id = :aid AND actual_qty IS NOT NULL"
            ),
            {"aid": adjustment_id},
        ).fetchall()

        done_at = _now()
        moves_created = []

        for line in lines:
            diff = Decimal(str(line.actual_qty)) - Decimal(str(line.theoretical_qty))
            if abs(diff) < Decimal("0.0001"):
                continue

            product   = _get_product(db, str(line.product_id))
            unit_cost = Decimal(str(line.unit_cost))
            total_cost = abs(diff) * unit_cost

            if diff > 0:
                move_type = "adjustment_in"
                src_loc = db.execute(
                    text(
                        "SELECT id FROM inventory_location "
                        "WHERE entity_id = :eid AND location_type = 'virtual' LIMIT 1"
                    ),
                    {"eid": str(adj.entity_id)},
                ).fetchone()
                if not src_loc:
                    raise ValueError("Virtual location type='virtual' belum dibuat untuk entity ini")
                src = str(src_loc.id)
                dst = str(adj.location_id)
                qty_done = diff
            else:
                move_type = "adjustment_out"
                virt_loc = db.execute(
                    text(
                        "SELECT id FROM inventory_location "
                        "WHERE entity_id = :eid AND location_type = 'virtual' LIMIT 1"
                    ),
                    {"eid": str(adj.entity_id)},
                ).fetchone()
                if not virt_loc:
                    raise ValueError("Virtual location type='virtual' belum dibuat untuk entity ini")
                src = str(adj.location_id)
                dst = str(virt_loc.id)
                qty_done = abs(diff)

            move_id = str(uuid.uuid4())
            db.execute(
                text(
                    "INSERT INTO stock_move "
                    "(id, entity_id, product_id, source_location_id, destination_location_id, "
                    " move_type, qty_done, unit_cost, total_cost, "
                    " adjustment_id, reference_no, status, done_at, done_by, created_by, created_at) "
                    "VALUES (:id, :eid, :pid, :src, :dst, :mt, :qty, :uc, :tc, "
                    "        :adjid, :ref, 'done', :done_at, :done_by, :cby, NOW())"
                ),
                {
                    "id": move_id, "eid": str(adj.entity_id), "pid": str(line.product_id),
                    "src": src, "dst": dst,
                    "mt": move_type,
                    "qty": float(qty_done), "uc": float(unit_cost), "tc": float(total_cost),
                    "adjid": adjustment_id, "ref": str(adj.adjustment_no),
                    "done_at": done_at, "done_by": confirmed_by, "cby": confirmed_by,
                },
            )

            if product["cost_method"] == "fifo":
                if move_type == "adjustment_out":
                    _consume_fifo_layers(db, product, src, qty_done, move_id)
                else:
                    layer_id = str(uuid.uuid4())
                    db.execute(
                        text(
                            "INSERT INTO stock_valuation_layer "
                            "(id, product_id, entity_id, location_id, move_id, qty, unit_cost, remaining_qty) "
                            "VALUES (:id, :pid, :eid, :loc, :mid, :qty, :uc, :qty)"
                        ),
                        {
                            "id": layer_id, "pid": str(line.product_id),
                            "eid": str(adj.entity_id), "loc": dst,
                            "mid": move_id, "qty": float(qty_done), "uc": float(unit_cost),
                        },
                    )

            move_row = {
                "id": move_id, "move_type": move_type,
                "total_cost": float(total_cost), "reference_no": str(adj.adjustment_no),
                "done_at": done_at,
            }
            gl_id = _post_gl_for_move(db, move_row, product, str(adj.entity_id), confirmed_by)
            if gl_id:
                db.execute(
                    text("UPDATE stock_move SET gl_journal_id = :gl WHERE id = :mid"),
                    {"gl": gl_id, "mid": move_id},
                )

            db.execute(
                text("UPDATE inventory_adjustment_line SET move_id = :mid WHERE id = :lid"),
                {"mid": move_id, "lid": str(line.id)},
            )
            moves_created.append({"move_id": move_id, "move_type": move_type, "qty": float(qty_done)})

        db.execute(
            text(
                "UPDATE inventory_adjustment "
                "SET status = 'done', confirmed_by = :cb, confirmed_at = :cat "
                "WHERE id = :aid"
            ),
            {"cb": confirmed_by, "cat": done_at, "aid": adjustment_id},
        )
        db.commit()
        return {
            "adjustment_id": adjustment_id,
            "adjustment_no": str(adj.adjustment_no),
            "status": "done",
            "moves_created": moves_created,
        }

    # ─── Reorder Rule Evaluation ──────────────────────────────────────────────

    @staticmethod
    def run_reorder_check(
        db: Session,
        entity_id: str,
        created_by: str = "system",
    ) -> list[dict]:
        """
        Periksa semua reorder_rule.
        Jika qty_available ≤ min_qty, buat draft PR di modul procurement.
        Return list produk yang trigger reorder.
        """
        alerts = db.execute(
            text("SELECT * FROM vw_low_stock_alert WHERE entity_id = :eid"),
            {"eid": entity_id},
        ).fetchall()

        triggered = []
        for alert in alerts:
            target_qty = Decimal(str(alert.target_qty))
            on_hand    = Decimal(str(alert.qty_available))
            order_qty  = target_qty - on_hand

            if order_qty <= 0:
                continue

            # Buat draft PR
            pr_id  = str(uuid.uuid4())
            today  = date.today()
            year, month = today.year, today.month

            row = db.execute(
                text(
                    "SELECT COUNT(*) AS cnt FROM purchase_request "
                    "WHERE pr_no LIKE :p"
                ),
                {"p": f"PR/{year}/{month:02d}/%"},
            ).fetchone()
            seq    = (row.cnt if row else 0) + 1
            pr_no  = f"PR/{year}/{month:02d}/{seq:04d}"

            db.execute(
                text(
                    "INSERT INTO purchase_request "
                    "(id, entity_id, pr_no, pr_date, requested_by, "
                    " description, status, created_by, created_at) "
                    "VALUES (:id, :eid, :pno, :dt, 'system', :desc, 'draft', :cby, NOW())"
                ),
                {
                    "id": pr_id, "eid": entity_id, "pno": pr_no,
                    "dt": today,
                    "desc": f"Auto-reorder: {alert.product_name} — stok {float(on_hand)} ≤ safety {float(alert.safety_stock)}",
                    "cby": created_by,
                },
            )

            pr_item_id = str(uuid.uuid4())
            db.execute(
                text(
                    "INSERT INTO pr_item "
                    "(id, pr_id, product_id, description, qty, uom_id, estimated_price, "
                    " vendor_id, created_at) "
                    "SELECT :id, :prid, :pid, :desc, :qty, p.uom_id, p.standard_price, :vid, NOW() "
                    "FROM product_product p WHERE p.id = :pid"
                ),
                {
                    "id": pr_item_id, "prid": pr_id,
                    "pid": str(alert.product_id),
                    "desc": f"Auto-reorder {alert.product_name}",
                    "qty": float(order_qty),
                    "vid": str(alert.vendor_id) if alert.vendor_id else None,
                },
            )

            triggered.append({
                "product_id": str(alert.product_id),
                "product_name": alert.product_name,
                "qty_available": float(on_hand),
                "safety_stock": float(alert.safety_stock),
                "order_qty": float(order_qty),
                "pr_id": pr_id,
                "pr_no": pr_no,
            })

        if triggered:
            db.commit()
        return triggered

    # ─── Stock Query Helpers ──────────────────────────────────────────────────

    @staticmethod
    def get_stock_summary(
        db: Session,
        entity_id: str,
        location_id: Optional[str] = None,
        product_id: Optional[str] = None,
    ) -> list[dict]:
        query = "SELECT * FROM vw_stock_summary WHERE entity_id = :eid"
        params: dict[str, Any] = {"eid": entity_id}
        if location_id:
            query += " AND location_id = :loc"
            params["loc"] = location_id
        if product_id:
            query += " AND product_id = :pid"
            params["pid"] = product_id
        rows = db.execute(text(query + " ORDER BY product_name"), params).fetchall()
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def get_stock_moves(
        db: Session,
        entity_id: str,
        product_id: Optional[str] = None,
        move_type: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        page: int = 1,
        size: int = 50,
    ) -> dict:
        filters = ["sm.entity_id = :eid", "sm.status = 'done'"]
        params: dict[str, Any] = {"eid": entity_id}

        if product_id:
            filters.append("sm.product_id = :pid")
            params["pid"] = product_id
        if move_type:
            filters.append("sm.move_type = :mt")
            params["mt"] = move_type
        if date_from:
            filters.append("sm.done_at >= :df")
            params["df"] = date_from
        if date_to:
            filters.append("sm.done_at < :dt")
            params["dt"] = date_to

        where = " AND ".join(filters)
        total = db.execute(
            text(f"SELECT COUNT(*) FROM stock_move sm WHERE {where}"), params
        ).scalar()

        params["offset"] = (page - 1) * size
        params["limit"]  = size
        rows = db.execute(
            text(
                f"SELECT sm.*, p.sku, p.product_name, "
                f"       src.location_name AS source_name, dst.location_name AS dest_name "
                f"FROM stock_move sm "
                f"JOIN product_product p ON p.id = sm.product_id "
                f"JOIN inventory_location src ON src.id = sm.source_location_id "
                f"JOIN inventory_location dst ON dst.id = sm.destination_location_id "
                f"WHERE {where} "
                f"ORDER BY sm.done_at DESC "
                f"LIMIT :limit OFFSET :offset"
            ),
            params,
        ).fetchall()

        return {
            "total": total,
            "page": page,
            "size": size,
            "items": [dict(r._mapping) for r in rows],
        }

    @staticmethod
    def get_fifo_layers(
        db: Session,
        entity_id: str,
        product_id: str,
        location_id: Optional[str] = None,
    ) -> list[dict]:
        query = (
            "SELECT svl.*, loc.location_name "
            "FROM stock_valuation_layer svl "
            "JOIN inventory_location loc ON loc.id = svl.location_id "
            "WHERE svl.entity_id = :eid AND svl.product_id = :pid AND svl.remaining_qty > 0"
        )
        params: dict[str, Any] = {"eid": entity_id, "pid": product_id}
        if location_id:
            query += " AND svl.location_id = :loc"
            params["loc"] = location_id
        query += " ORDER BY svl.created_at ASC"
        rows = db.execute(text(query), params).fetchall()
        return [dict(r._mapping) for r in rows]
