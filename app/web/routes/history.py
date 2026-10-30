from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Anomaly, Run
from app.metrics.indicators import BY_ID
from app.reports.tables import fmt
from app.runs.compare import compare_runs
from app.runs.scope import COMPARISON_LABELS, RunConfig
from app.web.deps import get_session, render
from app.web.routes.runs import RUN_STATUS

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
                        depts="、".join(cfg.ncu_depts),
                        comparisons="、".join(COMPARISON_LABELS[c] for c in sorted(cfg.comparisons))))
    return out


@router.get("/runs")
def history(request: Request, session: Session = Depends(get_session)):
    return render(request, "runs.html", runs=run_summaries(session))


@router.get("/runs/compare")
def compare(request: Request, a: int, b: int, all: int = 0, session: Session = Depends(get_session)):
    rows = compare_runs(session, a, b)
    if not all:
        rows = [r for r in rows if r.delta is None or r.delta != 0]
    return render(request, "compare.html", a=session.get(Run, a), b=session.get(Run, b), rows=rows, show_all=all,
                  labels={k: v.label for k, v in BY_ID.items()}, comparison_labels=COMPARISON_LABELS, fmt=fmt)
