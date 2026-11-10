"""每次啟動程式都要重新手動匯入：舊的匯入資料不刪除（過去的分析仍要用），只標記為不再是目前資料。"""

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.models import FundingImport, MappingVersion, TeacherImport


def retire_current_imports(session: Session) -> None:
    now = datetime.now()
    for model in (MappingVersion, TeacherImport, FundingImport):
        session.execute(update(model).where(model.retired_at.is_(None)).values(retired_at=now))
    session.commit()
    session.expire_all()  # 已載入的物件要重新讀取才看得到失效時間
