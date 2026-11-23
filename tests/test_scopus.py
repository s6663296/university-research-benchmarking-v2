import httpx

from app.elsevier.client import ElsevierClient
from app.elsevier.scopus import MISSING_AF_ID, build_query, overlapping_schools, school_names, search_docs
from tests.elsevier_fixtures import SCOPUS_URL, ids67, load, scopus_pages_handler


def client():
    return ElsevierClient("k", backoff_base=0, rate_per_sec=1000)


NTU, NTUST = "National Taiwan University", "National Taiwan University of Science and Technology"
SCHOOLS = [NTU, NTUST, "National Central University"]
TAIL = " AND AFFILCOUNTRY(Taiwan) AND PUBYEAR > 2020 AND PUBYEAR < 2026"


def test_build_query_requires_dept_near_school():
    # 系所與校名須在同一機構字串附近（W/15），不論順序、中間可夾「College of Management」
    assert build_query("Department of Finance, National Central University", 2021, 2025, SCHOOLS) ==         'AFFIL("Department of Finance" W/15 "National Central University")' + TAIL


def test_build_query_also_matches_dept_written_with_ampersand():
    # 「Industrial & Information Management」在 Scopus 中等同「Industrial Information Management」
    assert build_query("Department of Industrial and Information Management, National Central University",
                       2021, 2025, SCHOOLS).startswith(
        'AFFIL(("Department of Industrial and Information Management" OR '
        '"Department of Industrial Information Management") W/15 "National Central University") AND')


def test_build_query_excludes_papers_only_under_the_other_schools_af_id():
    # 「National Taiwan University」也出現在台科大校名中：排除在台科大代碼下、卻不在台大代碼下的論文，
    # 兩校合著的論文（兩個代碼都有）保留
    ids = {"National Taiwan University": "60005429", "national taiwan university of science and technology": "60027709"}
    assert build_query("Department of Information Management, National Taiwan University", 2021, 2025,
                       SCHOOLS, ids) ==         'AFFIL("Department of Information Management" W/15 "National Taiwan University")' + TAIL +         ' AND NOT (AF-ID(60027709) AND NOT AF-ID(60005429))'
    # 台科大的校名不被其他校名包含，不需要排除
    assert build_query("Department of Information Management, National Taiwan University of Science and Technology",
                       2021, 2025, SCHOOLS, ids).endswith(TAIL)


def test_build_query_marks_missing_af_id():
    q = build_query("Department of Information Management, National Taiwan University", 2021, 2025, SCHOOLS)
    assert q.endswith(f" AND NOT (AF-ID({MISSING_AF_ID}) AND NOT AF-ID({MISSING_AF_ID}))")


def test_overlapping_schools():
    assert overlapping_schools(SCHOOLS) == ["National Taiwan University",
                                            "National Taiwan University of Science and Technology"]
    assert overlapping_schools(["National Central University", "National Chengchi University"]) == []


def test_build_query_finds_school_without_comma_and_ignores_case():
    q = build_query("Graduate Institute of Business Administration NATIONAL CENTRAL UNIVERSITY", 2021, 2025, SCHOOLS)
    assert q.startswith('AFFIL("Graduate Institute of Business Administration" W/15 "National Central University")')


def test_build_query_falls_back_to_phrase_when_school_unknown():
    assert build_query("Department of Economics, Some University", 2021, 2025, []) ==         'AFFIL("Department of Economics" W/15 "Some University")' + TAIL
    assert build_query("Department of Economics", 2021, 2025, []) == 'AFFIL("Department of Economics")' + TAIL


def test_build_query_removes_quotes_and_parentheses():
    assert build_query('Dept of "X" (Taipei)', 2021, 2025, []).startswith('AFFIL("Dept of X Taipei") AND')


def test_school_names_dedupes_case_insensitively():
    assert school_names(["Dept A, National Sun Yat-sen University", "Dept B, National Sun Yat-Sen University",
                         "No comma here", "National Taiwan Normal University, Department of Electrical Engineering",
                         "Dept C, National Central University"]) ==         ["National Sun Yat-sen University", "National Taiwan Normal University", "National Central University"]


def test_build_query_handles_school_first_and_chinese_notes():
    assert build_query("National Central University, Department of Physics", 2021, 2025, SCHOOLS).startswith(
        'AFFIL("Department of Physics" W/15 "National Central University")')
    assert build_query("Department of History National Central University(沒有論文)", 2021, 2025, SCHOOLS).startswith(
        'AFFIL("Department of History" W/15 "National Central University")')


async def test_search_follows_cursor(respx_mock):
    route = respx_mock.get(url__regex=SCOPUS_URL).mock(side_effect=scopus_pages_handler)
    docs = await search_docs(client(), "q", use_cache=False)
    assert len(docs) == 67 and route.call_count == 3
    assert route.calls[0].request.url.params["cursor"] == "*"
    params = route.calls[0].request.url.params
    assert params["count"] == "200"  # 只取 id 時一頁可到 200 筆（完整欄位只能 25 筆）
    assert params["field"] == "dc:identifier,eid"
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


def test_build_query_ors_multiple_names_into_one_query():
    # 分號分隔的多個名稱合成一個查詢：同一篇論文只會出現一次
    q = build_query("Department of Finance, National Central University; "
                    "Department of Finance and Banking, National Central University", 2021, 2025, SCHOOLS)
    assert q == ('(AFFIL("Department of Finance" W/15 "National Central University") OR '
                 'AFFIL(("Department of Finance and Banking" OR "Department of Finance Banking") '
                 'W/15 "National Central University"))' + TAIL)


def test_build_query_multiple_names_same_school_keeps_af_id_exclusion_at_end():
    ids = {NTU: "60005429", NTUST: "60027709"}
    q = build_query("Department of Information Management, National Taiwan University; "
                    "Graduate Institute of Information Management, National Taiwan University", 2021, 2025, SCHOOLS, ids)
    assert q.startswith('(AFFIL("Department of Information Management" W/15 "National Taiwan University") OR '
                        'AFFIL("Graduate Institute of Information Management" W/15 "National Taiwan University"))')
    assert q.endswith(TAIL + " AND NOT (AF-ID(60027709) AND NOT AF-ID(60005429))")
    assert q.count("AND NOT (AF-ID") == 1


def test_build_query_multiple_names_different_schools_scopes_exclusion_per_name():
    # 名稱分屬不同學校時，只有台大的名稱要排除台科大的論文
    ids = {NTU: "60005429", NTUST: "60027709"}
    q = build_query("Department of Physics, National Taiwan University; "
                    "Department of Physics, National Central University", 2021, 2025, SCHOOLS, ids)
    assert q == ('((AFFIL("Department of Physics" W/15 "National Taiwan University") '
                 'AND NOT (AF-ID(60027709) AND NOT AF-ID(60005429))) OR '
                 'AFFIL("Department of Physics" W/15 "National Central University"))' + TAIL)


def test_build_query_multiple_names_still_marks_missing_af_id():
    q = build_query("Dept A, National Taiwan University; Dept B, National Taiwan University", 2021, 2025, SCHOOLS)
    assert MISSING_AF_ID in q


def test_school_names_reads_every_name():
    assert school_names(["Dept A, National Sun Yat-sen University; Dept B, National Chung Hsing University"]) == \
        ["National Sun Yat-sen University", "National Chung Hsing University"]
