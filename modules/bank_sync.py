# modules/bank_sync.py
# Bank Statement Import & Auto-Reconciliation Engine
#
# Flow:
#   Import CSV/Excel → parse → simpan ke bank_statement_line
#   Auto-match: debit → AP invoice, credit → AR invoice
#   Post jurnal untuk transaksi yang matched
#
# Bank yang didukung (auto-detect format):
#   BCA, Mandiri, BNI, BRI, OCBC, dan Generic CSV

import re
import io
from decimal import Decimal, InvalidOperation
from datetime import date, datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger

from modules.journal_engine import JournalEngine, JournalEntry, JournalLine


# ── Amount cleaner ─────────────────────────────────────────────────────────────

def _parse_amount(raw: str) -> Decimal:
    """
    Normalisasi angka dari berbagai format bank Indonesia:
    1.234.567,89  →  1234567.89
    1,234,567.89  →  1234567.89
    1234567       →  1234567
    -1.234.567    →  -1234567
    """
    if not raw:
        return Decimal("0")
    s = str(raw).strip().replace(" ", "").replace("\xa0", "")
    negative = s.startswith("-") or s.startswith("(")
    s = s.lstrip("+-( ").rstrip(") ")

    # Format: titik ribuan, koma desimal  →  1.234.567,89
    if re.search(r"\d\.\d{3},\d{1,2}$", s):
        s = s.replace(".", "").replace(",", ".")
    # Format: koma ribuan, titik desimal  →  1,234,567.89
    elif re.search(r"\d,\d{3}\.\d{1,2}$", s):
        s = s.replace(",", "")
    # Hanya koma tanpa titik  →  1234,50  (kemungkinan desimal)
    elif "," in s and "." not in s:
        parts = s.split(",")
        s = s.replace(",", ".") if len(parts[-1]) <= 2 else s.replace(",", "")
    # Hanya titik tanpa koma  →  1234.50
    # Sudah format OK, tidak perlu diubah

    try:
        result = Decimal(re.sub(r"[^\d.]", "", s))
        return -result if negative else result
    except InvalidOperation:
        return Decimal("0")


def _parse_date(raw: str) -> Optional[date]:
    """Parse berbagai format tanggal bank Indonesia."""
    if not raw:
        return None
    raw = str(raw).strip()
    for fmt in [
        "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d",
        "%d/%m/%y", "%d-%m-%y",
        "%d %b %Y", "%d %B %Y",
        "%Y/%m/%d",
    ]:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


# ── Bank CSV parsers ───────────────────────────────────────────────────────────

def _detect_bank(headers: list[str]) -> str:
    """Deteksi format bank dari header CSV."""
    h = " ".join(headers).upper()
    if "CABANG" in h and "JUMLAH" in h:
        return "BCA"
    if "TANGGAL TRANSAKSI" in h and "TANGGAL VALUTA" in h:
        return "MANDIRI"
    if "MUTASI" in h and "D/K" in h:
        return "BNI"
    if "DEBET" in h and "KREDIT" in h and "KETERANGAN" in h:
        return "BRI"
    if ("NOMINAL" in h and "JENIS" in h) or "KETERANGAN" in h and "JUMLAH" in h and "SALDO" in h:
        return "MANDIRI"
    return "GENERIC"


def _parse_bca(rows: list[dict]) -> list[dict]:
    """Parser format CSV BCA Internet Banking."""
    result = []
    for r in rows:
        keys = list(r.keys())
        raw_date = r.get("TANGGAL") or r.get(keys[0], "")
        raw_desc = r.get("KETERANGAN") or r.get(keys[1], "")
        raw_amt  = r.get("JUMLAH") or r.get(keys[3], "0")
        raw_bal  = r.get("SALDO") or r.get(keys[4], "0")

        amt = _parse_amount(raw_amt)
        result.append({
            "transaction_date": _parse_date(raw_date),
            "description":      str(raw_desc).strip(),
            "reference_no":     _extract_reference(str(raw_desc)),
            "debit_amount":     abs(amt) if amt < 0 else Decimal("0"),
            "credit_amount":    amt      if amt > 0 else Decimal("0"),
            "balance":          _parse_amount(raw_bal),
        })
    return result


