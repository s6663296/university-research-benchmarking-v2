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
from typing import TYPE_CHECKING

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
from app.funding.service import count_text, funding_table, funding_warnings, money
from app.mapping.parser import NCU_SHEET, is_ncu
from app.mapping.service import units_of
from app.reports.report_text import INDICATOR_KEYS, ReportText, report_values
from app.reports.tables import IndicatorTable, Row, fmt, indicator_table, summary_table

if TYPE_CHECKING:
    from app.ai.service import SummaryDraft

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
TEXT_INSETS = 2 * 45720          # 文字框預設的上下留白
TEXT_MAX_PT, TEXT_MIN_PT = 18, 12             # 文字頁字級範圍
HEAD_GAP, SECTION_GAP = 40000, 160000        # 文字頁：小標與內文、區塊之間的間距

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


def _fit_size(text: str, width: int, height: int, max_pt: float, min_pt: float):
    """使用者改長文字時由大到小試字級（每次 0.5pt），避免超出固定高度的文字框。"""
    size = max_pt
    while size > min_pt:
        unit = size * PT                 # 文字框可換行，不需要表格欄寬的安全係數
        lines = sum(max(1, math.ceil(_em(p) * unit / width)) for p in text.split("\n"))
        if lines * size * PT * 1.2 + TEXT_INSETS <= height:
            break
        size -= 0.5
    return Pt(max(size, min_pt))


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


def _block_height(text: str, size: float) -> int:
    """文字頁區塊的文字框高度：行數估計同 _fit_size。"""
    lines = sum(max(1, math.ceil(_em(p) * size * PT / CONTENT_W)) for p in text.split("\n"))
    return int(lines * size * PT * LINE + TEXT_INSETS)


def _split_long(text: str, size: float, avail: int) -> list[str]:
    """單段高度超過一頁可用空間（扣掉小標）時，依字寬切成每塊都放得下的幾段。"""
    budget = avail - _block_height("標", size + 2) - HEAD_GAP - SECTION_GAP
    lines = max(int((budget - TEXT_INSETS) / (size * PT * LINE)) - 1, 1)   # 留一行給換行損耗
    limit = lines * CONTENT_W / (size * PT)
    chunks, current, used = [], "", 0.0
    for ch in text:
        width = _em(ch)
        if used + width > limit:
            chunks.append(current)
            current, used = "", 0.0
        current, used = current + ch, used + width
    return [*chunks, current] if current else chunks


def _paginate(sections: list[tuple[str, str]], size: float, avail: int) -> list[list[tuple[bool, str]]]:
    """依段落貪婪填頁，回傳每頁的區塊 (是否為小標, 文字)；相鄰段落併成一個文字框，小標不會落在頁尾。"""
    def height(blocks):
        return sum(_block_height(t, size + 2 if head else size) + (HEAD_GAP if head else SECTION_GAP)
                   for head, t in blocks)

    pages: list[list[tuple[bool, str]]] = [[]]
    for heading, body in sections:
        paras = [c for p in body.split("\n") if p.strip() for c in _split_long(p, size, avail)]
        for n, para in enumerate([None, *paras]):         # None 代表小標
            def add(page):
                if para is None:
                    return [*page, (True, heading)]
                if page and not page[-1][0]:
                    return [*page[:-1], (False, page[-1][1] + "\n" + para)]
                return [*page, (False, para)]

            # 小標與它的第一段一起試放，放不下就整組換頁
            if para is None:
                probe = [*pages[-1], (True, heading), (False, paras[0] if paras else "")]
            else:
                probe = add(pages[-1])
            if height(probe) > avail and pages[-1]:
                pages.append([])
            pages[-1] = add(pages[-1])
    return pages


