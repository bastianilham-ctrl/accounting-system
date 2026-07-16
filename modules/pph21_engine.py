# modules/pph21_engine.py
# PPh 21 Withholding Tax Engine — pembayaran ke individu (non-pegawai)
#
# Referensi:
#   UU HPP 2021 Pasal 17          : tarif progresif PPh OP
#   PMK 168/2023 Pasal 14-15      : metode perhitungan PPh 21 bukan pegawai
#   Pasal 21 ayat 5a UU PPh       : tarif lebih tinggi 20% jika tanpa NPWP
#
# Kategori yang ditangani:
#   - Tenaga Ahli (konsultan, notaris, pengacara, dokter, akuntan, arsitek)
#     → PKP = 50% × bruto
#   - Bukan Tenaga Ahli (honorarium individual, narasumber, dll)
#     → PKP = bruto (tanpa norma biaya)
#   - Tanpa NPWP → rate × 1.20

from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID, uuid4
from datetime import date
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger

from modules.journal_engine import JournalEngine, JournalEntry, JournalLine


# ── Tarif Progresif UU HPP 2021 Pasal 17 ──────────────────────────────────────
# (batas_atas, rate)  |  batas_atas = None → tidak terbatas (bracket terakhir)

PROGRESSIVE_BRACKETS: list[tuple[Optional[Decimal], Decimal]] = [
    (Decimal("60_000_000"),    Decimal("0.05")),
    (Decimal("250_000_000"),   Decimal("0.15")),
    (Decimal("500_000_000"),   Decimal("0.25")),
    (Decimal("5_000_000_000"), Decimal("0.30")),
    (None,                     Decimal("0.35")),
]

# Keyword untuk identifikasi tenaga ahli
TENAGA_AHLI_KEYWORDS = [
    "konsultan", "consulting", "konsultansi", "notaris", "pengacara",
    "lawyer", "dokter", "akuntan", "accountant", "arsitek", "architect",
    "penilai", "appraiser", "aktuaris", "actuary", "auditor",
    "professional fee", "professional services", "jasa profesional",
    "advisory", "advisor",
]


# ── Pure calculation (no DB dependency) ───────────────────────────────────────

def _progressive_tax(pkp: Decimal) -> tuple[Decimal, list[dict]]:
    """
    Hitung PPh progresif dari PKP.
    Return: (total_pph, breakdown_per_layer)
    """
    if pkp <= 0:
        return Decimal("0"), []

    total     = Decimal("0")
    breakdown = []
    prev      = Decimal("0")

    for limit, rate in PROGRESSIVE_BRACKETS:
        if pkp <= prev:
            break
        upper    = min(pkp, limit) if limit else pkp
        taxable  = upper - prev
        layer_tax = (taxable * rate).quantize(Decimal("1"), ROUND_HALF_UP)
        breakdown.append({
            "layer":          f"s/d Rp {int(limit):,}" if limit else ">Rp 5.000.000.000",
            "taxable_amount": float(taxable),
            "rate_pct":       float(rate * 100),
            "tax":            float(layer_tax),
        })
        total += layer_tax
        prev   = upper
        if limit is None or pkp <= limit:
            break

    return total, breakdown


def calculate_pph21(
    gross_amount: Decimal,
    has_npwp: bool = True,
    is_tenaga_ahli: bool = True,
    ytd_gross_before: Decimal = Decimal("0"),
) -> dict:
    """
    Hitung PPh 21 untuk satu pembayaran ke individu (non-pegawai).

    Args:
        gross_amount     : penghasilan bruto pembayaran ini
        has_npwp         : False → tarif naik 20% (Pasal 21 ayat 5a)
        is_tenaga_ahli   : True  → PKP = 50% × bruto (PMK 168/2023)
                           False → PKP = bruto
        ytd_gross_before : akumulasi penghasilan bruto YTD ke payee ini
                           SEBELUM pembayaran ini (untuk bracket progresif tepat)
    """
    # 1. PKP per metode
    if is_tenaga_ahli:
        pkp_factor = Decimal("0.50")
        pkp_note   = "PKP = 50% × bruto (tenaga ahli, PMK 168/2023 Pasal 14)"
    else:
        pkp_factor = Decimal("1.00")
        pkp_note   = "PKP = bruto (bukan tenaga ahli)"

    pkp_this = (gross_amount * pkp_factor).quantize(Decimal("1"), ROUND_HALF_UP)

    # 2. Cumulative PKP: sebelum dan sesudah pembayaran ini
    pkp_before = (ytd_gross_before * pkp_factor).quantize(Decimal("1"), ROUND_HALF_UP)
    pkp_after  = pkp_before + pkp_this

    tax_after,  _         = _progressive_tax(pkp_after)
    tax_before, _         = _progressive_tax(pkp_before)
    pph_amount            = tax_after - tax_before

    # Breakdown ditampilkan berdasarkan PKP pembayaran ini saja
    _, breakdown = _progressive_tax(pkp_this)

    # 3. Koreksi tanpa NPWP: tarif ×1.20 (Pasal 21 ayat 5a UU PPh)
    if not has_npwp:
        pph_amount = (pph_amount * Decimal("1.20")).quantize(Decimal("1"), ROUND_HALF_UP)

    effective_rate = (
        pph_amount / gross_amount * 100
    ).quantize(Decimal("0.01")) if gross_amount > 0 else Decimal("0")

    return {
        "gross_amount":    float(gross_amount),
        "pkp":             float(pkp_this),
        "pkp_note":        pkp_note,
        "pph21_amount":    float(pph_amount),
        "effective_rate":  float(effective_rate),
        "has_npwp":        has_npwp,
        "npwp_penalty":    not has_npwp,
        "tax_breakdown":   breakdown,
        "ytd_gross_after": float(ytd_gross_before + gross_amount),
        "regulation":      "UU HPP 2021 Ps.17, PMK 168/2023 Ps.14-15",
    }


