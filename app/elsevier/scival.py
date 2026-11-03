"""SciVal Publication Metrics：逐篇取得報告需要的指標（設計 §6 步驟 4）。"""

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal

from app.elsevier.cache import cache_key
from app.elsevier.client import ElsevierClient, ElsevierError

METRICS_PATH = "/analytics/scival/publication/metrics"
METRIC_TYPES = "PublicationsInTopJournalPercentiles,OutputsInTopCitationPercentiles,FieldWeightedCitationImpact,Collaboration"
MAX_IDS = 100  # 一次請求最多 100 篇（實測 200 篇回 HTTP 400）


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


def citation_count(result: dict) -> int:
    return sum(year_sum(m) for m in result.get("metrics", []) if m.get("metricType") == "CitationCount")


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
        citations=citation_count(result) if "CitationCount" in metrics else None,
        raw=result,
    )


def _settle(fut: asyncio.Future, error: BaseException) -> None:
    if fut.done():
        return
    if isinstance(error, Exception):
        fut.set_exception(error)
        fut.exception()  # 沒有其他單位在等時，不要在關閉時印出「exception was never retrieved」
    else:
        fut.cancel()


class MetricsFetcher:
    """同一次分析共用：每篇論文只向 SciVal 要一次，並逐篇快取。

    各單位的論文常有重疊（院與系、合聘），逐篇處理後重疊的論文不會重複消耗額度；
    逐篇快取也讓續跑、重抓時即使批次組合不同，仍可重用當天已抓過的論文。
    include_self 不為 None 時，被引數（CitationCount）與其他指標同一次請求取得。
    """

    def __init__(self, client: ElsevierClient, *, year_range: str, include_self: str | None = None,
                 use_cache: bool):
        self.client = client
        self.use_cache = use_cache
        self.params = {
            "metricTypes": METRIC_TYPES + (",CitationCount" if include_self is not None else ""),
            "yearRange": year_range,
            "byYear": "true",
            "journalImpactType": "CiteScore",
            "includedDocs": "AllPublicationTypes",
        }
        if include_self is not None:
            self.params["includeSelfCitations"] = include_self
        self.data_source: dict = {}
        self._results: dict[str, asyncio.Future] = {}  # scopus_id → 該篇結果（SciVal 沒有的為 None）

    def _key(self, sid: str) -> str:
        return cache_key(METRICS_PATH, {**self.params, "publicationIds": sid})

    async def fetch(self, ids: list[str]) -> dict[str, PubMetrics]:
        """回傳 SciVal 有資料的論文；SciVal 沒有的 id 不出現在 dict 中。"""
        new = [sid for sid in dict.fromkeys(ids) if sid not in self._results]
        loop = asyncio.get_running_loop()
        for sid in new:
            self._results[sid] = loop.create_future()
        if new:
            try:
                await self._load(new)
            except BaseException as e:
                for sid in new:
                    _settle(self._results[sid], e)
                raise
        results = [await self._results[sid] for sid in dict.fromkeys(ids)]
        return {m.scopus_id: m for m in map(parse_result, filter(None, results))}

    async def _load(self, ids: list[str]) -> None:
        cache = self.client.cache
        cached = cache.get_many([self._key(sid) for sid in ids]) if cache is not None and self.use_cache else {}
        missing = []
        for sid in ids:
            entry = cached.get(self._key(sid))
            if entry is None:
                missing.append(sid)
            else:
                self.data_source = self.data_source or entry.get("dataSource") or {}
                self._results[sid].set_result(entry.get("result"))
        for chunk in batches(missing, MAX_IDS):
            await self._get_split(chunk)

    async def _get_split(self, ids: list[str]) -> None:
        """整批失敗（逾時、5xx 等）時拆成兩半重試，直到單篇；成功的部分立即存下。

        SciVal 忙碌時 100 篇的大批次容易逾時，拆小後通常就能成功，也能找出真正有問題的那一篇。
        被限速（429）、權限錯誤與參數錯誤拆了也沒用，直接往上拋。
        """
        try:
            data = await self.client.get_json(METRICS_PATH, {**self.params, "publicationIds": ",".join(ids)},
                                              use_cache=False, store=False)
        except ElsevierError as e:
            if len(ids) <= 1 or e.status in (200, 400, 401, 403, 429):
                raise
            mid = len(ids) // 2
            await self._get_split(ids[:mid])
            await self._get_split(ids[mid:])
            return
        self._store(ids, data)

    def _store(self, chunk: list[str], data: dict) -> None:
        source = data.get("dataSource") or {}
        self.data_source = source or self.data_source
        found = {str(r.get("publication", {}).get("id")): r for r in data.get("results", [])}
        entries = {sid: {"result": found.get(sid), "dataSource": source} for sid in chunk}
        if self.client.cache is not None:
            self.client.cache.put_many(METRICS_PATH, {self._key(sid): e for sid, e in entries.items()})
        for sid, e in entries.items():
            self._results[sid].set_result(e["result"])
