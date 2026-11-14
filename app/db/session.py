from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, delete, event, func, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import REFS_ENDPOINT, ApiCache, Base

ALEMBIC_DIR = Path(__file__).resolve().parents[2] / "alembic"
# 合併 main 時重新編號的 migration：舊版號 → 它原本接在後面的版本。資料庫停在舊版號時先退回，
# 再一路升級（重新編號的 migration 本身可重複執行）。
RENUMBERED = {"0008_run_api_usage": "0007_import_retired_at"}


def make_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def init_db(engine: Engine) -> None:
    """執行 Alembic migration 到最新版。"""
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    with engine.begin() as conn:
        if inspect(conn).has_table("alembic_version"):
            for old, base in RENUMBERED.items():
                conn.execute(text("UPDATE alembic_version SET version_num = :base WHERE version_num = :old"),
                             {"base": base, "old": old})
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def clear_all_data(engine: Engine) -> None:
    """清空所有資料表（含 app_settings，設定因此回到預設值），保留資料表結構與 migration 版本。

    參考文獻快取保留：重建全校要上萬次 Abstract Retrieval，只能用 clear_refs_cache 另外清除。
    """
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            stmt = table.delete()
            if table.name == ApiCache.__tablename__:
                stmt = stmt.where(ApiCache.endpoint != REFS_ENDPOINT)
            conn.execute(stmt)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("VACUUM")  # 釋放磁碟空間


def refs_cache_stats(session: Session) -> tuple[int, int]:
    """參考文獻快取的（篇數, 位元組數）。"""
    count, size = session.execute(select(func.count(), func.coalesce(func.sum(func.length(ApiCache.response_json)), 0))
                                  .where(ApiCache.endpoint == REFS_ENDPOINT)).one()
    return count, size


def clear_refs_cache(engine: Engine) -> int:
    """只刪除參考文獻快取，回傳刪除的篇數。"""
    with engine.begin() as conn:
        return conn.execute(delete(ApiCache).where(ApiCache.endpoint == REFS_ENDPOINT)).rowcount
