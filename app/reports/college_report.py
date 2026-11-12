"""全院總結簡報：與系所簡報共用 DeckBuilder 的版面（封面、內容頁、頁尾、長文分頁），只放 Claude 產生的文字。"""

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.db.models import Run
from app.mapping.parser import NCU_SHEET
from app.reports.pptx_report import DeckBuilder

if TYPE_CHECKING:
    from app.ai.service import SummaryDraft


def _period(run: Run) -> str:
    return f"{run.period_from}-{run.period_to}"


class CollegeReportBuilder(DeckBuilder):
    def __init__(self, session: Session, run_id: int, college: str, ai_text: "SummaryDraft"):
        super().__init__(session, run_id)
        self.college, self.ai_text = college, ai_text
        self.period = _period(self.run)

    def build(self, out_path: Path) -> Path:
        self._cover("員額小組論文資料", f"{NCU_SHEET}{self.college}",
                    f"{self.period} 論文指標全院總結\n{date.today().strftime('%Y/%m/%d')}")
        self.text_pages("全院總結", "I. 全院總結", [("全院總結", self.ai_text.summary)])
        comparison = self.ai_text.comparison or (None if self.ai_text.has_baseline else "無前期資料可比較")
        if comparison:
            self.text_pages("與前期比較", "II. 與前期比較", [("與前期比較", comparison)])
        self._footer(f"{NCU_SHEET} {self.college}｜{self.period} 論文指標全院總結")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        self.prs.save(out_path)
        return out_path


def build_college_report(session: Session, run_id: int, college: str, ai_text: "SummaryDraft",
                         out_path: Path) -> Path:
    return CollegeReportBuilder(session, run_id, college, ai_text).build(out_path)


def college_report_filename(run: Run, college: str) -> str:
    return f"{_period(run)}{college}論文指標全院總結.pptx"
