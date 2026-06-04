# SMDR2

Web 工具：上傳半導體封裝相關的 DXF 圖紙，框選樣板形狀建類別庫，自動
找出圖內所有同類別 instance，再把結果交給下游 Design Rule Check
（DRC）團隊做檢查。

```
[upload DXF]
  → preprocess (parse + flatten)
  → pick layers
  → ready_to_match
  → frame-select template → commit to library
  → match → Save Match (per file)
  → product-level Rule Check / DRC bundle export
```

## Quick start

```bash
uv sync                    # 安裝依賴（dev group 含 pytest / httpx / jsonschema）
uv run uvicorn app.main:app --reload
open http://localhost:8000
```

預設無 auth、預設綁 127.0.0.1，內網部署用。

## Repo 結構

```
app/                  FastAPI 後端 + frontend (static / templates)
  external_rule_check/  量測組（外部 DRC team）的 in-tree module — 目前是 _stub.py
  rule_check.py         Adapter：呼叫 external_rule_check.check_rules + 驗 envelope
  tools/                CLI 小工具（drc_dry_run 等）
  static/             dashboard.js / canvas.js / style.css / layer_modal.js
  templates/          dashboard.html / viewer.html
data/                 持久化資料（uploads / parsed / match / rule_check / library.sqlite）
openspec/
  specs/              各 capability 的正式 spec（machine-validated）
  changes/            進行中 / 已 archive 的變更提案
skill/                自訂 skill 定義（add-rule 等）
tests/                pytest 套件
```

## 可調整的「定值」一覽

下表列出**所有**散在 codebase 的可調常數、環境變數、persistence key。
任何 production 行為變動都該從這裡開始。

### 1) 比對（matching）—  `app/matching.py`

最常碰的一支。所有比對門檻都是 module-top 常數，改完重啟 server 即生效。

| 常數 | 預設 | 行：file | 作用 | 何時調 |
|---|---|---|---|---|
| `SCALE_MIN` / `SCALE_MAX` | `0.99` / `1.01` | 36–37 | 允許的縮放區間。樣板尺寸可以 ±1% 浮動 | 圖紙來源縮放不穩 → 放寬；要避免誤報 → 收緊 |
| `TOLERANCE_ABS` | `0.2` | 38 | Chamfer 距離容差，世界座標（mm）| 圖紙噪訊大 → 放寬；要更嚴格 → 收緊 |
| `VERTEX_COUNT_RATIO` | `0.3` | 39 | 候選頂點數差 ±30% 內才比 | 樣板頂點數變動大 → 放寬 |
| `PATH_LENGTH_RATIO` | `0.20` | 40 | 候選 path length 差 ±20% 內才比 | 縮放區間放寬時要同步放寬 |
| `RADIUS_RATIO` | `0.20` | 41 | 旋轉不變式：離 centroid 最大半徑 ±20% | rotation-invariant 預篩 gate |
| `SIGMA_RATIO_TOL` | `0.15` | 42 | 主軸 σ₂/σ₁ aspect 差容差 | 形狀寬高比預篩 gate |
| `CIRCLE_RADIUS_KEY_DIGITS` | `4` | 61 | 純 CIRCLE 比對的半徑 hash 精度 | 一般不動 |
| `RESAMPLE_N` | `64` | 69 | Polyline 沿弧長重採樣的點數 | 樣板太長 → 加；太細長碎邊 → 減 |
| `BRUTE_FORCE_CUTOFF` | `50` | 332 | 候選數小於此就 brute force（不建空間索引）| 一般不動 |
| `N_JOBS`（環境變數）| `1` | 75 | Worker process 數，讀 `SMDR2_N_JOBS` | 大圖時設為 CPU 核心數 |
| `_MIN_ITEMS_PER_WORKER` | `200` | 76 | 低於此 worker pool 太貴改 single-thread | 一般不動 |

#### 不同狀況怎麼調 matching 門檻

實務上「太多誤報」與「該抓到的沒抓到」要動的旋鈕不同。對照下表挑常數，
動完重啟 server。改一支看一輪結果再決定要不要再動下一支。

