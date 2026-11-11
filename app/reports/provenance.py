"""分析結果的逐格來源說明（設計 2026-10-31-cell-provenance）。

說明不另外保存：從已保存的原始資料（逐篇指標、教師數快照）用同一套計算函式重算，
並與存檔值比對，所以說明不會和表格數字各說各話。只讀不寫。
"""

import json
import threading
from collections import OrderedDict
from urllib.parse import urlencode
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Result, Run, RunPublication, RunUnit, Unit
from app.elsevier.scival import PubMetrics
from app.elsevier.selfcite import get_strategy
from app.mapping.service import units_of
from app.metrics.compute import UnitValues, compute_unit, rank, round2
from app.metrics.indicators import BY_ID, Indicator
from app.runs.executor import PUB_FIELDS
from app.runs.scope import COMPARISON_LABELS, RunConfig, build_groups
from app.teachers.moe import TEACHER_COLUMNS

Column = Literal["raw", "avg", "teachers", "value", "rank"]
TEACHER_SOURCE = {"moe": "教育部", "missing": "缺資料"}


@dataclass
class Source:
    label: str
    text: str


@dataclass
class Drill:
    kind: Literal["papers", "teachers", "group"]
    unit_id: int
    comparison: str
    indicator_id: str
    column: str
    filter: str | None = None   # papers：要標亮的逐篇欄位；None 表示全部

    @property
    def query(self) -> str:
        return urlencode(dict(unit=self.unit_id, cmp=self.comparison, ind=self.indicator_id, col=self.column))


@dataclass
class Explanation:
    title: str
    formula: str | None = None
    substituted: str | None = None
    sources: list[Source] = field(default_factory=list)
    stored: Decimal | int | None = None
    recomputed: Decimal | int | None = None
    missing_reason: str | None = None
    drill: Drill | None = None
    where: tuple[int, str, str, str] | None = None   # (unit_id, comparison, indicator_id, column)

    @property
    def mismatch(self) -> bool:
        return self.stored != self.recomputed

    @property
    def check_text(self) -> str | None:
        if self.mismatch:
            return f"⚠ 重算值與存檔值不符（存檔 {_num(self.stored)}，重算 {_num(self.recomputed)}）"
        return None if self.stored is None else "✓ 重算值與存檔值一致"

    def plain_text(self) -> str:
        """xlsx 註解用的純文字版。"""
        lines = []
        if self.mismatch:
            lines.append(self.check_text)
        if self.formula and self.substituted:
            lines.append(f"{self.formula} = {self.substituted}")
        elif self.substituted or self.formula:
            lines.append(self.substituted or self.formula)
        if self.missing_reason:
            lines.append(f"待確認：{self.missing_reason}")
        lines += [f"{s.label}：{s.text}" for s in self.sources]
        return "\n".join(lines)


@dataclass
class Paper:
    scopus_id: str
    eid: str
    in_scival: bool
    metrics: PubMetrics


def _num(x) -> str:
    if x is None:
        return "無值"
    if isinstance(x, int) or (isinstance(x, Decimal) and x == x.to_integral_value()
                              and x.as_tuple().exponent >= 0):
        return f"{int(x):,}"
    return f"{round2(Decimal(x)):.2f}"


def rank_text(values: dict[int, Decimal | None], me: int, labels: dict[int, str]) -> str | None:
    if values.get(me) is None:
        return None
    mine = values[me]
    missing = sum(1 for v in values.values() if v is None)
    higher = sum(1 for v in values.values() if v is not None and v > mine)
    ties = [labels[u] for u, v in values.items() if u != me and v == mine]
    text = f"同組 {len(values)} 個單位" + (f"（{missing} 個無值不排名）" if missing else "")
    text += f"，值比它高的有 {higher} 個 → 第 {higher + 1} 名"
    return text + (f"；與 {'、'.join(ties)} 同值" if ties else "")


