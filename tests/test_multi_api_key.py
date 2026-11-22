"""多組 API Key：一次輸入多組、額度用完自動換下一組、設定頁顯示組數與遮罩後的 Key。"""

import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, parse_api_keys
from app.elsevier.client import ElsevierClient, ElsevierError
from app.main import create_app
from app.web.routes.settings import mask_api_key

SCOPUS = r".*/content/search/scopus.*"
SCIVAL = r".*/analytics/scival/publication/metrics.*"
SCOPUS_PATH = "/content/search/scopus"
SCIVAL_PATH = "/analytics/scival/publication/metrics"


def exhausted():
    reset = str(int(time.time()) + 7 * 86400)
    return httpx.Response(429, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset,
                                        "X-RateLimit-Limit": "5000"})


def make(keys):
    return ElsevierClient(keys, backoff_base=0, rate_per_sec=1000)


def test_parse_api_keys_splits_on_commas_and_newlines_and_dedupes():
    assert parse_api_keys(" a1, b2\r\nc3\n\na1 ") == ["a1", "b2", "c3"]
    assert parse_api_keys("") == []


def test_mask_api_key_keeps_head_and_tail():
    # 中間固定 8 個 *，不論 Key 多長，清單才會對齊
    assert mask_api_key("abcd1234567890wxyz") == "abcd********wxyz"
    assert mask_api_key("abcd" + "x" * 60 + "wxyz") == "abcd********wxyz"
    assert mask_api_key("short") == "********"   # 太短的全部遮住，也看不出長度
    assert mask_api_key("") == ""


async def test_switches_to_next_key_when_quota_exhausted(respx_mock):
    route = respx_mock.get(url__regex=SCOPUS).mock(side_effect=[exhausted(), httpx.Response(200, json={"ok": 1})])
    assert await make(["key1", "key2"]).get_json(SCOPUS_PATH, {}, use_cache=False) == {"ok": 1}
    used = [call.request.headers["X-ELS-APIKey"] for call in route.calls]
    assert used == ["key1", "key2"]


async def test_raises_when_all_keys_exhausted(respx_mock):
    route = respx_mock.get(url__regex=SCIVAL).mock(return_value=exhausted())
    with pytest.raises(ElsevierError) as e:
        await make(["key1", "key2"]).get_json(SCIVAL_PATH, {}, use_cache=False)
    assert route.call_count == 2 and "額度已用完" in e.value.message and not e.value.retryable


async def test_keys_switch_per_api(respx_mock):
    respx_mock.get(url__regex=SCOPUS).mock(side_effect=[exhausted(), httpx.Response(200, json={})])
    scival = respx_mock.get(url__regex=SCIVAL).mock(return_value=httpx.Response(200, json={}))
    c = make(["key1", "key2"])
    await c.get_json(SCOPUS_PATH, {}, use_cache=False)
    await c.get_json(SCIVAL_PATH, {}, use_cache=False)
    assert scival.calls[0].request.headers["X-ELS-APIKey"] == "key1"  # SciVal 額度獨立，不跟著換


@pytest.fixture
def multi_client(tmp_path):
    s = Settings(_env_file=None, elsevier_api_key="aaaa1111111111zzzz,bbbb2222222222yyyy",
                 data_dir=tmp_path, env_path=tmp_path / ".env")
    with TestClient(create_app(s)) as c:
        yield c


def test_settings_page_shows_key_count_and_masked_keys(multi_client):
    text = multi_client.get("/settings").text
    assert text.count('class=\"api-key-no\"') == 2
    assert "aaaa" + "*" * 8 + "zzzz" in text and "bbbb" + "*" * 8 + "yyyy" in text
    assert "1111111111" not in text and "2222222222" not in text


def env_text(tmp_path):
    return (tmp_path / ".env").read_text(encoding="utf-8")


def test_add_key_appends_to_existing(multi_client, tmp_path):
    multi_client.post("/settings/api-keys/add", data={"api_key": " cccc3333333333xxxx "})
    assert "ELSEVIER_API_KEY=aaaa1111111111zzzz,bbbb2222222222yyyy,cccc3333333333xxxx" in env_text(tmp_path)
    assert multi_client.get("/settings").text.count('class=\"api-key-no\"') == 3


