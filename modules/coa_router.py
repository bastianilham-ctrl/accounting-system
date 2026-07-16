# modules/coa_router.py
# Listing Chart of Accounts per entity — dipakai oleh dropdown akun di FE
# (AP/AR invoice form, Journal Entry form, halaman COA).

from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

from core.database import get_db

router = APIRouter(prefix="/coa", tags=["Chart of Accounts"])


@router.get("/")
def list_coa(
    entity_id: str,
    account_type: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(default=500, le=2000),
    db: Session = Depends(get_db),
):
    """List akun COA per entity, opsional filter by account_type atau search code/nama."""
    filters = ["entity_id = :eid"]
    params: dict = {"eid": entity_id, "limit": limit}

    if account_type:
        filters.append("account_type = :atype")
        params["atype"] = account_type
    if search:
        filters.append("(account_code ILIKE :q OR account_name ILIKE :q)")
        params["q"] = f"%{search}%"

    rows = db.execute(
        text(f"""
            SELECT id, account_code, account_name, account_type, normal_balance,
                   parent_id, level, is_header, is_active, tax_object
            FROM chart_of_accounts
            WHERE {' AND '.join(filters)}
            ORDER BY account_code
            LIMIT :limit
        """),
        params,
    ).fetchall()
    return [dict(r._mapping) for r in rows]
