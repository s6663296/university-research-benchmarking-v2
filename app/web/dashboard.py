"""首頁流程儀表板：依資料庫目前的狀態，判斷自動化流程（設計 §6）每個步驟的進度。"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Anomaly, Export, Run, RunUnit
from app.elsevier.inset_selfcite import InSetSelfCitations
from app.elsevier.selfcite import get_strategy
from app.funding.service import effective_imports, imports as funding_imports
from app.mapping.service import error_count, latest_version, recheck_mapping
from app.runs.executor import SELFCITE_ERROR
from app.runs.scope import RunConfig
from app.teachers.moe import import_years, latest_import
from app.teachers.service import check_links

STATE_LABELS = {"done": "完成", "active": "進行中", "error": "錯誤", "warn": "需處理", "todo": "尚未開始"}
FETCH_LABEL = "Scopus／SciVal 取指標"
SELFCITE_LABEL = "排除自我引用"
REVIEW_LABEL = "計算與異常確認"
LABEL_TAILS = ("檢查", "取指標", "自我引用", "確認")  # 這些結尾固定放第二行


@dataclass(frozen=True)
class Stage:
    key: str
    label: str
    state: str  # done / active / error / warn / todo
    summary: str = ""
    href: str | None = None
    detail: str = ""  # 第二行補充資訊，例如檔名、匯入時間
    progress: tuple[str, ...] = ()  # 列在摘要上方的逐行進度

    @property
    def label_parts(self) -> tuple[str, str]:
        """名稱拆成兩行：固定在語意處斷行，不讓瀏覽器斷在詞中間。"""
        for tail in LABEL_TAILS:
            if self.label.endswith(tail):
                return self.label[:-len(tail)], tail
        return self.label, ""


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


def _funding(session: Session) -> Stage:
    label = "計畫經費統計表檢查"
    files = funding_imports(session)  # 新的在前
    if not files:
        return Stage("funding", label, "todo", "略過", "/funding")
    years = list(effective_imports(session))
    return Stage("funding", label, "done", f"{years[0]}～{years[-1]} 年度" if years else "", "/funding", files[0].filename)


HALTED = {"stopped": "已停止", "failed": "分析失敗"}  # 其餘未在背景執行的狀態都視為中斷


def _selfcite_failed(ru: RunUnit) -> bool:
    return ru.status == "failed" and (ru.error or "").startswith(SELFCITE_ERROR)


def _fetch_stage(run: Run, rus: list[RunUnit], running: bool) -> Stage:
    """Scopus 取 EID 與 SciVal 取指標合併成一步；running：背景程式是否正在執行這次分析。"""
    href = f"/runs/{run.id}/fetch"
    total = len(rus)
    # 已過 SciVal：正在或已完成排除自我引用，或在排除自我引用這一步失敗
    finished = sum(1 for ru in rus if ru.status in ("selfcite", "done") or _selfcite_failed(ru))
    failed = sum(1 for ru in rus if ru.status == "failed" and not _selfcite_failed(ru))
    waiting = total - finished - failed
    # eid_count 在 Scopus 完成時寫入（暫停退回 pending 也保留），以它判斷單位是否已過 Scopus 這一步
    past_scopus = sum(1 for ru in rus if ru.eid_count is not None)
    progress = (f"Scopus {past_scopus}／{total}", f"SciVal {finished}／{total}")

    def stage(state: str, summary: str = "") -> Stage:
        return Stage("fetch", FETCH_LABEL, state, summary, href, progress=progress)

    if failed:
        return stage("error", f"{failed} 個失敗")
    if not waiting:
        return stage("done")
    if running:
        return stage("active")
    return stage("warn", HALTED.get(run.status, "已中斷"))


def _selfcite_detail(live: InSetSelfCitations | None) -> tuple[str, ...]:
    """單位內的細部進度：大單位要抓上千篇參考文獻，只看完成的單位數會以為卡住。"""
    if live is None:
        return ()
    lines = []
    if live.refs_total:
        lines.append(f"參考文獻 {live.refs_done:,}／{live.refs_total:,} 篇")
    if live.scopus_only or live.abstract_exhausted:
        lines.append(f"Scopus 查詢 {live.searches:,} 次")
    return tuple(lines)


def _selfcite_stage(run: Run, rus: list[RunUnit], running: bool,
                    live: InSetSelfCitations | None = None) -> Stage:
    """SciVal 取完指標後，各單位減去組內互引；設定為不排除時略過。live：執行中的細部進度。"""
    href = f"/runs/{run.id}/fetch"
    if not get_strategy(run.self_citation_strategy).in_set:
        return Stage("selfcite", SELFCITE_LABEL, "done", "設定為不排除", href)
    total = len(rus)
    finished = sum(1 for ru in rus if ru.status == "done")
    failed = sum(1 for ru in rus if _selfcite_failed(ru))
    # 在前面步驟失敗的單位不會走到這一步，不算在等待中
    waiting = total - finished - failed - sum(1 for ru in rus if ru.status == "failed" and not _selfcite_failed(ru))
    progress = (f"單位 {finished}／{total}",) + (_selfcite_detail(live) if running else ())

    def stage(state: str, summary: str = "") -> Stage:
        return Stage("selfcite", SELFCITE_LABEL, state, summary, href, progress=progress)

    if failed:
        return stage("error", f"{failed} 個失敗")
    if not waiting:
        return stage("done")
    if running:
        started = finished or any(ru.status == "selfcite" for ru in rus)
        return stage("active" if started else "todo")
    return stage("warn", HALTED.get(run.status, "已中斷"))


def _review_stage(session: Session, run: Run, rus: list[RunUnit], running: bool) -> Stage:
    """計算與異常、人工確認合併成一步。"""
    detail = f"/runs/{run.id}"
    if running:  # 重抓單位時舊結果會被重算，等背景做完再顯示
        if all(ru.status in ("done", "failed") for ru in rus):
            return Stage("review", REVIEW_LABEL, "active", "計算中", detail)
        return Stage("review", REVIEW_LABEL, "todo")
    if run.status == "failed":
        return Stage("review", REVIEW_LABEL, "error", run.error or "分析失敗", detail)
    if run.status != "done":
        return Stage("review", REVIEW_LABEL, "todo")
    open_count = session.scalar(select(func.count()).select_from(Anomaly).where(
        Anomaly.run_id == run.id, Anomaly.status != "confirmed"))
    return Stage("review", REVIEW_LABEL, "warn" if open_count else "done",
                 f"{open_count} 項待確認" if open_count else "", f"{detail}?ind=anomalies")


def _run_stages(session: Session, run: Run, running: bool, selfcite: InSetSelfCitations | None) -> list[Stage]:
    rus = list(session.scalars(select(RunUnit).where(RunUnit.run_id == run.id)))
    out = [_fetch_stage(run, rus, running), _selfcite_stage(run, rus, running, selfcite),
           _review_stage(session, run, rus, running)]
    if running or run.status != "done":
        return out + [Stage("output", "產出", "todo")]
    exports = session.scalar(select(func.count()).select_from(Export).where(Export.run_id == run.id))
    out.append(Stage("output", "產出", "done" if exports else "todo",
                     f"已產出 {exports} 份" if exports else "尚未下載", f"/runs/{run.id}?ind=downloads"))
    return out


def pipeline(session: Session, run_id: int | None = None, running: bool = False,
             moe_running: bool = False, selfcite: InSetSelfCitations | None = None) -> list[Stage]:
    """流程的 8 個步驟，進度一律以背景程式的狀態為準：
    run_id 是背景程式最後啟動的分析、running 是它是否仍在執行、moe_running 是否正從教育部下載教師資料、
    selfcite 是這個分析排除自我引用的細部進度
    （服務重啟後背景沒有任何分析，儀表板就回到尚未執行，舊分析仍可在歷史頁查看）。"""
    stages = [_mapping(session), _teachers(session, moe_running), _funding(session)]
    run = session.get(Run, run_id) if run_id is not None else None
    if run is None:
        return stages + [Stage("run", "建立分析", "todo", "", "/runs/new")] + [
            Stage(k, label, "todo") for k, label in (("fetch", FETCH_LABEL), ("selfcite", SELFCITE_LABEL),
                                                     ("review", REVIEW_LABEL), ("output", "產出"))]
    depts = "、".join(RunConfig.from_json(run.scope_json).ncu_depts)
    stages.append(Stage("run", "建立分析", "done", f"#{run.id} {depts}", f"/runs/{run.id}"))
    return stages + _run_stages(session, run, running, selfcite)
