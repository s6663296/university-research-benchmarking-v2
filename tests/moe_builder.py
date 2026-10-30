"""測試用：產生教育部「教1-1」格式的 xlsx（所有值皆為字串，檔尾有統計說明）。"""

from io import BytesIO

from openpyxl import Workbook

HEADER = ("學年度", "設立別", "學校類別", "學校統計處代碼", "學校名稱", "單位代碼", "單位名稱",
          "專任教師數-教師總數總計", "專任助理教授以上人數-總計")


def build_moe(rows: list[tuple]) -> bytes:
    """rows：(學年度, 學校名稱, 單位代碼, 單位名稱, 總計[, 助理教授以上])"""
    wb = Workbook()
    ws = wb.active
    ws.title = "Current"
    ws.append(("教1-1.專任教師數-以「系(所)」統計",))
    ws.append(())
    ws.append(HEADER)
    for r in rows:
        year, school, code, name, total = r[:5]
        asst = r[5] if len(r) > 5 else total
        ws.append((str(year), "公立", "一般大學", "0000", school, code, name, str(total), str(asst)))
    ws.append(("統計說明：",))
    ws.append(("學年度：係指【學年度】為篩選條件。",))
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def sample_moe() -> bytes:
    """配合 mapping_builder.sample_mapping 的教師數。"""
    rows = []
    for year in (113, 114):
        rows += [
            (year, "國立中央大學", "06131053", "資訊管理學系", 24, 23),
            (year, "國立中央大學", "04131027", "財務金融學系", 10, 10),
            (year, "國立中央大學", "04199030", "會計研究所", 8, 8),
            (year, "國立臺灣大學", "01131001", "資訊管理學系", 16, 15),
            (year, "國立臺灣大學", "01131002", "財務金融學系", 20, 19),
            (year, "國立政治大學", "02131001", "資訊管理學系", 17, 17),
        ]
    return build_moe(rows)
