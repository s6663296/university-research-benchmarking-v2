# 員額小組論文指標自動化系統 v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 依設計文件做出可在承辦人電腦上以瀏覽器操作的本機系統：匯入 mapping 與教育部教師數 → 透過 Scopus Search／SciVal API 抓資料 → 計算師均指標與排名 → 產出網頁表格、各系所 pptx、整份 xlsx，並保存每次分析版本。

**Architecture:** FastAPI 單一行程，Jinja2 + HTMX 伺服器端渲染；SQLite（WAL）+ SQLAlchemy 2 + Alembic。領域模組（mapping、teachers、elsevier、metrics、runs、reports）只透過下列各 Task 的 Interfaces 互相呼叫；`metrics` 是純函式，不碰資料庫。背景抓取用同行程的 asyncio task。

**Tech Stack:** Python 3.11+（本機為 3.15，已實測所有套件可安裝）、FastAPI、Uvicorn、Jinja2、HTMX 2（本機檔）、SQLAlchemy 2、Alembic、httpx、openpyxl、python-pptx、pydantic-settings、pytest、pytest-asyncio、respx。

**Spec:** [docs/superpowers/specs/2026-10-30-research-benchmarking-design.md](../specs/2026-10-30-research-benchmarking-design.md)（以下稱「設計 §N」）

## Global Constraints

- 伺服器只監聽 `127.0.0.1`，預設 port 8000；`start.bat` 第一次執行建立 `.venv` 並安裝套件，之後啟動伺服器並開啟瀏覽器
- `.env`（`ELSEVIER_API_KEY`、`ELSEVIER_INSTTOKEN`）與 `data/` 不進版控；API Key 不得寫進任何其他檔案、資料庫、快取、fixture 或產出檔
- Elsevier 回應中的 `link` 欄位（**任何層級**，SciVal 的 `publication.link.@href` 內含 `apiKey=`）存檔前一律移除
- 所有指標以 `Decimal` 計算，最後 `ROUND_HALF_UP` 到小數第二位；畫面與報告一律顯示兩位小數（篇數、人數為整數）
- 查無資料或計算不出來時標「待人工確認」，**絕不寫成 0**；師均缺教師數時顯示「待確認」
- 計入所有文獻類型：SciVal 請求一律帶 `includedDocs=AllPublicationTypes`
- 排名：數值由高到低，同值同名次、下一名跳號（1, 2, 2, 4）；以捨入後的值排名；沒有值的單位不排名
- 介面文字一律繁體中文；精簡、正式、表格為主；深灰配色加一個主色；不載入 CSS/JS 框架與 CDN
- 需要真實 API 的測試只在 `RUN_LIVE_API_TESTS=1` 時執行
- 不實作：國科會經費（`app/funding/` 只放空的 `__init__.py`）、AI 功能、多人帳號

## Review Focus

1. **Mapping 儲存格的髒資料**：`\xa0`、全形空白、尾端空白、`x`／`X`／空白、中間整列空白（政治分頁第 8～9 列）、同一分頁兩個中大系所共用同一英文名稱（台大「網路學習科技研究所」與「資訊工程學系」）→ 清理後正確判斷有無對應；共用英文名稱只抓一次、兩個單位數字相同、列為提醒。測試在 Task 5。
2. **API Key 外洩**：SciVal 逐篇結果的 `publication.link.@href` 帶 `apiKey=`（已在 `api_test/output/` 錄到的檔案中確認）→ 快取、資料庫、fixture、xlsx 都不得含 `apiKey`。測試在 Task 3、Task 9、Task 16。
3. **沒連 VPN／Key 無效／抓到一半斷線**：401/403 不重試、立即標失敗；連線錯誤重試後標「待人工確認」，其他單位照常完成；分析狀態仍為完成而非整個崩潰。測試在 Task 3、Task 12。
4. **程式中途關閉後繼續**：重開時 `running` 變 `interrupted`；繼續時只抓未完成單位、走快取，`run_publications` 不重複。測試在 Task 12。
5. **教師數缺漏或為 0**：師均為「待確認」、不除以 0、不參與該指標排名，其他指標照常。測試在 Task 10。

---

## 檔案結構

```
pyproject.toml  start.bat  .env.example  .gitignore（加 data/ .env .venv/ *.egg-info）
alembic.ini  alembic/env.py  alembic/versions/0001_initial.py
app/__main__.py             # python -m app：啟動 uvicorn 並開啟瀏覽器
app/main.py                 # create_app()
app/config.py               # Settings（.env）、save_env()
app/textnorm.py             # clean()、norm_name()
app/db/models.py  app/db/session.py  app/db/settings_store.py
app/mapping/parser.py  app/mapping/service.py
app/teachers/moe.py  app/teachers/service.py
app/elsevier/client.py  app/elsevier/cache.py  app/elsevier/scopus.py  app/elsevier/scival.py  app/elsevier/selfcite.py
app/metrics/indicators.py  app/metrics/compute.py
app/runs/scope.py  app/runs/service.py  app/runs/executor.py  app/runs/anomaly.py  app/runs/manager.py  app/runs/compare.py
app/reports/tables.py  app/reports/pptx_report.py  app/reports/xlsx_report.py  app/reports/exports.py
app/funding/__init__.py     # 預留，空檔
app/web/deps.py  app/web/routes/{home,settings,mapping,teachers,runs}.py
app/web/templates/*.html  app/web/static/{htmx.min.js,app.css}
templates/report_template.pptx
scripts/make_report_template.py  scripts/sanitize_fixture.py
tests/conftest.py  tests/fixtures/...  tests/test_*.py  tests/live/test_live_api.py
```

---

## Milestone 1：骨架、資料庫、API 連線

### Task 1: 專案骨架與首頁

**Files:**
- Create: `pyproject.toml`, `start.bat`, `.env.example`, `app/__init__.py`, `app/__main__.py`, `app/main.py`, `app/config.py`, `app/web/deps.py`, `app/web/routes/home.py`, `app/web/templates/base.html`, `app/web/templates/home.html`, `app/web/static/app.css`, `app/web/static/htmx.min.js`, `app/funding/__init__.py`, `tests/conftest.py`, `tests/test_app.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces:
  - `app.config.Settings(BaseSettings)`：`elsevier_api_key: str = ""`、`elsevier_insttoken: str = ""`、`data_dir: Path = Path("data")`、`host: str = "127.0.0.1"`、`port: int = 8000`；讀 `.env`
  - `app.config.save_env(path: Path, api_key: str, insttoken: str) -> None`：改寫 `.env` 的這兩行，保留其他行
  - `app.main.create_app(settings: Settings | None = None) -> FastAPI`：`app.state.settings`；掛 `/static`；註冊各 router（之後的 Task 在這裡加）
  - `app.web.deps.templates: Jinja2Templates`、`render(request, name, **ctx) -> TemplateResponse`
  - `tests/conftest.py`：fixture `settings(tmp_path)`（`data_dir=tmp_path`、空 Key）、`client(settings)`（`TestClient(create_app(settings))`）

- [ ] **Step 1: 寫失敗測試** `tests/test_app.py`

```python
def test_home_page_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "員額小組論文指標" in r.text
    assert "htmx.min.js" in r.text and "cdn" not in r.text.lower()

def test_save_env_keeps_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("FOO=1\nELSEVIER_API_KEY=old\n", encoding="utf-8")
    save_env(env, "new", "tok")
    text = env.read_text(encoding="utf-8")
    assert "FOO=1" in text and "ELSEVIER_API_KEY=new" in text and "ELSEVIER_INSTTOKEN=tok" in text
```

- [ ] **Step 2: 執行確認失敗** — `pytest tests/test_app.py -v` → FAIL（ModuleNotFoundError: app）
- [ ] **Step 3: 實作**
  - `pyproject.toml`：套件 `app`，`requires-python = ">=3.11"`；依賴 `fastapi`、`uvicorn[standard]`、`jinja2`、`python-multipart`、`sqlalchemy>=2`、`alembic`、`httpx`、`openpyxl`、`python-pptx`、`pydantic-settings`；`[project.optional-dependencies] dev = pytest, pytest-asyncio, respx`；`[tool.pytest.ini_options] asyncio_mode = "auto"`、`testpaths = ["tests"]`
  - `htmx.min.js`：下載 htmx 2.x 正式版存入 repo（之後不連 CDN）
  - `base.html`：頂端導覽（首頁、Mapping 管理、教師數、新增分析、版本紀錄、設定），`{% block content %}`；`app.css` 自訂，約 150 行內
  - `app/__main__.py`：`uvicorn.run(create_app(), host=settings.host, port=settings.port)`，啟動前用 `threading.Timer(1.5, webbrowser.open, ...)` 開啟 `http://127.0.0.1:8000`
  - `start.bat`：`chcp 65001`；`cd /d "%~dp0"`；若無 `.venv` 則 `python -m venv .venv` 並 `.venv\Scripts\python -m pip install -e .`；若無 `.env` 則複製 `.env.example` 並提示填 Key；最後 `.venv\Scripts\python -X utf8 -m app`
  - `.gitignore` 加 `data/`、`.env`、`.venv/`、`*.egg-info/`、`__pycache__/`
