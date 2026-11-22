"""壓縮檔下載：在背景逐份產生簡報，按鈕旁顯示進度，完成後才下載。

產生幾十份簡報要一段時間，直接用連結下載時畫面沒有任何反應，使用者不知道在做事。
這裡改成：POST 開始 → 每半秒 GET 進度 → 完成後 GET file 取得檔案。
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from app.web.routes.ai_summary import write_college_zip
from app.web.routes.runs import write_dept_zip

log = logging.getLogger(__name__)
router = APIRouter()

KINDS = {"pptx": write_dept_zip, "college": write_college_zip}


@dataclass
class ExportJob:
    done: int = 0
    total: int = 0
    path: Path | None = None
    error: str | None = None
    task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def state(self) -> dict:
        return {"done": self.done, "total": self.total, "ready": self.path is not None, "error": self.error}


def _jobs(request: Request) -> dict[tuple[int, str], ExportJob]:
    if not hasattr(request.app.state, "export_jobs"):
        request.app.state.export_jobs = {}
    return request.app.state.export_jobs


def _kind(kind: str):
    if kind not in KINDS:
        raise HTTPException(404, "沒有這種下載")
    return KINDS[kind]


@router.post("/runs/{run_id}/exports/{kind}")
async def start_export(request: Request, run_id: int, kind: str):
    work = _kind(kind)
    jobs = _jobs(request)
    job = jobs.get((run_id, kind))
    if job is None or not job.running:   # 同一個檔案正在產生時，接著看同一份進度
        job = jobs[run_id, kind] = ExportJob()
        session_factory, settings = request.app.state.session_factory, request.app.state.settings

        def progress(done: int, total: int) -> None:
            job.done, job.total = done, total

        def build() -> Path:
            with session_factory() as session:
                return work(session, settings, run_id, progress)

        async def run() -> None:
            try:
                job.path = await asyncio.to_thread(build)
            except HTTPException as e:
                job.error = str(e.detail)
            except Exception as e:   # 記錄後顯示在按鈕旁，不讓背景工作默默失敗
                log.exception("產生壓縮檔失敗：分析 %s、%s", run_id, kind)
                job.error = f"產生失敗：{type(e).__name__}: {e}"

        job.task = asyncio.create_task(run())
    return JSONResponse(job.state(), status_code=202)


@router.get("/runs/{run_id}/exports/{kind}")
def export_progress(request: Request, run_id: int, kind: str):
    _kind(kind)
    job = _jobs(request).get((run_id, kind))
    if job is None:
        raise HTTPException(404, "沒有進行中的下載")
    return job.state()


@router.get("/runs/{run_id}/exports/{kind}/file")
def export_file(request: Request, run_id: int, kind: str):
    _kind(kind)
    job = _jobs(request).get((run_id, kind))
    if job is None or job.path is None:
        raise HTTPException(404, "檔案還沒準備好")
    return FileResponse(job.path, media_type="application/zip", filename=job.path.name)
