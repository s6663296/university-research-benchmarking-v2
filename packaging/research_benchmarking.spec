# PyInstaller 設定：python -m PyInstaller packaging/research_benchmarking.spec（由 packaging/build.bat 呼叫）
# 打包成資料夾（onedir）而不是單一 exe：啟動快、比較不會被防毒誤判，也方便安裝檔更新。
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent


def files(pattern, dest):
    return [(str(p), dest) for p in ROOT.glob(pattern)]


# 程式用 Path(__file__) 找這些檔案，打包後要放在相對於 app/ 相同的位置
datas = (
    files("app/web/templates/*.html", "app/web/templates")
    + files("app/web/static/*", "app/web/static")
    + files("templates/*.pptx", "templates")
    + files("alembic/env.py", "alembic")
    + files("alembic/versions/*.py", "alembic/versions")
    # mapping 檢查規則的指紋（RULES_HASH）讀這兩個模組的原始碼
    + files("app/mapping/parser.py", "app/mapping")
    + files("app/textnorm.py", "app")
    + collect_data_files("pptx")
)

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=datas,
    # uvicorn 以字串載入事件迴圈與協定；migration 檔不在分析範圍內，它們用到的模組也要列出
    hiddenimports=collect_submodules("uvicorn") + collect_submodules("app") + ["alembic.op", "sqlalchemy"],
    excludes=["tkinter", "pytest", "respx"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [("X utf8", None, "OPTION")],
    exclude_binaries=True,
    name="ResearchBenchmarking",
    console=True,  # 伺服器在這個視窗裡執行，關閉視窗即結束系統
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ResearchBenchmarking", upx=False)
