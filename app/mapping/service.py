"""Mapping 版本：匯入、查詢、差異。"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MappingIssue, MappingVersion, Unit
from app.mapping.parser import parse_mapping

UnitKey = tuple[str, str]  # (school, ncu_dept)


@dataclass
class MappingDiff:
    added: list[UnitKey] = field(default_factory=list)
    removed: list[UnitKey] = field(default_factory=list)
    changed: list[tuple[UnitKey, str, str]] = field(default_factory=list)


def latest_version(session: Session) -> MappingVersion | None:
    return session.scalars(select(MappingVersion).order_by(MappingVersion.id.desc()).limit(1)).first()


def previous_version(session: Session, version_id: int) -> MappingVersion | None:
    return session.scalars(select(MappingVersion).where(MappingVersion.id < version_id)
                           .order_by(MappingVersion.id.desc()).limit(1)).first()


def units_of(session: Session, version_id: int) -> list[Unit]:
    return list(session.scalars(select(Unit).where(Unit.mapping_version_id == version_id).order_by(Unit.id)))


def issues_of(session: Session, version_id: int) -> list[MappingIssue]:
    return list(session.scalars(select(MappingIssue).where(MappingIssue.mapping_version_id == version_id)
                                .order_by(MappingIssue.id)))


def import_mapping(session: Session, filename: str, data: bytes) -> tuple[MappingVersion, bool]:
    """匯入 mapping；內容與最新版相同時不建立新版本。解析失敗時拋出例外，不寫入資料庫。"""
    sha = hashlib.sha256(data).hexdigest()
    latest = latest_version(session)
    if latest is not None and latest.sha256 == sha:
        return latest, False
    parsed = parse_mapping(data)
    version = MappingVersion(filename=filename, sha256=sha, imported_at=datetime.now())
    session.add(version)
    session.flush()
    session.add_all(Unit(mapping_version_id=version.id, school=u.school, college=u.college, dept_zh=u.dept_zh,
                         dept_en=u.dept_en, ncu_college=u.ncu_college, ncu_dept=u.ncu_dept, remark=u.remark)
                    for u in parsed.units)
    session.add_all(MappingIssue(mapping_version_id=version.id, sheet=i.sheet, row=i.row, level=i.level,
                                 message=i.message) for i in parsed.issues)
    session.commit()
    return version, True


def diff_versions(session: Session, old_id: int | None, new_id: int) -> MappingDiff:
    old = {(u.school, u.ncu_dept): u.dept_en for u in units_of(session, old_id)} if old_id else {}
    new = {(u.school, u.ncu_dept): u.dept_en for u in units_of(session, new_id)}
    return MappingDiff(
        added=sorted(k for k in new if k not in old),
        removed=sorted(k for k in old if k not in new),
        changed=sorted((k, old[k], new[k]) for k in new if k in old and old[k] != new[k]),
    )
