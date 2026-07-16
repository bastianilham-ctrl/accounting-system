# modules/asset_engine.py
# Fixed Asset Management: jadwal depresiasi komersial + fiskal, prepaid amortization.
# Referensi fiskal: PMK 96/2009 (kelompok aset & tarif penyusutan).

import calendar
from uuid import UUID, uuid4
from decimal import Decimal, ROUND_HALF_UP
from datetime import date
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger

from modules.journal_engine import JournalEngine, JournalEntry, JournalLine


# ----------------------------------------------------------
# REFERENSI FISKAL — PMK 96/2009
# Kelompok aset, masa manfaat fiskal, tarif SL dan DB
# ----------------------------------------------------------

FISCAL_GROUPS: dict = {
    "kelompok_1": {
        "label": "Kelompok I (4 tahun)",
        "life_months": 48,
        "sl_rate": Decimal("25.00"),
        "db_rate": Decimal("50.00"),
    },
    "kelompok_2": {
        "label": "Kelompok II (8 tahun)",
        "life_months": 96,
        "sl_rate": Decimal("12.50"),
        "db_rate": Decimal("25.00"),
    },
    "kelompok_3": {
        "label": "Kelompok III (16 tahun)",
        "life_months": 192,
        "sl_rate": Decimal("6.25"),
        "db_rate": Decimal("12.50"),
    },
    "kelompok_4": {
        "label": "Kelompok IV (20 tahun)",
        "life_months": 240,
        "sl_rate": Decimal("5.00"),
        "db_rate": Decimal("10.00"),
    },
    "bangunan_permanen": {
        "label": "Bangunan Permanen (20 tahun)",
        "life_months": 240,
        "sl_rate": Decimal("5.00"),
        "db_rate": None,            # bangunan hanya SL
    },
    "bangunan_tidak_permanen": {
        "label": "Bangunan Tidak Permanen (10 tahun)",
        "life_months": 120,
        "sl_rate": Decimal("10.00"),
        "db_rate": None,
    },
    "intangible": {
        "label": "Aset Tak Berwujud",
        "life_months": 60,
        "sl_rate": Decimal("20.00"),
        "db_rate": None,
    },
}


