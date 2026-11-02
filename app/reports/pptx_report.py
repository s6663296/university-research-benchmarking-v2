"""每個系所一份簡報，依範例報告的結構產出（設計 §9.2）。只有表格與文字，本系所列標紅。

所有頁面套用同一套版面（16:9）：
  頂部　章節標籤（例如「III. 資訊管理學系各指標比較」）＋ 頁面標題 ＋ 細線 ＋ 灰色副標（指標重要性）
  中段　表格（深藍表頭、淺灰間隔列、本系所紅字），由上往下拉高填滿版面
  底部　左：計算方式（公式），右：資料來源註腳
  頁尾　報告名稱與頁碼
各區塊由上而下排列、互不重疊（測試會檢查）。
"""

import json
import math
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import groupby
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
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

# 配色與字型
FONT = "Microsoft JhengHei"      # 微軟正黑體（中英文同一字型）
NAVY = RGBColor(0x1F, 0x38, 0x64)
BLUE = RGBColor(0x2E, 0x75, 0xB6)
GRAY = RGBColor(0x59, 0x59, 0x59)
LIGHT = RGBColor(0xF2, 0xF2, 0xF2)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
RED = RGBColor(0xFF, 0x00, 0x00)

# 版面格線（16:9，12192000 x 6858000 EMU）：由上而下各區塊不重疊
SLIDE_W, SLIDE_H = 12192000, 6858000
MARGIN, GAP = 380000, 300000
CONTENT_W = SLIDE_W - 2 * MARGIN
HALF_W = (CONTENT_W - GAP) // 2
LEFT_X, RIGHT_X = MARGIN, MARGIN + HALF_W + GAP
KICKER_Y, KICKER_H = 200000, 280000          # 章節標籤
TITLE_Y, TITLE_H = 500000, 530000            # 頁面標題
RULE_Y, RULE_H = 1060000, 25400              # 標題下細線
SUB_Y, SUB_H = 1110000, 300000               # 副標（指標重要性）
LABEL_Y, LABEL_H = 1460000, 330000           # 「對標學校比較」等表格名稱
TABLE_Y = 1830000                            # 左右兩表
SUMMARY_Y = 1460000                          # 單一大表（排名總表、計畫經費）
NOTE_Y, NOTE_H = 5540000, 830000             # 底部：計算方式＋資料註腳
BOTTOM = NOTE_Y - 70000                      # 表格不可超過
FOOTER_RULE_Y = 6420000
FOOTER_Y, FOOTER_H = 6450000, 260000

# 表格自動排版：由大到小試字級，直到欄寬放得下、總高度不超出版面
FONT_SIZES = (20, 18, 16, 14, 12, 11, 10, 9)
DEFAULT_MAX_SIZE = 16            # 一般表格最大 16pt；欄位少的表格（計畫經費）可指定到 20pt
ROW_MAX = 520000                 # 表格拉高填滿版面時，每列高度上限（列數少時不會過高）
FUNDING_ROW_MAX = 640000
FUNDING_COLS = (0.23, 0.08, 0.14, 0.14, 0.14, 0.13, 0.14)  # 計畫經費表欄寬比例（系所名稱最長、件數最短）
PAD_X, PAD_Y = 64000, 22860
LINE = 1.3                       # 行高 / 字級（中文字型行距較大，取保守值）
WIDTH_SAFETY = 1.1               # 字寬估計的安全係數：實際字型略寬於估計，避免剛好放得下卻被換行
PT = 12700                       # EMU per point

TOC_ITEMS = [
    "論文發表師均（QS / THE 看總篇數、高教深耕看 Q1）",
    "論文被引次數達全球前 1% 論文篇數師均（THE 排名、高教深耕指標）",
    "論文被引次數達全球前 10% 論文篇數師均（THE 排名指標）",
    "FWCI（THE 排名、高教深耕指標）",
    "論文平均被引次數，排除自我引用（QS 排名指標）",
    "國際合著論文師均（QS / THE / 高教深耕指標）",
]


