#!/usr/bin/env python3
"""測試本案需要的兩支 Elsevier API 是否可用。

1. Scopus Search：用單位名稱取得指定年份的所有論文 EID（操作步驟 3～4）
2. SciVal Publication Metrics：用 EID 清單取得報告需要的指標（操作步驟 5～7）

用法：執行 run_api_test.bat，輸入 API Key 即可。
原始回應會存到 output/，可和網頁版 Scopus / SciVal 的結果比對。
"""

import getpass
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# ---- 測試資料（可自行修改）----
QUERY = "AFFIL(Department of Quantitative Finance National Tsing-Hua University)"
YEAR_FROM, YEAR_TO = 2021, 2025
# 比照操作步驟第 5 頁：Scopus 篩選 Country/territory = Taiwan
COUNTRY = "AFFILCOUNTRY(Taiwan)"

SCIVAL_METRICS = ["PublicationsInTopJournalPercentiles", "OutputsInTopCitationPercentiles",
                  "FieldWeightedCitationImpact", "Collaboration"]

API = "https://api.elsevier.com"
OUTDIR = Path(__file__).resolve().parent / "output"


def get(path, params, api_key):
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"X-ELS-APIKey": api_key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:
        return 0, str(e)


def save(name, data):
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if k != "link"}  # link 內含 API Key，不存檔
    OUTDIR.mkdir(exist_ok=True)
    path = OUTDIR / f"{datetime.now():%Y%m%d_%H%M%S}_{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_scopus(api_key):
    """翻頁取回所有論文，回傳 Scopus ID 清單。"""
    query = f"{QUERY} AND {COUNTRY} AND PUBYEAR > {YEAR_FROM - 1} AND PUBYEAR < {YEAR_TO + 1}"
    entries, cursor, total = [], "*", None
    while True:
        status, data = get("/content/search/scopus", {"query": query, "count": 25, "cursor": cursor}, api_key)
        if status != 200:
            print(f"[失敗] Scopus Search：HTTP {status} {data}")
            return []
        result = data["search-results"]
        total = int(result["opensearch:totalResults"])
        page = [e for e in result.get("entry", []) if "eid" in e]
        entries += page
        cursor = result.get("cursor", {}).get("@next")
        if not page or not cursor or len(entries) >= total:
            break

    path = save("scopus", entries)
    print(f"[OK]   Scopus Search：{YEAR_FROM}–{YEAR_TO} 共 {total} 筆，取回 {len(entries)} 筆 EID")
    print(f"       查詢：{query}")
    print(f"       原始資料：{path}")
    return [e["dc:identifier"].replace("SCOPUS_ID:", "") for e in entries]


def scival(api_key, ids, metrics, year_range, self_citations, name):
    params = {
        "metricTypes": ",".join(metrics),
        "publicationIds": ",".join(ids),
        "yearRange": year_range,
        "byYear": "true",
        "includeSelfCitations": self_citations,
    }
    status, data = get("/analytics/scival/publication/metrics", params, api_key)
    if status != 200:
        print(f"[失敗] SciVal（{name}）：HTTP {status} {data}")
        return None
    save(name, data)
    return data


def entries(data, metric):
    """SciVal 逐篇回傳，取出每篇的某項指標。"""
    return [m for r in data.get("results", []) for m in r.get("metrics", []) if m.get("metricType") == metric]


def year_sum(d):
    return sum(v or 0 for v in d.get("valueByYear", {}).values())


def subtotal(data, metric, key, value):
    """加總某項指標中特定門檻／類型的篇數，例如 threshold=25、collabType=International。"""
    found = [v for m in entries(data, metric) for v in m.get("values", []) if v.get(key) == value]
    return sum(year_sum(v) for v in found) if found else None


def show(label, value, note=""):
    tag = "[OK]  " if value is not None else "[缺少]"
    print(f"       {tag} {label}：{'—' if value is None else value}{note}")


def test_scival(api_key, ids):
    # 1. 篇數類指標與 FWCI：只算 2021–2025
    data = scival(api_key, ids, SCIVAL_METRICS, "5yrs", "false", "scival")
    if not data:
        return
    print(f"[OK]   SciVal Publication Metrics：送出 {len(ids)} 篇（請和 SciVal 網頁 Table 頁比對以下數字）")

    want = [str(y) for y in range(YEAR_FROM, YEAR_TO + 1)]
    got = sorted({y for m in entries(data, "FieldWeightedCitationImpact") for y in m.get("valueByYear", {})})
    if got == want:
        print(f"       [OK]   年份：yearRange=5yrs 涵蓋 {got[0]}–{got[-1]}，和報告相同")
    else:
        print(f"       [注意] 年份：SciVal 回傳 {got or '找不到逐年資料'}，報告需要 {want[0]}–{want[-1]}")

    fwci = [v for m in entries(data, "FieldWeightedCitationImpact")
            for v in m.get("valueByYear", {}).values() if v is not None]
    show("總篇數", len(ids), "（由 Scopus 查詢結果計算）")
    show("Q1 篇數", subtotal(data, "PublicationsInTopJournalPercentiles", "threshold", 25), "（CiteScore 前 25%）")
    show("Top 1% 篇數", subtotal(data, "OutputsInTopCitationPercentiles", "threshold", 1))
    show("Top 10% 篇數", subtotal(data, "OutputsInTopCitationPercentiles", "threshold", 10))
    show("FWCI", round(sum(fwci) / len(fwci), 2) if fwci else None, "（各篇平均）")
    show("國際合著篇數", subtotal(data, "Collaboration", "collabType", "International collaboration"))

    # 2. 被引數：要算到目前為止才會和網頁一致，所以用 5yrsAndCurrentAndFuture
    cites = {}
    for flag in ("true", "false"):
        d = scival(api_key, ids, ["CitationCount"], "5yrsAndCurrentAndFuture", flag, f"scival_citations_self_{flag}")
        cites[flag] = sum(year_sum(m) for m in entries(d, "CitationCount")) if d else None
    total = cites["true"]
    show("被引數（到目前為止）", total)
    show("平均被引次數", round(total / len(ids), 1) if total is not None else None, "（被引數 ÷ 總篇數）")
    if cites["false"] is None or total is None:
        print("       [待確認] 排除自我引用：請求失敗")
    elif cites["false"] == total:
        print(f"       [待確認] 排除自我引用：設定前後都是 {total}，includeSelfCitations 參數沒有作用")
    else:
        print(f"       [OK]   排除自我引用：{cites['false']}（含自引 {total}）")
    print(f"       原始資料：{OUTDIR}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    api_key = getpass.getpass("請輸入 API Key（輸入時不顯示）：").strip()
    if not api_key:
        return
    print()
    ids = test_scopus(api_key)
    if ids:
        test_scival(api_key, ids)


if __name__ == "__main__":
    main()
