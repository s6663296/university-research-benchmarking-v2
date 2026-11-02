import asyncio

import pytest
from sqlalchemy import select

from app.db.models import Run, RunUnit
from app.runs.manager import RunManager
from tests.run_helpers import make_run


@pytest.mark.asyncio
async def test_pause_cancels_task_and_preserves_completed_units(session, session_factory, monkeypatch):
    run_id = make_run(session).id
    started = asyncio.Event()

    async def fake_execute(run_id, factory, client, **kwargs):
        with factory() as s:
            run = s.get(Run, run_id)
            run.status = "running"
            units = s.scalars(select(RunUnit).where(RunUnit.run_id == run_id).order_by(RunUnit.id)).all()
            units[0].status = "done"
            units[1].status = "scival"
            s.commit()
        started.set()
        await asyncio.Event().wait()

    class Client:
        async def aclose(self):
            pass

    monkeypatch.setattr("app.runs.manager.execute_run", fake_execute)
    manager = RunManager(session_factory, Client)
    assert manager.start(run_id)
    await started.wait()
    assert await manager.pause(run_id)
    assert not manager.is_running(run_id)
    session.expire_all()
    assert session.get(Run, run_id).status == "interrupted"
    units = session.scalars(select(RunUnit).where(RunUnit.run_id == run_id).order_by(RunUnit.id)).all()
    assert units[0].status == "done" and units[1].status == "pending"
    assert not await manager.pause(run_id)


@pytest.mark.asyncio
async def test_pause_before_background_task_starts(session, session_factory):
    run_id = make_run(session).id
    manager = RunManager(session_factory, lambda: None)
    assert manager.start(run_id)
    assert await manager.pause(run_id)
    session.expire_all()
    assert session.get(Run, run_id).status == "interrupted"


@pytest.mark.asyncio
async def test_pause_cancels_real_executor_fetches(session, session_factory, monkeypatch):
    run_id = make_run(session).id
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked_search(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    class Client:
        async def aclose(self):
            pass

    monkeypatch.setattr("app.runs.executor.search_docs", blocked_search)
    manager = RunManager(session_factory, Client)
    assert manager.start(run_id)
    await started.wait()
    assert await manager.pause(run_id)
    assert cancelled.is_set()
    session.expire_all()
    assert session.get(Run, run_id).status == "interrupted"
    units = session.scalars(select(RunUnit).where(RunUnit.run_id == run_id)).all()
    assert all(unit.status == "pending" for unit in units)


@pytest.mark.asyncio
async def test_stop_ends_running_or_paused_run(session, session_factory):
    manager = RunManager(session_factory, lambda: None)
    running = make_run(session).id
    assert manager.start(running)
    assert await manager.stop(running)
    paused = make_run(session).id
    session.get(Run, paused).status = "interrupted"
    session.commit()
    assert await manager.stop(paused)
    session.expire_all()
    assert session.get(Run, running).status == session.get(Run, paused).status == "stopped"
    assert not await manager.stop(paused)  # 已停止的不再處理


def test_running_pipeline_offers_pause_and_stop(client, web_done_run):
    with client.app.state.session_factory() as s:
        s.get(Run, web_done_run).status = "running"
        s.commit()
    client.app.state.run_manager.is_running = lambda run_id: True
    text = client.get("/pipeline").text
    assert f'action="/runs/{web_done_run}/pause"' in text and ">❚❚ 暫停</button>" in text
    assert f'action="/runs/{web_done_run}/stop"' in text
    assert 'class="stop-icon" aria-hidden="true"' in text and ">停止</button>" in text
    assert 'action="/runs/start"' not in text


def test_paused_pipeline_offers_resume_and_stop(client, web_done_run):
    with client.app.state.session_factory() as s:
        s.get(Run, web_done_run).status = "interrupted"
        s.commit()
    text = client.get("/pipeline").text
    assert f'action="/runs/{web_done_run}/resume?home=1"' in text and ">▶ 繼續</button>" in text
    assert f'action="/runs/{web_done_run}/stop"' in text
    assert 'action="/runs/start"' not in text and "/pause" not in text


def test_stopped_run_can_still_resume_from_run_page(client, web_done_run):
    with client.app.state.session_factory() as s:
        s.get(Run, web_done_run).status = "stopped"
        s.commit()
    text = client.get("/pipeline").text
    assert 'action="/runs/start"' in text and "/stop" not in text  # 首頁回到可開始新分析
    detail = client.get(f"/runs/{web_done_run}").text
    assert f'action="/runs/{web_done_run}/resume"' in detail and "從中斷處繼續" in detail


def test_pause_route_targets_requested_run(client, web_done_run, monkeypatch):
    paused = []

    async def pause(run_id):
        paused.append(run_id)
        return True

    monkeypatch.setattr(client.app.state.run_manager, "pause", pause, raising=False)
    response = client.post(f"/runs/{web_done_run}/pause", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert paused == [web_done_run]


def test_stop_route_targets_requested_run(client, web_done_run, monkeypatch):
    stopped = []

    async def stop(run_id):
        stopped.append(run_id)
        return True

    monkeypatch.setattr(client.app.state.run_manager, "stop", stop, raising=False)
    response = client.post(f"/runs/{web_done_run}/stop", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert stopped == [web_done_run]


def test_run_page_controls_follow_background(client, web_done_run):
    manager = client.app.state.run_manager
    text = client.get(f"/runs/{web_done_run}").text
    assert f"/runs/{web_done_run}/pause" not in text  # 背景沒在跑
    manager.is_running = lambda run_id: True
    text = client.get(f"/runs/{web_done_run}").text
    assert f'action="/runs/{web_done_run}/pause?from=run"' in text
    assert f'action="/runs/{web_done_run}/stop?from=run"' in text


def test_pause_from_run_page_returns_there(client, web_done_run):
    r = client.post(f"/runs/{web_done_run}/pause?from=run", follow_redirects=False)
    assert r.headers["location"] == f"/runs/{web_done_run}"