def _parse_mandiri(rows: list[dict]) -> list[dict]:
    """Parser format CSV Mandiri Internet Banking."""
    result = []
    for r in rows:
        raw_date = r.get("TANGGAL TRANSAKSI") or r.get("TANGGAL") or ""
        raw_desc = r.get("DESKRIPSI") or r.get("KETERANGAN") or ""
        raw_amt  = r.get("NOMINAL") or r.get("JUMLAH") or "0"
        raw_type = (r.get("JENIS") or r.get("D/K") or "").upper()

        amt = abs(_parse_amount(raw_amt))
        is_debit = "DEBET" in raw_type or "D" == raw_type.strip()

        result.append({
            "transaction_date": _parse_date(raw_date),
            "description":      str(raw_desc).strip(),
            "reference_no":     _extract_reference(str(raw_desc)),
            "debit_amount":     amt if is_debit else Decimal("0"),
            "credit_amount":    amt if not is_debit else Decimal("0"),
            "balance":          _parse_amount(r.get("SALDO") or "0"),
        })
    return result


def _parse_bni(rows: list[dict]) -> list[dict]:
    """Parser format CSV BNI Internet Banking."""
    result = []
    for r in rows:
        raw_date = r.get("TANGGAL") or ""
        raw_desc = r.get("KETERANGAN") or ""
        raw_amt  = r.get("MUTASI") or "0"
        raw_type = (r.get("D/K") or "").upper().strip()

        amt = abs(_parse_amount(raw_amt))
        is_debit = raw_type == "D"

        result.append({
            "transaction_date": _parse_date(raw_date),
            "description":      str(raw_desc).strip(),
            "reference_no":     _extract_reference(str(raw_desc)),
            "debit_amount":     amt if is_debit else Decimal("0"),
            "credit_amount":    amt if not is_debit else Decimal("0"),
            "balance":          _parse_amount(r.get("SALDO") or "0"),
        })
    return result


def _parse_bri(rows: list[dict]) -> list[dict]:
    """Parser format CSV BRI Internet Banking."""
    result = []
    for r in rows:
        raw_date  = r.get("TANGGAL") or ""
        raw_desc  = r.get("KETERANGAN") or ""
        raw_debit = r.get("DEBET") or r.get("DEBIT") or "0"
        raw_kredit = r.get("KREDIT") or "0"

        result.append({
            "transaction_date": _parse_date(raw_date),
            "description":      str(raw_desc).strip(),
            "reference_no":     _extract_reference(str(raw_desc)),
            "debit_amount":     _parse_amount(raw_debit),
            "credit_amount":    _parse_amount(raw_kredit),
            "balance":          _parse_amount(r.get("SALDO") or "0"),
        })
    return result


def _parse_generic(rows: list[dict]) -> list[dict]:
    """Parser generic — deteksi kolom secara heuristik."""
    result = []
    for r in rows:
        keys   = list(r.keys())
        upper  = {k.upper(): v for k, v in r.items()}

        # Cari kolom tanggal
        date_val = None
        for kw in ["TANGGAL", "DATE", "TGL", "TRANSACTION DATE"]:
            if kw in upper and upper[kw]:
                date_val = _parse_date(upper[kw])
                break

        # Cari kolom deskripsi
        desc = ""
        for kw in ["KETERANGAN", "DESKRIPSI", "DESCRIPTION", "URAIAN", "REMARK"]:
            if kw in upper and upper[kw]:
                desc = str(upper[kw]).strip()
                break

        # Cari debit/kredit
        debit = credit = Decimal("0")
        if "DEBET" in upper or "DEBIT" in upper:
            debit  = _parse_amount(upper.get("DEBET") or upper.get("DEBIT") or "0")
            credit = _parse_amount(upper.get("KREDIT") or upper.get("CREDIT") or "0")
        elif "MUTASI" in upper and "D/K" in upper:
            amt = abs(_parse_amount(upper["MUTASI"]))
            if upper["D/K"].strip().upper() == "D":
                debit = amt
            else:
                credit = amt
        elif "JUMLAH" in upper:
            amt = _parse_amount(upper["JUMLAH"])
            if amt < 0:
                debit  = abs(amt)
            else:
                credit = amt
        elif len(keys) >= 4:
            # Tebak: kolom ke-3 = debit/kredit
            try:
                amt = _parse_amount(list(r.values())[2])
                if amt < 0:
                    debit = abs(amt)
                else:
                    credit = amt
            except Exception:
                pass

        result.append({
            "transaction_date": date_val,
            "description":      desc,
            "reference_no":     _extract_reference(desc),
            "debit_amount":     debit,
            "credit_amount":    credit,
            "balance":          _parse_amount(upper.get("SALDO") or upper.get("BALANCE") or "0"),
        })
    return result


