import re
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from app.db.models import Run
from app.mapping.parser import NCU_SHEET, is_ncu
from app.reports.pptx_report import AI_NOTE

WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=WEB_DIR / "templates")
templates.env.globals["is_ncu"] = is_ncu
templates.env.globals["NCU_SHEET"] = NCU_SHEET
templates.env.globals["AI_NOTE"] = AI_NOTE  # 網頁預覽與簡報用同一句註記


def split_figure(line: str) -> tuple[str, str]:
    """「參考文獻 2,889／3,537 篇」→（「參考文獻」,「2,889／3,537 篇」）：流程圖欄位太窄時數字換到下一行。"""
    m = re.match(r"(\D*?)\s*(\d.*)", line)
    return (m.group(1), m.group(2)) if m and m.group(1) else (line, "")


templates.env.filters["split_figure"] = split_figure


def run_no(run: Run, session: Session | None = None) -> int:
    """畫面上的分析編號：依建立先後排成 1…N，刪除後立即重排（資料庫 id 在有分析執行時不會重排）。"""
    session = session or object_session(run)
    if session is None:
        return run.id
    return session.scalar(select(func.count()).select_from(Run).where(Run.id <= run.id))


templates.env.globals["run_no"] = run_no


def get_session(request: Request):
    with request.app.state.session_factory() as session:
        yield session


def render(request: Request, name: str, status_code: int = 200, **ctx):
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