| 想達到的效果 | 優先收 / 放的常數 | 建議方向 |
|---|---|---|
| **更嚴格篩選整體 bbox / 形狀比例**（在意 entity 群的外觀） | `SIGMA_RATIO_TOL`、`RADIUS_RATIO` | `0.15 → 0.08`、`0.20 → 0.10` |
| **更嚴格篩選 entity 之間的相對關係**（同 cluster 內 entity 數量與相對位置） | `VERTEX_COUNT_RATIO`、`TOLERANCE_ABS` | `0.25 → 0.10`、`0.05 → 0.02` |
| **更嚴格篩選整體尺度**（不允許縮放浮動） | `SCALE_MIN/MAX`、`PATH_LENGTH_RATIO` | `0.95/1.05 → 0.98/1.02`、`0.20 → 0.10` |
| **誤報太多（找到不該找的）** | 先收 `SIGMA_RATIO_TOL` → 再收 `RADIUS_RATIO` → 最後 `TOLERANCE_ABS` | 每次只收一支，往 ~½ 的方向動 |
| **漏抓太多（該抓到的沒抓到）** | 先放 `TOLERANCE_ABS` → 再放 `VERTEX_COUNT_RATIO` → 最後 `SCALE_MIN/MAX` | 反向操作，每次放寬 ~50% |
| **圖紙來源縮放/噪訊不穩** | `SCALE_MIN/MAX`、`TOLERANCE_ABS`、`PATH_LENGTH_RATIO` 一起放寬 | 縮放窗放寬時 path length 必須同步放寬 |
| **樣板很細長 / 點數很多** | `RESAMPLE_N` ↑（如 `64 → 128`） | 重採樣不夠細會丟掉局部特徵 |
| **樣板很碎 / 點數很少** | `RESAMPLE_N` ↓（如 `64 → 32`） | 過度重採樣會放大噪訊 |

**調整原則**

1. `SIGMA_RATIO_TOL` 與 `RADIUS_RATIO` 是 rotation-invariant pre-filter gate，最先砍候選 — 想嚴格從這兩支動，CP 值最高。
2. `TOLERANCE_ABS` 是最後 chamfer 階段；收太緊會把有噪訊的真 match 誤殺，務必和 `BASE_TOLERANCE`（`dxf.py`，flatten 弦差）一起想 — flatten 噪訊不會比 `BASE_TOLERANCE` 還小。
3. `SCALE_MIN/MAX` 放寬時 `PATH_LENGTH_RATIO` 與 `RADIUS_RATIO` 要一起放，否則尺度窗開了但長度 gate 又把候選擋掉。
4. 一次只動一支常數、跑一輪 match 看 false-positive / false-negative，再決定下一步 — 同時動多支會無法歸因。

### 2) DXF 解析 / flatten — `app/dxf.py`

| 常數 | 預設 | 行 | 作用 |
|---|---|---|---|
| `BASE_TOLERANCE` | `0.01` | 42 | ezdxf flatten 曲線時的弦差 |
| `SCALE_FACTOR` | `1e-5` | 46 | 大半徑曲線的 base tolerance scale |
| `CIRCLE_MIN_VERTS` | `8` | 60 | 把多邊形「升等」為 CIRCLE primitive 的最小頂點數（含曲線轉出的）|
| `CIRCLE_MIN_VERTS_NOCURVE` | `11` | 61 | 同上，但純由直線 LINE 組成的多邊形要 11 個以上才算 |
| `CIRCLE_RADIAL_TOL` | `0.002` | 62 | 半徑相對誤差容差（判斷夠不夠圓）。與 client-side `measure_core.js::detectCircle` 保持一致 |
| `MAX_PRIMS_PER_THUMB` | `600` | 783 | layer 縮圖的 primitive 上限 |
| `MAX_VERTICES_PER_POLYLINE` | `24` | 784 | layer 縮圖中 polyline 的頂點上限 |

調整這些會影響「圓被識別為 circle 還是 polyline」、layer 縮圖細緻度。

### 3) DRC 介接 — `app/rule_check.py` + `app/external_rule_check/`

Rule 邏輯由**量測組（外部 DRC team）**負責，他們的 code 以 in-tree
Python module 形式 commit 在 `app/external_rule_check/`。SMDR2 端只負責：

1. 把要檢查的 product material 化成一個 handoff bundle 目錄
   （`manifest.json` + `dxfs/<file_id>.dxf` + `match/<file_id>.json`）
2. 呼叫量測組的 `check_rules(product_id, bundle_dir)`
3. 收 RuleChecking JSON、跑 envelope 驗證、寫到
   `data/rule_check/{product_id}.json`

