# 尋形(Conform / SMDR2)架構與維護指南

給**第一次接手本專案的工程師**。讀完你應該能:看懂整條 pipeline 與
資料怎麼流、知道哪些地方碰了會出事(併發 / 快取陷阱)、以及照既有
慣例安全地加功能(job / route / capability)。

> README.md 是「**怎麼用、怎麼調**」(常數表、API 速查、偵錯);
> 這份是「**怎麼運作、怎麼改**」;根目錄 `SYSTEM_DESIGN.md` 是
> 「**為什麼這樣設計**」(完整設計書 + 全部視圖 + 部署/SLA)。
> 正式行為契約一律以 `openspec/specs/<capability>/spec.md` 為準。

---

## 1. 全貌：一句話

FastAPI 後端 + 原生 JS 前端的**內網**工具。工程師上傳半導體封裝 DXF →
框選樣板形狀建類別庫 → 自動比對找出圖內所有同類別 instance →
把結果交給下游 DRC(量測組)團隊檢查。低併發(≤10 人)、可信任使用者
——設計取捨以此為前提(不做 web-scale 防護)。

**Auth(2026-06-12 起)**:`SMDR2_AUTH_MODE=bypass`(預設,本機/測試 =
合成 admin,行為同從前的無 auth)/ `oidc`(prod:Keycloak BFF 登入 +
自建授權 admin/editor/viewer × global/customer/product + product 編輯鎖
+ audit)。每條 `/api` 路由都掛 guard,開機有 default-deny 斷言。

**版本模型(2026-06-10,openspec `add-product-versioning`)**:product 是
version 的容器;每個 version 1:1 擁有自己的 library(templates + 每類
match 調參),檔案以 content-hash 跨版共用、綁定走 `version_files`
junction。建新版 = clone 上一版(library + 綁定);衍生 artifact 一律以
`(version_id, file_id)` 為 key,舊版永久可回看;**畫押(sign-off)**把
version 凍結成唯讀(server 端守門;可選附一張證明圖片)。規則
(rule-check 契約)掛 product、跨版不變。

```
              ┌────────── web process(可 ×N,無狀態)──────────┐
   browser ──►│  routes (main.py) ── guards (身分/角色/鎖/簽核)   │
   (canvas.js │       │                                           │
   dashboard) │  jobs.submit_*  =  jobs 表 INSERT(payload 解析完) │
              └───────┬───────────────────────────────────────────┘
                      ▼
              DB jobs 表(SQLite dev / MariaDB prod)── 跨 process 的佇列
                      ▲ 兩步認領 / heartbeat 30s / stale 120s requeue
              ┌───────┴────────── worker loop ────────────────────┐
              │  embedded thread(dev/測試,預設)或獨立 pod(k8s)  │
              │  claim → ProcessPool 執行 _*_worker → apply_success │
              │                                    /apply_failure   │
              └───────┬────────────────────────────────────────────┘
                      ▼
              BlobStore(Local: data/ 檔案系統 │ S3: MinIO, boto3)
                      = stage 間的契約(key 同路徑佈局)
```

關鍵心智模型:**route 很薄,重活在 worker;web 與 worker 之間只透過
DB(jobs 表 + stores)和 BlobStore 溝通——任何進程內記憶體都不可信
(多 replica)。**

---

## 2. Pipeline 與檔案狀態

一個 file 的生命週期（`status` 欄位，定義在 `app/files.py:26-32`）：

```
upload
  │
  ├─(一般路徑)─► discovering_layers ─► awaiting_layers ─►┐
  │                (Phase 1: 列 layer + 縮圖)  (等使用者選 layer)
  │                     │ 幾何散在多個 paper-space tab     │
  │                     ▼                                  │
  │              awaiting_layout(等使用者選 tab,選定後重跑 Phase 1)
  │                                                        │
  └─(skip_layer_pick=true 略過 Phase 1)───────────────────►│
                                                          ▼
                                              preprocessing (Phase 2: flatten)
                                                          │
                                                          ▼
                                              ready_to_match ──► (使用者框選比對) ──► Save Match
                                                          │
                          version 層 rule-check ◄─────────┘
                            checking_rules ─► report
                                                          
   任一 worker 失敗 ─► error（error 欄位帶 message + traceback）
```

