"""
Email Marketing Router
Prefix: /email-marketing
Tracking pixel (public): /email-marketing/track/{token}/open
"""

import smtplib
import uuid as uuid_lib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger

from core.database import get_db, SessionLocal
from config.settings import settings

router = APIRouter(prefix="/email-marketing", tags=["Email Marketing"])
tracking_router = APIRouter(prefix="/email-marketing", tags=["Email Tracking"])

# 1×1 transparent GIF
_PIXEL = (
    b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00'
    b'\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00'
    b'\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02'
    b'\x44\x01\x00\x3b'
)


# ── Pydantic Models ────────────────────────────────────────────────────────────

class CampaignCreate(BaseModel):
    entity_id: str
    name: str
    subject: str
    body_html: str
    created_by: Optional[str] = "system"


class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    subject: Optional[str] = None
    body_html: Optional[str] = None


class AddRecipientsRequest(BaseModel):
    person_ids: List[str]


class AddManualRecipient(BaseModel):
    campaign_id: str
    recipient_email: str
    recipient_name: Optional[str] = None
    company_name: Optional[str] = None


# ── Campaign CRUD ──────────────────────────────────────────────────────────────

@router.get("/campaigns")
def list_campaigns(entity_id: str, db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT
            ec.id, ec.name, ec.subject, ec.status,
            ec.scheduled_at, ec.sent_at, ec.created_by, ec.created_at,
            COUNT(ecr.id) AS total_recipients,
            COUNT(ecr.id) FILTER (WHERE ecr.status = 'sent') AS sent_count,
            COUNT(ecr.id) FILTER (WHERE ecr.status = 'failed') AS failed_count,
            COUNT(ecr.id) FILTER (WHERE ecr.opened_at IS NOT NULL) AS opened_count
        FROM email_campaign ec
        LEFT JOIN email_campaign_recipient ecr ON ecr.campaign_id = ec.id
        WHERE ec.entity_id = :eid
        GROUP BY ec.id
        ORDER BY ec.created_at DESC
    """), {"eid": entity_id}).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/campaigns")
def create_campaign(body: CampaignCreate, db: Session = Depends(get_db)):
    row = db.execute(text("""
        INSERT INTO email_campaign (entity_id, name, subject, body_html, created_by)
        VALUES (:eid, :name, :subject, :body, :by)
        RETURNING id, name, subject, status, created_at
    """), {
        "eid": body.entity_id, "name": body.name,
        "subject": body.subject, "body": body.body_html,
        "by": body.created_by,
    }).fetchone()
    db.commit()
    return dict(row._mapping)


@router.put("/campaigns/{campaign_id}")
def update_campaign(campaign_id: str, body: CampaignUpdate, db: Session = Depends(get_db)):
    existing = db.execute(
        text("SELECT status FROM email_campaign WHERE id = :id"), {"id": campaign_id}
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Campaign tidak ditemukan")
    if existing.status not in ("draft",):
        raise HTTPException(status_code=400, detail="Hanya campaign draft yang bisa diedit")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="Tidak ada field yang diupdate")
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["cid"] = campaign_id
    db.execute(text(f"UPDATE email_campaign SET {set_clause}, updated_at = NOW() WHERE id = :cid"), fields)
    db.commit()
    return {"success": True}


@router.delete("/campaigns/{campaign_id}")
def delete_campaign(campaign_id: str, db: Session = Depends(get_db)):
    db.execute(text("DELETE FROM email_campaign WHERE id = :id"), {"id": campaign_id})
    db.commit()
    return {"success": True}


# ── Recipient Management ───────────────────────────────────────────────────────

@router.get("/campaigns/{campaign_id}/recipients")
def list_recipients(campaign_id: str, db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT ecr.id, ecr.recipient_email, ecr.recipient_name, ecr.company_name,
               ecr.status, ecr.sent_at, ecr.opened_at, ecr.open_count,
               ecr.error_message, ecr.created_at,
               cp.title
        FROM email_campaign_recipient ecr
        LEFT JOIN contact_person cp ON cp.id = ecr.contact_person_id
        WHERE ecr.campaign_id = :cid
        ORDER BY ecr.created_at
    """), {"cid": campaign_id}).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/campaigns/{campaign_id}/recipients")
def add_recipients_from_persons(campaign_id: str, body: AddRecipientsRequest,
                                 db: Session = Depends(get_db)):
    campaign = db.execute(
        text("SELECT status FROM email_campaign WHERE id = :id"), {"id": campaign_id}
    ).fetchone()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign tidak ditemukan")

    added = 0
    for pid in body.person_ids:
        person = db.execute(text("""
            SELECT cp.full_name, cp.email, c.company_name
            FROM contact_person cp
            JOIN contact c ON c.id = cp.contact_id
            WHERE cp.id = :pid AND cp.email IS NOT NULL AND cp.email != ''
        """), {"pid": pid}).fetchone()
        if not person:
            continue
        existing = db.execute(text("""
            SELECT id FROM email_campaign_recipient
            WHERE campaign_id = :cid AND recipient_email = :email
        """), {"cid": campaign_id, "email": person.email}).fetchone()
        if existing:
            continue
        db.execute(text("""
            INSERT INTO email_campaign_recipient
                (campaign_id, contact_person_id, recipient_email, recipient_name, company_name)
            VALUES (:cid, :pid, :email, :name, :company)
        """), {
            "cid": campaign_id, "pid": pid,
            "email": person.email, "name": person.full_name,
            "company": person.company_name,
        })
        added += 1
    db.commit()
    return {"added": added}


