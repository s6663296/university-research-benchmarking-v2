import json
import re
import time

import httpx

from app.teachers.moe_fetch import BASE_URL, REPORT_PATH, SOURCE_URL
from tests.moe_builder import build_moe

URL = BASE_URL + REPORT_PATH
INDEX_HTML = '<select class="selectpicker" id="ddlPeriodList" multiple=""><option>113</option>\n<option>114</option></select>'
FILENAME = "教1-1.專任教師數-以「系(所)」統計.xlsx"


def mock_site(respx_mock, download=None):
    respx_mock.get(URL + "/Index").mock(return_value=httpx.Response(200, text=INDEX_HTML))
    export = respx_mock.post(URL + "/Export").mock(return_value=httpx.Response(
        200, json={"handle": "ng2-114-abc", "fileName": FILENAME, "fileResult": True}))
    data = download if download is not None else build_moe([(114, "國立中央大學", "X1", "資管系", 20)])
    respx_mock.get(URL + "/Download/").mock(return_value=httpx.Response(200, content=data))
    return export


def wait_fetch(client, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if not client.app.state.moe_fetcher.running and client.app.state.moe_fetcher.job:
            return client.app.state.moe_fetcher.job
        time.sleep(0.05)
    raise AssertionError("下載沒有在時間內完成")


def test_teachers_page_loads_fetch_panel(client):
    text = client.get("/teachers").text
    assert ">從教育部下載</button>" in text and '<dialog id="moe-dialog"' in text


def test_fetch_panel_lists_years_and_source(client, respx_mock):
    mock_site(respx_mock)
    text = client.get("/teachers/fetch").text
    assert re.search(r'value="114"\s+checked', text) and not re.search(r'value="113"\s+checked', text)
    assert f'href="{SOURCE_URL}"' in text


def test_fetch_panel_offline(client, respx_mock):
    respx_mock.get(URL + "/Index").mock(side_effect=httpx.ConnectError("x"))
    assert "無法從教育部網站取得學年度清單" in client.get("/teachers/fetch").text


def test_fetch_downloads_and_imports(client, respx_mock):
    export = mock_site(respx_mock)
    client.get("/teachers/fetch")
    r = client.post("/teachers/fetch", data={"years": ["114"]})
    assert "every 1s" in r.text and "向教育部網站要求產生 114 學年度檔案" in r.text
    job = wait_fetch(client)
    assert job.status == "done" and job.row_count == 1
    assert json.loads(dict(httpx.QueryParams(export.calls[0].request.content.decode()))["Parameter"])["Year"] == ["114"]
    poll = client.get("/teachers/fetch?poll=1")
    assert "完成：匯入 1 筆" in poll.text and "data-imported" in poll.text
    assert "every 1s" not in poll.text and "data-imported" not in client.get("/teachers/fetch").text
    assert "114～114 學年度" in client.get("/teachers").text
    assert client.get("/teachers/fetch/file").content.startswith(b"PK")


def test_fetch_rate_limited(client, respx_mock):
    mock_site(respx_mock, download="操作頻繁,請稍後再次下載，若再次發生請與管理者連絡確認,request:http://x".encode())
    client.get("/teachers/fetch")
    client.post("/teachers/fetch", data={"years": ["114"]})
    job = wait_fetch(client)
    assert job.status == "failed" and job.error == "教育部網站回應：操作頻繁,請稍後再次下載，若再次發生請與管理者連絡確認"
    poll = client.get("/teachers/fetch?poll=1")
    assert "失敗" in poll.text and "data-imported" not in poll.text
    assert "尚未匯入" in client.get("/teachers").text


def test_fetch_requires_year(client, respx_mock):
    mock_site(respx_mock)
    assert "請至少選一個學年度" in client.post("/teachers/fetch", data={}).text