- [ ] **Step 4: 執行確認通過** — `pip install -e ".[dev]"` 後 `pytest -v` → PASS
- [ ] **Step 5: Commit** — `git commit -m "feat: add project skeleton, home page and start.bat"`

### Task 2: 資料庫模型、migration 與設定儲存

**Files:**
- Create: `app/db/__init__.py`, `app/db/models.py`, `app/db/session.py`, `app/db/settings_store.py`, `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_initial.py`, `tests/test_db.py`
- Modify: `app/main.py`（lifespan 呼叫 `init_db`）、`tests/conftest.py`（加 `session` fixture）

**Interfaces:**
- Produces:
  - `app.db.models`：`Base` 與設計 §11 的全部資料表，類別名 `MappingVersion, Unit, MappingIssue, SchoolAlias, TeacherImport, TeacherCountRow, UnitTeacherLink, TeacherOverride, Run, RunUnit, RunPublication, ApiCache, Result, Anomaly, Export, AppSetting`。在設計 §11 之外加這些欄位：
    - `school_aliases.display_name`（報告用簡稱；「政治」分頁在範例報告顯示為「政大」）
    - `run_publications.metrics_json`（逐篇指標，已移除 link）
    - `results.raw`（A 篇數或未捨入的平均）、`results.five_year_avg`（A/年數）
    - `run_units.error`（失敗原因）
    - `app_settings(key PK, value_json)`
    - Decimal 欄位用自訂 `DecimalText` TypeDecorator 存成字串（SQLite 的 Numeric 會轉 float）
  - `app.db.session.make_engine(db_path: Path) -> Engine`（`check_same_thread=False`，連線時 `PRAGMA journal_mode=WAL`、`foreign_keys=ON`）
  - `app.db.session.init_db(engine) -> None`：以 Alembic 程式 API 執行 `upgrade head`
  - `app.db.session.make_session_factory(engine) -> sessionmaker[Session]`；`create_app` 存為 `app.state.session_factory`；`app.web.deps.get_session` 依賴
  - `app.db.settings_store.DEFAULT_SETTINGS: dict[str, Any]`、`get_setting(session, key) -> Any`、`set_setting(session, key, value) -> None`
  - `0001_initial` 同時寫入 `school_aliases` 預設 10 筆（sheet_name → moe_name / display_name）：中央→國立中央大學/中央、台大→國立臺灣大學/台大、清大→國立清華大學/清大、陽明交大→國立陽明交通大學/陽明交大、成大→國立成功大學/成大、政治→國立政治大學/政大、中山→國立中山大學/中山、師大→國立臺灣師範大學/師大、中興→國立中興大學/中興、台科大→國立臺灣科技大學/台科大
  - `DEFAULT_SETTINGS`：

```python
DEFAULT_SETTINGS = {
    "rate_per_sec": 3.0, "max_retries": 3, "backoff_base_sec": 1.0, "timeout_sec": 60,
    "cache_days": 1,                      # 1 = 只用當天的快取
    "scival_batch_size": 25,
    "anomaly_change_threshold": 0.30,
    "teacher_column": "total",            # 或 "asst_prof_plus"
    "self_citation_strategy": "scival_param",
    "rank_highlight_top": 2,              # 排名總表中名次 ≤ 2 標紅（範例第 4 頁）
    "dept_short_names": {"資訊管理學系": "資管系", "財務金融學系": "財金系", "企業管理學系": "企管系",
        "經濟學系": "經濟系", "產業經濟研究所": "產經所", "人力資源管理研究所": "人資所",
        "工業管理研究所": "工管所", "會計研究所": "會計所"},
    "college_short_names": {"管理學院": "管學院"},
}
```

- [ ] **Step 1: 寫失敗測試** `tests/test_db.py`

```python
EXPECTED = {"mapping_versions","units","mapping_issues","school_aliases","teacher_imports","teacher_counts",
            "unit_teacher_links","teacher_overrides","runs","run_units","run_publications","api_cache",
            "results","anomalies","exports","app_settings","alembic_version"}

def test_init_db_creates_tables_with_wal(tmp_path):
    engine = make_engine(tmp_path / "app.db"); init_db(engine)
    assert set(inspect(engine).get_table_names()) == EXPECTED
    with engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"

def test_migration_matches_models(tmp_path):
    engine = make_engine(tmp_path / "app.db"); init_db(engine)
    with engine.connect() as c:
        assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []

def test_default_school_aliases(session):
    a = session.get(SchoolAlias, "政治")
    assert (a.moe_name, a.display_name) == ("國立政治大學", "政大")

def test_settings_default_and_override(session):
    assert get_setting(session, "scival_batch_size") == 25
    set_setting(session, "scival_batch_size", 50)
    assert get_setting(session, "scival_batch_size") == 50

def test_decimal_roundtrip(session):
    session.add(Result(run_id=..., unit_id=..., comparison="benchmark", indicator_id="q1", value=Decimal("2.575")))  # 先建好 run/unit
    session.commit(); session.expire_all()
    assert session.scalars(select(Result.value)).one() == Decimal("2.575")
```

- [ ] **Step 2: 執行確認失敗** — `pytest tests/test_db.py -v` → FAIL
- [ ] **Step 3: 實作上述介面**；`alembic/env.py` 用 `Base.metadata`，URL 由 `init_db` 傳入的連線提供（`config.attributes["connection"]`）
- [ ] **Step 4: 執行確認通過** — `pytest -v` → PASS
- [ ] **Step 5: Commit** — `git commit -m "feat(db): add SQLite models, initial migration and settings store"`

### Task 3: Elsevier client（限速、重試、快取、移除 link）

**Files:**
- Create: `app/elsevier/__init__.py`, `app/elsevier/client.py`, `app/elsevier/cache.py`, `tests/test_elsevier_client.py`

**Interfaces:**
- Consumes: `ApiCache` model、`make_session_factory`（Task 2）
- Produces:
  - `strip_links(obj: Any) -> Any`：遞迴移除所有名為 `link` 的鍵（dict 與 list 內皆處理），回傳新物件
  - `cache_key(path: str, params: dict) -> str`：`sha256(path + json.dumps(params, sort_keys=True))`
  - `ApiCache(session_factory, cache_days: int)`：`get(key) -> dict | None`（`fetched_at.date() > today - cache_days` 才算有效）、`put(key, endpoint, data) -> None`（覆寫）
  - `class ElsevierError(Exception)`：`status: int | None`、`retryable: bool`、`message: str`（繁中，給畫面顯示）
  - `ElsevierClient(api_key, insttoken="", *, base_url="https://api.elsevier.com", rate_per_sec=3.0, max_retries=3, backoff_base=1.0, timeout=60.0, cache: ApiCache | None = None, transport: httpx.AsyncBaseTransport | None = None)`
    - `async get_json(path, params, *, use_cache=True) -> dict`：先查快取（`use_cache=False` 時跳過讀取但仍寫入）；送出時帶 `X-ELS-APIKey`、有 insttoken 時帶 `X-ELS-Insttoken`、`Accept: application/json`；429／5xx／`httpx.TimeoutException`／`httpx.TransportError` 以 `backoff_base * 2**attempt` 退避重試 `max_retries` 次；401/403 不重試；回傳與寫入快取的都是 `strip_links` 後的資料
    - `quota: dict[str, str]`：最後一次回應的 `X-RateLimit-Limit`、`X-RateLimit-Remaining`、`X-RateLimit-Reset`
    - `async check_connection() -> ConnectionStatus`：`GET /content/search/scopus?query=AFFILCOUNTRY(Taiwan)&count=1`，不用快取、不重試
    - `async aclose()`
  - `@dataclass ConnectionStatus: ok: bool; message: str; remaining: str | None`
  - `make_client(settings: Settings, session) -> ElsevierClient`：從 `.env` 與 `app_settings` 組出 client
  - 限速器：`AsyncRateLimiter(rate_per_sec)`，`async acquire()`，兩次放行間隔 ≥ `1/rate`

- [ ] **Step 1: 寫失敗測試**（respx 模擬；`backoff_base=0` 讓測試不等待）

```python
def test_strip_links_removes_nested():
    data = {"link": [1], "results": [{"publication": {"id": 1, "link": {"@href": "x?apiKey=SECRET"}}}]}
    out = strip_links(data)
    assert "SECRET" not in json.dumps(out) and out["results"][0]["publication"] == {"id": 1}

async def test_retries_429_then_succeeds(respx_mock):
    route = respx_mock.get(url__regex=r".*/content/search/scopus.*").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json={"ok": 1})])
    c = ElsevierClient("k", backoff_base=0)
    assert await c.get_json("/content/search/scopus", {"query": "q"}) == {"ok": 1}
    assert route.call_count == 2

async def test_401_not_retried(respx_mock):  # call_count == 1，ElsevierError.status == 401, retryable False
async def test_gives_up_after_max_retries(respx_mock):  # 一直 503 → 呼叫 1+3 次後 ElsevierError(retryable=True)
async def test_sends_insttoken_header_only_when_set(respx_mock):
async def test_cache_hit_skips_request_and_never_stores_key(respx_mock, session_factory):
    # 第二次同參數呼叫 route.call_count 仍為 1；ApiCache 表內容不含 "apiKey" 與 "k"
async def test_use_cache_false_refetches_and_updates_cache(respx_mock, session_factory):
async def test_cache_expires_next_day(session_factory):  # fetched_at 設為昨天 → get() is None
async def test_quota_read_from_headers(respx_mock):
async def test_check_connection_reports_unreachable(respx_mock):
    # side_effect=httpx.ConnectError → ok False，message 含「無法連線」與「VPN」
```

