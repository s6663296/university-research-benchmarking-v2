from decimal import Decimal

from sqlalchemy import select

from app.db.models import RunUnit, Unit
from app.reports.tables import fmt, indicator_table, summary_table, unit_label

SHORT = {"資訊管理學系": "資管系"}
DISPLAY = {"政治": "政大", "台大": "台大", "中央": "中央"}


def u(school, dept="資訊管理學系"):
    return Unit(school=school, ncu_dept=dept, dept_en="x")


def test_unit_label_uses_display_name():
    assert unit_label(u("政治"), "benchmark", SHORT, DISPLAY) == "資管系-政大"
    assert unit_label(u("中央"), "benchmark", SHORT, DISPLAY) == "資管系-中央"
    assert unit_label(u("中央"), "college", SHORT, DISPLAY) == "資管系"
    assert unit_label(u("中央", "會計研究所"), "college", SHORT, DISPLAY) == "會計研究所"


async def test_indicator_table_sorted(session, done_run):
    t = indicator_table(session, done_run.id, "benchmark", "資訊管理學系", "total")
    # 台大 20/5/16=0.25、中央 20/5/24=0.17、政大 10/5/17=0.12
    assert [r.label for r in t.rows] == ["資管系-台大", "資管系-中央", "資管系-政大"]
    ncu = t.rows[1]
    assert ncu.is_ncu and ncu.raw == 20 and ncu.teachers == 24 and ncu.value == Decimal("0.17") and ncu.rank == 2
    assert ncu.five_year_avg == Decimal("4.00")


async def test_none_sorted_last(session, done_run):
    ru = session.scalars(select(RunUnit).join(Unit, Unit.id == RunUnit.unit_id)
                         .where(RunUnit.run_id == done_run.id, Unit.school == "台大")).one()
    ru.teacher_count = None
    session.commit()
    from app.runs.executor import recompute
    recompute(session, done_run.id)
    t = indicator_table(session, done_run.id, "benchmark", "資訊管理學系", "total")
    assert t.rows[-1].label == "資管系-台大" and t.rows[-1].value is None


async def test_pubs_table_sorted_by_q1(session, done_run):
    t = indicator_table(session, done_run.id, "college", "管理學院", "total", sort_by="q1")
    q1 = indicator_table(session, done_run.id, "college", "管理學院", "q1")
    assert [r.unit_id for r in t.rows] == [r.unit_id for r in q1.rows]


async def test_summary_rows(session, done_run):
    rows = summary_table(session, done_run.id, "管理學院")
    im = [r for r in rows if r.dept_label == "資管系"]
    assert [(r.comparison, r.group_size) for r in im] == [("benchmark", 3), ("college", 3)]
    assert im[0].ranks["fwci"] is not None and list(im[0].ranks) == \
        ["q1", "top1", "top10", "fwci", "avg_citations", "intl_collab"]
    assert {r.comparison for r in rows if r.dept_label == "會計所"} == {"college"}


def test_fmt():
    assert fmt(None) == "待確認" and fmt(Decimal("1.2")) == "1.20" and fmt(Decimal("0.125")) == "0.13"
