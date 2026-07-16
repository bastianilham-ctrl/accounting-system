# modules/vendor_scraper.py
# Scraping data vendor dari DJP, OSS, dan website perusahaan.
#
# CATATAN DJP:
#   DJP tidak menyediakan API publik tanpa autentikasi untuk lookup NPWP.
#   Endpoint ereg/cetak-NPWP memerlukan session login.
#   Strategi ini: coba httpx dulu → fallback Playwright (headless) → mark manual.
#
# CATATAN OSS:
#   oss.go.id/oss/api/perizinan/search adalah internal API — bisa berubah.
#   Jika gagal, vendor di-flag scrape_status='needs_manual_djp'.

import httpx
import asyncio
import json
from uuid import UUID, uuid4
from typing import Optional
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger
import anthropic
from config.settings import settings


class VendorScraper:

    def __init__(self, db: Session):
        self.db      = db
        self.ai      = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.timeout = settings.SCRAPE_TIMEOUT

    # ----------------------------------------------------------
    # PUBLIC: enrichment lengkap satu vendor
    # ----------------------------------------------------------

    async def enrich_vendor(self, vendor_id: UUID) -> dict:
        """
        Jalankan semua sumber scraping untuk satu vendor.
        Urutan: DJP NPWP → OSS NIB/nama → website → AI klasifikasi.
        Setiap langkah independent — gagal satu tidak stop proses.
        """
        vendor = self._get_vendor(vendor_id)
        if not vendor:
            return {"success": False, "error": "Vendor tidak ditemukan"}

        results = {}
        failed  = []
        logger.info(f"Enrichment mulai: {vendor['vendor_name']}")

        # 1. DJP — lookup NPWP
        if vendor.get("npwp"):
            djp_data = await self._scrape_djp(vendor["npwp"])
            if djp_data:
                results["djp"] = djp_data
                self._update_vendor_from_djp(vendor_id, djp_data)
            else:
                failed.append("DJP")
                logger.warning(f"DJP lookup gagal untuk NPWP {vendor['npwp']} — perlu verifikasi manual")

        # 2. OSS — lookup NIB atau nama
        oss_data = await self._scrape_oss(
            nib=vendor.get("nib"),
            vendor_name=vendor["vendor_name"]
        )
        if oss_data:
            results["oss"] = oss_data
            self._update_vendor_from_oss(vendor_id, oss_data)
        else:
            failed.append("OSS")

        # 3. Website vendor
        if vendor.get("website"):
            web_data = await self._scrape_website(vendor["website"])
            if web_data:
                results["website"] = web_data

        # 4. AI klasifikasi gabungan
        if results:
            ai_result = self._ai_classify_vendor(vendor["vendor_name"], results)
            if ai_result:
                results["ai"] = ai_result
                self._save_vendor_services(vendor_id, ai_result)
                self._update_vendor_tax_profile(vendor_id, ai_result)

        # Update status scraping
        if failed and not results:
            status = "failed"
        elif failed:
            status = "partial"   # sebagian berhasil
        else:
            status = "success"

        self._update_scrape_status(vendor_id, status, needs_manual_djp="DJP" in failed)

        logger.info(f"Enrichment selesai: {vendor['vendor_name']} — {status}")
        return {
            "success":           True,
            "vendor_name":       vendor["vendor_name"],
            "scrape_status":     status,
            "sources_succeeded": list(results.keys()),
            "sources_failed":    failed,
            "needs_manual_djp":  "DJP" in failed,
            "data":              results,
        }

    # ----------------------------------------------------------
    # DJP — lookup NPWP
    # ----------------------------------------------------------

    async def _scrape_djp(self, npwp: str) -> Optional[dict]:
        """
        Coba dua strategi DJP:
        1. httpx ke endpoint publik ereg DJP
        2. Playwright headless jika httpx gagal (butuh JS rendering)
        Jika keduanya gagal, return None → vendor di-flag perlu verifikasi manual.
        """
        npwp_clean = "".join(c for c in npwp if c.isdigit())
        error_msg  = "unknown"

        # Strategi 1: httpx (cepat, mungkin tidak selalu berhasil)
        try:
            result = await self._djp_httpx(npwp_clean)
            if result:
                self._log_scrape(None, "DJP", f"NPWP:{npwp_clean}", "success", raw_data=result)
                return result
        except Exception as e:
            error_msg = str(e)
            logger.debug(f"DJP httpx fallthrough: {e}")

        # Strategi 2: Playwright (lebih robust, handles JS + redirects)
        try:
            result = await self._djp_playwright(npwp_clean)
            if result:
                self._log_scrape(None, "DJP", f"NPWP:{npwp_clean}", "success", raw_data=result)
                return result
        except Exception as e:
            error_msg = str(e)
            logger.debug(f"DJP playwright fallthrough: {e}")

        self._log_scrape(None, "DJP", f"NPWP:{npwp_clean}", "failed", error=error_msg)
        return None

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=6),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=False,
    )
    async def _djp_httpx(self, npwp_clean: str) -> Optional[dict]:
        """Coba endpoint publik DJP via httpx."""
        urls = [
            f"https://ereg.pajak.go.id/cetak/npwp/{npwp_clean}",
            f"https://djponline.pajak.go.id/account/login?npwp={npwp_clean}",
        ]
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        ) as client:
            for url in urls:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200 and len(resp.text) > 200:
                        soup = BeautifulSoup(resp.text, "lxml")
                        data = self._parse_djp_html(soup, npwp_clean)
                        if data.get("registered_name") or data.get("tax_status"):
                            return data
                except Exception:
                    continue
        return None

    async def _djp_playwright(self, npwp_clean: str) -> Optional[dict]:
        """
        Playwright headless browser untuk DJP yang butuh JS rendering.
        Playwright sudah ada di requirements.txt.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.debug("Playwright tidak terinstall, skip")
            return None

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                page    = await browser.new_page()
                await page.set_extra_http_headers({
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                })
                await page.goto(
                    f"https://ereg.pajak.go.id/cetak/npwp/{npwp_clean}",
                    timeout=self.timeout * 1000,
                    wait_until="domcontentloaded",
                )
                content = await page.content()
                await browser.close()

                soup = BeautifulSoup(content, "lxml")
                data = self._parse_djp_html(soup, npwp_clean)
                if data.get("registered_name") or data.get("tax_status"):
                    return data
        except Exception as e:
            logger.debug(f"Playwright DJP error: {e}")
        return None

    def _parse_djp_html(self, soup: BeautifulSoup, npwp: str) -> dict:
        """
        Parse HTML DJP menjadi dict terstruktur.
        DJP HTML layout bisa berubah — defensive parsing dengan multiple selector attempts.
        """
        data = {"npwp": npwp, "source": "DJP"}
        try:
            # Cari nama: bisa di <td> setelah label "Nama" atau di elemen dengan class tertentu
            for label in ["Nama", "Name", "NAMA"]:
                el = soup.find(lambda t: t.name in ("td", "th", "label", "span")
                               and t.string and label in t.string)
                if el:
                    sibling = el.find_next_sibling("td") or el.find_next("td")
                    if sibling:
                        data["registered_name"] = sibling.get_text(strip=True)
                        break

            # Status PKP
            for label in ["PKP", "Pengusaha Kena Pajak"]:
                el = soup.find(lambda t: t.name in ("td", "th", "span")
                               and t.string and label in t.string)
                if el:
                    sibling = el.find_next_sibling("td") or el.find_next("td")
                    if sibling:
                        status_text = sibling.get_text(strip=True).upper()
                        data["tax_status"] = "PKP" if "PKP" in status_text else "non_PKP"
                        break

            # KLU
            el = soup.find(lambda t: t.name in ("td", "th")
                           and t.string and "KLU" in t.string)
            if el:
                sibling = el.find_next_sibling("td") or el.find_next("td")
                if sibling:
                    data["klu"] = sibling.get_text(strip=True)

        except Exception as e:
            logger.debug(f"DJP HTML parse error: {e}")

        return data

    # ----------------------------------------------------------
    # OSS — lookup NIB / nama perusahaan
    # ----------------------------------------------------------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _scrape_oss(self, nib: Optional[str], vendor_name: str) -> Optional[dict]:
        """Lookup KBLI dan status usaha dari OSS."""
        search_term = nib or vendor_name
        error_msg   = "unknown"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept":     "application/json",
                    "Referer":    "https://oss.go.id/",
                },
            ) as client:
                # Coba endpoint v1 dan v2 OSS
                for url, params in [
                    ("https://oss.go.id/oss/api/perizinan/search",      {"keyword": search_term, "size": 1}),
                    ("https://oss.go.id/api/v1/perizinan/search",       {"q": search_term, "limit": 1}),
                ]:
                    try:
                        resp = await client.get(url, params=params)
                        if resp.status_code == 200:
                            data = self._parse_oss_response(resp.json())
                            if data:
                                self._log_scrape(None, "OSS", url, "success", raw_data=data)
                                return data
                    except Exception as e:
                        error_msg = str(e)
                        continue

        except Exception as e:
            error_msg = str(e)
            logger.warning(f"OSS scraping error untuk '{vendor_name}': {e}")

        self._log_scrape(None, "OSS", f"search:{search_term}", "failed", error=error_msg)
        return None

    def _parse_oss_response(self, data: dict) -> Optional[dict]:
        """Parse response OSS API — handle berbagai format response."""
        try:
            items = (
                data.get("content") or
                data.get("data") or
                data.get("result") or
                []
            )
            if isinstance(items, dict):
                items = [items]
            if not items:
                return None
            item = items[0]
            return {
                "source":        "OSS",
                "nib":           item.get("nib"),
                "kbli":          item.get("kbliUtama") or item.get("kbli") or item.get("kode_kbli"),
                "business_name": item.get("namaPerusahaan") or item.get("nama_perusahaan"),
                "business_type": item.get("jenisPerusahaan") or item.get("jenis_badan_usaha"),
                "address":       item.get("alamat"),
            }
        except Exception:
            return None

    # ----------------------------------------------------------
    # Website scraping
    # ----------------------------------------------------------

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=8))
    async def _scrape_website(self, url: str) -> Optional[dict]:
        """Scrape halaman layanan vendor untuk feed ke AI klasifikasi."""
        if not url.startswith("http"):
            url = f"https://{url}"

        target_paths = ["/", "/about", "/layanan", "/services", "/produk", "/tentang-kami"]
        all_text     = []
        error_msg    = "unknown"

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; AccountingBot/1.0)"},
            ) as client:
                for path in target_paths[:3]:
                    try:
                        resp = await client.get(url + path)
                        if resp.status_code == 200:
                            soup = BeautifulSoup(resp.text, "lxml")
                            for tag in soup(["script", "style", "nav", "footer", "header"]):
                                tag.decompose()
                            text = soup.get_text(separator=" ", strip=True)
                            if text:
                                all_text.append(text[:2000])
                    except Exception:
                        continue

            if all_text:
                return {"source": "website", "url": url, "content": " ".join(all_text)[:5000]}

        except Exception as e:
            error_msg = str(e)
            logger.warning(f"Website scraping error {url}: {e}")

        return None

    # ----------------------------------------------------------
    # AI Klasifikasi via Claude API
    # ----------------------------------------------------------

    def _ai_classify_vendor(self, vendor_name: str, raw_data: dict) -> Optional[dict]:
        """
        Kirim data scraping ke Claude untuk klasifikasi jenis jasa + tax profile.
        Return: dict dengan 'services' dan 'vendor_summary'.
        """
        prompt = f"""Kamu adalah ahli perpajakan Indonesia. Berdasarkan data berikut tentang vendor bernama "{vendor_name}", klasifikasikan informasi berikut dalam format JSON:

