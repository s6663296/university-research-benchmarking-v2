"""組內互引：SciVal 對 Publication Set 排除自我引用的定義（設計 §7.3）。

SciVal 網頁的 Publication Set 取消勾選「Include self-citations」時，只排除「同一組論文彼此之間的引用」：
2026-11-10 以 7 篇論文的 Publication Set 實測，含自我引用 44、排除後 40，少掉的 4 次恰好是這 7 篇之間的互相引用，
逐年也完全吻合；這 7 篇以外、由共同作者發表的論文引用則沒有排除。

所以單位的自我引用 = 單位論文（SciVal 有收錄的）之間的引用次數。每個單位的算法：
  0. 只看被引用過的論文 P（SciVal 被引數 > 0）；沒有就不必查
  1. 篩選（Scopus Search，約每 100 篇 1 次）：(單位查詢) AND (REF(P1) OR …)，
     取回「引用了單位論文的單位論文」C。中央財金 145 篇只有 47 篇，其餘不必查參考文獻
  2. C 的參考文獻：永久快取中已有的直接用；其餘用 Abstract Retrieval（view=FULL，每篇 1 次）取得並永久快取
  3. Abstract Retrieval 的額度用完時，改用只花 Scopus Search 的做法：
     OpenAlex 指出可能被單位內引用的 P → 逐篇 (單位查詢) AND REF(P) 取得精確的引用者；
     其餘 P 整組查一次，有結果才拆半，直到單篇。OpenAlex 只決定查詢順序，數字一律來自 Scopus
  中央財金 145 篇三種做法都和 SciVal 網頁逐篇一致（100 次）：第 2 步 47 次 Abstract Retrieval；
  第 3 步 44 次 Scopus Search + 0 次 Abstract Retrieval。

REF(<Scopus ID>) 可以找出引用這篇的論文，但不在官方文件中（REFEID 對這把 Key 不開放）。
每次分析先用已知的引用關係檢查一次，不符時全部改回逐篇查參考文獻（較慢，但數字一樣精確）。
"""

import asyncio
from collections.abc import Awaitable, Iterable

from app import openalex
from app.elsevier.cache import ApiCache
from app.elsevier.client import ElsevierClient, ElsevierError
from app.elsevier.scopus import SEARCH_PATH, search_docs

ABSTRACT_PATH = "/content/abstract/scopus_id/{}"
REFS_ENDPOINT = "/content/abstract/refs"   # 快取中參考文獻的分類名稱（只存 Scopus ID 清單，不存整份回應）
PERMANENT_DAYS = 36500
# 已知的引用關係：85141566632 的參考文獻有 85120042660（2026-11 以 Abstract Retrieval 與 SciVal 網頁確認）
REF_CHECK = ("85120042660", "85141566632")
# Scopus Search 的查詢長度上限：實測 2,550 字可用、3,050 字回 HTTP 400（網址太長）
MAX_QUERY = 2400


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


def count_refs(refs: dict[str, set[str]], members: Iterable[str]) -> dict[str, int]:
    """refs：引用者 → 它引用的 Scopus ID。回傳 members 中每篇被這些引用者引用的次數（0 次的不列出）。

    參考文獻列了自己的論文也算：Scopus 把它計入被引數，SciVal 排除時也會扣掉
    （2026-11-11 中央財金 145 篇：網頁排除 100 次 = 組內互引 98 次 + 引用自己 2 次）。
    """
    members = set(members)
    counts: dict[str, int] = {}
    for cited in refs.values():
        for sid in cited:
            if sid in members:
                counts[sid] = counts.get(sid, 0) + 1
    return counts


def count_in_set(refs: dict[str, set[str]]) -> dict[str, int]:
    """refs：組內每篇論文 → 它引用的 Scopus ID。回傳每篇被組內論文引用的次數。"""
    return count_refs(refs, refs)


def refs_key(scopus_id: str) -> str:
    return f"refs:{scopus_id}"


def ref_query(query: str, ids: list[str]) -> str:
    return f"({query}) AND (" + " OR ".join(f"REF({p})" for p in ids) + ")"


def ref_groups(query: str, ids: list[str]) -> list[list[str]]:
    """把論文分組，每組的 ref_query 不超過 MAX_QUERY 字（至少一篇一組）。"""
    groups: list[list[str]] = []
    size = len(query) + 10
    for p in ids:
        term = len(p) + 9   # "REF(…) OR "
        if not groups or size + term > MAX_QUERY:
            groups.append([])
            size = len(query) + 10
        groups[-1].append(p)
        size += term
    return groups


def quota_exhausted(e: ElsevierError) -> bool:
    """額度用完且短時間內不會重置（client 已換過所有 Key）。"""
    return e.status == 429 and not e.retryable


async def _all(awaitables: Iterable[Awaitable]) -> list:
    """等全部完成後才拋出第一個錯誤：其餘請求的結果照常存進快取，也不會留下「exception was never retrieved」。"""
    results = await asyncio.gather(*awaitables, return_exceptions=True)
    for r in results:
        if isinstance(r, BaseException):
            raise r
    return results


