"""儲存格文字正規化。"""

import re

_SPACES = "　\xa0"


def clean(value: object) -> str | None:
    """去除前後空白（含全形空白、不換行空白）；空白或 x／X 表示沒有值。"""
    if value is None:
        return None
    text = str(value).strip().strip(_SPACES).strip()
    if text == "" or text.lower() == "x":
        return None
    return text


def norm_name(value: str) -> str:
    """用於比對名稱：臺→台、移除所有空白。"""
    text = clean(value) or ""
    return re.sub(r"[\s　\xa0]+", "", text.replace("臺", "台"))
