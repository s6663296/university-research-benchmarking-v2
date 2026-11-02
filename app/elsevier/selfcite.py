"""排除自我引用的策略（設計 §7.3）。驗證結果出來後只需在設定頁切換。"""

from typing import Protocol

from app.elsevier.client import ElsevierClient, ElsevierError


class SelfCitationStrategy(Protocol):
    id: str
    label: str
    verified: bool
    # 不為 None 時，被引數以此 includeSelfCitations 值與其他 SciVal 指標同一次請求取得，不另外呼叫 citations()
    scival_include_self: str | None

    async def citations(self, client: ElsevierClient, ids: list[str], *, year_range: str,
                        use_cache: bool) -> dict[str, int]: ...


class SciValParamStrategy:
    id = "scival_param"
    label = "SciVal includeSelfCitations=false"
    verified = False  # 實測 true/false 結果相同，待使用者依設計 §7.3 驗證
    scival_include_self = "false"


class NoExclusionStrategy:
    id = "none"
    label = "不排除自我引用"
    verified = True
    scival_include_self = "true"


class ScopusCitationOverviewStrategy:
    id = "scopus_citation_overview"
    label = "Scopus Citation Overview（exclude-self）"
    verified = False
    scival_include_self = None

    async def citations(self, client, ids, *, year_range, use_cache):
        # 這支 API 需要另外申請授權；取得授權後再依實際回應格式實作
        raise ElsevierError(None, False, "Scopus Citation Overview API 尚未取得授權，請改用其他策略")


STRATEGIES: dict[str, SelfCitationStrategy] = {
    s.id: s for s in (SciValParamStrategy(), ScopusCitationOverviewStrategy(), NoExclusionStrategy())
}


def get_strategy(strategy_id: str) -> SelfCitationStrategy:
    return STRATEGIES[strategy_id]
