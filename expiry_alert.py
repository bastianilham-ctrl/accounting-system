"""
expiry_alert.py
===============
#3 dari roadmap: Expiry Alert System untuk dokumen pajak vendor (SKB & UMKM).

Apa yang dilakukan:
  1. Scan semua vendor dengan SKB / Surat UMKM yang:
     - Sudah expired
     - Akan expired dalam N hari (default 30)
  2. Kategorikan: EXPIRED / CRITICAL (<7 hari) / WARNING (<30 hari)
  3. Print laporan ke console
  4. (Opsional) Tulis ke tabel vendor_tax_log sebagai notifikasi
  5. (Opsional) Export ke CSV untuk dikirim ke tim finance

Cara jalankan:
  python expiry_alert.py                       ← laporan 30 hari ke depan
  python expiry_alert.py --days 60             ← horizon 60 hari
  python expiry_alert.py --csv alerts.csv      ← export ke CSV
  python expiry_alert.py --log                 ← catat ke vendor_tax_log
  python expiry_alert.py --entity-id <uuid>    ← filter satu entity

Untuk scheduling otomatis (Windows Task Scheduler):
  Jalankan harian: python C:\\...\\expiry_alert.py --csv daily_alerts.csv --log
"""

import os
import sys
import csv
import argparse
from datetime import date, datetime, timedelta
from uuid import uuid4

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from loguru import logger

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD','')}@"
    f"{os.getenv('DB_HOST','localhost')}:{os.getenv('DB_PORT','5432')}/"
    f"{os.getenv('DB_NAME','accounting_db')}"
)

CRITICAL_DAYS = 7    # < 7 hari = CRITICAL
WARNING_DAYS  = 30   # < 30 hari = WARNING (default horizon)

# ─────────────────────────────────────────────────────────────────────────────
# FETCH
# ─────────────────────────────────────────────────────────────────────────────

def get_expiring_documents(session, horizon_days=30, entity_id=None):
    """
    Ambil vendor dengan SKB / UMKM yang expired atau mau expired.
    Mengembalikan list of dict, satu baris per dokumen.
    """
    cutoff = date.today() + timedelta(days=horizon_days)

    entity_filter = "AND v.entity_id = :entity_id" if entity_id else ""
    params = {"cutoff": cutoff}
    if entity_id:
        params["entity_id"] = entity_id

    # SKB yang punya expiry dan <= cutoff
    skb_rows = session.execute(
        text(f"""
            SELECT
                v.id, v.vendor_code, v.vendor_name, v.entity_id,
                'SKB' AS doc_type,
                v.skb_number AS doc_number,
                v.skb_expiry AS expiry_date,
                (v.skb_expiry - CURRENT_DATE) AS days_left
            FROM vendor v
            WHERE v.has_skb = TRUE
              AND v.skb_expiry IS NOT NULL
              AND v.skb_expiry <= :cutoff
              {entity_filter}
            ORDER BY v.skb_expiry ASC
        """),
        params
    ).fetchall()

    # UMKM cert yang punya expiry dan <= cutoff
    umkm_rows = session.execute(
        text(f"""
            SELECT
                v.id, v.vendor_code, v.vendor_name, v.entity_id,
                'UMKM_CERT' AS doc_type,
                v.umkm_cert_number AS doc_number,
                v.umkm_cert_expiry AS expiry_date,
                (v.umkm_cert_expiry - CURRENT_DATE) AS days_left
            FROM vendor v
            WHERE v.vendor_category = 'UMKM'
              AND v.umkm_cert_expiry IS NOT NULL
              AND v.umkm_cert_expiry <= :cutoff
              {entity_filter}
            ORDER BY v.umkm_cert_expiry ASC
        """),
        params
    ).fetchall()

    docs = [dict(r._mapping) for r in skb_rows] + [dict(r._mapping) for r in umkm_rows]

    # Tambah kategori severity
    for d in docs:
        days = d["days_left"]
        if days < 0:
            d["severity"] = "EXPIRED"
        elif days <= CRITICAL_DAYS:
            d["severity"] = "CRITICAL"
        else:
            d["severity"] = "WARNING"

    # Urutkan: EXPIRED dulu, lalu by days_left
    severity_order = {"EXPIRED": 0, "CRITICAL": 1, "WARNING": 2}
    docs.sort(key=lambda d: (severity_order[d["severity"]], d["days_left"]))
    return docs


# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT
# ─────────────────────────────────────────────────────────────────────────────

