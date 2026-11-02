from sqlalchemy import select

from app.db.models import Anomaly, RunUnit, Unit


def test_detail_page_marks_ncu(client, web_done_run):
    text = client.get(f"/runs/{web_done_run}", params={"ind": "total"}).text
    assert 'class="ncu"' in text and "資訊管理學系-中央" in text and "對標學校比較" in text
    assert "管理學院內系所比較" in text


def test_detail_page_summary(client, web_done_run):
    text = client.get(f"/runs/{web_done_run}").text
    assert "論文指標排名" in text and "對標學校: 3間" in text


def test_missing_teacher_shows_pending(client, web_done_run):
    from app.runs.executor import recompute
    with client.app.state.session_factory() as s:
        ru = s.scalars(select(RunUnit).join(Unit, Unit.id == RunUnit.unit_id)
                       .where(RunUnit.run_id == web_done_run, Unit.school == "台大")).one()
        ru.teacher_count = None
        s.commit()
        recompute(s, web_done_run)
    text = client.get(f"/runs/{web_done_run}", params={"ind": "total"}).text
    assert "待確認" in text
    assert "缺教師數" in client.get(f"/runs/{web_done_run}", params={"ind": "anomalies"}).text


def test_resolve_anomaly(client, web_done_run):
    with client.app.state.session_factory() as s:
        ru = s.scalars(select(RunUnit).where(RunUnit.run_id == web_done_run)).first()
        s.add(Anomaly(run_id=web_done_run, unit_id=ru.unit_id, kind="zero_results", detail="測試", status="open"))
        s.commit()
        aid = s.scalars(select(Anomaly.id).where(Anomaly.run_id == web_done_run)).first()
    r = client.post(f"/runs/{web_done_run}/anomalies/{aid}", data={"note": "人工確認無誤"}, follow_redirects=True)
    assert "已確認" in r.text and "人工確認無誤" in r.text


def test_notes_saved(client, web_done_run):
    r = client.post(f"/runs/{web_done_run}/notes", data={"extra": "本次為試辦"}, follow_redirects=True)
    assert "本次為試辦" in r.text and "資料取得日期" in r.text


def test_unknown_run_404(client):
    assert client.get("/runs/999").status_code == 404
