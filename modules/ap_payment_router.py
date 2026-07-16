# modules/ap_payment_router.py
# AP Payment: record pembayaran ke vendor + setor PPh ke DJP

from uuid import UUID, uuid4
from decimal import Decimal
from datetime import date, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger

from core.database import get_db
from modules.journal_engine import JournalEngine, JournalEntry, JournalLine
from modules.revaluation_engine import RevaluationEngine

router = APIRouter(prefix="/ap", tags=["AP — Payment"])

# Default COA kredit per kategori hutang — bisa di-override manual lewat payable_coa
PAYABLE_CATEGORY_DEFAULT_COA = {
    "trade":         "2-1-001",  # Hutang Usaha
    "related_party": "2-1-016",  # Hutang Pihak Berelasi
    "bank_loan":     "2-1-015",  # Hutang Jangka Pendek — Bank
    "other":         "2-1-001",
}


# ----------------------------------------------------------
# REQUEST MODELS
# ----------------------------------------------------------

class APPaymentRequest(BaseModel):
    entity_id: UUID
    payment_date: date
    amount: Decimal
    bank_account: str = "1-1-001"       # COA Kas/Bank
    reference_no: Optional[str] = None  # nomor bukti transfer / cek
    notes: Optional[str] = None
    paid_by: str = "system"
    amount_fcy: Optional[Decimal] = None    # wajib jika invoice currency != IDR
    payment_rate: Optional[Decimal] = None  # kurs tanggal pembayaran, wajib jika invoice currency != IDR


class PPHRemittanceRequest(BaseModel):
    entity_id: UUID
    payment_date: date
    pph_type: str = "PPh23"             # PPh23 / PPh4(2) / PPh21 / dll
    amount: Decimal
    bank_account: str = "1-1-001"
    reference_no: Optional[str] = None  # nomor NTPN / bukti setor
    period_month: Optional[int] = None  # bulan masa pajak (1-12)
    period_year: Optional[int] = None
    paid_by: str = "system"


class APInvoiceLineItem(BaseModel):
    account_code: str
    description: Optional[str] = None
    amount: Decimal
    cost_center: Optional[str] = None
    project_id: Optional[UUID] = None
    amount_fcy: Optional[Decimal] = None  # nominal asli dalam mata uang invoice, jika currency != IDR


class APInvoiceRequest(BaseModel):
    entity_id: UUID
    vendor_id: UUID
    invoice_no: str
    invoice_date: date
    due_date: Optional[date] = None
    subtotal: Optional[Decimal] = None   # diisi otomatis dari sum(lines) jika lines dikirim
    ppn_amount: Decimal = Decimal("0")
    pph_type: Optional[str] = None       # jika kosong, diambil dari default vendor
    pph_rate: Optional[Decimal] = None   # jika kosong, diambil dari default vendor
    faktur_pajak_no: Optional[str] = None
    coa_expense: Optional[str] = None    # fallback single-line jika lines kosong
    cost_center: Optional[str] = None    # fallback single-line jika lines kosong
    project_id: Optional[UUID] = None    # fallback single-line jika lines kosong
    lines: Optional[List[APInvoiceLineItem]] = None  # rincian beban multi-baris (Odoo-style)
    payable_category: str = "trade"      # trade | related_party | bank_loan | other
    payable_coa: Optional[str] = None    # jika kosong, default per payable_category
    payment_term_days: int = 30          # untuk hitung due_date otomatis jika due_date kosong
    created_by: str = "system"
    currency: str = "IDR"                # mata uang invoice — subtotal/ppn/total tetap dalam IDR
    exchange_rate: Decimal = Decimal("1")  # kurs pembukuan (1 FCY = exchange_rate IDR), wajib jika currency != IDR
    amount_fcy: Optional[Decimal] = None   # fallback total FCY jika tidak ada lines (mode single-line)


# ----------------------------------------------------------
# AP INVOICE ENDPOINTS (input manual — vendor bill)
# ----------------------------------------------------------

