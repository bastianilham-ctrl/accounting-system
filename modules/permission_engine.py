"""
Multi-Entity User Permission Engine
Satu user bisa punya role berbeda di entity berbeda.

Role hierarchy (ascending): viewer < finance < approver < admin

Aturan:
  - Super-admin (users.role = 'admin') melewati semua cek entity → akses semua.
  - User biasa harus punya row aktif di user_entity_permission untuk entity tertentu.
  - Role expired (valid_until < today) dianggap tidak ada akses.
  - allowed_modules: jika null → semua modul; jika ada → hanya modul dalam list.

FastAPI dependency:
    from modules.permission_engine import require_entity_access

    @router.get(...)
    def my_endpoint(
        entity_id: UUID,
        db: Session  = Depends(get_db),
        _perm = Depends(require_entity_access("finance")),
    ):
        ...

    Atau dengan entity_id dari query param:
        _perm = Depends(require_entity_access("finance", entity_id_param="entity_id"))
"""

from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from modules.auth import get_current_active_user
from modules.audit_engine import AuditEngine


# ── Role Hierarchy ─────────────────────────────────────────────────────────────
_ROLE_LEVEL: dict[str, int] = {
    "viewer":   1,
    "finance":  2,
    "approver": 3,
    "admin":    4,
}


def _has_min_role(actual_role: str, required_role: str) -> bool:
    return _ROLE_LEVEL.get(actual_role, 0) >= _ROLE_LEVEL.get(required_role, 0)


