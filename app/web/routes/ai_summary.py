"""AI 總結：分頁、背景產生、人工修改、前期設定與全院總結下載。"""

import zipfile

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import service
from app.ai.baseline import baseline_choices, baseline_for, basis_differences
from app.db.models import AiSummary, Run
from app.funding.service import run_colleges
from app.reports.college_report import build_college_report, college_report_filename
from app.reports.exports import export_path, record_export
from app.web.deps import get_session, render
from app.web.routes.runs import PPTX_TYPE, _done_run, get_run

router = APIRouter()

KIND_LABELS = {"dept": "系所", "college": "學院"}
NEED_SUMMARY = "請先產生並確認全院總結"


def _back(run_id: int) -> RedirectResponse:
    return RedirectResponse(f"/runs/{run_id}?ind=ai", status_code=303)


def ai_context(session: Session, run: Run) -> dict:
    saved = {(r.kind, r.key): r for r in session.scalars(select(AiSummary).where(AiSummary.run_id == run.id))}
    rows = []
    for kind, key in service.targets(session, run):
        row = saved.get((kind, key))
        if row is None:
            status = "未產生"
        elif not service.is_fresh(session, row):
            status = "資料已更新，總結可能過時"
        else:
            status = "已修改" if row.edited else "已產生"
        base, diffs = None, []
        if kind == "dept":
            base = baseline_for(session, run, key)
            diffs = basis_differences(run, base, key) if base else []
        rows.append(dict(kind=kind, key=key, kind_label=KIND_LABELS[kind], row=row, status=status,
                         stale=status.startswith("資料已更新"), base=base, diffs=diffs))
    return dict(ai_rows=rows, baseline_options=baseline_choices(session, run))


def ai_progress_context(request: Request, run: Run) -> dict:
    jobs = request.app.state.ai_jobs
    state = jobs.state(run.id)
    return dict(run=run, ai_state=state, ai_polling=jobs.is_running(run.id),
                ai_errors=[(KIND_LABELS[k], key, msg) for (k, key), msg in state.errors.items()] if state else [])


@router.get("/runs/{run_id}/ai/progress")
def ai_progress(request: Request, run_id: int, session: Session = Depends(get_session)):
    ctx = ai_progress_context(request, _done_run(session, run_id))
    response = render(request, "_ai_progress.html", **ctx)
    if not ctx["ai_polling"]:
        response.headers["HX-Refresh"] = "true"  # 完成後重新載入，才看得到新的文字
    return response


@router.post("/runs/{run_id}/ai/generate")
async def generate_all(request: Request, run_id: int, session: Session = Depends(get_session)):
    _done_run(session, run_id)
    request.app.state.ai_jobs.start(run_id)
    return _back(run_id)


@router.post("/runs/{run_id}/ai/generate/{kind}/{key}")
async def generate_one(request: Request, run_id: int, kind: str, key: str, session: Session = Depends(get_session)):
    run = _done_run(session, run_id)
    if (kind, key) not in service.targets(session, run):
        raise HTTPException(404, "找不到這個總結對象")
    request.app.state.ai_jobs.start(run_id, only=(kind, key))
    return _back(run_id)


@router.post("/runs/{run_id}/ai/summaries/{summary_id}")
async def save_summary(request: Request, run_id: int, summary_id: int, session: Session = Depends(get_session)):
    _done_run(session, run_id)
    row = session.get(AiSummary, summary_id)
    if row is None or row.run_id != run_id:
        raise HTTPException(404, "找不到這筆總結")
    form = await request.form()
    text = str(form.get("summary", "")).strip()
    if not text:
        raise HTTPException(400, "總結不能是空白")
    service.save_edit(session, summary_id, text,
                      str(form.get("comparison", "")).strip() or None)
    return _back(run_id)


@router.post("/runs/{run_id}/ai/baseline")
async def set_baseline(request: Request, run_id: int, session: Session = Depends(get_session)):
    run = _done_run(session, run_id)
    value = str((await request.form()).get("baseline", "")).strip()
    if not value:
        run.ai_baseline_run_id = None
    else:
        if not value.isdigit() or int(value) not in {r.id for r in baseline_choices(session, run)}:
            raise HTTPException(400, "這個前期分析不能選用")
        run.ai_baseline_run_id = int(value)
    session.commit()  # 不自動重新產生；既有文字的指紋含前期，會顯示為過時
    return _back(run_id)


def _fresh_college(session: Session, run: Run, college: str):
    return service.fresh_text(session, run, "college", college)


@router.get("/runs/{run_id}/college-summary.zip")
def download_college_zip(request: Request, run_id: int, session: Session = Depends(get_session)):
    run = _done_run(session, run_id)
    settings = request.app.state.settings
    built = []
    for college in run_colleges(session, run):
        text = _fresh_college(session, run, college)
        if text is not None:
            name = college_report_filename(run, college)
            built.append((name, build_college_report(session, run_id, college, text,
                                                     export_path(settings, run_id, name))))
    if not built:
        raise HTTPException(409, NEED_SUMMARY)
    zip_path = export_path(settings, run_id, f"{run.period_from}-{run.period_to}各學院論文指標全院總結.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, path in built:
            zf.write(path, name)
    record_export(session, run_id, "college_pptx_zip", zip_path)
    return FileResponse(zip_path, media_type="application/zip", filename=zip_path.name)


@router.get("/runs/{run_id}/college-summary/{college}")
def download_college(request: Request, run_id: int, college: str, session: Session = Depends(get_session)):
    run = _done_run(session, run_id)
    if college not in run_colleges(session, run):
        raise HTTPException(404, "這次分析沒有這個學院")
    text = _fresh_college(session, run, college)
    if text is None:
        raise HTTPException(409, NEED_SUMMARY)
    name = college_report_filename(run, college)
    path = build_college_report(session, run_id, college, text, export_path(request.app.state.settings, run_id, name))
    record_export(session, run_id, "college_pptx", path)
    return FileResponse(path, media_type=PPTX_TYPE, filename=name)
