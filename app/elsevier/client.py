"""Elsevier API client：限速、重試、快取，並移除含 API Key 的 link 欄位。"""

import asyncio
import random
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.elsevier.cache import ApiCache, cache_key
from app.elsevier.quota import API_LABELS, Quota, QuotaStore, api_of, quota_from_headers

# 檢查連線時兩支 API 各打一次；SciVal 需要一篇實際存在的論文
CHECKS = (
    ("Scopus", "/content/search/scopus", {"query": "AFFILCOUNTRY(Taiwan)", "count": 1, "field": "eid"}),
    ("SciVal", "/analytics/scival/publication/metrics",
     {"metricTypes": "Collaboration", "publicationIds": "85121829056", "yearRange": "5yrs"}),
)
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


class AsyncRateLimiter:
    """兩次放行之間至少間隔 interval 秒；被限速時全部暫停並放慢，之後逐步恢復。

    全校分析時有大量請求排隊，若每個請求各自重試，限速解除的瞬間又會一起湧入而再次被擋。
    因此遇到 HTTP 429 時由這裡統一「全部暫停 + 間隔加倍」，連續成功一段時間後再減半，直到回到設定值。
    """

    MAX_INTERVAL = 10.0   # 最慢每 10 秒一個請求
    RECOVER_AFTER = 20    # 連續成功幾次後加速一級

    def __init__(self, rate_per_sec: float):
        self.base_interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self.interval = self.base_interval
        self._lock = asyncio.Lock()
        self._next = 0.0
        self._streak = 0
        self.throttled = 0   # 被限速的次數

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next = now + self.interval

    def penalize(self, pause: float) -> None:
        """被限速：所有請求暫停 pause 秒，之後的間隔加倍。"""
        self.throttled += 1
        self._streak = 0
        self.interval = min(max(self.interval * 2, self.base_interval, 0.5), self.MAX_INTERVAL)
        self._next = max(self._next, time.monotonic() + pause)

    def reward(self) -> None:
        self._streak += 1
        if self._streak >= self.RECOVER_AFTER and self.interval > self.base_interval:
            self.interval = max(self.base_interval, self.interval / 2)
            self._streak = 0


def retry_after_seconds(headers) -> float | None:
    """Retry-After 可能是秒數或 HTTP 日期。"""
    value = headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max((when - datetime.now(when.tzinfo)).total_seconds(), 0.0)


def quota_reset(headers) -> float | None:
    """額度用完（X-RateLimit-Remaining 為 0）時，回傳重置時間（epoch 秒）。"""
    if headers.get("X-RateLimit-Remaining") != "0":
        return None
    try:
        reset = float(headers.get("X-RateLimit-Reset", ""))
    except ValueError:
        return None
    return reset / 1000 if reset > 1e11 else reset   # 有些端點以毫秒表示


def api_label(path: str) -> str:
    return API_LABELS.get(api_of(path), "Elsevier API")


def _http_message(status: int) -> str:
    if status in (401, 403):
        return f"API Key 無效或沒有權限（HTTP {status}），請確認 API Key 與是否在校內網路或學校 VPN"
    if status == 429:
        return "API 請求次數超過限制（HTTP 429）"
    return f"Elsevier API 回應錯誤（HTTP {status}）"


# 官方文件（dev.elsevier.com/api_key_settings.html）各 API 的每秒上限：Scopus Search 9、Abstract Retrieval 9、
# SciVal 6，各自計算。保留一點餘裕；每支 API 各自排隊，彼此不必等待
API_MAX_RATE = {"scopus": 8.0, "abstract": 8.0, "scival": 5.0}
# 同時請求數的倍數：Abstract Retrieval 遇到大型合作論文（作者上千人）時一篇回應 5–6 MB、要 10 秒以上，
# 同時只送 4 個會讓每秒完成不到 1 篇；2026-11-13 實測 8 個並行下載仍全部成功、整體快 3 倍以上。
# 每秒請求數仍受 API_MAX_RATE 限制
API_CONCURRENCY_FACTOR = {"abstract": 3}


