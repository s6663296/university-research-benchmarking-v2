"""產生系統內建的信件範本 templates/letter_template.docx（研發處「給各位主任所長」信件）。

信中兩份 Excel 的檔名用標記 {{對標學校確認表}}、{{教師人數確認表}}，產生資料包時換成實際檔名。
研發處若另有修改版，可在「設定 → 信件範本」上傳，不需要改程式。執行：python scripts/make_letter_template.py
"""

from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt

OUT = Path(__file__).resolve().parents[1] / "templates" / "letter_template.docx"

BODY = [
    ("各位主任、所長，您好：", None),
    ("自民國 115 年度起，校方請研發處彙整各單位研究計畫及論文表現分析，供員額小組參考，相關內容詳如附件簡報。", None),
    ("簡報所列分析指標係參考 QS、THE 世界大學排名及教育部相關指標，論文資料取自 Scopus 及 SciVal，"
     "教師人數取自教育部資訊公開平台。對標學校主要包括標竿學校（臺大、清大、陽明交大、成大）"
     "及競爭學校（中山、中正、中興、政大）。", None),
    ("敬請協助檢視簡報所列對標學校及系所是否適合與貴單位比較，並依下列方式確認或修正資料：", None),
    ("一、確認對標學校及系所", "heading"),
    ("（一）如現列對標學校及系所合宜，請於 Excel 檔（檔名：{{對標學校確認表}}）"
     "確認該校系所在 Scopus 論文資料中使用的機構英文名稱是否正確。", "item"),
    ("（二）如建議調整對標學校或系所，請於同一 Excel 檔填列建議採用的學校、系所，"
     "以及該系所在 Scopus 論文資料中使用的機構英文名稱。建議對標學校請以上述標竿學校及競爭學校為原則。"
     "如建議納入其他學校，研發處將另提請校方核定。", "item"),
    ("二、修正或新增機構英文名稱", "heading"),
    ("請直接於上述 Excel 檔填寫，並以顏色標註修正或新增的儲存格。同一系所如有多個機構英文名稱，"
     "請填於同一儲存格，並以逗號分隔。", "item"),
    ("三、確認教師人數", "heading"),
    ("如教師人數需修正，請於教師人數 Excel 檔（檔名：{{教師人數確認表}}）直接更新，並註明修正理由。", "item"),
    ("敬請檢視後回覆確認結果。如有修正或新增資料，請一併回傳更新後的 Excel 檔，以利研發處彙整。", None),
    ("感謝各位主任、所長協助。", None),
]


def main() -> None:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "標楷體"
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "標楷體")
    style.font.size = Pt(12)
    style.paragraph_format.space_after = Pt(6)
    style.paragraph_format.line_spacing = 1.5
    for text, kind in BODY:
        p = doc.add_paragraph()
        run = p.add_run(text)
        if kind == "heading":
            run.bold = True
            p.paragraph_format.space_before = Pt(12)
            p.paragraph_format.space_after = Pt(0)
        elif kind is None and text not in (BODY[0][0], BODY[-1][0]):
            p.paragraph_format.first_line_indent = Pt(24)
        if text == BODY[-1][0]:
            p.paragraph_format.space_before = Pt(12)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
