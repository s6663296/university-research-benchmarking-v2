import json
import zipfile
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Anomaly, Run, RunUnit, Unit
from app.db.settings_store import get_setting
from app.elsevier.client import make_client
from app.mapping.parser import is_ncu
from app.mapping.service import latest_version, units_of
from app.metrics.indicators import BY_ID, INDICATORS
from app.reports.exports import export_path, record_export
from app.reports.pptx_report import build_dept_report, report_filename
from app.reports.xlsx_report import build_run_workbook
from app.reports.provenance import Explainer, columns_of, evidence_papers
from app.reports.tables import SUMMARY_INDICATORS, fmt, indicator_table, summary_table
from app.runs.anomaly import KIND_LABELS, resolve_anomaly
from app.runs.scope import COMPARISON_LABELS, RunConfig, default_period
from app.runs.service import create_run, last_config, preview_run
from app.teachers.moe import import_years, latest_import
from app.web.deps import get_session, render

router = APIRouter()

RUN_STATUS = {"pending": "等待中", "running": "執行中", "done": "完成", "failed": "失敗", "interrupted": "中斷",
              "stopped": "已停止"}
UNIT_STATUS = {"pending": "等待中", "scopus": "Scopus 查詢中", "scival": "SciVal 取指標中", "done": "完成", "failed": "失敗"}


