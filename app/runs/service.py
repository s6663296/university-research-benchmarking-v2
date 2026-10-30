"""建立分析版本：查詢預覽、教師數快照（設計 §5.6、§6 步驟 3）。"""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Run, RunUnit, Unit
from app.db.settings_store import get_setting
from app.elsevier.scival import scival_year_ranges
from app.elsevier.scopus import build_query
from app.mapping.parser import NCU_SHEET
from app.mapping.service import latest_version, units_of
from app.runs.scope import RunConfig, build_groups
from app.teachers.moe import import_years, latest_import
from app.teachers.service import TeacherCount, teacher_count, teacher_key


@dataclass
class PreviewRow:
    unit_id: int
    label: str
    school: str
    query: str
    teacher: TeacherCount


def _plan(session: Session, cfg: RunConfig, today: date | None):
    """回傳（mapping 版本, 教師數匯入, 本次的單位清單, 阻擋錯誤）。"""
    today = today or date.today()
    errors: list[str] = []
    version, imp = latest_version(session), latest_import(session)
    if version is None:
        errors.append("尚未匯入 mapping 表")
    if imp is None:
        errors.append("尚未匯入教師數（教育部檔案）")
    elif cfg.academic_year not in import_years(imp):
        errors.append(f"教師數資料中沒有 {cfg.academic_year} 學年度")
    try:
        scival_year_ranges(cfg.year_from, cfg.year_to, today.year)
    except ValueError as e:
        errors.append(str(e))
    units: list[Unit] = []
    if version is not None:
        all_units = {u.id: u for u in units_of(session, version.id)}
        ids = dict.fromkeys(uid for g in build_groups(list(all_units.values()), cfg) for uid in g.unit_ids)
        units = [all_units[i] for i in ids if i in all_units]
        if not units:
            errors.append("沒有任何可分析的單位，請確認已選擇系所，且 mapping 的「中央」分頁有這些系所的英文名稱")
    return version, imp, units, errors


def _teacher(session: Session, imp, cfg: RunConfig, unit: Unit) -> TeacherCount:
    if imp is None:
        return TeacherCount(None, "missing")
    return teacher_count(session, imp.id, cfg.academic_year, cfg.teacher_column, *teacher_key(unit))


def preview_run(session: Session, cfg: RunConfig, today: date | None = None) -> tuple[list[PreviewRow], list[str]]:
    _, imp, units, errors = _plan(session, cfg, today)
    rows = [PreviewRow(u.id, f"{u.ncu_dept}－{u.school}" + (f"（{u.dept_zh}）" if u.school != NCU_SHEET else ""),
                       u.school, build_query(u.dept_en, cfg.year_from, cfg.year_to), _teacher(session, imp, cfg, u))
            for u in units]
    return rows, errors


def create_run(session: Session, cfg: RunConfig, today: date | None = None) -> Run:
    version, imp, units, errors = _plan(session, cfg, today)
    if errors:
        raise ValueError("；".join(errors))
    run = Run(created_at=datetime.now(), period_from=cfg.year_from, period_to=cfg.year_to,
              scope_json=cfg.to_json(), mapping_version_id=version.id, teacher_import_id=imp.id,
              academic_year=cfg.academic_year, teacher_column=cfg.teacher_column,
              self_citation_strategy=get_setting(session, "self_citation_strategy"), status="pending",
              notes_json="{}")
    session.add(run)
    session.flush()
    for u in units:
        tc = _teacher(session, imp, cfg, u)
        source = f"manual:{tc.reason}" if tc.source == "manual" else tc.source
        session.add(RunUnit(run_id=run.id, unit_id=u.id, role="ncu" if u.school == NCU_SHEET else "benchmark",
                            query=build_query(u.dept_en, cfg.year_from, cfg.year_to), status="pending",
                            teacher_count=tc.value, teacher_source=source))
    session.commit()
    return run


def last_config(session: Session) -> RunConfig | None:
    run = session.scalars(select(Run).order_by(Run.id.desc()).limit(1)).first()
    return RunConfig.from_json(run.scope_json) if run else None
