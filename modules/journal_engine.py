# modules/journal_engine.py
# Core journal posting engine dengan double entry validation

from uuid import UUID, uuid4
from decimal import Decimal
from datetime import date, datetime
from typing import List, Optional
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger


# ============================================================
# DATA CLASSES — input untuk posting jurnal
# ============================================================

@dataclass
class JournalLine:
    account_code: str
    description: str
    debit_idr: Decimal = Decimal("0")
    credit_idr: Decimal = Decimal("0")
    vendor_id: Optional[UUID] = None
    cost_center: Optional[str] = None
    project_code: Optional[str] = None
    tax_code: Optional[str] = None
    tax_amount: Decimal = Decimal("0")
    currency: Optional[str] = None        # mata uang asli baris ini, None/'IDR' = tidak ada FCY
    amount_fcy: Optional[Decimal] = None  # jumlah dalam mata uang asli (sebelum konversi)
    exchange_rate: Optional[Decimal] = None  # kurs yang dipakai saat posting (1 FCY = rate IDR)


@dataclass
class JournalEntry:
    entity_id: UUID
    journal_type: str           # 'AP', 'AR', 'GL', 'BANK', 'ASSET', 'PREPAID'
    journal_date: date
    description: str
    lines: List[JournalLine]
    reference_no: Optional[str] = None
    currency: str = "IDR"
    fx_rate: Decimal = Decimal("1")
    source: str = "manual"      # 'OCR', 'bank_sync', 'auto', 'manual'
    created_by: str = "system"


# ============================================================
# JOURNAL ENGINE
# ============================================================