每個階段把產物寫到 **BlobStore**(`app/blobstore.py`)的不同 key,
**下一階段只認 blob、不認記憶體**(這是跨 process / 跨 pod 的唯一通道)。
key 由 `app/storage.py` 的 `*_key()` helpers 鑄造,本機(Local 後端)時
1:1 對應 `data/` 路徑;設 `S3_ENDPOINT_URL` 時同一個 key 進 MinIO:

| Blob key | 寫入者 | 內容(dict 形狀)|
|---|---|---|
| `uploads/{id}.dxf` | upload handler | 原始 DXF(byte-for-byte,content-hash 跨版本共用)|
| `parsed/{vid}/{id}.json` | `_preprocess_worker` | `{"primitives":[...], "bbox":[x0,y0,x1,y1], "background":"#…", "insunits":int|null, "applied_scale":float, "dxf_recover_notes":{…}|null}` |
| `prematch/{vid}/{id}.json` | `_preprocess_worker` | `{"by_class":{class:[handles]}, "total":int}`(class-toolbar 計數的前置快取)|
| `match/{vid}/{id}.json` | `_save_match_worker` | `{"<view>.<class_snake>.<idx>": [[handle,…], …]}`(交給 DRC 的契約,見 INTEGRATION.md)|
| `rule_check/{vid}.json` | `_rule_check_worker` | `{"<ruleName>": {"pass":bool, "text":str, "rules":[…]}}` |
| `layer_preview/{vid}/{id}/…` | `_discover_layers_worker` | `layers.json` + per-layer `.svg` 縮圖(+ `layouts/` 子目錄)|
| `sign_off_evidence/{vid}` | sign-off 端點 | 畫押證明圖片(選填;MIME 在 versions 表)|
| `library.sqlite` / MariaDB | stores(經 `app/db.py`)| product / version / library / template / file / auth / jobs 的持久化 |

> 這些 dict 形狀目前是**隱性契約**(靠註解 + 測試,沒有 dataclass/schema
> 強制)。動它們之前先看對應的 `openspec/specs/`。
> ⚠️ **禁用 S3 list API**(公司規定):BlobStore 介面沒有 list 操作,
> 刪除一律由 DB bindings + manifest 列舉精確 key(`_version_artifact_keys`)。

**讀這些檔的端點都過 `_load_json_or_http()`（`main.py`）**：檔案損毀時回
**HTTP 400 + 檔案路徑**，不是無資訊的 500。加新的讀取端點請沿用它。

---

## 3. 背景 job 模型（最容易踩雷的地方）

Job 佇列是 **DB 表**(`app/jobstore.py`,schema 見
`docs/schema-auth-jobs.md` §7),不是進程內狀態——這是多 replica 的
前提。執行端是 `app/worker_loop.py` 的 `WorkerLoop`:dev/測試時是 web
進程裡的 daemon thread(`SMDR2_EMBEDDED_WORKER` 預設 1),k8s 上是獨立
worker pod(web 設 0,只 enqueue)。job 種類:`discover` / `preprocess` /
`save_match` / `rule_check` / `reprocess-all`(父子)。

生命週期(`queued → running → done|error`,協定常數見 SYSTEM_DESIGN §7.1):

1. **submit**(web,`app/jobs.py` 的 `submit_*`):把 worker 需要的**全部
   參數解析成 payload** 寫進 INSERT——執行端可能是另一個 pod,不能依賴
   本地狀態;同 (kind, version, file) 已有 inflight 列就直接回它(去重)。
2. **claim**(worker loop):兩步樂觀認領——SELECT 候選 →
   `UPDATE … WHERE status='queued'`,rowcount 判勝負;多 worker 同搶
   恰一個贏。認領後丟 ProcessPool 執行。
