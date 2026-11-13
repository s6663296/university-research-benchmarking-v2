import json
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db.models import ApiCache as ApiCacheRow
from app.elsevier.cache import ApiCache, cache_key
from app.elsevier.client import ElsevierClient, ElsevierError, strip_links
from app.elsevier.quota import Quota, QuotaStore, quota_rows

SCOPUS = r".*/content/search/scopus.*"
SCIVAL = r".*/analytics/scival/.*"


def quota_headers(limit, remaining, reset="1793849792"):
    return {"X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": str(remaining), "X-RateLimit-Reset": reset}


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


async def test_quota_tracked_per_api(respx_mock):
    respx_mock.get(url__regex=SCOPUS).mock(return_value=httpx.Response(200, json={}, headers=quota_headers(20000, 19990)))
    respx_mock.get(url__regex=SCIVAL).mock(return_value=httpx.Response(200, json={}, headers=quota_headers(5000, 3700)))
    seen = []
    c = make(on_quota=lambda api, q: seen.append((api, q.remaining)))
    await c.get_json("/content/search/scopus", {})
    await c.get_json("/analytics/scival/publication/metrics", {})
    assert c.quota["scopus"] == Quota(20000, 19990, datetime.fromtimestamp(1793849792))
    assert c.quota["scival"].limit == 5000 and c.quota["scival"].remaining == 3700
    assert seen == [("scopus", 19990), ("scival", 3700)]


async def test_usage_counts_successful_requests_per_api(respx_mock, session_factory):
    respx_mock.get(url__regex=SCOPUS).mock(return_value=httpx.Response(200, json={}))
    c = make(cache=ApiCache(session_factory))
    await c.get_json("/content/search/scopus", {"q": 1})
    await c.get_json("/content/search/scopus", {"q": 1})   # 快取命中不花額度
    await c.get_json("/content/search/scopus", {"q": 2})
    assert c.usage == {"scopus": 2}


async def test_abstract_retrieval_gets_more_concurrent_requests():
    c = make(max_concurrency=4)
    # 大型合作論文的 Abstract Retrieval 回應很大，同時多送幾個；其他 API 照設定
    assert c._slots_of("abstract")._value == 12 and c._slots_of("scival")._value == 4


def test_quota_store_keeps_latest_per_api(session_factory):
    store = QuotaStore(session_factory)
    store.record("scival", Quota(5000, 3800, None))
    store.record("scopus", Quota(20000, 19000, None))
    store.record("scival", Quota(5000, 3700, None))
    with session_factory() as s:
        rows = quota_rows(s)
    assert [(r.api, r.label, r.remaining, r.limit) for r in rows] == [
        ("scopus", "Scopus Search API", 19000, 20000), ("scival", "SciVal API", 3700, 5000)]


async def test_check_connection_reports_unreachable(respx_mock):
    respx_mock.get(url__regex=SCOPUS).mock(side_effect=httpx.ConnectError("no route"))
    status = await make().check_connection()
    assert status.ok is False
    assert "無法連線" in status.message and "VPN" in status.message


async def test_check_connection_checks_both_apis(respx_mock):
    scopus = respx_mock.get(url__regex=SCOPUS).mock(return_value=httpx.Response(
        200, json={}, headers=quota_headers(20000, 123)))
    scival = respx_mock.get(url__regex=SCIVAL).mock(return_value=httpx.Response(
        200, json={}, headers=quota_headers(5000, 45)))
    c = make()
    status = await c.check_connection()
    assert status.ok is True and scopus.call_count == 1 and scival.call_count == 1
    assert c.quota["scopus"].remaining == 123 and c.quota["scival"].remaining == 45


async def test_check_connection_scival_denied(respx_mock):
    respx_mock.get(url__regex=SCOPUS).mock(return_value=httpx.Response(200, json={}))
    respx_mock.get(url__regex=SCIVAL).mock(return_value=httpx.Response(403))
    status = await make().check_connection()
    assert status.ok is False and status.message.startswith("SciVal") and "API Key" in status.message


async def test_check_connection_bad_key(respx_mock):
    respx_mock.get(url__regex=SCOPUS).mock(return_value=httpx.Response(401))
    status = await make().check_connection()
    assert status.ok is False and "API Key" in status.message
