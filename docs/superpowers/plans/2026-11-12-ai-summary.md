# AI 簡報總結與跨期比較 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 Claude 為每次分析產生 (A) 每份系所簡報最後一頁的總結與跨期比較、(B) 每個學院一份風格相同的全院總結簡報，經承辦人審閱修改後才放進產出。

**Architecture:** 新增 `app/ai/` 套件：`baseline.py` 挑前期分析、`inputs.py` 從資料庫組出送給模型的資料與指紋、`claude.py` 呼叫 Claude（透過 `SummaryWriter` 介面，測試用假的）、`service.py` 存取/判斷過時、`jobs.py` 背景產生。簡報端把 `ReportBuilder` 的版面抽成 `DeckBuilder` 基底，A 頁與 B 簡報共用同一套版面元件。網頁在分析結果頁加「AI 總結」分頁。

**Tech Stack:** Python 3.11、FastAPI + Jinja2 + htmx、SQLAlchemy 2 + Alembic（SQLite）、python-pptx、官方 `anthropic` SDK、pytest + respx。

**Spec:** `docs/superpowers/specs/2026-11-12-ai-summary-design.md`

## Global Constraints

- API key 只放 `.env` 的 `ANTHROPIC_API_KEY`，不寫進程式碼、資料庫或 log。
- 預設模型 `claude-opus-5-5`，可在設定頁更換（設定鍵 `ai_model`）。
- 呼叫 Claude 一律用官方 `anthropic` SDK；實作 `claude.py` 前先載入 `claude-api` skill 並讀 `python/claude-api/README.md` 與 structured outputs 段落，不憑記憶寫 SDK 呼叫。Opus 5.5：不送 `thinking: disabled`、不送 `temperature`；明確設 `output_config.effort = "medium"`；啟用 server-side fallback（beta `server-side-fallback-2026-07-01`，`fallbacks: "default"`），回應先檢查 `stop_reason == "refusal"`。
- 語氣：正式繁體中文、台灣用語；不尖銳；進步具體寫出；落後寫成「仍有成長空間／可持續努力的方向」；不用「嚴重落後」「表現不佳」等字眼。
- 只能引用提供的數字，不自行計算新數字；數字四捨五入到小數第二位（沿用 `app/reports/tables.fmt`）。
- 待人工確認（`Anomaly.status != "confirmed"`）的項目不得當作結論。
- 前後兩期基準不同（對標學校、教師數學年度、教師數欄位、自我引用方式、期間年數）時，文字附註「比較基準不同」。
- 只有承辦人按下按鈕才呼叫 Claude；AI 失敗不得影響原本簡報與 xlsx 下載。
- 無前期資料時寫「無前期資料可比較」。
- A 頁標題預設「{系所}總結與建議」；B 封面「{學院} {期間} 論文指標全院總結」。
- A、B 必須和現有系所簡報同一範本 `templates/report_template.pptx`、同一套版面元件（字型 `Microsoft JhengHei`、`NAVY/BLUE/GRAY` 配色、頁尾）。
- 真的呼叫 API 的測試放 `tests/live/`，預設不執行（沿用 `RUN_LIVE_API_TESTS=1` 慣例）。

## Review Focus

1. **被當作前期的分析遭刪除或重新編號** — `delete_run` 與 `renumber_runs` 會改 run id；`runs.ai_baseline_run_id` 與 `ai_summaries.baseline_run_id` 必須跟著更新或清成 NULL，不能指到別的分析。（Task 2 測試）
2. **改選的前期分析不含某些系所** — 那些系所顯示「無前期資料」，不 fallback 到自動挑選。（Task 3 測試）
3. **總結很長、或系所很多** — A 頁文字超出版面時縮字級到下限後分頁，不得與頁尾重疊；B 的系所多時同樣分頁。（Task 6、7 測試）
4. **產生到一半某個系所失敗**（額度用完、拒絕、網路斷）— 其他系所繼續產生，失敗的那筆顯示白話原因，已存的不被覆蓋。（Task 5 測試）
5. **人工修改後資料又變了** — 指紋不符時仍視為過時，下載的簡報不放這頁，網頁提示重新產生；人工修改的文字保留在資料庫不刪除。（Task 5 測試）

