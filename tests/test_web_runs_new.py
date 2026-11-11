import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.db.models import Run
from app.main import create_app
from tests.elsevier_fixtures import SCOPUS_URL
from tests.funding_builder import sample_funding
from tests.mapping_builder import sample_mapping
from tests.moe_builder import sample_moe
from tests.run_helpers import FakeElsevier

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FORM = {"year_from": "2021", "year_to": "2025", "ncu_depts": ["資訊管理學系"],
        "comparisons": ["benchmark", "college"], "schools": ["台大", "政治"], "academic_year": "114",
        "force_refresh": "1"}


@pytest.fixture
def seeded(tmp_path):
    s = Settings(_env_file=None, elsevier_api_key="testkey", data_dir=tmp_path, env_path=tmp_path / ".env")
    with TestClient(create_app(s)) as c:
        c.post("/mapping", files={"file": ("m.xlsx", sample_mapping(), XLSX)})
        c.post("/teachers/import", files={"file": ("moe.xlsx", sample_moe(), XLSX)})
        c.post("/funding/import", files={"file": ("f.xlsx", sample_funding(), XLSX)})
        yield c


def run_count(client):
    with client.app.state.session_factory() as s:
        return s.scalar(select(func.count()).select_from(Run))


def latest_run_id(client):
    with client.app.state.session_factory() as s:
        return s.scalar(select(func.max(Run.id)))