@router.post("/invoices")
def create_ap_invoice(req: APInvoiceRequest, db: Session = Depends(get_db)):
    """
    Buat AP invoice (vendor bill) secara manual.
    Status awal: draft — belum ada jurnal sampai /post-journal dipanggil.

    PPh type/rate otomatis diambil dari default vendor jika tidak diisi
    (alur OCR otomatis tetap berjalan terpisah via /ap/upload, tidak berubah).
    """
    vendor = db.execute(
        text("SELECT * FROM vendor WHERE id = :id"),
        {"id": str(req.vendor_id)}
    ).fetchone()
    if not vendor:
        raise HTTPException(404, "Vendor tidak ditemukan")
    v = dict(vendor._mapping)

    currency = (req.currency or "IDR").upper()
    if currency == "IDR":
        exchange_rate = Decimal("1")
    else:
        if req.exchange_rate is None or req.exchange_rate <= 0:
            raise HTTPException(400, "exchange_rate wajib diisi (> 0) untuk invoice mata uang asing")
        exchange_rate = req.exchange_rate

    pph_type = req.pph_type or v.get("default_pph_type")
    pph_rate = req.pph_rate if req.pph_rate is not None else (
        Decimal(str(v["default_pph_rate"])) if v.get("default_pph_rate") is not None else Decimal("0")
    )
    payable_category = req.payable_category or "trade"
    payable_coa = req.payable_coa or PAYABLE_CATEGORY_DEFAULT_COA.get(payable_category, "2-1-001")
    payment_term_days = req.payment_term_days if req.payment_term_days is not None else 30
    due_date = req.due_date or (req.invoice_date + timedelta(days=payment_term_days))

    # Rincian beban multi-baris (Odoo-style) — kalau ada, subtotal & header
    # coa_expense/cost_center/project_id dihitung dari baris-baris ini.
    has_lines = bool(req.lines)
    if has_lines:
        subtotal    = sum((l.amount for l in req.lines), Decimal("0"))
        coa_expense = None
        cc          = None
        pid         = None
        lines_fcy   = [l.amount_fcy for l in req.lines if l.amount_fcy is not None]
        amount_fcy  = sum(lines_fcy, Decimal("0")) if len(lines_fcy) == len(req.lines) else req.amount_fcy
    else:
        if req.subtotal is None:
            raise HTTPException(400, "subtotal atau lines wajib diisi")
        subtotal    = req.subtotal
        coa_expense = req.coa_expense or "6-1-001"
        cc          = req.cost_center
        pid         = str(req.project_id) if req.project_id else None
        amount_fcy  = req.amount_fcy

    if currency != "IDR" and amount_fcy is None:
        raise HTTPException(400, "amount_fcy wajib diisi untuk invoice mata uang asing")

    pph_amount   = (subtotal * pph_rate / 100).quantize(Decimal("1"))
    total_amount = subtotal + req.ppn_amount

    invoice_id = uuid4()
    db.execute(
        text("""
            INSERT INTO ap_invoice
                (id, entity_id, vendor_id, invoice_no, invoice_date, due_date,
                 subtotal, ppn_amount, pph_amount, total_amount, paid_amount,
                 status, faktur_pajak_no, pph_type, pph_rate, coa_expense,
                 classification, cost_center, project_id, payable_category, payable_coa,
                 payment_term_days, currency, exchange_rate, amount_fcy)
            VALUES
                (:id, :eid, :vid, :inv_no, :inv_date, :due_date,
                 :sub, :ppn, :pph, :total, 0,
                 'draft', :faktur, :pph_type, :pph_rate, :coa,
                 'expense', :cc, :pid, :pcat, :pcoa, :pterm,
                 :cur, :er, :fcy)
        """),
        {
            "id": str(invoice_id), "eid": str(req.entity_id), "vid": str(req.vendor_id),
            "inv_no": req.invoice_no, "inv_date": req.invoice_date, "due_date": due_date,
            "sub": float(subtotal), "ppn": float(req.ppn_amount),
            "pph": float(pph_amount), "total": float(total_amount),
            "faktur": req.faktur_pajak_no, "pph_type": pph_type, "pph_rate": float(pph_rate),
            "coa": coa_expense, "cc": cc, "pid": pid,
            "pcat": payable_category, "pcoa": payable_coa, "pterm": payment_term_days,
            "cur": currency, "er": float(exchange_rate),
            "fcy": float(amount_fcy) if amount_fcy is not None else None,
        }
    )

    if has_lines:
        for idx, line in enumerate(req.lines, start=1):
            db.execute(
                text("""
                    INSERT INTO ap_invoice_line
                        (ap_invoice_id, line_no, account_code, description, amount, cost_center, project_id, amount_fcy)
                    VALUES
                        (:iid, :lno, :acc, :desc, :amt, :cc, :pid, :amtfcy)
                """),
                {
                    "iid": str(invoice_id), "lno": idx, "acc": line.account_code,
                    "desc": line.description, "amt": float(line.amount),
                    "cc": line.cost_center, "pid": str(line.project_id) if line.project_id else None,
                    "amtfcy": float(line.amount_fcy) if line.amount_fcy is not None else None,
                }
            )

    db.commit()
    logger.info(f"AP invoice dibuat: {req.invoice_no} | {v['vendor_name']} | Rp {total_amount:,.0f}")
    return {
        "success":      True,
        "invoice_id":   str(invoice_id),
        "invoice_no":   req.invoice_no,
        "vendor_name":  v["vendor_name"],
        "subtotal":     float(subtotal),
        "line_count":   len(req.lines) if has_lines else 0,
        "ppn_amount":   float(req.ppn_amount),
        "pph_type":     pph_type,
        "pph_rate":     float(pph_rate),
        "pph_amount":   float(pph_amount),
        "total_amount": float(total_amount),
        "status":       "draft",
        "payable_category": payable_category,
        "payable_coa":      payable_coa,
        "payment_term_days": payment_term_days,
        "due_date":          due_date.isoformat(),
        "currency":          currency,
        "exchange_rate":     float(exchange_rate),
        "amount_fcy":        float(amount_fcy) if amount_fcy is not None else None,
    }


