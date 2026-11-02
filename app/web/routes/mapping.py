from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.teachers.moe import latest_import
from app.teachers.service import auto_link
from app.mapping.template import mapping_template
from app.mapping.service import (diff_versions, import_mapping, issues_of, latest_version, previous_version,
                                 recheck_mapping, units_of)
from app.web.deps import get_session, render

router = APIRouter()
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def xlsx_download(data: bytes, filename: str) -> Response:
    """下載 xlsx；中文檔名依 RFC 5987 編碼。"""
    return Response(data, media_type=XLSX,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@router.get("/mapping/template.xlsx")
def template():
    return xlsx_download(mapping_template(), "對標學校資料表_空白範本.xlsx")


def _page(request: Request, session: Session, school: str | None = None, status_code: int = 200, **extra):
    version = latest_version(session)
    ctx = dict(version=version, issues=[], diff=None, units=[], schools=[], school=school,
               message=None, error=None)
    if version is not None:
        recheck_mapping(session, version)
        prev = previous_version(session, version.id)
        units = units_of(session, version.id)
        ctx.update(
            issues=issues_of(session, version.id),
            diff=diff_versions(session, prev.id if prev else None, version.id),
            schools=sorted({u.school for u in units}),
            units=[u for u in units if not school or u.school == school],
        )
    ctx.update(extra)
    return render(request, "mapping.html", status_code=status_code, **ctx)


@router.get("/mapping")
def mapping_page(request: Request, school: str | None = None, session: Session = Depends(get_session)):
    return _page(request, session, school)


@router.post("/mapping")
async def upload_mapping(request: Request, file: UploadFile, session: Session = Depends(get_session)):
    data = await file.read()
    try:
        version, created = import_mapping(session, file.filename or "mapping.xlsx", data)
    except Exception:
        session.rollback()
        return _page(request, session, status_code=400,
                     error="無法讀取檔案，請確認上傳的是 mapping 表的 xlsx 檔")
    imp = latest_import(session)
    if imp is not None:
        auto_link(session, version.id, imp.id)
    message = "已匯入新版本" if created else "與目前版本相同，沒有建立新版本"
    return _page(request, session, message=message)
