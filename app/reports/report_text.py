"""簡報的目錄名稱與各頁備註：預設值、使用者修改（存於設定 report_text）與套用規則。

每個項目只有一個「目錄名稱」：沒有修改時，目錄與頁面標題都維持範例簡報的文字（兩者用字本來就不完全相同）；
修改後，目錄、章節標籤、頁面標題一律跟著新名稱，不必分開改。
文字可用 {系所}、{期間} 等變數，同一份設定才能套用到每個系所的簡報。
"""

import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Run
from app.db.settings_store import get_setting
from app.elsevier.selfcite import get_strategy
from app.funding.service import FUNDING_NOTE_TEMPLATES, FundingTable, funding_files_text, funding_table
from app.mapping.parser import is_ncu
from app.mapping.service import units_of
from app.metrics.indicators import BY_ID, importance_note
from app.runs.scope import RunConfig

# 變數名稱 → 給使用者看的白話名稱（編輯頁的標籤提示與插入選單）
VARIABLES = {
    "期間": "分析期間",
    "年數": "分析年數",
    "學院": "系所所屬學院",
    "系所": "系所名稱",
    "學年度": "教師數的學年度",
    "資料日期": "資料取得日期",
    "名次門檻": "排名總表標紅的名次",
    "自我引用方式": "排除自我引用的方式",
    "經費年度": "經分表採用的年度",
    "經費年數": "經分表的年度數",
    "經費檔案": "經分表檔名",
}
# 每一頁都用得到的變數；其餘只在預設文字用到它的頁面提供
GENERAL_VARIABLES = ("期間", "年數", "學院", "系所", "學年度", "資料日期")
# 還沒有完成的分析可取值時，編輯頁標籤顯示的範例
FALLBACK_SAMPLES = {
    "期間": "2021-2025", "年數": "5", "學院": "管理學院", "系所": "資訊管理學系", "學年度": "114",
    "資料日期": "2026-01-01", "名次門檻": "3", "自我引用方式": "排除自我引用", "經費年度": "114",
    "經費年數": "1", "經費檔案": "經分表.xlsx",
}

BASE_NOTES = ("註1：單年教師數來自教育部公開資訊平台（{學年度}學年度）；標 * 者為人工修改。\n"
              "註2：資料來源 Scopus、SciVal，資料取得日期 {資料日期}。")


@dataclass(frozen=True)
class PageText:
    key: str
    label: str                  # 編輯頁上的項目名稱
    name: str                   # 目錄名稱（預設）
    tag: str = ""               # 目錄名稱後的括號標註，只出現在目錄
    title: str = ""             # 頁面標題（預設）；空字串表示此章沒有自己的頁面
    title_prefix: str = ""      # 修改目錄名稱後，頁面標題 = title_prefix + 新名稱
    kicker: str = ""            # 章節標籤（預設，不含編號）；只有章節才有
    subtitle: str = ""
    formula: str = ""
    notes: str = ""
    is_chapter: bool = False


def _count_formula(definition: str) -> str:
    return f"A = {definition}的 {{年數}} 年篇數；B = 單年教師數\n單年師均 = A ÷ {{年數}} ÷ B"


