from datetime import datetime

from app.db.models import Run, RunUnit, Unit
from app.runs.anomaly import detect
from app.runs.service import create_run
from tests.run_helpers import TODAY, default_config, seed


def finished_run(session, counts: dict[str, int]):
    """建立一個已完成的 run，依學校設定篇數（不呼叫 API）。"""
    run = create_run(session, default_config(comparisons=frozenset({"benchmark"})), today=TODAY)
    run.status = "done"
    for ru in session.query(RunUnit).filter_by(run_id=run.id):
        ru.status, ru.eid_count, ru.fetched_at = "done", counts[session.get(Unit, ru.unit_id).school], datetime.now()
    session.commit()
    return run


def test_count_change_threshold(session):
    seed(session)
    finished_run(session, {"中央": 100, "台大": 50, "政治": 40})
    second = finished_run(session, {"中央": 65, "台大": 50, "政治": 40})
    found = detect(session, second.id, 0.30)
    assert [a.kind for a in found] == ["count_change"]
    assert "100" in found[0].detail and "65" in found[0].detail
    third = finished_run(session, {"中央": 75, "台大": 50, "政治": 40})
    assert [a.kind for a in detect(session, third.id, 0.30)] == []  # 和上一版 65 比，+15%


def test_first_run_has_no_count_change(session):
    seed(session)
    run = finished_run(session, {"中央": 100, "台大": 50, "政治": 40})
    assert detect(session, run.id, 0.30) == []


def test_interrupted_previous_run_ignored(session):
    seed(session)
    first = finished_run(session, {"中央": 100, "台大": 50, "政治": 40})
    session.get(Run, first.id).status = "interrupted"
    session.commit()
    second = finished_run(session, {"中央": 10, "台大": 50, "政治": 40})
    assert detect(session, second.id, 0.30) == []
