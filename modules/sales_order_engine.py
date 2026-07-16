"""
Sales Order Engine

Alur:
  1. create_quotation()       → Buat quotation (draft)
  2. confirm_quotation()      → Quotation → Sales Order (cek stok)
  3. create_so_direct()       → Buat SO langsung tanpa quotation
  4. confirm_so()             → SO confirmed → buat picking order
  5. validate_picking()       → Picking done → update qty_delivered + buat DO via InventoryEngine
  6. create_invoice_from_so() → SO delivered → buat AR Invoice
  7. get_so_detail()          → SO + lines + picking + DO status
  8. check_availability()     → cek stok tersedia untuk lines SO
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


class SalesOrderEngine:

    # ─────────────────────────────────────────────────────────────────────────
    # 1. CREATE QUOTATION
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def create_quotation(
        db: Session,
        entity_id: UUID,
        customer_id: UUID,
        quotation_date: date,
        valid_until: date,
        lines: list[dict],
        salesperson: str = None,
        currency: str = "IDR",
        exchange_rate: Decimal = Decimal("1"),
        notes: str = None,
        created_by: str = None,
    ) -> dict:
        """
        lines: [{product_id, qty, uom_id, unit_price, discount_pct, tax_rate, notes}]
        """
        if not lines:
            raise ValueError("Minimal 1 baris produk diperlukan.")

        seq = db.execute(
            text("""
                SELECT COUNT(*) + 1 AS seq FROM quotation
                WHERE entity_id=:eid AND EXTRACT(YEAR FROM quotation_date)=:yr
            """),
            {"eid": str(entity_id), "yr": quotation_date.year},
        ).fetchone().seq
        quot_no = f"QT-{quotation_date.strftime('%Y%m')}-{seq:04d}"

        row = db.execute(
            text("""
                INSERT INTO quotation (entity_id, quotation_no, customer_id,
                    quotation_date, valid_until, salesperson, currency, exchange_rate,
                    notes, status, created_by)
                VALUES (:eid, :no, :cust, :dt, :valid, :sales, :curr, :er, :notes, 'draft', :by)
                RETURNING id
            """),
            {
                "eid": str(entity_id), "no": quot_no, "cust": str(customer_id),
                "dt": quotation_date, "valid": valid_until, "sales": salesperson,
                "curr": currency, "er": float(exchange_rate), "notes": notes, "by": created_by,
            },
        ).fetchone()
        quot_id = str(row.id)

        subtotal = Decimal("0")
        tax_total = Decimal("0")

        for i, ln in enumerate(lines, start=1):
            qty      = Decimal(str(ln["qty"]))
            price    = Decimal(str(ln["unit_price"]))
            disc     = Decimal(str(ln.get("discount_pct", 0) or 0))
            tax_rate = Decimal(str(ln.get("tax_rate", 11) or 11))
            line_sub = qty * price * (1 - disc / 100)
            line_tax = line_sub * tax_rate / 100
            subtotal  += line_sub
            tax_total += line_tax

            db.execute(
                text("""
                    INSERT INTO quotation_line (
                        quotation_id, line_no, product_id, description,
                        qty, uom_id, unit_price, discount_pct, tax_rate, notes
                    ) VALUES (
                        :qid, :lno, :prod, :desc,
                        :qty, :uom, :price, :disc, :tax, :notes
                    )
                """),
                {
                    "qid":   quot_id, "lno":  i,
                    "prod":  str(ln["product_id"]),
                    "desc":  ln.get("description"),
                    "qty":   float(qty), "uom": str(ln["uom_id"]),
                    "price": float(price), "disc": float(disc),
                    "tax":   float(tax_rate), "notes": ln.get("notes"),
                },
            )

        total = subtotal + tax_total
        db.execute(
            text("""
                UPDATE quotation
                SET subtotal=:sub, tax_amount=:tax, total_amount=:total
                WHERE id=:id
            """),
            {"sub": float(subtotal), "tax": float(tax_total), "total": float(total), "id": quot_id},
        )
        db.commit()
        return {
            "quotation_id": quot_id,
            "quotation_no": quot_no,
            "subtotal":     float(subtotal),
            "tax_amount":   float(tax_total),
            "total_amount": float(total),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 2. CONFIRM QUOTATION → SO
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def confirm_quotation(
        db: Session,
        quotation_id: UUID,
        confirmed_by: str,
        requested_delivery_date: Optional[date] = None,
        warehouse_id: Optional[UUID] = None,
    ) -> dict:
        quot = db.execute(
            text("SELECT * FROM quotation WHERE id=:id"),
            {"id": str(quotation_id)},
        ).fetchone()
        if not quot:
            raise ValueError("Quotation tidak ditemukan.")
        if quot.status != "draft":
            raise ValueError(f"Quotation berstatus {quot.status}, hanya 'draft' yang bisa dikonfirmasi.")

        lines = db.execute(
            text("""
                SELECT ql.*, p.product_name
                FROM quotation_line ql
                JOIN product_product p ON p.id = ql.product_id
                WHERE ql.quotation_id = :qid ORDER BY ql.line_no
            """),
            {"qid": str(quotation_id)},
        ).fetchall()

        so = SalesOrderEngine._create_so_record(
            db,
            entity_id        = quot.entity_id,
            customer_id      = quot.customer_id,
            quotation_id     = quotation_id,
            so_date          = date.today(),
            currency         = quot.currency,
            exchange_rate    = quot.exchange_rate,
            subtotal         = quot.subtotal,
            discount_amount  = quot.discount_amount,
            tax_amount       = quot.tax_amount,
            total_amount     = quot.total_amount,
            salesperson      = quot.salesperson,
            notes            = quot.notes,
            warehouse_id     = warehouse_id,
            requested_delivery_date = requested_delivery_date,
            payment_term_days = db.execute(
                text("SELECT payment_term_days FROM customer WHERE id=:id"),
                {"id": str(quot.customer_id)},
            ).fetchone().payment_term_days,
            created_by       = confirmed_by,
        )
        so_id = so["so_id"]

        for i, ln in enumerate(lines, start=1):
            db.execute(
                text("""
                    INSERT INTO sales_order_line (
                        so_id, line_no, product_id, description,
                        qty_ordered, uom_id, unit_price, discount_pct, tax_rate, notes
                    ) VALUES (
                        :sid, :lno, :prod, :desc,
                        :qty, :uom, :price, :disc, :tax, :notes
                    )
                """),
                {
                    "sid":  so_id, "lno":  i,
                    "prod": str(ln.product_id),
                    "desc": ln.description,
                    "qty":  float(ln.qty), "uom": str(ln.uom_id),
                    "price": float(ln.unit_price), "disc": float(ln.discount_pct),
                    "tax":  float(ln.tax_rate), "notes": ln.notes,
                },
            )

        db.execute(
            text("UPDATE quotation SET status='confirmed', so_id=:sid WHERE id=:qid"),
            {"sid": so_id, "qid": str(quotation_id)},
        )
        db.commit()
        return {"so_id": so_id, "so_no": so["so_no"], "quotation_no": quot.quotation_no}

    # ─────────────────────────────────────────────────────────────────────────
    # 3. CREATE SO DIRECT
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def create_so_direct(
        db: Session,
        entity_id: UUID,
        customer_id: UUID,
        so_date: date,
        lines: list[dict],
        warehouse_id: Optional[UUID] = None,
        requested_delivery_date: Optional[date] = None,
        salesperson: str = None,
        currency: str = "IDR",
        exchange_rate: Decimal = Decimal("1"),
        notes: str = None,
        created_by: str = None,
    ) -> dict:
        if not lines:
            raise ValueError("Minimal 1 baris produk diperlukan.")

        subtotal = Decimal("0")
        tax_total = Decimal("0")
        for ln in lines:
            qty      = Decimal(str(ln["qty_ordered"]))
            price    = Decimal(str(ln["unit_price"]))
            disc     = Decimal(str(ln.get("discount_pct", 0) or 0))
            tax_rate = Decimal(str(ln.get("tax_rate", 11) or 11))
            line_sub = qty * price * (1 - disc / 100)
            subtotal  += line_sub
            tax_total += line_sub * tax_rate / 100

        cust = db.execute(
            text("SELECT payment_term_days FROM customer WHERE id=:id"),
            {"id": str(customer_id)},
        ).fetchone()
        if not cust:
            raise ValueError("Customer tidak ditemukan.")

        so = SalesOrderEngine._create_so_record(
            db,
            entity_id=entity_id, customer_id=customer_id,
            quotation_id=None, so_date=so_date,
            currency=currency, exchange_rate=exchange_rate,
            subtotal=float(subtotal), discount_amount=0,
            tax_amount=float(tax_total), total_amount=float(subtotal + tax_total),
            salesperson=salesperson, notes=notes, warehouse_id=warehouse_id,
            requested_delivery_date=requested_delivery_date,
            payment_term_days=cust.payment_term_days,
            created_by=created_by,
        )
        so_id = so["so_id"]

        for i, ln in enumerate(lines, start=1):
            db.execute(
                text("""
                    INSERT INTO sales_order_line (
                        so_id, line_no, product_id, description,
                        qty_ordered, uom_id, unit_price, discount_pct, tax_rate, notes, lot_id
                    ) VALUES (
                        :sid, :lno, :prod, :desc,
                        :qty, :uom, :price, :disc, :tax, :notes, :lot
                    )
                """),
                {
                    "sid":  so_id, "lno": i,
                    "prod": str(ln["product_id"]),
                    "desc": ln.get("description"),
                    "qty":  float(ln["qty_ordered"]),
                    "uom":  str(ln["uom_id"]),
                    "price": float(ln["unit_price"]),
                    "disc": float(ln.get("discount_pct", 0) or 0),
                    "tax":  float(ln.get("tax_rate", 11) or 11),
                    "notes": ln.get("notes"),
                    "lot":  str(ln["lot_id"]) if ln.get("lot_id") else None,
                },
            )
        db.commit()
        return {"so_id": so_id, "so_no": so["so_no"]}

    # ─────────────────────────────────────────────────────────────────────────
    # 4. CONFIRM SO → buat picking order
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def confirm_so(
        db: Session,
        so_id: UUID,
        confirmed_by: str,
    ) -> dict:
        """
        Konfirmasi SO:
          - Cek stok available untuk semua lines
          - Buat picking_order
          - Status SO → confirmed (picking akan dilakukan terpisah)
        """
        so = db.execute(
            text("SELECT * FROM sales_order WHERE id=:id"),
            {"id": str(so_id)},
        ).fetchone()
        if not so:
            raise ValueError("Sales Order tidak ditemukan.")
        if so.status != "draft":
            raise ValueError(f"SO berstatus {so.status}.")

        lines = db.execute(
            text("""
                SELECT sol.id, sol.product_id, sol.qty_ordered, sol.uom_id, sol.lot_id,
                       p.product_name, p.product_type
                FROM sales_order_line sol
                JOIN product_product p ON p.id = sol.product_id
                WHERE sol.so_id = :sid
            """),
            {"sid": str(so_id)},
        ).fetchall()

        warehouse_id = so.warehouse_id
        if not warehouse_id:
            # Ambil lokasi internal pertama
            wh = db.execute(
                text("""
                    SELECT id FROM inventory_location
                    WHERE entity_id=:eid AND location_type='internal' AND is_active=TRUE
                    ORDER BY created_at LIMIT 1
                """),
                {"eid": str(so.entity_id)},
            ).fetchone()
            if not wh:
                raise ValueError("Tidak ada warehouse/internal location yang dikonfigurasi.")
            warehouse_id = wh.id

        # Cek stok availability — hanya untuk product_type='storable' (yang ditrack di gudang).
        # Service/consumable (mis. jasa, sewa, subscription) tidak pernah punya stock_move, jadi
        # skip cek di sini sesuai BRD REQ-03 (non-stock item harus bypass validasi stok).
        insufficient = []
        for ln in lines:
            if ln.product_type != "storable":
                continue
            avail = db.execute(
                text("""
                    SELECT
                        COALESCE(SUM(CASE WHEN destination_location_id=:wh THEN qty_done ELSE 0 END), 0)
                        - COALESCE(SUM(CASE WHEN source_location_id=:wh THEN qty_done ELSE 0 END), 0)
                        AS qty_available
                    FROM stock_move
                    WHERE product_id=:prod AND status='done'
                      AND (destination_location_id=:wh OR source_location_id=:wh)
                """),
                {"prod": str(ln.product_id), "wh": str(warehouse_id)},
            ).fetchall()
            qty_avail = sum(float(r.qty_available or 0) for r in avail)
            if qty_avail < float(ln.qty_ordered):
                insufficient.append({
                    "product": ln.product_name,
                    "required": float(ln.qty_ordered),
                    "available": qty_avail,
                })

        if insufficient:
            raise ValueError(
                f"Stok tidak cukup untuk: "
                + "; ".join(f"{i['product']} (butuh {i['required']}, ada {i['available']})"
                            for i in insufficient)
            )

        # Buat picking order
        seq = db.execute(
            text("""
                SELECT COUNT(*) + 1 AS seq FROM picking_order
                WHERE entity_id=:eid AND EXTRACT(YEAR FROM picking_date)=:yr
            """),
            {"eid": str(so.entity_id), "yr": date.today().year},
        ).fetchone().seq
        picking_no = f"PK-{date.today().strftime('%Y%m')}-{seq:04d}"

        pk_row = db.execute(
            text("""
                INSERT INTO picking_order (entity_id, picking_no, so_id, warehouse_id, picking_date, status)
                VALUES (:eid, :no, :sid, :wh, CURRENT_DATE, 'draft')
                RETURNING id
            """),
            {
                "eid": str(so.entity_id), "no": picking_no,
                "sid": str(so_id), "wh": str(warehouse_id),
            },
        ).fetchone()

        for ln in lines:
            db.execute(
                text("""
                    INSERT INTO picking_order_line (
                        picking_id, so_line_id, product_id, lot_id,
                        qty_to_pick, qty_picked, uom_id
                    ) VALUES (:pk, :sol, :prod, :lot, :qty, 0, :uom)
                """),
                {
                    "pk":   str(pk_row.id),
                    "sol":  str(ln.id),
                    "prod": str(ln.product_id),
                    "lot":  str(ln.lot_id) if ln.lot_id else None,
                    "qty":  float(ln.qty_ordered),
                    "uom":  str(ln.uom_id),
                },
            )

        db.execute(
            text("""
                UPDATE sales_order
                SET status='confirmed', confirmed_by=:by, confirmed_at=NOW(),
                    warehouse_id=:wh
                WHERE id=:id
            """),
            {"by": confirmed_by, "wh": str(warehouse_id), "id": str(so_id)},
        )
        db.commit()
        return {
            "status":     "confirmed",
            "picking_id": str(pk_row.id),
            "picking_no": picking_no,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 5. VALIDATE PICKING → buat DO + update qty_delivered
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def validate_picking(
        db: Session,
        picking_id: UUID,
        picked_by: str,
        qty_picked_overrides: Optional[dict[str, float]] = None,
    ) -> dict:
        """
        Selesaikan picking:
          - Update qty_picked per baris (default = qty_to_pick)
          - Buat delivery_order via INSERT (InventoryEngine.validate_delivery_order dipanggil terpisah)
          - Update sales_order_line.qty_delivered
          - SO status → ready jika semua terpenuhi, picking jika partial
        """
        pk = db.execute(
            text("SELECT * FROM picking_order WHERE id=:id"),
            {"id": str(picking_id)},
        ).fetchone()
        if not pk:
            raise ValueError("Picking order tidak ditemukan.")
        if pk.status == "done":
            raise ValueError("Picking sudah selesai.")

        pk_lines = db.execute(
            text("""
                SELECT pol.id, pol.so_line_id, pol.product_id, pol.lot_id,
                       pol.qty_to_pick, pol.uom_id
                FROM picking_order_line pol
                WHERE pol.picking_id = :pk
            """),
            {"pk": str(picking_id)},
        ).fetchall()

        so = db.execute(
            text("SELECT * FROM sales_order WHERE id=:id"),
            {"id": str(pk.so_id)},
        ).fetchone()

        # Buat DO header
        seq = db.execute(
            text("""
                SELECT COUNT(*) + 1 AS seq FROM delivery_order
                WHERE entity_id=:eid AND EXTRACT(YEAR FROM do_date)=:yr
            """),
            {"eid": str(so.entity_id), "yr": date.today().year},
        ).fetchone().seq
        do_no = f"DO-{date.today().strftime('%Y%m')}-{seq:04d}"

        cust = db.execute(
            text("SELECT customer_name, address FROM customer WHERE id=:id"),
            {"id": str(so.customer_id)},
        ).fetchone()

        do_row = db.execute(
            text("""
                INSERT INTO delivery_order (
                    entity_id, do_no, customer_id, customer_name,
                    source_location_id, so_reference, delivery_address, do_date, status
                ) VALUES (
                    :eid, :no, :cid, :cname, :src, :soref, :addr, CURRENT_DATE, 'draft'
                ) RETURNING id
            """),
            {
                "eid":   str(so.entity_id),
                "no":    do_no,
                "cid":   str(so.customer_id),
                "cname": cust.customer_name if cust else "",
                "src":   str(pk.warehouse_id),
                "soref": so.so_no,
                "addr":  cust.address if cust else None,
            },
        ).fetchone()
        do_id = str(do_row.id)

        for line_no, pl in enumerate(pk_lines, start=1):
            override_qty = (qty_picked_overrides or {}).get(str(pl.id))
            qty_done = float(override_qty if override_qty is not None else pl.qty_to_pick)

            db.execute(
                text("UPDATE picking_order_line SET qty_picked=:qty WHERE id=:id"),
                {"qty": qty_done, "id": str(pl.id)},
            )
            db.execute(
                text("""
                    UPDATE sales_order_line
                    SET qty_delivered = qty_delivered + :qty
                    WHERE id = :id
                """),
                {"qty": qty_done, "id": str(pl.so_line_id)},
            )
            db.execute(
                text("""
                    INSERT INTO delivery_order_line (
                        do_id, line_no, product_id, lot_id, qty, uom_id
                    ) VALUES (:do, :lno, :prod, :lot, :qty, :uom)
                """),
                {
                    "do":   do_id,
                    "lno":  line_no,
                    "prod": str(pl.product_id),
                    "lot":  str(pl.lot_id) if pl.lot_id else None,
                    "qty":  qty_done,
                    "uom":  str(pl.uom_id),
                },
            )

        db.execute(
            text("UPDATE picking_order SET status='done', picked_by=:by, picked_at=NOW() WHERE id=:id"),
            {"by": picked_by, "id": str(picking_id)},
        )
        db.execute(
            text("UPDATE sales_order SET status='ready' WHERE id=:id"),
            {"id": str(so.id)},
        )
        db.commit()
        return {"status": "done", "do_id": do_id, "do_no": do_no}

    # ─────────────────────────────────────────────────────────────────────────
    # 6. CREATE AR INVOICE FROM SO
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def create_invoice_from_so(
        db: Session,
        so_id: UUID,
        invoice_date: date,
        created_by: str,
        invoice_type: str = "delivered",  # 'ordered' = invoice seluruh PO, 'delivered' = sesuai DO
    ) -> dict:
        so = db.execute(
            text("SELECT * FROM sales_order WHERE id=:id"),
            {"id": str(so_id)},
        ).fetchone()
        if not so:
            raise ValueError("Sales Order tidak ditemukan.")
        if so.status not in ("ready", "delivered"):
            raise ValueError(f"SO berstatus {so.status}. Harus 'ready' atau 'delivered' untuk invoicing.")

        cust = db.execute(
            text("SELECT * FROM customer WHERE id=:id"),
            {"id": str(so.customer_id)},
        ).fetchone()

        lines = db.execute(
            text("""
                SELECT sol.id, sol.product_id, sol.description,
                       sol.qty_ordered, sol.qty_delivered, sol.qty_invoiced,
                       sol.unit_price, sol.discount_pct, sol.tax_rate, sol.uom_id,
                       p.product_name
                FROM sales_order_line sol
                JOIN product_product p ON p.id = sol.product_id
                WHERE sol.so_id = :sid
            """),
            {"sid": str(so_id)},
        ).fetchall()

        # AR Invoice sequence
        seq = db.execute(
            text("""
                SELECT COUNT(*) + 1 AS seq FROM ar_invoice
                WHERE entity_id=:eid AND EXTRACT(YEAR FROM invoice_date)=:yr
            """),
            {"eid": str(so.entity_id), "yr": invoice_date.year},
        ).fetchone().seq
        inv_no = f"INV-{invoice_date.strftime('%Y%m')}-{seq:04d}"

        due_date = date(
            invoice_date.year,
            invoice_date.month + (so.payment_term_days // 30),
            invoice_date.day,
        ) if invoice_date.month + (so.payment_term_days // 30) <= 12 else date(
            invoice_date.year + 1,
            (invoice_date.month + (so.payment_term_days // 30)) % 12 or 12,
            invoice_date.day,
        )

        subtotal   = Decimal("0")
        tax_amount = Decimal("0")
        for ln in lines:
            qty  = Decimal(str(ln.qty_delivered if invoice_type == "delivered" else ln.qty_ordered))
            qty -= Decimal(str(ln.qty_invoiced))
            if qty <= 0:
                continue
            price = Decimal(str(ln.unit_price))
            disc  = Decimal(str(ln.discount_pct))
            line_sub = qty * price * (1 - disc / 100)
            subtotal   += line_sub
            tax_amount += line_sub * Decimal(str(ln.tax_rate)) / 100

        if subtotal <= 0:
            raise ValueError("Tidak ada qty yang belum diinvoice.")

        total = subtotal + tax_amount

        inv_row = db.execute(
            text("""
                INSERT INTO ar_invoice (
                    entity_id, invoice_no, customer_name, customer_npwp,
                    invoice_date, due_date,
                    subtotal, ppn_amount, total_amount,
                    status, contract_ref, generated_by
                ) VALUES (
                    :eid, :no, :cname, :npwp,
                    :dt, :due,
                    :sub, :tax, :total,
                    'draft', :soref, 'manual'
                ) RETURNING id
            """),
            {
                "eid":   str(so.entity_id),
                "no":    inv_no,
                "cname": cust.customer_name if cust else "",
                "npwp":  cust.npwp if cust else None,
                "dt":    invoice_date,
                "due":   due_date,
                "sub":   float(subtotal),
                "tax":   float(tax_amount),
                "total": float(total),
                "soref": so.so_no,
            },
        ).fetchone()
        inv_id = str(inv_row.id)

        # Update qty_invoiced
        for ln in lines:
            qty = Decimal(str(ln.qty_delivered if invoice_type == "delivered" else ln.qty_ordered))
            qty -= Decimal(str(ln.qty_invoiced))
            if qty <= 0:
                continue
            db.execute(
                text("UPDATE sales_order_line SET qty_invoiced=qty_invoiced+:qty WHERE id=:id"),
                {"qty": float(qty), "id": str(ln.id)},
            )

        db.execute(
            text("UPDATE sales_order SET status='invoiced' WHERE id=:id"),
            {"id": str(so_id)},
        )
        db.commit()
        return {
            "invoice_id":  inv_id,
            "invoice_no":  inv_no,
            "subtotal":    float(subtotal),
            "tax_amount":  float(tax_amount),
            "total_amount": float(total),
            "due_date":    due_date.isoformat(),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 7. GET SO DETAIL
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_so_detail(db: Session, so_id: UUID) -> dict:
        so = db.execute(
            text("""
                SELECT so.*, c.customer_name, c.npwp, c.phone, c.email
                FROM sales_order so
                JOIN customer c ON c.id = so.customer_id
                WHERE so.id=:id
            """),
            {"id": str(so_id)},
        ).fetchone()
        if not so:
            raise ValueError("Sales Order tidak ditemukan.")

        lines = db.execute(
            text("""
                SELECT sol.*, p.product_name, p.product_code, u.uom_name
                FROM sales_order_line sol
                JOIN product_product p ON p.id = sol.product_id
                JOIN product_uom u     ON u.id = sol.uom_id
                WHERE sol.so_id=:sid ORDER BY sol.line_no
            """),
            {"sid": str(so_id)},
        ).fetchall()

        pickings = db.execute(
            text("""
                SELECT id, picking_no, status, picking_date, picked_by
                FROM picking_order WHERE so_id=:sid
            """),
            {"sid": str(so_id)},
        ).fetchall()

        dos = db.execute(
            text("""
                SELECT id, do_no, status, do_date
                FROM delivery_order WHERE so_reference=:so_no
            """),
            {"so_no": so.so_no},
        ).fetchall()

        return {
            "so":       dict(so._mapping),
            "lines":    [dict(r._mapping) for r in lines],
            "pickings": [dict(r._mapping) for r in pickings],
            "delivery_orders": [dict(r._mapping) for r in dos],
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 8. CHECK AVAILABILITY
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def check_availability(
        db: Session,
        entity_id: UUID,
        lines: list[dict],
        warehouse_id: Optional[UUID] = None,
    ) -> list[dict]:
        """Cek ketersediaan stok sebelum SO dikonfirmasi."""
        result = []
        for ln in lines:
            product_id = str(ln["product_id"])
            qty_required = float(ln["qty"])

            product_type = db.execute(
                text("SELECT product_type FROM product_product WHERE id=:pid"),
                {"pid": product_id},
            ).scalar()
            if product_type != "storable":
                # Service/consumable tidak ditrack di gudang — selalu anggap cukup (REQ-03).
                result.append({
                    "product_id":    product_id,
                    "qty_required":  qty_required,
                    "qty_available": qty_required,
                    "sufficient":    True,
                    "shortage":      0.0,
                })
                continue

            filters = "AND sm.entity_id=:eid"
            params: dict = {"prod": product_id, "eid": str(entity_id)}
            if warehouse_id:
                filters += " AND (sm.destination_location_id=:wh OR sm.source_location_id=:wh)"
                params["wh"] = str(warehouse_id)

            row = db.execute(
                text(f"""
                    SELECT
                        COALESCE(SUM(CASE WHEN destination_location_id = wh.id THEN qty_done ELSE 0 END), 0)
                        - COALESCE(SUM(CASE WHEN source_location_id = wh.id THEN qty_done ELSE 0 END), 0)
                        AS qty_available
                    FROM stock_move sm
                    CROSS JOIN (
                        SELECT id FROM inventory_location
                        WHERE entity_id=:eid AND location_type='internal' AND is_active=TRUE
                        ORDER BY created_at LIMIT 1
                    ) wh
                    WHERE sm.product_id=:prod AND sm.status='done' {filters}
                """),
                params,
            ).fetchone()

            qty_avail = float(row.qty_available or 0) if row else 0.0
            result.append({
                "product_id":    product_id,
                "qty_required":  qty_required,
                "qty_available": qty_avail,
                "sufficient":    qty_avail >= qty_required,
                "shortage":      max(0.0, qty_required - qty_avail),
            })
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # INTERNAL HELPER
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _create_so_record(db, entity_id, customer_id, quotation_id, so_date,
                           currency, exchange_rate, subtotal, discount_amount,
                           tax_amount, total_amount, salesperson, notes, warehouse_id,
                           requested_delivery_date, payment_term_days, created_by) -> dict:
        seq = db.execute(
            text("""
                SELECT COUNT(*) + 1 AS seq FROM sales_order
                WHERE entity_id=:eid AND EXTRACT(YEAR FROM so_date)=:yr
            """),
            {"eid": str(entity_id), "yr": so_date.year},
        ).fetchone().seq
        so_no = f"SO-{so_date.strftime('%Y%m')}-{seq:04d}"

        row = db.execute(
            text("""
                INSERT INTO sales_order (
                    entity_id, so_no, customer_id, quotation_id, so_date,
                    currency, exchange_rate, subtotal, discount_amount,
                    tax_amount, total_amount, salesperson, notes, warehouse_id,
                    requested_delivery_date, payment_term_days, status, created_by
                ) VALUES (
                    :eid, :no, :cust, :quot, :dt,
                    :curr, :er, :sub, :disc,
                    :tax, :total, :sales, :notes, :wh,
                    :rdd, :top, 'draft', :by
                ) RETURNING id
            """),
            {
                "eid":   str(entity_id), "no":   so_no,
                "cust":  str(customer_id),
                "quot":  str(quotation_id) if quotation_id else None,
                "dt":    so_date, "curr": currency, "er": float(exchange_rate),
                "sub":   float(subtotal), "disc": float(discount_amount),
                "tax":   float(tax_amount), "total": float(total_amount),
                "sales": salesperson, "notes": notes,
                "wh":    str(warehouse_id) if warehouse_id else None,
                "rdd":   requested_delivery_date,
                "top":   payment_term_days, "by": created_by,
            },
        ).fetchone()
        return {"so_id": str(row.id), "so_no": so_no}