PERIOD = "{期間} "
PAGES: tuple[PageText, ...] = (
    PageText(
        "funding", "第一章：計畫經費", "{學院}國科會及產學計畫經費年平均", title="{學院}國科會及產學計畫經費年平均",
        kicker="國科會及產學計畫經費", is_chapter=True,
        subtitle="資料來源：計畫管理系統經分表（{經費年度} 年度）；以計畫主持人主聘單位歸屬系所",
        formula="A = 國科會計畫經費加總 ÷ {經費年數}（年度數）\nB = 產學合作計畫經費加總 ÷ {經費年數}（年度數）\n"
                "C = A + B；師均經費 = C ÷ D（單年教師數）",
        notes="\n".join(FUNDING_NOTE_TEMPLATES)),
    PageText(
        "summary", "第二章：論文指標排名", "{學院}各系所論文指標於對標學校排名", title="{學院}各系所論文指標排名",
        kicker="論文指標排名總覽", is_chapter=True,
        subtitle="各指標在比較群組內的名次（#1 為最高），名次 ≤ {名次門檻} 以紅字標示",
        formula="名次：在各比較群組內依指標數值由高到低排序，同值同名次\n"
                "對標學校: N間 = 含中央在內、該系所對標比較的單位數\n{學院}內 = 與{學院}內系所比較",
        notes=BASE_NOTES),
    PageText("detail", "第三章：各指標比較", "{系所}各指標比較", kicker="{系所}各指標比較", is_chapter=True),
    PageText(
        "pubs", "論文發表篇數", "論文發表師均", "QS / THE 看總篇數、高教深耕看 Q1", PERIOD + "論文發表篇數師均", PERIOD,
        subtitle=f"{importance_note(BY_ID['q1'])}（Q1）　{importance_note(BY_ID['total'])}（論文發表篇數）",
        formula="A = Q1 期刊論文 {年數} 年總數 ÷ {年數}；B = 論文總數 ÷ {年數}；C = 單年教師數\n"
                "Q1 師均 = A ÷ C；總計師均 = B ÷ C\nQ1：CiteScore 期刊排名前 25%",
        notes=BASE_NOTES + "\n註3：單位排序以 Q1 單年師均由高到低排序。"),
    PageText(
        "top1", "前 1% 論文", "論文被引次數達全球前 1% 論文篇數師均", "THE 排名、高教深耕指標",
        PERIOD + "被引數達全球前1%篇數（師均）", PERIOD, subtitle=importance_note(BY_ID["top1"]),
        formula=_count_formula("被引次數達全球同領域前 1% 的論文"), notes=BASE_NOTES),
    PageText(
        "top10", "前 10% 論文", "論文被引次數達全球前 10% 論文篇數師均", "THE 排名指標",
        PERIOD + "被引數達全球前10%篇數（師均）", PERIOD, subtitle=importance_note(BY_ID["top10"]),
        formula=_count_formula("被引次數達全球同領域前 10% 的論文"), notes=BASE_NOTES),
    PageText(
        "fwci", "FWCI", "FWCI", "THE 排名、高教深耕指標", PERIOD + "FWCI", PERIOD,
        subtitle=importance_note(BY_ID["fwci"]),
        formula="FWCI = 各篇論文 FWCI（領域加權引用影響係數）的平均\n1 表示與全球同領域平均相同", notes=BASE_NOTES),
    PageText(
        "avg_citations", "平均被引次數", "論文平均被引次數，排除自我引用", "QS 排名指標",
        PERIOD + "平均論文被引次數（排除自我引用）", PERIOD, subtitle=importance_note(BY_ID["avg_citations"]),
        formula="平均論文被引次數 = 各篇被引次數加總 ÷ 論文總篇數\n被引次數排除自我引用",
        notes=BASE_NOTES + "\n註3：排除自我引用方式：{自我引用方式}"),
    PageText(
        "intl_collab", "國際合著", "國際合著論文師均", "QS / THE / 高教深耕指標",
        PERIOD + "國際合著篇數（師均）", PERIOD, subtitle=importance_note(BY_ID["intl_collab"]),
        formula=_count_formula("與國外機構合著的論文"), notes=BASE_NOTES),
)
BY_KEY = {page.key: page for page in PAGES}
INDICATOR_KEYS = tuple(page.key for page in PAGES if not page.is_chapter)
FIELDS = ("name", "tag", "subtitle", "formula", "notes")
_VARIABLE = re.compile(r"\{(" + "|".join(VARIABLES) + r")\}")


