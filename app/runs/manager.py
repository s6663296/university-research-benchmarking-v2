"""背景任務：在同一行程內以 asyncio 執行分析（設計 §6.2）。"""

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Run, RunUnit
from app.elsevier.client import ElsevierClient
from app.elsevier.inset_selfcite import InSetSelfCitations
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


@dataclass
class Refetch:
    """已完成分析的單位重新抓取：在背景依序處理，執行中再按的單位排到下一輪。"""
    queued: list[int] = field(default_factory=list)   # 等下一輪
    active: set[int] = field(default_factory=set)     # 這一輪正在抓
    finished: list[int] = field(default_factory=list)

    @property
    def units(self) -> list[int]:
        """這一批按過的單位，依按下的順序。"""
        return [*self.finished, *(u for u in self.queued if u not in self.finished), *sorted(self.active)]

    @property
    def total(self) -> int:
        return len(self.queued) + len(self.active) + len(self.finished)

    def state(self, unit_id: int) -> str | None:
        if unit_id in self.active:
            return "active"
        if unit_id in self.queued:
            return "queued"
        return "finished" if unit_id in self.finished else None


class RunManager:
    def __init__(self, session_factory: sessionmaker[Session], client_factory: Callable[[], ElsevierClient]):
        self.session_factory = session_factory
        self.client_factory = client_factory
        self.tasks: dict[int, asyncio.Task] = {}  # 依啟動順序排列（重新啟動的移到最後）
        # 最後啟動的分析；結束時若已沒有其他分析在執行，改為剛結束的那個（見 display_run_id）
        self.current_run_id: int | None = None
        self.selfcite: dict[int, InSetSelfCitations] = {}  # 執行中分析的排除自我引用進度（首頁流程圖顯示）
        self.refetches: dict[int, Refetch] = {}  # 正在背景重新抓取單位的分析

    def is_running(self, run_id: int) -> bool:
        task = self.tasks.get(run_id)
        return task is not None and not task.done()

    def refetching(self, run_id: int) -> Refetch | None:
        """這個分析正在背景重新抓取單位（分析本身維持已完成，頁面照常顯示結果）。"""
        return self.refetches.get(run_id) if self.is_running(run_id) else None

    def any_running(self) -> bool:
        return any(not t.done() for t in self.tasks.values())

    def any_full_running(self) -> bool:
        """有完整分析在執行（不含已完成分析在背景重新抓取單位）。"""
        return any(self.is_running(run_id) and run_id not in self.refetches for run_id in self.tasks)

    @property
    def display_run_id(self) -> int | None:
        """首頁儀表板顯示的分析：有分析在執行就顯示它（多個時取最後啟動的），
        否則顯示最後結束的。不能只看最後啟動的：執行 #2 時對 #1 接續或重抓，#1 很快做完，
        首頁就會停在 #1 的結果而看不到仍在執行的 #2。"""
        running = [run_id for run_id in self.tasks if self.is_running(run_id)]
        full = [run_id for run_id in running if run_id not in self.refetches]  # 完整分析優先於重新抓取
        return (full or running)[-1] if running else self.current_run_id

    def _finished(self, run_id: int) -> None:
        if not self.any_running():
            self.current_run_id = run_id

    async def _cancel(self, run_id: int) -> bool:
        task = self.tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        return True

    def _set_status(self, run_id: int, status: str, from_status: tuple[str, ...]) -> bool:
        """抓到一半的單位退回 pending，已完成的保留。已完成分析重新抓取到一半被取消時，
        分析維持已完成，但單位一樣要退回，否則會一直顯示抓取中。"""
        with self.session_factory() as session:
            run = session.get(Run, run_id)
            if run is None:
                return False
            for ru in session.scalars(select(RunUnit).where(
                    RunUnit.run_id == run_id, RunUnit.status.in_(FETCHING))):
                ru.status = "pending"
            changed = run.status in from_status
            if changed:
                run.status = status
            session.commit()
        return changed

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

    def _launch(self, run_id: int, refetch: Refetch | None = None, **kwargs) -> bool:
        """已有作業在執行時不重複啟動，回傳 False。refetch：依序處理佇列裡的單位直到清空。"""
        if self.is_running(run_id):
            return False

        async def run_once(**options):
            client = self.client_factory()
            try:
                await execute_run(run_id, self.session_factory, client, live=self.selfcite, **options)
            finally:
                await client.aclose()

        async def job():
            if refetch is None:
                await run_once(**kwargs)
                return
            try:
                # 最後一次檢查佇列與離開佇列之間沒有 await：關連線期間再按的單位會在下一圈處理，不會遺漏
                while refetch.queued:
                    refetch.active, refetch.queued = set(refetch.queued), []
                    await run_once(unit_ids=set(refetch.active), use_cache=False)
                    refetch.finished += [u for u in refetch.active if u not in refetch.finished]
                    refetch.active = set()
            finally:
                if self.refetches.get(run_id) is refetch:
                    self.refetches.pop(run_id, None)

        self.tasks.pop(run_id, None)
        task = self.tasks[run_id] = asyncio.create_task(job())
        if refetch is None:  # 重新抓取結束後，首頁回到原本顯示的分析
            self.current_run_id = run_id
            task.add_done_callback(lambda _: self._finished(run_id))
        return True

    def start(self, run_id: int) -> bool:
        return self._launch(run_id)

    def resume(self, run_id: int) -> bool:
        return self._launch(run_id, only_unfinished=True)

    def refetch_unit(self, run_id: int, unit_id: int) -> bool:
        """重新抓取一個單位；同一分析已在重新抓取時排到下一輪。完整分析執行中則回傳 False。"""
        refetch = self.refetching(run_id)
        if refetch is not None:
            if refetch.state(unit_id) not in ("queued", "active"):
                if unit_id in refetch.finished:
                    refetch.finished.remove(unit_id)
                refetch.queued.append(unit_id)
            return True
        refetch = Refetch(queued=[unit_id])
        if not self._launch(run_id, refetch=refetch):
            return False
        self.refetches[run_id] = refetch  # 與建立 task 之間沒有 await，task 開始前一定已登記
        return True