def teacher_text(ru: RunUnit) -> str:
    if not ru.teacher_detail_json:
        source = ru.teacher_source
        label = f"人工修改：{source.split(':', 1)[1]}" if source.startswith("manual") else TEACHER_SOURCE.get(source, source)
        return f"本次分析未保存教育部對應明細（來源：{label}）"
    d = json.loads(ru.teacher_detail_json)
    head = f"教育部 {d['academic_year']} 學年「{TEACHER_COLUMNS.get(d['column'], d['column'])}」"
    rows = "、".join(f"{r['name']}（代碼 {r['code']}）{r['value']} 人" for r in d["rows"])
    if d.get("override"):
        o = d["override"]
        moe = f"；{head}原為 {rows}" if rows else f"；{head}查無對應系所"
        return f"人工修改為 {o['count']} 人：{o['reason']}（{o['created_at'][:10]}）{moe}"
    if not rows:
        return f"{head}查無對應系所"
    total = sum(r["value"] for r in d["rows"])
    return f"{head}：{rows}" + (f"，合計 {total} 人" if len(d["rows"]) > 1 else "")


# 解析逐篇 JSON 是說明最花時間的部分（每篇含數 KB 的 SciVal 原始回應），所以解析結果跨請求快取。
# 鍵含篇數與最大 id：重新抓取會刪掉再新增，id 一定改變，不會拿到舊資料。
_PAPER_CACHE: OrderedDict[tuple, list[Paper]] = OrderedDict()
_PAPER_CACHE_UNITS = 300
_PAPER_CACHE_LOCK = threading.Lock()


def _paper(scopus_id: str, eid: str, text: str | None) -> Paper:
    d = json.loads(text) if text else {}
    # 不保留 raw：說明與明細用不到，留著會讓快取占很多記憶體
    metrics = PubMetrics(scopus_id, **{f: d.get(f) for f in PUB_FIELDS},
                         fwci=None if d.get("fwci") is None else Decimal(d["fwci"]))
    return Paper(scopus_id, eid, bool(d.get("in_scival")), metrics)


def evidence_papers(session: Session, run_id: int, unit_id: int) -> list[Paper]:
    where = (RunPublication.run_id == run_id, RunPublication.unit_id == unit_id)
    count, max_id = session.execute(select(func.count(), func.max(RunPublication.id)).where(*where)).one()
    key = (run_id, unit_id, count, max_id)
    with _PAPER_CACHE_LOCK:
        if key in _PAPER_CACHE:
            _PAPER_CACHE.move_to_end(key)
            return list(_PAPER_CACHE[key])
    rows = session.execute(select(RunPublication.scopus_id, RunPublication.eid, RunPublication.metrics_json)
                           .where(*where).order_by(RunPublication.id)).all()
    papers = [_paper(r.scopus_id, r.eid, r.metrics_json) for r in rows]
    with _PAPER_CACHE_LOCK:
        _PAPER_CACHE[key] = papers
        while len(_PAPER_CACHE) > _PAPER_CACHE_UNITS:
            _PAPER_CACHE.popitem(last=False)
    return list(papers)


@dataclass
class _UnitData:
    unit: Unit
    ru: RunUnit | None
    pubs: list[PubMetrics] | None     # None：沒抓完或原始資料損毀
    values: UnitValues | None
    broken: bool = False


def columns_of(ind: Indicator) -> tuple[str, ...]:
    """指標表上有數字的欄位。"""
    return ("raw", "avg", "teachers", "value", "rank") if ind.aggregate == "count" else ("value", "rank")


COLUMN_LABELS = {"raw": "篇數 (A)", "avg": "年平均", "teachers": "單年教師數 (B)", "rank": "名次"}


