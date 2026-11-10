import json
from decimal import Decimal

from sqlalchemy import func, select

from app.db.models import Anomaly, Result, Run, RunPublication, RunUnit, Unit
from app.runs.anomaly import resolve_anomaly
from app.runs.executor import execute_run, recompute
from app.runs.manager import mark_interrupted
from tests.run_helpers import PAPERS, FakeElsevier, fake_client, make_run


def unit_of(session, school, dept):
    return session.scalars(select(Unit).where(Unit.school == school, Unit.ncu_dept == dept)).one()


def run_unit(session, run_id, school, dept):
    u = unit_of(session, school, dept)
    return session.scalars(select(RunUnit).where(RunUnit.run_id == run_id, RunUnit.unit_id == u.id)).one()


def result(session, run_id, school, dept, comparison, indicator):
    u = unit_of(session, school, dept)
    return session.scalars(select(Result).where(Result.run_id == run_id, Result.unit_id == u.id,
                                                Result.comparison == comparison,
                                                Result.indicator_id == indicator)).one()


def anomalies(session, run_id, kind=None):
    q = select(Anomaly).where(Anomaly.run_id == run_id)
    if kind:
        q = q.where(Anomaly.kind == kind)
    return session.scalars(q).all()


async def test_full_run_writes_results(session, session_factory, respx_mock):
    run = make_run(session)
    FakeElsevier().install(respx_mock)
    await execute_run(run.id, session_factory, fake_client())
    session.expire_all()
    assert session.get(Run, run.id).status == "done"
    r = result(session, run.id, "中央", "資訊管理學系", "benchmark", "total")
    assert r.raw == 20 and r.value == Decimal("0.17")  # 20/5/24
    assert r.rank is not None
    # 每組每指標都有結果：benchmark 3 單位 + college 3 單位，7 個指標
    assert session.scalar(select(func.count()).select_from(Result).where(Result.run_id == run.id)) == 6 * 7
    notes = json.loads(session.get(Run, run.id).notes_json)
    assert notes["data_date"] and "2026-10-25" in notes["source"] and "Abstract Retrieval" in notes["source"]
    assert notes["self_citation"].startswith("排除單位論文之間的互相引用")
    assert notes["importance"]["intl_collab"].startswith("※QS")
    pub = session.scalars(select(RunPublication).where(RunPublication.run_id == run.id)).first()
    assert "apiKey" not in pub.metrics_json


async def test_each_paper_requested_from_scival_once_with_citations(session, session_factory, respx_mock):
    run = make_run(session)
    # 中央財金與中央資管有 20 篇共同論文（不同查詢），SciVal 只問一次
    FakeElsevier(papers={**PAPERS, "Finance, National Central": slice(0, 30)}).install(respx_mock)
    await execute_run(run.id, session_factory, fake_client())
    calls = [c.request.url.params for c in respx_mock.calls if "/scival/" in str(c.request.url)]
    requested = [i for p in calls for i in p["publicationIds"].split(",")]
    assert sorted(requested) == sorted(set(requested))
    assert all("CitationCount" in p["metricTypes"] and p["yearRange"] == "5yrsAndCurrentAndFuture" for p in calls)
    pubs = session.scalars(select(RunPublication).where(RunPublication.run_id == run.id)).all()
    assert len(pubs) == 20 + 20 + 10 + 30 + 7
    assert all(json.loads(p.metrics_json)["citations"] is not None for p in pubs)


async def test_ranks_within_group(session, session_factory, respx_mock):
    run = make_run(session)
    FakeElsevier().install(respx_mock)
    await execute_run(run.id, session_factory, fake_client())
    # 對標組總篇數師均：中央 20/5/24=0.17、台大 20/5/16=0.25、政治 10/5/17=0.12
    ranks = {s: result(session, run.id, s, "資訊管理學系", "benchmark", "total").rank for s in ("中央", "台大", "政治")}
    assert ranks == {"台大": 1, "中央": 2, "政治": 3}


async def test_failed_unit_does_not_stop_others(session, session_factory, respx_mock):
    run = make_run(session)
    FakeElsevier(fail={"National Taiwan": 401}).install(respx_mock)
    await execute_run(run.id, session_factory, fake_client())
    session.expire_all()
    ntu = run_unit(session, run.id, "台大", "資訊管理學系")
    assert ntu.status == "failed" and "API Key" in ntu.error
    assert run_unit(session, run.id, "中央", "資訊管理學系").status == "done"
    assert session.get(Run, run.id).status == "done"
    assert [a.unit_id for a in anomalies(session, run.id, "fetch_failed")] == [ntu.unit_id]
    assert result(session, run.id, "台大", "資訊管理學系", "benchmark", "total").value is None


