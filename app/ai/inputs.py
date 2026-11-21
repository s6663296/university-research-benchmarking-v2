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
from app.reports.tables import IndicatorTable, Row, fmt, indicator_table, run_groups
from app.runs.compare import compare_runs
from app.runs.scope import RunConfig


def _group_name(comparison: str, key: str) -> str:
    return "對標學校比較" if comparison == "benchmark" else f"{key}內系所比較"


def _period(run: Run) -> str:
    return f"{run.period_from}-{run.period_to}"


def _ncu_unit(session: Session, run: Run, dept: str) -> Unit:
    return next(u for u in units_of(session, run.mapping_version_id) if is_ncu(u.school) and u.ncu_dept == dept)


def _school_of(row: Row) -> str:
    """對標比較的列名是「系所-校名」，送給模型只留校名。"""
    return row.label.rsplit("-", 1)[-1]


def _gap(a, b) -> str:
    """兩個數值的差（a − b），四捨五入到小數第二位；由程式先算好，模型不用自己減。"""
    return fmt(a - b) if a is not None and b is not None else "待確認"


def _standing(table: IndicatorTable, own: Row) -> dict:
    """本系所在對標群組中的相對位置：名次在前、在後、同名次的學校，以及和本系所的差距。"""
    if own.rank is None:
        return {}
    ahead, behind, tied, unknown = [], [], [], []
    for r in table.rows:
        if r.unit_id == own.unit_id:
            continue
        if r.rank is None:
            unknown.append(_school_of(r))
        elif r.rank < own.rank:
            ahead.append({"學校": _school_of(r), "數值": fmt(r.value), "名次": r.rank,
                          "領先本系所": _gap(r.value, own.value)})
        elif r.rank > own.rank:
            behind.append({"學校": _school_of(r), "數值": fmt(r.value), "名次": r.rank,
                           "本系所領先": _gap(own.value, r.value)})
        else:
            tied.append(_school_of(r))
    out = {"名次在前的學校": sorted(ahead, key=lambda e: e["名次"]),
           "名次在後的學校": sorted(behind, key=lambda e: e["名次"])}
    if tied:
        out["同名次的學校"] = tied
    if unknown:
        out["數值待確認的學校"] = unknown
    return out


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
            entry = {"比較": _group_name(g.comparison, g.key), "數值": fmt(row.value),
                     "名次": row.rank, "群組單位數": len(table.rows)}
            if g.comparison == "benchmark":
                entry.update(_standing(table, row))
            entries.append(entry)
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


def _relation(own_rank: int | None, rank: int | None) -> str | None:
    """對方相對本系所的名次位置：在前／在後／同名次。"""
    if own_rank is None or rank is None:
        return None
    return "在前" if rank < own_rank else "在後" if rank > own_rank else "同名次"


# (前期位置, 本期位置) → 值得點出的變化
_SHIFTS = {("在後", "在前"): "本期超越本系所", ("同名次", "在前"): "本期超越本系所",
           ("在前", "在後"): "本期被本系所超越", ("同名次", "在後"): "本期被本系所超越"}


def _rivals(diffs: list) -> list[dict]:
    """兩期都有的對標學校中，和本系所相對位置有明顯變化的項目（差距由程式算好）。"""
    own = {d.indicator_id: d for d in diffs if is_ncu(d.school)}
    out = []
    for d in diffs:
        mine = own.get(d.indicator_id)
        if is_ncu(d.school) or mine is None or d.rank_a is None or d.rank_b is None:
            continue
        before, after = _relation(mine.rank_a, d.rank_a), _relation(mine.rank_b, d.rank_b)
        if before is None or after is None:
            continue
        item = {"學校": d.school, "名稱": BY_ID[d.indicator_id].label,
                "前期名次": d.rank_a, "本期名次": d.rank_b,
                "前期對方減本系所": _gap(d.a, mine.a), "本期對方減本系所": _gap(d.b, mine.b)}
        shift = _SHIFTS.get((before, after))
        if shift is None and before == after == "在後" and None not in (d.a, d.b, mine.a, mine.b):
            if (mine.b - d.b) < (mine.a - d.a):
                shift = "仍落後本系所，但差距縮小（逼近本系所）"
        if shift is None and before == after == "在前" and None not in (d.a, d.b, mine.a, mine.b):
            if (d.b - mine.b) < (d.a - mine.a):
                shift = "仍領先本系所，但差距縮小（本系所追近）"
        if shift:
            out.append({**item, "變化": shift})
    return out


def _previous(session: Session, run: Run, base: Run, dept: str, college: str) -> dict:
    changes = []
    diffs = compare_runs(session, base.id, run.id)
    bench = [d for d in diffs if d.comparison == "benchmark" and d.ncu_dept == dept]
    for d in diffs:
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
            "指標變化": changes, "對標學校變化": _rivals(bench), "計畫經費變化": funding}


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
