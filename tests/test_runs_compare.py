from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models import Result, Unit
from app.runs.compare import compare_runs
from app.runs.executor import execute_run
from tests.run_helpers import FakeElsevier, fake_client, make_run


def set_value(session, run_id, school, comparison, indicator, value):
    r = session.scalars(select(Result).join(Unit, Unit.id == Result.unit_id).where(
        Result.run_id == run_id, Unit.school == school, Unit.ncu_dept == "資訊管理學系",
        Result.comparison == comparison, Result.indicator_id == indicator)).one()
    r.value = value
    session.commit()


@pytest.fixture
async def two_runs(session, session_factory, respx_mock):
    FakeElsevier().install(respx_mock)
    a = make_run(session, schools=("台大",))
    await execute_run(a.id, session_factory, fake_client())
    b = make_run(session, schools=("台大", "政治"))
    await execute_run(b.id, session_factory, fake_client())
    set_value(session, a.id, "中央", "benchmark", "q1", Decimal("1.10"))
    set_value(session, b.id, "中央", "benchmark", "q1", Decimal("1.20"))
    return a, b


async def test_compare_aligns_by_unit_key(session, two_runs):
    a, b = two_runs
    rows = compare_runs(session, a.id, b.id)
    row = next(r for r in rows if (r.school, r.ncu_dept, r.comparison, r.indicator_id) ==
               ("中央", "資訊管理學系", "benchmark", "q1"))
    assert (row.a, row.b, row.delta) == (Decimal("1.10"), Decimal("1.20"), Decimal("0.10"))


async def test_compare_unit_only_in_one(session, two_runs):
    a, b = two_runs
    row = next(r for r in compare_runs(session, a.id, b.id) if r.school == "政治" and r.indicator_id == "total")
    assert row.a is None and row.b is not None and row.delta is None


def test_history_and_compare_pages(client, web_done_run, respx_mock):
    import asyncio
    factory = client.app.state.session_factory
    with factory() as s:
        second = make_run(s).id
    asyncio.run(execute_run(second, factory, fake_client()))
    with factory() as s:
        set_value(s, web_done_run, "中央", "benchmark", "q1", Decimal("1.10"))
        set_value(s, second, "中央", "benchmark", "q1", Decimal("1.20"))
    text = client.get("/runs").text
    assert f"/runs/{web_done_run}" in text and f"/runs/{second}" in text
    compare = client.get("/runs/compare", params={"a": web_done_run, "b": second}).text
    assert "0.10" in compare and "資訊管理學系" in compare
    assert f"#{second}" in client.get("/").text
