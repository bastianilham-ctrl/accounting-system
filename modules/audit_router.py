"""
Audit Trail Router
Base prefix: /audit

Semua endpoint read-only (audit log tidak bisa diedit/dihapus via API).
"""

import csv
import io
from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from core.database import get_db
from modules.audit_engine import AuditEngine

router = APIRouter(prefix="/audit", tags=["Audit Trail"])


# ── History Satu Record ───────────────────────────────────────────────────────

@router.get("/records/{ref_type}/{ref_id}", summary="Timeline perubahan satu record")
def record_history(
    ref_type: str,
    ref_id:   UUID,
    limit:    int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """
    Tampilkan semua audit event untuk satu record tertentu.
    Contoh: GET /audit/records/ar_invoice/{invoice_id}
    """
    return AuditEngine.get_record_history(db, ref_type, ref_id, limit)


# ── Entity Activity Log ───────────────────────────────────────────────────────

@router.get("/entity/{entity_id}", summary="Semua aktivitas dalam satu entity")
def entity_activity(
    entity_id: UUID,
    module:    Optional[str] = None,
    action:    Optional[str] = None,
    severity:  Optional[str] = None,
    user_id:   Optional[UUID] = None,
    date_from: Optional[str] = Query(default=None, description="YYYY-MM-DD"),
    date_to:   Optional[str] = Query(default=None, description="YYYY-MM-DD"),
    page:      int           = Query(default=1, ge=1),
    size:      int           = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return AuditEngine.get_entity_activity(
        db, entity_id, module, action, severity, user_id, date_from, date_to, page, size
    )


# ── Critical Events ───────────────────────────────────────────────────────────

@router.get("/entity/{entity_id}/critical", summary="Event kritis 30 hari terakhir")
def critical_events(
    entity_id: UUID,
    days:      int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Tampilkan aksi kritis: DELETE, VOID, CLOSE, GRANT_ACCESS, REVERSE, dll."""
    return AuditEngine.get_critical_events(db, entity_id, days)


# ── Entity Stats ──────────────────────────────────────────────────────────────

@router.get("/entity/{entity_id}/stats", summary="Statistik audit per module (30 hari)")
def entity_stats(entity_id: UUID, db: Session = Depends(get_db)):
    return AuditEngine.get_entity_stats(db, entity_id)


# ── User Activity ─────────────────────────────────────────────────────────────

@router.get("/users/{user_id}", summary="Semua aktivitas satu user")
def user_activity(
    user_id:   UUID,
    entity_id: Optional[UUID] = None,
    date_from: Optional[str]  = Query(default=None, description="YYYY-MM-DD"),
    date_to:   Optional[str]  = Query(default=None, description="YYYY-MM-DD"),
    page:      int            = Query(default=1, ge=1),
    size:      int            = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return AuditEngine.get_user_activity(db, user_id, entity_id, date_from, date_to, page, size)


# ── Export CSV ────────────────────────────────────────────────────────────────

@router.get("/entity/{entity_id}/export", summary="Export audit log ke CSV")
def export_audit_csv(
    entity_id: UUID,
    date_from: str = Query(..., description="YYYY-MM-DD"),
    date_to:   str = Query(..., description="YYYY-MM-DD"),
    module:    Optional[str] = None,
    db: Session = Depends(get_db),
):
    rows = AuditEngine.export_csv(db, entity_id, date_from, date_to, module)
    if not rows:
        raise HTTPException(status_code=404, detail="Tidak ada data dalam rentang tanggal tersebut.")

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    output.seek(0)

    filename = f"audit_{date_from}_{date_to}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Available Modules & Actions (for filter dropdowns) ───────────────────────

@router.get("/meta/actions", summary="Daftar action yang tersedia")
def list_actions():
    return {"actions": [
        "LOGIN","LOGOUT","PASSWORD_CHANGE","TOKEN_REFRESH",
        "CREATE","UPDATE","DELETE","RESTORE",
        "SUBMIT","APPROVE","REJECT","CANCEL","VOID","REVERSE",
        "POST","LOCK","CLOSE","REOPEN","FINALIZE",
        "GRANT_ACCESS","REVOKE_ACCESS","ROLE_CHANGE",
        "IMPORT","EXPORT","SCHEDULER_RUN","OTHER",
    ]}


@router.get("/meta/modules", summary="Daftar module yang ada di audit log")
def list_modules(entity_id: UUID, db: Session = Depends(get_db)):
    rows = db.execute(
        __import__("sqlalchemy").text("""
            SELECT DISTINCT module FROM audit_log
            WHERE entity_id=:eid AND module IS NOT NULL
            ORDER BY module
        """),
        {"eid": str(entity_id)},
    ).fetchall()
    return {"modules": [r.module for r in rows]}
