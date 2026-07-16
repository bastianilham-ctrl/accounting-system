# modules/ar_router.py
# AR Module: invoice ke customer, penerimaan pembayaran, aging report

from uuid import UUID, uuid4
from decimal import Decimal
from datetime import date, datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger

from core.database import get_db
from modules.journal_engine import JournalEngine, JournalEntry, JournalLine
from modules.revaluation_engine import RevaluationEngine

router = APIRouter(prefix="/ar", tags=["AR — Accounts Receivable"])


# ----------------------------------------------------------
# REQUEST MODELS
# ----------------------------------------------------------

class ARInvoiceLineRequest(BaseModel):
    account_code: str
    description: Optional[str] = None
    amount: Decimal
    cost_center: Optional[str] = None
    project_id: Optional[UUID] = None
    amount_fcy: Optional[Decimal] = None  # nominal asli dalam mata uang invoice, jika currency != IDR


class ARInvoiceRequest(BaseModel):
    entity_id: UUID
    customer_name: str
    customer_npwp: Optional[str] = None
    invoice_no: str
    invoice_date: date
    due_date: Optional[date] = None
    subtotal: Optional[Decimal] = None  # computed from lines kalau lines diisi
    ppn_rate: int = 11              # 0 atau 11 (%)
    contract_ref: Optional[str] = None
    cost_center: Optional[str] = None
    project_id: Optional[UUID] = None
    lines: Optional[List[ARInvoiceLineRequest]] = None  # rincian pendapatan per baris (account+cost_center+project)
    created_by: str = "system"
    currency: str = "IDR"                  # mata uang invoice — subtotal/ppn/total tetap dalam IDR
    exchange_rate: Decimal = Decimal("1")  # kurs pembukuan, wajib jika currency != IDR
    amount_fcy: Optional[Decimal] = None   # fallback total FCY jika tidak ada lines (mode single-line)


class ARReceiptRequest(BaseModel):
    entity_id: UUID
    invoice_id: UUID
    receipt_date: date
    amount: Decimal                 # kas yang diterima dari customer
    bank_account: str = "1-1-001"  # COA Kas/Bank
    reference_no: Optional[str] = None
    pph_withheld: Decimal = Decimal("0")  # PPh 23 yang dipotong customer
    pph_type: str = "PPh23"
    received_by: str = "system"
    amount_fcy: Optional[Decimal] = None    # nominal FCY yang dilunasi, wajib jika invoice currency != IDR
    payment_rate: Optional[Decimal] = None  # kurs tanggal penerimaan, wajib jika invoice currency != IDR


# ----------------------------------------------------------
# AR INVOICE ENDPOINTS
# ----------------------------------------------------------

