"""AI 圖表分析：送給模型的資料、儲存、過時判斷、圖片內容，以及跟 AI 總結／系所簡報互不影響。"""

import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.ai import charts, service
from app.ai.claude import CHART_SCHEMA, ClaudeWriter
from app.ai.jobs import AiJobManager
from app.ai.prompts import DEFAULT_TONE
from app.ai.service import AiError
from app.db.models import AiChartNote
from app.reports.tables import indicator_table
from app.reports.trends import Series, TrendChart
from app.runs.executor import execute_run
from app.web.chart_slides import W, nice_max, slides, text_width, wrap, x_ticks
from tests.run_helpers import FakeElsevier, fake_client, make_run
from tests.test_ai_service import FakeWriter, fake_client as claude_client, response

DEPT = "資訊管理學系"


def chart(own: list, other: list, label="中山") -> TrendChart:
    years = [2021, 2022, 2023, 2024]
    to = lambda vals: {y: None if v is None else Decimal(v) for y, v in zip(years, vals)}
    return TrendChart("total", "benchmark", DEPT, years,
                      [Series(1, "中央", True, to(own)), Series(2, label, False, to(other))])


def test_crossings_report_overtakes_both_ways():
    assert charts.crossings(chart([10, 10, 10, 10], [5, 12, 12, 8])) == [
        "2022 年 中山 超越本系所", "2024 年 本系所超越 中山"]


def test_crossings_skip_missing_years_and_ties_staying_tied():
    assert charts.crossings(chart([10, None, 10, 10], [5, 12, 12, 10])) == []
    assert charts.crossings(chart([10, 10, 10, 10], [10, 12, 12, 12])) == ["2022 年 中山 超越本系所"]


async def test_input_has_trends_with_ncu_marked_and_no_period(session, done_run):
    payload = charts.chart_input(session, done_run, DEPT)
    assert payload["系所"] == DEPT and payload["兩期比較表"] is None
    codes = [e["代號"] for e in payload["逐年趨勢"]]
    assert codes and set(codes) <= {"total", "q1", "top1", "top10", "intl_collab", "fwci"}
    total = next(e for e in payload["逐年趨勢"] if e["代號"] == "total")
    assert total["年份"] == list(range(done_run.period_from, done_run.period_to + 1))
    assert sum(1 for s in total["各校"] if s.get("本系所")) == 1
    assert all(len(s["逐年"]) == len(total["年份"]) for s in total["各校"])
    assert "註" in next(e for e in payload["逐年趨勢"] if e["代號"] == "fwci")


async def test_generate_stores_only_known_indicators_and_no_period(session, done_run):
    writer = FakeWriter()
    row = charts.generate(session, done_run, DEPT, writer, DEFAULT_TONE, "m")
    system, payload = writer.calls[0]
    assert "圖表說明" in system and "與本系所的消長" in system and "只能引用提供的數字" in system
    notes = charts.notes_of(row)
    assert [c["indicator"] for c in notes["charts"]] == [e["代號"] for e in payload["逐年趨勢"]]
    first = notes["charts"][0]
    assert not first["notes"][0].startswith("・") and "**重點**" in first["notes"][0]   # 項目符號去掉、紅字標記保留
    assert notes["period"] is None                       # 沒有前期，模型多寫的也丟掉
    assert charts.is_fresh(session, row) and row.model == "m" and not row.edited


async def test_charts_do_not_touch_ai_summary(session, done_run):
    """圖表分析與 AI 總結分開：產生圖表不會新增或讓總結過時。"""
    summary = service.generate(session, done_run, "dept", DEPT, FakeWriter(), DEFAULT_TONE, "m")
    charts.generate(session, done_run, DEPT, FakeWriter(), DEFAULT_TONE, "m")
    assert service.is_fresh(session, summary)
    assert service.fresh_text(session, done_run, "dept", DEPT) is not None


async def test_error_keeps_previous_and_regenerate_clears_edit(session, done_run):
    row = charts.generate(session, done_run, DEPT, FakeWriter(), DEFAULT_TONE, "m")
    notes = charts.notes_of(row)
    notes["charts"][0]["highlight"] = "人工"
    charts.save_edit(session, row.id, notes)
    with pytest.raises(AiError):
        charts.generate(session, done_run, DEPT, FakeWriter(fail_on={DEPT}), DEFAULT_TONE, "m")
    session.expire_all()
    kept = session.scalars(select(AiChartNote)).one()
    assert kept.edited and charts.notes_of(kept)["charts"][0]["highlight"] == "人工"
    charts.generate(session, done_run, DEPT, FakeWriter(), DEFAULT_TONE, "m")
    session.expire_all()
    assert not session.scalars(select(AiChartNote)).one().edited


