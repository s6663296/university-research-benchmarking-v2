"""OpenAlex：排除自我引用時，用來決定「先查哪幾篇」的提示（免費、不占 Elsevier 額度）。

只提供提示，數字一律由 Scopus 決定（見 app/elsevier/inset_selfcite.py）：OpenAlex 指錯只會多查一次，
漏掉的由 Scopus 整組檢查補上。所以這裡任何錯誤（連線失敗、找不到論文）都當作「沒有提示」，不影響結果。
"""

import httpx

WORKS_URL = "https://api.openalex.org/works"
BATCH = 50          # 一次以 DOI 查幾篇（filter 的 | 清單）
TIMEOUT = 60.0


async def cited_within(dois: dict[str, str]) -> set[str]:
    """dois：單位論文的 Scopus ID → DOI。回傳 OpenAlex 認為「被單位內其他論文（或自己）引用」的 Scopus ID。"""
    by_doi = {d.lower(): sid for sid, d in dois.items() if d}
    works: dict[str, dict] = {}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as http:
            batch = list(by_doi)
            for i in range(0, len(batch), BATCH):
                r = await http.get(WORKS_URL, params={"filter": "doi:" + "|".join(batch[i:i + BATCH]),
                                                      "per-page": BATCH, "select": "id,doi,referenced_works"})
                r.raise_for_status()
                for w in r.json().get("results", []):
                    doi = (w.get("doi") or "").lower().removeprefix("https://doi.org/")
                    if doi in by_doi:
                        works[by_doi[doi]] = w
    except (httpx.HTTPError, ValueError):
        pass   # 已取得的部分照常使用，其餘論文交由 Scopus 整組檢查
    sid_of = {w["id"]: sid for sid, w in works.items() if w.get("id")}
    return {sid_of[r] for w in works.values() for r in w.get("referenced_works") or [] if r in sid_of}
