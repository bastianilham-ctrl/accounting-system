"""
File Attachment Router
Base prefix: /attachments

Upload & linking:
  POST /attachments/upload                 — upload satu file
  POST /attachments/upload-bulk            — upload banyak file sekaligus
  POST /attachments/{id}/link              — link file ke entity
  DELETE /attachments/{id}/link            — unlink

Query:
  GET /attachments/{id}                    — metadata + links
  GET /attachments/{id}/download           — download (stream)
  GET /attachments/by-entity/{type}/{id}   — semua file yang terhubung ke record
  GET /attachments/by-company              — semua file milik entity (dengan filter)
  GET /attachments/search                  — cari berdasarkan nama / deskripsi

Management:
  DELETE /attachments/{id}                 — soft delete
  POST /attachments/{id}/restore           — batalkan soft delete
  POST /attachments/{id}/copy              — copy ke entity lain
  GET /attachments/storage-summary         — statistik storage
"""

from pathlib import Path
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.database import get_db
from modules.attachment_engine import AttachmentEngine, VALID_REF_TYPES

router = APIRouter(prefix="/attachments", tags=["File Attachments"])


# ── Pydantic Models ──────────────────────────────────────────────────────────

class LinkReq(BaseModel):
    ref_type:   str
    ref_id:     UUID
    notes:      Optional[str] = None
    linked_by:  str = ""


class UnlinkReq(BaseModel):
    ref_type:   str
    ref_id:     UUID


class DeleteReq(BaseModel):
    deleted_by:  str = ""
    remove_file: bool = False


class RestoreReq(BaseModel):
    restored_by: str = ""


