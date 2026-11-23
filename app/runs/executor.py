"""執行分析：抓資料、計算、排名、異常偵測（設計 §6）。"""

import asyncio
import json
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Anomaly, Result, Run, RunPublication, RunUnit, Unit
from app.db.settings_store import get_setting, set_setting
from app.elsevier.client import ElsevierClient, ElsevierError
from app.elsevier.inset_selfcite import InSetSelfCitations
from app.elsevier.ref_selfcite import RefSelfCitations
from app.elsevier.quota import API_LABELS
from app.elsevier.scival import MetricsFetcher, PubMetrics, scival_year_ranges
from app.elsevier.overlap import Overlap, drop_other_schools, overlap_of
from app.elsevier.scopus import school_names, search_docs
from app.elsevier.selfcite import get_strategy
from app.mapping.service import units_of
from app.metrics.compute import compute_unit, rank
from app.metrics.indicators import INDICATORS, importance_note
from app.runs.anomaly import detect
from app.runs.scope import RunConfig, build_groups

# 單位抓取中的狀態：先 Scopus 取 EID，再 SciVal 取指標，最後排除自我引用（"fetching" 為舊版資料庫的狀態）
FETCHING = ("scopus", "scival", "selfcite", "fetching")
# 排除自我引用這一步失敗時，錯誤訊息的開頭（儀表板據此把失敗歸到「排除自我引用」這一步）
SELFCITE_ERROR = "排除自我引用："
PUB_FIELDS = ("year", "q1", "top1", "top10", "intl_collab", "citations", "self_citations")


def pub_to_json(m: PubMetrics, in_scival: bool) -> str:
    data = {f: getattr(m, f) for f in PUB_FIELDS}
    data.update(fwci=None if m.fwci is None else str(m.fwci), in_scival=in_scival, raw=m.raw)
    return json.dumps(data, ensure_ascii=False)


def pub_from_json(scopus_id: str, text: str | None) -> PubMetrics:
    if not text:
        return PubMetrics(scopus_id)
    d = json.loads(text)
    return PubMetrics(scopus_id, **{f: d.get(f) for f in PUB_FIELDS},
                      fwci=None if d.get("fwci") is None else Decimal(d["fwci"]), raw=d.get("raw") or {})


def unit_pubs(session: Session, run_id: int, unit_id: int) -> list[PubMetrics]:
    rows = session.execute(select(RunPublication.scopus_id, RunPublication.metrics_json).where(
        RunPublication.run_id == run_id, RunPublication.unit_id == unit_id).order_by(RunPublication.id)).all()
    return [pub_from_json(r.scopus_id, r.metrics_json) for r in rows]


def recompute(session: Session, run_id: int) -> None:
    """依目前抓到的資料重算所有結果與異常；已確認的異常保留。"""
    run = session.get(Run, run_id)
    cfg = RunConfig.from_json(run.scope_json)
    run_units = {ru.unit_id: ru for ru in session.scalars(select(RunUnit).where(RunUnit.run_id == run_id))}
    values = {}
    for unit_id, ru in run_units.items():
        pubs = unit_pubs(session, run_id, unit_id) if ru.status == "done" else []
        values[unit_id] = compute_unit(pubs, ru.teacher_count) if ru.status == "done" else None

    session.execute(delete(Result).where(Result.run_id == run_id))
    for group in build_groups(units_of(session, run.mapping_version_id), cfg):
        members = [u for u in group.unit_ids if u in run_units]
        for ind in INDICATORS:
            vals = {u: values[u].value[ind.id] if values[u] else None for u in members}
            ranks = rank(vals)
            for u in members:
                v = values[u]
                session.add(Result(run_id=run_id, unit_id=u, comparison=group.comparison, indicator_id=ind.id,
                                   value=vals[u], raw=v.raw[ind.id] if v else None, rank=ranks[u]))

    confirmed = {(a.unit_id, a.kind) for a in session.scalars(
        select(Anomaly).where(Anomaly.run_id == run_id, Anomaly.status == "confirmed"))}
    session.execute(delete(Anomaly).where(Anomaly.run_id == run_id, Anomaly.status != "confirmed"))
    threshold = float(get_setting(session, "anomaly_change_threshold"))
    session.add_all(a for a in detect(session, run_id, threshold) if (a.unit_id, a.kind) not in confirmed)
    session.commit()