def is_tenaga_ahli(description: str) -> bool:
    """Heuristik: apakah deskripsi invoice termasuk tenaga ahli."""
    desc_lower = (description or "").lower()
    return any(kw in desc_lower for kw in TENAGA_AHLI_KEYWORDS)


# ── DB-aware engine ────────────────────────────────────────────────────────────

class PPh21Engine:

    def __init__(self, db: Session):
        self.db      = db
        self.journal = JournalEngine(db)

    # ── YTD income helper ──────────────────────────────────────────────────────

    def get_ytd_gross(self, vendor_id: UUID, year: int) -> Decimal:
        """
        Total penghasilan bruto yang sudah dibayarkan ke vendor individu ini
        dalam tahun berjalan (untuk bracket progresif).
        Sumber: ap_invoice.subtotal dengan pph_type LIKE 'PPh21%'.
        """
        row = self.db.execute(
            text("""
                SELECT COALESCE(SUM(subtotal), 0) AS ytd_gross
                FROM ap_invoice
                WHERE vendor_id = :vid
                  AND pph_type LIKE 'PPh21%'
                  AND EXTRACT(YEAR FROM invoice_date) = :year
                  AND status NOT IN ('cancelled')
            """),
            {"vid": str(vendor_id), "year": year}
        ).fetchone()
        return Decimal(str(row.ytd_gross or 0))

    # ── Calculate + auto-post journal ─────────────────────────────────────────

    def calculate_for_invoice(
        self,
        vendor_id: UUID,
        gross_amount: Decimal,
        description: str,
        invoice_date: date,
    ) -> dict:
        """
        Hitung PPh 21 untuk sebuah invoice dari vendor individu.
        Otomatis memperhitungkan YTD income dari vendor yang sama.
        """
        vendor = self._get_vendor(vendor_id)
        if not vendor:
            return {"error": "Vendor tidak ditemukan"}

        has_npwp_flag  = bool(vendor.get("npwp"))
        is_ahli        = is_tenaga_ahli(description)
        ytd_before     = self.get_ytd_gross(vendor_id, invoice_date.year)

        result = calculate_pph21(
            gross_amount      = gross_amount,
            has_npwp          = has_npwp_flag,
            is_tenaga_ahli    = is_ahli,
            ytd_gross_before  = ytd_before,
        )
        result["vendor_name"]    = vendor.get("vendor_name")
        result["vendor_npwp"]    = vendor.get("npwp")
        result["is_tenaga_ahli"] = is_ahli
        return result

    # ── YTD summary per payee ─────────────────────────────────────────────────

    def get_ytd_summary(self, entity_id: UUID, year: int) -> list[dict]:
        """
        Ringkasan PPh 21 YTD per payee individu untuk entity tertentu.
        """
        rows = self.db.execute(
            text("""
                SELECT
                    v.id            AS vendor_id,
                    v.vendor_code,
                    v.vendor_name,
                    v.npwp,
                    COUNT(ai.id)                    AS invoice_count,
                    SUM(ai.subtotal)                AS total_bruto,
                    SUM(ai.pph_amount)              AS total_pph21,
                    MIN(ai.invoice_date)            AS first_invoice,
                    MAX(ai.invoice_date)            AS last_invoice
                FROM ap_invoice ai
                JOIN vendor v ON v.id = ai.vendor_id
                WHERE ai.entity_id  = :eid
                  AND ai.pph_type LIKE 'PPh21%'
                  AND EXTRACT(YEAR FROM ai.invoice_date) = :year
                  AND ai.status NOT IN ('cancelled')
                GROUP BY v.id, v.vendor_code, v.vendor_name, v.npwp
                ORDER BY total_bruto DESC
            """),
            {"eid": str(entity_id), "year": year}
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_vendor(self, vendor_id: UUID) -> Optional[dict]:
        row = self.db.execute(
            text("SELECT vendor_name, npwp, vendor_category FROM vendor WHERE id = :id"),
            {"id": str(vendor_id)}
        ).fetchone()
        return dict(row._mapping) if row else None
