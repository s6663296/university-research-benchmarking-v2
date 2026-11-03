"""解析研發處計畫管理系統匯出的「計畫基本資料」xlsx（經分表）。

第 1 列是區塊標題（含「報表產製日期時間」），第 2 列是欄位名稱，第 3 列起每列一個計畫。
「經費資訊」與「經費用途說明」兩個區塊有同名欄位（例如 業務費(300)），一律取第一次出現的欄位。
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from io import BytesIO

from openpyxl import load_workbook

from app.textnorm import clean, is_example_row

HEADER_SCAN_ROWS = 10

# 欄位 → 經分表上的欄位名稱
COLUMNS = {
    "year": "年度",
    "main_category": "計畫主類",
    "category": "計畫類別",
    "serial": "計畫流水號",
    "nstc_no": "國科會計畫編號",
    "title": "計畫名稱-中文",
    "pi": "計畫主持人",
    "pi_college": "計畫主持人主聘單位一級",
    "pi_dept": "計畫主持人主聘單位二級",
    "exec_college": "計畫執行單位一級名稱",
    "exec_dept": "計畫執行單位二級名稱",
    "sponsor": "委託單位名稱",
    "amount": "經費總額",
}
REQUIRED = ("year", "main_category", "amount", "pi_dept")


class FundingFormatError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedProject:
    row: int
    year: int
    main_category: str | None
    category: str | None
    serial: str | None
    nstc_no: str | None
    title: str | None
    pi: str | None
    pi_college: str | None
    pi_dept: str | None
    exec_college: str | None
    exec_dept: str | None
    sponsor: str | None
    amount: Decimal


@dataclass
class ParsedFunding:
    projects: list[ParsedProject] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    generated_at: str | None = None

    @property
    def years(self) -> list[int]:
        return sorted({p.year for p in self.projects})


def _key(value) -> str:
    return re.sub(r"\s+", "", str(value)) if value is not None else ""


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _amount(value) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return Decimal(0)
    try:
        return Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        return None


def parse_funding(data: bytes) -> ParsedFunding:
    wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        rows = ws.iter_rows(values_only=True)
        result = ParsedFunding()
        header: dict[str, int] | None = None
        header_row = 0
        for n, row in enumerate(rows, 1):
            if header is None:
                for v in row:
                    m = re.search(r"產製日期時間[:：]\s*([\d\-/: ]+)", str(v or ""))
                    if m:
                        result.generated_at = m.group(1).strip()
                names = [_key(v) for v in row]
                if "計畫主類" in names and "經費總額" in names:
                    header, header_row = {}, n
                    for i, name in enumerate(names):
                        header.setdefault(name, i)
                    missing = [COLUMNS[k] for k in REQUIRED if _key(COLUMNS[k]) not in header]
                    if missing:
                        raise FundingFormatError(f"缺少欄位：{'、'.join(missing)}")
                elif n >= HEADER_SCAN_ROWS:
                    raise FundingFormatError("找不到欄位名稱列（需要有「計畫主類」與「經費總額」欄位）")
                continue
            if not any(v not in (None, "") for v in row) or is_example_row(row):   # 範本的示範列不匯入
                continue
            values = {k: (row[header[_key(name)]] if _key(name) in header and header[_key(name)] < len(row) else None)
                      for k, name in COLUMNS.items()}
            year, amount = _int(values["year"]), _amount(values["amount"])
            if year is None:
                result.warnings.append(f"第 {n} 列沒有年度，已略過")
                continue
            if amount is None:
                result.warnings.append(f"第 {n} 列經費總額不是數字（{values['amount']}），已略過")
                continue
            text = {k: clean(values[k]) for k in COLUMNS if k not in ("year", "amount")}
            if text["serial"] is not None:
                text["serial"] = str(values["serial"]).strip()
            result.projects.append(ParsedProject(row=n, year=year, amount=amount, **text))
        if header is None:
            raise FundingFormatError("找不到欄位名稱列（需要有「計畫主類」與「經費總額」欄位）")
        if not result.projects:
            raise FundingFormatError(f"第 {header_row} 列之後沒有任何計畫資料")
        return result
    finally:
        wb.close()
