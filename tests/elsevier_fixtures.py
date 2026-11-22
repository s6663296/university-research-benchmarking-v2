"""測試用：錄下的 Elsevier 回應（已移除 link）與模擬伺服器。"""

import json
from functools import cache
from pathlib import Path

import httpx

FIXTURES = Path(__file__).parent / "fixtures" / "elsevier"
SCOPUS_URL = r".*/content/search/scopus.*"
SCIVAL_URL = r".*/analytics/scival/publication/metrics.*"
ABSTRACT_URL = r".*/content/abstract/scopus_id/.*"


@cache
def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def ids67() -> list[str]:
    return [str(r["publication"]["id"]) for r in load("scival_qf_67.json")["results"]]


def scopus_pages_handler(request: httpx.Request) -> httpx.Response:
    """依 cursor 回傳 3 頁（25+25+17）。"""
    page = {"*": 1, "AoE1": 2, "AoE2": 3}.get(request.url.params.get("cursor"))
    if page is None:
        empty = {"search-results": {"opensearch:totalResults": "67", "cursor": {"@next": "AoE3"}, "entry": []}}
        return httpx.Response(200, json=empty)
    return httpx.Response(200, json=load(f"scopus_page_{page}.json"))


def scival_handler(request: httpx.Request, drop: set[str] = frozenset()) -> httpx.Response:
    """依 metricTypes 與 publicationIds 回傳錄下來的逐篇結果；與真實 API 一樣一次最多 100 篇。"""
    requested = request.url.params["publicationIds"].split(",")
    if len(requested) > 100:
        return httpx.Response(400, text="<html>HTTP Status 400 – Bad Request</html>")
    ids = set(requested) - set(drop)
    types = set(request.url.params["metricTypes"].split(","))
    data = load("scival_qf_67.json")
    cites = {str(r["publication"]["id"]): r["metrics"] for r in load("scival_cites_qf_67.json")["results"]}
    results = []
    for r in data["results"]:
        sid = str(r["publication"]["id"])
        if sid in ids:
            metrics = [m for m in r["metrics"] + cites[sid] if m["metricType"] in types]
            results.append({**r, "metrics": metrics})
    return httpx.Response(200, json={"dataSource": data["dataSource"], "results": results})


def abstract_full(refs: list[str]) -> dict:
    """Abstract Retrieval view=FULL 的回應骨架：參考文獻在 item.bibrecord.tail.bibliography.reference。

    每筆參考文獻另附一個非 SGR 的 itemid，確認只取 Scopus ID；只有一筆時 Scopus 回傳物件而非清單。
    """
    items = [{"ref-info": {"refd-itemidlist": {"itemid": [{"@idtype": "DOI", "$": f"10.1/{r}"},
                                                         {"@idtype": "SGR", "$": r}]}}} for r in refs]
    reference = items[0] if len(items) == 1 else items
    return {"abstracts-retrieval-response": {"item": {"bibrecord": {"tail": {"bibliography": {
        "@refcount": str(len(refs)), "reference": reference}}}}}}