def _font(run, size=None, bold=None, color=None):
    """統一字型：中英文都用微軟正黑體。顏色要在字型之前設定（XML 元素順序）。"""
    f = run.font
    if size is not None:
        f.size = size
    if bold is not None:
        f.bold = bold
    if color is not None:
        f.color.rgb = color
    f.name = FONT
    rpr = run._r.get_or_add_rPr()
    if rpr.find(qn("a:ea")) is None:
        ea = etree.SubElement(rpr, qn("a:ea"))
        ea.set("typeface", FONT)
        rpr.find(qn("a:latin")).addnext(ea)


def _text(slide, text: str, left, top, width, height, size=Pt(14), color=GRAY, bold=False, align=None,
          anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(Emu(left), Emu(top), Emu(width), Emu(height))
    frame = box.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = anchor
    frame.margin_left = frame.margin_right = 0
    for i, line in enumerate(text.split("\n")):
        para = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
        if align is not None:
            para.alignment = align
        run = para.add_run()
        run.text = line
        _font(run, size, bold, color)
    return box


def _rect(slide, left, top, width, height, color):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(left), Emu(top), Emu(width), Emu(height))
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


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
            return size * PT * WIDTH_SAFETY * (1.05 if r < header_rows else 1.0)   # 表頭粗體略寬

        need = [0] * ncols
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                if (r, c) in covered or spans.get((r, c), (1, 1))[1] > 1:
                    continue
                if r < header_rows and "\n" not in text:
                    parts = re.findall(r"[!-~]+|\S", text)
                else:
                    parts = text.split("\n")
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


def _fit_fixed(rows: list[list[str]], header_rows: int, widths: list[int], max_height: int, max_size: int) -> _Fit:
    """欄寬固定時挑最大字級：每格（依手動換行分成多行）都放得下欄寬，且總高度不超出版面。"""
    sizes = [size for size in FONT_SIZES if size <= max_size]
    for size in sizes:
        unit = size * PT * WIDTH_SAFETY
        fits = all(_em(line) * unit * (1.05 if r < header_rows else 1.0) + 2 * PAD_X <= widths[c]
                   for r, row in enumerate(rows) for c, text in enumerate(row) for line in text.split("\n"))
        heights = [int(max(len(t.split("\n")) for t in row) * size * PT * LINE) + 2 * PAD_Y for row in rows]
        if (fits and sum(heights) <= max_height) or size == sizes[-1]:
            return _Fit(size, list(widths), heights, fits)


def _table(slide, rows: list[list[str]], left, top, width, max_height, header_rows=1, merges=(),
           red_rows=(), red_cells=(), max_size=DEFAULT_MAX_SIZE, row_max=ROW_MAX, ratios=None):
    """表頭深藍白字、內容淺灰間隔列；字級自動挑最大可用，再把多餘高度分給內容列（每列有上限）。
    ratios 指定欄寬比例時以固定欄寬排版（否則依內容分配欄寬）。"""
    if ratios:
        widths = [int(width * r) for r in ratios]
        widths[-1] += width - sum(widths)
        fit = _fit_fixed(rows, header_rows, widths, max_height, max_size)
    else:
        fit = _fit(rows, header_rows, merges, width, max_height, max_size)
    shape = slide.shapes.add_table(len(rows), len(rows[0]), Emu(left), Emu(top), Emu(width), Emu(sum(fit.heights)))
    table = shape.table
    table.first_row = True
    table.horz_banding = False
    for c, w in enumerate(fit.widths):
        table.columns[c].width = Emu(w)
    for r, h in enumerate(fit.heights):
        table.rows[r].height = Emu(h)
    for r, values in enumerate(rows):
        header = r < header_rows
        for c, value in enumerate(values):
            cell = table.cell(r, c)
            cell.text = value
            cell.margin_left = cell.margin_right = Emu(PAD_X)
            cell.margin_top = cell.margin_bottom = Emu(PAD_Y)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.fill.solid()
            cell.fill.fore_color.rgb = NAVY if header else (LIGHT if (r - header_rows) % 2 else WHITE)
            for para in cell.text_frame.paragraphs:
                para.alignment = PP_ALIGN.LEFT if c == 0 and not header else PP_ALIGN.CENTER
                for run in para.runs:
                    red = r in red_rows or (r, c) in red_cells
                    _font(run, Pt(fit.size), header or (red and r in red_rows),
                          WHITE if header else (RED if red else RGBColor(0x26, 0x26, 0x26)))
    for (r0, c0), (r1, c1) in merges:
        table.cell(r0, c0).merge(table.cell(r1, c1))
    _stretch(table, header_rows, max_height, row_max)
    return table


