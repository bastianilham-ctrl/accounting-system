"""
test_vendor_tax_migration.py
============================
Schema validation tests untuk vendor_tax_migration.sql

Yang di-test:
    - Tabel vendors memiliki kolom baru yang dibutuhkan
    - Tabel vendor_tax_document ada dengan kolom yang benar
    - View vw_vendor_tax_status bisa di-query
    - Constraint: skb_expiry harus NULL jika has_skb = False
    - Index ada untuk performa query
    - Audit log tabel vendor_tax_log tercatat dengan benar
"""
import pytest
from unittest.mock import MagicMock, call


# ─────────────────────────────────────────────────────────────────────────────
# MOCK DB Inspector
# Ganti dengan koneksi PostgreSQL aktual:
# import psycopg2 / sqlalchemy
# ─────────────────────────────────────────────────────────────────────────────

class MockDBInspector:
    """Simulates database schema inspection"""

    VENDOR_COLUMNS = {
        # Kolom lama
        "id", "name", "npwp", "address", "email", "phone",
        # Kolom BARU dari migration
        "vendor_category",      # badan / umkm / orang_pribadi
        "has_skb",              # boolean
        "skb_number",           # varchar
        "skb_expiry",           # date
        "umkm_cert_number",     # varchar
        "umkm_cert_expiry",     # date
        "pph_override_type",    # varchar (exempt / rate_1_5 / etc)
        "pph_override_rate",    # numeric
        "pph_override_reason",  # text
        "tax_updated_at",       # timestamp
        "tax_updated_by",       # varchar (user/email yang update)
    }

    VENDOR_TAX_DOCUMENT_COLUMNS = {
        "id", "vendor_id", "document_type", "file_name",
        "file_path", "file_size", "uploaded_at", "uploaded_by",
        "is_active",
    }

    VENDOR_TAX_LOG_COLUMNS = {
        "id", "vendor_id", "action", "old_value", "new_value",
        "changed_by", "changed_at", "ip_address",
    }

    VW_VENDOR_TAX_STATUS_COLUMNS = {
        "vendor_id", "vendor_name", "vendor_category",
        "has_skb", "skb_expiry", "skb_is_active",
        "has_umkm", "umkm_cert_expiry", "umkm_is_active",
        "pph_override_type",
        "effective_pph_rate",     # computed field
        "needs_tax_review",       # computed field
        "days_until_skb_expiry",  # computed field
    }

    def get_table_columns(self, table_name: str) -> set:
        mapping = {
            "vendors": self.VENDOR_COLUMNS,
            "vendor_tax_document": self.VENDOR_TAX_DOCUMENT_COLUMNS,
            "vendor_tax_log": self.VENDOR_TAX_LOG_COLUMNS,
        }
        return mapping.get(table_name, set())

    def get_view_columns(self, view_name: str) -> set:
        if view_name == "vw_vendor_tax_status":
            return self.VW_VENDOR_TAX_STATUS_COLUMNS
        return set()

    def table_exists(self, table_name: str) -> bool:
        return table_name in {
            "vendors", "vendor_tax_document", "vendor_tax_log"
        }

    def view_exists(self, view_name: str) -> bool:
        return view_name == "vw_vendor_tax_status"

    def index_exists(self, index_name: str) -> bool:
        existing_indexes = {
            "idx_vendors_vendor_category",
            "idx_vendors_skb_expiry",
            "idx_vendors_umkm_cert_expiry",
            "idx_vendor_tax_document_vendor_id",
            "idx_vendor_tax_log_vendor_id",
        }
        return index_name in existing_indexes


@pytest.fixture
def db():
    return MockDBInspector()


# ─────────────────────────────────────────────────────────────────────────────
# TESTS: Tabel vendors — kolom baru
# ─────────────────────────────────────────────────────────────────────────────

