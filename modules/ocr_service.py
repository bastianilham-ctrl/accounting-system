# modules/ocr_service.py
# OCR Invoice: EasyOCR (ekstraksi teks) → Ollama llama3.2 (parsing JSON akuntansi)
# Fallback: pdfplumber + AI Anthropic/Gemini jika Ollama tidak tersedia.
#
# Arsitektur:
#   PDF → pdf2image → EasyOCR → teks mentah → Ollama → JSON akuntansi
#                                            ↓ (fallback jika Ollama off)
#                                      pdfplumber-based OCRInvoice

import asyncio
import json
import os
from decimal import Decimal
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

POPPLER_PATH = r"C:\poppler-26.02.0\Library\bin"
OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2"

# EasyOCR reader: lazy-init agar tidak memperlambat startup server.
# Model didownload sekali ~100MB ke %USERPROFILE%\.EasyOCR/model/
_reader = None


def _get_easyocr_reader():
    """Lazy-init EasyOCR reader — dibuat pertama kali saat dibutuhkan."""
    global _reader
    if _reader is None:
        try:
            import easyocr
            logger.info("Inisialisasi EasyOCR reader (pertama kali, mungkin butuh 10-30 detik)...")
            _reader = easyocr.Reader(["id", "en"], gpu=False)
            logger.info("EasyOCR reader siap.")
        except ImportError:
            logger.warning("easyocr tidak terinstall. Jalankan: pip install easyocr")
            _reader = None
    return _reader


async def _pdf_to_images(file_path: str) -> list:
    """
    Konversi PDF ke list PIL Image menggunakan pdf2image (sudah ada di requirements).
    Dijalankan di thread executor agar tidak blokir event loop.
    """
    def _convert():
        from pdf2image import convert_from_path
        return convert_from_path(
            file_path,
            dpi=200,
            poppler_path=POPPLER_PATH if os.path.exists(POPPLER_PATH) else None,
        )

    loop   = asyncio.get_running_loop()
    images = await loop.run_in_executor(None, _convert)
    return images


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


async def _load_images(file_path: str) -> list:
    """
    Muat halaman sebagai list PIL Image.
    PDF → pdf2image (multi-halaman). JPG/PNG → langsung dibuka, jadi 1 halaman.
    """
    ext = Path(file_path).suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        from PIL import Image
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: [Image.open(file_path).convert("RGB")])
    return await _pdf_to_images(file_path)


async def _extract_text_easyocr(file_path: str) -> Optional[str]:
    """
    Ekstrak teks dari PDF/gambar via EasyOCR.
    Return gabungan semua teks, atau None jika gagal.
    """
    reader = _get_easyocr_reader()
    if reader is None:
        return None

    try:
        import numpy as np

        images = await _load_images(file_path)
        if not images:
            logger.warning(f"Tidak ada halaman/gambar yang bisa dibaca dari {file_path}")
            return None

        loop      = asyncio.get_running_loop()
        all_texts = []

        for i, img in enumerate(images[:3]):   # Batasi 3 halaman
            img_array = np.array(img)
            hasil     = await loop.run_in_executor(None, reader.readtext, img_array)
            page_text = " ".join([item[1] for item in hasil if item[1].strip()])
            all_texts.append(page_text)
            logger.debug(f"EasyOCR halaman {i+1}: {len(page_text)} karakter")

        return " ".join(all_texts) if all_texts else None

    except Exception as e:
        logger.warning(f"EasyOCR error: {e}")
        return None


