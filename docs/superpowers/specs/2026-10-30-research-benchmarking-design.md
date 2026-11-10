# 員額小組論文指標自動化系統：設計文件

- 日期：2026-10-30
- 狀態：設計已確認，尚未實作
- 依據：`docs/產製員額小組論文資料操作步驟.pptx`、`docs/中大與他校mapping表.xlsx`、`docs/產出範例-管理學院為例/`（8 份系所報告）、`docs/教1-1.專任教師數-以「系(所)」統計.xlsx`、第一次會議待確認事項（Google Doc）

## 1. 目標

把研發處「產製員額小組論文資料」的人工流程（操作步驟 1～9）改成程式自動執行：

- 步驟 1～2（找出各系所在 Scopus 上的英文單位名稱）由人工維護的 mapping 表取代，程式不再搜尋
- 步驟 3～7（Scopus 取 EID → SciVal 取指標）改用官方 API：**Scopus Search**、**SciVal Publication Metrics**
- 步驟 8（教育部教師數）改為匯入教育部檔案，並可人工修改
- 步驟 9（產製簡報）自動產出 pptx 與 xlsx，結果同時呈現在網頁上

### 成功標準

1. 承辦人不需要操作 Scopus／SciVal 網頁，就能產出與範例報告同格式的各系所簡報
2. 同一次分析中的所有單位在同一時間抓取資料，同一系所只有一組數字（避免範例中 227 篇與 188 篇並存的情形）
3. 每次分析都是可回看的版本，記錄資料取得日期、資料來源、指標重要性
4. 3 月以前可以開始使用（第一版以管理學院試辦）

### 不在第一版範圍

- 國科會及產學計畫經費（報告第 I 部分）：之後另行設計，第一版只保留模組位置
- AI 功能：廠商與預算尚未確認
- 多人帳號與權限：第一版只有承辦人一人使用

## 2. 使用情境與限制

- 在承辦人自己的 Windows 電腦上執行，用瀏覽器操作；伺服器只監聽 `127.0.0.1`
- Elsevier API 只能在校內網路或學校 VPN 下使用（要在校外使用，需另外申請 insttoken）
- 開發階段使用小組的測試 API Key，正式上線時改用承辦人的個人帳號
- 之後擴大到全小組時，改為在校內伺服器啟動即可，程式架構不需要調整

## 3. 技術選擇

| 項目 | 選擇 | 理由 |
|---|---|---|
| 後端 | Python 3.11+、FastAPI、Uvicorn | 沿用現有 `api_test/` 的 Python 程式；非同步方便並行呼叫 API |
| 前端 | Jinja2 伺服器端渲染 + HTMX + 自訂 CSS | 不需要 Node.js 與打包步驟；頁面輕、載入快；HTMX 檔案放在本機，不依賴 CDN |
| 資料庫 | SQLite（WAL 模式）+ SQLAlchemy 2 + Alembic | 單一檔案、免安裝；Alembic 讓之後升級程式時保留歷史版本資料 |
| HTTP | httpx（async） | 支援並行、逾時、重試 |
| 檔案 | openpyxl（xlsx）、python-pptx（pptx） | 讀 mapping／教師數檔、輸出報表 |
| 設定 | pydantic-settings + `.env` | API Key 等敏感設定不進版控 |
| 測試 | pytest、respx（模擬 httpx） | |

啟動方式：雙擊 `start.bat` → 第一次執行時建立 `.venv` 並安裝套件 → 啟動伺服器 → 自動開啟瀏覽器。

## 4. 系統架構

```
瀏覽器（HTML + HTMX）
        │ HTTP（127.0.0.1）
FastAPI
 ├─ web/        路由、頁面模板、靜態檔
 ├─ mapping/    mapping 表解析、驗證、版本差異
 ├─ teachers/   教育部教師數匯入、對應、人工修改
 ├─ elsevier/   Scopus Search / SciVal client（限速、重試、快取）
 ├─ metrics/    指標定義表、計算、排名、捨入
 ├─ runs/       分析任務協調、進度、異常偵測、版本
 ├─ reports/    pptx / xlsx 產出
 ├─ funding/    （預留）國科會經費
 └─ db/         SQLAlchemy 模型與 migration
        │
     SQLite（data/app.db）
```

各模組之間只透過明確的函式介面溝通：

