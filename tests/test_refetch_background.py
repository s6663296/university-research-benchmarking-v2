"""已完成分析在背景重新抓取單位：首頁流程圖必須即時反映每個單位實際走到哪一步。"""

import asyncio
import threading
import time

from sqlalchemy import select

from app.db.models import Run, RunUnit, Unit
from app.runs.manager import Refetch, RunManager
from app.web.dashboard import pipeline


def _ru(s, run_id, school):
    return s.scalars(select(RunUnit).join(Unit, Unit.id == RunUnit.unit_id)
                     .where(RunUnit.run_id == run_id, Unit.school == school)).first()


def _stages(s, run_id, refetch):
    return {st.key: st for st in pipeline(s, run_id, refetch=refetch)}


def test_home_follows_each_unit_through_refetch(client, web_done_run):
    """排隊 → Scopus → SciVal → 排除自我引用 → 重算 → 完成，每一步首頁都和資料庫的實際狀態一致。"""
    factory = client.app.state.session_factory
    with factory() as s:
        ntu, nccu = _ru(s, web_done_run, "台大"), _ru(s, web_done_run, "政治")
        a, b = ntu.unit_id, nccu.unit_id
        refetch = Refetch(queued=[b], active={a})

        def step(status, eid_count=None):
            ru = _ru(s, web_done_run, "台大")
            ru.status, ru.eid_count = status, eid_count
            s.commit()
            return _stages(s, web_done_run, refetch)

        st = step("scopus")
        assert st["fetch"].state == "active" and st["fetch"].progress == ("Scopus 0／2", "SciVal 0／2")
        assert st["fetch"].summary == "重新抓取 0／2"
        assert st["review"].state != "active" and st["output"].summary == "尚未下載"  # 其他單位的結果照常顯示

        st = step("scival", 10)
        assert st["fetch"].progress == ("Scopus 1／2", "SciVal 0／2")

        st = step("selfcite", 10)
        assert st["fetch"].progress == ("Scopus 1／2", "SciVal 1／2")
        assert st["selfcite"].state == "active" and st["selfcite"].progress[0] == "單位 0／2"

        st = step("done", 10)  # 這一輪的單位都完成、背景還在重算
        assert st["selfcite"].progress[0] == "單位 1／2"
        assert st["review"].state == "active" and st["review"].summary == "計算中"
        assert st["output"].state == "todo"

        refetch.active, refetch.finished = set(), [a]  # 第一輪結束，排隊的單位進入下一輪前
        st = _stages(s, web_done_run, refetch)
        assert st["fetch"].summary == "重新抓取 1／2" and st["review"].state != "active"

        refetch.queued, refetch.active = [], {b}
        nccu = _ru(s, web_done_run, "政治")
        nccu.status, nccu.error = "failed", "HTTP 401"
        s.commit()
        st = _stages(s, web_done_run, refetch)
        assert st["fetch"].state == "error" and "1 個失敗" in st["fetch"].summary


def _gated_manager(monkeypatch, gate: threading.Event, calls: list):
    import app.runs.manager as manager_module

    async def fake_execute(run_id, *args, unit_ids=None, **kwargs):
        calls.append((run_id, set(unit_ids) if unit_ids else None))
        while not gate.is_set():
            await asyncio.sleep(0.01)

    class FakeClient:
        async def aclose(self):
            await asyncio.sleep(0.05)  # 關連線期間再按，單位不能遺漏

    monkeypatch.setattr(manager_module, "execute_run", fake_execute)
    return FakeClient


def test_click_during_close_is_not_lost(monkeypatch):
    gate, calls = threading.Event(), []
    client_factory = _gated_manager(monkeypatch, gate, calls)

    async def scenario():
        m = RunManager(None, client_factory)
        m.current_run_id = 9
        assert m.refetch_unit(1, 10)
        await asyncio.sleep(0.02)
        gate.set()
        await asyncio.sleep(0.02)  # 第一輪結束、正在關連線
        assert m.refetch_unit(1, 11)
        for _ in range(100):
            if not m.any_running():
                break
            await asyncio.sleep(0.01)
        assert calls == [(1, {10}), (1, {11})]
        assert m.display_run_id == 9  # 重新抓取結束，首頁回到原本顯示的分析
        assert not m.any_full_running()

    asyncio.run(scenario())


def test_full_run_shown_over_refetch(monkeypatch):
    gate, calls = threading.Event(), []
    client_factory = _gated_manager(monkeypatch, gate, calls)

    async def scenario():
        m = RunManager(None, client_factory)
        m.start(2)
        m.refetch_unit(1, 10)
        assert m.display_run_id == 2 and m.any_full_running()
        assert m.refetching(1) is not None and m.refetching(2) is None
        gate.set()
        await asyncio.sleep(0.1)

    asyncio.run(scenario())


def test_clicking_page_is_not_reloaded_and_home_can_start(client, web_done_run, monkeypatch):
    """按下的那頁拿到新的狀態簽章（不會被自己觸發整頁重新整理）；重新抓取期間首頁仍可開始新分析。"""
    gate, calls = threading.Event(), []
    _gated_manager(monkeypatch, gate, calls)
    with client.app.state.session_factory() as s:
        unit_id = _ru(s, web_done_run, "台大").unit_id
    page = client.get(f"/runs/{web_done_run}?ind=anomalies").text
    old_watch = page.split('id="watch" hidden hx-get="')[1].split('"')[0]
    r = client.post(f"/runs/{web_done_run}/units/{unit_id}/refetch", headers={"HX-Request": "true"})
    new_watch = r.text.split('hx-get="/watch')[1].split('"')[0]
    assert 'hx-swap-oob="true"' in r.text and "/watch" + new_watch != old_watch
    assert "HX-Refresh" not in client.get("/watch" + new_watch).headers
    assert r.text.count('id="watch"') == 1
    page = client.get(f"/runs/{web_done_run}?ind=anomalies").text
    assert page.count('id="watch"') == 1  # 頁面上的按鈕不會各自帶一份簽章
    assert client.post("/runs/start", follow_redirects=False).headers["location"] == "/runs/new"
    gate.set()
    end = time.time() + 5
    while client.app.state.run_manager.any_running() and time.time() < end:
        time.sleep(0.02)
    assert "HX-Refresh" in client.get("/watch" + new_watch).headers  # 整批完成才重新整理一次


def test_cancelled_refetch_resets_units(client, web_done_run):
    """重新抓取到一半被暫停：分析維持已完成，抓到一半的單位退回待抓，不會一直顯示抓取中。"""
    factory = client.app.state.session_factory
    with factory() as s:
        _ru(s, web_done_run, "台大").status = "scival"
        s.commit()
    assert asyncio.run(client.app.state.run_manager.pause(web_done_run)) is False
    with factory() as s:
        assert s.get(Run, web_done_run).status == "done"
        assert _ru(s, web_done_run, "台大").status == "pending"
