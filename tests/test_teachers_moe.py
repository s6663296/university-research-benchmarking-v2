import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.models import TeacherCountRow
from app.teachers.moe import import_moe, latest_import, parse_moe
from tests.moe_builder import build_moe

REAL_PATH = Path("docs/教1-1.專任教師數-以「系(所)」統計.xlsx")


@pytest.mark.skipif(not REAL_PATH.exists(), reason="教育部原始檔不在 docs/ 中")
def test_real_file():
    rows = parse_moe(REAL_PATH.read_bytes())
    assert {r.academic_year for r in rows} == set(range(106, 115))
    ncu_im = [r for r in rows if r.school == "國立中央大學" and r.unit_code == "06131053"]
    assert len(ncu_im) == 9 and all(r.unit_name == "資訊管理學系" for r in ncu_im)
    assert next(r for r in ncu_im if r.academic_year == 114).total == 24
    assert not any(r.school.startswith("統計說明") for r in rows)


def test_parse_small_file_skips_footer():
    rows = parse_moe(build_moe([(114, "國立臺灣大學", "01131001", "資訊管理學系", 16, 15)]))
    assert len(rows) == 1
    r = rows[0]
    assert (r.academic_year, r.school, r.unit_code, r.unit_name, r.total, r.asst_prof_plus) == \
        (114, "國立臺灣大學", "01131001", "資訊管理學系", 16, 15)


def test_parse_rejects_file_without_header():
    with pytest.raises(ValueError, match="學年度"):
        parse_moe(_no_header())


def _no_header() -> bytes:
    from io import BytesIO

    from openpyxl import Workbook
    wb = Workbook()
    wb.active.append(("隨便",))
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_reimport_same_file_overwrites(session):
    data = build_moe([(113, "國立臺灣大學", "01131001", "資訊管理學系", 16), (114, "國立臺灣大學", "01131001", "資訊管理學系", 17)])
    first = import_moe(session, "moe.xlsx", data)
    assert json.loads(first.years_json) == [113, 114]
    again = import_moe(session, "moe2.xlsx", data)
    assert again.id != first.id and again.imported_at >= first.imported_at
    assert latest_import(session).id == again.id
    assert session.scalar(select(func.count()).select_from(TeacherCountRow)) == 4
