import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db.session import init_db, make_engine, make_session_factory
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, elsevier_api_key="", data_dir=tmp_path)


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
