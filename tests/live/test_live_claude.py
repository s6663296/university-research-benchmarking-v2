"""真實 Claude API 測試：設定 RUN_LIVE_API_TESTS=1 與 ANTHROPIC_API_KEY 才會執行。"""

import json
import os
import re

import pytest

from app.ai.claude import ClaudeWriter
from app.ai.prompts import DEFAULT_TONE, system_prompt
from app.config import Settings

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE_API_TESTS") != "1", reason="設定 RUN_LIVE_API_TESTS=1 才執行")

PAYLOAD = {
    "department": "資訊管理學系",
    "period": "2021-2025",
    "faculty_count": "20",
    "metrics": {
        "論文總篇數師均": {"value": "3.25", "rank": "2/5"},
        "Q1 師均": {"value": "1.80", "rank": "3/5"},
        "FWCI": {"value": "1.45", "rank": "1/5"},
    },
    "previous": None,
}


def test_claude_writer_live():
    settings = Settings()
    if not settings.anthropic_api_key:
        pytest.skip("沒有設定 ANTHROPIC_API_KEY")
    writer = ClaudeWriter(settings.anthropic_api_key, os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5"))
    draft = writer.write(system_prompt("dept", DEFAULT_TONE), PAYLOAD)

    assert draft.summary.strip()
    assert "嚴重落後" not in draft.summary and "表現不佳" not in draft.summary
    allowed = json.dumps(PAYLOAD, ensure_ascii=False)
    for number in re.findall(r"\d+\.\d{2}", draft.summary):
        assert number in allowed, f"出現 payload 以外的數字：{number}"
