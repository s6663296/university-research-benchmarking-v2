from pathlib import Path

from sqlalchemy import delete, func, select

from app.db.models import MappingIssue, MappingVersion, Unit
from app.mapping.service import (RULES_HASH, diff_versions, error_count, import_mapping, latest_version,
                                 previous_version, recheck_mapping, units_of)
from tests.mapping_builder import build_mapping, ncu_rows

# 2026/10 的原始檔（尚無「中央」分頁），固定下來，docs/ 中的檔案填寫後測試不受影響
REAL = Path("tests/fixtures/mapping_2026-10.xlsx").read_bytes()


def test_import_creates_version_with_units_and_issues(session):
    v, created = import_mapping(session, "mapping.xlsx", REAL)
    assert created and v.filename == "mapping.xlsx" and len(v.sha256) == 64
    assert len(units_of(session, v.id)) == 161
    levels = session.scalars(select(MappingIssue.level).where(MappingIssue.mapping_version_id == v.id)).all()
    assert "error" in levels


def test_same_file_not_duplicated(session):
    v1, _ = import_mapping(session, "a.xlsx", REAL)
    v2, created = import_mapping(session, "b.xlsx", REAL)
    assert created is False and v2.id == v1.id
    assert session.scalar(select(func.count()).select_from(MappingVersion)) == 1


def test_same_file_refreshes_issues(session):
    # 檢查規則更新後，重新上傳同一個檔案要看到新的檢查結果（例如之前誤報的錯誤）
    v, _ = import_mapping(session, "a.xlsx", REAL)
    session.add(MappingIssue(mapping_version_id=v.id, sheet="x", row=None, level="error", message="舊規則的誤報"))
    session.commit()
    import_mapping(session, "a.xlsx", REAL)
    messages = session.scalars(select(MappingIssue.message).where(MappingIssue.mapping_version_id == v.id)).all()
    assert "舊規則的誤報" not in messages and messages


def test_same_file_parsed_differently_creates_version(session):
    # 舊規則把整份檔判為無效（0 個單位）；規則更新後重新上傳同一檔案，要建立新版本
    v1, _ = import_mapping(session, "a.xlsx", REAL)
    session.execute(delete(Unit).where(Unit.mapping_version_id == v1.id))
    session.commit()
    v2, created = import_mapping(session, "a.xlsx", REAL)
    assert created and v2.id != v1.id and len(units_of(session, v2.id)) == 161


def test_diff_added_removed_changed(session):
    ntu = lambda dept, en: ("管理學院", dept, "管理學院", dept, en, None)
    old = build_mapping({"中央": ncu_rows(), "台大": [ntu("資訊管理學系", "IM NTU"), ntu("財務金融學系", "FIN NTU")]})
    new = build_mapping({"中央": ncu_rows(), "台大": [ntu("資訊管理學系", "IM NTU v2"), ntu("會計研究所", "ACC NTU")]})
    v1, _ = import_mapping(session, "old.xlsx", old)
    v2, _ = import_mapping(session, "new.xlsx", new)
    assert previous_version(session, v2.id).id == v1.id and latest_version(session).id == v2.id
    d = diff_versions(session, v1.id, v2.id)
    assert d.added == [("台大", "會計研究所")]
    assert d.removed == [("台大", "財務金融學系")]
    assert d.changed == [(("台大", "資訊管理學系"), "IM NTU", "IM NTU v2")]


def test_diff_without_previous_lists_all_added(session):
    v, _ = import_mapping(session, "m.xlsx", build_mapping({"中央": ncu_rows(("資訊管理學系",))}))
    assert diff_versions(session, None, v.id).added == [("中央", "資訊管理學系")]


def test_recheck_skips_when_rules_unchanged(session):
    v, _ = import_mapping(session, "a.xlsx", REAL)
    assert v.rules_hash == RULES_HASH
    session.add(MappingIssue(mapping_version_id=v.id, sheet="x", row=None, level="error", message="不會被重算"))
    session.commit()
    recheck_mapping(session, v)
    assert "不會被重算" in session.scalars(select(MappingIssue.message)).all()


def test_recheck_after_rules_change(session):
    # 程式更新後（指紋不同）首頁載入時用保存的原始檔重新檢查，取代舊規則的結果
    v, _ = import_mapping(session, "a.xlsx", REAL)
    errors = error_count(session, v.id)
    session.add(MappingIssue(mapping_version_id=v.id, sheet="x", row=None, level="error", message="舊規則的誤報"))
    v.rules_hash = "old"
    session.commit()
    recheck_mapping(session, v)
    assert v.rules_hash == RULES_HASH and error_count(session, v.id) == errors
    assert "舊規則的誤報" not in session.scalars(select(MappingIssue.message)).all()


def test_recheck_flags_units_parsed_differently(session):
    v, _ = import_mapping(session, "a.xlsx", REAL)
    session.execute(delete(Unit).where(Unit.mapping_version_id == v.id))
    v.rules_hash = "old"
    session.commit()
    recheck_mapping(session, v)
    assert any("請重新上傳" in m for m in session.scalars(select(MappingIssue.message)))


def test_recheck_keeps_result_without_stored_file(session):
    # 舊版本匯入時沒有保存原始檔，無法重新檢查，保留原結果
    v, _ = import_mapping(session, "a.xlsx", REAL)
    v.content, v.rules_hash = None, "old"
    session.commit()
    before = error_count(session, v.id)
    recheck_mapping(session, v)
    assert error_count(session, v.id) == before and v.rules_hash == "old"
