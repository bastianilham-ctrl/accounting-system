"""
Invoice Template Engine
========================
Mengelola template HTML invoice dan rendering ke HTML/PDF.

Fitur:
  - CRUD invoice template (Jinja2 HTML per entity)
  - Default template profesional siap pakai
  - Render invoice ke HTML (untuk preview di browser)
  - Render invoice ke PDF (WeasyPrint utama, xhtml2pdf fallback)
  - Template variables: company, invoice, customer, items, summary, payment

Dependencies:
  pip install jinja2 weasyprint xhtml2pdf

Fix Fontconfig di Windows:
  Set env var FONTCONFIG_FILE ke path fonts.conf di project root.
  Engine ini mengatur otomatis jika file fonts.conf ada di project root.
"""

from __future__ import annotations

import base64
import os

# ── Fix Fontconfig untuk Windows ──────────────────────────────────────────────
# WeasyPrint di Windows sering muncul error "Cannot load default config file"
# karena Fontconfig tidak tahu di mana file konfigurasinya.
# Solusi: tunjukkan ke fonts.conf yang kita buat di project root.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FONTS_CONF = os.path.join(_PROJECT_ROOT, "fonts.conf")
if os.path.isfile(_FONTS_CONF) and not os.environ.get("FONTCONFIG_FILE"):
    os.environ["FONTCONFIG_FILE"] = _FONTS_CONF
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from jinja2 import Environment, BaseLoader, select_autoescape
from sqlalchemy import text
from sqlalchemy.orm import Session


# ── Default Invoice HTML Template ─────────────────────────────────────────────

