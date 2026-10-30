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
    ncu = next(r for r in rows if r[0] == "資管系-中央")
    ntu = next(r for r in rows if r[0] == "資管系-台大")
    assert ncu[4] == 0.17 and ntu[4] == "待確認"
    cell = next(c for c in ws["E"] if c.value == 0.17)
    assert cell.number_format == "0.00" and cell.font.color.rgb.endswith("FF0000")


async def test_publication_sheet(session, done_run, tmp_path):
    ws = load_workbook(build(session, done_run, tmp_path))["逐篇指標"]
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0][:3] == ("學校", "中大系所", "scopus_id")
    assert len(rows) - 1 == 20 + 20 + 10 + 10 + 7


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
