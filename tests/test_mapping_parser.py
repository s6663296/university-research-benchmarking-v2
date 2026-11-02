from pathlib import Path

from app.mapping.parser import is_ncu, parse_mapping
from tests.mapping_builder import build_mapping

# 2026/10 的原始檔（尚無「中央」分頁），固定下來，docs/ 中的檔案填寫後測試不受影響
REAL = Path("tests/fixtures/mapping_2026-10.xlsx").read_bytes()


def by_key(result):
    return {(u.school, u.ncu_dept): u for u in result.units}


def test_real_file_management_units():
    u = by_key(parse_mapping(REAL))
    im = u[("台大", "資訊管理學系")]
    assert im.dept_en == "Department of Information Management, National Taiwan University"
    assert (im.ncu_college, im.college, im.dept_zh) == ("管理學院", "管理學院", "資訊管理學系")
    assert ("台大", "經濟學系") not in u                      # x
    assert ("政治", "歷史研究所") not in u                    # 空白英文名
    assert u[("台科大", "英美語文學系")].dept_en.startswith("Department of Applied")  # \xa0 已去除
    assert ("師大", "中國文學系") in u                        # 「系所英文名稱(scopus)」表頭


def test_real_file_issues():
    r = parse_mapping(REAL)
    msgs = [(i.level, i.sheet, i.message) for i in r.issues]
    assert any(lv == "error" and "中央" in m for lv, _, m in msgs)                    # 缺中央分頁
    assert any(lv == "warning" and s == "清大" and "I1" in m for lv, s, m in msgs)    # 表頭列多出的儲存格
    assert any(lv == "warning" and s == "台大" and "網路學習科技研究所" in m for lv, s, m in msgs)  # 共用英文名稱
    assert any(lv == "warning" and s == "政治" and "歷史研究所" in m for lv, s, m in msgs)  # 有中文缺英文


def test_full_school_names_as_sheet_names():
    # 分頁用教育部完整校名：中大分頁為「國立中央大學」
    data = build_mapping({
        "國立中央大學": [("管理學院", "資訊管理學系", "管理學院", "資訊管理學系", "IM NCU", None)],
        "國立臺灣大學": [("管理學院", "資訊管理學系", "管理學院", "資訊管理學系", "IM NTU", None),
                         ("管理學院", "不存在學系", "管理學院", "不存在學系", "X NTU", None)],
    })
    result = parse_mapping(data)
    assert not [i for i in result.issues if i.level == "error"]
    assert [(i.sheet, i.message) for i in result.issues] == [("國立臺灣大學", "中大系所「不存在學系」不在「國立中央大學」分頁中")]
    assert is_ncu("國立中央大學") and is_ncu("中央") and not is_ncu("國立臺灣大學")


def test_header_with_full_ncu_name():
    header = ("學院", "國立中央大學系所", "國立臺灣大學", "系所中文名稱", "系所英文名稱", "備註")
    data = build_mapping({"國立中央大學": [("管理學院", "資訊管理學系", "管理學院", "資訊管理學系", "IM NCU", None)],
                          "國立臺灣大學": [("管理學院", "資訊管理學系", "管理學院", "資訊管理學系", "IM NTU", None)]},
                         header=header)
    result = parse_mapping(data)
    assert [(u.school, u.ncu_dept) for u in result.units] == [("國立中央大學", "資訊管理學系"), ("國立臺灣大學", "資訊管理學系")]
    assert not result.issues


def test_dept_header_accepts_any_dept_suffix():
    # 第二欄標題的寫法每年可能不同（「中大系所」「國立中央大學系所」「中央大學系所」…），只要以「系所」結尾就接受
    for title in ("中大系所", "國立中央大學系所", "中央大學系所"):
        header = ("學院", title, "國立臺灣大學", "系所中文名稱", "系所英文名稱", "備註")
        result = parse_mapping(build_mapping(
            {"國立中央大學": [("管理學院", "資訊管理學系", "管理學院", "資訊管理學系", "IM NCU", None)]}, header=header))
        assert [(u.school, u.ncu_dept) for u in result.units] == [("國立中央大學", "資訊管理學系")], title
        assert not result.issues, title


def test_missing_dept_header_still_reported():
    header = ("學院", "隨便", "國立臺灣大學", "系所中文名稱", "系所英文名稱", "備註")
    result = parse_mapping(build_mapping({"國立臺灣大學": [("a", "b", "c", "d", "e", None)]}, header=header))
    assert not result.units and any("缺少「中大系所」" in i.message for i in result.issues)


def test_missing_ncu_sheet_names_full_name():
    result = parse_mapping(build_mapping({"國立臺灣大學": [("管理學院", "資訊管理學系", "管理學院", "資訊管理學系", "IM", None)]}))
    assert result.issues[0].level == "error" and "缺少「國立中央大學」分頁" in result.issues[0].message


def test_blank_rows_skipped():
    data = build_mapping({"中央": [("管理學院", "資訊管理學系", "管理學院", "資訊管理學系", "IM NCU", None),
                                  (None, None, None, None, None, None),
                                  ("管理學院", "會計研究所", "管理學院", "會計研究所", "ACC NCU", None)]})
    assert [u.ncu_dept for u in parse_mapping(data).units] == ["資訊管理學系", "會計研究所"]


def test_ncu_sheet_parsed_and_missing_en_is_error():
    data = build_mapping({"中央": [
        ("管理學院", "資訊管理學系", "管理學院", "資訊管理學系", "Department of Information Management, National Central University", None),
        ("管理學院", "會計研究所", "管理學院", "會計研究所", None, None),
        (None, "太空及遙測研究中心", None, None, None, None),
    ]})
    r = parse_mapping(data)
    assert [x.ncu_dept for x in r.units] == ["資訊管理學系"]
    errors = [i for i in r.issues if i.level == "error"]
    assert len(errors) == 1 and "會計研究所" in errors[0].message


def test_header_variant_scopus_suffix():
    data = build_mapping({"中央": [("管理學院", "資訊管理學系", "管理學院", "資訊管理學系", "IM NCU")],
                          "師大": [("管理學院", "資訊管理學系", "管理學院", "資訊管理研究所", "IM NTNU")]},
                         header=(None, "中大系所", "師大", "系所中文名稱", "系所英文名稱(scopus)"))
    u = by_key(parse_mapping(data))
    assert u[("師大", "資訊管理學系")].dept_en == "IM NTNU" and u[("師大", "資訊管理學系")].remark is None


def test_missing_en_header_is_error():
    data = build_mapping({"中央": [("管理學院", "資訊管理學系", "管理學院", "資訊管理學系", "IM")]},
                         header=(None, "中大系所", "中央", "系所中文名稱", "英文"))
    r = parse_mapping(data)
    assert r.units == []
    assert any(i.level == "error" and "系所英文名稱" in i.message for i in r.issues)


def test_unknown_ncu_dept_warning():
    data = build_mapping({"中央": [("管理學院", "資訊管理學系", "管理學院", "資訊管理學系", "IM NCU", None)],
                          "台大": [("管理學院", "不存在學系", "管理學院", "某系", "Dept X, NTU", None)]})
    r = parse_mapping(data)
    assert any(i.level == "warning" and i.sheet == "台大" and "不存在學系" in i.message for i in r.issues)
