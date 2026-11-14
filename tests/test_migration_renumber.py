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
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0010_run_api_usage"
        columns = {c["name"] for c in inspect(conn).get_columns("runs")}
        assert {"api_usage_json", "ai_baseline_run_id"} <= columns and inspect(conn).has_table("ai_summaries")
