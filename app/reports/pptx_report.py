"""每個系所一份簡報，依範例報告的結構產出（設計 §9.2）。只有表格與文字，中大列標紅。"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Emu, Pt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Anomaly, Run
from app.db.settings_store import get_setting
from app.elsevier.selfcite import get_strategy
from app.mapping.parser import NCU_SHEET
from app.mapping.service import units_of
from app.metrics.indicators import BY_ID, importance_note
from app.reports.tables import IndicatorTable, Row, fmt, indicator_table, short_names, summary_table

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "report_template.pptx"
RED = RGBColor(0xFF, 0x00, 0x00)
FONT = Pt(12)
LEFT_X, RIGHT_X, TABLE_Y, TABLE_W = Emu(259080), Emu(6193539), Emu(2450275), Emu(5650000)
ROW_H = Emu(330000)

TOC_ITEMS = [
    "    - 論文發表師均(QS/THE看總篇數、高教深耕看Q1)",
    "    - 論文被引次數達全球前1%論文篇數師均(THE排名/高教深耕指標)",
    "    - 論文被引次數達全球前10%論文篇數師均 (THE排名指標)",
    "    - FWCI (THE排名/高教深耕指標)",
    "    - 論文平均被引次數(排除自我引用) (QS排名指標)",
    "    - 國際合著論文師均(QS/THE/高教深耕指標)",
]


def _text(slide, text: str, left, top, width, height, size=Pt(14), color=None, bold=False):
    box = slide.shapes.add_textbox(left, top, width, height)
    frame = box.text_frame
    frame.word_wrap = True
    for i, line in enumerate(text.split("\n")):
        para = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
        run = para.add_run()
        run.text = line
        run.font.size, run.font.bold = size, bold
        if color is not None:
            run.font.color.rgb = color
    return box


def _table(slide, rows: list[list[str]], left, top, width, header_rows=1, red_rows=(), red_cells=()):
    shape = slide.shapes.add_table(len(rows), len(rows[0]), left, top, width, ROW_H * len(rows))
    table = shape.table
    for r, values in enumerate(rows):
        for c, value in enumerate(values):
            cell = table.cell(r, c)
            cell.text = value
            for para in cell.text_frame.paragraphs:
                for run in para.runs:
                    run.font.size = FONT
                    run.font.bold = r < header_rows
                    if r in red_rows or (r, c) in red_cells:
                        run.font.color.rgb = RED
    return table


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
        self.dept_short, _, self.college_short = short_names(session)
        unit = next(u for u in units_of(session, self.run.mapping_version_id)
                    if u.school == NCU_SHEET and u.ncu_dept == ncu_dept)
        self.college = unit.ncu_college or ""
        self.short = self.dept_short.get(ncu_dept, ncu_dept)
        self.college_label = self.college_short.get(self.college, self.college)
        self.period = f"{self.run.period_from}-{self.run.period_to}"
        self.years = self.run.period_to - self.run.period_from + 1
        self.open_anomalies = session.scalar(select(func.count()).select_from(Anomaly).where(
            Anomaly.run_id == run_id, Anomaly.status != "confirmed"))
        self.prs = Presentation(TEMPLATE)
        self.layouts = {layout.name: layout for layout in self.prs.slide_layouts}

    # ---- 共用 ----
    def _slide(self, title: str, layout: str = "只有標題"):
        slide = self.prs.slides.add_slide(self.layouts[layout])
        slide.shapes.title.text = title
        if self.open_anomalies:
            _text(slide, f"※本報告含 {self.open_anomalies} 項待人工確認資料", Emu(8500000), Emu(60000),
                  Emu(3600000), Emu(350000), Pt(12), RED, True)
        return slide

    def _footnotes(self, slide, indicator_id: str, extra: str = ""):
        _text(slide, importance_note(BY_ID[indicator_id]), LEFT_X, Emu(1150000), Emu(6500000), Emu(400000), Pt(14))
        note = (f"註1：單年教師數來自教育部公開資訊平台（{self.run.academic_year}學年度）；標 * 者為人工修改。\n"
                f"註2：資料來源 Scopus、SciVal，資料取得日期 {self.notes.get('data_date', '')}。")
        _text(slide, note + (f"\n{extra}" if extra else ""), Emu(7400000), Emu(1050000), Emu(4600000),
              Emu(800000), Pt(10))

    def _pair(self, slide, render, indicator_id: str, sort_by: str | None = None):
        """左：對標學校比較；右：院內系所比較。"""
        for x, comparison, key, label in (
                (LEFT_X, "benchmark", self.ncu_dept, "對標學校比較"),
                (RIGHT_X, "college", self.college, f"{self.college_label}內系所比較")):
            table = indicator_table(self.session, self.run_id, comparison, key, indicator_id, sort_by)
            if not table.rows:
                continue
            _text(slide, label, x, TABLE_Y - Emu(500000), Emu(3500000), Emu(450000), Pt(18), bold=True)
            render(slide, table, x)

    # ---- 各頁 ----
    def cover(self):
        slide = self.prs.slides.add_slide(self.layouts["標題投影片"])
        slide.shapes.title.text = f"國立中央大學{self.college}-{self.short}"
        slide.placeholders[1].text = date.today().strftime("%Y/%m/%d")

    def toc(self):
        slide = self.prs.slides.add_slide(self.layouts["標題及內容"])
        slide.shapes.title.text = "目錄"
        lines = [f"I. {self.college_label}各系所論文指標於對標學校排名", f"II. {self.short}各指標比較", *TOC_ITEMS]
        frame = slide.placeholders[1].text_frame
        frame.text = lines[0]
        for line in lines[1:]:
            frame.add_paragraph().text = line

    def summary(self):
        slide = self._slide(f"I、{self.college}各系所論文指標排名")
        highlight = int(get_setting(self.session, "rank_highlight_top"))
        rows = [["單位", "對外/對內項目", "優質期刊", f"{self.years}年論文被引次數", "", "", "", "國際合著篇數 (師均)"],
                ["", "", "Q1師均", "Top1%篇數 (師均)", "Top10%篇數 (師均)", "FWCI", "平均論文被引次數", ""]]
        red = set()
        for s in summary_table(self.session, self.run_id, self.college):
            label = f"對標學校: {s.group_size}間" if s.comparison == "benchmark" else f"{self.college_label}內"
            ranks = ["待確認" if v is None else f"#{v}" for v in s.ranks.values()]
            for c, v in enumerate(s.ranks.values(), start=2):
                if v is not None and v <= highlight:
                    red.add((len(rows), c))
            rows.append([s.dept_label, label, *ranks])
        table = _table(slide, rows, Emu(841248), Emu(900000), Emu(10500000), header_rows=2, red_cells=red)
        table.cell(0, 0).merge(table.cell(1, 0))
        table.cell(0, 1).merge(table.cell(1, 1))
        table.cell(0, 3).merge(table.cell(0, 6))
        table.cell(0, 7).merge(table.cell(1, 7))

    def pubs(self):
        slide = self._slide(f"II. {self.period}論文發表篇數師均")
        _text(slide, f"{importance_note(BY_ID['q1'])}（Q1）　{importance_note(BY_ID['total'])}（論文發表篇數）",
              LEFT_X, Emu(1150000), Emu(7000000), Emu(400000))
        self._footnotes_only(slide)

        def render(slide, t: IndicatorTable, x):
            total = {r.unit_id: r for r in indicator_table(self.session, self.run_id, t.comparison, t.key,
                                                           "total").rows}
            rows = [["單位", "Q1", "", "總計", "", "單年教師數(C)", "單年師均", ""],
                    ["", f"{self.years}年 總數", f"{self.years}年 平均 (A)", f"{self.years}年 總數",
                     f"{self.years}年 平均(B)", "", "Q1 (A/C)", "總計 (B/C)"]]
            red = []
            for r in t.rows:
                tot = total[r.unit_id]
                if r.is_ncu:
                    red.append(len(rows))
                rows.append([r.label, _count(r.raw), fmt(r.five_year_avg), _count(tot.raw), fmt(tot.five_year_avg),
                             _teachers(r), fmt(r.value), fmt(tot.value)])
            table = _table(slide, rows, x, TABLE_Y, TABLE_W, header_rows=2, red_rows=red)
            table.cell(0, 0).merge(table.cell(1, 0))
            table.cell(0, 1).merge(table.cell(0, 2))
            table.cell(0, 3).merge(table.cell(0, 4))
            table.cell(0, 5).merge(table.cell(1, 5))
            table.cell(0, 6).merge(table.cell(0, 7))

        self._pair(slide, render, "q1", sort_by="q1")

    def _footnotes_only(self, slide):
        note = (f"註1：單年教師數來自教育部公開資訊平台（{self.run.academic_year}學年度）；標 * 者為人工修改。\n"
                f"註2：資料來源 Scopus、SciVal，資料取得日期 {self.notes.get('data_date', '')}。\n"
                f"註3：單位排序以Q1單年師均數字高到低排序。")
        _text(slide, note, Emu(7400000), Emu(1050000), Emu(4600000), Emu(800000), Pt(10))

    def count_page(self, indicator_id: str, title: str):
        slide = self._slide(f"{self.period} {title}")
        self._footnotes(slide, indicator_id)

        def render(slide, t: IndicatorTable, x):
            n = self.years
            rows, red = [["單位", f"{n}年篇數 (A)", "單年教師數(B)", f"單年師均 (A/{n}/B)"]], []
            for r in t.rows:
                if r.is_ncu:
                    red.append(len(rows))
                rows.append([r.label, _count(r.raw), _teachers(r), fmt(r.value)])
            _table(slide, rows, x, TABLE_Y, Emu(4800000), red_rows=red)

        self._pair(slide, render, indicator_id)

    def value_page(self, indicator_id: str, title: str, header: str, extra: str = ""):
        slide = self._slide(f"{self.period}{title}")
        self._footnotes(slide, indicator_id, extra)

        def render(slide, t: IndicatorTable, x):
            rows, red = [["單位", header]], []
            for r in t.rows:
                if r.is_ncu:
                    red.append(len(rows))
                rows.append([r.label, fmt(r.value)])
            _table(slide, rows, x, TABLE_Y, Emu(4400000), red_rows=red)

        self._pair(slide, render, indicator_id)

    def build(self, out_path: Path) -> Path:
        self.cover()
        self.toc()
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
    return f"{b.period}{b.college}{b.short}論文表現分析.pptx"