- [ ] **Step 2: 執行確認失敗** — `pytest tests/test_elsevier_client.py -v` → FAIL
- [ ] **Step 3: 實作上述介面**
- [ ] **Step 4: 執行確認通過** — `pytest tests/test_elsevier_client.py -v` → PASS
- [ ] **Step 5: Commit** — `git commit -m "feat(elsevier): add rate-limited, retrying, cached API client"`

### Task 4: 設定頁與連線狀態

**Files:**
- Create: `app/web/routes/settings.py`, `app/web/templates/settings.html`, `app/web/templates/_status.html`, `tests/test_web_settings.py`
- Modify: `app/main.py`, `app/web/routes/home.py`, `app/web/templates/home.html`

**Interfaces:**
- Consumes: `save_env`、`get_setting/set_setting`、`SchoolAlias`、`make_client`、`ConnectionStatus`
- Produces:
  - `GET /settings`、`POST /settings`（表單欄位名＝`DEFAULT_SETTINGS` 的鍵；`dept_short_names`、`college_short_names` 以「全名=簡稱」每行一筆的 textarea 編輯；學校對照表三欄可編輯）、`POST /settings/api-key`（呼叫 `save_env` 並重新載入 `app.state.settings`）
  - `GET /status`：HTMX 片段 `_status.html`，顯示連線結果與剩餘額度；首頁與新增分析頁以 `hx-get="/status" hx-trigger="load"` 載入
  - API Key 在頁面上只顯示「已設定（末 4 碼 ****abcd）」或「未設定」

- [ ] **Step 1: 寫失敗測試**

```python
def test_settings_page_never_shows_full_key(client, monkeypatch):  # 設 key="abcdefgh1234" → 頁面含 "1234"、不含 "abcdefgh"
def test_post_settings_updates_values(client):  # POST scival_batch_size=40 → 再 GET 顯示 40
def test_post_rejects_invalid_number(client):   # rate_per_sec=-1 → 400 且顯示錯誤訊息，值不變
def test_status_without_key(client):            # GET /status 含「尚未設定 API Key」，不發出任何請求
def test_status_unreachable(client, respx_mock):  # ConnectError → 含「無法連線」
```

- [ ] **Step 2: 執行確認失敗** — `pytest tests/test_web_settings.py -v` → FAIL
- [ ] **Step 3: 實作**；首頁先放「目前 mapping 版本／教師數資料／連線狀態／最近分析」四個區塊，前兩項與最近分析在後續 Task 填入，現在顯示「尚未匯入」
- [ ] **Step 4: 執行確認通過** — `pytest -v` → PASS
- [ ] **Step 5: Commit** — `git commit -m "feat(web): add settings page and API connection status"`

---

## Milestone 2：Mapping

### Task 5: 文字正規化與 mapping 解析

**Files:**
- Create: `app/textnorm.py`, `app/mapping/__init__.py`, `app/mapping/parser.py`, `tests/test_textnorm.py`, `tests/test_mapping_parser.py`, `tests/mapping_builder.py`（測試用：以 dict 產生 xlsx bytes）

**Interfaces:**
- Produces:
  - `clean(value: object) -> str | None`：轉字串，去除前後空白、全形空白 `　`、`\xa0`；空字串或 `x`／`X` 回傳 `None`
  - `norm_name(value: str) -> str`：`clean` 後「臺」→「台」並移除所有空白
  - `@dataclass(frozen=True) ParsedUnit: school: str; ncu_college: str | None; ncu_dept: str; college: str | None; dept_zh: str | None; dept_en: str; remark: str | None; row: int`
  - `@dataclass(frozen=True) Issue: sheet: str; row: int | None; level: Literal["error", "warning"]; message: str`
  - `@dataclass ParseResult: units: list[ParsedUnit]; issues: list[Issue]`
  - `NCU_SHEET = "中央"`；`parse_mapping(data: bytes) -> ParseResult`

解析規則（設計 §5.2 之外，依實際檔案補充）：
- 第 1 列為表頭。第 1 欄（學院）表頭在現有檔案中是空白，以位置定位；第 2 欄必須是「中大系所」；第 3 欄為該校學院（表頭是學校全名，不檢查）；「系所中文名稱」「系所英文名稱」「備註」以表頭 `startswith` 定位（師大是「系所英文名稱(scopus)」，且沒有「備註」欄）
- `school` = 分頁名稱；「中大系所」為空的列略過（政治分頁中間的空白列）；學院為空（研究中心等列）照樣解析
- 只有 `dept_en` 清理後不為 `None` 時才產生單位
- **error**：缺「中央」分頁；分頁缺「中大系所」或「系所英文名稱」表頭；「中央」分頁中有學院的列缺英文名稱
- **warning**：有中文名稱但缺英文名稱；同一分頁兩個不同中大系所使用相同英文名稱（正規化後比較）；第 6 欄以後有值（訊息含儲存格座標）；他校分頁的中大系所不在「中央」分頁中（僅在有「中央」分頁時檢查）

- [ ] **Step 1: 寫失敗測試**

```python
def test_clean():
    assert clean("\xa0Department of X ") == "Department of X"
    assert clean("材料科學與工程學系　") == "材料科學與工程學系"
    assert clean("x") is None and clean(" X ") is None and clean(None) is None and clean("") is None

def test_norm_name():
    assert norm_name("國立臺灣 大學") == "國立台灣大學"

REAL = Path("docs/中大與他校mapping表.xlsx").read_bytes()

def test_real_file_management_units():
    r = parse_mapping(REAL)
    u = {(x.school, x.ncu_dept): x for x in r.units}
    assert u[("台大", "資訊管理學系")].dept_en == "Department of Information Management, National Taiwan University"
    assert ("台大", "經濟學系") not in u                      # x
    assert ("政治", "歷史研究所") not in u                    # 空白英文名
    assert u[("台科大", "英美語文學系")].dept_en.startswith("Department of Applied")  # \xa0 已去除

def test_real_file_issues():
    r = parse_mapping(REAL)
    msgs = [(i.level, i.sheet, i.message) for i in r.issues]
    assert any(l == "error" and "中央" in m for l, _, m in msgs)                  # 缺中央分頁
    assert any(l == "warning" and s == "清大" and "I1" in m for l, s, m in msgs)  # 表頭列多出的儲存格
    assert any(l == "warning" and s == "台大" and "網路學習科技研究所" in m for l, s, m in msgs)  # 共用英文名稱
    assert any(l == "warning" and s == "政治" and "歷史研究所" in m for l, s, m in msgs)  # 有中文缺英文

def test_ncu_sheet_parsed_and_missing_en_is_error():
    data = build_mapping({"中央": [("管理學院", "資訊管理學系", "管理學院", "資訊管理學系", "Department of Information Management, National Central University", None),
                                  ("管理學院", "會計研究所", "管理學院", "會計研究所", None, None)]})
    r = parse_mapping(data)
    assert [x.ncu_dept for x in r.units] == ["資訊管理學系"]
    assert any(i.level == "error" and "會計研究所" in i.message for i in r.issues)

def test_header_variant_scopus_suffix():  # 「系所英文名稱(scopus)」、無備註欄 → 照常解析
```

- [ ] **Step 2: 執行確認失敗** — `pytest tests/test_textnorm.py tests/test_mapping_parser.py -v` → FAIL
- [ ] **Step 3: 實作上述介面**（openpyxl `load_workbook(BytesIO(data), data_only=True)`）
- [ ] **Step 4: 執行確認通過** — 同上指令 → PASS
- [ ] **Step 5: Commit** — `git commit -m "feat(mapping): parse and validate mapping workbook"`

### Task 6: Mapping 版本、差異與管理頁

**Files:**
- Create: `app/mapping/service.py`, `app/web/routes/mapping.py`, `app/web/templates/mapping.html`, `tests/test_mapping_service.py`, `tests/test_web_mapping.py`
- Modify: `app/main.py`, `app/web/routes/home.py`

**Interfaces:**
- Consumes: `parse_mapping`、`MappingVersion`、`Unit`、`MappingIssue`
- Produces:
  - `UnitKey = tuple[str, str]`（school, ncu_dept）
  - `import_mapping(session, filename: str, data: bytes) -> tuple[MappingVersion, bool]`：第二個值為「是否新建」；SHA-256 與最新版相同時不新建，回傳最新版與 `False`
  - `latest_version(session) -> MappingVersion | None`、`previous_version(session, version_id) -> MappingVersion | None`
  - `@dataclass MappingDiff: added: list[UnitKey]; removed: list[UnitKey]; changed: list[tuple[UnitKey, str, str]]`（英文名稱舊→新）
  - `diff_versions(session, old_id: int | None, new_id: int) -> MappingDiff`
  - `units_of(session, version_id) -> list[Unit]`
  - 頁面：`GET /mapping`（目前版本、問題清單分 error／warning、與上一版差異、單位清單可依學校篩選）、`POST /mapping`（上傳）

- [ ] **Step 1: 寫失敗測試**

