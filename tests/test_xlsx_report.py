import zipfile

from openpyxl import load_workbook
from sqlalchemy import select

from app.db.models import Export, RunUnit, Unit
from app.reports.xlsx_report import build_run_workbook
from app.runs.executor import recompute


def build(session, run, tmp_path):
    return build_run_workbook(session, run.id, tmp_path / "r.xlsx")


async def test_workbook_sheets(session, done_run, tmp_path):
    wb = load_workbook(build(session, done_run, tmp_path))
    assert wb.sheetnames[0] == "排名總表"
    assert {"查詢條件", "逐篇指標", "教師數", "待確認", "版本備註"} <= set(wb.sheetnames)
    assert "平均論文被引次數(排除自我引用)" in wb.sheetnames
    assert all(len(n) <= 31 for n in wb.sheetnames)


async def test_values_and_pending(session, done_run, tmp_path):
    ru = session.scalars(select(RunUnit).join(Unit, Unit.id == RunUnit.unit_id)
                         .where(RunUnit.run_id == done_run.id, Unit.school == "台大")).one()
    ru.teacher_count = None
    session.commit()
    recompute(session, done_run.id)
    ws = load_workbook(build(session, done_run, tmp_path))["論文總篇數"]
    rows = list(ws.iter_rows(values_only=True))
    ncu = next(r for r in rows if r[0] == "資訊管理學系-中央")
    ntu = next(r for r in rows if r[0] == "資訊管理學系-台大")
    assert ncu[4] == 0.17 and ntu[4] == "待確認"
    cell = next(c for c in ws["E"] if c.value == 0.17)
    assert cell.number_format == "0.00" and cell.font.color.rgb.endswith("FF0000")


async def test_publication_sheet(session, done_run, tmp_path):
    ws = load_workbook(build(session, done_run, tmp_path))["逐篇指標"]
    rows = list(ws.iter_rows(values_only=True))
    assert "篩選" in rows[0][0]  # 第一列是使用說明
    assert rows[1][:4] == ("學校", "中大系所", "該校系所", "scopus_id")
    assert len(rows) - 2 == 20 + 20 + 10 + 10 + 7
    assert ws.auto_filter.ref.startswith("A2:") and ws.freeze_panes == "A3"


async def test_publication_sheet_filters_back_to_counts(session, done_run, tmp_path):
    wb = load_workbook(build(session, done_run, tmp_path))
    rows = list(wb["逐篇指標"].iter_rows(values_only=True))
    header = rows[1]
    q1 = header.index("q1")
    filtered = [r for r in rows[2:] if r[0] == "中央" and r[1] == "資訊管理學系" and r[q1] is True]
    a = next(r[1] for r in wb["Q1"].iter_rows(values_only=True) if r[0] == "資訊管理學系-中央")
    assert len(filtered) == a


async def test_cells_have_provenance_comments(session, done_run, tmp_path):
    wb = load_workbook(build(session, done_run, tmp_path))
    ws = wb["論文總篇數"]
    cell = next(c for c in ws["E"] if c.value == 0.17)
    assert "A ÷ 年數 ÷ B = 20 ÷ 5 ÷ 24 = 0.17" in cell.comment.text and "B：教育部 114 學年" in cell.comment.text
    assert ws.cell(cell.row, 1).comment is None  # 單位名稱不加
    rank_cell = next(c for c in wb["排名總表"]["C"] if isinstance(c.value, str) and c.value.startswith("#"))
    assert "同組" in rank_cell.comment.text
    fwci = wb["FWCI"]
    assert any(c.comment and "有值論文的平均" in c.comment.text for c in fwci["B"])


async def test_teacher_sheet_has_moe_detail(session, done_run, tmp_path):
    rows = list(load_workbook(build(session, done_run, tmp_path))["教師數"].iter_rows(values_only=True))
    header = rows[0]
    assert {"教育部學年", "系所代碼", "系所名稱", "該列人數"} <= set(header)
    ncu = [r for r in rows[1:] if r[0] == "中央" and r[1] == "資訊管理學系"]
    assert sum(r[header.index("該列人數")] for r in ncu) == 24 and all(r[header.index("系所代碼")] for r in ncu)


async def test_no_api_key_in_workbook(session, done_run, tmp_path):
    path = build(session, done_run, tmp_path)
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            assert b"apiKey" not in zf.read(name), name


def test_download_route(client, web_done_run):
    r = client.get(f"/runs/{web_done_run}/xlsx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert "attachment" in r.headers["content-disposition"]
    with client.app.state.session_factory() as s:
        assert "xlsx" in s.scalars(select(Export.kind).where(Export.run_id == web_done_run)).all()
