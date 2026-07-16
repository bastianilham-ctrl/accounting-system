"""
Audit Trail Engine
Setiap transaksi bisnis dicatat: siapa, kapan, aksi, record, before/after diff.

Cara pakai dari engine lain:
    AuditEngine.log(
        db,
        entity_id   = entity_id,
        user_id     = current_user.id,
        username    = current_user.username,
        action      = "APPROVE",
        module      = "expense_claim",
        ref_type    = "expense_claim",
        ref_id      = claim_id,
        ref_number  = claim.claim_number,
        description = f"Expense claim {claim.claim_number} diapprove oleh {approver}",
        before_data = before_dict,
        after_data  = after_dict,
    )

Severity auto-inferred jika tidak disediakan:
    CREATE/UPDATE/LOGIN → info
    DELETE/VOID/CANCEL/REJECT → warning
    CLOSE/LOCK/GRANT_ACCESS/REVOKE_ACCESS/REVERSE → critical
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session


# ── Auto-severity rules ───────────────────────────────────────────────────────
_CRITICAL_ACTIONS = {
    "CLOSE", "LOCK", "REVERSE", "GRANT_ACCESS", "REVOKE_ACCESS",
    "ROLE_CHANGE", "PASSWORD_CHANGE",
}
_WARNING_ACTIONS = {
    "DELETE", "VOID", "CANCEL", "REJECT", "RESTORE", "REOPEN",
}


def _infer_severity(action: str) -> str:
    if action in _CRITICAL_ACTIONS:
        return "critical"
    if action in _WARNING_ACTIONS:
        return "warning"
    return "info"


# ── Diff computation ──────────────────────────────────────────────────────────
def compute_diff(before: Optional[dict], after: Optional[dict]) -> Optional[dict]:
    """Kembalikan hanya field yang berubah: {field: {before: x, after: y}}."""
    if not before and not after:
        return None
    if not before:
        return {"_new_record": {"before": None, "after": after}}
    if not after:
        return {"_deleted_record": {"before": before, "after": None}}

    changed = {}
    all_keys = set(list(before.keys()) + list(after.keys()))
    for k in all_keys:
        b = before.get(k)
        a = after.get(k)
        if b != a:
            # Skip internal/timestamp fields from diff noise
            if k in ("updated_at", "last_modified_at"):
                continue
            changed[k] = {"before": b, "after": a}
    return changed or None


def _safe_json(obj: Any) -> Optional[dict]:
    """Convert arbitrary object to JSON-serializable dict."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {k: _safe_val(v) for k, v in obj.items()}
    return {"_value": _safe_val(obj)}


def _safe_val(v: Any) -> Any:
    if isinstance(v, (UUID,)):
        return str(v)
    if isinstance(v, (datetime,)):
        return v.isoformat()
    if isinstance(v, (bytes,)):
        return v.decode("utf-8", errors="replace")
    return v