```python
def test_import_creates_version_with_units_and_issues(session):  # 真實檔案 → units > 0、issues 含 error
def test_same_file_not_duplicated(session):    # 匯入兩次 → 第二次 created False，版本數 1
def test_diff_added_removed_changed(session):   # 用 build_mapping 造 v1/v2：一筆新增、一筆移除、一筆英文名稱變更
def test_upload_page_shows_issues_and_diff(client):  # POST /mapping 上傳真實檔 → 頁面含「缺少「中央」分頁」
def test_upload_rejects_non_xlsx(client):       # 上傳 b"not xlsx" → 顯示「無法讀取檔案」，不建立版本
```

- [ ] **Step 2: 執行確認失敗** — `pytest tests/test_mapping_service.py tests/test_web_mapping.py -v` → FAIL
- [ ] **Step 3: 實作上述介面**；首頁顯示目前 mapping 版本檔名、匯入時間、錯誤數
- [ ] **Step 4: 執行確認通過** — `pytest -v` → PASS
- [ ] **Step 5: Commit** — `git commit -m "feat(mapping): store mapping versions, diff and management page"`

---

## Milestone 3：教師數

### Task 7: 教育部檔案解析與匯入

**Files:**
- Create: `app/teachers/__init__.py`, `app/teachers/moe.py`, `tests/test_teachers_moe.py`, `tests/moe_builder.py`（產生小型教育部格式 xlsx）

**Interfaces:**
- Produces:
  - `TEACHER_COLUMNS = {"total": "專任教師數-教師總數總計", "asst_prof_plus": "專任助理教授以上人數-總計"}`
  - `@dataclass(frozen=True) MoeRow: academic_year: int; school: str; unit_code: str; unit_name: str; total: int; asst_prof_plus: int`
  - `parse_moe(data: bytes) -> list[MoeRow]`：`read_only=True`；在前 10 列中找第一格為「學年度」的列當表頭（實際是第 3 列）；欄位以表頭文字定位；所有值是字串，轉 int；「學年度」不是純數字的列（檔尾統計說明）略過
  - `import_moe(session, filename: str, data: bytes) -> tuple[TeacherImport, bool]`（同 SHA-256 不重複匯入）；`TeacherImport.years` 存檔內學年度排序清單
  - `latest_import(session) -> TeacherImport | None`

- [ ] **Step 1: 寫失敗測試**

```python
REAL = Path("docs/教1-1.專任教師數-以「系(所)」統計.xlsx").read_bytes()

def test_real_file():
    rows = parse_moe(REAL)
    assert {r.academic_year for r in rows} == set(range(106, 115))
    ncu_im = [r for r in rows if r.school == "國立中央大學" and r.unit_code == "06131053"]
    assert len(ncu_im) == 9 and all(r.unit_name == "資訊管理學系" for r in ncu_im)
    assert next(r for r in ncu_im if r.academic_year == 114).total == 24
    assert not any(r.school.startswith("統計說明") for r in rows)

def test_import_twice_is_noop(session):  # 第二次 created False
```

- [ ] **Step 2: 執行確認失敗** — `pytest tests/test_teachers_moe.py -v` → FAIL
- [ ] **Step 3: 實作上述介面**（3.6 萬列，`bulk_insert_mappings` 或 `session.execute(insert(...), rows)`）
- [ ] **Step 4: 執行確認通過** — 同上 → PASS（真實檔案測試應在 30 秒內）
- [ ] **Step 5: Commit** — `git commit -m "feat(teachers): parse and import MOE teacher counts"`

### Task 8: 教師數對應、人工修改與教師數頁

**Files:**
- Create: `app/teachers/service.py`, `app/web/routes/teachers.py`, `app/web/templates/teachers.html`, `tests/test_teachers_service.py`, `tests/test_web_teachers.py`
- Modify: `app/main.py`, `app/web/routes/home.py`

**Interfaces:**
- Consumes: `MoeRow` 資料表、`SchoolAlias`、`units_of`、`norm_name`
- Produces:
  - `@dataclass Unmatched: school: str; dept_zh: str; ncu_dept: str`
  - `auto_link(session, mapping_version_id: int, teacher_import_id: int) -> list[Unmatched]`：只處理尚無 link 的 (school, dept_zh)；中大單位用 `school="中央"`。以 `SchoolAlias.moe_name` 找學校，在該校所有學年度中找 `norm_name(unit_name) == norm_name(dept_zh)` 的代碼；只有一個代碼 → 建立 `matched_by="auto"`；多個代碼 → 取最新學年度有出現的那一個，仍不只一個則列為未對應；找不到 → 未對應
  - `set_links(session, school: str, dept_zh: str, unit_codes: list[str]) -> None`：取代既有 link，`matched_by="manual"`
  - `unit_choices(session, teacher_import_id: int, school: str) -> list[tuple[str, str]]`：該校最新學年度的（代碼, 名稱）
  - `add_override(session, school, dept_zh, academic_year: int, count: int, reason: str) -> None`：`reason` 去空白後為空 → `ValueError("請填寫修改原因")`；`count < 0` → `ValueError`
  - `@dataclass TeacherCount: value: int | None; source: Literal["moe", "manual", "missing"]; reason: str | None; unit_codes: list[str]`
  - `teacher_count(session, teacher_import_id, academic_year, column, school, dept_zh) -> TeacherCount`：人工修改優先；否則加總 link 代碼在該學年度的欄位值；沒有 link 或該年度一筆都沒有 → `missing`
  - 頁面 `GET /teachers`（匯入、學年度選單預覽各單位人數與來源、未對應清單附該校單位多選、修改表單）；`POST /teachers/import`、`POST /teachers/link`、`POST /teachers/override`

- [ ] **Step 1: 寫失敗測試**（用 `moe_builder` 與 `build_mapping` 造資料）

```python
def test_auto_link_normalizes_tai(session):   # mapping「國立台灣大學」以外：dept「資訊管理學系」對教育部「資訊管理學系」；學校 台大→國立臺灣大學
def test_code_survives_rename(session):       # 代碼 X 在 113 叫「資訊管理學系」、114 改名「資訊管理與科技學系」→ 114 仍取得人數
def test_unmatched_listed(session):           # 名稱對不到 → 回傳 Unmatched
def test_manual_multi_link_sums(session):     # set_links 兩個代碼 8+5 → value 13, source "moe"
def test_override_wins_and_needs_reason(session):
    with pytest.raises(ValueError, match="原因"): add_override(session, "中央", "資訊管理學系", 114, 23, "  ")
    add_override(session, "中央", "資訊管理學系", 114, 23, "依 SciVal")
    tc = teacher_count(session, imp.id, 114, "total", "中央", "資訊管理學系")
    assert (tc.value, tc.source, tc.reason) == (23, "manual", "依 SciVal")
def test_missing_year_is_missing(session):    # link 存在但該學年度無資料 → value None, source "missing"
def test_column_switch(session):              # column="asst_prof_plus" 取另一欄
def test_teachers_page_flow(client):          # 上傳 → 選學年度 → 頁面列出未對應 → POST link → 不再列出
```

- [ ] **Step 2: 執行確認失敗** — `pytest tests/test_teachers_service.py tests/test_web_teachers.py -v` → FAIL
- [ ] **Step 3: 實作上述介面**；匯入教育部檔案或 mapping 後都呼叫 `auto_link`；首頁顯示教師數檔名、學年度範圍、未對應數
- [ ] **Step 4: 執行確認通過** — `pytest -v` → PASS
- [ ] **Step 5: Commit** — `git commit -m "feat(teachers): link MOE units, manual overrides and teachers page"`

---

## Milestone 4：抓資料、計算、分析版本

### Task 9: Scopus Search、SciVal 指標與自我引用策略

**Files:**
- Create: `app/elsevier/scopus.py`, `app/elsevier/scival.py`, `app/elsevier/selfcite.py`, `scripts/sanitize_fixture.py`, `tests/fixtures/elsevier/scival_qf_67.json`, `tests/fixtures/elsevier/scival_cites_qf_67.json`, `tests/fixtures/elsevier/scopus_page_*.json`, `tests/test_scopus.py`, `tests/test_scival.py`, `tests/live/test_live_api.py`

