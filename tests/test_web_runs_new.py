import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.db.models import Run
from app.main import create_app
from tests.elsevier_fixtures import SCOPUS_URL
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
        yield c


def run_count(client):
    with client.app.state.session_factory() as s:
        return s.scalar(select(func.count()).select_from(Run))


def wait_done(client, run_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        with client.app.state.session_factory() as s:
            if s.get(Run, run_id).status in ("done", "failed"):
                return s.get(Run, run_id).status
        time.sleep(0.05)
    raise AssertionError("分析沒有在時間內完成")


def test_new_run_defaults(seeded):
    text = seeded.get("/runs/new").text
    assert 'name="year_from" value="2021"' in text and 'name="year_to" value="2025"' in text
    assert 'value="台大" checked' in text and 'value="政治" checked' in text


def test_preview_shows_queries(seeded):
    text = seeded.post("/runs/preview", data=FORM).text
    assert 'AFFIL(&#34;Department of Information Management, National Taiwan University&#34;)' in text
    assert "16" in text  # 台大資管教師數


def test_preview_college_selects_all_depts(seeded):
    form = {**FORM, "ncu_depts": [], "colleges": ["管理學院"]}
    assert "Graduate Institute of Accounting, National Central University" in seeded.post("/runs/preview", data=form).text


def test_preview_shows_blocking_errors(seeded):
    assert "今年" in seeded.post("/runs/preview", data={**FORM, "year_to": "2026"}).text


def test_create_blocked_when_offline(seeded, respx_mock):
    respx_mock.get(url__regex=SCOPUS_URL).mock(side_effect=httpx.ConnectError("x"))
    r = seeded.post("/runs", data=FORM)
    assert "無法連線" in r.text and run_count(seeded) == 0


def test_create_runs_in_background(seeded, respx_mock):
    FakeElsevier().install(respx_mock)
    r = seeded.post("/runs", data=FORM, follow_redirects=False)
    assert r.status_code == 303
    run_id = int(r.headers["location"].rsplit("/", 1)[1])
    assert wait_done(seeded, run_id) == "done"
    frag = seeded.get(f"/runs/{run_id}/progress")
    assert 'every 1s' not in frag.text and frag.headers.get("HX-Refresh") == "true"
    assert "every 1s" not in seeded.get(f"/runs/{run_id}").text


def test_reuse_last_config(seeded, respx_mock):
    FakeElsevier().install(respx_mock)
    r = seeded.post("/runs", data={**FORM, "schools": ["台大"]}, follow_redirects=False)
    wait_done(seeded, int(r.headers["location"].rsplit("/", 1)[1]))
    text = seeded.get("/runs/new", params={"reuse": 1}).text
    assert 'value="台大" checked' in text and 'value="政治" checked' not in text
