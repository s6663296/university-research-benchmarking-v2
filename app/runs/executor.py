"""執行分析：抓資料、計算、排名、異常偵測（設計 §6）。"""

import asyncio
import json
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Anomaly, Result, Run, RunPublication, RunUnit
from app.db.settings_store import get_setting
from app.elsevier.client import ElsevierClient, ElsevierError
from app.elsevier.scival import PubMetrics, fetch_metrics, scival_year_ranges
from app.elsevier.scopus import search_docs
from app.elsevier.selfcite import get_strategy
from app.mapping.service import units_of
from app.metrics.compute import compute_unit, rank
from app.metrics.indicators import INDICATORS, importance_note
from app.runs.anomaly import detect
from app.runs.scope import RunConfig, build_groups

PUB_FIELDS = ("year", "q1", "top1", "top10", "intl_collab", "citations")


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
        values[unit_id] = compute_unit(pubs, ru.teacher_count, cfg.years) if ru.status == "done" else None

    session.execute(delete(Result).where(Result.run_id == run_id))
    for group in build_groups(units_of(session, run.mapping_version_id), cfg):
        members = [u for u in group.unit_ids if u in run_units]
        for ind in INDICATORS:
            vals = {u: values[u].value[ind.id] if values[u] else None for u in members}
            ranks = rank(vals)
            for u in members:
                v = values[u]
                session.add(Result(run_id=run_id, unit_id=u, comparison=group.comparison, indicator_id=ind.id,
                                   value=vals[u], raw=v.raw[ind.id] if v else None,
                                   five_year_avg=v.five_year_avg[ind.id] if v else None, rank=ranks[u]))

    confirmed = {(a.unit_id, a.kind) for a in session.scalars(
        select(Anomaly).where(Anomaly.run_id == run_id, Anomaly.status == "confirmed"))}
    session.execute(delete(Anomaly).where(Anomaly.run_id == run_id, Anomaly.status != "confirmed"))
    threshold = float(get_setting(session, "anomaly_change_threshold"))
    session.add_all(a for a in detect(session, run_id, threshold) if (a.unit_id, a.kind) not in confirmed)
    session.commit()


def _write_notes(session: Session, run: Run, data_source: dict) -> None:
    notes = json.loads(run.notes_json or "{}")
    strategy = get_strategy(run.self_citation_strategy)
    updated = data_source.get("lastUpdated") or notes.get("scival_updated", "")
    notes.update(
        data_date=date.today().isoformat(),
        scival_updated=updated,
        source="Scopus Search API、SciVal Publication Metrics API" + (f"；SciVal 資料更新日 {updated}" if updated else ""),
        importance={i.id: importance_note(i) for i in INDICATORS},
        self_citation=strategy.label + ("（待驗證）" if not strategy.verified else ""),
    )
    run.notes_json = json.dumps(notes, ensure_ascii=False)


async def execute_run(run_id: int, session_factory: sessionmaker[Session], client: ElsevierClient, *,
                      only_unfinished: bool = False, unit_ids: set[int] | None = None,
                      use_cache: bool | None = None) -> None:
    with session_factory() as s:
        run = s.get(Run, run_id)
        cfg = RunConfig.from_json(run.scope_json)
        run.status, run.error = "running", None
        q = select(RunUnit).where(RunUnit.run_id == run_id)
        if unit_ids is not None:  # 共用同一查詢的單位一起重抓，數字才會一致
            queries = s.scalars(select(RunUnit.query).where(RunUnit.run_id == run_id,
                                                            RunUnit.unit_id.in_(unit_ids))).all()
            q = q.where(RunUnit.query.in_(queries))
        elif only_unfinished:
            q = q.where(RunUnit.status != "done")
        by_query: dict[str, list[int]] = defaultdict(list)
        for ru in s.scalars(q.order_by(RunUnit.id)):
            by_query[ru.query].append(ru.id)
        strategy = get_strategy(run.self_citation_strategy)
        batch_size = int(get_setting(s, "scival_batch_size"))
        year_range, cite_range = scival_year_ranges(run.period_from, run.period_to, run.created_at.year)
        s.commit()
    if use_cache is None:
        use_cache = only_unfinished or not cfg.force_refresh
    data_source: dict = {}

    def update(ru_ids: list[int], **fields) -> None:
        with session_factory() as s:
            for ru in s.scalars(select(RunUnit).where(RunUnit.id.in_(ru_ids))):
                for k, v in fields.items():
                    setattr(ru, k, v)
            s.commit()

    async def fetch(query: str, ru_ids: list[int]) -> None:
        update(ru_ids, status="fetching", error=None)
        try:
            docs = await search_docs(client, query, use_cache=use_cache)
            ids = [d.scopus_id for d in docs]
            metrics, source = await fetch_metrics(client, ids, year_range=year_range, batch_size=batch_size,
                                                  use_cache=use_cache) if ids else ({}, {})
            cites = await strategy.citations(client, ids, year_range=cite_range, batch_size=batch_size,
                                             use_cache=use_cache) if ids else {}
        except ElsevierError as e:
            update(ru_ids, status="failed", error=e.message)
            return
        except Exception as e:  # 單一單位的非預期錯誤不影響其他單位
            update(ru_ids, status="failed", error=f"非預期錯誤：{type(e).__name__}: {e}")
            return
        data_source.update(source)
        with session_factory() as s:
            for ru in s.scalars(select(RunUnit).where(RunUnit.id.in_(ru_ids))):
                s.execute(delete(RunPublication).where(RunPublication.run_id == run_id,
                                                       RunPublication.unit_id == ru.unit_id))
                for d in docs:
                    m = metrics.get(d.scopus_id) or PubMetrics(d.scopus_id)
                    m.citations = cites.get(d.scopus_id)
                    s.add(RunPublication(run_id=run_id, unit_id=ru.unit_id, scopus_id=d.scopus_id, eid=d.eid,
                                         metrics_json=pub_to_json(m, d.scopus_id in metrics)))
                ru.status, ru.eid_count, ru.fetched_at, ru.error = "done", len(docs), datetime.now(), None
            s.commit()

    try:
        await asyncio.gather(*(fetch(q, ids) for q, ids in by_query.items()))
        with session_factory() as s:
            recompute(s, run_id)
            run = s.get(Run, run_id)
            _write_notes(s, run, data_source)
            run.status = "done"
            s.commit()
    except Exception as e:  # 未預期的錯誤：整個分析標為失敗，保留已抓到的資料
        with session_factory() as s:
            run = s.get(Run, run_id)
            run.status, run.error = "failed", f"{type(e).__name__}: {e}"
            s.commit()