**Interfaces:**
- Consumes: `ElsevierClient.get_json`、`ElsevierError`
- Produces:
  - `build_query(dept_en: str, year_from: int, year_to: int) -> str` → `AFFIL("<dept_en>") AND AFFILCOUNTRY(Taiwan) AND PUBYEAR > <year_from-1> AND PUBYEAR < <year_to+1>`（`dept_en` 內的 `"` 移除）
  - `@dataclass(frozen=True) ScopusDoc: scopus_id: str; eid: str`
  - `async search_docs(client, query: str, *, use_cache: bool) -> list[ScopusDoc]`：`/content/search/scopus`，`count=25`、`cursor="*"`，跟隨 `search-results.cursor.@next`，頁面無 entry 或已取得 `opensearch:totalResults` 筆時停止；`dc:identifier` 去掉 `SCOPUS_ID:`；依 scopus_id 去重；`totalResults=0` 時回傳空清單（Scopus 會回一筆含 `error` 的 entry，要略過）
  - `scival_year_ranges(year_from, year_to, current_year) -> tuple[str, str]`：回傳（指標用, 被引用）。`year_to >= current_year` → `ValueError("期間不可包含今年")`；`year_from >= current_year-5` → `("5yrs", "5yrsAndCurrentAndFuture")`；`year_from >= current_year-10` → `("10yrs", "10yrsAndCurrentAndFuture")`；否則 `ValueError`
  - `@dataclass PubMetrics: scopus_id: str; year: int | None; q1: bool | None; top1: bool | None; top10: bool | None; fwci: Decimal | None; intl_collab: bool | None; citations: int | None; raw: dict`
  - `async fetch_metrics(client, ids: list[str], *, year_range: str, batch_size: int, use_cache: bool) -> tuple[dict[str, PubMetrics], dict]`：`/analytics/scival/publication/metrics`，參數 `metricTypes=PublicationsInTopJournalPercentiles,OutputsInTopCitationPercentiles,FieldWeightedCitationImpact,Collaboration`、`publicationIds`（逗號分隔）、`yearRange`、`byYear=true`、`journalImpactType=CiteScore`、`includedDocs=AllPublicationTypes`；第二個回傳值為 `dataSource`。逐篇判斷：q1 = threshold 25 的 `valueByYear` 加總 > 0；top1／top10 = OutputsInTopCitationPercentiles threshold 1／10；intl_collab = collabType `International collaboration`；fwci = `valueByYear` 中唯一非 null 值；API 沒回傳的 id 不出現在 dict 中；`citations` 留 `None`
  - `selfcite.py`：
    - `class SelfCitationStrategy(Protocol)`: `id: str`、`label: str`、`verified: bool`、`async citations(client, ids, *, year_range: str, batch_size: int, use_cache: bool) -> dict[str, int]`
    - `SciValParamStrategy`（`id="scival_param"`, `label="SciVal includeSelfCitations=false"`, `verified=False`）：`metricTypes=CitationCount`、`includeSelfCitations=false`、`byYear=true`、`includedDocs=AllPublicationTypes`，逐篇加總 `valueByYear`
    - `NoExclusionStrategy`（`id="none"`, `label="不排除自我引用"`, `verified=True`）：同上但 `includeSelfCitations=true`
    - `ScopusCitationOverviewStrategy`（`id="scopus_citation_overview"`, `label="Scopus Citation Overview（exclude-self）"`）：`citations` 直接 `raise ElsevierError(None, False, "Scopus Citation Overview API 尚未取得授權，請改用其他策略")`。設計 §7.3 說這支 API 需另外申請；取得授權後再依實際回應格式實作
    - `STRATEGIES: dict[str, SelfCitationStrategy]`、`get_strategy(id) -> SelfCitationStrategy`
  - `scripts/sanitize_fixture.py <in.json> <out.json>`：讀檔 → `strip_links` → 寫出；若輸出仍含 `apiKey` 則中止

- [ ] **Step 1: 建 fixture** — `python scripts/sanitize_fixture.py api_test/output/20261030_134509_scival.json tests/fixtures/elsevier/scival_qf_67.json`，引用數檔同理（`..._134511_scival_citations_self_true.json`）；Scopus 分頁 fixture 手寫 3 頁（25+25+17 筆，ID 取自 `api_test/output/20261030_134507_scopus.json`），每頁含 `opensearch:totalResults="67"` 與 `cursor.@next`（最後一頁無 entry 或 `@next` 不變）
- [ ] **Step 2: 寫失敗測試**

```python
def test_fixtures_have_no_api_key():
    for p in Path("tests/fixtures").rglob("*.json"):
        assert "apiKey" not in p.read_text(encoding="utf-8"), p

def test_build_query():
    assert build_query("Department of Finance, National Taiwan University", 2021, 2025) == \
        'AFFIL("Department of Finance, National Taiwan University") AND AFFILCOUNTRY(Taiwan) AND PUBYEAR > 2020 AND PUBYEAR < 2026'

async def test_search_follows_cursor(respx_mock):     # 3 頁 → 67 筆、route.call_count == 3、第一頁 cursor="*"
async def test_search_zero_results(respx_mock):        # totalResults "0" + error entry → []

def test_year_ranges():
    assert scival_year_ranges(2021, 2025, 2026) == ("5yrs", "5yrsAndCurrentAndFuture")
    assert scival_year_ranges(2019, 2023, 2026) == ("10yrs", "10yrsAndCurrentAndFuture")
    with pytest.raises(ValueError): scival_year_ranges(2022, 2026, 2026)

async def test_metrics_from_recorded_response(respx_mock):  # 清大計財 67 篇實測值
    m, source = await fetch_metrics(client, ids67, year_range="5yrs", batch_size=100, use_cache=False)
    assert len(m) == 67 and source["lastUpdated"] == "2026-10-25"
    assert sum(x.q1 for x in m.values()) == 40
    assert sum(x.top1 for x in m.values()) == 0 and sum(x.top10 for x in m.values()) == 0
    assert sum(x.intl_collab for x in m.values()) == 20
    assert all(x.fwci is not None for x in m.values())

async def test_metrics_batches(respx_mock):           # 67 篇 batch_size=25 → 3 次請求，每次 publicationIds 數量 25/25/17，皆含 includedDocs=AllPublicationTypes
async def test_missing_ids_absent(respx_mock):        # 回應少一篇 → dict 少該 id
async def test_scival_param_strategy_sum(respx_mock): # 錄下的回應 → 加總 290；請求含 includeSelfCitations=false
async def test_citation_overview_strategy_unavailable():  # raise ElsevierError，message 含「尚未取得授權」
```

- [ ] **Step 3: 執行確認失敗** — `pytest tests/test_scopus.py tests/test_scival.py -v` → FAIL
- [ ] **Step 4: 實作上述介面**；另寫 `tests/live/test_live_api.py`：`pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE_API_TESTS") != "1", ...)`，以清大計財查詢確認 `search_docs` 筆數 > 0 且 `fetch_metrics` 回傳筆數等於送出筆數
- [ ] **Step 5: 執行確認通過** — `pytest -v` → PASS（live 測試顯示 skipped）
- [ ] **Step 6: Commit** — `git commit -m "feat(elsevier): add Scopus search, SciVal metrics and self-citation strategies"`

### Task 10: 指標定義與計算（純函式）

**Files:**
- Create: `app/metrics/__init__.py`, `app/metrics/indicators.py`, `app/metrics/compute.py`, `tests/test_metrics.py`, `tests/test_example_reports.py`, `tests/fixtures/example_report_known_mismatches.json`

**Interfaces:**
- Consumes: `PubMetrics`（Task 9）
- Produces:
  - `@dataclass(frozen=True) Indicator: id: str; label: str; source: str; aggregate: Literal["count", "mean", "ratio"]; field: str | None; per_faculty: bool; rankings: tuple[str, ...]`
  - `INDICATORS: tuple[Indicator, ...]`，依序：

| id | label | aggregate | field | per_faculty | rankings |
|---|---|---|---|---|---|
| `total` | 論文總篇數 | count | None（全部篇數） | True | QS, THE |
| `q1` | Q1 | count | q1 | True | 高教深耕 |
| `top1` | 被引數達全球前1%篇數 | count | top1 | True | THE, 高教深耕 |
| `top10` | 被引數達全球前10%篇數 | count | top10 | True | THE |
| `fwci` | FWCI | mean | fwci | False | THE, 高教深耕 |
| `avg_citations` | 平均論文被引次數(排除自我引用) | ratio | citations | False | QS |
| `intl_collab` | 國際合著篇數 | count | intl_collab | True | QS, THE, 高教深耕 |

  - `RANKING_TEXT = {"QS": "QS世界大學排名指標", "THE": "THE世界大學排名指標", "高教深耕": "教育部高教深耕指標"}`；`importance_note(ind) -> str` → 例：`"※THE世界大學排名指標 ※教育部高教深耕指標"`
  - `round2(x: Decimal) -> Decimal`（`quantize(Decimal("0.01"), ROUND_HALF_UP)`）
  - `@dataclass(frozen=True) UnitValues: raw: dict[str, Decimal | None]; five_year_avg: dict[str, Decimal | None]; value: dict[str, Decimal | None]`
    - count 類：`raw` = 篇數 A（整數 Decimal），`five_year_avg` = round2(A / years)，`value` = round2(A / years / teachers)
    - fwci：`raw` = 有值各篇的平均（未捨入），`value` = round2(raw)；全部缺值 → None
    - avg_citations：`raw` = 有值各篇被引數加總，`value` = round2(raw / 總篇數)
  - `compute_unit(pubs: list[PubMetrics], teachers: int | None, years: int) -> UnitValues`：`pubs` 為空 → 所有 `value` 為 None；`teachers` 為 None 或 0 → per_faculty 的 `value` 為 None，其他照算；欄位為 None 的論文不計入該指標的 A
  - `rank(values: dict[int, Decimal | None]) -> dict[int, int | None]`

- [ ] **Step 1: 寫失敗測試** `tests/test_metrics.py`