class Explainer:
    """一次分析的說明產生器；逐單位延遲載入並快取，同一頁的多張表共用一個即可。"""

    def __init__(self, session: Session, run_id: int):
        self.session = session
        self.run = session.get(Run, run_id)
        cfg = RunConfig.from_json(self.run.scope_json)
        self.years = cfg.years
        self.notes = json.loads(self.run.notes_json or "{}")
        self.run_units = {ru.unit_id: ru for ru in session.scalars(select(RunUnit).where(RunUnit.run_id == run_id))}
        self.results = {(r.unit_id, r.comparison, r.indicator_id): r
                        for r in session.scalars(select(Result).where(Result.run_id == run_id))}
        self.groups = build_groups(units_of(session, self.run.mapping_version_id), cfg)
        self._units: dict[int, _UnitData] = {}

    def _data(self, unit_id: int) -> _UnitData:
        if unit_id not in self._units:
            ru = self.run_units.get(unit_id)
            unit = self.session.get(Unit, unit_id)
            pubs, values, broken = None, None, False
            if ru is not None and ru.status == "done":
                try:
                    pubs = [p.metrics for p in evidence_papers(self.session, self.run.id, unit_id)]
                    values = compute_unit(pubs, ru.teacher_count, self.years)
                except (ValueError, TypeError, KeyError, ArithmeticError):
                    pubs, broken = None, True
            self._units[unit_id] = _UnitData(unit, ru, pubs, values, broken)
        return self._units[unit_id]

    def label(self, unit_id: int) -> str:
        u = self._data(unit_id).unit
        return f"{u.school}－{u.dept_zh or u.ncu_dept}"

    def _missing(self, d: _UnitData, ind: Indicator) -> str | None:
        if d.ru is None:
            return "此單位未納入本次分析"
        if d.broken:
            return "原始資料損毀，無法重算"
        if d.ru.status == "failed":
            return f"抓取失敗：{d.ru.error or '未知錯誤'}"
        if d.ru.status != "done":
            return "尚未抓取完成"
        if not d.pubs:
            return "Scopus 查無論文"
        if d.values.value[ind.id] is None:
            if ind.aggregate == "count":
                return f"缺教師數（{teacher_text(d.ru)}）"
            return "SciVal 沒有回傳這項指標"
        return None

    def _common_sources(self, d: _UnitData) -> list[Source]:
        out = [Source("Scopus 查詢", d.ru.query)] if d.ru else []
        if self.notes.get("source"):
            out.append(Source("資料", f"{self.notes['source']}；取得日 {self.notes.get('data_date', '')}"))
        return out

    def _group(self, unit_id: int, comparison: str):
        return next((g for g in self.groups if g.comparison == comparison and unit_id in g.unit_ids), None)

    def cell(self, unit_id: int, comparison: str, indicator_id: str, column: Column) -> Explanation:
        ind = BY_ID[indicator_id]
        d = self._data(unit_id)
        res = self.results.get((unit_id, comparison, indicator_id))
        value_label = "單年師均" if ind.aggregate == "count" else ind.label
        e = Explanation(title=f"{COLUMN_LABELS.get(column, value_label)}（{ind.label}）　{self.label(unit_id)}",
                        where=(unit_id, comparison, indicator_id, column))
        missing = self._missing(d, ind)
        has_pubs = bool(d.pubs)
        papers = Drill("papers", unit_id, comparison, indicator_id, column, ind.field) if has_pubs else None

        if column == "teachers":
            e.stored = d.ru.teacher_count if d.ru else None
            e.recomputed = self._teacher_recomputed(d.ru)
            e.substituted = None if e.stored is None else f"{e.stored} 位"
            e.missing_reason = None if e.stored is not None else "缺教師數"
            e.sources = [Source("B", teacher_text(d.ru))] if d.ru else []
            e.drill = Drill("teachers", unit_id, comparison, indicator_id, column) if d.ru else None
            return e

        if column == "rank":
            group = self._group(unit_id, comparison)
            members = [u for u in group.unit_ids if u in self.run_units] if group else [unit_id]
            stored = {u: getattr(self.results.get((u, comparison, indicator_id)), "value", None) for u in members}
            recomputed = {u: (self._data(u).values.value[indicator_id] if self._data(u).values else None)
                          for u in members}
            e.stored = res.rank if res else None
            e.recomputed = rank(recomputed)[unit_id]
            e.substituted = rank_text(stored, unit_id, {u: self.label(u) for u in members})
            e.missing_reason = missing
            e.sources = [Source("比較範圍", f"{COMPARISON_LABELS.get(comparison, comparison)}"
                                         + (f"：{group.key}" if group else "")),
                         Source("排名規則", "由高到低；同值同名次、下一名跳號；沒有值的不排名")]
            e.drill = Drill("group", unit_id, comparison, indicator_id, column)
            return e

        e.missing_reason = missing
        e.drill = papers
        if d.values is None:
            e.stored = getattr(res, {"raw": "raw", "avg": "five_year_avg"}.get(column, "value"), None)
            return e
        n = len(d.pubs)
        rule = Source("判定", f"{ind.label}：{ind.source}")
        if ind.aggregate == "count":
            a = int(d.values.raw[ind.id])
            a_text = f"{a} 篇（共 {n} 篇）" if ind.field else f"{n} 篇"
            a_source = Source("A", f"{ind.label} {a_text}")
            if column == "raw":
                e.formula = "符合條件的論文篇數" if ind.field else "Scopus 查詢到的論文篇數"
                e.substituted = a_text
                e.stored, e.recomputed = res.raw if res else None, d.values.raw[ind.id]
                e.sources = [rule, *self._common_sources(d)]
            elif column == "avg":
                e.formula = "A ÷ 年數"
                avg = d.values.five_year_avg[ind.id]
                e.substituted = None if avg is None else f"{a} ÷ {self.years} = {_num(avg)}"
                e.stored, e.recomputed = res.five_year_avg if res else None, avg
                e.sources = [a_source, rule, *self._common_sources(d)]
            else:
                e.formula = "A ÷ 年數 ÷ B"
                v = d.values.value[ind.id]
                e.substituted = (f"{a} ÷ {self.years} ÷ {d.ru.teacher_count} = {_num(v)}"
                                 if v is not None else None)
                e.stored, e.recomputed = res.value if res else None, v
                e.sources = [a_source, Source("B", teacher_text(d.ru)), rule, *self._common_sources(d)]
            return e

        vals = [getattr(p, ind.field) for p in d.pubs if getattr(p, ind.field) is not None]
        v = d.values.value[ind.id]
        e.stored, e.recomputed = res.value if res else None, v
        if ind.aggregate == "mean":
            e.formula = "有值論文的平均"
            if vals:
                skipped = n - len(vals)
                e.substituted = (f"Σ {_num(sum(vals, Decimal(0)))} ÷ {len(vals)} 篇 = {_num(v)}"
                                 + (f"（另 {skipped} 篇無值不列入）" if skipped else ""))
            e.sources = [rule, *self._common_sources(d)]
        else:
            e.formula = "被引數加總 ÷ 總篇數"
            if vals:
                skipped = n - len(vals)
                e.substituted = (f"{sum(vals):,} ÷ {n} = {_num(v)}"
                                 + (f"（其中 {skipped} 篇無被引數資料，不加總但計入篇數）" if skipped else ""))
                excluded = sum(p.self_citations or 0 for p in d.pubs)
                if excluded:
                    e.substituted += f"（已扣除組內互引 {excluded:,} 次）"
            strategy = get_strategy(self.run.self_citation_strategy)
            e.sources = [rule, Source("自我引用", self.notes.get("self_citation") or strategy.label),
                         *self._common_sources(d)]
        return e

    @staticmethod
    def _teacher_recomputed(ru: RunUnit | None) -> int | None:
        """由快照重算教師數；沒有快照的舊分析只能沿用存檔值。"""
        if ru is None:
            return None
        if not ru.teacher_detail_json:
            return ru.teacher_count
        d = json.loads(ru.teacher_detail_json)
        if d.get("override"):
            return d["override"]["count"]
        return sum(r["value"] for r in d["rows"]) if d["rows"] else None

    def mismatches(self) -> list[Explanation]:
        """整次分析中重算值與存檔值不符的格子。"""
        return [e for (u, cmp, ind) in sorted(self.results) for col in columns_of(BY_ID[ind])
                if (e := self.cell(u, cmp, ind, col)).mismatch]

    def group_rows(self, unit_id: int, comparison: str, indicator_id: str) -> list[dict]:
        """名次明細：同組各單位的值與名次，由高到低。"""
        group = self._group(unit_id, comparison)
        members = [u for u in group.unit_ids if u in self.run_units] if group else []
        rows = [dict(unit_id=u, label=self.label(u), value=getattr(self.results.get((u, comparison, indicator_id)), "value", None),
                     rank=getattr(self.results.get((u, comparison, indicator_id)), "rank", None), me=u == unit_id)
                for u in members]
        return sorted(rows, key=lambda r: (r["value"] is None, -(r["value"] or 0)))
