from datetime import date

from app.db.models import Unit
from app.runs.scope import Group, RunConfig, build_groups, default_period


def u(id, school, dept, college="管理學院"):
    return Unit(id=id, school=school, ncu_dept=dept, ncu_college=college if school == "中央" else None,
                dept_zh=dept, dept_en=f"{dept} {school}")


UNITS = [u(1, "中央", "資訊管理學系"), u(2, "中央", "財務金融學系"), u(3, "中央", "會計研究所"),
         u(4, "台大", "資訊管理學系"), u(5, "台大", "財務金融學系"), u(6, "政治", "資訊管理學系"),
         u(7, "中央", "物理學系", "理學院")]


def cfg(**kw):
    base = dict(year_from=2021, year_to=2025, ncu_depts=("資訊管理學系",),
                comparisons=frozenset({"benchmark", "college"}), schools=("台大", "政治"),
                custom_unit_ids=(), academic_year=114, teacher_column="total")
    base.update(kw)
    return RunConfig(**base)


def test_default_period():
    assert default_period(date(2026, 10, 30)) == (2021, 2025)


def test_benchmark_group_respects_school_selection():
    groups = build_groups(UNITS, cfg(schools=("台大",), comparisons=frozenset({"benchmark"})))
    assert groups == [Group("benchmark", "資訊管理學系", (1, 4))]


def test_college_group_includes_all_college_depts():
    groups = build_groups(UNITS, cfg(comparisons=frozenset({"college"})))
    assert groups == [Group("college", "管理學院", (1, 2, 3))]


def test_missing_ncu_unit_skips_benchmark_group():
    units = [x for x in UNITS if x.id != 1]
    assert build_groups(units, cfg(comparisons=frozenset({"benchmark"}))) == []


def test_custom_group():
    groups = build_groups(UNITS, cfg(comparisons=frozenset({"custom"}), custom_unit_ids=(4, 6)))
    assert groups == [Group("custom", "custom", (4, 6))]


def test_config_json_roundtrip():
    c = cfg(custom_unit_ids=(4,))
    assert RunConfig.from_json(c.to_json()) == c
