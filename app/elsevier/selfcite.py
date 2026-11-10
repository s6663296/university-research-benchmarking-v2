"""排除自我引用的策略（設計 §7.3）。驗證結果出來後只需在設定頁切換。"""

from typing import Protocol

from app.elsevier.client import ElsevierClient, ElsevierError


class SelfCitationStrategy(Protocol):
    id: str
    label: str
    verified: bool
    # 不為 None 時，被引數以此 includeSelfCitations 值與其他 SciVal 指標同一次請求取得，不另外呼叫 citations()
    scival_include_self: str | None
    # True 時，被引數再減去單位論文之間的互相引用（SciVal Publication Set 的定義，見 inset_selfcite）
    in_set: bool

    async def citations(self, client: ElsevierClient, ids: list[str], *, year_range: str,
                        use_cache: bool) -> dict[str, int]: ...


class InSetStrategy:
    id = "scival_pubset"
    # 2026-11-10 以 7 篇論文的 SciVal Publication Set 驗證：網頁含／排除自我引用 44／40，差額恰為組內互引 4 次
    label = "排除單位論文之間的互相引用（與 SciVal 網頁 Publication Set 相同）"
    verified = True
    scival_include_self = "true"
    in_set = True


class SciValParamStrategy:
    id = "scival_param"
    # 2026-11-10 以 scripts/verify_self_citation.py 驗證：確定有自我引用的論文，true/false 回傳的被引數仍相同
    label = "SciVal includeSelfCitations=false（已驗證無效，數字含自我引用）"
    verified = True
    scival_include_self = "false"
    in_set = False


class NoExclusionStrategy:
    id = "none"
    label = "不排除自我引用"
    verified = True
    scival_include_self = "true"
    in_set = False


class ScopusCitationOverviewStrategy:
    id = "scopus_citation_overview"
    label = "Scopus Citation Overview（exclude-self）"
    verified = False
    scival_include_self = None
    in_set = False

    async def citations(self, client, ids, *, year_range, use_cache):
        # 這支 API 需要另外申請授權；取得授權後再依實際回應格式實作
        raise ElsevierError(None, False, "Scopus Citation Overview API 尚未取得授權，請改用其他策略")


STRATEGIES: dict[str, SelfCitationStrategy] = {
    s.id: s for s in (InSetStrategy(), SciValParamStrategy(), ScopusCitationOverviewStrategy(), NoExclusionStrategy())
}


def get_strategy(strategy_id: str) -> SelfCitationStrategy:
    return STRATEGIES[strategy_id]
