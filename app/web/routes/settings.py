from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import save_env
from app.db.models import SchoolAlias
from app.db.settings_store import get_setting, set_setting
from app.elsevier.client import make_client
from app.web.deps import get_session, render

router = APIRouter()

# (欄位, 名稱, 型別, 最小值)
NUMERIC_FIELDS = [
    ("rate_per_sec", "每秒請求數", float, 0.01),
    ("max_retries", "重試次數", int, 0),
    ("backoff_base_sec", "重試等待基準秒數", float, 0),
    ("timeout_sec", "逾時秒數", int, 1),
    ("cache_days", "快取有效天數", int, 0),
    ("scival_batch_size", "SciVal 每批篇數", int, 1),
    ("anomaly_change_threshold", "篇數變化門檻", float, 0.01),
    ("rank_highlight_top", "排名總表標紅名次", int, 0),
]
TEACHER_COLUMN_CHOICES = {"total": "專任教師數-教師總數總計", "asst_prof_plus": "專任助理教授以上人數-總計"}
SELF_CITATION_CHOICES = {
    "scival_param": "SciVal includeSelfCitations=false（待驗證）",
    "scopus_citation_overview": "Scopus Citation Overview（需另外申請授權）",
    "none": "不排除自我引用",
}


def mask_key(key: str) -> str:
    return f"已設定（末 4 碼 ****{key[-4:]}）" if key else "未設定"


def parse_pairs(text: str) -> dict[str, str]:
    """每行一筆「全名=簡稱」。"""
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = (x.strip() for x in line.split("=", 1))
            if k and v:
                out[k] = v
    return out


def parse_aliases(text: str) -> list[tuple[str, str, str]]:
    """每行一筆「分頁名稱, 教育部校名, 報告簡稱」。"""
    rows = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = [x.strip() for x in line.replace("，", ",").split(",")]
        if len(parts) != 3 or not all(parts):
            raise ValueError(f"學校對照第 {n} 行格式錯誤，應為「分頁名稱, 教育部校名, 報告簡稱」")
        rows.append((parts[0], parts[1], parts[2]))
    return rows


def _context(session: Session, request: Request, **extra) -> dict:
    values = {f: get_setting(session, f) for f, *_ in NUMERIC_FIELDS}
    aliases = session.scalars(select(SchoolAlias).order_by(SchoolAlias.sheet_name)).all()
    ctx = dict(
        numeric_fields=NUMERIC_FIELDS, values=values,
        teacher_column=get_setting(session, "teacher_column"), teacher_choices=TEACHER_COLUMN_CHOICES,
        strategy=get_setting(session, "self_citation_strategy"), strategy_choices=SELF_CITATION_CHOICES,
        dept_short="\n".join(f"{k}={v}" for k, v in get_setting(session, "dept_short_names").items()),
        college_short="\n".join(f"{k}={v}" for k, v in get_setting(session, "college_short_names").items()),
        aliases="\n".join(f"{a.sheet_name}, {a.moe_name}, {a.display_name}" for a in aliases),
        key_status=mask_key(request.app.state.settings.elsevier_api_key),
        insttoken_status=mask_key(request.app.state.settings.elsevier_insttoken),
        errors=[], saved=False,
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
    try:
        aliases = parse_aliases(str(form.get("school_aliases", "")))
    except ValueError as e:
        errors.append(str(e))
    if errors:
        return render(request, "settings.html", status_code=400, **_context(session, request, errors=errors))

    for field, value in parsed.items():
        set_setting(session, field, value)
    set_setting(session, "teacher_column", form["teacher_column"])
    set_setting(session, "self_citation_strategy", form["self_citation_strategy"])
    set_setting(session, "dept_short_names", parse_pairs(str(form.get("dept_short_names", ""))))
    set_setting(session, "college_short_names", parse_pairs(str(form.get("college_short_names", ""))))
    session.execute(delete(SchoolAlias))
    session.add_all(SchoolAlias(sheet_name=s, moe_name=m, display_name=d) for s, m, d in aliases)
    session.commit()
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


@router.get("/status")
async def status(request: Request, session: Session = Depends(get_session)):
    settings = request.app.state.settings
    if not settings.elsevier_api_key:
        return render(request, "_status.html", ok=False, message="尚未設定 API Key，請到「設定」頁填寫", remaining=None)
    client = make_client(settings, session)
    try:
        result = await client.check_connection()
    finally:
        await client.aclose()
    return render(request, "_status.html", ok=result.ok, message=result.message, remaining=result.remaining)
