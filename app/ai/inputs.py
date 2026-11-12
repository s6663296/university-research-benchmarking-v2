"""組出送給模型的資料（只含已存入的結果）與其指紋。"""

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.baseline import baseline_for, basis_differences
from app.db.models import Anomaly, Run, Unit
from app.funding.service import FundingRow, FundingTable, funding_table, money
from app.mapping.parser import is_ncu
from app.mapping.service import units_of
from app.metrics.indicators import BY_ID, INDICATORS
from app.reports.tables import fmt, indicator_table, run_groups
from app.runs.compare import compare_runs
from app.runs.scope import RunConfig


def _group_name(comparison: str, key: str) -> str:
    return "對標學校比較" if comparison == "benchmark" else f"{key}內系所比較"


def _period(run: Run) -> str:
    return f"{run.period_from}-{run.period_to}"


def _ncu_unit(session: Session, run: Run, dept: str) -> Unit:
    return next(u for u in units_of(session, run.mapping_version_id) if is_ncu(u.school) and u.ncu_dept == dept)


def _indicators(session: Session, run: Run, dept: str, college: str, unit_id: int) -> list[dict]:
    groups = [g for g in run_groups(session, run)
              if (g.comparison, g.key) in (("benchmark", dept), ("college", college))]
    out = []
    for ind in INDICATORS:
        entries = []
        for g in groups:
            table = indicator_table(session, run.id, g.comparison, g.key, ind.id)
            row = next((r for r in table.rows if r.unit_id == unit_id), None)
            if row is None:
                continue
            entries.append({"比較": _group_name(g.comparison, g.key), "數值": fmt(row.value),
                            "名次": row.rank, "群組單位數": len(table.rows)})
        if entries:
            out.append({"名稱": BY_ID[ind.id].label, "比較": entries})
    return out


def _funding_row(row: FundingRow) -> dict:
    return {"國科會年平均": money(row.nstc_avg), "產學年平均": money(row.industry_avg),
            "合計年平均": money(row.total_avg), "每位教師平均": money(row.per_faculty),
            "教師數": row.teachers}


def _funding(session: Session, run: Run, dept: str, college: str) -> dict | None:
    table = funding_table(session, run, college)
    if not table.has_data:
        return None
    row = next((r for r in table.rows if r.ncu_dept == dept), None)
    return {"年度": list(table.years), "該系所": _funding_row(row) if row else None}


def _dept_funding_row(session: Session, run: Run, dept: str, college: str) -> FundingRow | None:
    table = funding_table(session, run, college)
    return next((r for r in table.rows if r.ncu_dept == dept), None) if table.has_data else None


def _anomalies(session: Session, run: Run, dept: str) -> list[str]:
    rows = session.execute(
        select(Anomaly, Unit).outerjoin(Unit, Unit.id == Anomaly.unit_id)
        .where(Anomaly.run_id == run.id, Anomaly.status != "confirmed")
        .where((Anomaly.unit_id.is_(None)) | (Unit.ncu_dept == dept)).order_by(Anomaly.id)).all()
    return [a.detail if u is None else f"{u.school}{u.ncu_dept}：{a.detail}" for a, u in rows]


def _previous(session: Session, run: Run, base: Run, dept: str, college: str) -> dict:
    changes = []
    for d in compare_runs(session, base.id, run.id):
        if not is_ncu(d.school) or d.ncu_dept != dept or d.comparison not in ("benchmark", "college"):
            continue
        if d.a is None and d.b is None:
            continue
        changes.append({"名稱": BY_ID[d.indicator_id].label, "比較": _group_name(d.comparison, college),
                        "前期數值": fmt(d.a), "本期數值": fmt(d.b),
                        "前期名次": d.rank_a, "本期名次": d.rank_b})
    old_row = _dept_funding_row(session, base, dept, college)
    new_row = _dept_funding_row(session, run, dept, college)
    funding = None
    if old_row is not None:
        funding = {"前期": _funding_row(old_row), "本期": _funding_row(new_row) if new_row else None}
    return {"期間": _period(base),
            "比較基準差異": basis_differences(run, base, dept),
            "指標變化": changes, "計畫經費變化": funding}


def dept_input(session: Session, run: Run, dept: str) -> dict:
    unit = _ncu_unit(session, run, dept)
    college = unit.ncu_college
    base = baseline_for(session, run, dept)
    return {"系所": dept, "學院": college, "期間": _period(run), "教師數學年度": run.academic_year,
            "指標": _indicators(session, run, dept, college, unit.id),
            "計畫經費": _funding(session, run, dept, college),
            "待人工確認": _anomalies(session, run, dept),
            "前期": _previous(session, run, base, dept, college) if base else None}


def college_input(session: Session, run: Run, college: str) -> dict:
    scope = set(RunConfig.from_json(run.scope_json).ncu_depts)
    names = list(dict.fromkeys(u.ncu_dept for u in units_of(session, run.mapping_version_id)
                               if is_ncu(u.school) and u.ncu_college == college and u.ncu_dept in scope))
    top_level = {"學院", "期間", "教師數學年度"}
    table = funding_table(session, run, college)
    funding = None if not table.has_data else {
        "年度": list(table.years),
        "各系所": [{"系所": r.ncu_dept, **_funding_row(r)} for r in table.rows]}
    return {"學院": college, "期間": _period(run),
            "系所": [{k: v for k, v in dept_input(session, run, d).items() if k not in top_level} for d in names],
            "計畫經費": funding}


def input_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
