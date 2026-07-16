# modules/ap_classifier.py
# Auto-klasifikasi AP invoice dengan integrasi tax profile vendor

from decimal import Decimal
from uuid import UUID
from typing import Optional
from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger
from config.settings import settings
from modules.pph21_engine import calculate_pph21, is_tenaga_ahli as _is_tenaga_ahli


# ============================================================
# KEYWORD RULES
# ============================================================

PROFESSIONAL_FEE_KEYWORDS = [
    "consultancy", "consulting", "konsultansi", "konsultan",
    "professional fee", "professional services", "advisory",
    "legal fee", "audit fee", "accounting fee",
    "notaris", "lawyer", "pengacara", "solicitor",
    "management fee", "jasa manajemen",
    "technical assistance", "jasa teknik",
    "it consulting", "technology consulting",
]

EXPENSE_KEYWORDS = [
    "listrik", "pln", "telepon", "internet", "wifi", "air", "pdam",
    "gaji", "honorarium", "transport", "bensin", "bbm", "parkir",
    "makan", "konsumsi", "alat tulis", "atk", "fotokopi", "cetak",
    "jasa", "service", "maintenance", "perawatan", "perbaikan",
    "cleaning", "kebersihan", "security", "satpam",
    "langganan", "subscription", "software monthly",
    "asuransi bulanan", "premi",
]

PREPAID_KEYWORDS = [
    "sewa", "rental", "lease", "kontrak tahunan", "annual",
    "asuransi", "insurance", "premi tahunan",
    "lisensi tahunan", "license annual",
    "biaya dibayar dimuka", "uang muka",
]

ASSET_KEYWORDS = [
    "laptop", "komputer", "pc", "server", "printer", "scanner",
    "kendaraan", "mobil", "motor", "truk",
    "mesin", "peralatan", "equipment",
    "furnitur", "meja", "kursi", "lemari",
    "AC", "air conditioner", "genset",
    "handphone", "tablet", "kamera",
    "bangunan", "renovasi gedung",
]

ASSET_THRESHOLD_IDR = Decimal("5000000")
PREPAID_MONTHS_MIN  = 3

# Batas omzet UMKM sesuai PP 55/2022
UMKM_OMZET_THRESHOLD = Decimal("4800000000")