整條 boundary 沒有可調的閥值 — 距離 / 數量門檻都在量測組的 module 內。
正式契約見 `openspec/specs/design-rule-checking/spec.md` 兩個 requirement：
**RuleChecking JSON output shape** 與 **External rule function contract**。

#### 整合量測組程式碼

**檔案擺哪**：`app/external_rule_check/` 是一個 subpackage，量測組可以
自由拆檔（`rules.py` / `geometry.py` / …）。SMDR2 端的 import 只看
`__init__.py` re-export 的 `check_rules`，內部結構量測組自決。

```
app/external_rule_check/
├── __init__.py     # re-exports check_rules（由量測組維護）
├── _stub.py        # 預設 placeholder：raise NotImplementedError
└── ...             # 量測組的 rules.py / geometry.py / 等等
```

**交付步驟**（量測組那邊 → 我們 merge 的當下）：

1. 量測組把檔案放進 `app/external_rule_check/`、改 `__init__.py` 的
   `from app.external_rule_check._stub import check_rules` 指向他們自己的
   entry point、刪 `_stub.py`。
2. 他們的 `requirements.txt`（如 `shapely`, `rtree` 等）逐項 merge 進
   `pyproject.toml`（不要直接套他們的鎖檔）。
3. 跑 adapter 測試確認 envelope 契約守得住：
   ```bash
   uv run pytest tests/test_rule_check.py tests/test_rule_check_job.py -x
   ```
4. 他們自己的 unit test 可以放 `tests/` 或 `app/external_rule_check/tests/`，
   `pytest` 一起跑。

**量測組怎麼自測（不用起 SMDR2 server）**：

用 `app/tools/drc_dry_run.py` CLI — 給一個 product_id，腳本直接 materialise
bundle、呼叫 `check_rules`、印結果：

```bash
uv run python -m app.tools.drc_dry_run <product_id>
uv run python -m app.tools.drc_dry_run <product_id> --keep-bundle /tmp/out
```

`--keep-bundle` 會把材料 dump 一份出來方便他們翻 `manifest.json` / 拆 DXF。

**Stub 在的時候會怎樣**：任何 rule-check job 跑起來都會以
`status: error` 收場，訊息含 `"external rule module not yet committed"`
— 這是設計的失敗訊號，不是 bug。Adapter 測試（20 條）即使在 stub
階段仍應全綠（它們都 monkeypatch `_external_check_rules`）。

#### 開發用 mock checker

`_stub.py` 在環境變數 `SMDR2_DEV_MOCK_DRC=1` 時會 dispatch 到
`app/external_rule_check/_dev_mock.py`，回傳一份 3 條規則的假資料、
故意涵蓋 viewer 全部三種顯示模式：

```bash
SMDR2_DEV_MOCK_DRC=1 uv run uvicorn app.main:app --reload
# 或
SMDR2_DEV_MOCK_DRC=1 uv run python -m app.tools.drc_dry_run <product_id>
```

| Mock 規則 | 顯示模式 | text 範例 |
|---|---|---|
| **MockDistance** | from + to（虛線 + 中點 label）| `[mock] substrate ↔ smd_2t = 12.34 mm (> 5.0)` |
| **MockHighlight** | from 單獨（高亮 + 旁邊 label）| `[mock] first substrate on this DXF` |
| **MockTolerance** | tol + tol_text（紅色標註）| `⚠ [mock] smd_2t ±0.5 mm` |

Handle 從 bundle 內各檔的 match JSON 第一個 match group 取，所以**只要
產品的 Save Match 跑過、bundle 拿得到 match**，mock 就有料可用。
所有 text 都帶 `[mock]` 標記，永遠不會跟正式結果混淆。預設不開（stub
仍然 fail loud）；正式部署不會用到。

### 4) DRC handoff bundle — `app/drc_bundle.py`

| 常數 | 預設 | 行 | 作用 |
|---|---|---|---|
| `BUNDLE_VERSION` | `"1.0.0"` | 35 | Manifest 的 semver；schema 改了要 bump MAJOR |
| `MANIFEST_FILENAME` | `"manifest.json"` | 36 | zip 根層的 manifest 檔名 |
| `DXF_DIR` | `"dxfs"` | 37 | zip 內 DXF 子目錄 |
| `MATCH_DIR` | `"match"` | 38 | zip 內 Match JSON 子目錄 |

Schema 在 `openspec/specs/design-rule-checking/drc-manifest.schema.json`。

### 5) 背景 job 佇列 — `app/jobs.py`

