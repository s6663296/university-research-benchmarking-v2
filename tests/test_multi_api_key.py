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
    assert "已設定 2 組" in text
    assert "aaaa" + "*" * 8 + "zzzz" in text and "bbbb" + "*" * 8 + "yyyy" in text
    assert "1111111111" not in text and "2222222222" not in text


def env_text(tmp_path):
    return (tmp_path / ".env").read_text(encoding="utf-8")


def test_add_key_appends_to_existing(multi_client, tmp_path):
    multi_client.post("/settings/api-keys/add", data={"api_key": " cccc3333333333xxxx "})
    assert "ELSEVIER_API_KEY=aaaa1111111111zzzz,bbbb2222222222yyyy,cccc3333333333xxxx" in env_text(tmp_path)
    assert "已設定 3 組" in multi_client.get("/settings").text


def test_add_accepts_several_pasted_keys_and_skips_duplicates(multi_client, tmp_path):
    multi_client.post("/settings/api-keys/add", data={"api_key": "aaaa1111111111zzzz, dddd4444444444wwww"})
    assert "ELSEVIER_API_KEY=aaaa1111111111zzzz,bbbb2222222222yyyy,dddd4444444444wwww" in env_text(tmp_path)


def test_add_blank_changes_nothing(multi_client):
    multi_client.post("/settings/api-keys/add", data={"api_key": "  "})
    assert "已設定 2 組" in multi_client.get("/settings").text


def test_delete_key_by_position(multi_client, tmp_path):
    multi_client.post("/settings/api-keys/delete", data={"index": "0"})
    assert "ELSEVIER_API_KEY=bbbb2222222222yyyy\n" in env_text(tmp_path)
    text = multi_client.get("/settings").text
    assert "已設定 1 組" in text and "aaaa" not in text


def test_delete_out_of_range_changes_nothing(multi_client):
    assert multi_client.post("/settings/api-keys/delete", data={"index": "9"}, follow_redirects=False).status_code == 303
    assert "已設定 2 組" in multi_client.get("/settings").text


def test_each_key_row_has_delete_button(multi_client):
    text = multi_client.get("/settings").text
    assert text.count('action="/settings/api-keys/delete"') == 2


def test_save_insttoken_keeps_keys(multi_client, tmp_path):
    multi_client.post("/settings/insttoken", data={"insttoken": "tok5678"})
    env = env_text(tmp_path)
    assert "ELSEVIER_INSTTOKEN=tok5678" in env and "aaaa1111111111zzzz,bbbb2222222222yyyy" in env


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
    assert "已設定 2 組" in r.text
    after = env_text(tmp_path) if (tmp_path / ".env").exists() else None
    assert after == before  # 沒有改寫 .env


def test_adding_new_key_shows_confirmation(multi_client):
    r = multi_client.post("/settings/api-keys/add", data={"api_key": "cccc3333333333xxxx"})
    assert r.url.path == "/settings" and "已新增 1 組 API Key" in r.text


def test_pasting_mix_reports_added_and_duplicates(multi_client):
    r = multi_client.post("/settings/api-keys/add", data={"api_key": "aaaa1111111111zzzz\ncccc3333333333xxxx"})
    assert "已新增 1 組 API Key" in r.text and "第 1 組" in r.text and "沒有重複新增" in r.text
