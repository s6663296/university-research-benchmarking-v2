"""校名互相包含的學校（台大 ⊂ 台科大）：機構代碼分不出來的論文，逐篇讀機構全文判斷。

build_query 已排除只在他校機構代碼下的論文，但「台科大化工 + 台大其他系」這種論文兩個代碼都有，系所片語又
比對得到台科大的機構字串，查詢分不出來（2026-11-22 抽樣台大化工，12 篇中有 3 篇是這種）。這些論文用
Abstract Retrieval 讀每個機構的原文，去掉他校校名後，仍有一個機構同時寫了本系所與本校才保留。
"""

import asyncio
import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.elsevier.affiliation import name_key
from app.elsevier.cache import ApiCache
from app.elsevier.client import ElsevierClient
from app.elsevier.inset_selfcite import ABSTRACT_PATH, PERMANENT_DAYS, as_list
from app.elsevier.scopus import NEAR, ScopusDoc, _clean, _dept_terms, _split, search_docs
from app.textnorm import split_names

AFFIL_TEXTS_ENDPOINT = "/content/abstract/affiliations"   # 快取中只存機構原文，不存整份回應


def _words(text: str) -> str:
    """比對用：小寫英數字詞，去掉 and（Scopus 忽略「&」與 and 的差別），前後留空白以便整詞比對。"""
    return " " + " ".join(w for w in re.findall(r"[a-z0-9]+", text.lower()) if w != "and") + " "


@dataclass(frozen=True)
class Overlap:
    dept: str
    school: str | None        # 只有 MultiOverlap 中、沒寫校名的名稱才是 None
    others: tuple[str, ...]   # 名稱包含本校校名的他校

    def ambiguous_query(self, query: str) -> str:
        """單位查詢結果中，系所片語也比對得到他校機構字串的論文。"""
        terms = _dept_terms(self.dept)
        return f"({query}) AND (" + " OR ".join(f'AFFIL({terms} W/{NEAR} "{o}")' for o in self.others) + ")"

    def matches(self, affiliations: Iterable[str]) -> bool:
        """有一個機構去掉他校校名後，仍同時含本系所與本校校名。"""
        dept, school = _words(self.dept), _words(self.school)
        others = sorted((_words(o) for o in self.others), key=len, reverse=True)
        for text in affiliations:
            t = _words(text)
            for o in others:
                t = t.replace(o, " | ")
            if (self.school is None or school in t) and dept in t:
                return True
        return False


@dataclass(frozen=True)
class MultiOverlap:
    """mapping 寫了多個英文名稱（分號分隔）且其中有校名互相包含的學校：任一名稱比對得到就保留。"""
    parts: tuple[Overlap, ...]   # 每個名稱一個；沒有名稱相近他校的名稱 others 為空

    def ambiguous_query(self, query: str) -> str:
        clauses = [f'AFFIL({_dept_terms(p.dept)} W/{NEAR} "{o}")' for p in self.parts for o in p.others]
        return f"({query}) AND (" + " OR ".join(clauses) + ")"

    def matches(self, affiliations: Iterable[str]) -> bool:
        affiliations = list(affiliations)
        return any(p.matches(affiliations) for p in self.parts)


def _part(name: str, cleaned: list[str]) -> Overlap:
    dept, school = _split(_clean(name), cleaned)
    if school is None:
        return Overlap(dept, None, ())
    own = name_key(school)
    return Overlap(dept, school, tuple(o for o in cleaned if name_key(o) != own and own in name_key(o)))


def overlap_of(dept_en: str, schools: Iterable[str]) -> Overlap | MultiOverlap | None:
    cleaned = [_clean(s) for s in schools]
    parts = [_part(name, cleaned) for name in split_names(dept_en) if name]
    if not any(p.others for p in parts):
        return None
    return parts[0] if len(parts) == 1 else MultiOverlap(tuple(parts))


def affiliation_texts(data: dict) -> list[str]:
    """Abstract Retrieval（FULL）中每個作者群的機構原文；沒有原文時用機構名稱各段組合。"""
    head = ((((data.get("abstracts-retrieval-response") or {}).get("item") or {}).get("bibrecord") or {})
            .get("head") or {})
    out = []
    for group in as_list(head.get("author-group")):
        affil = (group or {}).get("affiliation") or {}
        parts = [str(o.get("$", "")) if isinstance(o, dict) else str(o) for o in as_list(affil.get("organization"))]
        text = affil.get("ce:source-text") or ", ".join(p for p in parts if p)
        if text:
            out.append(text)
    return out


async def _texts(client: ElsevierClient, ids: list[str]) -> dict[str, list[str]]:
    cache = ApiCache(client.cache.session_factory, PERMANENT_DAYS) if client.cache is not None else None
    keys = {sid: f"affiliations:{sid}" for sid in ids}
    cached = cache.get_many(list(keys.values())) if cache else {}
    out = {sid: cached[k]["affiliations"] for sid, k in keys.items() if k in cached}

    async def one(sid: str) -> None:
        data = await client.get_json(ABSTRACT_PATH.format(sid), {"view": "FULL"}, use_cache=False, store=False)
        out[sid] = affiliation_texts(data)
        if cache:
            cache.put(keys[sid], AFFIL_TEXTS_ENDPOINT, {"affiliations": out[sid]})

    await asyncio.gather(*(one(sid) for sid in ids if sid not in out))
    return out


async def drop_other_schools(client: ElsevierClient, query: str, docs: list[ScopusDoc],
                             overlap: Overlap | MultiOverlap | None, *,
                             use_cache: bool, strict: bool = False) -> list[ScopusDoc]:
    """剔除機構全文中其實沒有本系所與本校的論文；只檢查查詢分不出來的那些。"""
    if overlap is None or not docs:
        return docs
    ambiguous = await search_docs(client, overlap.ambiguous_query(query), use_cache=use_cache, strict=strict)
    texts = await _texts(client, [d.scopus_id for d in ambiguous])
    drop = {sid for sid, t in texts.items() if not overlap.matches(t)}
    return [d for d in docs if d.scopus_id not in drop]


async def unit_docs(client: ElsevierClient, query: str, overlap: Overlap | MultiOverlap | None, *, use_cache: bool,
                    strict: bool = False) -> list[ScopusDoc]:
    """單位的論文：Scopus 查詢結果再經 drop_other_schools。"""
    docs = await search_docs(client, query, use_cache=use_cache, strict=strict)
    return await drop_other_schools(client, query, docs, overlap, use_cache=use_cache, strict=strict)
