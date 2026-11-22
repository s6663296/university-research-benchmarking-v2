"""組內互引的 REF() 篩選算法（設計 §7.3，定義見 inset_selfcite）。

組內互引只可能來自「引用了單位論文的單位論文」，通常只佔單位論文的三分之一左右（中央財金 145 篇只有 47 篇），
所以先用 Scopus Search 篩出這些引用者，只抓它們的參考文獻：
  1. 被引論文（SciVal 被引數 > 0）分組，每組塞滿查詢長度：(單位查詢) AND (REF(P1) OR …)，
     結果與單位論文的交集就是引用者。各組同時送出，約每 100 篇被引論文 1 次
  2. 引用者的參考文獻與 Abstract 參考文獻算法共用（永久快取、重載檢查、每篇 1 次 Abstract Retrieval），
     各單位同時下載、重疊的論文只抓一次
  3. 組內互引 = 引用者的參考文獻中屬於單位論文的次數
篩選結果不快取（引用會增加），每次分析重查；同一天的分頁照一般快取設定沿用，暫停後續查不必重送。
REF(<Scopus ID>) 不在官方文件中（REFEID 對這把 Key 不開放），每次分析先以已知的引用關係檢查一次。
"""

import asyncio

from app.elsevier.client import ElsevierClient, ElsevierError
from app.elsevier.inset_selfcite import InSetSelfCitations, _all, check_totals, count_refs
from app.elsevier.scopus import SEARCH_PATH, search_docs

# 已知的引用關係：85141566632 的參考文獻有 85120042660（2026-11 以 Abstract Retrieval 與 SciVal 網頁確認）
REF_CHECK = ("85120042660", "85141566632")
# Scopus Search 的查詢長度上限：實測 2,550 字可用、3,050 字回 HTTP 400（網址太長）
MAX_QUERY = 2400


def ref_query(query: str, ids: list[str]) -> str:
    return f"({query}) AND (" + " OR ".join(f"REF({p})" for p in ids) + ")"


def ref_groups(query: str, ids: list[str]) -> list[list[str]]:
    """依序分組，每組的 ref_query 不超過 MAX_QUERY 字（至少一篇一組）。"""
    groups: list[list[str]] = []
    size = 0
    for p in ids:
        term = len(p) + 9   # "REF(…) OR "
        if not groups or size + term > MAX_QUERY:
            groups.append([])
            size = len(query) + 10
        groups[-1].append(p)
        size += term
    return groups


class RefSelfCitations(InSetSelfCitations):
    """同一次分析共用一個實例：參考文獻、REF() 檢查結果在各單位間共用。"""

    def __init__(self, client: ElsevierClient, *, use_cache: bool = True):
        super().__init__(client)
        self.use_cache = use_cache   # 篩選查詢是否沿用當天的一般快取（參考文獻一律永久快取）
        self._ref_ok: asyncio.Future | None = None

    async def count(self, ids: list[str], citations: dict[str, int | None], *,
                    query: str | None = None) -> dict[str, int]:
        """ids 是一個單位的論文（SciVal 的 Publication Set），query 是找出這些論文的 Scopus 查詢。"""
        ids = list(dict.fromkeys(ids))
        cited = sorted(s for s in ids if (citations.get(s) or 0) > 0)
        if not cited:   # 組內沒有任何論文被引用過，不可能有組內互引
            return {}
        if query is None:
            raise ElsevierError(None, False, "REF() 篩選需要單位的 Scopus 查詢")
        await self._ensure_ref()
        members = set(ids)
        found = await _all(self._search(ref_query(query, g)) for g in ref_groups(query, cited))
        citers = sorted(set().union(*found) & members)
        if not citers:
            return {}
        counts = count_refs(await self.refs_of(citers), members)
        check_totals(counts, citations)
        return counts

    async def _search(self, query: str) -> set[str]:
        self.searches += 1
        return {d.scopus_id for d in await search_docs(self.client, query, use_cache=self.use_cache, strict=True)}

    async def _ensure_ref(self) -> None:
        if self._ref_ok is None or (self._ref_ok.done() and self._ref_ok.exception() is not None):
            self._ref_ok = asyncio.ensure_future(self._check_ref())
        if not await asyncio.shield(self._ref_ok):
            raise ElsevierError(200, False, "Scopus 的 REF() 查詢沒有找到已知的引用關係，"
                                            "請在設定改用「全部論文參考文獻」方式排除自我引用")

    async def _check_ref(self) -> bool:
        """REF(<Scopus ID>) 不在官方文件中：用已知的引用關係確認它仍能找出引用者。"""
        cited, citing = REF_CHECK
        try:
            data = await self.client.get_json(SEARCH_PATH, {"query": f"REF({cited}) AND EID(2-s2.0-{citing})",
                                                            "count": 1, "field": "dc:identifier"},
                                              use_cache=False, store=False)
        except ElsevierError as e:
            if e.status == 400:   # 查詢語法不被接受
                return False
            raise
        self.searches += 1
        return int(data.get("search-results", {}).get("opensearch:totalResults") or 0) == 1
