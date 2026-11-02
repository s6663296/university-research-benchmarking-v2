from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.mapping.parser import NCU_SHEET, is_ncu

WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=WEB_DIR / "templates")
templates.env.globals["is_ncu"] = is_ncu
templates.env.globals["NCU_SHEET"] = NCU_SHEET


def get_session(request: Request):
    with request.app.state.session_factory() as session:
        yield session


def render(request: Request, name: str, status_code: int = 200, **ctx):
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