---

### Task 1: `compare_runs` 加上名次

**Files:**
- Modify: `app/runs/compare.py`
- Test: `tests/test_runs_compare.py`

**Interfaces:**
- Produces: `DiffRow` 新增欄位 `rank_a: int | None = None`、`rank_b: int | None = None`（放在 `unit_b` 之後，維持既有位置參數相容）。`compare_runs(session, run_a, run_b) -> list[DiffRow]` 簽名不變。

- [ ] **Step 1: Write the failing test**

```python
async def test_compare_includes_ranks(session, two_runs):
    a, b = two_runs
    row = next(r for r in compare_runs(session, a.id, b.id)
               if (r.school, r.ncu_dept, r.comparison, r.indicator_id) == ("中央", "資訊管理學系", "benchmark", "q1"))
    ranks = {rid: session.scalars(select(Result.rank).join(Unit, Unit.id == Result.unit_id).where(
        Result.run_id == rid, Unit.school == "中央", Unit.ncu_dept == "資訊管理學系",
        Result.comparison == "benchmark", Result.indicator_id == "q1")).one() for rid in (a.id, b.id)}
    assert (row.rank_a, row.rank_b) == (ranks[a.id], ranks[b.id])
```

- [ ] **Step 2: Run** `pytest tests/test_runs_compare.py::test_compare_includes_ranks -v` — Expected: FAIL（`DiffRow` 沒有 `rank_a`）
- [ ] **Step 3: Implement** — `_values` 多選 `Result.rank`，`compare_runs` 填入 `rank_a/rank_b`。
- [ ] **Step 4: Run** `pytest tests/test_runs_compare.py -v` — Expected: 全部 PASS（既有的 history 比較頁測試也跑：`pytest tests -k compare -v`）
- [ ] **Step 5: Commit** `git commit -m "feat(compare): include ranks in run diffs"`

---

### Task 2: 資料表 `ai_summaries` 與前期改選欄位

**Files:**
- Modify: `app/db/models.py`、`app/runs/renumber.py`、`app/web/routes/history.py`（`delete_run`）
- Create: `alembic/versions/0008_ai_summaries.py`
- Test: `tests/test_ai_storage.py`

**Interfaces:**
- Produces:
  - `Run.ai_baseline_run_id: Mapped[int | None]`（nullable，無外鍵約束以免刪除卡住；由程式維護）
  - `class AiSummary(Base)`，`__tablename__ = "ai_summaries"`，欄位：`id`、`run_id`（FK runs.id, index）、`kind: str`（`"dept"` 或 `"college"`）、`key: str`（系所名或學院名）、`baseline_run_id: int | None`、`summary: Text`、`comparison: Text | None`、`input_hash: str`、`model: str`、`edited: bool = False`、`created_at`、`updated_at`；`UniqueConstraint("run_id", "kind", "key")`。
  - `renumber_runs` 的 `CHILDREN` 加入 `AiSummary`，並同步改寫 `Run.ai_baseline_run_id` 與 `AiSummary.baseline_run_id` 中的舊編號。
  - `delete_run` 刪除該 run 的 `AiSummary`；把其他分析中指向它的 `ai_baseline_run_id`／`baseline_run_id` 設為 NULL。

- [ ] **Step 1: Write the failing tests** — 在 `tests/test_ai_storage.py`：
  - `test_migration_creates_table`：`init_db` 後 `inspect(engine).get_table_names()` 含 `"ai_summaries"`，`runs` 欄位含 `"ai_baseline_run_id"`。
  - `test_renumber_moves_baseline_refs`：建立 run 1、2、3；run 3 的 `ai_baseline_run_id = 2`、run 3 有一筆 `AiSummary(baseline_run_id=2)`；刪掉 run 1 的資料列後呼叫 `renumber_runs`；assert run（新 2）的 `ai_baseline_run_id == 1`、`AiSummary.run_id == 2`、`baseline_run_id == 1`。
  - `test_delete_run_clears_baseline_refs`（web client，用 `web_done_run` 建兩次分析）：B 的 `ai_baseline_run_id = A`，POST `/runs/{A}/delete` 後 B（已重編）的 `ai_baseline_run_id is None`，且 A 的 `AiSummary` 已刪除。
