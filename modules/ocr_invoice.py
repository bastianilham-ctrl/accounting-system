# modules/ocr_invoice.py
# OCR Invoice — pdfplumber + regex utama, AI hanya untuk field yang masih null

import re
import json
import base64
import io
from pathlib import Path
from typing import Optional
from decimal import Decimal
from datetime import datetime
import pdfplumber
from loguru import logger
from config.settings import settings

MONTH_MAP = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
    "januari": "01", "februari": "02", "maret": "03", "mei": "05",
    "juni": "06", "juli": "07", "agustus": "08", "oktober": "10",
    "desember": "12",
}

CONFIDENCE_THRESHOLD = 0.75
POPPLER_PATH = r"C:\poppler-26.02.0\Library\bin"

# Field yang wajib ada — kalau masih null, panggil AI
REQUIRED_FIELDS = ["vendor_name", "invoice_no", "invoice_date", "total_amount"]


class OCRInvoice:

    def __init__(self):
        self.upload_dir = Path(settings.UPLOAD_DIR)
        self.upload_dir.mkdir(exist_ok=True)
        self._ai = None

    def _get_ai(self):
        """Lazy init AI — hanya dibuat kalau dibutuhkan."""
        if self._ai is not None:
            return self._ai
        try:
            provider = getattr(settings, "AI_PROVIDER", "gemini").lower()
            if provider == "gemini" and getattr(settings, "GEMINI_API_KEY", ""):
                import google.generativeai as genai
                genai.configure(api_key=settings.GEMINI_API_KEY)
                self._ai = ("gemini", genai.GenerativeModel("gemini-1.5-flash"))
            elif getattr(settings, "ANTHROPIC_API_KEY", ""):
                import anthropic
                self._ai = ("anthropic", anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY))
        except Exception as e:
            logger.warning(f"AI init gagal: {e}")
            self._ai = None
        return self._ai

    # ----------------------------------------------------------
    # PUBLIC
    # ----------------------------------------------------------

    def process(self, file_path: str) -> dict:
        path = Path(file_path)
        if not path.exists():
            return {"success": False, "error": f"File tidak ditemukan: {file_path}"}

        logger.info(f"Memproses invoice: {path.name}")

        # STEP 1: Extract teks
        raw_text = self._extract_text(path)
        if not raw_text:
            return {"success": False, "error": "Tidak bisa extract teks dari PDF"}

        logger.debug(f"Raw text ({len(raw_text)} chars):\n{raw_text[:400]}")

        # STEP 2: Regex parse — utama
        result = self._regex_parse(raw_text)

        # STEP 3: Cek field yang masih null, panggil AI jika perlu
        missing = [f for f in REQUIRED_FIELDS if not result.get(f)]
        if missing:
            logger.info(f"Field masih null: {missing} — memanggil AI")
            ai_result = self._ai_parse(raw_text, missing)
            # Isi field yang masih kosong dengan hasil AI
            for field in missing:
                if ai_result.get(field):
                    result[field] = ai_result[field]
                    logger.info(f"AI mengisi {field}: {ai_result[field]}")

        # STEP 4: Hitung confidence
        filled = sum(1 for f in REQUIRED_FIELDS if result.get(f))
        confidence = round(filled / len(REQUIRED_FIELDS), 2)
        result["confidence"] = confidence

        result["success"] = True
        result["file_name"] = path.name
        result["raw_text_length"] = len(raw_text)
        result["needs_review"] = confidence < CONFIDENCE_THRESHOLD

        logger.info(
            f"Parsed: {result.get('invoice_no')} | "
            f"{result.get('vendor_name')} | "
            f"IDR {result.get('total_amount')} | "
            f"conf: {confidence}"
        )
        return result

    # ----------------------------------------------------------
    # EXTRACT TEXT
    # ----------------------------------------------------------

    def _extract_text(self, path: Path) -> str:
        text = ""
        try:
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text(x_tolerance=3, y_tolerance=3)
                    if page_text:
                        text += page_text + "\n"
                    tables = page.extract_tables()
                    for table in tables:
                        for row in table:
                            if row:
                                text += " | ".join([str(c or "") for c in row]) + "\n"
            if text.strip():
                return text.strip()
        except Exception as e:
            logger.warning(f"pdfplumber error: {e}")
        return self._ocr_fallback(path)

    def _ocr_fallback(self, path: Path) -> str:
        try:
            import pytesseract
            from pdf2image import convert_from_path
            logger.info(f"Fallback pytesseract: {path.name}")
            images = convert_from_path(str(path), dpi=300, poppler_path=POPPLER_PATH)
            return "\n".join(
                pytesseract.image_to_string(img, lang="ind+eng")
                for img in images
            ).strip()
        except Exception as e:
            logger.error(f"pytesseract error: {e}")
            return ""

    # ----------------------------------------------------------
    # REGEX PARSE — ekstrak semua field dari teks
    # ----------------------------------------------------------

    def _regex_parse(self, text: str) -> dict:
        result = {}

        # --- vendor_name ---
        # Cari "Company name: XXX" atau nama di header sebelum CLIENT INFORMATION
        company_m = re.search(r"Company\s+name\s*[:\-]\s*(.+)", text, re.IGNORECASE)
        if company_m:
            result["vendor_name"] = company_m.group(1).strip()
        else:
            # Ambil baris pertama sebelum "CLIENT INFORMATION"
            lines = text.split("\n")
            for i, line in enumerate(lines):
                line = line.strip()
                if line and line not in ["INVOICE", ""] and "CLIENT" not in line.upper():
                    # Skip baris yang mengandung nama client/penerima
                    if not any(kw in line.upper() for kw in ["PT BIBIT", "RDTX", "JL.", "JAKARTA", "DKI"]):
                        if len(line) > 3 and not re.match(r"^\d", line):
                            result["vendor_name"] = line
                            break

        # --- invoice_no ---
        inv_m = re.search(
            r"INVOICE\s+NO\.?\s*[:\-]?\s*([A-Z0-9\-\/]+)",
            text, re.IGNORECASE
        )
        if inv_m:
            result["invoice_no"] = inv_m.group(1).strip()

        # --- invoice_date ---
        date_m = re.search(
            r"DATE\s+(\w+\s+\d{1,2},?\s+\d{4})",
            text, re.IGNORECASE
        )
        if date_m:
            result["invoice_date"] = self._parse_date(date_m.group(1).strip())

        # --- due_date ---
        due_m = re.search(
            r"DUE\s+DATE\s+(\w+\s+\d{1,2},?\s+\d{4})",
            text, re.IGNORECASE
        )
        if due_m:
            result["due_date"] = self._parse_date(due_m.group(1).strip())

        # --- description ---
        desc_m = re.search(r"Re:\s*(.+)", text, re.IGNORECASE)
        if desc_m:
            result["description"] = desc_m.group(1).strip()
        else:
            # Ambil dari baris line item
            item_m = re.search(
                r"(Consultancy|Jasa|Service|Sewa|Maintenance|IT|Software)\s+.+\d{4}",
                text, re.IGNORECASE
            )
            if item_m:
                result["description"] = item_m.group(0).strip()[:100]

        # --- total_amount ---
        for pat in [
            r"AMOUNT\s+PAYABLE\s+IDR\s+([\d,\.]+)",
            r"Grand\s+Total\s+(?:IDR|Rp\.?)?\s*([\d,\.]+)",
            r"Total\s+(?:IDR|Rp\.?)?\s*([\d,\.]+)",
            r"IDR\s+([\d,\.]+)",
        ]:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                val = self._clean_amount(m.group(1))
                if val > 0:
                    result["total_amount"] = Decimal(str(val))
                    break

        # --- subtotal ---
        # Cari nominal di baris line item (sebelum AMOUNT PAYABLE)
        sub_m = re.search(
            r"(?:Consultancy|Jasa|Service|Sewa).+IDR\s+([\d,\.]+)",
            text, re.IGNORECASE
        )
        if sub_m:
            val = self._clean_amount(sub_m.group(1))
            if val > 0:
                result["subtotal"] = Decimal(str(val))
        elif result.get("total_amount"):
            result["subtotal"] = result["total_amount"]

        # --- PPN ---
        ppn_m = re.search(
            r"(?:PPN|VAT|Pajak)\s*(?:11%|10%)?\s*[:\-]?\s*(?:IDR|Rp\.?)?\s*([\d,\.]+)",
            text, re.IGNORECASE
        )
        if ppn_m:
            result["ppn_amount"] = Decimal(str(self._clean_amount(ppn_m.group(1))))
            result["ppn_rate"] = 11
        else:
            result["ppn_amount"] = Decimal("0")
            result["ppn_rate"] = 0

        # --- PPh ---
        pph_m = re.search(
            r"(?:PPh|Withholding)\s*(?:Pasal)?\s*(\d+)\s*[:\-]?\s*(?:IDR|Rp\.?)?\s*([\d,\.]+)",
            text, re.IGNORECASE
        )
        if pph_m:
            pasal = pph_m.group(1)
            result["pph_type"] = f"PPh{pasal}"
            result["pph_amount"] = Decimal(str(self._clean_amount(pph_m.group(2))))
        else:
            result["pph_type"] = None
            result["pph_amount"] = Decimal("0")
            result["pph_rate"] = 0

        # --- bank info ---
        bank_m = re.search(r"Bank\s*[:\-]\s*(.+)", text, re.IGNORECASE)
        if bank_m:
            result["bank_name"] = bank_m.group(1).strip()

        acc_m = re.search(
            r"(?:IDR\s+Account\s+No|Account\s+No|No\.?\s+Rek)\s*[:\-]?\s*([\d]+)",
            text, re.IGNORECASE
        )
        if acc_m:
            result["bank_account"] = acc_m.group(1).strip()

        # --- faktur pajak ---
        faktur_m = re.search(r"(\d{3}\.\d{3}-\d{2}\.\d{8})", text)
        if faktur_m:
            result["faktur_pajak_no"] = faktur_m.group(1)
        else:
            result["faktur_pajak_no"] = None

        # --- NPWP ---
        npwp_m = re.search(
            r"NPWP\s*[:\-]?\s*(\d{2}[\.\-]\d{3}[\.\-]\d{3}[\.\-]\d[\.\-]\d{3}[\.\-]\d{3})",
            text
        )
        if npwp_m:
            result["vendor_npwp"] = npwp_m.group(1)
        else:
            result["vendor_npwp"] = None

        result["currency"] = "IDR"
        result["line_items"] = []

        return result

    # ----------------------------------------------------------
    # AI PARSE — hanya untuk field yang masih null
    # ----------------------------------------------------------

    def _ai_parse(self, raw_text: str, missing_fields: list) -> dict:
        ai = self._get_ai()
        if not ai:
            logger.warning("AI tidak tersedia, skip AI parse")
            return {}

        provider, client = ai
        fields_str = ", ".join(missing_fields)
        prompt = f"""Dari teks invoice berikut, ekstrak HANYA field yang diminta: {fields_str}

Kembalikan HANYA JSON dengan field yang diminta saja.
Contoh jika diminta vendor_name dan due_date:
{{"vendor_name": "PT. Nir Vana Net", "due_date": "2026-03-10"}}

Format tanggal: YYYY-MM-DD
vendor_name = perusahaan PENGIRIM invoice (header atas, bukan CLIENT INFORMATION)

Teks invoice:
---
{raw_text[:3000]}
---"""

        try:
            if provider == "gemini":
                response = client.generate_content(prompt)
                text = response.text.strip()
            else:
                response = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=500,
                    messages=[{"role": "user", "content": prompt}]
                )
                text = response.content[0].text.strip()

            text = text.replace("```json", "").replace("```", "").strip()
            return json.loads(text)

        except Exception as e:
            logger.error(f"AI parse error: {e}")
            return {}

    # ----------------------------------------------------------
    # UTILITIES
    # ----------------------------------------------------------

    def _parse_date(self, date_str: str) -> Optional[str]:
        date_str = date_str.strip().rstrip(",")
        for pat in ["%B %d %Y", "%B %d, %Y", "%d %B %Y",
                    "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"]:
            try:
                return datetime.strptime(date_str, pat).strftime("%Y-%m-%d")
            except ValueError:
                continue
        m = re.match(r"(\w+)\s+(\d{1,2}),?\s+(\d{4})", date_str, re.IGNORECASE)
        if m:
            month = MONTH_MAP.get(m.group(1).lower())
            if month:
                return f"{m.group(3)}-{month}-{int(m.group(2)):02d}"
        return None

    def _clean_amount(self, val: str) -> float:
        if not val:
            return 0.0
        val = val.replace(" ", "")
        if "." in val and "," in val:
            if val.index(".") < val.index(","):
                val = val.replace(".", "").replace(",", ".")
            else:
                val = val.replace(",", "")
        elif "," in val:
            parts = val.split(",")
            val = val.replace(",", ".") if len(parts[-1]) == 2 else val.replace(",", "")
        try:
            return float(val)
        except Exception:
            return 0.0

    def _to_decimal(self, value) -> Decimal:
        if value is None:
            return Decimal("0")
        try:
            return Decimal(str(value))
        except Exception:
            return Decimal("0")