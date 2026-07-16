# modules/bank_router.py
# REST endpoints: bank account management, import statement, rekonsiliasi

from uuid import UUID, uuid4
from typing import Optional
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger

from core.database import get_db
from modules.bank_sync import BankReconciler, parse_bank_statement

router = APIRouter(prefix="/bank", tags=["Bank Sync & Rekonsiliasi"])


# ── Request models ─────────────────────────────────────────────────────────────

class BankAccountRequest(BaseModel):
    entity_id:    UUID
    bank_name:    str           # BCA / Mandiri / BNI / BRI / OCBC / dll
    account_no:   str
    account_name: str
    currency:     str = "IDR"
    coa_code:     str           # kode akun Kas/Bank di COA


class ManualMatchRequest(BaseModel):
    statement_line_id: UUID
    invoice_id:        UUID
    invoice_type:      str      # "AP" atau "AR"


class PostJournalRequest(BaseModel):
    statement_line_id: UUID
    bank_coa:          str = "1-1-002"   # default: Kas di Bank BCA
    posted_by:         str = "system"


class IgnoreLineRequest(BaseModel):
    statement_line_id: UUID
    reason:            Optional[str] = None


# ── Bank Account Endpoints ─────────────────────────────────────────────────────

@router.post("/accounts")
def create_bank_account(req: BankAccountRequest, db: Session = Depends(get_db)):
    """Daftarkan rekening bank baru."""
    # Resolve coa_id dari account_code
    coa = db.execute(
        text("""
            SELECT id FROM chart_of_accounts
            WHERE entity_id = :eid AND account_code = :code AND is_active = TRUE
        """),
        {"eid": str(req.entity_id), "code": req.coa_code}
    ).fetchone()
    if not coa:
        raise HTTPException(400, f"COA '{req.coa_code}' tidak ditemukan. Pastikan COA sudah dibuat.")

    acc_id = uuid4()
    db.execute(
        text("""
            INSERT INTO bank_account
                (id, entity_id, bank_name, account_no, account_name, currency, coa_id)
            VALUES
                (:id, :eid, :bank, :no, :name, :cur, :coa)
        """),
        {
            "id":   str(acc_id), "eid": str(req.entity_id),
            "bank": req.bank_name, "no": req.account_no,
            "name": req.account_name, "cur": req.currency,
            "coa":  str(coa.id),
        }
    )
    db.commit()
    logger.info(f"Bank account dibuat: {req.bank_name} {req.account_no}")
    return {
        "success":        True,
        "bank_account_id": str(acc_id),
        "bank_name":      req.bank_name,
        "account_no":     req.account_no,
    }


