import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from sqlalchemy import select

from app.ai import service
from app.ai.claude import ClaudeWriter
from app.ai.inputs import dept_input, input_hash
from app.ai.jobs import AiJobManager
from app.ai.prompts import DEFAULT_TONE, system_prompt
from app.ai.service import AiError, SummaryDraft
from app.db.models import AiSummary, Result

DEPT = "資訊管理學系"


class FakeWriter:
    def __init__(self, fail_on=None, summary="本期摘要"):
        self.calls = []
        self.fail_on = fail_on or set()
        self.summary = summary

    def write(self, system, payload):
        self.calls.append((system, payload))
        key = payload["系所"] if isinstance(payload["系所"], str) else payload["學院"]
        if key in self.fail_on:
            raise AiError("模型拒絕產生這段文字")
        return SummaryDraft(summary=f"{self.summary}-{key}", comparison="無前期資料可比較")


def rows(session):
    session.expire_all()
    return list(session.scalars(select(AiSummary)))


async def test_generate_saves_row(session, done_run):
    row = service.generate(session, done_run, "dept", DEPT, FakeWriter(), DEFAULT_TONE, "claude-opus-5-5")
    saved = rows(session)
    assert len(saved) == 1 and saved[0].id == row.id
    assert saved[0].input_hash == input_hash(dept_input(session, done_run, DEPT))
    assert saved[0].edited is False and saved[0].model == "claude-opus-5-5"
    assert saved[0].summary == f"本期摘要-{DEPT}"


async def test_system_prompt_has_tone_and_rules(session, done_run):
    writer = FakeWriter()
    service.generate(session, done_run, "dept", DEPT, writer, "自訂語氣XYZ", "m")
    system, payload = writer.calls[0]
    assert "自訂語氣XYZ" in system and "只能引用提供的數字" in system
    assert "待人工確認" in system and "只談這個系所" in system
    assert "無前期資料可比較" in system and "比較基準不同" in system
    assert payload["系所"] == DEPT
    assert "只談這個系所" not in system_prompt("college", "x")


async def test_error_keeps_previous_row(session, done_run):
    service.generate(session, done_run, "dept", DEPT, FakeWriter(), DEFAULT_TONE, "m")
    with pytest.raises(AiError):
        service.generate(session, done_run, "dept", DEPT, FakeWriter(fail_on={DEPT}, summary="新"), DEFAULT_TONE, "m")
    saved = rows(session)
    assert len(saved) == 1 and saved[0].summary == f"本期摘要-{DEPT}"


async def test_regenerate_replaces_and_clears_edit(session, done_run):
    first = service.generate(session, done_run, "dept", DEPT, FakeWriter(), DEFAULT_TONE, "m")
    service.save_edit(session, first.id, "改過", None)
    service.generate(session, done_run, "dept", DEPT, FakeWriter(summary="新"), DEFAULT_TONE, "m")
    saved = rows(session)
    assert len(saved) == 1 and saved[0].summary.startswith("新") and saved[0].edited is False


async def test_edit_then_data_change_is_stale(session, done_run):
    row = service.generate(session, done_run, "dept", DEPT, FakeWriter(), DEFAULT_TONE, "m")
    service.save_edit(session, row.id, "人工修改", "比較說明")
    draft = service.fresh_text(session, done_run, "dept", DEPT)
    assert draft == SummaryDraft("人工修改", "比較說明")
    result = session.scalars(select(Result).where(Result.run_id == done_run.id, Result.value.is_not(None))).first()
    result.value = result.value + 1
    session.commit()
    assert service.fresh_text(session, done_run, "dept", DEPT) is None
    saved = rows(session)
    assert len(saved) == 1 and saved[0].edited is True


async def test_targets_depts_then_colleges(session, done_run):
    t = service.targets(session, done_run)
    assert t[0] == ("dept", DEPT)
    assert len(t) >= 2 and all(k == "college" for k, _ in t[1:])


