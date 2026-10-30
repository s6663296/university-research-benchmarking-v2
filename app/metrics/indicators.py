"""指標定義表（設計 §7.1）：每個指標只在這裡定義一次。"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Indicator:
    id: str
    label: str
    source: str
    aggregate: Literal["count", "mean", "ratio"]
    field: str | None          # PubMetrics 的欄位；None 表示全部篇數
    per_faculty: bool
    rankings: tuple[str, ...]


INDICATORS: tuple[Indicator, ...] = (
    Indicator("total", "論文總篇數", "Scopus Search 篇數", "count", None, True, ("QS", "THE")),
    Indicator("q1", "Q1", "PublicationsInTopJournalPercentiles（CiteScore, threshold=25）",
              "count", "q1", True, ("高教深耕",)),
    Indicator("top1", "被引數達全球前1%篇數", "OutputsInTopCitationPercentiles（threshold=1）",
              "count", "top1", True, ("THE", "高教深耕")),
    Indicator("top10", "被引數達全球前10%篇數", "OutputsInTopCitationPercentiles（threshold=10）",
              "count", "top10", True, ("THE",)),
    Indicator("fwci", "FWCI", "FieldWeightedCitationImpact（逐篇平均）", "mean", "fwci", False, ("THE", "高教深耕")),
    Indicator("avg_citations", "平均論文被引次數(排除自我引用)", "CitationCount ÷ 總篇數",
              "ratio", "citations", False, ("QS",)),
    Indicator("intl_collab", "國際合著篇數", "Collaboration（International collaboration）",
              "count", "intl_collab", True, ("QS", "THE", "高教深耕")),
)
BY_ID = {i.id: i for i in INDICATORS}

RANKING_TEXT = {"QS": "QS世界大學排名指標", "THE": "THE世界大學排名指標", "高教深耕": "教育部高教深耕指標"}


def importance_note(ind: Indicator) -> str:
    return " ".join(f"※{RANKING_TEXT[r]}" for r in ind.rankings)
