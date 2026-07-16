"""
Invoice Template & Email Router
Prefix: /invoice-templates
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import (
    APIRouter, Depends, File, HTTPException, Query,
    Response, UploadFile,
)
from pydantic import BaseModel, EmailStr, Field, validator
from sqlalchemy.orm import Session

from core.database import get_db
from modules.auth import get_current_user
from .invoice_template_engine import InvoiceTemplateEngine
from .invoice_email_engine import InvoiceEmailEngine

router = APIRouter(prefix="/invoice-templates", tags=["Invoice Template & Email"])


# ── Pydantic Models ───────────────────────────────────────────────────────────

class InvoiceTemplateCreate(BaseModel):
    entity_id: str
    template_name: str = Field(..., min_length=1)
    is_default: bool = False
    template_html: Optional[str] = None       # kosong = pakai built-in default
    primary_color: str = "#1a56db"
    secondary_color: str = "#374151"
    font_family: str = "Inter, Arial, sans-serif"
    logo_url: Optional[str] = None
    company_display_name: Optional[str] = None
    company_address: Optional[str] = None
    company_phone: Optional[str] = None
    company_email: Optional[str] = None
    company_website: Optional[str] = None
    company_tax_number: Optional[str] = None
    company_bank_name: Optional[str] = None
    company_bank_account: Optional[str] = None
    company_bank_holder: Optional[str] = None
    company_bank_branch: Optional[str] = None
    footer_text: Optional[str] = None
    payment_terms_text: Optional[str] = None
    notes_default: Optional[str] = None
    paper_size: str = Field("A4", pattern="^(A4|Letter)$")


class InvoiceTemplateUpdate(BaseModel):
    template_name: Optional[str] = None
    is_default: Optional[bool] = None
    template_html: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    font_family: Optional[str] = None
    logo_url: Optional[str] = None
    company_display_name: Optional[str] = None
    company_address: Optional[str] = None
    company_phone: Optional[str] = None
    company_email: Optional[str] = None
    company_website: Optional[str] = None
    company_tax_number: Optional[str] = None
    company_bank_name: Optional[str] = None
    company_bank_account: Optional[str] = None
    company_bank_holder: Optional[str] = None
    company_bank_branch: Optional[str] = None
    footer_text: Optional[str] = None
    payment_terms_text: Optional[str] = None
    notes_default: Optional[str] = None
    paper_size: Optional[str] = None
    is_active: Optional[bool] = None


class EmailTemplateCreate(BaseModel):
    entity_id: str
    template_name: str = Field(..., min_length=1)
    is_default: bool = False
    subject_template: str = Field(
        "Invoice {{ invoice_number }} dari {{ company_name }}",
        min_length=1
    )
    body_template: Optional[str] = None      # kosong = pakai built-in default
    reply_to: Optional[str] = None


class EmailTemplateUpdate(BaseModel):
    template_name: Optional[str] = None
    is_default: Optional[bool] = None
    subject_template: Optional[str] = None
    body_template: Optional[str] = None
    reply_to: Optional[str] = None
    is_active: Optional[bool] = None


class SmtpConfig(BaseModel):
    entity_id: str
    smtp_host: str = Field(..., min_length=1)
    smtp_port: int = Field(587, ge=1, le=65535)
    smtp_username: str
    smtp_password: str
    from_name: str
    from_address: str
    use_tls: bool = True
    use_ssl: bool = False
    reply_to: Optional[str] = None


class SmtpTest(BaseModel):
    entity_id: str
    test_email: str


class InvoiceEmailUpdate(BaseModel):
    """Update field email di ar_invoice."""
    customer_email: Optional[str] = None
    email_cc: Optional[str] = None             # koma-separated
    invoice_template_id: Optional[str] = None
    email_template_id: Optional[str] = None


class SendInvoiceEmail(BaseModel):
    entity_id: str
    to_email: Optional[str] = None             # override email tujuan
    cc_emails: Optional[List[str]] = None
    subject: Optional[str] = None
    custom_message: Optional[str] = None
    sender_name: Optional[str] = None
    sender_title: Optional[str] = None
    sender_email: Optional[str] = None
    sender_phone: Optional[str] = None
    email_template_id: Optional[str] = None
    invoice_template_id: Optional[str] = None
    attach_pdf: bool = True


class PostAndSend(BaseModel):
    """Post invoice dan kirim email sekaligus."""
    entity_id: str
    to_email: Optional[str] = None
    cc_emails: Optional[List[str]] = None
    custom_message: Optional[str] = None
    sender_name: Optional[str] = None
    sender_title: Optional[str] = None
    sender_email: Optional[str] = None
    attach_pdf: bool = True
    send_email: bool = True                    # bisa false untuk posting saja tanpa email


# ── 1. Invoice Template ───────────────────────────────────────────────────────

@router.get("/default-html")
def get_default_html():
    """
    Ambil HTML template default bawaan sistem.
    Gunakan ini sebagai starting point untuk kustomisasi.
    """
    from .invoice_template_engine import DEFAULT_INVOICE_TEMPLATE
    return {"template_html": DEFAULT_INVOICE_TEMPLATE}


@router.get("")
def list_templates(
    entity_id: str = Query(...),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Daftar semua invoice template untuk entity."""
    return InvoiceTemplateEngine.list_templates(db, entity_id)


