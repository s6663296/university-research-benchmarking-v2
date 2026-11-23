"""可在設定頁修改的執行參數，存於 app_settings。"""

import json
from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from app.ai.prompts import DEFAULT_TONE, LEGACY_TONES
from app.db.models import AppSetting

DEFAULT_SETTINGS: dict[str, Any] = {
    "rate_per_sec": 8.0,                  # 每組 Key 的每支 API 各自計算，並以官方上限為準（client.API_MAX_RATE）
    "max_retries": 3,
    "backoff_base_sec": 1.0,
    "timeout_sec": 60,
    "cache_days": 1,                      # 1 = 只用當天的快取
    "load_balance": True,                 # 多組 Key 時平均分配並行送出；False 為單一 Key 優先（用完才換下一組）
    "max_concurrency": 4,                 # 每組 Key 的每支 API 同時送出的請求數上限（全校分析時避免被 SciVal 限速）
    "rate_limit_retries": 8,              # 遇到 HTTP 429 時的重試次數（與一般錯誤的重試分開計算）
    "max_backoff_sec": 120,               # 限速時單次等待的上限秒數
    "anomaly_change_threshold": 0.30,
    "teacher_column": "total",            # 或 "asst_prof_plus"
    "self_citation_strategy": "scival_pubset_ref",
    "rank_highlight_top": 2,              # 排名總表中名次 ≤ 2 標紅（範例第 4 頁）
    "ai_model": "claude-sonnet-5-5",        # AI 摘要使用的 Claude 模型
    "ai_effort": "medium",                # 思考程度（output_config.effort）
    "ai_tone_prompt": DEFAULT_TONE,       # AI 摘要的語氣提示
    "report_text": {},                    # 簡報目錄名稱與備註的修改（只存與預設不同的欄位）
    "recompute_results": False,           # 計算公式改變後由 migration 設為 True，啟動時重算所有舊分析
    "report_trends": False,               # 系所簡報加入「逐年趨勢」章（主管偏好表格，預設不加）
}


def get_setting(session: Session, key: str) -> Any:
    row = session.get(AppSetting, key)
    if row is not None:
        value = json.loads(row.value_json)
        if key == "ai_tone_prompt" and value in LEGACY_TONES:  # 存的是舊版預設，視同沒改過
            return DEFAULT_TONE
        return value
    return deepcopy(DEFAULT_SETTINGS[key])


def set_setting(session: Session, key: str, value: Any) -> None:
    if key not in DEFAULT_SETTINGS:
        raise KeyError(key)
    row = session.get(AppSetting, key)
    text = json.dumps(value, ensure_ascii=False)
    if row is None:
        session.add(AppSetting(key=key, value_json=text))
    else:
        row.value_json = text
    session.commit()
