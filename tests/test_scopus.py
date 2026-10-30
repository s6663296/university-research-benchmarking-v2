import httpx

from app.elsevier.client import ElsevierClient
from app.elsevier.scopus import build_query, search_docs
from tests.elsevier_fixtures import SCOPUS_URL, ids67, load, scopus_pages_handler


def client():
    return ElsevierClient("k", backoff_base=0, rate_per_sec=1000)


def test_build_query_matches_scopus_affiliation_search():
    # 不加引號，和 Scopus 網頁「Search within: Affiliation」相同（清大經濟：加引號 34 篇、不加 40 篇）
    assert build_query("Department of Finance, National Taiwan University", 2021, 2025) == \
        "AFFIL(Department of Finance, National Taiwan University) AND AFFILCOUNTRY(Taiwan) " \
        "AND PUBYEAR > 2020 AND PUBYEAR < 2026"


def test_build_query_removes_quotes_and_parentheses():
    assert build_query('Dept of "X" (Taipei)', 2021, 2025).startswith("AFFIL(Dept of X Taipei) AND")


async def test_search_follows_cursor(respx_mock):
    route = respx_mock.get(url__regex=SCOPUS_URL).mock(side_effect=scopus_pages_handler)
    docs = await search_docs(client(), "q", use_cache=False)
    assert len(docs) == 67 and route.call_count == 3
    assert route.calls[0].request.url.params["cursor"] == "*"
    assert route.calls[0].request.url.params["count"] == "25"
    assert {d.scopus_id for d in docs} == set(ids67())
    assert all(d.eid.startswith("2-s2.0-") and not d.scopus_id.startswith("SCOPUS_ID") for d in docs)


async def test_search_zero_results(respx_mock):
    respx_mock.get(url__regex=SCOPUS_URL).mock(return_value=httpx.Response(200, json=load("scopus_zero.json")))
    assert await search_docs(client(), "q", use_cache=False) == []


async def test_search_dedupes(respx_mock):
    page = load("scopus_page_3.json")
    dup = {"search-results": {**page["search-results"], "opensearch:totalResults": "34",
                              "entry": page["search-results"]["entry"] * 2,
                              "cursor": {"@next": "AoE3"}}}
    respx_mock.get(url__regex=SCOPUS_URL).mock(return_value=httpx.Response(200, json=dup))
    assert len(await search_docs(client(), "q", use_cache=False)) == 17
