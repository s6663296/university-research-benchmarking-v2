"""寄給各系所的資料包：信件範本填入、兩份確認表、打包下載、設定頁換範本。"""

import io
import time
import zipfile

from docx import Document
from openpyxl import load_workbook

from app.reports.package import (DEFAULT_LETTER_NAME, benchmark_filename, benchmark_workbook, dept_context,
                                 fill_letter, letter_template, markers_in, teacher_filename, teacher_workbook,
                                 write_all_packages, write_dept_package)

DEPT = "資訊管理學系"
BENCH = "資訊管理學系_對標學校確認表.xlsx"
TEACH = "資訊管理學系_教師人數確認表.xlsx"


def docx_bytes(build) -> bytes:
    doc = Document()
    build(doc)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def all_text(path) -> str:
    doc = Document(str(path))
    parts = [p.text for p in doc.paragraphs]
    parts += [c.text for t in doc.tables for row in t.rows for c in row.cells]
    parts += [p.text for s in doc.sections for p in s.header.paragraphs]
    return "\n".join(parts)


def fake_report(session, run, dept, folder):
    path = folder / f"{dept}.pptx"
    path.write_bytes(b"pptx")
    return path


def test_fill_letter_replaces_markers_split_across_runs(tmp_path):
    def build(doc):
        p = doc.add_paragraph()
        p.add_run("請填寫「{{對標")
        bold = p.add_run("學校確認表}}」")
        bold.bold = True
        p.add_run("與 XXX。")
        doc.add_paragraph("研發處草稿的寫法：OOO")
        doc.add_table(rows=1, cols=1).cell(0, 0).text = "{{系所}}（{{期間}}）"
        doc.sections[0].header.paragraphs[0].text = "{{學院}}"

    src = tmp_path / "t.docx"
    src.write_bytes(docx_bytes(build))
    values = {"{{系所}}": DEPT, "{{學院}}": "管理學院", "{{期間}}": "2021-2025",
              "{{對標學校確認表}}": BENCH, "{{教師人數確認表}}": TEACH}
    out = fill_letter(src, values, tmp_path / "out.docx")
    text = all_text(out)
    assert f"請填寫「{BENCH}」與 {TEACH}。" in text
    assert f"研發處草稿的寫法：{BENCH}" in text          # OOO = 對標學校確認表
    assert f"{DEPT}（2021-2025）" in text and "管理學院" in text
    assert "{{" not in text and "OOO" not in text and "XXX" not in text
    runs = Document(str(out)).paragraphs[0].runs
    assert [r.text for r in runs] == [f"請填寫「{BENCH}", "」", f"與 {TEACH}。"] and runs[1].bold  # 其他文字格式保留


def test_markers_in_reports_legacy_markers_as_new_ones(tmp_path):
    src = tmp_path / "t.docx"
    src.write_bytes(docx_bytes(lambda d: d.add_paragraph("OOO 與 {{系所}}")))
    assert markers_in(src) == ["{{系所}}", "{{對標學校確認表}}"]


def test_default_letter_template_has_every_marker(settings):
    t = letter_template(settings)
    assert t.is_default and t.filename == DEFAULT_LETTER_NAME and t.path.exists()
    assert len(markers_in(t.path)) == 5


async def test_benchmark_workbook_lists_mapping_schools(session, done_run, tmp_path):
    ws = load_workbook(benchmark_workbook(session, dept_context(session, done_run, DEPT), tmp_path / "b.xlsx")).active
    rows = list(ws.iter_rows(values_only=True))
    header_idx = next(i for i, r in enumerate(rows) if r[0] == "對標學校")
    assert rows[header_idx] == ("對標學校", "系所中文名稱", "Scopus 機構英文名稱", "備註", "是否合宜",
                                "建議調整的學校／系所", "修正或新增的英文名稱（多個以分號分隔）")
    body = rows[header_idx + 1:]
    assert body[0][0] == "中央（本系所）"
    assert {r[0] for r in body} == {"中央（本系所）", "台大", "政治"}
    ntu = next(r for r in body if r[0] == "台大")
    assert ntu[2] == "Department of Information Management, National Taiwan University" and ntu[4] is None
    assert any("分號" in (r[0] or "") for r in rows[:header_idx])   # 說明多個名稱的寫法
    # 系統填入灰底、請填寫黃底；是否合宜有下拉選單
    r = header_idx + 2
    assert ws.cell(r, 3).fill.fgColor.rgb.endswith("EDEDED") and ws.cell(r, 5).fill.fgColor.rgb.endswith("FFF2CC")
    assert ws.data_validations.dataValidation[0].formula1 == '"合宜,不合宜"'


async def test_benchmark_workbook_marks_school_without_counterpart(session, done_run, tmp_path):
    ctx = dept_context(session, done_run, "會計研究所")
    rows = list(load_workbook(benchmark_workbook(session, ctx, tmp_path / "b.xlsx")).active.iter_rows(values_only=True))
    ntu = next(r for r in rows if r[0] == "台大")
    assert ntu[1] == "（mapping 表無對應系所）" and ntu[3] == "本次分析未納入"


