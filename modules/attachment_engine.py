"""
File Attachment Engine
Handles upload, deduplication, polymorphic linking, download, and deletion.

Storage layout:
  {UPLOAD_ROOT}/attachments/{entity_id[:8]}/{YYYY-MM}/{uuid}_{clean_filename}

Deduplication:
  SHA-256 hash per entity. File identik (hash sama) hanya tersimpan sekali;
  upload ulang hanya menambah link baru ke record yang sudah ada.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

# ── Config ─────────────────────────────────────────────────────────────────────
UPLOAD_ROOT     = Path(os.getenv("UPLOAD_ROOT", "uploads"))
MAX_FILE_SIZE   = int(os.getenv("MAX_ATTACHMENT_SIZE_MB", "50")) * 1024 * 1024  # default 50 MB

ALLOWED_MIME_PREFIXES = {
    "image/",
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/zip",
    "text/plain",
    "text/csv",
}

CATEGORY_BY_MIME: dict[str, str] = {
    "application/pdf":          "receipt",
    "image/jpeg":               "photo",
    "image/png":                "photo",
    "image/gif":                "photo",
    "application/msword":       "other",
    "text/csv":                 "spreadsheet",
    "application/vnd.ms-excel": "spreadsheet",
}

VALID_REF_TYPES = {
    "ar_invoice", "ap_invoice", "expense_claim", "contract", "project",
    "project_task", "project_milestone",
    "vendor", "employee", "journal", "quotation", "sales_order",
    "bank_statement", "wht_transaction", "purchase_order",
    "delivery_order", "leave_request", "asset", "payroll", "other",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _clean_filename(name: str) -> str:
    """Remove special chars, keep extension."""
    name = name.replace(" ", "_")
    name = re.sub(r"[^\w.\-]", "", name)
    return name[:200]


def _compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _detect_mime(filename: str, data: bytes) -> str:
    mime, _ = mimetypes.guess_type(filename)
    if mime:
        return mime
    # Sniff first bytes
    if data[:4] == b"%PDF":
        return "application/pdf"
    if data[:2] in (b"\xff\xd8", b"\x89P"):  # JPEG / PNG
        return "image/jpeg" if data[:2] == b"\xff\xd8" else "image/png"
    return "application/octet-stream"


def _is_allowed_mime(mime: str) -> bool:
    for prefix in ALLOWED_MIME_PREFIXES:
        if mime.startswith(prefix):
            return True
    return False


def _infer_category(mime: str, original_name: str) -> str:
    if mime in CATEGORY_BY_MIME:
        return CATEGORY_BY_MIME[mime]
    name_lower = original_name.lower()
    if any(k in name_lower for k in ("receipt", "kwitansi", "nota")):
        return "receipt"
    if any(k in name_lower for k in ("invoice", "faktur")):
        return "invoice"
    if any(k in name_lower for k in ("contract", "kontrak", "perjanjian", "spk")):
        return "contract"
    if any(k in name_lower for k in ("po", "purchase", "order")):
        return "po"
    if any(k in name_lower for k in ("do", "delivery", "pengiriman")):
        return "do"
    if mime.startswith("image/"):
        return "photo"
    if "spreadsheet" in mime or "excel" in mime or name_lower.endswith((".xlsx", ".xls", ".csv")):
        return "spreadsheet"
    if "presentation" in mime or name_lower.endswith((".pptx", ".ppt")):
        return "presentation"
    return "other"


def _build_storage_path(entity_id: UUID, stored_name: str) -> Path:
    eid_prefix = str(entity_id)[:8]
    period     = datetime.now().strftime("%Y-%m")
    rel        = Path("attachments") / eid_prefix / period / stored_name
    return rel


# ── Engine ─────────────────────────────────────────────────────────────────────
class AttachmentEngine:

    # ── Upload ────────────────────────────────────────────────────────────────

    @staticmethod
    def upload(
        db: Session,
        entity_id: UUID,
        file_data: bytes,
        original_name: str,
        uploaded_by: str = "",
        description: Optional[str] = None,
        category: Optional[str] = None,
        # optional: immediately link on upload
        ref_type: Optional[str] = None,
        ref_id: Optional[UUID] = None,
        link_notes: Optional[str] = None,
    ) -> dict:
        # Validate size
        if len(file_data) > MAX_FILE_SIZE:
            raise ValueError(
                f"File terlalu besar: {len(file_data) // 1048576} MB. "
                f"Maksimum {MAX_FILE_SIZE // 1048576} MB."
            )

        # Detect MIME & validate
        mime = _detect_mime(original_name, file_data)
        if not _is_allowed_mime(mime):
            raise ValueError(f"Tipe file tidak diizinkan: {mime}")

        # SHA-256 dedup within entity
        sha256 = _compute_sha256(file_data)
        existing = db.execute(
            text("SELECT id, file_path FROM attachment WHERE entity_id=:eid AND sha256_hash=:h AND is_deleted=FALSE"),
            {"eid": str(entity_id), "h": sha256},
        ).fetchone()

        if existing:
            # File identical — just add link if requested
            attachment_id = existing.id
            created_new   = False
        else:
            # Save file to disk
            ext        = Path(original_name).suffix.lower()
            clean_name = _clean_filename(Path(original_name).stem)
            stored     = f"{uuid4().hex}_{clean_name}{ext}"
            rel_path   = _build_storage_path(entity_id, stored)
            abs_path   = UPLOAD_ROOT / rel_path

            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_bytes(file_data)

            inferred_cat = category or _infer_category(mime, original_name)

            row = db.execute(
                text("""
                    INSERT INTO attachment
                        (entity_id, original_name, stored_name, file_path, file_size,
                         mime_type, file_extension, sha256_hash, category, description,
                         uploaded_by)
                    VALUES (:eid, :orig, :stored, :path, :size,
                            :mime, :ext, :hash, :cat, :desc, :by)
                    RETURNING id
                """),
                {
                    "eid":    str(entity_id),
                    "orig":   original_name[:500],
                    "stored": stored,
                    "path":   str(rel_path),
                    "size":   len(file_data),
                    "mime":   mime,
                    "ext":    ext or None,
                    "hash":   sha256,
                    "cat":    inferred_cat,
                    "desc":   description,
                    "by":     uploaded_by,
                },
            ).fetchone()
            attachment_id = row.id
            created_new   = True

        # Link if ref provided
        link_id = None
        if ref_type and ref_id:
            link_id = AttachmentEngine._create_link(
                db, attachment_id, ref_type, ref_id, uploaded_by, link_notes
            )

        db.commit()

        return {
            "attachment_id": str(attachment_id),
            "original_name": original_name,
            "file_size":     len(file_data),
            "mime_type":     mime,
            "sha256":        sha256,
            "deduplicated":  not created_new,
            "link_id":       str(link_id) if link_id else None,
        }

    # ── Link to Entity ────────────────────────────────────────────────────────

    @staticmethod
    def link_to_entity(
        db: Session,
        attachment_id: UUID,
        ref_type: str,
        ref_id: UUID,
        linked_by: str = "",
        notes: Optional[str] = None,
    ) -> dict:
        if ref_type not in VALID_REF_TYPES:
            raise ValueError(f"ref_type tidak valid: {ref_type}")

        att = db.execute(
            text("SELECT id FROM attachment WHERE id=:aid AND is_deleted=FALSE"),
            {"aid": str(attachment_id)},
        ).fetchone()
        if not att:
            raise ValueError("Attachment tidak ditemukan.")

        link_id = AttachmentEngine._create_link(db, attachment_id, ref_type, ref_id, linked_by, notes)
        db.commit()
        return {"link_id": str(link_id), "attachment_id": str(attachment_id), "ref_type": ref_type, "ref_id": str(ref_id)}

    @staticmethod
    def _create_link(
        db: Session,
        attachment_id,
        ref_type: str,
        ref_id: UUID,
        created_by: str,
        notes: Optional[str],
    ) -> UUID:
        existing = db.execute(
            text("""
                SELECT id FROM attachment_link
                WHERE attachment_id=:aid AND ref_type=:rt AND ref_id=:rid
            """),
            {"aid": str(attachment_id), "rt": ref_type, "rid": str(ref_id)},
        ).fetchone()
        if existing:
            return existing.id

        row = db.execute(
            text("""
                INSERT INTO attachment_link (attachment_id, ref_type, ref_id, notes, created_by)
                VALUES (:aid, :rt, :rid, :notes, :by)
                RETURNING id
            """),
            {
                "aid":   str(attachment_id),
                "rt":    ref_type,
                "rid":   str(ref_id),
                "notes": notes,
                "by":    created_by,
            },
        ).fetchone()
        return row.id

    # ── Unlink ────────────────────────────────────────────────────────────────

    @staticmethod
    def unlink_from_entity(
        db: Session,
        attachment_id: UUID,
        ref_type: str,
        ref_id: UUID,
    ) -> dict:
        result = db.execute(
            text("""
                DELETE FROM attachment_link
                WHERE attachment_id=:aid AND ref_type=:rt AND ref_id=:rid
            """),
            {"aid": str(attachment_id), "rt": ref_type, "rid": str(ref_id)},
        )
        db.commit()
        if result.rowcount == 0:
            raise ValueError("Link tidak ditemukan.")
        return {"unlinked": True, "attachment_id": str(attachment_id)}

    # ── List by Entity ────────────────────────────────────────────────────────

    @staticmethod
    def list_by_entity(
        db: Session,
        ref_type: str,
        ref_id: UUID,
    ) -> list[dict]:
        rows = db.execute(
            text("""
                SELECT attachment_id, ref_type, ref_id, link_notes, linked_by, linked_at,
                       entity_id, original_name, file_size, mime_type, file_extension,
                       category, description, file_path, uploaded_by, uploaded_at
                FROM vw_entity_attachments
                WHERE ref_type=:rt AND ref_id=:rid
                ORDER BY uploaded_at DESC
            """),
            {"rt": ref_type, "rid": str(ref_id)},
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    # ── List by Entity (all attachments for entity_id) ────────────────────────

    @staticmethod
    def list_by_company(
        db: Session,
        entity_id: UUID,
        category: Optional[str] = None,
        ref_type: Optional[str] = None,
        page: int = 1,
        size: int = 50,
    ) -> dict:
        filters = ["a.entity_id=:eid", "a.is_deleted=FALSE"]
        params: dict = {"eid": str(entity_id)}

        if category:
            filters.append("a.category=:cat")
            params["cat"] = category

        join = ""
        if ref_type:
            join = "JOIN attachment_link al2 ON al2.attachment_id=a.id AND al2.ref_type=:rt"
            params["rt"] = ref_type

        count_row = db.execute(
            text(f"""
                SELECT COUNT(DISTINCT a.id) AS cnt
                FROM attachment a {join}
                WHERE {' AND '.join(filters)}
            """),
            params,
        ).fetchone()
        total = count_row.cnt if count_row else 0

        params["limit"]  = size
        params["offset"] = (page - 1) * size

        rows = db.execute(
            text(f"""
                SELECT DISTINCT ON (a.id)
                    a.id, a.original_name, a.file_size, a.mime_type,
                    a.file_extension, a.category, a.description,
                    a.sha256_hash, a.uploaded_by, a.created_at,
                    (SELECT COUNT(*) FROM attachment_link al3 WHERE al3.attachment_id=a.id) AS link_count
                FROM attachment a {join}
                WHERE {' AND '.join(filters)}
                ORDER BY a.id, a.created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            params,
        ).fetchall()

        return {
            "total": total,
            "page":  page,
            "size":  size,
            "items": [dict(r._mapping) for r in rows],
        }

    # ── Get Metadata ──────────────────────────────────────────────────────────

    @staticmethod
    def get_metadata(db: Session, attachment_id: UUID) -> dict:
        row = db.execute(
            text("""
                SELECT id, entity_id, original_name, stored_name, file_path,
                       file_size, mime_type, file_extension, sha256_hash,
                       category, description, is_deleted, uploaded_by, created_at,
                       link_count, linked_to
                FROM vw_attachment_list
                WHERE id=:aid
            """),
            {"aid": str(attachment_id)},
        ).fetchone()
        if not row:
            raise ValueError("Attachment tidak ditemukan.")
        return dict(row._mapping)

    # ── Resolve File Path (for download) ──────────────────────────────────────

    @staticmethod
    def get_file_path(db: Session, attachment_id: UUID) -> tuple[Path, str, str]:
        """Returns (abs_path, original_name, mime_type). Raises if not found / deleted."""
        row = db.execute(
            text("""
                SELECT file_path, original_name, mime_type
                FROM attachment
                WHERE id=:aid AND is_deleted=FALSE
            """),
            {"aid": str(attachment_id)},
        ).fetchone()
        if not row:
            raise ValueError("Attachment tidak ditemukan atau sudah dihapus.")

        abs_path = UPLOAD_ROOT / row.file_path
        if not abs_path.exists():
            raise ValueError(f"File tidak ada di disk: {row.file_path}")

        return abs_path, row.original_name, row.mime_type or "application/octet-stream"

    # ── Soft Delete ───────────────────────────────────────────────────────────

    @staticmethod
    def delete_attachment(
        db: Session,
        attachment_id: UUID,
        deleted_by: str,
        remove_file: bool = False,
    ) -> dict:
        row = db.execute(
            text("SELECT file_path, original_name FROM attachment WHERE id=:aid AND is_deleted=FALSE"),
            {"aid": str(attachment_id)},
        ).fetchone()
        if not row:
            raise ValueError("Attachment tidak ditemukan atau sudah dihapus.")

        # Check if still linked
        link_count = db.execute(
            text("SELECT COUNT(*) AS cnt FROM attachment_link WHERE attachment_id=:aid"),
            {"aid": str(attachment_id)},
        ).fetchone()
        if link_count and link_count.cnt > 0 and not remove_file:
            raise ValueError(
                f"File masih terhubung ke {link_count.cnt} record. "
                "Gunakan remove_file=true untuk hapus paksa."
            )

        db.execute(
            text("""
                UPDATE attachment
                SET is_deleted=TRUE, deleted_by=:by, deleted_at=NOW()
                WHERE id=:aid
            """),
            {"aid": str(attachment_id), "by": deleted_by},
        )
        db.commit()

        if remove_file:
            try:
                abs_path = UPLOAD_ROOT / row.file_path
                if abs_path.exists():
                    abs_path.unlink()
            except OSError:
                pass  # log but don't fail

        return {
            "deleted":      True,
            "attachment_id": str(attachment_id),
            "original_name": row.original_name,
            "file_removed":  remove_file,
        }

    # ── Restore (undo soft delete) ────────────────────────────────────────────

    @staticmethod
    def restore_attachment(db: Session, attachment_id: UUID, restored_by: str) -> dict:
        result = db.execute(
            text("""
                UPDATE attachment
                SET is_deleted=FALSE, deleted_by=NULL, deleted_at=NULL
                WHERE id=:aid AND is_deleted=TRUE
            """),
            {"aid": str(attachment_id)},
        )
        db.commit()
        if result.rowcount == 0:
            raise ValueError("Attachment tidak ditemukan atau tidak dalam status deleted.")
        return {"restored": True, "attachment_id": str(attachment_id)}

    # ── Bulk Upload (multiple files at once) ──────────────────────────────────

    @staticmethod
    def bulk_upload(
        db: Session,
        entity_id: UUID,
        files: list[tuple[bytes, str]],  # [(data, filename), ...]
        uploaded_by: str = "",
        ref_type: Optional[str] = None,
        ref_id: Optional[UUID] = None,
    ) -> dict:
        results = []
        errors  = []
        for data, name in files:
            try:
                r = AttachmentEngine.upload(
                    db, entity_id, data, name, uploaded_by,
                    ref_type=ref_type, ref_id=ref_id,
                )
                results.append(r)
            except ValueError as e:
                errors.append({"filename": name, "error": str(e)})
        return {
            "uploaded":  results,
            "errors":    errors,
            "total":     len(files),
            "success":   len(results),
            "failed":    len(errors),
        }

    # ── Storage Summary ───────────────────────────────────────────────────────

    @staticmethod
    def get_storage_summary(db: Session, entity_id: UUID) -> dict:
        row = db.execute(
            text("SELECT * FROM vw_attachment_storage_summary WHERE entity_id=:eid"),
            {"eid": str(entity_id)},
        ).fetchone()
        if not row:
            return {
                "total_files": 0, "active_files": 0,
                "total_size_bytes": 0, "total_size_mb": 0.0,
                "last_upload_at": None,
            }
        return dict(row._mapping)

    # ── Search Attachments ────────────────────────────────────────────────────

    @staticmethod
    def search(
        db: Session,
        entity_id: UUID,
        keyword: str,
        category: Optional[str] = None,
    ) -> list[dict]:
        filters = ["a.entity_id=:eid", "a.is_deleted=FALSE"]
        params: dict = {"eid": str(entity_id)}

        if keyword:
            filters.append("(a.original_name ILIKE :kw OR a.description ILIKE :kw)")
            params["kw"] = f"%{keyword}%"
        if category:
            filters.append("a.category=:cat")
            params["cat"] = category

        rows = db.execute(
            text(f"""
                SELECT a.id, a.original_name, a.file_size, a.mime_type,
                       a.category, a.description, a.uploaded_by, a.created_at
                FROM attachment a
                WHERE {' AND '.join(filters)}
                ORDER BY a.created_at DESC
                LIMIT 100
            """),
            params,
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    # ── Copy Attachment to Another Entity ─────────────────────────────────────

    @staticmethod
    def copy_to_entity(
        db: Session,
        attachment_id: UUID,
        target_entity_id: UUID,
        copied_by: str = "",
    ) -> dict:
        """
        Buat attachment record baru di entity lain (reuse file fisik yang sama).
        Useful untuk multi-entity setup dimana dokumen perlu dibagi.
        """
        src = db.execute(
            text("""
                SELECT original_name, stored_name, file_path, file_size,
                       mime_type, file_extension, sha256_hash, category, description
                FROM attachment WHERE id=:aid AND is_deleted=FALSE
            """),
            {"aid": str(attachment_id)},
        ).fetchone()
        if not src:
            raise ValueError("Attachment sumber tidak ditemukan.")

        # Check if same hash already exists in target entity
        existing = db.execute(
            text("SELECT id FROM attachment WHERE entity_id=:eid AND sha256_hash=:h AND is_deleted=FALSE"),
            {"eid": str(target_entity_id), "h": src.sha256_hash},
        ).fetchone()
        if existing:
            return {"attachment_id": str(existing.id), "copied": False, "note": "File sudah ada di entity tujuan."}

        row = db.execute(
            text("""
                INSERT INTO attachment
                    (entity_id, original_name, stored_name, file_path, file_size,
                     mime_type, file_extension, sha256_hash, category, description, uploaded_by)
                VALUES (:eid, :orig, :stored, :path, :size, :mime, :ext, :hash, :cat, :desc, :by)
                RETURNING id
            """),
            {
                "eid":    str(target_entity_id),
                "orig":   src.original_name,
                "stored": src.stored_name,
                "path":   src.file_path,     # reuse same physical file
                "size":   src.file_size,
                "mime":   src.mime_type,
                "ext":    src.file_extension,
                "hash":   src.sha256_hash,
                "cat":    src.category,
                "desc":   src.description,
                "by":     copied_by,
            },
        ).fetchone()
        db.commit()
        return {"attachment_id": str(row.id), "copied": True}
