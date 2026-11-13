"""全校分析時的 API 限速緩衝：共同暫停、放慢再恢復、429 獨立重試、額度用完立即停止、同時請求上限、批次拆半。"""

import asyncio
import time

import httpx
import pytest

from app.elsevier.client import AsyncRateLimiter, ElsevierClient, ElsevierError, quota_reset, retry_after_seconds
from app.elsevier.scival import MetricsFetcher

SCOPUS = r".*/content/search/scopus.*"
SCIVAL = r".*/analytics/scival/publication/metrics.*"


def make(**kw):
    kw.setdefault("backoff_base", 0)
    kw.setdefault("rate_per_sec", 1000)
    return ElsevierClient("k", **kw)


def test_retry_after_parsing():
    assert retry_after_seconds(httpx.Headers({"Retry-After": "3"})) == 3
    assert retry_after_seconds(httpx.Headers({"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})) == 0
    assert retry_after_seconds(httpx.Headers({})) is None


def test_quota_reset_only_when_exhausted():
    assert quota_reset(httpx.Headers({"X-RateLimit-Remaining": "5", "X-RateLimit-Reset": "100"})) is None
    assert quota_reset(httpx.Headers({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1700000000"})) == 1700000000
    assert quota_reset(httpx.Headers({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1700000000000"})) == 1700000000


def test_limiter_slows_down_and_recovers():
    lim = AsyncRateLimiter(10)
    lim.penalize(0)
    lim.penalize(0)
    assert lim.interval == 1.0 and lim.throttled == 2
    for _ in range(AsyncRateLimiter.RECOVER_AFTER * 4):
        lim.reward()
    assert lim.interval == lim.base_interval


async def test_429_waits_retry_after(respx_mock):
    route = respx_mock.get(url__regex=SCOPUS).mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "0.3"}), httpx.Response(200, json={"ok": 1})])
    start = time.monotonic()
    assert await make().get_json("/content/search/scopus", {}, use_cache=False) == {"ok": 1}
    assert time.monotonic() - start >= 0.3 and route.call_count == 2


async def test_429_has_its_own_retry_budget(respx_mock):
    # 一般錯誤只重試 1 次，限速仍可重試多次
    route = respx_mock.get(url__regex=SCOPUS).mock(side_effect=[httpx.Response(429)] * 3 + [
        httpx.Response(200, json={"ok": 1})])
    c = make(max_retries=1, rate_limit_retries=5)
    c.limiter("scopus").MAX_INTERVAL = 0.01
    assert await c.get_json("/content/search/scopus", {}, use_cache=False) == {"ok": 1}
    assert route.call_count == 4


async def test_429_gives_up_with_clear_message(respx_mock):
    respx_mock.get(url__regex=SCOPUS).mock(return_value=httpx.Response(429))
    c = make(rate_limit_retries=2)
    c.limiter("scopus").MAX_INTERVAL = 0.01
    with pytest.raises(ElsevierError) as e:
        await c.get_json("/content/search/scopus", {}, use_cache=False)
    assert "繼續" in e.value.message and e.value.status == 429


async def test_exhausted_quota_fails_fast(respx_mock):
    reset = str(int(time.time()) + 7 * 86400)
    route = respx_mock.get(url__regex=SCIVAL).mock(return_value=httpx.Response(
        429, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset, "X-RateLimit-Limit": "5000"}))
    with pytest.raises(ElsevierError) as e:
        await make().get_json("/analytics/scival/publication/metrics", {}, use_cache=False)
    assert route.call_count == 1 and "SciVal API 的額度已用完" in e.value.message and not e.value.retryable


async def test_concurrency_cap(respx_mock):
    in_flight = peak = 0

    async def slow(request):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return httpx.Response(200, json={})

    respx_mock.get(url__regex=SCOPUS).mock(side_effect=slow)
    c = make(max_concurrency=2)
    await asyncio.gather(*(c.get_json("/content/search/scopus", {"q": i}, use_cache=False) for i in range(8)))
    assert peak == 2


async def test_scival_batch_split_on_server_error(respx_mock):
    def handler(request):
        ids = request.url.params["publicationIds"].split(",")
        if len(ids) > 2:
            return httpx.Response(504)
        return httpx.Response(200, json={"results": [{"publication": {"id": int(i), "publicationYear": 2024}}
                                                     for i in ids]})

    route = respx_mock.get(url__regex=SCIVAL).mock(side_effect=handler)
    ids = [str(i) for i in range(1, 9)]
    out = await MetricsFetcher(make(max_retries=0), year_range="5yrs", use_cache=False).fetch(ids)
    assert sorted(out) == sorted(ids)
    assert route.call_count == 1 + 2 + 4   # 8 → 4+4 → 2+2+2+2


async def test_scival_split_does_not_hide_auth_errors(respx_mock):
    route = respx_mock.get(url__regex=SCIVAL).mock(return_value=httpx.Response(401))
    with pytest.raises(ElsevierError):
        await MetricsFetcher(make(), year_range="5yrs", use_cache=False).fetch(["1", "2", "3"])
    assert route.call_count == 1


@pytest.mark.parametrize("limit, expected", [(1, 1), (2, 2), (4, 4)])
async def test_run_processes_limited_units_at_once(session, session_factory, respx_mock, monkeypatch, limit, expected):
    """分析一次只處理「同時請求數上限」個單位，其餘排隊（測試分析共有 5 個不同查詢）。"""
    from app.db.settings_store import set_setting
    from app.runs import executor
    from tests.run_helpers import FakeElsevier, fake_client, make_run

    set_setting(session, "max_concurrency", limit)
    run = make_run(session)
    FakeElsevier().install(respx_mock)
    active = peak = 0
    original = executor.search_docs

    async def tracking(*args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.05)
            return await original(*args, **kwargs)
        finally:
            active -= 1

    monkeypatch.setattr(executor, "search_docs", tracking)
    await executor.execute_run(run.id, session_factory, fake_client())
    assert peak == expected


async def test_each_api_has_its_own_concurrency(respx_mock):
    """Scopus 與 SciVal 的限制各自獨立：一支 API 塞滿時，另一支不必排隊。"""
    in_flight = {"scopus": 0, "scival": 0}
    peak = 0

    def slow(api):
        async def handler(request):
            nonlocal peak
            in_flight[api] += 1
            peak = max(peak, sum(in_flight.values()))
            assert in_flight[api] <= 1
            await asyncio.sleep(0.02)
            in_flight[api] -= 1
            return httpx.Response(200, json={})
        return handler

    respx_mock.get(url__regex=SCOPUS).mock(side_effect=slow("scopus"))
    respx_mock.get(url__regex=SCIVAL).mock(side_effect=slow("scival"))
    c = make(max_concurrency=1)
    await asyncio.gather(*(c.get_json(path, {"q": i}, use_cache=False) for i in range(4)
                           for path in ("/content/search/scopus", "/analytics/scival/publication/metrics")))
    assert peak == 2


def test_official_limits_cap_each_api():
    c = make(rate_per_sec=6, api_max_rate={"scival": 5.0, "scopus": 8.0})
    assert c.limiter("scival").base_interval == pytest.approx(1 / 5)
    assert c.limiter("scopus").base_interval == pytest.approx(1 / 6)
