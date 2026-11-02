from app.runs.service import create_run
from tests.run_helpers import TODAY, default_config, make_run


def test_home_only_shows_three_latest_runs_but_history_keeps_all(client):
    with client.app.state.session_factory() as session:
        make_run(session)
        for _ in range(3):
            create_run(session, default_config(), today=TODAY)

    home = client.get("/").text
    recent = home.split("<h2>最近分析</h2>", 1)[1]
    assert all(f">#{run_id}</a>" in recent for run_id in (2, 3, 4))
    assert ">#1</a>" not in recent
    assert 'href="/runs">全部版本紀錄</a>' in recent
    assert ">#1</a>" in client.get("/runs").text