class APClassifier:

    def __init__(self, db: Session):
        self.db = db

    def _get_ai(self):
        try:
            provider = getattr(settings, "AI_PROVIDER", "gemini").lower()
            if provider == "gemini" and getattr(settings, "GEMINI_API_KEY", ""):
                import google.generativeai as genai
                genai.configure(api_key=settings.GEMINI_API_KEY)
                return ("gemini", genai.GenerativeModel("gemini-1.5-flash"))
            elif getattr(settings, "ANTHROPIC_API_KEY", ""):
                import anthropic
                return ("anthropic", anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY))
        except Exception as e:
            logger.warning(f"AI init gagal: {e}")
        return None

    # ----------------------------------------------------------
    # PUBLIC
    # ----------------------------------------------------------

    def classify(
        self,
        vendor_id: UUID,
        description: str,
        amount: Decimal,
        invoice_date=None,
        service_period_months: int = 1,
    ) -> dict:
        desc_lower = description.lower() if description else ""

        # 1. Ambil tax profile vendor dari database
        tax_profile = self._get_vendor_tax_profile(vendor_id)

        # 2. Tentukan treatment PPh berdasarkan tax profile
        pph_result = self._determine_pph_treatment(tax_profile, amount, description)

        # 3. Klasifikasi akun (expense/prepaid/asset)
        vendor_services = self._get_vendor_services(vendor_id)
        if vendor_services:
            acct_result = self._classify_from_vendor_services(
                vendor_services, desc_lower, amount, service_period_months
            )
            if acct_result and acct_result["confidence"] >= 0.8:
                logger.info(f"Classified via vendor DB: {acct_result['coa_code']}")
            else:
                acct_result = self._rule_engine(desc_lower, amount, service_period_months)
        else:
            acct_result = self._rule_engine(desc_lower, amount, service_period_months)

        if not acct_result:
            acct_result = self._ai_classify(vendor_id, description, amount, service_period_months)

        # 4. Gabungkan hasil akun + PPh
        final = {**acct_result}
        final["pph_type"]         = pph_result["pph_type"]
        final["pph_rate"]         = pph_result["pph_rate"]
        final["pph_amount"]       = pph_result["pph_amount"]
        final["pph_treatment"]    = pph_result["treatment"]
        final["pph_note"]         = pph_result["note"]
        final["needs_tax_review"] = pph_result.get("needs_review", False)

        logger.info(
            f"Classified: {final['coa_code']} | "
            f"PPh: {pph_result['treatment']} {pph_result['pph_rate']}% "
            f"= Rp {pph_result['pph_amount']:,.0f}"
        )
        return final

    # ----------------------------------------------------------
    # PPH TREATMENT — inti logika perpajakan
    # ----------------------------------------------------------

    def _determine_pph_treatment(
        self, tax_profile: dict, amount: Decimal, description: str = ""
    ) -> dict:
        """
        Tentukan treatment PPh berdasarkan status vendor:
        1. Bebas PPh (SKB masih berlaku)
        2. PPh Final UMKM 0.5%
        3. Override manual (input oleh user)
        4. PPh 21 — vendor Perorangan (individu)
        5. PPh 23 normal 2%
        """
        today = date.today()

        # --- SKENARIO 1: Vendor punya SKB ---
        if tax_profile.get("has_skb"):
            skb_expiry = tax_profile.get("skb_expiry")
            if skb_expiry is None or skb_expiry >= today:
                return {
                    "treatment": "bebas_pph",
                    "pph_type":  None,
                    "pph_rate":  0.0,
                    "pph_amount": Decimal("0"),
                    "note": f"Bebas PPh — SKB No. {tax_profile.get('skb_number', '-')}",
                    "needs_review": False,
                }
            else:
                # SKB sudah expired — tandai perlu review
                logger.warning(f"SKB vendor expired: {skb_expiry}")
                return {
                    "treatment": "skb_expired",
                    "pph_type":  "PPh23",
                    "pph_rate":  2.0,
                    "pph_amount": amount * Decimal("0.02"),
                    "note": f"SKB expired {skb_expiry} — diperlakukan PPh23 normal, perlu review",
                    "needs_review": True,
                }

        # --- SKENARIO 2: Vendor UMKM ---
        if tax_profile.get("vendor_category") == "UMKM":
            umkm_expiry = tax_profile.get("umkm_cert_expiry")
            if umkm_expiry and umkm_expiry < today:
                logger.warning(f"Surat UMKM vendor expired: {umkm_expiry}")
                return {
                    "treatment": "umkm_cert_expired",
                    "pph_type":  "PPh23",
                    "pph_rate":  2.0,
                    "pph_amount": amount * Decimal("0.02"),
                    "note": f"Surat UMKM expired {umkm_expiry} — perlu pembaruan dokumen",
                    "needs_review": True,
                }
            pph_amount = amount * Decimal("0.005")
            return {
                "treatment": "pph_final_umkm",
                "pph_type":  "PPh_Final_UMKM",
                "pph_rate":  0.5,
                "pph_amount": pph_amount,
                "note": f"PPh Final UMKM 0.5% (PP 55/2022) — Surat UMKM No. {tax_profile.get('umkm_cert_number', '-')}",
                "needs_review": False,
            }

        # --- SKENARIO 3: Override manual ---
        if tax_profile.get("pph_override_type"):
            raw_rate      = tax_profile.get("pph_override_rate")
            override_rate = Decimal(str(raw_rate if raw_rate is not None else 2.0))
            override_amount = amount * (override_rate / 100)
            return {
                "treatment": "override_manual",
                "pph_type":  tax_profile["pph_override_type"],
                "pph_rate":  float(override_rate),
                "pph_amount": override_amount,
                "note": f"Override manual: {tax_profile.get('pph_override_reason', '-')}",
                "needs_review": False,
            }

        # --- SKENARIO 4: PPh 21 — Vendor Perorangan (individu) ---
        if tax_profile.get("vendor_category") == "Perorangan":
            has_npwp   = bool(tax_profile.get("npwp"))
            is_ahli    = _is_tenaga_ahli(description)
            pph21_data = calculate_pph21(
                gross_amount     = amount,
                has_npwp         = has_npwp,
                is_tenaga_ahli   = is_ahli,
                ytd_gross_before = Decimal("0"),  # tanpa YTD — gunakan /pph21/calculate-for-vendor untuk akurasi
            )
            pph_type = "PPh21_TenagaAhli" if is_ahli else "PPh21_NonPegawai"
            return {
                "treatment":    "pph21_perorangan",
                "pph_type":     pph_type,
                "pph_rate":     pph21_data["effective_rate"],
                "pph_amount":   Decimal(str(pph21_data["pph21_amount"])),
                "note": (
                    f"PPh 21 {'Tenaga Ahli (PKP=50%×bruto)' if is_ahli else 'Non-Pegawai'} | "
                    f"{'Tanpa NPWP ×1.20' if not has_npwp else 'Dengan NPWP'} | "
                    "YTD belum diperhitungkan — gunakan endpoint /pph21/calculate-for-vendor"
                ),
                "needs_review": not has_npwp,
            }

        # --- SKENARIO 5: PPh 23 Normal ---
        default_rate   = Decimal(str(tax_profile.get("default_pph_rate") or 2.0))
        default_type   = tax_profile.get("default_pph_type") or "PPh23"
        pph_amount     = amount * (default_rate / 100)

        # Jika vendor baru dan belum ada data — tandai perlu review
        needs_review = not tax_profile.get("tax_reviewed_at")

        return {
            "treatment": "pph23_normal",
            "pph_type":  default_type,
            "pph_rate":  float(default_rate),
            "pph_amount": pph_amount,
            "note": f"PPh 23 normal {default_rate}%"
                    + (" — perlu konfirmasi status vendor" if needs_review else ""),
            "needs_review": needs_review,
        }

    # ----------------------------------------------------------
    # RULE ENGINE — klasifikasi akun
    # ----------------------------------------------------------

    def _rule_engine(self, desc_lower: str, amount: Decimal, months: int) -> Optional[dict]:
        for kw in PROFESSIONAL_FEE_KEYWORDS:
            if kw in desc_lower:
                return self._build_result(
                    classification="expense",
                    coa_code="6-1-008",
                    confidence=0.90,
                    note=f"Professional fee: '{kw}'",
                )
        for kw in ASSET_KEYWORDS:
            if kw in desc_lower and amount >= ASSET_THRESHOLD_IDR:
                return self._build_result(
                    classification="fixed_asset",
                    coa_code="1-6-001",
                    confidence=0.85,
                    note=f"Keyword aset: '{kw}'",
                )
        for kw in PREPAID_KEYWORDS:
            if kw in desc_lower and (months >= PREPAID_MONTHS_MIN or amount >= ASSET_THRESHOLD_IDR):
                return self._build_result(
                    classification="prepaid",
                    coa_code="1-5-001",
                    confidence=0.80,
                    note=f"Keyword prepaid: '{kw}'",
                )
        for kw in EXPENSE_KEYWORDS:
            if kw in desc_lower:
                return self._build_result(
                    classification="expense",
                    coa_code="6-1-001",
                    confidence=0.80,
                    note=f"Keyword beban: '{kw}'",
                )
        return None

    def _classify_from_vendor_services(
        self, services: list, desc_lower: str, amount: Decimal, months: int
    ) -> Optional[dict]:
        best = None
        for svc in services:
            svc_name = (svc["service_name"] or "").lower()
            svc_cat  = (svc["service_category"] or "").lower()
            if any(w in desc_lower for w in svc_name.split() if len(w) > 3) or \
               any(w in desc_lower for w in svc_cat.split() if len(w) > 3):
                coa = svc.get("coa_suggestion") or "6-1-001"
                if any(k in svc_cat for k in ["consult", "professional", "advisory", "teknik"]):
                    coa = "6-1-008"
                elif any(k in svc_cat for k in ["sewa", "rental"]):
                    coa = "1-5-001" if months >= PREPAID_MONTHS_MIN else "6-1-003"
                result = self._build_result(
                    classification="expense",
                    coa_code=coa,
                    confidence=float(svc.get("confidence", 0.85)),
                    note=f"Match vendor service: {svc['service_name']}",
                )
                if best is None or result["confidence"] > best["confidence"]:
                    best = result
        return best

    def _ai_classify(self, vendor_id: UUID, description: str, amount: Decimal, months: int) -> dict:
        vendor = self._get_vendor_info(vendor_id)
        vendor_name = vendor.get("vendor_name", "Unknown") if vendor else "Unknown"
        prompt = f"""Klasifikasikan transaksi AP ini ke akun COA yang tepat:
Vendor: {vendor_name} | Deskripsi: {description} | Nominal: Rp {amount:,.2f}

COA tersedia:
6-1-008: Professional Fees | 6-1-001: Beban Umum
6-1-002: Beban Listrik | 6-1-003: Beban Sewa
6-1-004: Beban IT | 6-1-005: Beban Gaji
1-5-001: Prepaid | 1-6-001: Aset Tetap

Kembalikan JSON: {{"classification":"expense","coa_code":"6-1-008","confidence":0.9,"reasoning":"..."}}"""
        try:
            import json
            ai = self._get_ai()
            if not ai:
                raise Exception("AI tidak tersedia")
            provider, client = ai
            raw = client.generate_content(prompt).text if provider == "gemini" else \
                  client.messages.create(model="claude-sonnet-4-20250514", max_tokens=300,
                      messages=[{"role":"user","content":prompt}]).content[0].text
            raw = raw.replace("```json","").replace("```","").strip()
            data = json.loads(raw)
            return self._build_result(
                classification=data.get("classification","expense"),
                coa_code=data.get("coa_code","6-1-001"),
                confidence=data.get("confidence",0.75),
                note=data.get("reasoning","AI classification"),
                source="AI",
            )
        except Exception as e:
            logger.error(f"AI classify error: {e}")
            return self._build_result("expense","6-1-001",0.5,"AI gagal — review manual","fallback")

    # ----------------------------------------------------------
    # HELPERS
    # ----------------------------------------------------------

    def _build_result(self, classification, coa_code, confidence=0.80,
                      note="", source="rule", **kwargs) -> dict:
        return {
            "classification": classification,
            "coa_code":       coa_code,
            "confidence":     confidence,
            "note":           note,
            "source":         source,
        }

    def _get_vendor_tax_profile(self, vendor_id: UUID) -> dict:
        """Ambil tax profile lengkap dari view vw_vendor_tax_status."""
        try:
            row = self.db.execute(
                text("SELECT * FROM vw_vendor_tax_status WHERE id = :id"),
                {"id": str(vendor_id)}
            ).fetchone()
            if row:
                return dict(row._mapping)
        except Exception as e:
            logger.warning(f"Tax profile not found (migration belum dijalankan?): {e}")
        # Fallback default jika view belum ada
        row = self.db.execute(
            text("""SELECT vendor_name, default_pph_type, default_pph_rate,
                           tax_status, vendor_code
                    FROM vendor WHERE id = :id"""),
            {"id": str(vendor_id)}
        ).fetchone()
        return dict(row._mapping) if row else {}

    def _get_vendor_services(self, vendor_id: UUID) -> list:
        rows = self.db.execute(
            text("""SELECT service_name, service_category, pph_object,
                           pph_rate, coa_suggestion, confidence
                    FROM vendor_services WHERE vendor_id = :vid
                    ORDER BY confidence DESC"""),
            {"vid": str(vendor_id)}
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    def _get_vendor_info(self, vendor_id: UUID) -> Optional[dict]:
        row = self.db.execute(
            text("SELECT vendor_name, default_pph_type, default_pph_rate FROM vendor WHERE id = :id"),
            {"id": str(vendor_id)}
        ).fetchone()
        return dict(row._mapping) if row else None