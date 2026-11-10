from pathlib import Path

from sqlalchemy import select

from app.db.models import Anomaly, Run, RunUnit
from app.funding.service import import_funding
from app.reports.exports import record_export
from app.web.dashboard import pipeline
from tests.funding_builder import build_funding
from tests.run_helpers import make_run


def stages(session):
    """以資料庫中最新的分析當作背景程式正在處理的分析。"""
    run = session.scalars(select(Run).order_by(Run.id.desc()).limit(1)).first()
    running = run is not None and run.status in ("pending", "running")
    return {s.key: s for s in pipeline(session, run.id if run else None, running)}


def set_units(session, run_id, *statuses, eid_count=None):
    """依序設定分析中各單位的狀態；statuses 不足時其餘維持原狀。"""
    rus = session.scalars(select(RunUnit).where(RunUnit.run_id == run_id).order_by(RunUnit.id)).all()
    for ru, st in zip(rus, statuses):
        ru.status = st
        ru.eid_count = eid_count if st in ("scival", "failed") else (10 if st == "done" else None)
    session.commit()
    return rus


def test_empty_database(session):
    s = stages(session)
    assert list(s) == ["mapping", "teachers", "funding", "run", "fetch", "review", "output"]
    assert s["mapping"].state == "todo" and s["mapping"].href == "/mapping"
    assert s["teachers"].state == "todo" and s["teachers"].href == "/teachers"
    assert s["funding"].state == "todo" and s["funding"].summary == "尚未匯入" and s["funding"].href == "/funding"
    assert s["run"].state == "todo" and s["run"].href == "/runs/new"
    assert all(s[k].state == "todo" and s[k].href is None for k in ("fetch", "review", "output"))


def test_funding_stage_shows_years_and_latest_file(session):
    import_funding(session, "113.xlsx", build_funding([(113, "國科會計畫", "管理學院", "資訊管理學系", 1)]))
    import_funding(session, "114.xlsx", build_funding([(114, "國科會計畫", "管理學院", "資訊管理學系", 2)]))
    f = stages(session)["funding"]
    assert (f.state, f.label, f.summary, f.detail) == ("done", "計畫經費統計表檢查", "113～114 年度", "114.xlsx")


def test_running_run_shows_fetch_progress(session):
    run = make_run(session)
    session.get(Run, run.id).status = "running"
    rus = set_units(session, run.id, "done", "scival", "scopus", eid_count=5)
    s = stages(session)
    assert s["mapping"].state == "done"
    assert s["run"].state == "done" and s["run"].href == f"/runs/{run.id}"
    assert s["fetch"].state == "active" and s["fetch"].summary == ""
    assert s["fetch"].progress == (f"Scopus 2／{len(rus)}", f"SciVal 1／{len(rus)}")
    assert s["fetch"].href == f"/runs/{run.id}/fetch"
    assert s["review"].state == "todo"


def test_paused_units_past_scopus_still_count(session):
    """暫停後退回 pending 但已有 eid_count 的單位，Scopus 這步仍算完成。"""
    run = make_run(session)
    session.get(Run, run.id).status = "interrupted"
    rus = set_units(session, run.id, *["done"] * 99)
    rus[0].status = "pending"  # Scopus 已完成、SciVal 未完成
    session.commit()
    s = stages(session)
    assert s["fetch"].state == "warn" and s["fetch"].summary == "已中斷"
    assert s["fetch"].progress == (f"Scopus {len(rus)}／{len(rus)}", f"SciVal {len(rus) - 1}／{len(rus)}")


def test_stages_follow_background_not_db_status(session):
    """資料庫還寫著執行中，但背景已沒在跑（例如被中止）時，不顯示進行中。"""
    run = make_run(session)
    session.get(Run, run.id).status = "running"
    set_units(session, run.id, "done", "scival", eid_count=5)
    s = {x.key: x for x in pipeline(session, run.id, running=False)}
    assert s["fetch"].state == "warn" and s["fetch"].summary.startswith("已中斷")
    assert s["review"].state == "todo"


def test_teachers_stage_active_while_moe_downloading(session):
    make_run(session)
    s = {x.key: x for x in pipeline(session, None, moe_running=True)}
    assert s["teachers"].state == "active" and s["teachers"].summary == "從教育部下載中"


def test_failures_from_scopus_and_scival_are_added_up(session):
    run = make_run(session)
    session.get(Run, run.id).status = "running"
    rus = session.scalars(select(RunUnit).where(RunUnit.run_id == run.id)).all()
    for ru in rus:
        ru.status, ru.eid_count = "done", 10
    rus[0].status, rus[0].eid_count = "failed", None  # Scopus 就失敗
    rus[1].status, rus[1].eid_count = "failed", 5  # Scopus 成功、SciVal 失敗
    session.commit()
    s = stages(session)
    n = len(rus)
    assert s["fetch"].state == "error" and s["fetch"].summary == "2 個失敗"
    assert s["fetch"].progress == (f"Scopus {n - 1}／{n}", f"SciVal {n - 2}／{n}")
    assert s["review"].state == "active" and s["review"].summary == "計算中"  # 全部抓完、分析仍在執行


def test_interrupted_run_is_warning(session):
    run = make_run(session)
    session.get(Run, run.id).status = "interrupted"
    rus = set_units(session, run.id, "done")
    s = stages(session)
    assert s["fetch"].state == "warn" and "中斷" in s["fetch"].summary
    assert s["fetch"].progress == (f"Scopus 1／{len(rus)}", f"SciVal 1／{len(rus)}")


def test_run_level_error_shown_on_review(session):
    run = make_run(session)
    r = session.get(Run, run.id)
    r.status, r.error = "failed", "RuntimeError: 壞掉了"
    set_units(session, run.id, *["done"] * 20)
    s = stages(session)
    assert s["review"].state == "error" and "壞掉了" in s["review"].summary
    assert s["review"].href == f"/runs/{run.id}"