```python
def test_half_up_not_bankers():
    assert round2(Decimal("2.575")) == Decimal("2.58")   # 範例：資管系-台大 206/5/16

def test_per_faculty_matches_example():
    v = compute_unit(pubs(n=206, q1=133), teachers=16, years=5)
    assert v.value["q1"] == Decimal("1.66") and v.value["total"] == Decimal("2.58")
    assert v.five_year_avg["q1"] == Decimal("26.60") and v.raw["q1"] == 133

def test_recorded_qf_unit():  # 用 Task 9 的 67 篇 fixture 建 PubMetrics（citations 合計 290）
    v = compute_unit(qf67, teachers=None, years=5)
    assert v.value["fwci"] == Decimal("0.36") and v.value["avg_citations"] == Decimal("4.33")
    assert v.value["q1"] is None and v.raw["q1"] == 40      # 缺教師數 → 師均待確認，A 照算

def test_zero_teachers_no_division():  # teachers=0 → per_faculty 皆 None，不丟例外
def test_no_pubs_all_none():           # pubs=[] → value 全 None（不是 0）
def test_rank_competition():
    assert rank({1: D("1.20"), 2: D("1.66"), 3: D("1.66"), 4: None, 5: D("0.30")}) == {2: 1, 3: 1, 1: 3, 5: 4, 4: None}
def test_importance_note():
    assert importance_note(BY_ID["intl_collab"]) == "※QS世界大學排名指標 ※THE世界大學排名指標 ※教育部高教深耕指標"
```

- [ ] **Step 2: 寫對照測試** `tests/test_example_reports.py`：用 python-pptx 讀 `docs/產出範例-管理學院為例/管理學院/*.pptx` 第 5～10 頁中有「教師數」欄的表格，取每列 (A, B, 顯示值)，與 `A/5/B` 比較，`abs(差) < 0.01` 視為相符（容許範例的截斷與三位小數）。不相符的列寫成 `(檔名, 頁, 單位, 指標)`，斷言集合等於 `example_report_known_mismatches.json`。這個 JSON 第一次由實作者執行產生，**逐筆檢查並在每筆加上 `reason`**（例：「未除以 5」「來源不明」）後提交；設計 §13 記載全部有 46 個不符的值（含經費頁），本測試只涵蓋師均，筆數應 ≤ 46，超過就代表抽取有誤
- [ ] **Step 3: 執行確認失敗** — `pytest tests/test_metrics.py tests/test_example_reports.py -v` → FAIL
- [ ] **Step 4: 實作上述介面**
- [ ] **Step 5: 執行確認通過** — 同上 → PASS
- [ ] **Step 6: Commit** — `git commit -m "feat(metrics): add indicator definitions, calculation and ranking"`

### Task 11: 建立分析（比較範圍、查詢預覽、教師數快照）

**Files:**
- Create: `app/runs/__init__.py`, `app/runs/scope.py`, `app/runs/service.py`, `tests/test_runs_scope.py`, `tests/test_runs_service.py`

**Interfaces:**
- Consumes: `units_of`、`latest_version`、`latest_import`、`teacher_count`、`build_query`、`scival_year_ranges`、`get_setting`
- Produces:
  - `Comparison = Literal["benchmark", "college", "custom"]`
  - `@dataclass(frozen=True) RunConfig: year_from: int; year_to: int; ncu_depts: tuple[str, ...]; comparisons: frozenset[Comparison]; schools: tuple[str, ...]; custom_unit_ids: tuple[int, ...]; academic_year: int; teacher_column: str; force_refresh: bool = True`；`to_json() / from_json()` 存入 `runs.scope_json`
  - `default_period(today: date) -> tuple[int, int]` → 2026 年 → (2021, 2025)
  - `@dataclass(frozen=True) Group: comparison: Comparison; key: str; unit_ids: tuple[int, ...]`（benchmark 的 key = 中大系所；college 的 key = 中大學院；custom 的 key = `"custom"`）
  - `build_groups(units: list[Unit], cfg: RunConfig) -> list[Group]`：
    - benchmark：每個所選中大系所 → 中央單位 + 所選學校中同一 `ncu_dept` 的單位；中央單位不存在（中央分頁缺英文名稱）時不建立該組
    - college：所選系所涉及的每個學院 → 該學院**全部**中央單位
    - custom：`custom_unit_ids`
  - `@dataclass PreviewRow: unit_id: int; label: str; school: str; query: str; teacher: TeacherCount`
  - `preview_run(session, cfg) -> tuple[list[PreviewRow], list[str]]`：第二個值為阻擋錯誤（無 mapping、無教師數匯入、期間不合法、沒有任何單位）
  - `create_run(session, cfg) -> Run`：有阻擋錯誤時 `ValueError`；建立 `Run`（`status="pending"`、`mapping_version_id`、`teacher_import_id`、`self_citation_strategy` 取當下設定）與每個單位一筆 `RunUnit`（`role` = ncu／benchmark、`query`、`teacher_count`、`teacher_source` = `moe`／`manual:<原因>`／`missing`）。單位只建一次，即使出現在多個組
  - `last_config(session) -> RunConfig | None`（「沿用上一版設定」）

- [ ] **Step 1: 寫失敗測試**（`build_mapping` 造：中央 3 個管理學院系所、台大／政治各有資管對應、政治無會計）

```python
def test_default_period(): assert default_period(date(2026, 10, 30)) == (2021, 2025)
def test_benchmark_group_respects_school_selection():  # schools=("台大",) → 資管組只有 中央+台大
def test_college_group_includes_all_college_depts():   # 只選資管 → college 組含中央 3 個系所
def test_missing_ncu_unit_skips_benchmark_group():
def test_preview_blocks_without_teacher_import(session):  # errors 含「尚未匯入教師數」
def test_create_run_snapshots_teacher_counts(session):
    # 建立後再 add_override → 已建立 run 的 RunUnit.teacher_count 不變
def test_unit_shared_by_groups_created_once(session):  # 中央資管同時在 benchmark 與 college → RunUnit 只有一筆
def test_last_config_roundtrip(session):
```

- [ ] **Step 2: 執行確認失敗** — `pytest tests/test_runs_scope.py tests/test_runs_service.py -v` → FAIL
- [ ] **Step 3: 實作上述介面**
- [ ] **Step 4: 執行確認通過** — 同上 → PASS
- [ ] **Step 5: Commit** — `git commit -m "feat(runs): build comparison groups and create run snapshots"`

### Task 12: 執行分析、異常偵測、中斷與重抓

**Files:**
- Create: `app/runs/executor.py`, `app/runs/anomaly.py`, `app/runs/manager.py`, `tests/test_runs_executor.py`, `tests/test_anomaly.py`
- Modify: `app/main.py`（lifespan：`mark_interrupted`；`app.state.run_manager`）

**Interfaces:**
- Consumes: `search_docs`、`fetch_metrics`、`get_strategy`、`compute_unit`、`rank`、`INDICATORS`、`build_groups`、`RunConfig`、`make_client`
- Produces:
  - `async execute_run(run_id: int, session_factory, client: ElsevierClient, *, only_unfinished: bool = False) -> None`：
    1. `run.status="running"`；所有（未完成的）`RunUnit` 並行處理（`asyncio.gather`，速率由 client 控制）
    2. 單位流程：`status` fetching → `search_docs` → 刪除該單位舊的 `RunPublication` 再寫入 → `fetch_metrics` + 策略 `citations` → 合併寫入 `metrics_json` → `eid_count`、`fetched_at`、`status="done"`；任何 `ElsevierError` → `status="failed"`、`error=message`，不影響其他單位
    3. 共用同一查詢字串的單位只抓一次（`asyncio.Lock` per query 或先分組），結果寫給每個單位
    4. 全部結束後 `recompute(session, run_id)`；`run.status="done"`，`notes_json` 寫入 `data_date`（今天）、`source`（「Scopus Search API、SciVal Publication Metrics API；SciVal 資料更新日 <lastUpdated>」）、`importance`（各指標 `importance_note`）、`self_citation`（策略 label，`verified=False` 時附「待驗證」）
    5. 未預期例外 → `run.status="failed"` 並記錄訊息
    - `use_cache = not cfg.force_refresh`；`only_unfinished=True` 時一律 `use_cache=True`
  - `recompute(session, run_id) -> None`：刪除該 run 的 `results` 與自動產生的未處理 `anomalies`，依 `build_groups` 對每組每指標寫入 `Result(value, raw, five_year_avg, rank)`，再執行異常偵測。已標「已確認」的異常保留
  - `anomaly.detect(session, run_id, threshold: float) -> list[Anomaly]`，kind：
    - `zero_results`（eid_count == 0，detail 提示檢查 mapping 英文名稱）
    - `count_change`（同 school + ncu_dept + dept_en 在上一個 `done` 的 run 中的 eid_count，`abs(新-舊)/舊 > threshold`，detail 列兩版數字）
    - `missing_teacher`、`fetch_failed`（附 error）、`scival_missing`（metrics 篇數 < eid_count，附缺幾篇）
  - `resolve_anomaly(session, anomaly_id, note: str) -> None`（`status="confirmed"`）
  - `RunManager`：`start(run_id)`、`resume(run_id)`（`only_unfinished=True`）、`refetch_unit(run_id, unit_id)`（只重抓該單位、`use_cache=False`，再 `recompute`）、`is_running(run_id) -> bool`；以 `asyncio.create_task` 執行，保存 task 參考
  - `mark_interrupted(session) -> int`：把 `running`／`pending` 的 run 改 `interrupted`，回傳數量

- [ ] **Step 1: 寫失敗測試**（respx 模擬 Scopus/SciVal；fixture 建好 mapping、教師數、run）

