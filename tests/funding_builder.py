"""測試用：產生計畫管理系統「計畫基本資料」格式的 xlsx（經分表）。

與實際檔案相同：第 1 列是區塊標題（含報表產製日期時間），第 2 列是欄位名稱，
且「經費資訊」與「經費用途說明」有同名欄位（業務費(300) 出現兩次，第二次是文字說明）。
"""

from io import BytesIO

from openpyxl import Workbook

HEADER = ("#", "計畫主類代碼", "計畫主類", "計畫類別", "計畫流水號", "國科會計畫編號", "計畫名稱-中文",
          "委託單位名稱", "計畫執行單位一級名稱", "計畫執行單位二級名稱", "計畫主持人主聘單位一級",
          "計畫主持人主聘單位二級", "計畫主持人", "年度", "學年度", "經費總額", "業務費(300)", "業務費(300)")


def build_funding(rows: list[tuple], generated: str = "2026-11-02 09:57:42") -> bytes:
    """rows：(年度, 計畫主類, 主聘單位一級, 主聘單位二級, 經費總額[, 執行單位二級])"""
    wb = Workbook()
    ws = wb.active
    ws.title = "Worksheet"
    ws.append((None, f"計畫基本資料 【報表產製日期時間:{generated}】", *([None] * 13), "經費資訊", None, "經費用途說明"))
    ws.append(HEADER)
    for n, r in enumerate(rows, 1):
        year, main, college, dept, amount = r[:5]
        exec_dept = r[5] if len(r) > 5 else f"{college}{dept}"
        ws.append((n, "MOST" if main == "國科會計畫" else "AA", main, main, f"S{n:04d}", None, f"計畫{n}", "委託單位",
                   college, exec_dept, college, dept, f"主持人{n}", year, year, amount, amount, "人事費說明文字"))
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def sample_funding() -> bytes:
    """114 年度：資管 國科會 2 件 + 產學 1 件；財金 國科會 1 件；另有不計入的技術服務收入與他院計畫。"""
    return build_funding([
        (114, "國科會計畫", "管理學院", "資訊管理學系", 1000000),
        (114, "國科會計畫", "管理學院", "資訊管理學系", 500000),
        (114, "產學合作計畫", "管理學院", "資訊管理學系", 300000),
        (114, "國科會計畫", "管理學院", "財務金融學系", 800000),
        (114, "技術服務收入-校內", "管理學院", "資訊管理學系", 999999),
        (114, "國科會計畫", "資訊電機學院", "資訊工程學系", 700000),
    ])
