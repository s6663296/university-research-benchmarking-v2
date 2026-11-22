"""Elsevier API client：限速、重試、快取，並移除含 API Key 的 link 欄位。"""

import asyncio
import hashlib
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

# 分析前的連線檢查。首頁手動更新額度時也會查詢同一篇論文的書目端點。
CHECK_PUBLICATION = "85121829056"
CHECKS = (
    ("Scopus", "/content/search/scopus", {"query": "AFFILCOUNTRY(Taiwan)", "count": 1, "field": "eid"}),
    ("SciVal", "/analytics/scival/publication/metrics",
     {"metricTypes": "Collaboration", "publicationIds": CHECK_PUBLICATION, "yearRange": "5yrs"}),
)
ABSTRACT_CHECK = ("Scopus Abstract Retrieval", f"/content/abstract/scopus_id/{CHECK_PUBLICATION}",
                  {"field": "identifier"})
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


def quota_exceeded(headers) -> bool:
    """額度用完的 429 不帶 X-RateLimit-* 標頭，只有 X-ELS-Status: QUOTA_EXCEEDED（2026-11-22 實測 Abstract Retrieval）。"""
    return headers.get("X-ELS-Status", "").upper().startswith("QUOTA_EXCEEDED")


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
# Scopus Search 一次約 0.5–1 秒，REF() 篩選與分頁同時大量送出時，4 個並行只跑得到每秒 4–8 次，
# 加倍後由每秒上限決定速度。每秒請求數仍受 API_MAX_RATE 限制
API_CONCURRENCY_FACTOR = {"abstract": 3, "scopus": 2}


def key_id(api_key: str) -> str:
    """額度紀錄用的 Key 識別碼：不把 Key 本身存進資料庫。"""
    return hashlib.sha256(api_key.encode()).hexdigest()[:12]