class JournalEngine:

    def __init__(self, db: Session):
        self.db = db

    # ----------------------------------------------------------
    # PUBLIC: posting jurnal
    # ----------------------------------------------------------

    def post_journal(self, entry: JournalEntry) -> dict:
        """
        Validasi dan posting jurnal ke database.
        Return: {'success': bool, 'journal_no': str, 'journal_id': str, 'error': str}
        """
        # 1. Validasi double entry
        validation = self._validate_double_entry(entry.lines)
        if not validation["valid"]:
            logger.warning(f"Double entry validation failed: {validation['error']}")
            return {"success": False, "error": validation["error"]}

        # 2. Cek period tidak terkunci
        period = self._get_or_create_period(entry.entity_id, entry.journal_date)
        if period["is_locked"]:
            return {"success": False, "error": f"Period {period['period_name']} sudah dikunci"}

        # 3. Generate journal number
        journal_no = self._generate_journal_no(entry.entity_id, entry.journal_type, entry.journal_date)

        # 4. Resolve account IDs dari account_code
        lines_with_ids = self._resolve_account_ids(entry.entity_id, entry.lines)
        if lines_with_ids is None:
            return {"success": False, "error": "Satu atau lebih account_code tidak ditemukan di COA"}

        # 5. Insert ke database
        journal_id = self._insert_journal(
            entry=entry,
            period_id=period["id"],
            journal_no=journal_no,
            lines=lines_with_ids,
        )

        logger.info(f"Journal posted: {journal_no} | {entry.journal_type} | {entry.description}")
        return {
            "success": True,
            "journal_no": journal_no,
            "journal_id": str(journal_id),
        }

    def reverse_journal(self, journal_id: UUID, reason: str, reversed_by: str) -> dict:
        """Buat jurnal balik (reverse) dari jurnal yang sudah diposting."""
        result = self.db.execute(
            text("SELECT * FROM gl_journal WHERE id = :id AND status = 'posted'"),
            {"id": str(journal_id)}
        ).fetchone()

        if not result:
            return {"success": False, "error": "Jurnal tidak ditemukan atau belum posted"}

        lines = self.db.execute(
            text("SELECT * FROM gl_line WHERE journal_id = :jid ORDER BY line_no"),
            {"jid": str(journal_id)}
        ).fetchall()

        # Balik setiap baris: debit jadi credit, credit jadi debit
        reversed_lines = []
        for line in lines:
            reversed_lines.append(JournalLine(
                account_code=self._get_account_code(line.account_id),
                description=f"[REVERSAL] {line.description or ''}",
                debit_idr=Decimal(str(line.credit_idr)),
                credit_idr=Decimal(str(line.debit_idr)),
                vendor_id=line.vendor_id,
                tax_code=line.tax_code,
                tax_amount=Decimal(str(line.tax_amount or 0)),
                currency=line.currency,
                amount_fcy=Decimal(str(line.amount_fcy)) if line.amount_fcy is not None else None,
                exchange_rate=Decimal(str(line.exchange_rate)) if line.exchange_rate is not None else None,
            ))

        reverse_entry = JournalEntry(
            entity_id=result.entity_id,
            journal_type=result.journal_type,
            journal_date=date.today(),
            description=f"[REVERSAL] {result.description} — {reason}",
            lines=reversed_lines,
            reference_no=result.journal_no,
            source="manual",
            created_by=reversed_by,
        )

        post_result = self.post_journal(reverse_entry)
        if post_result["success"]:
            # Update status jurnal asal menjadi 'reversed'
            self.db.execute(
                text("UPDATE gl_journal SET status = 'reversed' WHERE id = :id"),
                {"id": str(journal_id)}
            )
            self.db.commit()

        return post_result

    # ----------------------------------------------------------
    # PRIVATE: validasi
    # ----------------------------------------------------------

    def _validate_double_entry(self, lines: List[JournalLine]) -> dict:
        """Pastikan total debit = total credit dan tidak ada baris kosong."""
        if not lines:
            return {"valid": False, "error": "Jurnal harus memiliki minimal 2 baris"}

        if len(lines) < 2:
            return {"valid": False, "error": "Double entry minimal 2 baris (1 debit, 1 credit)"}

        total_debit  = sum(l.debit_idr  for l in lines)
        total_credit = sum(l.credit_idr for l in lines)

        # Toleransi pembulatan 1 rupiah
        if abs(total_debit - total_credit) > Decimal("1"):
            return {
                "valid": False,
                "error": (
                    f"Debit ({total_debit:,.2f}) != Credit ({total_credit:,.2f}). "
                    f"Selisih: {abs(total_debit - total_credit):,.2f}"
                ),
            }

        # Cek tidak ada baris yang debit DAN credit sekaligus
        for i, line in enumerate(lines):
            if line.debit_idr > 0 and line.credit_idr > 0:
                return {
                    "valid": False,
                    "error": f"Baris {i+1} ({line.account_code}): tidak boleh isi debit dan credit bersamaan",
                }

        return {"valid": True}

    def _resolve_account_ids(self, entity_id: UUID, lines: List[JournalLine]):
        """Ubah account_code menjadi account_id dari tabel COA."""
        resolved = []
        for line in lines:
            result = self.db.execute(
                text("""
                    SELECT id FROM chart_of_accounts
                    WHERE entity_id = :eid AND account_code = :code AND is_active = TRUE
                """),
                {"eid": str(entity_id), "code": line.account_code}
            ).fetchone()

            if not result:
                logger.error(f"Account code tidak ditemukan: {line.account_code}")
                return None

            resolved.append({**line.__dict__, "account_id": result.id})

        return resolved

    def _get_or_create_period(self, entity_id: UUID, journal_date: date) -> dict:
        """Cari period yang sesuai, buat otomatis jika belum ada."""
        year  = journal_date.year
        month = journal_date.month

        result = self.db.execute(
            text("""
                SELECT id, period_name, is_locked FROM fiscal_period
                WHERE entity_id = :eid AND year = :y AND month = :m
            """),
            {"eid": str(entity_id), "y": year, "m": month}
        ).fetchone()

        if result:
            return {"id": result.id, "period_name": result.period_name, "is_locked": result.is_locked}

        # Auto-create period jika belum ada
        period_id = uuid4()
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        self.db.execute(
            text("""
                INSERT INTO fiscal_period
                    (id, entity_id, period_name, year, month, date_start, date_end)
                VALUES
                    (:id, :eid, :name, :y, :m, :ds, :de)
            """),
            {
                "id": str(period_id), "eid": str(entity_id),
                "name": f"{year}-{month:02d}",
                "y": year, "m": month,
                "ds": date(year, month, 1),
                "de": date(year, month, last_day),
            }
        )
        self.db.commit()
        return {"id": period_id, "period_name": f"{year}-{month:02d}", "is_locked": False}

    def _generate_journal_no(self, entity_id: UUID, journal_type: str, journal_date: date) -> str:
        """Generate nomor jurnal otomatis: AP/2026/05/0001"""
        prefix = f"{journal_type}/{journal_date.year}/{journal_date.month:02d}"
        result = self.db.execute(
            text("""
                SELECT COUNT(*) as cnt FROM gl_journal
                WHERE entity_id = :eid
                  AND journal_type = :jt
                  AND EXTRACT(YEAR  FROM journal_date) = :y
                  AND EXTRACT(MONTH FROM journal_date) = :m
            """),
            {
                "eid": str(entity_id), "jt": journal_type,
                "y": journal_date.year, "m": journal_date.month,
            }
        ).fetchone()

        seq = (result.cnt or 0) + 1
        return f"{prefix}/{seq:04d}"

    def _insert_journal(
        self,
        entry: JournalEntry,
        period_id: UUID,
        journal_no: str,
        lines: list,
    ) -> UUID:
        """Insert gl_journal dan gl_line ke database."""
        journal_id = uuid4()

        has_fcy = any(
            line.get("currency") and line["currency"] != "IDR" and line.get("amount_fcy") is not None
            for line in lines
        )

        self.db.execute(
            text("""
                INSERT INTO gl_journal
                    (id, entity_id, period_id, journal_no, journal_type, journal_date,
                     description, reference_no, currency, fx_rate, has_fcy, status, source,
                     posted_by, posted_at, created_by)
                VALUES
                    (:id, :eid, :pid, :jno, :jtype, :jdate,
                     :desc, :ref, :cur, :fx, :has_fcy, 'posted', :src,
                     :posted_by, NOW(), :created_by)
            """),
            {
                "id": str(journal_id), "eid": str(entry.entity_id),
                "pid": str(period_id), "jno": journal_no,
                "jtype": entry.journal_type, "jdate": entry.journal_date,
                "desc": entry.description, "ref": entry.reference_no,
                "cur": entry.currency, "fx": float(entry.fx_rate), "has_fcy": has_fcy,
                "src": entry.source,
                "posted_by": entry.created_by, "created_by": entry.created_by,
            }
        )

        for i, line in enumerate(lines, start=1):
            self.db.execute(
                text("""
                    INSERT INTO gl_line
                        (id, journal_id, line_no, account_id, description,
                         debit_idr, credit_idr, vendor_id, cost_center, project_code,
                         tax_code, tax_amount, currency, amount_fcy, exchange_rate)
                    VALUES
                        (:id, :jid, :lno, :aid, :desc,
                         :dr, :cr, :vid, :cc, :pc,
                         :tc, :ta, :fcur, :famt, :frate)
                """),
                {
                    "id": str(uuid4()), "jid": str(journal_id),
                    "lno": i, "aid": str(line["account_id"]),
                    "desc": line["description"],
                    "dr": float(line["debit_idr"]), "cr": float(line["credit_idr"]),
                    "vid": str(line["vendor_id"]) if line.get("vendor_id") else None,
                    "cc": line.get("cost_center"), "pc": line.get("project_code"),
                    "tc": line.get("tax_code"),
                    "ta": float(line.get("tax_amount", 0)),
                    "fcur": line.get("currency"),
                    "famt": float(line["amount_fcy"]) if line.get("amount_fcy") is not None else None,
                    "frate": float(line["exchange_rate"]) if line.get("exchange_rate") is not None else None,
                }
            )

        self.db.commit()
        return journal_id

    def _get_account_code(self, account_id: UUID) -> str:
        result = self.db.execute(
            text("SELECT account_code FROM chart_of_accounts WHERE id = :id"),
            {"id": str(account_id)}
        ).fetchone()
        return result.account_code if result else ""