async def _parse_with_ollama(teks_mentah: str) -> Optional[dict]:
    """
    Kirim teks OCR ke Ollama (lokal) untuk parsing JSON akuntansi.
    Return dict akuntansi atau None jika Ollama tidak tersedia / gagal.
    """
    prompt = f"""Kamu adalah sistem AI akuntansi Indonesia. Ekstrak data invoice dari teks berikut menjadi JSON.

Teks invoice:
\"\"\"{teks_mentah[:3000]}\"\"\"

Format JSON yang wajib dikembalikan:
{{
    "vendor_name": "nama perusahaan penerbit invoice",
    "invoice_date": "YYYY-MM-DD",
    "invoice_no": "nomor invoice",
    "total_amount": 0.0,
    "subtotal": 0.0,
    "ppn_amount": 0.0,
    "pph_amount": 0.0,
    "vendor_npwp": "nomor NPWP jika ada, null jika tidak"
}}

Aturan:
- invoice_date HARUS format YYYY-MM-DD
- Semua angka tanpa titik ribuan, gunakan titik untuk desimal
- Jika data tidak ada, isi null
- Keluarkan HANYA JSON, tanpa penjelasan!"""

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                OLLAMA_URL,
                json={
                    "model":  OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                },
            )
            if response.status_code != 200:
                logger.warning(f"Ollama HTTP {response.status_code}")
                return None

            raw = response.json().get("response", "{}")

            # Bersihkan jika ada markdown wrapper
            raw = raw.replace("```json", "").replace("```", "").strip()
            data = json.loads(raw)

            # Konversi dan validasi tipe data
            for field in ("total_amount", "subtotal", "ppn_amount", "pph_amount"):
                if field in data and data[field] is not None:
                    try:
                        data[field] = Decimal(str(data[field]))
                    except Exception:
                        data[field] = Decimal("0")

            if "invoice_date" in data and data["invoice_date"]:
                try:
                    datetime.strptime(data["invoice_date"], "%Y-%m-%d")
                except ValueError:
                    logger.warning(f"Format tanggal Ollama tidak valid: {data['invoice_date']}")
                    data["invoice_date"] = datetime.now().strftime("%Y-%m-%d")

            logger.info(f"Ollama berhasil parse: vendor={data.get('vendor_name')}, "
                        f"invoice={data.get('invoice_no')}, total={data.get('total_amount')}")
            return data

    except httpx.ConnectError:
        logger.warning("Ollama tidak tersedia di localhost:11434 — menggunakan fallback pdfplumber")
        return None
    except json.JSONDecodeError as e:
        logger.warning(f"Ollama JSON parse error: {e}")
        return None
    except Exception as e:
        logger.warning(f"Ollama error: {e}")
        return None


async def _fallback_pdfplumber(file_path: str) -> Optional[dict]:
    """
    Fallback ke OCRInvoice berbasis pdfplumber + AI (Anthropic/Gemini).
    Digunakan jika Ollama tidak tersedia.
    """
    try:
        from modules.ocr_invoice import OCRInvoice
        loop   = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, OCRInvoice().process, file_path)
        if result.get("success"):
            logger.info("Fallback pdfplumber OCR berhasil")
        return result
    except Exception as e:
        logger.error(f"Fallback pdfplumber juga gagal: {e}")
        return None


# ----------------------------------------------------------
# PUBLIC — entry point yang dipanggil ocr_router.py
# ----------------------------------------------------------

async def proses_ai_ocr(file_path: str) -> dict:
    """
    Pipeline OCR lengkap:
    1. EasyOCR  → teks mentah dari PDF (via pdf2image)
    2. Ollama   → parse JSON akuntansi dari teks
    3. Fallback → pdfplumber + Claude/Gemini jika Ollama off
    """
    logger.info(f"Memulai AI-OCR: {Path(file_path).name}")

    # Step 1: Ekstrak teks via EasyOCR
    teks_mentah = await _extract_text_easyocr(file_path)

    if teks_mentah:
        logger.debug(f"EasyOCR ekstrak {len(teks_mentah)} karakter")

        # Step 2: Parse via Ollama
        data = await _parse_with_ollama(teks_mentah)
        if data:
            return {"success": True, **data}

        logger.info("Ollama tidak tersedia atau gagal, fallback ke pdfplumber...")
    else:
        logger.info("EasyOCR gagal ekstrak teks, fallback ke pdfplumber...")

    # Step 3: Fallback ke pdfplumber + Claude/Gemini
    result = await _fallback_pdfplumber(file_path)
    if result:
        return result

    return {"success": False, "error": "Semua metode OCR gagal"}
