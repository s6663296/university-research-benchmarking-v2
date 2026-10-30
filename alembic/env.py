from alembic import context

from app.db.models import Base

config = context.config


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


connection = config.attributes.get("connection")
if connection is not None:
    _run(connection)
else:  # 從命令列執行 alembic（例如產生新的 migration）時，使用 data/app.db
    from app.config import Settings
    from app.db.session import make_engine

    engine = make_engine(Settings().data_dir / "app.db")
    with engine.begin() as conn:
        _run(conn)
