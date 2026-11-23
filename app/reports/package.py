"""寄給各系所主管的資料包：Word 信件、對標學校確認表、教師人數確認表、系所簡報（研發處 2026-11-23 第 6～10 題）。

信件範本可在設定頁換成研發處確認版（存在 data/letter_template/），沒有上傳時用 templates/letter_template.docx。
範本中的標記會換成實際內容，見 letter_values；研發處草稿原本用 OOO、XXX 代表兩份 Excel 的檔名，也照樣替換。
"""

import re
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Run, RunUnit, Unit
from app.mapping.parser import is_ncu
from app.mapping.service import units_of
from app.reports.exports import export_path
from app.reports.provenance import teacher_text
from app.runs.scope import RunConfig, build_groups

DEFAULT_LETTER = Path(__file__).resolve().parents[2] / "templates" / "letter_template.docx"
DEFAULT_LETTER_NAME = "給各位主任所長.docx"
LETTER_DIR = "letter_template"

# 範本中可用的標記（設定頁列給使用者看）
MARKERS = {
    "{{系所}}": "系所名稱",
    "{{學院}}": "系所所屬學院",
    "{{期間}}": "分析期間，例如 2021-2025",
    "{{對標學校確認表}}": "對標學校確認表的檔名（研發處草稿中的 OOO）",
    "{{教師人數確認表}}": "教師人數確認表的檔名（研發處草稿中的 XXX）",
}
LEGACY_MARKERS = {"OOO": "{{對標學校確認表}}", "XXX": "{{教師人數確認表}}"}

PREFILLED = PatternFill("solid", fgColor="EDEDED")   # 系統填入
TO_FILL = PatternFill("solid", fgColor="FFF2CC")     # 請單位填寫
HEADER_FONT = Font(bold=True)
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")


class LetterTemplateError(ValueError):
    pass


@dataclass(frozen=True)
class LetterTemplate:
    path: Path
    filename: str        # 放進資料包時的檔名
    is_default: bool


def safe_name(text: str) -> str:
    """檔名與 zip 內路徑不能有的字元換成「_」。"""
    return re.sub(r'[\\/:*?"<>|]', "_", text).strip() or "_"


def benchmark_filename(dept: str) -> str:
    return f"{safe_name(dept)}_對標學校確認表.xlsx"


def teacher_filename(dept: str) -> str:
    return f"{safe_name(dept)}_教師人數確認表.xlsx"


# ---------- 信件範本 ----------

def _upload_dir(settings: Settings) -> Path:
    return settings.data_dir / LETTER_DIR


def letter_template(settings: Settings) -> LetterTemplate:
    uploaded = sorted(_upload_dir(settings).glob("*.docx")) if _upload_dir(settings).exists() else []
    if uploaded:
        return LetterTemplate(uploaded[0], uploaded[0].name, False)
    return LetterTemplate(DEFAULT_LETTER, DEFAULT_LETTER_NAME, True)


def _paragraphs(doc):
    """內文、表格（含巢狀）、頁首頁尾中的所有段落。"""
    def walk(container):
        yield from container.paragraphs
        for table in getattr(container, "tables", []):
            for row in table.rows:
                for cell in row.cells:
                    yield from walk(cell)

    yield from walk(doc)
    for section in doc.sections:
        for part in (section.header, section.footer, section.first_page_header, section.first_page_footer,
                     section.even_page_header, section.even_page_footer):
            if not part.is_linked_to_previous:
                yield from walk(part)


def _all_markers() -> list[str]:
    return sorted([*MARKERS, *LEGACY_MARKERS], key=len, reverse=True)


def markers_in(path: Path) -> list[str]:
    """範本中出現的標記（OOO、XXX 以對應的新標記表示），依 MARKERS 的順序。"""
    found = set()
    for p in _paragraphs(Document(str(path))):
        text = p.text
        for m in _all_markers():
            if m in text:
                found.add(LEGACY_MARKERS.get(m, m))
    return [m for m in MARKERS if m in found]


def save_letter_template(settings: Settings, filename: str, data: bytes) -> list[str]:
    """驗證並存下上傳的範本，取代之前上傳的；回傳範本中找到的標記。"""
    name = safe_name(Path(filename or DEFAULT_LETTER_NAME).name)
    if not name.lower().endswith(".docx"):
        raise LetterTemplateError("範本必須是 Word 的 .docx 檔（.doc 請先在 Word 另存成 .docx）")
    folder = _upload_dir(settings)
    tmp = settings.data_dir / f".{LETTER_DIR}.upload"
    tmp.write_bytes(data)
    try:
        found = markers_in(tmp)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise LetterTemplateError("無法讀取這個 Word 檔，請確認是 .docx 格式") from e
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    tmp.replace(folder / name)
    return found


def reset_letter_template(settings: Settings) -> None:
    shutil.rmtree(_upload_dir(settings), ignore_errors=True)


