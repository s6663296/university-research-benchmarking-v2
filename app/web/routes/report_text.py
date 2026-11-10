"""編輯簡報的目錄名稱與各頁備註。"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db.settings_store import get_setting, set_setting
from app.reports.report_text import BY_KEY, FIELDS, PAGES, VARIABLES, clean_overrides, page_variables, sample_values
from app.web.deps import get_session, render

router = APIRouter()


def _pages(overrides: dict) -> list[dict]:
    """編輯頁每個項目的目前值：有修改用修改值，沒有就是預設值。"""
    return [dict(page=page, current={f: overrides.get(page.key, {}).get(f, getattr(page, f)) for f in FIELDS},
                 customized=page.key in overrides, variables=page_variables(page)) for page in PAGES]


@router.get("/report-text")
def report_text_page(request: Request, session: Session = Depends(get_session)):
    samples = sample_values(session)
    return render(request, "report_text.html", pages=_pages(get_setting(session, "report_text")),
                  variables={name: {"label": label, "sample": samples[name]} for name, label in VARIABLES.items()},
                  saved=request.query_params.get("saved") == "1")


@router.post("/report-text")
async def save_report_text(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    raw = {page.key: {f: str(form[f"{page.key}.{f}"]) for f in FIELDS if f"{page.key}.{f}" in form} for page in PAGES}
    overrides = clean_overrides(raw)
    reset_key = form.get("reset_key")           # 「恢復此頁預設」：其他項目尚未儲存的修改照常保存
    if reset_key == "__all__":
        overrides = {}
    elif reset_key:
        overrides.pop(str(reset_key), None)
    set_setting(session, "report_text", overrides)
    current = reset_key if reset_key in BY_KEY else form.get("active_key")    # 存檔後回到正在編輯的項目
    anchor = f"#{current}" if current in BY_KEY else ""
    return RedirectResponse(f"/report-text?saved=1{anchor}", status_code=303)
