from datetime import datetime

from sqlalchemy import delete, inspect, select

from app.db.models import AiSummary, Run
from app.runs.renumber import CHILDREN, renumber_runs
from tests.run_helpers import make_run


def test_migration_creates_table(engine):
    insp = inspect(engine)
    assert "ai_summaries" in insp.get_table_names()
    assert "ai_baseline_run_id" in {c["name"] for c in insp.get_columns("runs")}


def _summary(run_id, baseline=None, key="資管系"):
    now = datetime(2026, 11, 12)
    return AiSummary(run_id=run_id, kind="dept", key=key, baseline_run_id=baseline, summary="摘要",
                     input_hash="h", model="m", created_at=now, updated_at=now)


def test_renumber_moves_baseline_refs(session, tmp_path):
    for _ in range(3):
        make_run(session)
    r3 = session.get(Run, 3)
    r3.ai_baseline_run_id = 2
    session.add(_summary(3, baseline=2))
    session.commit()
    for model in CHILDREN:
        session.execute(delete(model).where(model.run_id == 1))
    session.delete(session.get(Run, 1))
    session.commit()
    renumber_runs(session, tmp_path)
    run = session.get(Run, 2)
    assert run.ai_baseline_run_id == 1
    s = session.scalars(select(AiSummary)).one()
    assert (s.run_id, s.baseline_run_id) == (2, 1)


def test_delete_run_clears_baseline_refs(client, web_done_run):
    factory = client.app.state.session_factory
    with factory() as s:
        from tests.run_helpers import make_run as mk
        a = web_done_run
        b = mk(s).id
        s.get(Run, b).ai_baseline_run_id = a
        s.add(_summary(a))
        s.add(_summary(b, baseline=a, key="財金系"))
        s.commit()
    r = client.post(f"/runs/{a}/delete", follow_redirects=False)
    assert r.status_code == 303
    with factory() as s:
        runs = list(s.scalars(select(Run)))
        assert [x.id for x in runs] == [1]
        assert runs[0].ai_baseline_run_id is None
        sums = list(s.scalars(select(AiSummary)))
        assert [(x.run_id, x.baseline_run_id) for x in sums] == [(1, None)]
