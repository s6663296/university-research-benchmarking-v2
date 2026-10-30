import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, elsevier_api_key="", data_dir=tmp_path)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c