class CopyReq(BaseModel):
    target_entity_id: UUID
    copied_by:        str = ""


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post("/upload", summary="Upload satu file attachment")
async def upload_file(
    entity_id:   UUID                   = Form(...),
    file:        UploadFile             = File(...),
    uploaded_by: str                    = Form(default=""),
    description: Optional[str]         = Form(default=None),
    category:    Optional[str]         = Form(default=None),
    ref_type:    Optional[str]         = Form(default=None),
    ref_id:      Optional[UUID]        = Form(default=None),
    link_notes:  Optional[str]         = Form(default=None),
    db: Session = Depends(get_db),
):
    """
    Upload file dan opsional langsung link ke satu entity.

    - **entity_id**: UUID perusahaan/entity pemilik file
    - **file**: file yang diupload (maks 50 MB; PDF / image / Office)
    - **ref_type** + **ref_id**: opsional — langsung link ke record
    """
    if ref_type and ref_type not in VALID_REF_TYPES:
        raise HTTPException(status_code=400, detail=f"ref_type tidak valid: {ref_type}")

    data = await file.read()
    try:
        return AttachmentEngine.upload(
            db,
            entity_id     = entity_id,
            file_data     = data,
            original_name = file.filename or "unknown",
            uploaded_by   = uploaded_by,
            description   = description,
            category      = category,
            ref_type      = ref_type,
            ref_id        = ref_id,
            link_notes    = link_notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/upload-bulk", summary="Upload banyak file sekaligus")
async def upload_bulk(
    entity_id:   UUID                   = Form(...),
    files:       List[UploadFile]       = File(...),
    uploaded_by: str                    = Form(default=""),
    ref_type:    Optional[str]         = Form(default=None),
    ref_id:      Optional[UUID]        = Form(default=None),
    db: Session = Depends(get_db),
):
    """Upload hingga 10 file sekaligus, semua otomatis di-link ke ref_type/ref_id jika disediakan."""
    if len(files) > 10:
        raise HTTPException(status_code=400, detail="Maksimum 10 file per request.")

    file_list = []
    for f in files:
        data = await f.read()
        file_list.append((data, f.filename or "unknown"))

    return AttachmentEngine.bulk_upload(
        db, entity_id, file_list, uploaded_by, ref_type, ref_id
    )


# ── Link / Unlink ─────────────────────────────────────────────────────────────

@router.post("/{attachment_id}/link", summary="Link file ke entity")
def link_attachment(
    attachment_id: UUID,
    req: LinkReq,
    db: Session = Depends(get_db),
):
    try:
        return AttachmentEngine.link_to_entity(
            db,
            attachment_id = attachment_id,
            ref_type      = req.ref_type,
            ref_id        = req.ref_id,
            linked_by     = req.linked_by,
            notes         = req.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{attachment_id}/link", summary="Unlink file dari entity")
def unlink_attachment(
    attachment_id: UUID,
    req: UnlinkReq,
    db: Session = Depends(get_db),
):
    try:
        return AttachmentEngine.unlink_from_entity(
            db, attachment_id, req.ref_type, req.ref_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Query ─────────────────────────────────────────────────────────────────────

@router.get("/{attachment_id}", summary="Metadata + daftar link attachment")
def get_attachment(attachment_id: UUID, db: Session = Depends(get_db)):
    try:
        return AttachmentEngine.get_metadata(db, attachment_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{attachment_id}/download", summary="Download / stream file")
def download_attachment(attachment_id: UUID, db: Session = Depends(get_db)):
    try:
        abs_path, original_name, mime_type = AttachmentEngine.get_file_path(db, attachment_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return FileResponse(
        path         = str(abs_path),
        media_type   = mime_type,
        filename     = original_name,
    )


@router.get("/by-entity/{ref_type}/{ref_id}", summary="Semua file yang terhubung ke satu record")
def list_by_entity(
    ref_type: str,
    ref_id:   UUID,
    db: Session = Depends(get_db),
):
    if ref_type not in VALID_REF_TYPES:
        raise HTTPException(status_code=400, detail=f"ref_type tidak valid: {ref_type}")
    return AttachmentEngine.list_by_entity(db, ref_type, ref_id)


@router.get("/by-company", summary="Semua file milik satu entity (dengan filter)")
def list_by_company(
    entity_id: UUID,
    category:  Optional[str] = None,
    ref_type:  Optional[str] = None,
    page:      int           = Query(default=1, ge=1),
    size:      int           = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return AttachmentEngine.list_by_company(db, entity_id, category, ref_type, page, size)


@router.get("/search", summary="Cari file berdasarkan nama atau deskripsi")
def search_attachments(
    entity_id: UUID,
    q:         str,
    category:  Optional[str] = None,
    db: Session = Depends(get_db),
):
    return AttachmentEngine.search(db, entity_id, q, category)


@router.get("/storage-summary", summary="Statistik storage per entity")
def storage_summary(entity_id: UUID, db: Session = Depends(get_db)):
    return AttachmentEngine.get_storage_summary(db, entity_id)


# ── Management ────────────────────────────────────────────────────────────────

@router.delete("/{attachment_id}", summary="Soft delete attachment")
def delete_attachment(
    attachment_id: UUID,
    req: DeleteReq,
    db: Session = Depends(get_db),
):
    try:
        return AttachmentEngine.delete_attachment(
            db, attachment_id, req.deleted_by, req.remove_file
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{attachment_id}/restore", summary="Batalkan soft delete")
def restore_attachment(
    attachment_id: UUID,
    req: RestoreReq,
    db: Session = Depends(get_db),
):
    try:
        return AttachmentEngine.restore_attachment(db, attachment_id, req.restored_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{attachment_id}/copy", summary="Copy file ke entity lain (multi-entity)")
def copy_attachment(
    attachment_id: UUID,
    req: CopyReq,
    db: Session = Depends(get_db),
):
    try:
        return AttachmentEngine.copy_to_entity(
            db, attachment_id, req.target_entity_id, req.copied_by
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Ref Type Catalogue ────────────────────────────────────────────────────────

@router.get("/ref-types", summary="Daftar ref_type yang valid")
def list_ref_types():
    return {"ref_types": sorted(VALID_REF_TYPES)}