def print_report(docs, horizon_days):
    expired  = [d for d in docs if d["severity"] == "EXPIRED"]
    critical = [d for d in docs if d["severity"] == "CRITICAL"]
    warning  = [d for d in docs if d["severity"] == "WARNING"]

    print(f"\n{'='*72}")
    print(f"  EXPIRY ALERT — DOKUMEN PAJAK VENDOR")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Horizon: {horizon_days} hari")
    print(f"{'='*72}")
    print(f"  🔴 EXPIRED          : {len(expired):>4}")
    print(f"  🟠 CRITICAL (<{CRITICAL_DAYS}hr) : {len(critical):>4}")
    print(f"  🟡 WARNING  (<{horizon_days}hr): {len(warning):>4}")
    print(f"  Total dokumen       : {len(docs):>4}")
    print(f"{'='*72}")

    if not docs:
        print(f"\n  ✅ Tidak ada dokumen yang expired atau mendekati expiry.\n")
        return

    icon = {"EXPIRED": "🔴", "CRITICAL": "🟠", "WARNING": "🟡"}

    print(f"\n  {'':<2} {'TIPE':<10} {'VENDOR':<26} {'NO DOKUMEN':<16} {'EXPIRY':<12} {'SISA':>8}")
    print(f"  {'-'*82}")
    for d in docs:
        days = d["days_left"]
        sisa = f"{days}hr" if days >= 0 else f"{abs(days)}hr lalu"
        print(
            f"  {icon[d['severity']]} "
            f"{d['doc_type']:<10} "
            f"{str(d['vendor_name'])[:25]:<26} "
            f"{str(d['doc_number'] or '-')[:15]:<16} "
            f"{str(d['expiry_date']):<12} "
            f"{sisa:>8}"
        )
    print(f"{'='*72}\n")


def export_csv(docs, csv_path):
    if not docs:
        logger.info("Tidak ada data untuk di-export ke CSV")
        return
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Severity", "Tipe Dokumen", "Vendor Code", "Vendor Name",
            "No Dokumen", "Tanggal Expiry", "Sisa Hari"
        ])
        for d in docs:
            writer.writerow([
                d["severity"], d["doc_type"], d["vendor_code"], d["vendor_name"],
                d["doc_number"] or "", d["expiry_date"], d["days_left"]
            ])
    logger.info(f"CSV ter-export: {csv_path} ({len(docs)} baris)")


def log_alerts(session, docs):
    """Catat alert ke vendor_tax_log."""
    if not docs:
        return
    count = 0
    for d in docs:
        try:
            session.execute(
                text("""
                    INSERT INTO vendor_tax_log
                        (id, vendor_id, changed_by, changed_at,
                         field_changed, old_value, new_value, reason)
                    VALUES
                        (:id, :vid, 'expiry_alert_system', NOW(),
                         :field, NULL, :sev, :reason)
                """),
                {
                    "id":     str(uuid4()),
                    "vid":    str(d["id"]),
                    "field":  f"{d['doc_type']}_EXPIRY_ALERT",
                    "sev":    d["severity"],
                    "reason": f"{d['doc_type']} {d['doc_number'] or '-'} "
                              f"expiry {d['expiry_date']} (sisa {d['days_left']} hari)",
                }
            )
            count += 1
        except Exception as e:
            logger.warning(f"Log alert gagal untuk {d['vendor_name']}: {e}")
            session.rollback()
    session.commit()
    logger.info(f"{count} alert tercatat di vendor_tax_log")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Expiry Alert untuk dokumen pajak vendor")
    parser.add_argument("--days",      type=int, default=WARNING_DAYS,
                        help=f"Horizon hari ke depan (default: {WARNING_DAYS})")
    parser.add_argument("--csv",       dest="csv_path", default=None,
                        help="Export hasil ke file CSV")
    parser.add_argument("--log",       action="store_true",
                        help="Catat alert ke vendor_tax_log")
    parser.add_argument("--entity-id", dest="entity_id", default=None,
                        help="Filter satu entity saja")
    args = parser.parse_args()

    # ── Koneksi ──────────────────────────────────────────────────────────────
    try:
        engine  = create_engine(DATABASE_URL)
        Session = sessionmaker(bind=engine)
        session = Session()
        logger.info("Koneksi DB berhasil")
    except Exception as e:
        logger.error(f"Gagal koneksi DB: {e}")
        sys.exit(1)

    # ── Scan ─────────────────────────────────────────────────────────────────
    try:
        docs = get_expiring_documents(session, args.days, args.entity_id)
    except Exception as e:
        logger.error(f"Query gagal: {e}")
        sys.exit(1)

    # ── Output ───────────────────────────────────────────────────────────────
    print_report(docs, args.days)

    if args.csv_path:
        export_csv(docs, args.csv_path)

    if args.log:
        log_alerts(session, docs)

    session.close()

    # Exit code: 1 jika ada yang EXPIRED (berguna untuk scheduler/monitoring)
    has_expired = any(d["severity"] == "EXPIRED" for d in docs)
    sys.exit(1 if has_expired else 0)


if __name__ == "__main__":
    main()