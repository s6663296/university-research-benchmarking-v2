"""組內互引：SciVal 對 Publication Set 排除自我引用的定義（設計 §7.3）。

SciVal 網頁的 Publication Set 取消勾選「Include self-citations」時，只排除「同一組論文彼此之間的引用」：
2026-11-10 以 7 篇論文的 Publication Set 實測，含自我引用 44、排除後 40，少掉的 4 次恰好是這 7 篇之間的互相引用，
逐年也完全吻合；這 7 篇以外、由共同作者發表的論文引用則沒有排除。
SciVal API 的 includeSelfCitations 對逐篇指標沒有作用（2026-11-21 以 100 篇重測，true／false 完全相同），只能自己算。

所以單位的自我引用 = 單位論文（SciVal 有收錄的）之間的引用次數，由每篇論文的參考文獻算出：
  0. 組內沒有任何論文被引用過（SciVal 被引數 > 0）時不必計算
  1. 參考文獻以論文為單位存在永久快取，各單位、各次分析共用（跨系合著只下載一次）
  2. 快取中已有的論文，用 Scopus Search（EID 清單 AND LOAD-DATE AFT <上次確認日>，每 80 篇 1 次）
     找出之後被 Scopus 重載的紀錄，只重抓這些
  3. 缺少或重載的論文用 Abstract Retrieval（view=FULL，每篇 1 次）取得，核對參考文獻筆數完整才存入快取；
     缺少的不必等第 2 步的查詢，直接下載
  4. 組內互引超過 SciVal 被引數時表示兩邊資料不一致，該單位失敗，資料來源更新後再續查
第一次分析要補齊所有論文的參考文獻（大型合作論文一篇 5–6 MB、10 秒以上），之後幾乎只花 Scopus Search。
額度快用完時 client 會等到重置後自動續查（executor 開啟 wait_for_quota_reset）。
"""

import asyncio
from collections.abc import Awaitable, Iterable
from datetime import datetime, timedelta

from app.db.models import REFS_ENDPOINT   # 快取中參考文獻的分類名稱（只存 Scopus ID 清單，不存整份回應）
from app.elsevier.cache import ApiCache, cache_key
from app.elsevier.client import ElsevierClient, ElsevierError
from app.elsevier.scopus import search_docs

ABSTRACT_PATH = "/content/abstract/scopus_id/{}"
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


def check_totals(counts: dict[str, int], citations: dict[str, int | None]) -> None:
    """組內互引超過 SciVal 被引數時表示兩邊資料不一致，該單位失敗，資料來源更新後再續查。"""
    for sid, count in counts.items():
        total = citations.get(sid)
        if total is not None and count > total:
            raise ElsevierError(200, True, f"論文 {sid} 的組內互引 {count} 超出 SciVal 被引數 {total}，"
                                              "請於資料來源更新後續查")


def refs_key(scopus_id: str) -> str:
    return f"refs:{scopus_id}"


async def _all(awaitables: Iterable[Awaitable]) -> list:
    """等全部完成後才拋出第一個錯誤：其餘請求的結果照常存進快取，也不會留下「exception was never retrieved」。"""
    results = await asyncio.gather(*awaitables, return_exceptions=True)
    for r in results:
        if isinstance(r, BaseException):
            raise r
    return results