async def test_teacher_workbook_uses_run_teacher_counts(session, done_run, tmp_path):
    ws = load_workbook(teacher_workbook(session, dept_context(session, done_run, DEPT), tmp_path / "t.xlsx")).active
    rows = list(ws.iter_rows(values_only=True))
    header_idx = next(i for i, r in enumerate(rows) if r[0] == "學校")
    assert rows[header_idx] == ("學校", "系所", "學年度", "教育部教師數", "來源說明", "修正後人數", "修正理由")
    body = {r[0]: r for r in rows[header_idx + 1:]}
    assert body["中央（本系所）"][2:4] == (114, 24) and body["台大"][3] == 16 and body["政治"][3] == 17
    assert "教育部 114 學年" in body["中央（本系所）"][4]
    assert body["台大"][5] is None and body["台大"][6] is None


async def test_dept_package_has_four_files_and_letter_names_the_excels(session, settings, done_run):
    path = write_dept_package(session, settings, done_run, DEPT, fake_report)
    with zipfile.ZipFile(path) as zf:
        assert sorted(zf.namelist()) == sorted([DEFAULT_LETTER_NAME, benchmark_filename(DEPT),
                                                teacher_filename(DEPT), f"{DEPT}.pptx"])
        letter = settings.data_dir / "letter.docx"
        letter.write_bytes(zf.read(DEFAULT_LETTER_NAME))
    text = all_text(letter)
    assert BENCH in text and TEACH in text and DEPT in text and "{{" not in text


async def test_all_packages_has_a_folder_per_dept(session, settings, done_run):
    steps = []
    path = write_all_packages(session, settings, done_run, fake_report, lambda d, t: steps.append((d, t)))
    with zipfile.ZipFile(path) as zf:
        assert all(n.startswith(f"{DEPT}/") for n in zf.namelist()) and len(zf.namelist()) == 4
    assert steps[-1] == (1, 1)


def wait(client, url):
    for _ in range(200):
        state = client.get(url).json()
        if state["ready"] or state["error"]:
            return state
        time.sleep(0.05)
    raise AssertionError("壓縮檔一直沒有完成")


def test_web_dept_package_download(client, web_done_run):
    url = f"/runs/{web_done_run}/exports/package/dept/{DEPT}"
    assert client.post(url).status_code == 202
    state = wait(client, url)
    assert state["ready"] and state["done"] == state["total"] == 4
    r = client.get(url + "/file")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
    assert BENCH in names and TEACH in names and any(n.endswith(".pptx") for n in names)


def test_web_all_packages_and_unknown_dept(client, web_done_run):
    url = f"/runs/{web_done_run}/exports/packages"
    client.post(url)
    assert wait(client, url)["ready"]
    bad = f"/runs/{web_done_run}/exports/package/dept/不存在學系"
    client.post(bad)
    assert wait(client, bad)["error"] == "這次分析沒有這個系所"
    assert client.post(f"/runs/{web_done_run}/exports/nope/dept/{DEPT}").status_code == 404


def test_downloads_tab_lists_packages_and_default_template_notice(client, web_done_run):
    text = client.get(f"/runs/{web_done_run}?ind=downloads").text
    assert f'data-export="/runs/{web_done_run}/exports/packages"' in text
    assert f"/exports/package/dept/%E8%B3%87" in text        # 系所名稱經過 URL 編碼
    assert "暫用信件範本" in text


def test_settings_upload_and_reset_letter_template(client, web_done_run):
    data = docx_bytes(lambda d: d.add_paragraph("各位主任：請填 OOO 與 XXX"))
    r = client.post("/settings/letter-template", files={"file": ("給各位主任所長_確認版.docx", data)})
    assert r.status_code == 200 and "已換成新的信件範本" in r.text and "給各位主任所長_確認版.docx" in r.text
    assert "暫用信件範本" not in client.get(f"/runs/{web_done_run}?ind=downloads").text
    assert client.get("/settings/letter-template.docx").content == data

    url = f"/runs/{web_done_run}/exports/package/dept/{DEPT}"
    client.post(url)
    wait(client, url)
    with zipfile.ZipFile(io.BytesIO(client.get(url + "/file").content)) as zf:
        letter = Document(io.BytesIO(zf.read("給各位主任所長_確認版.docx")))
    assert letter.paragraphs[0].text == f"各位主任：請填 {BENCH} 與 {TEACH}"

    r = client.post("/settings/letter-template/reset")
    assert "已改回系統暫用範本" in r.text
    assert "暫用信件範本" in client.get(f"/runs/{web_done_run}?ind=downloads").text


def test_settings_rejects_non_docx(client):
    r = client.post("/settings/letter-template", files={"file": ("letter.doc", b"old word")})
    assert r.status_code == 400 and ".docx" in r.text
    r = client.post("/settings/letter-template", files={"file": ("letter.docx", b"not a zip")})
    assert r.status_code == 400 and "無法讀取" in r.text
    assert "系統暫用範本" in client.get("/settings").text
