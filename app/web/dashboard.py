"""首頁流程儀表板：依資料庫目前的狀態，判斷自動化流程（設計 §6）每個步驟的進度。"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Anomaly, Export, Run, RunUnit
from app.mapping.service import error_count, latest_version, recheck_mapping
from app.runs.scope import RunConfig
from app.teachers.moe import import_years, latest_import
from app.teachers.service import check_links

STATE_LABELS = {"done": "完成", "active": "進行中", "error": "錯誤", "warn": "需處理", "todo": "尚未開始"}


@dataclass(frozen=True)
class Stage:
    key: str
    label: str
    state: str  # done / active / error / warn / todo
    summary: str = ""
    href: str | None = None
    detail: str = ""  # 第二行補充資訊，例如檔名、匯入時間


def _mapping(session: Session) -> Stage:
    version = latest_version(session)
    if version is None:
        return Stage("mapping", "對標學校資料表檢查", "todo", "尚未匯入", "/mapping")
    recheck_mapping(session, version)
    errors = error_count(session, version.id)
    if errors:
        return Stage("mapping", "對標學校資料表檢查", "error", f"{errors} 項錯誤", "/mapping", version.filename)
    return Stage("mapping", "對標學校資料表檢查", "done", version.filename, "/mapping",
                 f"匯入 {version.imported_at.strftime('%Y/%m/%d %H:%M')}")


def _teachers(session: Session, moe_running: bool = False) -> Stage:
    if moe_running:  # 背景正在從教育部下載
        return Stage("teachers", "教師人數統計表檢查", "active", "從教育部下載中", "/teachers")
    imp, version = latest_import(session), latest_version(session)
    if imp is None:
        return Stage("teachers", "教師人數統計表檢查", "todo", "尚未匯入", "/teachers")
    # 對應與教育部資料都可能在上傳後變動（手動對應、換新檔），每次都依目前的資料重新判斷
    missing, stale = (len(x) for x in check_links(session, version.id, imp.id)) if version else (0, 0)
    years = import_years(imp)
    span = f"{years[0]}～{years[-1]} 學年度" if years else ""
    if missing or stale:
        problems = [f"{missing} 個單位未對應"] * bool(missing) + [f"{stale} 個單位的代碼不在目前資料"] * bool(stale)
        return Stage("teachers", "教師人數統計表檢查", "warn", "、".join(problems), "/teachers", span)
    return Stage("teachers", "教師人數統計表檢查", "done", span, "/teachers", imp.source_filename)


HALTED = {"stopped": "已停止", "failed": "分析失敗"}  # 其餘未在背景執行的狀態都視為中斷


def _fetch_stage(key: str, label: str, run: Run, running: bool, done: int, failed: int, waiting: int, total: int,
                 started: bool) -> Stage:
    """running：背景程式是否正在執行這次分析；done／failed：做完／卡在這一步的單位數；
    waiting：還沒做完的單位數；started：已有單位進到這一步。"""
    href = f"/runs/{run.id}/fetch"
    progress = f"{done}／{total}"
    if failed:
        return Stage(key, label, "error", f"{failed} 個單位失敗" + (f"，{progress}" if waiting else ""), href)
    if not waiting:
        return Stage(key, label, "done", f"{done} 個單位完成", href)
    if running:
        return Stage(key, label, "active" if started else "todo", progress, href)
    return Stage(key, label, "warn", f"{HALTED.get(run.status, '已中斷')} {progress}", href)


def _run_stages(session: Session, run: Run, running: bool) -> list[Stage]:
    rus = session.scalars(select(RunUnit).where(RunUnit.run_id == run.id)).all()
    total = len(rus)
    # eid_count 在 Scopus 完成時寫入（暫停退回 pending 也保留），以它判斷單位是否已過 Scopus 這一步
    scopus_failed = sum(1 for ru in rus if ru.status == "failed" and ru.eid_count is None)
    scival_failed = sum(1 for ru in rus if ru.status == "failed" and ru.eid_count is not None)
    past_scopus = sum(1 for ru in rus if ru.eid_count is not None)
    finished = sum(1 for ru in rus if ru.status == "done")
    scival_total = total - scopus_failed
    out = [
        _fetch_stage("scopus", "Scopus 取 EID", run, running, past_scopus, scopus_failed,
                     total - past_scopus - scopus_failed, total, started=True),
        _fetch_stage("scival", "SciVal 取指標", run, running, finished, scival_failed,
                     scival_total - finished - scival_failed, scival_total, started=past_scopus > 0),
    ]
    fetched = all(ru.status in ("done", "failed") for ru in rus)
    detail = f"/runs/{run.id}"
    if running:
        out.append(Stage("compute", "計算與異常", "active", "計算中", detail) if fetched
                   else Stage("compute", "計算與異常", "todo"))
    elif run.status == "done":
        out.append(Stage("compute", "計算與異常", "done", "完成", detail))
    elif run.status == "failed":
        out.append(Stage("compute", "計算與異常", "error", run.error or "分析失敗", detail))
    else:
        out.append(Stage("compute", "計算與異常", "todo"))

    if running or run.status != "done":  # 重抓單位時舊結果會被重算，等背景做完再顯示
        return out + [Stage("review", "人工確認", "todo"), Stage("output", "產出", "todo")]
    open_count = session.scalar(select(func.count()).select_from(Anomaly).where(
        Anomaly.run_id == run.id, Anomaly.status != "confirmed"))
    out.append(Stage("review", "人工確認", "warn" if open_count else "done",
                     f"{open_count} 項待確認" if open_count else "無待確認", f"{detail}?ind=anomalies"))
    exports = session.scalar(select(func.count()).select_from(Export).where(Export.run_id == run.id))
    out.append(Stage("output", "產出", "done" if exports else "todo",
                     f"已產出 {exports} 份" if exports else "尚未下載", f"{detail}?ind=downloads"))
    return out


def pipeline(session: Session, run_id: int | None = None, running: bool = False,
             moe_running: bool = False) -> list[Stage]:
    """流程的 8 個步驟，進度一律以背景程式的狀態為準：
    run_id 是背景程式最後啟動的分析、running 是它是否仍在執行、moe_running 是否正從教育部下載教師資料
    （服務重啟後背景沒有任何分析，儀表板就回到尚未執行，舊分析仍可在歷史頁查看）。"""
    stages = [_mapping(session), _teachers(session, moe_running)]
    run = session.get(Run, run_id) if run_id is not None else None
    if run is None:
        return stages + [Stage("run", "建立分析", "todo", "前往分析設定", "/runs/new")] + [
            Stage(k, label, "todo") for k, label in (("scopus", "Scopus 取 EID"), ("scival", "SciVal 取指標"),
                                                     ("compute", "計算與異常"), ("review", "人工確認"),
                                                     ("output", "產出"))]
    depts = "、".join(RunConfig.from_json(run.scope_json).ncu_depts)
    stages.append(Stage("run", "建立分析", "done", f"#{run.id} {depts}", f"/runs/{run.id}"))
    return stages + _run_stages(session, run, running)