class ElsevierClient:
    def __init__(self, api_key: str | list[str], *, base_url: str = "https://api.elsevier.com",
                 rate_per_sec: float = 3.0, max_retries: int = 3, backoff_base: float = 1.0,
                 timeout: float = 60.0, cache: ApiCache | None = None,
                 on_quota: Callable[[str, Quota, str], None] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None, max_concurrency: int = 4,
                 rate_limit_retries: int = 8, max_backoff: float = 120.0,
                 api_max_rate: dict[str, float] | None = None, load_balance: bool = True):
        # 每秒上限與同時請求數以 Key 計算（Elsevier 對每組 Key 各自限速）。
        # load_balance：每個請求交給目前最空閒的 Key，有幾組 Key 就有幾倍的請求同時在途；
        # 關閉時單一 Key 優先：一律用第一組還有額度的 Key，用完才換下一組。
        # 某組 Key 的某支 API 額度用完時，那組 Key 不再分到該 API 的請求，直到重置
        self.api_keys = ([api_key] if isinstance(api_key, str) else list(api_key)) or [""]
        self.key_ids = [key_id(k) for k in self.api_keys]
        self.load_balance = load_balance
        self._http = httpx.AsyncClient(base_url=base_url, headers={"Accept": "application/json"}, timeout=timeout,
                                       transport=transport)
        # 速率與同時請求數以（API, Key）為單位：Scopus、Abstract Retrieval、SciVal 的限制各自獨立
        self.rate_per_sec = rate_per_sec
        self.api_max_rate = api_max_rate or {}
        self.max_concurrency = max(max_concurrency, 1)
        self._limiters: dict[tuple[str, int], AsyncRateLimiter] = {}
        self._slots: dict[tuple[str, int], asyncio.Semaphore] = {}
        self._in_flight: Counter[tuple[str, int]] = Counter()
        self._exhausted: dict[tuple[str, int], float] = {}   # 額度用完的（API, Key）→ 重置時間（epoch 秒）
        self._resets: dict[tuple[str, int], float] = {}      # 各（API, Key）最近一次回應標頭中的重置時間
        self.max_retries = max_retries
        self.rate_limit_retries = rate_limit_retries
        self.backoff_base = backoff_base
        self.max_backoff = max_backoff
        self.wait_for_quota_reset = False
        self.quota_resume_at: dict[str, float] = {}   # 所有 Key 都用完時，該 API 等到何時自動續查
        self.cache = cache
        self.on_quota = on_quota
        self.quota: dict[str, Quota] = {}  # "scopus" / "scival" → 最近一次回應的額度
        self.usage: Counter[str] = Counter()  # 各 API 實際成功的請求數（快取命中不算），即消耗的額度

    def limiter(self, api: str, key: int = 0) -> AsyncRateLimiter:
        if (api, key) not in self._limiters:
            rate = min(self.rate_per_sec, self.api_max_rate.get(api, self.rate_per_sec))
            self._limiters[api, key] = AsyncRateLimiter(rate)
        return self._limiters[api, key]

    def _slots_of(self, api: str, key: int = 0) -> asyncio.Semaphore:
        if (api, key) not in self._slots:   # 每組 Key 的每支 API 同時在途的請求數上限
            self._slots[api, key] = asyncio.Semaphore(self.max_concurrency * API_CONCURRENCY_FACTOR.get(api, 1))
        return self._slots[api, key]

    def _backoff(self, attempt: int) -> float:
        """指數退避加上隨機抖動，避免大家同時重試。"""
        return min(self.backoff_base * 2 ** attempt, self.max_backoff) * (1 + random.uniform(0, 0.25))

    def seed_quota(self, api: str, kid: str, quota: Quota) -> None:
        """啟動時帶入上次記下的額度：已經用完的 Key 不必再被拒絕一次才知道。"""
        if kid not in self.key_ids or quota.reset_at is None:
            return
        index, reset = self.key_ids.index(kid), quota.reset_at.timestamp()
        if reset > time.time():
            self._resets[api, index] = reset
            if quota.remaining == 0:
                self._exhausted[api, index] = reset

    def _mark_exhausted(self, api: str, index: int, reset: float | None) -> float:
        """這組 Key 的額度用完。重置時間未知時先停用一小時，之後再試一次。"""
        if reset is None:
            known = self._resets.get((api, index))
            reset = known if known and known > time.time() else time.time() + 3600
        self._exhausted[api, index] = reset
        return reset

    def _pick_key(self, api: str) -> int | None:
        """選預計最快送出的 Key（排隊數 × 間隔 + 被限速的暫停）；單一 Key 優先時選第一組可用的。

        全部用完時回傳 None。
        """
        now, mono = time.time(), time.monotonic()
        best, best_score = None, None
        for i in range(len(self.api_keys)):
            until = self._exhausted.get((api, i))
            if until is not None:
                if until > now:
                    continue
                del self._exhausted[api, i]
            if not self.load_balance:
                return i
            limiter = self.limiter(api, i)
            # 同樣空閒時選較久沒用的 Key（_next 較早），依序送出時會輪流
            score = (max(limiter._next - mono, 0.0) + self._in_flight[api, i] * limiter.interval,
                     self._in_flight[api, i], limiter._next)
            if best_score is None or score < best_score:
                best, best_score = i, score
        return best

    def _exhausted_error(self, path: str, reset: float) -> ElsevierError:
        when = datetime.fromtimestamp(reset).strftime("%Y-%m-%d %H:%M")
        scope = "所有 API Key " if len(self.api_keys) > 1 else ""
        return ElsevierError(429, False, f"{api_label(path)} {scope}的額度已用完，{when} 才會重置；"
                                         "已抓到的資料會保留，屆時可按「繼續」接著抓")

    async def _request(self, path: str, params: dict, retries: int, *, key: int | None = None) -> dict:
        """key：指定用哪一組 Key（檢查連線時逐組查額度）；None 時交給最空閒的 Key。"""
        attempt = throttled = 0
        rate_limit_retries = self.rate_limit_retries if retries else 0   # 檢查連線時不重試
        api = api_of(path)
        while True:
            index = key if key is not None else self._pick_key(api)
            if index is None:   # 所有 Key 的這支 API 額度都用完
                reset = min(self._exhausted[api, i] for i in range(len(self.api_keys)))
                if not self.wait_for_quota_reset:
                    raise self._exhausted_error(path, reset)
                # 保持背景工作可暫停；重置後仍經過原有的速率與並行限制。
                self.quota_resume_at[api] = reset
                delay = reset + 1 - time.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                if self.quota_resume_at.get(api) == reset:
                    del self.quota_resume_at[api]
                for i in range(len(self.api_keys)):   # 已到重置時間的 Key 重新加入分配
                    if self._exhausted.get((api, i), reset) <= reset:
                        self._exhausted.pop((api, i), None)
                continue
            limiter = self.limiter(api, index)
            self._in_flight[api, index] += 1
            try:
                await limiter.acquire()
                async with self._slots_of(api, index):
                    resp = await self._http.get(path, params=params,
                                                headers={"X-ELS-APIKey": self.api_keys[index]})
            except (httpx.TimeoutException, httpx.TransportError) as e:
                error = ElsevierError(None, True, f"{OFFLINE_MESSAGE}（{type(e).__name__}）")
            else:
                quota = quota_from_headers(resp.headers)
                if quota is not None:
                    self.quota[api] = quota
                    if self.on_quota is not None:
                        self.on_quota(api, quota, self.key_ids[index])
                    if quota.reset_at is not None:
                        self._resets[api, index] = quota.reset_at.timestamp()
                        if quota.remaining == 0 and resp.status_code == 200:   # 這次是最後一次，之後改用其他 Key
                            self._mark_exhausted(api, index, quota.reset_at.timestamp())
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
                    if quota_exceeded(resp.headers) or (reset is not None and reset - time.time() > self.max_backoff):
                        # 這組 Key 的額度用完：之後的請求改由其他 Key 處理，不當成限速重試
                        reset = self._mark_exhausted(api, index, reset)
                        if key is not None:
                            raise self._exhausted_error(path, reset)
                        continue
                    if throttled >= rate_limit_retries:
                        detail = f"，已暫停等待 {throttled} 次仍被限速" if throttled else ""
                        raise ElsevierError(429, True, _http_message(429) + detail +
                                            "；稍後可按「繼續」接著抓，或在設定中降低每秒請求數、同時請求數")
                    wait = retry_after_seconds(resp.headers)
                    if wait is None and reset is not None:
                        wait = max(reset - time.time(), 0.0)
                    wait = min(wait if wait is not None else self._backoff(throttled), self.max_backoff)
                    limiter.penalize(wait)   # 這組 Key 的請求一起暫停並放慢，其他 Key 照常
                    throttled += 1
                    continue
                retryable = resp.status_code >= 500
                error = ElsevierError(resp.status_code, retryable, _http_message(resp.status_code))
            finally:
                self._in_flight[api, index] -= 1
            if not error.retryable or attempt >= retries:
                raise error
            await asyncio.sleep(self._backoff(attempt))
            attempt += 1

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

    async def check_connection(self, *, include_abstract: bool = False) -> ConnectionStatus:
        """首頁手動查詢三種獨立額度，分析前只測必用的搜尋和指標 API。有多組 Key 時逐組查詢。"""
        checks = (*CHECKS, ABSTRACT_CHECK) if include_abstract else CHECKS
        errors = []
        for index in range(len(self.api_keys)):
            prefix = f"第 {index + 1} 組 Key " if len(self.api_keys) > 1 else ""
            for name, path, params in checks:
                try:
                    await self._request(path, params, 0, key=index)
                except ElsevierError as e:
                    if e.status is None:   # 連不上 Elsevier，換 Key 也一樣
                        errors.append(f"{prefix}{name}：{OFFLINE_MESSAGE}")
                        return ConnectionStatus(False, "、".join(errors))
                    errors.append(f"{prefix}{name}：{e.message}")
                    if not include_abstract:
                        break
        if errors:
            return ConnectionStatus(False, "、".join(errors))
        return ConnectionStatus(True, "連線正常")

    async def aclose(self) -> None:
        await self._http.aclose()


def make_client(settings, session) -> ElsevierClient:
    """依 .env 與設定頁的參數建立 client。"""
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    from app.db.models import ApiQuota
    from app.db.settings_store import get_setting

    # 快取用自己的 session，背景任務中也能安全寫入
    cache = ApiCache(sessionmaker(session.get_bind(), expire_on_commit=False), get_setting(session, "cache_days"))
    client = ElsevierClient(
        settings.elsevier_api_keys,
        load_balance=bool(get_setting(session, "load_balance")),
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
    for row in session.scalars(select(ApiQuota)):
        client.seed_quota(row.api, row.key_id, Quota(row.limit, row.remaining, row.reset_at))
    return client