3. **執行**(`_*_worker`,子 process):必須可 pickle、import 放函式內;
   I/O 一律走 `get_blobstore()`(每個子 process 自行從 env 解析)。
4. **收尾**(worker loop 收割 future):成功走 `jobs.apply_success(job,
   result)`(FILE_STORE 狀態翻轉、父 job bump、ERR-005 log),失敗走
   `jobs.apply_failure` + `store.fail`。**apply_success 內部任何例外都
   會把 job 翻成 error**(`<kind>_callback_failed`,ERR-009)——不允許
   靜默吞掉。
5. **自癒**:running 列的 heartbeat 靜默 120s = 認領者死亡 → attempts<3
   就 requeue 給別的 worker;耗盡走 apply_failure(不留殭屍)。

**加一個 job 種類**:worker 函式(picklable)→ `submit_*`(payload 解析
完 + `JOB_STORE.insert` + `ensure_embedded()`)→ `execution_plan()` 加
dispatch 分支 → `apply_success` / `apply_failure` 加收尾分支 → 測試直接
`JOB_STORE.insert` + `WorkerLoop().run_once()` 驅動。

---

## 4. 兩個必知的併發 / 快取陷阱

### 4.1 Worker 必須用 `Store.load_library`，絕不用 `LIBRARIES` 快取

`LIBRARIES`(`app/library.py`)的 registry 每次 `get()` 都從 DB 重建
(跨 pod 正確性),但 worker 子進程裡仍一律用 `Store.load_library(...)`
重新讀——worker pool **重用 process**,任何 module 級狀態在第一個 job
之後就是過時快照,會把新 commit 的 template **無聲地漏掉**(match JSON
少東西,超難 debug)。

```python
# ✅ 對：worker 內每次重新讀
store = Store(DB_PATH); lib = store.load_library(library_id)
# ❌ 錯：worker 內讀 process 級快取（過時）
from app.library import LIBRARIES; lib = LIBRARIES.get(library_id)
```

這條規則寫在 `app/jobs.py` module docstring,並有一條 **AST regression
test** 守著:任何 worker 出現 `LIBRARIES.get` 就 fail。同理:MinIO
client / DB 連線**不 fork-safe**,worker 內一律 lazy 建立
(`get_blobstore()` / `Store(DB_PATH)`),絕不從 module-level 繼承。

### 4.2 Store 的副作用只在 worker loop 執行緒做

FILE_STORE / 父 job 的狀態翻轉全部集中在 `apply_success` /
`apply_failure`,由 worker loop 的單一執行緒呼叫——不在子 process(改
不到別人的記憶體)、不在 route handler(多 replica 下 route 看不到別台
的 future)。store 方法各自持 RLock,跨 store 的順序由 apply_* 統一定義。

---

## 5. 同尺寸圓的分流：view 約束

class-agnostic 的 matcher 會讓共用同尺寸圓的兩個 class（典型
`BGABall` vs `FiducialCircle`）在同一批 handle 都中。靠**互斥的 view 約束**
分流，而非密度啟發式：`CLASS_VIEW_CONSTRAINTS`（`app/library.py`）規定
`BGABall` 只能落 bottom_view、`FiducialCircle` / `C4Ball` 只能落 top_view；
`is_allowed_view()` 判定 (class, view) 是否合法，`split_matches_by_side`
（`app/side_regions.py`）依工程師畫的 side region 把每個 instance 指派到唯一
一個 view，落在不允許 view 的 instance 直接 drop。確定性、免參數、operator
看得懂。完整 view 約束定義見 `openspec/specs/template-library/spec.md`。

> 舊版以「鄰居密度」分流的 `app/class_arbitration.py` 子系統已移除——view
> 約束上線後它退化成 no-op dead code，於 `remove-density-arbitration-subsystem`
> change 無行為變更下刪除。

---

## 6. 怎麼安全地改東西

### 加一條 route
1. 在 `app/main.py` 寫 handler,沿用既有錯誤碼慣例:`404` 找不到、`400`
   輸入錯 / 檔損毀、`401/403/423/409` 守門鏈(SYSTEM_DESIGN §4)、
   `413` 上傳過大、`425` 檔案未 ready。
