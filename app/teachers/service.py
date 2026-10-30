"""教師數：對應教育部單位、人工修改、取得某單位某學年度的人數（設計 §5.5）。"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import SchoolAlias, TeacherCountRow, TeacherOverride, Unit, UnitTeacherLink
from app.mapping.service import units_of
from app.teachers.moe import TEACHER_COLUMNS
from app.textnorm import norm_name


@dataclass(frozen=True)
class Unmatched:
    school: str
    dept_zh: str
    ncu_dept: str


@dataclass
class TeacherCount:
    value: int | None
    source: Literal["moe", "manual", "missing"]
    reason: str | None = None
    unit_codes: list[str] = field(default_factory=list)


def teacher_key(unit: Unit) -> tuple[str, str]:
    """教師數以（學校, 系所中文名稱）對應；中文名稱空白時用中大系所名稱。"""
    return unit.school, unit.dept_zh or unit.ncu_dept


def _links(session: Session, school: str, dept_zh: str) -> list[UnitTeacherLink]:
    return list(session.scalars(select(UnitTeacherLink).where(
        UnitTeacherLink.school == school, UnitTeacherLink.dept_zh == dept_zh).order_by(UnitTeacherLink.id)))


def teacher_keys(session: Session, mapping_version_id: int) -> list[tuple[str, str, str]]:
    seen, out = set(), []
    for u in units_of(session, mapping_version_id):
        school, dept = teacher_key(u)
        if (school, dept) not in seen:
            seen.add((school, dept))
            out.append((school, dept, u.ncu_dept))
    return out


def moe_school(session: Session, school: str) -> str | None:
    alias = session.get(SchoolAlias, school)
    return alias.moe_name if alias else None


def unmatched(session: Session, mapping_version_id: int) -> list[Unmatched]:
    return [Unmatched(s, d, n) for s, d, n in teacher_keys(session, mapping_version_id) if not _links(session, s, d)]


def auto_link(session: Session, mapping_version_id: int, teacher_import_id: int) -> list[Unmatched]:
    """為尚未對應的單位，以正規化名稱在同校教育部單位中找代碼。"""
    for school, dept, _ in teacher_keys(session, mapping_version_id):
        if _links(session, school, dept):
            continue
        moe_name = moe_school(session, school)
        if moe_name is None:
            continue
        rows = session.execute(select(TeacherCountRow.moe_unit_code, TeacherCountRow.moe_unit_name,
                                      TeacherCountRow.academic_year).where(
            TeacherCountRow.teacher_import_id == teacher_import_id,
            TeacherCountRow.moe_school == moe_name)).all()
        target = norm_name(dept)
        codes = {r.moe_unit_code for r in rows if norm_name(r.moe_unit_name) == target}
        if len(codes) > 1:
            latest = max((r.academic_year for r in rows), default=None)
            codes = {r.moe_unit_code for r in rows
                     if r.moe_unit_code in codes and r.academic_year == latest}
        if len(codes) == 1:
            session.add(UnitTeacherLink(school=school, dept_zh=dept, moe_unit_code=codes.pop(), matched_by="auto"))
    session.commit()
    return unmatched(session, mapping_version_id)


def set_links(session: Session, school: str, dept_zh: str, unit_codes: list[str]) -> None:
    session.execute(delete(UnitTeacherLink).where(UnitTeacherLink.school == school,
                                                  UnitTeacherLink.dept_zh == dept_zh))
    session.add_all(UnitTeacherLink(school=school, dept_zh=dept_zh, moe_unit_code=c, matched_by="manual")
                    for c in dict.fromkeys(unit_codes) if c)
    session.commit()


def unit_choices(session: Session, teacher_import_id: int, school: str) -> list[tuple[str, str]]:
    """該校最新學年度的（代碼, 名稱）。"""
    moe_name = moe_school(session, school)
    if moe_name is None:
        return []
    base = (TeacherCountRow.teacher_import_id == teacher_import_id, TeacherCountRow.moe_school == moe_name)
    latest = session.scalar(select(func.max(TeacherCountRow.academic_year)).where(*base))
    rows = session.execute(select(TeacherCountRow.moe_unit_code, TeacherCountRow.moe_unit_name)
                           .where(*base, TeacherCountRow.academic_year == latest)
                           .order_by(TeacherCountRow.moe_unit_code)).all()
    return [(r.moe_unit_code, r.moe_unit_name) for r in rows]


def add_override(session: Session, school: str, dept_zh: str, academic_year: int, count: int, reason: str) -> None:
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("請填寫修改原因")
    if count < 0:
        raise ValueError("教師數不可小於 0")
    session.add(TeacherOverride(school=school, dept_zh=dept_zh, academic_year=academic_year, count=count,
                                reason=reason, created_at=datetime.now()))
    session.commit()


def latest_override(session: Session, school: str, dept_zh: str, academic_year: int) -> TeacherOverride | None:
    return session.scalars(select(TeacherOverride).where(
        TeacherOverride.school == school, TeacherOverride.dept_zh == dept_zh,
        TeacherOverride.academic_year == academic_year).order_by(TeacherOverride.id.desc()).limit(1)).first()


def teacher_count(session: Session, teacher_import_id: int, academic_year: int, column: str,
                  school: str, dept_zh: str) -> TeacherCount:
    if column not in TEACHER_COLUMNS:
        raise ValueError(f"未知的教師數欄位：{column}")
    codes = [link.moe_unit_code for link in _links(session, school, dept_zh)]
    override = latest_override(session, school, dept_zh, academic_year)
    if override is not None:
        return TeacherCount(override.count, "manual", override.reason, codes)
    if not codes:
        return TeacherCount(None, "missing", None, codes)
    col = getattr(TeacherCountRow, column)
    values = session.scalars(select(col).where(
        TeacherCountRow.teacher_import_id == teacher_import_id,
        TeacherCountRow.academic_year == academic_year,
        TeacherCountRow.moe_school == moe_school(session, school),
        TeacherCountRow.moe_unit_code.in_(codes))).all()
    if not values:
        return TeacherCount(None, "missing", None, codes)
    return TeacherCount(sum(values), "moe", None, codes)
