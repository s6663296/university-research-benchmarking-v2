"""確認系統的「組內互引」算出的被引數，與 SciVal 網頁 Publication Set 排除自我引用的數字相同。

做法與系統執行分析時相同：
  1. SciVal 取含自我引用的被引數（includeSelfCitations=true）
  2. Abstract Retrieval 取各篇參考文獻，數出這組論文彼此之間的引用（組內互引）
  3. 排除後 = 含自我引用 − 組內互引
把結果和 SciVal 網頁上同一組論文的 Citation Count（含／excl. self-citations）比對。

用法（在專案根目錄，或雙擊 scripts\\check_pubset_selfcite.bat）：
  第一步：不帶參數，用 2026-11-10 在 SciVal 網頁測過的 7 篇，預期 含 44、排除後 40（約 8 次請求）
    .venv\\Scripts\\python.exe -X utf8 scripts\\check_pubset_selfcite.py
  第二步：整個系所。用與系統相同的 Scopus 查詢找出論文、存成 EID 檔（匯入 SciVal 建立 Publication Set 用），
  算完後輸入 SciVal 網頁上的兩個數字比對（每篇論文約 1 次 Abstract Retrieval）
    .venv\\Scripts\\python.exe -X utf8 scripts\\check_pubset_selfcite.py --dept "Department of Finance, National Central University"
  其他論文清單：EID 或 Scopus ID，直接列出或放在文字檔（一行一個）；--web 直接帶入網頁數字
    .venv\\Scripts\\python.exe -X utf8 scripts\\check_pubset_selfcite.py --file eids.txt --web 1234 1200
API Key 讀 .env 的 ELSEVIER_API_KEY，沒有時會詢問。使用系統的設定與快取：參考文獻存入永久快取，之後正式分析不必再查。
結束代碼：0 = 與網頁相同，1 = 不同，2 = 無法判斷（請求失敗或沒有網頁數字可比）。
"""

import argparse
import asyncio
import getpass
import re
import sys
from datetime import date
from pathlib import Path

from app.config import Settings
from app.elsevier.client import ElsevierClient, ElsevierError, make_client
from app.elsevier.inset_selfcite import InSetSelfCitations
from app.elsevier.scival import MetricsFetcher, scival_year_ranges
from app.elsevier.scopus import build_query, school_names, search_docs

OUTDIR = Path(__file__).resolve().parent.parent / "api_test" / "output"
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
    return list(dict.fromkeys(scopus_id(t) for t in raw if t.strip()))


def parse_web(text: str) -> tuple[int, int] | None:
    nums = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", text)]
    return (nums[0], nums[1]) if len(nums) >= 2 else None


def open_client(settings: Settings) -> ElsevierClient:
    """與系統相同的 client：設定頁的速率、重試參數，以及 data/app.db 的快取與額度紀錄。"""
    from app.db.session import init_db, make_engine, make_session_factory

    engine = make_engine(settings.data_dir / "app.db")
    init_db(engine)
    with make_session_factory(engine)() as session:
        return make_client(settings, session)


def mapping_schools(settings: Settings) -> list[str]:
    """對照表中的英文校名，讓查詢和系統一樣排除校名相近的他校（台大 ⊂ 台科大）；沒有對照表時為空。"""
    from app.db.session import make_engine, make_session_factory
    from app.mapping.service import latest_version, units_of

    db = settings.data_dir / "app.db"
    if not db.exists():
        return []
    with make_session_factory(make_engine(db))() as session:
        version = latest_version(session)
        return school_names(u.dept_en for u in units_of(session, version.id)) if version else []


