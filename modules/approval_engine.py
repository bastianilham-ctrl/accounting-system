# modules/approval_engine.py
# Generic, reusable approval workflow engine — plug-and-play.
#
# Tidak tahu apa-apa soal modul spesifik (PR/PO/dll). Beroperasi murni atas
# (document_type, document_id) yang didaftarkan modul pemanggil. Strategi resolusi
# approval dibaca dari tabel master `approval_policy` (lihat schema_approval_engine.sql):
#   - hierarchy     : naik rantai employee.manager_id sampai ketemu role terminal
#                      (finance/admin/superadmin) atau puncak hirarki (lalu fallback role)
#   - amount_matrix : baca po_approval_matrix berdasar nominal dokumen (1 step)
#   - single_role   : 1 step, role minimum = fallback_role (default kalau belum diatur)
#
# Untuk plug modul baru: panggil .start() saat dokumen disubmit, .act() saat user
# approve/reject, .get_steps()/.get_current_approver_label() untuk tampilkan progress.
# Tidak perlu ubah skema atau kode engine ini.

from typing import Optional
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from modules.auth import ROLE_LEVEL


class ApprovalEngine:
    def __init__(self, db: Session):
        self.db = db

    # ── Policy ───────────────────────────────────────────────────────────────────

    def _get_policy(self, entity_id: str, document_type: str) -> dict:
        row = self.db.execute(
            text("""
                SELECT * FROM approval_policy
                WHERE document_type = :dt AND is_active = TRUE
                  AND (entity_id = :eid OR entity_id IS NULL)
                ORDER BY entity_id NULLS LAST
                LIMIT 1
            """),
            {"dt": document_type, "eid": entity_id}
        ).fetchone()
        if not row:
            return {"strategy": "single_role", "fallback_role": "finance", "terminal_roles": "finance,admin,superadmin"}
        return dict(row._mapping)

    # ── Chain builders ───────────────────────────────────────────────────────────

    def _build_hierarchy_chain(self, entity_id, requested_by_email, fallback_role, terminal_roles) -> list[dict]:
        terminal_set = {r.strip() for r in terminal_roles.split(",") if r.strip()}

        if not requested_by_email:
            return [{"approver_employee_id": None, "required_role": fallback_role,
                     "approver_label": f"Role {fallback_role} (tidak ada email pemohon)"}]

        requester = self.db.execute(
            text("""
                SELECT * FROM employee
                WHERE entity_id = :eid AND lower(email) = lower(:email) AND status = 'active'
                LIMIT 1
            """),
            {"eid": entity_id, "email": requested_by_email}
        ).fetchone()

        if not requester or not requester.manager_id:
            return [{"approver_employee_id": None, "required_role": fallback_role,
                     "approver_label": f"Role {fallback_role} (data karyawan/atasan tidak ditemukan)"}]

        steps: list[dict] = []
        current_manager_id = requester.manager_id
        visited = set()
        while current_manager_id and current_manager_id not in visited:
            visited.add(current_manager_id)
            mgr = self.db.execute(
                text("SELECT * FROM employee WHERE id = :id"), {"id": current_manager_id}
            ).fetchone()
            if not mgr:
                break
            steps.append({
                "approver_employee_id": str(mgr.id),
                "required_role": None,
                "approver_label": mgr.full_name + (f" ({mgr.position})" if mgr.position else ""),
            })
            mgr_user = self.db.execute(
                text("SELECT role FROM app_user WHERE lower(email) = lower(:email) AND is_active = TRUE LIMIT 1"),
                {"email": mgr.email}
            ).fetchone()
            if mgr_user and mgr_user.role in terminal_set:
                return steps
            current_manager_id = mgr.manager_id

        # Puncak hirarki tercapai tanpa pernah ketemu role terminal — pengaman akhir.
        steps.append({
            "approver_employee_id": None, "required_role": fallback_role,
            "approver_label": f"Role {fallback_role} (puncak hirarki tercapai)",
        })
        return steps

    def _build_amount_matrix_chain(self, entity_id, document_amount) -> list[dict]:
        amt = document_amount or 0
        row = self.db.execute(
            text("""
                SELECT approver_role, threshold_name FROM po_approval_matrix
                WHERE entity_id = :eid AND is_active = TRUE
                  AND min_amount <= :amt AND (max_amount IS NULL OR max_amount >= :amt)
                ORDER BY level DESC LIMIT 1
            """),
            {"eid": entity_id, "amt": amt}
        ).fetchone()
        role = row.approver_role if row else "finance"
        label = f"Role {role}" + (f" ({row.threshold_name})" if row else "")
        return [{"approver_employee_id": None, "required_role": role, "approver_label": label}]

    def _build_single_role_chain(self, fallback_role) -> list[dict]:
        return [{"approver_employee_id": None, "required_role": fallback_role, "approver_label": f"Role {fallback_role}"}]

    # ── Public API ───────────────────────────────────────────────────────────────

    def start(
        self, entity_id: str, document_type: str, document_id: str,
        document_ref: Optional[str], document_amount: Optional[float], requested_by_email: Optional[str],
    ) -> dict:
        policy = self._get_policy(entity_id, document_type)
        if policy["strategy"] == "hierarchy":
            steps = self._build_hierarchy_chain(entity_id, requested_by_email, policy["fallback_role"], policy["terminal_roles"])
        elif policy["strategy"] == "amount_matrix":
            steps = self._build_amount_matrix_chain(entity_id, document_amount)
        else:
            steps = self._build_single_role_chain(policy["fallback_role"])

        # Defensif: hapus request lama untuk dokumen ini kalau ada (mis. re-submit).
        self.db.execute(
            text("DELETE FROM approval_request WHERE document_type = :dt AND document_id = :did"),
            {"dt": document_type, "did": document_id}
        )

        request_id = str(uuid4())
        self.db.execute(
            text("""
                INSERT INTO approval_request (id, entity_id, document_type, document_id, document_ref,
                    document_amount, requested_by, status)
                VALUES (:id, :eid, :dt, :did, :ref, :amt, :by, 'pending')
            """),
            {"id": request_id, "eid": entity_id, "dt": document_type, "did": document_id,
             "ref": document_ref, "amt": document_amount, "by": requested_by_email}
        )
        for idx, step in enumerate(steps, start=1):
            self.db.execute(
                text("""
                    INSERT INTO approval_step (id, request_id, level, approver_employee_id,
                        required_role, approver_label, status)
                    VALUES (uuid_generate_v4(), :rid, :lvl, :emp, :role, :label, 'pending')
                """),
                {"rid": request_id, "lvl": idx, "emp": step["approver_employee_id"],
                 "role": step["required_role"], "label": step["approver_label"]}
            )
        self.db.commit()
        return {"request_id": request_id, "steps": self.get_steps(document_type, document_id)}

    def _get_pending_step(self, document_type: str, document_id: str):
        return self.db.execute(
            text("""
                SELECT s.* FROM approval_step s
                JOIN approval_request r ON r.id = s.request_id
                WHERE r.document_type = :dt AND r.document_id = :did AND s.status = 'pending'
                ORDER BY s.level ASC LIMIT 1
            """),
            {"dt": document_type, "did": document_id}
        ).fetchone()

    def act(
        self, document_type: str, document_id: str, acting_user: dict, action: str, notes: Optional[str] = None,
    ) -> dict:
        request = self.db.execute(
            text("SELECT * FROM approval_request WHERE document_type = :dt AND document_id = :did"),
            {"dt": document_type, "did": document_id}
        ).fetchone()
        if not request:
            raise HTTPException(404, "Approval request tidak ditemukan untuk dokumen ini")
        if request.status != "pending":
            raise HTTPException(400, f"Approval sudah selesai (status: {request.status})")

        step = self._get_pending_step(document_type, document_id)
        if not step:
            raise HTTPException(400, "Tidak ada step pending — data approval tidak konsisten")

        acting_email = (acting_user.get("email") or "").lower()
        acting_role = acting_user.get("role", "viewer")

        authorized = False
        if step.approver_employee_id:
            approver_emp = self.db.execute(
                text("SELECT email FROM employee WHERE id = :id"), {"id": step.approver_employee_id}
            ).fetchone()
            if approver_emp and approver_emp.email and approver_emp.email.lower() == acting_email:
                authorized = True
            elif acting_role in ("admin", "superadmin"):
                authorized = True  # override — manager mungkin tidak punya akun sistem
        else:
            required_level = ROLE_LEVEL.get(step.required_role or "finance", 2)
            if ROLE_LEVEL.get(acting_role, 0) >= required_level:
                authorized = True

        if not authorized:
            raise HTTPException(403, f"Anda bukan approver untuk level ini (seharusnya: {step.approver_label})")

        if action == "approved":
            self.db.execute(
                text("""
                    UPDATE approval_step SET status = 'approved', acted_by = :by, acted_at = NOW(), notes = :notes
                    WHERE id = :id
                """),
                {"id": step.id, "by": acting_user.get("email"), "notes": notes}
            )
            next_step = self._get_pending_step(document_type, document_id)
            if next_step:
                self.db.commit()
                return {"request_status": "pending", "level": step.level, "is_final": False}
            self.db.execute(
                text("UPDATE approval_request SET status = 'approved', completed_at = NOW() WHERE id = :id"),
                {"id": request.id}
            )
            self.db.commit()
            return {"request_status": "approved", "level": step.level, "is_final": True}

        self.db.execute(
            text("""
                UPDATE approval_step SET status = 'rejected', acted_by = :by, acted_at = NOW(), notes = :notes
                WHERE id = :id
            """),
            {"id": step.id, "by": acting_user.get("email"), "notes": notes}
        )
        self.db.execute(
            text("UPDATE approval_step SET status = 'skipped' WHERE request_id = :rid AND status = 'pending'"),
            {"rid": request.id}
        )
        self.db.execute(
            text("UPDATE approval_request SET status = 'rejected', completed_at = NOW() WHERE id = :id"),
            {"id": request.id}
        )
        self.db.commit()
        return {"request_status": "rejected", "level": step.level, "is_final": True}

    def get_steps(self, document_type: str, document_id: str) -> list[dict]:
        rows = self.db.execute(
            text("""
                SELECT s.* FROM approval_step s
                JOIN approval_request r ON r.id = s.request_id
                WHERE r.document_type = :dt AND r.document_id = :did
                ORDER BY s.level
            """),
            {"dt": document_type, "did": document_id}
        ).fetchall()
        return [dict(r._mapping) for r in rows]

    def get_current_approver_label(self, document_type: str, document_id: str) -> Optional[str]:
        step = self._get_pending_step(document_type, document_id)
        return step.approver_label if step else None