| 模組 | 輸入 | 輸出 | 依賴 |
|---|---|---|---|
| `mapping` | xlsx 檔 | `MappingVersion`（單位清單）、驗證問題清單、與前一版的差異 | db |
| `teachers` | 教育部 xlsx/csv、人工修改 | 每個單位的教師數與來源（教育部／人工＋原因） | db、mapping |
| `elsevier` | 查詢字串、期間、Scopus ID 清單 | EID 清單、逐篇指標 | httpx、db（快取） |
| `metrics` | 逐篇指標、教師數、指標定義 | 各單位的指標值、排名 | 無（純計算，可獨立測試） |
| `runs` | 分析設定 | 一個完整的分析版本 | 以上全部 |
| `reports` | 分析版本 | pptx、xlsx 檔 | metrics 的結果 |

## 5. 核心概念

### 5.1 單位（Unit）

一個（學校, 系所），附 Scopus 查詢用的英文名稱。所有單位都來自 mapping 表。

### 5.2 Mapping 表格式

- 一間學校一個分頁，**新增「中央」分頁**放中大各系所自己的英文名稱
- 每個分頁的欄位：`學院 | 中大系所 | <學校名> | 系所中文名稱 | 系所英文名稱 | 備註`
  - 第 1～2 欄是中大的學院與系所（對應的鍵）
  - 第 3～5 欄是該校對應的學院、系所中文名稱、英文名稱
  - 「中央」分頁的第 3～5 欄填中大自己的資料
- 解析規則：
  - 欄位以表頭文字定位，容許些微差異（例如師大分頁是「系所英文名稱(scopus)」）
  - 去除前後空白、全形空白（`　`）、不換行空白（`\xa0`）
  - `x`／`X` 或空白表示「該校無對應系所」
  - 表頭以外、第 6 欄之後的儲存格（目前清大、陽明交大、成大分頁的表頭列有）忽略並列為提醒
- 驗證問題分「錯誤」（無法使用，例如中大系所缺英文名稱）與「提醒」（例如有中文名稱但缺英文名稱）
- 每次匯入都存成新的 `MappingVersion`（記錄檔名、SHA-256、匯入時間），畫面顯示與上一版的差異（新增、移除、英文名稱變更）

### 5.3 對標組與比較範圍

- **對標學校比較**：中大某系所 + 所有在 mapping 中有英文名稱的他校對應系所。預設全選，每次分析可以取消或勾選個別學校；提供「沿用上一版設定」
- **院內比較**：中大同一學院的所有系所
- **自訂比較**：任意挑選單位

範例報告第 4 頁的排名總表，是同一次分析中「對標學校比較」與「院內比較」兩種結果的彙整。

### 5.4 期間

預設為分析年度的前 5 年（2026 年 → 2021–2025），可以修改。

### 5.5 教師數

#### 教育部檔案格式（依 `docs/教1-1.專任教師數-以「系(所)」統計.xlsx`）

- 單一工作表 `Current`：第 1 列是標題，第 3 列是表頭，第 4 列起是資料，最後約 15 列是合併儲存格的統計說明（解析時略過）
- **一個檔案就包含多個學年度**（範例是 106～114 學年度、169 校、約 3.6 萬列），所以匯入一次即可，分析時再選學年度
- 使用的欄位：`學年度`、`學校名稱`、`單位代碼`、`單位名稱`、`專任教師數-教師總數總計`
- 預設使用「專任教師數-教師總數總計」，可在設定中改用「專任助理教授以上人數-總計」等其他欄位。依據：範例報告中他校的教師數與 114 學年度的「總計」一致（例如台大資管是 16，助理教授以上是 15；成大、中山、台科大、政大也都相符）

#### 對應方式

- **學校**：mapping 分頁名稱（台大、成大…）與教育部校名（國立臺灣大學…）不同，因此設一張「學校對照」設定表，預先填好目前的 10 所學校，可以在畫面上修改
- **系所**：在同一所學校內，以正規化後的單位名稱（臺→台、去除空白）比對 mapping 的系所中文名稱
  - 比對成功後記住教育部的 `單位代碼`。代碼跨年度不變（例如中大資管 106～114 學年度都是 `06131053`），之後的學年度直接用代碼對應，系所改名也不受影響
  - 對應不到的列出來，由承辦人從該校的單位清單中挑選
  - 一個單位可以對應多個教育部單位，人數相加（例如中山「光電工程學系」在 mapping 中同時包含研究所與學系）；也可以只選其中一個（例如成大有「工業與資訊管理學系」和「資訊管理研究所」，範例報告只採用前者的 17 人）

