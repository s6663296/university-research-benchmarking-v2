"""API 回應快取：同一查詢在有效期內不重新抓取（設計 §6.1）。"""

import hashlib
import json
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ApiCache as ApiCacheRow


def cache_key(path: str, params: dict) -> str:
    raw = path + json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ApiCache:
    def __init__(self, session_factory: sessionmaker[Session], cache_days: int = 1):
        self.session_factory = session_factory
        self.cache_days = cache_days

    def get(self, key: str) -> dict | None:
        with self.session_factory() as s:
            row = s.get(ApiCacheRow, key)
            if row is None:
                return None
            oldest_valid = date.today() - timedelta(days=self.cache_days)
            if row.fetched_at.date() <= oldest_valid:
                return None
            return json.loads(row.response_json)

    def put(self, key: str, endpoint: str, data: dict) -> None:
        text = json.dumps(data, ensure_ascii=False)
        with self.session_factory() as s:
            row = s.get(ApiCacheRow, key)
            if row is None:
                s.add(ApiCacheRow(key=key, endpoint=endpoint, fetched_at=datetime.now(), response_json=text))
            else:
                row.fetched_at = datetime.now()
                row.response_json = text
            s.commit()