- [ ] **Step 2: Run** `pytest tests/test_ai_storage.py -v` — Expected: FAIL
- [ ] **Step 3: Implement** model、migration（`revision = "0008_ai_summaries"`，`down_revision = "0007_import_retired_at"`；downgrade 用 `batch_alter_table` 移除欄位並 drop table）、renumber 與 delete 的改動。
- [ ] **Step 4: Run** `pytest tests/test_ai_storage.py tests/test_db.py -v` — Expected: PASS
- [ ] **Step 5: Commit** `git commit -m "feat(db): store AI summaries and the chosen baseline run"`

---

### Task 3: 挑選前期分析

**Files:**
- Create: `app/ai/__init__.py`、`app/ai/baseline.py`
- Test: `tests/test_ai_baseline.py`

**Interfaces:**
- Consumes: `Run`、`RunConfig.from_json`
- Produces:
  - `auto_baseline(session, run: Run, dept: str) -> Run | None` — 候選：`status == "done"`、`id != run.id`、`period_to < run.period_to`、`dept in ncu_depts`；取 `period_to` 最大者，同期間取 `id` 最大（最後完成）。
  - `baseline_for(session, run: Run, dept: str) -> Run | None` — `run.ai_baseline_run_id` 有值時：該分析存在、已完成且含 `dept` 就回傳，否則 `None`（**不** fallback 到自動）；沒值時回傳 `auto_baseline`。
  - `basis_differences(run: Run, base: Run, dept: str) -> list[str]` — 回傳白話差異，依序檢查：對標學校（`RunConfig.schools_for(dept)` 集合不同 → `"對標學校不同"`）、`academic_year` → `"教師數學年度不同"`、`teacher_column` → `"教師數採用欄位不同"`、`self_citation_strategy` → `"自我引用處理方式不同"`、期間年數 → `"分析年數不同"`。
  - `baseline_choices(session, run: Run) -> list[Run]` — 下拉選單用：其他所有已完成分析，新到舊。

- [ ] **Step 1: Write the failing tests**（用 `make_run(session, year_from=..., year_to=...)` 建 run，直接把 `status` 設 `"done"`，不需抓資料）
  - `test_picks_previous_period_latest_rerun`：2020–2024 跑兩次（id p1 < p2）、2021–2025 一次（cur）、2019–2023 一次；`auto_baseline(cur, "資訊管理學系").id == p2`。
  - `test_falls_back_to_nearest_earlier_period`：只有 2018–2022 與 cur 2021–2025 → 回傳 2018–2022。
  - `test_same_period_rerun_is_not_baseline`：只有另一個 2021–2025 → `None`。
  - `test_requires_dept`：前期分析的 `ncu_depts=("財務金融學系",)` → `None`。
  - `test_override_without_dept_gives_none`：`cur.ai_baseline_run_id` 指向不含該系所的分析，即使有合格的自動前期 → `baseline_for` 為 `None`。
  - `test_basis_differences`：前期 `schools=("台大",)`、`academic_year=113` → 回傳 `["對標學校不同", "教師數學年度不同"]`。
- [ ] **Step 2: Run** `pytest tests/test_ai_baseline.py -v` — Expected: FAIL（module not found）
- [ ] **Step 3: Implement** 上列函式。
- [ ] **Step 4: Run** `pytest tests/test_ai_baseline.py -v` — Expected: PASS
- [ ] **Step 5: Commit** `git commit -m "feat(ai): pick the previous-period run to compare against"`

---

### Task 4: 送給模型的資料與指紋

**Files:**
- Create: `app/ai/inputs.py`
- Test: `tests/test_ai_inputs.py`

