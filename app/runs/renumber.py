"""刪除版本後把分析編號重排成 1…N（保持原本的先後順序），並同步子表與產出檔資料夾。"""

import shutil
from pathlib import Path

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.db.models import AiSummary, Anomaly, Export, Result, Run, RunPublication, RunUnit

CHILDREN = (Result, Anomaly, RunPublication, RunUnit, Export, AiSummary)


def renumber_runs(session: Session, data_dir: Path) -> dict[int, int]:
    """回傳 {舊編號: 新編號}（只含有變動的）。呼叫前須確認沒有分析正在執行。"""
    old_ids = list(session.scalars(select(Run.id).order_by(Run.id)))
    moves = {old: new for new, old in enumerate(old_ids, start=1) if old != new}
    if not moves:
        return {}
    # 由小到大搬：新編號一定小於舊編號，且上一輪已把它讓出來
    session.execute(text("PRAGMA defer_foreign_keys = ON"))  # 交易結束時才檢查外鍵
    exports = data_dir / "exports"
    for old, new in moves.items():
        for model in CHILDREN:
            session.execute(update(model).where(model.run_id == old).values(run_id=new))
        session.execute(update(Run).where(Run.id == old).values(id=new))
        session.execute(update(Run).where(Run.ai_baseline_run_id == old).values(ai_baseline_run_id=new))
        session.execute(update(AiSummary).where(AiSummary.baseline_run_id == old).values(baseline_run_id=new))
        _rename_exports(session, exports, old, new)
    session.commit()
    return moves


def _rename_exports(session: Session, exports: Path, old: int, new: int) -> None:
    src, dst = exports / str(old), exports / str(new)
    if src.exists():
        shutil.rmtree(dst, ignore_errors=True)  # 殘留的舊資料夾
        src.rename(dst)
    for e in session.scalars(select(Export).where(Export.run_id == new)):
        p = Path(e.path)
        if p.parent == src:
            e.path = str(dst / p.name)
