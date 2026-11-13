"""把目前的三份匯入資料標記為不再使用（資料不刪除，過去的分析仍要用）。

程式啟動時已不再呼叫：匯入過的資料重開程式後仍然有效。保留給測試模擬舊版資料庫。
"""

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
