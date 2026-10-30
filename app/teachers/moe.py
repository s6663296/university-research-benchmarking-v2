"""教育部「教1-1. 專任教師數-以「系(所)」統計」檔案（設計 §5.5）。"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from openpyxl import load_workbook
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.db.models import TeacherCountRow, TeacherImport

TEACHER_COLUMNS = {"total": "專任教師數-教師總數總計", "asst_prof_plus": "專任助理教授以上人數-總計"}
REQUIRED = ("學年度", "學校名稱", "單位代碼", "單位名稱", *TEACHER_COLUMNS.values())


@dataclass(frozen=True)
class MoeRow:
    academic_year: int
    school: str
    unit_code: str
    unit_name: str
    total: int
    asst_prof_plus: int


def _int(value) -> int:
    text = str(value or "0").strip().replace(",", "")
    return int(float(text)) if text else 0


def parse_moe(data: bytes) -> list[MoeRow]:
    wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
    try:
        rows = wb.worksheets[0].iter_rows(values_only=True)
        idx = None
        for _, row in zip(range(10), rows):
            if row and str(row[0] or "").strip() == "學年度":
                header = [str(v or "").strip() for v in row]
                missing = [c for c in REQUIRED if c not in header]
                if missing:
                    raise ValueError(f"教育部檔案缺少欄位：{'、'.join(missing)}")
                idx = {c: header.index(c) for c in REQUIRED}
                break
        if idx is None:
            raise ValueError("找不到「學年度」表頭，請確認是教育部「教1-1」下載檔")
        out = []
        for row in rows:
            year = str(row[idx["學年度"]] or "").strip() if row else ""
            if not year.isdigit():
                continue  # 檔尾統計說明
            out.append(MoeRow(
                academic_year=int(year),
                school=str(row[idx["學校名稱"]]).strip(),
                unit_code=str(row[idx["單位代碼"]]).strip(),
                unit_name=str(row[idx["單位名稱"]]).strip(),
                total=_int(row[idx[TEACHER_COLUMNS["total"]]]),
                asst_prof_plus=_int(row[idx[TEACHER_COLUMNS["asst_prof_plus"]]]),
            ))
        return out
    finally:
        wb.close()


def latest_import(session: Session) -> TeacherImport | None:
    return session.scalars(select(TeacherImport).order_by(TeacherImport.id.desc()).limit(1)).first()


def import_years(imp: TeacherImport) -> list[int]:
    return json.loads(imp.years_json)


def import_moe(session: Session, filename: str, data: bytes) -> tuple[TeacherImport, bool]:
    sha = hashlib.sha256(data).hexdigest()
    existing = session.scalars(select(TeacherImport).where(TeacherImport.sha256 == sha)).first()
    if existing is not None:
        return existing, False
    rows = parse_moe(data)
    imp = TeacherImport(source_filename=filename, sha256=sha, imported_at=datetime.now(),
                        years_json=json.dumps(sorted({r.academic_year for r in rows})))
    session.add(imp)
    session.flush()
    if rows:
        session.execute(insert(TeacherCountRow), [
            dict(teacher_import_id=imp.id, academic_year=r.academic_year, moe_school=r.school,
                 moe_unit_code=r.unit_code, moe_unit_name=r.unit_name, total=r.total,
                 asst_prof_plus=r.asst_prof_plus) for r in rows])
    session.commit()
    return imp, True