# ── Main Engine ────────────────────────────────────────────────────────────────
class AuditEngine:

    @staticmethod
    def log(
        db: Session,
        description: str,
        action: str,
        entity_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
        username: Optional[str] = None,
        user_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        module: Optional[str] = None,
        ref_type: Optional[str] = None,
        ref_id: Optional[UUID] = None,
        ref_number: Optional[str] = None,
        before_data: Optional[dict] = None,
        after_data: Optional[dict] = None,
        severity: Optional[str] = None,
        is_system: bool = False,
        request_id: Optional[UUID] = None,
        metadata: Optional[dict] = None,
        commit: bool = False,   # biasanya False; commit dilakukan oleh caller
    ) -> str:
        """
        Tulis satu audit log entry. Return log ID.
        Fire-and-forget: exception diabaikan agar tidak memblok transaksi utama.
        """
        try:
            before_safe = _safe_json(before_data)
            after_safe  = _safe_json(after_data)
            diff        = compute_diff(before_safe, after_safe)
            sev         = severity or _infer_severity(action)

            row = db.execute(
                text("""
                    INSERT INTO audit_log
                        (entity_id, user_id, username, user_ip, user_agent,
                         action, module, ref_type, ref_id, ref_number,
                         description, before_data, after_data, diff_data,
                         metadata, severity, is_system, request_id)
                    VALUES
                        (:eid, :uid, :uname, :ip::inet, :ua,
                         :action, :module, :rtype, :rid, :rnum,
                         :desc, :before::jsonb, :after::jsonb, :diff::jsonb,
                         :meta::jsonb, :sev, :sys, :reqid)
                    RETURNING id
                """),
                {
                    "eid":    str(entity_id) if entity_id else None,
                    "uid":    str(user_id)   if user_id   else None,
                    "uname":  username,
                    "ip":     user_ip,
                    "ua":     user_agent,
                    "action": action,
                    "module": module,
                    "rtype":  ref_type,
                    "rid":    str(ref_id) if ref_id else None,
                    "rnum":   ref_number,
                    "desc":   description,
                    "before": json.dumps(before_safe) if before_safe else None,
                    "after":  json.dumps(after_safe)  if after_safe  else None,
                    "diff":   json.dumps(diff)         if diff        else None,
                    "meta":   json.dumps(metadata)     if metadata    else None,
                    "sev":    sev,
                    "sys":    is_system,
                    "reqid":  str(request_id) if request_id else None,
                },
            ).fetchone()
            if commit:
                db.commit()
            return str(row.id)
        except Exception:
            # Never let audit logging break the main transaction
            try:
                db.rollback()
            except Exception:
                pass
            return ""

    # ── Query Methods ─────────────────────────────────────────────────────────

    @staticmethod
    def get_record_history(
        db: Session,
        ref_type: str,
        ref_id: UUID,
        limit: int = 100,
    ) -> list[dict]:
        """Timeline perubahan satu record (mis. semua event untuk invoice INV-001)."""
        rows = db.execute(
            text("""
                SELECT id, entity_id, user_id, username, action, module,
                       ref_number, description, diff_data, severity,
                       is_system, user_ip, created_at
                FROM audit_log
                WHERE ref_type=:rt AND ref_id=:rid
                ORDER BY created_at DESC
                LIMIT :lim
            """),
            {"rt": ref_type, "rid": str(ref_id), "lim": limit},
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def get_entity_activity(
        db: Session,
        entity_id: UUID,
        module: Optional[str] = None,
        action: Optional[str] = None,
        severity: Optional[str] = None,
        user_id: Optional[UUID] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        page: int = 1,
        size: int = 50,
    ) -> dict:
        """Semua aktivitas dalam satu entity, dengan filter."""
        filters = ["entity_id=:eid"]
        params: dict = {"eid": str(entity_id)}

        if module:
            filters.append("module=:mod");     params["mod"] = module
        if action:
            filters.append("action=:action");  params["action"] = action
        if severity:
            filters.append("severity=:sev");   params["sev"] = severity
        if user_id:
            filters.append("user_id=:uid");    params["uid"] = str(user_id)
        if date_from:
            filters.append("created_at >= :df::timestamptz"); params["df"] = date_from
        if date_to:
            filters.append("created_at <  :dt::timestamptz + INTERVAL '1 day'"); params["dt"] = date_to

        where = " AND ".join(filters)

        total = db.execute(
            text(f"SELECT COUNT(*) AS cnt FROM audit_log WHERE {where}"),
            params,
        ).fetchone().cnt

        params.update({"lim": size, "off": (page - 1) * size})
        rows = db.execute(
            text(f"""
                SELECT id, user_id, username, action, module, ref_type, ref_id,
                       ref_number, description, diff_data, severity, is_system,
                       user_ip, created_at
                FROM audit_log
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT :lim OFFSET :off
            """),
            params,
        ).fetchall()

        return {
            "total": total, "page": page, "size": size,
            "items": [dict(r._mapping) for r in rows],
        }

    @staticmethod
    def get_user_activity(
        db: Session,
        user_id: UUID,
        entity_id: Optional[UUID] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        page: int = 1,
        size: int = 50,
    ) -> dict:
        filters = ["user_id=:uid"]
        params: dict = {"uid": str(user_id)}
        if entity_id:
            filters.append("entity_id=:eid"); params["eid"] = str(entity_id)
        if date_from:
            filters.append("created_at >= :df::timestamptz"); params["df"] = date_from
        if date_to:
            filters.append("created_at <  :dt::timestamptz + INTERVAL '1 day'"); params["dt"] = date_to

        where = " AND ".join(filters)
        total = db.execute(
            text(f"SELECT COUNT(*) AS cnt FROM audit_log WHERE {where}"), params
        ).fetchone().cnt

        params.update({"lim": size, "off": (page - 1) * size})
        rows = db.execute(
            text(f"""
                SELECT id, entity_id, action, module, ref_type, ref_id,
                       ref_number, description, severity, is_system,
                       user_ip, created_at
                FROM audit_log
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT :lim OFFSET :off
            """),
            params,
        ).fetchall()

        return {
            "total": total, "page": page, "size": size,
            "items": [dict(r._mapping) for r in rows],
        }

    @staticmethod
    def get_critical_events(
        db: Session,
        entity_id: UUID,
        days: int = 30,
    ) -> list[dict]:
        """Critical/warning events dalam N hari terakhir (untuk security review)."""
        rows = db.execute(
            text("""
                SELECT id, username, action, module, ref_type, ref_id,
                       ref_number, description, diff_data, user_ip, created_at
                FROM vw_audit_critical_events
                WHERE entity_id=:eid
                  AND created_at >= NOW() - (:days || ' days')::INTERVAL
                ORDER BY created_at DESC
                LIMIT 500
            """),
            {"eid": str(entity_id), "days": days},
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def get_entity_stats(db: Session, entity_id: UUID) -> dict:
        """Statistik audit per module dan action dalam 30 hari."""
        rows = db.execute(
            text("""
                SELECT module, action, severity, event_count, unique_users
                FROM vw_audit_entity_summary
                WHERE entity_id=:eid
                  AND activity_date >= CURRENT_DATE - 30
                ORDER BY event_count DESC
            """),
            {"eid": str(entity_id)},
        ).fetchall()

        total_today = db.execute(
            text("""
                SELECT COUNT(*) AS cnt FROM audit_log
                WHERE entity_id=:eid AND created_at >= CURRENT_DATE
            """),
            {"eid": str(entity_id)},
        ).fetchone()

        total_critical = db.execute(
            text("""
                SELECT COUNT(*) AS cnt FROM audit_log
                WHERE entity_id=:eid AND severity='critical'
                  AND created_at >= CURRENT_DATE - 30
            """),
            {"eid": str(entity_id)},
        ).fetchone()

        return {
            "events_today":         int(total_today.cnt) if total_today else 0,
            "critical_last_30d":    int(total_critical.cnt) if total_critical else 0,
            "breakdown_by_module":  [dict(r._mapping) for r in rows],
        }

    @staticmethod
    def export_csv(
        db: Session,
        entity_id: UUID,
        date_from: str,
        date_to: str,
        module: Optional[str] = None,
    ) -> list[dict]:
        """Untuk export ke CSV dari router."""
        filters = ["entity_id=:eid", "created_at >= :df::date", "created_at < :dt::date + INTERVAL '1 day'"]
        params: dict = {"eid": str(entity_id), "df": date_from, "dt": date_to}
        if module:
            filters.append("module=:mod"); params["mod"] = module

        rows = db.execute(
            text(f"""
                SELECT
                    created_at, username, action, module, ref_type, ref_number,
                    description, severity, user_ip,
                    diff_data::text AS diff_data
                FROM audit_log
                WHERE {' AND '.join(filters)}
                ORDER BY created_at DESC
                LIMIT 10000
            """),
            params,
        ).fetchall()
        return [dict(r._mapping) for r in rows]
