import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Anomaly, Run, RunUnit, Unit
from app.db.settings_store import get_setting
from app.elsevier.client import make_client
from app.mapping.parser import NCU_SHEET
from app.mapping.service import latest_version, units_of
from app.metrics.indicators import BY_ID, INDICATORS
from app.reports.tables import SUMMARY_INDICATORS, fmt, indicator_table, run_groups, short_names, summary_table
from app.runs.anomaly import KIND_LABELS, resolve_anomaly
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


def result_context(session: Session, run: Run, ind: str) -> dict:
    groups = run_groups(session, run)
    _, _, college_short = short_names(session)
    colleges = list(dict.fromkeys(g.key for g in groups if g.comparison == "college"))
    if not colleges:
        colleges = list(dict.fromkeys(session.get(Unit, g.unit_ids[0]).ncu_college for g in groups
                                      if g.comparison == "benchmark" and session.get(Unit, g.unit_ids[0]).ncu_college))
    tables = []
    if ind in BY_ID:
        for g in groups:
            title = {"benchmark": f"對標學校比較：{g.key}",
                     "college": f"{college_short.get(g.key, g.key)}內系所比較",
                     "custom": "自訂比較"}[g.comparison]
            tables.append((title, indicator_table(session, run.id, g.comparison, g.key, ind)))
    anomalies = []
    for a in session.scalars(select(Anomaly).where(Anomaly.run_id == run.id).order_by(Anomaly.status.desc(), Anomaly.id)):
        unit = session.get(Unit, a.unit_id) if a.unit_id else None
        anomalies.append(dict(a=a, unit=unit, kind=KIND_LABELS.get(a.kind, a.kind)))
    return dict(
        indicators=INDICATORS, ind=ind, indicator=BY_ID.get(ind), tables=tables,
        summaries=[(c, college_short.get(c, c), summary_table(session, run.id, c)) for c in colleges],
        summary_labels={i: BY_ID[i].label for i in SUMMARY_INDICATORS},
        anomalies=anomalies, open_anomalies=sum(1 for x in anomalies if x["a"].status != "confirmed"),
        notes=json.loads(run.notes_json or "{}"), fmt=fmt,
    )


@router.get("/runs/{run_id}")
def run_detail(request: Request, run_id: int, ind: str = "summary", session: Session = Depends(get_session)):
    run = get_run(session, run_id)
    ctx = progress_context(session, run)
    if run.status == "done":
        ctx.update(result_context(session, run, ind))
    return render(request, "run_detail.html", **ctx)


@router.post("/runs/{run_id}/anomalies/{anomaly_id}")
async def resolve(request: Request, run_id: int, anomaly_id: int, session: Session = Depends(get_session)):
    form = await request.form()
    a = session.get(Anomaly, anomaly_id)
    if a is None or a.run_id != run_id:
        raise HTTPException(404, "找不到這個待確認項目")
    resolve_anomaly(session, anomaly_id, str(form.get("note", "")))
    return RedirectResponse(f"/runs/{run_id}?ind=anomalies", status_code=303)


@router.post("/runs/{run_id}/notes")
async def save_notes(request: Request, run_id: int, session: Session = Depends(get_session)):
    form = await request.form()
    run = get_run(session, run_id)
    notes = json.loads(run.notes_json or "{}")
    notes["extra"] = str(form.get("extra", "")).strip()
    run.notes_json = json.dumps(notes, ensure_ascii=False)
    session.commit()
    return RedirectResponse(f"/runs/{run_id}?ind=notes", status_code=303)


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
