"""產出檔的存放位置與紀錄：data/exports/<run_id>/。"""

from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Export


def export_path(settings: Settings, run_id: int, filename: str) -> Path:
    folder = settings.data_dir / "exports" / str(run_id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / filename


def record_export(session: Session, run_id: int, kind: str, path: Path) -> None:
    session.add(Export(run_id=run_id, kind=kind, path=str(path), created_at=datetime.now()))
    session.commit()