def _replace_in_paragraph(paragraph, values: dict[str, str]) -> None:
    """替換段落中的標記；標記被 Word 拆在好幾個 run 時，取代文字放在第一個 run，保留其他文字的格式。"""
    runs = paragraph.runs
    if not runs or not any(m in paragraph.text for m in values):
        return
    pattern = re.compile("|".join(re.escape(m) for m in sorted(values, key=len, reverse=True)))
    text = "".join(r.text for r in runs)
    for match in reversed(list(pattern.finditer(text))):   # 由後往前，前面的位置不受影響
        start, end = match.span()
        pos, first = 0, True
        for r in runs:
            a, b = pos, pos + len(r.text)
            pos = b
            if b <= start or a >= end:
                continue
            s, e = max(start, a) - a, min(end, b) - a
            r.text = r.text[:s] + (values[match.group()] if first else "") + r.text[e:]
            first = False


def fill_letter(template: Path, values: dict[str, str], out_path: Path) -> Path:
    doc = Document(str(template))
    full = {**values, **{legacy: values[new] for legacy, new in LEGACY_MARKERS.items() if new in values}}
    for p in _paragraphs(doc):
        _replace_in_paragraph(p, full)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path


# ---------- 兩份確認表 ----------

@dataclass(frozen=True)
class DeptContext:
    run: Run
    cfg: RunConfig
    dept: str
    college: str
    ncu: Unit | None
    group_ids: tuple[int, ...]       # 本次分析的對標組（中大在前）；沒有做對標比較時只有中大自己
    units: list[Unit]                # mapping 中這個中大系所在各校的對應單位

    @property
    def period(self) -> str:
        return f"{self.run.period_from}-{self.run.period_to}"


def dept_context(session: Session, run: Run, dept: str) -> DeptContext:
    cfg = RunConfig.from_json(run.scope_json)
    all_units = units_of(session, run.mapping_version_id)
    units = [u for u in all_units if u.ncu_dept == dept]
    ncu = next((u for u in units if is_ncu(u.school)), None)
    group = next((g for g in build_groups(all_units, cfg) if g.comparison == "benchmark" and g.key == dept), None)
    ids = group.unit_ids if group else ((ncu.id,) if ncu else ())
    return DeptContext(run, cfg, dept, (ncu.ncu_college if ncu else None) or "", ncu, ids, units)


def letter_values(ctx: DeptContext) -> dict[str, str]:
    return {"{{系所}}": ctx.dept, "{{學院}}": ctx.college, "{{期間}}": ctx.period,
            "{{對標學校確認表}}": benchmark_filename(ctx.dept), "{{教師人數確認表}}": teacher_filename(ctx.dept)}


def _write_table(ws, title: str, notes: list[str], headers: list[str], prefilled: int, rows: list[list],
                 widths: list[int]) -> int:
    """標題、說明、表頭與資料列；前 prefilled 欄灰底（系統填入），其餘黃底（請填寫）。回傳表頭的列號。"""
    ws.append([title])
    ws.cell(1, 1).font = Font(bold=True, size=14)
    for n in notes:
        ws.append([n])
    ws.append([])
    header_row = ws.max_row + 1
    ws.append(headers)
    for c, _ in enumerate(headers, 1):
        cell = ws.cell(header_row, c)
        cell.font, cell.border, cell.alignment = HEADER_FONT, BORDER, WRAP
        cell.fill = PREFILLED if c <= prefilled else TO_FILL
    for row in rows:
        ws.append(row)
        r = ws.max_row
        for c in range(1, len(headers) + 1):
            cell = ws.cell(r, c)
            cell.border, cell.alignment = BORDER, WRAP
            cell.fill = PREFILLED if c <= prefilled else TO_FILL
    for c, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(header_row, c).column_letter].width = w
    ws.freeze_panes = ws.cell(header_row + 1, 1)
    return header_row


