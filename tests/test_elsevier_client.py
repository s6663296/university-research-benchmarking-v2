import json
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db.models import ApiCache as ApiCacheRow
from app.elsevier.cache import ApiCache, cache_key
from app.elsevier.client import ElsevierClient, ElsevierError, strip_links

SCOPUS = r".*/content/search/scopus.*"


def make(**kw):
    kw.setdefault("backoff_base", 0)
    kw.setdefault("rate_per_sec", 1000)
    return ElsevierClient("k", **kw)


def test_strip_links_removes_nested():
    data = {"link": [1], "results": [{"publication": {"id": 1, "link": {"@href": "x?apiKey=SECRET"}}}]}
    out = strip_links(data)
    assert "SECRET" not in json.dumps(out)
    assert out["results"][0]["publication"] == {"id": 1}
    assert data["link"] == [1]  # 原物件不變


def test_cache_key_ignores_param_order():
    assert cache_key("/p", {"a": 1, "b": 2}) == cache_key("/p", {"b": 2, "a": 1})
    assert cache_key("/p", {"a": 1}) != cache_key("/p", {"a": 2})


async def test_retries_429_then_succeeds(respx_mock):
    route = respx_mock.get(url__regex=SCOPUS).mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json={"ok": 1})])
    c = make()
    assert await c.get_json("/content/search/scopus", {"query": "q"}) == {"ok": 1}
    assert route.call_count == 2


async def test_retries_timeout(respx_mock):
    route = respx_mock.get(url__regex=SCOPUS).mock(
        side_effect=[httpx.ReadTimeout("t"), httpx.Response(200, json={"ok": 1})])
    assert await make().get_json("/content/search/scopus", {}) == {"ok": 1}
    assert route.call_count == 2


async def test_401_not_retried(respx_mock):
    route = respx_mock.get(url__regex=SCOPUS).mock(return_value=httpx.Response(401))
    with pytest.raises(ElsevierError) as e:
        await make().get_json("/content/search/scopus", {"query": "q"})
    assert route.call_count == 1
    assert e.value.status == 401 and e.value.retryable is False


async def test_gives_up_after_max_retries(respx_mock):
    route = respx_mock.get(url__regex=SCOPUS).mock(return_value=httpx.Response(503))
    with pytest.raises(ElsevierError) as e:
        await make(max_retries=3).get_json("/content/search/scopus", {})
    assert route.call_count == 4
    assert e.value.retryable is True and e.value.status == 503


async def test_sends_insttoken_header_only_when_set(respx_mock):
    route = respx_mock.get(url__regex=SCOPUS).mock(return_value=httpx.Response(200, json={}))
    await make().get_json("/content/search/scopus", {})
    assert route.calls[-1].request.headers["X-ELS-APIKey"] == "k"
    assert "X-ELS-Insttoken" not in route.calls[-1].request.headers
    await make(insttoken="tok").get_json("/content/search/scopus", {})
    assert route.calls[-1].request.headers["X-ELS-Insttoken"] == "tok"


async def test_cache_hit_skips_request_and_never_stores_key(respx_mock, session_factory):
    body = {"results": [{"publication": {"id": 1, "link": {"@href": "u?apiKey=k"}}}]}
    route = respx_mock.get(url__regex=SCOPUS).mock(return_value=httpx.Response(200, json=body))
    c = make(cache=ApiCache(session_factory, cache_days=1))
    first = await c.get_json("/content/search/scopus", {"query": "q"})
    second = await c.get_json("/content/search/scopus", {"query": "q"})
    assert route.call_count == 1 and first == second == {"results": [{"publication": {"id": 1}}]}
    with session_factory() as s:
        stored = s.scalars(select(ApiCacheRow.response_json)).all()
    assert len(stored) == 1 and "apiKey" not in stored[0]


async def test_use_cache_false_refetches_and_updates_cache(respx_mock, session_factory):
    route = respx_mock.get(url__regex=SCOPUS).mock(
        side_effect=[httpx.Response(200, json={"v": 1}), httpx.Response(200, json={"v": 2})])
    c = make(cache=ApiCache(session_factory, cache_days=1))
    await c.get_json("/content/search/scopus", {"query": "q"})
    assert await c.get_json("/content/search/scopus", {"query": "q"}, use_cache=False) == {"v": 2}
    assert await c.get_json("/content/search/scopus", {"query": "q"}) == {"v": 2}
    assert route.call_count == 2


def test_cache_expires_next_day(session_factory):
    cache = ApiCache(session_factory, cache_days=1)
    cache.put("k1", "/x", {"a": 1})
    assert cache.get("k1") == {"a": 1}
    with session_factory() as s:
        s.get(ApiCacheRow, "k1").fetched_at = datetime.now() - timedelta(days=1)
        s.commit()
    assert cache.get("k1") is None


async def test_quota_read_from_headers(respx_mock):
    respx_mock.get(url__regex=SCOPUS).mock(return_value=httpx.Response(
        200, json={}, headers={"X-RateLimit-Limit": "20000", "X-RateLimit-Remaining": "19990",
                               "X-RateLimit-Reset": "1700000000"}))
    c = make()
    await c.get_json("/content/search/scopus", {})
    assert c.quota["X-RateLimit-Remaining"] == "19990"


async def test_check_connection_reports_unreachable(respx_mock):
    respx_mock.get(url__regex=SCOPUS).mock(side_effect=httpx.ConnectError("no route"))
    status = await make().check_connection()
    assert status.ok is False
    assert "無法連線" in status.message and "VPN" in status.message


async def test_check_connection_ok(respx_mock):
    respx_mock.get(url__regex=SCOPUS).mock(return_value=httpx.Response(
        200, json={}, headers={"X-RateLimit-Remaining": "123"}))
    status = await make().check_connection()
    assert status.ok is True and status.remaining == "123"


async def test_check_connection_bad_key(respx_mock):
    respx_mock.get(url__regex=SCOPUS).mock(return_value=httpx.Response(401))
    status = await make().check_connection()
    assert status.ok is False and "API Key" in status.message