async def test_data_change_makes_charts_stale(session, done_run):
    row = charts.generate(session, done_run, DEPT, FakeWriter(), DEFAULT_TONE, "m")
    row.input_hash = "old"
    session.commit()
    assert not charts.is_fresh(session, row)


async def test_baseline_adds_period_table_and_slides(session, session_factory, respx_mock):
    FakeElsevier().install(respx_mock)
    old = make_run(session, year_from=2020, year_to=2024)
    await execute_run(old.id, session_factory, fake_client())
    cur = make_run(session)
    await execute_run(cur.id, session_factory, fake_client())
    session.expire_all()
    data = charts.chart_data(session, cur, DEPT)
    t = data.table
    assert t is not None and t.base_period == "2020-2024" and t.period == f"{cur.period_from}-{cur.period_to}"
    own = next(r for r in t.rows if r.is_own)
    total = next(r for r in indicator_table(session, cur.id, "benchmark", DEPT, "total").rows if r.is_ncu)
    assert own.b["total"] == total.raw                 # 論文數是期間總數，不是師均
    payload = charts.chart_input(session, cur, DEPT, data)
    school = next(s for s in payload["兩期比較表"]["各校"] if s.get("本系所"))
    assert set(school) >= {"論文數", "FWCI", "Top 1%", "被引次數"} and "變化率" in school["論文數"]

    row = charts.generate(session, cur, DEPT, FakeWriter(), DEFAULT_TONE, "m")
    notes = charts.notes_of(row)
    assert notes["period"] == {"notes": ["兩期說明"], "highlights": ["兩期重點"]}
    pages = slides(data, notes)
    assert [p.filename for p in pages[-2:]] == [f"{DEPT}_兩期比較表.png", f"{DEPT}_兩期泡泡圖.png"]
    assert "兩期說明" in pages[-2].svg and "兩期重點" in pages[-1].svg
    assert "▲" in pages[-2].svg or "▼" in pages[-2].svg or "0.00%" in pages[-2].svg


async def test_slides_pair_indicators_and_show_notes(session, done_run):
    row = charts.generate(session, done_run, DEPT, FakeWriter(), DEFAULT_TONE, "m")
    data = charts.chart_data(session, done_run, DEPT)
    pages = slides(data, charts.notes_of(row))
    have = {c.indicator_id for c in data.charts}
    assert len(pages) == sum(1 for pair in charts.PAIRS if have & set(pair))
    svg = str(pages[0].svg)
    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg"') and f'viewBox="0 0 {W} 720"' in svg
    assert "圖表說明" in svg and "total紅框重點" in svg and "fill=\"#e00000\"" in svg   # **重點** 標紅
    assert "※本頁由 AI 依系統數據統整" in svg and pages[0].filename.endswith(".png")


def test_wrap_keeps_lines_within_width_and_numbers_whole():
    text = "本系所論文數自 2021 年 25 篇增至 2025 年 41 篇，**穩定成長**，" * 3
    from app.reports.emphasis import emphasis
    lines = wrap(emphasis(text), 300, 18)
    assert len(lines) > 2
    assert all(text_width("".join(t for t, _ in ln), 18) <= 300 for ln in lines)
    assert any(red for ln in lines for _, red in ln)
    joined = ["".join(t for t, _ in ln) for ln in lines]
    assert not any(j.endswith("202") for j in joined)    # 數字不從中間斷開


def test_axis_helpers():
    assert nice_max(57) == 60 and nice_max(0) == 1.0 and nice_max(2.1) == 2.4
    assert x_ticks((0.5, 2.2), 0.1) == [0.5, 0.9, 1.3, 1.7, 2.1]


def test_claude_writer_charts_uses_chart_schema():
    seen = {}

    def create(**kw):
        seen.update(kw)
        return response(json.dumps({"charts": [], "period": None}))

    out = ClaudeWriter("key", "m", client=claude_client(create)).write_charts("s", {"系所": DEPT})
    assert out == {"charts": [], "period": None}
    assert seen["output_config"]["format"]["schema"] is CHART_SCHEMA
    with pytest.raises(AiError):
        ClaudeWriter("key", "m", client=claude_client(lambda **kw: response("[]"))).write_charts("s", {})


async def test_job_generates_charts_for_every_dept(session, session_factory, done_run):
    manager = AiJobManager(session_factory, FakeWriter)
    manager.start(done_run.id, all_charts=True)
    await manager.tasks[done_run.id]
    state = manager.state(done_run.id)
    depts = [k for kind, k in service.targets(session, done_run) if kind == "dept"]
    assert state.finished and state.total == len(depts) and not state.errors
    session.expire_all()
    assert {r.key for r in session.scalars(select(AiChartNote))} == set(depts)
