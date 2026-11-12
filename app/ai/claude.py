"""以官方 anthropic SDK 呼叫 Claude，用 structured outputs 取得摘要。"""

import json

import anthropic

from app.ai.service import AiError, SummaryDraft

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "comparison": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["summary", "comparison"],
    "additionalProperties": False,
}


class ClaudeWriter:
    def __init__(self, api_key: str, model: str, client=None):
        self.api_key = api_key
        self.model = model
        self._client = client

    def _get_client(self):
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def write(self, system: str, payload: dict) -> SummaryDraft:
        if not self.api_key:
            raise AiError("尚未設定 Claude API Key，請到「設定」頁填寫")
        user = ("請依下列 JSON 資料撰寫摘要（summary）與前後期比較說明（comparison，沒有可比較時可為 null）：\n"
                + json.dumps(payload, ensure_ascii=False, indent=1))
        try:
            response = self._get_client().beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError:
            raise AiError("Claude API Key 無效") from None
        except anthropic.RateLimitError:
            raise AiError("Claude 額度或速率已達上限，請稍後再試") from None
        except anthropic.APIConnectionError:
            raise AiError("無法連線到 Claude，請檢查網路") from None
        except anthropic.APIStatusError as e:
            raise AiError(f"Claude 服務錯誤（{e.status_code}）") from None
        if response.stop_reason == "refusal":
            raise AiError("模型拒絕產生這段文字")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
            return SummaryDraft(summary=data["summary"], comparison=data.get("comparison"))
        except (ValueError, KeyError, TypeError):
            raise AiError("Claude 回覆的格式無法解讀，請重試") from None