**Interfaces:**
- Consumes: `baseline_for`、`basis_differences`（Task 3）、`compare_runs` 的 `rank_a/rank_b`（Task 1）、`summary_table`、`indicator_table`、`fmt`（`app/reports/tables.py`）、`funding_table`、`run_colleges`、`money`（`app/funding/service.py`）、`BY_ID`、`INDICATORS`。
- Produces:
  - `dept_input(session, run: Run, dept: str) -> dict` — 鍵：`"系所"`、`"學院"`、`"期間"`、`"教師數學年度"`、`"指標"`（每個指標：`名稱`、各比較群組的 `數值`（`fmt` 字串）、`名次`、`群組單位數`）、`"計畫經費"`（有資料時該系所列與全院列數，用 `money`）、`"待人工確認"`（該系所相關的未確認 anomaly 文字清單）、`"前期"`（`None` 或 `{"分析編號", "期間", "比較基準差異": [...], "指標變化": [{名稱, 比較, 前期數值, 本期數值, 前期名次, 本期名次}], "計畫經費變化": {前期, 本期} 或 None}`）。只放中大該系所的列。計畫經費變化用 `funding_table(session, base_run, college)` 取前期該系所列；前期 `has_data` 為 False 時為 None。
  - `college_input(session, run: Run, college: str) -> dict` — `"學院"`、`"期間"`、`"系所"`: 該學院在本次分析中的每個系所的 `dept_input` 精簡版（指標名次與數值、前期變化）、`"計畫經費"` 全院表。B 的前期：自動模式各系所各自 `baseline_for`；改選模式 `baseline_for` 已處理。
  - `input_hash(payload: dict) -> str` — `sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True))` 的 hex。

- [ ] **Step 1: Write the failing tests**（用 `done_run` fixture）
  - `test_dept_input_matches_report_numbers`：`dept_input(done_run, "資訊管理學系")["指標"]` 中 top1 對標的數值等於 `fmt(indicator_table(...).ncu row.value)`、名次等於 `summary_table` 的對應名次。
  - `test_no_baseline_means_none`：只有一次分析 → `payload["前期"] is None`。
  - `test_baseline_changes_and_basis`：再建一個 2020–2024、`schools=("台大",)` 的完成分析（`execute_run` 跑完），`payload["前期"]["比較基準差異"]` 含 `"對標學校不同"`，`指標變化` 含 q1 的前後數值字串。
  - `test_open_anomaly_listed`：新增一筆該系所單位的 open anomaly → 出現在 `"待人工確認"`；狀態改 `"confirmed"` 後消失。
  - `test_hash_changes_when_data_changes`：把某 `Result.value` 改掉後 `input_hash(dept_input(...))` 不同；不改則相同（呼叫兩次相等）。
- [ ] **Step 2: Run** `pytest tests/test_ai_inputs.py -v` — Expected: FAIL
- [ ] **Step 3: Implement** 上列函式；dict 內一律用字串化後的數字（`fmt`/`money`），確保 hash 穩定。
- [ ] **Step 4: Run** `pytest tests/test_ai_inputs.py -v` — Expected: PASS
- [ ] **Step 5: Commit** `git commit -m "feat(ai): build model input and fingerprint from stored results"`

---

### Task 5: Claude 呼叫、設定、儲存與背景產生

**Files:**
- Create: `app/ai/prompts.py`、`app/ai/claude.py`、`app/ai/service.py`、`app/ai/jobs.py`
- Modify: `pyproject.toml`（加 `anthropic`）、`.env.example`（加 `ANTHROPIC_API_KEY=`）、`app/config.py`、`app/db/settings_store.py`、`app/main.py`、`app/web/routes/settings.py`、`app/web/templates/settings.html`
- Test: `tests/test_ai_service.py`、`tests/test_web_settings.py`

