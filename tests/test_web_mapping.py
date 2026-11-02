from pathlib import Path

from tests.mapping_builder import sample_mapping

# 2026/10 的原始檔（尚無「中央」分頁），固定下來，docs/ 中的檔案填寫後測試不受影響
REAL = Path("tests/fixtures/mapping_2026-10.xlsx").read_bytes()
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def upload(client, data, name="mapping.xlsx"):
    return client.post("/mapping", files={"file": (name, data, XLSX)})


def test_mapping_page_empty(client):
    r = client.get("/mapping")
    assert r.status_code == 200 and "尚未匯入" in r.text


def test_upload_page_shows_issues_and_diff(client):
    r = upload(client, REAL)
    assert r.status_code == 200
    assert "缺少「國立中央大學」分頁" in r.text
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


def test_mapping_template_download_is_readable(client):
    from app.mapping.parser import NCU_SHEET, parse_mapping
    r = client.get("/mapping/template.xlsx")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    result = parse_mapping(r.content)
    assert result.units == [] and not [i for i in result.issues if i.level == "error"]
    from io import BytesIO
    from openpyxl import load_workbook
    wb = load_workbook(BytesIO(r.content))
    assert wb.sheetnames[0] == NCU_SHEET
    assert [c.value for c in wb[NCU_SHEET][1]] == ["學院", f"{NCU_SHEET}系所", NCU_SHEET, "系所中文名稱", "系所英文名稱", "備註"]
    assert "/mapping/template.xlsx" in client.get("/mapping").text
