"""
Invoice Email Engine
=====================
Mengirim invoice via email dengan attachment PDF.

Config SMTP (dari .env atau entity_email_config):
  SMTP_HOST         = smtp.gmail.com
  SMTP_PORT         = 587
  SMTP_USERNAME     = your@email.com
  SMTP_PASSWORD     = yourpassword
  SMTP_USE_TLS      = true
  EMAIL_FROM_NAME   = PT Company Name
  EMAIL_FROM_ADDRESS = noreply@company.com

Untuk Gmail: aktifkan "App Password" (bukan password biasa).
Untuk Exchange/Outlook: gunakan port 587 + STARTTLS.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from .invoice_template_engine import InvoiceTemplateEngine


class InvoiceEmailEngine:

    # ── Send Invoice ──────────────────────────────────────────────────────────

    @staticmethod
    def send_invoice(
        db: Session,
        invoice_id: str,
        entity_id: str,
        sent_by: str,
        to_email: Optional[str] = None,
        cc_emails: Optional[list[str]] = None,
        subject: Optional[str] = None,
        custom_message: Optional[str] = None,
        sender_name: Optional[str] = None,
        sender_title: Optional[str] = None,
        sender_email: Optional[str] = None,
        sender_phone: Optional[str] = None,
        email_template_id: Optional[str] = None,
        invoice_template_id: Optional[str] = None,
        attach_pdf: bool = True,
    ) -> dict:
        """
        Kirim invoice via email dengan attachment PDF.

        Jika to_email tidak diberikan, gunakan customer_email dari invoice
        atau email master dari data customer.

        Return: {success, message_id, log_id}
        Tidak raise exception — semua error dicatat ke email_log.
        """
        # Render email
        try:
            rendered = InvoiceTemplateEngine.render_email(
                db=db,
                invoice_id=invoice_id,
                email_template_id=email_template_id,
                custom_message=custom_message,
                sender_name=sender_name,
                sender_title=sender_title,
                sender_email=sender_email,
                sender_phone=sender_phone,
            )
        except Exception as e:
            InvoiceEmailEngine._log_attempt(
                db, invoice_id, entity_id, to_email or "unknown",
                None, None, None, "failed", str(e), sent_by
            )
            return {"success": False, "error": str(e)}

        final_to = to_email or rendered["to_email"]
        if not final_to:
            msg = "Email tujuan tidak ditemukan. Lengkapi email customer atau field customer_email di invoice."
            InvoiceEmailEngine._log_attempt(db, invoice_id, entity_id, "", None, None, None, "failed", msg, sent_by)
            return {"success": False, "error": msg}

        final_cc = cc_emails or rendered["cc_emails"]
        final_subject = subject or rendered["subject"]
        body_html = rendered["body_html"]

        # Generate PDF
        pdf_bytes = None
        pdf_filename = None
        if attach_pdf:
            try:
                pdf_bytes = InvoiceTemplateEngine.render_pdf(db, invoice_id, invoice_template_id)
                inv_num = db.execute(text("SELECT invoice_number FROM ar_invoice WHERE id = :id"),
                                     {"id": invoice_id}).scalar()
                pdf_filename = f"Invoice_{(inv_num or invoice_id[:8]).replace('/', '-')}.pdf"
            except Exception as e:
                # PDF gagal bukan blocker — kirim tanpa attachment
                pdf_bytes = None
                pdf_filename = None

        # Ambil SMTP config
        smtp_cfg = InvoiceEmailEngine._get_smtp_config(db, entity_id)

        # Build MIME message
        msg = MIMEMultipart("mixed")
        msg["From"] = formataddr((smtp_cfg["from_name"], smtp_cfg["from_address"]))
        msg["To"] = final_to
        if final_cc:
            msg["Cc"] = ", ".join(final_cc)
        msg["Subject"] = final_subject
        msg["Reply-To"] = smtp_cfg.get("reply_to") or smtp_cfg["from_address"]

        # HTML body
        html_part = MIMEText(body_html, "html", "utf-8")
        msg.attach(html_part)

        # PDF attachment
        if pdf_bytes:
            part = MIMEBase("application", "pdf")
            part.set_payload(pdf_bytes)
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition", f'attachment; filename="{pdf_filename}"'
            )
            msg.attach(part)

        # Send
        try:
            all_recipients = [final_to] + (final_cc or [])
            InvoiceEmailEngine._smtp_send(smtp_cfg, msg, all_recipients)

            # Update ar_invoice: email_sent = true
            db.execute(text("""
                UPDATE ar_invoice
                   SET email_sent = TRUE,
                       email_sent_at = NOW(),
                       email_error = NULL,
                       customer_email = COALESCE(customer_email, :email),
                       email_send_count = email_send_count + 1
                WHERE id = :id
            """), {"id": invoice_id, "email": final_to})
            db.commit()

            log_id = InvoiceEmailEngine._log_attempt(
                db, invoice_id, entity_id, final_to,
                ", ".join(final_cc) if final_cc else None,
                final_subject, body_html, "success", None, sent_by
            )
            return {
                "success": True,
                "to": final_to,
                "cc": final_cc,
                "subject": final_subject,
                "pdf_attached": pdf_bytes is not None,
                "log_id": log_id,
            }

        except Exception as e:
            error_msg = str(e)
            db.execute(text("""
                UPDATE ar_invoice
                   SET email_error = :err, email_send_count = email_send_count + 1
                WHERE id = :id
            """), {"id": invoice_id, "err": error_msg})
            db.commit()

            log_id = InvoiceEmailEngine._log_attempt(
                db, invoice_id, entity_id, final_to,
                ", ".join(final_cc) if final_cc else None,
                final_subject, body_html, "failed", error_msg, sent_by
            )
            return {"success": False, "error": error_msg, "log_id": log_id}

    @staticmethod
    def _smtp_send(smtp_cfg: dict, msg: MIMEMultipart, recipients: list[str]):
        host = smtp_cfg["host"]
        port = smtp_cfg["port"]
        username = smtp_cfg["username"]
        password = smtp_cfg["password"]
        use_tls = smtp_cfg.get("use_tls", True)
        use_ssl = smtp_cfg.get("use_ssl", False)

        if use_ssl:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=ctx) as server:
                if username and password:
                    server.login(username, password)
                server.sendmail(smtp_cfg["from_address"], recipients, msg.as_string())
        else:
            with smtplib.SMTP(host, port) as server:
                server.ehlo()
                if use_tls:
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                if username and password:
                    server.login(username, password)
                server.sendmail(smtp_cfg["from_address"], recipients, msg.as_string())

    @staticmethod
    def _get_smtp_config(db: Session, entity_id: str) -> dict:
        """
        Ambil SMTP config dari entity_email_config, fallback ke .env global.
        """
        row = db.execute(text("""
            SELECT * FROM entity_email_config
            WHERE entity_id = :eid AND is_active = TRUE
        """), {"eid": entity_id}).first()

        if row:
            return {
                "host": row.smtp_host,
                "port": row.smtp_port or 587,
                "username": row.smtp_username,
                "password": row.smtp_password,
                "use_tls": row.use_tls,
                "use_ssl": row.use_ssl,
                "from_name": row.from_name,
                "from_address": row.from_address,
                "reply_to": row.reply_to,
            }

        # Fallback dari environment variables
        host = os.getenv("SMTP_HOST")
        if not host:
            raise ValueError(
                "SMTP belum dikonfigurasi. Isi entity_email_config atau set variabel "
                "SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD di .env"
            )
        return {
            "host": host,
            "port": int(os.getenv("SMTP_PORT", "587")),
            "username": os.getenv("SMTP_USERNAME"),
            "password": os.getenv("SMTP_PASSWORD"),
            "use_tls": os.getenv("SMTP_USE_TLS", "true").lower() == "true",
            "use_ssl": os.getenv("SMTP_USE_SSL", "false").lower() == "true",
            "from_name": os.getenv("EMAIL_FROM_NAME", "Accounting System"),
            "from_address": os.getenv("EMAIL_FROM_ADDRESS", os.getenv("SMTP_USERNAME", "")),
            "reply_to": os.getenv("EMAIL_REPLY_TO"),
        }

    @staticmethod
    def _log_attempt(
        db: Session,
        invoice_id: str,
        entity_id: str,
        sent_to: str,
        sent_cc: Optional[str],
        subject: Optional[str],
        body_html: Optional[str],
        status: str,
        error_message: Optional[str],
        sent_by: str,
    ) -> str:
        try:
            row = db.execute(text("""
                INSERT INTO invoice_email_log
                    (ar_invoice_id, entity_id, sent_to, sent_cc, subject,
                     body_html, status, error_message, sent_by, sent_at)
                VALUES
                    (:inv, :eid, :to, :cc, :subj,
                     :body, :status, :err, :by, NOW())
                RETURNING id
            """), {
                "inv": invoice_id, "eid": entity_id,
                "to": sent_to, "cc": sent_cc, "subj": subject,
                "body": body_html[:10000] if body_html else None,
                "status": status, "err": error_message, "by": sent_by,
            }).first()
            db.commit()
            return str(row.id)
        except Exception:
            return ""

    # ── Test SMTP ─────────────────────────────────────────────────────────────

    @staticmethod
    def test_smtp(
        db: Session,
        entity_id: str,
        test_email: str,
    ) -> dict:
        """
        Kirim test email untuk verifikasi SMTP config.
        """
        smtp_cfg = InvoiceEmailEngine._get_smtp_config(db, entity_id)

        msg = MIMEMultipart()
        msg["From"] = formataddr((smtp_cfg["from_name"], smtp_cfg["from_address"]))
        msg["To"] = test_email
        msg["Subject"] = "Test Email - Accounting System"
        msg.attach(MIMEText(
            "<h2>Test berhasil!</h2>"
            "<p>Konfigurasi SMTP Anda berfungsi dengan baik. "
            "Invoice akan dikirim menggunakan konfigurasi ini.</p>",
            "html", "utf-8"
        ))

        try:
            InvoiceEmailEngine._smtp_send(smtp_cfg, msg, [test_email])
            db.execute(text("""
                UPDATE entity_email_config
                   SET last_test_at = NOW(), last_test_ok = TRUE
                WHERE entity_id = :eid
            """), {"eid": entity_id})
            db.commit()
            return {"success": True, "message": f"Test email berhasil dikirim ke {test_email}"}
        except Exception as e:
            db.execute(text("""
                UPDATE entity_email_config
                   SET last_test_at = NOW(), last_test_ok = FALSE
                WHERE entity_id = :eid
            """), {"eid": entity_id})
            db.commit()
            return {"success": False, "error": str(e)}

    # ── Save SMTP Config ──────────────────────────────────────────────────────

    @staticmethod
    def save_smtp_config(
        db: Session,
        entity_id: str,
        smtp_host: str,
        smtp_port: int,
        smtp_username: str,
        smtp_password: str,
        from_name: str,
        from_address: str,
        use_tls: bool = True,
        use_ssl: bool = False,
        reply_to: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> dict:
        db.execute(text("""
            INSERT INTO entity_email_config
                (entity_id, smtp_host, smtp_port, smtp_username, smtp_password,
                 from_name, from_address, use_tls, use_ssl, reply_to, created_by)
            VALUES
                (:eid, :host, :port, :user, :pwd,
                 :fname, :faddr, :tls, :ssl, :rt, :cb)
            ON CONFLICT (entity_id) DO UPDATE SET
                smtp_host     = EXCLUDED.smtp_host,
                smtp_port     = EXCLUDED.smtp_port,
                smtp_username = EXCLUDED.smtp_username,
                smtp_password = EXCLUDED.smtp_password,
                from_name     = EXCLUDED.from_name,
                from_address  = EXCLUDED.from_address,
                use_tls       = EXCLUDED.use_tls,
                use_ssl       = EXCLUDED.use_ssl,
                reply_to      = EXCLUDED.reply_to,
                is_active     = TRUE
        """), {
            "eid": entity_id, "host": smtp_host, "port": smtp_port,
            "user": smtp_username, "pwd": smtp_password,
            "fname": from_name, "faddr": from_address,
            "tls": use_tls, "ssl": use_ssl, "rt": reply_to, "cb": created_by,
        })
        db.commit()
        return {"entity_id": entity_id, "smtp_host": smtp_host, "from_address": from_address}

    # ── Get Email Log ─────────────────────────────────────────────────────────

    @staticmethod
    def get_email_log(
        db: Session,
        invoice_id: Optional[str] = None,
        entity_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        if invoice_id:
            rows = db.execute(text("""
                SELECT id, sent_to, sent_cc, subject, status,
                       error_message, sent_by, sent_at
                FROM invoice_email_log
                WHERE ar_invoice_id = :inv
                ORDER BY sent_at DESC LIMIT :lim
            """), {"inv": invoice_id, "lim": limit}).fetchall()
        else:
            rows = db.execute(text("""
                SELECT el.id, el.ar_invoice_id, ai.invoice_number,
                       el.sent_to, el.subject, el.status,
                       el.error_message, el.sent_by, el.sent_at
                FROM invoice_email_log el
                JOIN ar_invoice ai ON ai.id = el.ar_invoice_id
                WHERE el.entity_id = :eid
                ORDER BY el.sent_at DESC LIMIT :lim
            """), {"eid": entity_id, "lim": limit}).fetchall()
        return [dict(r._mapping) for r in rows]
