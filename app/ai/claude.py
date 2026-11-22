"""以官方 anthropic SDK 呼叫 Claude，用 structured outputs 取得摘要。"""

import json

import anthropic

from app.ai.service import AiError, SummaryDraft
from app.reports.emphasis import strip_bullet

_BULLETS = {"type": "array", "items": {"type": "string"}}
SCHEMA = {
    "type": "object",
    "properties": {
        "strengths": _BULLETS,
        "challenges": _BULLETS,
        "self_changes": {"anyOf": [_BULLETS, {"type": "null"}]},
        "rival_changes": {"anyOf": [_BULLETS, {"type": "null"}]},
    },
    "required": ["strengths", "challenges", "self_changes", "rival_changes"],
    "additionalProperties": False,
}
BULLET = "・"
EFFORT_CHOICES = {"low": "低", "medium": "中", "high": "高", "xhigh": "很高", "max": "最高"}


def _join(items) -> str:
    """要點陣列 → 每行一點、以「・」開頭的文字（模型自己加的項目符號先去掉，避免重複）。"""
    lines = [strip_bullet(str(i)) for i in items or []]
    return "\n".join(BULLET + line for line in lines if line)


def list_models(api_key: str, client=None) -> list[tuple[str, str]]:
    """這組 Key 目前可用的模型 (id, 顯示名稱)，新的在前。"""
    client = client or anthropic.Anthropic(api_key=api_key, max_retries=0, timeout=10)
    try:
        return [(m.id, m.display_name) for m in client.models.list(limit=1000, lifecycle=["active"])]
    except anthropic.AuthenticationError:
        raise AiError("Claude API Key 無效") from None
    except anthropic.APIConnectionError:
        raise AiError("無法連線到 Claude，請檢查網路") from None
    except anthropic.APIStatusError as e:
        raise AiError(f"Claude 服務錯誤（{e.status_code}）") from None


class ClaudeWriter:
    def __init__(self, api_key: str, model: str, client=None, effort: str = "medium"):
        self.api_key = api_key
        self.model = model
        self.effort = effort
        self._client = client

    def _get_client(self):
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def write(self, system: str, payload: dict) -> SummaryDraft:
        if not self.api_key:
            raise AiError("尚未設定 Claude API Key，請到「設定」頁填寫")
        user = ("請依下列 JSON 資料，以條列要點撰寫摘要（summary）與前後期比較（comparison，沒有前期資料時為 null）：\n"
                + json.dumps(payload, ensure_ascii=False, indent=1))
        try:
            response = self._get_client().beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
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
            return SummaryDraft(summary=_join(data["strengths"]), comparison=_join(data.get("self_changes")) or None,
                                challenges=_join(data.get("challenges")) or None,
                                rival_changes=_join(data.get("rival_changes")) or None)
        except (ValueError, KeyError, TypeError):
            raise AiError("Claude 回覆的格式無法解讀，請重試") from None
