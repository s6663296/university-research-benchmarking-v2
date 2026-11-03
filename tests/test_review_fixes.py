"""最終審查發現的問題：每項先重現再修正。"""

import time

import httpx
from sqlalchemy import select

from app.db.models import Run, RunUnit, Unit
from tests.elsevier_fixtures import SCOPUS_URL


def wait_status(client, run_id, statuses=("done", "failed"), timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        with client.app.state.session_factory() as s:
            status = s.get(Run, run_id).status
        if status in statuses:
            return status
        time.sleep(0.05)
    raise AssertionError("分析沒有在時間內結束")


def unit_row(s, run_id, school):
    return s.scalars(select(RunUnit).join(Unit, Unit.id == RunUnit.unit_id)
                     .where(RunUnit.run_id == run_id, Unit.school == school)).first()


def test_resume_route_runs_in_background(client, web_done_run):
    with client.app.state.session_factory() as s:
        s.get(Run, web_done_run).status = "interrupted"
        unit_row(s, web_done_run, "台大").status = "pending"
        s.commit()
    r = client.post(f"/runs/{web_done_run}/resume", follow_redirects=False)
    assert r.status_code == 303
    assert wait_status(client, web_done_run) == "done"


def test_refetch_route_runs_in_background(client, web_done_run):
    with client.app.state.session_factory() as s:
        ru = unit_row(s, web_done_run, "台大")
        ru.status, unit_id = "failed", ru.unit_id
        s.commit()
    r = client.post(f"/runs/{web_done_run}/units/{unit_id}/refetch", follow_redirects=False)
    assert r.status_code == 303
    time.sleep(0.2)
    assert wait_status(client, web_done_run) == "done"
    with client.app.state.session_factory() as s:
        assert unit_row(s, web_done_run, "台大").status == "done"


async def test_non_json_response_fails_only_that_unit(session, session_factory, respx_mock):
    from app.runs.executor import execute_run
    from tests.run_helpers import FakeElsevier, fake_client, make_run

    run = make_run(session)
    fake = FakeElsevier()
    original = fake.scopus

    def scopus(request):
        if "Chengchi" in request.url.params.get("query", ""):
            return httpx.Response(200, text="<html>login</html>")
        return original(request)

    fake.scopus = scopus
    fake.install(respx_mock)
    await execute_run(run.id, session_factory, fake_client())
    session.expire_all()
    assert session.get(Run, run.id).status == "done"
    nccu = unit_row(session, run.id, "政治")
    assert nccu.status == "failed" and "JSON" in nccu.error
    assert unit_row(session, run.id, "中央").status == "done"


async def test_unexpected_unit_error_fails_only_that_unit(session, session_factory, respx_mock, monkeypatch):
    import app.runs.executor as executor
    from tests.run_helpers import FakeElsevier, fake_client, make_run

    run = make_run(session)
    FakeElsevier().install(respx_mock)
    real = executor.search_docs

    async def flaky(client, query, use_cache):
        if "Chengchi" in query:
            raise KeyError("search-results")
        return await real(client, query, use_cache=use_cache)

    monkeypatch.setattr(executor, "search_docs", flaky)
    await executor.execute_run(run.id, session_factory, fake_client())
    session.expire_all()
    assert session.get(Run, run.id).status == "done"
    assert unit_row(session, run.id, "政治").status == "failed"


def test_done_run_with_failed_units_offers_retry_all(client, web_done_run):
    with client.app.state.session_factory() as s:
        unit_row(s, web_done_run, "台大").status = "failed"
        s.commit()
    text = client.get(f"/runs/{web_done_run}").text
    assert f'action="/runs/{web_done_run}/resume"' in text and "重新抓取所有未完成單位" in text


def test_failed_run_offers_resume(client, web_done_run):
    with client.app.state.session_factory() as s:
        s.get(Run, web_done_run).status = "failed"
        s.commit()
    assert f'action="/runs/{web_done_run}/resume"' in client.get(f"/runs/{web_done_run}").text


def test_busy_run_says_so(client, web_done_run):
    manager = client.app.state.run_manager
    manager.is_running = lambda run_id: True
    r = client.post(f"/runs/{web_done_run}/resume", follow_redirects=True)
    assert "已有抓取作業正在執行" in r.text


async def test_refetch_includes_units_sharing_query(session, session_factory, respx_mock):
    from app.runs.executor import execute_run
    from tests.run_helpers import FakeElsevier, fake_client, make_run

    run = make_run(session)
    ncu, ntu = unit_row(session, run.id, "中央"), unit_row(session, run.id, "台大")
    ntu.query = ncu.query
    session.commit()
    fake = FakeElsevier()
    fake.install(respx_mock)
    await execute_run(run.id, session_factory, fake_client())
    fake.papers["Information Management, National Central"] = slice(0, 5)
    await execute_run(run.id, session_factory, fake_client(), unit_ids={ncu.unit_id}, use_cache=False)
    session.expire_all()
    assert session.get(RunUnit, ncu.id).eid_count == session.get(RunUnit, ntu.id).eid_count == 5


async def test_report_headers_use_period_length(session, session_factory, respx_mock, tmp_path):
    from pptx import Presentation
    from openpyxl import load_workbook

    from app.reports.pptx_report import build_dept_report
    from app.reports.xlsx_report import build_run_workbook
    from app.runs.executor import execute_run
    from tests.run_helpers import FakeElsevier, fake_client, make_run

    run = make_run(session, year_from=2023, year_to=2025)
    FakeElsevier().install(respx_mock)
    await execute_run(run.id, session_factory, fake_client())
    prs = Presentation(build_dept_report(session, run.id, "資訊管理學系", tmp_path / "r.pptx"))
    top1 = [s.table for s in prs.slides[4].shapes if s.has_table][0]
    assert [c.text for c in top1.rows[0].cells] == ["單位", "3年篇數\n(A)", "單年教師數\n(B)", "單年師均\n(A/3/B)"]
    pubs = [s.table for s in prs.slides[3].shapes if s.has_table][0]
    assert pubs.cell(1, 1).text == "3年\n總數"
    ws = load_workbook(build_run_workbook(session, run.id, tmp_path / "r.xlsx"))["論文總篇數"]
    assert "3年篇數 (A)" in [c.value for c in ws[2]]