**Interfaces:**
- Consumes: `dept_input`、`college_input`、`input_hash`（Task 4）、`baseline_for`（Task 3）、`AiSummary`（Task 2）、`run_colleges`。
- Produces:
  - `Settings.anthropic_api_key: str = ""`；`save_env(path, values: dict[str, str])`（改成通用版，既有兩個呼叫點改傳 dict；`reset_all` 一併清空 `ANTHROPIC_API_KEY`）。
  - `DEFAULT_SETTINGS["ai_model"] = "claude-opus-5-5"`、`DEFAULT_SETTINGS["ai_tone_prompt"] = prompts.DEFAULT_TONE`。
  - `prompts.DEFAULT_TONE: str`（Global Constraints 的語氣規則全文）、`prompts.system_prompt(kind: str, tone: str) -> str`（`kind` 為 `"dept"`／`"college"`；加上「只能引用提供的數字」「待人工確認不可作結論」「比較基準差異需附註」「無前期寫『無前期資料可比較』」；`kind == "dept"` 時加「只談這個系所，不評論其他系所」）。
  - `@dataclass SummaryDraft(summary: str, comparison: str | None)`。
  - `class SummaryWriter(Protocol): def write(self, system: str, payload: dict) -> SummaryDraft`。
  - `class AiError(Exception)`，`message` 為白話中文。
  - `class ClaudeWriter(SummaryWriter)`：`__init__(self, api_key: str, model: str, client=None)`；用 structured outputs 取得 `{"summary": str, "comparison": str | null}`；例外轉換：未設 key → `AiError("尚未設定 Claude API Key，請到「設定」頁填寫")`、`AuthenticationError` → `"Claude API Key 無效"`、`RateLimitError` → `"Claude 額度或速率已達上限，請稍後再試"`、`APIConnectionError` → `"無法連線到 Claude，請檢查網路"`、`stop_reason == "refusal"` → `"模型拒絕產生這段文字"`、其他 `APIStatusError` → `"Claude 服務錯誤（{status}）"`。
  - `service.generate(session, run: Run, kind: str, key: str, writer: SummaryWriter, tone: str, model: str) -> AiSummary` — 組 payload、呼叫 writer、upsert（`edited=False`，存 `input_hash`、`baseline_run_id`、`model`）。writer 拋 `AiError` 時不動既有資料列，直接往上拋。
  - `service.save_edit(session, summary_id: int, summary: str, comparison: str | None) -> AiSummary` — `edited=True`，不改 `input_hash`。
  - `service.is_fresh(session, row: AiSummary) -> bool` — 重新計算 payload 的 hash 與 `row.input_hash` 比較。
  - `service.fresh_text(session, run: Run, kind: str, key: str) -> SummaryDraft | None` — 有資料列且 `is_fresh` 才回傳。
  - `service.targets(session, run: Run) -> list[tuple[str, str]]` — 所有 `("dept", 系所)` 依 `ncu_depts` 順序，再加每個 `("college", 學院)`。
  - `jobs.AiJobManager(session_factory, writer_factory: Callable[[], SummaryWriter])`：`start(run_id, only: tuple[str, str] | None = None) -> bool`（已在跑回 False）、`state(run_id) -> JobState | None`；`JobState(total: int, done: int, errors: dict[tuple[str, str], str], finished: bool)`。逐一產生（同步呼叫用 `asyncio.to_thread`），單筆 `AiError` 記到 `errors` 後繼續。`app.state.ai_jobs` 在 lifespan 建立；`writer_factory` 每次讀 `app.state.settings.anthropic_api_key` 與 `get_setting("ai_model")`。
  - 設定頁：新增 Claude API Key 欄（顯示 `mask_key`，POST `/settings/ai-key` 寫 `.env`）、模型文字欄、語氣提示 textarea（空白時回到預設）。

- [ ] **Step 1: Write the failing tests**（`tests/test_ai_service.py`，用 `done_run`；`FakeWriter` 記錄收到的 system/payload，回傳固定 `SummaryDraft`，可設定對某 key 拋 `AiError`）
  - `test_generate_saves_row`：`generate(... "dept", "資訊管理學系", FakeWriter())` 後資料庫有一筆，`input_hash == input_hash(dept_input(...))`、`edited is False`。
  - `test_system_prompt_has_tone_and_rules`：FakeWriter 收到的 system 含設定的語氣全文與「只能引用提供的數字」。
  - `test_error_keeps_previous_row`：先成功產生，再用會拋 `AiError` 的 writer 產生 → 拋出 `AiError`，資料列內容不變。
  - `test_edit_then_data_change_is_stale`：`save_edit` 後 `fresh_text` 回傳修改後文字；把某 `Result.value` 改掉後 `fresh_text is None`，資料列仍在且 `edited is True`。
  - `test_job_continues_after_one_failure`：`AiJobManager` 對 `("dept","資訊管理學系")` 拋錯，`targets` 其他項成功；等 task 完成後 `state.finished`、`errors` 只有那一筆、其他項都有資料列。
  - `test_claude_writer_maps_errors`：用假的 `client`（物件，其 messages 呼叫拋 `anthropic.AuthenticationError`／回傳 `stop_reason="refusal"`）→ 對應的 `AiError.message`；空 key → 未設定訊息且不呼叫 client。
  - `test_claude_writer_parses_output`：假 client 回傳 structured output JSON → `SummaryDraft` 欄位正確，且請求中的 `model` 等於傳入的 model。
  - `tests/test_web_settings.py::test_save_ai_key_masks_and_writes_env`：POST `/settings/ai-key` 後 `.env` 有 `ANTHROPIC_API_KEY=...`，設定頁只顯示末 4 碼；`reset` 後清空。
