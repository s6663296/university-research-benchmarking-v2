"""組內互引：SciVal 對 Publication Set 排除自我引用的定義（設計 §7.3）。

SciVal 網頁的 Publication Set 取消勾選「Include self-citations」時，只排除「同一組論文彼此之間的引用」：
2026-11-10 以 7 篇論文的 Publication Set 實測，含自我引用 44、排除後 40，少掉的 4 次恰好是這 7 篇之間的互相引用，
逐年也完全吻合；這 7 篇以外、由共同作者發表的論文引用則沒有排除。

所以單位的自我引用 = 單位論文（SciVal 有收錄的）之間的引用次數，只需要每篇論文的參考文獻：
Abstract Retrieval（view=FULL）每篇 1 次請求、一次就是完整清單（view=REF 每頁 40 筆、翻頁容易被拒）。
參考文獻在論文發表後不會改變，永久快取；同一次分析共用一個實例，各單位重疊的論文只查一次。
"""

import asyncio

from app.elsevier.cache import ApiCache
from app.elsevier.client import ElsevierClient, ElsevierError

ABSTRACT_PATH = "/content/abstract/scopus_id/{}"
REFS_ENDPOINT = "/content/abstract/refs"   # 快取中參考文獻的分類名稱（只存 Scopus ID 清單，不存整份回應）
PERMANENT_DAYS = 36500


def as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def full_view_refs(data: dict) -> list[str]:
    """view=FULL 的參考文獻在 item.bibrecord.tail.bibliography.reference，Scopus ID 是 idtype=SGR 的 itemid。"""
    resp = data.get("abstracts-retrieval-response") or {}
    bib = (((resp.get("item") or {}).get("bibrecord") or {}).get("tail") or {}).get("bibliography") or {}
    ids = []
    for ref in as_list(bib.get("reference")):
        items = as_list(((ref.get("ref-info") or {}).get("refd-itemidlist") or {}).get("itemid"))
        ids += [str(i["$"]) for i in items if i.get("@idtype") == "SGR" and i.get("$")]
    return list(dict.fromkeys(ids))


def count_in_set(refs: dict[str, set[str]]) -> dict[str, int]:
    """refs：組內每篇論文 → 它引用的 Scopus ID。回傳每篇被組內其他論文引用的次數（0 次的不列出）。"""
    counts: dict[str, int] = {}
    for citing, cited in refs.items():
        for sid in cited:
            if sid != citing and sid in refs:
                counts[sid] = counts.get(sid, 0) + 1
    return counts


def refs_key(scopus_id: str) -> str:
    return f"refs:{scopus_id}"


class InSetSelfCitations:
    def __init__(self, client: ElsevierClient):
        self.client = client
        self._ref_cache: ApiCache | None = None
        self._refs: dict[str, asyncio.Future] = {}   # 論文 → 它引用的 Scopus ID
        self.fetched = 0                              # 這次實際向 Abstract Retrieval 要的篇數

    async def count(self, ids: list[str], citations: dict[str, int | None]) -> dict[str, int]:
        """ids 是一個單位的論文（即 SciVal 的 Publication Set）；回傳每篇被組內其他論文引用的次數。

        組內沒有任何論文被引用過時，不可能有組內互引，不查參考文獻。
        """
        ids = list(dict.fromkeys(ids))
        if len(ids) < 2 or not any((citations.get(s) or 0) > 0 for s in ids):
            return {}
        self._preload(ids)
        results = await asyncio.gather(*(self._refs_of(s) for s in ids), return_exceptions=True)
        for r in results:   # 等全部完成才拋出：其餘請求的結果照常存進快取
            if isinstance(r, BaseException):
                raise r
        return count_in_set(dict(zip(ids, results)))

    @property
    def ref_cache(self) -> ApiCache | None:
        """第一次查參考文獻時才建立：沒有用到這個策略的流程不受 client 有沒有快取影響。"""
        if self._ref_cache is None and self.client.cache is not None:
            self._ref_cache = ApiCache(self.client.cache.session_factory, PERMANENT_DAYS)
        return self._ref_cache

    def _preload(self, ids: list[str]) -> None:
        """一次從快取讀出已知的參考文獻，避免逐篇查資料庫。"""
        missing = [s for s in ids if s not in self._refs]
        if self.ref_cache is None or not missing:
            return
        cached = self.ref_cache.get_many([refs_key(s) for s in missing])
        loop = asyncio.get_running_loop()
        for sid in missing:
            entry = cached.get(refs_key(sid))
            if entry is not None:
                self._refs[sid] = loop.create_future()
                self._refs[sid].set_result(set(entry["refs"]))

    def _refs_of(self, sid: str) -> asyncio.Future:
        if sid not in self._refs:
            self._refs[sid] = asyncio.ensure_future(self._fetch(sid))
        return self._refs[sid]

    async def _fetch(self, sid: str) -> set[str]:
        try:
            data = await self.client.get_json(ABSTRACT_PATH.format(sid), {"view": "FULL"},
                                              use_cache=False, store=False)
            refs = full_view_refs(data)
        except ElsevierError as e:
            if e.status != 404:   # Scopus 已移除的論文：當作沒有參考文獻
                raise
            refs = []
        self.fetched += 1
        if self.ref_cache is not None:   # 逐篇存下，額度用完中斷後可以接著做
            self.ref_cache.put(refs_key(sid), REFS_ENDPOINT, {"refs": refs})
        return set(refs)