class InSetSelfCitations:
    """同一次分析共用一個實例：參考文獻、REF 檢查結果、Abstract Retrieval 額度狀態都在各單位間共用。"""

    def __init__(self, client: ElsevierClient, *, use_cache: bool = True):
        self.client = client
        self.use_cache = use_cache                    # Scopus Search 篩選結果是否沿用一般快取（參考文獻一律永久快取）
        self._ref_cache: ApiCache | None = None
        self._refs: dict[str, asyncio.Future] = {}   # 論文 → 它引用的 Scopus ID
        self._ref_ok: asyncio.Future | None = None   # REF(<Scopus ID>) 是否可用（每次分析檢查一次）
        self.abstract_exhausted = False               # Abstract Retrieval 額度用完後，其餘單位改用 Scopus Search
        self.fetched = 0                              # 這次實際向 Abstract Retrieval 要的篇數
        self.searches = 0                             # 這次為了排除自我引用送出的 Scopus Search 查詢數

    async def count(self, ids: list[str], citations: dict[str, int | None], *, query: str | None = None,
                    dois: dict[str, str] | None = None) -> dict[str, int]:
        """ids 是一個單位的論文（即 SciVal 的 Publication Set）；回傳每篇被組內論文引用的次數。

        query 是找出這些論文的 Scopus 查詢；沒有時只能逐篇查參考文獻。dois 用來對照 OpenAlex。
        """
        ids = list(dict.fromkeys(ids))
        cited = [s for s in ids if (citations.get(s) or 0) > 0]
        if not cited:   # 組內沒有任何論文被引用過，不可能有組內互引
            return {}
        if query is None or not await self._ref_usable():
            return await self._by_references(ids, ids)
        members = set(ids)
        citers = set().union(*await _all(self._search(ref_query(query, g)) for g in ref_groups(query, cited))) & members
        if not citers:
            return {}
        if not self.abstract_exhausted:
            try:
                return await self._by_references(sorted(citers), members)
            except ElsevierError as e:
                if not quota_exhausted(e):
                    raise
                self.abstract_exhausted = True
        return await self._by_cited(query, cited, members, dois or {})

    # ---- 第 2 步：引用者的參考文獻（Abstract Retrieval，永久快取）----

    async def _by_references(self, citers: list[str], members: Iterable[str]) -> dict[str, int]:
        self._preload(citers)
        refs = await _all(self._refs_of(s) for s in citers)
        return count_refs(dict(zip(citers, refs)), members)

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
        fut = self._refs.get(sid)
        # 額度用完等原因失敗的，之後可以再查
        if fut is None or (fut.done() and (fut.cancelled() or fut.exception() is not None)):
            fut = self._refs[sid] = asyncio.ensure_future(self._fetch(sid))
        return fut

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

    # ---- 第 3 步：只用 Scopus Search（OpenAlex 決定查詢順序）----

    async def _by_cited(self, query: str, cited: list[str], members: set[str],
                        dois: dict[str, str]) -> dict[str, int]:
        hinted_set = await openalex.cited_within({s: dois[s] for s in members if s in dois})
        hinted = [p for p in cited if p in hinted_set]
        found = await _all(self._search(ref_query(query, [p])) for p in hinted)
        counts = {p: len(c & members) for p, c in zip(hinted, found) if c & members}
        # OpenAlex 沒指出的（包括 OpenAlex 找不到的論文）整組檢查，有結果才拆開查
        rest = [p for p in cited if p not in hinted_set]
        groups = ref_groups(query, rest)
        found = await _all(self._search(ref_query(query, g)) for g in groups)
        for part in await _all(self._split(query, g, c & members, members) for g, c in zip(groups, found)):
            counts |= part
        return counts

    async def _split(self, query: str, group: list[str], citers: set[str], members: set[str]) -> dict[str, int]:
        """citers：引用了這組任一篇的單位論文。拆半直到單篇，單篇時的引用者數就是答案。"""
        if not citers:
            return {}
        if len(group) == 1:
            return {group[0]: len(citers)}
        left, right = group[:len(group) // 2], group[len(group) // 2:]
        left_citers = await self._search(ref_query(query, left)) & members
        # 左半沒有任何引用者時，這組的引用者都是引用右半的，不必再查
        right_citers = citers if not left_citers else await self._search(ref_query(query, right)) & members
        return {**await self._split(query, left, left_citers, members),
                **await self._split(query, right, right_citers, members)}

    # ---- Scopus Search ----

    async def _search(self, query: str) -> set[str]:
        self.searches += 1
        return {d.scopus_id for d in await search_docs(self.client, query, use_cache=self.use_cache)}

    async def _ref_usable(self) -> bool:
        if self._ref_ok is None:
            self._ref_ok = asyncio.ensure_future(self._check_ref())
        return await self._ref_ok

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
        return int(data.get("search-results", {}).get("opensearch:totalResults") or 0) == 1
