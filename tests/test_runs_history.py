import json

from sqlalchemy import select

from app.db.models import Run
from app.runs.executor import execute_run
from tests.run_helpers import FakeElsevier, fake_client, make_run


def test_history_page_lists_runs_without_ab_compare(client, web_done_run):
    import asyncio
    factory = client.app.state.session_factory
    with factory() as s:
        second = make_run(s).id
    asyncio.run(execute_run(second, factory, fake_client()))
    text = client.get("/runs").text
    assert f"/runs/{web_done_run}" in text and f"/runs/{second}" in text
    assert 'name="a"' not in text and "/runs/compare" not in text
    assert client.get("/runs/compare", params={"a": web_done_run, "b": second}).status_code in (404, 422)
    assert f"#{second}" in client.get("/").text


async def test_run_records_api_usage_and_accumulates(session, session_factory, respx_mock):
    FakeElsevier().install(respx_mock)
    run = make_run(session)
    client = fake_client()
    await execute_run(run.id, session_factory, client)
    session.expire_all()
    usage = json.loads(session.get(Run, run.id).api_usage_json)
    assert set(usage) == {"scopus", "abstract", "scival"}
    assert usage == {api: client.usage[api] for api in usage} and usage["scopus"] > 0 and usage["scival"] > 0
    # 重抓不走快取：用量累加在同一個分析上
    again = fake_client()
    await execute_run(run.id, session_factory, again, use_cache=False)
    session.expire_all()
    total = json.loads(session.get(Run, run.id).api_usage_json)
    assert total == {api: usage[api] + again.usage[api] for api in usage}


def test_history_shows_api_usage_column(client, web_done_run):
    factory = client.app.state.session_factory
    with factory() as s:
        s.get(Run, web_done_run).api_usage_json = json.dumps({"scopus": 1234, "abstract": 0, "scival": 56})
        old = make_run(s)
        old.api_usage_json = "{}"
        s.commit()
        old_id = old.id
    text = client.get("/runs").text
    assert "<th>API 使用</th>" in text
    assert "Scopus Search <span class=\"num\">1,234</span>" in text and "SciVal <span class=\"num\">56</span>" in text
    assert "Abstract Retrieval" not in text  # 沒用到的 API 不列
    assert "這個分析建立時尚未記錄 API 用量" in text  # old_id 的舊分析
    assert f"/runs/{old_id}" in text


def test_delete_run_removes_it_and_its_data(client, web_done_run):
    from app.db.models import Result, RunUnit
    r = client.post(f"/runs/{web_done_run}/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/runs"
    assert client.get(f"/runs/{web_done_run}").status_code == 404
    with client.app.state.session_factory() as s:
        assert s.scalar(select(Result).where(Result.run_id == web_done_run)) is None
        assert s.scalar(select(RunUnit).where(RunUnit.run_id == web_done_run)) is None


def test_cannot_delete_running_run(client, web_done_run):
    client.app.state.run_manager.is_running = lambda run_id: True
    assert client.post(f"/runs/{web_done_run}/delete").status_code == 409
    assert client.get(f"/runs/{web_done_run}").status_code == 200


def test_delete_run_renumbers_the_rest(client, web_done_run):
    import asyncio
    factory = client.app.state.session_factory
    with factory() as s:
        second, third = make_run(s).id, make_run(s).id
    for rid in (second, third):
        asyncio.run(execute_run(rid, factory, fake_client()))
    exports = client.app.state.settings.data_dir / "exports"
    (exports / str(third)).mkdir(parents=True)
    (exports / str(third) / "r.xlsx").write_bytes(b"x")
    with factory() as s:
        from app.db.models import Export
        s.add(Export(run_id=third, kind="xlsx", path=str(exports / str(third) / "r.xlsx"),
                     created_at=__import__("datetime").datetime.now()))
        s.commit()
    assert client.post(f"/runs/{second}/delete", follow_redirects=False).status_code == 303
    with factory() as s:
        from app.db.models import Export, Run, RunUnit
        assert list(s.scalars(select(Run.id).order_by(Run.id))) == [web_done_run, second]
        assert s.scalar(select(RunUnit).where(RunUnit.run_id == second)) is not None  # 原 third 的資料跟著改號
        assert s.scalars(select(Export.path)).one() == str(exports / str(second) / "r.xlsx")
    assert (exports / str(second) / "r.xlsx").exists() and not (exports / str(third)).exists()
    assert client.get(f"/runs/{third}").status_code == 404