@router.post("/invoices")
def create_ar_invoice(req: ARInvoiceRequest, db: Session = Depends(get_db)):
    """
    Buat invoice AR (tagihan ke customer).
    Status awal: draft — belum ada jurnal sampai /post-journal dipanggil.

    Kalau `lines` diisi, subtotal dihitung dari SUM(lines.amount) dan tiap baris
    disimpan ke `ar_invoice_line` (account_code + cost_center + project_id per baris).
    Kalau tidak, perilaku lama tetap dipakai (subtotal header + cost_center/project_id header).
    """
    currency = (req.currency or "IDR").upper()
    if currency == "IDR":
        exchange_rate = Decimal("1")
    else:
        if req.exchange_rate is None or req.exchange_rate <= 0:
            raise HTTPException(400, "exchange_rate wajib diisi (> 0) untuk invoice mata uang asing")
        exchange_rate = req.exchange_rate

    has_lines = bool(req.lines)
    if has_lines:
        subtotal = sum(l.amount for l in req.lines)
        cc, pid = None, None
        lines_fcy  = [l.amount_fcy for l in req.lines if l.amount_fcy is not None]
        amount_fcy = sum(lines_fcy, Decimal("0")) if len(lines_fcy) == len(req.lines) else req.amount_fcy
    else:
        if req.subtotal is None:
            raise HTTPException(400, "subtotal wajib diisi kalau lines tidak diisi")
        subtotal = req.subtotal
        cc, pid = req.cost_center, req.project_id
        amount_fcy = req.amount_fcy

    if currency != "IDR" and amount_fcy is None:
        raise HTTPException(400, "amount_fcy wajib diisi untuk invoice mata uang asing")

    ppn_amount   = (subtotal * Decimal(str(req.ppn_rate)) / 100).quantize(Decimal("1"))
    total_amount = subtotal + ppn_amount

    invoice_id = uuid4()
    db.execute(
        text("""
            INSERT INTO ar_invoice
                (id, entity_id, customer_name, customer_npwp, invoice_no,
                 invoice_date, due_date, subtotal, ppn_amount, total_amount,
                 paid_amount, status, contract_ref, generated_by,
                 cost_center, project_id, currency, exchange_rate, amount_fcy)
            VALUES
                (:id, :eid, :cname, :cnpwp, :inv_no,
                 :inv_date, :due_date, :sub, :ppn, :total,
                 0, 'draft', :cref, 'manual',
                 :cc, :pid, :cur, :er, :fcy)
        """),
        {
            "id": str(invoice_id), "eid": str(req.entity_id),
            "cname": req.customer_name, "cnpwp": req.customer_npwp,
            "inv_no": req.invoice_no,
            "inv_date": req.invoice_date, "due_date": req.due_date,
            "sub": float(subtotal), "ppn": float(ppn_amount),
            "total": float(total_amount), "cref": req.contract_ref,
            "cc": cc,
            "pid": str(pid) if pid else None,
            "cur": currency, "er": float(exchange_rate),
            "fcy": float(amount_fcy) if amount_fcy is not None else None,
        }
    )

    if has_lines:
        for i, line in enumerate(req.lines, start=1):
            db.execute(
                text("""
                    INSERT INTO ar_invoice_line
                        (ar_invoice_id, line_no, account_code, description, amount, cost_center, project_id, amount_fcy)
                    VALUES
                        (:inv_id, :line_no, :acc, :desc, :amt, :cc, :pid, :amtfcy)
                """),
                {
                    "inv_id": str(invoice_id), "line_no": i,
                    "acc": line.account_code, "desc": line.description,
                    "amt": float(line.amount), "cc": line.cost_center,
                    "pid": str(line.project_id) if line.project_id else None,
                    "amtfcy": float(line.amount_fcy) if line.amount_fcy is not None else None,
                }
            )

    db.commit()
    logger.info(f"AR invoice dibuat: {req.invoice_no} | {req.customer_name} | Rp {total_amount:,.0f}")
    return {
        "success":      True,
        "invoice_id":   str(invoice_id),
        "invoice_no":   req.invoice_no,
        "subtotal":     float(subtotal),
        "ppn_amount":   float(ppn_amount),
        "total_amount": float(total_amount),
        "line_count":   len(req.lines) if has_lines else 0,
        "status":       "draft",
        "currency":      currency,
        "exchange_rate": float(exchange_rate),
        "amount_fcy":    float(amount_fcy) if amount_fcy is not None else None,
    }


