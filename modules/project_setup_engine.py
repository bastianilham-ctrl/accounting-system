"""
Project Setup Engine
Implements:
  - Critical Path Method (CPM): Forward pass → Backward pass → Float → Critical path
  - Gantt chart data generation
  - Resource loading & utilization
  - Budget vs actual analysis (EVM: EV, PV, AC, SPI, CPI)
  - Risk scoring & heatmap
  - WBS cost rollup
  - Auto-numbering (CR, risk code, task code)
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _add_workdays(start: date, days: int) -> date:
    """Add `days` calendar days (not business days — keeps it simple & universal)."""
    return start + timedelta(days=max(days - 1, 0))


def _days_between(d1: date, d2: date) -> int:
    return (d2 - d1).days + 1


def _gen_cr_no(db: Session) -> str:
    today = date.today()
    year, month = today.year, today.month
    row = db.execute(
        text(
            "SELECT COUNT(*) AS cnt FROM project_change_request "
            "WHERE cr_no LIKE :p"
        ),
        {"p": f"CR/{year}/{month:02d}/%"},
    ).fetchone()
    seq = (row.cnt if row else 0) + 1
    return f"CR/{year}/{month:02d}/{seq:04d}"


def _next_task_code(db: Session, project_id: str) -> str:
    row = db.execute(
        text(
            "SELECT COUNT(*) AS cnt FROM project_task WHERE project_id = :pid"
        ),
        {"pid": project_id},
    ).fetchone()
    seq = (row.cnt if row else 0) + 1
    return f"T-{seq:03d}"


def _next_risk_code(db: Session, project_id: str) -> str:
    row = db.execute(
        text(
            "SELECT COUNT(*) AS cnt FROM project_risk WHERE project_id = :pid"
        ),
        {"pid": project_id},
    ).fetchone()
    seq = (row.cnt if row else 0) + 1
    return f"R-{seq:03d}"


# ─────────────────────────────────────────────────────────────────────────────
# CPM Data Structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CPMNode:
    task_id:     str
    duration:    int            # hari
    planned_start: date
    # CPM outputs
    es: Optional[date] = None   # Early Start
    ef: Optional[date] = None   # Early Finish
    ls: Optional[date] = None   # Late Start
    lf: Optional[date] = None   # Late Finish
    total_float:  int  = 0
    free_float:   int  = 0
    is_critical:  bool = False
    predecessors: list[tuple[str, str, int]] = field(default_factory=list)
    # (predecessor_id, dependency_type, lag_days)
    successors:   list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# ProjectSetupEngine
# ─────────────────────────────────────────────────────────────────────────────

class ProjectSetupEngine:

    # ─── CPM ─────────────────────────────────────────────────────────────────

    @staticmethod
    def compute_cpm(db: Session, project_id: str) -> dict:
        """
        Hitung Critical Path Method untuk semua task dalam satu proyek.
        Menyimpan hasil (ES/EF/LS/LF/float/is_critical) ke tabel project_task.

        Algoritma:
          1. Bangun graph (adjacency list + predecessor list)
          2. Topological sort (Kahn's algorithm)
          3. Forward pass: ES/EF per task
          4. Backward pass: LS/LF per task
          5. Float = LS - ES; Critical = float == 0
        """
        # Load tasks
        tasks_raw = db.execute(
            text(
                "SELECT id, planned_start, duration_days "
                "FROM project_task "
                "WHERE project_id = :pid AND status != 'cancelled'"
            ),
            {"pid": project_id},
        ).fetchall()

        if not tasks_raw:
            return {"critical_path": [], "project_duration": 0, "tasks_updated": 0}

        # Load dependencies
        deps_raw = db.execute(
            text(
                "SELECT predecessor_id, successor_id, dependency_type, lag_days "
                "FROM task_dependency "
                "WHERE project_id = :pid"
            ),
            {"pid": project_id},
        ).fetchall()

        # Build node map
        nodes: dict[str, CPMNode] = {}
        for t in tasks_raw:
            nodes[str(t.id)] = CPMNode(
                task_id=str(t.id),
                duration=int(t.duration_days or 1),
                planned_start=t.planned_start,
            )

        # Build predecessor/successor lists
        in_degree: dict[str, int] = {tid: 0 for tid in nodes}
        for dep in deps_raw:
            pred_id = str(dep.predecessor_id)
            succ_id = str(dep.successor_id)
            if pred_id not in nodes or succ_id not in nodes:
                continue
            nodes[pred_id].successors.append(succ_id)
            nodes[succ_id].predecessors.append(
                (pred_id, dep.dependency_type, int(dep.lag_days or 0))
            )
            in_degree[succ_id] += 1

        # Topological sort (Kahn)
        queue: deque[str] = deque(
            [tid for tid, deg in in_degree.items() if deg == 0]
        )
        topo_order: list[str] = []
        remaining = dict(in_degree)
        while queue:
            tid = queue.popleft()
            topo_order.append(tid)
            for succ_id in nodes[tid].successors:
                remaining[succ_id] -= 1
                if remaining[succ_id] == 0:
                    queue.append(succ_id)

        if len(topo_order) != len(nodes):
            # Cycle detected — fallback: use planned dates as-is
            return {"critical_path": [], "project_duration": 0, "tasks_updated": 0, "warning": "Circular dependency detected"}

        # ── Forward Pass ─────────────────────────────────────────────────────
        for tid in topo_order:
            node = nodes[tid]
            if not node.predecessors:
                node.es = node.planned_start
            else:
                candidate = node.planned_start  # tidak boleh lebih awal dari planned_start
                for pred_id, dep_type, lag in node.predecessors:
                    pred = nodes[pred_id]
                    if dep_type == "FS":
                        # successor start ≥ predecessor finish + lag
                        earliest = pred.ef + timedelta(days=1 + lag) if pred.ef else node.planned_start
                    elif dep_type == "SS":
                        earliest = pred.es + timedelta(days=lag) if pred.es else node.planned_start
                    elif dep_type == "FF":
                        # successor finish ≥ predecessor finish + lag → es = ef - duration + 1
                        if pred.ef:
                            tentative_ef = pred.ef + timedelta(days=lag)
                            earliest = tentative_ef - timedelta(days=node.duration - 1)
                        else:
                            earliest = node.planned_start
                    else:  # SF
                        earliest = pred.es + timedelta(days=lag) if pred.es else node.planned_start
                    if earliest > candidate:
                        candidate = earliest
                node.es = candidate

            node.ef = _add_workdays(node.es, node.duration)

        # Project end = latest EF
        project_end = max(n.ef for n in nodes.values())
        project_start = min(n.es for n in nodes.values())
        project_duration = _days_between(project_start, project_end)

        # ── Backward Pass ────────────────────────────────────────────────────
        for tid in reversed(topo_order):
            node = nodes[tid]
            if not node.successors:
                node.lf = project_end
            else:
                candidate = project_end
                for succ_id in node.successors:
                    succ = nodes[succ_id]
                    # Find this node's relationship to successor
                    dep_type, lag = "FS", 0
                    for (p_id, d_type, d_lag) in succ.predecessors:
                        if p_id == tid:
                            dep_type, lag = d_type, d_lag
                            break

                    if dep_type == "FS":
                        latest = succ.ls - timedelta(days=1 + lag) if succ.ls else project_end
                    elif dep_type == "SS":
                        latest = succ.ls - timedelta(days=lag) if succ.ls else project_end
                    elif dep_type == "FF":
                        latest = succ.lf - timedelta(days=lag) if succ.lf else project_end
                    else:  # SF
                        latest = succ.lf - timedelta(days=lag) if succ.lf else project_end

                    if latest < candidate:
                        candidate = latest
                node.lf = candidate

            node.ls = node.lf - timedelta(days=node.duration - 1)
            node.total_float = (node.ls - node.es).days if node.ls and node.es else 0
            node.is_critical = node.total_float == 0

        # Free float: slack before earliest successor's ES
        for tid in topo_order:
            node = nodes[tid]
            if not node.successors:
                node.free_float = (project_end - node.ef).days if node.ef else 0
                continue
            min_succ_es = min(
                nodes[succ_id].es for succ_id in node.successors if nodes[succ_id].es
            )
            node.free_float = (min_succ_es - node.ef).days - 1 if node.ef else 0
            node.free_float = max(0, node.free_float)

        # ── Save to DB ───────────────────────────────────────────────────────
        critical_path: list[str] = []
        for tid, node in nodes.items():
            db.execute(
                text(
                    "UPDATE project_task SET "
                    "  early_start  = :es, early_finish = :ef, "
                    "  late_start   = :ls, late_finish  = :lf, "
                    "  total_float  = :tf, free_float   = :ff, "
                    "  is_critical  = :ic, updated_at   = NOW() "
                    "WHERE id = :tid"
                ),
                {
                    "es": node.es, "ef": node.ef,
                    "ls": node.ls, "lf": node.lf,
                    "tf": node.total_float, "ff": node.free_float,
                    "ic": node.is_critical, "tid": tid,
                },
            )
            if node.is_critical:
                critical_path.append(tid)

        db.commit()
        return {
            "project_id": project_id,
            "project_start": str(project_start),
            "project_end": str(project_end),
            "project_duration_days": project_duration,
            "critical_path_task_ids": critical_path,
            "tasks_updated": len(nodes),
        }

    # ─── Gantt Data ───────────────────────────────────────────────────────────

    @staticmethod
    def get_gantt_data(db: Session, project_id: str) -> dict:
        """Return structured Gantt chart data (tasks + milestones + dependencies)."""
        tasks = db.execute(
            text("SELECT * FROM vw_gantt_chart WHERE project_id = :pid ORDER BY planned_start, wbs_code"),
            {"pid": project_id},
        ).fetchall()

        milestones = db.execute(
            text(
                "SELECT pm.*, pt.task_name AS linked_task_name "
                "FROM project_milestone pm "
                "LEFT JOIN project_task pt ON pt.id = pm.linked_task_id "
                "WHERE pm.project_id = :pid ORDER BY pm.target_date"
            ),
            {"pid": project_id},
        ).fetchall()

        dependencies = db.execute(
            text(
                "SELECT td.*, "
                "  pt1.task_name AS pred_name, pt2.task_name AS succ_name "
                "FROM task_dependency td "
                "JOIN project_task pt1 ON pt1.id = td.predecessor_id "
                "JOIN project_task pt2 ON pt2.id = td.successor_id "
                "WHERE td.project_id = :pid"
            ),
            {"pid": project_id},
        ).fetchall()

        return {
            "project_id": project_id,
            "tasks": [dict(r._mapping) for r in tasks],
            "milestones": [dict(r._mapping) for r in milestones],
            "dependencies": [dict(r._mapping) for r in dependencies],
        }

    # ─── Resource Loading / Utilization ──────────────────────────────────────

    @staticmethod
    def get_resource_loading(
        db: Session,
        project_id: str,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> list[dict]:
        """
        Hitung utilization rate per anggota tim proyek.
        available_hours = working days × 8 jam/hari
        utilization = planned_hours / available_hours × 100
        """
        members = db.execute(
            text(
                "SELECT * FROM vw_resource_loading WHERE project_id = :pid"
            ),
            {"pid": project_id},
        ).fetchall()

        result = []
        for m in members:
            start = date_from or m.start_date
            end   = date_to   or m.end_date

            if start and end:
                total_days      = max((end - start).days + 1, 1)
                # Simple: count weekends only (no holiday calendar)
                weeks           = total_days // 7
                remaining_days  = total_days % 7
                weekday_start   = start.weekday()
                extra_weekends  = sum(
                    1 for i in range(remaining_days)
                    if (weekday_start + i) % 7 >= 5
                )
                working_days    = total_days - (weeks * 2) - extra_weekends
                available_hours = working_days * 8 * (float(m.allocation_pct) / 100)
            else:
                available_hours = 0

            planned = float(m.planned_hours_total or 0)
            actual  = float(m.actual_hours_total or 0)
            utilization = round(planned / available_hours * 100, 1) if available_hours > 0 else 0

            result.append({
                "employee_id":      str(m.employee_id),
                "full_name":        m.full_name,
                "role_in_project":  m.role_in_project,
                "allocation_pct":   float(m.allocation_pct),
                "assigned_tasks":   int(m.assigned_tasks or 0),
                "planned_hours":    planned,
                "actual_hours":     actual,
                "available_hours":  round(available_hours, 1),
                "utilization_pct":  utilization,
                "is_overloaded":    utilization > 100,
            })

        return sorted(result, key=lambda x: -x["utilization_pct"])

    # ─── Mandays Plan vs Actual (terintegrasi project_timesheet/absensi) ────────

    @staticmethod
    def get_mandays_summary(db: Session, project_id: str) -> dict:
        """
        Plan: SUM(project_task.planned_hours) — rencana PM, manual.
        Actual: SUM(project_timesheet.hours) berstatus approved — log aktual dari
        sistem timesheet/absensi konsultan (modul Costing), bukan actual_hours
        manual di task (lebih objektif karena sudah lewat approval finance).
        1 manday = 8 jam.
        """
        planned_hours = db.execute(
            text(
                "SELECT COALESCE(SUM(planned_hours), 0) AS h FROM project_task "
                "WHERE project_id = :pid AND status != 'cancelled'"
            ),
            {"pid": project_id},
        ).scalar() or 0

        actual_hours = db.execute(
            text(
                "SELECT COALESCE(SUM(hours), 0) AS h FROM project_timesheet "
                "WHERE project_id = :pid AND status = 'approved'"
            ),
            {"pid": project_id},
        ).scalar() or 0

        by_employee = db.execute(
            text(
                "SELECT pt.employee_id, e.full_name, SUM(pt.hours) AS actual_hours "
                "FROM project_timesheet pt "
                "JOIN employee e ON e.id = pt.employee_id "
                "WHERE pt.project_id = :pid AND pt.status = 'approved' "
                "GROUP BY pt.employee_id, e.full_name ORDER BY e.full_name"
            ),
            {"pid": project_id},
        ).fetchall()

        planned_mandays = round(float(planned_hours) / 8, 1)
        actual_mandays  = round(float(actual_hours) / 8, 1)

        return {
            "planned_hours":   float(planned_hours),
            "actual_hours":    float(actual_hours),
            "planned_mandays": planned_mandays,
            "actual_mandays":  actual_mandays,
            "burn_pct":        round(actual_mandays / planned_mandays * 100, 1) if planned_mandays > 0 else 0,
            "by_employee": [
                {
                    "employee_id":  str(r.employee_id),
                    "full_name":    r.full_name,
                    "actual_hours": float(r.actual_hours),
                    "actual_mandays": round(float(r.actual_hours) / 8, 1),
                }
                for r in by_employee
            ],
        }

    # ─── Parent-child Task -> Milestone progress rollup ──────────────────────

    PROGRESS_STATUS_RANK = {"not_started": 0, "in_progress": 1, "completed": 2}

    @staticmethod
    def recompute_milestone_progress(db: Session, milestone_id: str) -> None:
        """
        Milestone = Parent (besaran), Task = Child (detail) — request user 2026-06-24,
        diperbarui ke Weighted Average 2026-06-24 (lanjutan).
        progress_pct milestone di-rollup dari WEIGHTED AVERAGE progress_pct task anaknya,
        dibobotkan pakai planned_hours (man-days). Kalau total planned_hours seluruh task
        anak = 0 (belum diisi), fallback ke bobot planned_cost. Kalau keduanya 0 (task
        belum ada estimasi sama sekali), fallback ke rata-rata sederhana (avg_pct) supaya
        tidak div-by-zero dan tetap ada angka yang masuk akal.
        Milestone TANPA task anak (task_count=0) tidak disentuh — tetap manual seperti
        sebelumnya (backward compatible utk milestone governance/non-detail).
        Status HANYA boleh maju (not_started -> in_progress -> completed), TIDAK auto-revert
        kalau weighted progress turun lagi setelah naik (hindari status flapping yang
        membingungkan PM/Finance, terutama kalau BAST/invoice sudah terlanjur dipicu saat
        100%) — progress_pct sendiri tetap update jujur naik-turun.
        """
        agg = db.execute(
            text(
                "SELECT COUNT(*) AS task_count, "
                "COALESCE(SUM(progress_pct * planned_hours), 0) AS weighted_hours_sum, "
                "COALESCE(SUM(planned_hours), 0) AS total_hours, "
                "COALESCE(SUM(progress_pct * planned_cost), 0) AS weighted_cost_sum, "
                "COALESCE(SUM(planned_cost), 0) AS total_cost, "
                "COALESCE(AVG(progress_pct), 0) AS avg_pct "
                "FROM project_task WHERE milestone_id = :mid AND status != 'cancelled'"
            ),
            {"mid": milestone_id},
        ).fetchone()
        if not agg or not agg.task_count:
            return

        if agg.total_hours and float(agg.total_hours) > 0:
            weighted_pct = float(agg.weighted_hours_sum) / float(agg.total_hours)
        elif agg.total_cost and float(agg.total_cost) > 0:
            weighted_pct = float(agg.weighted_cost_sum) / float(agg.total_cost)
        else:
            weighted_pct = float(agg.avg_pct)
        weighted_pct = round(weighted_pct)

        current = db.execute(
            text("SELECT progress_status FROM project_milestone WHERE id = :mid"),
            {"mid": milestone_id},
        ).fetchone()
        if not current:
            return

        current_rank = ProjectSetupEngine.PROGRESS_STATUS_RANK.get(current.progress_status, 0)

        if weighted_pct >= 100 and current_rank < ProjectSetupEngine.PROGRESS_STATUS_RANK["completed"]:
            db.execute(
                text(
                    "UPDATE project_milestone SET progress_pct = :pct, progress_status = 'completed', "
                    "status = 'achieved', actual_date = CURRENT_DATE WHERE id = :mid"
                ),
                {"pct": weighted_pct, "mid": milestone_id},
            )
        elif weighted_pct > 0 and current_rank < ProjectSetupEngine.PROGRESS_STATUS_RANK["in_progress"]:
            db.execute(
                text(
                    "UPDATE project_milestone SET progress_pct = :pct, progress_status = 'in_progress' "
                    "WHERE id = :mid"
                ),
                {"pct": weighted_pct, "mid": milestone_id},
            )
        else:
            db.execute(
                text("UPDATE project_milestone SET progress_pct = :pct WHERE id = :mid"),
                {"pct": weighted_pct, "mid": milestone_id},
            )

    # ─── Earned Value Management (EVM) ───────────────────────────────────────

    @staticmethod
    def get_evm(db: Session, project_id: str, status_date: Optional[date] = None) -> dict:
        """
        Earned Value Analysis:
          BAC  = Budget At Completion (total planned_amount)
          PV   = Planned Value: budget yang seharusnya terpakai s/d status_date
          EV   = Earned Value: % complete × BAC
          AC   = Actual Cost: biaya aktual terpakai s/d status_date
          SPI  = EV / PV  (Schedule Performance Index; <1 = terlambat)
          CPI  = EV / AC  (Cost Performance Index; <1 = melebihi budget)
          SV   = EV - PV  (Schedule Variance; negatif = terlambat)
          CV   = EV - AC  (Cost Variance; negatif = over budget)
          EAC  = BAC / CPI (Estimate at Completion)
          ETC  = EAC - AC  (Estimate to Complete)
          VAC  = BAC - EAC (Variance at Completion)
        """
        if status_date is None:
            status_date = date.today()

        project = db.execute(
            text("SELECT * FROM project WHERE id = :pid"), {"pid": project_id}
        ).fetchone()
        if not project:
            raise ValueError(f"Project {project_id} tidak ditemukan")

        bac_row = db.execute(
            text("SELECT COALESCE(SUM(planned_amount), 0) AS bac FROM project_budget_line WHERE project_id = :pid"),
            {"pid": project_id},
        ).fetchone()
        bac = float(bac_row.bac or 0)

        # AC: actual cost s/d status_date
        ac_row = db.execute(
            text(
                "SELECT COALESCE(SUM(actual_amount), 0) AS ac "
                "FROM project_budget_line WHERE project_id = :pid"
            ),
            {"pid": project_id},
        ).fetchone()
        ac = float(ac_row.ac or 0)

        # % complete dari task (weighted by planned_cost atau simple average)
        tasks_row = db.execute(
            text(
                "SELECT "
                "  COALESCE(AVG(progress_pct), 0) AS avg_progress, "
                "  COALESCE(SUM(planned_cost), 0) AS total_planned_cost, "
                "  COALESCE(SUM(planned_cost * progress_pct / 100.0), 0) AS weighted_ev "
                "FROM project_task "
                "WHERE project_id = :pid AND status != 'cancelled'"
            ),
            {"pid": project_id},
        ).fetchone()

        total_planned_cost = float(tasks_row.total_planned_cost or 0)
        weighted_ev = float(tasks_row.weighted_ev or 0)

        completion_pct = (weighted_ev / total_planned_cost * 100) if total_planned_cost > 0 else 0
        ev = bac * completion_pct / 100.0

        # PV: prorated berdasarkan durasi proyek hingga status_date
        start_date = project.start_date
        end_date   = project.end_date
        if start_date and end_date and end_date > start_date:
            elapsed = max((status_date - start_date).days, 0)
            total   = (end_date - start_date).days
            pct_elapsed = min(elapsed / total, 1.0)
        else:
            pct_elapsed = 0.0
        pv = bac * pct_elapsed

        spi = round(ev / pv,  3) if pv  > 0 else None
        cpi = round(ev / ac,  3) if ac  > 0 else None
        sv  = round(ev - pv,  2)
        cv  = round(ev - ac,  2)
        eac = round(bac / cpi, 2) if cpi and cpi > 0 else bac
        etc = round(eac - ac, 2)
        vac = round(bac - eac, 2)

        return {
            "project_id":        project_id,
            "status_date":       str(status_date),
            "bac":               round(bac, 2),
            "pv":                round(pv, 2),
            "ev":                round(ev, 2),
            "ac":                round(ac, 2),
            "completion_pct":    round(completion_pct, 1),
            "spi":               spi,
            "cpi":               cpi,
            "sv":                sv,
            "cv":                cv,
            "eac":               eac,
            "etc":               etc,
            "vac":               vac,
            "status": {
                "schedule": "on_track" if (spi or 1) >= 0.95 else ("at_risk" if (spi or 1) >= 0.85 else "behind"),
                "cost":     "on_track" if (cpi or 1) >= 0.95 else ("at_risk" if (cpi or 1) >= 0.85 else "over_budget"),
            },
        }

    # ─── Budget Summary ───────────────────────────────────────────────────────

    @staticmethod
    def get_budget_summary(db: Session, project_id: str) -> dict:
        """Budget vs Actual per cost_type + total + contingency."""
        rows = db.execute(
            text(
                "SELECT * FROM vw_project_budget_vs_actual WHERE project_id = :pid "
                "ORDER BY cost_type"
            ),
            {"pid": project_id},
        ).fetchall()

        items = [dict(r._mapping) for r in rows]

        total_planned  = sum(float(r.get("planned")  or 0) for r in items)
        total_actual   = sum(float(r.get("actual")   or 0) for r in items)
        total_variance = total_planned - total_actual
        burn_pct       = round(total_actual / total_planned * 100, 1) if total_planned > 0 else 0

        return {
            "project_id":     project_id,
            "lines":          items,
            "total_planned":  round(total_planned, 2),
            "total_actual":   round(total_actual, 2),
            "total_variance": round(total_variance, 2),
            "burn_pct":       burn_pct,
        }

    # ─── Risk Analysis ────────────────────────────────────────────────────────

    @staticmethod
    def get_risk_analysis(db: Session, project_id: str) -> dict:
        """
        Risk register + heatmap matrix (5×5 probability×impact grid) + financial exposure.
        """
        rows = db.execute(
            text("SELECT * FROM vw_risk_matrix WHERE project_id = :pid ORDER BY risk_score DESC"),
            {"pid": project_id},
        ).fetchall()

        risks = [dict(r._mapping) for r in rows]

        # Build 5×5 heatmap counts
        heatmap: dict[str, dict[str, int]] = {
            str(p): {str(i): 0 for i in range(1, 6)} for p in range(1, 6)
        }
        for r in risks:
            heatmap[str(r.get("probability"))][str(r.get("impact"))] += 1

        by_level = {
            "critical": [r for r in risks if r.get("risk_level") == "critical"],
            "high":     [r for r in risks if r.get("risk_level") == "high"],
            "medium":   [r for r in risks if r.get("risk_level") == "medium"],
            "low":      [r for r in risks if r.get("risk_level") == "low"],
        }

        total_exposure = sum(
            float(r.get("financial_impact") or 0) for r in risks
            if r.get("risk_level") in ("critical", "high")
        )

        return {
            "project_id":       project_id,
            "total_risks":      len(risks),
            "risks":            risks,
            "by_level":         {k: len(v) for k, v in by_level.items()},
            "heatmap":          heatmap,
            "financial_exposure_idr": round(total_exposure, 2),
        }

    # ─── WBS Cost Rollup ─────────────────────────────────────────────────────

    @staticmethod
    def get_wbs_cost_rollup(db: Session, project_id: str) -> list[dict]:
        """
        Hitung biaya planned/actual per WBS item (termasuk rollup ke parent).
        """
        wbs_items = db.execute(
            text(
                "SELECT w.*, "
                "  COALESCE(SUM(pt.planned_cost), 0) AS task_planned, "
                "  COALESCE(SUM(pt.actual_cost), 0)  AS task_actual, "
                "  COALESCE(SUM(pbl.planned_amount), 0) AS budget_planned, "
                "  COALESCE(SUM(pbl.actual_amount), 0)  AS budget_actual "
                "FROM wbs_item w "
                "LEFT JOIN project_task pt ON pt.wbs_item_id = w.id "
                "LEFT JOIN project_budget_line pbl ON pbl.wbs_item_id = w.id "
                "WHERE w.project_id = :pid "
                "GROUP BY w.id "
                "ORDER BY w.wbs_code"
            ),
            {"pid": project_id},
        ).fetchall()

        return [dict(r._mapping) for r in wbs_items]

    # ─── Burn Rate (timesheet-driven, objektif) ───────────────────────────────

    @staticmethod
    def get_burn_rate(db: Session, project_id: str) -> dict:
        """
        Financial exposure dari jam kerja aktual × tarif biaya karyawan (BRD timesheet
        2026-06-24, section 5) — BEDA sumber data dari EVM.AC (project_budget_line.actual_amount,
        input manual PM) — ini murni objektif dari project_timesheet approved × employee_cost_rate.
        Dibandingkan ke BAC yang sama dengan EVM (SUM(project_budget_line.planned_amount)) supaya
        2 angka "actual cost" (manual vs timesheet-driven) bisa dibandingkan apple-to-apple.
        Jam timesheet yang employee-nya belum punya employee_cost_rate approved tahun itu
        TIDAK ikut dihitung ke burned cost (tidak ada tarif) — dilaporkan terpisah sebagai
        unrated_hours supaya angka burn tidak diam-diam under-state tanpa disadari PM.
        """
        bac_row = db.execute(
            text("SELECT COALESCE(SUM(planned_amount), 0) AS bac FROM project_budget_line WHERE project_id = :pid"),
            {"pid": project_id},
        ).fetchone()
        bac = float(bac_row.bac or 0)

        burn_row = db.execute(
            text(
                "SELECT "
                "  COALESCE(SUM(pts.hours * ecr.unit_cost_per_hour), 0) AS burned_cost, "
                "  COALESCE(SUM(pts.hours) FILTER (WHERE ecr.unit_cost_per_hour IS NOT NULL), 0) AS rated_hours, "
                "  COALESCE(SUM(pts.hours) FILTER (WHERE ecr.unit_cost_per_hour IS NULL), 0) AS unrated_hours "
                "FROM project_timesheet pts "
                "LEFT JOIN employee_cost_rate ecr "
                "  ON ecr.employee_id = pts.employee_id "
                "  AND ecr.fiscal_year = EXTRACT(YEAR FROM pts.timesheet_date) "
                "  AND ecr.is_approved = TRUE "
                "WHERE pts.project_id = :pid AND pts.status = 'approved'"
            ),
            {"pid": project_id},
        ).fetchone()

        burned_cost = float(burn_row.burned_cost or 0)
        burn_pct = round(burned_cost / bac * 100, 2) if bac > 0 else None

        return {
            "bac":            bac,
            "burned_cost":    burned_cost,
            "burn_pct":       burn_pct,
            "rated_hours":    float(burn_row.rated_hours or 0),
            "unrated_hours":  float(burn_row.unrated_hours or 0),
        }

    # ─── Schedule Variance Alerts ──────────────────────────────────────────────

    @staticmethod
    def get_schedule_variance_alerts(db: Session, project_id: str) -> list[dict]:
        """
        BRD timesheet 2026-06-24, section 5: flag task yang actual_hours sudah melebihi
        planned_hours TAPI progress_pct masih <100% — sinyal task "membengkak" jam tanpa
        progress sepadan (kemungkinan under-estimate, blocked, atau scope creep).
        """
        rows = db.execute(
            text(
                "SELECT id, task_code, task_name, planned_hours, actual_hours, progress_pct, status "
                "FROM project_task "
                "WHERE project_id = :pid AND status != 'cancelled' "
                "  AND actual_hours > planned_hours AND progress_pct < 100 "
                "ORDER BY (actual_hours - planned_hours) DESC"
            ),
            {"pid": project_id},
        ).fetchall()
        return [
            {
                "task_id":        str(r.id),
                "task_code":      r.task_code,
                "task_name":      r.task_name,
                "planned_hours":  float(r.planned_hours or 0),
                "actual_hours":   float(r.actual_hours or 0),
                "overrun_hours":  float(r.actual_hours or 0) - float(r.planned_hours or 0),
                "progress_pct":   r.progress_pct,
                "status":         r.status,
            }
            for r in rows
        ]

    # ─── Project Health Dashboard ─────────────────────────────────────────────

    @staticmethod
    def get_project_health(db: Session, project_id: str) -> dict:
        """One-stop health dashboard: progress, schedule, cost, risk, milestones."""
        summary = db.execute(
            text("SELECT * FROM vw_project_summary WHERE id = :pid"),
            {"pid": project_id},
        ).fetchone()
        if not summary:
            raise ValueError(f"Project {project_id} tidak ditemukan")

        evm = ProjectSetupEngine.get_evm(db, project_id)
        risks = ProjectSetupEngine.get_risk_analysis(db, project_id)
        burn_rate = ProjectSetupEngine.get_burn_rate(db, project_id)
        schedule_variance_alerts = ProjectSetupEngine.get_schedule_variance_alerts(db, project_id)

        overdue_tasks = db.execute(
            text(
                "SELECT COUNT(*) AS cnt FROM project_task "
                "WHERE project_id = :pid "
                "  AND planned_end < CURRENT_DATE "
                "  AND status NOT IN ('completed','cancelled')"
            ),
            {"pid": project_id},
        ).fetchone()

        overdue_milestones = db.execute(
            text(
                "SELECT COUNT(*) AS cnt FROM project_milestone "
                "WHERE project_id = :pid "
                "  AND target_date < CURRENT_DATE "
                "  AND status = 'pending'"
            ),
            {"pid": project_id},
        ).fetchone()

        # RAG status (Red / Amber / Green)
        cpi = evm.get("cpi")
        spi = evm.get("spi")
        high_risks = risks["by_level"].get("critical", 0) + risks["by_level"].get("high", 0)

        if (cpi and cpi < 0.85) or (spi and spi < 0.85) or high_risks >= 3:
            rag = "RED"
        elif (cpi and cpi < 0.95) or (spi and spi < 0.95) or high_risks >= 1:
            rag = "AMBER"
        else:
            rag = "GREEN"

        return {
            "project_id":         project_id,
            "project_name":       summary.project_name,
            "charter_status":     summary.charter_status,
            "rag_status":         rag,
            "completion_pct":     float(summary.completion_pct or 0),
            "total_tasks":        int(summary.total_tasks or 0),
            "completed_tasks":    int(summary.completed_tasks or 0),
            "overdue_tasks":      int(overdue_tasks.cnt or 0),
            "critical_tasks":     int(summary.critical_tasks or 0),
            "pending_milestones": int(summary.pending_milestones or 0),
            "overdue_milestones": int(overdue_milestones.cnt or 0),
            "team_size":          int(summary.team_size or 0),
            "budget": {
                "planned": float(summary.total_planned_budget or 0),
                "actual":  float(summary.total_actual_cost or 0),
                "bac":     evm["bac"],
                "cpi":     cpi,
                "spi":     spi,
                "ev":      evm["ev"],
                "pv":      evm["pv"],
                "eac":     evm["eac"],
                "vac":     evm["vac"],
            },
            "risks": {
                "total":    risks["total_risks"],
                "by_level": risks["by_level"],
                "financial_exposure": risks["financial_exposure_idr"],
            },
            "burn_rate": burn_rate,
            "schedule_variance_alerts": schedule_variance_alerts,
        }

    # ─── Auto-initialize Project Charter ─────────────────────────────────────

    @staticmethod
    def initialize_project(
        db: Session,
        entity_id: str,
        project_code: str,
        project_name: str,
        industry_type: str,
        objective: str,
        start_date: date,
        end_date: date,
        budget_amount: float,
        currency: str = "IDR",
        priority: str = "medium",
        cost_center_id: Optional[str] = None,
        project_manager_id: Optional[str] = None,
        sponsor_id: Optional[str] = None,
        client_id: Optional[str] = None,
        contingency_pct: float = 5.0,
        in_scope_items: Optional[list[str]] = None,
        out_scope_items: Optional[list[str]] = None,
        assumptions: Optional[list[str]] = None,
        constraints: Optional[list[str]] = None,
        created_by: str = "system",
    ) -> dict:
        """
        Inisialisasi proyek lengkap:
        1. Insert/update project record
        2. Buat project_scope (SOW) v1
        3. Buat project_scope_item (in/out/assumption/constraint)
        4. Buat cost center type='project' jika cost_center_id None
        5. Return project_id
        """
        # Cek existing project
        existing = db.execute(
            text(
                "SELECT id FROM project WHERE entity_id = :eid AND project_code = :pc"
            ),
            {"eid": entity_id, "pc": project_code},
        ).fetchone()

        if existing:
            project_id = str(existing.id)
        else:
            project_id = str(uuid4())

            # Auto-create cost center project jika tidak disediakan
            cc_id = cost_center_id
            if not cc_id:
                cc_id = str(uuid4())
                db.execute(
                    text(
                        "INSERT INTO cost_center "
                        "(id, entity_id, cc_code, cc_name, cc_type, created_by) "
                        "VALUES (:id, :eid, :cc, :cn, 'project', :cby)"
                    ),
                    {
                        "id": cc_id, "eid": entity_id,
                        "cc": f"PRJ-{project_code}",
                        "cn": f"Project: {project_name}",
                        "cby": created_by,
                    },
                )

            db.execute(
                text(
                    "INSERT INTO project "
                    "(id, entity_id, project_code, project_name, industry_type, priority, "
                    " start_date, end_date, budget_amount, currency, contingency_pct, "
                    " cost_center_id, project_manager_id, sponsor_id, client_id, "
                    " charter_status, status, created_by) "
                    "VALUES (:id, :eid, :pc, :pn, :pt, :pri, "
                    "        :sd, :ed, :ba, :cur, :cpct, "
                    "        :ccid, :pmid, :spid, :clid, "
                    "        'draft', 'planning', :cby)"
                ),
                {
                    "id": project_id, "eid": entity_id,
                    "pc": project_code, "pn": project_name, "pt": industry_type,
                    "pri": priority, "sd": start_date, "ed": end_date,
                    "ba": budget_amount, "cur": currency, "cpct": contingency_pct,
                    "ccid": cc_id, "pmid": project_manager_id,
                    "spid": sponsor_id, "clid": client_id, "cby": created_by,
                },
            )

        # Buat project_scope (SOW)
        scope_id = str(uuid4())
        db.execute(
            text(
                "INSERT INTO project_scope "
                "(id, project_id, objective, version, is_current, created_by) "
                "VALUES (:id, :pid, :obj, 1, TRUE, :cby)"
            ),
            {"id": scope_id, "pid": project_id, "obj": objective, "cby": created_by},
        )

        # Insert scope items
        def _insert_items(items: Optional[list[str]], item_type: str) -> None:
            if not items:
                return
            for i, desc in enumerate(items, start=1):
                db.execute(
                    text(
                        "INSERT INTO project_scope_item "
                        "(id, project_id, scope_id, item_type, sequence, description) "
                        "VALUES (:id, :pid, :sid, :it, :seq, :desc)"
                    ),
                    {
                        "id": str(uuid4()), "pid": project_id, "sid": scope_id,
                        "it": item_type, "seq": i, "desc": desc,
                    },
                )

        _insert_items(in_scope_items,  "in_scope")
        _insert_items(out_scope_items, "out_of_scope")
        _insert_items(assumptions,     "assumption")
        _insert_items(constraints,     "constraint")

        db.commit()
        return {
            "project_id":   project_id,
            "scope_id":     scope_id,
            "project_code": project_code,
            "project_name": project_name,
            "charter_status": "draft",
        }

    # ─── Cost Center Helpers ──────────────────────────────────────────────────

    @staticmethod
    def get_cost_center_performance(db: Session, entity_id: str, fiscal_year: int) -> list[dict]:
        rows = db.execute(
            text(
                "SELECT * FROM vw_cost_center_performance "
                "WHERE entity_id = :eid AND fiscal_year = :fy "
                "ORDER BY cc_code"
            ),
            {"eid": entity_id, "fy": fiscal_year},
        ).fetchall()
        return [dict(r._mapping) for r in rows]
