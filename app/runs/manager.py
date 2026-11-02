"""背景任務：在同一行程內以 asyncio 執行分析（設計 §6.2）。"""

import asyncio
from collections.abc import Callable
from contextlib import suppress

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Run, RunUnit
from app.elsevier.client import ElsevierClient
from app.runs.executor import FETCHING, execute_run


def mark_interrupted(session: Session) -> int:
    """程式重新啟動時，把未完成的分析標為中斷。"""
    runs = session.scalars(select(Run).where(Run.status.in_(("running", "pending")))).all()
    for run in runs:
        run.status = "interrupted"
    for ru in session.scalars(select(RunUnit).where(RunUnit.status.in_(FETCHING))):
        ru.status = "pending"
    session.commit()
    return len(runs)


class RunManager:
    def __init__(self, session_factory: sessionmaker[Session], client_factory: Callable[[], ElsevierClient]):
        self.session_factory = session_factory
        self.client_factory = client_factory
        self.tasks: dict[int, asyncio.Task] = {}
        self.current_run_id: int | None = None  # 這個行程最後啟動（開始／接續／重抓）的分析，首頁儀表板顯示它

    def is_running(self, run_id: int) -> bool:
        task = self.tasks.get(run_id)
        return task is not None and not task.done()

    def any_running(self) -> bool:
        return any(not t.done() for t in self.tasks.values())

    async def _cancel(self, run_id: int) -> bool:
        task = self.tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        return True

    def _set_status(self, run_id: int, status: str, from_status: tuple[str, ...]) -> bool:
        """抓到一半的單位退回 pending，已完成的保留。"""
        with self.session_factory() as session:
            run = session.get(Run, run_id)
            if run is None or run.status not in from_status:
                return False
            run.status = status
            for ru in session.scalars(select(RunUnit).where(
                    RunUnit.run_id == run_id, RunUnit.status.in_(FETCHING))):
                ru.status = "pending"
            session.commit()
        return True

    async def pause(self, run_id: int) -> bool:
        """暫停：停下背景作業，之後可從中斷處繼續。"""
        try:
            cancelled = await self._cancel(run_id)
        finally:
            self._set_status(run_id, "interrupted", ("running", "pending"))
        return cancelled

    async def stop(self, run_id: int) -> bool:
        """停止：結束這次分析（執行中或暫停中皆可），首頁回到可開始新分析；仍可從分析頁面繼續補抓。"""
        try:
            await self._cancel(run_id)
        finally:
            stopped = self._set_status(run_id, "stopped", ("running", "pending", "interrupted"))
        return stopped

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
        self.current_run_id = run_id
        return True

    def start(self, run_id: int) -> bool:
        return self._launch(run_id)

    def resume(self, run_id: int) -> bool:
        return self._launch(run_id, only_unfinished=True)

    def refetch_unit(self, run_id: int, unit_id: int) -> bool:
        return self._launch(run_id, unit_ids={unit_id}, use_cache=False)
