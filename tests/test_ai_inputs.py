from decimal import Decimal

from sqlalchemy import delete, select

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
    assert "分析編號" not in prev and prev["期間"] == "2020-2024"
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


async def test_renumbering_baseline_keeps_hash(session, session_factory, respx_mock, tmp_path):
    from app.db.models import Run, RunUnit
    from app.runs.renumber import renumber_runs
    FakeElsevier().install(respx_mock)
    dummy = make_run(session)
    old = make_run(session, year_from=2020, year_to=2024, schools=("台大",))
    await execute_run(old.id, session_factory, fake_client())
    cur = make_run(session)
    await execute_run(cur.id, session_factory, fake_client())
    session.expire_all()
    cur_id = cur.id
    before = input_hash(dept_input(session, session.get(Run, cur.id), DEPT))
    session.execute(delete(RunUnit).where(RunUnit.run_id == dummy.id))
    session.delete(session.get(Run, dummy.id))
    session.commit()
    assert renumber_runs(session, tmp_path)
    session.expire_all()
    cur_after = session.scalars(select(Run).order_by(Run.id.desc())).first()
    assert cur_after.id != cur_id
    assert input_hash(dept_input(session, cur_after, DEPT)) == before


async def test_standing_lists_every_school_with_gaps(session, done_run):
    bench = entry(dept_input(session, done_run, DEPT), "q1", "對標學校比較")
    table = indicator_table(session, done_run.id, "benchmark", DEPT, "q1")
    own = next(r for r in table.rows if r.is_ncu)
    listed = ([e["學校"] for e in bench["名次在前的學校"]] + [e["學校"] for e in bench["名次在後的學校"]]
              + bench.get("同名次的學校", []) + bench.get("數值待確認的學校", []))
    assert sorted(listed) == sorted(r.label.rsplit("-", 1)[-1] for r in table.rows if not r.is_ncu)
    for e in bench["名次在前的學校"]:
        other = next(r for r in table.rows if r.label.endswith(e["學校"]))
        assert e["名次"] < own.rank and e["領先本系所"] == fmt(other.value - own.value)
    for e in bench["名次在後的學校"]:
        other = next(r for r in table.rows if r.label.endswith(e["學校"]))
        assert e["名次"] > own.rank and e["本系所領先"] == fmt(own.value - other.value)


async def test_college_group_has_no_school_lists(session, done_run):
    payload = dept_input(session, done_run, DEPT)
    college = entry(payload, "q1", f"{payload['學院']}內系所比較")
    assert "名次在前的學校" not in college


def _set(session, run_id, school, indicator_id, value, rank):
    r = session.scalars(select(Result).join(Unit, Unit.id == Result.unit_id).where(
        Result.run_id == run_id, Result.comparison == "benchmark", Result.indicator_id == indicator_id,
        Unit.school == school, Unit.ncu_dept == DEPT)).one()
    r.value, r.rank = Decimal(value), rank


async def test_rival_changes_flag_overtake_and_closing_gap(session, session_factory, respx_mock):
    FakeElsevier().install(respx_mock)
    old = make_run(session, year_from=2020, year_to=2024)
    await execute_run(old.id, session_factory, fake_client())
    cur = make_run(session)
    await execute_run(cur.id, session_factory, fake_client())
    session.expire_all()
    # 前期：中央 10 第一、台大 8 第二、政治 2 第三；本期：台大 12 超越中央 11，政治 9 逼近
    for run_id, values in ((old.id, (("中央", "10", 1), ("台大", "8", 2), ("政治", "2", 3))),
                           (cur.id, (("台大", "12", 1), ("中央", "11", 2), ("政治", "9", 3)))):
        for school, value, rank in values:
            _set(session, run_id, school, "q1", value, rank)
    session.commit()
    rivals = [c for c in dept_input(session, cur, DEPT)["前期"]["對標學校變化"] if c["名稱"] == BY_ID["q1"].label]
    by_school = {c["學校"]: c for c in rivals}
    assert by_school["台大"]["變化"] == "本期超越本系所"
    assert (by_school["台大"]["前期對方減本系所"], by_school["台大"]["本期對方減本系所"]) == ("-2.00", "1.00")
    assert "逼近本系所" in by_school["政治"]["變化"]