Data vendor:
{json.dumps(raw_data, ensure_ascii=False, indent=2)[:3000]}

Berikan output JSON dengan struktur:
{{
  "services": [
    {{
      "service_name": "nama jasa",
      "service_category": "kategori (IT Services / Sewa / Jasa Konsultansi / dll)",
      "pph_object": "PPh23_jasa_teknik / PPh23_jasa_manajemen / PPh4(2)_sewa / PPh21 / non_taxable",
      "pph_rate": 2.0,
      "coa_suggestion": "kode akun",
      "confidence": 0.95
    }}
  ],
  "vendor_summary": {{
    "main_business": "deskripsi bisnis utama",
    "tax_status": "PKP / non_PKP / unknown",
    "default_pph_type": "PPh23",
    "default_pph_rate": 2.0,
    "default_ppn_eligible": true,
    "regulation_notes": "catatan PMK/PP yang relevan"
  }}
}}

Hanya berikan JSON, tanpa penjelasan.
Referensi tarif: PPh23 jasa teknik 2%, PPh23 manajemen 2%, PPh4(2) sewa tanah/bangunan 10%, sewa selain itu 2%."""

        try:
            response = self.ai.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = response.content[0].text.strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(f"AI classification JSON parse error untuk {vendor_name}: {e}")
            return None
        except Exception as e:
            logger.error(f"AI classification error untuk {vendor_name}: {e}")
            return None

    # ----------------------------------------------------------
    # DB helpers
    # ----------------------------------------------------------

    def _get_vendor(self, vendor_id: UUID) -> Optional[dict]:
        result = self.db.execute(
            text("SELECT * FROM vendor WHERE id = :id"),
            {"id": str(vendor_id)}
        ).fetchone()
        return dict(result._mapping) if result else None

    def _update_vendor_from_djp(self, vendor_id: UUID, data: dict):
        self.db.execute(
            text("""
                UPDATE vendor SET
                    klu        = COALESCE(:klu, klu),
                    tax_status = COALESCE(:ts,  tax_status),
                    updated_at = NOW()
                WHERE id = :id
            """),
            {"klu": data.get("klu"), "ts": data.get("tax_status"), "id": str(vendor_id)}
        )
        self.db.commit()
        self._log_scrape(vendor_id, "DJP", None, "success", raw_data=data)

    def _update_vendor_from_oss(self, vendor_id: UUID, data: dict):
        self.db.execute(
            text("""
                UPDATE vendor SET
                    nib        = COALESCE(:nib,  nib),
                    kbli       = COALESCE(:kbli, kbli),
                    updated_at = NOW()
                WHERE id = :id
            """),
            {"nib": data.get("nib"), "kbli": data.get("kbli"), "id": str(vendor_id)}
        )
        self.db.commit()
        self._log_scrape(vendor_id, "OSS", None, "success", raw_data=data)

    def _save_vendor_services(self, vendor_id: UUID, ai_result: dict):
        """Simpan daftar jasa vendor hasil AI ke tabel vendor_services."""
        for svc in ai_result.get("services", []):
            self.db.execute(
                text("""
                    INSERT INTO vendor_services
                        (id, vendor_id, service_name, service_category, source,
                         pph_object, pph_rate, coa_suggestion, confidence)
                    VALUES
                        (:id, :vid, :sn, :sc, 'AI', :po, :pr, :cs, :conf)
                    ON CONFLICT DO NOTHING
                """),
                {
                    "id": str(uuid4()), "vid": str(vendor_id),
                    "sn":   svc.get("service_name"),
                    "sc":   svc.get("service_category"),
                    "po":   svc.get("pph_object"),
                    "pr":   svc.get("pph_rate"),
                    "cs":   svc.get("coa_suggestion"),
                    "conf": svc.get("confidence", 1.0),
                }
            )
        self.db.commit()

    def _update_vendor_tax_profile(self, vendor_id: UUID, ai_result: dict):
        """Update profil pajak default vendor dari hasil AI."""
        summary = ai_result.get("vendor_summary", {})
        self.db.execute(
            text("""
                UPDATE vendor SET
                    default_pph_type     = COALESCE(:pt, default_pph_type),
                    default_pph_rate     = COALESCE(:pr, default_pph_rate),
                    default_ppn_eligible = COALESCE(:pv, default_ppn_eligible),
                    updated_at           = NOW()
                WHERE id = :id
            """),
            {
                "pt": summary.get("default_pph_type"),
                "pr": summary.get("default_pph_rate"),
                "pv": summary.get("default_ppn_eligible"),
                "id": str(vendor_id),
            }
        )
        self.db.commit()

    def _update_scrape_status(
        self,
        vendor_id: UUID,
        status: str,
        needs_manual_djp: bool = False,
    ):
        notes = "DJP lookup gagal — verifikasi NPWP/status PKP secara manual" if needs_manual_djp else None
        self.db.execute(
            text("""
                UPDATE vendor SET
                    scrape_status   = :status,
                    last_scraped_at = NOW(),
                    scrape_notes    = COALESCE(:notes, scrape_notes)
                WHERE id = :id
            """),
            {"status": status, "notes": notes, "id": str(vendor_id)}
        )
        self.db.commit()

    def _log_scrape(
        self,
        vendor_id: Optional[UUID],
        source: str,
        url: Optional[str],
        status: str,
        raw_data: Optional[dict] = None,
        error: Optional[str] = None,
    ):
        try:
            self.db.execute(
                text("""
                    INSERT INTO vendor_scrape_log
                        (id, vendor_id, scrape_source, scrape_url, status, raw_data, error_message)
                    VALUES
                        (:id, :vid, :src, :url, :status, :raw, :err)
                """),
                {
                    "id":     str(uuid4()),
                    "vid":    str(vendor_id) if vendor_id else None,
                    "src":    source,
                    "url":    url,
                    "status": status,
                    "raw":    json.dumps(raw_data, ensure_ascii=False) if raw_data else None,
                    "err":    error,
                }
            )
            self.db.commit()
        except Exception:
            pass
