"""經分表：匯入、選用資料、計算各系所國科會及產學計畫經費年平均（報告第 I 部分，範例第 3 頁）。

計算方式：
- 國科會：計畫主類為「國科會計畫」；產學：計畫主類為「產學合作計畫」；其他主類（技術服務收入等）不計入
- 歸屬系所：以「計畫主持人主聘單位二級」對應中大系所（財經系報告註明以主持人主聘單位計算）
- 年平均 = 分析期間內各年度加總 ÷ 有經分表資料的年度數；件數同樣取年平均
- 師均經費 = （國科會年平均 + 產學年平均）÷ 單年教師數（與論文指標相同的教育部教師數）
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import FundingImport, FundingProject, Run, RunUnit, Unit
from app.funding.parser import parse_funding
from app.mapping.parser import is_ncu
from app.mapping.service import units_of
from app.runs.scope import RunConfig
from app.teachers.service import teacher_count, teacher_key
from app.textnorm import norm_name

ROC_OFFSET = 1911
NSTC_CATEGORIES = ("國科會計畫",)
INDUSTRY_CATEGORIES = ("產學合作計畫",)


def to_roc(year: int) -> int:
    return year - ROC_OFFSET


def import_funding(session: Session, filename: str, data: bytes) -> tuple[FundingImport, list[str]]:
    """匯入經分表；內容與既有檔案相同時不重複匯入。回傳（匯入紀錄, 略過的列）。"""
    sha = hashlib.sha256(data).hexdigest()
    same = session.scalars(select(FundingImport).where(FundingImport.sha256 == sha)).first()
    if same is not None:
        return same, []
    parsed = parse_funding(data)
    imp = FundingImport(filename=filename, sha256=sha, imported_at=datetime.now(), generated_at=parsed.generated_at,
                        years_json=json.dumps(parsed.years), row_count=len(parsed.projects))
    session.add(imp)
    session.flush()
    session.add_all(FundingProject(funding_import_id=imp.id, row=p.row, year=p.year, main_category=p.main_category,
                                   category=p.category, serial=p.serial, nstc_no=p.nstc_no, title=p.title, pi=p.pi,
                                   pi_college=p.pi_college, pi_dept=p.pi_dept, exec_college=p.exec_college,
                                   exec_dept=p.exec_dept, sponsor=p.sponsor, amount=p.amount)
                    for p in parsed.projects)
    session.commit()
    return imp, parsed.warnings


def delete_import(session: Session, import_id: int) -> None:
    session.execute(delete(FundingProject).where(FundingProject.funding_import_id == import_id))
    session.execute(delete(FundingImport).where(FundingImport.id == import_id))
    session.commit()


def imports(session: Session) -> list[FundingImport]:
    return list(session.scalars(select(FundingImport).order_by(FundingImport.id.desc())))


def import_years(imp: FundingImport) -> list[int]:
    return json.loads(imp.years_json or "[]")


def effective_imports(session: Session, roc_years: list[int] | None = None) -> dict[int, FundingImport]:
    """每個年度採用最後匯入、且含有該年度的檔案（新檔取代舊檔）。roc_years 為 None 時取全部年度。"""
    chosen: dict[int, FundingImport] = {}
    for imp in imports(session):  # 新的在前
        for y in import_years(imp):
            if (roc_years is None or y in roc_years) and y not in chosen:
                chosen[y] = imp
    return dict(sorted(chosen.items()))


def category_of(main_category: str | None) -> str | None:
    if main_category in NSTC_CATEGORIES:
        return "nstc"
    if main_category in INDUSTRY_CATEGORIES:
        return "industry"
    return None


def projects_of(session: Session, sources: dict[int, FundingImport]) -> list[FundingProject]:
    out: list[FundingProject] = []
    for year, imp in sources.items():
        out += session.scalars(select(FundingProject).where(
            FundingProject.funding_import_id == imp.id, FundingProject.year == year).order_by(FundingProject.row))
    return out


@dataclass
class FundingRow:
    ncu_dept: str
    unit_id: int
    nstc_count: int = 0
    nstc_amount: Decimal = Decimal(0)
    industry_count: int = 0
    industry_amount: Decimal = Decimal(0)
    years: int = 1
    teachers: int | None = None
    teacher_manual: bool = False
    projects: list[FundingProject] = field(default_factory=list)

    @property
    def nstc_count_avg(self) -> Decimal:
        return Decimal(self.nstc_count) / self.years

    @property
    def nstc_avg(self) -> Decimal:
        return self.nstc_amount / self.years

    @property
    def industry_avg(self) -> Decimal:
        return self.industry_amount / self.years

    @property
    def total_avg(self) -> Decimal:
        return self.nstc_avg + self.industry_avg

    @property
    def per_faculty(self) -> Decimal | None:
        return self.total_avg / self.teachers if self.teachers else None


@dataclass
class FundingTable:
    college: str
    rows: list[FundingRow]
    years: list[int]                 # 採用的年度（民國年）
    missing_years: list[int]         # 期間內沒有經分表資料的年度
    sources: dict[int, FundingImport]

    @property
    def has_data(self) -> bool:
        return bool(self.years)


def money(value: Decimal | None) -> str:
    """金額四捨五入到元，加千分位；0 顯示「-」（範例第 3 頁）。"""
    if value is None:
        return "待確認"
    n = value.quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return "-" if n == 0 else f"{n:,}"


def count_text(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(int(value))
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}"


def _teachers(session: Session, run: Run, unit: Unit, run_units: dict[int, RunUnit]) -> tuple[int | None, bool]:
    """與論文指標相同的教師數：分析中有這個單位就用分析保存的人數，否則依分析設定查教育部資料。"""
    ru = run_units.get(unit.id)
    if ru is not None:
        return ru.teacher_count, ru.teacher_source.startswith("manual")
    if run.teacher_import_id is None:
        return None, False
    tc = teacher_count(session, run.teacher_import_id, run.academic_year, run.teacher_column, *teacher_key(unit))
    return tc.value, tc.source == "manual"


def funding_table(session: Session, run: Run, college: str) -> FundingTable:
    period = [to_roc(y) for y in range(run.period_from, run.period_to + 1)]
    sources = effective_imports(session, period)
    years = list(sources)
    projects = projects_of(session, sources)
    run_units = {ru.unit_id: ru for ru in session.scalars(select(RunUnit).where(RunUnit.run_id == run.id))}
    rows = []
    for unit in units_of(session, run.mapping_version_id):
        if not is_ncu(unit.school) or unit.ncu_college != college:
            continue
        teachers, manual = _teachers(session, run, unit, run_units)
        row = FundingRow(unit.ncu_dept, unit.id, years=max(len(years), 1), teachers=teachers, teacher_manual=manual)
        target = norm_name(unit.ncu_dept)
        for p in projects:
            kind = category_of(p.main_category)
            if kind is None or norm_name(p.pi_dept or "") != target:
                continue
            row.projects.append(p)
            if kind == "nstc":
                row.nstc_count += 1
                row.nstc_amount += p.amount
            else:
                row.industry_count += 1
                row.industry_amount += p.amount
        rows.append(row)
    rows.sort(key=lambda r: (-r.nstc_avg, -r.total_avg))   # 範例依國科會年平均由高到低
    return FundingTable(college, rows, years, [y for y in period if y not in sources], sources)


def run_colleges(session: Session, run: Run) -> list[str]:
    """這次分析選到的系所所屬的學院。"""
    depts = set(RunConfig.from_json(run.scope_json).ncu_depts)
    return list(dict.fromkeys(u.ncu_college for u in units_of(session, run.mapping_version_id)
                              if is_ncu(u.school) and u.ncu_dept in depts and u.ncu_college))


def funding_notes(table: FundingTable) -> list[str]:
    """報告註腳：資料來源、年度、計算方式。"""
    files = "、".join(dict.fromkeys(
        imp.filename + (f"（產製 {imp.generated_at}）" if imp.generated_at else "") for imp in table.sources.values()))
    notes = [
        f"註1：資料來源為計畫管理系統經分表 {files}，採用 {'、'.join(map(str, table.years))} 年度。",
        f"註2：國科會為計畫主類「{'、'.join(NSTC_CATEGORIES)}」，產學為「{'、'.join(INDUSTRY_CATEGORIES)}」；"
        "以計畫主持人主聘單位計算。",
        "註3：單年教師數來自教育部公開資訊平台；標 * 者為人工修改。",
    ]
    if table.missing_years:
        notes.append(f"※經分表缺少 {'、'.join(map(str, table.missing_years))} 年度，年平均只以有資料的年度計算。")
    return notes