async def test_zero_results_anomaly_and_none_values(session, session_factory, respx_mock):
    run = make_run(session, ncu_depts=("財務金融學系",), schools=("台大",))
    FakeElsevier().install(respx_mock)
    await execute_run(run.id, session_factory, fake_client())
    r = result(session, run.id, "台大", "財務金融學系", "benchmark", "q1")
    assert r.value is None and r.rank is None
    assert len(anomalies(session, run.id, "zero_results")) == 1


async def test_shared_query_fetched_once(session, session_factory, respx_mock):
    fake = FakeElsevier()
    run = make_run(session)
    # 讓台大資管與中央資管使用同一查詢
    ncu = run_unit(session, run.id, "中央", "資訊管理學系")
    ntu = run_unit(session, run.id, "台大", "資訊管理學系")
    ntu.query = ncu.query
    session.commit()
    fake.install(respx_mock)
    await execute_run(run.id, session_factory, fake_client())
    assert fake.scopus_calls.count(ncu.query) == 1
    session.expire_all()
    assert session.get(RunUnit, ntu.id).eid_count == session.get(RunUnit, ncu.id).eid_count == 20


async def test_resume_after_interrupt(session, session_factory, respx_mock):
    fake = FakeElsevier(fail={"National Taiwan": 503})
    run = make_run(session)
    fake.install(respx_mock)
    await execute_run(run.id, session_factory, fake_client())
    session.get(Run, run.id).status = "running"
    session.commit()
    assert mark_interrupted(session) == 1
    session.expire_all()
    assert session.get(Run, run.id).status == "interrupted"
    before = session.scalar(select(func.count()).select_from(RunPublication))
    fake.fail.clear()
    fake.scopus_calls.clear()
    await execute_run(run.id, session_factory, fake_client(), only_unfinished=True)
    session.expire_all()
    assert len(fake.scopus_calls) == 1 and "National Taiwan" in fake.scopus_calls[0]
    assert session.scalar(select(func.count()).select_from(RunPublication)) == before + 20
    assert session.get(Run, run.id).status == "done"


async def test_resume_keeps_scopus_progress(session, session_factory, respx_mock, monkeypatch):
    """暫停時已過 Scopus 的單位，接續時直接進 SciVal，不會退回 Scopus（儀表板才不會倒退）。"""
    from app.runs import executor
    FakeElsevier().install(respx_mock)
    run = make_run(session)
    await execute_run(run.id, session_factory, fake_client())
    ntu = run_unit(session, run.id, "台大", "資訊管理學系")
    ntu.status = "pending"  # 暫停時在 SciVal 階段，退回 pending、保留 eid_count
    session.get(Run, run.id).status = "interrupted"
    session.commit()
    seen = []
    original = executor.search_docs

    async def spy(*args, **kwargs):
        with session_factory() as s:
            ru = s.get(RunUnit, ntu.id)
            seen.append((ru.status, ru.eid_count))
        return await original(*args, **kwargs)

    monkeypatch.setattr(executor, "search_docs", spy)
    await execute_run(run.id, session_factory, fake_client(), only_unfinished=True)
    assert seen == [("scival", 20)]
    session.expire_all()
    assert session.get(RunUnit, ntu.id).status == "done"


async def test_refetch_unit_recomputes(session, session_factory, respx_mock):
    fake = FakeElsevier(fail={"National Taiwan": 401})
    run = make_run(session)
    fake.install(respx_mock)
    await execute_run(run.id, session_factory, fake_client())
    fake.fail.clear()
    ntu = run_unit(session, run.id, "台大", "資訊管理學系")
    await execute_run(run.id, session_factory, fake_client(), unit_ids={ntu.unit_id}, use_cache=False)
    session.expire_all()
    assert session.get(RunUnit, ntu.id).status == "done"
    assert anomalies(session, run.id, "fetch_failed") == []
    assert result(session, run.id, "台大", "資訊管理學系", "benchmark", "total").value == Decimal("0.25")


