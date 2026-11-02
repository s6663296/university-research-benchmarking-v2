"""教師數：對應教育部單位、人工修改、取得某單位某學年度的人數（設計 §5.5）。"""

from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import TeacherCountRow, TeacherOverride, Unit, UnitTeacherLink
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
    # 實際讀到的教育部各列（人工修改時仍記錄原本的人數）與人工修改紀錄，供分析保存快照（來源說明用）
    rows: list[dict] = field(default_factory=list)
    override: dict | None = None


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


def _in_order(short: str, full: str) -> bool:
    chars = iter(full)
    return all(c in chars for c in short)


def _moe_school_names(session: Session, teacher_import_id: int) -> list[str]:
    """匯入後內容不再變動，同一個 session 內以匯入 id 快取，避免教師數頁面每列都掃一次全表。"""
    cache = session.info.setdefault("moe_school_names", {})
    if teacher_import_id not in cache:
        cache[teacher_import_id] = session.scalars(select(TeacherCountRow.moe_school).distinct().where(
            TeacherCountRow.teacher_import_id == teacher_import_id)).all()
    return cache[teacher_import_id]


def moe_school(session: Session, teacher_import_id: int, school: str) -> str | None:
    """學校名稱以 mapping 分頁為主，不內建校名對照表（對標學校每年可能更動）。
    分頁名稱就是教育部校名時直接採用；否則以「分頁名稱的字依序出現在教育部校名中」且只有一間符合時採用，
    例如「海大」；多間符合時不猜，改由教師數頁面手動對應。"""
    key = school.replace("台", "臺")
    names = _moe_school_names(session, teacher_import_id)
    exact = [n for n in names if n.replace("台", "臺") == key]
    hits = exact or [n for n in names if _in_order(key, n.replace("台", "臺"))]
    return hits[0] if len(hits) == 1 else None


def _loose_name(value: str) -> str:
    """名稱完全相同比不到時才用：「學系所」與「系」、有無「學」字（物理系／物理學系、統計研究所／統計學研究所）視為同名。"""
    return norm_name(value).replace("系所", "系").replace("學", "")


def _all_links(session: Session) -> dict[tuple[str, str], list[str]]:
    """一次讀出全部對應，避免每個單位各查一次（首頁每次載入都會用到）。"""
    out: dict[tuple[str, str], list[str]] = {}
    for school, dept, code in session.execute(select(UnitTeacherLink.school, UnitTeacherLink.dept_zh,
                                                     UnitTeacherLink.moe_unit_code).order_by(UnitTeacherLink.id)):
        out.setdefault((school, dept), []).append(code)
    return out


def unmatched(session: Session, mapping_version_id: int) -> list[Unmatched]:
    return check_links(session, mapping_version_id)[0]


def check_links(session: Session, mapping_version_id: int,
                teacher_import_id: int | None = None) -> tuple[list[Unmatched], list[Unmatched]]:
    """回傳（未對應, 代碼失效）的單位。代碼失效：已對應、但對應的代碼都不在這份教育部資料中
    （例如換了新檔、系所改代碼），教師數會變成缺值。首頁每次載入都會呼叫，查詢次數固定、不隨單位數增加。"""
    links = _all_links(session)
    keys = teacher_keys(session, mapping_version_id)
    missing = [Unmatched(s, d, n) for s, d, n in keys if (s, d) not in links]
    if teacher_import_id is None:
        return missing, []
    codes = {c for s, d, _ in keys for c in links.get((s, d), ())}
    present = {tuple(r) for r in session.execute(
        select(TeacherCountRow.moe_school, TeacherCountRow.moe_unit_code).distinct().where(
            TeacherCountRow.teacher_import_id == teacher_import_id, TeacherCountRow.moe_unit_code.in_(codes)))}
    schools = {s: moe_school(session, teacher_import_id, s) for s, _, _ in keys}
    stale = [Unmatched(s, d, n) for s, d, n in keys if (s, d) in links
             and not any((schools[s], c) in present for c in links[(s, d)])]
    return missing, stale


def auto_link(session: Session, mapping_version_id: int, teacher_import_id: int) -> list[Unmatched]:
    """為尚未對應的單位，以正規化名稱在同校教育部單位中找代碼。"""
    for school, dept, _ in teacher_keys(session, mapping_version_id):
        if _links(session, school, dept):
            continue
        moe_name = moe_school(session, teacher_import_id, school)
        if moe_name is None:
            continue
        rows = session.execute(select(TeacherCountRow.moe_unit_code, TeacherCountRow.moe_unit_name,
                                      TeacherCountRow.academic_year).where(
            TeacherCountRow.teacher_import_id == teacher_import_id,
            TeacherCountRow.moe_school == moe_name)).all()
        codes = {r.moe_unit_code for r in rows if norm_name(r.moe_unit_name) == norm_name(dept)}
        if not codes:
            codes = {r.moe_unit_code for r in rows if _loose_name(r.moe_unit_name) == _loose_name(dept)}
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


def unit_choices(session: Session, teacher_import_id: int, school: str,
                 dept_zh: str | None = None) -> list[tuple[str, str]]:
    """該校最新學年度的（代碼, 名稱）；給了系所名稱時，名稱最接近的排在前面方便挑選。"""
    moe_name = moe_school(session, teacher_import_id, school)
    if moe_name is None:
        return []
    base = (TeacherCountRow.teacher_import_id == teacher_import_id, TeacherCountRow.moe_school == moe_name)
    latest = session.scalar(select(func.max(TeacherCountRow.academic_year)).where(*base))
    rows = session.execute(select(TeacherCountRow.moe_unit_code, TeacherCountRow.moe_unit_name)
                           .where(*base, TeacherCountRow.academic_year == latest)
                           .order_by(TeacherCountRow.moe_unit_code)).all()
    choices = [(r.moe_unit_code, r.moe_unit_name) for r in rows]
    if dept_zh:
        target = _loose_name(dept_zh)
        choices.sort(key=lambda c: -SequenceMatcher(None, target, _loose_name(c[1])).ratio())
    return choices


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
    found = session.scalars(select(TeacherCountRow).where(
        TeacherCountRow.teacher_import_id == teacher_import_id,
        TeacherCountRow.academic_year == academic_year,
        TeacherCountRow.moe_school == moe_school(session, teacher_import_id, school),
        TeacherCountRow.moe_unit_code.in_(codes))).all() if codes else []
    rows = [dict(school=r.moe_school, code=r.moe_unit_code, name=r.moe_unit_name, value=getattr(r, column))
            for r in found]
    override = latest_override(session, school, dept_zh, academic_year)
    if override is not None:
        return TeacherCount(override.count, "manual", override.reason, codes, rows, dict(
            count=override.count, reason=override.reason, created_at=override.created_at.isoformat(timespec="seconds")))
    if not rows:
        return TeacherCount(None, "missing", None, codes)
    return TeacherCount(sum(r["value"] for r in rows), "moe", None, codes, rows)
