from pptx import Presentation

from app.ai.service import SummaryDraft
from app.reports.college_report import build_college_report, college_report_filename
from app.reports.pptx_report import FOOTER_RULE_Y, NAVY, build_dept_report
from tests.test_pptx_report import boxes, texts_on

DRAFT = SummaryDraft("全院 Q1 表現穩定。", "Q1 師均由 1.10 提升至 1.20。")


def build(session, run, tmp_path, draft=DRAFT):
    return Presentation(build_college_report(session, run.id, "管理學院", draft, tmp_path / "c.pptx"))


async def test_college_deck_pages(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path)
    assert len(p.slides) == 3
    assert "管理學院" in p.slides[0].shapes.title.text
    assert "全院 Q1 表現穩定。" in texts_on(p.slides[1])
    assert "Q1 師均由 1.10 提升至 1.20。" in texts_on(p.slides[2])
    assert "論文指標全院總結" in texts_on(p.slides[0])


async def test_college_deck_without_baseline(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path, SummaryDraft("穩定。", None))
    assert "無前期資料可比較" in texts_on(p.slides[2])


async def test_college_deck_with_baseline_but_no_comparison_omits_page(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path, SummaryDraft("穩定。", None, has_baseline=True))
    assert len(p.slides) == 2
    assert all("無前期資料可比較" not in texts_on(s) for s in p.slides)


async def test_college_filename(session, done_run):
    assert college_report_filename(done_run, "管理學院") == (
        f"{done_run.period_from}-{done_run.period_to}管理學院論文指標全院總結.pptx")


async def test_college_deck_matches_dept_style(session, done_run, tmp_path):
    college = build(session, done_run, tmp_path)
    dept = Presentation(build_dept_report(session, done_run.id, "資訊管理學系", tmp_path / "d.pptx"))
    assert college.slides[0].background.fill.fore_color.rgb == NAVY
    assert college.slides[0].background.fill.fore_color.rgb == dept.slides[0].background.fill.fore_color.rgb
    a = college.slides[1].shapes.title.text_frame.paragraphs[0].runs[0].font
    b = dept.slides[2].shapes.title.text_frame.paragraphs[0].runs[0].font
    assert (a.name, a.color.rgb, a.size, a.bold) == (b.name, b.color.rgb, b.size, b.bold)
    assert "2 / 3" in texts_on(college.slides[1])
    assert (college.slide_width, college.slide_height) == (dept.slide_width, dept.slide_height)


async def test_college_deck_long_text_paginates(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path, SummaryDraft("\n".join("字" * 60 for _ in range(40)), None))
    assert len(p.slides) > 3
    for slide in list(p.slides)[1:]:
        shapes = list(boxes(slide))
        for name, x0, y0, x1, y1 in shapes:
            if "Title" not in name and y0 < FOOTER_RULE_Y:
                assert y1 <= FOOTER_RULE_Y, name
        for i, a in enumerate(shapes):
            for b in shapes[i + 1:]:
                assert a[3] <= b[1] or b[3] <= a[1] or a[4] <= b[2] or b[4] <= a[2], (a[0], b[0])
