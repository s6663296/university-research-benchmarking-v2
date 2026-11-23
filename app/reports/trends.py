"""逐年趨勢（docs/progress/2026-11-23-趨勢圖評估.md 第一階段：系所層級）。

只用已存的逐篇資料（出版年與各篇指標）按年份分組計算，不呼叫 API，舊分析也能補畫。
網頁、pptx、xlsx 共用這裡的數字。

- 篇數類（論文總篇數、Q1、Top 1%、Top 10%、國際合著）畫逐年篇數，不除以教師數：
  系統只有單一學年度的教師數，逐年師均要每一年的教師數，還沒決定怎麼對應
- FWCI 畫逐年平均（該年有值論文的平均）
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.models import Run
from app.elsevier.scival import PubMetrics
from app.metrics.compute import round2
from app.metrics.indicators import BY_ID, Indicator
from app.reports.provenance import Explainer
from app.reports.tables import indicator_table

TREND_INDICATORS = ("total", "q1", "top1", "top10", "intl_collab", "fwci")
# 被引相關指標：新論文還來不及被引用，最近一兩年一定偏低
CITATION_BASED = frozenset({"top1", "top10", "fwci"})
RECENT_NOTE = "最近一兩年的論文發表時間短、被引次數還在累積，Top 1%、Top 10%、FWCI 通常偏低，不一定代表表現下滑。"


def trend_label(ind: Indicator) -> str:
    return f"{ind.label}（逐年平均）" if ind.aggregate == "mean" else f"{ind.label}（逐年篇數）"


def yearly_values(pubs: Iterable[PubMetrics], years: list[int], ind: Indicator) -> dict[int, Decimal | None]:
    """篇數類：該年符合條件的篇數；平均類：該年有值論文的平均（該年沒有值為 None）。出版年不在期間內的不計。"""
    by_year: dict[int, list[PubMetrics]] = {y: [] for y in years}
    for p in pubs:
        if p.year in by_year:
            by_year[p.year].append(p)
    out: dict[int, Decimal | None] = {}
    for y, ps in by_year.items():
        if ind.aggregate == "count":
            out[y] = Decimal(len(ps) if ind.field is None else sum(1 for p in ps if getattr(p, ind.field)))
        else:
            vals = [getattr(p, ind.field) for p in ps if getattr(p, ind.field) is not None]
            out[y] = round2(sum(vals, Decimal(0)) / len(vals)) if vals else None
    return out


@dataclass
class Series:
    unit_id: int
    label: str          # 對標比較只列校名，院內比較只列系所名（同一張圖上不重複）
    is_ncu: bool        # 要標紅加粗的「本系所」：對標比較是中大那一列；院內比較每列都是中大，只標 own_unit_id
    values: dict[int, Decimal | None]   # 沒抓完、查無論文的單位整條為 None


@dataclass
class TrendChart:
    indicator_id: str
    comparison: str
    key: str
    years: list[int]
    series: list[Series]

    @property
    def indicator(self) -> Indicator:
        return BY_ID[self.indicator_id]

    @property
    def has_data(self) -> bool:
        return any(v is not None for s in self.series for v in s.values.values())


def _short_label(label: str, comparison: str) -> str:
    dept, _, school = label.rpartition("-")
    return school if comparison == "benchmark" and dept else label


def trend_chart(session: Session, run_id: int, comparison: str, key: str, indicator_id: str,
                explainer: Explainer | None = None, own_unit_id: int | None = None) -> TrendChart:
    """單位順序與指標表相同（依整個期間的值由高到低），本系所與指標表一樣不另外移到最前面。

    own_unit_id：要標示的本系所；不給時對標比較標中大那一列，院內比較（全是中大）都不標。"""
    explainer = explainer or Explainer(session, run_id)
    run = session.get(Run, run_id)
    years = list(range(run.period_from, run.period_to + 1))
    ind = BY_ID[indicator_id]
    table = indicator_table(session, run_id, comparison, key, indicator_id, explainer=None)
    series = []
    for row in table.rows:
        pubs = explainer.pubs(row.unit_id)
        values = yearly_values(pubs, years, ind) if pubs else dict.fromkeys(years)
        own = row.unit_id == own_unit_id if own_unit_id is not None else row.is_ncu and comparison != "college"
        series.append(Series(row.unit_id, _short_label(row.label, comparison), own, values))
    return TrendChart(indicator_id, comparison, key, years, series)


def fmt_trend(value: Decimal | None, ind: Indicator) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}" if ind.aggregate == "mean" else str(int(value))