def test_done_run_review_and_output(session, tmp_path):
    run = make_run(session)
    session.get(Run, run.id).status = "done"
    n = len(set_units(session, run.id, *["done"] * 20))
    session.add(Anomaly(run_id=run.id, kind="zero_results", detail="x"))
    session.add(Anomaly(run_id=run.id, kind="zero_results", detail="y", status="confirmed"))
    session.commit()
    s = stages(session)
    assert s["fetch"].state == "done" and s["fetch"].summary == ""
    assert s["fetch"].progress == (f"Scopus {n}／{n}", f"SciVal {n}／{n}")
    assert s["review"].label == "計算與異常確認"
    assert s["review"].state == "warn" and s["review"].summary == "1 項待確認"
    assert s["review"].href == f"/runs/{run.id}?ind=anomalies"
    assert s["output"].state == "todo" and s["output"].href == f"/runs/{run.id}?ind=downloads"

    for a in session.scalars(select(Anomaly)):
        a.status = "confirmed"
    record_export(session, run.id, "xlsx", Path(tmp_path / "a.xlsx"))
    s = stages(session)
    assert s["review"].state == "done"
    assert s["output"].state == "done" and s["output"].summary == "已產出 1 份"


def test_shows_run_given_by_background(session):
    first = make_run(session)
    from app.runs.service import create_run
    from tests.run_helpers import TODAY, default_config
    create_run(session, default_config(), today=TODAY)
    s = {x.key: x for x in pipeline(session, first.id)}
    assert s["run"].href == f"/runs/{first.id}"


def test_no_background_run_resets_pipeline(session):
    run = make_run(session)
    session.get(Run, run.id).status = "done"
    session.commit()
    s = {x.key: x for x in pipeline(session, None)}
    assert s["mapping"].state == "done"  # 前面的檢查照常
    assert s["run"].state == "todo" and s["run"].href == "/runs/new"
    assert all(s[k].state == "todo" for k in ("fetch", "review", "output"))


def test_restart_resets_pipeline(settings, web_done_run):
    from fastapi.testclient import TestClient

    from app.main import create_app
    with TestClient(create_app(settings)) as c:
        text = c.get("/").text.split('id="pipeline"')[1]
    assert f'href="/runs/{web_done_run}?ind=downloads"' not in text and 'href="/runs/new"' in text


def test_resumed_run_after_restart_shows_on_home(settings, web_done_run, respx_mock):
    """重啟後接續先前中斷的分析，首頁要跟著背景程式顯示它，而不是停在尚未開始。"""
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.run_helpers import FakeElsevier
    with TestClient(create_app(settings)) as c:
        with c.app.state.session_factory() as s:
            s.get(Run, web_done_run).status = "interrupted"
            s.commit()
        manager = c.app.state.run_manager
        manager.is_running = lambda run_id: True  # 背景作業仍在跑
        manager.current_run_id = web_done_run
        text = c.get("/").text.split('id="pipeline"')[1]
        assert f'href="/runs/{web_done_run}"' in text and f'action="/runs/{web_done_run}/stop"' in text
        assert "每 2 秒更新" in text


def test_home_shows_pipeline_and_polls_while_running(client, web_done_run):
    r = client.get("/")
    assert 'id="pipeline"' in r.text and f'href="/runs/{web_done_run}?ind=downloads"' in r.text
    assert "hx-trigger" not in r.text.split('id="pipeline"')[1].split(">")[0]
    with client.app.state.session_factory() as s:
        s.get(Run, web_done_run).status = "running"
        s.commit()
    client.app.state.run_manager.is_running = lambda run_id: True
    r = client.get("/pipeline")
    assert r.status_code == 200 and 'hx-get="/pipeline"' in r.text


def test_fetch_stage_shows_only_scopus_and_scival_lines_when_done(client, web_done_run):
    with client.app.state.session_factory() as s:
        n = len(s.scalars(select(RunUnit).where(RunUnit.run_id == web_done_run)).all())
    text = client.get("/pipeline").text
    assert text.index(f"Scopus {n}／{n}") < text.index(f"SciVal {n}／{n}")
    fetch_stage = text.split("取指標</span>")[1].split("</li>")[0]
    assert "stage-summary" not in fetch_stage and "個單位完成" not in text


def test_stage_labels_break_at_meaningful_points(client):
    text = client.get("/pipeline").text
    assert 'Scopus／SciVal <span class="stage-label-tail">取指標</span>' in text
    assert '計算與異常<span class="stage-label-tail">確認</span>' in text
    assert '計畫經費統計表<span class="stage-label-tail">檢查</span>' in text
    assert '<span class="stage-label">建立分析</span>' in text


def test_mapping_and_teacher_details(session):
    make_run(session)
    s = stages(session)
    assert s["mapping"].summary == "m.xlsx" and s["mapping"].detail.startswith("匯入 ")
    years = f"{s['teachers'].summary}{s['teachers'].detail}"
    assert "學年度" in years and (s["teachers"].state == "warn" or s["teachers"].detail == "moe.xlsx")


def test_home_cards_moved_into_pipeline(client):
    text = client.get("/").text
    assert "<h2>Mapping 版本</h2>" not in text and "<h2>教師數資料</h2>" not in text
    assert "尚未匯入" in text.split('id="pipeline"')[1]


def test_home_has_start_button_and_settings_icon(client):
    text = client.get("/").text
    assert 'action="/runs/start"' in text and ">▶ 開始分析</button>" in text
    assert 'href="/runs/new?reuse=1"' in text and 'title="分析設定"' in text
