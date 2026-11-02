import re
from decimal import Decimal

from sqlalchemy import select

from app.db.models import Result, RunUnit, Unit


def ncu_unit(client, run_id):
    with client.app.state.session_factory() as s:
        return s.scalars(select(RunUnit.unit_id).join(Unit, Unit.id == RunUnit.unit_id).where(
            RunUnit.run_id == run_id, Unit.school == "中央", Unit.ncu_dept == "資訊管理學系")).one()


def test_every_number_cell_has_explanation(client, web_done_run):
    text = client.get(f"/runs/{web_done_run}", params={"ind": "total"}).text
    # 對標 3 列 + 院內 3 列，每列 5 個數字格
    assert text.count('<template class="ev-body"') == 30
    assert "20 ÷ 5 ÷ 24 = 0.17" in text and "✓ 重算值與存檔值一致" in text
    assert "/static/evidence.js" in text


def test_mean_indicator_cells(client, web_done_run):
    text = client.get(f"/runs/{web_done_run}", params={"ind": "fwci"}).text
    assert text.count('<template class="ev-body"') == 12


def test_summary_cells_link_to_indicator(client, web_done_run):
    text = client.get(f"/runs/{web_done_run}").text
    uid = ncu_unit(client, web_done_run)
    assert '<template class="ev-body"' in text and f"?ind=q1&amp;hl={uid}" in text


def test_highlight_row(client, web_done_run):
    uid = ncu_unit(client, web_done_run)
    text = client.get(f"/runs/{web_done_run}", params={"ind": "total", "hl": uid}).text
    assert re.search(rf'<tr id="u{uid}-benchmark"[^>]*class="[^"]*hl', text)


def test_evidence_papers(client, web_done_run):
    uid = ncu_unit(client, web_done_run)
    text = client.get(f"/runs/{web_done_run}/evidence",
                      params={"unit": uid, "cmp": "benchmark", "ind": "q1", "col": "raw"}).text
    raw = re.search(r"(\d+) 篇（共 20 篇）", text).group(1)
    assert text.count('class="ev-paper ev-hitrow"') == int(raw)
    assert text.count('class="ev-paper') == 20
    assert "scopus.com/record/display.uri?eid=2-s2.0-" in text


def test_evidence_teachers_and_group(client, web_done_run):
    uid = ncu_unit(client, web_done_run)
    teachers = client.get(f"/runs/{web_done_run}/evidence",
                          params={"unit": uid, "cmp": "benchmark", "ind": "total", "col": "teachers"}).text
    assert "教育部 114 學年" in teachers and "代碼" in teachers
    group = client.get(f"/runs/{web_done_run}/evidence",
                       params={"unit": uid, "cmp": "benchmark", "ind": "total", "col": "rank"}).text
    assert group.count('class="ev-group') == 3 and "ev-me" in group


def test_evidence_bad_params(client, web_done_run):
    uid = ncu_unit(client, web_done_run)
    for params in ({"unit": 99999, "cmp": "benchmark", "ind": "total", "col": "value"},
                   {"unit": uid, "cmp": "benchmark", "ind": "nope", "col": "value"},
                   {"unit": uid, "cmp": "benchmark", "ind": "fwci", "col": "raw"}):
        r = client.get(f"/runs/{web_done_run}/evidence", params=params)
        assert r.status_code == 404 and "找不到資料" in r.text


def test_mismatch_banner(client, web_done_run):
    uid = ncu_unit(client, web_done_run)
    with client.app.state.session_factory() as s:
        r = s.scalars(select(Result).where(Result.run_id == web_done_run, Result.unit_id == uid,
                                           Result.comparison == "benchmark", Result.indicator_id == "total")).one()
        r.value = Decimal("9.99")
        s.commit()
    text = client.get(f"/runs/{web_done_run}").text
    assert "有 1 格重算值與存檔值不符" in text and f"?ind=total&amp;hl={uid}" in text
    assert "⚠ 重算值與存檔值不符" in client.get(f"/runs/{web_done_run}", params={"ind": "total"}).text