def recompute_flagged(session: Session) -> int:
    """計算公式改變後（migration 留下 recompute_results 標記），依原始資料重算所有已有結果的分析。

    啟動時、沒有分析在跑時呼叫；回傳重算的分析數。
    """
    if not get_setting(session, "recompute_results"):
        return 0
    run_ids = list(session.scalars(select(Result.run_id).distinct().order_by(Result.run_id)))
    for run_id in run_ids:
        recompute(session, run_id)
    set_setting(session, "recompute_results", False)
    return len(run_ids)


def _write_notes(session: Session, run: Run, data_source: dict) -> None:
    notes = json.loads(run.notes_json or "{}")
    strategy = get_strategy(run.self_citation_strategy)
    updated = data_source.get("lastUpdated") or notes.get("scival_updated", "")
    notes.update(
        data_date=date.today().isoformat(),
        scival_updated=updated,
        source="Scopus Search API、SciVal Publication Metrics API"
               + ("、Scopus Abstract Retrieval API（參考文獻）" if strategy.in_set else "")
               + (f"；SciVal 資料更新日 {updated}" if updated else ""),
        importance={i.id: importance_note(i) for i in INDICATORS},
        self_citation=strategy.label,
    )
    run.notes_json = json.dumps(notes, ensure_ascii=False)


