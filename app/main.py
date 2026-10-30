from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import Settings
from app.web.deps import WEB_DIR
from app.web.routes import home


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="員額小組論文指標自動化系統")
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.include_router(home.router)
    return app