@router.get("/invoices/{invoice_id}/lines")
def get_ar_invoice_lines(invoice_id: UUID, db: Session = Depends(get_db)):
    """List rincian pendapatan (baris) suatu AR invoice."""
    rows = db.execute(
        text("""
            SELECT id, line_no, account_code, description, amount, cost_center, project_id, amount_fcy
            FROM ar_invoice_line
            WHERE ar_invoice_id = :id
            ORDER BY line_no
        """),
        {"id": str(invoice_id)}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/invoices/{invoice_id}/post-journal")
def post_ar_journal(
    invoice_id: UUID,
    coa_revenue: str = "4-1-001",
    created_by: str = "system",
    db: Session = Depends(get_db),
):
    """
    Posting jurnal untuk AR invoice yang sudah dibuat.

    Dr. Piutang Usaha (1-1-002)    subtotal + PPN
      Cr. Pendapatan Jasa          subtotal
      Cr. PPN Keluaran (2-2-001)   ppn_amount  [jika ada]
    """
    row = db.execute(
        text("SELECT * FROM ar_invoice WHERE id = :id AND status = 'draft'"),
        {"id": str(invoice_id)}
    ).fetchone()
    if not row:
        raise HTTPException(404, "Invoice tidak ditemukan atau sudah diposting")

    inv        = dict(row._mapping)
    subtotal   = Decimal(str(inv["subtotal"]))
    ppn_amount = Decimal(str(inv["ppn_amount"]))
    total      = Decimal(str(inv["total_amount"]))

    cc  = inv.get("cost_center")
    pid = inv.get("project_id")
    pc  = str(pid) if pid else None

    line_rows = db.execute(
        text("SELECT * FROM ar_invoice_line WHERE ar_invoice_id = :id ORDER BY line_no"),
        {"id": str(invoice_id)}
    ).fetchall()

    invoice_currency = inv.get("currency") or "IDR"
    receivable_line_kwargs = {}
    if invoice_currency != "IDR":
        invoice_rate = Decimal(str(inv["exchange_rate"]))
        receivable_line_kwargs = {
            "currency": invoice_currency,
            "exchange_rate": invoice_rate,
            # Nilai FCY dari saldo Piutang Usaha (subtotal+ppn), dikonversi balik
            # dari `total` IDR supaya konsisten dengan pembulatan PPN.
            "amount_fcy": (total / invoice_rate).quantize(Decimal("0.01")),
        }

    lines = [
        JournalLine(
            account_code="1-1-002",     # Piutang Usaha
            description=f"AR Invoice {inv['invoice_no']} — {inv['customer_name']}",
            debit_idr=total,
            cost_center=cc,
            project_code=pc,
            **receivable_line_kwargs,
        ),
    ]
    if line_rows:
        for r in line_rows:
            ln = dict(r._mapping)
            lines.append(JournalLine(
                account_code=ln["account_code"],
                description=ln["description"] or f"Pendapatan Invoice {inv['invoice_no']}",
                credit_idr=Decimal(str(ln["amount"])),
                cost_center=ln["cost_center"],
                project_code=str(ln["project_id"]) if ln["project_id"] else None,
            ))
    else:
        lines.append(JournalLine(
            account_code=coa_revenue,   # Pendapatan Jasa
            description=f"Pendapatan Invoice {inv['invoice_no']}",
            credit_idr=subtotal,
            cost_center=cc,
            project_code=pc,
        ))
    if ppn_amount > 0:
        lines.append(JournalLine(
            account_code="2-2-001",     # PPN Keluaran
            description=f"PPN Keluaran Invoice {inv['invoice_no']}",
            credit_idr=ppn_amount,
            cost_center=cc,
            project_code=pc,
        ))

    engine = JournalEngine(db)
    entry  = JournalEntry(
        entity_id=inv["entity_id"],
        journal_type="AR",
        journal_date=inv["invoice_date"],
        description=f"AR Invoice {inv['invoice_no']} — {inv['customer_name']}",
        lines=lines,
        reference_no=inv["invoice_no"],
        source="manual",
        created_by=created_by,
    )
    result = engine.post_journal(entry)
    if not result["success"]:
        raise HTTPException(400, result["error"])

    db.execute(
        text("UPDATE ar_invoice SET status = 'approved', journal_id = :jid WHERE id = :id"),
        {"jid": result["journal_id"], "id": str(invoice_id)}
    )
    db.commit()
    return result


@router.get("/invoices")
def list_ar_invoices(
    entity_id: str,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List AR invoice per entity, opsional filter by status."""
    query = """
        SELECT
            id, invoice_no, invoice_date, due_date,
            customer_name, customer_npwp,
            subtotal, ppn_amount, total_amount, paid_amount,
            (total_amount - paid_amount) AS outstanding,
            status, contract_ref, cost_center, project_id,
            currency, exchange_rate, amount_fcy,
            (SELECT COUNT(*) FROM ar_invoice_line WHERE ar_invoice_id = ar_invoice.id) AS line_count
        FROM ar_invoice
        WHERE entity_id = :eid
    """
    params: dict = {"eid": entity_id}
    if status:
        query += " AND status = :status"
        params["status"] = status
    query += " ORDER BY invoice_date DESC"
    rows = db.execute(text(query), params).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/aging/{entity_id}")
def ar_aging(entity_id: str, db: Session = Depends(get_db)):
    """AR aging per customer — grouping outstanding ke bucket 0/1-30/31-60/61-90/>90 hari."""
    rows = db.execute(
        text("""
            SELECT
                customer_name,
                customer_npwp,
                COUNT(*)                                                           AS invoice_count,
                SUM(total_amount - paid_amount)                                    AS total_outstanding,
                SUM(CASE WHEN (CURRENT_DATE - due_date) <= 0
                         THEN total_amount - paid_amount ELSE 0 END)               AS current_amount,
                SUM(CASE WHEN (CURRENT_DATE - due_date) BETWEEN 1  AND 30
                         THEN total_amount - paid_amount ELSE 0 END)               AS days_1_30,
                SUM(CASE WHEN (CURRENT_DATE - due_date) BETWEEN 31 AND 60
                         THEN total_amount - paid_amount ELSE 0 END)               AS days_31_60,
                SUM(CASE WHEN (CURRENT_DATE - due_date) BETWEEN 61 AND 90
                         THEN total_amount - paid_amount ELSE 0 END)               AS days_61_90,
                SUM(CASE WHEN (CURRENT_DATE - due_date) > 90
                         THEN total_amount - paid_amount ELSE 0 END)               AS over_90
            FROM ar_invoice
            WHERE entity_id = :eid
              AND status NOT IN ('paid', 'cancelled')
            GROUP BY customer_name, customer_npwp
            ORDER BY total_outstanding DESC
        """),
        {"eid": entity_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ----------------------------------------------------------
# AR RECEIPT ENDPOINTS
# ----------------------------------------------------------

@router.post("/receipts")
def record_ar_receipt(req: ARReceiptRequest, db: Session = Depends(get_db)):
    """
    Record penerimaan pembayaran dari customer.

    Dr. Kas/Bank (bank_account)        amount
    Dr. Beban PPh Dipotong (6-4-001)   pph_withheld  [jika customer potong PPh]
      Cr. Piutang Usaha (1-1-002)      amount + pph_withheld
    """
    invoice = db.execute(
        text("""
            SELECT * FROM ar_invoice
            WHERE id = :id AND status NOT IN ('cancelled', 'paid')
        """),
        {"id": str(req.invoice_id)}
    ).fetchone()
    if not invoice:
        raise HTTPException(404, "Invoice tidak ditemukan atau sudah lunas/dibatalkan")

    inv         = dict(invoice._mapping)
    invoice_currency = inv.get("currency") or "IDR"
    outstanding = Decimal(str(inv["total_amount"])) - Decimal(str(inv["paid_amount"]))

    realized_gl_result = None
    if invoice_currency != "IDR":
        if req.amount_fcy is None or req.payment_rate is None or req.payment_rate <= 0:
            raise HTTPException(
                400,
                f"Invoice ini dalam mata uang {invoice_currency} — amount_fcy dan payment_rate wajib diisi."
            )
        invoice_rate = Decimal(str(inv["exchange_rate"]))
        # Piutang dilunasi pada kurs pembukuan (booking rate) — dipakai untuk
        # paid_amount supaya outstanding tidak terdistorsi fluktuasi kurs harian.
        receivable_leg_idr = (req.amount_fcy * invoice_rate).quantize(Decimal("1"))
        # Kas riil yang masuk ke bank dihitung pada kurs hari penerimaan.
        bank_leg_idr = (req.amount_fcy * req.payment_rate).quantize(Decimal("1"))

        realized_gl_result = RevaluationEngine.compute_realized_gain_loss(
            db,
            entity_id=str(req.entity_id),
            invoice_type="ar",
            invoice_currency=invoice_currency,
            invoice_amount_fcy=req.amount_fcy,
            invoice_rate=invoice_rate,
            payment_rate=req.payment_rate,
        )
    else:
        receivable_leg_idr = req.amount
        bank_leg_idr = req.amount

    total_clear = receivable_leg_idr + req.pph_withheld

    if total_clear > outstanding + Decimal("1"):
        raise HTTPException(
            400,
            f"Total pembayaran + PPh (Rp {total_clear:,.0f}) "
            f"melebihi outstanding Rp {outstanding:,.0f}"
        )

    lines = [
        JournalLine(
            account_code=req.bank_account,
            description=f"Terima pembayaran AR {inv['invoice_no']} — {inv['customer_name']}",
            debit_idr=bank_leg_idr,
        ),
    ]
    if req.pph_withheld > 0:
        lines.append(JournalLine(
            account_code="6-4-001",     # Beban PPh Dipotong Customer
            description=f"{req.pph_type} dipotong customer {inv['customer_name']}",
            debit_idr=req.pph_withheld,
        ))
    # Tag baris pelunasan dengan FCY yang sama seperti baris invoice supaya saldo FCY
    # akun piutang bersih ke nol setelah lunas (dipakai vw_fcy_exposure/revaluasi).
    settle_line_kwargs = {}
    if invoice_currency != "IDR":
        settle_line_kwargs = {
            "currency": invoice_currency,
            "exchange_rate": invoice_rate,
            "amount_fcy": req.amount_fcy,
        }
    lines.append(JournalLine(
        account_code="1-1-002",         # Piutang Usaha
        description=f"Pelunasan AR {inv['invoice_no']} — {inv['customer_name']}",
        credit_idr=total_clear,
        **settle_line_kwargs,
    ))
    if realized_gl_result:
        lines.append(JournalLine(
            account_code=realized_gl_result["account_code"],
            description=realized_gl_result["description"],
            debit_idr=Decimal("0") if realized_gl_result["is_gain"] else realized_gl_result["abs_amount"],
            credit_idr=realized_gl_result["abs_amount"] if realized_gl_result["is_gain"] else Decimal("0"),
        ))

    engine = JournalEngine(db)
    entry  = JournalEntry(
        entity_id=req.entity_id,
        journal_type="AR",
        journal_date=req.receipt_date,
        description=f"Terima pembayaran AR {inv['invoice_no']} — {inv['customer_name']}",
        lines=lines,
        reference_no=req.reference_no or inv["invoice_no"],
        source="manual",
        created_by=req.received_by,
    )
    result = engine.post_journal(entry)
    if not result["success"]:
        raise HTTPException(400, result["error"])

    if realized_gl_result:
        RevaluationEngine.mark_realized_gl_journal(db, "ar", str(req.invoice_id), result["journal_id"])

    # Simpan ke ar_receipt
    receipt_id = uuid4()
    db.execute(
        text("""
            INSERT INTO ar_receipt
                (id, entity_id, invoice_id, receipt_date, amount,
                 bank_account, reference_no, journal_id, currency, amount_fcy,
                 payment_rate, realized_gl)
            VALUES
                (:id, :eid, :inv_id, :rdate, :amt,
                 :bank, :ref, :jid, :cur, :fcy,
                 :prate, :rgl)
        """),
        {
            "id": str(receipt_id), "eid": str(req.entity_id),
            "inv_id": str(req.invoice_id), "rdate": req.receipt_date,
            "amt": float(bank_leg_idr), "bank": req.bank_account,
            "ref": req.reference_no, "jid": result["journal_id"],
            "cur": invoice_currency,
            "fcy": float(req.amount_fcy) if req.amount_fcy is not None else None,
            "prate": float(req.payment_rate) if req.payment_rate is not None else None,
            "rgl": float(realized_gl_result["realized_gl"]) if realized_gl_result else None,
        }
    )

    new_paid   = Decimal(str(inv["paid_amount"])) + total_clear
    new_status = "paid" if new_paid >= outstanding - Decimal("1") else "partial"
    db.execute(
        text("""
            UPDATE ar_invoice
            SET paid_amount = :paid, status = :status
            WHERE id = :id
        """),
        {"paid": float(new_paid), "status": new_status, "id": str(req.invoice_id)}
    )
    db.commit()

    logger.info(
        f"AR receipt: {inv['invoice_no']} | terima Rp {receivable_leg_idr:,.0f} "
        f"+ PPh Rp {req.pph_withheld:,.0f} | status: {new_status}"
    )
    return {
        "success":       True,
        "receipt_id":    str(receipt_id),
        "journal_no":    result["journal_no"],
        "invoice_status": new_status,
        "amount_received": float(receivable_leg_idr),
        "pph_withheld":  float(req.pph_withheld),
        "total_cleared": float(total_clear),
        "remaining":     float(outstanding - total_clear),
        "realized_gl":   float(realized_gl_result["realized_gl"]) if realized_gl_result else None,
        "realized_gl_is_gain": realized_gl_result["is_gain"] if realized_gl_result else None,
    }


@router.get("/receipts/{entity_id}")
def list_ar_receipts(entity_id: str, db: Session = Depends(get_db)):
    """List semua penerimaan AR per entity."""
    rows = db.execute(
        text("""
            SELECT
                r.id, r.receipt_date, r.amount, r.reference_no,
                r.bank_account, r.journal_id,
                r.currency, r.amount_fcy, r.payment_rate, r.realized_gl,
                i.invoice_no, i.customer_name, i.customer_npwp
            FROM ar_receipt r
            JOIN ar_invoice i ON i.id = r.invoice_id
            WHERE r.entity_id = :eid
            ORDER BY r.receipt_date DESC
        """),
        {"eid": entity_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]