async def test_job_continues_after_one_failure(session, session_factory, done_run):
    writer = FakeWriter(fail_on={DEPT})
    manager = AiJobManager(session_factory, lambda: writer)
    assert manager.state(done_run.id) is None
    assert manager.start(done_run.id) is True
    await manager.tasks[done_run.id]
    state = manager.state(done_run.id)
    targets = service.targets(session, done_run)
    assert state.finished and state.total == len(targets) == state.done
    assert list(state.errors) == [("dept", DEPT)]
    assert state.errors[("dept", DEPT)] == "模型拒絕產生這段文字"
    saved = {(r.kind, r.key) for r in rows(session)}
    assert saved == set(targets) - {("dept", DEPT)}


async def test_job_only_one_target_and_no_double_start(session, session_factory, done_run):
    writer = FakeWriter()
    manager = AiJobManager(session_factory, lambda: writer)
    assert manager.start(done_run.id, only=("dept", DEPT)) is True
    assert manager.start(done_run.id) is False
    await manager.tasks[done_run.id]
    assert [(r.kind, r.key) for r in rows(session)] == [("dept", DEPT)]
    assert manager.state(done_run.id).total == 1


async def test_job_reports_writer_factory_failure(session, session_factory, done_run, caplog):
    def broken():
        raise RuntimeError("boom")

    manager = AiJobManager(session_factory, broken)
    manager.start(done_run.id)
    await manager.tasks[done_run.id]
    state = manager.state(done_run.id)
    targets = service.targets(session, done_run)
    assert state.finished and state.done == state.total == len(targets)
    assert set(state.errors) == set(targets)
    assert all(m == "產生時發生未預期的錯誤" for m in state.errors.values())
    assert "boom" in caplog.text and rows(session) == []


def fake_client(create):
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))


def response(text="", stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])


def status_error(cls, status):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("x", response=httpx.Response(status, request=req), body=None)


def test_claude_writer_maps_errors():
    def raising(exc):
        def create(**kw):
            raise exc
        return create

    cases = [
        (status_error(anthropic.AuthenticationError, 401), "Claude API Key 無效"),
        (status_error(anthropic.RateLimitError, 429), "Claude 額度或速率已達上限，請稍後再試"),
        (anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")), "無法連線到 Claude，請檢查網路"),
        (status_error(anthropic.InternalServerError, 500), "Claude 服務錯誤（500）"),
    ]
    for exc, message in cases:
        with pytest.raises(AiError) as e:
            ClaudeWriter("key", "m", client=fake_client(raising(exc))).write("s", {})
        assert e.value.message == message
    with pytest.raises(AiError) as e:
        ClaudeWriter("key", "m", client=fake_client(lambda **kw: response(stop_reason="refusal"))).write("s", {})
    assert e.value.message == "模型拒絕產生這段文字"


def test_claude_writer_missing_key_does_not_call_client():
    called = []
    writer = ClaudeWriter("", "m", client=fake_client(lambda **kw: called.append(kw)))
    with pytest.raises(AiError) as e:
        writer.write("s", {})
    assert e.value.message == "尚未設定 Claude API Key，請到「設定」頁填寫" and not called


def test_claude_writer_parses_output_and_sends_expected_request():
    seen = {}

    def create(**kw):
        seen.update(kw)
        return response(json.dumps({"summary": "整體穩定", "comparison": None}, ensure_ascii=False))

    draft = ClaudeWriter("key", "claude-test", client=fake_client(create)).write("系統", {"系所": "資管"})
    assert draft == SummaryDraft("整體穩定", None)
    assert seen["model"] == "claude-test" and seen["system"] == "系統"
    assert "temperature" not in seen and "thinking" not in seen
    assert seen["output_config"]["effort"] == "medium"
    assert seen["output_config"]["format"]["type"] == "json_schema"
    assert seen["output_config"]["format"]["schema"]["properties"]["comparison"] == {
        "anyOf": [{"type": "string"}, {"type": "null"}]}
    assert seen["fallbacks"] == "default" and seen["betas"] == ["server-side-fallback-2026-07-01"]
    assert "資管" in seen["messages"][0]["content"]
