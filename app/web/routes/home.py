from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
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
    # 播放鍵式控制：執行中可暫停／停止；暫停中可繼續／停止；已完成只提供前往產出（避免重複按開始分析）；
    # 其餘可開始新分析
    if running:
        control = "running"
    elif run is not None and run.status == "interrupted":
        control = "paused"
    elif run is not None and run.status == "done":
        control = "done"
    else:
        control = "idle"
    return dict(stages=pipeline(session, run_id, running, moe_running), polling=running or moe_running,
                control=control, watch=run.id if running else None,
                control_run_id=run.id if run is not None else None, state_labels=STATE_LABELS)


def downloads_url(run_id: int) -> str:
    return f"/runs/{run_id}?ind=downloads"


@router.get("/pipeline")
def pipeline_partial(request: Request, watch: int | None = None, session: Session = Depends(get_session)):
    """watch：首頁輪詢時正在執行的分析；它做完時直接跳到產出頁。"""
    if watch is not None and not request.app.state.run_manager.is_running(watch):
        run = session.get(Run, watch)
        if run is not None and run.status == "done":
            return Response(headers={"HX-Redirect": downloads_url(watch)})
    return render(request, "_pipeline.html", **pipeline_context(request, session))


@router.get("/")
def home(request: Request, session: Session = Depends(get_session)):
    return render(request, "home.html", runs=run_summaries(session, limit=3),
                  **pipeline_context(request, session))