def test_add_accepts_several_pasted_keys_and_skips_duplicates(multi_client, tmp_path):
    multi_client.post("/settings/api-keys/add", data={"api_key": "aaaa1111111111zzzz, dddd4444444444wwww"})
    assert "ELSEVIER_API_KEY=aaaa1111111111zzzz,bbbb2222222222yyyy,dddd4444444444wwww" in env_text(tmp_path)


def test_add_blank_changes_nothing(multi_client):
    multi_client.post("/settings/api-keys/add", data={"api_key": "  "})
    assert multi_client.get("/settings").text.count('class=\"api-key-no\"') == 2


def test_delete_key_by_position(multi_client, tmp_path):
    multi_client.post("/settings/api-keys/delete", data={"index": "0"})
    assert "ELSEVIER_API_KEY=bbbb2222222222yyyy\n" in env_text(tmp_path)
    text = multi_client.get("/settings").text
    assert text.count('class=\"api-key-no\"') == 1 and "aaaa" not in text


def test_delete_out_of_range_changes_nothing(multi_client):
    assert multi_client.post("/settings/api-keys/delete", data={"index": "9"}, follow_redirects=False).status_code == 303
    assert multi_client.get("/settings").text.count('class=\"api-key-no\"') == 2


def test_each_key_row_has_delete_button(multi_client):
    text = multi_client.get("/settings").text
    assert text.count('action="/settings/api-keys/delete"') == 2


def test_settings_page_has_no_insttoken(multi_client):
    assert "insttoken" not in multi_client.get("/settings").text.lower()


async def test_empty_key_list_still_sends_request(respx_mock):
    # 未設定 Key 時行為與原本相同：送出請求，交給 API 回應錯誤
    route = respx_mock.get(url__regex=SCOPUS).mock(return_value=httpx.Response(200, json={}))
    await make([]).get_json(SCOPUS_PATH, {}, use_cache=False)
    assert route.call_count == 1


def test_adding_existing_key_warns_with_its_position(multi_client, tmp_path):
    before = env_text(tmp_path) if (tmp_path / ".env").exists() else None
    r = multi_client.post("/settings/api-keys/add", data={"api_key": "bbbb2222222222yyyy"})
    assert r.url.path == "/settings"  # 送出後回到設定頁
    assert "這組 API Key 已經在清單中（第 2 組），沒有重複新增" in r.text
    assert r.text.count('class=\"api-key-no\"') == 2
    after = env_text(tmp_path) if (tmp_path / ".env").exists() else None
    assert after == before  # 沒有改寫 .env


def test_adding_new_key_shows_confirmation(multi_client):
    r = multi_client.post("/settings/api-keys/add", data={"api_key": "cccc3333333333xxxx"})
    assert r.url.path == "/settings" and "已新增 1 組 API Key" in r.text


def test_pasting_mix_reports_added_and_duplicates(multi_client):
    r = multi_client.post("/settings/api-keys/add", data={"api_key": "aaaa1111111111zzzz\ncccc3333333333xxxx"})
    assert "已新增 1 組 API Key" in r.text and "第 1 組" in r.text and "沒有重複新增" in r.text


async def test_requests_are_spread_across_keys(respx_mock):
    route = respx_mock.get(url__regex=SCOPUS).mock(return_value=httpx.Response(200, json={}))
    c = make(["key1", "key2"])
    for i in range(4):
        await c.get_json(SCOPUS_PATH, {"q": i}, use_cache=False)
    assert [call.request.headers["X-ELS-APIKey"] for call in route.calls] == ["key1", "key2", "key1", "key2"]


async def test_concurrent_requests_scale_with_key_count(respx_mock):
    import asyncio
    from collections import Counter

    active, peak = Counter(), Counter()

    async def slow(request):
        key = request.headers["X-ELS-APIKey"]
        active[key] += 1
        active["all"] += 1
        peak[key] = max(peak[key], active[key])
        peak["all"] = max(peak["all"], active["all"])
        await asyncio.sleep(0.02)
        active[key] -= 1
        active["all"] -= 1
        return httpx.Response(200, json={})

    respx_mock.get(url__regex=SCIVAL).mock(side_effect=slow)
    c = ElsevierClient(["key1", "key2", "key3"], backoff_base=0, rate_per_sec=1000, max_concurrency=1)
    await asyncio.gather(*(c.get_json(SCIVAL_PATH, {"q": i}, use_cache=False) for i in range(9)))
    # 每組 Key 同時只送 1 個，三組 Key 一起送就是 3 個
    assert peak["all"] == 3 and peak["key1"] == peak["key2"] == peak["key3"] == 1