def wait_done(client, run_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        with client.app.state.session_factory() as s:
            if s.get(Run, run_id).status in ("done", "failed"):
                return s.get(Run, run_id).status
        time.sleep(0.05)
    raise AssertionError("分析沒有在時間內完成")


def checked(text: str, value: str) -> bool:
    """勾選框是否預設勾選（屬性順序不固定，以該 input 標籤判斷）。"""
    import re
    tag = re.search(rf'<input[^>]*value="{re.escape(value)}"[^>]*>', text)
    assert tag, value
    return " checked" in tag.group(0)


def test_new_run_defaults(seeded):
    text = seeded.get("/runs/new").text
    assert 'name="year_from" value="2021"' in text and 'name="year_to" value="2025"' in text
    # 對標學校表格：每個系所在 mapping 有對應的學校才有勾選框，預設全部勾選
    assert checked(text, "資訊管理學系::台大") and checked(text, "資訊管理學系::政治")
    assert checked(text, "財務金融學系::台大") and 'value="財務金融學系::政治"' not in text
    # 院內比較：預設納入整個學院
    assert checked(text, "管理學院::會計研究所")


def test_new_run_omits_connection_panel_and_reuse_link(seeded):
    text = seeded.get('/runs/new').text
    assert 'API 連線狀態' not in text
    assert '沿用上一版設定' not in text
    assert 'name="year_from"' in text


def test_preview_shows_queries(seeded):
    text = seeded.post("/runs/preview", data=FORM).text
    assert 'AFFIL(&#34;Department of Information Management&#34; W/15 &#34;National Taiwan University&#34;)' in text
    assert "16" in text  # 台大資管教師數


def test_preview_college_selects_all_depts(seeded):
    form = {**FORM, "ncu_depts": [], "colleges": ["管理學院"]}
    assert "Graduate Institute of Accounting&#34; W/15 &#34;National Central University" in seeded.post("/runs/preview", data=form).text


def test_preview_shows_blocking_errors(seeded):
    assert "今年" in seeded.post("/runs/preview", data={**FORM, "year_to": "2026"}).text


def test_create_blocked_when_offline(seeded, respx_mock):
    respx_mock.get(url__regex=SCOPUS_URL).mock(side_effect=httpx.ConnectError("x"))
    r = seeded.post("/runs", data=FORM)
    assert "無法連線" in r.text and run_count(seeded) == 0


def test_create_runs_in_background(seeded, respx_mock):
    FakeElsevier().install(respx_mock)
    r = seeded.post("/runs", data=FORM, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"  # 回首頁看儀表板
    run_id = latest_run_id(seeded)
    assert wait_done(seeded, run_id) == "done"
    frag = seeded.get(f"/runs/{run_id}/progress")
    assert 'every 1s' not in frag.text and frag.headers.get("HX-Refresh") == "true"
    assert "every 1s" not in seeded.get(f"/runs/{run_id}").text


def test_reuse_last_config(seeded, respx_mock):
    FakeElsevier().install(respx_mock)
    seeded.post("/runs", data={**FORM, "schools": ["台大"]}, follow_redirects=False)
    wait_done(seeded, latest_run_id(seeded))
    text = seeded.get("/runs/new", params={"reuse": 1}).text
    assert checked(text, "資訊管理學系::台大") and not checked(text, "資訊管理學系::政治")


def test_settings_page_renamed(seeded):
    text = seeded.get("/runs/new").text
    assert "<h1>分析設定</h1>" in text and ">開始分析</button>" in text
    assert '<a href="/runs/new">分析設定</a>' in text  # 上方選單


def test_home_start_without_previous_run_goes_to_settings(seeded):
    r = seeded.post("/runs/start", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/runs/new" and run_count(seeded) == 0


def test_home_start_reuses_last_settings(seeded, respx_mock):
    FakeElsevier().install(respx_mock)
    seeded.post("/runs", data={**FORM, "schools": ["台大"]}, follow_redirects=False)
    wait_done(seeded, latest_run_id(seeded))
    r = seeded.post("/runs/start", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/" and run_count(seeded) == 2
    wait_done(seeded, latest_run_id(seeded))
    with seeded.app.state.session_factory() as s:
        first, second = s.scalars(select(Run).order_by(Run.id)).all()
        assert first.scope_json == second.scope_json


def test_home_start_blocked_while_running(seeded):
    from tests.run_helpers import TODAY, default_config
    from app.runs.service import create_run
    with seeded.app.state.session_factory() as s:
        run_id = create_run(s, default_config(), today=TODAY).id
        s.get(Run, run_id).status = "running"
        s.commit()
    manager = seeded.app.state.run_manager
    manager.any_running = manager.is_running = lambda *a: True
    manager.current_run_id = run_id
    r = seeded.post("/runs/start", follow_redirects=False)
    assert r.headers["location"] == "/" and run_count(seeded) == 1
    assert ">❚❚ 暫停</button>" in seeded.get("/").text


def test_fetch_page_shows_unit_status_after_done(seeded, respx_mock):
    FakeElsevier().install(respx_mock)
    seeded.post("/runs", data=FORM, follow_redirects=False)
    run_id = latest_run_id(seeded)
    wait_done(seeded, run_id)
    text = seeded.get(f"/runs/{run_id}/fetch").text
    assert "<th>狀態</th>" in text and "完成" in text and f'href="/runs/{run_id}"' in text


def test_department_picker_cards(seeded):
    r = seeded.get("/runs/new")
    assert r.status_code == 200
    assert 'class="college-card' in r.text and "data-college-all" in r.text
    # 學院勾選框只負責在頁面上勾選系所，不另外送出；送出的永遠是實際勾選的系所
    assert 'name="colleges"' not in r.text
    assert r.text.count('type="checkbox" name="ncu_depts"') == 3


def test_comparison_cards(seeded):
    r = seeded.get("/runs/new")
    assert r.text.count('class="compare-card"') == 3
    assert "簡報左邊表格" in r.text and "簡報右邊表格" in r.text
    # 自訂比較改為勾選清單（依學校分組，中大在最前），不再是多選下拉框
    assert '<select name="custom_unit_ids"' not in r.text
    assert 'type="checkbox" name="custom_unit_ids"' in r.text
    assert r.text.index('class="unit-school">中央') < r.text.index('class="unit-school">台大')


def test_custom_units_submitted_as_checkboxes(seeded, respx_mock):
    FakeElsevier().install(respx_mock)
    with seeded.app.state.session_factory() as s:
        from app.db.models import Unit
        ids = [str(u.id) for u in s.query(Unit).limit(2)]
    form = {**FORM, "comparisons": ["benchmark", "college", "custom"], "custom_unit_ids": ids}
    r = seeded.post("/runs/preview", data=form)
    assert r.status_code == 200


def _groups(client):
    from app.db.models import Unit
    from app.reports.tables import run_groups
    with client.app.state.session_factory() as s:
        run = s.get(Run, latest_run_id(client))
        return {(g.comparison, g.key): sorted((s.get(Unit, u).school, s.get(Unit, u).ncu_dept) for u in g.unit_ids)
                for g in run_groups(s, run)}


def test_each_department_has_its_own_schools(seeded, respx_mock):
    FakeElsevier().install(respx_mock)
    form = {**FORM, "ncu_depts": ["資訊管理學系", "財務金融學系"], "dept_school_form": "1",
            "dept_school": ["資訊管理學系::政治", "財務金融學系::台大"]}
    seeded.post("/runs", data=form, follow_redirects=False)
    groups = _groups(seeded)
    assert groups[("benchmark", "資訊管理學系")] == [("中央", "資訊管理學系"), ("政治", "資訊管理學系")]
    assert groups[("benchmark", "財務金融學系")] == [("中央", "財務金融學系"), ("台大", "財務金融學系")]
    text = seeded.get("/runs/new", params={"reuse": 1}).text
    assert checked(text, "資訊管理學系::政治") and not checked(text, "資訊管理學系::台大")


def test_department_with_no_schools_compares_only_itself(seeded, respx_mock):
    FakeElsevier().install(respx_mock)
    seeded.post("/runs", data={**FORM, "dept_school_form": "1", "dept_school": []}, follow_redirects=False)
    assert _groups(seeded)[("benchmark", "資訊管理學系")] == [("中央", "資訊管理學系")]


def test_college_comparison_members(seeded, respx_mock):
    FakeElsevier().install(respx_mock)
    form = {**FORM, "college_dept_form": "管理學院", "college_dept": ["管理學院::財務金融學系"]}
    seeded.post("/runs", data=form, follow_redirects=False)
    # 只勾財金，但本次分析的資管一定納入；會計所沒勾所以不在院內比較
    assert _groups(seeded)[("college", "管理學院")] == [("中央", "財務金融學系"), ("中央", "資訊管理學系")]
    text = seeded.get("/runs/new", params={"reuse": 1}).text
    assert checked(text, "管理學院::財務金融學系") and not checked(text, "管理學院::會計研究所")


def test_old_config_without_new_fields_keeps_behavior():
    from app.runs.scope import RunConfig
    old = '{"year_from": 2021, "year_to": 2025, "ncu_depts": ["資訊管理學系"], "comparisons": ["benchmark"],' \
          ' "schools": ["台大"], "custom_unit_ids": [], "academic_year": 114, "teacher_column": "total"}'
    cfg = RunConfig.from_json(old)
    assert cfg.schools_for("資訊管理學系") == ("台大",) and cfg.college_members("管理學院") is None
    assert RunConfig.from_json(cfg.to_json()) == cfg
