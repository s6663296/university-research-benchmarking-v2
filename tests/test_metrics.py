from decimal import Decimal as D

from app.elsevier.scival import PubMetrics, parse_result
from app.metrics.compute import compute_unit, rank, round2
from app.metrics.indicators import BY_ID, INDICATORS, importance_note
from tests.elsevier_fixtures import load


def pubs(n, q1=0, top1=0, top10=0, intl=0, fwci="1.00", cites=0):
    return [PubMetrics(scopus_id=str(i), year=2023, q1=i < q1, top1=i < top1, top10=i < top10,
                       fwci=D(fwci), intl_collab=i < intl, citations=cites) for i in range(n)]


def qf67():
    cites = {str(r["publication"]["id"]): sum(v or 0 for m in r["metrics"] for v in m["valueByYear"].values())
             for r in load("scival_cites_qf_67.json")["results"]}
    out = []
    for r in load("scival_qf_67.json")["results"]:
        m = parse_result(r)
        m.citations = cites[m.scopus_id]
        out.append(m)
    return out


def test_half_up_not_bankers():
    assert round2(D("2.575")) == D("2.58")   # 範例：資管系-台大 206/5/16
    assert round2(D("0.125")) == D("0.13")


def test_per_faculty_matches_example():
    v = compute_unit(pubs(n=206, q1=133), teachers=16, years=5)
    assert v.value["q1"] == D("1.66") and v.value["total"] == D("2.58")
    assert v.five_year_avg["q1"] == D("26.60") and v.raw["q1"] == 133 and v.raw["total"] == 206


def test_recorded_qf_unit():
    v = compute_unit(qf67(), teachers=None, years=5)
    assert v.value["fwci"] == D("0.36") and v.value["avg_citations"] == D("4.33")
    assert v.value["q1"] is None and v.raw["q1"] == 40 and v.raw["intl_collab"] == 20
    assert v.raw["avg_citations"] == 290


def test_zero_teachers_no_division():
    v = compute_unit(pubs(10, q1=5), teachers=0, years=5)
    assert all(v.value[i.id] is None for i in INDICATORS if i.per_faculty)
    assert v.value["fwci"] == D("1.00")


def test_no_pubs_all_none():
    v = compute_unit([], teachers=10, years=5)
    assert all(val is None for val in v.value.values())
    assert v.raw["total"] == 0


def test_missing_metric_not_counted():
    p = pubs(4, q1=2, fwci="2.00")
    p[3].q1 = None
    p[3].fwci = None
    p[2].citations = None
    v = compute_unit(p, teachers=1, years=1)
    assert v.raw["q1"] == 2 and v.value["fwci"] == D("2.00")
    assert v.value["avg_citations"] == D("0.00")  # 有值的被引數加總 ÷ 總篇數


def test_custom_period_years():
    assert compute_unit(pubs(30, q1=30), teachers=10, years=3).value["q1"] == D("1.00")


def test_rank_competition():
    assert rank({1: D("1.20"), 2: D("1.66"), 3: D("1.66"), 4: None, 5: D("0.30")}) == \
        {2: 1, 3: 1, 1: 3, 5: 4, 4: None}


def test_indicator_table_matches_spec():
    assert [i.id for i in INDICATORS] == ["total", "q1", "top1", "top10", "fwci", "avg_citations", "intl_collab"]
    assert BY_ID["fwci"].aggregate == "mean" and not BY_ID["fwci"].per_faculty
    assert BY_ID["avg_citations"].aggregate == "ratio"


def test_importance_note():
    assert importance_note(BY_ID["intl_collab"]) == "※QS世界大學排名指標 ※THE世界大學排名指標 ※教育部高教深耕指標"
    assert importance_note(BY_ID["q1"]) == "※教育部高教深耕指標"
