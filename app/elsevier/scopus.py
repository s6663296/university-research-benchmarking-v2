"""Scopus Search：以單位英文名稱取得期間內所有論文（設計 §6 步驟 4）。"""

from dataclasses import dataclass

from app.elsevier.client import ElsevierClient

SEARCH_PATH = "/content/search/scopus"
PAGE_SIZE = 25


def build_query(dept_en: str, year_from: int, year_to: int) -> str:
    name = dept_en.replace('"', "").strip()
    return (f'AFFIL("{name}") AND AFFILCOUNTRY(Taiwan) '
            f"AND PUBYEAR > {year_from - 1} AND PUBYEAR < {year_to + 1}")


@dataclass(frozen=True)
class ScopusDoc:
    scopus_id: str
    eid: str


async def search_docs(client: ElsevierClient, query: str, *, use_cache: bool) -> list[ScopusDoc]:
    docs: dict[str, ScopusDoc] = {}
    cursor = "*"
    while True:
        data = await client.get_json(SEARCH_PATH, {"query": query, "count": PAGE_SIZE, "cursor": cursor},
                                     use_cache=use_cache)
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
