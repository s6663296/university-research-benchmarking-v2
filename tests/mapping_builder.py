"""測試用：以 dict 產生 mapping xlsx。"""

from io import BytesIO

from openpyxl import Workbook

HEADER = (None, "中大系所", "學校", "系所中文名稱", "系所英文名稱", "備註")

NCU_EN = {
    "資訊管理學系": "Department of Information Management, National Central University",
    "財務金融學系": "Department of Finance, National Central University",
    "會計研究所": "Graduate Institute of Accounting, National Central University",
}


def build_mapping(sheets: dict[str, list[tuple]], header: tuple = HEADER) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        ws.append(header)
        for r in rows:
            ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def ncu_rows(depts=("資訊管理學系", "財務金融學系", "會計研究所")) -> list[tuple]:
    return [("管理學院", d, "管理學院", d, NCU_EN.get(d, f"Department {d}, National Central University"), None) for d in depts]


def sample_mapping() -> bytes:
    """中央 3 系所；台大有資管與財金；政治只有資管。"""
    return build_mapping({
        "中央": ncu_rows(),
        "台大": [
            ("管理學院", "資訊管理學系", "管理學院", "資訊管理學系", "Department of Information Management, National Taiwan University", None),
            ("管理學院", "財務金融學系", "管理學院", "財務金融學系", "Department of Finance, National Taiwan University", None),
            ("管理學院", "會計研究所", "x", "x", "x", None),
        ],
        "政治": [
            ("管理學院", "資訊管理學系", "商學院", "資訊管理學系", "Department of Management Information Systems, National Chengchi University", None),
            ("管理學院", "會計研究所", "X", "X", None, None),
        ],
    })
