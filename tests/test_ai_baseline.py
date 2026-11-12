from app.ai.baseline import auto_baseline, baseline_choices, baseline_for, basis_differences
from app.runs.service import create_run
from tests.run_helpers import TODAY, default_config, seed

DEPT = "資訊管理學系"


def mk(session, years, **kw):
    run = create_run(session, default_config(year_from=years[0], year_to=years[1], **kw), today=TODAY)
    run.status = "done"
    session.flush()
    return run


def test_picks_previous_period_latest_rerun(session):
    seed(session)
    p1 = mk(session, (2020, 2024))
    p2 = mk(session, (2020, 2024))
    cur = mk(session, (2021, 2025))
    mk(session, (2019, 2023))
    assert p1.id < p2.id
    assert auto_baseline(session, cur, DEPT).id == p2.id


def test_falls_back_to_nearest_earlier_period(session):
    seed(session)
    old = mk(session, (2018, 2022))
    cur = mk(session, (2021, 2025))
    assert auto_baseline(session, cur, DEPT).id == old.id


def test_same_period_rerun_is_not_baseline(session):
    seed(session)
    mk(session, (2021, 2025))
    cur = mk(session, (2021, 2025))
    assert auto_baseline(session, cur, DEPT) is None


def test_requires_dept(session):
    seed(session)
    mk(session, (2020, 2024), ncu_depts=("財務金融學系",))
    cur = mk(session, (2021, 2025))
    assert auto_baseline(session, cur, DEPT) is None


def test_override_without_dept_gives_none(session):
    seed(session)
    other = mk(session, (2019, 2023), ncu_depts=("財務金融學系",))
    mk(session, (2020, 2024))
    cur = mk(session, (2021, 2025))
    cur.ai_baseline_run_id = other.id
    assert baseline_for(session, cur, DEPT) is None
    cur.ai_baseline_run_id = 99999
    assert baseline_for(session, cur, DEPT) is None


def test_override_valid_and_default(session):
    seed(session)
    p = mk(session, (2020, 2024))
    cur = mk(session, (2021, 2025))
    assert baseline_for(session, cur, DEPT).id == p.id
    cur.ai_baseline_run_id = p.id
    assert baseline_for(session, cur, DEPT).id == p.id


def test_basis_differences(session):
    seed(session)
    base = mk(session, (2020, 2024), schools=("台大",), academic_year=113)
    cur = mk(session, (2021, 2025))
    assert basis_differences(cur, base, DEPT) == ["對標學校不同", "教師數學年度不同"]


def test_basis_differences_years_and_choices(session):
    seed(session)
    base = mk(session, (2019, 2024))
    cur = mk(session, (2021, 2025))
    assert basis_differences(cur, base, DEPT) == ["分析年數不同"]
    assert [r.id for r in baseline_choices(session, cur)] == [base.id]