async def test_exhausted_key_is_skipped_while_others_continue(respx_mock):
    def respond(request):
        return exhausted() if request.headers["X-ELS-APIKey"] == "key1" else httpx.Response(200, json={})

    route = respx_mock.get(url__regex=SCOPUS).mock(side_effect=respond)
    c = make(["key1", "key2"])
    for i in range(3):
        await c.get_json(SCOPUS_PATH, {"q": i}, use_cache=False)
    used = [call.request.headers["X-ELS-APIKey"] for call in route.calls]
    assert used == ["key1", "key2", "key2", "key2"]   # 用完的 Key 直到重置前不再分到請求


async def test_waits_for_reset_only_when_every_key_is_exhausted(respx_mock, monkeypatch):
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr("app.elsevier.client.asyncio.sleep", sleep)
    respx_mock.get(url__regex=SCOPUS).mock(side_effect=[exhausted(), exhausted(), httpx.Response(200, json={"ok": 1})])
    c = make(["key1", "key2"])
    c.wait_for_quota_reset = True
    assert await c.get_json(SCOPUS_PATH, {}, use_cache=False) == {"ok": 1}
    assert len(waits) == 1 and waits[0] > 6 * 86400


async def test_check_connection_checks_every_key(respx_mock):
    from tests.test_elsevier_client import quota_headers

    scopus = respx_mock.get(url__regex=SCOPUS).mock(return_value=httpx.Response(
        200, json={}, headers=quota_headers(20000, 123)))
    respx_mock.get(url__regex=SCIVAL).mock(side_effect=lambda r: httpx.Response(
        403 if r.headers["X-ELS-APIKey"] == "key2" else 200, json={}))
    seen = []
    c = ElsevierClient(["key1", "key2"], backoff_base=0, rate_per_sec=1000,
                       on_quota=lambda api, q, kid: seen.append((api, kid)))
    status = await c.check_connection()
    assert [call.request.headers["X-ELS-APIKey"] for call in scopus.calls] == ["key1", "key2"]
    assert seen == [("scopus", c.key_ids[0]), ("scopus", c.key_ids[1])]
    assert not status.ok and status.message.startswith("第 2 組 Key SciVal：")


def test_home_quota_shows_grid_for_several_keys(multi_client):
    from app.elsevier.client import key_id
    from app.elsevier.quota import Quota, QuotaStore

    store = QuotaStore(multi_client.app.state.session_factory)
    store.record("scopus", Quota(20000, 15000, None), key_id("aaaa1111111111zzzz"))
    store.record("scopus", Quota(20000, 1000, None), key_id("bbbb2222222222yyyy"))
    store.record("scival", Quota(5000, 5000, None), key_id("aaaa1111111111zzzz"))
    text = multi_client.get("/status/panel").text
    assert 'class="quota-grid"' in text and "Key 1" in text and "Key 2" in text
    assert "75%" in text and "5%" in text and "100%" in text
    assert 'class="quota-cell low"' in text   # 第 2 組 Scopus 只剩 5%
    assert "1111111111" not in text   # 欄位提示只放遮罩後的 Key


def test_home_quota_keeps_list_for_single_key(tmp_path):
    from app.elsevier.client import key_id
    from app.elsevier.quota import Quota, QuotaStore

    s = Settings(_env_file=None, elsevier_api_key="aaaa1111111111zzzz", data_dir=tmp_path, env_path=tmp_path / ".env")
    with TestClient(create_app(s)) as c:
        QuotaStore(c.app.state.session_factory).record("scopus", Quota(20000, 19876, None),
                                                       key_id("aaaa1111111111zzzz"))
        text = c.get("/status/panel").text
    assert "quota-grid" not in text and "19,876" in text and "次/週" in text


def test_quota_saved_before_upgrade_counts_as_first_key(session_factory):
    from app.elsevier.quota import Quota, QuotaStore, quota_grid

    QuotaStore(session_factory).record("scival", Quota(5000, 2500, None))   # 舊紀錄沒有 Key 識別碼
    with session_factory() as s:
        grid = quota_grid(s, ["id1", "id2"])
    assert [(r.api, [c and c.remaining for c in r.cells]) for r in grid] == [("scival", [2500, None])]


