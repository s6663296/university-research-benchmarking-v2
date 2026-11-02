"""可在設定頁修改的執行參數，存於 app_settings。"""

import json
from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AppSetting

DEFAULT_SETTINGS: dict[str, Any] = {
    "rate_per_sec": 3.0,
    "max_retries": 3,
    "backoff_base_sec": 1.0,
    "timeout_sec": 60,
    "cache_days": 1,                      # 1 = 只用當天的快取
    "max_concurrency": 4,                 # 同時送出的請求數上限（全校分析時避免被 SciVal 限速）
    "rate_limit_retries": 8,              # 遇到 HTTP 429 時的重試次數（與一般錯誤的重試分開計算）
    "max_backoff_sec": 120,               # 限速時單次等待的上限秒數
    "anomaly_change_threshold": 0.30,
    "teacher_column": "total",            # 或 "asst_prof_plus"
    "self_citation_strategy": "scival_param",
    "rank_highlight_top": 2,              # 排名總表中名次 ≤ 2 標紅（範例第 4 頁）
}


def get_setting(session: Session, key: str) -> Any:
    row = session.get(AppSetting, key)
    if row is not None:
        return json.loads(row.value_json)
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