class ElsevierClient:
    def __init__(self, api_key: str | list[str], insttoken: str = "", *, base_url: str = "https://api.elsevier.com",
                 rate_per_sec: float = 3.0, max_retries: int = 3, backoff_base: float = 1.0,
                 timeout: float = 60.0, cache: ApiCache | None = None,
                 on_quota: Callable[[str, Quota], None] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None, max_concurrency: int = 4,
                 rate_limit_retries: int = 8, max_backoff: float = 120.0,
                 api_max_rate: dict[str, float] | None = None):
        # 可傳入多組 Key：某支 API 的額度用完時換下一組（Scopus、SciVal 額度各自獨立，分開切換）
        self.api_keys = ([api_key] if isinstance(api_key, str) else list(api_key)) or [""]
        self._key_index: dict[str, int] = {}
        headers = {"Accept": "application/json"}
        if insttoken:
            headers["X-ELS-Insttoken"] = insttoken
        self._http = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout, transport=transport)
        # 速率與同時請求數以 API 為單位（api_of）：Scopus、Abstract Retrieval、SciVal 的限制各自獨立
        self.rate_per_sec = rate_per_sec
        self.api_max_rate = api_max_rate or {}
        self.max_concurrency = max(max_concurrency, 1)
        self._limiters: dict[str, AsyncRateLimiter] = {}
        self._slots: dict[str, asyncio.Semaphore] = {}
        self.max_retries = max_retries
        self.rate_limit_retries = rate_limit_retries
        self.backoff_base = backoff_base
        self.max_backoff = max_backoff
        self.cache = cache
        self.on_quota = on_quota
        self.quota: dict[str, Quota] = {}  # "scopus" / "scival" → 最近一次回應的額度
        self.usage: Counter[str] = Counter()  # 各 API 實際成功的請求數（快取命中不算），即消耗的額度

    def limiter(self, api: str) -> AsyncRateLimiter:
        if api not in self._limiters:
            rate = min(self.rate_per_sec, self.api_max_rate.get(api, self.rate_per_sec))
            self._limiters[api] = AsyncRateLimiter(rate)
        return self._limiters[api]

    def _slots_of(self, api: str) -> asyncio.Semaphore:
        if api not in self._slots:   # 每支 API 同時在途的請求數上限
            self._slots[api] = asyncio.Semaphore(self.max_concurrency * API_CONCURRENCY_FACTOR.get(api, 1))
        return self._slots[api]

    def _backoff(self, attempt: int) -> float:
        """指數退避加上隨機抖動，避免大家同時重試。"""
        return min(self.backoff_base * 2 ** attempt, self.max_backoff) * (1 + random.uniform(0, 0.25))

    async def _request(self, path: str, params: dict, retries: int) -> dict:
        attempt = throttled = 0
        rate_limit_retries = self.rate_limit_retries if retries else 0   # 檢查連線時不重試
        api = api_of(path)
        limiter = self.limiter(api)
        while True:
            await limiter.acquire()
            try:
                index = self._key_index.get(api, 0)
                async with self._slots_of(api):
                    resp = await self._http.get(path, params=params,
                                                headers={"X-ELS-APIKey": self.api_keys[index]})
            except (httpx.TimeoutException, httpx.TransportError) as e:
                error = ElsevierError(None, True, f"{OFFLINE_MESSAGE}（{type(e).__name__}）")
            else:
                quota = quota_from_headers(resp.headers)
                if quota is not None:
                    self.quota[api_of(path)] = quota
                    if self.on_quota is not None:
                        self.on_quota(api_of(path), quota)
                if resp.status_code == 200:
                    limiter.reward()
                    self.usage[api] += 1
                    try:
                        return strip_links(resp.json())
                    except ValueError:
                        raise ElsevierError(200, False, "Elsevier 回應不是 JSON（可能是登入頁或代理伺服器），"
                                                        "請確認是否在校內網路或學校 VPN") from None
                if resp.status_code == 429:
                    reset = quota_reset(resp.headers)
                    if reset is not None and reset - time.time() > self.max_backoff:
                        if self._next_key(api_of(path), index):
                            continue
                        when = datetime.fromtimestamp(reset).strftime("%Y-%m-%d %H:%M")
                        raise ElsevierError(429, False, f"{api_label(path)} 的額度已用完，{when} 才會重置；"
                                                        "已抓到的資料會保留，屆時可按「繼續」接著抓")
                    if throttled >= rate_limit_retries:
                        detail = f"，已暫停等待 {throttled} 次仍被限速" if throttled else ""
                        raise ElsevierError(429, True, _http_message(429) + detail +
                                            "；稍後可按「繼續」接著抓，或在設定中降低每秒請求數、同時請求數")
                    wait = retry_after_seconds(resp.headers)
                    if wait is None and reset is not None:
                        wait = max(reset - time.time(), 0.0)
                    wait = min(wait if wait is not None else self._backoff(throttled), self.max_backoff)
                    limiter.penalize(wait)   # 同一支 API 的請求一起暫停並放慢，而不是各自重試
                    throttled += 1
                    continue
                retryable = resp.status_code >= 500
                error = ElsevierError(resp.status_code, retryable, _http_message(resp.status_code))
            if not error.retryable or attempt >= retries:
                raise error
            await asyncio.sleep(self._backoff(attempt))
            attempt += 1

    def _next_key(self, api: str, failed: int) -> bool:
        """第 failed 組 Key 的額度用完：換下一組，沒有下一組就回傳 False。

        同時有多個請求在途時，可能已被其他請求換過，此時直接用目前這組重試。
        """
        current = self._key_index.get(api, 0)
        if current != failed:
            return True
        if current + 1 >= len(self.api_keys):
            return False
        self._key_index[api] = current + 1
        return True

    async def get_json(self, path: str, params: dict, *, use_cache: bool = True, store: bool = True) -> dict:
        """store=False：呼叫端自行快取（例如 SciVal 逐篇快取），不存整個回應。"""
        key = cache_key(path, params)
        if self.cache is not None and use_cache:
            cached = self.cache.get(key)
            if cached is not None:
                return cached
        data = await self._request(path, params, self.max_retries)
        if self.cache is not None and store:
            self.cache.put(key, path, data)
        return data

    async def check_connection(self) -> ConnectionStatus:
        """Scopus 與 SciVal 的額度與權限各自獨立，兩支都檢查（各用 1 次額度）。"""
        for name, path, params in CHECKS:
            try:
                await self._request(path, params, 0)
            except ElsevierError as e:
                message = e.message if e.status is not None else OFFLINE_MESSAGE
                return ConnectionStatus(False, f"{name}：{message}")
        return ConnectionStatus(True, "連線正常")

    async def aclose(self) -> None:
        await self._http.aclose()


def make_client(settings, session) -> ElsevierClient:
    """依 .env 與設定頁的參數建立 client。"""
    from sqlalchemy.orm import sessionmaker

    from app.db.settings_store import get_setting

    # 快取用自己的 session，背景任務中也能安全寫入
    cache = ApiCache(sessionmaker(session.get_bind(), expire_on_commit=False), get_setting(session, "cache_days"))
    return ElsevierClient(
        settings.elsevier_api_keys, settings.elsevier_insttoken,
        rate_per_sec=float(get_setting(session, "rate_per_sec")),
        max_retries=int(get_setting(session, "max_retries")),
        backoff_base=float(get_setting(session, "backoff_base_sec")),
        timeout=float(get_setting(session, "timeout_sec")),
        cache=cache,
        on_quota=QuotaStore(cache.session_factory).record,
        max_concurrency=int(get_setting(session, "max_concurrency")),
        rate_limit_retries=int(get_setting(session, "rate_limit_retries")),
        api_max_rate=API_MAX_RATE,
        max_backoff=float(get_setting(session, "max_backoff_sec")),
    )