def quota_exceeded_response():
    """實際的額度用完回應：沒有 X-RateLimit-* 標頭，只有 X-ELS-Status（2026-11-22 實測）。"""
    return httpx.Response(429, headers={"X-ELS-Status": "QUOTA_EXCEEDED - Quota Exceeded"},
                          json={"service-error": {"status": {"statusCode": "QUOTA_EXCEEDED"}}})


async def test_quota_exceeded_without_ratelimit_headers_switches_key(respx_mock):
    def respond(request):
        if request.headers["X-ELS-APIKey"] == "key1":
            return quota_exceeded_response()
        return httpx.Response(200, json={})

    route = respx_mock.get(url__regex=SCOPUS).mock(side_effect=respond)
    c = make(["key1", "key2"])
    for i in range(3):
        await c.get_json(SCOPUS_PATH, {"q": i}, use_cache=False)
    used = [call.request.headers["X-ELS-APIKey"] for call in route.calls]
    assert used == ["key1", "key2", "key2", "key2"]   # 不當成限速一直重試 key1
    assert c.limiter("scopus", 0).throttled == 0


async def test_quota_exceeded_on_only_key_is_reported_not_retried(respx_mock):
    route = respx_mock.get(url__regex=SCOPUS).mock(return_value=quota_exceeded_response())
    with pytest.raises(ElsevierError) as e:
        await make(["key1"]).get_json(SCOPUS_PATH, {}, use_cache=False)
    assert route.call_count == 1 and "額度已用完" in e.value.message


async def test_last_request_of_quota_moves_on_to_next_key(respx_mock):
    reset = str(int(time.time()) + 86400)
    route = respx_mock.get(url__regex=SCOPUS).mock(side_effect=lambda r: httpx.Response(
        200, json={}, headers={"X-RateLimit-Limit": "100", "X-RateLimit-Reset": reset,
                               "X-RateLimit-Remaining": "0" if r.headers["X-ELS-APIKey"] == "key1" else "50"}))
    c = make(["key1", "key2"])
    c.load_balance = False
    for i in range(3):
        await c.get_json(SCOPUS_PATH, {"q": i}, use_cache=False)
    assert [call.request.headers["X-ELS-APIKey"] for call in route.calls] == ["key1", "key2", "key2"]


async def test_seeded_exhausted_key_is_skipped(respx_mock):
    from datetime import datetime, timedelta

    from app.elsevier.quota import Quota

    route = respx_mock.get(url__regex=SCOPUS).mock(return_value=httpx.Response(200, json={}))
    c = make(["key1", "key2"])
    c.seed_quota("scopus", c.key_ids[0], Quota(100, 0, datetime.now() + timedelta(days=2)))
    await c.get_json(SCOPUS_PATH, {}, use_cache=False)
    assert route.calls[0].request.headers["X-ELS-APIKey"] == "key2"


async def test_single_key_first_uses_first_key_until_exhausted(respx_mock):
    calls = []

    def respond(request):
        calls.append(request.headers["X-ELS-APIKey"])
        return exhausted() if len(calls) == 3 else httpx.Response(200, json={})

    respx_mock.get(url__regex=SCOPUS).mock(side_effect=respond)
    c = ElsevierClient(["key1", "key2"], backoff_base=0, rate_per_sec=1000, load_balance=False)
    for i in range(4):
        await c.get_json(SCOPUS_PATH, {"q": i}, use_cache=False)
    assert calls == ["key1", "key1", "key1", "key2", "key2"]


def test_load_balance_checkbox_saves_setting(multi_client):
    from app.db.settings_store import get_setting

    text = multi_client.get("/settings").text
    assert 'name="load_balance"' in text and "checked" in text.split('name="load_balance"')[1].split(">")[0]
    multi_client.post("/settings/load-balance", data={})
    with multi_client.app.state.session_factory() as s:
        assert get_setting(s, "load_balance") is False
    multi_client.post("/settings/load-balance", data={"load_balance": "1"})
    with multi_client.app.state.session_factory() as s:
        assert get_setting(s, "load_balance") is True
