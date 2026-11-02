"""每個系所一份簡報，依範例報告的結構產出（設計 §9.2）。只有表格與文字，中大列標紅。"""

import json
import math
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import groupby
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Pt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Anomaly, Run
from app.db.settings_store import get_setting
from app.elsevier.selfcite import get_strategy
from app.funding.service import count_text, funding_notes, funding_table, money
from app.mapping.parser import NCU_SHEET, is_ncu
from app.mapping.service import units_of
from app.metrics.indicators import BY_ID, importance_note
from app.reports.tables import IndicatorTable, Row, fmt, indicator_table, summary_table

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "report_template.pptx"
RED = RGBColor(0xFF, 0x00, 0x00)

# 版面格線（16:9，12192000 x 6858000 EMU）：標題列、說明列、表格名稱列、表格區由上而下不重疊
SLIDE_W, SLIDE_H = 12192000, 6858000
MARGIN, GAP = 259080, 275000
CONTENT_W = SLIDE_W - 2 * MARGIN
HALF_W = (CONTENT_W - GAP) // 2
LEFT_X, RIGHT_X = MARGIN, MARGIN + HALF_W + GAP
TITLE_Y, TITLE_H = 300000, 560000
INFO_Y, INFO_H = 900000, 720000
LABEL_Y, LABEL_H = 1640000, 400000
TABLE_Y = 2060000
SUMMARY_Y = 950000
BOTTOM = SLIDE_H - 150000

# 表格自動排版：由大到小試字級，直到欄寬放得下、總高度不超出版面
FONT_SIZES = (20, 18, 16, 14, 12, 11, 10, 9)
DEFAULT_MAX_SIZE = 16            # 一般表格最大 16pt；欄位少的表格（計畫經費）可指定到 20pt
FUNDING_ROW_MAX = 640000         # 計畫經費表列高上限：表格拉高填滿版面，但列數少時不會過高
FUNDING_COLS = (0.21, 0.085, 0.145, 0.145, 0.145, 0.13, 0.14)  # 計畫經費表欄寬比例（系所名稱最長、件數最短）
PAD_X, PAD_Y = 45720, 22860
LINE = 1.3                       # 行高 / 字級（中文字型行距較大，取保守值）
PT = 12700                       # EMU per point

TOC_ITEMS = [
    "    - 論文發表師均(QS/THE看總篇數、高教深耕看Q1)",
    "    - 論文被引次數達全球前1%論文篇數師均(THE排名/高教深耕指標)",
    "    - 論文被引次數達全球前10%論文篇數師均 (THE排名指標)",
    "    - FWCI (THE排名/高教深耕指標)",
    "    - 論文平均被引次數(排除自我引用) (QS排名指標)",
    "    - 國際合著論文師均(QS/THE/高教深耕指標)",
]


def _text(slide, text: str, left, top, width, height, size=Pt(14), color=None, bold=False, align=None):
    box = slide.shapes.add_textbox(Emu(left), Emu(top), Emu(width), Emu(height))
    frame = box.text_frame
    frame.word_wrap = True
    for i, line in enumerate(text.split("\n")):
        para = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
        if align is not None:
            para.alignment = align
        run = para.add_run()
        run.text = line
        run.font.size, run.font.bold = size, bold
        if color is not None:
            run.font.color.rgb = color
    return box


def _em(text: str) -> float:
    """估計字寬（以字級為單位）：全形字 1，半形字 0.6。"""
    return sum(0.6 if ord(ch) < 0x2E80 else 1.0 for ch in text)


@dataclass
class _Fit:
    size: int
    widths: list[int]
    heights: list[int]
    ok: bool


def _spans(merges) -> dict[tuple[int, int], tuple[int, int]]:
    return {(r0, c0): (r1 - r0 + 1, c1 - c0 + 1) for (r0, c0), (r1, c1) in merges}