DEFAULT_INVOICE_TEMPLATE = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Invoice {{ invoice.number }}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: {{ template.font_family or 'Inter, Arial, sans-serif' }};
    font-size: 13px;
    color: #1f2937;
    background: #fff;
    padding: 0;
  }
  .page {
    width: 210mm;
    min-height: 297mm;
    padding: {{ template.margin_top or 20 }}mm {{ template.margin_right or 15 }}mm {{ template.margin_bottom or 20 }}mm {{ template.margin_left or 15 }}mm;
    margin: 0 auto;
    background: #fff;
  }

  /* Header */
  .header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 32px; border-bottom: 3px solid {{ template.primary_color or '#1a56db' }}; padding-bottom: 20px; }
  .company-logo img { max-height: 60px; max-width: 180px; object-fit: contain; }
  .company-logo .company-name-text { font-size: 22px; font-weight: 700; color: {{ template.primary_color or '#1a56db' }}; }
  .invoice-title { text-align: right; }
  .invoice-title h1 { font-size: 28px; font-weight: 700; color: {{ template.primary_color or '#1a56db' }}; letter-spacing: 1px; }
  .invoice-title .invoice-number { font-size: 16px; font-weight: 600; color: #374151; margin-top: 4px; }
  .invoice-title .invoice-status {
    display: inline-block; padding: 3px 10px; border-radius: 12px;
    font-size: 11px; font-weight: 600; letter-spacing: 0.5px; margin-top: 6px;
    {% if invoice.status == 'paid' %}background:#d1fae5;color:#065f46;
    {% elif invoice.status == 'overdue' %}background:#fee2e2;color:#991b1b;
    {% else %}background:#dbeafe;color:#1e40af;{% endif %}
  }

  /* Company & Customer Info */
  .parties { display: flex; justify-content: space-between; margin-bottom: 24px; gap: 20px; }
  .party-block { flex: 1; }
  .party-label { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: #6b7280; margin-bottom: 6px; }
  .party-name { font-size: 15px; font-weight: 600; color: #111827; margin-bottom: 3px; }
  .party-detail { font-size: 12px; color: #4b5563; line-height: 1.6; }
  .party-npwp { font-size: 11px; color: #6b7280; margin-top: 3px; }

  /* Invoice Meta */
  .invoice-meta { background: #f9fafb; border-radius: 8px; padding: 16px; margin-bottom: 24px; display: flex; gap: 24px; flex-wrap: wrap; }
  .meta-item { flex: 1; min-width: 100px; }
  .meta-label { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.8px; color: #9ca3af; margin-bottom: 4px; }
  .meta-value { font-size: 14px; font-weight: 600; color: #111827; }
  .meta-value.overdue { color: #dc2626; }

  /* Items Table */
  .items-section { margin-bottom: 20px; }
  table.items { width: 100%; border-collapse: collapse; }
  table.items thead th {
    background: {{ template.primary_color or '#1a56db' }};
    color: white; padding: 10px 12px;
    font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;
    text-align: left;
  }
  table.items thead th:last-child,
  table.items thead th.qty,
  table.items thead th.price { text-align: right; }
  table.items tbody tr { border-bottom: 1px solid #f3f4f6; }
  table.items tbody tr:hover { background: #f9fafb; }
  table.items tbody td { padding: 9px 12px; font-size: 12.5px; vertical-align: top; }
  table.items tbody td.amount { text-align: right; font-weight: 500; white-space: nowrap; }
  table.items tbody td.qty, table.items tbody td.price { text-align: right; }
  table.items tbody td.no { color: #9ca3af; width: 32px; }
  .item-desc { font-weight: 500; }
  .item-notes { font-size: 11px; color: #6b7280; margin-top: 2px; }

  /* Summary */
  .summary-wrapper { display: flex; justify-content: flex-end; margin-bottom: 20px; }
  .summary-table { min-width: 280px; }
  .summary-row { display: flex; justify-content: space-between; padding: 5px 0; border-bottom: 1px dashed #e5e7eb; }
  .summary-row:last-child { border-bottom: none; }
  .summary-label { color: #6b7280; font-size: 12.5px; }
  .summary-value { font-size: 12.5px; font-weight: 500; text-align: right; padding-left: 20px; }
  .summary-total { display: flex; justify-content: space-between; padding: 10px 14px; background: {{ template.primary_color or '#1a56db' }}; color: white; border-radius: 8px; margin-top: 8px; }
  .summary-total .label { font-size: 13px; font-weight: 600; }
  .summary-total .value { font-size: 16px; font-weight: 700; }

  /* Payment Info */
  .payment-section { border: 1px solid #e5e7eb; border-radius: 8px; padding: 16px; margin-bottom: 20px; background: #fafafa; }
  .payment-title { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: #6b7280; margin-bottom: 10px; }
  .payment-grid { display: flex; gap: 32px; flex-wrap: wrap; }
  .payment-item { }
  .payment-item .label { font-size: 10.5px; color: #9ca3af; }
  .payment-item .value { font-size: 13px; font-weight: 600; color: #111827; }

  /* Notes */
  .notes-section { margin-bottom: 20px; }
  .notes-title { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: #6b7280; margin-bottom: 6px; }
  .notes-body { font-size: 12px; color: #4b5563; line-height: 1.7; }

  /* Footer */
  .footer {
    border-top: 1px solid #e5e7eb; padding-top: 14px;
    text-align: center; font-size: 11px; color: #9ca3af; line-height: 1.6;
    margin-top: auto;
  }
  .footer strong { color: #6b7280; }

  /* Watermark DRAFT */
  {% if invoice.status == 'draft' %}
  .page::before {
    content: 'DRAFT';
    position: fixed; top: 50%; left: 50%;
    transform: translate(-50%, -50%) rotate(-45deg);
    font-size: 120px; font-weight: 900;
    color: rgba(0,0,0,0.04);
    pointer-events: none;
    z-index: 0;
    letter-spacing: 10px;
  }
  {% endif %}

  @media print {
    body { padding: 0; }
    .page { padding: 10mm; width: 100%; }
  }
</style>
</head>
<body>
<div class="page">

  <!-- Header -->
  <div class="header">
    <div class="company-logo">
      {% if template.logo_base64 %}
        <img src="data:image/png;base64,{{ template.logo_base64 }}" alt="{{ company.name }}">
      {% elif template.logo_url %}
        <img src="{{ template.logo_url }}" alt="{{ company.name }}">
      {% else %}
        <div class="company-name-text">{{ company.name }}</div>
      {% endif %}
    </div>
    <div class="invoice-title">
      <h1>INVOICE</h1>
      <div class="invoice-number">{{ invoice.number }}</div>
      <div class="invoice-status">{{ invoice.status | upper }}</div>
    </div>
  </div>

  <!-- Parties -->
  <div class="parties">
    <div class="party-block">
      <div class="party-label">Dari</div>
      <div class="party-name">{{ company.name }}</div>
      <div class="party-detail">
        {{ company.address | replace('\n', '<br>') }}<br>
        {% if company.phone %}Tel: {{ company.phone }}<br>{% endif %}
        {% if company.email %}{{ company.email }}<br>{% endif %}
        {% if company.website %}{{ company.website }}{% endif %}
      </div>
      {% if company.tax_number %}
      <div class="party-npwp">NPWP: {{ company.tax_number }}</div>
      {% endif %}
    </div>

    <div class="party-block">
      <div class="party-label">Kepada Yth.</div>
      <div class="party-name">{{ customer.name }}</div>
      <div class="party-detail">
        {{ customer.address | replace('\n', '<br>') }}<br>
        {% if customer.phone %}Tel: {{ customer.phone }}<br>{% endif %}
        {% if customer.email %}{{ customer.email }}{% endif %}
      </div>
      {% if customer.tax_number %}
      <div class="party-npwp">NPWP: {{ customer.tax_number }}</div>
      {% endif %}
    </div>
  </div>

  <!-- Invoice Meta -->
  <div class="invoice-meta">
    <div class="meta-item">
      <div class="meta-label">No. Invoice</div>
      <div class="meta-value">{{ invoice.number }}</div>
    </div>
    <div class="meta-item">
      <div class="meta-label">Tanggal Invoice</div>
      <div class="meta-value">{{ invoice.date }}</div>
    </div>
    <div class="meta-item">
      <div class="meta-label">Jatuh Tempo</div>
      <div class="meta-value{% if invoice.is_overdue %} overdue{% endif %}">{{ invoice.due_date }}</div>
    </div>
    {% if invoice.po_number %}
    <div class="meta-item">
      <div class="meta-label">No. PO</div>
      <div class="meta-value">{{ invoice.po_number }}</div>
    </div>
    {% endif %}
    {% if invoice.currency != 'IDR' %}
    <div class="meta-item">
      <div class="meta-label">Mata Uang</div>
      <div class="meta-value">{{ invoice.currency }} @ {{ invoice.exchange_rate }}</div>
    </div>
    {% endif %}
  </div>

  <!-- Line Items -->
  <div class="items-section">
    <table class="items">
      <thead>
        <tr>
          <th class="no">#</th>
          <th>Deskripsi</th>
          <th class="qty">Qty</th>
          <th>Sat</th>
          <th class="price">Harga Satuan</th>
          {% if has_discount %}<th class="price">Diskon</th>{% endif %}
          {% if has_tax %}<th>Pajak</th>{% endif %}
          <th class="price">Jumlah</th>
        </tr>
      </thead>
      <tbody>
        {% for item in items %}
        <tr>
          <td class="no">{{ loop.index }}</td>
          <td>
            <div class="item-desc">{{ item.description }}</div>
            {% if item.notes %}<div class="item-notes">{{ item.notes }}</div>{% endif %}
          </td>
          <td class="qty">{{ item.quantity | round(2) }}</td>
          <td>{{ item.uom or '-' }}</td>
          <td class="price">{{ invoice.currency_symbol }}{{ item.unit_price | format_number }}</td>
          {% if has_discount %}
          <td class="price">
            {% if item.discount_pct %}{{ item.discount_pct }}%{% else %}-{% endif %}
          </td>
          {% endif %}
          {% if has_tax %}
          <td>{{ item.tax_code or '-' }}</td>
          {% endif %}
          <td class="amount">{{ invoice.currency_symbol }}{{ item.amount | format_number }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <!-- Summary -->
  <div class="summary-wrapper">
    <div class="summary-table">
      <div class="summary-row">
        <span class="summary-label">Subtotal</span>
        <span class="summary-value">{{ invoice.currency_symbol }}{{ summary.subtotal | format_number }}</span>
      </div>
      {% if summary.discount_total > 0 %}
      <div class="summary-row">
        <span class="summary-label">Diskon</span>
        <span class="summary-value">- {{ invoice.currency_symbol }}{{ summary.discount_total | format_number }}</span>
      </div>
      {% endif %}
      {% if summary.tax_total > 0 %}
      <div class="summary-row">
        <span class="summary-label">PPN ({{ summary.tax_rate }}%)</span>
        <span class="summary-value">{{ invoice.currency_symbol }}{{ summary.tax_total | format_number }}</span>
      </div>
      {% endif %}
      {% if summary.other_charges > 0 %}
      <div class="summary-row">
        <span class="summary-label">Biaya Lain</span>
        <span class="summary-value">{{ invoice.currency_symbol }}{{ summary.other_charges | format_number }}</span>
      </div>
      {% endif %}
      {% if summary.paid_amount > 0 %}
      <div class="summary-row">
        <span class="summary-label">Sudah Dibayar</span>
        <span class="summary-value" style="color:#059669;">- {{ invoice.currency_symbol }}{{ summary.paid_amount | format_number }}</span>
      </div>
      {% endif %}
      <div class="summary-total">
        <span class="label">{% if summary.paid_amount > 0 %}Sisa Tagihan{% else %}Total{% endif %}</span>
        <span class="value">{{ invoice.currency_symbol }}{{ summary.outstanding | format_number }}</span>
      </div>
      {% if invoice.currency != 'IDR' %}
      <div style="text-align:right;margin-top:6px;font-size:11px;color:#9ca3af;">
        Ekuivalen IDR: Rp{{ summary.total_idr | format_number }}
      </div>
      {% endif %}
    </div>
  </div>

  <!-- Payment Info -->
  {% if company.bank_name %}
  <div class="payment-section">
    <div class="payment-title">Informasi Pembayaran</div>
    <div class="payment-grid">
      <div class="payment-item">
        <div class="label">Bank</div>
        <div class="value">{{ company.bank_name }}</div>
      </div>
      <div class="payment-item">
        <div class="label">No. Rekening</div>
        <div class="value">{{ company.bank_account }}</div>
      </div>
      <div class="payment-item">
        <div class="label">Atas Nama</div>
        <div class="value">{{ company.bank_holder }}</div>
      </div>
      {% if company.bank_branch %}
      <div class="payment-item">
        <div class="label">Cabang</div>
        <div class="value">{{ company.bank_branch }}</div>
      </div>
      {% endif %}
      {% if company.swift_code %}
      <div class="payment-item">
        <div class="label">SWIFT</div>
        <div class="value">{{ company.swift_code }}</div>
      </div>
      {% endif %}
    </div>
  </div>
  {% endif %}

  <!-- Notes -->
  {% if notes or template.notes_default %}
  <div class="notes-section">
    <div class="notes-title">Catatan</div>
    <div class="notes-body">{{ notes or template.notes_default }}</div>
  </div>
  {% endif %}

  {% if template.payment_terms_text %}
  <div class="notes-section">
    <div class="notes-title">Syarat Pembayaran</div>
    <div class="notes-body">{{ template.payment_terms_text }}</div>
  </div>
  {% endif %}

  <!-- Footer -->
  <div class="footer">
    {% if template.footer_text %}
      {{ template.footer_text }}<br>
    {% endif %}
    <strong>{{ company.name }}</strong>
    {% if company.tax_number %} · NPWP {{ company.tax_number }}{% endif %}
    {% if company.email %} · {{ company.email }}{% endif %}
    {% if company.phone %} · {{ company.phone }}{% endif %}
  </div>

</div>
</body>
</html>"""


# ── Default Email Template ─────────────────────────────────────────────────────

DEFAULT_EMAIL_BODY = """<!DOCTYPE html>
<html lang="id">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Invoice {{ invoice_number }}</title>
<style>
  body { font-family: Inter, Arial, sans-serif; background: #f3f4f6; margin: 0; padding: 20px; }
  .container { max-width: 600px; margin: 0 auto; background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 8px rgba(0,0,0,0.08); }
  .header { background: {{ primary_color or '#1a56db' }}; padding: 28px 32px; }
  .header h1 { color: #fff; font-size: 20px; font-weight: 700; margin: 0; }
  .header p { color: rgba(255,255,255,0.85); font-size: 13px; margin: 6px 0 0; }
  .body { padding: 28px 32px; }
  .greeting { font-size: 15px; color: #111827; margin-bottom: 16px; }
  .body p { font-size: 14px; color: #4b5563; line-height: 1.7; margin-bottom: 12px; }
  .invoice-box { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; padding: 20px; margin: 20px 0; }
  .invoice-row { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #e5e7eb; }
  .invoice-row:last-child { border-bottom: none; }
  .invoice-row .label { font-size: 13px; color: #6b7280; }
  .invoice-row .value { font-size: 13px; font-weight: 600; color: #111827; }
  .total-row .label { font-size: 14px; font-weight: 600; color: #111827; }
  .total-row .value { font-size: 16px; font-weight: 700; color: {{ primary_color or '#1a56db' }}; }
  .due-badge { display: inline-block; background: {% if is_overdue %}#fee2e2{% else %}#dbeafe{% endif %}; color: {% if is_overdue %}#991b1b{% else %}#1e40af{% endif %}; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; margin-top: 4px; }
  .btn { display: inline-block; background: {{ primary_color or '#1a56db' }}; color: #fff !important; padding: 12px 28px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 14px; margin: 8px 0; }
  .footer { background: #f9fafb; padding: 18px 32px; text-align: center; font-size: 12px; color: #9ca3af; border-top: 1px solid #e5e7eb; }
  .footer a { color: #6b7280; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>{{ company_name }}</h1>
    <p>Invoice untuk {{ customer_name }}</p>
  </div>
  <div class="body">
    <div class="greeting">Yth. {{ customer_name }},</div>
    <p>{{ custom_message or 'Bersama email ini kami sampaikan invoice dengan rincian sebagai berikut:' }}</p>

    <div class="invoice-box">
      <div class="invoice-row">
        <span class="label">No. Invoice</span>
        <span class="value">{{ invoice_number }}</span>
      </div>
      <div class="invoice-row">
        <span class="label">Tanggal Invoice</span>
        <span class="value">{{ invoice_date }}</span>
      </div>
      <div class="invoice-row">
        <span class="label">Jatuh Tempo</span>
        <span class="value">
          {{ due_date }}
          <br><span class="due-badge">
            {% if is_overdue %}Terlambat {{ overdue_days }} hari
            {% elif days_until_due == 0 %}Jatuh tempo hari ini
            {% else %}{{ days_until_due }} hari lagi{% endif %}
          </span>
        </span>
      </div>
      <div class="invoice-row total-row">
        <span class="label">Total Tagihan</span>
        <span class="value">{{ currency_symbol }}{{ total_amount }}</span>
      </div>
    </div>

    <p>Invoice terlampir dalam format PDF. Mohon lakukan pembayaran sebelum tanggal jatuh tempo.</p>

    {% if bank_name %}
    <p><strong>Pembayaran ke:</strong><br>
    Bank {{ bank_name }} · Rekening {{ bank_account }} · a.n. {{ bank_holder }}</p>
    {% endif %}

    <p>{{ closing_message or 'Jika ada pertanyaan, jangan ragu untuk menghubungi kami.' }}</p>

    <p style="margin-top: 24px;">Hormat kami,<br>
    <strong>{{ sender_name or company_name }}</strong><br>
    {{ sender_title or '' }}<br>
    {% if sender_email %}<a href="mailto:{{ sender_email }}">{{ sender_email }}</a>{% endif %}
    {% if sender_phone %} · {{ sender_phone }}{% endif %}
    </p>
  </div>
  <div class="footer">
    &copy; {{ company_name }}{% if company_email %} · <a href="mailto:{{ company_email }}">{{ company_email }}</a>{% endif %}
    {% if company_phone %} · {{ company_phone }}{% endif %}
    <br>Email ini dikirim otomatis dari sistem akuntansi.
  </div>
</div>
</body>
</html>"""

DEFAULT_EMAIL_SUBJECT = "Invoice {{ invoice_number }} - {{ company_name }} (Jatuh Tempo: {{ due_date }})"


# ── Jinja2 Setup ──────────────────────────────────────────────────────────────

def _make_jinja_env() -> Environment:
    env = Environment(loader=BaseLoader(), autoescape=select_autoescape(["html"]))

    def format_number(value):
        try:
            v = float(value)
            if v == int(v):
                return f"{int(v):,}".replace(",", ".")
            return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except (TypeError, ValueError):
            return str(value)

    env.filters["format_number"] = format_number
    return env


_jinja = _make_jinja_env()


class InvoiceTemplateEngine:

    # ── Template CRUD ─────────────────────────────────────────────────────────

    @staticmethod
    def create_template(
        db: Session,
        entity_id: str,
        template_name: str,
        is_default: bool = False,
        template_html: Optional[str] = None,
        logo_base64: Optional[str] = None,
        logo_url: Optional[str] = None,
        primary_color: str = "#1a56db",
        secondary_color: str = "#374151",
        font_family: str = "Inter, Arial, sans-serif",
        company_display_name: Optional[str] = None,
        company_address: Optional[str] = None,
        company_phone: Optional[str] = None,
        company_email: Optional[str] = None,
        company_website: Optional[str] = None,
        company_tax_number: Optional[str] = None,
        company_bank_name: Optional[str] = None,
        company_bank_account: Optional[str] = None,
        company_bank_holder: Optional[str] = None,
        company_bank_branch: Optional[str] = None,
        footer_text: Optional[str] = None,
        payment_terms_text: Optional[str] = None,
        notes_default: Optional[str] = None,
        paper_size: str = "A4",
        created_by: Optional[str] = None,
    ) -> dict:
        html = template_html or DEFAULT_INVOICE_TEMPLATE

        if is_default:
            # Lepas default flag dari template lain dulu
            db.execute(text("""
                UPDATE invoice_template SET is_default = FALSE
                WHERE entity_id = :eid AND is_default = TRUE
            """), {"eid": entity_id})

        row = db.execute(text("""
            INSERT INTO invoice_template
                (entity_id, template_name, is_default, template_html,
                 logo_base64, logo_url, primary_color, secondary_color, font_family,
                 company_display_name, company_address, company_phone, company_email,
                 company_website, company_tax_number,
                 company_bank_name, company_bank_account, company_bank_holder, company_bank_branch,
                 footer_text, payment_terms_text, notes_default,
                 paper_size, created_by)
            VALUES
                (:eid, :name, :def, :html,
                 :logo, :logo_url, :pc, :sc, :ff,
                 :cdn, :ca, :cp, :ce, :cw, :ctn,
                 :cbn, :cba, :cbh, :cbb,
                 :ft, :pt, :nd, :ps, :cb)
            RETURNING id
        """), {
            "eid": entity_id, "name": template_name, "def": is_default, "html": html,
            "logo": logo_base64, "logo_url": logo_url, "pc": primary_color,
            "sc": secondary_color, "ff": font_family,
            "cdn": company_display_name, "ca": company_address, "cp": company_phone,
            "ce": company_email, "cw": company_website, "ctn": company_tax_number,
            "cbn": company_bank_name, "cba": company_bank_account,
            "cbh": company_bank_holder, "cbb": company_bank_branch,
            "ft": footer_text, "pt": payment_terms_text, "nd": notes_default,
            "ps": paper_size, "cb": created_by,
        }).first()
        db.commit()
        return {"id": str(row.id), "template_name": template_name, "is_default": is_default}

    @staticmethod
    def update_template(db: Session, template_id: str, entity_id: str, **kwargs) -> dict:
        allowed = {
            "template_name", "is_default", "template_html",
            "logo_base64", "logo_url", "primary_color", "secondary_color", "font_family",
            "company_display_name", "company_address", "company_phone", "company_email",
            "company_website", "company_tax_number",
            "company_bank_name", "company_bank_account", "company_bank_holder", "company_bank_branch",
            "footer_text", "payment_terms_text", "notes_default", "paper_size", "is_active",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            raise ValueError("Tidak ada field yang diupdate.")

        if updates.get("is_default"):
            db.execute(text("""
                UPDATE invoice_template SET is_default = FALSE
                WHERE entity_id = :eid AND is_default = TRUE AND id != :id
            """), {"eid": entity_id, "id": template_id})

        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        updates["id"] = template_id
        updates["eid"] = entity_id
        db.execute(text(f"""
            UPDATE invoice_template
               SET {set_clause}, updated_at = NOW()
            WHERE id = :id AND entity_id = :eid
        """), updates)
        db.commit()
        return {"id": template_id, "updated_fields": list(updates.keys())}

    @staticmethod
    def list_templates(db: Session, entity_id: str) -> list[dict]:
        rows = db.execute(text("""
            SELECT id, template_name, is_default, is_active,
                   primary_color, paper_size, created_at, updated_at
            FROM invoice_template
            WHERE entity_id = :eid AND is_active = TRUE
            ORDER BY is_default DESC, template_name
        """), {"eid": entity_id}).fetchall()
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def get_template(db: Session, template_id: str) -> dict:
        row = db.execute(text("""
            SELECT * FROM invoice_template WHERE id = :id
        """), {"id": template_id}).first()
        if row is None:
            raise ValueError("Template tidak ditemukan.")
        return dict(row._mapping)

    @staticmethod
    def get_default_template(db: Session, entity_id: str) -> Optional[dict]:
        row = db.execute(text("""
            SELECT * FROM invoice_template
            WHERE entity_id = :eid AND is_default = TRUE AND is_active = TRUE
        """), {"eid": entity_id}).first()
        if row is None:
            # Fallback ke template pertama yang ada
            row = db.execute(text("""
                SELECT * FROM invoice_template
                WHERE entity_id = :eid AND is_active = TRUE
                ORDER BY created_at LIMIT 1
            """), {"eid": entity_id}).first()
        return dict(row._mapping) if row else None

    @staticmethod
    def upload_logo(
        db: Session,
        template_id: str,
        entity_id: str,
        image_bytes: bytes,
        mime_type: str = "image/png",
    ) -> dict:
        """Upload logo dan simpan sebagai base64 di template."""
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        prefix = f"data:{mime_type};base64,"
        full_b64 = b64  # Jinja template sudah tambahkan prefix data:image/png;base64,

        db.execute(text("""
            UPDATE invoice_template SET logo_base64 = :logo, updated_at = NOW()
            WHERE id = :id AND entity_id = :eid
        """), {"logo": full_b64, "id": template_id, "eid": entity_id})
        db.commit()
        return {"id": template_id, "logo_uploaded": True, "size_bytes": len(image_bytes)}

    # ── Render Invoice ─────────────────────────────────────────────────────────

    @staticmethod
    def build_context(db: Session, invoice_id: str, template: dict) -> dict:
        """
        Bangun context lengkap untuk rendering template dari data invoice.
        """
        inv = db.execute(text("""
            SELECT
                ai.*,
                c.customer_name, c.address AS customer_address,
                c.phone AS customer_phone, c.email AS customer_email_master,
                c.tax_number AS customer_tax_number,
                e.entity_name
            FROM ar_invoice ai
            JOIN customer c  ON c.id = ai.customer_id
            JOIN entity e    ON e.id = ai.entity_id
            WHERE ai.id = :id
        """), {"id": invoice_id}).first()

        if inv is None:
            raise ValueError("Invoice tidak ditemukan.")

        lines = db.execute(text("""
            SELECT
                ail.description, ail.notes,
                ail.quantity, ail.uom,
                ail.unit_price, ail.discount_pct, ail.discount_amount,
                ail.tax_code, ail.tax_rate, ail.tax_amount,
                ail.amount_before_tax, ail.amount
            FROM ar_invoice_line ail
            WHERE ail.invoice_id = :id
            ORDER BY ail.line_number
        """), {"id": invoice_id}).fetchall()

        from decimal import Decimal
        from datetime import date as _date

        def _d(v):
            return Decimal(str(v)) if v else Decimal("0")

        subtotal = sum(_d(l.amount_before_tax) for l in lines)
        discount_total = sum(_d(l.discount_amount or 0) for l in lines)
        tax_total = _d(inv.tax_amount)
        total = _d(inv.total_amount)
        paid = _d(getattr(inv, "paid_amount", 0))
        outstanding = total - paid

        today = _date.today()
        due = inv.due_date
        is_overdue = due < today if due else False
        days_until_due = (due - today).days if due else 0

        currency = getattr(inv, "currency", "IDR") or "IDR"
        currency_symbol = "Rp" if currency == "IDR" else currency + " "

        has_discount = any(l.discount_pct or l.discount_amount for l in lines)
        has_tax = any(l.tax_amount for l in lines)

        # Tax rate (ambil dari line pertama yang ada tax)
        tax_rate = next((l.tax_rate for l in lines if l.tax_rate), 11)

        context = {
            "template": {
                "logo_base64": template.get("logo_base64"),
                "logo_url": template.get("logo_url"),
                "primary_color": template.get("primary_color", "#1a56db"),
                "secondary_color": template.get("secondary_color", "#374151"),
                "font_family": template.get("font_family", "Inter, Arial, sans-serif"),
                "footer_text": template.get("footer_text"),
                "payment_terms_text": template.get("payment_terms_text"),
                "notes_default": template.get("notes_default"),
                "margin_top": template.get("margin_top", 20),
                "margin_bottom": template.get("margin_bottom", 20),
                "margin_left": template.get("margin_left", 15),
                "margin_right": template.get("margin_right", 15),
            },
            "company": {
                "name": template.get("company_display_name") or inv.entity_name,
                "address": template.get("company_address", ""),
                "phone": template.get("company_phone"),
                "email": template.get("company_email"),
                "website": template.get("company_website"),
                "tax_number": template.get("company_tax_number"),
                "bank_name": template.get("company_bank_name"),
                "bank_account": template.get("company_bank_account"),
                "bank_holder": template.get("company_bank_holder"),
                "bank_branch": template.get("company_bank_branch"),
                "swift_code": template.get("company_swift_code"),
            },
            "invoice": {
                "number": inv.invoice_number,
                "date": inv.invoice_date.strftime("%d %B %Y") if inv.invoice_date else "-",
                "due_date": inv.due_date.strftime("%d %B %Y") if inv.due_date else "-",
                "status": inv.status,
                "po_number": getattr(inv, "po_number", None),
                "currency": currency,
                "currency_symbol": currency_symbol,
                "exchange_rate": getattr(inv, "exchange_rate", 1),
                "is_overdue": is_overdue,
                "days_until_due": days_until_due,
            },
            "customer": {
                "name": inv.customer_name,
                "address": inv.customer_address or "",
                "phone": inv.customer_phone,
                "email": inv.customer_email or getattr(inv, "customer_email_master", None),
                "tax_number": inv.customer_tax_number,
            },
            "items": [
                {
                    "description": l.description,
                    "notes": l.notes,
                    "quantity": float(l.quantity),
                    "uom": l.uom,
                    "unit_price": float(l.unit_price),
                    "discount_pct": float(l.discount_pct) if l.discount_pct else None,
                    "discount_amount": float(l.discount_amount) if l.discount_amount else None,
                    "tax_code": l.tax_code,
                    "tax_rate": float(l.tax_rate) if l.tax_rate else None,
                    "tax_amount": float(l.tax_amount) if l.tax_amount else 0,
                    "amount": float(l.amount),
                }
                for l in lines
            ],
            "summary": {
                "subtotal": float(subtotal),
                "discount_total": float(discount_total),
                "tax_total": float(tax_total),
                "tax_rate": float(tax_rate) if tax_rate else 0,
                "other_charges": 0,
                "total": float(total),
                "paid_amount": float(paid),
                "outstanding": float(outstanding),
                "total_idr": float(total * _d(getattr(inv, "exchange_rate", 1))),
            },
            "notes": getattr(inv, "notes", None),
            "has_discount": has_discount,
            "has_tax": has_tax,
        }
        return context

    @staticmethod
    def render_html(db: Session, invoice_id: str, template_id: Optional[str] = None) -> str:
        """
        Render invoice ke HTML.
        Jika template_id tidak diberikan, gunakan template default entity.
        """
        # Ambil entity_id dari invoice
        inv_row = db.execute(text("""
            SELECT entity_id, invoice_template_id FROM ar_invoice WHERE id = :id
        """), {"id": invoice_id}).first()
        if inv_row is None:
            raise ValueError("Invoice tidak ditemukan.")

        tid = template_id or inv_row.invoice_template_id
        entity_id = str(inv_row.entity_id)

        if tid:
            template = InvoiceTemplateEngine.get_template(db, str(tid))
        else:
            template = InvoiceTemplateEngine.get_default_template(db, entity_id)

        if template is None:
            # Gunakan built-in default
            template = {
                "template_html": DEFAULT_INVOICE_TEMPLATE,
                "primary_color": "#1a56db",
                "font_family": "Inter, Arial, sans-serif",
            }

        context = InvoiceTemplateEngine.build_context(db, invoice_id, template)
        tmpl = _jinja.from_string(template["template_html"])
        return tmpl.render(**context)

    @staticmethod
    def render_pdf(db: Session, invoice_id: str, template_id: Optional[str] = None) -> bytes:
        """
        Render invoice ke PDF bytes.
        Urutan coba:
          1. WeasyPrint  (kualitas terbaik, perlu GTK di Windows)
          2. xhtml2pdf   (fallback, pure Python, install: pip install xhtml2pdf)
        Jika keduanya tidak tersedia → raise RuntimeError.
        """
        html_content = InvoiceTemplateEngine.render_html(db, invoice_id, template_id)
        return _html_to_pdf(html_content)


def _html_to_pdf(html_content: str) -> bytes:
    """
    Konversi HTML ke PDF. Coba WeasyPrint dulu, fallback ke xhtml2pdf.
    Diletakkan di level module agar bisa dipanggil tanpa instance.
    """
    # ── Coba WeasyPrint ────────────────────────────────────────────────────────
    try:
        import logging
        # Suppress Fontconfig warning di log WeasyPrint (sudah kita fix via env var)
        logging.getLogger("weasyprint").setLevel(logging.ERROR)
        logging.getLogger("fontTools").setLevel(logging.ERROR)

        from weasyprint import HTML as WeasyprintHTML
        pdf_bytes = WeasyprintHTML(string=html_content).write_pdf()
        return pdf_bytes

    except ImportError:
        pass  # WeasyPrint tidak ada, coba fallback
    except Exception as wp_err:
        # WeasyPrint ada tapi error (mis. font issue) → coba fallback
        _fallback_err = str(wp_err)
    else:
        _fallback_err = None

    # ── Fallback: xhtml2pdf ────────────────────────────────────────────────────
    try:
        import io
        from xhtml2pdf import pisa

        result_buffer = io.BytesIO()
        pisa_status = pisa.CreatePDF(
            html_content.encode("utf-8"),
            dest=result_buffer,
            encoding="utf-8",
        )
        if pisa_status.err:
            raise RuntimeError(f"xhtml2pdf error: {pisa_status.err}")
        return result_buffer.getvalue()

    except ImportError:
        raise RuntimeError(
            "Tidak ada library PDF yang tersedia.\n"
            "Install WeasyPrint: pip install weasyprint\n"
            "Atau install xhtml2pdf (fallback): pip install xhtml2pdf\n\n"
            "Untuk fix Fontconfig di Windows:\n"
            "  Set env: FONTCONFIG_FILE=C:\\path\\to\\accounting_system\\fonts.conf\n"
            "  Atau jalankan ulang server setelah menambahkan fonts.conf di project root."
        )

    # ── Email Template CRUD ───────────────────────────────────────────────────

    @staticmethod
    def create_email_template(
        db: Session,
        entity_id: str,
        template_name: str,
        subject_template: str,
        body_template: str,
        is_default: bool = False,
        reply_to: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> dict:
        if is_default:
            db.execute(text("""
                UPDATE invoice_email_template SET is_default = FALSE
                WHERE entity_id = :eid AND is_default = TRUE
            """), {"eid": entity_id})

        row = db.execute(text("""
            INSERT INTO invoice_email_template
                (entity_id, template_name, is_default, subject_template, body_template,
                 reply_to, created_by)
            VALUES (:eid, :name, :def, :subj, :body, :rt, :cb)
            RETURNING id
        """), {
            "eid": entity_id, "name": template_name, "def": is_default,
            "subj": subject_template, "body": body_template,
            "rt": reply_to, "cb": created_by,
        }).first()
        db.commit()
        return {"id": str(row.id), "template_name": template_name}

    @staticmethod
    def get_default_email_template(db: Session, entity_id: str) -> dict:
        row = db.execute(text("""
            SELECT * FROM invoice_email_template
            WHERE entity_id = :eid AND is_default = TRUE AND is_active = TRUE
        """), {"eid": entity_id}).first()
        if row is None:
            row = db.execute(text("""
                SELECT * FROM invoice_email_template
                WHERE entity_id = :eid AND is_active = TRUE
                ORDER BY created_at LIMIT 1
            """), {"eid": entity_id}).first()
        if row:
            return dict(row._mapping)
        # Return default bawaan sistem
        return {
            "subject_template": DEFAULT_EMAIL_SUBJECT,
            "body_template": DEFAULT_EMAIL_BODY,
        }

    @staticmethod
    def list_email_templates(db: Session, entity_id: str) -> list[dict]:
        rows = db.execute(text("""
            SELECT id, template_name, is_default, is_active, subject_template,
                   created_at, updated_at
            FROM invoice_email_template
            WHERE entity_id = :eid AND is_active = TRUE
            ORDER BY is_default DESC, template_name
        """), {"eid": entity_id}).fetchall()
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def update_email_template(db: Session, template_id: str, entity_id: str, **kwargs) -> dict:
        allowed = {"template_name", "is_default", "subject_template", "body_template",
                   "reply_to", "is_active"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            raise ValueError("Tidak ada field yang diupdate.")

        if updates.get("is_default"):
            db.execute(text("""
                UPDATE invoice_email_template SET is_default = FALSE
                WHERE entity_id = :eid AND is_default = TRUE AND id != :id
            """), {"eid": entity_id, "id": template_id})

        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        updates["id"] = template_id
        updates["eid"] = entity_id
        db.execute(text(f"""
            UPDATE invoice_email_template
               SET {set_clause}, updated_at = NOW()
            WHERE id = :id AND entity_id = :eid
        """), updates)
        db.commit()
        return {"id": template_id, "updated_fields": list(updates.keys())}

    @staticmethod
    def render_email(
        db: Session,
        invoice_id: str,
        email_template_id: Optional[str] = None,
        custom_message: Optional[str] = None,
        sender_name: Optional[str] = None,
        sender_title: Optional[str] = None,
        sender_email: Optional[str] = None,
        sender_phone: Optional[str] = None,
    ) -> dict:
        """
        Render email subject dan body untuk invoice tertentu.
        Return: {subject, body_html, to_email, cc_emails}
        """
        inv = db.execute(text("""
            SELECT ai.*,
                   c.customer_name, c.email AS customer_email_master,
                   e.entity_name,
                   ai.customer_email AS invoice_email,
                   ai.email_cc,
                   ai.email_template_id AS inv_email_tmpl
            FROM ar_invoice ai
            JOIN customer c ON c.id = ai.customer_id
            JOIN entity e   ON e.id = ai.entity_id
            WHERE ai.id = :id
        """), {"id": invoice_id}).first()

        if inv is None:
            raise ValueError("Invoice tidak ditemukan.")

        entity_id = str(inv.entity_id)
        tid = email_template_id or inv.inv_email_tmpl

        if tid:
            tmpl_row = db.execute(text("""
                SELECT * FROM invoice_email_template WHERE id = :id
            """), {"id": str(tid)}).first()
            email_tmpl = dict(tmpl_row._mapping) if tmpl_row else InvoiceTemplateEngine.get_default_email_template(db, entity_id)
        else:
            email_tmpl = InvoiceTemplateEngine.get_default_email_template(db, entity_id)

        # Ambil template invoice untuk dapat primary_color
        inv_tmpl = InvoiceTemplateEngine.get_default_template(db, entity_id) or {}

        from datetime import date as _date
        from decimal import Decimal

        def _d(v):
            return Decimal(str(v)) if v else Decimal("0")

        today = _date.today()
        due = inv.due_date
        is_overdue = due < today if due else False
        days_until_due = (due - today).days if due else 0
        overdue_days = (today - due).days if (due and is_overdue) else 0

        total = _d(inv.total_amount)
        currency = getattr(inv, "currency", "IDR") or "IDR"
        currency_symbol = "Rp" if currency == "IDR" else currency + " "

        def fmt(v):
            try:
                n = float(v)
                if n == int(n):
                    return f"{int(n):,}".replace(",", ".")
                return f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            except Exception:
                return str(v)

        ctx = {
            "customer_name": inv.customer_name,
            "invoice_number": inv.invoice_number,
            "invoice_date": inv.invoice_date.strftime("%d %B %Y") if inv.invoice_date else "-",
            "due_date": inv.due_date.strftime("%d %B %Y") if inv.due_date else "-",
            "total_amount": fmt(total),
            "currency": currency,
            "currency_symbol": currency_symbol,
            "company_name": inv.entity_name,
            "company_email": inv_tmpl.get("company_email"),
            "company_phone": inv_tmpl.get("company_phone"),
            "bank_name": inv_tmpl.get("company_bank_name"),
            "bank_account": inv_tmpl.get("company_bank_account"),
            "bank_holder": inv_tmpl.get("company_bank_holder"),
            "is_overdue": is_overdue,
            "days_until_due": max(days_until_due, 0),
            "overdue_days": overdue_days,
            "primary_color": inv_tmpl.get("primary_color", "#1a56db"),
            "custom_message": custom_message,
            "sender_name": sender_name,
            "sender_title": sender_title,
            "sender_email": sender_email,
            "sender_phone": sender_phone,
        }

        subject = _jinja.from_string(email_tmpl["subject_template"]).render(**ctx)
        body_html = _jinja.from_string(email_tmpl["body_template"]).render(**ctx)

        to_email = inv.invoice_email or inv.customer_email_master
        cc_emails = [e.strip() for e in (inv.email_cc or "").split(",") if e.strip()]

        return {
            "subject": subject,
            "body_html": body_html,
            "to_email": to_email,
            "cc_emails": cc_emails,
        }
