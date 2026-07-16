# modules/auth.py
# JWT authentication: token creation, verification, password hashing, FastAPI dependencies.

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from sqlalchemy import text

from core.database import get_db
from config.settings import settings

# ── Config ────────────────────────────────────────────────────────────────────
ALGORITHM          = "HS256"
ACCESS_TOKEN_TTL   = timedelta(minutes=60)       # access token: 1 jam
REFRESH_TOKEN_TTL  = timedelta(days=7)           # refresh token: 7 hari

pwd_context    = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12,
    bcrypt__truncate_error=False,   # kompatibilitas bcrypt 4.x
)
oauth2_scheme  = OAuth2PasswordBearer(tokenUrl="/auth/login")

# Role hierarchy: makin tinggi index, makin luas aksesnya
ROLE_LEVEL = {"viewer": 1, "finance": 2, "admin": 3, "superadmin": 4}


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ── Token creation ────────────────────────────────────────────────────────────

def create_access_token(user_id: str, username: str, role: str,
                        entity_id: Optional[str] = None) -> str:
    payload = {
        "sub":       user_id,
        "username":  username,
        "role":      role,
        "entity_id": entity_id,
        "type":      "access",
        "exp":       datetime.now(timezone.utc) + ACCESS_TOKEN_TTL,
    }
    return jwt.encode(payload, settings.API_SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    payload = {
        "sub":  user_id,
        "type": "refresh",
        "exp":  datetime.now(timezone.utc) + REFRESH_TOKEN_TTL,
    }
    return jwt.encode(payload, settings.API_SECRET_KEY, algorithm=ALGORITHM)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_user_by_username(db: Session, username: str) -> Optional[dict]:
    row = db.execute(
        text("SELECT * FROM app_user WHERE username = :u AND is_active = TRUE"),
        {"u": username}
    ).fetchone()
    return dict(row._mapping) if row else None


def get_user_by_id(db: Session, user_id: str) -> Optional[dict]:
    row = db.execute(
        text("SELECT * FROM app_user WHERE id = :id AND is_active = TRUE"),
        {"id": user_id}
    ).fetchone()
    return dict(row._mapping) if row else None


def save_refresh_token(db: Session, user_id: str, token: str):
    from uuid import uuid4
    expires = datetime.now(timezone.utc) + REFRESH_TOKEN_TTL
    db.execute(
        text("""
            INSERT INTO refresh_token (id, user_id, token_hash, expires_at)
            VALUES (:id, :uid, :th, :exp)
        """),
        {"id": str(uuid4()), "uid": user_id,
         "th": _token_hash(token), "exp": expires}
    )
    db.commit()


def revoke_refresh_token(db: Session, token: str):
    db.execute(
        text("UPDATE refresh_token SET revoked = TRUE WHERE token_hash = :th"),
        {"th": _token_hash(token)}
    )
    db.commit()


def validate_refresh_token(db: Session, token: str) -> Optional[str]:
    """Validasi refresh token — return user_id jika valid, None jika tidak."""
    try:
        payload = jwt.decode(token, settings.API_SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            return None
        user_id = payload.get("sub")
    except JWTError:
        return None

    row = db.execute(
        text("""
            SELECT id FROM refresh_token
            WHERE token_hash = :th
              AND user_id    = :uid
              AND revoked    = FALSE
              AND expires_at > NOW()
        """),
        {"th": _token_hash(token), "uid": user_id}
    ).fetchone()
    return user_id if row else None


def update_last_login(db: Session, user_id: str):
    db.execute(
        text("UPDATE app_user SET last_login_at = NOW() WHERE id = :id"),
        {"id": user_id}
    )
    db.commit()


# ── FastAPI Dependencies ──────────────────────────────────────────────────────

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Dependency: decode JWT, return user dict. Raise 401 jika invalid."""
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token tidak valid atau sudah expired",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.API_SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            raise credentials_exc
        user_id: str = payload.get("sub")
        if not user_id:
            raise credentials_exc
    except JWTError:
        raise credentials_exc

    user = get_user_by_id(db, user_id)
    if not user:
        raise credentials_exc
    return user


def get_current_active_user(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Dependency: pastikan user aktif."""
    if not current_user.get("is_active"):
        raise HTTPException(status_code=403, detail="Akun dinonaktifkan")
    return current_user


def require_role(*roles: str):
    """
    Dependency factory untuk role-based access.
    Contoh: Depends(require_role('admin', 'superadmin'))
    """
    def _check(current_user: dict = Depends(get_current_active_user)) -> dict:
        user_role = current_user.get("role", "viewer")
        if user_role not in roles and user_role != "superadmin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Akses ditolak. Role yang dibutuhkan: {list(roles)}"
            )
        return current_user
    return _check


def require_min_role(min_role: str):
    """
    Dependency: user harus punya role minimal tertentu (berdasarkan level hierarki).
    Contoh: require_min_role('finance') — finance, admin, superadmin bisa akses.
    """
    min_level = ROLE_LEVEL.get(min_role, 1)

    def _check(current_user: dict = Depends(get_current_active_user)) -> dict:
        user_level = ROLE_LEVEL.get(current_user.get("role", "viewer"), 0)
        if user_level < min_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Butuh minimal role '{min_role}' untuk akses ini"
            )
        return current_user
    return _check


def check_entity_access(entity_id: str, current_user: dict):
    """
    Pastikan user berhak akses ke entity tertentu.
    superadmin bisa akses semua entity.
    User lain hanya bisa akses entity_id miliknya.
    """
    if current_user.get("role") == "superadmin":
        return  # superadmin bebas akses semua entity
    user_entity = str(current_user.get("entity_id") or "")
    if user_entity != str(entity_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Anda tidak memiliki akses ke entity ini"
        )
