# modules/auth_router.py
# Endpoints: login, register, refresh token, profile, user management

from uuid import uuid4
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger

from core.database import get_db
from modules.auth import (
    hash_password, verify_password,
    create_access_token, create_refresh_token,
    get_user_by_username, get_user_by_id,
    save_refresh_token, revoke_refresh_token, validate_refresh_token,
    update_last_login,
    get_current_active_user, require_role,
)

router = APIRouter(prefix="/auth", tags=["Auth"])


# ── Request / Response models ─────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username:   str
    email:      str
    full_name:  str
    password:   str
    role:       str = "viewer"         # viewer | finance | admin | superadmin
    entity_id:  Optional[str] = None   # wajib untuk non-superadmin


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class UpdateUserRequest(BaseModel):
    full_name:  Optional[str] = None
    email:      Optional[str] = None
    role:       Optional[str] = None
    entity_id:  Optional[str] = None
    is_active:  Optional[bool] = None


class RefreshRequest(BaseModel):
    refresh_token: str


# ── Helpers ───────────────────────────────────────────────────────────────────

VALID_ROLES = {"superadmin", "admin", "finance", "viewer"}

def _user_response(user: dict) -> dict:
    return {
        "id":           str(user["id"]),
        "username":     user["username"],
        "email":        user["email"],
        "full_name":    user["full_name"],
        "role":         user["role"],
        "entity_id":    str(user["entity_id"]) if user.get("entity_id") else None,
        "is_active":    user["is_active"],
        "last_login_at": str(user["last_login_at"]) if user.get("last_login_at") else None,
    }


# ── Login ─────────────────────────────────────────────────────────────────────