- [ ] **Step 2: Run** `pytest tests/test_ai_service.py tests/test_web_settings.py -v` — Expected: FAIL
- [ ] **Step 3: Implement** 上列介面（先載入 `claude-api` skill 再寫 `claude.py`）。`pip install -e ".[dev]"` 安裝 `anthropic`。
- [ ] **Step 4: Run** `pytest tests/test_ai_service.py tests/test_web_settings.py -v`，再跑 `pytest` 全部 — Expected: PASS
- [ ] **Step 5: Commit** `git commit -m "feat(ai): generate, store and refresh summaries with Claude"`

---

### Task 6: 共用版面基底與 A 總結頁

**Files:**
- Modify: `app/reports/pptx_report.py`
- Test: `tests/test_pptx_report.py`

**Interfaces:**
- Consumes: `SummaryDraft`（Task 5）
- Produces:
  - `class DeckBuilder`：持有 `session`、`prs`、`layouts`、`open_anomalies`；方法 `_slide(title, kicker="", subtitle="")`（從 `ReportBuilder` 搬來）、`_cover(label: str, title: str, subtitle: str)`（封面版面從 `ReportBuilder.cover` 抽出，文字由參數給）、`_footer(name: str)`、`text_pages(title: str, kicker: str, sections: list[tuple[str, str]])`（每節為 `(小標, 內文)`；用 `_fit_size` 由 18pt 往下縮到 12pt，仍放不下就以段落為界分頁，續頁標題加「（續）」；內容區在 `SUB_Y` 與 `FOOTER_RULE_Y` 之間）。
  - `ReportBuilder(DeckBuilder)`：`__init__(session, run_id, ncu_dept, ai_text: SummaryDraft | None = None)`；`build()` 在指標頁之後、`_footer` 之前，若 `ai_text` 有值呼叫 `ai_page()`：標題 `f"{ncu_dept}總結與建議"`，章節標籤沿用 detail 章的編號樣式，sections = `[("總結", ai_text.summary)] + ([("與前期比較", ai_text.comparison)] if comparison else [("與前期比較", "無前期資料可比較")])`。
  - `build_dept_report(session, run_id, ncu_dept, out_path, ai_text: SummaryDraft | None = None)`。

- [ ] **Step 1: Write the failing tests**
  - `test_no_ai_text_keeps_structure`：既有 `test_report_structure` 仍為 9 頁（不傳 `ai_text`）。
  - `test_ai_page_is_last`：傳 `SummaryDraft("本系 Q1 表現穩定。", "Q1 師均由 1.10 提升至 1.20。")` → 10 頁；最後一頁含 `"資訊管理學系總結與建議"`、兩段文字，且有頁尾 `"10 / 10"`。
  - `test_ai_page_without_baseline_says_so`：`comparison=None` → 最後一頁含 `"無前期資料可比較"`。
  - `test_long_ai_text_splits_without_overlap`：summary 為 40 段各 60 字 → 頁數 > 10，續頁標題含「（續）」，每個文字框底部 `top + height <= FOOTER_RULE_Y`（沿用既有不重疊檢查的寫法）。
  - `test_ai_page_uses_report_style`：AI 頁標題字型為 `Microsoft JhengHei`、顏色 `NAVY`，和指標頁標題相同。