#### 使用規則

- 師均使用**單一年度**的教師數，不是 5 年總和（操作步驟第 11 頁）；學年度在建立分析時選擇，預設為檔案中最新的學年度（範例報告 2026/03 產出，使用的是 114 學年度）
- 每筆都可以人工修改，修改時必填原因；報告註腳會標示來源；重新匯入（上傳或從教育部下載）會清除所有人工修改，以新資料為準（過去的分析已存下當時數字）
- 對標比較與院內比較使用同一個教師數（範例中中大資管在兩處都是 23）
- 注意：範例報告中**中大各系所**的教師數（資管 23、財金 10、企管 18…）在 106～114 學年度的教育部資料中都找不到相同數字；報告註 2 寫的是「校內系所比較教師數來自 SciVal」。因此中大的數字很可能需要人工修改，確認來源前先列為待確認事項（見第 14 節）

### 5.6 分析版本（Run）

每次執行分析都產生一個不可變更的版本，內容包括：

- 期間、比較範圍、選取的學校
- 使用的 mapping 版本、教師數（含人工修改）
- 每個單位實際送出的查詢字串、取得時間、EID 清單、逐篇指標原始資料
- 計算結果與排名
- 備註：資料取得日期（自動）、資料來源（自動）、指標重要性（由指標定義表自動產生，可補充文字）
- 產出的 pptx / xlsx

版本可以回看、重新下載，也可以比較兩個版本的指標差異。

## 6. 資料流程

1. **匯入 mapping** → 驗證 → 存成新版本
2. **匯入教師數** → 對應單位 → 人工修正
3. **建立分析**：選期間、學院或系所、比較範圍、學校、教師數學年度 → 預覽每個單位的查詢字串
4. **抓取資料**（背景任務，每個單位有各自的進度）
   - Scopus Search：`AFFIL(<英文名稱>) AND AFFILCOUNTRY(Taiwan) AND PUBYEAR > <起年-1> AND PUBYEAR < <迄年+1>`，以 cursor 翻頁取得全部 EID（`AFFILCOUNTRY(Taiwan)` 依操作步驟第 5 頁的篩選）
     - 英文名稱**不加引號**，與 Scopus 網頁「Search within: Affiliation」相同（操作步驟第 5 頁）。加引號會變成連續片語比對，篇數較少：清大經濟 2021–2025 加引號 34 篇、不加 40 篇（2026-10-30 實測）
   - SciVal Publication Metrics：Scopus ID 分批送出（每批大小可設定，預設 25，實作時依實測調整）
     - 所有請求都明確帶 `includedDocs=AllPublicationTypes`（與 SciVal 網頁的「All publication types」相同）
     - 篇數類指標與 FWCI：`yearRange=5yrs`、`byYear=true`
     - Q1：`PublicationsInTopJournalPercentiles`、`journalImpactType=CiteScore`、取 `threshold=25`（已確認 Q1 指 CiteScore 前 25%）
     - 被引數：`yearRange=5yrsAndCurrentAndFuture`，計算到目前為止（實測這樣才會和網頁一致）；自我引用的處理見第 7.3 節
5. **計算**（見第 7 節）
6. **產出**：網頁表格、pptx、xlsx

### 6.1 API 呼叫策略

- 並行呼叫，用速率限制器控制每秒請求數（可設定）
- 遇到 429、5xx 或逾時時，以指數退避重試，次數可設定；仍失敗就把該單位標為「待人工確認」，不影響其他單位
- 讀取回應標頭中的剩餘額度，顯示在畫面上
- **快取**：同一個查詢字串加上同一個期間，在快取有效期內（預設當天）不重新抓取，讓重試或中斷後繼續時不必從頭來；建立新分析時預設重新抓取，確保所有單位的資料都是同一時間取得的
- 回應中含有 API Key 的 `link` 欄位，存檔前一律移除

### 6.2 背景任務

- 第一版是單人使用，所以在同一個行程內用 asyncio 執行任務，不另外架設任務佇列
- 進度寫入資料庫，頁面以 HTMX 每秒輪詢更新
- 程式中途關閉時，未完成的分析標為「中斷」，可以從中斷處繼續（已抓到的部分走快取）

