import httpx

from app.elsevier.client import ElsevierClient
from app.elsevier.overlap import affiliation_texts, overlap_of, unit_docs
from tests.elsevier_fixtures import ABSTRACT_URL, SCOPUS_URL

NTU, NTUST = "National Taiwan University", "National Taiwan University of Science and Technology"
SCHOOLS = [NTU, NTUST, "National Central University"]
CHEME = "Department of Chemical Engineering, National Taiwan University"


def test_overlap_only_for_schools_contained_in_others():
    ov = overlap_of(CHEME, SCHOOLS)
    assert (ov.dept, ov.school, ov.others) == ("Department of Chemical Engineering", NTU, (NTUST,))
    assert overlap_of("Department of Chemical Engineering, National Taiwan University of Science and Technology",
                      SCHOOLS) is None
    assert overlap_of("Department of Physics, National Central University", SCHOOLS) is None


def test_matches_needs_own_dept_and_school_in_one_affiliation():
    ov = overlap_of(CHEME, SCHOOLS)
    # 2026-11-22 台大化工的實例：台科大化工 + 台大其他系所，不算台大化工
    assert not ov.matches(["Department of Chemical Engineering, National Taiwan University of Science and "
                           "Technology, Taipei 10607, Taiwan",
                           "Institute of Polymer Science and Engineering, National Taiwan University, Taipei"])
    assert ov.matches(["Department of Chemical Engineering, National Taiwan University, Taipei 10617, Taiwan",
                       "Department of Chemical Engineering, National Taiwan University of Science and Technology"])
    amp = overlap_of("Department of Industrial and Information Management, National Taiwan University", SCHOOLS)
    assert amp.matches(["Dept. of Industrial & Information Management, National Taiwan University"]) is False
    assert amp.matches(["Department of Industrial & Information Management, National Taiwan University"])


def test_ambiguous_query_wraps_unit_query():
    assert overlap_of(CHEME, SCHOOLS).ambiguous_query("Q") == \
        '(Q) AND (AFFIL("Department of Chemical Engineering" W/15 "National Taiwan University of Science and Technology"))'


def full(*affiliations, source=True):
    groups = [{"affiliation": {"ce:source-text": a} if source else {"organization": [{"$": p} for p in a.split(", ")]}}
              for a in affiliations]
    return {"abstracts-retrieval-response": {"item": {"bibrecord": {"head": {"author-group": groups}}}}}


def test_affiliation_texts_uses_source_text_or_organization():
    assert affiliation_texts(full("A, B")) == ["A, B"]
    assert affiliation_texts(full("Dept X, Univ Y", source=False)) == ["Dept X, Univ Y"]
    assert affiliation_texts({"abstracts-retrieval-response": {"item": {"bibrecord": {"head": {
        "author-group": {"affiliation": {"ce:source-text": "only one"}}}}}}}) == ["only one"]


async def test_unit_docs_drops_papers_without_own_dept(respx_mock):
    def scopus(request: httpx.Request) -> httpx.Response:
        ids = ["1", "2"] if "Science and Technology" in request.url.params["query"] else ["1", "2", "3"]
        return httpx.Response(200, json={"search-results": {"opensearch:totalResults": str(len(ids)), "entry": [
            {"dc:identifier": f"SCOPUS_ID:{i}", "eid": f"2-s2.0-{i}"} for i in ids]}})

    def abstract(request: httpx.Request) -> httpx.Response:
        sid = request.url.path.rsplit("/", 1)[-1]
        own = f"Department of Chemical Engineering, {NTU}" if sid == "1" else f"Department of Physics, {NTU}"
        return httpx.Response(200, json=full(f"Department of Chemical Engineering, {NTUST}", own))

    respx_mock.get(url__regex=SCOPUS_URL).mock(side_effect=scopus)
    route = respx_mock.get(url__regex=ABSTRACT_URL).mock(side_effect=abstract)
    client = ElsevierClient("k", backoff_base=0, rate_per_sec=1000)
    docs = await unit_docs(client, "Q", overlap_of(CHEME, SCHOOLS), use_cache=False)
    assert [d.scopus_id for d in docs] == ["1", "3"]   # 3 不在分不出來的論文中，不用逐篇檢查
    assert route.call_count == 2


def test_multiple_names_overlap_keeps_paper_matching_any_name():
    from app.elsevier.overlap import MultiOverlap

    ov = overlap_of(CHEME + "; Department of Chemical Engineering, National Central University", SCHOOLS)
    assert isinstance(ov, MultiOverlap)
    # 只有台大的名稱需要檢查台科大
    assert ov.ambiguous_query("Q") == \
        '(Q) AND (AFFIL("Department of Chemical Engineering" W/15 "National Taiwan University of Science and Technology"))'
    ntust_only = ["Department of Chemical Engineering, National Taiwan University of Science and Technology"]
    assert not ov.matches(ntust_only)
    assert ov.matches(ntust_only + ["Department of Chemical Engineering, National Central University, Taoyuan"])
    # 兩個名稱都沒有名稱相近的他校：不需要檢查
    assert overlap_of("Department of Physics, National Central University; Dept of Physics, National Central "
                      "University", SCHOOLS) is None
    # 只有一個名稱時維持原本的 Overlap
    assert not isinstance(overlap_of(CHEME, SCHOOLS), MultiOverlap)