@router.post("/recipients/manual")
def add_manual_recipient(body: AddManualRecipient, db: Session = Depends(get_db)):
    db.execute(text("""
        INSERT INTO email_campaign_recipient
            (campaign_id, recipient_email, recipient_name, company_name)
        VALUES (:cid, :email, :name, :company)
        ON CONFLICT DO NOTHING
    """), {
        "cid": body.campaign_id, "email": body.recipient_email,
        "name": body.recipient_name, "company": body.company_name,
    })
    db.commit()
    return {"success": True}


@router.delete("/recipients/{recipient_id}")
def delete_recipient(recipient_id: str, db: Session = Depends(get_db)):
    db.execute(text("DELETE FROM email_campaign_recipient WHERE id = :id"), {"id": recipient_id})
    db.commit()
    return {"success": True}


# ── Send Campaign ──────────────────────────────────────────────────────────────

@router.post("/campaigns/{campaign_id}/send")
def send_campaign(campaign_id: str, request: Request,
                  background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    campaign = db.execute(text("""
        SELECT id, name, subject, body_html, status FROM email_campaign WHERE id = :id
    """), {"id": campaign_id}).fetchone()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign tidak ditemukan")
    if campaign.status not in ("draft", "scheduled"):
        raise HTTPException(status_code=400, detail=f"Campaign sudah {campaign.status}")

    pending = db.execute(text("""
        SELECT COUNT(*) FROM email_campaign_recipient
        WHERE campaign_id = :cid AND status = 'pending'
    """), {"cid": campaign_id}).scalar()
    if not pending:
        raise HTTPException(status_code=400, detail="Tidak ada penerima pending")

    db.execute(text("UPDATE email_campaign SET status = 'sending', updated_at = NOW() WHERE id = :id"),
               {"id": campaign_id})
    db.commit()

    base_url = str(request.base_url).rstrip("/")
    background_tasks.add_task(_send_all, campaign_id, base_url)
    return {"success": True, "message": f"Mengirim ke {pending} penerima di latar belakang"}


def _send_all(campaign_id: str, base_url: str):
    db = SessionLocal()
    try:
        campaign = db.execute(text("""
            SELECT subject, body_html FROM email_campaign WHERE id = :id
        """), {"id": campaign_id}).fetchone()
        if not campaign:
            return

        recipients = db.execute(text("""
            SELECT id, recipient_email, recipient_name, tracking_token
            FROM email_campaign_recipient
            WHERE campaign_id = :cid AND status = 'pending'
        """), {"cid": campaign_id}).fetchall()

        success = 0
        failed = 0

        for r in recipients:
            try:
                pixel_url = f"{base_url}/email-marketing/track/{r.tracking_token}/open"
                html = campaign.body_html + (
                    f'\n<img src="{pixel_url}" width="1" height="1" '
                    f'style="display:none" alt="" />'
                )
                _smtp_send(r.recipient_email, r.recipient_name, campaign.subject, html)
                db.execute(text("""
                    UPDATE email_campaign_recipient
                    SET status = 'sent', sent_at = NOW()
                    WHERE id = :id
                """), {"id": r.id})
                success += 1
            except Exception as e:
                db.execute(text("""
                    UPDATE email_campaign_recipient
                    SET status = 'failed', error_message = :err
                    WHERE id = :id
                """), {"id": r.id, "err": str(e)[:500]})
                failed += 1

        db.execute(text("""
            UPDATE email_campaign
            SET status = 'sent', sent_at = NOW(), updated_at = NOW()
            WHERE id = :id
        """), {"id": campaign_id})
        db.commit()
        logger.info(f"Campaign {campaign_id}: {success} terkirim, {failed} gagal")
    except Exception as e:
        logger.error(f"Send campaign error: {e}")
        db.execute(text("""
            UPDATE email_campaign SET status = 'draft', updated_at = NOW() WHERE id = :id
        """), {"id": campaign_id})
        db.commit()
    finally:
        db.close()


def _smtp_send(to_email: str, to_name: Optional[str], subject: str, html: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_USER}>"
    msg["To"] = f"{to_name} <{to_email}>" if to_name else to_email
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as s:
        s.ehlo()
        s.starttls()
        s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        s.sendmail(settings.SMTP_USER, to_email, msg.as_string())


# ── Stats ──────────────────────────────────────────────────────────────────────

@router.get("/campaigns/{campaign_id}/stats")
def campaign_stats(campaign_id: str, db: Session = Depends(get_db)):
    campaign = db.execute(text("""
        SELECT id, name, subject, status, sent_at, created_at FROM email_campaign WHERE id = :id
    """), {"id": campaign_id}).fetchone()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign tidak ditemukan")

    stats = db.execute(text("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE status = 'sent') AS sent,
            COUNT(*) FILTER (WHERE status = 'failed') AS failed,
            COUNT(*) FILTER (WHERE status = 'pending') AS pending,
            COUNT(*) FILTER (WHERE opened_at IS NOT NULL) AS opened,
            ROUND(
                COUNT(*) FILTER (WHERE opened_at IS NOT NULL)::numeric
                / NULLIF(COUNT(*) FILTER (WHERE status = 'sent'), 0) * 100, 1
            ) AS open_rate_pct
        FROM email_campaign_recipient
        WHERE campaign_id = :cid
    """), {"cid": campaign_id}).fetchone()

    return {**dict(campaign._mapping), **dict(stats._mapping)}


# ── Open Tracking Pixel (PUBLIC — no auth) ─────────────────────────────────────

@tracking_router.get("/track/{token}/open")
def track_open(token: str, db: Session = Depends(get_db)):
    db.execute(text("""
        UPDATE email_campaign_recipient
        SET opened_at = COALESCE(opened_at, NOW()),
            open_count = open_count + 1
        WHERE tracking_token = :token
    """), {"token": token})
    db.commit()
    return Response(content=_PIXEL, media_type="image/gif",
                    headers={"Cache-Control": "no-cache, no-store"})
