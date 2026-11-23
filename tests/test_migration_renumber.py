from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.db.session import ALEMBIC_DIR, init_db, make_engine


def test_db_on_old_api_usage_revision_upgrades_to_head(tmp_path):
    """開發版曾把 api_usage_json 編為 0008_run_api_usage；這種資料庫要能補上 main 的 migration 並升到最新。"""
    engine = make_engine(tmp_path / "app.db")
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0007_import_retired_at")
        conn.execute(text("ALTER TABLE runs ADD COLUMN api_usage_json TEXT NOT NULL DEFAULT '{}'"))
        conn.execute(text("UPDATE alembic_version SET version_num = '0008_run_api_usage'"))

    init_db(engine)

    with engine.connect() as conn:
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0016_ai_chart_notes"
        columns = {c["name"] for c in inspect(conn).get_columns("runs")}
        assert {"api_usage_json", "ai_baseline_run_id"} <= columns and inspect(conn).has_table("ai_summaries")
        assert {"challenges", "rival_changes"} <= {c["name"] for c in inspect(conn).get_columns("ai_summaries")}
        assert "uid" in columns


def _insert(conn, table: str, **values) -> None:
    """只填 NOT NULL 欄位的假值，其餘用指定值。"""
    for col in conn.execute(text(f"PRAGMA table_info({table})")).mappings():
        if col["notnull"] and col["dflt_value"] is None and not col["pk"] and col["name"] not in values:
            values[col["name"]] = 0 if "INT" in (col["type"] or "").upper() else "x"
    conn.execute(text(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join(':' + k for k in values)})"), values)


def test_uid_migration_keeps_child_rows_and_gives_distinct_uids(tmp_path):
    """外鍵檢查開著、子表有資料指向 runs 時也要升級得過（曾因重建 runs 表 DROP TABLE 失敗）。"""
    engine = make_engine(tmp_path / "app.db")
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0011_ai_two_columns")
        _insert(conn, "mapping_versions", id=1)
        for i in (1, 3, 4):
            _insert(conn, "runs", id=i, mapping_version_id=1, teacher_import_id=None)
        _insert(conn, "units", id=1, mapping_version_id=1)
        _insert(conn, "run_units", run_id=3, unit_id=1)
    init_db(engine)
    with engine.connect() as conn:
        uids = conn.scalars(text("SELECT uid FROM runs")).all()
        assert conn.scalar(text("SELECT count(*) FROM run_units WHERE run_id = 3")) == 1
    assert len(set(uids)) == 3 and all(len(u) == 8 for u in uids)


def test_quota_rows_survive_per_key_upgrade(tmp_path):
    """額度改以 Key 為單位時，舊紀錄保留下來（key_id 為空字串，視為第 1 組 Key）。"""
    engine = make_engine(tmp_path / "app.db")
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0012_run_uid")
        conn.execute(text("INSERT INTO api_quota (api, \"limit\", remaining, observed_at) "
                          "VALUES ('scopus', 20000, 123, '2026-11-20 10:00:00')"))

    init_db(engine)

    with engine.connect() as conn:
        assert conn.execute(text("SELECT api, key_id, remaining FROM api_quota")).all() == [("scopus", "", 123)]
