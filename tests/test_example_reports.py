"""對照：以 8 份範例報告驗證師均公式 A/5/B（設計 §13）。

範例數字本身有些不符合公式（未除以 5、截斷、來源不明），逐筆記錄在
fixtures/example_report_known_mismatches.json 並附原因，不當成失敗。
"""

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pptx import Presentation

REPORTS = sorted(Path("docs/產出範例-管理學院為例/管理學院").glob("*.pptx"))
KNOWN = Path(__file__).parent / "fixtures" / "example_report_known_mismatches.json"


def num(text: str) -> Decimal | None:
    try:
        return Decimal(text.strip().replace(",", ""))
    except InvalidOperation:
        return None


def checks(table):
    """回傳 (單位, 指標, A, B, 顯示值)。"""
    rows = [[c.text.strip() for c in r.cells] for r in table.rows]
    ncols = len(rows[0])
    if ncols == 8:      # 篇數頁：Q1 與總計
        cols = [("q1", 1, 5, 6), ("total", 3, 5, 7)]
    elif ncols == 4 and "教師數" in rows[0][2].replace(" ", ""):
        cols = [("per_faculty", 1, 2, 3)]
    else:
        return []
    out = []
    for r in rows:
        for name, a, b, v in cols:
            A, B, V = num(r[a]), num(r[b]), num(r[v])
            if A is not None and B not in (None, 0) and V is not None:
                out.append((r[0], name, A, B, V))
    return out


def find_mismatches():
    found = []
    for path in REPORTS:
        prs = Presentation(path)
        for page, slide in enumerate(prs.slides, 1):
            if page < 5:
                continue
            for shape in slide.shapes:
                if not shape.has_table:
                    continue
                for unit, name, A, B, V in checks(shape.table):
                    if abs(A / 5 / B - V) >= Decimal("0.01"):
                        found.append({"file": path.name, "page": page, "unit": unit, "indicator": name,
                                      "A": str(A), "B": str(B), "shown": str(V)})
    return found


def test_reports_present():
    assert len(REPORTS) == 8


def test_formula_matches_examples_except_known():
    known = json.loads(KNOWN.read_text(encoding="utf-8"))
    assert all(k.get("reason") for k in known)
    strip = lambda items: sorted(json.dumps({k: v for k, v in i.items() if k != "reason"}, ensure_ascii=False)
                                 for i in items)
    assert strip(find_mismatches()) == strip(known)
    assert len(known) <= 46
