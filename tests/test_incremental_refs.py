import asyncio
import time

import httpx
import pytest

from app.db.models import REFS_ENDPOINT
from app.elsevier.cache import ApiCache
from app.elsevier.client import ElsevierClient, ElsevierError
from app.elsevier.inset_selfcite import PERMANENT_DAYS, InSetSelfCitations, refs_key
from app.elsevier.scopus import search_docs
from tests.elsevier_fixtures import abstract_full

EMPTY_SEARCH = {"search-results": {"opensearch:totalResults": "0", "entry": []}}


async def test_unit_does_not_wait_for_another_units_downloads():
    release = asyncio.Event()
    references = {"1": [], "2": ["2"], "3": [], "4": ["3"]}

    async def respond(request):
        sid = request.url.path.rsplit("/", 1)[1]
        if sid == "1":   # 大型合作論文：下載很久
            await release.wait()
        return httpx.Response(200, json=abstract_full(references[sid]))

    client = ElsevierClient("k", rate_per_sec=0, transport=httpx.MockTransport(respond))
    counter = InSetSelfCitations(client)
    try:
        slow = asyncio.create_task(counter.count(["1", "2"], {"1": 1, "2": 1}))
        await asyncio.sleep(0.01)
        assert await asyncio.wait_for(counter.count(["3", "4"], {"3": 1, "4": 1}), 1) == {"3": 1}
        assert not slow.done()
        release.set()
        assert await slow == {"2": 1}
    finally:
        await client.aclose()


async def test_shared_paper_is_downloaded_once_by_units_counting_together():
    calls = []
    references = {"1": ["2"], "2": [], "3": ["1"]}

    async def respond(request):
        sid = request.url.path.rsplit("/", 1)[1]
        calls.append(sid)
        await asyncio.sleep(0.01)
        return httpx.Response(200, json=abstract_full(references[sid]))

    client = ElsevierClient("k", rate_per_sec=0, transport=httpx.MockTransport(respond))
    counter = InSetSelfCitations(client)
    try:
        a, b = await asyncio.gather(counter.count(["1", "2"], {"1": 1, "2": 1}),
                                    counter.count(["1", "3"], {"1": 1, "3": 1}))
    finally:
        await client.aclose()
    assert (a, b) == ({"2": 1}, {"1": 1})
    assert sorted(calls) == ["1", "2", "3"]


async def test_cancelled_unit_does_not_break_the_unit_downloading_its_papers():
    release = asyncio.Event()
    references = {"1": ["2"], "2": [], "3": ["1"]}

    async def respond(request):
        sid = request.url.path.rsplit("/", 1)[1]
        if sid == "1":
            await release.wait()
        return httpx.Response(200, json=abstract_full(references[sid]))

    client = ElsevierClient("k", rate_per_sec=0, transport=httpx.MockTransport(respond))
    counter = InSetSelfCitations(client)
    try:
        owner = asyncio.create_task(counter.count(["1", "2"], {"1": 1, "2": 1}))
        await asyncio.sleep(0.01)
        waiting = asyncio.create_task(counter.count(["1", "3"], {"1": 1, "3": 1}))   # 等 owner 下載的 1
        await asyncio.sleep(0.01)
        waiting.cancel()
        release.set()
        assert await owner == {"2": 1}
    finally:
        await client.aclose()


async def test_missing_references_download_without_waiting_for_the_reload_check(session_factory):
    ApiCache(session_factory, PERMANENT_DAYS).put(refs_key("1"), REFS_ENDPOINT, {"refs": ["2"], "complete": True})
    release = asyncio.Event()
    downloads = []

    async def respond(request):
        if request.url.path.endswith("/search/scopus"):   # 快取中的 1 是否被重載：查很久
            await release.wait()
            return httpx.Response(200, json=EMPTY_SEARCH)
        downloads.append(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, json=abstract_full([]))

    client = ElsevierClient("k", rate_per_sec=0, cache=ApiCache(session_factory), transport=httpx.MockTransport(respond))
    try:
        task = asyncio.create_task(InSetSelfCitations(client).count(["1", "2"], {"1": 1, "2": 1}))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if downloads:
                break
        assert downloads == ["2"] and not task.done()
        release.set()
        assert await task == {"2": 1}
    finally:
        await client.aclose()


