"""空白的對標學校資料表（mapping 表）範本：只有欄位名稱與說明，供承辦人下載後填寫。

範本必須能被 parse_mapping 讀取（測試會檢查），格式改了這裡也要一起改。
"""

from io import BytesIO

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill

from app.mapping.parser import NCU_SHEET
from app.textnorm import EXAMPLE_MARK

EXAMPLE_SHEET = "對標學校（分頁名改成校名）"
WIDTHS = (14, 22, 16, 24, 60, 30)
HEADER_FILL = PatternFill("solid", fgColor="DDE7F3")
EXAMPLE_FILL = PatternFill("solid", fgColor="FFF2CC")
EXAMPLE_FONT = Font(italic=True, color="7F6000")
EXAMPLE_NOTE = f"{EXAMPLE_MARK}此列僅供參考格式，上傳時系統會自動略過；填寫時可刪除或直接覆蓋"
NOTES = (
    "學院：中大系所所屬的中大學院（例如 管理學院）。系統用這欄分院做院內比較。",
    "中大系所：中大系所的中文全名（例如 資訊管理學系）。各校分頁用這欄對應到中大分頁的同一列，"
    "名稱必須和「國立中央大學」分頁完全相同。標題只要以「系所」結尾即可。",
    "該校學院：該校對應系所所屬的學院（例如 商學院）。中大分頁填中大自己的學院。",
    "系所中文名稱：該校對應系所的中文名稱，用來對應教育部教師數。填 x 或留空表示該校沒有對應系所。",
    "系所英文名稱：Scopus 的查詢條件，需和 Scopus 上的單位名稱一致，"
    "例如 Department of Information Management, National Taiwan University。填 x 或留空表示不列入對標。",
    "備註：自由填寫，系統不讀取。",
)


def _sheet(ws, school_header: str, example: tuple) -> None:
    headers = ("學院", f"{NCU_SHEET}系所", school_header, "系所中文名稱", "系所英文名稱", "備註")
    ws.append(headers)
    ws.append((*example, EXAMPLE_NOTE))
    for cell in ws[2]:
        cell.fill, cell.font = EXAMPLE_FILL, EXAMPLE_FONT
    for col, (width, note) in enumerate(zip(WIDTHS, NOTES), start=1):
        cell = ws.cell(1, col)
        cell.font, cell.fill = Font(bold=True), HEADER_FILL
        cell.alignment = Alignment(vertical="center")
        cell.comment = Comment(note, "系統", width=320, height=140)
        ws.column_dimensions[cell.column_letter].width = width
    ws.freeze_panes = "A2"


def mapping_template() -> bytes:
    """第一個分頁是中大自己（名稱固定「國立中央大學」），之後每間對標學校一個分頁，分頁名稱就是校名。"""
    wb = Workbook()
    _sheet(wb.active, NCU_SHEET, ("管理學院", "資訊管理學系", "管理學院", "資訊管理學系",
                                  "Department of Information Management, National Central University"))
    wb.active.title = NCU_SHEET
    _sheet(wb.create_sheet(EXAMPLE_SHEET), "（該校名稱）",
           ("管理學院", "資訊管理學系", "管理學院", "資訊管理學系",
            "Department of Information Management, National Taiwan University"))
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
