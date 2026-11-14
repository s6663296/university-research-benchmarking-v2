from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.ai.claude import ClaudeWriter
from app.ai.jobs import AiJobManager
from app.config import Settings
from app.db.settings_store import get_setting
from app.db.session import init_db, make_engine, make_session_factory
from app.elsevier.client import make_client
from app.runs.manager import RunManager, mark_interrupted
from app.runs.renumber import renumber_runs
from app.teachers.moe_fetch import MoeFetcher
from app.web import live
from app.web.deps import WEB_DIR
from app.web.routes import (
    ai_summary,
    funding,
    history,
    home,
    mapping,
    report_text,
    runs,
    settings as settings_routes,
    teachers,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.data_dir / "app.db")
        init_db(engine)
        session_factory = make_session_factory(engine)
        app.state.engine = engine
        app.state.session_factory = session_factory
        with session_factory() as s:
            mark_interrupted(s)
            renumber_runs(s, settings.data_dir)  # 啟動時沒有任何分析在跑，順便補上先前刪除留下的空號

        def client_factory():
            with session_factory() as s:
                return make_client(app.state.settings, s)

        app.state.run_manager = RunManager(session_factory, client_factory)

        def writer_factory():
            with session_factory() as s:
                model = get_setting(s, "ai_model")
            return ClaudeWriter(app.state.settings.anthropic_api_key, model)

        app.state.ai_jobs = AiJobManager(session_factory, writer_factory)
        app.state.moe_fetcher = MoeFetcher(session_factory, settings.data_dir)
        yield
        engine.dispose()

    app = FastAPI(title="員額小組論文指標自動化系統", lifespan=lifespan)
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.include_router(home.router)
    app.include_router(live.router)
    app.include_router(settings_routes.router)
    app.include_router(mapping.router)
    app.include_router(teachers.router)
    app.include_router(funding.router)
    app.include_router(report_text.router)
    app.include_router(history.router)
    app.include_router(ai_summary.router)  # 要在 runs 之前，避免被 /runs/{run_id} 系列路由搶先
    app.include_router(runs.router)
    return app
