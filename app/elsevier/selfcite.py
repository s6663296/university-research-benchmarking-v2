"""排除自我引用的策略（設計 §7.3），在設定頁切換。

被引數一律由 SciVal 取得含自我引用的數字（includeSelfCitations=true，與其他指標同一次請求），
需要排除時再由 inset_selfcite 減去組內互引。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SelfCitationStrategy:
    id: str
    label: str
    # True 時，被引數減去單位論文之間的互相引用（SciVal Publication Set 的定義，見 inset_selfcite）
    in_set: bool
    # True 時先用 Scopus REF() 篩出引用者，只抓它們的參考文獻（ref_selfcite）；False 時抓所有單位論文的參考文獻
    ref_filter: bool = False


STRATEGIES: dict[str, SelfCitationStrategy] = {s.id: s for s in (
    # 2026-11-10 以 7 篇論文的 SciVal Publication Set 驗證：網頁含／排除自我引用 44／40，差額恰為組內互引 4 次
    # 兩種算法結果相同，差在要下載多少篇參考文獻
    SelfCitationStrategy("scival_pubset_ref", "排除單位論文之間的互相引用（與 SciVal 網頁 Publication Set 相同；"
                                              "REF() 篩選引用者後只抓它們的參考文獻）", in_set=True, ref_filter=True),
    SelfCitationStrategy("scival_pubset", "排除單位論文之間的互相引用（與 SciVal 網頁 Publication Set 相同；"
                                          "抓所有單位論文的參考文獻）", in_set=True),
    SelfCitationStrategy("none", "不排除自我引用", in_set=False),
)}

DEFAULT_STRATEGY = "scival_pubset_ref"

# 已移除的策略，只為了舊分析還能顯示名稱；這些分析的被引數都含自我引用
REMOVED: dict[str, SelfCitationStrategy] = {s.id: s for s in (
    SelfCitationStrategy("scival_param", "SciVal includeSelfCitations=false（已移除：參數無效，數字含自我引用）",
                         in_set=False),
    SelfCitationStrategy("scopus_citation_overview", "Scopus Citation Overview（已移除）", in_set=False),
)}


def get_strategy(strategy_id: str) -> SelfCitationStrategy:
    return STRATEGIES.get(strategy_id) or REMOVED[strategy_id]


def usable_strategy_id(strategy_id: str) -> str:
    """設定中存的是已移除的策略時（舊版存下的），改用預設策略。"""
    return strategy_id if strategy_id in STRATEGIES else DEFAULT_STRATEGY