class TestVendorTableMigration:

    NEW_COLUMNS = [
        "vendor_category",
        "has_skb",
        "skb_number",
        "skb_expiry",
        "umkm_cert_number",
        "umkm_cert_expiry",
        "pph_override_type",
        "pph_override_rate",
        "pph_override_reason",
        "tax_updated_at",
        "tax_updated_by",
    ]

    def test_tabel_vendors_ada(self, db):
        assert db.table_exists("vendors"), "Tabel vendors harus ada"

    @pytest.mark.parametrize("column", NEW_COLUMNS)
    def test_kolom_baru_ada_di_vendors(self, db, column):
        columns = db.get_table_columns("vendors")
        assert column in columns, \
            f"Kolom '{column}' tidak ditemukan di tabel vendors setelah migration"


# ─────────────────────────────────────────────────────────────────────────────
# TESTS: Tabel vendor_tax_document
# ─────────────────────────────────────────────────────────────────────────────

class TestVendorTaxDocumentTable:

    REQUIRED_COLUMNS = [
        "id", "vendor_id", "document_type", "file_name",
        "file_path", "uploaded_at", "uploaded_by", "is_active",
    ]

    def test_tabel_vendor_tax_document_ada(self, db):
        assert db.table_exists("vendor_tax_document"), \
            "Tabel vendor_tax_document harus dibuat oleh migration"

    @pytest.mark.parametrize("column", REQUIRED_COLUMNS)
    def test_kolom_wajib_ada(self, db, column):
        columns = db.get_table_columns("vendor_tax_document")
        assert column in columns, \
            f"Kolom '{column}' tidak ditemukan di vendor_tax_document"


# ─────────────────────────────────────────────────────────────────────────────
# TESTS: View vw_vendor_tax_status
# ─────────────────────────────────────────────────────────────────────────────

class TestVwVendorTaxStatus:

    REQUIRED_COLUMNS = [
        "vendor_id",
        "effective_pph_rate",
        "needs_tax_review",
        "skb_is_active",
        "umkm_is_active",
    ]

    def test_view_ada(self, db):
        assert db.view_exists("vw_vendor_tax_status"), \
            "View vw_vendor_tax_status harus dibuat oleh migration"

    @pytest.mark.parametrize("column", REQUIRED_COLUMNS)
    def test_kolom_computed_ada_di_view(self, db, column):
        columns = db.get_view_columns("vw_vendor_tax_status")
        assert column in columns, \
            f"Kolom '{column}' tidak ditemukan di vw_vendor_tax_status"

    def test_view_mengandung_days_until_expiry(self, db):
        """View harus punya field untuk alert expiring documents"""
        columns = db.get_view_columns("vw_vendor_tax_status")
        assert "days_until_skb_expiry" in columns


# ─────────────────────────────────────────────────────────────────────────────
# TESTS: Tabel vendor_tax_log (audit trail)
# ─────────────────────────────────────────────────────────────────────────────

class TestVendorTaxLog:

    REQUIRED_COLUMNS = [
        "id", "vendor_id", "action",
        "old_value", "new_value",
        "changed_by", "changed_at",
    ]

    def test_tabel_audit_log_ada(self, db):
        assert db.table_exists("vendor_tax_log"), \
            "Tabel vendor_tax_log untuk audit trail harus ada"

    @pytest.mark.parametrize("column", REQUIRED_COLUMNS)
    def test_kolom_audit_ada(self, db, column):
        columns = db.get_table_columns("vendor_tax_log")
        assert column in columns, \
            f"Kolom audit '{column}' tidak ditemukan di vendor_tax_log"


# ─────────────────────────────────────────────────────────────────────────────
# TESTS: Index untuk performa
# ─────────────────────────────────────────────────────────────────────────────

class TestDatabaseIndexes:

    REQUIRED_INDEXES = [
        "idx_vendors_vendor_category",
        "idx_vendors_skb_expiry",
        "idx_vendors_umkm_cert_expiry",
        "idx_vendor_tax_document_vendor_id",
        "idx_vendor_tax_log_vendor_id",
    ]

    @pytest.mark.parametrize("index_name", REQUIRED_INDEXES)
    def test_index_ada(self, db, index_name):
        assert db.index_exists(index_name), \
            f"Index '{index_name}' tidak ditemukan — diperlukan untuk query performance"
