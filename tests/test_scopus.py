import httpx

from app.elsevier.client import ElsevierClient
from app.elsevier.scopus import build_query, school_names, search_docs
from tests.elsevier_fixtures import SCOPUS_URL, ids67, load, scopus_pages_handler


def client():
    return ElsevierClient("k", backoff_base=0, rate_per_sec=1000)


SCHOOLS = ["National Taiwan University", "National Taiwan University of Science and Technology",
           "National Central University"]
TAIL = " AND AFFILCOUNTRY(Taiwan) AND PUBYEAR > 2020 AND PUBYEAR < 2026"


def test_build_query_requires_dept_near_school():
    # 系所與校名須在同一機構字串附近（W/15），不論順序、中間可夾「College of Management」
    assert build_query("Department of Finance, National Central University", 2021, 2025, SCHOOLS) == \
        '(AFFIL("Department of Finance" W/15 "National Central University"))' + TAIL


def test_build_query_also_matches_dept_written_with_ampersand():
    # 「Industrial & Information Management」在 Scopus 中等同「Industrial Information Management」
    assert build_query("Department of Industrial and Information Management, National Central University",
                       2021, 2025, SCHOOLS).startswith(
        '(AFFIL(("Department of Industrial and Information Management" OR '
        '"Department of Industrial Information Management") W/15 "National Central University"))')


def test_build_query_excludes_schools_containing_the_name():
    # 「National Taiwan University」也出現在台科大校名中，需排除台科大的同名系所
    assert build_query("Department of Information Management, National Taiwan University", 2021, 2025, SCHOOLS) == \
        '(AFFIL("Department of Information Management" W/15 "National Taiwan University") AND NOT ' \
        'AFFIL("Department of Information Management" W/15 ' \
        '"National Taiwan University of Science and Technology"))' + TAIL


def test_build_query_finds_school_without_comma_and_ignores_case():
    q = build_query("Graduate Institute of Business Administration NATIONAL CENTRAL UNIVERSITY", 2021, 2025, SCHOOLS)
    assert q.startswith('(AFFIL("Graduate Institute of Business Administration" W/15 "National Central University"))')


def test_build_query_falls_back_to_phrase_when_school_unknown():
    assert build_query("Department of Economics, Some University", 2021, 2025, []) == \
        '(AFFIL("Department of Economics" W/15 "Some University"))' + TAIL
    assert build_query("Department of Economics", 2021, 2025, []) == '(AFFIL("Department of Economics"))' + TAIL


def test_build_query_removes_quotes_and_parentheses():
    assert build_query('Dept of "X" (Taipei)', 2021, 2025, []).startswith('(AFFIL("Dept of X Taipei")) AND')


def test_school_names_dedupes_case_insensitively():
    assert school_names(["Dept A, National Sun Yat-sen University", "Dept B, National Sun Yat-Sen University",
                         "No comma here", "National Taiwan Normal University, Department of Electrical Engineering",
                         "Dept C, National Central University"]) == \
        ["National Sun Yat-sen University", "National Taiwan Normal University", "National Central University"]


def test_build_query_handles_school_first_and_chinese_notes():
    assert build_query("National Central University, Department of Physics", 2021, 2025, SCHOOLS).startswith(
        '(AFFIL("Department of Physics" W/15 "National Central University"))')
    assert build_query("Department of History National Central University(沒有論文)", 2021, 2025, SCHOOLS).startswith(
        '(AFFIL("Department of History" W/15 "National Central University"))')


async def test_search_follows_cursor(respx_mock):
    route = respx_mock.get(url__regex=SCOPUS_URL).mock(side_effect=scopus_pages_handler)
    docs = await search_docs(client(), "q", use_cache=False)
    assert len(docs) == 67 and route.call_count == 3
    assert route.calls[0].request.url.params["cursor"] == "*"
    params = route.calls[0].request.url.params
    assert params["count"] == "200"  # 只取 id 時一頁可到 200 筆（完整欄位只能 25 筆）
    assert params["field"] == "dc:identifier,eid,prism:doi"
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
