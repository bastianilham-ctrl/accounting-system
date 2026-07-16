from sqlalchemy import text
from sqlalchemy.orm import Session
from datetime import date, timedelta
from uuid import uuid4
from loguru import logger

class NotificationEngine:
    def __init__(self, db: Session):
        self.db = db

    def create_notification(self, entity_id, n_type, severity, message, ref_id=None):
        """Simpan notifikasi ke database dengan proteksi duplikasi."""
        try:
            self.db.execute(
                text("""
                    INSERT INTO notifications (id, entity_id, type, severity, message, ref_id, is_read, created_at)
                    VALUES (:id, :eid, :type, :sev, :msg, :ref, FALSE, NOW())
                    ON CONFLICT (entity_id, type, ref_id) WHERE is_read = FALSE DO NOTHING
                """),
                {
                    "id": str(uuid4()),
                    "eid": str(entity_id),
                    "type": n_type,
                    "sev": severity,
                    "msg": message,
                    "ref": str(ref_id) if ref_id else None
                }
            )
            self.db.commit()
        except Exception as e:
            logger.error(f"Gagal membuat notifikasi: {e}")
            self.db.rollback()

    def check_overdue_invoices(self):
        """Cek AR dan AP yang melewati due_date."""
        logger.info("Scanning for overdue invoices...")
        
        # 1. Overdue AR (Piutang Klien)
        ar_rows = self.db.execute(text("""
            SELECT id, entity_id, invoice_no, customer_name, total_amount - paid_amount as outstanding
            FROM ar_invoice
            WHERE status NOT IN ('paid', 'cancelled') AND due_date < CURRENT_DATE
        """)).fetchall()
        
        for row in ar_rows:
            msg = f"Invoice AR {row.invoice_no} ({row.customer_name}) OVERDUE. Sisa tagihan: Rp {row.outstanding:,.0f}"
            self.create_notification(row.entity_id, "overdue_ar", "critical", msg, row.id)

        # 2. Overdue AP (Hutang Vendor)
        ap_rows = self.db.execute(text("""
            SELECT ai.id, ai.entity_id, ai.invoice_no, v.vendor_name
            FROM ap_invoice ai
            JOIN vendor v ON v.id = ai.vendor_id
            WHERE ai.status NOT IN ('paid', 'cancelled') AND ai.due_date < CURRENT_DATE
        """)).fetchall()
        
        for row in ap_rows:
            msg = f"Invoice AP {row.invoice_no} ({row.vendor_name}) OVERDUE. Perlu segera dibayar."
            self.create_notification(row.entity_id, "overdue_ap", "warning", msg, row.id)

    def check_low_stock(self):
        """Cek stok barang berdasarkan view alert yang sudah ada."""
        logger.info("Scanning for low stock items...")
        # Menggunakan view vw_low_stock_alert dari inventory_engine
        rows = self.db.execute(text("""
            SELECT product_id, entity_id, product_name, qty_available, safety_stock
            FROM vw_low_stock_alert
        """)).fetchall()
        
        for row in rows:
            msg = f"Stok kritis: {row.product_name}. Tersedia {row.qty_available}, batas aman {row.safety_stock}."
            self.create_notification(row.entity_id, "low_stock", "warning", msg, row.product_id)

    def check_contract_expiry(self, days_threshold=30):
        """Cek kontrak yang akan berakhir dalam waktu dekat."""
        logger.info(f"Scanning for contracts expiring within {days_threshold} days...")
        cutoff = date.today() + timedelta(days=days_threshold)
        
        rows = self.db.execute(text("""
            SELECT id, entity_id, contract_number, contract_title, end_date
            FROM project_contract
            WHERE end_date <= :cutoff AND contract_status IN ('ACTIVE', 'AMENDED')
        """), {"cutoff": cutoff}).fetchall()
        
        for row in rows:
            days_left = (row.end_date - date.today()).days
            severity = "critical" if days_left <= 7 else "warning"
            status_txt = "EXPIRED" if days_left < 0 else f"berakhir dalam {days_left} hari"
            
            msg = f"Kontrak {row.contract_number} ({row.contract_title}) {status_txt}."
            self.create_notification(row.entity_id, "contract_expiry", severity, msg, row.id)

    def run_all_checks(self):
        """Fungsi utama untuk dijalankan oleh scheduler."""
        self.check_overdue_invoices()
        self.check_low_stock()
        self.check_contract_expiry()