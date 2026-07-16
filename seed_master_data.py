"""
seed_master_data.py
===================
Seed database dengan:
  1. Entity demo untuk setiap jenis usaha (7 entity)
  2. COA standard sesuai template masing-masing

Cara pakai:
  python seed_master_data.py                    -- seed semua 7 jenis
  python seed_master_data.py --type jasa        -- hanya satu jenis
  python seed_master_data.py --list             -- lihat daftar template
"""

import os
import sys
import argparse
from uuid import uuid4

# Windows PowerShell default encoding tidak support emoji — paksa UTF-8
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

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

from modules.coa_templates import TEMPLATES, get_template, list_templates

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD','')}@"
    f"{os.getenv('DB_HOST','localhost')}:{os.getenv('DB_PORT','5432')}/"
    f"{os.getenv('DB_NAME','accounting_db')}"
)

# NPWP demo per jenis usaha (format valid, bukan data nyata)
DEMO_ENTITIES = {
    "jasa":       {"code": "JASA",  "name": "PT Demo Jasa Konsultan",       "npwp": "01.111.111.1-001.000"},
    "dagang":     {"code": "DAGANG","name": "PT Demo Perdagangan Umum",     "npwp": "01.222.222.2-001.000"},
    "konstruksi": {"code": "KONST", "name": "PT Demo Konstruksi Nusantara", "npwp": "01.333.333.3-001.000"},
    "manufaktur": {"code": "MANUF", "name": "PT Demo Industri Manufaktur",  "npwp": "01.444.444.4-001.000"},
    "rental":     {"code": "RENT",  "name": "PT Demo Rental Sarana",        "npwp": "01.555.555.5-001.000"},
    "properti":   {"code": "PROP",  "name": "PT Demo Properti Investama",   "npwp": "01.666.666.6-001.000"},
    "logistik":   {"code": "LOGIS", "name": "PT Demo Logistik Andalan",     "npwp": "01.777.777.7-001.000"},
}


def get_or_create_entity(session, biz_type: str) -> str:
    """Buat entity demo jika belum ada, return entity_id."""
    info = DEMO_ENTITIES[biz_type]

    row = session.execute(
        text("SELECT id FROM entity WHERE code = :code"),
        {"code": info["code"]}
    ).fetchone()

    if row:
        eid = str(row.id)
        logger.info(f"Entity sudah ada: {info['code']} ({eid})")
        return eid

    eid = str(uuid4())
    session.execute(
        text("""
            INSERT INTO entity (id, code, name, npwp, currency, country)
            VALUES (:id, :code, :name, :npwp, 'IDR', 'ID')
        """),
        {"id": eid, "code": info["code"], "name": info["name"], "npwp": info["npwp"]}
    )
    session.commit()
    logger.info(f"Entity dibuat: {info['name']} → {eid}")
    return eid


def seed_coa(session, entity_id: str, biz_type: str) -> dict:
    """Insert COA template untuk satu entity. Skip akun yang sudah ada."""
    accounts = get_template(biz_type)
    inserted = 0
    skipped  = 0

    for (code, name, acc_type, normal_bal, is_header) in accounts:
        existing = session.execute(
            text("""
                SELECT id FROM chart_of_accounts
                WHERE entity_id = :eid AND account_code = :code
            """),
            {"eid": entity_id, "code": code}
        ).fetchone()

        if existing:
            skipped += 1
            continue

        session.execute(
            text("""
                INSERT INTO chart_of_accounts
                    (id, entity_id, account_code, account_name,
                     account_type, normal_balance, is_header, is_active, level)
                VALUES
                    (:id, :eid, :code, :name,
                     :atype, :nbal, :header, TRUE, :level)
            """),
            {
                "id":     str(uuid4()),
                "eid":    entity_id,
                "code":   code,
                "name":   name,
                "atype":  acc_type,
                "nbal":   normal_bal,
                "header": is_header,
                "level":  len(code.split("-")),
            }
        )
        inserted += 1

    session.commit()
    return {"inserted": inserted, "skipped": skipped, "total": len(accounts)}


def seed_one(session, biz_type: str) -> dict:
    """Seed entity + COA untuk satu jenis usaha."""
    entity_id = get_or_create_entity(session, biz_type)
    result    = seed_coa(session, entity_id, biz_type)
    tpl_name  = TEMPLATES[biz_type]["name"]

    print(f"  [OK] {tpl_name:<40} entity: {entity_id}")
    print(f"       COA: {result['inserted']} akun baru, {result['skipped']} sudah ada")
    return {"entity_id": entity_id, **result}


def main():
    parser = argparse.ArgumentParser(description="Seed master data COA")
    parser.add_argument("--type",  default=None, help="Jenis usaha: jasa/dagang/konstruksi/...")
    parser.add_argument("--list",  action="store_true", help="Tampilkan daftar template")
    args = parser.parse_args()

    if args.list:
        print("\nTemplate COA tersedia:")
        for k, v in list_templates().items():
            print(f"  {k:<12} — {v['name']:<40} ({v['account_count']} akun)")
        print()
        return

    try:
        engine  = create_engine(DATABASE_URL)
        Session = sessionmaker(bind=engine)
        session = Session()
        session.execute(text("SELECT 1"))  # test koneksi
        logger.info("Koneksi DB OK")
    except Exception as e:
        print(f"\n[ERR] Gagal koneksi database: {e}")
        print("    Pastikan DATABASE_URL di .env sudah benar.\n")
        sys.exit(1)

    types_to_seed = [args.type] if args.type else list(TEMPLATES.keys())

    # Validasi
    for t in types_to_seed:
        if t not in TEMPLATES:
            print(f"\n[ERR] Jenis usaha '{t}' tidak dikenal.")
            print(f"    Pilih: {list(TEMPLATES.keys())}\n")
            sys.exit(1)

    print(f"\n{'='*65}")
    print(f"  SEED MASTER DATA — {len(types_to_seed)} jenis usaha")
    print(f"{'='*65}")

    results = {}
    for biz_type in types_to_seed:
        try:
            results[biz_type] = seed_one(session, biz_type)
        except Exception as e:
            logger.error(f"Seed gagal untuk {biz_type}: {e}")
            session.rollback()
            print(f"  [ERR] {biz_type}: {e}")

    session.close()

    print(f"\n{'='*65}")
    print(f"  SELESAI — {len(results)}/{len(types_to_seed)} berhasil")
    print(f"{'='*65}")
    print()
    print("  Entity IDs (simpan untuk digunakan di API):")
    for biz, r in results.items():
        print(f"  {DEMO_ENTITIES[biz]['code']:<8} {r['entity_id']}")
    print()


if __name__ == "__main__":
    main()
