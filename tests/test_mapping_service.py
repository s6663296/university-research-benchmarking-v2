from pathlib import Path

from sqlalchemy import func, select

from app.db.models import MappingIssue, MappingVersion
from app.mapping.service import diff_versions, import_mapping, latest_version, previous_version, units_of
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
