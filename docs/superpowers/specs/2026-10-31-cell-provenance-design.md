# 分析結果逐格來源說明：設計文件

- 日期：2026-10-31
- 狀態：設計已確認，尚未實作
- 上層文件：`2026-10-30-research-benchmarking-design.md`（§7 指標與計算、§9 產出）

## 1. 目標

本專案要求所有數據都能查證。分析結果中的每一個數字格，都要能說出「怎麼算出來的」，並能追到構成它的每一篇論文或每一列教育部資料。

### 成功標準

- 有人問到任何一格時，承辦人不用翻原始資料，在系統內就能拿出公式、代入數字、資料來源與逐篇清單。
- 說明與表格數字不可能互相矛盾：說明用同一套計算函式從原始資料重算，重算值與存檔值不符時會明確標示。
- 拿到 xlsx 的人不開系統，也能從註解與逐篇工作表自行核對。

### 範圍

| 範圍 | 內容 |
|---|---|
| 網頁：分析詳情的排名總表 | 停留說明，點擊跳到指標分頁並標亮該列 |
| 網頁：7 個指標表（對標組、院內） | 停留說明，點擊打開逐篇明細抽屜 |
| 網頁：版本比較表 | 停留說明（A、B 兩版的代入式與差異） |
| xlsx | 數字格加註解；逐篇工作表加篩選；教師數工作表補上教育部明細 |

### 不做

- pptx 不加說明（簡報是給人看的，版面放不下）。
- 不提供說明框內的試算功能。
- 不回填歷史分析的教師數快照（見 §4.3）。

## 2. 做法

說明**不存進資料庫**，而是在顯示時從已保存的原始資料（`RunPublication.metrics_json`、`RunUnit`、`Result`）**用 `app/metrics/compute.py` 的同一套函式重算**。這樣說明不會和數字各說各話，也順便讓每一格都做一次自我核對。

只有一項原始資料目前沒有保存：教師數的教育部對應明細。它要在建立分析時存成快照（§4）。

網頁的說明文字在產生頁面時就寫進 HTML（每格一個 `<template>`），停留時馬上顯示。逐篇論文清單可能有數百列，所以等點擊後才用 htmx 向伺服器取。

考慮過但沒有採用的做法：

- 每次停留都向伺服器要說明：掃視表格時每格都要等。
- 計算時把說明存成 `Result.explain_json`：和原始資料重複，日後改說明文字還得搬舊資料。

## 3. 元件

### 3.1 `app/reports/provenance.py`（新增，只讀不寫）

```python
Column = Literal["raw", "avg", "teachers", "value", "rank"]

@dataclass
class Source:
    label: str          # 例如 "A"、"B"、"資料"
    text: str           # 例如 "Q1 篇數 37／總篇數 123；SciVal PublicationsInTopJournalPercentiles（CiteScore, ≤25%）"

@dataclass
class Drill:
    kind: Literal["papers", "teachers", "group"]
    unit_id: int
    indicator_id: str
    filter: str | None  # papers 時：要標亮的 PubMetrics 欄位（q1/top1/...），None 表示全部

@dataclass
class Explanation:
    title: str                  # "單年師均（Q1） 資訊管理學系－中央大學"
    formula: str | None         # "A ÷ 年數 ÷ B"
    substituted: str | None     # "37 ÷ 5 ÷ 18 = 0.41"
    sources: list[Source]
    stored: Decimal | None      # Result 裡的值
    recomputed: Decimal | None  # 用原始資料重算的值
    mismatch: bool              # stored 與 recomputed 不同（皆 None 視為相同）
    missing_reason: str | None  # 值為待確認時的原因
    drill: Drill | None

def explain_cell(session, run_id, unit_id, comparison, indicator_id, column: Column) -> Explanation
def explain_unit(session, run_id, unit_id, comparison) -> dict[tuple[str, Column], Explanation]
```

- `explain_unit` 一次讀取該單位的論文與教師資料，產生所有指標、所有欄的說明，避免同一頁重複讀取。`explain_cell` 是它的單格版本。
- 重算時呼叫 `compute_unit(unit_pubs(...), ru.teacher_count, years)`；名次則對同組所有單位的 `Result.value` 呼叫 `rank()`。
- 指標名稱、SciVal 指標與門檻來自 `INDICATORS`（`Indicator.source`），不另外寫死文字。

### 3.2 各欄說明內容

