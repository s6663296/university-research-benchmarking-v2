import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

SCOPUS = r".*/content/search/scopus.*"


@pytest.fixture
def keyed_client(tmp_path):
    s = Settings(_env_file=None, elsevier_api_key="abcdefgh1234", data_dir=tmp_path, env_path=tmp_path / ".env")
    with TestClient(create_app(s)) as c:
        yield c


def form(client, **overrides):
    """取出設定頁目前的值，套上修改後送出。"""
    data = {
        "rate_per_sec": "3.0", "max_retries": "3", "backoff_base_sec": "1.0", "timeout_sec": "60",
        "cache_days": "1", "scival_batch_size": "25", "anomaly_change_threshold": "0.3",
        "teacher_column": "total", "self_citation_strategy": "scival_param", "rank_highlight_top": "2",
        "dept_short_names": "資訊管理學系=資管系", "college_short_names": "管理學院=管學院",
        "school_aliases": "中央, 國立中央大學, 中央\n政治, 國立政治大學, 政大",
    }
    data.update(overrides)
    return client.post("/settings", data=data)


def test_settings_page_never_shows_full_key(keyed_client):
    r = keyed_client.get("/settings")
    assert r.status_code == 200
    assert "1234" in r.text and "abcdefgh" not in r.text


def test_post_settings_updates_values(client):
    r = form(client, scival_batch_size="40")
    assert r.status_code == 200
    assert 'value="40"' in client.get("/settings").text


def test_post_settings_updates_aliases_and_short_names(client):
    form(client, school_aliases="政治, 國立政治大學, 政治大學", dept_short_names="會計研究所=會研所")
    text = client.get("/settings").text
    assert "政治大學" in text and "會計研究所=會研所" in text


def test_post_rejects_invalid_number(client):
    r = form(client, rate_per_sec="-1", scival_batch_size="40")
    assert r.status_code == 400
    assert "每秒請求數" in r.text
    assert 'value="25"' in client.get("/settings").text


def test_save_api_key_writes_env(keyed_client, tmp_path):
    keyed_client.post("/settings/api-key", data={"api_key": "newkey9999", "insttoken": ""})
    assert "ELSEVIER_API_KEY=newkey9999" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert "9999" in keyed_client.get("/settings").text


def test_status_without_key(client):
    r = client.get("/status")
    assert "尚未設定 API Key" in r.text


def test_status_unreachable(keyed_client, respx_mock):
    respx_mock.get(url__regex=SCOPUS).mock(side_effect=httpx.ConnectError("x"))
    assert "無法連線" in keyed_client.get("/status").text


def test_status_ok_shows_remaining(keyed_client, respx_mock):
    respx_mock.get(url__regex=SCOPUS).mock(return_value=httpx.Response(
        200, json={}, headers={"X-RateLimit-Remaining": "19876"}))
    text = keyed_client.get("/status").text
    assert "連線正常" in text and "19876" in text


def test_home_loads_status(client):
    assert 'hx-get="/status"' in client.get("/").text