| 常數 | 預設 | 行 | 作用 |
|---|---|---|---|
| `MAX_WORKERS` | `2`（env `SMDR2_MAX_WORKERS`）| 58 | preprocess / save_match / rule_check 同時跑幾支 worker process，讀 `SMDR2_MAX_WORKERS` 環境變數 |

大量併發上傳時調高；單機 dev 用 1–2 就好。

> ⚠️ **Worker store-access 不變式**：所有背景 worker（`_preprocess_worker` /
> `_save_match_worker` / `_rule_check_worker` / `_discover_layers_worker`）讀
> library / template 狀態時**必須**用 `Store.load_library(...)` 重新讀，**不可**
> 讀 process-level 的 `LIBRARIES` 快取。`LIBRARIES` 只在 parent FastAPI process
> 內由 `add_template` 更新；worker pool 重用 process，第一個 job 之後快取就是過時
> 快照，會把新 commit 的 template **無聲地漏掉**。這條規則寫在 `app/jobs.py` module
> docstring，並有一條 AST regression test 守著（任何 worker 出現 `LIBRARIES.get` 就
> fail）。

### 5.5) 上傳限制 — `app/main.py`

| 常數 | 預設 | 行 | 作用 |
|---|---|---|---|
| `MAX_UPLOAD_BYTES` | `300 MB`（env `SMDR2_MAX_UPLOAD_MB`）| 86 | 單檔 DXF 上傳大小上限，超過 `upload_product_file` 回 HTTP 413。讀 `SMDR2_MAX_UPLOAD_MB`（MB 單位）|

防的是誤傳超大檔 / 壞檔凍結 server，不是擋惡意攻擊（內網工具）。

### 6) 預設類別 / 函式庫 — `app/library.py`

| 常數 | 行 | 作用 |
|---|---|---|
| `DEFAULT_CLASSES` | 32 | 新 library 自動 seed 的 17 個類別清單（Substrate / Pin-1 / Lid / LidOuter / LidInner / DieArea / FiducialCircle / FiducialCross / FiducialSquare / SMD-2T / C4Ball / BGABall / Protrusion / 2DBarcode / SMD-3T / SMD-8T / SMD-14T）|
| `DEPRECATED_CLASSES` | 53 | 已停用但歷史 DB 可能還有 — migration 會清除 |
| `CLASS_JSON_KEY` | 59 | 顯示名 → match JSON 內 snake_case key 對照 |
| `LEGACY_CLASS_RENAME` | 80 | 一次性 migration：舊 class 名 → 新 ID |
| `DEFAULT_LIBRARY_ID` / `DEFAULT_LIBRARY_NAME` | 92–93 | `"default"` / `"Default"` |

加新 class：把 PascalCase 加進 `DEFAULT_CLASSES`，配對 snake_case 加進
`CLASS_JSON_KEY`，重啟即可。

### 7) 檔案系統路徑 — `app/storage.py`

所有資料根目錄都從 `PROJECT_ROOT` 算出來；要換位置改這支：

| 變數 | 預設 | 用途 |
|---|---|---|
| `DATA_DIR` | `data/` | 全部資料根 |
| `UPLOADS_DIR` | `data/uploads/` | 上傳的 DXF 原檔 |
| `PARSED_DIR` | `data/parsed/` | flatten 完的 primitives JSON cache |
| `PREMATCH_DIR` | `data/prematch/` | 前置 hash / 索引快取 |
| `MATCH_DIR` | `data/match/` | 每檔 Save Match 後存的 Match JSON |
| `RULE_CHECK_DIR` | `data/rule_check/` | 每個 product 的 rule check 結果 |
| `LAYER_PREVIEW_DIR` | `data/layer_preview/` | layer 篩選用的 SVG 縮圖 |
| `DB_PATH` | `data/library.sqlite` | SQLite：library / templates / products / files |

整個 `data/` 目錄 portable — 備份/搬遷直接拷貝即可。

### 8) 檔案狀態列舉 — `app/files.py`

`status` 欄位可能值（行 27–33）：
`discovering_layers` / `awaiting_layers` / `preprocessing` /
`ready_to_match` / `checking_rules` / `report` / `error`。

要新增狀態：列舉這裡 + dashboard.js 的 `fileStatusBits()` 對應顏色 / 標籤。

### 9) Frontend 持久化 keys

`localStorage`（跨 session）：

