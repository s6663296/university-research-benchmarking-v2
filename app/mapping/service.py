"""Mapping 版本：匯入、查詢、差異。"""

import hashlib
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import textnorm
from app.db.models import MappingIssue, MappingVersion, Unit
from app.mapping import parser
from app.mapping.parser import Issue, ParseResult, parse_mapping

UnitKey = tuple[str, str]  # (school, ncu_dept)

# 檢查規則的指紋：解析與檢查都在這兩個模組，程式改了指紋就不同，已匯入的版本需要重新檢查
RULES_HASH = hashlib.sha256(b"".join(Path(m.__file__).read_bytes() for m in (parser, textnorm))).hexdigest()
_recheck_lock = threading.Lock()


@dataclass
class MappingDiff:
    added: list[UnitKey] = field(default_factory=list)
    removed: list[UnitKey] = field(default_factory=list)
    changed: list[tuple[UnitKey, str, str]] = field(default_factory=list)


def latest_version(session: Session) -> MappingVersion | None:
    """目前使用的版本；尚未匯入時為 None。"""
    return session.scalars(select(MappingVersion).where(MappingVersion.retired_at.is_(None))
                           .order_by(MappingVersion.id.desc()).limit(1)).first()


def previous_version(session: Session, version_id: int) -> MappingVersion | None:
    return session.scalars(select(MappingVersion).where(MappingVersion.id < version_id)
                           .order_by(MappingVersion.id.desc()).limit(1)).first()


def units_of(session: Session, version_id: int) -> list[Unit]:
    return list(session.scalars(select(Unit).where(Unit.mapping_version_id == version_id).order_by(Unit.id)))


def issues_of(session: Session, version_id: int) -> list[MappingIssue]:
    return list(session.scalars(select(MappingIssue).where(MappingIssue.mapping_version_id == version_id)
                                .order_by(MappingIssue.id)))


def import_mapping(session: Session, filename: str, data: bytes) -> tuple[MappingVersion, bool]:
    """匯入 mapping；檔案與最新版相同且解析出的單位也相同時不建立新版本，只依目前的規則重新檢查。
    啟動程式後重新匯入同一份檔案時沿用舊版本，讓它再次成為目前資料（第二個回傳值同樣為 True，表示匯入成功）。
    解析失敗時拋出例外，不寫入資料庫。"""
    sha = hashlib.sha256(data).hexdigest()
    latest = session.scalars(select(MappingVersion).order_by(MappingVersion.id.desc()).limit(1)).first()
    parsed = parse_mapping(data)
    if latest is not None and latest.sha256 == sha and _same_units(session, latest.id, parsed):
        _replace_issues(session, latest.id, parsed.issues)
        latest.content, latest.rules_hash = data, RULES_HASH
        reactivated = latest.retired_at is not None
        if reactivated:
            latest.retired_at, latest.imported_at, latest.filename = None, datetime.now(), filename
        session.commit()
        return latest, reactivated
    version = MappingVersion(filename=filename, sha256=sha, imported_at=datetime.now(), content=data,
                             rules_hash=RULES_HASH)
    session.add(version)
    session.flush()
    session.add_all(Unit(mapping_version_id=version.id, school=u.school, college=u.college, dept_zh=u.dept_zh,
                         dept_en=u.dept_en, ncu_college=u.ncu_college, ncu_dept=u.ncu_dept, remark=u.remark)
                    for u in parsed.units)
    _replace_issues(session, version.id, parsed.issues)
    session.commit()
    return version, True


def recheck_mapping(session: Session, version: MappingVersion) -> None:
    """依目前的規則重新檢查已匯入的 mapping。首頁每次載入都會呼叫：規則指紋與上次檢查相同時結果必然相同，
    直接略過，不讀原始檔；只有程式更新後的第一次才重新解析。舊版本沒有保存原始檔時保留匯入當時的結果。"""
    if version.rules_hash == RULES_HASH:
        return
    with _recheck_lock:  # 首頁與輪詢可能同時進來，只檢查一次
        session.expire(version, ["rules_hash"])
        if version.rules_hash == RULES_HASH or version.content is None:
            return
        parsed = parse_mapping(version.content)
        issues = list(parsed.issues)
        if not _same_units(session, version.id, parsed):
            issues.insert(0, Issue("（全部）", None, "warning",
                                   "依目前的規則解析出的單位與匯入時不同，請重新上傳此檔建立新版本"))
        _replace_issues(session, version.id, issues)
        version.rules_hash = RULES_HASH
        session.commit()


def error_count(session: Session, version_id: int) -> int:
    return session.scalar(select(func.count()).select_from(MappingIssue).where(
        MappingIssue.mapping_version_id == version_id, MappingIssue.level == "error"))


def _same_units(session: Session, version_id: int, parsed: ParseResult) -> bool:
    return sorted((u.school, u.ncu_dept, u.dept_en) for u in units_of(session, version_id))         == sorted((u.school, u.ncu_dept, u.dept_en) for u in parsed.units)


def _replace_issues(session: Session, version_id: int, issues: list[Issue]) -> None:
    session.execute(delete(MappingIssue).where(MappingIssue.mapping_version_id == version_id))
    session.add_all(MappingIssue(mapping_version_id=version_id, sheet=i.sheet, row=i.row, level=i.level,
                                 message=i.message) for i in issues)


def diff_versions(session: Session, old_id: int | None, new_id: int) -> MappingDiff:
    old = {(u.school, u.ncu_dept): u.dept_en for u in units_of(session, old_id)} if old_id else {}
    new = {(u.school, u.ncu_dept): u.dept_en for u in units_of(session, new_id)}
    return MappingDiff(
        added=sorted(k for k in new if k not in old),
        removed=sorted(k for k in old if k not in new),
        changed=sorted((k, old[k], new[k]) for k in new if k in old and old[k] != new[k]),
    )
