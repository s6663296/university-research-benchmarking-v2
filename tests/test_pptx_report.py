from pptx import Presentation
from pptx.dml.color import RGBColor
from sqlalchemy import select

from app.db.models import Anomaly, Export, RunUnit
from app.ai.service import SummaryDraft
from app.reports.pptx_report import FOOTER_RULE_Y, NAVY, TEMPLATE, build_dept_report
from app.reports.tables import fmt, indicator_table

RED = RGBColor(0xFF, 0x00, 0x00)


def tables_on(slide):
    return [s.table for s in slide.shapes if s.has_table]


def texts_on(slide):
    return "\n".join(s.text_frame.text for s in slide.shapes if s.has_text_frame)


def build(session, run, tmp_path, ai_text=None):
    return Presentation(build_dept_report(session, run.id, "資訊管理學系", tmp_path / "r.pptx", ai_text))


def test_template_has_no_slides():
    p = Presentation(TEMPLATE)
    assert len(p.slides) == 0 and "標題及內容" in [layout.name for layout in p.slide_layouts]


async def test_report_structure(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path)
    assert len(p.slides) == 9
    assert p.slides[0].shapes.title.text == "國立中央大學管理學院-\v資訊管理學系"
    assert "目錄" in texts_on(p.slides[1])
    assert "管理學院各系所論文指標排名" in texts_on(p.slides[2])
    assert "2021-2025" in texts_on(p.slides[3])


async def test_ncu_row_red_and_values(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path)
    t = tables_on(p.slides[4])[0]                   # Top1% 對標表
    rows = [[c.text for c in r.cells] for r in t.rows]
    assert rows[0] == ["單位", "5年篇數\n(A)", "單年教師數\n(B)", "單年師均\n(A/5/B)"]
    ncu = next(r for r in t.rows if r.cells[0].text == "資訊管理學系\n中央")
    colors = {run.font.color.rgb for c in ncu.cells for para in c.text_frame.paragraphs for run in para.runs}
    assert colors == {RED}
    expected = next(r for r in indicator_table(session, done_run.id, "benchmark", "資訊管理學系", "top1").rows
                    if r.is_ncu)
    assert ncu.cells[3].text == fmt(expected.value)
    other = next(r for r in t.rows if r.cells[0].text == "資訊管理學系\n台大")
    assert RED not in {run.font.color.rgb for c in other.cells for para in c.text_frame.paragraphs
                       for run in para.runs if run.font.color and run.font.color.type}


async def test_pubs_page_has_both_tables(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path)
    left, right = tables_on(p.slides[3])
    assert len(left.columns) == 8 and left.cell(1, 6).text == "Q1\n(A/C)"
    assert "管理學院內系所比較" in texts_on(p.slides[3])
    assert len(right.rows) == 2 + 3


async def test_summary_ranks(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path)
    t = tables_on(p.slides[2])[0]
    texts = [[c.text for c in r.cells] for r in t.rows]
    assert any(r[1] == "對標學校: 3間" for r in texts) and any(r[1] == "管理學院內" for r in texts)
    assert all(v.startswith("#") or v in ("", "待確認") for r in texts[2:] for v in r[2:])


async def test_college_table_only_marks_own_dept_red(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path)
    t = tables_on(p.slides[4])[1]                   # Top1% 院內表
    red = [r.cells[0].text for r in t.rows
           if RED in {run.font.color.rgb for para in r.cells[0].text_frame.paragraphs for run in para.runs
                      if run.font.color and run.font.color.type}]
    assert red == ["資訊管理學系"]


def boxes(slide):
    for s in slide.shapes:
        height = sum(r.height for r in s.table.rows) if s.has_table else s.height
        yield s.name, s.left, s.top, s.left + s.width, s.top + height


async def test_content_slides_fit_and_do_not_overlap(session, done_run, tmp_path):
    ru = session.scalars(select(RunUnit).where(RunUnit.run_id == done_run.id)).first()
    session.add(Anomaly(run_id=done_run.id, unit_id=ru.unit_id, kind="zero_results", detail="x", status="open"))
    session.commit()
    p = build(session, done_run, tmp_path)
    for slide in list(p.slides)[2:]:
        shapes = list(boxes(slide))
        for name, x0, y0, x1, y1 in shapes:
            assert 0 <= x0 and 0 <= y0 and x1 <= p.slide_width and y1 <= p.slide_height, name
        for i, a in enumerate(shapes):
            for b in shapes[i + 1:]:
                assert a[3] <= b[1] or b[3] <= a[1] or a[4] <= b[2] or b[4] <= a[2], (a[0], b[0])


