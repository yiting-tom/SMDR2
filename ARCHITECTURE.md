# SMDR2 架構與維護指南

給**第一次接手 SMDR2 的工程師**。讀完你應該能：看懂整條 pipeline 與
資料怎麼流、知道哪些地方碰了會出事（併發 / 快取陷阱）、以及照既有
慣例安全地加功能（job / route / capability）。

> README.md 是「**怎麼用、怎麼調**」（常數表、API 速查、偵錯）；
> 這份是「**怎麼運作、怎麼改**」。正式行為契約一律以
> `openspec/specs/<capability>/spec.md` 為準。

---

## 1. 全貌：一句話

FastAPI 後端 + 原生 JS 前端的**內網**工具。工程師上傳半導體封裝 DXF →
框選樣板形狀建類別庫 → 自動比對找出圖內所有同類別 instance →
把結果交給下游 DRC（量測組）團隊檢查。**無 auth、預設綁 127.0.0.1、
低併發、可信任使用者**——所有設計取捨都以此為前提（不做 web-scale 防護）。

```
              ┌─────────────── parent FastAPI process ───────────────┐
   browser ──►│  routes (main.py)   singletons: FILE_STORE / LIBRARIES │
   (canvas.js │       │              / PRODUCT_STORE  (in-memory + SQLite)│
   dashboard) │       ▼                                                 │
              │  jobs.submit_*  ──►  ProcessPoolExecutor (MAX_WORKERS)   │
              │       ▲                    │ pickle                      │
              │       │ done-callback      ▼                             │
              │  (parent thread)     worker subprocess (_*_worker)       │
              │                       讀 data/ + Store.load_library      │
              └───────────────────────────┬─────────────────────────────┘
                                           ▼
                            data/  (檔案系統 = stage 間的契約)
```

關鍵心智模型：**route 很薄，重活在 worker；worker 跑在獨立 process，
只能透過 `data/` 檔案 + 重新開 `Store` 跟 parent 溝通。**

---

## 2. Pipeline 與檔案狀態

一個 file 的生命週期（`status` 欄位，定義在 `app/files.py:26-32`）：

```
upload
  │
  ├─(一般路徑)─► discovering_layers ─► awaiting_layers ─►┐
  │                (Phase 1: 列 layer + 縮圖)  (等使用者選 layer)
  │                                                       │
  └─(skip_layer_pick=true 略過 Phase 1)──────────────────►│
                                                          ▼
                                              preprocessing (Phase 2: flatten)
                                                          │
                                                          ▼
                                              ready_to_match ──► (使用者框選比對) ──► Save Match
                                                          │
                          product 層 rule-check ◄─────────┘
                            checking_rules ─► report
                                                          
   任一 worker 失敗 ─► error（error 欄位帶 message + traceback）
```

每個階段把產物寫到 `data/` 的不同目錄，**下一階段只認檔案、不認記憶體**
（這是 worker 跨 process 的唯一通道）。路徑全在 `app/storage.py`：

| 目錄 | 寫入者 | 內容（dict 形狀）|
|---|---|---|
| `data/uploads/{id}.dxf` | upload handler | 原始 DXF（byte-for-byte）|
| `data/parsed/{id}.json` | `_preprocess_worker` | `{"primitives":[...], "bbox":[x0,y0,x1,y1], "background":"#…", "insunits":int|null, "applied_scale":float, "dxf_recover_notes":{…}|null}` |
| `data/prematch/{id}.json` | `_preprocess_worker` | `{"by_class":{class:[handles]}, "total":int}`（class-toolbar 計數的前置快取）|
| `data/match/{id}.json` | `_save_match_worker` | `{"<view>.<class_snake>.<idx>": [[handle,…], …]}`（交給 DRC 的契約，見 INTEGRATION.md）|
| `data/rule_check/{pid}.json` | `_rule_check_worker` | `{"<ruleName>": {"pass":bool, "text":str, "rules":[…]}}` |
| `data/layer_preview/{id}/` | `_discover_layers_worker` | `layers.json` + per-layer `.svg` 縮圖 |
| `data/library.sqlite` | parent（`Store`）| library / template / product / file 的持久化 |

> 這些 dict 形狀目前是**隱性契約**（靠註解 + 測試，沒有 dataclass/schema
> 強制）。動它們之前先看對應的 `openspec/specs/`。整個 `data/` 可攜——
> 備份/搬遷直接 copy。

**讀這些檔的端點都過 `_load_json_or_http()`（`main.py`）**：檔案損毀時回
**HTTP 400 + 檔案路徑**，不是無資訊的 500。加新的讀取端點請沿用它。

