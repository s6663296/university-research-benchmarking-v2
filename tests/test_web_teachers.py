from tests.mapping_builder import build_mapping, ncu_rows, sample_mapping
from tests.moe_builder import build_moe, sample_moe

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def upload_mapping(client, data):
    client.post("/mapping", files={"file": ("m.xlsx", data, XLSX)})


def upload_moe(client, data):
    return client.post("/teachers/import", files={"file": ("moe.xlsx", data, XLSX)}, follow_redirects=True)


def test_teachers_page_empty(client):
    assert "尚未匯入" in client.get("/teachers").text


def test_teachers_page_shows_counts(client):
    upload_mapping(client, sample_mapping())
    r = upload_moe(client, sample_moe())
    assert r.status_code == 200
    assert "114" in r.text and "24" in r.text


def test_teachers_page_flow(client):
    upload_mapping(client, build_mapping({"中央": ncu_rows(("資訊管理學系",))}))
    text = upload_moe(client, build_moe([(114, "國立中央大學", "X1", "資管系", 20)])).text
    assert "未對應" in text and "資管系" in text
    r = client.post("/teachers/link", data={"school": "中央", "dept_zh": "資訊管理學系", "codes": ["X1"]},
                    follow_redirects=True)
    assert "未對應的單位（1）" not in r.text
    assert "20" in r.text


def test_override_requires_reason(client):
    upload_mapping(client, sample_mapping())
    upload_moe(client, sample_moe())
    r = client.post("/teachers/override", data={"school": "中央", "dept_zh": "資訊管理學系",
                                                "academic_year": "114", "count": "23", "reason": ""})
    assert r.status_code == 400 and "請填寫修改原因" in r.text
    r = client.post("/teachers/override", data={"school": "中央", "dept_zh": "資訊管理學系",
                                                "academic_year": "114", "count": "23", "reason": "依 SciVal"},
                    follow_redirects=True)
    assert "依 SciVal" in r.text


def test_upload_bad_moe_file(client):
    r = upload_moe(client, b"garbage")
    assert r.status_code == 400 and "無法讀取" in r.text


def test_home_shows_teacher_import(client):
    upload_mapping(client, sample_mapping())
    upload_moe(client, sample_moe())
    assert "113～114 學年度" in client.get("/").text
