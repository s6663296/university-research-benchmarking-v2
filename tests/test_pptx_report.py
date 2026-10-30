from pptx import Presentation
from pptx.dml.color import RGBColor
from sqlalchemy import select

from app.db.models import Anomaly, Export, RunUnit
from app.reports.pptx_report import TEMPLATE, build_dept_report
from app.reports.tables import fmt, indicator_table

RED = RGBColor(0xFF, 0x00, 0x00)


def tables_on(slide):
    return [s.table for s in slide.shapes if s.has_table]


def texts_on(slide):
    return "\n".join(s.text_frame.text for s in slide.shapes if s.has_text_frame)


def build(session, run, tmp_path):
    return Presentation(build_dept_report(session, run.id, "資訊管理學系", tmp_path / "r.pptx"))


def test_template_has_no_slides():
    p = Presentation(TEMPLATE)
    assert len(p.slides) == 0 and "標題及內容" in [layout.name for layout in p.slide_layouts]


async def test_report_structure(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path)
    assert len(p.slides) == 9
    assert p.slides[0].shapes.title.text == "國立中央大學管理學院-資管系"
    assert "目錄" in texts_on(p.slides[1])
    assert "管理學院各系所論文指標排名" in texts_on(p.slides[2])
    assert "2021-2025" in texts_on(p.slides[3])


async def test_ncu_row_red_and_values(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path)
    t = tables_on(p.slides[4])[0]                   # Top1% 對標表
    rows = [[c.text for c in r.cells] for r in t.rows]
    assert rows[0] == ["單位", "5年篇數 (A)", "單年教師數(B)", "單年師均 (A/5/B)"]
    ncu = next(r for r in t.rows if r.cells[0].text == "資管系-中央")
    colors = {run.font.color.rgb for c in ncu.cells for para in c.text_frame.paragraphs for run in para.runs}
    assert colors == {RED}
    expected = next(r for r in indicator_table(session, done_run.id, "benchmark", "資訊管理學系", "top1").rows
                    if r.is_ncu)
    assert ncu.cells[3].text == fmt(expected.value)
    other = next(r for r in t.rows if r.cells[0].text == "資管系-台大")
    assert RED not in {run.font.color.rgb for c in other.cells for para in c.text_frame.paragraphs
                       for run in para.runs if run.font.color and run.font.color.type}


async def test_pubs_page_has_both_tables(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path)
    left, right = tables_on(p.slides[3])
    assert len(left.columns) == 8 and left.cell(1, 6).text == "Q1 (A/C)"
    assert "管學院內系所比較" in texts_on(p.slides[3])
    assert len(right.rows) == 2 + 3


async def test_summary_ranks(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path)
    t = tables_on(p.slides[2])[0]
    texts = [[c.text for c in r.cells] for r in t.rows]
    assert any(r[1] == "對標學校: 3間" for r in texts) and any(r[1] == "管學院內" for r in texts)
    assert all(v.startswith("#") or v in ("", "待確認") for r in texts[2:] for v in r[2:])


async def test_pending_banner_and_missing_teacher(session, done_run, tmp_path):
    ru = session.scalars(select(RunUnit).where(RunUnit.run_id == done_run.id)).first()
    session.add(Anomaly(run_id=done_run.id, unit_id=ru.unit_id, kind="zero_results", detail="x", status="open"))
    session.commit()
    p = build(session, done_run, tmp_path)
    assert "待人工確認" in texts_on(p.slides[4])


async def test_self_citation_footnote(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path)
    assert "待驗證" in texts_on(p.slides[7])
    assert "114學年度" in texts_on(p.slides[4])


def test_download_route(client, web_done_run):
    r = client.get(f"/runs/{web_done_run}/pptx/資訊管理學系")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.presentationml")
    with client.app.state.session_factory() as s:
        assert s.scalars(select(Export.kind).where(Export.run_id == web_done_run)).all() == ["pptx"]


def test_zip_route(client, web_done_run):
    r = client.get(f"/runs/{web_done_run}/pptx.zip")
    assert r.status_code == 200 and r.content[:2] == b"PK"