async def test_incremental_reuses_only_unchanged_sources_and_recounts_new_members(session_factory):
    calls = []
    search_queries = []
    references = {"1": ["2", "3"], "2": ["2"], "3": ["1"]}

    def respond(request):
        if request.url.path.endswith("/search/scopus"):
            search_queries.append(request.url.params["query"])
            return httpx.Response(200, json={"search-results": {"opensearch:totalResults": "1",
                "entry": [{"dc:identifier": "SCOPUS_ID:2", "eid": "2-s2.0-2"}]}})
        sid = request.url.path.rsplit("/", 1)[1]
        calls.append(sid)
        return httpx.Response(200, json=abstract_full(references[sid]))

    client = ElsevierClient("k", cache=ApiCache(session_factory), transport=httpx.MockTransport(respond),
                            rate_per_sec=0)
    try:
        first = InSetSelfCitations(client)
        assert await first.count(["1", "2"], {"1": 2, "2": 2}) == {"2": 2}
        assert sorted(calls) == ["1", "2"] and not search_queries
        calls.clear()
        references["2"] = ["1"]
        second = InSetSelfCitations(client)
        assert await second.count(["1", "2", "3"], dict.fromkeys("123", 3)) == {"1": 2, "2": 1, "3": 1}
        assert sorted(calls) == ["2", "3"] and len(search_queries) == 1
        assert "LOAD-DATE AFT" in search_queries[0]
        assert await second.count(["1", "3"], dict.fromkeys("13", 3)) == {"1": 1, "3": 1}
        assert len(calls) == 2
    finally:
        await client.aclose()


async def test_strict_search_rejects_missing_page():
    response = {"search-results": {"opensearch:totalResults": "2",
                "entry": [{"dc:identifier": "SCOPUS_ID:1", "eid": "2-s2.0-1"}]}}
    client = ElsevierClient("k", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)))
    try:
        with pytest.raises(ElsevierError):
            await search_docs(client, "unit", use_cache=False, strict=True)
    finally:
        await client.aclose()


async def test_null_bibliography_checks_the_reference_total():
    def respond(request):
        if request.url.params["view"] == "FULL":
            return httpx.Response(200, json={"abstracts-retrieval-response": {"item": {"bibrecord": {"tail": None}}}})
        return httpx.Response(200, json={"abstracts-retrieval-response": {"references": {"@total-references": "0"}}})

    client = ElsevierClient("k", transport=httpx.MockTransport(respond), rate_per_sec=0)
    try:
        counter = InSetSelfCitations(client)
        assert await counter.count(["123"], {"123": 1}) == {}
    finally:
        await client.aclose()


async def test_incremental_rejects_more_internal_citations_than_scival_received():
    client = ElsevierClient("k", rate_per_sec=0, transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=abstract_full(["1"]))))
    try:
        with pytest.raises(ElsevierError, match="SciVal"):
            await InSetSelfCitations(client).count(["1", "2"], {"1": 1, "2": 2})
    finally:
        await client.aclose()


async def test_incremental_waits_for_reported_quota_reset(monkeypatch):
    requests = []
    waits = []

    def respond(request):
        requests.append(request.url.path)
        if len(requests) == 1:
            return httpx.Response(429, headers={"X-RateLimit-Remaining": "0",
                                                "X-RateLimit-Reset": str(int(time.time()) + 3600)})
        return httpx.Response(200, json={"updated": True})

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr("app.elsevier.client.asyncio.sleep", sleep)
    client = ElsevierClient("k", transport=httpx.MockTransport(respond), rate_per_sec=0)
    client.wait_for_quota_reset = True
    try:
        assert await client.get_json("/example", {}) == {"updated": True}
        assert len(waits) == 1 and 3590 < waits[0] < 3610
    finally:
        await client.aclose()


async def test_low_quota_does_not_pause(monkeypatch):
    """剩不到 10% 也照常送出，只有真的用完（HTTP 429）才等重置。"""
    waits = []

    async def sleep(delay):
        waits.append(delay)

    reset = str(int(time.time()) + 3600)
    headers = {"X-RateLimit-Remaining": "9", "X-RateLimit-Limit": "100", "X-RateLimit-Reset": reset}
    client = ElsevierClient("k", rate_per_sec=0, transport=httpx.MockTransport(
        lambda _: httpx.Response(200, headers=headers, json={"ok": True})))
    monkeypatch.setattr("app.elsevier.client.asyncio.sleep", sleep)
    client.wait_for_quota_reset = True
    try:
        await client.get_json("/content/abstract/scopus_id/1", {})
        await client.get_json("/content/search/scopus", {})
        await client.get_json("/content/abstract/scopus_id/2", {})
        assert waits == [] and not client.quota_resume_at
    finally:
        await client.aclose()
