"""從範例報告整理出簡報範本：保留母片與版面配置，刪除所有投影片。

用法：python scripts/make_report_template.py
"""

from pathlib import Path

from pptx import Presentation

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/產出範例-管理學院為例/管理學院/2021-2025管理學院資管系論文表現分析_0511更新版.pptx"
TARGET = ROOT / "templates/report_template.pptx"


def main() -> None:
    prs = Presentation(SOURCE)
    slide_ids = prs.slides._sldIdLst
    for slide_id in list(slide_ids):
        prs.part.drop_rel(slide_id.rId)
        slide_ids.remove(slide_id)
    TARGET.parent.mkdir(exist_ok=True)
    prs.save(TARGET)
    print(f"wrote {TARGET}")


if __name__ == "__main__":
    main()
