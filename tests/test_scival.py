from decimal import Decimal
from functools import partial
from pathlib import Path

import pytest

from app.elsevier.client import ElsevierClient, ElsevierError
from app.elsevier.scival import fetch_metrics, scival_year_ranges
from app.elsevier.selfcite import STRATEGIES, get_strategy
from tests.elsevier_fixtures import SCIVAL_URL, ids67, scival_handler


def client():
    return ElsevierClient("k", backoff_base=0, rate_per_sec=1000)


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
    respx_mock.get(url__regex=SCIVAL_URL).mock(side_effect=scival_handler)
    m, source = await fetch_metrics(client(), ids67(), year_range="5yrs", batch_size=100, use_cache=False)
    assert len(m) == 67 and source["lastUpdated"] == "2026-10-25"
    assert sum(x.q1 for x in m.values()) == 40
    assert sum(x.top1 for x in m.values()) == 0 and sum(x.top10 for x in m.values()) == 0
    assert sum(x.intl_collab for x in m.values()) == 20
    assert all(isinstance(x.fwci, Decimal) for x in m.values())
    assert all(x.citations is None and 2021 <= x.year <= 2025 for x in m.values())


async def test_metrics_batches(respx_mock):
    route = respx_mock.get(url__regex=SCIVAL_URL).mock(side_effect=scival_handler)
    await fetch_metrics(client(), ids67(), year_range="5yrs", batch_size=25, use_cache=False)
    sizes = [len(c.request.url.params["publicationIds"].split(",")) for c in route.calls]
    assert sizes == [25, 25, 17]
    p = route.calls[0].request.url.params
    assert p["includedDocs"] == "AllPublicationTypes" and p["byYear"] == "true"
    assert p["journalImpactType"] == "CiteScore" and p["yearRange"] == "5yrs"


async def test_missing_ids_absent(respx_mock):
    missing = ids67()[0]
    respx_mock.get(url__regex=SCIVAL_URL).mock(side_effect=partial(scival_handler, drop={missing}))
    m, _ = await fetch_metrics(client(), ids67(), year_range="5yrs", batch_size=25, use_cache=False)
    assert len(m) == 66 and missing not in m


async def test_empty_ids_no_request(respx_mock):
    route = respx_mock.get(url__regex=SCIVAL_URL)
    assert (await fetch_metrics(client(), [], year_range="5yrs", batch_size=25, use_cache=False))[0] == {}
    assert route.call_count == 0


async def test_scival_param_strategy_sum(respx_mock):
    route = respx_mock.get(url__regex=SCIVAL_URL).mock(side_effect=scival_handler)
    s = get_strategy("scival_param")
    cites = await s.citations(client(), ids67(), year_range="5yrsAndCurrentAndFuture", batch_size=25, use_cache=False)
    assert sum(cites.values()) == 290 and len(cites) == 67
    p = route.calls[0].request.url.params
    assert p["includeSelfCitations"] == "false" and p["includedDocs"] == "AllPublicationTypes"
    assert p["yearRange"] == "5yrsAndCurrentAndFuture"
    assert s.verified is False


async def test_no_exclusion_strategy_includes_self(respx_mock):
    route = respx_mock.get(url__regex=SCIVAL_URL).mock(side_effect=scival_handler)
    await get_strategy("none").citations(client(), ids67()[:3], year_range="5yrsAndCurrentAndFuture",
                                         batch_size=25, use_cache=False)
    assert route.calls[0].request.url.params["includeSelfCitations"] == "true"


async def test_citation_overview_strategy_unavailable():
    with pytest.raises(ElsevierError, match="尚未取得授權"):
        await get_strategy("scopus_citation_overview").citations(
            client(), ["1"], year_range="5yrsAndCurrentAndFuture", batch_size=25, use_cache=False)


def test_strategy_ids():
    assert set(STRATEGIES) == {"scival_param", "scopus_citation_overview", "none"}
    with pytest.raises(KeyError):
        get_strategy("unknown")