2. **`/api` 路由必掛 guard**(`viewer_guard` / `editor_guard` /
   `admin_guard`,`app/guards.py`)——漏掛會直接 boot failure
   (default-deny 斷言),這是故意的。
3. 用 `_resolve_file(file_id)` 取 file(內含 not-found / not-ready 檢查)。
4. 讀持久化 JSON 一律走 `_load_json_or_http(key, kind=...)`。
5. 重活(>100ms)走 §3 的 job 佇列,不要卡 async event loop;純讀也建議
   用 sync def 讓 FastAPI 丟 thread pool。
6. 新頁面的 template 記得**先載 `csrf.js`**(oidc 模式下所有寫入要帶
   CSRF header,csrf.js 包 fetch 自動處理)。

### 加一個 background job 種類
照 §3 的三段式 pattern。重點：worker 可 pickle、callback 必掛且必包
try/except、DB 變更只在 callback、worker 讀 library 用 `Store.load_library`。

### 加一個 class
README §6：PascalCase 加進 `DEFAULT_CLASSES`、snake_case 配進
`CLASS_JSON_KEY`，重啟即可。

### 加一條 DRC rule
那是量測組的 in-tree module（`app/external_rule_check/`），見
`skill/add-rule/SKILL.md` 與 `openspec/specs/design-rule-checking/INTEGRATION.md`。

### 加一個對外 HTTPS 連線（或改 TLS 驗證）
任何對外的 httpx / boto3 client，`verify=` 一律帶 `app.tlsconfig.ssl_verify()`
——**不要自己讀 `SSL_VERIFY` 或自己判斷**。內網全是自簽 CA,這個開關是唯一
的政策來源(設計理由見 SYSTEM_DESIGN §7.9,變數見 §13.1)。新增環境變數則
**先補進 SYSTEM_DESIGN §13.1 那張總表**(唯一完整來源),再視需要在 README §10
(開發子集)或 `deploy/PRODUCTION_DEPLOY.md`(上線操作)補一行。

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
uv run uvicorn app.main:app --reload          # http://localhost:8000(bypass auth、SQLite、Local blob)
SMDR2_DEV_MOCK_DRC=1 uv run uvicorn app.main:app --reload   # 帶 mock DRC
uv run pytest -q                              # 全部測試(零外部依賴)
uv run python -m app.tools.drc_dry_run <pid>  # 不起 server 跑 DRC(量測組自測)

docker compose up --build                     # prod 縮小鏡像:LB+web×2+worker
                                              # +MariaDB+MinIO+Keycloak(oidc 模式)
                                              # 入口 http://localhost:8080,見 deploy/README.md
```

job 卡住 / 結果不對 → 看 README「偵錯:背景 job 與日誌」一節(grep server
stderr 的 `job_id=…`,或 `GET /api/jobs/{id}`——任一 replica 可答)。
前端目前**沒有**自動化測試,UI 變動要手動 smoke。

---

## 9. 延伸閱讀

- `SYSTEM_DESIGN.md` — 完整設計書:需求/容量/API 全表/資料模型/部署/
  SLA/技術債/全部視圖(C4、DFD、UML)
- `README.md` — 可調常數、env、API 速查、偵錯
- `deploy/README.md` — compose 開發環境、k8s manifest、CI/CD pipeline
- `docs/schema-auth-jobs.md` — auth/jobs/lock 的逐欄 schema 與協定
- `openspec/specs/<capability>/spec.md` — 正式行為契約(8 個 capability)
- `openspec/specs/design-rule-checking/INTEGRATION.md` — 給量測組的串接指南
- `openspec/changes/` — 變更提案歷史(每次行為變更的 why + delta)
- 決策史:`docs/DISCUSSION.md`、`docs/auth-permissions.md`、
  `docs/product-versioning.md`(point-in-time 紀錄,結論以 SYSTEM_DESIGN 為準)