```python
async def test_full_run_writes_results(run_env):        # status done；每組每指標有 Result；rank 正確；notes_json 有 data_date
async def test_failed_unit_does_not_stop_others(run_env):  # 台大查詢回 401 → 該 RunUnit failed + fetch_failed 異常；中央照常 done；run done
async def test_zero_results_anomaly_and_none_values(run_env):  # 0 篇 → zero_results；Result.value 為 None 而非 0
async def test_shared_query_fetched_once(run_env):     # 兩個單位同英文名稱 → Scopus route 只被呼叫一輪；兩單位 eid_count 相同
async def test_resume_after_interrupt(run_env):
    # 先讓一個單位 done、run 設 running → mark_interrupted == 1 → resume
    # → 已 done 單位不再請求；RunPublication 筆數不重複
async def test_refetch_unit_recomputes(run_env):       # 失敗單位改為成功回應 → refetch 後 status done、fetch_failed 異常消失
def test_count_change_threshold(session):              # 上一版 100、這版 65 → 有異常；這版 75 → 無
async def test_confirmed_anomaly_kept_on_recompute(run_env):
```

- [ ] **Step 2: 執行確認失敗** — `pytest tests/test_runs_executor.py tests/test_anomaly.py -v` → FAIL
- [ ] **Step 3: 實作上述介面**
- [ ] **Step 4: 執行確認通過** — `pytest -v` → PASS
- [ ] **Step 5: Commit** — `git commit -m "feat(runs): execute runs with anomaly detection, resume and refetch"`

### Task 13: 新增分析頁與執行進度

**Files:**
- Create: `app/web/routes/runs.py`, `app/web/templates/run_new.html`, `app/web/templates/_preview.html`, `app/web/templates/_progress.html`, `tests/test_web_runs_new.py`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: `RunConfig`、`default_period`、`last_config`、`preview_run`、`create_run`、`RunManager`、`ElsevierClient.check_connection`
- Produces:
  - `GET /runs/new`（`?reuse=1` 時以 `last_config` 預填）：期間（預設 `default_period`）、學院下拉＋系所勾選（選學院＝全選該院系所）、比較範圍勾選（預設 benchmark + college）、學校勾選（預設全選 mapping 中所有他校分頁）、自訂單位多選、教師數學年度（預設檔內最新）、「重新抓取全部資料」勾選（預設勾）
  - `POST /runs/preview`：HTMX 回傳 `_preview.html`（每個單位：標籤、查詢字串、教師數與來源；阻擋錯誤）
  - `POST /runs`：先 `check_connection`，不通則回到表單並顯示原因，不建立 run；通過則 `create_run` → `RunManager.start` → 303 到 `/runs/{id}`
  - `GET /runs/{id}/progress`：`_progress.html`，每個單位一列（狀態、篇數、錯誤）；run 未結束時含 `hx-get` 每 1 秒輪詢，結束時不再輪詢並觸發整頁重新整理
  - `POST /runs/{id}/resume`、`POST /runs/{id}/units/{unit_id}/refetch`

- [ ] **Step 1: 寫失敗測試**

```python
def test_new_run_defaults(client, seeded):     # 頁面期間預設 2021～2025、學校全勾
def test_preview_shows_queries(client, seeded):  # 含 'AFFIL("Department of Information Management, National Taiwan University")'
def test_create_blocked_when_offline(client, seeded, respx_mock):  # ConnectError → 顯示「無法連線」，Run 數量 0
def test_progress_stops_polling_when_done(client, seeded_done_run):  # done 的 run 片段不含 hx-trigger="every 1s"
```

- [ ] **Step 2: 執行確認失敗** — `pytest tests/test_web_runs_new.py -v` → FAIL
- [ ] **Step 3: 實作上述介面**
- [ ] **Step 4: 執行確認通過** — `pytest -v` → PASS
- [ ] **Step 5: Commit** — `git commit -m "feat(web): add new-run form, query preview and progress polling"`

---

## Milestone 5：結果與產出

### Task 14: 表格資料模型與結果頁

**Files:**
- Create: `app/reports/__init__.py`, `app/reports/tables.py`, `app/web/templates/run_detail.html`, `tests/test_report_tables.py`, `tests/test_web_run_detail.py`
- Modify: `app/web/routes/runs.py`

**Interfaces:**
- Consumes: `Result`、`RunUnit`、`Unit`、`SchoolAlias.display_name`、`INDICATORS`、`dept_short_names`／`college_short_names`／`rank_highlight_top` 設定、`resolve_anomaly`
- Produces（網頁、pptx、xlsx 共用）：
  - `unit_label(unit, comparison, dept_short: dict, school_display: dict) -> str`：benchmark → `"<中大系所簡稱>-<學校簡稱>"`（例 `資管系-台大`、`資管系-政大`、`資管系-中央`）；college → `"<中大系所簡稱>"`；簡稱缺漏時用全名
  - `@dataclass Row: unit_id: int; label: str; is_ncu: bool; raw: Decimal | None; five_year_avg: Decimal | None; teachers: int | None; teacher_manual: bool; value: Decimal | None; rank: int | None`
  - `@dataclass IndicatorTable: indicator_id: str; comparison: str; key: str; rows: list[Row]`（依 value 由高到低，None 排最後）
  - `indicator_table(session, run_id, comparison, key, indicator_id, sort_by: str | None = None) -> IndicatorTable`；`sort_by="q1"` 用於「篇數」頁（Q1 與總計同表，依 Q1 師均排序）
  - `@dataclass SummaryRow: dept_label: str; comparison: str; group_size: int; ranks: dict[str, int | None]`
  - `summary_table(session, run_id, college: str) -> list[SummaryRow]`：每個中大系所兩列（benchmark：「對標學校: N間」，N＝該組單位數含中央；college：「<學院簡稱>內」），`ranks` 為中央單位在各指標的名次，指標順序 q1、top1、top10、fwci、avg_citations、intl_collab
  - `fmt(value: Decimal | None) -> str`：None → `"待確認"`；其他 → 兩位小數
  - `GET /runs/{id}`：進度區（Task 13 片段）、指標頁籤（每頁兩張表：對標學校比較、<學院>內系所比較；中央列 class `ncu` 紅字；人工教師數加 `*`）、排名總表、待確認清單（逐筆「已確認＋備註」與「重新抓取」按鈕）、版本備註（`POST /runs/{id}/notes` 補充文字）、下載按鈕（Task 15、16 接上）

- [ ] **Step 1: 寫失敗測試**（用 Task 12 的 `run_env` 產生已完成 run，或直接寫入 Result）

```python
def test_unit_label_uses_display_name(session):   # 政治 → "資管系-政大"
def test_indicator_table_sorted_none_last(session):
def test_pubs_table_sorted_by_q1(session):        # sort_by="q1"
def test_summary_rows(session):                   # 資管：("資管系", "benchmark", 6, {"q1": 4, ...}) 與 college 列
def test_fmt():
    assert fmt(None) == "待確認" and fmt(Decimal("1.2")) == "1.20"
def test_detail_page_marks_ncu_and_pending(client, done_run):  # 中央列有 class="ncu"；缺教師數顯示「待確認」
def test_resolve_anomaly(client, done_run):       # POST 後該項顯示「已確認」與備註
```

- [ ] **Step 2: 執行確認失敗** — `pytest tests/test_report_tables.py tests/test_web_run_detail.py -v` → FAIL
- [ ] **Step 3: 實作上述介面**
- [ ] **Step 4: 執行確認通過** — `pytest -v` → PASS
- [ ] **Step 5: Commit** — `git commit -m "feat(reports): add shared table models and run result page"`

### Task 15: pptx 報告

**Files:**
- Create: `scripts/make_report_template.py`, `templates/report_template.pptx`, `app/reports/pptx_report.py`, `app/reports/exports.py`, `tests/test_pptx_report.py`
- Modify: `app/web/routes/runs.py`

**Interfaces:**
- Consumes: `indicator_table`、`summary_table`、`fmt`、`importance_note`、`Run.notes_json`、未處理 `Anomaly` 數
- Produces:
  - `scripts/make_report_template.py`：複製 `docs/產出範例-管理學院為例/管理學院/2021-2025管理學院資管系論文表現分析_0511更新版.pptx`，刪除全部投影片（移除 `sldIdLst` 項目與對應 relationship），保留母片與版面，存成 `templates/report_template.pptx`
  - `build_dept_report(session, run_id: int, ncu_dept: str, out_path: Path) -> Path`，版面配置「標題投影片」「標題及內容」「只有標題」，16:9（12192000×6858000 EMU）：