def benchmark_workbook(session: Session, ctx: DeptContext, out_path: Path) -> Path:
    """對標學校確認表：mapping 表中這個系所在各校的對應單位，請單位確認是否合宜、修正英文名稱。"""
    in_run = set(ctx.group_ids)
    schools = list(dict.fromkeys(u.school for u in units_of(session, ctx.run.mapping_version_id)))
    by_school = {u.school: u for u in ctx.units}
    rows = []
    for school in sorted(schools, key=lambda s: not is_ncu(s)):   # 中大（本系所）在第一列
        u = by_school.get(school)
        label = f"{school}（本系所）" if is_ncu(school) else school
        if u is None:
            rows.append([label, "（mapping 表無對應系所）", "", "本次分析未納入", "", "", ""])
        else:
            rows.append([label, u.dept_zh or "", u.dept_en, "" if u.id in in_run else "本次分析未納入", "", "", ""])
    wb = Workbook()
    ws = wb.active
    ws.title = "對標學校確認"
    header = _write_table(
        ws, f"{ctx.dept} 對標學校確認表（{ctx.period}）",
        ["灰底欄位由系統依 mapping 表填入；黃底欄位請貴單位填寫。",
         "Scopus 機構英文名稱是查詢論文的條件，同一系所有多個名稱（改名、不同寫法）時請以分號（;）分隔。",
         "若建議更換或新增對標學校／系所，請填在「建議調整的學校／系所」，並附上該系所的英文名稱。"],
        ["對標學校", "系所中文名稱", "Scopus 機構英文名稱", "備註", "是否合宜", "建議調整的學校／系所",
         "修正或新增的英文名稱（多個以分號分隔）"],
        4, rows, [16, 22, 60, 16, 12, 28, 60])
    if rows:
        dv = DataValidation(type="list", formula1='"合宜,不合宜"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"E{header + 1}:E{header + len(rows)}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path


def teacher_workbook(session: Session, ctx: DeptContext, out_path: Path) -> Path:
    """教師人數確認表：本次分析使用的本系所與各對標系所教師數，請單位填修正後人數與理由。"""
    run_units = {ru.unit_id: ru for ru in session.scalars(
        select(RunUnit).where(RunUnit.run_id == ctx.run.id, RunUnit.unit_id.in_(ctx.group_ids)))}
    rows = []
    for uid in ctx.group_ids:
        u, ru = session.get(Unit, uid), run_units.get(uid)
        label = f"{u.school}（本系所）" if is_ncu(u.school) else u.school
        count = ru.teacher_count if ru and ru.teacher_count is not None else "查無資料"
        rows.append([label, u.dept_zh or u.ncu_dept, ctx.cfg.academic_year, count,
                     teacher_text(ru) if ru else "", "", ""])
    wb = Workbook()
    ws = wb.active
    ws.title = "教師人數確認"
    _write_table(
        ws, f"{ctx.dept} 教師人數確認表（{ctx.period}）",
        ["灰底欄位為本次分析使用的教師數（教育部大專校院校務資訊公開平台的專任教師數）；黃底欄位請貴單位填寫。",
         "人數正確時黃底欄位留空即可；有誤時請填修正後人數與理由。"],
        ["學校", "系所", "學年度", "教育部教師數", "來源說明", "修正後人數", "修正理由"],
        5, rows, [16, 22, 10, 14, 60, 14, 40])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path


# ---------- 打包 ----------

Progress = Callable[[int, int], None]
# (session, run, dept, 輸出資料夾) → 簡報路徑（檔名由它決定）；由網頁層提供，才能帶入 AI 總結
BuildReport = Callable[[Session, Run, str, Path], Path]


def dept_files(session: Session, settings: Settings, run: Run, dept: str, build_report: BuildReport,
               progress: Callable[[], None] = lambda: None) -> list[tuple[Path, str]]:
    """一個系所的四份檔案：[(檔案路徑, 放進 zip 的檔名)]；每完成一份呼叫一次 progress。"""
    ctx = dept_context(session, run, dept)
    folder = export_path(settings, run.id, "package") / safe_name(dept)
    folder.mkdir(parents=True, exist_ok=True)
    template = letter_template(settings)
    out = [(fill_letter(template.path, letter_values(ctx), folder / template.filename), template.filename)]
    progress()
    for build, name in ((benchmark_workbook, benchmark_filename(dept)), (teacher_workbook, teacher_filename(dept))):
        out.append((build(session, ctx, folder / name), name))
        progress()
    report = build_report(session, run, dept, folder)
    out.append((report, report.name))
    progress()
    return out


FILES_PER_DEPT = 4


def write_dept_package(session: Session, settings: Settings, run: Run, dept: str, build_report: BuildReport,
                       progress: Progress = lambda d, t: None) -> Path:
    """單一系所的 zip：四份檔案放在最上層。"""
    zip_path = export_path(settings, run.id, f"{run.period_from}-{run.period_to}{safe_name(dept)}_寄送資料.zip")
    done = 0
    progress(0, FILES_PER_DEPT)

    def step():
        nonlocal done
        done += 1
        progress(done, FILES_PER_DEPT)

    files = dept_files(session, settings, run, dept, build_report, step)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, name in files:
            zf.write(path, name)
    return zip_path


def write_all_packages(session: Session, settings: Settings, run: Run, build_report: BuildReport,
                       progress: Progress = lambda d, t: None) -> Path:
    """全部系所的 zip：每個系所一個資料夾。進度以系所為單位。"""
    depts = RunConfig.from_json(run.scope_json).ncu_depts
    zip_path = export_path(settings, run.id, f"{run.period_from}-{run.period_to}各系所寄送資料.zip")
    progress(0, len(depts))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, dept in enumerate(depts, 1):
            for path, name in dept_files(session, settings, run, dept, build_report):
                zf.write(path, f"{safe_name(dept)}/{name}")
            progress(i, len(depts))
    return zip_path
