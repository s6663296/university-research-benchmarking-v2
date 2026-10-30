import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db.session import init_db, make_engine, make_session_factory
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, elsevier_api_key="", data_dir=tmp_path, env_path=tmp_path / ".env")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def engine(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)


@pytest.fixture
def session(session_factory):
    with session_factory() as s:
        yield s


@pytest.fixture
async def done_run(session, session_factory, respx_mock):
    """已執行完成的分析（資管：對標台大、政治；院內：管理學院 3 系所）。"""
    from app.runs.executor import execute_run
    from tests.run_helpers import FakeElsevier, fake_client, make_run

    run = make_run(session)
    FakeElsevier().install(respx_mock)
    await execute_run(run.id, session_factory, fake_client())
    session.expire_all()
    return run


@pytest.fixture
def web_done_run(client, respx_mock):
    """在網頁 client 的資料庫中建立已完成的分析，回傳 run id。"""
    import asyncio

    from app.runs.executor import execute_run
    from tests.run_helpers import FakeElsevier, fake_client, make_run

    factory = client.app.state.session_factory
    with factory() as s:
        run_id = make_run(s).id
    FakeElsevier().install(respx_mock)
    asyncio.run(execute_run(run_id, factory, fake_client()))
    return run_id
