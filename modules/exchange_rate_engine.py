"""
Exchange Rate Engine
====================
Mengelola kurs mata uang asing (FCY) terhadap IDR.

Fitur:
  - Simpan kurs harian (manual / BI rate / market)
  - Lookup kurs untuk tanggal tertentu (fallback ke tanggal terdekat sebelumnya)
  - Konversi FCY → IDR dan sebaliknya
  - Import batch kurs (misal dari BI atau market data)
  - History & trend kurs
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from sqlalchemy import text
from sqlalchemy.orm import Session


class ExchangeRateEngine:

    # ── Lookup ───────────────────────────────────────────────────────────────

    @staticmethod
    def get_rate(
        db: Session,
        from_currency: str,
        to_currency: str = "IDR",
        rate_date: Optional[date] = None,
        rate_type: str = "middle",
    ) -> Optional[Decimal]:
        """
        Ambil kurs dari_currency → ke_currency pada tanggal tertentu.
        Jika tidak ada kurs tepat pada tanggal tersebut, gunakan kurs
        terbaru yang tersedia SEBELUM tanggal itu (lookback max 30 hari).
        Return None jika tidak ada kurs sama sekali.
        """
        if from_currency == to_currency:
            return Decimal("1")

        if rate_date is None:
            rate_date = date.today()

        # IDR adalah base currency → rate = 1
        if from_currency == "IDR" and to_currency == "IDR":
            return Decimal("1")

        # Kalau to_currency != IDR → kita cross via IDR
        if to_currency != "IDR":
            rate_fc_to_idr = ExchangeRateEngine.get_rate(db, from_currency, "IDR", rate_date, rate_type)
            rate_tc_to_idr = ExchangeRateEngine.get_rate(db, to_currency, "IDR", rate_date, rate_type)
            if rate_fc_to_idr is None or rate_tc_to_idr is None or rate_tc_to_idr == 0:
                return None
            return (rate_fc_to_idr / rate_tc_to_idr).quantize(Decimal("0.000001"), ROUND_HALF_UP)

        row = db.execute(text("""
            SELECT rate FROM exchange_rate
            WHERE from_currency = :fc
              AND to_currency   = :tc
              AND rate_type     = :rt
              AND rate_date    <= :dt
            ORDER BY rate_date DESC
            LIMIT 1
        """), {"fc": from_currency, "tc": to_currency, "rt": rate_type, "dt": rate_date}).first()

        if row is None:
            return None
        return Decimal(str(row.rate))

    @staticmethod
    def get_rate_or_raise(
        db: Session,
        from_currency: str,
        to_currency: str = "IDR",
        rate_date: Optional[date] = None,
        rate_type: str = "middle",
    ) -> Decimal:
        """get_rate tapi raise ValueError jika tidak ditemukan."""
        rate = ExchangeRateEngine.get_rate(db, from_currency, to_currency, rate_date, rate_type)
        if rate is None:
            d = rate_date or date.today()
            raise ValueError(
                f"Kurs {from_currency}/{to_currency} tidak ditemukan untuk tanggal {d}. "
                "Silakan input kurs terlebih dahulu."
            )
        return rate

    # ── Konversi ─────────────────────────────────────────────────────────────

    @staticmethod
    def convert(
        db: Session,
        amount: Decimal,
        from_currency: str,
        to_currency: str = "IDR",
        rate_date: Optional[date] = None,
        rate_type: str = "middle",
    ) -> Decimal:
        """
        Konversi jumlah dari from_currency ke to_currency.
        Raise ValueError jika kurs tidak tersedia.
        """
        if from_currency == to_currency:
            return Decimal(str(amount))

        rate = ExchangeRateEngine.get_rate_or_raise(db, from_currency, to_currency, rate_date, rate_type)
        result = Decimal(str(amount)) * rate
        # Bulatkan ke 0 desimal untuk IDR
        if to_currency == "IDR":
            return result.quantize(Decimal("1"), ROUND_HALF_UP)
        return result.quantize(Decimal("0.01"), ROUND_HALF_UP)

    @staticmethod
    def convert_batch(
        db: Session,
        items: list[dict],
        to_currency: str = "IDR",
        rate_type: str = "middle",
    ) -> list[dict]:
        """
        Konversi batch.
        items: [{amount, from_currency, rate_date(opsional)}]
        Return: item dict + idr_amount + rate
        """
        results = []
        for item in items:
            from_cur = item["from_currency"]
            amt = Decimal(str(item["amount"]))
            dt = item.get("rate_date") or date.today()

            if from_cur == to_currency:
                results.append({**item, "idr_amount": amt, "rate": Decimal("1")})
                continue

            rate = ExchangeRateEngine.get_rate(db, from_cur, to_currency, dt, rate_type)
            if rate is None:
                results.append({**item, "idr_amount": None, "rate": None, "error": f"Kurs {from_cur} tidak ada"})
            else:
                converted = (amt * rate).quantize(Decimal("1"), ROUND_HALF_UP)
                results.append({**item, "idr_amount": converted, "rate": rate})
        return results

    # ── Simpan Kurs ───────────────────────────────────────────────────────────

    @staticmethod
    def set_rate(
        db: Session,
        from_currency: str,
        to_currency: str = "IDR",
        rate_date: date = None,
        rate: Decimal = None,
        rate_type: str = "middle",
        source: str = "manual",
        notes: str = None,
        created_by: str = None,
    ) -> dict:
        """
        Simpan atau update kurs.
        UPSERT: jika (from_currency, to_currency, rate_date, rate_type) sudah ada → update.
        """
        if rate_date is None:
            rate_date = date.today()
        if rate is None or Decimal(str(rate)) <= 0:
            raise ValueError("Rate harus > 0")
        if from_currency == to_currency:
            raise ValueError("from_currency dan to_currency tidak boleh sama")

        row = db.execute(text("""
            INSERT INTO exchange_rate
                (from_currency, to_currency, rate_date, rate, rate_type, source, notes, created_by)
            VALUES
                (:fc, :tc, :dt, :rate, :rt, :src, :notes, :cb)
            ON CONFLICT (from_currency, to_currency, rate_date, rate_type)
            DO UPDATE SET
                rate       = EXCLUDED.rate,
                source     = EXCLUDED.source,
                notes      = EXCLUDED.notes,
                created_by = EXCLUDED.created_by,
                created_at = NOW()
            RETURNING id, rate_date
        """), {
            "fc": from_currency, "tc": to_currency, "dt": rate_date,
            "rate": str(rate), "rt": rate_type, "src": source,
            "notes": notes, "cb": created_by
        }).first()
        db.commit()
        return {"id": str(row.id), "from_currency": from_currency, "rate_date": str(rate_date), "rate": str(rate)}

    @staticmethod
    def import_bi_rates(
        db: Session,
        rates: list[dict],
        created_by: str = "system",
    ) -> dict:
        """
        Batch import kurs dari Bank Indonesia atau source lain.
        rates: [{from_currency, rate_date, rate, rate_type?}]
        """
        inserted = 0
        updated = 0
        errors = []
        for item in rates:
            try:
                existing = db.execute(text("""
                    SELECT id FROM exchange_rate
                    WHERE from_currency = :fc AND to_currency = 'IDR'
                      AND rate_date = :dt AND rate_type = :rt
                """), {
                    "fc": item["from_currency"],
                    "dt": item["rate_date"],
                    "rt": item.get("rate_type", "middle"),
                }).first()

                if existing:
                    db.execute(text("""
                        UPDATE exchange_rate SET rate = :rate, source = :src, created_by = :cb
                        WHERE id = :id
                    """), {"rate": str(item["rate"]), "src": item.get("source", "bi_rate"),
                           "cb": created_by, "id": str(existing.id)})
                    updated += 1
                else:
                    db.execute(text("""
                        INSERT INTO exchange_rate
                            (from_currency, to_currency, rate_date, rate, rate_type, source, created_by)
                        VALUES (:fc, 'IDR', :dt, :rate, :rt, :src, :cb)
                    """), {
                        "fc": item["from_currency"],
                        "dt": item["rate_date"],
                        "rate": str(item["rate"]),
                        "rt": item.get("rate_type", "middle"),
                        "src": item.get("source", "bi_rate"),
                        "cb": created_by,
                    })
                    inserted += 1
            except Exception as e:
                errors.append({"item": item, "error": str(e)})

        db.commit()
        return {"inserted": inserted, "updated": updated, "errors": errors}

    # ── Hapus Kurs ────────────────────────────────────────────────────────────

    @staticmethod
    def delete_rate(
        db: Session,
        from_currency: str,
        to_currency: str,
        rate_date: date,
        rate_type: str = "middle",
    ) -> bool:
        result = db.execute(text("""
            DELETE FROM exchange_rate
            WHERE from_currency = :fc AND to_currency = :tc
              AND rate_date = :dt AND rate_type = :rt
        """), {"fc": from_currency, "tc": to_currency, "dt": rate_date, "rt": rate_type})
        db.commit()
        return result.rowcount > 0

    # ── Query / History ───────────────────────────────────────────────────────

    @staticmethod
    def list_currencies(db: Session, active_only: bool = True) -> list[dict]:
        rows = db.execute(text("""
            SELECT currency_code, currency_name, symbol, decimal_places,
                   is_base_currency, is_active
            FROM currency
            WHERE (:active_only = FALSE OR is_active = TRUE)
            ORDER BY is_base_currency DESC, currency_code
        """), {"active_only": active_only}).fetchall()
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def get_rate_history(
        db: Session,
        from_currency: str,
        to_currency: str = "IDR",
        rate_type: str = "middle",
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        limit: int = 90,
    ) -> list[dict]:
        if date_to is None:
            date_to = date.today()
        if date_from is None:
            date_from = date_to - timedelta(days=90)

        rows = db.execute(text("""
            SELECT rate_date, rate, rate_type, source, notes, created_by, created_at
            FROM exchange_rate
            WHERE from_currency = :fc AND to_currency = :tc
              AND rate_type = :rt
              AND rate_date BETWEEN :df AND :dt
            ORDER BY rate_date DESC
            LIMIT :lim
        """), {
            "fc": from_currency, "tc": to_currency, "rt": rate_type,
            "df": date_from, "dt": date_to, "lim": limit,
        }).fetchall()
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def get_latest_rates(
        db: Session,
        currency_list: Optional[list[str]] = None,
        rate_type: str = "middle",
    ) -> list[dict]:
        """
        Ambil kurs terbaru semua mata uang (atau subset).
        Berguna untuk tampilan dashboard kurs hari ini.
        """
        if currency_list:
            filter_sql = "AND from_currency = ANY(:currencies)"
            params = {"currencies": currency_list, "rt": rate_type}
        else:
            filter_sql = ""
            params = {"rt": rate_type}

        rows = db.execute(text(f"""
            SELECT DISTINCT ON (from_currency)
                from_currency,
                to_currency,
                rate_type,
                rate_date,
                rate,
                source
            FROM exchange_rate
            WHERE to_currency = 'IDR' AND rate_type = :rt
              {filter_sql}
            ORDER BY from_currency, rate_date DESC
        """), params).fetchall()
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def get_rate_for_period(
        db: Session,
        from_currency: str,
        fiscal_year: int,
        fiscal_month: int,
        rate_type: str = "middle",
    ) -> Optional[dict]:
        """
        Ambil kurs akhir bulan (tanggal 28/29/30/31) untuk sebuah periode fiskal.
        Digunakan saat revaluation → cari kurs tanggal akhir bulan.
        """
        import calendar
        last_day = calendar.monthrange(fiscal_year, fiscal_month)[1]
        period_end = date(fiscal_year, fiscal_month, last_day)

        rate = ExchangeRateEngine.get_rate(db, from_currency, "IDR", period_end, rate_type)
        if rate is None:
            return None
        return {
            "from_currency": from_currency,
            "to_currency": "IDR",
            "rate_date": str(period_end),
            "rate": str(rate),
            "rate_type": rate_type,
        }

    # ── Manage Currency Master ─────────────────────────────────────────────────

    @staticmethod
    def add_currency(
        db: Session,
        currency_code: str,
        currency_name: str,
        symbol: str = None,
        decimal_places: int = 2,
    ) -> dict:
        row = db.execute(text("""
            INSERT INTO currency (currency_code, currency_name, symbol, decimal_places)
            VALUES (:code, :name, :symbol, :dp)
            ON CONFLICT (currency_code) DO UPDATE
               SET currency_name = EXCLUDED.currency_name,
                   symbol = EXCLUDED.symbol,
                   decimal_places = EXCLUDED.decimal_places,
                   is_active = TRUE
            RETURNING id, currency_code
        """), {
            "code": currency_code.upper(),
            "name": currency_name,
            "symbol": symbol,
            "dp": decimal_places,
        }).first()
        db.commit()
        return {"id": str(row.id), "currency_code": row.currency_code}

    @staticmethod
    def deactivate_currency(db: Session, currency_code: str) -> bool:
        if currency_code == "IDR":
            raise ValueError("IDR adalah base currency, tidak bisa dinonaktifkan.")
        result = db.execute(text("""
            UPDATE currency SET is_active = FALSE WHERE currency_code = :code
        """), {"code": currency_code})
        db.commit()
        return result.rowcount > 0
