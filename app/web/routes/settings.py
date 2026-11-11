import shutil

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import save_env
from app.db.session import clear_all_data
from app.db.settings_store import get_setting, set_setting
from app.elsevier.selfcite import usable_strategy_id
from app.elsevier.client import ConnectionStatus, make_client
from app.elsevier.quota import quota_rows
from app.web.deps import get_session, render

router = APIRouter()

# (欄位, 名稱, 型別, 最小值)
NUMERIC_FIELDS = [
    ("rate_per_sec", "每秒請求數", float, 0.01),
    ("max_retries", "重試次數", int, 0),
    ("backoff_base_sec", "重試等待基準秒數", float, 0),
    ("timeout_sec", "逾時秒數", int, 1),
    ("cache_days", "快取有效天數", int, 0),
    ("max_concurrency", "同時請求數上限", int, 1),
    ("rate_limit_retries", "遇到限速（HTTP 429）的重試次數", int, 0),
    ("max_backoff_sec", "限速時單次等待上限秒數", float, 1),
    ("anomaly_change_threshold", "篇數變化門檻", float, 0.01),
    ("rank_highlight_top", "排名總表標紅名次", int, 0),
]
TEACHER_COLUMN_CHOICES = {"total": "專任教師數-教師總數總計", "asst_prof_plus": "專任助理教授以上人數-總計"}
SELF_CITATION_CHOICES = {
    "scival_pubset": "排除自我引用",
    "none": "不排除自我引用",
}


def mask_key(key: str) -> str:
    return f"已設定（末 4 碼 ****{key[-4:]}）" if key else "未設定"


def _context(session: Session, request: Request, **extra) -> dict:
    values = {f: get_setting(session, f) for f, *_ in NUMERIC_FIELDS}
    ctx = dict(
        numeric_fields=NUMERIC_FIELDS, values=values,
        teacher_column=get_setting(session, "teacher_column"), teacher_choices=TEACHER_COLUMN_CHOICES,
        strategy=usable_strategy_id(get_setting(session, "self_citation_strategy")), strategy_choices=SELF_CITATION_CHOICES,
        key_status=mask_key(request.app.state.settings.elsevier_api_key),
        insttoken_status=mask_key(request.app.state.settings.elsevier_insttoken),
        errors=[], saved=False, reset=request.query_params.get("reset") == "1",
    )
    ctx.update(extra)
    return ctx


@router.get("/settings")
def settings_page(request: Request, session: Session = Depends(get_session)):
    return render(request, "settings.html", **_context(session, request))


@router.post("/settings")
async def save_settings(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    errors, parsed = [], {}
    for field, label, typ, minimum in NUMERIC_FIELDS:
        try:
            value = typ(str(form.get(field, "")).strip())
            if value < minimum:
                raise ValueError
            parsed[field] = value
        except ValueError:
            errors.append(f"{label}必須是不小於 {minimum} 的{'整數' if typ is int else '數字'}")
    if form.get("teacher_column") not in TEACHER_COLUMN_CHOICES:
        errors.append("教師數採用欄位不正確")
    if form.get("self_citation_strategy") not in SELF_CITATION_CHOICES:
        errors.append("自我引用策略不正確")
    if errors:
        return render(request, "settings.html", status_code=400, **_context(session, request, errors=errors))

    for field, value in parsed.items():
        set_setting(session, field, value)
    set_setting(session, "teacher_column", form["teacher_column"])
    set_setting(session, "self_citation_strategy", form["self_citation_strategy"])
    return render(request, "settings.html", **_context(session, request, saved=True))


@router.post("/settings/api-key")
async def save_api_key(request: Request):
    form = await request.form()
    settings = request.app.state.settings
    api_key = str(form.get("api_key", "")).strip() or settings.elsevier_api_key
    insttoken = str(form.get("insttoken", "")).strip()
    save_env(settings.env_path, api_key, insttoken)
    request.app.state.settings = settings.model_copy(
        update={"elsevier_api_key": api_key, "elsevier_insttoken": insttoken})
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/reset")
def reset_all(request: Request):
    """清除所有資料：資料庫、產出檔與 .env 中的 API Key；設定回到預設值。"""
    if request.app.state.run_manager.any_running():
        raise HTTPException(409, "仍有分析在抓取中，請等它結束後再清除")
    settings = request.app.state.settings
    clear_all_data(request.app.state.engine)
    shutil.rmtree(settings.data_dir / "exports", ignore_errors=True)
    save_env(settings.env_path, "", "")
    request.app.state.settings = settings.model_copy(update={"elsevier_api_key": "", "elsevier_insttoken": ""})
    return RedirectResponse("/settings?reset=1", status_code=303)


@router.get("/status/panel")
def quota_panel(request: Request, session: Session = Depends(get_session)):
    """首頁載入時用：只讀已存的額度，不打 API。"""
    return render(request, "_quota.html", quotas=quota_rows(session), check=None)


@router.get("/status")
async def status(request: Request, session: Session = Depends(get_session)):
    """檢查連線：Scopus、SciVal 各打一次，順便更新兩者的額度。"""
    settings = request.app.state.settings
    if not settings.elsevier_api_key:
        check = ConnectionStatus(False, "尚未設定 API Key，請到「設定」頁填寫")
    else:
        client = make_client(settings, session)
        try:
            check = await client.check_connection()
        finally:
            await client.aclose()
    session.expire_all()
    return render(request, "_quota.html", quotas=quota_rows(session), check=check)