## 7. 指標與計算

### 7.1 指標定義表

每個指標用宣告式的方式定義在一處（`metrics/indicators.py`）：

| 欄位 | 說明 |
|---|---|
| `id` | 例如 `q1_per_faculty` |
| `label` | 簡報上顯示的名稱 |
| `source` | SciVal metricType 與篩選條件（例如 `PublicationsInTopJournalPercentiles`、`threshold=25`） |
| `aggregate` | `count`（篇數加總）、`mean`（逐篇平均）、`ratio`（被引數 ÷ 篇數） |
| `per_faculty` | 是否計算師均（A ÷ 5 ÷ 單年教師數） |
| `rankings` | 對應的排名或指標，例如 `QS`、`THE`、`高教深耕`，用來自動產生「※指標重要性」註記 |

第一版的指標（依範例報告）：

| 指標 | 計算 | 重要性註記 |
|---|---|---|
| 論文總篇數、師均 | Scopus 篇數；A/5/教師數 | QS、THE |
| Q1 篇數、師均 | CiteScore 前 25% 期刊的篇數；A/5/教師數 | 高教深耕 |
| Top 1% 篇數、師均 | 被引次數達全球前 1% 的篇數；A/5/教師數 | THE、高教深耕 |
| Top 10% 篇數、師均 | 被引次數達全球前 10% 的篇數；A/5/教師數 | THE |
| FWCI | 逐篇 FWCI 平均 | THE、高教深耕 |
| 平均被引次數（排除自我引用） | 被引數 ÷ 總篇數 | QS |
| 國際合著篇數、師均 | `Collaboration` 中 International 的篇數；A/5/教師數 | QS、THE、高教深耕 |

### 7.2 計算規則

- 數字一律以 `Decimal` 計算，最後四捨五入（ROUND_HALF_UP）到小數第二位（會議確認「ALL 小數點第二位」）。範例報告中有截斷、有四捨五入，也有三位小數，系統統一採用這條規則
- 計入所有文獻類型
- 排名：數值由高到低；同值同名次，下一名跳號（1, 2, 2, 4）
- 範例第 5 頁的表格依 Q1 單年師均排序，其他頁依該頁的指標排序

### 7.3 自我引用排除（可替換策略）

**人工作法（已確認）**：SciVal Benchmarking → 指標選 Cited › Citation Count → **取消勾選「Include self-citations」**，Include 選「All publication types」。平均被引次數 = 這個被引數 ÷ 總篇數。

**API 對應**：SciVal Publication Metrics 的 `metricTypes=CitationCount`、`includeSelfCitations=false`、`includedDocs=AllPublicationTypes`。Elsevier 的 API 文件說明這個旗標「只有部分指標會使用」；先前實測時，`true` 和 `false` 回傳的被引數都是 290，參數沒有產生差異。

**第一版作法**：先使用「SciVal 參數」策略，結果頁與報告註腳標示「排除自我引用：待驗證」。驗證不阻擋實作，由使用者之後進行。

**之後的驗證步驟**（用同一組論文，例如清大計財 67 篇）：

1. 在 SciVal 網頁上勾選與取消「Include self-citations」，記下兩個被引數
2. 呼叫 API，`includeSelfCitations` 分別設 `true`／`false`，並試 `byYear=false`
3. 比對結果：
   - 網頁兩個數字相同 → 這組論文本來就沒有自我引用，API 參數可以直接使用，改用其他系所再驗證一次
   - 網頁不同、但 API 相同 → 這支 API 不支援排除自我引用，改用備案

**備案**：Scopus Citation Overview API 提供排除自我引用的參數（`citation=exclude-self`）。這支 API 需要另外申請授權；它對自我引用的定義也要再和 SciVal 比對，數字可能不完全相同。

**驗證結果（2026-11-10）**：

- API：`scripts/verify_self_citation.py` 找出 7 篇確定被共同作者引用的論文，`includeSelfCitations` 為 `true`／`false` 時被引數完全相同 → SciVal Publication Metrics API 忽略這個參數
- 網頁：以這 7 篇建立 Publication Set，Citation Count 含自我引用 44（與 API 相同）、排除後 40。少掉的 4 次恰好是這 7 篇之間的互相引用，逐年也吻合（2021 14→13、2022 11→8、2023 14→14、2024 5→5）；由這 7 篇以外、共同作者發表的論文所做的 8 次引用沒有排除
- 結論：SciVal 對 Publication Set 的自我引用 = 組內論文彼此之間的引用，不是 Scopus「任一作者」的定義