def _int(value, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def config_from_form(form, units: list[Unit], session: Session) -> RunConfig:
    ncu = [u for u in units if is_ncu(u.school)]
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
    ncu = [u for u in units if is_ncu(u.school)]
    colleges: dict[str, list[str]] = {}
    for u in ncu:
        colleges.setdefault(u.ncu_college or "（未分學院）", []).append(u.ncu_dept)
    schools = list(dict.fromkeys(u.school for u in units if not is_ncu(u.school)))
    unit_groups: dict[str, list[Unit]] = {}   # 自訂比較的單位清單：依學校分組，中大排最前
    for u in sorted(units, key=lambda u: not is_ncu(u.school)):
        unit_groups.setdefault(u.school, []).append(u)
    years = import_years(imp) if imp else []
    if cfg is None:
        y_from, y_to = default_period(date.today())
        cfg = RunConfig(y_from, y_to, (), frozenset({"benchmark", "college"}), tuple(schools), (),
                        years[-1] if years else 0, get_setting(session, "teacher_column"))
    return render(request, "run_new.html", status_code=status_code, cfg=cfg, colleges=colleges, schools=schools,
                  units=units, unit_groups=unit_groups, years=years, labels=COMPARISON_LABELS, error=error,
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
    return await _launch(request, session, cfg)


@router.post("/runs/start")
async def start_from_home(request: Request, session: Session = Depends(get_session)):
    """首頁「開始分析」：沿用上一次分析的設定；沒有上一次就先到分析設定。"""
    if request.app.state.run_manager.any_running():  # 已有分析在背景執行，不重複開始
        return RedirectResponse("/", status_code=303)
    cfg = last_config(session)
    if cfg is None:
        return RedirectResponse("/runs/new", status_code=303)
    return await _launch(request, session, cfg)


async def _launch(request: Request, session: Session, cfg: RunConfig):
    """確認 API 可用後建立分析並在背景執行，成功就回首頁看儀表板；失敗則回分析設定頁顯示原因。"""
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
    return RedirectResponse("/", status_code=303)


def progress_context(request: Request, session: Session, run: Run) -> dict:
    """polling：背景程式是否正在執行這次分析（以背景狀態為準，不看資料庫的 status）。"""
    rows = []
    for ru in session.scalars(select(RunUnit).where(RunUnit.run_id == run.id).order_by(RunUnit.id)):
        u = session.get(Unit, ru.unit_id)
        rows.append(dict(ru=ru, unit=u, status=UNIT_STATUS.get(ru.status, ru.status)))
    done = sum(1 for r in rows if r["ru"].status in ("done", "failed"))
    unfinished = sum(1 for r in rows if r["ru"].status != "done")
    return dict(run=run, rows=rows, done=done, total=len(rows), run_status=RUN_STATUS.get(run.status, run.status),
                polling=request.app.state.run_manager.is_running(run.id), unfinished=unfinished, busy=False)


def get_run(session: Session, run_id: int) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "找不到這個分析")
    return run


@router.get("/runs/{run_id}/progress")
def progress(request: Request, run_id: int, session: Session = Depends(get_session)):
    ctx = progress_context(request, session, get_run(session, run_id))
    response = render(request, "_progress.html", **ctx)
    if not ctx["polling"]:
        response.headers["HX-Refresh"] = "true"
    return response


@router.get("/runs/{run_id}/fetch")
def fetch_status(request: Request, run_id: int, session: Session = Depends(get_session)):
    """各單位的抓取狀態（Scopus 取 EID、SciVal 取指標）；分析完成後仍可查看。"""
    return render(request, "run_fetch.html", **progress_context(request, session, get_run(session, run_id)))


def result_context(session: Session, run: Run, ind: str) -> dict:
    explainer = Explainer(session, run.id)
    groups = explainer.groups
    colleges = list(dict.fromkeys(g.key for g in groups if g.comparison == "college"))
    if not colleges:
        colleges = list(dict.fromkeys(session.get(Unit, g.unit_ids[0]).ncu_college for g in groups
                                      if g.comparison == "benchmark" and session.get(Unit, g.unit_ids[0]).ncu_college))
    tables = []
    if ind in BY_ID:
        for g in groups:
            title = {"benchmark": f"對標學校比較：{g.key}",
                     "college": f"{g.key}內系所比較",
                     "custom": "自訂比較"}[g.comparison]
            tables.append((title, indicator_table(session, run.id, g.comparison, g.key, ind, explainer=explainer)))
    anomalies = []
    for a in session.scalars(select(Anomaly).where(Anomaly.run_id == run.id).order_by(Anomaly.status.desc(), Anomaly.id)):
        unit = session.get(Unit, a.unit_id) if a.unit_id else None
        anomalies.append(dict(a=a, unit=unit, kind=KIND_LABELS.get(a.kind, a.kind)))
    return dict(
        indicators=INDICATORS, ind=ind, indicator=BY_ID.get(ind), tables=tables,
        summaries=[(c, summary_table(session, run.id, c, explainer)) for c in colleges],
        mismatches=[dict(e=e, label=explainer.label(e.where[0]), indicator=BY_ID[e.where[2]].label)
                    for e in explainer.mismatches()],
        summary_labels={i: BY_ID[i].label for i in SUMMARY_INDICATORS},
        anomalies=anomalies, open_anomalies=sum(1 for x in anomalies if x["a"].status != "confirmed"),
        notes=json.loads(run.notes_json or "{}"), fmt=fmt,
        report_depts=RunConfig.from_json(run.scope_json).ncu_depts,
    )


@router.get("/runs/{run_id}")
def run_detail(request: Request, run_id: int, ind: str = "summary", busy: int = 0, hl: int = 0,
               session: Session = Depends(get_session)):
    run = get_run(session, run_id)
    ctx = progress_context(request, session, run)
    ctx["busy"], ctx["hl"] = bool(busy), hl
    if run.status == "done":
        ctx.update(result_context(session, run, ind))
    return render(request, "run_detail.html", **ctx)


@router.get("/runs/{run_id}/evidence")
def evidence(request: Request, run_id: int, unit: int = 0, cmp: str = "", ind: str = "", col: str = "",
             session: Session = Depends(get_session)):
    """逐格來源明細（點擊可查的格子）：逐篇論文、教育部教師數或同組名次。"""
    run = get_run(session, run_id)
    explainer = Explainer(session, run.id)
    if (ind not in BY_ID or col not in columns_of(BY_ID[ind]) or unit not in explainer.run_units
            or (unit, cmp, ind) not in explainer.results):
        return render(request, "_evidence_drawer.html", status_code=404, e=None)
    e = explainer.cell(unit, cmp, ind, col)
    ctx = dict(e=e, run=run, kind=e.drill.kind if e.drill else None)
    if ctx["kind"] == "papers":
        papers = evidence_papers(session, run.id, unit)
        field = e.drill.filter
        if field in ("fwci", "citations"):  # 數值欄由高到低，無值的排最後
            papers.sort(key=lambda p: (getattr(p.metrics, field) is None, -(getattr(p.metrics, field) or 0)))
        elif field:  # 判定欄：被算進去的排前面
            papers.sort(key=lambda p: not getattr(p.metrics, field))
        ctx.update(papers=papers, field=field, hit=field not in (None, "fwci", "citations"))
    elif ctx["kind"] == "teachers":
        ru = explainer.run_units[unit]
        ctx.update(detail=json.loads(ru.teacher_detail_json) if ru.teacher_detail_json else None, ru=ru)
    elif ctx["kind"] == "group":
        ctx.update(group=explainer.group_rows(unit, cmp, ind))
    return render(request, "_evidence_drawer.html", fmt=fmt, **ctx)


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


def back_to(request: Request, run_id: int) -> str:
    """從分析頁面操作時回到分析頁面，其餘回首頁。"""
    return f"/runs/{run_id}" if request.query_params.get("from") == "run" else "/"


# 背景任務要建立在 event loop 上，所以這些路由必須是 async def
@router.post("/runs/{run_id}/pause")
async def pause(request: Request, run_id: int, session: Session = Depends(get_session)):
    get_run(session, run_id)
    await request.app.state.run_manager.pause(run_id)
    return RedirectResponse(back_to(request, run_id), status_code=303)


@router.post("/runs/{run_id}/stop")
async def stop(request: Request, run_id: int, session: Session = Depends(get_session)):
    get_run(session, run_id)
    await request.app.state.run_manager.stop(run_id)
    return RedirectResponse(back_to(request, run_id), status_code=303)


@router.post("/runs/{run_id}/resume")
async def resume(request: Request, run_id: int, home: bool = False, session: Session = Depends(get_session)):
    """home=1：從首頁按「繼續」，回首頁看儀表板。"""
    get_run(session, run_id)
    started = request.app.state.run_manager.resume(run_id)
    if home:
        return RedirectResponse("/", status_code=303)
    return RedirectResponse(f"/runs/{run_id}" + ("" if started else "?busy=1"), status_code=303)


@router.post("/runs/{run_id}/units/{unit_id}/refetch")
async def refetch(request: Request, run_id: int, unit_id: int, session: Session = Depends(get_session)):
    get_run(session, run_id)
    started = request.app.state.run_manager.refetch_unit(run_id, unit_id)
    return RedirectResponse(f"/runs/{run_id}" + ("" if started else "?busy=1"), status_code=303)


PPTX_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _done_run(session: Session, run_id: int) -> Run:
    run = get_run(session, run_id)
    if run.status != "done":
        raise HTTPException(409, "分析尚未完成，無法產出報告")
    return run


@router.get("/runs/{run_id}/pptx.zip")
def download_all_pptx(request: Request, run_id: int, session: Session = Depends(get_session)):
    run = _done_run(session, run_id)
    settings = request.app.state.settings
    zip_path = export_path(settings, run_id, f"{run.period_from}-{run.period_to}各系所論文表現分析.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dept in RunConfig.from_json(run.scope_json).ncu_depts:
            name = report_filename(session, run_id, dept)
            zf.write(build_dept_report(session, run_id, dept, export_path(settings, run_id, name)), name)
    record_export(session, run_id, "pptx_zip", zip_path)
    return FileResponse(zip_path, media_type="application/zip", filename=zip_path.name)


@router.get("/runs/{run_id}/pptx/{ncu_dept}")
def download_pptx(request: Request, run_id: int, ncu_dept: str, session: Session = Depends(get_session)):
    run = _done_run(session, run_id)
    if ncu_dept not in RunConfig.from_json(run.scope_json).ncu_depts:
        raise HTTPException(404, "這次分析沒有這個系所")
    name = report_filename(session, run_id, ncu_dept)
    path = build_dept_report(session, run_id, ncu_dept, export_path(request.app.state.settings, run_id, name))
    record_export(session, run_id, "pptx", path)
    return FileResponse(path, media_type=PPTX_TYPE, filename=name)


@router.get("/runs/{run_id}/xlsx")
def download_xlsx(request: Request, run_id: int, session: Session = Depends(get_session)):
    run = _done_run(session, run_id)
    name = f"{run.period_from}-{run.period_to}論文指標分析_run{run_id}.xlsx"
    path = build_run_workbook(session, run_id, export_path(request.app.state.settings, run_id, name))
    record_export(session, run_id, "xlsx", path)
    return FileResponse(path, filename=name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
