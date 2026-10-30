"""異常偵測：查無資料或數字異常時標「待人工確認」，不當成 0（設計 §8）。"""

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Anomaly, Run, RunPublication, RunUnit, Unit

KIND_LABELS = {
    "zero_results": "Scopus 查到 0 篇",
    "count_change": "篇數與上一版差異過大",
    "missing_teacher": "缺教師數",
    "fetch_failed": "抓取失敗",
    "scival_missing": "SciVal 缺少部分論文",
}


def _previous_count(session: Session, run: Run, unit: Unit) -> tuple[int, int] | None:
    """上一個已完成版本中，同一（學校, 中大系所, 英文名稱）的篇數：(run_id, 篇數)。"""
    row = session.execute(
        select(RunUnit.run_id, RunUnit.eid_count)
        .join(Unit, Unit.id == RunUnit.unit_id).join(Run, Run.id == RunUnit.run_id)
        .where(Run.id < run.id, Run.status == "done", RunUnit.status == "done",
               Unit.school == unit.school, Unit.ncu_dept == unit.ncu_dept, Unit.dept_en == unit.dept_en)
        .order_by(Run.id.desc()).limit(1)).first()
    return (row.run_id, row.eid_count) if row and row.eid_count is not None else None


def _missing_in_scival(session: Session, run_id: int, unit_id: int) -> int:
    texts = session.scalars(select(RunPublication.metrics_json).where(
        RunPublication.run_id == run_id, RunPublication.unit_id == unit_id)).all()
    return sum(1 for t in texts if t and not json.loads(t).get("in_scival", True))


def detect(session: Session, run_id: int, threshold: float) -> list[Anomaly]:
    run = session.get(Run, run_id)
    found: list[Anomaly] = []

    def add(ru: RunUnit, kind: str, detail: str):
        found.append(Anomaly(run_id=run_id, unit_id=ru.unit_id, kind=kind, detail=detail, status="open"))

    for ru in session.scalars(select(RunUnit).where(RunUnit.run_id == run_id).order_by(RunUnit.id)):
        unit = session.get(Unit, ru.unit_id)
        if ru.status == "failed":
            add(ru, "fetch_failed", ru.error or "抓取失敗")
        elif ru.status == "done":
            if ru.eid_count == 0:
                add(ru, "zero_results", f"查詢沒有結果，請檢查 mapping 的英文名稱：{unit.dept_en}")
            else:
                prev = _previous_count(session, run, unit)
                if prev and prev[1] > 0 and abs(ru.eid_count - prev[1]) / prev[1] > threshold:
                    add(ru, "count_change", f"上一版（分析 #{prev[0]}）{prev[1]} 篇，本次 {ru.eid_count} 篇")
                missing = _missing_in_scival(session, run_id, ru.unit_id)
                if missing:
                    add(ru, "scival_missing", f"Scopus {ru.eid_count} 篇中有 {missing} 篇 SciVal 沒有回傳指標")
        if not ru.teacher_count:
            add(ru, "missing_teacher", "缺教師數，師均顯示待確認；可到「教師數」頁修改後重新建立分析")
    return found


def resolve_anomaly(session: Session, anomaly_id: int, note: str) -> None:
    a = session.get(Anomaly, anomaly_id)
    a.status = "confirmed"
    a.resolution_note = note.strip() or None
    session.commit()
