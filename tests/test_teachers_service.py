import pytest

from app.mapping.service import import_mapping
from app.teachers.moe import import_moe
from app.teachers.service import (add_override, auto_link, set_links, teacher_count, unit_choices,
                                  unmatched)
from tests.mapping_builder import build_mapping, ncu_rows, sample_mapping
from tests.moe_builder import build_moe, sample_moe


def setup(session, mapping: bytes, moe: bytes):
    v, _ = import_mapping(session, "m.xlsx", mapping)
    imp, _ = import_moe(session, "moe.xlsx", moe)
    return v, imp


def test_auto_link_normalizes_tai(session):
    v, imp = setup(session, sample_mapping(), sample_moe())
    left = auto_link(session, v.id, imp.id)
    assert left == []
    tc = teacher_count(session, imp.id, 114, "total", "台大", "資訊管理學系")
    assert (tc.value, tc.source, tc.unit_codes) == (16, "moe", ["01131001"])
    assert teacher_count(session, imp.id, 114, "total", "中央", "資訊管理學系").value == 24


def test_code_survives_rename(session):
    moe = build_moe([(113, "國立中央大學", "X1", "資訊管理學系", 20),
                     (114, "國立中央大學", "X1", "資訊管理與科技學系", 22)])
    v, imp = setup(session, build_mapping({"中央": ncu_rows(("資訊管理學系",))}), moe)
    assert auto_link(session, v.id, imp.id) == []
    assert teacher_count(session, imp.id, 114, "total", "中央", "資訊管理學系").value == 22


def test_unmatched_listed(session):
    moe = build_moe([(114, "國立中央大學", "X1", "資管系", 20)])
    v, imp = setup(session, build_mapping({"中央": ncu_rows(("資訊管理學系",))}), moe)
    left = auto_link(session, v.id, imp.id)
    assert [(u.school, u.dept_zh) for u in left] == [("中央", "資訊管理學系")]
    assert unmatched(session, v.id) == left
    assert unit_choices(session, imp.id, "中央") == [("X1", "資管系")]


def test_manual_multi_link_sums(session):
    moe = build_moe([(114, "國立中央大學", "A", "資訊管理學系", 8), (114, "國立中央大學", "B", "資訊管理研究所", 5)])
    v, imp = setup(session, build_mapping({"中央": ncu_rows(("資訊管理學系",))}), moe)
    auto_link(session, v.id, imp.id)
    set_links(session, "中央", "資訊管理學系", ["A", "B"])
    tc = teacher_count(session, imp.id, 114, "total", "中央", "資訊管理學系")
    assert (tc.value, tc.source, sorted(tc.unit_codes)) == (13, "moe", ["A", "B"])


def test_override_wins_and_needs_reason(session):
    v, imp = setup(session, sample_mapping(), sample_moe())
    auto_link(session, v.id, imp.id)
    with pytest.raises(ValueError, match="原因"):
        add_override(session, "中央", "資訊管理學系", 114, 23, "  ")
    with pytest.raises(ValueError):
        add_override(session, "中央", "資訊管理學系", 114, -1, "x")
    add_override(session, "中央", "資訊管理學系", 114, 23, "依 SciVal")
    tc = teacher_count(session, imp.id, 114, "total", "中央", "資訊管理學系")
    assert (tc.value, tc.source, tc.reason) == (23, "manual", "依 SciVal")
    assert teacher_count(session, imp.id, 113, "total", "中央", "資訊管理學系").source == "moe"


def test_latest_override_wins(session):
    v, imp = setup(session, sample_mapping(), sample_moe())
    add_override(session, "中央", "資訊管理學系", 114, 23, "第一次")
    add_override(session, "中央", "資訊管理學系", 114, 22, "第二次")
    assert teacher_count(session, imp.id, 114, "total", "中央", "資訊管理學系").value == 22


def test_missing_year_is_missing(session):
    v, imp = setup(session, sample_mapping(), sample_moe())
    auto_link(session, v.id, imp.id)
    tc = teacher_count(session, imp.id, 110, "total", "台大", "資訊管理學系")
    assert (tc.value, tc.source) == (None, "missing")


def test_column_switch(session):
    v, imp = setup(session, sample_mapping(), sample_moe())
    auto_link(session, v.id, imp.id)
    assert teacher_count(session, imp.id, 114, "asst_prof_plus", "台大", "資訊管理學系").value == 15


def test_existing_manual_link_kept_by_auto_link(session):
    v, imp = setup(session, sample_mapping(), sample_moe())
    set_links(session, "台大", "資訊管理學系", ["01131002"])
    auto_link(session, v.id, imp.id)
    assert teacher_count(session, imp.id, 114, "total", "台大", "資訊管理學系").unit_codes == ["01131002"]
