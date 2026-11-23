"""產生系統內建的暫用信件範本 templates/letter_template.docx。

研發處的正式信件（給各位主任所長.docx）要經研發長確認，確認版由承辦人在「設定 → 信件範本」上傳，
不放進程式。這份只示範可用的標記，並註明是暫用範本。執行：python scripts/make_letter_template.py
"""

from pathlib import Path

from docx import Document
from docx.shared import Pt, RGBColor

OUT = Path(__file__).resolve().parents[1] / "templates" / "letter_template.docx"


def main() -> None:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "標楷體"
    style.font.size = Pt(12)
    note = doc.add_paragraph()
    run = note.add_run("【系統暫用範本】正式信件請到系統「設定 → 信件範本」上傳研發處確認版，上傳後不會再出現這段文字。")
    run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
    run.bold = True
    doc.add_paragraph("{{學院}}{{系所}} 主管您好：")
    doc.add_paragraph("檢附本單位 {{期間}} 論文表現分析簡報，以及下列兩份確認表，敬請協助確認後回傳：")
    doc.add_paragraph("一、{{對標學校確認表}}：確認對標學校與系所是否合宜，以及 Scopus 機構英文名稱"
                      "（多個名稱請以分號「;」分隔）。")
    doc.add_paragraph("二、{{教師人數確認表}}：確認本單位與各對標系所的教師人數，有誤請填寫修正後人數與理由。")
    doc.add_paragraph("研究發展處 敬上")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
