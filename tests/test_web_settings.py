import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

SCOPUS = r".*/content/search/scopus.*"
SCIVAL = r".*/analytics/scival/.*"


@pytest.fixture
def keyed_client(tmp_path):
    s = Settings(_env_file=None, elsevier_api_key="abcdefgh1234", data_dir=tmp_path, env_path=tmp_path / ".env")
    with TestClient(create_app(s)) as c:
        yield c


def form(client, **overrides):
    """取出設定頁目前的值，套上修改後送出。"""
    data = {
        "rate_per_sec": "3.0", "max_retries": "3", "backoff_base_sec": "1.0", "timeout_sec": "60",
        "cache_days": "1", "anomaly_change_threshold": "0.3",
        "max_concurrency": "4", "rate_limit_retries": "8", "max_backoff_sec": "120",
        "teacher_column": "total", "self_citation_strategy": "none", "rank_highlight_top": "2",
    }
    data.update(overrides)
    return client.post("/settings", data=data)


def test_settings_page_never_shows_full_key(keyed_client):
    r = keyed_client.get("/settings")
    assert r.status_code == 200
    assert "1234" in r.text and "abcdefgh" not in r.text


def test_post_settings_updates_values(client):
    r = form(client, timeout_sec="45")
    assert r.status_code == 200
    assert 'value="45"' in client.get("/settings").text


def test_settings_page_has_no_name_tables(client):
    text = client.get("/settings").text
    assert "學校對照" not in text and "簡稱" not in text


def test_post_rejects_invalid_number(client):
    r = form(client, rate_per_sec="-1", timeout_sec="45")
    assert r.status_code == 400
    assert "每秒請求數" in r.text
    assert 'value="60"' in client.get("/settings").text


def test_save_api_key_writes_env(keyed_client, tmp_path):
    keyed_client.post("/settings/api-keys/delete", data={"index": "0"})
    keyed_client.post("/settings/api-keys/add", data={"api_key": "newkey9999"})
    assert "ELSEVIER_API_KEY=newkey9999" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert "9999" in keyed_client.get("/settings").text


def test_status_without_key(client):
    r = client.get("/status")
    assert "尚未設定 API Key" in r.text


def test_status_unreachable(keyed_client, respx_mock):
    respx_mock.get(url__regex=SCOPUS).mock(side_effect=httpx.ConnectError("x"))
    assert "無法連線" in keyed_client.get("/status").text


def quota_headers(limit, remaining):
    return {"X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Reset": "1793849792"}


def test_status_check_shows_each_api_quota(keyed_client, respx_mock):
    respx_mock.get(url__regex=SCOPUS).mock(return_value=httpx.Response(200, json={}, headers=quota_headers(20000, 19876)))
    respx_mock.get(url__regex=SCIVAL).mock(return_value=httpx.Response(200, json={}, headers=quota_headers(5000, 3700)))
    text = keyed_client.get("/status").text
    assert "連線正常" in text
    assert "Scopus Search API" in text and "19,876" in text and "20,000" in text
    assert "SciVal API" in text and "3,700" in text and "5,000" in text


def test_quota_panel_uses_saved_values_without_requests(keyed_client, respx_mock):
    route = respx_mock.get(url__regex=SCOPUS)
    assert "尚無額度資料" in keyed_client.get("/status/panel").text
    respx_mock.get(url__regex=SCIVAL).mock(return_value=httpx.Response(200, json={}, headers=quota_headers(5000, 3700)))
    route.mock(return_value=httpx.Response(200, json={}, headers=quota_headers(20000, 19876)))
    keyed_client.get("/status")
    text = keyed_client.get("/status/panel").text
    assert "19,876" in text and "3,700" in text
    assert route.call_count == 1  # 只有按「檢查連線」那次


def test_home_card_loads_saved_quota_and_checks(client):
    text = client.get("/").text
    assert 'hx-get="/status/panel"' in text and 'hx-get="/status"' in text and "檢查連線</button>" in text


def test_reset_clears_data_key_and_settings(client, tmp_path, web_done_run):
    from sqlalchemy import func, select

    from app.db.models import AppSetting, Base

    form(client, timeout_sec="45")
    client.post("/settings/api-keys/add", data={"api_key": "newkey9999"})
    client.post("/settings/insttoken", data={"insttoken": "tok5678"})
    (tmp_path / "exports" / str(web_done_run)).mkdir(parents=True, exist_ok=True)
    (tmp_path / ".env").write_text((tmp_path / ".env").read_text(encoding="utf-8") + "PORT=9000\n", encoding="utf-8")

    r = client.post("/settings/reset", follow_redirects=True)

    assert r.status_code == 200 and "所有資料已清除" in r.text
    with client.app.state.session_factory() as s:
        for table in Base.metadata.sorted_tables:
            assert s.scalar(select(func.count()).select_from(table)) == 0, table.name
        assert s.get(AppSetting, "timeout_sec") is None
    assert 'value="60"' in r.text  # 回到預設值
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "ELSEVIER_API_KEY=\n" in env and "ELSEVIER_INSTTOKEN=\n" in env and "PORT=9000" in env
    assert not (tmp_path / "exports").exists()
    assert client.app.state.settings.elsevier_api_key == ""
    assert "尚未設定 API Key" in r.text


def test_reset_refused_while_run_is_fetching(client):
    manager = client.app.state.run_manager
    manager.any_running = lambda: True
    r = client.post("/settings/reset")
    assert r.status_code == 409
