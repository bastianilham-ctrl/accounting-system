from uuid import UUID, uuid4
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger
from modules.ocr_invoice import OCRInvoice
from modules.journal_engine import JournalEngine, JournalEntry, JournalLine

class APProcessor:
    def __init__(self, db: Session):
        self.db = db
        self.ocr = OCRInvoice()
        self.engine = JournalEngine(db)

    def process_invoice_upload(self, file_path: str, entity_id: UUID, user_id: str):
        """
        Workflow Utama:
        1. OCR Extract
        2. Vendor Matching & Tax Profile Lookup
        3. Tax Calculation (PPh & PPN)
        4. Create AP Invoice Record
        5. Auto-Post to Journal Engine
        """
        # 1. OCR
        ocr_data = self.ocr.process(file_path)
        if not ocr_data.get("success"):
            return ocr_data

        # 2. Vendor Matching
        vendor = self._find_vendor(entity_id, ocr_data)
        if not vendor:
            return {"success": False, "error": "Vendor tidak terdaftar. Mohon daftarkan vendor terlebih dahulu."}

        # 3. Get Effective Tax Treatment dari View vw_vendor_tax_status
        tax_profile = self._get_tax_profile(vendor["id"])
        
        # 4. Calculate Taxes
        subtotal = Decimal(str(ocr_data.get("subtotal", 0)))
        pph_rate = Decimal(str(tax_profile["effective_pph_rate"]))
        pph_amount = (subtotal * pph_rate / 100).quantize(Decimal("1"))
        
        # PPN (asumsi 11% jika vendor PKP)
        ppn_amount = Decimal(str(ocr_data.get("ppn_amount", 0)))
        total_payable = subtotal + ppn_amount - pph_amount

        # 5. Prepare Journal Entry
        lines = [
            # Debit: Expense (Account code harusnya didapat dari vendor.coa_suggestion)
            JournalLine(
                account_code=vendor.get("default_coa", "6-1-001"), 
                description=f"Beban {ocr_data['description']}",
                debit_idr=subtotal
            ),
            # Debit: PPN Masukan (Jika ada)
            JournalLine(
                account_code="1-2-003", # Contoh kode PPN Masukan
                description=f"PPN Masukan {ocr_data['invoice_no']}",
                debit_idr=ppn_amount
            ) if ppn_amount > 0 else None,
            # Credit: Hutang PPh (Jika ada)
            JournalLine(
                account_code="2-1-002",
                description=f"Hutang {tax_profile['pph_treatment']} {vendor['vendor_name']}",
                credit_idr=pph_amount
            ) if pph_amount > 0 else None,
            # Credit: Hutang Usaha
            JournalLine(
                account_code="2-1-001",
                description=f"Hutang Usaha {vendor['vendor_name']}",
                credit_idr=total_payable,
                vendor_id=vendor["id"]
            )
        ]
        
        # Bersihkan lines dari None
        lines = [l for l in lines if l is not None]

        entry = JournalEntry(
            entity_id=entity_id,
            journal_type="AP",
            journal_date=ocr_data["invoice_date"],
            description=f"Recording Invoice {ocr_data['invoice_no']} - {vendor['vendor_name']}",
            reference_no=ocr_data["invoice_no"],
            lines=lines,
            source="OCR",
            created_by=user_id
        )

        # 6. Post
        result = self.engine.post_journal(entry)
        return {
            "success": True,
            "invoice_no": ocr_data["invoice_no"],
            "vendor": vendor["vendor_name"],
            "tax_treatment": tax_profile["pph_treatment"],
            "pph_amount": float(pph_amount),
            "journal_no": result.get("journal_no")
        }

    def _find_vendor(self, entity_id, ocr_data):
        # Logika pencocokan vendor berdasarkan NPWP (prioritas) atau Nama
        sql = text("""
            SELECT id, vendor_name, vendor_code FROM vendor 
            WHERE entity_id = :eid AND (npwp = :npwp OR vendor_name ILIKE :name)
            LIMIT 1
        """)
        res = self.db.execute(sql, {"eid": str(entity_id), "npwp": ocr_data.get("vendor_npwp"), "name": f"%{ocr_data.get('vendor_name')}%"}).fetchone()
        return dict(res._mapping) if res else None

    def _get_tax_profile(self, vendor_id):
        sql = text("SELECT pph_treatment, effective_pph_rate FROM vw_vendor_tax_status WHERE id = :id")
        res = self.db.execute(sql, {"id": str(vendor_id)}).fetchone()
        return dict(res._mapping)