async def test_pending_banner_and_missing_teacher(session, done_run, tmp_path):
    ru = session.scalars(select(RunUnit).where(RunUnit.run_id == done_run.id)).first()
    session.add(Anomaly(run_id=done_run.id, unit_id=ru.unit_id, kind="zero_results", detail="x", status="open"))
    session.commit()
    p = build(session, done_run, tmp_path)
    assert "待人工確認" in texts_on(p.slides[4])


async def test_self_citation_footnote(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path)
    assert "排除單位論文之間的互相引用" in texts_on(p.slides[7])
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


def save_text(session, overrides):
    from app.db.settings_store import set_setting

    set_setting(session, "report_text", overrides)


def toc_text(p):
    return texts_on(p.slides[1])


async def test_default_text_matches_sample_wording(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path)
    assert "管理學院各系所論文指標於對標學校排名" in toc_text(p)
    assert "論文發表師均（QS / THE 看總篇數、高教深耕看 Q1）" in toc_text(p)
    assert p.slides[3].shapes.title.text == "2021-2025 論文發表篇數師均"
    assert p.slides[2].shapes.title.text == "管理學院各系所論文指標排名"


async def test_renaming_toc_updates_toc_title_and_kicker(session, done_run, tmp_path):
    save_text(session, {"pubs": {"name": "期刊論文產出"}, "fwci": {"name": "引用影響力", "tag": ""},
                        "summary": {"name": "{學院}排名一覽"}, "detail": {"name": "{系所}指標細項"}})
    p = build(session, done_run, tmp_path)
    assert "・期刊論文產出（QS / THE 看總篇數、高教深耕看 Q1）" in toc_text(p)
    assert "・引用影響力\n" in toc_text(p) + "\n"
    assert p.slides[3].shapes.title.text == "2021-2025 期刊論文產出"
    assert p.slides[6].shapes.title.text == "2021-2025 引用影響力"
    assert p.slides[2].shapes.title.text == "管理學院排名一覽"
    assert "I. 管理學院排名一覽" in texts_on(p.slides[2])
    assert "II. 資訊管理學系指標細項" in toc_text(p) and "II. 資訊管理學系指標細項" in texts_on(p.slides[3])
    assert p.slides[4].shapes.title.text == "2021-2025 被引數達全球前1%篇數（師均）"     # 沒改的頁面維持預設


async def test_notes_default_edit_and_clear(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path)
    assert "※教育部高教深耕指標" in texts_on(p.slides[3]) and "計算方式" in texts_on(p.slides[6])
    assert "註3：單位排序以 Q1 單年師均由高到低排序。" in texts_on(p.slides[3])
    save_text(session, {"pubs": {"subtitle": "※自訂標示", "notes": "註：{系所}{期間}自訂"},
                        "fwci": {"formula": "", "notes": ""}})
    p = build(session, done_run, tmp_path)
    assert "※自訂標示" in texts_on(p.slides[3]) and "註：資訊管理學系2021-2025自訂" in texts_on(p.slides[3])
    assert "註3：單位排序" not in texts_on(p.slides[3])
    assert "計算方式" not in texts_on(p.slides[6]) and "註1" not in texts_on(p.slides[6])
    assert "註1" in texts_on(p.slides[5])


async def test_long_custom_text_stays_in_box_and_unknown_braces_kept(session, done_run, tmp_path):
    long = "很長的名稱" * 12
    save_text(session, {"top1": {"name": long, "notes": "\n".join(["註：{未知}" + "說明" * 40] * 4)}})
    p = build(session, done_run, tmp_path)
    slide = p.slides[4]
    assert slide.shapes.title.text.endswith(long)
    assert slide.shapes.title.text_frame.paragraphs[0].runs[0].font.size.pt < 26
    note = next(s for s in slide.shapes if s.has_text_frame and "{未知}" in s.text_frame.text)
    assert note.text_frame.paragraphs[0].runs[0].font.size.pt == 8


