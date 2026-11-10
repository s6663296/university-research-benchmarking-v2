import json

import httpx
from sqlalchemy import select

from app.db.models import RunPublication, RunUnit, Unit
from app.db.settings_store import set_setting
from app.elsevier.cache import ApiCache
from app.elsevier.client import ElsevierClient
from app.elsevier.inset_selfcite import InSetSelfCitations, count_in_set, full_view_refs
from app.elsevier.scival import citation_count
from app.runs.executor import execute_run
from tests.elsevier_fixtures import ABSTRACT_URL, abstract_full, ids67, load
from tests.run_helpers import PAPERS, FakeElsevier, fake_client, make_run


def client(cache=None):
    return ElsevierClient("k", backoff_base=0, rate_per_sec=1000, max_retries=0, cache=cache)


def base_citations() -> dict[str, int]:
    return {str(r["publication"]["id"]): citation_count(r) for r in load("scival_cites_qf_67.json")["results"]}


def test_full_view_refs_keeps_only_scopus_ids():
    assert full_view_refs(abstract_full(["1", "2", "1"])) == ["1", "2"]
    assert full_view_refs(abstract_full(["9"])) == ["9"]   # 只有一筆時是物件
    assert full_view_refs({"abstracts-retrieval-response": {}}) == []


def test_count_in_set_ignores_self_reference_and_outside_papers():
    refs = {"A": {"B", "X"}, "B": {"B", "C"}, "C": {"B"}, "D": set()}
    assert count_in_set(refs) == {"B": 2, "C": 1}


async def test_no_request_when_nothing_cited(respx_mock):
    route = respx_mock.get(url__regex=ABSTRACT_URL)
    assert await InSetSelfCitations(client()).count(["1", "2"], {"1": 0, "2": None}) == {}
    assert await InSetSelfCitations(client()).count(["1"], {"1": 5}) == {}
    assert route.call_count == 0


async def test_count_and_permanent_cache(respx_mock, session_factory):
    refs = {"2": ["1", "99"], "3": ["1", "2"]}
    route = respx_mock.get(url__regex=ABSTRACT_URL).mock(side_effect=lambda req: (
        httpx.Response(404) if req.url.path.endswith("/4")
        else httpx.Response(200, json=abstract_full(refs.get(req.url.path.rsplit("/", 1)[-1], [])))))
    cache = ApiCache(session_factory, cache_days=1)
    counter = InSetSelfCitations(client(cache))
    ids = ["1", "2", "3", "4"]
    assert await counter.count(ids, {"1": 5}) == {"1": 2, "2": 1}
    assert route.call_count == 4 and counter.fetched == 4
    # 同一實例：重疊的論文不再請求；新實例：從永久快取讀出
    assert await counter.count(["1", "2"], {"1": 5}) == {"1": 1}
    again = InSetSelfCitations(client(cache))
    assert await again.count(ids, {"1": 5}) == {"1": 2, "2": 1}
    assert route.call_count == 4 and again.fetched == 0


async def test_run_subtracts_citations_within_each_unit(session, session_factory, respx_mock):
    ids = ids67()
    base = base_citations()
    im = ids[0:20]                         # 中央資管
    p = next(i for i in im if base[i] >= 3)
    q1, q2 = [i for i in im if i != p][:2]
    outside = ids[35]                      # 台大資管的論文：不在中央資管、中央財金的組內
    extra = ids[21]                        # 只在中央財金（0:30）的組內
    fake = FakeElsevier(papers={**PAPERS, "Finance, National Central": slice(0, 30)},
                        refs={q1: [p], q2: [p, "999"], outside: [p], extra: [p]})
    fake.install(respx_mock)
    run = make_run(session)
    assert run.self_citation_strategy == "scival_pubset"
    await execute_run(run.id, session_factory, fake_client())

    def pub(dept):
        unit = session.scalars(select(Unit).where(Unit.school == "中央", Unit.ncu_dept == dept)).first()
        ru = session.scalars(select(RunUnit).where(RunUnit.run_id == run.id, RunUnit.unit_id == unit.id)).one()
        row = session.scalars(select(RunPublication).where(
            RunPublication.run_id == run.id, RunPublication.unit_id == ru.unit_id,
            RunPublication.scopus_id == p)).one()
        return json.loads(row.metrics_json)

    session.expire_all()
    assert pub("資訊管理學系")["self_citations"] == 2
    assert pub("資訊管理學系")["citations"] == base[p] - 2
    assert pub("財務金融學系")["self_citations"] == 3
    assert sorted(fake.abstract_calls) == sorted(set(fake.abstract_calls))   # 每篇只查一次


async def test_other_strategies_do_not_fetch_references(session, session_factory, respx_mock):
    set_setting(session, "self_citation_strategy", "none")
    fake = FakeElsevier()
    fake.install(respx_mock)
    run = make_run(session)
    await execute_run(run.id, session_factory, fake_client())
    assert fake.abstract_calls == []
    row = session.scalars(select(RunPublication).where(RunPublication.run_id == run.id)).first()
    assert json.loads(row.metrics_json)["self_citations"] is None
