"""API 額度：每組 Key 的 Scopus Search、Abstract Retrieval 與 SciVal 各自計算，從每次回應的 X-RateLimit-* 標頭取得並存檔。"""

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
# 多組 Key 的額度表格空間較小，用短名稱
SHORT_LABELS = {"scopus": "Scopus Search", "abstract": "Abstract Retrieval", "scival": "SciVal"}


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
    """每組 Key 的每個 API 只保留最新一筆，首頁與各頁的額度面板讀這裡，不必為了看額度再打 API。"""

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def record(self, api: str, quota: Quota, key_id: str = "") -> None:
        with self.session_factory() as s:
            s.merge(ApiQuota(api=api, key_id=key_id, limit=quota.limit, remaining=quota.remaining, reset_at=quota.reset_at,
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
    def expired(self) -> bool:
        return self.reset_at is not None and self.reset_at <= datetime.now()

    @property
    def low(self) -> bool:
        return bool(self.limit and self.remaining is not None and self.remaining < self.limit * 0.1)

    @property
    def percent(self) -> int:
        return round(100 * self.remaining / self.limit) if self.limit and self.remaining is not None else 0


def _rows_by_key(session: Session, key_ids: list[str]) -> dict[tuple[str, int], QuotaRow]:
    """（API, 第幾組 Key）→ 額度。升級前沒有 Key 識別碼的紀錄視為第 1 組。"""
    saved = {(r.api, r.key_id): r for r in session.scalars(select(ApiQuota))}
    out = {}
    for api in API_LABELS:
        for i, kid in enumerate(key_ids):
            r = saved.get((api, kid)) or (saved.get((api, "")) if i == 0 else None)
            if r is not None:
                out[api, i] = QuotaRow(api, API_LABELS[api], r.limit, r.remaining, r.reset_at, r.observed_at)
    return out


def quota_rows(session: Session, key_ids: list[str] | None = None) -> list[QuotaRow]:
    """第 1 組 Key 的各 API 額度（只有一組 Key 時首頁顯示這個）。"""
    rows = _rows_by_key(session, (key_ids or [""])[:1])
    return [rows[api, 0] for api in API_LABELS if (api, 0) in rows]


@dataclass(frozen=True)
class QuotaGridRow:
    api: str
    label: str
    cells: list[QuotaRow | None]   # 依 Key 順序，尚未有紀錄的為 None


def quota_grid(session: Session, key_ids: list[str]) -> list[QuotaGridRow]:
    """多組 Key：每支 API 一列、每組 Key 一欄。"""
    rows = _rows_by_key(session, key_ids)
    return [QuotaGridRow(api, SHORT_LABELS[api], [rows.get((api, i)) for i in range(len(key_ids))])
            for api in API_LABELS if any((api, i) in rows for i in range(len(key_ids)))]
