"""網頁、pptx、xlsx 共用的表格資料（設計 §9）。"""

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Result, Run, RunUnit, Unit
from app.mapping.parser import is_ncu
from app.mapping.service import units_of
from app.metrics.indicators import BY_ID
from app.metrics.compute import round2
from app.reports.provenance import Explainer, Explanation, columns_of
from app.runs.scope import Group, RunConfig, build_groups

SUMMARY_INDICATORS = ("q1", "top1", "top10", "fwci", "avg_citations", "intl_collab")


@dataclass
class Row:
    unit_id: int
    label: str
    is_ncu: bool
    raw: Decimal | None
    five_year_avg: Decimal | None
    teachers: int | None
    teacher_manual: bool
    value: Decimal | None
    rank: int | None
    explain: dict[str, Explanation] = field(default_factory=dict)  # 欄位 → 來源說明


@dataclass
class IndicatorTable:
    indicator_id: str
    comparison: str
    key: str
    rows: list[Row]


@dataclass
class SummaryRow:
    dept_label: str
    comparison: str
    group_size: int
    ranks: dict[str, int | None]
    explain: dict[str, Explanation] = field(default_factory=dict)  # 指標 → 名次的來源說明


def fmt(value: Decimal | None) -> str:
    return "待確認" if value is None else f"{round2(Decimal(value)):.2f}"


def unit_label(unit: Unit, comparison: str) -> str:
    """名稱一律沿用 mapping：系所全名，對標比較加上分頁的校名。"""
    return unit.ncu_dept if comparison == "college" else f"{unit.ncu_dept}-{unit.school}"


def run_groups(session: Session, run: Run) -> list[Group]:
    return build_groups(units_of(session, run.mapping_version_id), RunConfig.from_json(run.scope_json))


def _sort(rows: list[Row], keys: dict[int, Decimal | None]) -> list[Row]:
    return sorted(rows, key=lambda r: (keys.get(r.unit_id) is None, -(keys.get(r.unit_id) or 0)))


def indicator_table(session: Session, run_id: int, comparison: str, key: str, indicator_id: str,
                    sort_by: str | None = None, explainer: Explainer | None = None) -> IndicatorTable:
    """有給 explainer 才附上逐格來源說明（網頁、xlsx 用；pptx 不需要）。"""
    run = session.get(Run, run_id)
    groups = explainer.groups if explainer else run_groups(session, run)
    group = next((g for g in groups if g.comparison == comparison and g.key == key), None)
    if group is None:
        return IndicatorTable(indicator_id, comparison, key, [])
    def results(ind: str) -> dict[int, Result]:
        return {r.unit_id: r for r in session.scalars(select(Result).where(
            Result.run_id == run_id, Result.comparison == comparison, Result.indicator_id == ind,
            Result.unit_id.in_(group.unit_ids)))}

    own = results(indicator_id)
    run_units = {ru.unit_id: ru for ru in session.scalars(select(RunUnit).where(RunUnit.run_id == run_id))}
    columns = columns_of(BY_ID[indicator_id])
    rows = []
    for uid in group.unit_ids:
        unit, res, ru = session.get(Unit, uid), own.get(uid), run_units.get(uid)
        rows.append(Row(
            unit_id=uid, label=unit_label(unit, comparison), is_ncu=is_ncu(unit.school),
            raw=res.raw if res else None, five_year_avg=res.five_year_avg if res else None,
            teachers=ru.teacher_count if ru else None,
            teacher_manual=bool(ru and ru.teacher_source.startswith("manual")),
            value=res.value if res else None, rank=res.rank if res else None,
            explain={c: explainer.cell(uid, comparison, indicator_id, c) for c in columns} if explainer else {}))
    sort_values = {u: r.value for u, r in (results(sort_by) if sort_by else own).items()}
    return IndicatorTable(indicator_id, comparison, key, _sort(rows, sort_values))


def summary_table(session: Session, run_id: int, college: str,
                  explainer: Explainer | None = None) -> list[SummaryRow]:
    """學院各系所在各指標的名次（範例第 4 頁）：每個系所一列對標、一列院內。"""
    run = session.get(Run, run_id)
    groups = explainer.groups if explainer else run_groups(session, run)
    ncu_units = [u for u in units_of(session, run.mapping_version_id)
                 if is_ncu(u.school) and u.ncu_college == college]
    rows = []
    for unit in ncu_units:
        for comparison, key in (("benchmark", unit.ncu_dept), ("college", college)):
            group = next((g for g in groups if g.comparison == comparison and g.key == key), None)
            if group is None:
                continue
            ranks = {r.indicator_id: r.rank for r in session.scalars(select(Result).where(
                Result.run_id == run_id, Result.unit_id == unit.id, Result.comparison == comparison))}
            rows.append(SummaryRow(unit.ncu_dept, comparison, len(group.unit_ids),
                                   {i: ranks.get(i) for i in SUMMARY_INDICATORS},
                                   {i: explainer.cell(unit.id, comparison, i, "rank") for i in SUMMARY_INDICATORS}
                                   if explainer else {}))
    return rows