class AssetEngine:

    def __init__(self, db: Session):
        self.db      = db
        self.journal = JournalEngine(db)

    # ----------------------------------------------------------
    # FIXED ASSET — CREATE
    # ----------------------------------------------------------

    def create_asset(
        self,
        entity_id: UUID,
        asset_name: str,
        category: str,
        acquisition_date: date,
        acquisition_cost: Decimal,
        salvage_value: Decimal = Decimal("0"),
        useful_life_months: Optional[int] = None,
        method: str = "straight_line",
        coa_asset: str = "1-6-001",
        coa_accum_dep: str = "1-7-001",
        coa_dep_expense: str = "6-2-001",
        ap_invoice_id: Optional[UUID] = None,
        fiscal_method: str = "straight_line",
    ) -> dict:
        """
        Daftarkan aset tetap baru dan generate jadwal depresiasi otomatis.
        Masa manfaat komersial bisa berbeda dengan fiskal (PMK 96/2009).
        """
        fiscal_ref  = FISCAL_GROUPS.get(category, {})

        if useful_life_months is None:
            useful_life_months = fiscal_ref.get("life_months") or 60

        fiscal_life = fiscal_ref.get("life_months") or useful_life_months

        rate_key    = "sl_rate" if fiscal_method == "straight_line" else "db_rate"
        fiscal_rate = fiscal_ref.get(rate_key) or Decimal("0")

        asset_code = self._generate_asset_code(entity_id)
        asset_id   = uuid4()

        self.db.execute(
            text("""
                INSERT INTO fixed_asset
                    (id, entity_id, asset_code, asset_name, category,
                     acquisition_date, acquisition_cost, salvage_value,
                     useful_life_months, fiscal_life_months, method,
                     coa_asset, coa_accum_dep, coa_dep_expense,
                     ap_invoice_id, fiscal_group, fiscal_method, fiscal_rate, status)
                VALUES
                    (:id, :eid, :code, :name, :cat,
                     :adate, :cost, :salvage,
                     :life, :flife, :method,
                     :coa_a, :coa_ad, :coa_de,
                     :ap_inv, :fgroup, :fmethod, :frate, 'active')
            """),
            {
                "id": str(asset_id), "eid": str(entity_id),
                "code": asset_code, "name": asset_name, "cat": category,
                "adate": acquisition_date,
                "cost": float(acquisition_cost), "salvage": float(salvage_value),
                "life": useful_life_months, "flife": fiscal_life,
                "method": method,
                "coa_a": coa_asset, "coa_ad": coa_accum_dep, "coa_de": coa_dep_expense,
                "ap_inv": str(ap_invoice_id) if ap_invoice_id else None,
                "fgroup": category, "fmethod": fiscal_method,
                "frate": float(fiscal_rate),
            }
        )
        self.db.commit()

        schedule_count = self.generate_depreciation_schedule(asset_id)

        logger.info(
            f"Asset created: {asset_code} — {asset_name} | "
            f"cost Rp {acquisition_cost:,.0f} | {schedule_count} periode"
        )
        return {
            "success":          True,
            "asset_id":         str(asset_id),
            "asset_code":       asset_code,
            "useful_life_months": useful_life_months,
            "fiscal_life_months": fiscal_life,
            "fiscal_rate":      float(fiscal_rate),
            "schedule_periods": schedule_count,
        }

    # ----------------------------------------------------------
    # DEPRECIATION SCHEDULE — GENERATE
    # ----------------------------------------------------------

    def generate_depreciation_schedule(self, asset_id: UUID) -> int:
        """
        Generate jadwal depresiasi bulanan: komersial DAN fiskal per periode.
        Beda waktu (temp_diff) = commercial_dep - fiscal_dep → dasar koreksi fiskal SPT Badan.
        Hapus jadwal yang belum diposting sebelum regenerate.
        """
        asset = self._get_asset(asset_id)
        if not asset:
            return 0

        self.db.execute(
            text("""
                DELETE FROM asset_depreciation_schedule
                WHERE asset_id = :id AND is_posted = FALSE
            """),
            {"id": str(asset_id)}
        )

        cost          = Decimal(str(asset["acquisition_cost"]))
        salvage       = Decimal(str(asset["salvage_value"]))
        life          = int(asset["useful_life_months"])
        fiscal_life   = int(asset["fiscal_life_months"] or life)
        method        = asset["method"]
        fiscal_method = asset["fiscal_method"] or method
        fiscal_rate   = Decimal(str(asset["fiscal_rate"] or 0))
        acq_date      = asset["acquisition_date"]

        depreciable   = cost - salvage
        monthly_sl    = (depreciable / life).quantize(Decimal("0.01"), ROUND_HALF_UP)

        book_value    = cost
        fiscal_bv     = cost
        periods       = 0

        for i in range(life):
            period_date = self._add_months(acq_date, i + 1)
            period_id   = self._get_or_create_period_id(asset["entity_id"], period_date)

            # --- Depresiasi komersial ---
            if method == "straight_line":
                com_dep = monthly_sl
                if i == life - 1:
                    com_dep = book_value - salvage   # last: sisanya sekalian
            elif method == "declining_balance":
                annual_rate = Decimal("2") / Decimal(str(life)) * 12
                com_dep = (book_value * annual_rate / 12).quantize(Decimal("0.01"), ROUND_HALF_UP)
                if book_value - com_dep < salvage:
                    com_dep = book_value - salvage
            else:
                com_dep = monthly_sl

            com_dep = max(com_dep, Decimal("0"))

            # --- Depresiasi fiskal (PMK 96/2009) ---
            if i < fiscal_life:
                if fiscal_method == "straight_line":
                    fis_dep = (cost * fiscal_rate / 100 / 12).quantize(Decimal("0.01"), ROUND_HALF_UP)
                elif fiscal_method == "declining_balance":
                    fis_dep = (fiscal_bv * fiscal_rate / 100 / 12).quantize(Decimal("0.01"), ROUND_HALF_UP)
                    if fiscal_bv - fis_dep < Decimal("0"):
                        fis_dep = fiscal_bv
                else:
                    fis_dep = Decimal("0")
            else:
                fis_dep = Decimal("0")

            book_value = max(book_value - com_dep, salvage)
            fiscal_bv  = max(fiscal_bv  - fis_dep, Decimal("0"))

            self.db.execute(
                text("""
                    INSERT INTO asset_depreciation_schedule
                        (id, asset_id, period_id, period_date,
                         commercial_dep, fiscal_dep,
                         book_value_end, fiscal_value_end, is_posted)
                    VALUES
                        (:id, :aid, :pid, :pdate,
                         :com_dep, :fis_dep,
                         :bv_end, :fv_end, FALSE)
                    ON CONFLICT (asset_id, period_id) DO NOTHING
                """),
                {
                    "id": str(uuid4()), "aid": str(asset_id),
                    "pid": str(period_id), "pdate": period_date,
                    "com_dep": float(com_dep), "fis_dep": float(fis_dep),
                    "bv_end": float(book_value), "fv_end": float(fiscal_bv),
                }
            )
            periods += 1

        self.db.commit()
        return periods

    # ----------------------------------------------------------
    # POST MONTHLY DEPRECIATION — BATCH
    # ----------------------------------------------------------

    def post_monthly_depreciation(
        self,
        entity_id: UUID,
        period_date: date,
        posted_by: str = "system",
    ) -> dict:
        """
        Posting jurnal depresiasi komersial untuk semua aset aktif pada bulan tertentu.
        Dr. Beban Depresiasi (coa_dep_expense)   commercial_dep
          Cr. Akumulasi Depresiasi (coa_accum_dep)  commercial_dep
        """
        year, month = period_date.year, period_date.month

        schedules = self.db.execute(
            text("""
                SELECT
                    ads.id           AS schedule_id,
                    ads.asset_id,
                    ads.period_id,
                    ads.commercial_dep,
                    ads.fiscal_dep,
                    fa.asset_code,
                    fa.asset_name,
                    fa.entity_id,
                    fa.coa_dep_expense,
                    fa.coa_accum_dep
                FROM asset_depreciation_schedule ads
                JOIN fixed_asset fa ON fa.id = ads.asset_id
                WHERE fa.entity_id = :eid
                  AND fa.status    = 'active'
                  AND EXTRACT(YEAR  FROM ads.period_date) = :y
                  AND EXTRACT(MONTH FROM ads.period_date) = :m
                  AND ads.is_posted = FALSE
            """),
            {"eid": str(entity_id), "y": year, "m": month}
        ).fetchall()

        if not schedules:
            return {
                "success": True, "posted": 0,
                "message": f"Tidak ada depresiasi untuk periode {year}-{month:02d}",
            }

        posted = 0
        errors = []
        for row in schedules:
            s   = dict(row._mapping)
            dep = Decimal(str(s["commercial_dep"]))
            if dep <= 0:
                continue

            lines = [
                JournalLine(
                    account_code=s["coa_dep_expense"] or "6-2-001",
                    description=f"Depresiasi {s['asset_name']} {year}/{month:02d}",
                    debit_idr=dep,
                ),
                JournalLine(
                    account_code=s["coa_accum_dep"] or "1-7-001",
                    description=f"Akum. Dep {s['asset_name']} {year}/{month:02d}",
                    credit_idr=dep,
                ),
            ]
            entry = JournalEntry(
                entity_id=entity_id,
                journal_type="ASSET",
                journal_date=period_date,
                description=f"Depresiasi {s['asset_code']} — {s['asset_name']}",
                lines=lines,
                source="auto",
                created_by=posted_by,
            )
            result = self.journal.post_journal(entry)
            if result["success"]:
                self.db.execute(
                    text("""
                        UPDATE asset_depreciation_schedule
                        SET is_posted = TRUE, journal_id = :jid
                        WHERE id = :sid
                    """),
                    {"jid": result["journal_id"], "sid": s["schedule_id"]}
                )
                posted += 1
            else:
                errors.append(f"{s['asset_code']}: {result['error']}")

        self.db.commit()
        logger.info(f"Depresiasi posted: {posted} aset | periode {year}-{month:02d}")
        return {
            "success": True,
            "posted":  posted,
            "errors":  errors,
            "period":  f"{year}-{month:02d}",
        }

    # ----------------------------------------------------------
    # FISCAL CORRECTION REPORT — SPT BADAN
    # ----------------------------------------------------------

    def get_fiscal_correction_summary(self, entity_id: UUID, year: int) -> dict:
        """
        Laporan koreksi fiskal depresiasi untuk SPT Badan.
        Beda waktu = commercial_dep - fiscal_dep per aset per tahun.
        Koreksi positif (com > fis) = tambah laba fiskal.
        Koreksi negatif (fis > com) = kurangi laba fiskal.
        """
        rows = self.db.execute(
            text("""
                SELECT
                    fa.asset_code,
                    fa.asset_name,
                    fa.category,
                    fa.fiscal_group,
                    fa.fiscal_method,
                    fa.fiscal_rate,
                    fa.acquisition_cost,
                    SUM(ads.commercial_dep) AS commercial_total,
                    SUM(ads.fiscal_dep)     AS fiscal_total,
                    SUM(ads.temp_diff)      AS temp_diff_year
                FROM asset_depreciation_schedule ads
                JOIN fixed_asset fa ON fa.id = ads.asset_id
                WHERE fa.entity_id                   = :eid
                  AND EXTRACT(YEAR FROM ads.period_date) = :y
                  AND ads.is_posted = TRUE
                GROUP BY fa.id, fa.asset_code, fa.asset_name, fa.category,
                         fa.fiscal_group, fa.fiscal_method, fa.fiscal_rate,
                         fa.acquisition_cost
                ORDER BY fa.asset_code
            """),
            {"eid": str(entity_id), "y": year}
        ).fetchall()

        assets = [dict(r._mapping) for r in rows]
        total_com  = sum(float(r["commercial_total"] or 0) for r in assets)
        total_fis  = sum(float(r["fiscal_total"]     or 0) for r in assets)
        koreksi    = total_com - total_fis

        return {
            "year":   year,
            "assets": assets,
            "summary": {
                "total_commercial_dep":    total_com,
                "total_fiscal_dep":        total_fis,
                "koreksi_fiskal_positif":  max(koreksi,  0),   # com > fis → laba fiskal naik
                "koreksi_fiskal_negatif":  max(-koreksi, 0),   # fis > com → laba fiskal turun
                "net_koreksi":             koreksi,
            },
        }

    # ----------------------------------------------------------
    # PREPAID EXPENSE — CREATE
    # ----------------------------------------------------------

    def create_prepaid(
        self,
        entity_id: UUID,
        description: str,
        start_date: date,
        end_date: date,
        total_amount: Decimal,
        coa_prepaid: str = "1-5-001",
        coa_expense: str = "6-1-003",
        ap_invoice_id: Optional[UUID] = None,
    ) -> dict:
        """
        Buat biaya dibayar dimuka (prepaid expense) dan generate jadwal amortisasi.
        Monthly amount = total / jumlah bulan (sisa cent ke bulan terakhir).
        """
        months = self._months_between(start_date, end_date)
        if months <= 0:
            raise ValueError("end_date harus setelah start_date")

        monthly      = (total_amount / months).quantize(Decimal("1"), ROUND_HALF_UP)
        prepaid_code = self._generate_prepaid_code(entity_id)
        prepaid_id   = uuid4()

        self.db.execute(
            text("""
                INSERT INTO prepaid_expense
                    (id, entity_id, prepaid_code, description,
                     start_date, end_date, total_amount, monthly_amount,
                     coa_prepaid, coa_expense, ap_invoice_id, status)
                VALUES
                    (:id, :eid, :code, :desc,
                     :sd, :ed, :total, :monthly,
                     :coa_p, :coa_e, :ap_inv, 'active')
            """),
            {
                "id": str(prepaid_id), "eid": str(entity_id),
                "code": prepaid_code, "desc": description,
                "sd": start_date, "ed": end_date,
                "total": float(total_amount), "monthly": float(monthly),
                "coa_p": coa_prepaid, "coa_e": coa_expense,
                "ap_inv": str(ap_invoice_id) if ap_invoice_id else None,
            }
        )
        self.db.commit()

        schedule_count = self.generate_amortization_schedule(prepaid_id)
        logger.info(f"Prepaid created: {prepaid_code} | {description} | {schedule_count} bulan")
        return {
            "success":        True,
            "prepaid_id":     str(prepaid_id),
            "prepaid_code":   prepaid_code,
            "months":         months,
            "monthly_amount": float(monthly),
            "schedule_months": schedule_count,
        }

    # ----------------------------------------------------------
    # AMORTIZATION SCHEDULE — GENERATE
    # ----------------------------------------------------------

    def generate_amortization_schedule(self, prepaid_id: UUID) -> int:
        """Generate jadwal amortisasi bulanan untuk prepaid expense."""
        row = self.db.execute(
            text("SELECT * FROM prepaid_expense WHERE id = :id"),
            {"id": str(prepaid_id)}
        ).fetchone()
        if not row:
            return 0
        p = dict(row._mapping)

        self.db.execute(
            text("""
                DELETE FROM prepaid_amortization_schedule
                WHERE prepaid_id = :id AND is_posted = FALSE
            """),
            {"id": str(prepaid_id)}
        )

        start     = p["start_date"]
        end       = p["end_date"]
        total     = Decimal(str(p["total_amount"]))
        monthly   = Decimal(str(p["monthly_amount"]))
        remaining = total
        months    = self._months_between(start, end)
        count     = 0

        for i in range(months):
            period_date  = self._add_months(start, i)
            period_id    = self._get_or_create_period_id(p["entity_id"], period_date)
            is_last      = (i == months - 1)
            amort_amount = remaining if is_last else min(monthly, remaining)
            remaining   -= amort_amount

            self.db.execute(
                text("""
                    INSERT INTO prepaid_amortization_schedule
                        (id, prepaid_id, period_id, period_date,
                         amortize_amount, remaining_amount, is_posted)
                    VALUES
                        (:id, :pid, :period_id, :pdate,
                         :amt, :rem, FALSE)
                    ON CONFLICT (prepaid_id, period_id) DO NOTHING
                """),
                {
                    "id": str(uuid4()), "pid": str(prepaid_id),
                    "period_id": str(period_id), "pdate": period_date,
                    "amt": float(amort_amount), "rem": float(remaining),
                }
            )
            count += 1

        self.db.commit()
        return count

    # ----------------------------------------------------------
    # POST MONTHLY AMORTIZATION — BATCH
    # ----------------------------------------------------------

    def post_monthly_amortization(
        self,
        entity_id: UUID,
        period_date: date,
        posted_by: str = "system",
    ) -> dict:
        """
        Posting jurnal amortisasi untuk semua prepaid aktif pada bulan tertentu.
        Dr. Beban Sewa/Asuransi/dll (coa_expense)   amortize_amount
          Cr. Biaya Dibayar Dimuka (coa_prepaid)     amortize_amount
        """
        year, month = period_date.year, period_date.month

        schedules = self.db.execute(
            text("""
                SELECT
                    pas.id           AS schedule_id,
                    pas.prepaid_id,
                    pas.period_id,
                    pas.amortize_amount,
                    pe.prepaid_code,
                    pe.description,
                    pe.entity_id,
                    pe.coa_prepaid,
                    pe.coa_expense
                FROM prepaid_amortization_schedule pas
                JOIN prepaid_expense pe ON pe.id = pas.prepaid_id
                WHERE pe.entity_id = :eid
                  AND pe.status    = 'active'
                  AND EXTRACT(YEAR  FROM pas.period_date) = :y
                  AND EXTRACT(MONTH FROM pas.period_date) = :m
                  AND pas.is_posted = FALSE
            """),
            {"eid": str(entity_id), "y": year, "m": month}
        ).fetchall()

        if not schedules:
            return {
                "success": True, "posted": 0,
                "message": f"Tidak ada amortisasi untuk periode {year}-{month:02d}",
            }

        posted = 0
        errors = []
        for row in schedules:
            s   = dict(row._mapping)
            amt = Decimal(str(s["amortize_amount"]))
            if amt <= 0:
                continue

            lines = [
                JournalLine(
                    account_code=s["coa_expense"] or "6-1-003",
                    description=f"Amortisasi {s['description']} {year}/{month:02d}",
                    debit_idr=amt,
                ),
                JournalLine(
                    account_code=s["coa_prepaid"] or "1-5-001",
                    description=f"Amortisasi {s['prepaid_code']} {year}/{month:02d}",
                    credit_idr=amt,
                ),
            ]
            entry = JournalEntry(
                entity_id=entity_id,
                journal_type="PREPAID",
                journal_date=period_date,
                description=f"Amortisasi {s['prepaid_code']} — {s['description']}",
                lines=lines,
                source="auto",
                created_by=posted_by,
            )
            result = self.journal.post_journal(entry)
            if result["success"]:
                self.db.execute(
                    text("""
                        UPDATE prepaid_amortization_schedule
                        SET is_posted = TRUE, journal_id = :jid
                        WHERE id = :sid
                    """),
                    {"jid": result["journal_id"], "sid": s["schedule_id"]}
                )
                posted += 1
            else:
                errors.append(f"{s['prepaid_code']}: {result['error']}")

        self.db.commit()
        logger.info(f"Amortisasi posted: {posted} prepaid | periode {year}-{month:02d}")
        return {
            "success": True,
            "posted":  posted,
            "errors":  errors,
            "period":  f"{year}-{month:02d}",
        }

    # ----------------------------------------------------------
    # HELPERS
    # ----------------------------------------------------------

    def _get_asset(self, asset_id: UUID) -> Optional[dict]:
        row = self.db.execute(
            text("SELECT * FROM fixed_asset WHERE id = :id"),
            {"id": str(asset_id)}
        ).fetchone()
        return dict(row._mapping) if row else None

    def _get_or_create_period_id(self, entity_id, period_date: date) -> UUID:
        period = self.journal._get_or_create_period(entity_id, period_date)
        return period["id"]

    def _generate_asset_code(self, entity_id: UUID) -> str:
        result = self.db.execute(
            text("SELECT COUNT(*) AS cnt FROM fixed_asset WHERE entity_id = :eid"),
            {"eid": str(entity_id)}
        ).fetchone()
        return f"FA-{(result.cnt or 0) + 1:04d}"

    def _generate_prepaid_code(self, entity_id: UUID) -> str:
        result = self.db.execute(
            text("SELECT COUNT(*) AS cnt FROM prepaid_expense WHERE entity_id = :eid"),
            {"eid": str(entity_id)}
        ).fetchone()
        return f"PP-{(result.cnt or 0) + 1:04d}"

    def _add_months(self, d: date, months: int) -> date:
        month = d.month - 1 + months
        year  = d.year + month // 12
        month = month % 12 + 1
        day   = min(d.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)

    def _months_between(self, start: date, end: date) -> int:
        return (end.year - start.year) * 12 + (end.month - start.month) + 1
