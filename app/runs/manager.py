"""背景任務：在同一行程內以 asyncio 執行分析（設計 §6.2）。"""

import asyncio
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Run, RunUnit
from app.elsevier.client import ElsevierClient
from app.runs.executor import execute_run


def mark_interrupted(session: Session) -> int:
    """程式重新啟動時，把未完成的分析標為中斷。"""
    runs = session.scalars(select(Run).where(Run.status.in_(("running", "pending")))).all()
    for run in runs:
        run.status = "interrupted"
    for ru in session.scalars(select(RunUnit).where(RunUnit.status == "fetching")):
        ru.status = "pending"
    session.commit()
    return len(runs)


class RunManager:
    def __init__(self, session_factory: sessionmaker[Session], client_factory: Callable[[], ElsevierClient]):
        self.session_factory = session_factory
        self.client_factory = client_factory
        self.tasks: dict[int, asyncio.Task] = {}

    def is_running(self, run_id: int) -> bool:
        task = self.tasks.get(run_id)
        return task is not None and not task.done()

    def _launch(self, run_id: int, **kwargs) -> bool:
        """已有作業在執行時不重複啟動，回傳 False。"""
        if self.is_running(run_id):
            return False

        async def job():
            client = self.client_factory()
            try:
                await execute_run(run_id, self.session_factory, client, **kwargs)
            finally:
                await client.aclose()

        self.tasks[run_id] = asyncio.create_task(job())
        return True

    def start(self, run_id: int) -> bool:
        return self._launch(run_id)

    def resume(self, run_id: int) -> bool:
        return self._launch(run_id, only_unfinished=True)

    def refetch_unit(self, run_id: int, unit_id: int) -> bool:
        return self._launch(run_id, unit_ids={unit_id}, use_cache=False)
