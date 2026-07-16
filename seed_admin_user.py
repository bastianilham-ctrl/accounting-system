"""
seed_admin_user.py
==================
Buat superadmin pertama untuk sistem.
Jalankan sekali setelah schema_users.sql diapply.

  python seed_admin_user.py
"""

import os, sys

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from uuid import uuid4
from modules.auth import hash_password

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD','')}@"
    f"{os.getenv('DB_HOST','localhost')}:{os.getenv('DB_PORT','5432')}/"
    f"{os.getenv('DB_NAME','accounting_db')}"
)

USERS = [
    # (username, email, full_name, password, role, entity_code)
    ("superadmin", "admin@kba.co.id",   "Super Administrator",  "Admin@12345!", "superadmin", None),
    ("kba_admin",  "kba@kba.co.id",     "KBA Admin",            "Kba@12345!",  "admin",       "KBA"),
    ("kba_finance","finance@kba.co.id", "KBA Finance Staff",    "Finance@123!", "finance",    "KBA"),
]

def main():
    try:
        engine  = create_engine(DATABASE_URL)
        Session = sessionmaker(bind=engine)
        session = Session()
        session.execute(text("SELECT 1"))
    except Exception as e:
        print(f"[ERR] Koneksi DB gagal: {e}")
        sys.exit(1)

    print(f"\n{'='*55}")
    print("  SEED ADMIN USERS")
    print(f"{'='*55}")

    for username, email, full_name, password, role, entity_code in USERS:
        # Cek sudah ada
        existing = session.execute(
            text("SELECT id FROM app_user WHERE username = :u"),
            {"u": username}
        ).fetchone()
        if existing:
            print(f"  [SKIP] {username} sudah ada")
            continue

        # Resolve entity_id dari kode
        entity_id = None
        if entity_code:
            row = session.execute(
                text("SELECT id FROM entity WHERE code = :code"),
                {"code": entity_code}
            ).fetchone()
            entity_id = str(row.id) if row else None

        uid = str(uuid4())
        session.execute(
            text("""
                INSERT INTO app_user
                    (id, username, email, full_name, hashed_password, role, entity_id)
                VALUES
                    (:id, :u, :e, :fn, :hp, :role, :eid)
            """),
            {
                "id":   uid,
                "u":    username,
                "e":    email,
                "fn":   full_name,
                "hp":   hash_password(password),
                "role": role,
                "eid":  entity_id,
            }
        )
        session.commit()
        print(f"  [OK]  {username:<15} role: {role:<12} entity: {entity_code or 'ALL'}")

    session.close()

    print(f"\n{'='*55}")
    print("  CREDENTIALS (simpan & ganti password setelah login pertama!)")
    print(f"{'='*55}")
    for username, _, _, password, role, _ in USERS:
        print(f"  {username:<15} / {password}")
    print(f"{'='*55}\n")
    print("  Login via: POST /auth/login")
    print("  Body     : username=xxx&password=xxx (form-data)\n")


if __name__ == "__main__":
    main()
