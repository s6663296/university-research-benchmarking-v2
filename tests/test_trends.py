"""逐年趨勢（系所層級）：計算、簡報線圖、網頁分頁、Excel 工作表。"""

from decimal import Decimal

from openpyxl import load_workbook
from pptx import Presentation
from pptx.util import Pt

from app.db.settings_store import set_setting
from app.elsevier.scival import PubMetrics
from app.metrics.indicators import BY_ID
from app.reports.pptx_report import RED, build_dept_report
from app.reports.tables import indicator_table
from app.reports.trends import TREND_INDICATORS, trend_chart, yearly_values
from app.reports.xlsx_report import build_run_workbook
from tests.test_pptx_report import boxes, texts_on


def test_yearly_counts_and_means():
    pubs = [PubMetrics("1", year=2021, top1=True, fwci=Decimal("1.5")),
            PubMetrics("2", year=2021, top1=False, fwci=Decimal("0.5")),
            PubMetrics("3", year=2023, top1=True, fwci=None),
            PubMetrics("4", year=2019, top1=True, fwci=Decimal("9")),    # 期間外不計
            PubMetrics("5", year=None, top1=True)]                       # 沒有出版年不計
    years = [2021, 2022, 2023]
    assert yearly_values(pubs, years, BY_ID["total"]) == {2021: 2, 2022: 0, 2023: 1}
    assert yearly_values(pubs, years, BY_ID["top1"]) == {2021: 1, 2022: 0, 2023: 1}
    assert yearly_values(pubs, years, BY_ID["fwci"]) == {2021: Decimal("1.00"), 2022: None, 2023: None}


async def test_yearly_counts_add_up_to_period_total(session, done_run):
    for comparison, key in (("benchmark", "資訊管理學系"), ("college", "管理學院")):
        for ind_id in ("total", "q1", "top1", "intl_collab"):
            raw = {r.unit_id: r.raw for r in indicator_table(session, done_run.id, comparison, key, ind_id).rows}
            chart = trend_chart(session, done_run.id, comparison, key, ind_id)
            assert chart.years == [2021, 2022, 2023, 2024, 2025]
            for s in chart.series:
                if raw[s.unit_id] is not None:
                    assert sum(v for v in s.values.values()) == raw[s.unit_id], (key, ind_id, s.label)


async def test_series_labels_short_and_ncu_flagged(session, done_run):
    chart = trend_chart(session, done_run.id, "benchmark", "資訊管理學系", "total")
    assert {s.label for s in chart.series} == {"中央", "台大", "政治"}
    assert [s.label for s in chart.series if s.is_ncu] == ["中央"]
    college = trend_chart(session, done_run.id, "college", "管理學院", "total")
    assert "資訊管理學系" in {s.label for s in college.series}
    assert not any(s.is_ncu for s in college.series)          # 院內全是中大，網頁上不特別標哪一個
    own = next(s.unit_id for s in college.series if s.label == "資訊管理學系")
    marked = trend_chart(session, done_run.id, "college", "管理學院", "total", own_unit_id=own)
    assert [s.label for s in marked.series if s.is_ncu] == ["資訊管理學系"]


def build(session, run, tmp_path):
    return Presentation(build_dept_report(session, run.id, "資訊管理學系", tmp_path / "r.pptx"))


async def test_report_has_no_trend_chapter_by_default(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path)
    assert not any(s.has_chart for slide in p.slides for s in slide.shapes)
    assert "逐年趨勢" not in texts_on(p.slides[1])


async def test_trend_chapter_native_charts(session, done_run, tmp_path):
    set_setting(session, "report_trends", True)
    p = build(session, done_run, tmp_path)
    assert len(p.slides) == 9 + len(TREND_INDICATORS)
    assert "III. 逐年趨勢" in texts_on(p.slides[1])
    trend_slides = list(p.slides)[9:]
    assert "III. 資訊管理學系逐年趨勢" in texts_on(trend_slides[0])
    for slide in trend_slides:
        charts = [s.chart for s in slide.shapes if s.has_chart]
        assert len(charts) == 2                        # 對標學校、院內系所
        bench = charts[0]
        assert list(bench.plots[0].categories) == ["2021", "2022", "2023", "2024", "2025"]
        own = next(s for s in bench.plots[0].series if s.name == "中央")
        assert own.format.line.color.rgb == RED and own.format.line.width == Pt(3.5)
        others = [s for s in bench.plots[0].series if s.name != "中央"]
        assert others and all(s.format.line.color.rgb != RED for s in others)
        college = charts[1]                            # 院內全是中大系所，只有本系所標紅
        reds = [s.name for s in college.plots[0].series if s.format.line.color.rgb == RED]
        assert reds == ["資訊管理學系"]
    fwci = trend_slides[TREND_INDICATORS.index("fwci")]
    assert "被引次數還在累積" in texts_on(fwci)
    assert "被引次數還在累積" not in texts_on(trend_slides[TREND_INDICATORS.index("total")])