---

## 3. 背景 job 模型（最容易踩雷的地方）

`app/jobs.py` 用一個 module 級 `ProcessPoolExecutor`（`MAX_WORKERS`，
env `SMDR2_MAX_WORKERS`）跑所有長任務。job 狀態存在 module 級
`_jobs: dict`（`_lock: RLock` 保護）。job 種類：`preprocess`、
`discover_layers`、`unit_override_preprocess`、`save_match`、`rule_check`。

每個 job 都是**三段式**，請照抄這個 pattern：

```python
# (1) Worker — 跑在子 process，必須可 pickle（不能有 closure / lambda 捕捉狀態）
def _my_worker(file_id: str, ...) -> dict:
    # import 放函式內，spawned worker 才會乾淨 re-import
    from app.library import Store
    from app.storage import DB_PATH
    store = Store(DB_PATH)
    lib = store.load_library(library_id, product_id=...)   # ← 見 §4，務必這樣讀
    ...
    return {"result": ...}          # 回傳值會被 pickle 回 parent

# (2) Submit — 跑在 parent thread，註冊 job + 掛 callback
def submit_my_job(file_id: str) -> str:
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = {"id": job_id, "status": "queued", ...}
    fut = _get_executor().submit(_my_worker, file_id, ...)
    fut.add_done_callback(lambda f: _on_my_job_done(job_id, f))   # ← 一定要掛
    return job_id

# (3) Done-callback — 跑在 parent thread，read result + 改 FILE_STORE + 翻 status
def _on_my_job_done(job_id: str, fut: Future) -> None:
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        return
    try:
        result = fut.result()        # worker 拋的例外在這裡 re-raise
    except Exception as e:
        logger.warning("my_job_failed job_id=%s %s: %s", job_id, type(e).__name__, e)
        with _lock:
            job["status"] = "error"; job["error"] = f"{e}\n{traceback.format_exc()}"
        return
    # ⚠️ post-result work（FILE_STORE 更新等）也要包在 try 裡——見下方鐵則
    try:
        FILE_STORE.update_...(...)
    except Exception as e:
        logger.error("my_job_callback_failed job_id=%s", job_id, exc_info=True)
        with _lock:
            job["status"] = "error"; job["error"] = str(e)
        return
    with _lock:
        job["status"] = "done"; job["result"] = result
    logger.info("my_job_done job_id=%s file_id=%s", job_id, file_id)
```

**callback 鐵則（observability-launch-hardening 立的）：**

- callback **一定要掛**，否則 worker 例外無人接、job 永遠卡 `running`。
- callback 的 **post-result work 也要 try/except**。曾有 bug：status 先翻
  `done`、後面 `FILE_STORE` 更新才拋例外 → 例外被吞、job 假裝成功。現在
  一律記 ERROR + 翻 `error`，**不允許任何 callback 例外被靜默吞掉**。
- 所有 DB / `FILE_STORE` 變更都在 **callback（parent thread）**做，不在
  worker——worker 是獨立 process，改不到 parent 的記憶體狀態。

---

## 4. 兩個必知的併發 / 快取陷阱

### 4.1 Worker 必須用 `Store.load_library`，絕不用 `LIBRARIES` 快取

`LIBRARIES`（`app/library.py`）是 **parent process 的記憶體快取**，只在
parent 內由 `add_template` 等更新。worker 是獨立 process，看不到這些更新；
而且 worker pool **重用 process**——第一個 job 之後快取就是過時快照，會把
新 commit 的 template **無聲地漏掉**（match JSON 少東西，超難 debug）。

```python
# ✅ 對：worker 內每次重新讀
store = Store(DB_PATH); lib = store.load_library(library_id, product_id=...)
# ❌ 錯：worker 內讀 process 級快取（過時）
from app.library import LIBRARIES; lib = LIBRARIES.get(library_id)
```

這條規則寫在 `app/jobs.py` module docstring，並有一條 **AST regression test**
守著：任何 worker 出現 `LIBRARIES.get` 就 fail。parent thread 的 route
handler 讀 `LIBRARIES` 是 OK 的（它就是那份快取的擁有者）。

### 4.2 Callback 跑在 parent，`_jobs` 用 `_lock` 保護

done-callback 在 parent 的 thread pool 跑，不是 worker process。改 `_jobs`
一律 `with _lock`。（已知殘留：`_on_preprocess_done` 對 `FILE_STORE` 的
更新目前在 `_lock` 外——同檔重複 preprocess 的競態尚未完全消除，列在
post-launch backlog。現有改動只保證 callback **fail-safe**，不保證
**race-free**。）

