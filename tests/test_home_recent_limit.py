from app.runs.service import create_run
from tests.run_helpers import TODAY, default_config, make_run


def test_home_only_shows_three_latest_runs_but_history_keeps_all(client):
    with client.app.state.session_factory() as session:
        make_run(session)
        for _ in range(3):
            create_run(session, default_config(), today=TODAY)

    home = client.get("/").text
    recent = home.split("<h2>最近分析</h2>", 1)[1]
    assert all(f'href="/runs/{run_id}"' in recent for run_id in (2, 3, 4))
    assert 'href="/runs/1"' not in recent
    assert 'href="/runs">全部</a>' in home
    assert ">#1</a>" in client.get("/runs").text


def test_interrupted_runs_offer_resume_on_home_and_history(client):
    with client.app.state.session_factory() as session:
        stopped = make_run(session)
        stopped.status = "stopped"
        done = create_run(session, default_config(), today=TODAY)
        done.status = "done"
        session.commit()
        stopped_id, done_id = stopped.id, done.id
    for path in ("/", "/runs"):
        text = client.get(path).text
        assert f'action="/runs/{stopped_id}/resume?home=1"' in text
        assert f'action="/runs/{done_id}/resume' not in text
