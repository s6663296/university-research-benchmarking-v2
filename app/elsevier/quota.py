"""API 額度：Scopus Search、Abstract Retrieval 與 SciVal 各自計算，從每次回應的 X-RateLimit-* 標頭取得並存檔。"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ApiQuota

# 顯示順序與名稱
API_LABELS = {
    "scopus": "Scopus Search API",
    "abstract": "Scopus Abstract Retrieval API",
    "scival": "SciVal API",
}


@dataclass(frozen=True)
class Quota:
    limit: int | None
    remaining: int | None
    reset_at: datetime | None


def api_of(path: str) -> str:
    if path.startswith("/analytics/scival"):
        return "scival"
    if path.startswith("/content/search/scopus"):
        return "scopus"
    if path.startswith("/content/abstract"):
        return "abstract"
    return path


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def quota_from_headers(headers) -> Quota | None:
    limit, remaining = _int(headers.get("X-RateLimit-Limit")), _int(headers.get("X-RateLimit-Remaining"))
    if limit is None and remaining is None:
        return None
    reset = _int(headers.get("X-RateLimit-Reset"))
    return Quota(limit, remaining, datetime.fromtimestamp(reset) if reset else None)


class QuotaStore:
    """每個 API 只保留最新一筆，首頁與各頁的額度面板讀這裡，不必為了看額度再打 API。"""

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def record(self, api: str, quota: Quota) -> None:
        with self.session_factory() as s:
            s.merge(ApiQuota(api=api, limit=quota.limit, remaining=quota.remaining, reset_at=quota.reset_at,
                             observed_at=datetime.now()))
            s.commit()


@dataclass(frozen=True)
class QuotaRow:
    api: str
    label: str
    limit: int | None
    remaining: int | None
    reset_at: datetime | None
    observed_at: datetime

    @property
    def low(self) -> bool:
        return bool(self.limit and self.remaining is not None and self.remaining < self.limit * 0.1)

    @property
    def percent(self) -> int:
        return round(100 * self.remaining / self.limit) if self.limit and self.remaining is not None else 0


def quota_rows(session: Session) -> list[QuotaRow]:
    rows = {r.api: r for r in session.scalars(select(ApiQuota))}
    return [QuotaRow(api, API_LABELS[api], r.limit, r.remaining, r.reset_at, r.observed_at)
            for api in API_LABELS if (r := rows.get(api)) is not None]
