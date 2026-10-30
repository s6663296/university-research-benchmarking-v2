"""Elsevier API client：限速、重試、快取，並移除含 API Key 的 link 欄位。"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.elsevier.cache import ApiCache, cache_key

RATE_HEADERS = ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset")
OFFLINE_MESSAGE = "無法連線到 api.elsevier.com，請確認是否在校內網路或已連上學校 VPN"


def strip_links(obj: Any) -> Any:
    """遞迴移除所有名為 link 的鍵（SciVal 的 publication.link.@href 內含 apiKey）。"""
    if isinstance(obj, dict):
        return {k: strip_links(v) for k, v in obj.items() if k != "link"}
    if isinstance(obj, list):
        return [strip_links(v) for v in obj]
    return obj


class ElsevierError(Exception):
    def __init__(self, status: int | None, retryable: bool, message: str):
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.message = message


@dataclass
class ConnectionStatus:
    ok: bool
    message: str
    remaining: str | None = None


class AsyncRateLimiter:
    """兩次放行之間至少間隔 1/rate 秒。"""

    def __init__(self, rate_per_sec: float):
        self.interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next = now + self.interval


def _http_message(status: int) -> str:
    if status in (401, 403):
        return f"API Key 無效或沒有權限（HTTP {status}），請確認 API Key 與是否在校內網路或學校 VPN"
    if status == 429:
        return "API 請求次數超過限制（HTTP 429）"
    return f"Elsevier API 回應錯誤（HTTP {status}）"


class ElsevierClient:
    def __init__(self, api_key: str, insttoken: str = "", *, base_url: str = "https://api.elsevier.com",
                 rate_per_sec: float = 3.0, max_retries: int = 3, backoff_base: float = 1.0,
                 timeout: float = 60.0, cache: ApiCache | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
        if insttoken:
            headers["X-ELS-Insttoken"] = insttoken
        self._http = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout, transport=transport)
        self._limiter = AsyncRateLimiter(rate_per_sec)
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.cache = cache
        self.quota: dict[str, str] = {}

    async def _request(self, path: str, params: dict, retries: int) -> dict:
        attempt = 0
        while True:
            await self._limiter.acquire()
            try:
                resp = await self._http.get(path, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                error = ElsevierError(None, True, f"{OFFLINE_MESSAGE}（{type(e).__name__}）")
            else:
                for h in RATE_HEADERS:
                    if h in resp.headers:
                        self.quota[h] = resp.headers[h]
                if resp.status_code == 200:
                    return strip_links(resp.json())
                retryable = resp.status_code == 429 or resp.status_code >= 500
                error = ElsevierError(resp.status_code, retryable, _http_message(resp.status_code))
            if not error.retryable or attempt >= retries:
                raise error
            await asyncio.sleep(self.backoff_base * 2 ** attempt)
            attempt += 1

    async def get_json(self, path: str, params: dict, *, use_cache: bool = True) -> dict:
        key = cache_key(path, params)
        if self.cache is not None and use_cache:
            cached = self.cache.get(key)
            if cached is not None:
                return cached
        data = await self._request(path, params, self.max_retries)
        if self.cache is not None:
            self.cache.put(key, path, data)
        return data

    async def check_connection(self) -> ConnectionStatus:
        try:
            await self._request("/content/search/scopus", {"query": "AFFILCOUNTRY(Taiwan)", "count": 1}, 0)
        except ElsevierError as e:
            message = e.message if e.status is not None else OFFLINE_MESSAGE
            return ConnectionStatus(False, message, self.quota.get("X-RateLimit-Remaining"))
        return ConnectionStatus(True, "連線正常", self.quota.get("X-RateLimit-Remaining"))

    async def aclose(self) -> None:
        await self._http.aclose()


def make_client(settings, session) -> ElsevierClient:
    """依 .env 與設定頁的參數建立 client。"""
    from sqlalchemy.orm import sessionmaker

    from app.db.settings_store import get_setting

    # 快取用自己的 session，背景任務中也能安全寫入
    cache = ApiCache(sessionmaker(session.get_bind(), expire_on_commit=False), get_setting(session, "cache_days"))
    return ElsevierClient(
        settings.elsevier_api_key, settings.elsevier_insttoken,
        rate_per_sec=float(get_setting(session, "rate_per_sec")),
        max_retries=int(get_setting(session, "max_retries")),
        backoff_base=float(get_setting(session, "backoff_base_sec")),
        timeout=float(get_setting(session, "timeout_sec")),
        cache=cache,
    )
