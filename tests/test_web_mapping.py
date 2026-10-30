from pathlib import Path

from tests.mapping_builder import sample_mapping

REAL = Path("docs/中大與他校mapping表.xlsx").read_bytes()
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def upload(client, data, name="mapping.xlsx"):
    return client.post("/mapping", files={"file": (name, data, XLSX)})


def test_mapping_page_empty(client):
    r = client.get("/mapping")
    assert r.status_code == 200 and "尚未匯入" in r.text


def test_upload_page_shows_issues_and_diff(client):
    r = upload(client, REAL)
    assert r.status_code == 200
    assert "缺少「中央」分頁" in r.text
    assert "新增 161" in r.text


def test_upload_same_file_says_unchanged(client):
    upload(client, REAL)
    assert "與目前版本相同" in upload(client, REAL).text


def test_unit_list_filter_by_school(client):
    upload(client, sample_mapping())
    text = client.get("/mapping", params={"school": "政治"}).text
    assert "National Chengchi University" in text and "National Taiwan University" not in text


def test_upload_rejects_non_xlsx(client):
    r = upload(client, b"not xlsx", "a.txt")
    assert r.status_code == 400 and "無法讀取檔案" in r.text
    assert "尚未匯入" in client.get("/mapping").text


def test_home_shows_mapping_version(client):
    upload(client, sample_mapping(), "管理學院mapping.xlsx")
    assert "管理學院mapping.xlsx" in client.get("/").text
