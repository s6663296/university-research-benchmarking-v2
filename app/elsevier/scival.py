"""SciVal Publication Metrics：逐篇取得報告需要的指標（設計 §6 步驟 4）。"""

from dataclasses import dataclass, field
from decimal import Decimal

from app.elsevier.client import ElsevierClient

METRICS_PATH = "/analytics/scival/publication/metrics"
METRIC_TYPES = "PublicationsInTopJournalPercentiles,OutputsInTopCitationPercentiles,FieldWeightedCitationImpact,Collaboration"


@dataclass
class PubMetrics:
    scopus_id: str
    year: int | None = None
    q1: bool | None = None
    top1: bool | None = None
    top10: bool | None = None
    fwci: Decimal | None = None
    intl_collab: bool | None = None
    citations: int | None = None
    raw: dict = field(default_factory=dict)


def scival_year_ranges(year_from: int, year_to: int, current_year: int) -> tuple[str, str]:
    """回傳（指標用, 被引數用）的 SciVal yearRange。SciVal 只支援從去年往回 5 年或 10 年。"""
    if year_from > year_to:
        raise ValueError("期間起始年不可晚於結束年")
    if year_to >= current_year:
        raise ValueError("期間不可包含今年（SciVal 只提供到去年為止的完整年度）")
    if year_from >= current_year - 5:
        return "5yrs", "5yrsAndCurrentAndFuture"
    if year_from >= current_year - 10:
        return "10yrs", "10yrsAndCurrentAndFuture"
    raise ValueError(f"SciVal 只提供近 10 年的資料，起始年不可早於 {current_year - 10}")


def batches(ids: list[str], size: int):
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


def year_sum(entry: dict) -> int:
    return sum(v or 0 for v in (entry.get("valueByYear") or {}).values())


def _has(metric: dict | None, key: str, value) -> bool | None:
    if metric is None:
        return None
    found = [v for v in metric.get("values", []) if v.get(key) == value]
    return any(year_sum(v) > 0 for v in found) if found else None


def parse_result(result: dict) -> PubMetrics:
    pub = result.get("publication", {})
    metrics = {m.get("metricType"): m for m in result.get("metrics", [])}
    fwci_metric = metrics.get("FieldWeightedCitationImpact")
    fwci_values = [v for v in (fwci_metric or {}).get("valueByYear", {}).values() if v is not None]
    return PubMetrics(
        scopus_id=str(pub.get("id")),
        year=pub.get("publicationYear"),
        q1=_has(metrics.get("PublicationsInTopJournalPercentiles"), "threshold", 25),
        top1=_has(metrics.get("OutputsInTopCitationPercentiles"), "threshold", 1),
        top10=_has(metrics.get("OutputsInTopCitationPercentiles"), "threshold", 10),
        fwci=Decimal(str(fwci_values[0])) if fwci_values else None,
        intl_collab=_has(metrics.get("Collaboration"), "collabType", "International collaboration"),
        raw=result,
    )


async def fetch_metrics(client: ElsevierClient, ids: list[str], *, year_range: str, batch_size: int,
                        use_cache: bool) -> tuple[dict[str, PubMetrics], dict]:
    out: dict[str, PubMetrics] = {}
    source: dict = {}
    for chunk in batches(ids, batch_size):
        data = await client.get_json(METRICS_PATH, {
            "metricTypes": METRIC_TYPES,
            "publicationIds": ",".join(chunk),
            "yearRange": year_range,
            "byYear": "true",
            "journalImpactType": "CiteScore",
            "includedDocs": "AllPublicationTypes",
        }, use_cache=use_cache)
        source = data.get("dataSource") or source
        for result in data.get("results", []):
            m = parse_result(result)
            out[m.scopus_id] = m
    return out, source