class InSetSelfCitations:
    """同一次分析共用一個實例：已確認的參考文獻在各單位間共用。

    各單位同時計算、互不等待：每篇論文由第一個需要它的單位負責確認（查快取是否重載、下載），
    其他需要同一篇的單位只等那一篇。下載請求依送出順序排隊，所以先到的單位先完成。
    """

    def __init__(self, client: ElsevierClient):
        self.client = client
        # 論文 → 它引用的 Scopus ID（這次分析已確認是最新的）；還在確認的也在這裡，其他單位直接等結果
        self._refs: dict[str, asyncio.Future] = {}
        self._ref_cache: ApiCache | None = None
        self.fetched = 0                              # 這次實際向 Abstract Retrieval 要的篇數
        self.searches = 0                             # 這次為了確認快取是否過期送出的 Scopus Search 查詢數
        self.refs_total = 0                           # 進度：需要下載參考文獻的篇數，各單位累計
        self.refs_done = 0                            # 進度：其中已下載的篇數

    async def count(self, ids: list[str], citations: dict[str, int | None], *,
                    query: str | None = None) -> dict[str, int]:
        """ids 是一個單位的論文（即 SciVal 的 Publication Set）；回傳每篇被組內論文引用的次數。query 不使用。"""
        ids = list(dict.fromkeys(ids))
        if not any((citations.get(s) or 0) > 0 for s in ids):   # 組內沒有任何論文被引用過，不可能有組內互引
            return {}
        counts = count_refs(await self.refs_of(ids), ids)
        check_totals(counts, citations)
        return counts

    async def refs_of(self, ids: list[str]) -> dict[str, set[str]]:
        """這些論文各自引用的 Scopus ID（快取中已確認的直接用，其餘確認或下載）。"""
        mine = self._claim(ids)
        if mine:
            await self._verify(mine)
        # shield：這個單位被取消時，不要連帶取消其他單位也在等的論文
        refs = await _all(asyncio.shield(self._refs[sid]) for sid in ids)
        return dict(zip(ids, refs))

    def _claim(self, ids: list[str]) -> list[str]:
        """這個單位負責確認的論文：還沒有單位在確認的，以及之前確認失敗的（例如額度用完），之後可以再查。"""
        loop = asyncio.get_running_loop()
        mine = []
        for sid in ids:
            fut = self._refs.get(sid)
            if fut is None or (fut.done() and (fut.cancelled() or fut.exception() is not None)):
                self._refs[sid] = loop.create_future()
                mine.append(sid)
        return sorted(mine)

    async def _verify(self, mine: list[str]) -> None:
        try:
            # 每組 80 個 EID，查詢低於 Scopus 網址上限；各組同時進行
            await _all(self._verify_group(mine[i:i + 80]) for i in range(0, len(mine), 80))
        except BaseException as e:   # 沒確認到的論文：正在等它的單位一起失敗，之後的單位會重查
            error = e if isinstance(e, Exception) else ElsevierError(None, True, "參考文獻的確認已中止")
            for sid in mine:
                fut = self._refs[sid]
                if not fut.done():
                    fut.set_exception(error)
                    fut.exception()   # 已交給等待的單位處理，不必再記錄「exception was never retrieved」
            raise

    async def _verify_group(self, group: list[str]) -> None:
        """快取中沒有的論文直接下載；有的先以 EID 查 Scopus 之後是否重載，只重抓重載的。完成一組就存下進度。"""
        cache = self.ref_cache
        started = datetime.now().astimezone()
        rows = cache.get_many_dated([refs_key(sid) for sid in group]) if cache else {}
        known = {}
        dates = []
        for sid in group:
            entry = rows.get(refs_key(sid))
            if entry:
                fetched, data = entry
                # 舊格式也會將 404 存成空清單；要以有效的書目回應再確認一次。
                if data.get("refs") or data.get("complete"):
                    known[sid] = set(data["refs"])
                    dates.append(fetched.astimezone().date())
        checkpoint = cache_key("selfcite/reference-check", {"ids": group})
        missing = [sid for sid in group if sid not in known]
        self.refs_total += len(missing)

        async def recheck() -> None:
            checked = cache.get(checkpoint) if cache else None
            since = min(dates)
            if checked:
                since = max(since, datetime.fromisoformat(checked["started"]).date())
            # LOAD-DATE 精度是日；重疊邊界日期，涵蓋時區與同日的後續更正。
            boundary = (since - timedelta(days=2)).strftime("%Y%m%d")
            terms = " OR ".join(f"EID(2-s2.0-{sid})" for sid in known)
            self.searches += 1
            changed = await search_docs(self.client, f"({terms}) AND LOAD-DATE AFT {boundary}",
                                        use_cache=False, strict=True)
            reloaded = {doc.scopus_id for doc in changed}
            if reloaded - known.keys():
                raise ElsevierError(200, True, "Scopus 更新清單含查詢範圍外的論文")
            for sid in known.keys() - reloaded:
                self._refs[sid].set_result(known[sid])
            self.refs_total += len(reloaded)
            await _all(self._download(sid) for sid in sorted(reloaded))

        await _all([*(self._download(sid) for sid in missing), *([recheck()] if known else [])])
        if cache:
            cache.put(checkpoint, "selfcite/reference-check", {"started": started.isoformat()})

    async def _download(self, sid: str) -> None:
        """每篇成功就寫入快取。停用分析時不會遺失已下載的書目。"""
        self._refs[sid].set_result(await self._read_complete_refs(sid))

    async def _read_complete_refs(self, sid: str) -> set[str]:
        path = ABSTRACT_PATH.format(sid)
        data = await self.client.get_json(path, {"view": "FULL"}, use_cache=False, store=False)
        root = data.get("abstracts-retrieval-response") or {}
        record = (root.get("item") or {}).get("bibrecord") or {}
        bib = (record.get("tail") or {}).get("bibliography") or {}
        entries = as_list(bib.get("reference"))
        core_id = (root.get("coredata") or {}).get("dc:identifier")
        if core_id and core_id != f"SCOPUS_ID:{sid}":
            raise ElsevierError(200, True, f"Scopus 回傳的論文與 {sid} 不符")
        if core_id == f"SCOPUS_ID:{sid}" and "tail" in record and record["tail"] is None:
            # FULL 的勘誤紀錄可能明確給 tail:null，代表索引中沒有書目。
            total = 0
        elif not bib:
            # 部分零參考文獻紀錄省略 bibliography；使用 REF 的總筆數確認。
            check = await self.client.get_json(path, {"view": "REF"}, use_cache=False, store=False)
            total = check.get("abstracts-retrieval-response", {}).get("references", {}).get("@total-references")
        else:
            total = bib.get("@refcount")
        if not str(total).isdigit() or int(total) != len(entries):
            raise ElsevierError(200, True, f"Scopus 論文 {sid} 的參考文獻不完整")
        refs = full_view_refs(data)
        if self.ref_cache:
            self.ref_cache.put(refs_key(sid), REFS_ENDPOINT, {"refs": refs, "complete": True})
        self.fetched += 1
        self.refs_done += 1
        return set(refs)

    @property
    def ref_cache(self) -> ApiCache | None:
        """第一次查參考文獻時才建立：沒有用到這個策略的流程不受 client 有沒有快取影響。"""
        if self._ref_cache is None and self.client.cache is not None:
            self._ref_cache = ApiCache(self.client.cache.session_factory, PERMANENT_DAYS)
        return self._ref_cache