@router.get("/accounts/{entity_id}")
def list_bank_accounts(entity_id: str, db: Session = Depends(get_db)):
    """List semua rekening bank per entity."""
    rows = db.execute(
        text("""
            SELECT
                ba.id, ba.bank_name, ba.account_no, ba.account_name,
                ba.currency, ba.last_sync_at,
                coa.account_code, coa.account_name AS coa_name,
                (SELECT COUNT(*) FROM bank_statement_line bsl
                 WHERE bsl.bank_account_id = ba.id) AS total_lines,
                (SELECT COUNT(*) FROM bank_statement_line bsl
                 WHERE bsl.bank_account_id = ba.id
                   AND bsl.match_status = 'unmatched') AS unmatched_lines
            FROM bank_account ba
            LEFT JOIN chart_of_accounts coa ON coa.id = ba.coa_id
            WHERE ba.entity_id = :eid
            ORDER BY ba.bank_name
        """),
        {"eid": entity_id}
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Import Statement ───────────────────────────────────────────────────────────

@router.post("/import/{bank_account_id}")
async def import_bank_statement(
    bank_account_id: UUID,
    file:       UploadFile = File(...),
    skip_rows:  int = Query(default=0, description="Baris header non-data di awal file (BCA=7, Mandiri=0)"),
    db:         Session = Depends(get_db),
):
    """
    Import mutasi bank dari file CSV atau Excel.

    Format yang didukung: BCA, Mandiri, BNI, BRI, Generic CSV/Excel.

    **Tips skip_rows per bank:**
    - BCA Internet Banking export : skip_rows=7
    - Mandiri Internet Banking    : skip_rows=0
    - BNI Internet Banking        : skip_rows=0
    - BRI Internet Banking        : skip_rows=0
    - Generic CSV                 : skip_rows=0
    """
    # Validasi bank account ada
    account = db.execute(
        text("SELECT id, bank_name, account_no, entity_id FROM bank_account WHERE id = :id"),
        {"id": str(bank_account_id)}
    ).fetchone()
    if not account:
        raise HTTPException(404, "Bank account tidak ditemukan")

    # Validasi format file
    fname = file.filename.lower()
    if not any(fname.endswith(ext) for ext in (".csv", ".xlsx", ".xls")):
        raise HTTPException(400, "Format file tidak didukung. Gunakan CSV atau Excel (.xlsx/.xls)")

    # Baca dan parse
    content = await file.read()
    try:
        transactions = parse_bank_statement(content, file.filename, skip_rows=skip_rows)
    except Exception as e:
        raise HTTPException(422, f"Gagal parse file: {e}")

    if not transactions:
        raise HTTPException(422, "Tidak ada transaksi yang berhasil dibaca dari file")

    # Import ke DB
    reconciler = BankReconciler(db)
    result     = reconciler.import_statement(bank_account_id, transactions)

    # Update last_sync_at
    db.execute(
        text("UPDATE bank_account SET last_sync_at = NOW() WHERE id = :id"),
        {"id": str(bank_account_id)}
    )
    db.commit()

    logger.info(
        f"Import {account.bank_name} {account.account_no}: "
        f"{result['inserted']} baru, {result['skipped']} skip"
    )
    return {
        "success":          True,
        "bank_account_id":  str(bank_account_id),
        "bank_name":        account.bank_name,
        "file_name":        file.filename,
        "total_parsed":     len(transactions),
        "inserted":         result["inserted"],
        "skipped_duplicate": result["skipped"],
    }


# ── List Statement Lines ───────────────────────────────────────────────────────

@router.get("/statements/{bank_account_id}")
def list_statement_lines(
    bank_account_id: UUID,
    match_status:    Optional[str] = None,
    date_from:       Optional[date] = None,
    date_to:         Optional[date] = None,
    limit:           int = 100,
    offset:          int = 0,
    db:              Session = Depends(get_db),
):
    """List transaksi bank yang sudah diimport."""
    filters = ["bsl.bank_account_id = :bid"]
    params: dict  = {"bid": str(bank_account_id), "limit": limit, "offset": offset}

    if match_status:
        filters.append("bsl.match_status = :status")
        params["status"] = match_status
    if date_from:
        filters.append("bsl.transaction_date >= :df")
        params["df"] = date_from
    if date_to:
        filters.append("bsl.transaction_date <= :dt")
        params["dt"] = date_to

    where = " AND ".join(filters)
    rows  = db.execute(
        text(f"""
            SELECT
                bsl.id, bsl.transaction_date, bsl.description, bsl.reference_no,
                bsl.debit_amount, bsl.credit_amount, bsl.balance,
                bsl.match_status, bsl.matched_invoice_id, bsl.journal_id
            FROM bank_statement_line bsl
            WHERE {where}
            ORDER BY bsl.transaction_date DESC
            LIMIT :limit OFFSET :offset
        """),
        params
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Auto-Reconciliation ────────────────────────────────────────────────────────

@router.post("/reconcile/{bank_account_id}/auto")
def auto_reconcile(
    bank_account_id: UUID,
    tolerance_days:  int = Query(default=7, description="Toleransi selisih hari untuk match tanggal"),
    db:              Session = Depends(get_db),
):
    """
    Auto-match transaksi unmatched ke AP/AR invoice berdasarkan:
    1. Nomor invoice di deskripsi (skor tertinggi)
    2. Nama vendor/customer di deskripsi
    3. Amount yang sama dalam toleransi tanggal
    """
    reconciler = BankReconciler(db)
    result     = reconciler.auto_match(bank_account_id, tolerance_days)
    return {"success": True, "bank_account_id": str(bank_account_id), **result}


@router.post("/reconcile/match-manual")
def manual_match(req: ManualMatchRequest, db: Session = Depends(get_db)):
    """
    Match manual satu transaksi ke invoice tertentu.
    Gunakan jika auto-match tidak berhasil atau salah match.
    """
    # Validasi statement line ada
    line = db.execute(
        text("SELECT id, match_status FROM bank_statement_line WHERE id = :id"),
        {"id": str(req.statement_line_id)}
    ).fetchone()
    if not line:
        raise HTTPException(404, "Statement line tidak ditemukan")

    # Validasi invoice ada sesuai tipe
    table = "ar_invoice" if req.invoice_type == "AR" else "ap_invoice"
    inv   = db.execute(
        text(f"SELECT id FROM {table} WHERE id = :id"),
        {"id": str(req.invoice_id)}
    ).fetchone()
    if not inv:
        raise HTTPException(404, f"{req.invoice_type} invoice tidak ditemukan")

    db.execute(
        text("""
            UPDATE bank_statement_line
            SET match_status       = 'matched',
                matched_invoice_id = :inv_id
            WHERE id = :id
        """),
        {"inv_id": str(req.invoice_id), "id": str(req.statement_line_id)}
    )
    db.commit()
    return {"success": True, "message": "Manual match berhasil"}


@router.post("/reconcile/ignore")
def ignore_line(req: IgnoreLineRequest, db: Session = Depends(get_db)):
    """
    Tandai transaksi sebagai 'ignored' (tidak perlu dicocokkan).
    Contoh: transfer antar rekening sendiri, biaya admin bank.
    """
    db.execute(
        text("""
            UPDATE bank_statement_line
            SET match_status = 'ignored'
            WHERE id = :id
        """),
        {"id": str(req.statement_line_id)}
    )
    db.commit()
    return {"success": True, "message": "Transaksi ditandai ignored"}


@router.post("/reconcile/post-journal")
def post_reconcile_journal(req: PostJournalRequest, db: Session = Depends(get_db)):
    """
    Posting jurnal untuk 1 transaksi bank yang sudah matched.
    - Credit (uang masuk) → Dr. Kas/Bank | Cr. Piutang Usaha
    - Debit  (uang keluar) → Dr. Hutang Usaha | Cr. Kas/Bank
    """
    reconciler = BankReconciler(db)
    result     = reconciler.post_matched_journal(
        statement_line_id=req.statement_line_id,
        bank_coa=req.bank_coa,
        posted_by=req.posted_by,
    )
    if not result.get("success"):
        raise HTTPException(400, result.get("error"))
    return result


@router.post("/reconcile/{bank_account_id}/post-all-matched")
def post_all_matched(
    bank_account_id: UUID,
    bank_coa:        str = Query(default="1-1-002"),
    posted_by:       str = Query(default="system"),
    db:              Session = Depends(get_db),
):
    """
    Batch posting jurnal untuk SEMUA transaksi matched yang belum punya jurnal.
    """
    lines = db.execute(
        text("""
            SELECT id FROM bank_statement_line
            WHERE bank_account_id = :bid
              AND match_status    = 'matched'
              AND journal_id      IS NULL
        """),
        {"bid": str(bank_account_id)}
    ).fetchall()

    reconciler = BankReconciler(db)
    posted = errors = 0
    for line in lines:
        result = reconciler.post_matched_journal(
            statement_line_id=line.id,
            bank_coa=bank_coa,
            posted_by=posted_by,
        )
        if result.get("success"):
            posted += 1
        else:
            errors += 1

    return {
        "success": True,
        "posted":  posted,
        "errors":  errors,
        "total":   len(lines),
    }


# ── Summary & Reporting ────────────────────────────────────────────────────────

@router.get("/reconcile/{bank_account_id}/summary")
def reconcile_summary(bank_account_id: UUID, db: Session = Depends(get_db)):
    """Ringkasan status rekonsiliasi untuk satu rekening bank."""
    reconciler = BankReconciler(db)
    return reconciler.get_reconciliation_summary(bank_account_id)


@router.get("/reconcile/{bank_account_id}/unmatched")
def list_unmatched(bank_account_id: UUID, db: Session = Depends(get_db)):
    """
    List transaksi yang belum berhasil dicocokkan.
    Tampilkan kandidat invoice yang mungkin cocok untuk membantu match manual.
    """
    rows = db.execute(
        text("""
            SELECT
                bsl.id, bsl.transaction_date, bsl.description,
                bsl.debit_amount, bsl.credit_amount, bsl.reference_no
            FROM bank_statement_line bsl
            WHERE bsl.bank_account_id = :bid
              AND bsl.match_status    = 'unmatched'
            ORDER BY bsl.transaction_date DESC
            LIMIT 50
        """),
        {"bid": str(bank_account_id)}
    ).fetchall()

    result = []
    for r in rows:
        tx     = dict(r._mapping)
        amount = float(tx["credit_amount"] or 0) or float(tx["debit_amount"] or 0)

        # Cari kandidat invoice dengan amount mendekati
        candidates = db.execute(
            text("""
                (SELECT 'AR' AS type, invoice_no, customer_name AS name,
                        total_amount, due_date, status
                 FROM ar_invoice
                 WHERE status NOT IN ('paid','cancelled')
                   AND ABS(total_amount - paid_amount - :amt) < :amt * 0.05
                 LIMIT 3)
                UNION ALL
                (SELECT 'AP' AS type, invoice_no, vendor_name AS name,
                        total_amount, due_date, status
                 FROM ap_invoice ai JOIN vendor v ON v.id = ai.vendor_id
                 WHERE ai.status NOT IN ('paid','cancelled')
                   AND ABS(ai.total_amount - ai.pph_amount - ai.paid_amount - :amt) < :amt * 0.05
                 LIMIT 3)
            """),
            {"amt": amount}
        ).fetchall()

        tx["amount"]     = amount
        tx["candidates"] = [dict(c._mapping) for c in candidates]
        result.append(tx)

    return result
