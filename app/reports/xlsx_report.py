"""每次分析一份 Excel：所有表格各一張工作表，附查詢條件、逐篇指標、教師數、待確認、版本備註（設計 §9.3）。

數字格附儲存格註解說明計算方式與來源（2026-10-31-cell-provenance §6）。
"""

import json
import re
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Anomaly, Run, RunPublication, RunUnit, Unit
from app.metrics.indicators import INDICATORS
from app.reports.provenance import Explainer, Explanation
from app.reports.tables import SUMMARY_INDICATORS, indicator_table, run_groups, summary_table
from app.runs.anomaly import KIND_LABELS
from app.runs.scope import COMPARISON_LABELS
from app.teachers.moe import TEACHER_COLUMNS

RED = Font(color="FFFF0000")
BOLD = Font(bold=True)
PENDING = "待確認"


def sheet_title(text: str) -> str:
    return re.sub(r"[\[\]:*?/\\]", "", text)[:31]


def _num(value: Decimal | None):
    return PENDING if value is None else float(value)


def _append(ws, values, font=None, number_cols=()):
    ws.append(list(values))
    row = ws.max_row
    for c in number_cols:
        cell = ws.cell(row, c)
        if isinstance(cell.value, float):
            cell.number_format = "0.00"
    if font is not None:
        for cell in ws[row]:
            cell.font = font