- [ ] **Step 2: Run** `pytest tests/test_pptx_report.py -v` — Expected: 新測試 FAIL，舊測試 PASS
- [ ] **Step 3: Implement** 抽出 `DeckBuilder`（純搬移，不改既有頁面輸出），加 `text_pages` 與 `ai_page`。
- [ ] **Step 4: Run** `pytest tests/test_pptx_report.py tests/test_example_reports.py -v` — Expected: PASS
- [ ] **Step 5: Commit** `git commit -m "feat(pptx): add the AI summary page on a shared deck base"`

---

### Task 7: B 全院總結簡報

**Files:**
- Create: `app/reports/college_report.py`
- Test: `tests/test_college_report.py`

**Interfaces:**
- Consumes: `DeckBuilder`、`text_pages`（Task 6）、`SummaryDraft`（Task 5）
- Produces:
  - `build_college_report(session, run_id: int, college: str, ai_text: SummaryDraft, out_path: Path) -> Path` — 頁面：封面（label `"員額小組論文資料"`、title `f"{NCU_SHEET}{college}"`、subtitle `f"{period} 論文指標全院總結\n{今天 yyyy/mm/dd}"`）→ `text_pages("全院總結", "I. 全院總結", [("全院總結", summary)])` → `text_pages("與前期比較", "II. 與前期比較", [...])`（comparison 為 None 時內文「無前期資料可比較」）→ `_footer(f"{NCU_SHEET} {college}｜{period} 論文指標全院總結")`。
  - `college_report_filename(run: Run, college: str) -> str` = `f"{period}{college}論文指標全院總結.pptx"`。

- [ ] **Step 1: Write the failing tests**（用 `done_run`）
  - `test_college_deck_pages`：3 頁；封面標題含 `"管理學院"`；第 2 頁含 summary 文字；第 3 頁含 comparison 或「無前期資料可比較」。
  - `test_college_deck_matches_dept_style`：B 封面背景色等於系所簡報封面背景色（`NAVY`）；B 內容頁標題字型／顏色與系所簡報第 3 頁相同；頁尾格式 `"2 / 3"`；兩者 `slide_width/slide_height` 相同。
  - `test_college_deck_long_text_paginates`：很長的 summary → 頁數 > 3 且不重疊。
- [ ] **Step 2: Run** `pytest tests/test_college_report.py -v` — Expected: FAIL
- [ ] **Step 3: Implement** `CollegeReportBuilder(DeckBuilder)` 與上列函式。
- [ ] **Step 4: Run** `pytest tests/test_college_report.py -v` — Expected: PASS
- [ ] **Step 5: Commit** `git commit -m "feat(pptx): add the college summary deck in the report style"`

---

### Task 8: 網頁「AI 總結」分頁與下載

**Files:**
- Create: `app/web/routes/ai_summary.py`、`app/web/templates/_ai_summary.html`
- Modify: `app/main.py`（include router，放在 `runs.router` 之前）、`app/web/routes/runs.py`（`download_pptx`、`download_all_pptx` 傳 `ai_text=fresh_text(...)`；`result_context` 在 `ind == "ai"` 時提供 AI 資料）、`app/web/templates/run_detail.html`（分頁列加 `<a href="?ind=ai">AI 總結</a>`、下載分頁加 B 的連結）
- Test: `tests/test_web_ai_summary.py`

