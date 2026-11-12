"""產生、儲存與判斷 AI 摘要是否仍符合目前的資料。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.baseline import baseline_for
from app.ai.inputs import college_input, dept_input, input_hash
from app.ai.prompts import system_prompt
from app.db.models import AiSummary, Run
from app.funding.service import run_colleges
from app.runs.scope import RunConfig


@dataclass
class SummaryDraft:
    summary: str
    comparison: str | None


class SummaryWriter(Protocol):
    def write(self, system: str, payload: dict) -> SummaryDraft: ...


class AiError(Exception):
    """message 是可直接顯示給承辦人的白話中文。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _payload(session: Session, run: Run, kind: str, key: str) -> dict:
    return dept_input(session, run, key) if kind == "dept" else college_input(session, run, key)


def _baseline_id(session: Session, run: Run, kind: str, key: str) -> int | None:
    if kind == "college":
        return run.ai_baseline_run_id
    base = baseline_for(session, run, key)
    return base.id if base else None


def _row(session: Session, run_id: int, kind: str, key: str) -> AiSummary | None:
    return session.scalars(select(AiSummary).where(
        AiSummary.run_id == run_id, AiSummary.kind == kind, AiSummary.key == key)).first()


def generate(session: Session, run: Run, kind: str, key: str, writer: SummaryWriter,
             tone: str, model: str) -> AiSummary:
    payload = _payload(session, run, kind, key)
    draft = writer.write(system_prompt(kind, tone), payload)  # AiError 直接往上拋，不動既有資料
    values = dict(summary=draft.summary, comparison=draft.comparison, input_hash=input_hash(payload),
                  baseline_run_id=_baseline_id(session, run, kind, key), model=model, edited=False,
                  updated_at=datetime.now())
    row = _row(session, run.id, kind, key)
    if row is None:
        row = AiSummary(run_id=run.id, kind=kind, key=key, created_at=values["updated_at"], **values)
        session.add(row)
    else:
        for name, value in values.items():
            setattr(row, name, value)
    session.commit()
    return row


def save_edit(session: Session, summary_id: int, summary: str, comparison: str | None) -> AiSummary:
    row = session.get(AiSummary, summary_id)
    row.summary, row.comparison = summary, comparison
    row.edited, row.updated_at = True, datetime.now()
    session.commit()
    return row


def is_fresh(session: Session, row: AiSummary) -> bool:
    run = session.get(Run, row.run_id)
    return input_hash(_payload(session, run, row.kind, row.key)) == row.input_hash


def fresh_text(session: Session, run: Run, kind: str, key: str) -> SummaryDraft | None:
    row = _row(session, run.id, kind, key)
    if row is None or not is_fresh(session, row):
        return None
    return SummaryDraft(row.summary, row.comparison)


def targets(session: Session, run: Run) -> list[tuple[str, str]]:
    depts = RunConfig.from_json(run.scope_json).ncu_depts
    return [("dept", d) for d in depts] + [("college", c) for c in run_colleges(session, run)]