| 欄 | formula / substituted | sources | drill |
|---|---|---|---|
| raw（篇數 A，count） | 「符合條件篇數」／「37 篇（共 123 篇）」 | 判定規則（`Indicator.source`）、Scopus 查詢字串、資料取得日與 SciVal 更新日 | papers，filter = `Indicator.field` |
| avg（年平均） | 「A ÷ 年數」／「37 ÷ 5 = 7.40」 | 同 raw | 同 raw |
| teachers（B） | 無 | 教育部學年、欄位、各對應系所的代碼／名稱／人數；人工修改時顯示原因與修改日期 | teachers |
| value（count，師均） | 「A ÷ 年數 ÷ B」／「37 ÷ 5 ÷ 18 = 0.41」 | A、B 的來源 | 同 raw |
| value（mean，FWCI） | 「有 FWCI 的論文平均」／「Σ 152.31 ÷ 118 篇 = 1.29（5 篇無值不列入）」 | SciVal 指標、更新日 | papers，依 FWCI 排序 |
| value（ratio，平均被引） | 「被引數加總 ÷ 總篇數」／「1,845 ÷ 123 = 15.00」 | 自我引用排除策略（`run.self_citation_strategy` 的 label）、更新日 | papers，依被引數排序 |
| rank | 「同組 12 個單位，值比它高的有 2 個 → 第 3 名」；有同值時加「與 X 同值」 | 比較範圍（對標組或院內） | group |
| 排名總表的名次格 | 同 rank，並附該指標的值 | 同 rank | 連到 `?ind=<指標>&hl=<unit_id>` |
| 版本比較 A／B 格 | 該版本 value 的 substituted | 該版本的分析編號與建立時間 | 連到該版本的指標分頁 |
| 版本比較差異格 | 「B − A」／「0.45 − 0.41 = +0.04」 | 無 | 無 |

值為 `None`（待確認）時，`missing_reason` 依序判斷為：

1. `RunUnit.status == "failed"`：「抓取失敗：{error}」
2. 篇數為 0：「Scopus 查無論文」
3. 篇數類指標的 B 為 None：「缺教師數」，並附教師數說明
4. mean／ratio 沒有任何一篇有值：「SciVal 沒有回傳這項指標」

### 3.3 `app/reports/tables.py`

`Row` 新增 `explain: dict[Column, Explanation]`，`SummaryRow` 新增 `explain: dict[str, Explanation]`（以指標為鍵）。`DiffRow` 由 compare 路由另外補上 A、B 的說明。網頁和 xlsx 都從這裡取，不各自組文字。

## 4. 教師數快照

### 4.1 資料

`RunUnit` 新增欄位 `teacher_detail_json: Text | None`（Alembic migration）。內容如下：

```json
{
  "source": "moe | manual | missing",
  "academic_year": 113,
  "column": "total",
  "moe_school": "國立中央大學",
  "rows": [{"code": "xxxxx", "name": "資訊管理學系", "value": 18}],
  "override": {"count": 20, "reason": "...", "created_at": "2026-10-20T10:00:00"}
}
```

`override` 只在人工修改時出現。`rows` 在人工修改時仍然記錄教育部原本的數字，方便比對修改前後。

### 4.2 寫入時機

`app/runs/service.py` 的 `create_run` 建立 `RunUnit` 時一起寫入。`teacher_count()` 改成同時回傳實際讀到的各列資料（在 `TeacherCount` 新增 `rows`、`override_created_at`），不用另外再查一次。

### 4.3 舊分析

舊分析的 `teacher_detail_json` 是 NULL。說明框顯示「本次分析未保存教育部對應明細」，只列出 `teacher_source` 的內容，**不會**用目前的對應倒推。

## 5. 網頁

### 5.1 標記

- 可查的格子加上 `class="ev"`、`tabindex="0"`、`data-ev="<template id>"`。樣式為虛線底線，游標為 `help`。
- 每格的說明放在 `<template id="ev-…">`，由共用片段 `_evidence.html` 從 `Explanation` 產生。
- 沒有說明的格子（標題、單位名稱）不加標記。

### 5.2 說明框（`static/evidence.js`，新增）

- 整頁只有一個浮動框（`role="tooltip"`），停留的格子以 `aria-describedby` 指向它。
- 滑鼠停留 150ms 後顯示；滑鼠可以移進框內選取、複製文字；離開格子和框 200ms 後關閉。
- 格子取得焦點時顯示；按 Enter 等同點擊；按 Esc 關閉。
- 觸控：點一下顯示，框內的「查看明細」連結才打開抽屜。
- 框的位置預設在格子下方，空間不夠時改到上方，並限制在畫面範圍內。
- `mismatch` 時框頂端顯示紅色「⚠ 重算值與存檔值不符（存檔 X，重算 Y）」，一致時顯示「✓ 重算值與存檔值一致」。

### 5.3 逐篇明細抽屜

