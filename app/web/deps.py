from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=WEB_DIR / "templates")


def render(request: Request, name: str, status_code: int = 200, **ctx):
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
