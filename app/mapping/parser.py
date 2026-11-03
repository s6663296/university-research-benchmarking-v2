"""解析人工維護的 mapping 表（設計 §5.2）。

每間學校一個分頁；欄位：學院 | 中大系所（標題只要以「系所」結尾）| <學校> | 系所中文名稱 | 系所英文名稱 | 備註。
中大分頁（「國立中央大學」，舊版用「中央」）放中大各系所自己的英文名稱。
"""

from dataclasses import dataclass, field
from io import BytesIO
from typing import Literal

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from app.textnorm import clean, is_example_row, norm_name

NCU_SHEET = "國立中央大學"
NCU_SHEETS = (NCU_SHEET, "中央")  # 舊版 mapping 的分頁用簡稱


def is_ncu(school: str) -> bool:
    return school in NCU_SHEETS
MAX_COLUMNS = 6  # 第 7 欄（G）以後的內容忽略並提醒


def _is_dept_header(title: str | None) -> bool:
    """第二欄標題的寫法每年可能不同（「中大系所」「國立中央大學系所」…），以「系所」結尾即可。"""
    return bool(title) and title.replace(" ", "").endswith("系所") and len(title.replace(" ", "")) > 2


@dataclass(frozen=True)
class ParsedUnit:
    school: str
    ncu_college: str | None
    ncu_dept: str
    college: str | None
    dept_zh: str | None
    dept_en: str
    remark: str | None
    row: int


@dataclass(frozen=True)
class Issue:
    sheet: str
    row: int | None
    level: Literal["error", "warning"]
    message: str


@dataclass
class ParseResult:
    units: list[ParsedUnit] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


def _find_column(header: list[str | None], prefix: str) -> int | None:
    for i, h in enumerate(header):
        if h and h.replace(" ", "").startswith(prefix):
            return i
    return None


def _parse_sheet(ws, result: ParseResult) -> list[ParsedUnit]:
    sheet = ws.title
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = [clean(v) for v in rows[0]]
    col_dept = 1 if len(header) > 1 and _is_dept_header(header[1]) else None
    col_zh = _find_column(header, "系所中文名稱")
    col_en = _find_column(header, "系所英文名稱")
    col_remark = _find_column(header, "備註")
    if col_dept is None or col_en is None:
        missing = "「中大系所」" if col_dept is None else "「系所英文名稱」"
        result.issues.append(Issue(sheet, 1, "error", f"分頁「{sheet}」缺少{missing}欄位，整個分頁無法使用"))
        return []

    def cell(row: tuple, idx: int | None) -> str | None:
        return clean(row[idx]) if idx is not None and idx < len(row) else None

    units: list[ParsedUnit] = []
    for r_idx, row in enumerate(rows, start=1):
        for c_idx in range(MAX_COLUMNS, len(row)):
            if clean(row[c_idx]) is not None:
                ref = f"{get_column_letter(c_idx + 1)}{r_idx}"
                result.issues.append(Issue(sheet, r_idx, "warning", f"儲存格 {ref} 在第 {MAX_COLUMNS} 欄之後，已忽略：{row[c_idx]}"))
        if r_idx == 1 or is_example_row(row):   # 範本的示範列不匯入
            continue
        ncu_dept = cell(row, col_dept)
        if ncu_dept is None:
            continue
        ncu_college, college = cell(row, 0), cell(row, 2)
        dept_zh, dept_en = cell(row, col_zh), cell(row, col_en)
        if dept_en is None:
            if is_ncu(sheet) and ncu_college is not None:
                result.issues.append(Issue(sheet, r_idx, "error", f"中大系所「{ncu_dept}」缺少英文名稱，無法查詢"))
            elif dept_zh is not None:
                result.issues.append(Issue(sheet, r_idx, "warning",
                                           f"「{ncu_dept}」對應「{dept_zh}」有中文名稱但缺英文名稱，不列入對標"))
            continue
        units.append(ParsedUnit(sheet, ncu_college, ncu_dept, college, dept_zh, dept_en, cell(row, col_remark), r_idx))

    seen: dict[str, ParsedUnit] = {}
    for u in units:
        key = norm_name(u.dept_en).lower()
        if key in seen and seen[key].ncu_dept != u.ncu_dept:
            result.issues.append(Issue(sheet, u.row, "warning",
                                       f"「{seen[key].ncu_dept}」與「{u.ncu_dept}」使用相同的英文名稱 {u.dept_en}，兩者會得到相同的數字"))
        seen.setdefault(key, u)
    return units


def parse_mapping(data: bytes) -> ParseResult:
    wb = load_workbook(BytesIO(data), data_only=True)
    result = ParseResult()
    for ws in wb.worksheets:
        result.units += _parse_sheet(ws, result)

    ncu_sheet = next((s for s in wb.sheetnames if is_ncu(s)), None)
    if ncu_sheet is None:
        result.issues.insert(0, Issue(NCU_SHEET, None, "error",
                                      f"缺少「{NCU_SHEET}」分頁，沒有中大系所的英文名稱，無法建立分析"))
    else:
        ncu_rows = {clean(r[1]) for r in wb[ncu_sheet].iter_rows(min_row=2, values_only=True) if len(r) > 1}
        for u in result.units:
            if not is_ncu(u.school) and u.ncu_dept not in ncu_rows:
                result.issues.append(Issue(u.school, u.row, "warning",
                                           f"中大系所「{u.ncu_dept}」不在「{ncu_sheet}」分頁中"))
    return result
