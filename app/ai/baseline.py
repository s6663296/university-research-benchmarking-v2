"""挑選 AI 摘要要比較的前期分析。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Run
from app.runs.scope import RunConfig


def _has_dept(run: Run, dept: str) -> bool:
    return dept in RunConfig.from_json(run.scope_json).ncu_depts


def auto_baseline(session: Session, run: Run, dept: str) -> Run | None:
    """同系所、期間較早的已完成分析中，期間最晚者；同期間取最後完成（id 最大）。"""
    rows = session.scalars(
        select(Run).where(Run.status == "done", Run.id != run.id, Run.period_to < run.period_to)
        .order_by(Run.period_to.desc(), Run.id.desc())).all()
    return next((r for r in rows if _has_dept(r, dept)), None)


def baseline_for(session: Session, run: Run, dept: str) -> Run | None:
    """使用者指定前期時只認指定的那一筆（不合格就沒有前期）；沒指定才自動挑。"""
    if run.ai_baseline_run_id is None:
        return auto_baseline(session, run, dept)
    chosen = session.get(Run, run.ai_baseline_run_id)
    if chosen is not None and chosen.status == "done" and _has_dept(chosen, dept):
        return chosen
    return None


def basis_differences(run: Run, base: Run, dept: str) -> list[str]:
    """兩期計算基準不同之處（白話），讓摘要不要把基準差異當成表現變化。"""
    a, b = RunConfig.from_json(run.scope_json), RunConfig.from_json(base.scope_json)
    diffs = []
    if set(a.schools_for(dept)) != set(b.schools_for(dept)):
        diffs.append("對標學校不同")
    if run.academic_year != base.academic_year:
        diffs.append("教師數學年度不同")
    if run.teacher_column != base.teacher_column:
        diffs.append("教師數採用欄位不同")
    if run.self_citation_strategy != base.self_citation_strategy:
        diffs.append("自我引用處理方式不同")
    if run.period_to - run.period_from != base.period_to - base.period_from:
        diffs.append("分析年數不同")
    return diffs


def baseline_choices(session: Session, run: Run) -> list[Run]:
    """下拉選單：其他已完成的分析，新到舊；同期間重跑的不算前期，不列入。"""
    same_period = (Run.period_from == run.period_from) & (Run.period_to == run.period_to)
    return list(session.scalars(
        select(Run).where(Run.status == "done", Run.id != run.id, ~same_period).order_by(Run.id.desc())))
