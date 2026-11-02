from decimal import Decimal

from sqlalchemy import delete, select

from app.db.models import Result, RunPublication, RunUnit, Unit
from app.reports.provenance import Explainer, evidence_papers, rank_text
from app.runs.executor import recompute, unit_pubs


def unit_id(session, run, school, dept="資訊管理學系"):
    return session.scalars(select(RunUnit.unit_id).join(Unit, Unit.id == RunUnit.unit_id).where(
        RunUnit.run_id == run.id, Unit.school == school, Unit.ncu_dept == dept)).one()


async def test_count_value_substitutes_numbers(session, done_run):
    e = Explainer(session, done_run.id).cell(unit_id(session, done_run, "中央"), "benchmark", "total", "value")
    assert e.formula == "A ÷ 年數 ÷ B"
    assert e.substituted == "20 ÷ 5 ÷ 24 = 0.17"
    assert e.stored == e.recomputed == Decimal("0.17") and not e.mismatch
    assert e.drill.kind == "papers"
    assert any("教育部 114 學年" in s.text for s in e.sources)
    assert any(s.label == "Scopus 查詢" and "AFFIL" in s.text for s in e.sources)


async def test_count_raw_and_avg(session, done_run):
    uid = unit_id(session, done_run, "中央")
    q1 = sum(1 for p in unit_pubs(session, done_run.id, uid) if p.q1)
    ex = Explainer(session, done_run.id)
    raw = ex.cell(uid, "benchmark", "q1", "raw")
    assert raw.substituted == f"{q1} 篇（共 20 篇）" and raw.recomputed == q1 and not raw.mismatch
    assert raw.drill.filter == "q1"
    avg = ex.cell(uid, "benchmark", "q1", "avg")
    assert avg.formula == "A ÷ 年數" and avg.substituted.startswith(f"{q1} ÷ 5 = ")
    assert not avg.mismatch


async def test_mean_and_ratio(session, done_run):
    uid = unit_id(session, done_run, "中央")
    ex = Explainer(session, done_run.id)
    fwci = ex.cell(uid, "benchmark", "fwci", "value")
    assert not fwci.mismatch and fwci.stored is not None and "÷" in fwci.substituted
    cites = ex.cell(uid, "benchmark", "avg_citations", "value")
    assert not cites.mismatch and cites.substituted.endswith(f"= {cites.stored:.2f}")
    assert any(s.label == "自我引用" for s in cites.sources)


async def test_rank_lists_group(session, done_run):
    e = Explainer(session, done_run.id).cell(unit_id(session, done_run, "中央"), "benchmark", "total", "rank")
    assert e.stored == e.recomputed == 2 and not e.mismatch
    assert "同組 3 個單位" in e.substituted and "1 個" in e.substituted
    assert e.drill.kind == "group"


def test_rank_text_ties():
    vals = {1: Decimal("2"), 2: Decimal("1"), 3: Decimal("1"), 4: None}
    labels = {1: "甲", 2: "乙", 3: "丙", 4: "丁"}
    assert rank_text(vals, 3, labels) == "同組 4 個單位（1 個無值不排名），值比它高的有 1 個 → 第 2 名；與 乙 同值"
    assert rank_text(vals, 4, labels) is None


async def test_teachers_cell(session, done_run):
    uid = unit_id(session, done_run, "中央")
    e = Explainer(session, done_run.id).cell(uid, "benchmark", "total", "teachers")
    assert e.stored == e.recomputed == 24 and e.drill.kind == "teachers"
    ru = session.scalars(select(RunUnit).where(RunUnit.run_id == done_run.id, RunUnit.unit_id == uid)).one()
    ru.teacher_detail_json = None  # 舊分析
    session.commit()
    e = Explainer(session, done_run.id).cell(uid, "benchmark", "total", "teachers")
    assert any("未保存" in s.text for s in e.sources)


async def test_mismatch_detected(session, done_run):
    uid = unit_id(session, done_run, "中央")
    r = session.scalars(select(Result).where(Result.run_id == done_run.id, Result.unit_id == uid,
                                             Result.comparison == "benchmark", Result.indicator_id == "total")).one()
    r.value = Decimal("9.99")
    session.commit()
    e = Explainer(session, done_run.id).cell(uid, "benchmark", "total", "value")
    assert e.mismatch and e.stored == Decimal("9.99") and e.recomputed == Decimal("0.17")


async def test_missing_reasons(session, done_run):
    ntu, ncu = unit_id(session, done_run, "台大"), unit_id(session, done_run, "中央")
    ru = session.scalars(select(RunUnit).where(RunUnit.run_id == done_run.id, RunUnit.unit_id == ntu)).one()
    ru.teacher_count = None
    session.execute(delete(RunPublication).where(RunPublication.run_id == done_run.id, RunPublication.unit_id == ncu))
    session.commit()
    recompute(session, done_run.id)
    ex = Explainer(session, done_run.id)
    assert ex.cell(ntu, "benchmark", "total", "value").missing_reason.startswith("缺教師數")
    assert ex.cell(ncu, "benchmark", "total", "value").missing_reason == "Scopus 查無論文"
    assert ex.cell(ncu, "benchmark", "total", "value").drill is None
    ru.status, ru.error = "failed", "HTTP 500"
    session.commit()
    recompute(session, done_run.id)
    e = Explainer(session, done_run.id).cell(ntu, "benchmark", "q1", "raw")
    assert e.missing_reason == "抓取失敗：HTTP 500" and e.drill is None


async def test_evidence_papers_match_count(session, done_run):
    uid = unit_id(session, done_run, "中央")
    papers = evidence_papers(session, done_run.id, uid)
    assert len(papers) == 20 and all(p.eid.startswith("2-s2.0-") for p in papers)
    raw = Explainer(session, done_run.id).cell(uid, "benchmark", "top10", "raw").recomputed
    assert sum(1 for p in papers if p.metrics.top10) == raw


async def test_plain_text_lines(session, done_run):
    e = Explainer(session, done_run.id).cell(unit_id(session, done_run, "中央"), "benchmark", "total", "value")
    text = e.plain_text()
    assert "A ÷ 年數 ÷ B = 20 ÷ 5 ÷ 24 = 0.17" in text and "B：" in text


async def test_mismatches_lists_changed_cells(session, done_run):
    assert Explainer(session, done_run.id).mismatches() == []
    uid = unit_id(session, done_run, "中央")
    r = session.scalars(select(Result).where(Result.run_id == done_run.id, Result.unit_id == uid,
                                             Result.comparison == "college", Result.indicator_id == "fwci")).one()
    r.value = Decimal("99")
    session.commit()
    bad = Explainer(session, done_run.id).mismatches()
    assert [e.where for e in bad] == [(uid, "college", "fwci", "value")]
