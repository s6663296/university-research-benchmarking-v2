from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Run, RunUnit, Unit
from app.db.settings_store import get_setting
from app.elsevier.client import make_client
from app.mapping.parser import NCU_SHEET
from app.mapping.service import latest_version, units_of
from app.runs.scope import COMPARISON_LABELS, RunConfig, default_period
from app.runs.service import create_run, last_config, preview_run
from app.teachers.moe import import_years, latest_import
from app.web.deps import get_session, render

router = APIRouter()

RUN_STATUS = {"pending": "等待中", "running": "執行中", "done": "完成", "failed": "失敗", "interrupted": "中斷"}
UNIT_STATUS = {"pending": "等待中", "fetching": "抓取中", "done": "完成", "failed": "失敗"}


def _int(value, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def config_from_form(form, units: list[Unit], session: Session) -> RunConfig:
    ncu = [u for u in units if u.school == NCU_SHEET]
    colleges = set(form.getlist("colleges"))
    depts = dict.fromkeys([*form.getlist("ncu_depts"), *(u.ncu_dept for u in ncu if u.ncu_college in colleges)])
    y_from, y_to = default_period(date.today())
    return RunConfig(
        year_from=_int(form.get("year_from"), y_from), year_to=_int(form.get("year_to"), y_to),
        ncu_depts=tuple(d for d in (u.ncu_dept for u in ncu) if d in depts),
        comparisons=frozenset(c for c in form.getlist("comparisons") if c in COMPARISON_LABELS),
        schools=tuple(form.getlist("schools")),
        custom_unit_ids=tuple(_int(i, 0) for i in form.getlist("custom_unit_ids")),
        academic_year=_int(form.get("academic_year"), 0),
        teacher_column=get_setting(session, "teacher_column"),
        force_refresh=bool(form.get("force_refresh")),
    )


def _form_page(request: Request, session: Session, cfg: RunConfig | None = None, error: str | None = None,
               status_code: int = 200):
    version, imp = latest_version(session), latest_import(session)
    units = units_of(session, version.id) if version else []
    ncu = [u for u in units if u.school == NCU_SHEET]
    colleges: dict[str, list[str]] = {}
    for u in ncu:
        colleges.setdefault(u.ncu_college or "（未分學院）", []).append(u.ncu_dept)
    schools = list(dict.fromkeys(u.school for u in units if u.school != NCU_SHEET))
    years = import_years(imp) if imp else []
    if cfg is None:
        y_from, y_to = default_period(date.today())
        cfg = RunConfig(y_from, y_to, (), frozenset({"benchmark", "college"}), tuple(schools), (),
                        years[-1] if years else 0, get_setting(session, "teacher_column"))
    return render(request, "run_new.html", status_code=status_code, cfg=cfg, colleges=colleges, schools=schools,
                  units=units, years=years, labels=COMPARISON_LABELS, error=error,
                  ready=version is not None and imp is not None)


@router.get("/runs/new")
def new_run(request: Request, reuse: int = 0, session: Session = Depends(get_session)):
    return _form_page(request, session, last_config(session) if reuse else None)


@router.post("/runs/preview")
async def preview(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    version = latest_version(session)
    cfg = config_from_form(form, units_of(session, version.id) if version else [], session)
    rows, errors = preview_run(session, cfg)
    return render(request, "_preview.html", rows=rows, errors=errors)


@router.post("/runs")
async def start_run(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    version = latest_version(session)
    cfg = config_from_form(form, units_of(session, version.id) if version else [], session)
    settings = request.app.state.settings
    if not settings.elsevier_api_key:
        return _form_page(request, session, cfg, "尚未設定 API Key，請到「設定」頁填寫", 400)
    client = make_client(settings, session)
    try:
        status = await client.check_connection()
    finally:
        await client.aclose()
    if not status.ok:
        return _form_page(request, session, cfg, f"無法開始分析：{status.message}", 400)
    try:
        run = create_run(session, cfg)
    except ValueError as e:
        return _form_page(request, session, cfg, str(e), 400)
    request.app.state.run_manager.start(run.id)
    return RedirectResponse(f"/runs/{run.id}", status_code=303)


def progress_context(session: Session, run: Run) -> dict:
    rows = []
    for ru in session.scalars(select(RunUnit).where(RunUnit.run_id == run.id).order_by(RunUnit.id)):
        u = session.get(Unit, ru.unit_id)
        rows.append(dict(ru=ru, unit=u, status=UNIT_STATUS.get(ru.status, ru.status)))
    done = sum(1 for r in rows if r["ru"].status in ("done", "failed"))
    return dict(run=run, rows=rows, done=done, total=len(rows), run_status=RUN_STATUS.get(run.status, run.status),
                polling=run.status in ("pending", "running"))


def get_run(session: Session, run_id: int) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "找不到這個分析")
    return run


@router.get("/runs/{run_id}/progress")
def progress(request: Request, run_id: int, session: Session = Depends(get_session)):
    ctx = progress_context(session, get_run(session, run_id))
    response = render(request, "_progress.html", **ctx)
    if not ctx["polling"]:
        response.headers["HX-Refresh"] = "true"
    return response


@router.get("/runs/{run_id}")
def run_detail(request: Request, run_id: int, session: Session = Depends(get_session)):
    run = get_run(session, run_id)
    return render(request, "run_detail.html", **progress_context(session, run))


@router.post("/runs/{run_id}/resume")
def resume(request: Request, run_id: int, session: Session = Depends(get_session)):
    get_run(session, run_id)
    request.app.state.run_manager.resume(run_id)
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@router.post("/runs/{run_id}/units/{unit_id}/refetch")
def refetch(request: Request, run_id: int, unit_id: int, session: Session = Depends(get_session)):
    get_run(session, run_id)
    request.app.state.run_manager.refetch_unit(run_id, unit_id)
    return RedirectResponse(f"/runs/{run_id}", status_code=303)