# ── Core Engine ────────────────────────────────────────────────────────────────
class PermissionEngine:

    # ── Grant Access ──────────────────────────────────────────────────────────

    @staticmethod
    def grant(
        db: Session,
        user_id: UUID,
        entity_id: UUID,
        role: str,
        granted_by: str,
        allowed_modules: Optional[list[str]] = None,
        valid_from: Optional[date] = None,
        valid_until: Optional[date] = None,
        notes: Optional[str] = None,
        audit_user_id: Optional[UUID] = None,
    ) -> dict:
        if role not in _ROLE_LEVEL:
            raise ValueError(f"Role tidak valid: {role}. Pilihan: {list(_ROLE_LEVEL)}")

        # Validate entity exists
        ent = db.execute(
            text("SELECT id, entity_name FROM entity WHERE id=:eid"),
            {"eid": str(entity_id)},
        ).fetchone()
        if not ent:
            raise ValueError("Entity tidak ditemukan.")

        import json
        existing = db.execute(
            text("SELECT id, role, is_active FROM user_entity_permission WHERE user_id=:uid AND entity_id=:eid"),
            {"uid": str(user_id), "eid": str(entity_id)},
        ).fetchone()

        old_role = existing.role if existing else None

        if existing:
            db.execute(
                text("""
                    UPDATE user_entity_permission
                    SET role=:role, is_active=TRUE, allowed_modules=:mods::jsonb,
                        valid_from=:vf, valid_until=:vu, granted_by=:by,
                        granted_at=NOW(), revoked_by=NULL, revoked_at=NULL, notes=:notes
                    WHERE user_id=:uid AND entity_id=:eid
                """),
                {
                    "uid":   str(user_id), "eid": str(entity_id),
                    "role":  role,
                    "mods":  json.dumps(allowed_modules) if allowed_modules else None,
                    "vf":    str(valid_from)  if valid_from  else None,
                    "vu":    str(valid_until) if valid_until else None,
                    "by":    granted_by, "notes": notes,
                },
            )
            action = "role_changed" if old_role != role else "reactivated"
        else:
            db.execute(
                text("""
                    INSERT INTO user_entity_permission
                        (user_id, entity_id, role, allowed_modules, valid_from,
                         valid_until, granted_by, notes)
                    VALUES (:uid, :eid, :role, :mods::jsonb, :vf, :vu, :by, :notes)
                """),
                {
                    "uid":   str(user_id), "eid": str(entity_id),
                    "role":  role,
                    "mods":  json.dumps(allowed_modules) if allowed_modules else None,
                    "vf":    str(valid_from)  if valid_from  else None,
                    "vu":    str(valid_until) if valid_until else None,
                    "by":    granted_by, "notes": notes,
                },
            )
            action = "granted"

        # Log to permission_history
        db.execute(
            text("""
                INSERT INTO permission_history
                    (user_id, entity_id, action, old_role, new_role, performed_by, reason)
                VALUES (:uid, :eid, :action, :old, :new, :by, :reason)
            """),
            {
                "uid": str(user_id), "eid": str(entity_id),
                "action": action, "old": old_role, "new": role,
                "by": granted_by, "reason": notes,
            },
        )

        # Audit log
        AuditEngine.log(
            db,
            entity_id   = entity_id,
            user_id     = audit_user_id,
            username    = granted_by,
            action      = "GRANT_ACCESS",
            module      = "permission",
            ref_type    = "other",
            description = f"Akses {action}: user {user_id} → entity '{ent.entity_name}' role '{role}'",
            after_data  = {"user_id": str(user_id), "entity_id": str(entity_id), "role": role},
            before_data = {"role": old_role} if old_role else None,
        )

        db.commit()
        return {
            "status":      action,
            "user_id":     str(user_id),
            "entity_id":   str(entity_id),
            "entity_name": ent.entity_name,
            "role":        role,
        }

    # ── Revoke Access ─────────────────────────────────────────────────────────

    @staticmethod
    def revoke(
        db: Session,
        user_id: UUID,
        entity_id: UUID,
        revoked_by: str,
        reason: Optional[str] = None,
        audit_user_id: Optional[UUID] = None,
    ) -> dict:
        perm = db.execute(
            text("SELECT id, role FROM user_entity_permission WHERE user_id=:uid AND entity_id=:eid AND is_active=TRUE"),
            {"uid": str(user_id), "eid": str(entity_id)},
        ).fetchone()
        if not perm:
            raise ValueError("Permission tidak ditemukan atau sudah tidak aktif.")

        ent = db.execute(
            text("SELECT entity_name FROM entity WHERE id=:eid"), {"eid": str(entity_id)}
        ).fetchone()

        db.execute(
            text("""
                UPDATE user_entity_permission
                SET is_active=FALSE, revoked_by=:by, revoked_at=NOW()
                WHERE user_id=:uid AND entity_id=:eid
            """),
            {"uid": str(user_id), "eid": str(entity_id), "by": revoked_by},
        )

        db.execute(
            text("""
                INSERT INTO permission_history
                    (user_id, entity_id, action, old_role, performed_by, reason)
                VALUES (:uid, :eid, 'revoked', :old, :by, :reason)
            """),
            {
                "uid": str(user_id), "eid": str(entity_id),
                "old": perm.role, "by": revoked_by, "reason": reason,
            },
        )

        AuditEngine.log(
            db,
            entity_id   = entity_id,
            user_id     = audit_user_id,
            username    = revoked_by,
            action      = "REVOKE_ACCESS",
            module      = "permission",
            description = f"Akses dicabut: user {user_id} dari entity '{ent.entity_name}'",
            before_data = {"user_id": str(user_id), "role": perm.role},
        )

        db.commit()
        return {"status": "revoked", "user_id": str(user_id), "entity_id": str(entity_id)}

    # ── Check Access ──────────────────────────────────────────────────────────

    @staticmethod
    def get_role(
        db: Session,
        user_id: UUID,
        entity_id: UUID,
    ) -> Optional[str]:
        """
        Return user's effective role for this entity, or None if no access.
        Does NOT handle super-admin bypass — that's done by check_access().
        """
        perm = db.execute(
            text("""
                SELECT role, valid_until, allowed_modules
                FROM user_entity_permission
                WHERE user_id=:uid AND entity_id=:eid AND is_active=TRUE
            """),
            {"uid": str(user_id), "eid": str(entity_id)},
        ).fetchone()
        if not perm:
            return None
        if perm.valid_until and perm.valid_until < date.today():
            return None  # expired
        return perm.role

    @staticmethod
    def check_access(
        db: Session,
        user_id: UUID,
        entity_id: UUID,
        min_role: str = "viewer",
        is_super_admin: bool = False,
    ) -> bool:
        """
        Return True jika user boleh mengakses entity dengan minimal min_role.
        Super-admin bypass semua cek.
        """
        if is_super_admin:
            return True
        role = PermissionEngine.get_role(db, user_id, entity_id)
        if not role:
            return False
        return _has_min_role(role, min_role)

    @staticmethod
    def check_module_access(
        db: Session,
        user_id: UUID,
        entity_id: UUID,
        module: str,
        is_super_admin: bool = False,
    ) -> bool:
        """
        Cek apakah user boleh mengakses modul tertentu di entity ini.
        Jika allowed_modules = null → semua modul diizinkan.
        """
        if is_super_admin:
            return True

        perm = db.execute(
            text("""
                SELECT role, valid_until, allowed_modules
                FROM user_entity_permission
                WHERE user_id=:uid AND entity_id=:eid AND is_active=TRUE
            """),
            {"uid": str(user_id), "eid": str(entity_id)},
        ).fetchone()
        if not perm:
            return False
        if perm.valid_until and perm.valid_until < date.today():
            return False
        if perm.allowed_modules is None:
            return True  # no restriction
        return module in (perm.allowed_modules or [])

    # ── List / Query ──────────────────────────────────────────────────────────

    @staticmethod
    def get_user_entities(db: Session, user_id: UUID) -> list[dict]:
        """List semua entity yang bisa diakses user (active + non-expired)."""
        rows = db.execute(
            text("""
                SELECT entity_id, entity_name, role, allowed_modules,
                       valid_from, valid_until, is_valid, granted_at
                FROM vw_user_access_matrix
                WHERE user_id=:uid AND is_valid=TRUE
                ORDER BY entity_name
            """),
            {"uid": str(user_id)},
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def get_entity_users(
        db: Session,
        entity_id: UUID,
        include_inactive: bool = False,
    ) -> list[dict]:
        """List semua user yang punya akses ke entity, beserta rolenya."""
        filters = ["uep.entity_id=:eid"]
        if not include_inactive:
            filters.append("uep.is_active=TRUE")
        params = {"eid": str(entity_id)}

        rows = db.execute(
            text(f"""
                SELECT
                    uep.user_id,
                    uep.role,
                    uep.is_active,
                    uep.allowed_modules,
                    uep.valid_from,
                    uep.valid_until,
                    uep.granted_by,
                    uep.granted_at,
                    uep.revoked_by,
                    uep.revoked_at,
                    uep.notes,
                    CASE WHEN uep.valid_until IS NOT NULL AND uep.valid_until < CURRENT_DATE
                         THEN TRUE ELSE FALSE END AS is_expired
                FROM user_entity_permission uep
                WHERE {' AND '.join(filters)}
                ORDER BY uep.role DESC, uep.granted_at
            """),
            params,
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def get_permission_history(
        db: Session,
        entity_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
        limit: int = 100,
    ) -> list[dict]:
        filters = []
        params: dict = {"lim": limit}
        if entity_id:
            filters.append("entity_id=:eid"); params["eid"] = str(entity_id)
        if user_id:
            filters.append("user_id=:uid"); params["uid"] = str(user_id)

        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        rows = db.execute(
            text(f"""
                SELECT id, user_id, entity_id, action, old_role, new_role,
                       performed_by, reason, created_at
                FROM permission_history
                {where}
                ORDER BY created_at DESC LIMIT :lim
            """),
            params,
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    # ── Bulk Grant (onboarding user baru ke banyak entity) ────────────────────

    @staticmethod
    def bulk_grant(
        db: Session,
        user_id: UUID,
        grants: list[dict],   # [{"entity_id": UUID, "role": str, "allowed_modules": list}]
        granted_by: str,
        audit_user_id: Optional[UUID] = None,
    ) -> dict:
        results = []
        errors  = []
        for g in grants:
            try:
                r = PermissionEngine.grant(
                    db,
                    user_id         = user_id,
                    entity_id       = UUID(str(g["entity_id"])),
                    role            = g["role"],
                    granted_by      = granted_by,
                    allowed_modules = g.get("allowed_modules"),
                    valid_until     = g.get("valid_until"),
                    notes           = g.get("notes"),
                    audit_user_id   = audit_user_id,
                )
                results.append(r)
            except Exception as e:
                errors.append({"entity_id": str(g.get("entity_id")), "error": str(e)})

        return {
            "granted": results,
            "errors":  errors,
            "total":   len(grants),
            "success": len(results),
            "failed":  len(errors),
        }


# ── FastAPI Dependencies ───────────────────────────────────────────────────────

def require_entity_access(min_role: str = "viewer", entity_id_param: str = "entity_id"):
    """
    FastAPI dependency factory. Cek apakah current user boleh akses entity
    dengan minimal role tertentu.

    Usage:
        @router.get("/")
        def my_endpoint(
            entity_id: UUID,
            db = Depends(get_db),
            _  = Depends(require_entity_access("finance")),
        ):
            ...
    """
    def _dependency(
        entity_id: UUID = Query(..., alias=entity_id_param),
        db: Session = Depends(get_db),
        current_user = Depends(get_current_active_user),
    ):
        # Super-admin bypass
        is_super = getattr(current_user, "role", "") == "admin"
        if is_super:
            return {"role": "admin", "entity_id": str(entity_id)}

        role = PermissionEngine.get_role(db, current_user.id, entity_id)
        if not role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Anda tidak memiliki akses ke entity ini.",
            )
        if not _has_min_role(role, min_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' tidak cukup. Minimal dibutuhkan: '{min_role}'.",
            )
        return {"role": role, "entity_id": str(entity_id)}

    return _dependency


def get_entity_role(entity_id_param: str = "entity_id"):
    """
    Dependency yang mengembalikan role user di entity ini (tidak raise jika tidak ada akses).
    Useful untuk endpoint yang menampilkan UI berbeda per role.
    """
    def _dependency(
        entity_id: UUID = Query(..., alias=entity_id_param),
        db: Session = Depends(get_db),
        current_user = Depends(get_current_active_user),
    ) -> Optional[str]:
        if getattr(current_user, "role", "") == "admin":
            return "admin"
        return PermissionEngine.get_role(db, current_user.id, entity_id)

    return _dependency
