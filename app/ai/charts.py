"""AI 圖表分析：仿研發處過往簡報第 2、4、5、6 頁（docs/reference/研發處過往簡報/），
在網頁 AI 總結分頁產生「圖表＋AI 圖表說明＋紅框重點」的圖片，可下載成 PNG。

跟 AI 總結（兩欄文字）分開產生、分開儲存、分開判斷是否過時：產生圖表分析不會讓 AI 總結變成過時，
系所簡報（pptx）也不受影響。只做系所層級，比較對象是對標學校。
"""

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.baseline import baseline_for, basis_differences
from app.ai.inputs import _anomalies, _ncu_unit, _pct, _period, _school_of, input_hash
from app.db.models import AiChartNote, Run
from app.metrics.indicators import BY_ID
from app.reports.emphasis import strip_bullet
from app.reports.provenance import Explainer
from app.reports.tables import indicator_table
from app.reports.trends import CITATION_BASED, RECENT_NOTE, TREND_INDICATORS, TrendChart, fmt_trend, trend_chart, \
    trend_label

# 一張圖片放兩張線圖（同過往簡報第 4 頁：論文數＋FWCI；第 2 頁：Top 1%＋Top 10%）
PAIRS = (("total", "fwci"), ("top1", "top10"), ("q1", "intl_collab"))
# 兩期比較表的欄位（過往簡報第 5、6 頁）：(代號, 欄名)
PERIOD_COLS = (("total", "論文數"), ("fwci", "FWCI"), ("top1", "Top 1%"), ("citations", "被引次數"))


@dataclass
class PeriodRow:
    school: str
    is_own: bool
    a: dict[str, Decimal | None]      # 前期
    b: dict[str, Decimal | None]      # 本期


@dataclass
class PeriodTable:
    base_period: str
    period: str
    rows: list[PeriodRow]
    differences: list[str]            # 兩期計算基準的差異（教師數學年度等），要在圖上註明


@dataclass
class ChartData:
    """一個系所的圖表資料：網頁圖片與送給 AI 的資料用同一份，數字才會一致。"""
    dept: str
    period: str
    charts: list[TrendChart]          # 有資料的逐年趨勢（對標學校）
    table: PeriodTable | None         # 有前期時的兩期比較


def fmt_period(col: str, value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}" if col == "fwci" else f"{int(value):,}"