def _stretch(table, header_rows: int, max_height: int, row_max: int) -> None:
    """列數少時表格只佔上方一小塊：把多餘高度平均分給內容列，不超過 max_height，每列不超過 row_max。"""
    body = list(table.rows)[header_rows:]
    spare = max_height - sum(row.height for row in table.rows)
    if body and spare > 0:
        for row in body:
            row.height = Emu(min(row.height + spare // len(body), max(row.height, row_max)))


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
        nums = ("II", "III") if self.funding.has_data else ("I", "II")
        self.kickers = {
            "funding": "I. 國科會及產學計畫經費",
            "summary": f"{nums[0]}. 論文指標排名總覽",
            "detail": f"{nums[1]}. {self.ncu_dept}各指標比較",
        }
        self.sections = {
            "funding": f"I. {self.college}國科會及產學計畫經費年平均",
            "summary": f"{nums[0]}. {self.college}各系所論文指標於對標學校排名",
            "detail": f"{nums[1]}. {self.ncu_dept}各指標比較",
        }

    # ---- 共用 ----
    def _slide(self, title: str, kicker: str = "", subtitle: str = ""):
        """內容頁：章節標籤、標題、細線、副標；有待確認資料時右上角標示。"""
        slide = self.prs.slides.add_slide(self.layouts["只有標題"])
        banner_w = 4200000 if self.open_anomalies else 0
        if kicker:
            _text(slide, kicker, MARGIN, KICKER_Y, CONTENT_W - banner_w - 100000, KICKER_H, Pt(12), BLUE, True,
                  anchor=MSO_ANCHOR.MIDDLE)
        if self.open_anomalies:
            _text(slide, f"※本報告含 {self.open_anomalies} 項待人工確認資料", SLIDE_W - MARGIN - banner_w, KICKER_Y,
                  banner_w, KICKER_H, Pt(11), RED, True, PP_ALIGN.RIGHT, MSO_ANCHOR.MIDDLE)
        shape = slide.shapes.title
        shape.left, shape.top, shape.width, shape.height = Emu(MARGIN), Emu(TITLE_Y), Emu(CONTENT_W), Emu(TITLE_H)
        shape.text = title
        frame = shape.text_frame
        frame.word_wrap, frame.vertical_anchor = True, MSO_ANCHOR.MIDDLE
        frame.margin_left = frame.margin_right = 0
        frame.paragraphs[0].alignment = PP_ALIGN.LEFT
        for run in frame.paragraphs[0].runs:
            _font(run, Pt(26), True, NAVY)
        _rect(slide, MARGIN, RULE_Y, CONTENT_W, RULE_H, BLUE)
        if subtitle:
            _text(slide, subtitle, MARGIN, SUB_Y, CONTENT_W, SUB_H, Pt(12), GRAY, anchor=MSO_ANCHOR.MIDDLE)
        return slide

    def _bottom(self, slide, formula: str, *extra: str, notes: list[str] | None = None):
        """底部：左邊是計算方式，右邊是資料註腳。"""
        if formula:
            _text(slide, "計算方式\n" + formula, LEFT_X, NOTE_Y, HALF_W, NOTE_H, Pt(10.5), GRAY)
            for run in slide.shapes[-1].text_frame.paragraphs[0].runs:
                _font(run, bold=True, color=NAVY)
        if notes is None:
            notes = [f"註1：單年教師數來自教育部公開資訊平台（{self.run.academic_year}學年度）；標 * 者為人工修改。",
                     f"註2：資料來源 Scopus、SciVal，資料取得日期 {self.notes.get('data_date', '')}。"]
        _text(slide, "\n".join([*notes, *extra]), RIGHT_X, NOTE_Y, HALF_W, NOTE_H, Pt(9.5), GRAY)

    def _label(self, slide, x, text: str):
        """表格名稱：左側藍色短條＋粗體字。"""
        _rect(slide, x, LABEL_Y + 70000, 50000, LABEL_H - 140000, BLUE)
        _text(slide, text, x + 120000, LABEL_Y, HALF_W - 120000, LABEL_H, Pt(15), NAVY, True,
              anchor=MSO_ANCHOR.MIDDLE)

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
            self._label(slide, x, label)
            _table(slide, rows, x, TABLE_Y, HALF_W, max_height, header_rows, merges, red, max_size=size)

    def _footer(self):
        """每頁（封面除外）頁尾：細線、報告名稱、頁碼。最後統一加上才知道總頁數。"""
        slides = list(self.prs.slides)
        name = f"{NCU_SHEET} {self.college} {self.ncu_dept}｜{self.period} 論文表現分析"
        for n, slide in enumerate(slides[1:], start=2):
            _rect(slide, MARGIN, FOOTER_RULE_Y, CONTENT_W, 9525, RGBColor(0xBF, 0xBF, 0xBF))
            _text(slide, name, MARGIN, FOOTER_Y, CONTENT_W - 1300000, FOOTER_H, Pt(9), GRAY,
                  anchor=MSO_ANCHOR.MIDDLE)
            _text(slide, f"{n} / {len(slides)}", SLIDE_W - MARGIN - 1100000, FOOTER_Y, 1100000, FOOTER_H, Pt(9),
                  GRAY, align=PP_ALIGN.RIGHT, anchor=MSO_ANCHOR.MIDDLE)

    # ---- 各頁 ----
    def cover(self):
        """封面：深藍底、白字標題，下方為報告期間與產出日期。"""
        slide = self.prs.slides.add_slide(self.layouts["標題投影片"])
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = NAVY
        _text(slide, "員額小組論文資料", 1000000, 1450000, 8000000, 380000, Pt(16), RGBColor(0xBD, 0xD7, 0xEE), True)
        title = slide.shapes.title
        title.left, title.top, title.width, title.height = Emu(1000000), Emu(1900000), Emu(10200000), Emu(1700000)
        title.text = f"{NCU_SHEET}{self.college}-\v{self.ncu_dept}"
        frame = title.text_frame
        frame.word_wrap, frame.vertical_anchor = True, MSO_ANCHOR.TOP
        for para in frame.paragraphs:
            para.alignment = PP_ALIGN.LEFT
            for run in para.runs:
                _font(run, Pt(40), True, WHITE)
        _rect(slide, 1000000, 3750000, 1600000, 50000, RGBColor(0x9D, 0xC3, 0xE6))
        sub = slide.placeholders[1]
        sub.left, sub.top, sub.width, sub.height = Emu(1000000), Emu(3950000), Emu(10200000), Emu(1100000)
        sub.text = f"{self.period} 論文表現分析\n{date.today().strftime('%Y/%m/%d')}"
        for i, para in enumerate(sub.text_frame.paragraphs):
            para.alignment = PP_ALIGN.LEFT
            for run in para.runs:
                _font(run, Pt(22 if i == 0 else 16), i == 0, WHITE if i == 0 else RGBColor(0xBD, 0xD7, 0xEE))

    def toc(self):
        slide = self._slide("目錄")
        sections = [self.sections["summary"], self.sections["detail"]]
        if self.funding.has_data:
            sections.insert(0, self.sections["funding"])
        box = slide.shapes.add_textbox(Emu(MARGIN + 200000), Emu(SUB_Y + 100000), Emu(CONTENT_W - 400000),
                                       Emu(FOOTER_RULE_Y - SUB_Y - 250000))
        frame = box.text_frame
        frame.word_wrap = True
        first = True
        for i, section in enumerate(sections):
            para = frame.paragraphs[0] if first else frame.add_paragraph()
            first = False
            para.space_before = Pt(14 if i else 0)
            _font(para.add_run(), Pt(20), True, NAVY)
            para.runs[0].text = section
            if i == len(sections) - 1:   # 最後一節（本系所各指標）列出各指標
                for item in TOC_ITEMS:
                    sub = frame.add_paragraph()
                    sub.level = 1
                    sub.space_before = Pt(4)
                    run = sub.add_run()
                    run.text = f"・{item}"
                    _font(run, Pt(15), False, GRAY)

    def funding_page(self):
        """範例第 3 頁：學院各系所國科會及產學計畫經費年平均（資料來自經分表），本系所列標紅。"""
        t = self.funding
        n = len(t.years)
        slide = self._slide(f"{self.college}國科會及產學計畫經費年平均", self.kickers["funding"],
                            f"資料來源：計畫管理系統經分表（{'、'.join(map(str, t.years))} 年度）；以計畫主持人主聘單位歸屬系所")
        # 表頭附公式代號、手動分兩行，窄欄才不會被擠成一字一行
        rows = [["系所", "國科會\n件數" + ("(年平均)" if n > 1 else ""), "國科會\n年平均 (A)", "產學\n年平均 (B)",
                 "加總\n(C=A+B)", "單年教師數\n(D)", "師均經費\n(C/D)"]]
        red = []
        for r in t.rows:
            if r.unit_id == self.unit_id:
                red.append(len(rows))
            rows.append([r.ncu_dept, count_text(r.nstc_count_avg), money(r.nstc_avg), money(r.industry_avg),
                         money(r.total_avg), _teachers(r), money(r.per_faculty)])
        # 固定欄寬比例：自動分配會把只有數字的件數欄擠得太窄
        _table(slide, rows, MARGIN, SUMMARY_Y, CONTENT_W, BOTTOM - SUMMARY_Y, red_rows=red, max_size=FONT_SIZES[0],
               row_max=FUNDING_ROW_MAX, ratios=FUNDING_COLS)
        self._bottom(slide, f"A = 國科會計畫經費加總 ÷ {n}（年度數）\n"
                            f"B = 產學合作計畫經費加總 ÷ {n}（年度數）\n"
                            "C = A + B；師均經費 = C ÷ D（單年教師數）", notes=funding_notes(t))

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
            slide = self._slide(f"{self.college}各系所論文指標排名" + ("（續）" if i else ""), self.kickers["summary"],
                                f"各指標在比較群組內的名次（#1 為最高），名次 ≤ {highlight} 以紅字標示")
            rows, red = body(page)
            _table(slide, rows, MARGIN, SUMMARY_Y, CONTENT_W, max_height, 2, merges, red_cells=red)
            self._bottom(slide, "名次：在各比較群組內依指標數值由高到低排序，同值同名次\n"
                                "對標學校: N間 = 含中央在內、該系所對標比較的單位數\n"
                                f"{self.college}內 = 與{self.college}內系所比較")

    def pubs(self):
        n = self.years
        slide = self._slide(f"{self.period} 論文發表篇數師均", self.kickers["detail"],
                            f"{importance_note(BY_ID['q1'])}（Q1）　{importance_note(BY_ID['total'])}（論文發表篇數）")

        def render(t: IndicatorTable):
            total = {r.unit_id: r for r in indicator_table(self.session, self.run_id, t.comparison, t.key,
                                                           "total").rows}
            rows = [["單位", "Q1", "", "總計", "", "單年\n教師數(C)", "單年師均", ""],
                    ["", f"{n}年\n總數", f"{n}年平均\n(A)", f"{n}年\n總數", f"{n}年平均\n(B)", "", "Q1\n(A/C)",
                     "總計\n(B/C)"]]
            for r in t.rows:
                tot = total[r.unit_id]
                rows.append([_label(r, t.comparison), _count(r.raw), fmt(r.five_year_avg), _count(tot.raw),
                             fmt(tot.five_year_avg), _teachers(r), fmt(r.value), fmt(tot.value)])
            return rows

        merges = [((0, 0), (1, 0)), ((0, 1), (0, 2)), ((0, 3), (0, 4)), ((0, 5), (1, 5)), ((0, 6), (0, 7))]
        self._pair(slide, render, "q1", sort_by="q1", header_rows=2, merges=merges)
        self._bottom(slide, f"A = Q1 期刊論文 {n} 年總數 ÷ {n}；B = 論文總數 ÷ {n}；C = 單年教師數\n"
                            "Q1 師均 = A ÷ C；總計師均 = B ÷ C\n"
                            "Q1：CiteScore 期刊排名前 25%",
                     "註3：單位排序以 Q1 單年師均由高到低排序。")

    def count_page(self, indicator_id: str, title: str, definition: str):
        n = self.years
        slide = self._slide(f"{self.period} {title}", self.kickers["detail"], importance_note(BY_ID[indicator_id]))

        def render(t: IndicatorTable):
            return [["單位", f"{n}年篇數\n(A)", "單年教師數\n(B)", f"單年師均\n(A/{n}/B)"],
                    *([_label(r, t.comparison), _count(r.raw), _teachers(r), fmt(r.value)] for r in t.rows)]

        self._pair(slide, render, indicator_id)
        self._bottom(slide, f"A = {definition}的 {n} 年篇數；B = 單年教師數\n單年師均 = A ÷ {n} ÷ B")

    def value_page(self, indicator_id: str, title: str, header: str, formula: str, *extra: str):
        slide = self._slide(f"{self.period} {title}", self.kickers["detail"], importance_note(BY_ID[indicator_id]))

        def render(t: IndicatorTable):
            return [["單位", header], *([_label(r, t.comparison), fmt(r.value)] for r in t.rows)]

        self._pair(slide, render, indicator_id)
        self._bottom(slide, formula, *extra)

    def build(self, out_path: Path) -> Path:
        self.cover()
        self.toc()
        if self.funding.has_data:
            self.funding_page()
        self.summary()
        self.pubs()
        self.count_page("top1", "被引數達全球前1%篇數（師均）", "被引次數達全球同領域前 1% 的論文")
        self.count_page("top10", "被引數達全球前10%篇數（師均）", "被引次數達全球同領域前 10% 的論文")
        self.value_page("fwci", "FWCI", "FWCI",
                        "FWCI = 各篇論文 FWCI（領域加權引用影響係數）的平均\n1 表示與全球同領域平均相同")
        strategy = get_strategy(self.run.self_citation_strategy)
        extra = f"註3：排除自我引用方式：{strategy.label}" + ("（待驗證）" if not strategy.verified else "")
        self.value_page("avg_citations", "平均論文被引次數（排除自我引用）", "平均論文被引次數(排除自我引用)",
                        "平均論文被引次數 = 各篇被引次數加總 ÷ 論文總篇數\n被引次數排除自我引用", extra)
        self.count_page("intl_collab", "國際合著篇數（師均）", "與國外機構合著的論文")
        self._footer()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        self.prs.save(out_path)
        return out_path


def build_dept_report(session: Session, run_id: int, ncu_dept: str, out_path: Path) -> Path:
    return ReportBuilder(session, run_id, ncu_dept).build(out_path)


def report_filename(session: Session, run_id: int, ncu_dept: str) -> str:
    b = ReportBuilder(session, run_id, ncu_dept)
    return f"{b.period}{b.college}{b.ncu_dept}論文表現分析.pptx"