def _fit(rows: list[list[str]], header_rows: int, merges, width: int, max_height: int,
         max_size: int = DEFAULT_MAX_SIZE) -> _Fit:
    """挑最大可用字級：資料格不斷行（單位欄依 \\n 斷行），表頭可斷行；欄寬按內容分配。"""
    spans = _spans(merges)
    covered = {(r, c) for (r0, c0), (rs, cs) in spans.items()
               for r in range(r0, r0 + rs) for c in range(c0, c0 + cs)} - set(spans)
    ncols = len(rows[0])
    # 多出來的寬度依表頭字數分配，表頭較長的欄位少斷行
    weight = [1 + sum(_em(rows[r][c]) for r in range(header_rows)
                      if (r, c) not in covered and spans.get((r, c), (1, 1))[1] == 1) for c in range(ncols)]
    sizes = [size for size in FONT_SIZES if size <= max_size]
    for size in sizes:
        def em(r):
            return size * PT * (1.05 if r < header_rows else 1.0)   # 表頭粗體略寬

        need = [0] * ncols
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                if (r, c) in covered or spans.get((r, c), (1, 1))[1] > 1:
                    continue
                parts = re.findall(r"[!-~]+|\S", text) if r < header_rows else text.split("\n")
                w = max((_em(p) for p in parts), default=0) * em(r)
                need[c] = max(need[c], int(w) + 2 * PAD_X)
        spare = width - sum(need)
        if spare >= 0:
            widths = [n + int(spare * w / sum(weight)) for n, w in zip(need, weight)]
        else:
            widths = [n * width // sum(need) for n in need]
        widths[-1] += width - sum(widths)

        lines = [1] * len(rows)
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                if (r, c) in covered:
                    continue
                rs, cs = spans.get((r, c), (1, 1))
                inner = max(sum(widths[c:c + cs]) - 2 * PAD_X, 1)
                n = sum(max(1, math.ceil(_em(p) * em(r) / inner)) for p in text.split("\n"))
                for rr in range(r, r + rs):
                    lines[rr] = max(lines[rr], math.ceil(n / rs))
        heights = [int(n * size * PT * LINE) + 2 * PAD_Y for n in lines]
        ok = spare >= 0 and sum(heights) <= max_height
        if ok or size == sizes[-1]:
            return _Fit(size, widths, heights, ok)


def _table(slide, rows: list[list[str]], left, top, width, max_height, header_rows=1, merges=(),
           red_rows=(), red_cells=(), max_size=DEFAULT_MAX_SIZE):
    fit = _fit(rows, header_rows, merges, width, max_height, max_size)
    shape = slide.shapes.add_table(len(rows), len(rows[0]), Emu(left), Emu(top), Emu(width), Emu(sum(fit.heights)))
    table = shape.table
    for c, w in enumerate(fit.widths):
        table.columns[c].width = Emu(w)
    for r, h in enumerate(fit.heights):
        table.rows[r].height = Emu(h)
    for r, values in enumerate(rows):
        for c, value in enumerate(values):
            cell = table.cell(r, c)
            cell.text = value
            cell.margin_left = cell.margin_right = Emu(PAD_X)
            cell.margin_top = cell.margin_bottom = Emu(PAD_Y)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            for para in cell.text_frame.paragraphs:
                para.alignment = PP_ALIGN.LEFT if c == 0 else PP_ALIGN.CENTER
                for run in para.runs:
                    run.font.size = Pt(fit.size)
                    run.font.bold = r < header_rows
                    if r in red_rows or (r, c) in red_cells:
                        run.font.color.rgb = RED
    for (r0, c0), (r1, c1) in merges:
        table.cell(r0, c0).merge(table.cell(r1, c1))
    return table


def _label(row: Row, comparison: str) -> str:
    """對標表的「系所-校名」拆兩行，單位欄才不會被擠得很寬或在字中間斷行。"""
    dept, _, school = row.label.rpartition("-")
    return f"{dept}\n{school}" if comparison == "benchmark" and dept else row.label


def _teachers(row: Row) -> str:
    if row.teachers is None:
        return "待確認"
    return f"{row.teachers}{'*' if row.teacher_manual else ''}"


def _count(value: Decimal | None) -> str:
    return "待確認" if value is None else str(int(value))


class ReportBuilder:
    def __init__(self, session: Session, run_id: int, ncu_dept: str):
        self.session, self.run_id, self.ncu_dept = session, run_id, ncu_dept
        self.run = session.get(Run, run_id)
        self.notes = json.loads(self.run.notes_json or "{}")
        unit = next(u for u in units_of(session, self.run.mapping_version_id)
                    if is_ncu(u.school) and u.ncu_dept == ncu_dept)
        self.unit_id, self.college = unit.id, unit.ncu_college or ""
        self.period = f"{self.run.period_from}-{self.run.period_to}"
        self.years = self.run.period_to - self.run.period_from + 1
        self.open_anomalies = session.scalar(select(func.count()).select_from(Anomaly).where(
            Anomaly.run_id == run_id, Anomaly.status != "confirmed"))
        self.prs = Presentation(TEMPLATE)
        self.layouts = {layout.name: layout for layout in self.prs.slide_layouts}
        # 有經分表資料時，第 I 部分是計畫經費，論文指標順延為 II、III（範例報告的編號）
        self.funding = funding_table(session, self.run, self.college)
        self.sections = ("II", "III") if self.funding.has_data else ("I", "II")

    # ---- 共用 ----
    def _slide(self, title: str, layout: str = "只有標題"):
        slide = self.prs.slides.add_slide(self.layouts[layout])
        shape = slide.shapes.title
        shape.left, shape.top, shape.width, shape.height = Emu(MARGIN), Emu(TITLE_Y), Emu(CONTENT_W), Emu(TITLE_H)
        shape.text = title
        frame = shape.text_frame
        frame.word_wrap, frame.vertical_anchor = True, MSO_ANCHOR.MIDDLE
        frame.paragraphs[0].alignment = PP_ALIGN.LEFT
        for run in frame.paragraphs[0].runs:
            run.font.size, run.font.bold = Pt(30), True
        if self.open_anomalies:
            _text(slide, f"※本報告含 {self.open_anomalies} 項待人工確認資料", SLIDE_W - MARGIN - 4000000, 10000,
                  4000000, 280000, Pt(12), RED, True, PP_ALIGN.RIGHT)
        return slide

    def _notes(self, slide, importance: str, *extra: str):
        """說明列：左邊是排名指標，右邊是資料註腳。"""
        _text(slide, importance, LEFT_X, INFO_Y, HALF_W, INFO_H, Pt(12))
        notes = [f"註1：單年教師數來自教育部公開資訊平台（{self.run.academic_year}學年度）；標 * 者為人工修改。",
                 f"註2：資料來源 Scopus、SciVal，資料取得日期 {self.notes.get('data_date', '')}。", *extra]
        _text(slide, "\n".join(notes), RIGHT_X, INFO_Y, HALF_W, INFO_H, Pt(10))

    def _pair(self, slide, render, indicator_id: str, sort_by: str | None = None, header_rows=1, merges=()):
        """左：對標學校比較；右：院內系所比較。render(table) 回傳含表頭的列；兩表用同一字級，本系所列標紅。"""
        tables = []
        for x, comparison, key, label in (
                (LEFT_X, "benchmark", self.ncu_dept, "對標學校比較"),
                (RIGHT_X, "college", self.college, f"{self.college}內系所比較")):
            table = indicator_table(self.session, self.run_id, comparison, key, indicator_id, sort_by)
            if table.rows:
                red = [header_rows + i for i, r in enumerate(table.rows) if r.unit_id == self.unit_id]
                tables.append((x, label, render(table), red))
        max_height = BOTTOM - TABLE_Y
        size = min((_fit(rows, header_rows, merges, HALF_W, max_height).size for _, _, rows, _ in tables),
                   default=DEFAULT_MAX_SIZE)
        for x, label, rows, red in tables:
            _text(slide, label, x, LABEL_Y, HALF_W, LABEL_H, Pt(16), bold=True)
            _table(slide, rows, x, TABLE_Y, HALF_W, max_height, header_rows, merges, red, max_size=size)

    # ---- 各頁 ----
    def cover(self):
        slide = self.prs.slides.add_slide(self.layouts["標題投影片"])
        slide.shapes.title.text = f"{NCU_SHEET}{self.college}-\v{self.ncu_dept}"
        slide.placeholders[1].text = date.today().strftime("%Y/%m/%d")

    def toc(self):
        slide = self.prs.slides.add_slide(self.layouts["標題及內容"])
        slide.shapes.title.text = "目錄"
        summary_no, detail_no = self.sections
        lines = [f"{summary_no}. {self.college}各系所論文指標於對標學校排名", f"{detail_no}. {self.ncu_dept}各指標比較",
                 *TOC_ITEMS]
        if self.funding.has_data:
            lines.insert(0, f"I. {self.college}國科會及產學計畫經費年平均")
        frame = slide.placeholders[1].text_frame
        frame.text = lines[0]
        for line in lines[1:]:
            frame.add_paragraph().text = line

    def funding_page(self):
        """範例第 3 頁：學院各系所國科會及產學計畫經費年平均（資料來自經分表），本系所列標紅。"""
        t = self.funding
        slide = self._slide(f"I. {self.college}國科會及產學計畫經費年平均")
        n = len(t.years)
        # 表頭附公式代號（與論文指標頁的「單年師均 (A/5/B)」相同寫法）
        # 表頭手動分兩行，窄欄才不會被擠成一字一行
        rows = [["系所", "國科會\n件數" + ("(年平均)" if n > 1 else ""), "國科會\n年平均 (A)", "產學\n年平均 (B)",
                 "加總\n(C=A+B)", "單年教師數\n(D)", "師均經費\n(C/D)"]]
        red = []
        for r in t.rows:
            if r.unit_id == self.unit_id:
                red.append(len(rows))
            rows.append([r.ncu_dept, count_text(r.nstc_count_avg), money(r.nstc_avg), money(r.industry_avg),
                         money(r.total_avg), _teachers(r), money(r.per_faculty)])
        # 表格緊接標題，下方留給公式與註腳
        top, gap = INFO_Y, 100000
        area = BOTTOM - INFO_H - gap - top
        table = _table(slide, rows, MARGIN, top, CONTENT_W, area, red_rows=red, max_size=FONT_SIZES[0])
        # 固定欄寬比例：自動分配會把只有數字的件數欄擠得太窄，表頭變成一字一行
        size = table.cell(0, 0).text_frame.paragraphs[0].runs[0].font.size.pt
        for c, ratio in enumerate(FUNDING_COLS):
            table.columns[c].width = Emu(int(CONTENT_W * ratio))
        last = len(FUNDING_COLS) - 1
        table.columns[last].width = Emu(CONTENT_W - sum(table.columns[c].width for c in range(last)))
        header_lines = max(len(text.split("\n")) for text in rows[0])
        table.rows[0].height = Emu(int(header_lines * size * PT * LINE) + 2 * PAD_Y)
        # 系所少時表格只佔上方一小塊：把多餘的高度平均分給資料列，填到說明文字上方為止（每列有上限）
        body = list(table.rows)[1:]
        spare = area - sum(row.height for row in table.rows)
        if body and spare > 0:
            for row in body:
                row.height = Emu(min(row.height + spare // len(body), max(row.height, FUNDING_ROW_MAX)))
        # 表格下方：左邊是計算公式，右邊是資料註腳
        note_y = top + sum(row.height for row in table.rows) + gap
        formula = (f"A = 國科會計畫經費加總 ÷ {n}（年度數）\n"
                   f"B = 產學合作計畫經費加總 ÷ {n}（年度數）\n"
                   "C = A + B；師均經費 = C ÷ D（單年教師數）")
        _text(slide, formula, LEFT_X, note_y, HALF_W, INFO_H, Pt(14))
        _text(slide, "\n".join(funding_notes(t)), RIGHT_X, note_y, HALF_W, INFO_H, Pt(10))

    def summary(self):
        highlight = int(get_setting(self.session, "rank_highlight_top"))
        header = [["單位", "對外/對內項目", "優質期刊", f"{self.years}年論文被引次數", "", "", "", "國際合著篇數 (師均)"],
                  ["", "", "Q1師均", "Top1%篇數 (師均)", "Top10%篇數 (師均)", "FWCI", "平均論文被引次數", ""]]
        merges = [((0, 0), (1, 0)), ((0, 1), (1, 1)), ((0, 3), (0, 6)), ((0, 7), (1, 7))]
        max_height = BOTTOM - SUMMARY_Y

        def body(summaries):
            rows, red = [], set()
            for s in summaries:
                label = f"對標學校: {s.group_size}間" if s.comparison == "benchmark" else f"{self.college}內"
                for c, v in enumerate(s.ranks.values(), start=2):
                    if v is not None and v <= highlight:
                        red.add((len(header) + len(rows), c))
                rows.append([s.dept_label, label, *("待確認" if v is None else f"#{v}" for v in s.ranks.values())])
            return header + rows, red

        # 系所多到一頁放不下時分頁，同一系所的對標/院內兩列不拆開
        pages = [[]]
        for _, group in groupby(summary_table(self.session, self.run_id, self.college), key=lambda s: s.dept_label):
            group = list(group)
            if pages[-1] and not _fit(body(pages[-1] + group)[0], 2, merges, CONTENT_W, max_height).ok:
                pages.append(group)
            else:
                pages[-1] += group
        for i, page in enumerate(pages):
            slide = self._slide(f"{self.sections[0]}、{self.college}各系所論文指標排名" + ("（續）" if i else ""))
            rows, red = body(page)
            _table(slide, rows, MARGIN, SUMMARY_Y, CONTENT_W, max_height, 2, merges, red_cells=red)

    def pubs(self):
        slide = self._slide(f"{self.sections[1]}. {self.period}論文發表篇數師均")
        self._notes(slide, f"{importance_note(BY_ID['q1'])}（Q1）　{importance_note(BY_ID['total'])}（論文發表篇數）",
                    "註3：單位排序以Q1單年師均數字高到低排序。")

        def render(t: IndicatorTable):
            total = {r.unit_id: r for r in indicator_table(self.session, self.run_id, t.comparison, t.key,
                                                           "total").rows}
            rows = [["單位", "Q1", "", "總計", "", "單年教師數(C)", "單年師均", ""],
                    ["", f"{self.years}年 總數", f"{self.years}年 平均 (A)", f"{self.years}年 總數",
                     f"{self.years}年 平均(B)", "", "Q1 (A/C)", "總計 (B/C)"]]
            for r in t.rows:
                tot = total[r.unit_id]
                rows.append([_label(r, t.comparison), _count(r.raw), fmt(r.five_year_avg), _count(tot.raw),
                             fmt(tot.five_year_avg), _teachers(r), fmt(r.value), fmt(tot.value)])
            return rows

        merges = [((0, 0), (1, 0)), ((0, 1), (0, 2)), ((0, 3), (0, 4)), ((0, 5), (1, 5)), ((0, 6), (0, 7))]
        self._pair(slide, render, "q1", sort_by="q1", header_rows=2, merges=merges)

    def count_page(self, indicator_id: str, title: str):
        slide = self._slide(f"{self.period} {title}")
        self._notes(slide, importance_note(BY_ID[indicator_id]))

        def render(t: IndicatorTable):
            n = self.years
            return [["單位", f"{n}年篇數 (A)", "單年教師數(B)", f"單年師均 (A/{n}/B)"],
                    *([_label(r, t.comparison), _count(r.raw), _teachers(r), fmt(r.value)] for r in t.rows)]

        self._pair(slide, render, indicator_id)

    def value_page(self, indicator_id: str, title: str, header: str, *extra: str):
        slide = self._slide(f"{self.period}{title}")
        self._notes(slide, importance_note(BY_ID[indicator_id]), *extra)

        def render(t: IndicatorTable):
            return [["單位", header], *([_label(r, t.comparison), fmt(r.value)] for r in t.rows)]

        self._pair(slide, render, indicator_id)

    def build(self, out_path: Path) -> Path:
        self.cover()
        self.toc()
        if self.funding.has_data:
            self.funding_page()
        self.summary()
        self.pubs()
        self.count_page("top1", "被引數達全球前1%篇數(師均)")
        self.count_page("top10", "被引數達全球前10%篇數(師均)")
        self.value_page("fwci", " FWCI", "FWCI")
        strategy = get_strategy(self.run.self_citation_strategy)
        extra = f"排除自我引用方式：{strategy.label}" + ("（待驗證）" if not strategy.verified else "")
        self.value_page("avg_citations", "平均論文被引次數(排除自我引用)", "平均論文被引次數(排除自我引用)", extra)
        self.count_page("intl_collab", "國際合著篇數(師均)")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        self.prs.save(out_path)
        return out_path


def build_dept_report(session: Session, run_id: int, ncu_dept: str, out_path: Path) -> Path:
    return ReportBuilder(session, run_id, ncu_dept).build(out_path)


def report_filename(session: Session, run_id: int, ncu_dept: str) -> str:
    b = ReportBuilder(session, run_id, ncu_dept)
    return f"{b.period}{b.college}{b.ncu_dept}論文表現分析.pptx"
