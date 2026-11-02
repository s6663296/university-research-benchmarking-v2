"""比較兩個分析版本的指標差異（設計 §5.6）。"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Result, Unit
from app.mapping.parser import is_ncu
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
    unit_a: int | None = None   # 兩版各自的單位（來源說明用）
    unit_b: int | None = None


def _values(session: Session, run_id: int) -> dict[tuple, tuple[Decimal | None, int]]:
    rows = session.execute(select(Unit.school, Unit.ncu_dept, Result.comparison, Result.indicator_id, Result.value,
                                  Result.unit_id).join(Unit, Unit.id == Result.unit_id).where(Result.run_id == run_id)).all()
    return {(r.school, r.ncu_dept, r.comparison, r.indicator_id): (r.value, r.unit_id) for r in rows}


def compare_runs(session: Session, run_a: int, run_b: int) -> list[DiffRow]:
    a, b = _values(session, run_a), _values(session, run_b)
    keys = sorted(set(a) | set(b), key=lambda k: (k[2], k[1], not is_ncu(k[0]), k[0], ORDER.get(k[3], 99)))
    out = []
    for k in keys:
        (va, ua), (vb, ub) = a.get(k, (None, None)), b.get(k, (None, None))
        out.append(DiffRow(*k, va, vb, vb - va if va is not None and vb is not None else None, ua, ub))
    return out
