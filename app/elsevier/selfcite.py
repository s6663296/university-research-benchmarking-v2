"""排除自我引用的策略（設計 §7.3）。驗證結果出來後只需在設定頁切換。"""

from typing import Protocol

from app.elsevier.client import ElsevierClient, ElsevierError
from app.elsevier.scival import METRICS_PATH, batches, year_sum


class SelfCitationStrategy(Protocol):
    id: str
    label: str
    verified: bool

    async def citations(self, client: ElsevierClient, ids: list[str], *, year_range: str, batch_size: int,
                        use_cache: bool) -> dict[str, int]: ...


class _SciValCitationCount:
    include_self = "false"

    async def citations(self, client, ids, *, year_range, batch_size, use_cache):
        out: dict[str, int] = {}
        for chunk in batches(ids, batch_size):
            data = await client.get_json(METRICS_PATH, {
                "metricTypes": "CitationCount",
                "publicationIds": ",".join(chunk),
                "yearRange": year_range,
                "byYear": "true",
                "includeSelfCitations": self.include_self,
                "includedDocs": "AllPublicationTypes",
            }, use_cache=use_cache)
            for result in data.get("results", []):
                sid = str(result.get("publication", {}).get("id"))
                out[sid] = sum(year_sum(m) for m in result.get("metrics", [])
                               if m.get("metricType") == "CitationCount")
        return out


class SciValParamStrategy(_SciValCitationCount):
    id = "scival_param"
    label = "SciVal includeSelfCitations=false"
    verified = False  # 實測 true/false 結果相同，待使用者依設計 §7.3 驗證


class NoExclusionStrategy(_SciValCitationCount):
    id = "none"
    label = "不排除自我引用"
    verified = True
    include_self = "true"


class ScopusCitationOverviewStrategy:
    id = "scopus_citation_overview"
    label = "Scopus Citation Overview（exclude-self）"
    verified = False

    async def citations(self, client, ids, *, year_range, batch_size, use_cache):
        # 這支 API 需要另外申請授權；取得授權後再依實際回應格式實作
        raise ElsevierError(None, False, "Scopus Citation Overview API 尚未取得授權，請改用其他策略")


STRATEGIES: dict[str, SelfCitationStrategy] = {
    s.id: s for s in (SciValParamStrategy(), ScopusCitationOverviewStrategy(), NoExclusionStrategy())
}


def get_strategy(strategy_id: str) -> SelfCitationStrategy:
    return STRATEGIES[strategy_id]
