"""
Multi-Entity Permission Router
Base prefix: /permissions

Manage siapa boleh akses entity mana dengan role apa.
Hanya admin (super-admin atau admin di entity tersebut) yang bisa mengelola permission.
"""

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text

from core.database import get_db
from modules.auth import get_current_active_user
from modules.permission_engine import PermissionEngine

router = APIRouter(prefix="/permissions", tags=["Multi-Entity Permissions"])


# ── Pydantic Models ──────────────────────────────────────────────────────────

class GrantReq(BaseModel):
    user_id:         UUID
    entity_id:       UUID
    role:            str                          # viewer | finance | approver | admin
    granted_by:      str                          = ""
    allowed_modules: Optional[list[str]]          = None    # null = semua modul
    valid_from:      Optional[date]               = None
    valid_until:     Optional[date]               = None    # null = tidak expire
    notes:           Optional[str]                = None


class RevokeReq(BaseModel):
    user_id:    UUID
    entity_id:  UUID
    revoked_by: str  = ""
    reason:     Optional[str] = None


class BulkGrantItem(BaseModel):
    entity_id:       UUID
    role:            str
    allowed_modules: Optional[list[str]] = None
    valid_until:     Optional[date]      = None
    notes:           Optional[str]       = None


class BulkGrantReq(BaseModel):
    user_id:    UUID
    grants:     list[BulkGrantItem] = Field(..., min_length=1, max_length=50)
    granted_by: str = ""


class UpdateRoleReq(BaseModel):
    user_id:    UUID
    entity_id:  UUID
    new_role:   str
    updated_by: str = ""
    notes:      Optional[str] = None


# ── Grant / Revoke ────────────────────────────────────────────────────────────

@router.post("/grant", summary="Berikan akses user ke entity dengan role tertentu")
def grant_access(req: GrantReq, db: Session = Depends(get_db)):
    """
    Jika user sudah punya permission di entity ini, role akan diupdate.
    Jika belum ada, buat baru.
    """
    try:
        current_user = None
        return PermissionEngine.grant(
            db,
            user_id         = req.user_id,
            entity_id       = req.entity_id,
            role            = req.role,
            granted_by      = req.granted_by,
            allowed_modules = req.allowed_modules,
            valid_from      = req.valid_from,
            valid_until     = req.valid_until,
            notes           = req.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/revoke", summary="Cabut akses user dari entity")
def revoke_access(req: RevokeReq, db: Session = Depends(get_db)):
    try:
        return PermissionEngine.revoke(
            db,
            user_id    = req.user_id,
            entity_id  = req.entity_id,
            revoked_by = req.revoked_by,
            reason     = req.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/update-role", summary="Update role user di entity tertentu")
def update_role(req: UpdateRoleReq, db: Session = Depends(get_db)):
    try:
        return PermissionEngine.grant(
            db,
            user_id    = req.user_id,
            entity_id  = req.entity_id,
            role       = req.new_role,
            granted_by = req.updated_by,
            notes      = req.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/bulk-grant", summary="Berikan akses ke banyak entity sekaligus")
def bulk_grant(req: BulkGrantReq, db: Session = Depends(get_db)):
    grants = [
        {
            "entity_id":       g.entity_id,
            "role":            g.role,
            "allowed_modules": g.allowed_modules,
            "valid_until":     g.valid_until,
            "notes":           g.notes,
        }
        for g in req.grants
    ]
    return PermissionEngine.bulk_grant(db, req.user_id, grants, req.granted_by)


# ── Query ─────────────────────────────────────────────────────────────────────

@router.get("/users/{user_id}/entities", summary="Semua entity yang bisa diakses user")
def user_entities(user_id: UUID, db: Session = Depends(get_db)):
    """List entity + role yang dimiliki user (hanya aktif + belum expired)."""
    return PermissionEngine.get_user_entities(db, user_id)


@router.get("/entities/{entity_id}/users", summary="Semua user yang punya akses ke entity")
def entity_users(
    entity_id:        UUID,
    include_inactive: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    return PermissionEngine.get_entity_users(db, entity_id, include_inactive)


@router.get("/check", summary="Cek apakah user punya akses ke entity")
def check_access(
    user_id:        UUID,
    entity_id:      UUID,
    min_role:       str  = Query(default="viewer"),
    db: Session = Depends(get_db),
):
    """
    Return {has_access: true/false, role: 'finance'|null, can_proceed: true/false}
    """
    role = PermissionEngine.get_role(db, user_id, entity_id)
    from modules.permission_engine import _has_min_role
    has_access = bool(role) and _has_min_role(role, min_role) if role else False

    return {
        "user_id":    str(user_id),
        "entity_id":  str(entity_id),
        "role":       role,
        "min_role":   min_role,
        "has_access": has_access,
    }


@router.get("/history", summary="Riwayat grant/revoke permission")
def permission_history(
    entity_id: Optional[UUID] = None,
    user_id:   Optional[UUID] = None,
    limit:     int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    if not entity_id and not user_id:
        raise HTTPException(status_code=400, detail="Sediakan entity_id atau user_id.")
    return PermissionEngine.get_permission_history(db, entity_id, user_id, limit)


# ── Me — My Access ────────────────────────────────────────────────────────────

@router.get("/me/entities", summary="Entity yang bisa saya akses (current user)")
def my_entities(
    current_user = Depends(get_current_active_user),
    db: Session  = Depends(get_db),
):
    """Shortcut: tidak perlu tahu user_id sendiri."""
    if current_user["role"] == "superadmin":
        # Super-admin: list semua entity
        rows = db.execute(
            text("SELECT id, name FROM entity WHERE is_active = TRUE ORDER BY name"),
        ).fetchall()
        return [{"entity_id": str(r.id), "entity_name": r.name, "role": "superadmin"} for r in rows]

    return PermissionEngine.get_user_entities(db, current_user["id"])


@router.get("/me/check", summary="Cek akses saya ke entity tertentu")
def my_check(
    entity_id: UUID,
    min_role:  str = Query(default="viewer"),
    current_user = Depends(get_current_active_user),
    db: Session  = Depends(get_db),
):
    is_super = getattr(current_user, "role", "") == "admin"
    if is_super:
        return {"role": "admin", "has_access": True, "is_super_admin": True}

    role = PermissionEngine.get_role(db, current_user.id, entity_id)
    from modules.permission_engine import _has_min_role
    has_access = bool(role) and _has_min_role(role, min_role) if role else False

    return {
        "role":          role,
        "has_access":    has_access,
        "is_super_admin": False,
    }


# ── Role Catalogue ─────────────────────────────────────────────────────────────

@router.get("/roles", summary="Daftar role + level hierarchy")
def list_roles():
    return {
        "roles": [
            {"role": "viewer",   "level": 1, "description": "Read-only: lihat data, tidak bisa ubah apapun"},
            {"role": "finance",  "level": 2, "description": "Finance: posting jurnal, buat invoice, bayar AP/AR"},
            {"role": "approver", "level": 3, "description": "Approver: approve workflow (expense, purchase, leave)"},
            {"role": "admin",    "level": 4, "description": "Admin: akses penuh termasuk konfigurasi & tutup buku"},
        ]
    }