| Key | 寫入處 | 作用 |
|---|---|---|
| `smdr2.dashboard.devMode` | `dashboard.js:31` | `"1"` 開、移除/`"0"` 關。Dev mode toggle 狀態 |

`sessionStorage`（單 session）：

| Key | 寫入處 | 作用 |
|---|---|---|
| `smdr2.dashboard.selectedLibrary` | `dashboard.js:93` | dashboard library 下拉的選項 |
| `smdr2.hiddenLayers.<file_id>` | `canvas.js:49` | viewer 隱藏的 layer 集合（per file）|
| `smdr2.viewer.ruleOpened` | `canvas.js:1422` | rule sidebar 中被使用者展開的 rule 名稱集合（預設全部摺疊；fail 排在 pass 前面）|

清除方法：DevTools → Application → Storage → 該域名。

### 10) 環境變數

| 變數 | 預設 | 用途 |
|---|---|---|
| `SMDR2_N_JOBS` | `1` | 比對引擎的 worker 數，見 `matching.py:75` |
| `SMDR2_DEV_MOCK_DRC` | unset | `"1"` → `app/external_rule_check/_stub.py` dispatch 到 `_dev_mock.py`，回傳 3 條 mock 規則（涵蓋 viewer 全部三種顯示模式）給開發 smoke 用；正式部署留空 |

其他 host / port 等請傳給 `uvicorn` CLI。

## 主要功能與位置

每個 capability 都有正式 spec，行為改變請以 spec 為準。

| 功能 | 後端模組 | 前端 | Spec |
|---|---|---|---|
| DXF 解析 / flatten | `app/dxf.py` | — | `openspec/specs/dxf-pipeline/spec.md` |
| Layer 篩選 | `app/dxf.py` + `main.py` | `static/layer_modal.js` | `openspec/specs/dxf-pipeline/spec.md` |
| Template library | `app/library.py` | `viewer.html` 框選介面 | `openspec/specs/template-library/spec.md` |
| Pattern matching | `app/matching.py` + `jobs.py` | `static/canvas.js` | `openspec/specs/pattern-matching/spec.md` |
| 同尺寸圓分流（BGABall ↔ FiducialCircle，依 view 約束）| `app/library.py`（`CLASS_VIEW_CONSTRAINTS`）+ `app/side_regions.py` | — | `openspec/specs/template-library/spec.md` |
| Product / 多 DXF per role | `app/products.py` + `files.py` | `static/dashboard.js` | `openspec/specs/product-files/spec.md` |
| Product view 覆蓋（role ↔ view 對應）| `app/product_views.py` | — | （併入 `openspec/specs/product-files/spec.md`）|
| Side regions (top/bottom/side view rect) | `app/side_regions.py` | viewer 框選 | `openspec/specs/viewer-ui/spec.md` |
| Viewer UI | — | `static/canvas.js` | `openspec/specs/viewer-ui/spec.md` |
| Dev 參數覆寫（matching 門檻即時調）| `app/dev_overrides.py` + `main.py` | dashboard dev panel | `openspec/specs/dev-parameter-overrides/spec.md` |
| Design Rule Check (adapter → 量測組 module) | `app/rule_check.py` + `app/external_rule_check/` | dashboard rule modal + viewer rule sidebar | `openspec/specs/design-rule-checking/spec.md` |
| DRC handoff bundle (handoff zip + 內部 bundle dir) | `app/drc_bundle.py` + `main.py` | dashboard dev-mode 按鈕 | 同上 + `INTEGRATION.md` |
| DRC dry-run CLI | `app/tools/drc_dry_run.py` | — | （給量測組自測，不入 spec） |
| Dashboard developer mode | — | `static/dashboard.js` | （`dashboard-ui` spec 尚未 archive 進 `openspec/specs/`；行為見 in-flight change）|

## HTTP API 速查

`app/main.py` 共約 45 條路由，下表只列日常會碰的重點；**完整清單以
`app/main.py` 的 `@app.*` decorator 為準**（含 layer 篩選、template CRUD、
side-regions、scan-all、prematch、dev 端點等）。

錯誤碼慣例：`404` 找不到資源、`400` 輸入錯誤 / 持久化檔損毀（帶檔案路徑
context）、`413` 上傳超過 `SMDR2_MAX_UPLOAD_MB`、`425` 檔案尚未 ready。