@router.post("/login")
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db:   Session = Depends(get_db),
):
    """
    Login dengan username + password.
    Return: access_token (Bearer) + refresh_token.
    """
    user = get_user_by_username(db, form.username)
    if not user or not verify_password(form.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username atau password salah",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="Akun dinonaktifkan")

    access_token   = create_access_token(
        user_id=str(user["id"]),
        username=user["username"],
        role=user["role"],
        entity_id=str(user["entity_id"]) if user.get("entity_id") else None,
    )
    refresh_token  = create_refresh_token(str(user["id"]))
    save_refresh_token(db, str(user["id"]), refresh_token)
    update_last_login(db, str(user["id"]))

    logger.info(f"Login: {user['username']} ({user['role']})")
    return {
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "token_type":    "bearer",
        "expires_in":    3600,
        "user":          _user_response(user),
    }


@router.post("/refresh")
def refresh_token(req: RefreshRequest, db: Session = Depends(get_db)):
    """
    Tukar refresh_token lama dengan access_token baru.
    Refresh token lama direvoke setelah dipakai (rotation).
    """
    user_id = validate_refresh_token(db, req.refresh_token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token tidak valid atau sudah expired"
        )

    user = get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User tidak ditemukan")

    # Revoke token lama (rotation — satu refresh token hanya bisa dipakai sekali)
    revoke_refresh_token(db, req.refresh_token)

    new_access   = create_access_token(
        user_id=str(user["id"]),
        username=user["username"],
        role=user["role"],
        entity_id=str(user["entity_id"]) if user.get("entity_id") else None,
    )
    new_refresh  = create_refresh_token(str(user["id"]))
    save_refresh_token(db, str(user["id"]), new_refresh)

    return {
        "access_token":  new_access,
        "refresh_token": new_refresh,
        "token_type":    "bearer",
        "expires_in":    3600,
    }


@router.post("/logout")
def logout(
    req:          RefreshRequest,
    current_user: dict = Depends(get_current_active_user),
    db:           Session = Depends(get_db),
):
    """Revoke refresh token — invalidasi sesi aktif."""
    revoke_refresh_token(db, req.refresh_token)
    logger.info(f"Logout: {current_user['username']}")
    return {"success": True, "message": "Logout berhasil"}


# ── Profile ───────────────────────────────────────────────────────────────────

@router.get("/me")
def get_my_profile(current_user: dict = Depends(get_current_active_user)):
    """Info user yang sedang login."""
    return _user_response(current_user)


@router.put("/me/password")
def change_password(
    req:          ChangePasswordRequest,
    current_user: dict = Depends(get_current_active_user),
    db:           Session = Depends(get_db),
):
    """Ganti password sendiri."""
    if not verify_password(req.old_password, current_user["hashed_password"]):
        raise HTTPException(400, "Password lama tidak cocok")
    if len(req.new_password) < 8:
        raise HTTPException(400, "Password baru minimal 8 karakter")

    db.execute(
        text("""
            UPDATE app_user
            SET hashed_password = :hp, updated_at = NOW()
            WHERE id = :id
        """),
        {"hp": hash_password(req.new_password), "id": str(current_user["id"])}
    )
    db.commit()
    return {"success": True, "message": "Password berhasil diubah"}


# ── User Management (admin/superadmin) ────────────────────────────────────────

@router.post("/register")
def register_user(
    req:          RegisterRequest,
    current_user: dict = Depends(require_role("admin", "superadmin")),
    db:           Session = Depends(get_db),
):
    """
    Daftarkan user baru. Hanya admin / superadmin yang bisa.
    - admin hanya bisa daftarkan user untuk entity-nya sendiri, max role 'finance'
    - superadmin bisa daftarkan siapa saja termasuk superadmin lain
    """
    if req.role not in VALID_ROLES:
        raise HTTPException(400, f"Role tidak valid. Pilih: {VALID_ROLES}")

    # admin tidak bisa buat superadmin atau admin lain di luar entity-nya
    if current_user["role"] == "admin":
        if req.role in ("superadmin", "admin"):
            raise HTTPException(403, "Admin hanya bisa daftarkan role finance/viewer")
        if req.entity_id and req.entity_id != str(current_user.get("entity_id")):
            raise HTTPException(403, "Admin hanya bisa daftarkan user untuk entity-nya sendiri")
        req.entity_id = str(current_user["entity_id"])

    # Cek username & email unik
    dup = db.execute(
        text("SELECT id FROM app_user WHERE username = :u OR email = :e"),
        {"u": req.username, "e": req.email}
    ).fetchone()
    if dup:
        raise HTTPException(409, "Username atau email sudah dipakai")

    if len(req.password) < 8:
        raise HTTPException(400, "Password minimal 8 karakter")

    uid = str(uuid4())
    db.execute(
        text("""
            INSERT INTO app_user
                (id, username, email, full_name, hashed_password, role, entity_id)
            VALUES
                (:id, :u, :e, :fn, :hp, :role, :eid)
        """),
        {
            "id":   uid,
            "u":    req.username,
            "e":    req.email,
            "fn":   req.full_name,
            "hp":   hash_password(req.password),
            "role": req.role,
            "eid":  req.entity_id,
        }
    )
    db.commit()

    logger.info(f"User baru: {req.username} ({req.role}) by {current_user['username']}")
    return {
        "success":   True,
        "user_id":   uid,
        "username":  req.username,
        "role":      req.role,
        "entity_id": req.entity_id,
    }


@router.get("/users")
def list_users(
    current_user: dict = Depends(require_role("admin", "superadmin")),
    db:           Session = Depends(get_db),
):
    """Daftar semua user. Admin hanya lihat user di entity-nya."""
    if current_user["role"] == "superadmin":
        rows = db.execute(text("""
            SELECT u.id, u.username, u.email, u.full_name, u.role,
                   u.entity_id, u.is_active, u.last_login_at,
                   e.name AS entity_name
            FROM app_user u
            LEFT JOIN entity e ON e.id = u.entity_id
            ORDER BY u.created_at DESC
        """)).fetchall()
    else:
        rows = db.execute(
            text("""
                SELECT u.id, u.username, u.email, u.full_name, u.role,
                       u.entity_id, u.is_active, u.last_login_at,
                       e.name AS entity_name
                FROM app_user u
                LEFT JOIN entity e ON e.id = u.entity_id
                WHERE u.entity_id = :eid
                ORDER BY u.created_at DESC
            """),
            {"eid": str(current_user["entity_id"])}
        ).fetchall()
    return [dict(r._mapping) for r in rows]


@router.put("/users/{user_id}")
def update_user(
    user_id:      str,
    req:          UpdateUserRequest,
    current_user: dict = Depends(require_role("admin", "superadmin")),
    db:           Session = Depends(get_db),
):
    """Update data user. Admin hanya bisa update user di entity-nya."""
    target = get_user_by_id(db, user_id)
    if not target:
        raise HTTPException(404, "User tidak ditemukan")

    if current_user["role"] == "admin":
        if str(target.get("entity_id")) != str(current_user.get("entity_id")):
            raise HTTPException(403, "Tidak bisa update user di luar entity Anda")
        if req.role in ("superadmin", "admin"):
            raise HTTPException(403, "Admin tidak bisa assign role admin/superadmin")

    fields = {}
    if req.full_name  is not None: fields["full_name"]  = req.full_name
    if req.email      is not None: fields["email"]      = req.email
    if req.role       is not None: fields["role"]       = req.role
    if req.entity_id  is not None: fields["entity_id"]  = req.entity_id
    if req.is_active  is not None: fields["is_active"]  = req.is_active

    if not fields:
        raise HTTPException(400, "Tidak ada field yang diupdate")

    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = user_id
    db.execute(
        text(f"UPDATE app_user SET {set_clause}, updated_at = NOW() WHERE id = :id"),
        fields
    )
    db.commit()
    return {"success": True, "updated_fields": list(fields.keys())}


@router.delete("/users/{user_id}")
def deactivate_user(
    user_id:      str,
    current_user: dict = Depends(require_role("superadmin")),
    db:           Session = Depends(get_db),
):
    """Nonaktifkan user (soft delete). Hanya superadmin."""
    if user_id == str(current_user["id"]):
        raise HTTPException(400, "Tidak bisa menonaktifkan akun sendiri")
    db.execute(
        text("UPDATE app_user SET is_active = FALSE, updated_at = NOW() WHERE id = :id"),
        {"id": user_id}
    )
    db.commit()
    return {"success": True, "message": "User dinonaktifkan"}
