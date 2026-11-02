from decimal import Decimal

import pytest
from openpyxl import load_workbook
from pptx import Presentation

from app.funding.parser import FundingFormatError, parse_funding
from app.funding.service import count_text, funding_table, import_funding, money
from app.reports.pptx_report import build_dept_report
from app.reports.xlsx_report import build_run_workbook
from tests.funding_builder import build_funding, sample_funding


def test_parse_reads_header_row_and_generated_time():
    parsed = parse_funding(sample_funding())
    assert parsed.generated_at == "2026-11-02 09:57:42"
    assert parsed.years == [114]
    first = parsed.projects[0]
    assert (first.row, first.main_category, first.pi_dept, first.amount) == (3, "國科會計畫", "資訊管理學系", 1000000)


def test_parse_rejects_other_files():
    from tests.moe_builder import sample_moe
    with pytest.raises(FundingFormatError):
        parse_funding(sample_moe())


def test_parse_skips_rows_without_year():
    parsed = parse_funding(build_funding([(None, "國科會計畫", "管理學院", "資訊管理學系", 1),
                                          (114, "國科會計畫", "管理學院", "資訊管理學系", 2)]))
    assert len(parsed.projects) == 1 and "第 3 列" in parsed.warnings[0]


def test_import_is_idempotent(session):
    a, _ = import_funding(session, "f.xlsx", sample_funding())
    b, _ = import_funding(session, "f2.xlsx", sample_funding())
    assert a.id == b.id and a.row_count == 6


async def test_funding_table_single_year(session, done_run):
    import_funding(session, "f.xlsx", sample_funding())
    t = funding_table(session, done_run, "管理學院")
    assert t.years == [114] and t.missing_years == [110, 111, 112, 113]
    rows = {r.ncu_dept: r for r in t.rows}
    im = rows["資訊管理學系"]
    assert (im.nstc_count, im.nstc_amount, im.industry_amount) == (2, 1500000, 300000)  # 技術服務收入不計入
    assert im.total_avg == 1800000 and im.per_faculty == Decimal(1800000) / im.teachers
    assert rows["財務金融學系"].nstc_amount == 800000
    assert rows["會計研究所"].nstc_amount == 0
    assert t.rows[0].ncu_dept == "資訊管理學系"  # 依國科會年平均排序


async def test_average_over_years_and_newer_file_wins(session, done_run):
    import_funding(session, "113.xlsx", build_funding([(113, "國科會計畫", "管理學院", "資訊管理學系", 400000)]))
    import_funding(session, "114舊.xlsx", build_funding([(114, "國科會計畫", "管理學院", "資訊管理學系", 1)]))
    import_funding(session, "114新.xlsx", build_funding([(114, "國科會計畫", "管理學院", "資訊管理學系", 600000),
                                                        (114, "國科會計畫", "管理學院", "資訊管理學系", 200000)]))
    t = funding_table(session, done_run, "管理學院")
    im = next(r for r in t.rows if r.ncu_dept == "資訊管理學系")
    assert t.years == [113, 114] and t.sources[114].filename == "114新.xlsx"
    assert im.nstc_avg == 600000 and count_text(im.nstc_count_avg) == "1.50"


async def test_years_outside_period_are_ignored(session, done_run):
    import_funding(session, "f.xlsx", build_funding([(109, "國科會計畫", "管理學院", "資訊管理學系", 5)]))
    assert not funding_table(session, done_run, "管理學院").has_data


def test_money_format():
    assert money(Decimal("1883172.5")) == "1,883,173"
    assert money(Decimal(0)) == "-"
    assert money(None) == "待確認"


def _texts(slide):
    return "\n".join(s.text_frame.text for s in slide.shapes if s.has_text_frame)


async def test_pptx_has_funding_section_and_renumbers(session, done_run, tmp_path):
    import_funding(session, "f.xlsx", sample_funding())
    p = Presentation(build_dept_report(session, done_run.id, "資訊管理學系", tmp_path / "r.pptx"))
    toc = _texts(p.slides[1])
    assert "I. 管理學院國科會及產學計畫經費年平均" in toc and "II. 管理學院各系所論文指標於對標學校排名" in toc
    assert "I. 管理學院國科會及產學計畫經費年平均" in _texts(p.slides[2])
    table = next(s.table for s in p.slides[2].shapes if s.has_table)
    rows = [[c.text for c in r.cells] for r in table.rows]
    assert rows[0] == ["系所", "國科會件數", "國科會經費", "產學經費", "加總", "單年教師數", "師均經費"]
    assert rows[1][:5] == ["資訊管理學系", "2", "1,500,000", "300,000", "1,800,000"]
    assert rows[3][3] == "-"                                       # 會計所沒有產學計畫
    assert table.cell(1, 0).text_frame.paragraphs[0].runs[0].font.color.rgb == (0xFF, 0, 0)   # 本系所標紅
    assert "缺少 110、111、112、113 年度" in _texts(p.slides[2])
    assert "II、管理學院各系所論文指標排名" in _texts(p.slides[3])
    assert "III. 2021-2025論文發表篇數師均" in _texts(p.slides[4])


async def test_pptx_without_funding_is_unchanged(session, done_run, tmp_path):
    p = Presentation(build_dept_report(session, done_run.id, "資訊管理學系", tmp_path / "r.pptx"))
    assert "I. 管理學院各系所論文指標於對標學校排名" in _texts(p.slides[1])
    assert "I、管理學院各系所論文指標排名" in _texts(p.slides[2])


async def test_xlsx_funding_sheets(session, done_run, tmp_path):
    import_funding(session, "f.xlsx", sample_funding())
    wb = load_workbook(build_run_workbook(session, done_run.id, tmp_path / "r.xlsx"))
    assert "計畫經費" in wb.sheetnames and "計畫經費明細" in wb.sheetnames
    detail = list(wb["計畫經費明細"].iter_rows(min_row=2, values_only=True))
    assert len(detail) == 4 and {r[1] for r in detail} == {"資訊管理學系", "財務金融學系"}


def test_web_upload_list_and_delete(client):
    r = client.post("/funding/import", files={"file": ("經分表.xlsx", sample_funding())})
    assert r.status_code == 200 and "經分表.xlsx" in r.text and "資訊管理學系" in r.text and "1,500,000" in r.text
    assert "計畫經費統計表" in client.get("/").text            # 導覽列
    r = client.post("/funding/1/delete")
    assert "尚未匯入經分表" in r.text


def test_web_upload_rejects_wrong_file(client):
    r = client.post("/funding/import", files={"file": ("x.xlsx", b"not an xlsx")})
    assert r.status_code == 400 and "無法讀取" in r.text