def page_variables(page: PageText) -> list[str]:
    """編輯這一頁時可插入的變數，依 VARIABLES 的順序。"""
    used = set(_VARIABLE.findall(" ".join((page.title, page.title_prefix, *(getattr(page, f) for f in FIELDS)))))
    return [name for name in VARIABLES if name in GENERAL_VARIABLES or name in used]


def report_values(session: Session, run: Run, ncu_dept: str, college: str, funding: FundingTable) -> dict[str, Any]:
    """單一系所的簡報中，各變數實際代入的值。"""
    strategy = get_strategy(run.self_citation_strategy)
    return {
        "期間": f"{run.period_from}-{run.period_to}", "年數": run.period_to - run.period_from + 1,
        "學院": college, "系所": ncu_dept, "學年度": run.academic_year,
        "資料日期": json.loads(run.notes_json or "{}").get("data_date", ""),
        "名次門檻": get_setting(session, "rank_highlight_top"),
        "自我引用方式": strategy.label + ("（待驗證）" if not strategy.verified else ""),
        "經費年度": "、".join(map(str, funding.years)), "經費年數": len(funding.years),
        "經費檔案": funding_files_text(funding),
    }


def sample_values(session: Session) -> dict[str, str]:
    """編輯頁標籤顯示的範例值：取最近一次完成的分析中第一個系所，取不到的項目用固定範例。"""
    run = session.scalars(select(Run).where(Run.status == "done").order_by(Run.id.desc()).limit(1)).first()
    if run is None:
        return dict(FALLBACK_SAMPLES)
    depts = RunConfig.from_json(run.scope_json).ncu_depts
    unit = next((u for u in units_of(session, run.mapping_version_id) if is_ncu(u.school) and u.ncu_dept in depts), None)
    if unit is None:
        return dict(FALLBACK_SAMPLES)
    college = unit.ncu_college or ""
    values = report_values(session, run, unit.ncu_dept, college, funding_table(session, run, college))
    return {name: str(values[name] or "") or FALLBACK_SAMPLES[name] for name in VARIABLES}


def fill(text: str, values: dict[str, Any]) -> str:
    """只代換認得的變數；使用者自己打的其他大括號原樣保留。"""
    return _VARIABLE.sub(lambda m: str(values.get(m.group(1), "")), text)


def default_value(key: str, field: str) -> str:
    return getattr(BY_KEY[key], field)


def clean_overrides(raw: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """只留下與預設不同的欄位。目錄名稱不可空白（空白視為恢復預設）；其他欄位清空表示該頁不顯示。"""
    result: dict[str, dict[str, str]] = {}
    for key, values in raw.items():
        if key not in BY_KEY:
            continue
        kept = {}
        for field, value in values.items():
            if field not in FIELDS:
                continue
            value = value.replace("\r\n", "\n").strip()
            if value != default_value(key, field) and (value or field != "name"):
                kept[field] = value
        if kept:
            result[key] = kept
    return result


class ReportText:
    """把預設值、使用者修改與單一系所的變數合成簡報上實際顯示的文字。"""

    def __init__(self, overrides: dict[str, dict[str, str]], values: dict[str, Any]):
        self.overrides, self.values = overrides, values

    def field(self, key: str, field: str) -> str:
        return fill(self.overrides.get(key, {}).get(field, default_value(key, field)), self.values)

    def _renamed(self, key: str) -> bool:
        return "name" in self.overrides.get(key, {})

    def toc_line(self, key: str) -> str:
        tag = self.field(key, "tag")
        return f"{self.field(key, 'name')}（{tag}）" if tag else self.field(key, "name")

    def title(self, key: str) -> str:
        page = BY_KEY[key]
        return fill(page.title_prefix, self.values) + self.field(key, "name") if self._renamed(key) \
            else fill(page.title, self.values)

    def kicker(self, key: str) -> str:
        return self.field(key, "name") if self._renamed(key) else fill(BY_KEY[key].kicker, self.values)
