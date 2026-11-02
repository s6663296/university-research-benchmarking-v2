"""空白的經分表範本：與計畫管理系統匯出的「計畫基本資料」xlsx 欄位完全相同（共 102 欄），沒有任何資料。

系統會讀取的欄位以底色標示。範本必須能被 parse_funding 找到欄位名稱列（測試會檢查）。
"""

from io import BytesIO

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill

from app.funding.parser import COLUMNS, REQUIRED

# 第 1 列的區塊標題：（欄位索引, 文字）
BLOCKS = ((1, "計畫基本資料 【報表產製日期時間:】"), (57, "分項分總簽核資訊"), (65, "經費資訊"), (78, "經費用途說明"))
HEADERS = (
    "#", "計畫主類代碼", "計畫主類", "計畫類別代碼", "計畫類別", "是否專簽管理費調降", "計畫流水號", "期別",
    "國科會計畫編號", "計畫名稱-中文", "計畫名稱-英文", "委託單位統計用代碼", "委託單位名稱", "委託單位屬性",
    "委託單位資本額", "委託單位地點", "是否為國際單位", "是否為台灣分公司/分部/辦事處", "公司/委託單位國別",
    "其他委託單位", "管理費單位類型", "計畫執行單位一級代碼", "計畫執行單位一級名稱", "計畫執行單位二級代碼",
    "計畫執行單位二級名稱", "計畫主持人主聘單位一級", "計畫主持人主聘單位二級", "計畫主持人", "計畫主持人身分別",
    "計畫主持人在職狀態", "執行計畫身份是否為主管", "執行計畫身份單位", "執行計畫身份職稱", "計畫歸屬",
    "計畫期間是否延長/提前終止", "(西元年)計畫起日", "(西元年)計畫迄日", "(西元年)原計畫迄日", "(民國年)計畫起日",
    "(民國年)計畫迄日", "(民國年)原計畫迄日", "年度", "學年度", "承接計畫來源", "研究性質", "彈性支用額度",
    "是否國際合作", "國際合作單位/國家", "計畫型別", "總計畫/子計畫", "政府GRB領域", "國科會學門領域",
    "國科會研究計畫類別", "計畫中文摘要", "計畫英文摘要", "計畫中文關鍵詞", "計畫英文關鍵詞", "計畫權責單位",
    "是否結案", "TableId", "總計畫流水號", "國科會分案類別", "是否與聯合國永續發展目標SDGs相關",
    "是否同意於教師履歷平台公開", "SDGs目標", "平行簽總主持人是否為主管", "平行簽總主持人執行計畫身份單位",
    "平行簽總主持人執行計畫身份職稱", "總主持人", "平行簽分項主持人是否為主管", "平行簽分項主持人執行計畫身份單位",
    "平行簽分項主持人執行計畫身份職稱", "分項主持人", "經費總額", "人事費(100)", "經常門(1000)", "鐘點費(120)",
    "公費(29)", "業務費(300)", "彈性支用(3001)", "P人事費(3002)", "產學及技術媒合服務費(3006)", "耗材費(323)",
    "管理費(333)", "國外差旅費(503)", "研究設備費(980)", "人事費(100)", "經常門(1000)", "鐘點費(120)", "公費(29)",
    "業務費(300)", "彈性支用(3001)", "P人事費(3002)", "產學及技術媒合服務費(3006)", "耗材費(323)", "管理費(333)",
    "國外差旅費(503)", "執行國際合作與移地研究(503-01)", "出席國際學術會議(503-02)", "出國參訪及考察(503-03)",
    "參與國際展覽費(503-04)", "研究設備費(980)",
)
REQUIRED_FILL = PatternFill("solid", fgColor="F8CBAD")   # 必要欄位：缺少就無法匯入
USED_FILL = PatternFill("solid", fgColor="DDE7F3")       # 系統會讀取，但缺少仍可匯入
NOTES = {
    "年度": "必要。民國年（例如 114），系統依此欄判斷計畫屬於哪一年。",
    "計畫主類": "必要。「國科會計畫」計入國科會、「產學合作計畫」計入產學，其他主類不計入。",
    "計畫主持人主聘單位二級": "必要。依此欄把計畫歸到中大系所，名稱需和對標學校資料表的中大系所相同（例如 資訊管理學系）。",
    "經費總額": "必要。數字，空白視為 0。",
}


def funding_template() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Worksheet"
    first = [None] * len(HEADERS)
    for idx, text in BLOCKS:
        first[idx] = text
    ws.append(first)
    ws.append(HEADERS)
    required = {COLUMNS[k] for k in REQUIRED}
    used = set(COLUMNS.values())
    for col, name in enumerate(HEADERS, start=1):
        cell = ws.cell(2, col)
        cell.font = Font(bold=True)
        if name in used and HEADERS.index(name) == col - 1:   # 同名欄位只標第一個（系統讀第一個）
            cell.fill = REQUIRED_FILL if name in required else USED_FILL
            cell.comment = Comment(NOTES.get(name, "系統會讀取這一欄（用於明細與核對）。"), "系統", width=300, height=110)
        ws.column_dimensions[cell.column_letter].width = max(10, min(len(name) * 2 + 2, 30))
    for col in range(1, len(HEADERS) + 1):
        if first[col - 1]:
            ws.cell(1, col).font = Font(bold=True)
    ws.freeze_panes = "A3"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