| Method · Path | 用途 |
|---|---|
| `GET /` | Dashboard |
| `GET /viewer/{file_id}` | DXF viewer |
| `GET /api/products` · `POST` · `GET /{id}` · `DELETE /{id}` | Product CRUD |
| `POST /api/products/{pid}/files` | 上傳 DXF 到 product 的某 role |
| `GET /api/files/{id}/primitives` | 取 flatten 過的繪圖 primitives |
| `POST /api/files/{id}/match` | 對單一 template 跑 match |
| `POST /api/files/{id}/match-json` | Save Match — 寫 `data/match/{id}.json`；回傳 payload 包含 `arbitration_counts`（class-arbitration 仲裁結果，BGABall vs FiducialCircle 等同尺寸衝突的分流統計）|
| `GET /api/files/{id}/match-json` | 讀回 Match JSON（dev mode "Download Match" 用這條）|
| `POST /api/products/{pid}/rule-check` | 跑 mock DRC |
| `GET /api/products/{pid}/rule-check` | 取最近一次 DRC 結果 |
| `GET /api/products/{pid}/drc-bundle` | 下載 DRC handoff zip（dev mode "Download All Match" 用這條）|

## 開發者模式

Dashboard 右上角 **Developer Mode** 按鈕（橘色 = ON），打開後：

- 每個 file row（match_saved=true）多一顆 **Download Match** — 拉該檔的
  Match JSON。
- 每個 product card 多一顆 **Download All Match** — 拉該 product 的
  DRC handoff bundle zip（內含 manifest + 全部 DXF + 全部 Match JSON），
  跟外部 DRC 團隊收到的完全一樣。

狀態持久在 `localStorage.smdr2.dashboard.devMode`，重新整理仍保留。

## 測試

```bash
uv run pytest -q                       # 全部
uv run pytest tests/test_rule_check.py # 單一檔
uv run pytest -k "drc_bundle"          # 關鍵字
```

UI 變動需要手動 smoke：開 dashboard、跑流程，目前沒有 browser
test harness。

## 偵錯：背景 job 與日誌

所有背景 job（discover / preprocess / save_match / rule_check）都走
`app/jobs.py` 的 ProcessPool，並透過 module logger（`logging.getLogger`）
發結構化日誌——**成功 INFO 里程碑、失敗 WARNING/ERROR 帶 exception 型別與
detail**。job 卡住或結果不對時，這是第一手線索。

| 階段 | 成功訊號（INFO）| 失敗訊號（WARNING/ERROR）|
|---|---|---|
| Preprocess | `preprocess_done file_id=… primitive_count=…` | `preprocess_failed` / `preprocess_callback_failed` |
| Save Match | `save_match_done file_id=…` | `save_match_failed` / `save_match_callback_failed` |
| Rule Check | `rule_check_done product_id=… pass_count=…` | `rule_check_failed` |

`*_callback_failed` 特別重要：它代表 worker 本身成功、但**收尾的
`FILE_STORE` 更新出錯**。這條 path 以前會被靜默吞掉（job 假裝 `done`）；
現在一律記 ERROR 並把 job 翻成 `error`，不會再無聲失敗。

除錯流程：

1. `GET /api/jobs/{job_id}` 看 `status`（queued / running / done / error）
   與 `error` 欄位。
2. 在 server stderr（uvicorn 預設輸出）grep `job_id=<id>` 找對應日誌行。
3. 持久化檔（`data/parsed|prematch|match|rule_check/*.json`）若損毀，讀取
   端點會回 **HTTP 400 並帶檔案路徑**（不是無資訊的 500）——直接照路徑去
   看那個檔。

> 不另外配置 logging handler（不呼叫 `basicConfig`），讓 uvicorn / CLI
> 決定輸出去向。callback 跑在 **parent process**，所以日誌不會隨 worker
> 退出而消失。

## 文件

- 架構與維護指南（新人先讀）：`ARCHITECTURE.md` — pipeline 資料流、worker
  併發模型、快取陷阱、怎麼加 job / route / capability、OpenSpec 工作流
- 正式契約：`openspec/specs/<capability>/spec.md`（每個 capability 一份）
- 變更提案歷史：`openspec/changes/`
- 外部 DRC 串接：`openspec/specs/design-rule-checking/INTEGRATION.md`
- 新增 DRC rule：`skill/add-rule/SKILL.md`
- Manifest schema：`openspec/specs/design-rule-checking/drc-manifest.schema.json`

驗證 spec：`openspec validate --specs`（CI / pre-merge gate）。
