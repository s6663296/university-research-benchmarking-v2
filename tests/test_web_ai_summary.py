import io
import time

import pytest
from pptx import Presentation
from sqlalchemy import select

from app.ai.service import AiError
from app.db.models import AiSummary, Result, Run
from tests.test_ai_service import FakeWriter

DEPT = "資訊管理學系"
STALE = "資料已更新，總結可能過時"


@pytest.fixture
def run_id(client, web_done_run):
    client.app.state.ai_jobs.writer_factory = lambda: FakeWriter()
    return web_done_run


def wait_job(client, run_id):
    jobs = client.app.state.ai_jobs
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = jobs.state(run_id)
        if state is not None and state.finished:
            return state
        time.sleep(0.02)
    raise AssertionError("AI job did not finish")


def generate(client, run_id):
    assert client.post(f"/runs/{run_id}/ai/generate", follow_redirects=False).status_code == 303
    return wait_job(client, run_id)


def slide_texts(content: bytes) -> list[str]:
    prs = Presentation(io.BytesIO(content))
    return ["\n".join(sh.text_frame.text for sh in s.shapes if sh.has_text_frame) for s in prs.slides]


def college_of(client, run_id):
    with client.app.state.session_factory() as s:
        return s.scalars(select(AiSummary.key).where(AiSummary.kind == "college")).first()


def test_ai_tab_lists_targets(client, run_id):
    html = client.get(f"/runs/{run_id}?ind=ai").text
    assert DEPT in html and "管理學院" in html and "產生 AI 總結" in html
    assert "自動（上一期）" in html


def test_generate_then_download_has_ai_page(client, run_id):
    generate(client, run_id)
    r = client.get(f"/runs/{run_id}/pptx/{DEPT}")
    assert r.status_code == 200
    assert "總結與建議" in slide_texts(r.content)[-1]


def test_stale_summary_not_in_download(client, run_id):
    generate(client, run_id)
    with client.app.state.session_factory() as s:
        result = s.scalars(select(Result).where(Result.run_id == run_id, Result.value.is_not(None))).first()
        result.value += 1
        s.commit()
    r = client.get(f"/runs/{run_id}/pptx/{DEPT}")
    assert r.status_code == 200
    assert all("總結與建議" not in t for t in slide_texts(r.content))
    assert STALE in client.get(f"/runs/{run_id}?ind=ai").text


def test_edit_is_used_in_download(client, run_id):
    generate(client, run_id)
    with client.app.state.session_factory() as s:
        sid = s.scalars(select(AiSummary.id).where(AiSummary.key == DEPT)).first()
    r = client.post(f"/runs/{run_id}/ai/summaries/{sid}", data={"summary": "人工改過的總結", "comparison": ""},
                    follow_redirects=False)
    assert r.status_code == 303
    last = slide_texts(client.get(f"/runs/{run_id}/pptx/{DEPT}").content)[-1]
    assert "人工改過的總結" in last
    assert "已修改" in client.get(f"/runs/{run_id}?ind=ai").text


def test_college_download_requires_summary(client, run_id):
    with client.app.state.session_factory() as s:
        from app.ai.service import targets
        college = next(k for kind, k in targets(s, s.get(Run, run_id)) if kind == "college")
    assert client.get(f"/runs/{run_id}/college-summary/{college}").status_code == 409
    assert client.get(f"/runs/{run_id}/college-summary.zip").status_code == 409
    generate(client, run_id)
    r = client.get(f"/runs/{run_id}/college-summary/{college}")
    assert r.status_code == 200 and "presentationml" in r.headers["content-type"]
    assert client.get(f"/runs/{run_id}/college-summary.zip").headers["content-type"] == "application/zip"


def test_baseline_override_marks_stale(client, run_id):
    factory = client.app.state.session_factory
    with factory() as s:
        run = s.get(Run, run_id)
        other = Run(**{c.name: getattr(run, c.name) for c in Run.__table__.columns if c.name != "id"})
        s.add(other)
        s.commit()
        other_id = other.id
    generate(client, run_id)
    assert STALE not in client.get(f"/runs/{run_id}?ind=ai").text
    r = client.post(f"/runs/{run_id}/ai/baseline", data={"baseline": str(other_id)}, follow_redirects=False)
    assert r.status_code == 303
    assert STALE in client.get(f"/runs/{run_id}?ind=ai").text
    with factory() as s:
        assert s.get(Run, run_id).ai_baseline_run_id == other_id
    client.post(f"/runs/{run_id}/ai/baseline", data={"baseline": ""})
    with factory() as s:
        assert s.get(Run, run_id).ai_baseline_run_id is None


def test_missing_key_shows_message(client, web_done_run):
    run_id = web_done_run
    message = "尚未設定 Claude API Key，請到「設定」頁填寫"

    class NoKey:
        def write(self, system, payload):
            raise AiError(message)

    client.app.state.ai_jobs.writer_factory = lambda: NoKey()
    generate(client, run_id)
    assert message in client.get(f"/runs/{run_id}/ai/progress").text
    assert client.get(f"/runs/{run_id}/pptx/{DEPT}").status_code == 200


def test_routes_404_and_409(client, run_id):
    assert client.post("/runs/9999/ai/generate").status_code == 404
    assert client.get("/runs/9999/ai/progress").status_code == 404
    assert client.post(f"/runs/{run_id}/ai/summaries/9999", data={"summary": "x"}).status_code == 404
    assert client.post(f"/runs/{run_id}/ai/generate/dept/不存在").status_code == 404
    with client.app.state.session_factory() as s:
        s.get(Run, run_id).status = "running"
        s.commit()
    assert client.post(f"/runs/{run_id}/ai/generate").status_code == 409


def test_fresh_text_failure_does_not_break_download(client, run_id, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr("app.web.routes.runs.fresh_text", boom)
    assert client.get(f"/runs/{run_id}/pptx/{DEPT}").status_code == 200
    assert client.get(f"/runs/{run_id}/pptx.zip").status_code == 200


@pytest.mark.parametrize("value", ["", "   \n "])
def test_save_empty_summary_rejected(client, run_id, value):
    generate(client, run_id)
    with client.app.state.session_factory() as s:
        row = s.scalars(select(AiSummary)).first()
        sid, old = row.id, row.summary
    r = client.post(f"/runs/{run_id}/ai/summaries/{sid}", data={"summary": value}, follow_redirects=False)
    assert r.status_code == 400
    with client.app.state.session_factory() as s:
        assert s.get(AiSummary, sid).summary == old