async def execute_run(run_id: int, session_factory: sessionmaker[Session], client: ElsevierClient, *,
                      only_unfinished: bool = False, unit_ids: set[int] | None = None,
                      use_cache: bool | None = None, live: dict[int, InSetSelfCitations] | None = None) -> None:
    """live：執行期間把排除自我引用的進度物件放在 live[run_id]，首頁流程圖讀它顯示細部進度。"""
    with session_factory() as s:
        run = s.get(Run, run_id)
        cfg = RunConfig.from_json(run.scope_json)
        if unit_ids is None or run.status != "done":  # 已完成的分析重新抓取單位時維持已完成，結果頁照常顯示
            run.status, run.error = "running", None
        q = select(RunUnit).where(RunUnit.run_id == run_id)
        if unit_ids is not None:  # 共用同一查詢的單位一起重抓，數字才會一致
            queries = s.scalars(select(RunUnit.query).where(RunUnit.run_id == run_id,
                                                            RunUnit.unit_id.in_(unit_ids))).all()
            q = q.where(RunUnit.query.in_(queries))
        elif only_unfinished:
            q = q.where(RunUnit.status != "done")
        by_query: dict[str, list[int]] = defaultdict(list)
        scopus_done: set[int] = set()  # 接續時 Scopus 已完成的單位，進度直接從 SciVal 算起
        # 校名互相包含的學校（台大 ⊂ 台科大）：查詢分不出來的論文要逐篇讀機構全文
        schools = school_names(u.dept_en for u in units_of(s, run.mapping_version_id))
        overlaps: dict[str, Overlap | None] = {}
        for ru in s.scalars(q.order_by(RunUnit.id)):
            by_query[ru.query].append(ru.id)
            overlaps.setdefault(ru.query, overlap_of(s.get(Unit, ru.unit_id).dept_en, schools))
            if only_unfinished and ru.eid_count is not None:
                scopus_done.add(ru.id)
        strategy = get_strategy(run.self_citation_strategy)
        _, cite_range = scival_year_ranges(run.period_from, run.period_to, run.created_at.year)
        # 一次只處理幾個單位：全校分析時不會所有單位同時湧入，進度也會逐一完成而不是全部卡在半途。
        # 負載平衡時請求會分散到各組 Key 並行送出，同時處理的單位數也跟著乘上 Key 的組數
        keys = len(client.api_keys) if client.load_balance else 1
        units_at_once = asyncio.Semaphore(max(int(get_setting(s, "max_concurrency")), 1) * keys)
        s.commit()
    if use_cache is None:
        use_cache = only_unfinished or not cfg.force_refresh
    # 被引數要含今年以後的引用，用 cite_range；其他指標在兩種 yearRange 下逐篇結果相同（實測 100 篇），
    # 所以整批改用 cite_range，被引數與其他指標同一次請求。SciVal 逐篇指標忽略 includeSelfCitations，
    # 一律取含自我引用的數字，需要排除時再減去組內互引
    fetcher = MetricsFetcher(client, year_range=cite_range, include_self="true", use_cache=use_cache)
    if not strategy.in_set:
        in_set = None
    elif strategy.ref_filter:
        in_set = RefSelfCitations(client, use_cache=use_cache)
    else:
        in_set = InSetSelfCitations(client)
    if in_set is not None:   # 第一次補齊參考文獻可能用完額度：等到重置後自動續查，不中斷分析
        client.wait_for_quota_reset = True
    if live is not None and in_set is not None:
        live[run_id] = in_set
    saved_usage = Counter(client.usage)  # 已寫入這次分析的部分；同一個 client 之前的用量不算

    def save_usage(s: Session) -> None:
        """把 client 新增的請求數累加到分析的 api_usage_json（接續、重抓會一直累加）。"""
        run = s.get(Run, run_id)
        usage = Counter(json.loads(run.api_usage_json or "{}"))
        usage.update(client.usage - saved_usage)
        run.api_usage_json = json.dumps({api: usage[api] for api in API_LABELS})
        saved_usage.clear()
        saved_usage.update(client.usage)

    def update(ru_ids: list[int], **fields) -> None:
        with session_factory() as s:
            for ru in s.scalars(select(RunUnit).where(RunUnit.id.in_(ru_ids))):
                for k, v in fields.items():
                    setattr(ru, k, v)
            s.commit()

    def fail(ru_ids: list[int], prefix: str, e: Exception) -> None:
        message = e.message if isinstance(e, ElsevierError) else f"非預期錯誤：{type(e).__name__}: {e}"
        update(ru_ids, status="failed", error=prefix + message)

    async def fetch(query: str, ru_ids: list[int]) -> None:
        async with units_at_once:   # 排除自我引用時讓出名額，其他單位繼續取指標
            fetched = await _metrics(query, ru_ids)
        if fetched is None:
            return
        docs, metrics = fetched
        if in_set is not None:
            update(ru_ids, status="selfcite")
            try:
                # SciVal Publication Set 只含 SciVal 有收錄的論文，組內互引也只算這些論文之間的引用
                selfs = await in_set.count([d.scopus_id for d in docs if d.scopus_id in metrics],
                                           {i: m.citations for i, m in metrics.items()}, query=query)
            except Exception as e:  # 單一單位的錯誤不影響其他單位
                fail(ru_ids, SELFCITE_ERROR, e)
                return
            for i, m in metrics.items():
                if m.citations is not None:
                    m.self_citations = selfs.get(i, 0)
                    m.citations = max(m.citations - m.self_citations, 0)
        save(ru_ids, docs, metrics)

    async def _metrics(query: str, ru_ids: list[int]):
        """Scopus 取 EID、SciVal 取指標；失敗時記下原因並回傳 None。"""
        if all(i in scopus_done for i in ru_ids):
            update(ru_ids, status="scival", error=None)
        else:
            update(ru_ids, status="scopus", error=None, eid_count=None)
        try:
            docs = await search_docs(client, query, use_cache=use_cache, strict=in_set is not None)
            docs = await drop_other_schools(client, query, docs, overlaps[query], use_cache=use_cache,
                                            strict=in_set is not None)
            # 先記下篇數：失敗時看 eid_count 有沒有值，就知道是卡在 Scopus 還是 SciVal
            update(ru_ids, status="scival", eid_count=len(docs))
            return docs, await fetcher.fetch([d.scopus_id for d in docs])
        except Exception as e:  # 單一單位的錯誤不影響其他單位
            fail(ru_ids, "", e)
            return None

    def save(ru_ids: list[int], docs, metrics: dict[str, PubMetrics]) -> None:
        with session_factory() as s:
            for ru in s.scalars(select(RunUnit).where(RunUnit.id.in_(ru_ids))):
                s.execute(delete(RunPublication).where(RunPublication.run_id == run_id,
                                                       RunPublication.unit_id == ru.unit_id))
                for d in docs:
                    m = metrics.get(d.scopus_id) or PubMetrics(d.scopus_id)
                    s.add(RunPublication(run_id=run_id, unit_id=ru.unit_id, scopus_id=d.scopus_id, eid=d.eid,
                                         metrics_json=pub_to_json(m, d.scopus_id in metrics)))
                ru.status, ru.eid_count, ru.fetched_at, ru.error = "done", len(docs), datetime.now(), None
            save_usage(s)
            s.commit()

    try:
        await asyncio.gather(*(fetch(q, ids) for q, ids in by_query.items()))
        with session_factory() as s:
            recompute(s, run_id)
            run = s.get(Run, run_id)
            _write_notes(s, run, fetcher.data_source)
            run.status = "done"
            s.commit()
    except Exception as e:  # 未預期的錯誤：整個分析標為失敗，保留已抓到的資料
        with session_factory() as s:
            run = s.get(Run, run_id)
            run.status, run.error = "failed", f"{type(e).__name__}: {e}"
            s.commit()
    finally:  # 暫停（取消）、失敗也要記下已用掉的額度
        if live is not None and live.get(run_id) is in_set:
            del live[run_id]
        with session_factory() as s:
            save_usage(s)
            s.commit()
