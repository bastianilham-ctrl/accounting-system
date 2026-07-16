"""
Entity Router — Base/Functional Currency Setting
Base prefix: /entities

Catatan: entity.currency adalah currency pembukuan (functional currency) yang
dideklarasikan sekali di awal setup, sebelum ada transaksi — mengikuti praktik
akuntansi (ganti base currency setelah ada transaksi butuh restatement, bukan
toggle biasa). GL tetap selalu dicatat dalam IDR (debit_idr/credit_idr, sesuai
desain modul Multi-Currency) — currency ini dipakai sebagai default mata uang
input transaksi dan untuk identifikasi/pelaporan functional currency entity.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db

router = APIRouter(prefix="/entities", tags=["Entity Settings"])


class SetCurrencyReq(BaseModel):
    currency: str


@router.get("/{entity_id}", summary="Detail entity termasuk currency pembukuan")
def get_entity(entity_id: UUID, db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT id, code, name, npwp, currency, country, is_active FROM entity WHERE id = :id"),
        {"id": str(entity_id)},
    ).fetchone()
    if not row:
        raise HTTPException(404, "Entity tidak ditemukan")
    return dict(row._mapping)


@router.get("/{entity_id}/currency-lock-status", summary="Cek apakah base currency masih bisa diubah")
def currency_lock_status(entity_id: UUID, db: Session = Depends(get_db)):
    count = db.execute(
        text("SELECT COUNT(*) AS cnt FROM gl_journal WHERE entity_id = :eid"),
        {"eid": str(entity_id)},
    ).fetchone()
    locked = count.cnt > 0
    return {"entity_id": str(entity_id), "locked": locked, "journal_count": count.cnt}


@router.patch("/{entity_id}/currency", summary="Set base/functional currency (hanya sebelum ada jurnal)")
def set_base_currency(entity_id: UUID, req: SetCurrencyReq, db: Session = Depends(get_db)):
    entity = db.execute(text("SELECT id FROM entity WHERE id = :id"), {"id": str(entity_id)}).fetchone()
    if not entity:
        raise HTTPException(404, "Entity tidak ditemukan")

    currency = req.currency.upper()
    cur_row = db.execute(
        text("SELECT currency_code FROM currency WHERE currency_code = :code AND is_active = TRUE"),
        {"code": currency},
    ).fetchone()
    if not cur_row:
        raise HTTPException(400, f"Currency '{currency}' tidak ditemukan atau tidak aktif di currency master.")

    journal_count = db.execute(
        text("SELECT COUNT(*) AS cnt FROM gl_journal WHERE entity_id = :eid"),
        {"eid": str(entity_id)},
    ).fetchone().cnt
    if journal_count > 0:
        raise HTTPException(
            400,
            f"Base currency tidak bisa diubah — entity sudah punya {journal_count} jurnal terposting. "
            "Base currency hanya bisa di-set sebelum ada transaksi (baseline historis).",
        )

    db.execute(
        text("UPDATE entity SET currency = :cur WHERE id = :id"),
        {"cur": currency, "id": str(entity_id)},
    )
    db.commit()
    return {"entity_id": str(entity_id), "currency": currency}