def _extract_reference(description: str) -> Optional[str]:
    """Coba ekstrak nomor referensi/invoice dari deskripsi transaksi."""
    patterns = [
        r"\b(INV[-/]?\d+)\b",
        r"\b(AP[-/]?\d+)\b",
        r"\b(AR[-/]?\d+)\b",
        r"\b(\d{4}[-/]\d{2}[-/]\d{4,})\b",  # pola nomor invoice umum
        r"REF[:\s]+([A-Z0-9\-/]+)",
        r"NO[:\s]+([A-Z0-9\-/]+)",
    ]
    for pat in patterns:
        m = re.search(pat, description.upper())
        if m:
            return m.group(1)
    return None


# ── File reader ────────────────────────────────────────────────────────────────

def read_bank_file(file_content: bytes, filename: str, skip_rows: int = 0) -> list[dict]:
    """
    Baca file CSV atau Excel → list of dicts (header normalized to uppercase).
    skip_rows: jumlah baris header non-data di awal file (mis. info rekening BCA).
    """
    import csv

    fname = filename.lower()

    # Excel
    if fname.endswith((".xlsx", ".xls")):
        import openpyxl
        wb  = openpyxl.load_workbook(io.BytesIO(file_content), data_only=True)
        ws  = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        # Skip baris kosong/header bank di atas
        data_start = skip_rows
        for i, row in enumerate(rows):
            if any(cell for cell in row):
                data_start = max(data_start, i)
                break
        headers = [str(c).strip().upper() if c else f"COL{j}" for j, c in enumerate(rows[data_start])]
        result  = []
        for row in rows[data_start + 1:]:
            if not any(row):
                continue
            result.append({headers[j]: str(v).strip() if v is not None else "" for j, v in enumerate(row)})
        return result

    # CSV
    try:
        text_data = file_content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text_data = file_content.decode("latin-1")

    lines = text_data.splitlines()

    # Skip baris kosong/info di awal (BCA punya 5-8 baris header)
    header_idx = skip_rows
    for i, line in enumerate(lines[skip_rows:], start=skip_rows):
        # Baris header biasanya berisi kata: TANGGAL / DATE / TGL
        if re.search(r"\b(TANGGAL|DATE|TGL)\b", line.upper()):
            header_idx = i
            break

    reader  = csv.DictReader(lines[header_idx:])
    records = []
    for row in reader:
        if not any(row.values()):
            continue
        records.append({k.strip().upper(): v.strip() for k, v in row.items() if k})
    return records


def parse_bank_statement(file_content: bytes, filename: str, skip_rows: int = 0) -> list[dict]:
    """
    Entry point parser: auto-detect bank format lalu parse ke list transaksi standar.
    """
    raw_rows = read_bank_file(file_content, filename, skip_rows)
    if not raw_rows:
        return []

    headers  = list(raw_rows[0].keys())
    bank     = _detect_bank(headers)
    logger.info(f"Detected bank format: {bank} ({len(raw_rows)} rows)")

    parsers = {
        "BCA":     _parse_bca,
        "MANDIRI": _parse_mandiri,
        "BNI":     _parse_bni,
        "BRI":     _parse_bri,
        "GENERIC": _parse_generic,
    }
    return parsers.get(bank, _parse_generic)(raw_rows)


# ── Reconciliation Engine ──────────────────────────────────────────────────────