---

## 5. Class arbitration：兩個 entry point

class-agnostic 的 matcher 會讓共用同尺寸圓的兩個 class（典型
`BGABall` vs `FiducialCircle`）在同一批 handle 都中。`app/class_arbitration.py`
靠「鄰居密度」把每個 instance 分流到唯一一個 class。

**有兩個 stage-specific entry point，差別只在 view 約束強制與否**：

| 函式 | 用在 | view 約束 |
|---|---|---|
| `arbitrate_for_prematch(out, shapes, groups)` | preprocess（side region 還沒畫）| **不**強制（每個 instance `view_prefix=None`，強制的話會把所有受限 class 全丟掉）|
| `arbitrate_for_match(out, shapes, groups)` | save-match / scan-all（side region 已畫）| **強制**：reassign 後 class 不允許該 view 的 instance 會被 drop 進 `dropped_by_view` |

底層 `arbitrate(..., enforce_view_constraints=...)` 仍在（給單元測試用顯式
模式）；**production code 一律用上面兩個 wrapper，不要直接傳 flag**。完整
演算法與 group 定義見 `openspec/specs/class-arbitration/spec.md`。

`POST /api/files/{id}/match-json` 回應的 `arbitration_counts` 就是這層的
分流統計。

---

## 6. 怎麼安全地改東西

### 加一條 route
1. 在 `app/main.py` 寫 handler，沿用既有錯誤碼慣例：`404` 找不到、`400`
   輸入錯 / 檔損毀、`413` 上傳過大、`425` 檔案未 ready。
2. 用 `_resolve_file(file_id)` 取 file（內含 not-found / not-ready 檢查）。
3. 讀持久化 JSON 一律走 `_load_json_or_http(path, kind=...)`。
4. 重活（>100ms）丟 worker，不要卡 async event loop；純讀也建議用 sync def
   讓 FastAPI 丟 thread pool。

### 加一個 background job 種類
照 §3 的三段式 pattern。重點：worker 可 pickle、callback 必掛且必包
try/except、DB 變更只在 callback、worker 讀 library 用 `Store.load_library`。

### 加一個 class
README §6：PascalCase 加進 `DEFAULT_CLASSES`、snake_case 配進
`CLASS_JSON_KEY`，重啟即可。

### 加一條 DRC rule
那是量測組的 in-tree module（`app/external_rule_check/`），見
`skill/add-rule/SKILL.md` 與 `openspec/specs/design-rule-checking/INTEGRATION.md`。

---

## 7. OpenSpec 工作流（改「行為」一定要走）

`openspec/specs/<capability>/spec.md` 是**機器驗證的行為契約**。改任何
spec 涵蓋的行為，走 propose → apply → archive：

1. **Propose** — `/opsx:propose`（或 openspec-propose skill）：產出
   `openspec/changes/<id>/` 下的 `proposal.md` / `design.md` /
   `specs/<cap>/spec.md`（delta）/ `tasks.md`。
2. **Apply** — `/opsx:apply`：照 `tasks.md` 實作，邊做邊勾。
3. **Archive** — `/opsx:archive`：把 spec delta 併進 live spec、change
   移到 `openspec/changes/archive/`。

`openspec validate --specs` 是 CI / pre-merge gate。純 bugfix / 重構 /
補測試（不改契約行為）可以跳過這套，直接開 branch 做。

---

## 8. 本機跑與測試

```bash
uv sync
uv run uvicorn app.main:app --reload          # http://localhost:8000
SMDR2_DEV_MOCK_DRC=1 uv run uvicorn app.main:app --reload   # 帶 mock DRC
uv run pytest -q                              # 全部測試
uv run python -m app.tools.drc_dry_run <pid>  # 不起 server 跑 DRC（量測組自測）
```

job 卡住 / 結果不對 → 看 README「偵錯：背景 job 與日誌」一節（grep server
stderr 的 `job_id=…`）。前端目前**沒有**自動化測試，UI 變動要手動 smoke。

---

## 9. 延伸閱讀

- `README.md` — 可調常數、env、API 速查、偵錯
- `openspec/specs/<capability>/spec.md` — 正式行為契約（8 個 capability）
- `openspec/specs/design-rule-checking/INTEGRATION.md` — 給量測組的串接指南
- `openspec/changes/` — 變更提案歷史（每次行為變更的 why + delta）
