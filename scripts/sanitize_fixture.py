"""把錄下的 Elsevier 回應轉成測試 fixture：移除所有 link 欄位（內含 API Key）。

用法：python scripts/sanitize_fixture.py <輸入.json> <輸出.json>
"""

import json
import sys
from pathlib import Path

from app.elsevier.client import strip_links


def main(src: str, dst: str) -> None:
    data = strip_links(json.loads(Path(src).read_text(encoding="utf-8")))
    text = json.dumps(data, ensure_ascii=False, indent=1)
    if "apiKey" in text:
        sys.exit("輸出仍含 apiKey，已中止")
    Path(dst).write_text(text, encoding="utf-8")
    print(f"wrote {dst}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