@router.get("/invoices")
def list_ap_invoices(
    entity_id: str,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List AP invoice per entity, opsional filter by status."""
    query = """
        SELECT
            ai.id, ai.invoice_no, ai.invoice_date, ai.due_date,
            ai.vendor_id, v.vendor_name, v.vendor_code,
            ai.subtotal, ai.ppn_amount, ai.pph_amount, ai.total_amount, ai.paid_amount,
            (ai.total_amount - ai.pph_amount - ai.paid_amount) AS outstanding,
            ai.status, ai.pph_type, ai.pph_rate, ai.faktur_pajak_no,
            ai.cost_center, ai.project_id, ai.payable_category, ai.payable_coa,
            ai.payment_term_days, ai.currency, ai.exchange_rate, ai.amount_fcy
        FROM ap_invoice ai
        JOIN vendor v ON v.id = ai.vendor_id
        WHERE ai.entity_id = :eid
    """
    params: dict = {"eid": entity_id}
    if status:
        query += " AND ai.status = :status"
        params["status"] = status
    query += " ORDER BY ai.invoice_date DESC"
    rows = db.execute(text(query), params).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/invoices/{invoice_id}/lines")
def list_ap_invoice_lines(invoice_id: UUID, db: Session = Depends(get_db)):
    """Rincian baris beban (account/cost center/project) suatu AP invoice."""
    rows = db.execute(
        text("""
            SELECT id, line_no, account_code, description, amount, cost_center, project_id, amount_fcy
            FROM ap_invoice_line
            WHERE ap_invoice_id = :id
            ORDER BY line_no
        """),
        {"id": str(invoice_id)}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/invoices/{invoice_id}/post-journal")
def post_ap_journal(
    invoice_id: UUID,
    created_by: str = "system",
    db: Session = Depends(get_db),
):
    """
    Posting jurnal untuk AP invoice (vendor bill) yang sudah dibuat.

    Dr. Beban (coa_expense)       subtotal
    Dr. PPN Masukan (1-1-005)     ppn_amount  [jika ada]
      Cr. Hutang PPh (2-1-002)    pph_amount  [jika ada]
      Cr. Hutang Usaha (2-1-001)  subtotal + ppn - pph
    """
    row = db.execute(
        text("""
            SELECT ai.*, v.vendor_name
            FROM ap_invoice ai
            JOIN vendor v ON v.id = ai.vendor_id
            WHERE ai.id = :id AND ai.status = 'draft'
        """),
        {"id": str(invoice_id)}
    ).fetchone()
    if not row:
        raise HTTPException(404, "Invoice tidak ditemukan atau sudah diposting")

    inv         = dict(row._mapping)
    subtotal    = Decimal(str(inv["subtotal"]))
    ppn_amount  = Decimal(str(inv["ppn_amount"] or 0))
    pph_amount  = Decimal(str(inv["pph_amount"] or 0))
    ap_amount   = subtotal + ppn_amount - pph_amount
    cc  = inv.get("cost_center")
    pid = inv.get("project_id")
    pc  = str(pid) if pid else None

    invoice_lines = db.execute(
        text("""
            SELECT account_code, description, amount, cost_center, project_id
            FROM ap_invoice_line
            WHERE ap_invoice_id = :id
            ORDER BY line_no
        """),
        {"id": str(invoice_id)}
    ).fetchall()

    if invoice_lines:
        # Rincian beban multi-baris — satu baris debit jurnal per baris invoice
        lines = [
            JournalLine(
                account_code=il.account_code,
                description=il.description or f"Beban AP Invoice {inv['invoice_no']} — {inv['vendor_name']}",
                debit_idr=Decimal(str(il.amount)),
                cost_center=il.cost_center,
                project_code=str(il.project_id) if il.project_id else None,
            )
            for il in invoice_lines
        ]
    else:
        # Fallback single-line (invoice lama / hasil OCR otomatis)
        lines = [
            JournalLine(
                account_code=inv["coa_expense"] or "6-1-001",
                description=f"Beban AP Invoice {inv['invoice_no']} — {inv['vendor_name']}",
                debit_idr=subtotal,
                cost_center=cc,
                project_code=pc,
            ),
        ]
    if ppn_amount > 0:
        lines.append(JournalLine(
            account_code="1-1-005",     # PPN Masukan
            description=f"PPN Masukan Invoice {inv['invoice_no']}",
            debit_idr=ppn_amount,
            cost_center=cc,
            project_code=pc,
        ))
    if pph_amount > 0:
        lines.append(JournalLine(
            account_code="2-1-002",     # Hutang PPh
            description=f"{inv['pph_type']} dipotong dari {inv['vendor_name']}",
            credit_idr=pph_amount,
            cost_center=cc,
            project_code=pc,
        ))
    invoice_currency = inv.get("currency") or "IDR"
    payable_line_kwargs = {}
    if invoice_currency != "IDR":
        invoice_rate = Decimal(str(inv["exchange_rate"]))
        payable_line_kwargs = {
            "currency": invoice_currency,
            "exchange_rate": invoice_rate,
            # Nilai FCY dari saldo Hutang Usaha (subtotal+ppn-pph), dikonversi balik
            # dari ap_amount IDR supaya konsisten dengan pembulatan PPN/PPh.
            "amount_fcy": (ap_amount / invoice_rate).quantize(Decimal("0.01")),
        }
    lines.append(JournalLine(
        account_code=inv.get("payable_coa") or "2-1-001",
        description=f"AP Invoice {inv['invoice_no']} — {inv['vendor_name']}",
        credit_idr=ap_amount,
        cost_center=cc,
        project_code=pc,
        **payable_line_kwargs,
    ))

    engine = JournalEngine(db)
    entry  = JournalEntry(
        entity_id=inv["entity_id"],
        journal_type="AP",
        journal_date=inv["invoice_date"],
        description=f"AP Invoice {inv['invoice_no']} — {inv['vendor_name']}",
        lines=lines,
        reference_no=inv["invoice_no"],
        source="manual",
        created_by=created_by,
    )
    result = engine.post_journal(entry)
    if not result["success"]:
        raise HTTPException(400, result["error"])

    db.execute(
        text("UPDATE ap_invoice SET status = 'approved', journal_id = :jid, updated_at = NOW() WHERE id = :id"),
        {"jid": result["journal_id"], "id": str(invoice_id)}
    )
    db.commit()
    return result


# ----------------------------------------------------------
# AP PAYMENT ENDPOINTS
# ----------------------------------------------------------

@router.post("/invoices/{invoice_id}/pay")
def pay_ap_invoice(
    invoice_id: UUID,
    req: APPaymentRequest,
    db: Session = Depends(get_db),
):
    """
    Record pembayaran ke vendor untuk AP invoice.

    Saat invoice diposting, jurnal yang terbentuk adalah:
      Dr. Beban Jasa            subtotal
      Dr. PPN Masukan           ppn
        Cr. Hutang PPh 23       pph
        Cr. Hutang Usaha (AP)   subtotal + ppn - pph   ← ini yang dibayar

    Jurnal pembayaran:
      Dr. Hutang Usaha (2-1-001)   amount
        Cr. Kas/Bank               amount

    PPh liability (2-1-002) tetap ada sampai disetorkan ke DJP via /ap/pph-remittance.
    """
    invoice = db.execute(
        text("""
            SELECT ai.*, v.vendor_name
            FROM ap_invoice ai
            JOIN vendor v ON v.id = ai.vendor_id
            WHERE ai.id = :id
              AND ai.status NOT IN ('cancelled', 'paid')
        """),
        {"id": str(invoice_id)}
    ).fetchone()
    if not invoice:
        raise HTTPException(404, "Invoice tidak ditemukan atau sudah lunas/dibatalkan")

    inv = dict(invoice._mapping)
    invoice_currency = inv.get("currency") or "IDR"

    # AP balance di Hutang Usaha = total - pph (pph sudah ke akun Hutang PPh tersendiri)
    ap_balance  = Decimal(str(inv["total_amount"])) - Decimal(str(inv["pph_amount"] or 0))
    paid_so_far = Decimal(str(inv["paid_amount"]))
    outstanding = ap_balance - paid_so_far

    realized_gl_result = None
    if invoice_currency != "IDR":
        if req.amount_fcy is None or req.payment_rate is None or req.payment_rate <= 0:
            raise HTTPException(
                400,
                f"Invoice ini dalam mata uang {invoice_currency} — amount_fcy dan payment_rate wajib diisi."
            )
        invoice_rate = Decimal(str(inv["exchange_rate"]))
        # AP payable dilunasi pada kurs pembukuan (booking rate) — ini yang dipakai
        # untuk paid_amount supaya outstanding tidak terdistorsi fluktuasi kurs harian.
        payable_leg_idr = (req.amount_fcy * invoice_rate).quantize(Decimal("1"))
        # Uang riil yang keluar dari bank dihitung pada kurs hari pembayaran.
        bank_leg_idr = (req.amount_fcy * req.payment_rate).quantize(Decimal("1"))
        pay_amount = payable_leg_idr

        realized_gl_result = RevaluationEngine.compute_realized_gain_loss(
            db,
            entity_id=str(req.entity_id),
            invoice_type="ap",
            invoice_currency=invoice_currency,
            invoice_amount_fcy=req.amount_fcy,
            invoice_rate=invoice_rate,
            payment_rate=req.payment_rate,
        )
    else:
        payable_leg_idr = req.amount
        bank_leg_idr = req.amount
        pay_amount = req.amount

    if pay_amount > outstanding + Decimal("1"):
        raise HTTPException(
            400,
            f"Pembayaran Rp {pay_amount:,.0f} melebihi saldo AP Rp {outstanding:,.0f}. "
            f"PPh {inv.get('pph_amount', 0):,.0f} disetorkan terpisah via /ap/pph-remittance."
        )

    # Tag baris pelunasan dengan FCY yang sama seperti baris invoice supaya saldo FCY
    # akun payable bersih ke nol setelah lunas (dipakai vw_fcy_exposure/revaluasi).
    settle_line_kwargs = {}
    if invoice_currency != "IDR":
        settle_line_kwargs = {
            "currency": invoice_currency,
            "exchange_rate": invoice_rate,
            "amount_fcy": req.amount_fcy,
        }

    lines = [
        JournalLine(
            account_code=inv.get("payable_coa") or "2-1-001",
            description=f"Pelunasan AP {inv['invoice_no']} — {inv['vendor_name']}",
            debit_idr=payable_leg_idr,
            **settle_line_kwargs,
        ),
        JournalLine(
            account_code=req.bank_account,
            description=f"Bayar AP {inv['invoice_no']} — {inv['vendor_name']}",
            credit_idr=bank_leg_idr,
        ),
    ]
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
        journal_type="AP",
        journal_date=req.payment_date,
        description=f"Pembayaran AP {inv['invoice_no']} — {inv['vendor_name']}",
        lines=lines,
        reference_no=req.reference_no or inv["invoice_no"],
        source="manual",
        created_by=req.paid_by,
    )
    result = engine.post_journal(entry)
    if not result["success"]:
        raise HTTPException(400, result["error"])

    if realized_gl_result:
        RevaluationEngine.mark_realized_gl_journal(db, "ap", str(invoice_id), result["journal_id"])

    # Simpan ke tabel ap_payment
    payment_id = uuid4()
    db.execute(
        text("""
            INSERT INTO ap_payment
                (id, entity_id, invoice_id, payment_date, amount,
                 bank_account, reference_no, journal_id, currency, amount_fcy,
                 payment_rate, realized_gl)
            VALUES
                (:id, :eid, :inv_id, :pdate, :amt,
                 :bank, :ref, :jid, :cur, :fcy,
                 :prate, :rgl)
        """),
        {
            "id": str(payment_id), "eid": str(req.entity_id),
            "inv_id": str(invoice_id), "pdate": req.payment_date,
            "amt": float(bank_leg_idr), "bank": req.bank_account,
            "ref": req.reference_no, "jid": result["journal_id"],
            "cur": invoice_currency,
            "fcy": float(req.amount_fcy) if req.amount_fcy is not None else None,
            "prate": float(req.payment_rate) if req.payment_rate is not None else None,
            "rgl": float(realized_gl_result["realized_gl"]) if realized_gl_result else None,
        }
    )

    # Update paid_amount dan status (berbasis nilai IDR pada kurs pembukuan)
    new_paid   = paid_so_far + payable_leg_idr
    new_status = "paid" if new_paid >= outstanding - Decimal("1") else "partial"
    db.execute(
        text("""
            UPDATE ap_invoice
            SET paid_amount = :paid, status = :status, updated_at = NOW()
            WHERE id = :id
        """),
        {"paid": float(new_paid), "status": new_status, "id": str(invoice_id)}
    )
    db.commit()

    logger.info(
        f"AP payment: {inv['invoice_no']} | Rp {payable_leg_idr:,.0f} "
        f"| status: {new_status}"
    )
    return {
        "success":       True,
        "payment_id":    str(payment_id),
        "journal_no":    result["journal_no"],
        "invoice_no":    inv["invoice_no"],
        "vendor":        inv["vendor_name"],
        "invoice_status": new_status,
        "amount_paid":   float(payable_leg_idr),
        "total_paid_ap": float(new_paid),
        "remaining_ap":  float(max(outstanding - payable_leg_idr, Decimal("0"))),
        "pph_outstanding": float(inv.get("pph_amount") or 0),
        "realized_gl":   float(realized_gl_result["realized_gl"]) if realized_gl_result else None,
        "realized_gl_is_gain": realized_gl_result["is_gain"] if realized_gl_result else None,
        "note": (
            f"PPh Rp {float(inv.get('pph_amount') or 0):,.0f} masih tercatat di Hutang PPh (2-1-002). "
            "Gunakan POST /ap/pph-remittance untuk setor ke DJP."
            if (inv.get("pph_amount") or 0) > 0 else None
        ),
    }


@router.post("/pph-remittance")
def post_pph_remittance(req: PPHRemittanceRequest, db: Session = Depends(get_db)):
    """
    Setor PPh yang sudah dipotong ke kas negara.

    Dr. Hutang PPh (2-1-002)    amount
      Cr. Kas/Bank               amount

    Gunakan setelah masa pajak selesai (paling lambat tgl 10/15 bulan berikutnya).
    """
    if req.amount <= 0:
        raise HTTPException(400, "Amount harus lebih dari 0")

    period_info = ""
    if req.period_month and req.period_year:
        period_info = f" masa {req.period_month:02d}/{req.period_year}"

    lines = [
        JournalLine(
            account_code="2-1-002",     # Hutang PPh 23
            description=f"Setor {req.pph_type}{period_info} — ref: {req.reference_no or '-'}",
            debit_idr=req.amount,
        ),
        JournalLine(
            account_code=req.bank_account,
            description=f"Kas keluar setor {req.pph_type}{period_info}",
            credit_idr=req.amount,
        ),
    ]

    engine = JournalEngine(db)
    entry  = JournalEntry(
        entity_id=req.entity_id,
        journal_type="GL",
        journal_date=req.payment_date,
        description=f"Setor {req.pph_type}{period_info}",
        lines=lines,
        reference_no=req.reference_no,
        source="manual",
        created_by=req.paid_by,
    )
    result = engine.post_journal(entry)
    if not result["success"]:
        raise HTTPException(400, result["error"])

    logger.info(f"PPh remittance: {req.pph_type} Rp {req.amount:,.0f}{period_info}")
    return result


@router.get("/payments/{entity_id}")
def list_ap_payments(entity_id: str, db: Session = Depends(get_db)):
    """List semua pembayaran AP per entity."""
    rows = db.execute(
        text("""
            SELECT
                p.id, p.payment_date, p.amount,
                p.reference_no, p.bank_account, p.journal_id,
                p.currency, p.amount_fcy, p.payment_rate, p.realized_gl,
                i.invoice_no, i.invoice_date,
                v.vendor_name, v.vendor_code
            FROM ap_payment p
            JOIN ap_invoice i ON i.id = p.invoice_id
            JOIN vendor v     ON v.id = i.vendor_id
            WHERE p.entity_id = :eid
            ORDER BY p.payment_date DESC
        """),
        {"eid": entity_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/invoices/{entity_id}/outstanding")
def list_outstanding_invoices(entity_id: str, db: Session = Depends(get_db)):
    """
    Daftar AP invoice yang masih outstanding (belum atau sebagian dibayar),
    termasuk sisa AP balance dan sisa PPh yang belum disetor.
    """
    rows = db.execute(
        text("""
            SELECT
                ai.id, ai.invoice_no, ai.invoice_date, ai.due_date,
                v.vendor_name, v.vendor_code, v.npwp,
                ai.subtotal, ai.ppn_amount, ai.pph_amount,
                ai.total_amount, ai.paid_amount,
                (ai.total_amount - ai.pph_amount - ai.paid_amount) AS ap_outstanding,
                ai.pph_type, ai.pph_rate, ai.status,
                ai.currency, ai.exchange_rate, ai.amount_fcy,
                (CURRENT_DATE - ai.due_date) AS days_overdue
            FROM ap_invoice ai
            JOIN vendor v ON v.id = ai.vendor_id
            WHERE ai.entity_id = :eid
              AND ai.status NOT IN ('paid', 'cancelled')
            ORDER BY ai.due_date ASC NULLS LAST
        """),
        {"eid": entity_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]
