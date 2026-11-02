"""計畫經費統計表：匯入經分表（計畫管理系統的「計畫基本資料」xlsx）。"""

from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.funding.parser import FundingFormatError
from app.funding.template import funding_template
from app.funding.service import (INDUSTRY_CATEGORIES, NSTC_CATEGORIES, category_of, delete_import,
                                 effective_imports, import_funding, import_years, imports, money, projects_of)
from app.web.deps import get_session, render
from app.web.routes.mapping import xlsx_download

router = APIRouter()
UNSET = "（未設定）"


def _summary(session: Session, college: str | None):
    """目前採用的資料（每年度最新匯入的檔案），依計畫主持人主聘單位彙總：學院清單、（系所, 年度）→ 件數與金額。"""
    sources = effective_imports(session)
    colleges: set[str] = set()
    table: dict[tuple, dict] = defaultdict(lambda: {k: [0, Decimal(0)] for k in ("nstc", "industry", "other")})
    for p in projects_of(session, sources):
        c = p.pi_college or UNSET
        colleges.add(c)
        if c == college:
            cell = table[(p.pi_dept or UNSET, p.year)][category_of(p.main_category) or "other"]
            cell[0] += 1
            cell[1] += p.amount
    return sorted(colleges), sorted(table.items()), sources


def _page(request: Request, session: Session, college: str | None = None, error: str | None = None,
          status_code: int = 200, warnings: list[str] | None = None):
    colleges, _, _ = _summary(session, None)
    if college not in colleges:
        college = "管理學院" if "管理學院" in colleges else (colleges[0] if colleges else None)
    _, rows, sources = _summary(session, college)
    return render(request, "funding.html", status_code=status_code, imports=imports(session), years_of=import_years,
                  colleges=colleges, college=college, rows=rows, sources=sources, money=money, error=error,
                  warnings=warnings or [], nstc=NSTC_CATEGORIES, industry=INDUSTRY_CATEGORIES)


@router.get("/funding")
def funding_page(request: Request, college: str | None = None, session: Session = Depends(get_session)):
    return _page(request, session, college)


@router.get("/funding/template.xlsx")
def template():
    return xlsx_download(funding_template(), "經分表_空白範本.xlsx")


@router.post("/funding/import")
async def import_file(request: Request, file: UploadFile, session: Session = Depends(get_session)):
    data = await file.read()
    try:
        _, warnings = import_funding(session, file.filename or "經分表.xlsx", data)
    except FundingFormatError as e:
        session.rollback()
        return _page(request, session, error=f"無法匯入：{e}", status_code=400)
    except Exception:
        session.rollback()
        return _page(request, session, status_code=400,
                     error="無法讀取檔案，請確認上傳的是計畫管理系統匯出的「計畫基本資料」xlsx")
    if warnings:
        return _page(request, session, warnings=warnings)
    return RedirectResponse("/funding", status_code=303)


@router.post("/funding/{import_id}/delete")
def remove(import_id: int, session: Session = Depends(get_session)):
    delete_import(session, import_id)
    return RedirectResponse("/funding", status_code=303)
