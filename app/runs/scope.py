"""分析設定與比較範圍（設計 §5.3、§5.4）。"""

import json
from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.db.models import Unit
from app.mapping.parser import NCU_SHEET

Comparison = Literal["benchmark", "college", "custom"]
COMPARISON_LABELS = {"benchmark": "對標學校比較", "college": "院內比較", "custom": "自訂比較"}


@dataclass(frozen=True)
class RunConfig:
    year_from: int
    year_to: int
    ncu_depts: tuple[str, ...]
    comparisons: frozenset[Comparison]
    schools: tuple[str, ...]
    custom_unit_ids: tuple[int, ...]
    academic_year: int
    teacher_column: str
    force_refresh: bool = True

    @property
    def years(self) -> int:
        return self.year_to - self.year_from + 1

    def to_json(self) -> str:
        return json.dumps({
            "year_from": self.year_from, "year_to": self.year_to, "ncu_depts": list(self.ncu_depts),
            "comparisons": sorted(self.comparisons), "schools": list(self.schools),
            "custom_unit_ids": list(self.custom_unit_ids), "academic_year": self.academic_year,
            "teacher_column": self.teacher_column, "force_refresh": self.force_refresh,
        }, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "RunConfig":
        d = json.loads(text)
        return cls(year_from=d["year_from"], year_to=d["year_to"], ncu_depts=tuple(d["ncu_depts"]),
                   comparisons=frozenset(d["comparisons"]), schools=tuple(d["schools"]),
                   custom_unit_ids=tuple(d["custom_unit_ids"]), academic_year=d["academic_year"],
                   teacher_column=d["teacher_column"], force_refresh=d.get("force_refresh", True))


@dataclass(frozen=True)
class Group:
    comparison: Comparison
    key: str
    unit_ids: tuple[int, ...]


def default_period(today: date) -> tuple[int, int]:
    return today.year - 5, today.year - 1


def build_groups(units: list[Unit], cfg: RunConfig) -> list[Group]:
    ncu = {u.ncu_dept: u for u in units if u.school == NCU_SHEET}
    groups: list[Group] = []
    if "benchmark" in cfg.comparisons:
        for dept in cfg.ncu_depts:
            if dept not in ncu:
                continue
            others = [u for school in cfg.schools if school != NCU_SHEET
                      for u in units if u.school == school and u.ncu_dept == dept]
            groups.append(Group("benchmark", dept, (ncu[dept].id, *(u.id for u in others))))
    if "college" in cfg.comparisons:
        colleges = dict.fromkeys(ncu[d].ncu_college for d in cfg.ncu_depts if d in ncu and ncu[d].ncu_college)
        for college in colleges:
            groups.append(Group("college", college,
                                tuple(u.id for u in units if u.school == NCU_SHEET and u.ncu_college == college)))
    if "custom" in cfg.comparisons and cfg.custom_unit_ids:
        groups.append(Group("custom", "custom", tuple(cfg.custom_unit_ids)))
    return groups
