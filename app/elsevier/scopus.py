"""Scopus Search：以單位英文名稱取得期間內所有論文（設計 §6 步驟 4）。"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.elsevier.client import ElsevierClient

SEARCH_PATH = "/content/search/scopus"
PAGE_SIZE = 200  # 只取 id 時一頁最多 200 筆；不指定 field 的完整欄位一頁只能 25 筆
FIELDS = "dc:identifier,eid"


# 系所名稱與校名之間允許的字數：可容納「Department of Finance, College of Management and Center for
# Research in Econometric Theory and Applications, National Taiwan University」這類夾了中心名稱的寫法
NEAR = 15


def _clean(text: str) -> str:
    """移除會破壞查詢語法的引號與括號，以及 mapping 備註混入的中文（如「(沒有論文)」）。"""
    text = re.sub(r"[　-鿿＀-￯]+", " ", text)
    return " ".join(re.sub(r'["()]', " ", text).split())


def _school_part(name: str) -> str | None:
    """「系所, 校名」或「校名, 系所」中含 University 的那一段；都沒有時取最後一段。"""
    if "," not in name:
        return None
    first, *_, last = [part.strip() for part in name.split(",")]
    for part in (last, first):
        if "universit" in part.lower():
            return part
    return last


def school_names(dept_ens: Iterable[str]) -> list[str]:
    """mapping 中出現過、含 University 的英文校名，不分大小寫去重、保留第一次出現的寫法。"""
    seen: dict[str, str] = {}
    for en in dept_ens:
        name = _school_part(_clean(en))
        if name and "universit" in name.lower():
            seen.setdefault(name.lower(), name)
    return list(seen.values())


def _split(name: str, schools: list[str]) -> tuple[str, str | None]:
    """把英文名稱拆成（系所, 校名）；優先比對已知校名（最長者優先），找不到才依逗號切。"""
    lower = name.lower()
    for school in sorted(schools, key=len, reverse=True):
        s = school.lower()
        if lower.endswith(s) and len(lower) > len(s):
            return name[: -len(s)].strip(" ,"), school
        if lower.startswith(s) and len(lower) > len(s):
            return name[len(s):].strip(" ,"), school
    school = _school_part(name)
    if school is None:
        return name, None
    parts = [part.strip() for part in name.split(",")]
    parts.remove(school)
    return ", ".join(parts), school


def _dept_terms(dept: str) -> str:
    """系所片語；名稱含 and 時加上省略 and 的寫法（Scopus 忽略「&」，「Industrial & Information」等同少了 and）。"""
    terms = [dept]
    without_and = " ".join(w for w in dept.split() if w.lower() != "and")
    if without_and != dept:
        terms.append(without_and)
    quoted = " OR ".join(f'"{t}"' for t in terms)
    return f"({quoted})" if len(terms) > 1 else quoted


def build_query(dept_en: str, year_from: int, year_to: int, schools: Iterable[str] = ()) -> str:
    """系所片語與校名片語須出現在同一機構字串的鄰近位置（W/NEAR，不分先後）。

    不加引號時 Scopus 只要求各字出現在論文任一機構中：台大資管 2021–2025 會抓到 2914 篇，
    其中多為北商大、高科大、政大等他校論文；整段加引號又會漏掉「National Taiwan University,
    Department of …」或中間夾「College of Management」的寫法，且會把台科大一併算入。
    若其他已知校名包含本校校名（台大 ⊂ 台科大），再排除他校同名系所。
    """
    cleaned = [_clean(s) for s in schools]
    dept, school = _split(_clean(dept_en), cleaned)
    if school is None:
        affil = f'AFFIL("{dept}")'
    else:
        terms = _dept_terms(dept)
        affil = f'AFFIL({terms} W/{NEAR} "{school}")'
        for other in cleaned:
            if other.lower() != school.lower() and school.lower() in other.lower():
                affil += f' AND NOT AFFIL({terms} W/{NEAR} "{other}")'
    # AND NOT 的優先順序最低，必須加括號，否則後面的國家與年份條件會被一起排除
    return (f"({affil}) AND AFFILCOUNTRY(Taiwan) "
            f"AND PUBYEAR > {year_from - 1} AND PUBYEAR < {year_to + 1}")


@dataclass(frozen=True)
class ScopusDoc:
    scopus_id: str
    eid: str


async def search_docs(client: ElsevierClient, query: str, *, use_cache: bool) -> list[ScopusDoc]:
    docs: dict[str, ScopusDoc] = {}
    cursor = "*"
    while True:
        params = {"query": query, "count": PAGE_SIZE, "field": FIELDS, "cursor": cursor}
        data = await client.get_json(SEARCH_PATH, params, use_cache=use_cache)
        result = data.get("search-results", {})
        total = int(result.get("opensearch:totalResults") or 0)
        page = [e for e in result.get("entry", []) if "eid" in e and "dc:identifier" in e]
        for e in page:
            sid = e["dc:identifier"].replace("SCOPUS_ID:", "")
            docs.setdefault(sid, ScopusDoc(sid, e["eid"]))
        next_cursor = result.get("cursor", {}).get("@next")
        if total == 0 or not page or not next_cursor or next_cursor == cursor or len(docs) >= total:
            return list(docs.values())
        cursor = next_cursor
