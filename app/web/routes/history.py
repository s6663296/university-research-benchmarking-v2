import shutil

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.db.models import AiSummary, Anomaly, Export, Result, Run, RunPublication, RunUnit
from app.metrics.indicators import BY_ID
from app.reports.provenance import Explainer, Explanation, Source
from app.reports.tables import fmt
from app.runs.compare import compare_runs
from app.runs.renumber import renumber_runs
from app.runs.scope import COMPARISON_LABELS, RunConfig
from app.web.deps import get_session, render
from app.web.routes.runs import RUN_STATUS, get_run

router = APIRouter()


def run_summaries(session: Session, limit: int | None = None) -> list[dict]:
    q = select(Run).order_by(Run.id.desc())
    if limit:
        q = q.limit(limit)
    out = []
    for run in session.scalars(q):
        cfg = RunConfig.from_json(run.scope_json)
        open_count = session.scalar(select(func.count()).select_from(Anomaly).where(
            Anomaly.run_id == run.id, Anomaly.status != "confirmed"))
        out.append(dict(run=run, status=RUN_STATUS.get(run.status, run.status), open=open_count,
                        depts=cfg.ncu_depts,
                        comparisons="、".join(COMPARISON_LABELS[c] for c in sorted(cfg.comparisons))))
    return out


@router.get("/runs")
def history(request: Request, session: Session = Depends(get_session)):
    return render(request, "runs.html", runs=run_summaries(session))


@router.post("/runs/{run_id}/delete")
def delete_run(request: Request, run_id: int, session: Session = Depends(get_session)):
    run = get_run(session, run_id)
    if request.app.state.run_manager.is_running(run_id):
        raise HTTPException(409, "這個分析仍在抓取中，無法刪除")
    if request.app.state.ai_jobs.any_running():
        raise HTTPException(409, "AI 總結仍在產生中，請等它結束後再刪除")
    for model in (Result, Anomaly, RunPublication, RunUnit, Export, AiSummary):
        session.execute(delete(model).where(model.run_id == run_id))
    # 其他分析若以它為前期，清掉指向（不設外鍵，由程式維護）
    session.execute(update(Run).where(Run.ai_baseline_run_id == run_id).values(ai_baseline_run_id=None))
    session.execute(update(AiSummary).where(AiSummary.baseline_run_id == run_id).values(baseline_run_id=None))
    session.delete(run)
    session.commit()
    data_dir = request.app.state.settings.data_dir
    shutil.rmtree(data_dir / "exports" / str(run_id), ignore_errors=True)
    if not request.app.state.run_manager.any_running() and not request.app.state.ai_jobs.any_running():  # 有分析在跑時編號不能動，留到下次刪除或重啟
        renumber_runs(session, data_dir)
    return RedirectResponse("/runs", status_code=303)


@router.get("/runs/compare")
def compare(request: Request, a: int, b: int, all: int = 0, session: Session = Depends(get_session)):
    run_a, run_b = get_run(session, a), get_run(session, b)
    rows = compare_runs(session, a, b)
    if not all:
        rows = [r for r in rows if r.delta is None or r.delta != 0]
    ex_a, ex_b = Explainer(session, a), Explainer(session, b)

    def explain(r) -> dict:
        label = BY_ID[r.indicator_id].label
        delta = None
        if r.delta is not None:
            delta = Explanation(title=f"差異（{label}）　{r.school}－{r.ncu_dept}", formula="B − A",
                                substituted=f"{fmt(r.b)} − {fmt(r.a)} = {'+' if r.delta > 0 else ''}{fmt(r.delta)}",
                                sources=[Source("A", f"分析 #{a}（{run_a.created_at:%Y/%m/%d %H:%M}）"),
                                         Source("B", f"分析 #{b}（{run_b.created_at:%Y/%m/%d %H:%M}）")])
        return dict(a=ex_a.cell(r.unit_a, r.comparison, r.indicator_id, "value") if r.unit_a else None,
                    b=ex_b.cell(r.unit_b, r.comparison, r.indicator_id, "value") if r.unit_b else None, delta=delta)

    return render(request, "compare.html", a=run_a, b=run_b, rows=[(r, explain(r)) for r in rows], show_all=all,
                  labels={k: v.label for k, v in BY_ID.items()}, comparison_labels=COMPARISON_LABELS, fmt=fmt)
