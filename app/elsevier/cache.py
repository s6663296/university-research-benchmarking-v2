"""API 回應快取：同一查詢在有效期內不重新抓取（設計 §6.1）。"""

import hashlib
import json
from datetime import date, datetime, timedelta

from sqlalchemy import select
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
        return self.get_many([key]).get(key)

    def get_many(self, keys: list[str]) -> dict[str, dict]:
        """一次查多筆，只回傳仍有效的。"""
        oldest_valid = date.today() - timedelta(days=self.cache_days)
        out: dict[str, dict] = {}
        with self.session_factory() as s:
            for i in range(0, len(keys), 500):  # SQLite 的 IN 參數有上限
                rows = s.scalars(select(ApiCacheRow).where(ApiCacheRow.key.in_(keys[i:i + 500])))
                out.update((r.key, json.loads(r.response_json)) for r in rows if r.fetched_at.date() > oldest_valid)
        return out

    def put(self, key: str, endpoint: str, data: dict) -> None:
        self.put_many(endpoint, {key: data})

    def put_many(self, endpoint: str, items: dict[str, dict]) -> None:
        now = datetime.now()
        with self.session_factory() as s:
            for key, data in items.items():
                s.merge(ApiCacheRow(key=key, endpoint=endpoint, fetched_at=now,
                                    response_json=json.dumps(data, ensure_ascii=False)))
            s.commit()