| 頁 | 標題 | 內容 |
|---|---|---|
| 1 | `國立中央大學<學院>-<系所簡稱>` | 副標題：產出日期 `YYYY/MM/DD` |
| 2 | 目錄 | 依範例第 2 頁文字，系所與學院名稱代入；第一版不列經費項目 |
| 3 | `II、<學院>各系所論文指標排名` | `summary_table`：表頭兩列（單位／對外對內項目／優質期刊／5年論文被引次數／國際合著篇數(師均)），名次以 `#n` 顯示，名次 ≤ `rank_highlight_top` 紅字 |
| 4 | `III. <起>-<迄>論文發表篇數師均` | 左：對標學校比較、右：<學院簡稱>內系所比較；欄位：單位、Q1 5年總數、Q1 5年平均(A)、總計 5年總數、總計 5年平均(B)、單年教師數(C)、Q1 (A/C)、總計 (B/C)；依 Q1 師均排序 |
| 5 | `<起>-<迄> 被引數達全球前1%篇數(師均)` | 左右兩表：單位、5年篇數 (A)、單年教師數(B)、單年師均 (A/5/B) |
| 6 | `<起>-<迄> 被引數達全球前10%篇數(師均)` | 同上 |
| 7 | `<起>-<迄> FWCI` | 單位、FWCI |
| 8 | `<起>-<迄>平均論文被引次數(排除自我引用)` | 單位、平均論文被引次數(排除自我引用) |
| 9 | `<起>-<迄>國際合著篇數(師均)` | 同第 5 頁欄位 |

  - 每頁（4～9）：左上 `importance_note`；右上註腳文字框「註1：單年教師數來自教育部公開資訊平台（<學年度>學年度）；標 * 者為人工修改。註2：資料來源 Scopus、SciVal，資料取得日期 <data_date>。」；第 8 頁另加「排除自我引用方式：<策略 label>（待驗證）」（`verified` 為 False 時）
  - 中央列整列文字 `RGBColor(0xFF, 0x00, 0x00)`；值為 None 顯示「待確認」
  - 有未處理異常時，每頁頂端加紅字「※本報告含 N 項待人工確認資料」
  - 範例第 3 頁（經費）第一版省略，所以總頁數 9
  - `exports.export_path(settings, run_id, filename) -> Path`（`data/exports/<run_id>/`）；`record_export(session, run_id, kind, path)`
  - `GET /runs/{id}/pptx/{ncu_dept}`：產生（已存在且 run 未重算則沿用）並下載，檔名 `<起>-<迄><學院><系所簡稱>論文表現分析.pptx`；`GET /runs/{id}/pptx.zip`：該 run 所有中大系所的報告打包

- [ ] **Step 1: 執行 `python scripts/make_report_template.py`** — 產生的範本用 python-pptx 開啟後 `len(prs.slides) == 0` 且 `"標題及內容" in [l.name for l in prs.slide_layouts]`
- [ ] **Step 2: 寫失敗測試**（產出後重新讀取）

```python
def test_report_structure(done_run, tmp_path):
    p = Presentation(build_dept_report(session, run.id, "資訊管理學系", tmp_path / "r.pptx"))
    assert len(p.slides) == 9
    assert p.slides[0].shapes.title.text == "國立中央大學管理學院-資管系"

def test_ncu_row_red_and_values(done_run, tmp_path):
    t = tables_on(p.slides[4])[0]                    # Top1% 對標表
    ncu = next(r for r in t.rows if r.cells[0].text == "資管系-中央")
    assert all(run.font.color.rgb == RGBColor(0xFF, 0, 0) for c in ncu.cells for para in c.text_frame.paragraphs for run in para.runs)
    assert ncu.cells[3].text == fmt(expected_value)

def test_pending_banner_and_missing_teacher(done_run_with_anomaly, tmp_path):  # 含「待人工確認」與「待確認」
def test_self_citation_footnote(done_run, tmp_path):  # 第 8 頁含「待驗證」
def test_download_route(client, done_run):            # 200、content-type 為 pptx；exports 有紀錄
```

- [ ] **Step 3: 執行確認失敗** — `pytest tests/test_pptx_report.py -v` → FAIL
- [ ] **Step 4: 實作上述介面**（表格位置與欄寬參考範例：左表 x≈0.26M EMU、右表 x≈6.5M EMU、表格 top≈2.6M EMU）
- [ ] **Step 5: 執行確認通過** — `pytest -v` → PASS；另外手動開啟一份產出檔，與範例資管系報告並排檢查版面
- [ ] **Step 6: Commit** — `git commit -m "feat(reports): generate per-department pptx reports"`

### Task 16: xlsx 產出

**Files:**
- Create: `app/reports/xlsx_report.py`, `tests/test_xlsx_report.py`
- Modify: `app/web/routes/runs.py`

**Interfaces:**
- Consumes: `indicator_table`、`summary_table`、`RunUnit`、`RunPublication.metrics_json`、`Anomaly`、`Run.notes_json`、`export_path`、`record_export`
- Produces:
  - `build_run_workbook(session, run_id: int, out_path: Path) -> Path`，工作表依序：`排名總表`、每個指標一張（名稱為 `Indicator.label` 去除 `[]:*?/\` 後截到 31 字；內容為各組的表格，上下排列，每組上方一列組名）、`查詢條件`（單位、學校、查詢字串、狀態、篇數、取得時間）、`逐篇指標`（單位、scopus_id、eid、year、q1、top1、top10、fwci、intl_collab、citations）、`教師數`（單位、人數、來源、原因、教育部代碼）、`待確認`（類型、單位、說明、狀態、備註）、`版本備註`
  - 數值寫入儲存格為數字（Decimal → float，number_format `0.00`），None 寫字串「待確認」；中央列字型紅色
  - `GET /runs/{id}/xlsx`：產生並下載，檔名 `<起>-<迄>論文指標分析_run<id>.xlsx`

- [ ] **Step 1: 寫失敗測試**

```python
def test_workbook_sheets(done_run, tmp_path):
    wb = load_workbook(build_run_workbook(session, run.id, tmp_path / "r.xlsx"))
    assert wb.sheetnames[0] == "排名總表" and {"查詢條件", "逐篇指標", "教師數", "待確認", "版本備註"} <= set(wb.sheetnames)
    assert all(len(n) <= 31 for n in wb.sheetnames)

def test_values_and_pending(done_run, tmp_path):   # 師均儲存格為數字、format "0.00"；缺教師數為「待確認」
def test_no_api_key_in_workbook(done_run, tmp_path):
    # 解壓 xlsx，所有 XML 內容不含 "apiKey"
def test_download_route(client, done_run):
```

- [ ] **Step 2: 執行確認失敗** — `pytest tests/test_xlsx_report.py -v` → FAIL
- [ ] **Step 3: 實作上述介面**
- [ ] **Step 4: 執行確認通過** — `pytest -v` → PASS
- [ ] **Step 5: Commit** — `git commit -m "feat(reports): export run workbook"`

---

## Milestone 6：版本紀錄

### Task 17: 版本紀錄、版本比較與收尾

**Files:**
- Create: `app/runs/compare.py`, `app/web/templates/runs.html`, `app/web/templates/compare.html`, `tests/test_runs_compare.py`
- Modify: `app/web/routes/runs.py`, `app/web/routes/home.py`, `app/web/templates/home.html`, `README.md`

**Interfaces:**
- Consumes: `Result`、`Unit`、`indicator_table`、`fmt`
- Produces:
  - `@dataclass DiffRow: school: str; ncu_dept: str; comparison: str; indicator_id: str; a: Decimal | None; b: Decimal | None; delta: Decimal | None`
  - `compare_runs(session, run_a: int, run_b: int) -> list[DiffRow]`：以 (school, ncu_dept, comparison, indicator_id) 對齊兩版結果；只在一邊有的單位也列出（另一邊 None）；`delta = b - a`，任一邊 None 則為 None
  - `GET /runs`：歷次分析（建立時間、期間、範圍摘要、狀態、未處理異常數、下載連結）；`GET /runs/compare?a=&b=`：差異表，只顯示 `delta != 0` 或一邊缺值的列，可切換顯示全部
  - 首頁「最近分析」顯示最近 5 筆；`README.md` 的「目前狀態」改為「第一版已實作」並補上實際的頁面名稱與指令

- [ ] **Step 1: 寫失敗測試**

```python
def test_compare_aligns_by_unit_key(session):   # 兩個 run，資管 q1 1.10 → 1.20：delta 0.10
def test_compare_unit_only_in_one(session):     # b 多了中興 → a 為 None、delta None
def test_history_and_compare_pages(client, two_runs):  # /runs 列出兩筆；/runs/compare 顯示 "0.10"
```

- [ ] **Step 2: 執行確認失敗** — `pytest tests/test_runs_compare.py -v` → FAIL
- [ ] **Step 3: 實作上述介面並更新 README**
- [ ] **Step 4: 全部驗證** — `pytest -v` 全數通過；雙擊 `start.bat`（或 `.venv\Scripts\python -m app`）確認瀏覽器開啟首頁、各頁可進入；有校內網路時加跑 `set RUN_LIVE_API_TESTS=1 && pytest tests/live -v`
- [ ] **Step 5: Commit** — `git commit -m "feat(runs): add run history and version comparison"`

---

## 實作備註

- 測試共用 fixture 集中在 `tests/conftest.py`：`settings`、`client`、`session`、`session_factory`、`seeded`（匯入加了「中央」分頁的 mapping 與小型教育部檔）、`run_env`、`done_run`。後面的 Task 擴充它，不要各自重寫
- `api_test/` 保持不動，作為連線診斷工具；它錄下的 `api_test/output/*.json` 含 API Key（已在 .gitignore 中），只能經 `scripts/sanitize_fixture.py` 轉成 fixture
- 目前 mapping 檔沒有「中央」分頁，真實檔案匯入後會有 error 且沒有中大單位可分析——這是正確行為；開發與測試用 `build_mapping` 產生含「中央」分頁的檔案
