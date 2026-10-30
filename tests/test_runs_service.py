from datetime import date

import pytest
from sqlalchemy import select

from app.db.models import RunUnit, Unit
from app.mapping.service import import_mapping
from app.runs.scope import RunConfig
from app.runs.service import create_run, last_config, preview_run
from app.teachers.moe import import_moe
from app.teachers.service import add_override, auto_link
from tests.mapping_builder import sample_mapping
from tests.moe_builder import sample_moe

TODAY = date(2026, 10, 30)


def cfg(**kw):
    base = dict(year_from=2021, year_to=2025, ncu_depts=("資訊管理學系",),
                comparisons=frozenset({"benchmark", "college"}), schools=("台大", "政治"),
                custom_unit_ids=(), academic_year=114, teacher_column="total")
    base.update(kw)
    return RunConfig(**base)


@pytest.fixture
def seeded(session):
    v, _ = import_mapping(session, "m.xlsx", sample_mapping())
    imp, _ = import_moe(session, "moe.xlsx", sample_moe())
    auto_link(session, v.id, imp.id)
    return session


def test_preview_blocks_without_teacher_import(session):
    import_mapping(session, "m.xlsx", sample_mapping())
    rows, errors = preview_run(session, cfg(), today=TODAY)
    assert any("尚未匯入教師數" in e for e in errors)


def test_preview_blocks_without_mapping(session):
    _, errors = preview_run(session, cfg(), today=TODAY)
    assert any("mapping" in e for e in errors)


def test_preview_blocks_bad_period(seeded):
    _, errors = preview_run(seeded, cfg(year_to=2026), today=TODAY)
    assert any("今年" in e for e in errors)


def test_preview_blocks_unknown_year(seeded):
    _, errors = preview_run(seeded, cfg(academic_year=110), today=TODAY)
    assert any("110" in e for e in errors)


def test_preview_rows(seeded):
    rows, errors = preview_run(seeded, cfg(), today=TODAY)
    assert errors == []
    by_school = {(r.school, seeded.get(Unit, r.unit_id).ncu_dept): r for r in rows}
    ntu = by_school[("台大", "資訊管理學系")]
    assert ntu.query.startswith('AFFIL("Department of Information Management, National Taiwan University")')
    assert ntu.teacher.value == 16
    assert len(rows) == 5  # 中央 3 系所 + 台大資管 + 政治資管


def test_create_run_snapshots_teacher_counts(seeded):
    run = create_run(seeded, cfg(), today=TODAY)
    ncu_im = next(ru for ru in run_units(seeded, run.id)
                  if seeded.get(Unit, ru.unit_id).school == "中央" and seeded.get(Unit, ru.unit_id).ncu_dept == "資訊管理學系")
    assert (ncu_im.teacher_count, ncu_im.teacher_source, ncu_im.role) == (24, "moe", "ncu")
    add_override(seeded, "中央", "資訊管理學系", 114, 23, "依 SciVal")
    seeded.expire_all()
    assert seeded.get(RunUnit, ncu_im.id).teacher_count == 24
    run2 = create_run(seeded, cfg(), today=TODAY)
    again = next(ru for ru in run_units(seeded, run2.id) if ru.unit_id == ncu_im.unit_id)
    assert (again.teacher_count, again.teacher_source) == (23, "manual:依 SciVal")
    assert run.status == "pending" and run.self_citation_strategy == "scival_param"


def test_unit_shared_by_groups_created_once(seeded):
    run = create_run(seeded, cfg(), today=TODAY)
    ids = [ru.unit_id for ru in run_units(seeded, run.id)]
    assert len(ids) == len(set(ids)) == 5


def test_create_run_raises_on_blocking_error(session):
    with pytest.raises(ValueError):
        create_run(session, cfg(), today=TODAY)


def test_last_config_roundtrip(seeded):
    assert last_config(seeded) is None
    c = cfg(schools=("台大",))
    create_run(seeded, c, today=TODAY)
    assert last_config(seeded) == c


def run_units(session, run_id):
    return session.scalars(select(RunUnit).where(RunUnit.run_id == run_id)).all()
