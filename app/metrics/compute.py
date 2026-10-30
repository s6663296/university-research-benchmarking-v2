"""指標計算與排名（設計 §7.2）。純函式，不碰資料庫。"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.elsevier.scival import PubMetrics
from app.metrics.indicators import INDICATORS

CENT = Decimal("0.01")


def round2(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class UnitValues:
    raw: dict[str, Decimal | None]
    five_year_avg: dict[str, Decimal | None]
    value: dict[str, Decimal | None]


def compute_unit(pubs: list[PubMetrics], teachers: int | None, years: int) -> UnitValues:
    """篇數類：raw = A、five_year_avg = A/年數、value = A/年數/教師數；查無論文時 value 全為 None。"""
    raw, avg, value = {}, {}, {}
    n = len(pubs)
    for ind in INDICATORS:
        if ind.aggregate == "count":
            a = n if ind.field is None else sum(1 for p in pubs if getattr(p, ind.field))
            raw[ind.id] = Decimal(a)
            avg[ind.id] = round2(Decimal(a) / years) if n else None
            value[ind.id] = round2(Decimal(a) / years / teachers) if n and teachers else None
        elif ind.aggregate == "mean":
            vals = [getattr(p, ind.field) for p in pubs if getattr(p, ind.field) is not None]
            raw[ind.id] = sum(vals, Decimal(0)) / len(vals) if vals else None
            avg[ind.id] = None
            value[ind.id] = round2(raw[ind.id]) if vals else None
        else:  # ratio：有值各篇的被引數加總 ÷ 總篇數
            vals = [getattr(p, ind.field) for p in pubs if getattr(p, ind.field) is not None]
            raw[ind.id] = Decimal(sum(vals)) if vals else None
            avg[ind.id] = None
            value[ind.id] = round2(raw[ind.id] / n) if vals and n else None
    return UnitValues(raw, avg, value)


def rank(values: dict[int, Decimal | None]) -> dict[int, int | None]:
    """由高到低；同值同名次、下一名跳號；沒有值的不排名。"""
    present = [v for v in values.values() if v is not None]
    return {k: None if v is None else 1 + sum(1 for other in present if other > v) for k, v in values.items()}