class DeckBuilder:
    """各份簡報共用的版面基底：內容頁、封面、頁尾與長文分頁，不含任何系所專屬內容。"""

    def __init__(self, session: Session, run_id: int):
        self.session, self.run_id = session, run_id
        self.run = session.get(Run, run_id)
        self.open_anomalies = session.scalar(select(func.count()).select_from(Anomaly).where(
            Anomaly.run_id == run_id, Anomaly.status != "confirmed"))
        self.prs = Presentation(TEMPLATE)
        self.layouts = {layout.name: layout for layout in self.prs.slide_layouts}

    def _slide(self, title: str, kicker: str = "", subtitle: str = ""):
        """內容頁：章節標籤、標題、細線、副標；有待確認資料時右上角標示。"""
        slide = self.prs.slides.add_slide(self.layouts["只有標題"])
        banner_w = 4200000 if self.open_anomalies else 0
        if kicker:
            width = CONTENT_W - banner_w - 100000
            _text(slide, kicker, MARGIN, KICKER_Y, width, KICKER_H, _fit_size(kicker, width, KICKER_H, 12, 9), BLUE, True,
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
            _font(run, _fit_size(title, CONTENT_W, TITLE_H, 26, 14), True, NAVY)
        _rect(slide, MARGIN, RULE_Y, CONTENT_W, RULE_H, BLUE)
        if subtitle:
            _text(slide, subtitle, MARGIN, SUB_Y, CONTENT_W, SUB_H, _fit_size(subtitle, CONTENT_W, SUB_H, 12, 9), GRAY,
                  anchor=MSO_ANCHOR.MIDDLE)
        return slide

    def _cover(self, label: str, title: str, subtitle: str):
        """封面：深藍底、白字標題；label 在標題上方，subtitle 第一行為粗體主副標，其餘為小字。"""
        slide = self.prs.slides.add_slide(self.layouts["標題投影片"])
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = NAVY
        _text(slide, label, 1000000, 1450000, 8000000, 380000, Pt(16), RGBColor(0xBD, 0xD7, 0xEE), True)
        shape = slide.shapes.title
        shape.left, shape.top, shape.width, shape.height = Emu(1000000), Emu(1900000), Emu(10200000), Emu(1700000)
        shape.text = title
        frame = shape.text_frame
        frame.word_wrap, frame.vertical_anchor = True, MSO_ANCHOR.TOP
        for para in frame.paragraphs:
            para.alignment = PP_ALIGN.LEFT
            for run in para.runs:
                _font(run, Pt(40), True, WHITE)
        _rect(slide, 1000000, 3750000, 1600000, 50000, RGBColor(0x9D, 0xC3, 0xE6))
        sub = slide.placeholders[1]
        sub.left, sub.top, sub.width, sub.height = Emu(1000000), Emu(3950000), Emu(10200000), Emu(1100000)
        sub.text = subtitle
        for i, para in enumerate(sub.text_frame.paragraphs):
            para.alignment = PP_ALIGN.LEFT
            for run in para.runs:
                _font(run, Pt(22 if i == 0 else 16), i == 0, WHITE if i == 0 else RGBColor(0xBD, 0xD7, 0xEE))

    def _footer(self, name: str):
        """每頁（封面除外）頁尾：細線、報告名稱、頁碼。最後統一加上才知道總頁數。"""
        slides = list(self.prs.slides)
        for n, slide in enumerate(slides[1:], start=2):
            _rect(slide, MARGIN, FOOTER_RULE_Y, CONTENT_W, 9525, RGBColor(0xBF, 0xBF, 0xBF))
            _text(slide, name, MARGIN, FOOTER_Y, CONTENT_W - 1300000, FOOTER_H, Pt(9), GRAY,
                  anchor=MSO_ANCHOR.MIDDLE)
            _text(slide, f"{n} / {len(slides)}", SLIDE_W - MARGIN - 1100000, FOOTER_Y, 1100000, FOOTER_H, Pt(9),
                  GRAY, align=PP_ALIGN.RIGHT, anchor=MSO_ANCHOR.MIDDLE)

    def text_pages(self, title: str, kicker: str, sections: list[tuple[str, str]]):
        """文字頁：每節（小標, 內文）由上往下排；字級由 18pt 往下縮到 12pt，仍放不下就以段落為界分頁。"""
        top = SUB_Y + 100000
        avail = FOOTER_RULE_Y - 100000 - top
        size = TEXT_MAX_PT
        while True:
            pages = _paginate(sections, size, avail)
            if len(pages) == 1 or size <= TEXT_MIN_PT:
                break
            size -= 0.5
        for i, blocks in enumerate(pages):
            slide = self._slide(title + ("（續）" if i else ""), kicker)
            y = top
            for head, text in blocks:
                pt = size + 2 if head else size
                height = _block_height(text, pt)
                _text(slide, text, MARGIN, y, CONTENT_W, height, Pt(pt), NAVY if head else GRAY, head)
                y += height + (HEAD_GAP if head else SECTION_GAP)


class ReportBuilder(DeckBuilder):
    def __init__(self, session: Session, run_id: int, ncu_dept: str, ai_text: "SummaryDraft | None" = None):
        super().__init__(session, run_id)
        self.ncu_dept, self.ai_text = ncu_dept, ai_text
        self.notes = json.loads(self.run.notes_json or "{}")
        unit = next(u for u in units_of(session, self.run.mapping_version_id)
                    if is_ncu(u.school) and u.ncu_dept == ncu_dept)
        self.unit_id, self.college = unit.id, unit.ncu_college or ""
        self.period = f"{self.run.period_from}-{self.run.period_to}"
        self.years = self.run.period_to - self.run.period_from + 1
        # 有經分表資料時，第 I 部分是計畫經費，論文指標順延為 II、III（範例報告的編號）
        self.funding = funding_table(session, self.run, self.college)
        nums = ("II", "III") if self.funding.has_data else ("I", "II")
        self.numbers = {"funding": "I", "summary": nums[0], "detail": nums[1],
                        "ai": "IV" if self.funding.has_data else "III"}
        self.text = ReportText(get_setting(session, "report_text"),
                               report_values(session, self.run, ncu_dept, self.college, self.funding))

    # ---- 共用 ----
    def _page(self, key: str, chapter: str, suffix: str = ""):
        """依設定的文字建立內容頁：標題、章節標籤與副標都來自 report_text。"""
        kicker = f"{self.numbers[chapter]}. {self.text.kicker(chapter)}"
        return self._slide(self.text.title(key) + suffix, kicker, self.text.field(key, "subtitle"))

    def _bottom(self, slide, key: str, warnings: list[str] | None = None):
        """底部：左邊是計算方式，右邊是資料註腳；內容來自 report_text，清空的欄位不顯示。"""
        formula = self.text.field(key, "formula")
        if formula:
            text = "計算方式\n" + formula
            _text(slide, text, LEFT_X, NOTE_Y, HALF_W, NOTE_H, _fit_size(text, HALF_W, NOTE_H, 10.5, 8), GRAY)
            for run in slide.shapes[-1].text_frame.paragraphs[0].runs:
                _font(run, bold=True, color=NAVY)
        notes = "\n".join(filter(None, [self.text.field(key, "notes"), *(warnings or [])]))
        if notes:
            _text(slide, notes, RIGHT_X, NOTE_Y, HALF_W, NOTE_H, _fit_size(notes, HALF_W, NOTE_H, 9.5, 8), GRAY)

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

    # ---- 各頁 ----
    def cover(self):
        """封面：深藍底、白字標題，下方為報告期間與產出日期。"""
        self._cover("員額小組論文資料", f"{NCU_SHEET}{self.college}-\v{self.ncu_dept}",
                    f"{self.period} 論文表現分析\n{date.today().strftime('%Y/%m/%d')}")

    def toc(self):
        slide = self._slide("目錄")
        chapters = ["summary", "detail"]
        if self.funding.has_data:
            chapters.insert(0, "funding")
        sections = [f"{self.numbers[c]}. {self.text.field(c, 'name')}" for c in chapters]
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
                for key in INDICATOR_KEYS:
                    sub = frame.add_paragraph()
                    sub.level = 1
                    sub.space_before = Pt(4)
                    run = sub.add_run()
                    run.text = f"・{self.text.toc_line(key)}"
                    _font(run, Pt(15), False, GRAY)

    def funding_page(self):
        """範例第 3 頁：學院各系所國科會及產學計畫經費年平均（資料來自經分表），本系所列標紅。"""
        t = self.funding
        n = len(t.years)
        slide = self._page("funding", "funding")
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
        self._bottom(slide, "funding", funding_warnings(t))

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
            slide = self._page("summary", "summary", "（續）" if i else "")
            rows, red = body(page)
            _table(slide, rows, MARGIN, SUMMARY_Y, CONTENT_W, max_height, 2, merges, red_cells=red)
            self._bottom(slide, "summary")

    def pubs(self):
        n = self.years
        slide = self._page("pubs", "detail")

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
        self._bottom(slide, "pubs")

    def count_page(self, indicator_id: str):
        n = self.years
        slide = self._page(indicator_id, "detail")

        def render(t: IndicatorTable):
            return [["單位", f"{n}年篇數\n(A)", "單年教師數\n(B)", f"單年師均\n(A/{n}/B)"],
                    *([_label(r, t.comparison), _count(r.raw), _teachers(r), fmt(r.value)] for r in t.rows)]

        self._pair(slide, render, indicator_id)
        self._bottom(slide, indicator_id)

    def value_page(self, indicator_id: str, header: str):
        slide = self._page(indicator_id, "detail")

        def render(t: IndicatorTable):
            return [["單位", header], *([_label(r, t.comparison), fmt(r.value)] for r in t.rows)]

        self._pair(slide, render, indicator_id)
        self._bottom(slide, indicator_id)

    def ai_page(self):
        """最後一章：Claude 產生的總結與前期比較（無前期資料時明說）。"""
        comparison = self.ai_text.comparison or "無前期資料可比較"
        self.text_pages(f"{self.ncu_dept}總結與建議", f"{self.numbers['ai']}. 總結與建議",
                        [("總結", self.ai_text.summary), ("與前期比較", comparison)])

    def build(self, out_path: Path) -> Path:
        self.cover()
        self.toc()
        if self.funding.has_data:
            self.funding_page()
        self.summary()
        self.pubs()
        self.count_page("top1")
        self.count_page("top10")
        self.value_page("fwci", "FWCI")
        self.value_page("avg_citations", "平均論文被引次數(排除自我引用)")
        self.count_page("intl_collab")
        if self.ai_text:
            self.ai_page()
        self._footer(f"{NCU_SHEET} {self.college} {self.ncu_dept}｜{self.period} 論文表現分析")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        self.prs.save(out_path)
        return out_path


def build_dept_report(session: Session, run_id: int, ncu_dept: str, out_path: Path,
                      ai_text: "SummaryDraft | None" = None) -> Path:
    return ReportBuilder(session, run_id, ncu_dept, ai_text).build(out_path)


def report_filename(session: Session, run_id: int, ncu_dept: str) -> str:
    b = ReportBuilder(session, run_id, ncu_dept)
    return f"{b.period}{b.college}{b.ncu_dept}論文表現分析.pptx"