def test_report_text_page_saves_only_changes_and_resets(client):
    from app.reports.report_text import BY_KEY

    r = client.get("/report-text")
    assert r.status_code == 200 and "論文發表師均" in r.text and "{系所}各指標比較" in r.text
    data = {"pubs.name": "新名稱", "pubs.tag": BY_KEY["pubs"].tag, "pubs.notes": "第一行\r\n第二行",
            "top1.name": "  ", "fwci.name": BY_KEY["fwci"].name}
    r = client.post("/report-text", data=data)
    assert r.status_code == 200
    assert "已儲存" in r.text
    assert "新名稱" in r.text
    assert "第一行\n第二行" in r.text, repr(r.text[r.text.find("pubs-notes"):][:160])

    from app.db.settings_store import get_setting

    def saved():
        with client.app.state.session_factory() as s:
            return get_setting(s, "report_text")

    assert saved() == {"pubs": {"name": "新名稱", "notes": "第一行\n第二行"}}
    client.post("/report-text", data={**data, "reset_key": "pubs", "fwci.notes": "只留這個"})
    assert saved() == {"fwci": {"notes": "只留這個"}}
    client.post("/report-text", data={"reset_key": "__all__"})
    assert saved() == {}


def test_custom_text_reaches_download(client, web_done_run):
    client.post("/report-text", data={"pubs.name": "下載驗證名稱"})
    r = client.get(f"/runs/{web_done_run}/pptx/資訊管理學系")
    from io import BytesIO

    p = Presentation(BytesIO(r.content))
    assert "下載驗證名稱" in toc_text(p) and p.slides[3].shapes.title.text == "2021-2025 下載驗證名稱"


DRAFT = SummaryDraft("本系 Q1 表現穩定。", "Q1 師均由 1.10 提升至 1.20。")


async def test_no_ai_text_keeps_structure(session, done_run, tmp_path):
    assert len(build(session, done_run, tmp_path).slides) == 9


async def test_ai_page_is_last(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path, DRAFT)
    assert len(p.slides) == 10
    last = texts_on(p.slides[-1])
    assert "資訊管理學系總結與建議" in last and "本系 Q1 表現穩定。" in last
    assert "Q1 師均由 1.10 提升至 1.20。" in last and "10 / 10" in last
    assert "III. 總結與建議" in last


async def test_ai_page_without_baseline_says_so(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path, SummaryDraft("本系穩定。", None))
    assert "無前期資料可比較" in texts_on(p.slides[-1])


async def test_ai_page_with_baseline_but_no_comparison_omits_section(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path, SummaryDraft("本系穩定。", None, has_baseline=True))
    last = texts_on(p.slides[-1])
    assert "無前期資料可比較" not in last and "與前期比較" not in last and "本系穩定。" in last


async def test_toc_lists_ai_chapter(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path, DRAFT)
    assert "III. 總結與建議" in toc_text(p)
    assert "總結與建議" not in toc_text(build(session, done_run, tmp_path))


async def test_long_ai_text_splits_without_overlap(session, done_run, tmp_path):
    draft = SummaryDraft("\n".join("字" * 60 for _ in range(40)), None)
    p = build(session, done_run, tmp_path, draft)
    assert len(p.slides) > 10
    assert "（續）" in p.slides[-1].shapes.title.text
    for slide in list(p.slides)[9:]:
        shapes = list(boxes(slide))
        for name, x0, y0, x1, y1 in shapes:
            assert y1 <= p.slide_height and x1 <= p.slide_width, name
        for name, x0, y0, x1, y1 in shapes:
            if "Title" not in name and y0 < FOOTER_RULE_Y:
                assert y1 <= FOOTER_RULE_Y, name
        for i, a in enumerate(shapes):
            for b in shapes[i + 1:]:
                assert a[3] <= b[1] or b[3] <= a[1] or a[4] <= b[2] or b[4] <= a[2], (a[0], b[0])


async def test_ai_page_uses_report_style(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path, DRAFT)
    for slide in (p.slides[3], p.slides[-1]):
        run = slide.shapes.title.text_frame.paragraphs[0].runs[0]
        assert run.font.name == "Microsoft JhengHei" and run.font.color.rgb == NAVY


async def test_single_huge_paragraph_stays_above_footer(session, done_run, tmp_path):
    p = build(session, done_run, tmp_path, SummaryDraft("字" * 3000, None))
    assert len(p.slides) > 10
    for slide in list(p.slides)[9:]:
        shapes = list(boxes(slide))
        for name, x0, y0, x1, y1 in shapes:
            if "Title" not in name and y0 < FOOTER_RULE_Y:
                assert y1 <= FOOTER_RULE_Y, name
        for i, a in enumerate(shapes):
            for b in shapes[i + 1:]:
                assert a[3] <= b[1] or b[3] <= a[1] or a[4] <= b[2] or b[4] <= a[2], (a[0], b[0])
