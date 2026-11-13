"""頁面連動：背景分析的狀態改變時（開始、完成、暫停、停止、失敗、刪除），讓已開著的頁面自動重新整理。

頁面記下載入當下的狀態簽章，每隔幾秒問 /watch 一次；簽章不同就回 HX-Refresh 讓瀏覽器重新載入。
執行中的逐步進度由各頁自己的輪詢（首頁流程圖、分析進度表）更新，不算進簽章，以免執行中整頁一直重載。
"""

import hashlib

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Anomaly, Export, Run
from app.web.deps import get_session

router = APIRouter()


def state_signature(request: Request, session: Session, run_id: int | None = None) -> str:
    """run_id：只看這個分析；None：所有分析（首頁、版本紀錄）。"""
    manager = request.app.state.run_manager
    runs = select(Run.id, Run.status).order_by(Run.id)
    opened = select(Anomaly.run_id, func.count()).where(Anomaly.status != "confirmed").group_by(Anomaly.run_id)
    exports = select(Export.run_id, func.count()).group_by(Export.run_id)
    if run_id is not None:
        runs = runs.where(Run.id == run_id)
        opened = opened.where(Anomaly.run_id == run_id)
        exports = exports.where(Export.run_id == run_id)
    open_counts, export_counts = dict(session.execute(opened).all()), dict(session.execute(exports).all())
    parts = [f"{rid}:{status}:{int(manager.is_running(rid))}:{open_counts.get(rid, 0)}:{export_counts.get(rid, 0)}"
             for rid, status in session.execute(runs)]
    parts.append(f"show:{manager.display_run_id}")
    return hashlib.md5("|".join(parts).encode()).hexdigest()[:12]


def watch_url(request: Request, session: Session, run_id: int | None = None) -> str:
    query = f"sig={state_signature(request, session, run_id)}"
    return f"/watch?{query}" + (f"&run={run_id}" if run_id is not None else "")


@router.get("/watch")
def watch(request: Request, sig: str, run: int | None = None, session: Session = Depends(get_session)):
    response = Response(status_code=200)
    if sig != state_signature(request, session, run):
        response.headers["HX-Refresh"] = "true"
    return response
