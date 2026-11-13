from decimal import Decimal
from functools import partial
from pathlib import Path

import pytest

import asyncio

import httpx

from app.elsevier import scival
from app.elsevier.cache import ApiCache
from app.elsevier.client import ElsevierClient, ElsevierError
from app.elsevier.scival import MetricsFetcher, scival_year_ranges
from app.elsevier.selfcite import STRATEGIES, get_strategy, usable_strategy_id
from tests.elsevier_fixtures import SCIVAL_URL, ids67, scival_handler


def client(cache=None):
    return ElsevierClient("k", backoff_base=0, rate_per_sec=1000, max_retries=0, cache=cache)


def fetcher(c=None, *, include_self=None, use_cache=False, year_range="5yrs"):
    return MetricsFetcher(c or client(), year_range=year_range, include_self=include_self, use_cache=use_cache)


def sent_ids(route) -> list[list[str]]:
    return [c.request.url.params["publicationIds"].split(",") for c in route.calls]


def test_fixtures_have_no_api_key():
    for p in Path("tests/fixtures").rglob("*.json"):
        assert "apiKey" not in p.read_text(encoding="utf-8"), p


def test_year_ranges():
    assert scival_year_ranges(2021, 2025, 2026) == ("5yrs", "5yrsAndCurrentAndFuture")
    assert scival_year_ranges(2019, 2023, 2026) == ("10yrs", "10yrsAndCurrentAndFuture")
    with pytest.raises(ValueError, match="今年"):
        scival_year_ranges(2022, 2026, 2026)
    with pytest.raises(ValueError):
        scival_year_ranges(2010, 2014, 2026)
    with pytest.raises(ValueError):
        scival_year_ranges(2024, 2022, 2026)


async def test_metrics_from_recorded_response(respx_mock):
    route = respx_mock.get(url__regex=SCIVAL_URL).mock(side_effect=scival_handler)
    f = fetcher()
    m = await f.fetch(ids67())
    assert len(m) == 67 and f.data_source["lastUpdated"] == "2026-10-25"
    assert route.call_count == 1  # 一次最多 100 篇，67 篇一次就好
    assert sum(x.q1 for x in m.values()) == 40
    assert sum(x.top1 for x in m.values()) == 0 and sum(x.top10 for x in m.values()) == 0
    assert sum(x.intl_collab for x in m.values()) == 20
    assert all(isinstance(x.fwci, Decimal) for x in m.values())
    assert all(x.citations is None and 2021 <= x.year <= 2025 for x in m.values())
    p = route.calls[0].request.url.params
    assert p["includedDocs"] == "AllPublicationTypes" and p["byYear"] == "true"
    assert p["journalImpactType"] == "CiteScore" and p["yearRange"] == "5yrs"
    assert "CitationCount" not in p["metricTypes"] and "includeSelfCitations" not in p


async def test_metrics_batches_of_at_most_max_ids(respx_mock, monkeypatch):
    assert scival.MAX_IDS == 100  # 實測 SciVal 一次 200 篇會回 HTTP 400
    monkeypatch.setattr(scival, "MAX_IDS", 25)
    route = respx_mock.get(url__regex=SCIVAL_URL).mock(side_effect=scival_handler)
    assert len(await fetcher().fetch(ids67())) == 67
    assert [len(ids) for ids in sent_ids(route)] == [25, 25, 17]


async def test_citations_come_with_other_metrics_in_one_request(respx_mock):
    route = respx_mock.get(url__regex=SCIVAL_URL).mock(side_effect=scival_handler)
    m = await fetcher(include_self="false", year_range="5yrsAndCurrentAndFuture").fetch(ids67())
    assert route.call_count == 1
    assert sum(x.citations for x in m.values()) == 290
    assert sum(x.q1 for x in m.values()) == 40 and sum(x.intl_collab for x in m.values()) == 20
    p = route.calls[0].request.url.params
    assert p["metricTypes"].split(",")[-1] == "CitationCount"
    assert p["includeSelfCitations"] == "false" and p["yearRange"] == "5yrsAndCurrentAndFuture"


async def test_same_paper_requested_once_across_units(respx_mock):
    route = respx_mock.get(url__regex=SCIVAL_URL).mock(side_effect=scival_handler)
    f = fetcher()
    a, b = await asyncio.gather(f.fetch(ids67()[:40]), f.fetch(ids67()[20:]))
    again = await f.fetch(ids67()[10:30])
    assert set(a) == set(ids67()[:40]) and set(b) == set(ids67()[20:]) and set(again) == set(ids67()[10:30])
    requested = [i for ids in sent_ids(route) for i in ids]
    assert sorted(requested) == sorted(ids67())


async def test_per_paper_cache_reused_with_different_batches(respx_mock, session_factory):
    route = respx_mock.get(url__regex=SCIVAL_URL).mock(side_effect=scival_handler)
    cache = ApiCache(session_factory, cache_days=1)
    await fetcher(client(cache), use_cache=True).fetch(ids67()[:40])
    f = fetcher(client(cache), use_cache=True)
    m = await f.fetch(list(reversed(ids67())))
    assert len(m) == 67 and f.data_source["lastUpdated"] == "2026-10-25"
    assert sent_ids(route)[1] == list(reversed(ids67()[40:]))  # 只抓快取裡沒有的
    await fetcher(client(cache), use_cache=False).fetch(ids67()[:5])
    assert route.call_count == 3  # use_cache=False 一律重抓


async def test_cache_depends_on_request_params(respx_mock, session_factory):
    route = respx_mock.get(url__regex=SCIVAL_URL).mock(side_effect=scival_handler)
    cache = ApiCache(session_factory, cache_days=1)
    await fetcher(client(cache), use_cache=True).fetch(ids67())
    m = await fetcher(client(cache), use_cache=True, include_self="false").fetch(ids67())
    assert route.call_count == 2 and sum(x.citations for x in m.values()) == 290


async def test_missing_ids_absent_and_cached(respx_mock, session_factory):
    missing = ids67()[0]
    route = respx_mock.get(url__regex=SCIVAL_URL).mock(side_effect=lambda r: scival_handler(r, drop={missing}))
    cache = ApiCache(session_factory, cache_days=1)
    m = await fetcher(client(cache), use_cache=True).fetch(ids67())
    assert len(m) == 66 and missing not in m
    assert missing not in await fetcher(client(cache), use_cache=True).fetch([missing])
    assert route.call_count == 1


async def test_failed_batch_fails_every_waiting_unit(respx_mock):
    route = respx_mock.get(url__regex=SCIVAL_URL).mock(return_value=httpx.Response(429))
    f = fetcher()
    results = await asyncio.gather(f.fetch(ids67()[:10]), f.fetch(ids67()[:5]), return_exceptions=True)
    assert all(isinstance(r, ElsevierError) and r.status == 429 for r in results)
    assert route.call_count == 1


async def test_empty_ids_no_request(respx_mock):
    route = respx_mock.get(url__regex=SCIVAL_URL)
    assert await fetcher().fetch([]) == {}
    assert route.call_count == 0


def test_strategy_ids():
    assert set(STRATEGIES) == {"scival_pubset", "none"}
    assert get_strategy("scival_pubset").in_set and not get_strategy("none").in_set
    with pytest.raises(KeyError):
        get_strategy("unknown")


def test_removed_strategies_still_label_old_runs():
    for old in ("scival_param", "scopus_citation_overview"):
        assert "已移除" in get_strategy(old).label and not get_strategy(old).in_set
        assert usable_strategy_id(old) == "scival_pubset"
    assert usable_strategy_id("none") == "none"