async def run(args) -> int:
    settings = Settings()
    if not settings.elsevier_api_key:
        settings.elsevier_api_key = getpass.getpass("請輸入 API Key（輸入時不顯示）：").strip()
        if not settings.elsevier_api_key:
            return 2
    today = date.today()
    year_from, year_to = args.years or (today.year - 5, today.year - 1)
    _, cite_range = scival_year_ranges(year_from, year_to, today.year)
    ids = read_ids(args)
    web = tuple(args.web) if args.web else None
    if not ids and not args.dept:
        ids, web = SAMPLE_IDS, web or SAMPLE_WEB

    client = open_client(settings)
    try:
        if args.dept:
            query = build_query(args.dept, year_from, year_to, mapping_schools(settings))
            print(f"0/2 Scopus 搜尋論文（與系統相同的查詢）：{query}")
            docs = await search_docs(client, query, use_cache=False)
            ids = [d.scopus_id for d in docs]
            OUTDIR.mkdir(parents=True, exist_ok=True)
            path = Path(args.save) if args.save else OUTDIR / f"eids_{re.sub(r'\W+', '_', args.dept).strip('_')}.txt"
            path.write_text("".join(f"{d.eid}\n" for d in docs), encoding="utf-8")
            print(f"    共 {len(ids)} 篇，EID 清單：{path}")
            if not ids:
                return 2
        print(f"1/2 SciVal 取 {len(ids)} 篇的被引數（含自我引用，{cite_range}）")
        metrics = await MetricsFetcher(client, year_range=cite_range, include_self="true",
                                       use_cache=False).fetch(ids)
        cites = {i: m.citations for i, m in metrics.items()}
        missing = [i for i in ids if i not in metrics]
        if missing:
            print(f"    SciVal 沒有收錄 {len(missing)} 篇，不列入（SciVal 網頁的 Publication Set 也不會有）："
                  + ", ".join(missing[:20]) + (" …" if len(missing) > 20 else ""))
        print("2/2 Abstract Retrieval 取參考文獻，計算組內互引（已查過的論文從快取讀取）")
        counter = InSetSelfCitations(client)
        selfs = await counter.count([i for i in ids if i in metrics], cites)
    except ElsevierError as e:
        print(f"\n請求失敗：{e.message}")
        return 2
    finally:
        await client.aclose()

    print(f"\n{'Scopus ID':<14}{'發表年':>6}{'含自我引用':>10}{'組內互引':>8}{'排除後':>8}")
    rows = sorted(metrics, key=lambda s: (metrics[s].year or 0, s))
    for i in rows if len(rows) <= args.show else [r for r in rows if selfs.get(r)]:
        c, n = cites[i] or 0, selfs.get(i, 0)
        print(f"{i:<14}{metrics[i].year or '?':>6}{c:>10}{n:>8}{c - n:>8}")
    if len(rows) > args.show:
        print(f"（共 {len(rows)} 篇，只列出有組內互引的 {sum(1 for r in rows if selfs.get(r))} 篇）")
    by_year: dict = {}
    for i in rows:
        y = by_year.setdefault(metrics[i].year, [0, 0])
        y[0] += cites[i] or 0
        y[1] += selfs.get(i, 0)
    print("\n逐年（可對照 SciVal 網頁各年欄位）：" + "、".join(
        f"{y} {c}→{c - n}" for y, (c, n) in sorted(by_year.items(), key=lambda kv: kv[0] or 0)))
    total, inset = sum(v or 0 for v in cites.values()), sum(selfs.values())
    print(f"合計：{len(rows)} 篇（SciVal 網頁的 Scholarly Output 應相同），含自我引用 {total}，組內互引 {inset}，"
          f"排除後 {total - inset}（Abstract Retrieval 實際請求 {counter.fetched} 次）")

    if web is None and sys.stdin.isatty():
        print("\n請把 EID 清單匯入 SciVal 建立 Publication Set，看 Overall 欄的兩個指標（不是 Scholarly Output 篇數）：\n"
              "  Citation Count（含自我引用）、Citation Count (excl. self-citations)")
        web = parse_web(input("依序輸入這兩個被引數（空白分隔，直接 Enter 略過）："))
    if web is None:
        print("沒有網頁數字可比；之後可用 --file <EID 清單> --web <含自我引用> <排除後> 再比對")
        return 2
    if web[0] == len(rows) and web[0] != total:
        print(f"注意：第一個數字 {web[0]} 等於篇數，可能輸入成 Scholarly Output；應輸入 Citation Count（含自我引用）")
    ok = (total, total - inset) == web
    print(f"SciVal 網頁：含 {web[0]}，排除後 {web[1]} → {'相同' if ok else '不同'}")
    if not ok:
        if total != web[0]:
            print("提示：含自我引用的數字就不同，通常是 SciVal 網頁與 API 的資料更新日不同，或兩邊的論文清單不一致"
                  "（先對 Scholarly Output 篇數）")
        print(f"網頁差額 {web[0] - web[1]}，系統的組內互引 {inset}：兩者相同時，排除自我引用的做法就與網頁一致")
    return 0 if ok else 1


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dept", help="系所英文名稱（與對照表相同寫法），例如 \"Department of Finance, National Central University\"")
    p.add_argument("--years", type=int, nargs=2, metavar=("FROM", "TO"), help="--dept 的發表年，預設為近 5 個完整年度")
    p.add_argument("--save", help="--dept 的 EID 清單存放位置，預設 api_test/output/eids_<系所>.txt")
    p.add_argument("--ids", nargs="+", metavar="ID", help="EID 或 Scopus ID")
    p.add_argument("--file", help="EID／Scopus ID 清單的文字檔，一行一個")
    p.add_argument("--web", type=int, nargs=2, metavar=("INCL", "EXCL"),
                   help="SciVal 網頁上的 Citation Count：含自我引用、excl. self-citations（Overall）")
    p.add_argument("--show", type=int, default=30, help="篇數超過這個數量時，只逐篇列出有組內互引的論文")
    sys.exit(asyncio.run(run(p.parse_args())))


if __name__ == "__main__":
    main()
