import pytest

from app.mapping.service import import_mapping
from app.teachers.moe import import_moe
from app.teachers.service import (add_override, auto_link, moe_school, set_links, check_links, teacher_count,
                                  unit_choices, unmatched)
from tests.mapping_builder import build_mapping, ncu_rows, sample_mapping
from tests.moe_builder import build_moe, sample_moe


def setup(session, mapping: bytes, moe: bytes):
    v, _ = import_mapping(session, "m.xlsx", mapping)
    imp = import_moe(session, "moe.xlsx", moe)
    return v, imp


def test_auto_link_normalizes_tai(session):
    v, imp = setup(session, sample_mapping(), sample_moe())
    left = auto_link(session, v.id, imp.id)
    assert left == []
    tc = teacher_count(session, imp.id, 114, "total", "台大", "資訊管理學系")
    assert (tc.value, tc.source, tc.unit_codes) == (16, "moe", ["01131001"])
    assert teacher_count(session, imp.id, 114, "total", "中央", "資訊管理學系").value == 24


def test_school_matched_to_moe_school_by_name_only(session):
    # 學校名稱以 mapping 分頁為主、不寫死對照表：分頁名稱的字依序出現在教育部校名中，且只有一間符合
    moe = build_moe([(114, "國立臺灣海洋大學", "H1", "資訊管理學系", 9),
                     (114, "國立臺灣大學", "T1", "資訊管理學系", 16)])
    _, imp = setup(session, build_mapping({"中央": ncu_rows(("資訊管理學系",))}), moe)
    assert moe_school(session, imp.id, "海大") == "國立臺灣海洋大學"
    assert moe_school(session, imp.id, "台大") is None  # 「台…大」兩間都符合，多間符合時不猜
    assert moe_school(session, imp.id, "台灣大") is None
    # mapping 分頁直接用完整校名：與教育部校名相同就採用（「國立臺灣大學」也依序出現在其他校名中，不能靠猜）
    assert moe_school(session, imp.id, "國立臺灣大學") == "國立臺灣大學"
    assert moe_school(session, imp.id, "國立臺灣海洋大學") == "國立臺灣海洋大學"


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


@pytest.mark.parametrize("ours, moe_name", [
    ("物理系", "物理學系"),
    ("化學學系", "化學系"),
    ("統計研究所", "統計學研究所"),
    ("電機工程學系所", "電機工程學系"),
])
def test_auto_link_tolerates_wording(session, ours, moe_name):
    # 「系／學系」、「學系所」、多一個「學」字只是寫法不同，清單中只有一個這樣的單位時自動對應
    moe = build_moe([(114, "國立中央大學", "A", moe_name, 8), (114, "國立中央大學", "B", "化學工程學系", 5)])
    v, imp = setup(session, build_mapping({"中央": ncu_rows((ours,))}), moe)
    assert auto_link(session, v.id, imp.id) == []
    assert teacher_count(session, imp.id, 114, "total", "中央", ours).unit_codes == ["A"]


def test_auto_link_prefers_exact_name(session):
    moe = build_moe([(114, "國立中央大學", "A", "化學系", 8), (114, "國立中央大學", "B", "化學學系", 5)])
    v, imp = setup(session, build_mapping({"中央": ncu_rows(("化學學系",))}), moe)
    auto_link(session, v.id, imp.id)
    assert teacher_count(session, imp.id, 114, "total", "中央", "化學學系").unit_codes == ["B"]


def test_unit_choices_most_similar_first(session):
    moe = build_moe([(114, "國立中央大學", "01", "教育學系", 1), (114, "國立中央大學", "02", "英國語文學系", 2),
                     (114, "國立中央大學", "03", "日本語文學系", 3)])
    _, imp = setup(session, build_mapping({"中央": ncu_rows(("英語學系",))}), moe)
    assert unit_choices(session, imp.id, "中央", "英語學系")[0] == ("02", "英國語文學系")
    assert unit_choices(session, imp.id, "中央") == [("01", "教育學系"), ("02", "英國語文學系"), ("03", "日本語文學系")]


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


def test_reimport_clears_overrides(session):
    v, imp = setup(session, sample_mapping(), sample_moe())
    auto_link(session, v.id, imp.id)
    add_override(session, "中央", "資訊管理學系", 114, 23, "依 SciVal")
    imp2 = import_moe(session, "moe2.xlsx", sample_moe())
    assert teacher_count(session, imp2.id, 114, "total", "中央", "資訊管理學系").source == "moe"


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


def test_stale_link_when_code_missing_from_new_import(session):
    # 手動對應的代碼在新一份教育部資料中不存在（系所改代碼），要重新被列為需處理
    v, imp = setup(session, build_mapping({"中央": ncu_rows(("資訊管理學系",))}),
                   build_moe([(114, "國立中央大學", "X1", "資管系", 20)]))
    set_links(session, "中央", "資訊管理學系", ["X1"])
    assert check_links(session, v.id, imp.id) == ([], [])
    new = import_moe(session, "moe2.xlsx", build_moe([(115, "國立中央大學", "X9", "資管系", 21)]))
    assert [(u.school, u.dept_zh) for u in check_links(session, v.id, new.id)[1]] == [("中央", "資訊管理學系")]
