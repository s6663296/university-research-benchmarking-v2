"""三份匯入資料：重開程式後仍然有效；被停用（舊版資料庫）的舊資料只供過去的分析使用。"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.db.import_session import retire_current_imports
from app.db.models import FundingImport, MappingVersion, Run, TeacherCountRow, TeacherImport, TeacherOverride
from app.funding.service import effective_imports, import_funding, imports
from app.main import create_app
from app.mapping.service import import_mapping, latest_version
from app.runs.service import create_run
from app.teachers.moe import import_moe, latest_import
from app.web.dashboard import pipeline
from tests.test_web_runs_new import latest_run_id
from tests.test_web_runs_new import wait_done as _wait
from tests.funding_builder import build_funding, sample_funding
from tests.mapping_builder import sample_mapping
from tests.moe_builder import sample_moe
from tests.run_helpers import TODAY, FakeElsevier, default_config, make_run, seed

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def funding_file(year):
    return build_funding([(year, "國科會計畫", "管理學院", "資訊管理學系", 1)])


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_retired_imports_are_no_longer_current(session):
    seed(session)
    import_funding(session, "f.xlsx", sample_funding())
    retire_current_imports(session)
    assert latest_version(session) is None and latest_import(session) is None and imports(session) == []
    assert effective_imports(session) == {}


def test_first_three_stages_return_to_not_imported(session):
    seed(session)
    import_funding(session, "f.xlsx", sample_funding())
    retire_current_imports(session)
    first_three = pipeline(session)[:3]
    assert [s.summary for s in first_three] == ["尚未匯入", "尚未匯入", "略過"] and all(s.state == "todo" for s in first_three)


def test_same_mapping_file_comes_back_without_new_version(session):
    first, _ = import_mapping(session, "m.xlsx", sample_mapping())
    retire_current_imports(session)
    again, created = import_mapping(session, "改名.xlsx", sample_mapping())
    assert again.id == first.id and created and count(session, MappingVersion) == 1
    assert latest_version(session).id == first.id and again.filename == "改名.xlsx"


def test_same_teacher_file_comes_back_without_duplicating_rows(session):
    first = import_moe(session, "moe.xlsx", sample_moe())
    rows = count(session, TeacherCountRow)
    session.add(TeacherOverride(school="中央", dept_zh="資管", academic_year=114, count=9, reason="人工", created_at=datetime.now()))
    session.commit()
    retire_current_imports(session)
    again = import_moe(session, "moe.xlsx", sample_moe())
    assert again.id == first.id and latest_import(session).id == first.id
    assert count(session, TeacherImport) == 1 and count(session, TeacherCountRow) == rows
    assert count(session, TeacherOverride) == 1


def test_different_teacher_file_after_restart_is_a_new_import(session):
    first = import_moe(session, "moe.xlsx", sample_moe())
    retire_current_imports(session)
    first.sha256 = "changed"
    session.commit()
    assert import_moe(session, "new.xlsx", sample_moe()).id != first.id


def test_same_funding_file_comes_back(session):
    first, _ = import_funding(session, "f.xlsx", sample_funding())
    retire_current_imports(session)
    again, _ = import_funding(session, "f2.xlsx", sample_funding())
    assert again.id == first.id and [i.id for i in imports(session)] == [first.id]
    assert count(session, FundingImport) == 1


def test_retiring_again_keeps_the_first_retired_time(session):
    imp, _ = import_funding(session, "f.xlsx", sample_funding())
    retire_current_imports(session)
    stamp = imp.retired_at
    retire_current_imports(session)
    session.refresh(imp)
    assert imp.retired_at == stamp


def test_past_run_keeps_old_funding_but_new_run_does_not_see_it(session):
    old_run = make_run(session)
    import_funding(session, "113.xlsx", funding_file(113))
    retire_current_imports(session)
    retired_at = session.scalars(select(FundingImport)).first().retired_at
    old_run.created_at = retired_at - timedelta(days=1)
    import_funding(session, "114.xlsx", funding_file(114))
    seed(session)
    new_run = create_run(session, default_config(), today=TODAY)
    new_run.created_at = retired_at + timedelta(seconds=1)
    session.commit()
    assert list(effective_imports(session, [113, 114], run=old_run)) == [113, 114]
    assert list(effective_imports(session, [113, 114], run=new_run)) == [114]
    assert list(effective_imports(session, [113, 114])) == [114]


def test_imports_are_remembered_after_restart(settings):
    with TestClient(create_app(settings)) as first:
        first.post("/mapping", files={"file": ("m.xlsx", sample_mapping(), XLSX)})
        first.post("/teachers/import", files={"file": ("moe.xlsx", sample_moe(), XLSX)})
        first.post("/funding/import", files={"file": ("f.xlsx", sample_funding(), XLSX)})
        assert first.get("/pipeline").text.count(">尚未匯入<") == 0
    with TestClient(create_app(settings)) as second:
        assert second.get("/pipeline").text.count(">尚未匯入<") == 0
        with second.app.state.session_factory() as s:
            assert latest_version(s) is not None and latest_import(s) is not None and imports(s)
            assert count(s, MappingVersion) == 1 and count(s, TeacherImport) == 1 and count(s, FundingImport) == 1


def test_upgrade_restores_imports_retired_at_last_start(tmp_path):
    """舊版每次啟動都會停用三份匯入資料；升級時把最後一次被停用的那批恢復成目前資料。"""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.orm import Session as OrmSession

    from app.db.session import ALEMBIC_DIR, init_db, make_engine

    engine = make_engine(tmp_path / "app.db")
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0008_ai_summaries")
    with OrmSession(engine) as s:
        import_funding(s, "old.xlsx", funding_file(112))
        retire_current_imports(s)                       # 更早一次啟動
        seed(s)
        import_funding(s, "new.xlsx", funding_file(113))
        retire_current_imports(s)                       # 最後一次啟動
        assert latest_version(s) is None and imports(s) == []
    init_db(engine)
    with OrmSession(engine) as s:
        assert latest_version(s) is not None and latest_import(s) is not None
        assert [f.filename for f in imports(s)] == ["new.xlsx"]   # 更早被停用的不恢復


@pytest.fixture
def missing_funding(tmp_path):
    s = Settings(_env_file=None, elsevier_api_key="testkey", data_dir=tmp_path, env_path=tmp_path / ".env")
    with TestClient(create_app(s)) as c:
        c.post("/mapping", files={"file": ("m.xlsx", sample_mapping(), XLSX)})
        c.post("/teachers/import", files={"file": ("moe.xlsx", sample_moe(), XLSX)})
        yield c


def test_analysis_form_does_not_require_funding(missing_funding):
    text = missing_funding.get("/runs/new").text
    assert 'id="run-form"' in text and 'href="/funding"' not in text.split("<main>")[1]


def test_analysis_runs_without_funding(missing_funding, respx_mock):
    """沒匯入經分表也能分析，報告就略過計畫經費頁。"""
    FakeElsevier().install(respx_mock)
    r = missing_funding.post("/runs", data={"year_from": "2021", "year_to": "2025", "ncu_depts": ["資訊管理學系"],
                                            "comparisons": ["benchmark"], "schools": ["台大"], "academic_year": "114"},
                             follow_redirects=False)
    assert r.status_code == 303
    run_id = wait_done(missing_funding)
    assert missing_funding.get(f"/runs/{run_id}/pptx.zip").status_code == 200


def wait_done(client):
    run_id = latest_run_id(client)
    assert _wait(client, run_id) == "done"
    return run_id
