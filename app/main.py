from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import Settings
from app.db.session import init_db, make_engine, make_session_factory
from app.web.deps import WEB_DIR
from app.web.routes import home, mapping, settings as settings_routes, teachers


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.data_dir / "app.db")
        init_db(engine)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        yield
        engine.dispose()

    app = FastAPI(title="員額小組論文指標自動化系統", lifespan=lifespan)
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.include_router(home.router)
    app.include_router(settings_routes.router)
    app.include_router(mapping.router)
    app.include_router(teachers.router)
    return app