- 路由：`GET /runs/{run_id}/evidence?unit=&cmp=&ind=&col=`，回傳 htmx 片段，放進頁面右側的抽屜。
- `drill.kind == "papers"`：上方重複該格的公式、代入式與 Scopus 查詢字串。表格欄位為 EID（連到 `https://www.scopus.com/record/display.uri?eid=<eid>&origin=resultslist`）、年份、Q1、Top1%、Top10%、國際合著、FWCI、被引數、是否在 SciVal 中。`filter` 指定的欄位整欄加底色，符合的列排在前面。表格加上 `table-controls.js` 的搜尋與分頁。
- `drill.kind == "teachers"`：列出快照中的教育部各列與人工修改紀錄。
- `drill.kind == "group"`：列出同組各單位的值與名次，標亮目前這個單位。
- 參數對不到資料時回傳 404 片段，抽屜內顯示「找不到資料」。

### 5.4 整頁提示

分析詳情頁若有任何格子 `mismatch`，在頁面頂端顯示「有 N 格重算值與存檔值不符」，並列出涉及的單位與指標（連到該指標分頁並標亮該列）。處理方式是沿用現有的單位「重新抓取」，這次不新增「只重算」的按鈕。

> 與討論時的差異：原本打算新增異常類型 `provenance_mismatch`。但異常是在 `recompute()` 內偵測的，而 recompute 剛好用同一套函式寫入數值，在那裡永遠不會偵測到不符。不符只會在資料或程式於計算之後才改變時出現，所以改在顯示時偵測並提示，不新增異常類型。

## 6. xlsx

- 7 張指標工作表與排名總表的數字格，加上 openpyxl `Comment`。內容為說明的純文字版：formula、substituted、sources 各一行；不符時第一行為警告。註解作者為「系統」。
- 「逐篇指標」工作表：第一列為使用說明（「篩選學校、中大系所，再篩指標欄 = TRUE，筆數即為該指標的 A」），第二列為標題，加上 AutoFilter 並凍結標題列。
- 「教師數」工作表新增欄位：教育部學年、欄位、系所代碼、系所名稱、該列人數。一個單位對到多列時拆成多列；沒有快照的單位，代碼欄填「未保存」。
- 數字格維持存值，不改用 Excel 公式。原因：部分檢視器不會重算而顯示空白；SciVal 的逐篇判定本來就無法在 Excel 裡重做。

## 7. 錯誤處理

| 情況 | 處理 |
|---|---|
| 重算值與存檔值不符 | 說明框與 xlsx 註解顯示警告，詳情頁頂端提示；不擋報告產出 |
| 舊分析沒有教師數快照 | 顯示「未保存」，不倒推 |
| 單位抓取失敗或查無論文 | 說明框顯示原因，不提供明細連結 |
| `metrics_json` 無法解析 | 該格說明顯示「原始資料損毀」並標為不符，其餘格子照常 |
| 明細路由參數錯誤 | 404 片段 |

## 8. 測試

沿用 `tests/run_helpers.py` 的假資料。

- `tests/test_provenance.py`
  - count／mean／ratio 三類指標的每一欄：代入式字串正確，`recomputed == stored`
  - 名次：同值同名次、下一名跳號、無值不排名
  - `missing_reason` 的四種情況
  - 直接改動 `Result.value` 後 `mismatch` 為 True
- 教師數快照：`create_run` 後 `teacher_detail_json` 包含正確的各列與 override；事後修改 `UnitTeacherLink` 或新增 override，說明不變
- 網頁
  - 指標分頁每個數字格都有對應的 `<template>`
  - `/runs/{id}/evidence` 的 papers 清單中，符合 filter 的列數等於 A
  - 排名總表、版本比較的格子有說明
  - 錯誤參數回傳 404
- xlsx：抽查指標格的註解內容；逐篇工作表依單位與 `q1` 篩選後的列數等於 Q1 的 A；教師數工作表有代碼欄
- `evidence.js` 的互動（停留、焦點、Esc、觸控、定位）以瀏覽器實際操作驗證，不寫自動化測試

## 9. 影響的檔案

| 檔案 | 變更 |
|---|---|
| `app/reports/provenance.py` | 新增 |
| `app/reports/tables.py` | Row／SummaryRow 加 explain |
| `app/teachers/service.py` | `TeacherCount` 加 rows、override_created_at |
| `app/runs/service.py` | 寫入 `teacher_detail_json` |
| `app/db/models.py`、`alembic/versions/` | `RunUnit.teacher_detail_json` 與 migration |
| `app/web/routes/runs.py` | evidence 路由、mismatch 計數、`hl` 參數 |
| `app/web/routes/history.py`（compare） | 比較表說明 |
| `app/web/templates/run_detail.html`、`compare.html`、`_evidence.html`（新增）、`_evidence_drawer.html`（新增）、`base.html` | 標記、抽屜容器、載入 JS |
| `app/web/static/evidence.js`（新增）、`app.css` | 說明框與抽屜 |
| `app/reports/xlsx_report.py` | 註解、篩選、教師數欄位 |
