from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.mapping.service import issues_of, latest_version
from app.web.deps import get_session, render

router = APIRouter()


@router.get("/")
def home(request: Request, session: Session = Depends(get_session)):
    version = latest_version(session)
    errors = [i for i in issues_of(session, version.id) if i.level == "error"] if version else []
    return render(request, "home.html", mapping=version, mapping_errors=len(errors))