**現行作法（策略 `scival_pubset`，預設）**：SciVal 取含自我引用的被引數，再減去單位論文（SciVal 有收錄者）之間的互相引用；參考文獻以 Abstract Retrieval `view=FULL` 逐篇取得、永久快取（`app/elsevier/inset_selfcite.py`）。

**設計**：策略 `SelfCitationStrategy` 只保留「組內互引」（預設）與「不排除」兩種，在設定頁切換；結果頁與報告註腳標示使用的策略。「SciVal 參數」（驗證無效）與「Scopus Citation Overview」（沒有授權）已移除，舊分析仍顯示原本的策略名稱，設定中存著舊策略時改用預設策略。

## 8. 異常處理

下列情況將該單位或該數值標為「**待人工確認**」，不寫成 0：

| 情況 | 處理 |
|---|---|
| Scopus 查到 0 篇 | 列為待確認，提示檢查 mapping 的英文名稱 |
| 和上一個版本相比，篇數差異超過門檻（預設 ±30%，可設定） | 列為待確認，顯示兩版數字 |
| 缺教師數 | 師均顯示「待確認」，其他指標照常計算 |
| API 重試後仍然失敗 | 列為待確認，可以單獨重新抓取 |
| 連不到 API（例如沒有連 VPN） | 首頁和建立分析頁直接顯示連線狀態與原因，不允許開始執行 |

結果頁集中列出所有待確認項目，承辦人可以逐一標記「已確認」（附備註）或重新抓取。還有未處理的待確認項目時，仍然可以產出報告，但報告上會加上標示。

## 9. 產出

### 9.1 網頁

結果頁依指標分頁籤顯示「對標學校比較」與「院內比較」兩張表，中大列以紅色標示；另有排名總表（同範例第 4 頁）、待確認清單、版本備註。

### 9.2 pptx（每個系所一份）

依範例報告的 10 頁結構產出：

| 頁 | 內容 |
|---|---|
| 1 | 封面：學院－系所、產出日期 |
| 2 | 目錄 |
| 3 | 學院國科會及產學計畫經費（第一版沒有經費資料時省略本頁） |
| 4 | 學院各系所論文指標排名（對標／院內） |
| 5 | 論文發表篇數師均（Q1、總計） |
| 6 | Top 1% 篇數師均 |
| 7 | Top 10% 篇數師均 |
| 8 | FWCI |
| 9 | 平均論文被引次數（排除自我引用） |
| 10 | 國際合著篇數師均 |

- 樣式來自 `templates/report_template.pptx`（從範例報告整理出版面），程式只負責填入表格與文字
- 中大列標紅；每頁附「※指標重要性」與資料來源註腳
- 只有表格與文字，沒有圖表（與範例相同）

### 9.3 xlsx（每次分析一份）

所有表格各一個工作表，另附：每個單位的查詢字串與篇數、逐篇原始指標、教師數與來源、待確認清單、版本備註。

## 10. 網頁畫面

| 頁面 | 內容 |
|---|---|
| 首頁 | 目前的 mapping 版本、教師數資料狀態、API 連線狀態與剩餘額度、最近幾次分析 |
| Mapping 管理 | 上傳、驗證結果、與上一版的差異、瀏覽單位清單 |
| 教師數 | 匯入教育部檔案、選學年度預覽、未對應清單（從該校單位清單挑選）、逐筆修改（含原因） |
| 新增分析 | 期間、學院或系所、比較範圍、學校勾選、教師數學年度、查詢預覽、執行 |
| 分析結果 | 執行進度、各指標表格、排名總表、待確認清單、備註、下載 pptx / xlsx |
| 版本紀錄 | 歷次分析列表、比較兩個版本 |
| 設定 | API Key、insttoken、速率與重試、異常門檻、SciVal 每批篇數、學校對照、教師數採用欄位、自我引用策略 |

介面原則：精簡、正式；以表格為主，沒有裝飾性元素；預設深灰配色加上一個主色；不載入大型 CSS 或 JS 框架。

## 11. 資料模型（SQLite）

