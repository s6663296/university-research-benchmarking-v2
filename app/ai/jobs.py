"""背景產生 AI 摘要：逐筆呼叫，單筆失敗記錄後繼續。"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session, sessionmaker

from app.ai import service
from app.ai.service import AiError, SummaryWriter
from app.db.models import Run
from app.db.settings_store import get_setting


@dataclass
class JobState:
    total: int
    done: int = 0
    errors: dict[tuple[str, str], str] = field(default_factory=dict)
    finished: bool = False


class AiJobManager:
    def __init__(self, session_factory: sessionmaker[Session], writer_factory: Callable[[], SummaryWriter]):
        self.session_factory = session_factory
        self.writer_factory = writer_factory
        self.tasks: dict[int, asyncio.Task] = {}
        self.states: dict[int, JobState] = {}

    def state(self, run_id: int) -> JobState | None:
        return self.states.get(run_id)

    def is_running(self, run_id: int) -> bool:
        task = self.tasks.get(run_id)
        return task is not None and not task.done()

    def start(self, run_id: int, only: tuple[str, str] | None = None) -> bool:
        """已在跑回 False。"""
        if self.is_running(run_id):
            return False
        with self.session_factory() as session:
            run = session.get(Run, run_id)
            todo = [only] if only else service.targets(session, run)
            tone, model = get_setting(session, "ai_tone_prompt"), get_setting(session, "ai_model")
        state = self.states[run_id] = JobState(total=len(todo))
        self.tasks[run_id] = asyncio.create_task(self._run(run_id, todo, tone, model, state))
        return True

    async def _run(self, run_id: int, todo: list[tuple[str, str]], tone: str, model: str, state: JobState) -> None:
        try:
            writer = self.writer_factory()
            for kind, key in todo:
                try:
                    await asyncio.to_thread(self._one, run_id, kind, key, writer, tone, model)
                except AiError as e:
                    state.errors[(kind, key)] = e.message
                except Exception:  # 單筆非預期錯誤也不能中斷其餘項目
                    state.errors[(kind, key)] = "產生時發生未預期的錯誤"
                state.done += 1
        finally:
            state.finished = True

    def _one(self, run_id: int, kind: str, key: str, writer: SummaryWriter, tone: str, model: str) -> None:
        with self.session_factory() as session:
            service.generate(session, session.get(Run, run_id), kind, key, writer, tone, model)
