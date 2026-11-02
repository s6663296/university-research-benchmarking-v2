from datetime import datetime
from decimal import Decimal

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select

from app.db.models import Base, MappingVersion, Result, Run, Unit
from app.db.session import init_db, make_engine
from app.db.settings_store import get_setting, set_setting

EXPECTED = {"mapping_versions", "units", "mapping_issues", "teacher_imports", "teacher_counts",
            "unit_teacher_links", "teacher_overrides", "runs", "run_units", "run_publications", "api_cache", "api_quota",
            "results", "anomalies", "exports", "app_settings", "alembic_version"}


def test_init_db_creates_tables_with_wal(tmp_path):
    engine = make_engine(tmp_path / "app.db")
    init_db(engine)
    assert set(inspect(engine).get_table_names()) == EXPECTED
    with engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"


def test_init_db_is_idempotent(tmp_path):
    engine = make_engine(tmp_path / "app.db")
    init_db(engine)
    init_db(engine)
    assert "runs" in inspect(engine).get_table_names()


def test_migration_matches_models(tmp_path):
    engine = make_engine(tmp_path / "app.db")
    init_db(engine)
    with engine.connect() as c:
        assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []


def test_settings_default_and_override(session):
    assert get_setting(session, "timeout_sec") == 60
    set_setting(session, "timeout_sec", 45)
    assert get_setting(session, "timeout_sec") == 45


def test_decimal_roundtrip(session):
    mv = MappingVersion(filename="m.xlsx", sha256="x", imported_at=datetime.now())
    session.add(mv)
    session.flush()
    unit = Unit(mapping_version_id=mv.id, school="中央", ncu_dept="資訊管理學系", dept_en="X")
    run = Run(created_at=datetime.now(), period_from=2021, period_to=2025, scope_json="{}",
              mapping_version_id=mv.id, academic_year=114, teacher_column="total",
              self_citation_strategy="scival_param", status="pending")
    session.add_all([unit, run])
    session.flush()
    session.add(Result(run_id=run.id, unit_id=unit.id, comparison="benchmark", indicator_id="q1",
                       value=Decimal("2.575")))
    session.commit()
    session.expire_all()
    assert session.scalars(select(Result.value)).one() == Decimal("2.575")
