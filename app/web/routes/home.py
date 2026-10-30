from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.mapping.service import issues_of, latest_version
from app.teachers.moe import import_years, latest_import
from app.teachers.service import unmatched
from app.web.deps import get_session, render
from app.web.routes.history import run_summaries

router = APIRouter()


@router.get("/")
def home(request: Request, session: Session = Depends(get_session)):
    version = latest_version(session)
    errors = [i for i in issues_of(session, version.id) if i.level == "error"] if version else []
    imp = latest_import(session)
    return render(request, "home.html", mapping=version, mapping_errors=len(errors), teacher_import=imp,
                  teacher_years=import_years(imp) if imp else [],
                  unmatched_count=len(unmatched(session, version.id)) if version else 0,
                  runs=run_summaries(session, limit=5))
