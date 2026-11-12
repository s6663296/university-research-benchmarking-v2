from decimal import Decimal

from sqlalchemy import select

from app.ai.inputs import college_input, dept_input, input_hash
from app.db.models import Anomaly, Result, Unit
from app.metrics.indicators import BY_ID
from app.reports.tables import fmt, indicator_table, summary_table
from app.runs.executor import execute_run
from tests.run_helpers import FakeElsevier, fake_client, make_run

DEPT = "資訊管理學系"


def ncu_unit(session):
    return session.scalars(select(Unit).where(Unit.school == "中央", Unit.ncu_dept == DEPT)).one()


def entry(payload, indicator_id, group):
    ind = next(i for i in payload["指標"] if i["名稱"] == BY_ID[indicator_id].label)
    return next(e for e in ind["比較"] if e["比較"] == group)


async def test_dept_input_matches_report_numbers(session, done_run):
    payload = dept_input(session, done_run, DEPT)
    bench = entry(payload, "top1", "對標學校比較")
    table = indicator_table(session, done_run.id, "benchmark", DEPT, "top1")
    row = next(r for r in table.rows if r.is_ncu)
    assert bench["數值"] == fmt(row.value)
    assert bench["群組單位數"] == len(table.rows)
    summary = next(s for s in summary_table(session, done_run.id, payload["學院"])
                   if s.dept_label == DEPT and s.comparison == "benchmark")
    assert bench["名次"] == summary.ranks["top1"]
    assert college_input(session, done_run, payload["學院"])["系所"]


async def test_no_baseline_means_none(session, done_run):
    assert dept_input(session, done_run, DEPT)["前期"] is None


async def test_baseline_changes_and_basis(session, session_factory, respx_mock):
    FakeElsevier().install(respx_mock)
    old = make_run(session, year_from=2020, year_to=2024, schools=("台大",))
    await execute_run(old.id, session_factory, fake_client())
    cur = make_run(session)
    await execute_run(cur.id, session_factory, fake_client())
    session.expire_all()
    prev = dept_input(session, cur, DEPT)["前期"]
    assert prev["分析編號"] == old.id
    assert "對標學校不同" in prev["比較基準差異"]
    q1 = next(c for c in prev["指標變化"] if c["比較"] == "對標學校比較" and c["名稱"] == BY_ID["q1"].label)
    old_row = next(r for r in indicator_table(session, old.id, "benchmark", DEPT, "q1").rows if r.is_ncu)
    new_row = next(r for r in indicator_table(session, cur.id, "benchmark", DEPT, "q1").rows if r.is_ncu)
    assert (q1["前期數值"], q1["本期數值"]) == (fmt(old_row.value), fmt(new_row.value))


async def test_open_anomaly_listed(session, done_run):
    a = Anomaly(run_id=done_run.id, unit_id=ncu_unit(session).id, kind="x", detail="某某異常")
    session.add(a)
    session.commit()
    assert any("某某異常" in t for t in dept_input(session, done_run, DEPT)["待人工確認"])
    a.status = "confirmed"
    session.commit()
    assert not any("某某異常" in t for t in dept_input(session, done_run, DEPT)["待人工確認"])


async def test_hash_changes_when_data_changes(session, done_run):
    h = input_hash(dept_input(session, done_run, DEPT))
    assert h == input_hash(dept_input(session, done_run, DEPT))
    r = session.scalars(select(Result).where(
        Result.run_id == done_run.id, Result.unit_id == ncu_unit(session).id,
        Result.comparison == "benchmark", Result.indicator_id == "q1")).one()
    r.value = (r.value or Decimal(0)) + Decimal("1.00")
    session.commit()
    assert h != input_hash(dept_input(session, done_run, DEPT))


async def test_funding_has_no_derived_totals(session, done_run):
    from app.funding.service import funding_table
    dept = dept_input(session, done_run, DEPT)
    college = college_input(session, done_run, dept["學院"])
    assert "全院" not in str(dept["計畫經費"]) and "全院" not in str(college["計畫經費"])
    table = funding_table(session, done_run, dept["學院"])
    if table.has_data:
        assert [r["系所"] for r in college["計畫經費"]["各系所"]] == [r.ncu_dept for r in table.rows]