async def test_trend_chart_values_match_calculation(session, done_run, tmp_path):
    set_setting(session, "report_trends", True)
    p = build(session, done_run, tmp_path)
    slide = list(p.slides)[9 + TREND_INDICATORS.index("total")]
    bench = next(s.chart for s in slide.shapes if s.has_chart)
    expected = trend_chart(session, done_run.id, "benchmark", "資訊管理學系", "total")
    for s in expected.series:
        plotted = next(ps for ps in bench.plots[0].series if ps.name == s.label)
        assert list(plotted.values) == [float(s.values[y]) for y in expected.years]


async def test_trend_slides_do_not_overlap(session, done_run, tmp_path):
    set_setting(session, "report_trends", True)
    p = build(session, done_run, tmp_path)
    for slide in list(p.slides)[9:]:
        shapes = list(boxes(slide))
        for name, x0, y0, x1, y1 in shapes:
            assert 0 <= x0 and 0 <= y0 and x1 <= p.slide_width and y1 <= p.slide_height, name
        for i, a in enumerate(shapes):
            for b in shapes[i + 1:]:
                assert a[3] <= b[1] or b[3] <= a[1] or a[4] <= b[2] or b[4] <= a[2], (a[0], b[0])


async def test_ai_chapter_renumbered_after_trends(session, done_run, tmp_path):
    from app.ai.service import SummaryDraft

    set_setting(session, "report_trends", True)
    draft = SummaryDraft(summary="現況", comparison=None, has_baseline=False, challenges="挑戰", rival_changes=None)
    p = Presentation(build_dept_report(session, done_run.id, "資訊管理學系", tmp_path / "r.pptx", draft))
    assert "IV. 總結與建議" in texts_on(p.slides[1])
    assert "IV. 總結與建議" in texts_on(list(p.slides)[9 + len(TREND_INDICATORS)])


async def test_workbook_trend_sheet(session, done_run, tmp_path):
    wb = load_workbook(build_run_workbook(session, done_run.id, tmp_path / "r.xlsx"))
    rows = list(wb["逐年趨勢"].iter_rows(values_only=True))
    start = next(i for i, r in enumerate(rows) if r[0] == "對標學校比較：資訊管理學系｜論文總篇數（逐年篇數）")
    assert rows[start + 1][:6] == ("單位", 2021, 2022, 2023, 2024, 2025)
    chart = trend_chart(session, done_run.id, "benchmark", "資訊管理學系", "total")
    ncu = next(r for r in rows[start + 2:start + 2 + len(chart.series)] if r[0] == "中央")
    own = next(s for s in chart.series if s.is_ncu)
    assert list(ncu[1:6]) == [int(own.values[y]) for y in chart.years]


def test_trend_tab_and_toggle(client, web_done_run):
    text = client.get(f"/runs/{web_done_run}", params={"ind": "trend"}).text
    assert "逐年趨勢" in text and "<svg" in text and "論文總篇數（逐年篇數）" in text
    assert "系所簡報目前不含逐年趨勢" in text
    r = client.post(f"/runs/{web_done_run}/report-trends?grp=1", data={"report_trends": "1"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("ind=trend&grp=1")
    text = client.get(f"/runs/{web_done_run}", params={"ind": "trend", "grp": 1}).text
    assert "系所簡報已加入「逐年趨勢」章" in text and "管理學院內系所比較" in text


def test_trend_tab_bad_group_falls_back(client, web_done_run):
    r = client.get(f"/runs/{web_done_run}", params={"ind": "trend", "grp": 99})
    assert r.status_code == 200 and "<svg" in r.text