| 資料表 | 主要欄位 |
|---|---|
| `mapping_versions` | id, filename, sha256, imported_at, note |
| `units` | id, mapping_version_id, school, college, dept_zh, dept_en, ncu_college, ncu_dept, remark |
| `mapping_issues` | mapping_version_id, sheet, row, level, message |
| `school_aliases` | sheet_name（台大）, moe_name（國立臺灣大學） |
| `teacher_imports` | id, source_filename, sha256, imported_at, years（檔案內的學年度） |
| `teacher_counts` | teacher_import_id, academic_year, moe_school, moe_unit_code, moe_unit_name, total, asst_prof_plus |
| `unit_teacher_links` | school, dept_zh, moe_unit_code（一個單位可對應多個代碼）, matched_by（auto／manual） |
| `teacher_overrides` | school, dept_zh, academic_year, count, reason, created_at |
| `runs` | id, created_at, period_from, period_to, scope_json, mapping_version_id, teacher_import_id, academic_year, teacher_column, self_citation_strategy, status, notes_json |
| `run_units` | run_id, unit_id, role（ncu／benchmark）, query, status, fetched_at, eid_count, teacher_count, teacher_source |
| `run_publications` | run_id, unit_id, scopus_id, eid |
| `api_cache` | key（查詢＋參數雜湊）, endpoint, fetched_at, response_json |
| `results` | run_id, unit_id, comparison（benchmark／college／custom）, indicator_id, value, rank |
| `anomalies` | run_id, unit_id, kind, detail, status, resolution_note |
| `exports` | run_id, kind, path, created_at |

產出檔放在 `data/exports/<run_id>/`；`data/` 整個目錄不進版控。

## 12. 專案結構

```
university-research-benchmarking/
├─ start.bat                 # 一鍵啟動
├─ pyproject.toml
├─ .env.example
├─ alembic/                  # 資料庫 migration
├─ app/
│  ├─ main.py                # FastAPI 進入點、啟動時開啟瀏覽器
│  ├─ config.py
│  ├─ db/
│  ├─ mapping/
│  ├─ teachers/
│  ├─ elsevier/
│  ├─ metrics/
│  ├─ runs/
│  ├─ reports/
│  ├─ funding/               # 預留
│  └─ web/
│     ├─ routes/
│     ├─ templates/
│     └─ static/             # htmx.min.js、app.css
├─ templates/
│  └─ report_template.pptx
├─ tests/
│  ├─ fixtures/              # 範例 mapping、錄下來的 API 回應（已移除 Key）
│  └─ ...
├─ api_test/                 # 現有的 API 測試程式，保留作為連線診斷工具
├─ data/                     # （gitignore）app.db、快取、產出檔
└─ docs/
```

## 13. 測試

| 層級 | 內容 |
|---|---|
| 單元 | mapping 解析與驗證（以 `docs/中大與他校mapping表.xlsx` 為測試資料）；教師數對應；指標計算、捨入、排名 |
| 對照 | 以 8 份範例報告的數字驗證計算公式；已知有 46 個值本身不符合公式，在測試中逐一標註，不當成失敗 |
| Client | 以 respx 播放錄下來的 Scopus／SciVal 回應，測試翻頁、分批、重試、快取 |
| 產出 | 產出 pptx 後重新讀取，檢查頁數、表格內容、中大列的顏色 |
| 真實 API | 設定環境變數 `RUN_LIVE_API_TESTS=1` 時才會執行，必須在校內網路下 |

## 14. 待確認事項（不影響架構）

已確認：Q1 指 CiteScore 期刊前 25%；排除自我引用的人工作法（第 7.3 節）；教育部檔案格式（第 5.5 節）。

仍待確認：

1. SciVal API 的 `includeSelfCitations=false` 能否得到與網頁相同的數字（依第 7.3 節的步驟驗證）
2. 範例報告中中大各系所的教師數（資管 23 等）從哪裡來（第 5.5 節）
3. 國科會經費 Excel 的欄位（之後另行設計）

## 15. 建議的開發順序

1. 專案骨架、`start.bat`、設定頁、API 連線檢查
2. Mapping 匯入與驗證
3. 教師數匯入與修改
4. Elsevier client（限速、重試、快取）與分析任務
5. 指標計算與結果頁
6. pptx / xlsx 產出
7. 版本紀錄與版本比較