class BankReconciler:

    def __init__(self, db: Session):
        self.db      = db
        self.journal = JournalEngine(db)

    # ── Import ─────────────────────────────────────────────────────────────────

    def import_statement(
        self,
        bank_account_id: UUID,
        transactions: list[dict],
    ) -> dict:
        """Simpan transaksi bank ke tabel bank_statement_line. Skip duplikat."""
        inserted = skipped = 0

        for tx in transactions:
            if not tx.get("transaction_date"):
                skipped += 1
                continue
            if tx["debit_amount"] == 0 and tx["credit_amount"] == 0:
                skipped += 1
                continue

            # Cek duplikat: tanggal + deskripsi + amount
            dup = self.db.execute(
                text("""
                    SELECT id FROM bank_statement_line
                    WHERE bank_account_id = :bid
                      AND transaction_date = :td
                      AND description      = :desc
                      AND debit_amount     = :dr
                      AND credit_amount    = :cr
                    LIMIT 1
                """),
                {
                    "bid":  str(bank_account_id),
                    "td":   tx["transaction_date"],
                    "desc": tx["description"],
                    "dr":   float(tx["debit_amount"]),
                    "cr":   float(tx["credit_amount"]),
                }
            ).fetchone()

            if dup:
                skipped += 1
                continue

            import json as _json
            self.db.execute(
                text("""
                    INSERT INTO bank_statement_line
                        (id, bank_account_id, transaction_date,
                         description, reference_no,
                         debit_amount, credit_amount, balance,
                         match_status, raw_data)
                    VALUES
                        (:id, :bid, :td, :desc, :ref,
                         :dr, :cr, :bal,
                         'unmatched', :raw)
                """),
                {
                    "id":   str(uuid4()),
                    "bid":  str(bank_account_id),
                    "td":   tx["transaction_date"],
                    "desc": tx.get("description", ""),
                    "ref":  tx.get("reference_no"),
                    "dr":   float(tx["debit_amount"]),
                    "cr":   float(tx["credit_amount"]),
                    "bal":  float(tx.get("balance") or 0),
                    "raw":  _json.dumps({k: str(v) for k, v in tx.items()}, ensure_ascii=False),
                }
            )
            inserted += 1

        self.db.commit()
        logger.info(f"Import selesai: {inserted} baru, {skipped} dilewati")
        return {"inserted": inserted, "skipped": skipped}

    # ── Auto-match ─────────────────────────────────────────────────────────────

    def auto_match(self, bank_account_id: UUID, tolerance_days: int = 7) -> dict:
        """
        Auto-match transaksi unmatched:
        - Kredit (uang masuk)  → coba match ke AR invoice outstanding
        - Debit (uang keluar)  → coba match ke AP invoice outstanding

        Strategi match (berurutan):
        1. Exact amount + reference number di deskripsi
        2. Exact amount + date dalam toleransi ± N hari
        3. Exact amount saja (ambiguous — flagged sebagai 'partial')
        """
        unmatched = self.db.execute(
            text("""
                SELECT * FROM bank_statement_line
                WHERE bank_account_id = :bid
                  AND match_status    = 'unmatched'
                ORDER BY transaction_date
            """),
            {"bid": str(bank_account_id)}
        ).fetchall()

        matched = partial = 0
        for row in unmatched:
            tx = dict(row._mapping)

            # Kredit → AR invoice (uang masuk dari customer)
            if float(tx["credit_amount"]) > 0:
                result = self._match_to_ar(tx, tolerance_days)
            # Debit → AP invoice (uang keluar ke vendor)
            elif float(tx["debit_amount"]) > 0:
                result = self._match_to_ap(tx, tolerance_days)
            else:
                continue

            if result == "matched":
                matched += 1
            elif result == "partial":
                partial += 1

        self.db.commit()
        logger.info(f"Auto-match selesai: {matched} matched, {partial} partial")
        return {
            "total_processed": len(unmatched),
            "matched":         matched,
            "partial":         partial,
            "still_unmatched": len(unmatched) - matched - partial,
        }

    def _match_to_ar(self, tx: dict, tolerance_days: int) -> str:
        """Coba match transaksi kredit ke AR invoice."""
        amount   = Decimal(str(tx["credit_amount"]))
        tx_date  = tx["transaction_date"]
        desc     = (tx["description"] or "").upper()
        ref      = (tx["reference_no"] or "").upper()

        # Query AR invoice yang outstanding dengan amount sama
        candidates = self.db.execute(
            text("""
                SELECT id, invoice_no, invoice_date, due_date,
                       total_amount, paid_amount, customer_name,
                       (total_amount - paid_amount) AS outstanding,
                       entity_id
                FROM ar_invoice
                WHERE status NOT IN ('paid','cancelled')
                  AND (total_amount - paid_amount) BETWEEN :amt_low AND :amt_high
                ORDER BY ABS(due_date - :td) ASC
                LIMIT 10
            """),
            {
                "amt_low":  float(amount * Decimal("0.99")),
                "amt_high": float(amount * Decimal("1.01")),
                "td":       tx_date,
            }
        ).fetchall()

        if not candidates:
            return "none"

        best = None
        score = 0

        for c in candidates:
            inv = dict(c._mapping)
            s   = 0

            # Referensi cocok di deskripsi
            if inv["invoice_no"].upper() in desc or inv["invoice_no"].upper() in ref:
                s += 3
            # Nama customer di deskripsi
            if any(w in desc for w in inv["customer_name"].upper().split() if len(w) > 3):
                s += 2
            # Tanggal dalam toleransi
            if inv["due_date"] and abs((tx_date - inv["due_date"]).days) <= tolerance_days:
                s += 1

            if s > score:
                score = s
                best  = inv

        if best is None:
            best  = dict(candidates[0]._mapping)
            score = 0

        status = "matched" if score >= 2 else "partial"
        self.db.execute(
            text("""
                UPDATE bank_statement_line SET
                    match_status       = :status,
                    matched_invoice_id = :inv_id
                WHERE id = :id
            """),
            {"status": status, "inv_id": str(best["id"]), "id": str(tx["id"])}
        )
        return status

    def _match_to_ap(self, tx: dict, tolerance_days: int) -> str:
        """Coba match transaksi debit ke AP invoice."""
        amount  = Decimal(str(tx["debit_amount"]))
        tx_date = tx["transaction_date"]
        desc    = (tx["description"] or "").upper()
        ref     = (tx["reference_no"] or "").upper()

        # AP outstanding (amount yang harus dibayar = total - pph - paid)
        candidates = self.db.execute(
            text("""
                SELECT ai.id, ai.invoice_no, ai.invoice_date, ai.due_date,
                       ai.total_amount, ai.pph_amount, ai.paid_amount,
                       v.vendor_name,
                       (ai.total_amount - ai.pph_amount - ai.paid_amount) AS ap_outstanding,
                       ai.entity_id
                FROM ap_invoice ai
                JOIN vendor v ON v.id = ai.vendor_id
                WHERE ai.status NOT IN ('paid','cancelled')
                  AND (ai.total_amount - ai.pph_amount - ai.paid_amount)
                      BETWEEN :amt_low AND :amt_high
                ORDER BY ABS(ai.due_date - :td) ASC NULLS LAST
                LIMIT 10
            """),
            {
                "amt_low":  float(amount * Decimal("0.99")),
                "amt_high": float(amount * Decimal("1.01")),
                "td":       tx_date,
            }
        ).fetchall()

        if not candidates:
            return "none"

        best  = None
        score = 0

        for c in candidates:
            inv = dict(c._mapping)
            s   = 0
            if inv["invoice_no"].upper() in desc or inv["invoice_no"].upper() in ref:
                s += 3
            if any(w in desc for w in inv["vendor_name"].upper().split() if len(w) > 3):
                s += 2
            if inv["due_date"] and abs((tx_date - inv["due_date"]).days) <= tolerance_days:
                s += 1
            if s > score:
                score = s
                best  = inv

        if best is None:
            best  = dict(candidates[0]._mapping)
            score = 0

        status = "matched" if score >= 2 else "partial"
        self.db.execute(
            text("""
                UPDATE bank_statement_line SET
                    match_status       = :status,
                    matched_invoice_id = :inv_id
                WHERE id = :id
            """),
            {"status": status, "inv_id": str(best["id"]), "id": str(tx["id"])}
        )
        return status

    # ── Post Journal untuk transaksi matched ───────────────────────────────────

    def post_matched_journal(
        self,
        statement_line_id: UUID,
        bank_coa: str,
        posted_by: str = "system",
    ) -> dict:
        """
        Posting jurnal untuk 1 transaksi bank yang sudah matched:
        Kredit (terima AR):  Dr. Kas/Bank | Cr. Piutang Usaha
        Debit  (bayar AP):   Dr. Hutang Usaha | Cr. Kas/Bank
        """
        line = self.db.execute(
            text("""
                SELECT bsl.*, ba.entity_id, ba.coa_id
                FROM bank_statement_line bsl
                JOIN bank_account ba ON ba.id = bsl.bank_account_id
                WHERE bsl.id = :id
            """),
            {"id": str(statement_line_id)}
        ).fetchone()

        if not line:
            return {"success": False, "error": "Statement line tidak ditemukan"}
        if line.match_status not in ("matched", "partial"):
            return {"success": False, "error": "Transaksi belum matched"}
        if line.journal_id:
            return {"success": False, "error": "Jurnal sudah pernah diposting"}

        tx      = dict(line._mapping)
        entity  = str(tx["entity_id"])
        tx_date = tx["transaction_date"]
        credit  = Decimal(str(tx["credit_amount"]))
        debit   = Decimal(str(tx["debit_amount"]))
        desc    = tx["description"] or "Bank transaction"

        if credit > 0:
            # Uang masuk — AR receipt
            journal_lines = [
                JournalLine(account_code=bank_coa,  description=desc, debit_idr=credit),
                JournalLine(account_code="1-1-002", description=desc, credit_idr=credit),
            ]
            jtype = "AR"
        else:
            # Uang keluar — AP payment
            journal_lines = [
                JournalLine(account_code="2-1-001", description=desc, debit_idr=debit),
                JournalLine(account_code=bank_coa,  description=desc, credit_idr=debit),
            ]
            jtype = "AP"

        entry  = JournalEntry(
            entity_id=entity,
            journal_type=jtype,
            journal_date=tx_date,
            description=f"Bank {jtype} — {desc[:80]}",
            lines=journal_lines,
            reference_no=tx.get("reference_no"),
            source="bank_sync",
            created_by=posted_by,
        )
        result = self.journal.post_journal(entry)

        if result["success"]:
            self.db.execute(
                text("""
                    UPDATE bank_statement_line
                    SET journal_id = :jid, match_status = 'matched'
                    WHERE id = :id
                """),
                {"jid": result["journal_id"], "id": str(statement_line_id)}
            )
            self.db.commit()

        return result

    # ── Summary ────────────────────────────────────────────────────────────────

    def get_reconciliation_summary(self, bank_account_id: UUID) -> dict:
        """Ringkasan status rekonsiliasi untuk satu rekening bank."""
        row = self.db.execute(
            text("""
                SELECT
                    COUNT(*)                                          AS total,
                    SUM(CASE WHEN match_status='matched'  THEN 1 END) AS matched,
                    SUM(CASE WHEN match_status='partial'  THEN 1 END) AS partial,
                    SUM(CASE WHEN match_status='unmatched'THEN 1 END) AS unmatched,
                    SUM(CASE WHEN match_status='ignored'  THEN 1 END) AS ignored,
                    SUM(credit_amount) AS total_credit,
                    SUM(debit_amount)  AS total_debit,
                    MIN(transaction_date) AS date_from,
                    MAX(transaction_date) AS date_to
                FROM bank_statement_line
                WHERE bank_account_id = :bid
            """),
            {"bid": str(bank_account_id)}
        ).fetchone()

        r = dict(row._mapping)
        return {
            "bank_account_id":   str(bank_account_id),
            "total_lines":       r["total"]     or 0,
            "matched":           r["matched"]   or 0,
            "partial":           r["partial"]   or 0,
            "unmatched":         r["unmatched"] or 0,
            "ignored":           r["ignored"]   or 0,
            "total_credit":      float(r["total_credit"] or 0),
            "total_debit":       float(r["total_debit"]  or 0),
            "net_flow":          float((r["total_credit"] or 0) - (r["total_debit"] or 0)),
            "date_from":         str(r["date_from"]) if r["date_from"] else None,
            "date_to":           str(r["date_to"])   if r["date_to"]   else None,
        }