**Interfaces:**
- Consumes: Task 3、5、6、7 的全部 Produces。
- Produces（路由）：
  - `GET /runs/{id}?ind=ai`：列出每個 target：名稱、狀態（未產生／已產生／已修改／**資料已更新，總結可能過時**／錯誤訊息）、兩個 textarea（總結、與前期比較）與「儲存」「重新產生」按鈕；上方：「產生 AI 總結」按鈕、前期下拉選單（`自動（上一期）` + `baseline_choices`）、每個系所目前使用的前期與 `basis_differences`。
  - `POST /runs/{id}/ai/generate` → `ai_jobs.start(id)`，303 回 `?ind=ai`。
  - `POST /runs/{id}/ai/generate/{kind}/{key}` → `ai_jobs.start(id, only=(kind, key))`。
  - `GET /runs/{id}/ai/progress` → htmx 片段（`done/total`、錯誤清單），未完成時每 2 秒輪詢（比照 `_progress.html`）。
  - `POST /runs/{id}/ai/summaries/{summary_id}` → `save_edit`。
  - `POST /runs/{id}/ai/baseline`（form `baseline` = `""` 或 run id）→ 設 `run.ai_baseline_run_id`；不自動重新產生（既有文字因指紋含前期而變成過時）。
  - `GET /runs/{id}/college-summary/{college}` → 有 fresh 的 B 文字才產生 pptx 下載，否則 409「請先產生並確認全院總結」；`GET /runs/{id}/college-summary.zip` 打包所有學院中 fresh 的那些。
  - 分析未完成（`status != "done"`）時 AI 路由回 409，訊息沿用「分析尚未完成」。

- [ ] **Step 1: Write the failing tests**（`web_done_run`；把 `client.app.state.ai_jobs.writer_factory` 換成回傳 `FakeWriter`）
  - `test_ai_tab_lists_targets`：GET `?ind=ai` 含「資訊管理學系」「管理學院」與「產生 AI 總結」。
  - `test_generate_then_download_has_ai_page`：POST generate，等 job 完成，GET `/runs/{id}/pptx/資訊管理學系` 的簡報最後一頁含「總結與建議」。
  - `test_stale_summary_not_in_download`：產生後改一筆 `Result.value`，下載的簡報沒有 AI 頁；`?ind=ai` 顯示「資料已更新，總結可能過時」。
  - `test_edit_is_used_in_download`：POST 修改文字後下載，最後一頁含修改後文字。
  - `test_college_download_requires_summary`：未產生時 GET college-summary → 409；產生後 → 200，回應為 pptx。
  - `test_baseline_override_marks_stale`：產生後 POST baseline 改成另一個分析 → 該系所顯示過時。
  - `test_missing_key_shows_message`：writer 拋 `AiError("尚未設定 Claude API Key，請到「設定」頁填寫")` → progress 片段顯示該訊息；原本的 pptx 下載仍 200。
- [ ] **Step 2: Run** `pytest tests/test_web_ai_summary.py -v` — Expected: FAIL
- [ ] **Step 3: Implement** 路由、模板、下載整合。
- [ ] **Step 4: Run** `pytest` 全部 — Expected: PASS
- [ ] **Step 5: Commit** `git commit -m "feat(web): AI summary tab, review flow and downloads"`

---

### Task 9: 實際 API 測試與文件

**Files:**
- Create: `tests/live/test_live_claude.py`
- Modify: `README.md`（「功能」加 AI 總結；「安裝與啟動」的 `.env` 範例加 `ANTHROPIC_API_KEY`；新增「AI 總結」一節：怎麼產生、審閱、過時提示、改選前期、B 下載；「尚未納入」刪掉「AI 功能：待確認」）

**Interfaces:**
- Consumes: `ClaudeWriter`、`system_prompt`、`DEFAULT_TONE`

- [ ] **Step 1: Write the live test** `test_claude_writer_live`：`RUN_LIVE_API_TESTS != "1"` 或沒有 `ANTHROPIC_API_KEY` 時 skip；用一份小的固定 payload 呼叫 `ClaudeWriter(...).write(system_prompt("dept", DEFAULT_TONE), payload)`，assert `summary` 非空、不含「嚴重落後」「表現不佳」，且沒有出現 payload 以外的數字（從 summary 抽出 `\d+\.\d{2}` 全部都在 payload 的字串值中）。
- [ ] **Step 2: Run** `pytest tests/live/test_live_claude.py -v` — Expected: SKIPPED（預設）；有 key 時手動 `set RUN_LIVE_API_TESTS=1` 跑一次確認 PASS。
- [ ] **Step 3: Update README** 如上。
- [ ] **Step 4: Run** `pytest` 全部 — Expected: PASS（live 測試 skipped）
- [ ] **Step 5: Commit** `git commit -m "docs: describe AI summaries; add live Claude test"`
