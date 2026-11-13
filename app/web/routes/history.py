import json
import shutil

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import Anomaly, Export, Result, Run, RunPublication, RunUnit
from app.runs.renumber import renumber_runs
from app.runs.scope import COMPARISON_LABELS, RunConfig
from app.web.deps import get_session, render
from app.web.live import watch_url
from app.web.routes.runs import RUN_STATUS, get_run

router = APIRouter()

# 版本紀錄的「API 使用」欄，各 API 額度分開計算所以分開列
USAGE_LABELS = {"scopus": "Scopus Search", "abstract": "Abstract Retrieval", "scival": "SciVal"}
RESUMABLE = {"interrupted", "stopped", "failed"}  # 未在背景執行、可從中斷處繼續的狀態


def api_usage(run: Run) -> list[tuple[str, int]] | None:
    """None：這個分析在開始記錄用量之前建立；空串列：全部取自快取，沒有用到額度。"""
    usage = json.loads(run.api_usage_json or "{}")
    if not usage:
        return None
    return [(label, usage[api]) for api, label in USAGE_LABELS.items() if usage.get(api)]


def run_summaries(session: Session, limit: int | None = None) -> list[dict]:
    q = select(Run).order_by(Run.id.desc())
    if limit:
        q = q.limit(limit)
    out = []
    for run in session.scalars(q):
        cfg = RunConfig.from_json(run.scope_json)
        open_count = session.scalar(select(func.count()).select_from(Anomaly).where(
            Anomaly.run_id == run.id, Anomaly.status != "confirmed"))
        out.append(dict(run=run, status=RUN_STATUS.get(run.status, run.status), open=open_count, resumable=run.status in RESUMABLE,
                        depts=cfg.ncu_depts, usage=api_usage(run),
                        comparisons="、".join(COMPARISON_LABELS[c] for c in sorted(cfg.comparisons))))
    return out


@router.get("/runs")
def history(request: Request, session: Session = Depends(get_session)):
    return render(request, "runs.html", runs=run_summaries(session), watch=watch_url(request, session))


@router.post("/runs/{run_id}/delete")
def delete_run(request: Request, run_id: int, session: Session = Depends(get_session)):
    run = get_run(session, run_id)
    if request.app.state.run_manager.is_running(run_id):
        raise HTTPException(409, "這個分析仍在抓取中，無法刪除")
    for model in (Result, Anomaly, RunPublication, RunUnit, Export):
        session.execute(delete(model).where(model.run_id == run_id))
    session.delete(run)
    session.commit()
    data_dir = request.app.state.settings.data_dir
    shutil.rmtree(data_dir / "exports" / str(run_id), ignore_errors=True)
    if not request.app.state.run_manager.any_running():  # 有分析在跑時編號不能動，留到下次刪除或重啟
        renumber_runs(session, data_dir)
    return RedirectResponse("/runs", status_code=303)
