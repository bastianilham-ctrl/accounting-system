"""
batch_pph_recalculation.py  (v2 — tanpa migration, pakai kolom existing)
=========================================================================
Menyesuaikan dengan kolom vendor yang ada sekarang:
  id, entity_id, vendor_code, vendor_name, npwp,
  tax_status, default_pph_type, default_pph_rate,
  default_ppn_eligible, created_at, updated_at

Logika recalculation (tanpa SKB/UMKM override karena kolom belum ada):
  - Ambil default_pph_type + default_pph_rate dari tabel vendor
  - Hitung ulang pph_amount = subtotal * (default_pph_rate / 100)
  - Bandingkan dengan pph_amount yang tersimpan di ap_invoice
  - Jika berbeda → update + catat di tax_advice_log

Cara jalankan:
  python batch_pph_recalculation.py                      ← dry-run
  python batch_pph_recalculation.py --apply              ← apply ke DB
  python batch_pph_recalculation.py --apply --from 2026-01-01
  python batch_pph_recalculation.py --apply --entity-id <uuid>
"""

import os
import sys
import argparse
from decimal import Decimal
from datetime import date, datetime
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

RECALC_STATUSES = ("draft", "approved", "partial")

# ─────────────────────────────────────────────────────────────────────────────
# FETCH
# ─────────────────────────────────────────────────────────────────────────────

