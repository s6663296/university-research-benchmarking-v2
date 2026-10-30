from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db.settings_store import get_setting
from app.mapping.service import latest_version
from app.teachers.moe import TEACHER_COLUMNS, import_moe, import_years, latest_import
from app.teachers.service import teacher_keys, auto_link, add_override, set_links, teacher_count, unit_choices, unmatched
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
        left = [dict(u=u, choices=unit_choices(session, imp.id, u.school)) for u in unmatched(session, version.id)]
    return render(request, "teachers.html", status_code=status_code, imp=imp, version=version, years=years,
                  year=year, column_label=TEACHER_COLUMNS[column], rows=rows, unmatched=left, error=error)


@router.get("/teachers")
def teachers_page(request: Request, year: int | None = None, session: Session = Depends(get_session)):
    return _page(request, session, year)


@router.post("/teachers/import")
async def import_teachers(request: Request, file: UploadFile, session: Session = Depends(get_session)):
    data = await file.read()
    try:
        imp, _ = import_moe(session, file.filename or "moe.xlsx", data)
    except Exception:
        session.rollback()
        return _page(request, session, status_code=400,
                     error="無法讀取檔案，請確認上傳的是教育部「教1-1」下載的 xlsx 檔")
    version = latest_version(session)
    if version:
        auto_link(session, version.id, imp.id)
    return RedirectResponse("/teachers", status_code=303)


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
