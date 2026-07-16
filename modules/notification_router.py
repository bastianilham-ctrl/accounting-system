from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Optional
from core.database import get_db
from uuid import UUID

router = APIRouter(prefix="/notifications", tags=["Notifications & Alerts"])

@router.get("/{entity_id}")
def get_notifications(entity_id: UUID, unread_only: bool = True, db: Session = Depends(get_db)):
    """Ambil daftar notifikasi untuk entity tertentu."""
    query = "SELECT * FROM notifications WHERE entity_id = :eid"
    if unread_only:
        query += " AND is_read = FALSE"
    query += " ORDER BY created_at DESC LIMIT 50"
    
    rows = db.execute(text(query), {"eid": str(entity_id)}).fetchall()
    return [dict(r._mapping) for r in rows]

@router.post("/{notification_id}/read")
def mark_as_read(notification_id: UUID, db: Session = Depends(get_db)):
    """Tandai notifikasi sebagai sudah dibaca."""
    db.execute(
        text("UPDATE notifications SET is_read = TRUE WHERE id = :id"),
        {"id": str(notification_id)}
    )
    db.commit()
    return {"success": True}

@router.post("/read-all/{entity_id}")
def read_all(entity_id: UUID, db: Session = Depends(get_db)):
    """Tandai semua notifikasi entity sebagai sudah dibaca."""
    db.execute(
        text("UPDATE notifications SET is_read = TRUE WHERE entity_id = :eid"),
        {"eid": str(entity_id)}
    )
    db.commit()
    return {"success": True}