@router.post("", status_code=201)
def create_template(
    data: InvoiceTemplateCreate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Buat template invoice baru.
    Jika template_html dikosongkan, menggunakan template default bawaan sistem.
    """
    try:
        return InvoiceTemplateEngine.create_template(
            db=db,
            entity_id=data.entity_id,
            template_name=data.template_name,
            is_default=data.is_default,
            template_html=data.template_html,
            logo_url=data.logo_url,
            primary_color=data.primary_color,
            secondary_color=data.secondary_color,
            font_family=data.font_family,
            company_display_name=data.company_display_name,
            company_address=data.company_address,
            company_phone=data.company_phone,
            company_email=data.company_email,
            company_website=data.company_website,
            company_tax_number=data.company_tax_number,
            company_bank_name=data.company_bank_name,
            company_bank_account=data.company_bank_account,
            company_bank_holder=data.company_bank_holder,
            company_bank_branch=data.company_bank_branch,
            footer_text=data.footer_text,
            payment_terms_text=data.payment_terms_text,
            notes_default=data.notes_default,
            paper_size=data.paper_size,
            created_by=user.get("username"),
        )
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/{template_id}")
def get_template(
    template_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Detail template (termasuk HTML template body)."""
    try:
        return InvoiceTemplateEngine.get_template(db, template_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.put("/{template_id}")
def update_template(
    template_id: str,
    entity_id: str = Query(...),
    data: InvoiceTemplateUpdate = ...,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Update template (partial update — hanya field yang diisi)."""
    try:
        return InvoiceTemplateEngine.update_template(
            db, template_id, entity_id, **data.dict(exclude_none=True)
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{template_id}/upload-logo")
def upload_logo(
    template_id: str,
    entity_id: str = Query(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Upload logo perusahaan (PNG/JPG/SVG, max 2MB).
    Disimpan sebagai base64 di database dan ditampilkan di invoice.
    """
    if file.content_type not in ("image/png", "image/jpeg", "image/jpg", "image/svg+xml", "image/webp"):
        raise HTTPException(400, "Format file harus PNG, JPG, atau SVG.")

    content = file.file.read()
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(400, "Logo terlalu besar (max 2MB).")

    try:
        return InvoiceTemplateEngine.upload_logo(
            db, template_id, entity_id, content, file.content_type
        )
    except Exception as e:
        raise HTTPException(400, str(e))


# ── 2. Email Template ─────────────────────────────────────────────────────────

@router.get("/default-email-html")
def get_default_email_html():
    """HTML template email default bawaan sistem."""
    from .invoice_template_engine import DEFAULT_EMAIL_BODY, DEFAULT_EMAIL_SUBJECT
    return {
        "subject_template": DEFAULT_EMAIL_SUBJECT,
        "body_template": DEFAULT_EMAIL_BODY,
        "available_variables": [
            "customer_name", "invoice_number", "invoice_date", "due_date",
            "total_amount", "currency", "currency_symbol",
            "company_name", "company_email", "company_phone",
            "bank_name", "bank_account", "bank_holder",
            "is_overdue", "days_until_due", "overdue_days",
            "primary_color", "custom_message",
            "sender_name", "sender_title", "sender_email", "sender_phone",
        ],
    }


@router.get("/email-templates")
def list_email_templates(
    entity_id: str = Query(...),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Daftar semua email template untuk entity."""
    return InvoiceTemplateEngine.list_email_templates(db, entity_id)


@router.post("/email-templates", status_code=201)
def create_email_template(
    data: EmailTemplateCreate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Buat email template baru.
    Subject dan body bisa menggunakan variabel Jinja2 (lihat GET /default-email-html).
    """
    from .invoice_template_engine import DEFAULT_EMAIL_BODY
    try:
        return InvoiceTemplateEngine.create_email_template(
            db=db,
            entity_id=data.entity_id,
            template_name=data.template_name,
            subject_template=data.subject_template,
            body_template=data.body_template or DEFAULT_EMAIL_BODY,
            is_default=data.is_default,
            reply_to=data.reply_to,
            created_by=user.get("username"),
        )
    except Exception as e:
        raise HTTPException(400, str(e))


@router.put("/email-templates/{template_id}")
def update_email_template(
    template_id: str,
    entity_id: str = Query(...),
    data: EmailTemplateUpdate = ...,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Update email template."""
    try:
        return InvoiceTemplateEngine.update_email_template(
            db, template_id, entity_id, **data.dict(exclude_none=True)
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── 3. SMTP Config ────────────────────────────────────────────────────────────

@router.post("/smtp-config")
def save_smtp_config(
    data: SmtpConfig,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Simpan atau update konfigurasi SMTP per entity.

    Jika tidak dikonfigurasi di sini, sistem menggunakan variabel .env:
    SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD,
    EMAIL_FROM_NAME, EMAIL_FROM_ADDRESS
    """
    try:
        return InvoiceEmailEngine.save_smtp_config(
            db=db,
            entity_id=data.entity_id,
            smtp_host=data.smtp_host,
            smtp_port=data.smtp_port,
            smtp_username=data.smtp_username,
            smtp_password=data.smtp_password,
            from_name=data.from_name,
            from_address=data.from_address,
            use_tls=data.use_tls,
            use_ssl=data.use_ssl,
            reply_to=data.reply_to,
            created_by=user.get("username"),
        )
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/smtp-config/test")
def test_smtp(
    data: SmtpTest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Kirim test email untuk verifikasi konfigurasi SMTP.
    Berguna sebelum go-live untuk pastikan konfigurasi benar.
    """
    return InvoiceEmailEngine.test_smtp(db, data.entity_id, data.test_email)


# ── 4. Invoice Email Fields ───────────────────────────────────────────────────

@router.patch("/invoices/{invoice_id}/email-fields")
def update_invoice_email_fields(
    invoice_id: str,
    data: InvoiceEmailUpdate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Update field email di invoice:
      - customer_email: alamat tujuan email
      - email_cc: CC (koma-separated)
      - invoice_template_id: template layout invoice
      - email_template_id: template email

    Bisa diisi sebelum preview/kirim.
    """
    from sqlalchemy import text
    updates = data.dict(exclude_none=True)
    if not updates:
        raise HTTPException(400, "Tidak ada field yang diupdate.")

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = invoice_id
    db.execute(text(f"UPDATE ar_invoice SET {set_clause} WHERE id = :id"), updates)
    db.commit()
    return {"invoice_id": invoice_id, "updated": list(updates.keys())}


# ── 5. Preview Invoice ────────────────────────────────────────────────────────

@router.get("/invoices/{invoice_id}/preview-html")
def preview_invoice_html(
    invoice_id: str,
    template_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Preview invoice sebagai HTML.
    Tampilkan di browser sebelum kirim ke customer.
    Jika invoice masih draft, akan ada watermark 'DRAFT'.
    """
    try:
        html = InvoiceTemplateEngine.render_html(db, invoice_id, template_id)
        return Response(content=html, media_type="text/html")
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/invoices/{invoice_id}/preview-pdf")
def preview_invoice_pdf(
    invoice_id: str,
    template_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Preview invoice sebagai PDF (download).
    Memerlukan WeasyPrint: pip install weasyprint
    """
    try:
        pdf = InvoiceTemplateEngine.render_pdf(db, invoice_id, template_id)

        # Ambil nomor invoice untuk nama file
        from sqlalchemy import text
        inv_num = db.execute(
            text("SELECT invoice_number FROM ar_invoice WHERE id = :id"),
            {"id": invoice_id}
        ).scalar()
        filename = f"Invoice_{(inv_num or invoice_id[:8]).replace('/', '-')}.pdf"

        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f"inline; filename={filename}"},
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(501, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/invoices/{invoice_id}/preview-email")
def preview_email(
    invoice_id: str,
    email_template_id: Optional[str] = Query(None),
    custom_message: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Preview email yang akan dikirim (HTML).
    Tampilkan subject, body, dan penerima sebelum kirim.
    """
    try:
        rendered = InvoiceTemplateEngine.render_email(
            db=db,
            invoice_id=invoice_id,
            email_template_id=email_template_id,
            custom_message=custom_message,
        )
        return rendered
    except ValueError as e:
        raise HTTPException(404, str(e))


# ── 6. Send Email Manual ──────────────────────────────────────────────────────

@router.post("/invoices/{invoice_id}/send-email")
def send_email(
    invoice_id: str,
    data: SendInvoiceEmail,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Kirim invoice via email manual.
    Bisa digunakan untuk resend atau kirim ke email berbeda.

    PDF invoice otomatis di-attach (jika WeasyPrint tersedia).
    """
    return InvoiceEmailEngine.send_invoice(
        db=db,
        invoice_id=invoice_id,
        entity_id=data.entity_id,
        sent_by=user.get("username"),
        to_email=data.to_email,
        cc_emails=data.cc_emails,
        subject=data.subject,
        custom_message=data.custom_message,
        sender_name=data.sender_name,
        sender_title=data.sender_title,
        sender_email=data.sender_email,
        sender_phone=data.sender_phone,
        email_template_id=data.email_template_id,
        invoice_template_id=data.invoice_template_id,
        attach_pdf=data.attach_pdf,
    )


# ── 7. Post Invoice + Auto-Send Email ─────────────────────────────────────────

@router.post("/invoices/{invoice_id}/post-and-send")
def post_and_send(
    invoice_id: str,
    data: PostAndSend,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    **Post invoice dan kirim email sekaligus.**

    Langkah yang dilakukan:
    1. Validasi: invoice harus dalam status 'draft'
    2. Ubah status invoice → 'posted'
    3. Kirim email ke customer (jika send_email=True)

    Jika pengiriman email gagal, posting TETAP berhasil
    (error dicatat di email_log, bisa di-resend nanti).
    """
    from sqlalchemy import text

    # Ambil invoice
    inv = db.execute(text("""
        SELECT id, status, invoice_number, entity_id
        FROM ar_invoice WHERE id = :id AND entity_id = :eid
    """), {"id": invoice_id, "eid": data.entity_id}).first()

    if inv is None:
        raise HTTPException(404, "Invoice tidak ditemukan.")
    if inv.status != "draft":
        raise HTTPException(400, f"Invoice status '{inv.status}'. Hanya invoice 'draft' yang bisa diposting.")

    # Post invoice: ubah status → posted
    db.execute(text("""
        UPDATE ar_invoice
           SET status = 'posted', posted_at = NOW(), posted_by = :pb
        WHERE id = :id
    """), {"pb": user.get("username"), "id": invoice_id})
    db.commit()

    result = {
        "invoice_id": invoice_id,
        "invoice_number": inv.invoice_number,
        "status": "posted",
        "posted_by": user.get("username"),
        "email_result": None,
    }

    # Kirim email
    if data.send_email:
        email_result = InvoiceEmailEngine.send_invoice(
            db=db,
            invoice_id=invoice_id,
            entity_id=data.entity_id,
            sent_by=user.get("username"),
            to_email=data.to_email,
            cc_emails=data.cc_emails,
            custom_message=data.custom_message,
            sender_name=data.sender_name,
            sender_title=data.sender_title,
            sender_email=data.sender_email,
            attach_pdf=data.attach_pdf,
        )
        result["email_result"] = email_result
        if not email_result.get("success"):
            result["email_warning"] = (
                "Invoice berhasil diposting, tapi pengiriman email gagal. "
                f"Error: {email_result.get('error')}. "
                "Gunakan /send-email untuk mencoba lagi."
            )

    return result


# ── 8. Email Log ──────────────────────────────────────────────────────────────

@router.get("/invoices/{invoice_id}/email-log")
def get_invoice_email_log(
    invoice_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Riwayat pengiriman email untuk invoice tertentu."""
    return InvoiceEmailEngine.get_email_log(db, invoice_id=invoice_id)


@router.get("/email-log")
def get_entity_email_log(
    entity_id: str = Query(...),
    limit: int = Query(50, le=500),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Riwayat pengiriman email semua invoice untuk entity."""
    return InvoiceEmailEngine.get_email_log(db, entity_id=entity_id, limit=limit)


@router.get("/email-status")
def get_email_status(
    entity_id: str = Query(...),
    only_unsent: bool = False,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Status email semua invoice: sudah terkirim / belum / gagal.
    Berguna untuk memantau invoice yang belum diemailkan ke customer.
    """
    from sqlalchemy import text
    where = "WHERE entity_id = :eid AND invoice_status = 'posted'"
    if only_unsent:
        where += " AND (email_sent = FALSE OR email_sent IS NULL)"

    rows = db.execute(text(f"""
        SELECT * FROM vw_invoice_email_status {where}
        ORDER BY invoice_date DESC
        LIMIT 200
    """), {"eid": entity_id}).fetchall()
    return [dict(r._mapping) for r in rows]
