"""
CRM Contact Router
Prefix: /contacts  (company) and /contact-persons (person under company)
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from core.database import get_db

router = APIRouter(prefix="/contacts", tags=["CRM Contacts"])


# ── Pydantic Models ────────────────────────────────────────────────────────────

class ContactCreate(BaseModel):
    entity_id: str
    company_name: str
    industry: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    country: Optional[str] = "Indonesia"
    phone: Optional[str] = None
    email: Optional[str] = None
    source: Optional[str] = "cold_call"
    status: Optional[str] = "prospect"
    assigned_to: Optional[str] = None
    notes: Optional[str] = None


class ContactUpdate(BaseModel):
    company_name: Optional[str] = None
    industry: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    country: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    source: Optional[str] = None
    status: Optional[str] = None
    assigned_to: Optional[str] = None
    notes: Optional[str] = None


class PersonCreate(BaseModel):
    contact_id: str
    full_name: str
    title: Optional[str] = None
    department: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    linkedin: Optional[str] = None
    is_primary: Optional[bool] = False
    is_decision_maker: Optional[bool] = False
    notes: Optional[str] = None


class PersonUpdate(BaseModel):
    full_name: Optional[str] = None
    title: Optional[str] = None
    department: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    linkedin: Optional[str] = None
    is_primary: Optional[bool] = None
    is_decision_maker: Optional[bool] = None
    notes: Optional[str] = None


# ── Company Contact Endpoints ──────────────────────────────────────────────────

@router.get("")
def list_contacts(entity_id: str, status: Optional[str] = None,
                  search: Optional[str] = None, db: Session = Depends(get_db)):
    sql = """
        SELECT c.id, c.company_name, c.industry, c.city, c.phone, c.email,
               c.source, c.status, c.assigned_to, c.notes,
               c.website, c.address, c.province, c.country,
               c.created_at,
               COUNT(cp.id) AS person_count
        FROM contact c
        LEFT JOIN contact_person cp ON cp.contact_id = c.id
        WHERE c.entity_id = :eid
    """
    params: dict = {"eid": entity_id}
    if status:
        sql += " AND c.status = :status"
        params["status"] = status
    if search:
        sql += " AND (c.company_name ILIKE :q OR c.city ILIKE :q OR c.industry ILIKE :q)"
        params["q"] = f"%{search}%"
    sql += " GROUP BY c.id ORDER BY c.created_at DESC"
    rows = db.execute(text(sql), params).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("")
def create_contact(body: ContactCreate, db: Session = Depends(get_db)):
    row = db.execute(text("""
        INSERT INTO contact
            (entity_id, company_name, industry, website, address, city, province, country,
             phone, email, source, status, assigned_to, notes)
        VALUES
            (:eid, :name, :industry, :website, :address, :city, :province, :country,
             :phone, :email, :source, :status, :assigned_to, :notes)
        RETURNING id, company_name, status, created_at
    """), {
        "eid": body.entity_id, "name": body.company_name,
        "industry": body.industry, "website": body.website,
        "address": body.address, "city": body.city,
        "province": body.province, "country": body.country,
        "phone": body.phone, "email": body.email,
        "source": body.source, "status": body.status,
        "assigned_to": body.assigned_to, "notes": body.notes,
    }).fetchone()
    db.commit()
    return dict(row._mapping)


@router.put("/{contact_id}")
def update_contact(contact_id: str, body: ContactUpdate, db: Session = Depends(get_db)):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="Tidak ada field yang diupdate")
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["cid"] = contact_id
    db.execute(text(f"UPDATE contact SET {set_clause}, updated_at = NOW() WHERE id = :cid"), fields)
    db.commit()
    return {"success": True}


@router.delete("/{contact_id}")
def delete_contact(contact_id: str, db: Session = Depends(get_db)):
    db.execute(text("DELETE FROM contact WHERE id = :id"), {"id": contact_id})
    db.commit()
    return {"success": True}


# ── Contact Person Endpoints ───────────────────────────────────────────────────

@router.get("/{contact_id}/persons")
def list_persons(contact_id: str, db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT id, contact_id, full_name, title, department, email, phone,
               whatsapp, linkedin, is_primary, is_decision_maker, notes, created_at
        FROM contact_person
        WHERE contact_id = :cid
        ORDER BY is_primary DESC, full_name
    """), {"cid": contact_id}).fetchall()
    return [dict(r._mapping) for r in rows]


person_router = APIRouter(prefix="/contact-persons", tags=["CRM Contact Persons"])


@person_router.post("")
def create_person(body: PersonCreate, db: Session = Depends(get_db)):
    row = db.execute(text("""
        INSERT INTO contact_person
            (contact_id, full_name, title, department, email, phone,
             whatsapp, linkedin, is_primary, is_decision_maker, notes)
        VALUES
            (:cid, :name, :title, :dept, :email, :phone,
             :wa, :li, :primary, :dm, :notes)
        RETURNING id, full_name, title, email, is_primary
    """), {
        "cid": body.contact_id, "name": body.full_name,
        "title": body.title, "dept": body.department,
        "email": body.email, "phone": body.phone,
        "wa": body.whatsapp, "li": body.linkedin,
        "primary": body.is_primary, "dm": body.is_decision_maker,
        "notes": body.notes,
    }).fetchone()
    db.commit()
    return dict(row._mapping)


@person_router.put("/{person_id}")
def update_person(person_id: str, body: PersonUpdate, db: Session = Depends(get_db)):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="Tidak ada field yang diupdate")
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["pid"] = person_id
    db.execute(text(f"UPDATE contact_person SET {set_clause} WHERE id = :pid"), fields)
    db.commit()
    return {"success": True}


@person_router.delete("/{person_id}")
def delete_person(person_id: str, db: Session = Depends(get_db)):
    db.execute(text("DELETE FROM contact_person WHERE id = :id"), {"id": person_id})
    db.commit()
    return {"success": True}
