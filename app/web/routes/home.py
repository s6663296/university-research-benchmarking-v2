from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db.models import Run

from app.web.dashboard import STATE_LABELS, pipeline
from app.web.deps import get_session, render
from app.web.routes.history import run_summaries

router = APIRouter()


def pipeline_context(request: Request, session: Session) -> dict:
    manager = request.app.state.run_manager  # 以背景程式的狀態為準
    run_id = manager.current_run_id
    run = session.get(Run, run_id) if run_id is not None else None
    running = run is not None and manager.is_running(run.id)
    moe_running = request.app.state.moe_fetcher.running
    # 播放鍵式控制：執行中可暫停／停止；暫停中可繼續／停止；其餘可開始新分析
    control = "running" if running else ("paused" if run is not None and run.status == "interrupted" else "idle")
    return dict(stages=pipeline(session, run_id, running, moe_running), polling=running or moe_running,
                control=control,
                control_run_id=run.id if run is not None else None, state_labels=STATE_LABELS)


@router.get("/pipeline")
def pipeline_partial(request: Request, session: Session = Depends(get_session)):
    return render(request, "_pipeline.html", **pipeline_context(request, session))


@router.get("/")
def home(request: Request, session: Session = Depends(get_session)):
    return render(request, "home.html", runs=run_summaries(session, limit=3),
                  **pipeline_context(request, session))
