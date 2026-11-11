"""確認系統的「組內互引」算出的被引數，與 SciVal 網頁 Publication Set 排除自我引用的數字相同。

做法與系統執行分析時相同：
  1. SciVal 取含自我引用的被引數（includeSelfCitations=true）
  2. Abstract Retrieval 取各篇參考文獻，數出這組論文彼此之間的引用（組內互引）
  3. 排除後 = 含自我引用 − 組內互引
把結果和 SciVal 網頁上同一組論文的 Citation Count（含／excl. self-citations）比對。

用法（在專案根目錄，或雙擊 scripts\\check_pubset_selfcite.bat）：
  不帶參數：用 2026-11-10 在 SciVal 網頁測過的 7 篇，預期 含 44、排除後 40（約 8 次請求）
  .venv\\Scripts\\python.exe -X utf8 scripts\\check_pubset_selfcite.py
  其他論文：EID 或 Scopus ID，直接列出或放在文字檔（一行一個）
  .venv\\Scripts\\python.exe -X utf8 scripts\\check_pubset_selfcite.py --ids 2-s2.0-85120042660 85141566632
  .venv\\Scripts\\python.exe -X utf8 scripts\\check_pubset_selfcite.py --file eids.txt --web 1234 1200
API Key 讀 .env 的 ELSEVIER_API_KEY，沒有時會詢問。不寫入系統的資料庫與快取。
結束代碼：0 = 與網頁相同，1 = 不同，2 = 無法判斷（請求失敗或沒有網頁數字可比）。
"""

import argparse
import asyncio
import getpass
import sys
from datetime import date
from pathlib import Path

from app.config import Settings
from app.elsevier.client import ElsevierClient, ElsevierError
from app.elsevier.inset_selfcite import InSetSelfCitations
from app.elsevier.scival import MetricsFetcher, scival_year_ranges

# 2026-11-10 SciVal 網頁測試用的 Publication Set（發表年 2021–2025）：Citation Count 含 44、excl. self-citations 40
SAMPLE_IDS = ["85120042660", "85141566632", "85144826309", "85170429613", "85150390837", "85178007617",
              "85093682401"]
SAMPLE_WEB = (44, 40)


def scopus_id(text: str) -> str:
    return text.strip().removeprefix("2-s2.0-")


def read_ids(args) -> list[str]:
    raw = list(args.ids or [])
    if args.file:
        raw += Path(args.file).read_text(encoding="utf-8-sig").split()
    return list(dict.fromkeys(scopus_id(t) for t in raw if t.strip())) or SAMPLE_IDS


async def run(args) -> int:
    ids = read_ids(args)
    web = tuple(args.web) if args.web else (SAMPLE_WEB if ids == SAMPLE_IDS else None)
    settings = Settings()
    api_key = settings.elsevier_api_key or getpass.getpass("請輸入 API Key（輸入時不顯示）：").strip()
    if not api_key:
        return 2
    _, cite_range = scival_year_ranges(date.today().year - 5, date.today().year - 1, date.today().year)
    client = ElsevierClient(api_key, settings.elsevier_insttoken, rate_per_sec=args.rate)
    try:
        print(f"1/2 SciVal 取 {len(ids)} 篇的被引數（含自我引用，{cite_range}）")
        metrics = await MetricsFetcher(client, year_range=cite_range, include_self="true",
                                       use_cache=False).fetch(ids)
        cites = {i: m.citations for i, m in metrics.items()}
        missing = [i for i in ids if i not in metrics]
        if missing:
            print(f"    SciVal 沒有收錄 {len(missing)} 篇，不列入（SciVal 網頁的 Publication Set 也不會有）：{', '.join(missing)}")
        print("2/2 Abstract Retrieval 取參考文獻，計算組內互引")
        counter = InSetSelfCitations(client)
        selfs = await counter.count([i for i in ids if i in metrics], cites)
    except ElsevierError as e:
        print(f"\n請求失敗：{e.message}")
        return 2
    finally:
        await client.aclose()

    print(f"\n{'Scopus ID':<14}{'發表年':>6}{'含自我引用':>10}{'組內互引':>8}{'排除後':>8}")
    for i in sorted(metrics, key=lambda s: (metrics[s].year or 0, s)):
        c, n = cites[i] or 0, selfs.get(i, 0)
        print(f"{i:<14}{metrics[i].year or '?':>6}{c:>10}{n:>8}{c - n:>8}")
    total, inset = sum(v or 0 for v in cites.values()), sum(selfs.values())
    print(f"\n合計：含自我引用 {total}，組內互引 {inset}，排除後 {total - inset}"
          f"（Abstract Retrieval {counter.fetched} 次）")

    if web is None:
        print("沒有網頁數字可比；用 --web <含自我引用> <排除後> 帶入 SciVal 網頁 Overall 的兩個數字")
        return 2
    ok = (total, total - inset) == web
    print(f"SciVal 網頁：含 {web[0]}，排除後 {web[1]} → {'相同' if ok else '不同'}")
    if not ok and total != web[0]:
        print("提示：含自我引用的數字就不同，通常是 SciVal 網頁與 API 的資料更新日不同，或兩邊的論文清單不一致；"
              "可比較差額（網頁 含 − 排除後）是否等於組內互引")
    return 0 if ok else 1


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ids", nargs="+", metavar="ID", help="EID 或 Scopus ID；不指定時用 7 篇測試論文")
    p.add_argument("--file", help="EID／Scopus ID 清單的文字檔，一行一個")
    p.add_argument("--web", type=int, nargs=2, metavar=("INCL", "EXCL"),
                   help="SciVal 網頁上的 Citation Count：含自我引用、excl. self-citations（Overall）")
    p.add_argument("--rate", type=float, default=3.0, help="每秒請求數")
    sys.exit(asyncio.run(run(p.parse_args())))


if __name__ == "__main__":
    main()