def get_invoices_to_recalc(session, from_date=None, entity_id=None):
    """
    Ambil ap_invoice JOIN vendor.
    Hanya kolom existing — tanpa tax_reviewed_at / has_skb / vendor_category.
    """
    filters = ["ai.status IN :statuses"]
    params  = {"statuses": tuple(RECALC_STATUSES)}

    if from_date:
        filters.append("ai.invoice_date >= :from_date")
        params["from_date"] = from_date

    if entity_id:
        filters.append("ai.entity_id = :entity_id")
        params["entity_id"] = entity_id

    where_clause = " AND ".join(filters)

    rows = session.execute(
        text(f"""
            SELECT
                ai.id,
                ai.entity_id,
                ai.vendor_id,
                ai.invoice_no,
                ai.invoice_date,
                ai.subtotal,
                ai.pph_type        AS old_pph_type,
                ai.pph_rate        AS old_pph_rate,
                ai.pph_amount      AS old_pph_amount,
                ai.total_amount,
                v.vendor_name,
                v.vendor_code,
                v.default_pph_type AS vendor_pph_type,
                v.default_pph_rate AS vendor_pph_rate,
                v.tax_status
            FROM ap_invoice ai
            JOIN vendor v ON v.id = ai.vendor_id
            WHERE {where_clause}
            ORDER BY ai.invoice_date DESC
        """),
        params
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# RECALCULATE
# ─────────────────────────────────────────────────────────────────────────────

def recalculate_invoice(invoice):
    """
    Hitung ulang PPh berdasarkan default_pph_type & default_pph_rate vendor.
    Tidak butuh APClassifier — langsung dari kolom vendor existing.
    """
    subtotal = Decimal(str(invoice["subtotal"] or 0))

    # Rate dari vendor (fallback 2% jika NULL)
    vendor_rate = invoice.get("vendor_pph_rate")
    new_rate    = Decimal(str(vendor_rate if vendor_rate is not None else 2.0))
    new_type    = invoice.get("vendor_pph_type") or "PPh23"

    # Rate lama dari invoice
    old_rate   = Decimal(str(invoice["old_pph_rate"] or 0))
    old_amount = Decimal(str(invoice["old_pph_amount"] or 0))

    # Hitung PPh baru
    new_amount = subtotal * (new_rate / 100)

    rate_changed   = abs(new_rate - old_rate) > Decimal("0.001")
    amount_changed = abs(new_amount - old_amount) > Decimal("1")

    # Flag needs_review: invoice yang pph_amount = 0 padahal rate > 0
    needs_review = (new_rate > 0 and old_amount == 0 and subtotal > 0)

    return {
        "invoice_id":      invoice["id"],
        "invoice_no":      invoice["invoice_no"],
        "invoice_date":    invoice["invoice_date"],
        "vendor_id":       invoice["vendor_id"],
        "vendor_name":     invoice["vendor_name"],
        "vendor_code":     invoice["vendor_code"],
        "subtotal":        subtotal,
        # Lama
        "old_pph_type":    invoice["old_pph_type"],
        "old_pph_rate":    float(old_rate),
        "old_pph_amount":  float(old_amount),
        # Baru
        "new_pph_type":    new_type,
        "new_pph_rate":    float(new_rate),
        "new_pph_amount":  float(new_amount),
        # Flag
        "has_change":      rate_changed or amount_changed,
        "needs_review":    needs_review,
        "delta_pph":       float(new_amount - old_amount),
        "note": (
            f"Recalc: {new_type} {new_rate}% × Rp {subtotal:,.0f} = Rp {new_amount:,.0f}"
            + (" [PERLU REVIEW: pph_amount sebelumnya 0]" if needs_review else "")
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# APPLY
# ─────────────────────────────────────────────────────────────────────────────

def apply_recalculation(session, change):
    """Update ap_invoice dengan nilai PPh baru + recalculate total_amount."""
    old_total  = Decimal(str(change["subtotal"])) + Decimal(str(change["old_pph_amount"]))
    new_total  = Decimal(str(change["subtotal"])) + Decimal(str(change["new_pph_amount"]))

    session.execute(
        text("""
            UPDATE ap_invoice SET
                pph_type     = :pph_type,
                pph_rate     = :pph_rate,
                pph_amount   = :pph_amount,
                total_amount = :total_amount,
                updated_at   = NOW()
            WHERE id = :id
        """),
        {
            "pph_type":    change["new_pph_type"],
            "pph_rate":    change["new_pph_rate"],
            "pph_amount":  change["new_pph_amount"],
            "total_amount": float(new_total),
            "id":          change["invoice_id"],
        }
    )


def log_recalculation(session, change, run_id):
    """Catat ke tax_advice_log untuk audit trail."""
    # Cek apakah tabel tax_advice_log ada
    try:
        session.execute(
            text("""
                INSERT INTO tax_advice_log
                    (id, entity_id, vendor_id, ref_type, ref_id,
                     advice_type, old_value, new_value, reason, created_at)
                VALUES
                    (:id, NULL, :vendor_id, 'ap_invoice', :ref_id,
                     'BATCH_PPH_RECALC',
                     :old_value, :new_value, :reason, NOW())
            """),
            {
                "id":        str(uuid4()),
                "vendor_id": str(change["vendor_id"]),
                "ref_id":    str(change["invoice_id"]),
                "old_value": f"{change['old_pph_type']} {change['old_pph_rate']}% = Rp {change['old_pph_amount']:,.0f}",
                "new_value": f"{change['new_pph_type']} {change['new_pph_rate']}% = Rp {change['new_pph_amount']:,.0f}",
                "reason":    f"[run:{run_id}] {change['note']}",
            }
        )
    except Exception as e:
        logger.warning(f"tax_advice_log insert gagal (skip): {e}")
        session.rollback()


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

def print_summary(results, dry_run):
    changed      = [r for r in results if r["has_change"]]
    needs_review = [r for r in results if r["needs_review"]]
    no_change    = [r for r in results if not r["has_change"]]
    total_delta  = sum(r["delta_pph"] for r in changed)

    mode = "⚠️  DRY RUN — tidak ada perubahan ke DB" if dry_run else "✅ APPLIED ke database"

    print(f"\n{'='*65}")
    print(f"  BATCH PPH RECALCULATION — {mode}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*65}")
    print(f"  Total invoice diproses : {len(results):>6,}")
    print(f"  Ada perubahan PPh      : {len(changed):>6,}")
    print(f"  Tidak ada perubahan    : {len(no_change):>6,}")
    print(f"  Perlu tax review       : {len(needs_review):>6,}")
    print(f"  Net delta PPh total    : Rp {total_delta:>15,.0f}")
    print(f"{'='*65}")

    if changed:
        print(f"\n  {'INVOICE NO':<20} {'VENDOR':<22} {'LAMA':>11} {'BARU':>11} {'DELTA':>12}")
        print(f"  {'-'*80}")
        for r in changed[:25]:
            print(
                f"  {str(r['invoice_no']):<20} "
                f"{str(r['vendor_name'])[:21]:<22} "
                f"Rp {r['old_pph_amount']:>8,.0f} "
                f"Rp {r['new_pph_amount']:>8,.0f} "
                f"Rp {r['delta_pph']:>9,.0f}"
            )
        if len(changed) > 25:
            print(f"  ... dan {len(changed)-25} invoice lainnya")

    if needs_review:
        print(f"\n  ⚠️  Invoice PPh = 0 padahal seharusnya kena PPh ({len(needs_review)}):")
        for r in needs_review[:10]:
            print(f"     [{r['invoice_date']}] {r['invoice_no']} | {r['vendor_name']} "
                  f"| expected Rp {r['new_pph_amount']:,.0f}")
        if len(needs_review) > 10:
            print(f"     ... dan {len(needs_review)-10} lainnya")

    if dry_run:
        print(f"\n  💡 Tambahkan --apply untuk terapkan perubahan ke DB")
    print(f"{'='*65}\n")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Batch recalculate PPh untuk ap_invoice existing"
    )
    parser.add_argument("--apply",      action="store_true",
                        help="Apply perubahan ke DB (default: dry-run)")
    parser.add_argument("--from",       dest="from_date", default=None,
                        help="Dari tanggal invoice, contoh: 2026-01-01")
    parser.add_argument("--entity-id",  dest="entity_id", default=None,
                        help="Filter satu entity saja")
    parser.add_argument("--batch-size", dest="batch_size", type=int, default=100,
                        help="Jumlah invoice per batch commit (default: 100)")
    args = parser.parse_args()

    dry_run   = not args.apply
    from_date = date.fromisoformat(args.from_date) if args.from_date else None
    run_id    = str(uuid4())[:8]

    # ── Koneksi ──────────────────────────────────────────────────────────────
    try:
        engine  = create_engine(DATABASE_URL)
        Session = sessionmaker(bind=engine)
        session = Session()
        logger.info(f"Koneksi DB berhasil — run_id: {run_id}")
    except Exception as e:
        logger.error(f"Gagal koneksi DB: {e}")
        sys.exit(1)

    # ── Ambil invoice ─────────────────────────────────────────────────────────
    logger.info("Mengambil daftar invoice...")
    try:
        invoices = get_invoices_to_recalc(session, from_date, args.entity_id)
    except Exception as e:
        logger.error(f"Query gagal: {e}")
        sys.exit(1)

    logger.info(f"Ditemukan {len(invoices)} invoice untuk diproses")

    if not invoices:
        print("\n✅ Tidak ada invoice yang perlu di-recalculate.\n")
        return

    # ── Proses ───────────────────────────────────────────────────────────────
    results     = []
    batch_count = 0

    for i, invoice in enumerate(invoices, 1):
        try:
            change = recalculate_invoice(invoice)
            results.append(change)

            if not dry_run and change["has_change"]:
                apply_recalculation(session, change)
                log_recalculation(session, change, run_id)

            batch_count += 1
            if not dry_run and batch_count % args.batch_size == 0:
                session.commit()
                logger.info(f"Committed {batch_count} records ({i}/{len(invoices)})")

        except Exception as e:
            logger.error(f"Error invoice {invoice.get('invoice_no')}: {e}")
            if not dry_run:
                session.rollback()

    # ── Final commit ──────────────────────────────────────────────────────────
    if not dry_run:
        try:
            session.commit()
            logger.info(f"Selesai — {len([r for r in results if r['has_change']])} invoice diupdate")
        except Exception as e:
            logger.error(f"Final commit gagal: {e}")
            session.rollback()
    else:
        session.rollback()

    session.close()
    print_summary(results, dry_run)


if __name__ == "__main__":
    main()