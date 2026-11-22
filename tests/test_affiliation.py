from datetime import date

import httpx
import pytest

from app.db.models import SchoolAffiliation
from app.elsevier import affiliation
from app.elsevier.affiliation import Candidate, name_key, pick
from app.elsevier.client import ElsevierClient
from app.mapping.service import import_mapping
from app.runs.scope import RunConfig
from app.runs.service import create_run, preview_run, schools_needing_af_id
from app.teachers.moe import import_moe
from app.teachers.service import auto_link
from tests.mapping_builder import build_mapping, ncu_rows
from tests.moe_builder import sample_moe

AFFIL_URL = r".*/content/search/affiliation.*"
NTU, NTUST = "National Taiwan University", "National Taiwan University of Science and Technology"


def entry(af_id, name, docs):
    return {"dc:identifier": f"AFFILIATION_ID:{af_id}", "affiliation-name": name, "city": "Taipei",
            "document-count": str(docs)}


# 2026-11-22 實際查詢「National Taiwan University」的前幾筆：模糊比對，連台科大也在其中
NTU_RESULTS = [entry(60005429, NTU, 157641), entry(60073385, "National Taiwan University Hospital", 43767),
               entry(60027709, NTUST, 36570), entry(60025370, "National Taiwan University College of Medicine", 31849)]


def test_name_key_ignores_case_punctuation_and_the():
    assert name_key("National Sun Yat-sen University") == name_key("national sun yat sen university")
    assert name_key("The University of Tokyo") == name_key("University of Tokyo")


def test_pick_takes_exact_name_with_most_documents():
    cands = [Candidate(af_id, name, "", docs) for af_id, name, docs in
             [("1", NTU, 10), ("2", "National Taiwan University Hospital", 99), ("3", NTU, 50), ("4", NTUST, 80)]]
    assert pick(NTU, cands).af_id == "3"
    assert pick(NTUST, cands).af_id == "4"
    assert pick("National Taiwan Univ.", cands) is None   # 沒有完全相同的名稱就不猜


def answer(request: httpx.Request) -> httpx.Response:
    query = request.url.params["query"]
    entries = [entry(60027709, NTUST, 36570)] if "Science" in query else NTU_RESULTS
    return httpx.Response(200, json={"search-results": {"entry": entries}})


async def test_resolve_stores_codes_and_skips_known_ones(session, respx_mock):
    route = respx_mock.get(url__regex=AFFIL_URL).mock(side_effect=answer)
    client = ElsevierClient("k", backoff_base=0, rate_per_sec=1000)
    assert await affiliation.resolve(session, client, [NTU, NTUST]) == []
    assert affiliation.af_ids(session, [NTU, NTUST]) == {NTU: "60005429", NTUST: "60027709"}
    assert 'AFFIL("National Taiwan University") AND AFFILCOUNTRY(Taiwan)' in [
        c.request.url.params["query"] for c in route.calls]
    await affiliation.resolve(session, client, [NTU, NTUST])
    assert route.call_count == 2   # 已有代碼的不重查


async def test_resolve_reports_unmatched_and_keeps_candidates(session, respx_mock):
    respx_mock.get(url__regex=AFFIL_URL).mock(return_value=httpx.Response(200, json={"search-results": {
        "entry": [entry(60005429, NTU, 157641)]}}))
    client = ElsevierClient("k", backoff_base=0, rate_per_sec=1000)
    assert await affiliation.resolve(session, client, ["National Taiwan Univ"]) == ["National Taiwan Univ"]
    row = affiliation.get(session, "National Taiwan Univ")
    assert row.af_id is None and [c.af_id for c in affiliation.candidates_of(row)] == ["60005429"]
    affiliation.set_manual(session, "National Taiwan Univ", "60005429")
    row = affiliation.get(session, "National Taiwan Univ")
    assert (row.af_id, row.af_name, row.matched_by) == ("60005429", NTU, "manual")
    with pytest.raises(ValueError):
        affiliation.set_manual(session, "National Taiwan Univ", "abc")


def overlap_mapping() -> bytes:
    """在範例 mapping 加上台科大資管：台大與台科大校名互相包含。"""
    return build_mapping({
        "中央": ncu_rows(("資訊管理學系",)),
        "台大": [("管理學院", "資訊管理學系", "管理學院", "資訊管理學系", f"Department of Information Management, {NTU}", None)],
        "台科": [("管理學院", "資訊管理學系", "管理學院", "資訊管理學系", f"Department of Information Management, {NTUST}", None)],
    })


def cfg():
    return RunConfig(year_from=2021, year_to=2025, ncu_depts=("資訊管理學系",),
                     comparisons=frozenset({"benchmark"}), schools=("台大", "台科"),
                     custom_unit_ids=(), academic_year=114, teacher_column="total")


def test_run_blocked_until_overlapping_schools_have_codes(session):
    v, _ = import_mapping(session, "m.xlsx", overlap_mapping())
    imp = import_moe(session, "moe.xlsx", sample_moe())
    auto_link(session, v.id, imp.id)
    assert schools_needing_af_id(session) == [NTU, NTUST]
    rows, errors = preview_run(session, cfg(), today=date(2026, 10, 30))
    assert any("Scopus 機構代碼" in e for e in errors)
    with pytest.raises(ValueError):
        create_run(session, cfg(), today=date(2026, 10, 30))
    affiliation.set_manual(session, NTU, "60005429")
    affiliation.set_manual(session, NTUST, "60027709")
    rows, errors = preview_run(session, cfg(), today=date(2026, 10, 30))
    assert errors == []
    ntu = next(r for r in rows if r.school == "台大")
    assert ntu.query.endswith(" AND NOT (AF-ID(60027709) AND NOT AF-ID(60005429))")
    assert session.get(SchoolAffiliation, name_key(NTU)).matched_by == "manual"
