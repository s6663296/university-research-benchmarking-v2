"""AI 文字的紅字標記：模型用 **兩個星號** 包住重點，網頁與簡報都標成紅字。"""

import re

_MARK = re.compile(r"\*\*(.+?)\*\*")


def emphasis(text: str) -> list[tuple[str, bool]]:
    """把一行文字拆成 (片段, 是否紅字)；沒有成對的 ** 就整行一般字。"""
    parts, pos = [], 0
    for m in _MARK.finditer(text):
        if m.start() > pos:
            parts.append((text[pos:m.start()], False))
        parts.append((m.group(1), True))
        pos = m.end()
    if pos < len(text):
        parts.append((text[pos:], False))
    return parts or [("", False)]


def plain(text: str) -> str:
    """去掉紅字標記，只留文字（排版估算字數用）。"""
    return _MARK.sub(r"\1", text)


def strip_bullet(text: str) -> str:
    """去掉開頭的項目符號；開頭的 **（紅字標記）要保留。"""
    text = text.strip()
    while text[:1] in ("・", "-", "•", "□") or (text[:1] == "*" and text[:2] != "**"):
        text = text[1:].strip()
    return text