async def test_confirmed_anomaly_kept_on_recompute(session, session_factory, respx_mock):
    run = make_run(session, ncu_depts=("財務金融學系",), schools=("台大",))
    FakeElsevier().install(respx_mock)
    await execute_run(run.id, session_factory, fake_client())
    a = anomalies(session, run.id, "zero_results")[0]
    resolve_anomaly(session, a.id, "台大財金確實查無資料")
    recompute(session, run.id)
    session.expire_all()
    kept = anomalies(session, run.id, "zero_results")
    assert len(kept) == 1 and kept[0].status == "confirmed" and kept[0].resolution_note == "台大財金確實查無資料"


async def test_missing_teacher_anomaly(session, session_factory, respx_mock):
    run = make_run(session)
    ru = run_unit(session, run.id, "政治", "資訊管理學系")
    ru.teacher_count = None
    session.commit()
    FakeElsevier().install(respx_mock)
    await execute_run(run.id, session_factory, fake_client())
    assert [a.unit_id for a in anomalies(session, run.id, "missing_teacher")] == [ru.unit_id]
    r = result(session, run.id, "政治", "資訊管理學系", "benchmark", "total")
    assert r.value is None and r.rank is None and r.raw == 10


async def test_unexpected_error_marks_run_failed(session, session_factory, respx_mock, monkeypatch):
    run = make_run(session)
    FakeElsevier().install(respx_mock)
    import app.runs.executor as executor

    def boom(*a, **k):
        raise RuntimeError("壞掉了")
    monkeypatch.setattr(executor, "recompute", boom)
    await execute_run(run.id, session_factory, fake_client())
    session.expire_all()
    assert session.get(Run, run.id).status == "failed" and "壞掉了" in session.get(Run, run.id).error


def test_app_startup_marks_interrupted(settings):
    from fastapi.testclient import TestClient

    from app.db.session import init_db, make_engine, make_session_factory
    from app.main import create_app

    engine = make_engine(settings.data_dir / "app.db")
    init_db(engine)
    with make_session_factory(engine)() as s:
        run = make_run(s)
        run.status = "running"
        s.commit()
        run_id = run.id
    engine.dispose()
    with TestClient(create_app(settings)) as c:
        assert c.app.state.run_manager is not None
        with c.app.state.session_factory() as s:
            assert s.get(Run, run_id).status == "interrupted"


async def test_unit_status_goes_through_scopus_then_scival(session, session_factory, respx_mock, monkeypatch):
    run = make_run(session)
    FakeElsevier().install(respx_mock)
    import app.runs.executor as executor
    at_search, at_fetch = [], []

    def units(**where):
        with session_factory() as s:
            q = select(RunUnit).where(RunUnit.run_id == run.id, *(getattr(RunUnit, k) == v for k, v in where.items()))
            return [(ru.status, ru.eid_count) for ru in s.scalars(q)]

    real_search, real_fetch = executor.search_docs, executor.MetricsFetcher.fetch

    async def search(client, query, **k):
        at_search.extend(units(query=query))
        return await real_search(client, query, **k)

    async def fetch(self, ids):
        at_fetch.append(len(units(status="scival", eid_count=len(ids))))
        return await real_fetch(self, ids)
    monkeypatch.setattr(executor, "search_docs", search)
    monkeypatch.setattr(executor.MetricsFetcher, "fetch", fetch)
    await execute_run(run.id, session_factory, fake_client())
    assert at_search and set(at_search) == {("scopus", None)}
    assert at_fetch and all(n >= 1 for n in at_fetch)


async def test_scival_failure_keeps_scopus_count(session, session_factory, respx_mock, monkeypatch):
    from app.elsevier.client import ElsevierError
    run = make_run(session)
    FakeElsevier().install(respx_mock)
    import app.runs.executor as executor

    async def fail(self, ids):
        raise ElsevierError(500, False, "SciVal 壞了")
    monkeypatch.setattr(executor.MetricsFetcher, "fetch", fail)
    await execute_run(run.id, session_factory, fake_client())
    session.expire_all()
    ru = run_unit(session, run.id, "中央", "資訊管理學系")
    assert (ru.status, ru.eid_count) == ("failed", 20)


def test_mark_interrupted_resets_both_fetch_steps(session):
    run = make_run(session)
    rus = session.scalars(select(RunUnit).where(RunUnit.run_id == run.id).order_by(RunUnit.id)).all()
    rus[0].status, rus[1].status = "scopus", "scival"
    session.get(Run, run.id).status = "running"
    session.commit()
    mark_interrupted(session)
    session.expire_all()
    assert session.get(RunUnit, rus[0].id).status == session.get(RunUnit, rus[1].id).status == "pending"
