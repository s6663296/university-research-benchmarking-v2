"""比較兩個分析版本的指標差異（設計 §5.6）。"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Result, Unit
from app.metrics.indicators import INDICATORS

ORDER = {i.id: n for n, i in enumerate(INDICATORS)}


@dataclass
class DiffRow:
    school: str
    ncu_dept: str
    comparison: str
    indicator_id: str
    a: Decimal | None
    b: Decimal | None
    delta: Decimal | None


def _values(session: Session, run_id: int) -> dict[tuple, Decimal | None]:
    rows = session.execute(select(Unit.school, Unit.ncu_dept, Result.comparison, Result.indicator_id, Result.value)
                           .join(Unit, Unit.id == Result.unit_id).where(Result.run_id == run_id)).all()
    return {(r.school, r.ncu_dept, r.comparison, r.indicator_id): r.value for r in rows}


def compare_runs(session: Session, run_a: int, run_b: int) -> list[DiffRow]:
    a, b = _values(session, run_a), _values(session, run_b)
    keys = sorted(set(a) | set(b), key=lambda k: (k[2], k[1], k[0] != "中央", k[0], ORDER.get(k[3], 99)))
    out = []
    for k in keys:
        va, vb = a.get(k), b.get(k)
        out.append(DiffRow(*k, va, vb, vb - va if va is not None and vb is not None else None))
    return out