def change(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    return None if a is None or b is None or a == 0 else (b - a) / a * 100


def _period_values(session: Session, run: Run, dept: str) -> dict[str, tuple[bool, dict[str, Decimal | None]]]:
    """對標群組中每所學校的論文數、FWCI、Top 1% 篇數、被引次數（期間總數，不除以教師數）。"""
    out: dict[str, tuple[bool, dict]] = {}
    for col, ind, attr in (("total", "total", "raw"), ("fwci", "fwci", "value"), ("top1", "top1", "raw"),
                           ("citations", "avg_citations", "raw")):
        for row in indicator_table(session, run.id, "benchmark", dept, ind).rows:
            school = _school_of(row)
            out.setdefault(school, (row.is_ncu, {}))[1][col] = getattr(row, attr)
    return out


def period_table(session: Session, run: Run, base: Run, dept: str) -> PeriodTable | None:
    now, before = _period_values(session, run, dept), _period_values(session, base, dept)
    if not now or not before:
        return None
    rows = [PeriodRow(school, own, before.get(school, (own, {}))[1], values) for school, (own, values) in now.items()]
    rows.sort(key=lambda r: (r.b.get("total") is None, -(r.b.get("total") or 0)))
    return PeriodTable(_period(base), _period(run), rows, basis_differences(run, base, dept))


def chart_data(session: Session, run: Run, dept: str, explainer: Explainer | None = None) -> ChartData:
    explainer = explainer or Explainer(session, run.id)
    unit = _ncu_unit(session, run, dept)
    charts = [c for ind in TREND_INDICATORS
              if (c := trend_chart(session, run.id, "benchmark", dept, ind, explainer, own_unit_id=unit.id)).has_data]
    base = baseline_for(session, run, dept)
    return ChartData(dept, _period(run), charts, period_table(session, run, base, dept) if base else None)


def crossings(chart: TrendChart) -> list[str]:
    """逐年比較本系所與各校的高低，列出「哪一年誰超越本系所／本系所超越誰」（由程式判斷，模型不用自己比）。"""
    own = next((s for s in chart.series if s.is_ncu), None)
    if own is None:
        return []
    out = []
    for s in chart.series:
        if s is own:
            continue
        for y0, y1 in zip(chart.years, chart.years[1:]):
            values = (own.values[y0], own.values[y1], s.values[y0], s.values[y1])
            if None in values:
                continue
            before, after = values[2] - values[0], values[3] - values[1]
            if before <= 0 < after:
                out.append(f"{y1} 年 {s.label} 超越本系所")
            elif before >= 0 > after:
                out.append(f"{y1} 年 本系所超越 {s.label}")
    return sorted(out)


def _trend_entry(chart: TrendChart) -> dict:
    ind = chart.indicator
    entry = {"代號": chart.indicator_id, "名稱": trend_label(ind), "年份": chart.years,
             "各校": [{"學校": s.label, **({"本系所": True} if s.is_ncu else {}),
                       "逐年": [fmt_trend(s.values[y], ind) for y in chart.years]} for s in chart.series],
             "與本系所的消長": crossings(chart)}
    if chart.indicator_id in CITATION_BASED:
        entry["註"] = RECENT_NOTE
    return entry


def _table_entry(t: PeriodTable) -> dict:
    def school(r: PeriodRow) -> dict:
        out = {"學校": r.school, **({"本系所": True} if r.is_own else {})}
        for col, name in PERIOD_COLS:
            out[name] = {"前期": fmt_period(col, r.a.get(col)), "本期": fmt_period(col, r.b.get(col)),
                         "變化率": _pct(r.a.get(col), r.b.get(col))}
        return out
    return {"前期": t.base_period, "本期": t.period, "比較基準差異": t.differences, "各校": [school(r) for r in t.rows]}


def chart_input(session: Session, run: Run, dept: str, data: ChartData | None = None) -> dict:
    data = data or chart_data(session, run, dept)
    return {"系所": dept, "期間": data.period, "逐年趨勢": [_trend_entry(c) for c in data.charts],
            "兩期比較表": _table_entry(data.table) if data.table else None,
            "待人工確認": _anomalies(session, run, dept)}


_RULES = [
    "你要為大學系所的論文指標圖表撰寫「圖表說明」與「重點」，放在圖表旁給主管看。資料以 JSON 提供，數字都已算好。",
    "比較對象是對標學校（其他大學的同類系所）；「本系所」標為 true 的那一列是本校系所，文中稱「本系所」。",
    "只能引用提供的數字（含已算好的變化率），不可自行計算、推估或補上新的數字。"
    "數值是「待確認」或「—」，或列在「待人工確認」的項目，不可據此下結論。",
    "charts：「逐年趨勢」中每個指標寫一項，indicator 填該指標的「代號」。"
    "notes 寫 2 至 3 點圖表說明：各校逐年走勢、本系所的位置變化、哪一年被誰超越或超越誰"
    "（「與本系所的消長」已由程式判斷好，優先引用），每點一句、不超過 40 字，寫出年份與數值。"
    "highlight 寫 1 句最值得主管注意的重點，不超過 30 字，例如「2021 年中山超越本系所」"
    "「本系所 FWCI 自 2019 年起居對標學校之首」；沒有明顯重點時為 null。",
    "有「註」的指標（被引相關）最近一兩年的數值偏低是因為被引次數還在累積，不可寫成表現下滑。",
    "period：有「兩期比較表」時，notes 寫 2 至 3 點說明各校兩期之間的變化（引用變化率），"
    "highlights 寫 1 至 2 句重點，例如「清華引用表現快速超車台大」「中山急速接近本系所位階」；"
    "「比較基準差異」不是空的時，要在 notes 註明「比較基準不同」。沒有「兩期比較表」時 period 回傳 null。",
    "每點把最關鍵的判斷用 **兩個星號** 包起來（例如「**超越本系所**」），圖上會標成紅字；每點最多一處。",
    "不寫開場白與結語，不重述指標定義或計算方式；只談這個系所，不評論本校其他系所。",
]


def system_prompt(tone: str) -> str:
    body = "\n".join(f"{i}. {r}" for i, r in enumerate(_RULES, 1))
    return f"{tone.strip()}\n\n請遵守下列規則：\n{body}"


class ChartWriter(Protocol):
    def write_charts(self, system: str, payload: dict) -> dict: ...


def _lines(items) -> list[str]:
    return [line for line in (strip_bullet(str(i)) for i in items or []) if line]


def normalize(data: dict, payload: dict) -> dict:
    """模型回覆 → 儲存格式：只留資料中有的指標、依固定順序；去掉模型自己加的項目符號。"""
    known = [e["代號"] for e in payload["逐年趨勢"]]
    by_ind = {c.get("indicator"): c for c in data.get("charts") or [] if isinstance(c, dict)}
    charts = [{"indicator": ind, "notes": _lines(by_ind[ind].get("notes")),
               "highlight": strip_bullet(str(by_ind[ind].get("highlight") or "")) or None}
              for ind in known if ind in by_ind]
    period = data.get("period") if payload["兩期比較表"] else None
    if isinstance(period, dict):
        period = {"notes": _lines(period.get("notes")), "highlights": _lines(period.get("highlights"))}
    else:
        period = None
    return {"charts": charts, "period": period}


def _row(session: Session, run_id: int, dept: str) -> AiChartNote | None:
    return session.scalars(select(AiChartNote).where(AiChartNote.run_id == run_id, AiChartNote.key == dept)).first()


def get(session: Session, run_id: int, dept: str) -> AiChartNote | None:
    return _row(session, run_id, dept)


def generate(session: Session, run: Run, dept: str, writer: ChartWriter, tone: str, model: str) -> AiChartNote:
    payload = chart_input(session, run, dept)
    notes = normalize(writer.write_charts(system_prompt(tone), payload), payload)  # AiError 直接往上拋
    base = baseline_for(session, run, dept)
    now = datetime.now()
    values = dict(notes_json=json.dumps(notes, ensure_ascii=False), input_hash=input_hash(payload),
                  baseline_run_id=base.id if base else None, model=model, edited=False, updated_at=now)
    row = _row(session, run.id, dept)
    if row is None:
        row = AiChartNote(run_id=run.id, key=dept, created_at=now, **values)
        session.add(row)
    else:
        for name, value in values.items():
            setattr(row, name, value)
    session.commit()
    return row


def notes_of(row: AiChartNote) -> dict:
    return json.loads(row.notes_json)


def save_edit(session: Session, row_id: int, notes: dict) -> AiChartNote:
    row = session.get(AiChartNote, row_id)
    row.notes_json = json.dumps(notes, ensure_ascii=False)
    row.edited, row.updated_at = True, datetime.now()
    session.commit()
    return row


def is_fresh(session: Session, row: AiChartNote, data: ChartData | None = None) -> bool:
    run = session.get(Run, row.run_id)
    return input_hash(chart_input(session, run, row.key, data)) == row.input_hash


def label(indicator_id: str) -> str:
    return BY_ID[indicator_id].label
