import httpx
from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db.settings_store import get_setting
from app.mapping.service import latest_version
from app.teachers.moe import TEACHER_COLUMNS, import_moe, import_years, latest_import
from app.teachers.moe_fetch import SOURCE_NAME, SOURCE_URL, MoeFetchError
from app.teachers.service import (teacher_keys, auto_link, add_override, set_links, check_links, teacher_count,
                                  unit_choices)
from app.web.deps import get_session, render

router = APIRouter()


def _page(request: Request, session: Session, year: int | None = None, status_code: int = 200, error=None):
    imp, version = latest_import(session), latest_version(session)
    years = import_years(imp) if imp else []
    year = year if year in years else (years[-1] if years else None)
    column = get_setting(session, "teacher_column")
    rows, left = [], []
    if imp and version:
        for school, dept, ncu_dept in teacher_keys(session, version.id):
            tc = teacher_count(session, imp.id, year, column, school, dept)
            rows.append(dict(school=school, dept=dept, ncu_dept=ncu_dept, tc=tc))
        left = [dict(u=u, choices=unit_choices(session, imp.id, u.school, u.dept_zh))
                for u in sum(check_links(session, version.id, imp.id), [])]
    return render(request, "teachers.html", status_code=status_code, imp=imp, version=version, years=years,
                  year=year, column_label=TEACHER_COLUMNS[column], rows=rows, unmatched=left, error=error)


@router.get("/teachers")
def teachers_page(request: Request, year: int | None = None, session: Session = Depends(get_session)):
    return _page(request, session, year)


@router.post("/teachers/import")
async def import_teachers(request: Request, file: UploadFile, session: Session = Depends(get_session)):
    data = await file.read()
    try:
        imp = import_moe(session, file.filename or "moe.xlsx", data)
    except Exception:
        session.rollback()
        return _page(request, session, status_code=400,
                     error="無法讀取檔案，請確認上傳的是教育部「教1-1」下載的 xlsx 檔")
    version = latest_version(session)
    if version:
        auto_link(session, version.id, imp.id)
    return RedirectResponse("/teachers", status_code=303)


def _fetch_panel(request: Request, years: list[int], error: str | None = None, poll: bool = False):
    fetcher = request.app.state.moe_fetcher
    # 輪詢時剛完成匯入：標記起來，關閉對話框後重新載入整頁以更新表格
    just_imported = poll and not fetcher.running and fetcher.job is not None and fetcher.job.status == "done"
    return render(request, "_moe_fetch.html", fetcher=fetcher, job=fetcher.job, years=years, error=error,
                  just_imported=just_imported, source_url=SOURCE_URL, source_name=SOURCE_NAME)


@router.get("/teachers/fetch")
async def fetch_panel(request: Request, poll: bool = False):
    try:
        years = await request.app.state.moe_fetcher.available_years()
    except (MoeFetchError, httpx.HTTPError):
        return _fetch_panel(request, [], error="無法從教育部網站取得學年度清單，請稍後重新整理")
    return _fetch_panel(request, years, poll=poll)


@router.post("/teachers/fetch")
async def start_fetch(request: Request):
    fetcher = request.app.state.moe_fetcher
    years = [int(y) for y in (await request.form()).getlist("years")]
    if not years:
        return _fetch_panel(request, fetcher.years, error="請至少選一個學年度")
    fetcher.start(years)
    return _fetch_panel(request, fetcher.years)


@router.get("/teachers/fetch/file")
def fetched_file(request: Request):
    job = request.app.state.moe_fetcher.job
    if not job or not job.file_path or not job.file_path.exists():
        return RedirectResponse("/teachers", status_code=303)
    return FileResponse(job.file_path, filename=job.file_path.name)


@router.post("/teachers/link")
async def link_unit(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    set_links(session, str(form["school"]), str(form["dept_zh"]), [str(c) for c in form.getlist("codes")])
    return RedirectResponse("/teachers", status_code=303)


@router.post("/teachers/override")
async def override(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    year = int(str(form.get("academic_year")))
    try:
        add_override(session, str(form["school"]), str(form["dept_zh"]), year,
                     int(str(form.get("count", "")).strip()), str(form.get("reason", "")))
    except ValueError as e:
        message = str(e) if "原因" in str(e) or "小於" in str(e) else "教師數必須是整數"
        return _page(request, session, year, status_code=400, error=message)
    return RedirectResponse(f"/teachers?year={year}", status_code=303)