def _comment(ws, col: int, e: Explanation | None) -> None:
    """在最後一列的第 col 欄加上來源說明註解。"""
    if e is None:
        return
    text = e.plain_text()
    lines = sum(1 + len(line) // 40 for line in text.splitlines())
    ws.cell(ws.max_row, col).comment = Comment(text, "系統", width=420, height=min(22 * lines + 20, 400))


def _indicator_sheet(wb, session, run, ind, groups, explainer):
    ws = wb.create_sheet(sheet_title(ind.label))
    for g in groups:
        title = {"benchmark": f"對標學校比較：{g.key}", "college": f"{g.key}內系所比較",
                 "custom": "自訂比較"}[g.comparison]
        _append(ws, [title], BOLD)
        t = indicator_table(session, run.id, g.comparison, g.key, ind.id, explainer=explainer)
        if ind.aggregate == "count":
            n = run.period_to - run.period_from + 1
            _append(ws, ["單位", f"{n}年篇數 (A)", f"{n}年平均", "單年教師數 (B)", "單年師均", "名次", "教師數人工修改"], BOLD)
            for r in t.rows:
                _append(ws, [r.label, PENDING if r.raw is None else int(r.raw), _num(r.five_year_avg),
                             PENDING if r.teachers is None else r.teachers, _num(r.value), r.rank,
                             "是" if r.teacher_manual else ""],
                        RED if r.is_ncu else None, number_cols=(3, 5))
                for col, key in enumerate(("raw", "avg", "teachers", "value", "rank"), start=2):
                    _comment(ws, col, r.explain.get(key))
        else:
            _append(ws, ["單位", ind.label, "名次"], BOLD)
            for r in t.rows:
                _append(ws, [r.label, _num(r.value), r.rank], RED if r.is_ncu else None, number_cols=(2,))
                _comment(ws, 2, r.explain.get("value"))
                _comment(ws, 3, r.explain.get("rank"))
        ws.append([])


def build_run_workbook(session: Session, run_id: int, out_path: Path) -> Path:
    run = session.get(Run, run_id)
    groups = run_groups(session, run)
    notes = json.loads(run.notes_json or "{}")
    explainer = Explainer(session, run_id)
    wb = Workbook()

    ws = wb.active
    ws.title = "排名總表"
    colleges = list(dict.fromkeys(g.key for g in groups if g.comparison == "college"))
    labels = {i.id: i.label for i in INDICATORS}
    for college in colleges:
        _append(ws, [f"{college}各系所論文指標排名"], BOLD)
        _append(ws, ["單位", "對外/對內項目", *(labels[i] for i in SUMMARY_INDICATORS)], BOLD)
        for s in summary_table(session, run_id, college, explainer):
            item = f"對標學校: {s.group_size}間" if s.comparison == "benchmark" else f"{college}內"
            ws.append([s.dept_label, item, *(PENDING if v is None else f"#{v}" for v in s.ranks.values())])
            for col, ind_id in enumerate(SUMMARY_INDICATORS, start=3):
                _comment(ws, col, s.explain.get(ind_id))
        ws.append([])

    for ind in INDICATORS:
        _indicator_sheet(wb, session, run, ind, groups, explainer)

    run_units = list(session.scalars(select(RunUnit).where(RunUnit.run_id == run_id).order_by(RunUnit.id)))
    units = {ru.unit_id: session.get(Unit, ru.unit_id) for ru in run_units}

    ws = wb.create_sheet("查詢條件")
    _append(ws, ["學校", "中大系所", "該校系所", "角色", "Scopus 查詢字串", "狀態", "篇數", "取得時間", "錯誤"], BOLD)
    for ru in run_units:
        u = units[ru.unit_id]
        ws.append([u.school, u.ncu_dept, u.dept_zh, ru.role, ru.query, ru.status, ru.eid_count,
                   ru.fetched_at.strftime("%Y-%m-%d %H:%M") if ru.fetched_at else "", ru.error or ""])

    ws = wb.create_sheet("逐篇指標")
    fields = ["year", "q1", "top1", "top10", "fwci", "intl_collab", "citations", "in_scival"]
    ws.append(["核對方式：用標題列的篩選選出學校、中大系所，再篩指標欄（q1、top1、top10、intl_collab）= TRUE，"
               "筆數即為該指標的篇數 A；fwci、citations 欄可直接加總。"])
    _append(ws, ["學校", "中大系所", "該校系所", "scopus_id", "eid", *fields], BOLD)
    for p in session.scalars(select(RunPublication).where(RunPublication.run_id == run_id)
                             .order_by(RunPublication.unit_id, RunPublication.id)):
        u = units[p.unit_id]
        d = json.loads(p.metrics_json or "{}")
        ws.append([u.school, u.ncu_dept, u.dept_zh, p.scopus_id, p.eid,
                   *(float(d["fwci"]) if f == "fwci" and d.get("fwci") is not None else d.get(f) for f in fields)])
    ws.auto_filter.ref = f"A2:{get_column_letter(5 + len(fields))}{max(ws.max_row, 2)}"
    ws.freeze_panes = "A3"

    ws = wb.create_sheet("教師數")
    _append(ws, ["學校", "中大系所", "該校系所", "教師數", "來源", "原因",
                 "教育部學年", "欄位", "系所代碼", "系所名稱", "該列人數"], BOLD)
    for ru in run_units:
        u = units[ru.unit_id]
        manual = ru.teacher_source.startswith("manual")
        base = [u.school, u.ncu_dept, u.dept_zh, PENDING if ru.teacher_count is None else ru.teacher_count,
                "人工修改" if manual else {"moe": "教育部", "missing": "缺資料"}.get(ru.teacher_source, ru.teacher_source),
                ru.teacher_source.split(":", 1)[1] if manual else ""]
        if not ru.teacher_detail_json:  # 舊分析沒有快照
            ws.append([*base, "", "", "未保存", "", ""])
            continue
        detail = json.loads(ru.teacher_detail_json)
        moe = [detail["academic_year"], TEACHER_COLUMNS.get(detail["column"], detail["column"])]
        for r in detail["rows"] or [dict(code="查無對應", name="", value=None)]:  # 對到多列就拆成多列
            ws.append([*base, *moe, r["code"], r["name"], r["value"]])

    ws = wb.create_sheet("待確認")
    _append(ws, ["類型", "學校", "中大系所", "說明", "狀態", "備註"], BOLD)
    for a in session.scalars(select(Anomaly).where(Anomaly.run_id == run_id).order_by(Anomaly.id)):
        u = units.get(a.unit_id)
        ws.append([KIND_LABELS.get(a.kind, a.kind), u.school if u else "", u.ncu_dept if u else "", a.detail,
                   "已確認" if a.status == "confirmed" else "待確認", a.resolution_note or ""])

    ws = wb.create_sheet("版本備註")
    rows = [
        ("分析編號", run.id), ("建立時間", run.created_at.strftime("%Y-%m-%d %H:%M")),
        ("期間", f"{run.period_from}-{run.period_to}"),
        ("比較範圍", "、".join(COMPARISON_LABELS[c] for c in sorted({g.comparison for g in groups}))),
        ("資料取得日期", notes.get("data_date", "")), ("資料來源", notes.get("source", "")),
        ("教師數", f"教育部公開資訊平台 {run.academic_year} 學年度"),
        ("排除自我引用", notes.get("self_citation", "")),
        *((f"指標重要性：{i.label}", (notes.get("importance") or {}).get(i.id, "")) for i in INDICATORS),
        ("補充說明", notes.get("extra", "")),
    ]
    for row in rows:
        ws.append(list(row))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path
