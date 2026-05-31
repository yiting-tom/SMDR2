# Production 儲存與資料庫遷移規劃（MinIO / DB）

> 狀態：**討論中、尚未實作**。本文是決策文件（design / ADR），不是已定案的施工單。
> 最後更新：2026-05-31。
> 待 infra 回覆兩個問題後（見 §9），才會收斂成 OpenSpec change（propose-first）。

本文回答一個問題：**SMDR2 上 production（TKS / K8s）時，blob 與關聯資料要怎麼放，才能維護負擔最低、技術棧最小。**

---

## 0. TL;DR

- **MinIO 是物件儲存，不是資料庫。** 它很適合放 `data/` 底下的 blob，但**絕對不能放 `library.sqlite`**（POSIX locking + 無 partial-write → 壞檔）。所以「MinIO 當 database」不存在；關聯層是**另一個獨立決策**。
- **部署在 TKS（K8s），但「先不會多 replica」** → 單 replica 下 SQLite 功能上完全沒問題，記憶體裡的 `_jobs` 也 OK。
- **公司 DB 很可能是 Oracle** → 對這個 codebase 來說，Oracle 是**最重的遷移目標**（每條 SQL 的 `?` 綁定都要改、`INSERT OR REPLACE/IGNORE` 要改 `MERGE`、大 JSON 欄位要 CLOB、交易要手動 commit）。跟「低維護」目標衝突最大。
- **結論（推薦 Plan A）**：**blob → MinIO**、**SQLite 保留**、用已經有的 MinIO 跑 **Litestream** 做連續備份 / PITR。**完全不 port DB**。等真的需要多 replica（目前「先不會」）再走 Plan B。

| | blob | 關聯 | 一次性工 | 維護/棧 | 限制 |
|---|---|---|---|---|---|
| **A ★推薦** | MinIO | **SQLite 不動** + Litestream→MinIO | 小（只有 BlobStore + Litestream） | 最小 | 單 replica；備份是幾秒非同步窗口 |
| **B 次選** | MinIO | **PostgreSQL**（要爭取得到） | 中 | 小，state 歸 infra | 需公司提供 Postgres |
| **C 最後手段** | MinIO | **Oracle** | **最大** | 跟目標衝突 | 政策強制才做 |

三個方案的 **blob→MinIO** 與 **Phase 0 清漏**（§6）是共用的；差別只在關聯層。

---

## 1. 背景與限制（這次遷移的前提）

| 限制 | 來源 | 影響 |
|---|---|---|
| 上 **TKS（K8s 類平台）** | 使用者 | pod 檔案系統是 ephemeral；持久資料必須外部化或掛 PVC |
| 公司**已有 managed DB + MinIO** | 使用者 | 不用自架；DB/MinIO 的 ops 歸 infra 團隊 |
| **目標：維護難度最低、技術棧不要太大** | 使用者 | 不加 ORM / message queue / Redis 等重框架，只加薄 driver |
| **先不會多 replica** | 使用者 | 單 replica → SQLite 與記憶體 `_jobs` 都可留 |
| 公司 DB **可能是 Oracle**（會再確認有無 Postgres） | 使用者 | Oracle port 成本高，能避則避 |
| 內部工具、低併發、信任使用者、無 auth | 專案既有定位 | 垂直擴展（更大機器 + 更多 worker）多半就夠 |

---

## 2. 現況（grounded，實際讀 code 得到）

### 2.1 兩個可清楚切開的關注點

**A. Blob（檔案系統，全走 `app/storage.py` 的 path helper）**

```
data/
  uploads/{file_id}.dxf          原始 DXF
  parsed/{file_id}.json          flatten primitives + bbox + background
  prematch/{file_id}.json        跨 class 仲裁後的 prematch count
  match/{file_id}.json           per-file Match JSON
  rule_check/{file_id}.json      product-scoped DRC 結果
  layer_preview/{file_id}/       layers.json + <safe>.svg + primitives.json(transient)
```

實際量級（2026-05-31，參考用）：`data/` 共 ~62M，其中 `uploads` 34M、`layer_preview` 11M、`parsed` 7.8M、`library.sqlite` 3.1M。**blob 量很小**，所以「parent 下載 input → 丟給 worker → parent 上傳 output」這種代理 I/O 成本可忽略。

**B. 關聯（一個 `library.sqlite`，被 3 個 module-level singleton 各自獨立開啟）**

| singleton | 模組 | 負責的表 |
|---|---|---|
| `FILE_STORE` | `app/files.py` `FileStore` | `files`（DXF metadata / 生命週期） |
| `_STORE` / `LIBRARIES` | `app/library.py` `Store` / `LibraryRegistry` | `libraries` / `classes` / `templates` |
| `PRODUCT_STORE` | `app/products.py` `ProductStore` | `products` |

三個各自 `sqlite3.connect(..., check_same_thread=False)` + 各自 `RLock` + 各自開 `PRAGMA journal_mode=WAL`，共用同一個檔。

### 2.2 行程模型（決定能不能多 replica）

- 單一 `uvicorn` 行程（README 記錄 `uv run uvicorn app.main:app`）。**repo 內目前沒有任何 Dockerfile / compose / k8s manifest** → production 部署檔要從零建。
- `app/jobs.py` 用 `ProcessPoolExecutor`（`SMDR2_MAX_WORKERS`，預設 2）。worker 是 pickle 隔離的獨立 OS 行程。
- **Worker store-access invariant**：worker 必須呼叫 `Store.load_library()` 重讀，**絕不可用 `LIBRARIES` 記憶體 cache**（跨重用 worker 會 stale）。
- **`_jobs` 是 module-level 記憶體 dict（`_lock` 保護）**：job 狀態存在「接到上傳的那個行程」裡，前端用 HTTP polling 讀。

### 2.3 既有的 env 設定慣例（可沿用，不需設定框架）

`SMDR2_MAX_WORKERS`、`SMDR2_MAX_UPLOAD_MB`、`SMDR2_N_JOBS` —— 全是 `os.environ.get(..., default)`。新增 `SMDR2_BLOB_BACKEND` / `SMDR2_MINIO_*` 完全套得進這個慣例。

---

## 3. 核心觀念：MinIO 解 blob，不解 database

- **MinIO（S3 相容物件儲存）** 很適合 `data/` 的 blob：put / get / delete / presigned URL。
- **MinIO 不能放 `library.sqlite`**：SQLite 依賴 POSIX file locking 與就地部分寫入；物件儲存（與 NFS/EFS）都不保證這兩者 → **靜默壞檔**。這是硬規則。
- 因此關聯層是獨立決策：**SQLite 留在 PVC / ephemeral disk（單 writer）** vs **換成真 DB（Postgres / Oracle）**。

---

## 4. 決策分岔（這兩題決定走哪條路）

```
Q1：會不會需要「多個 app replica」（HA / 零停機 / 單機 CPU 不夠）？
    否（= 目前「先不會」）──► 關聯層可留 SQLite，只外部化 blob
    是 ─────────────────────► 必須 (a) 換 DB，且 (b) 把 _jobs 外部化
                               （光換 DB 不夠！見 §5）

Q2：公司政策是否強制「app 持久資料一定要進公司 DB」？
    否 ──► Plan A（SQLite + Litestream→MinIO）
    是 ──► Plan B（爭取 Postgres）；真的只有 Oracle 才走 Plan C
```

> 重點澄清：**「使用者變多」≠ 需要多 replica。** SMDR2 的 web 層很輕，重的是 ProcessPool 的 CPU-bound 比對。單 pod 配大一點的 CPU/記憶體 + 調高 `SMDR2_MAX_WORKERS` 可以撐很多人。真正逼你走「是」的是 **HA 需求** 或 **單機 CPU 不夠**。

---

## 5. 為什麼「多 replica」不只是換 DB —— `_jobs` 才是硬牆

即使 blob 上了 MinIO、關聯上了 Postgres，只要 `_jobs` 還在記憶體：

- replica A 接到上傳 → job 建在 A 的 `_jobs`。
- 狀態 poll 被 LB 導到 replica B → B 的 `_jobs` 沒這個 job → **回 404**。

所以多 replica 的**前置條件**是把 `_jobs` 外部化（存到 DB 的 `jobs` 表或 Redis）。在那之前，不管 blob/DB 放哪，app 都是**單 replica only**。

好消息：因為「先不會」多 replica，這道牆**現在可以不用拆**。`_jobs` 留記憶體、單 replica，是這次的合理選擇。

---

## 6. Phase 0（三個方案共用）：收掉 raw SQL 漏點

DB 存取**幾乎**全包在三個 store class 內，但 `app/main.py` 有 **3 處直接寫 SQL**（繞過 store）：

- `app/main.py:401-408`
- `app/main.py:478-480`
- `app/main.py:506-508`

都是 `with FILE_STORE.lock, FILE_STORE.conn: ... UPDATE files SET ...`。

**動作**：把這 3 段抽成 `FileStore` 的方法（例如 `clear_product_binding(file_id)`、`set_product_binding(...)`），讓關聯存取 **100% 封裝在 store class 內**。這樣未來任何 DB swap（Plan B/C）都只動那三個檔，不會散到 `main.py`。約 0.5 天。

---

## 7. Phase 1（三個方案共用）：blob → MinIO

### 7.1 設計：`BlobStore` 抽象

新增 `app/blobstore.py`，定義 protocol：

```python
class BlobStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def open_stream(self, key: str): ...        # 給 SVG / 大檔 streaming
    def presigned_url(self, key: str) -> str: ...
```

兩個後端：
- `LocalBlobStore`（包現在的檔案系統，**dev 預設**）。
- `MinioBlobStore`（`minio` 或 `boto3`，prod，以 `SMDR2_BLOB_BACKEND=minio` 切換）。

`storage.py` 的 `upload_path()` 等從「回傳 `Path`」改成「回傳 **object key 字串**」（`uploads/{id}.dxf`）；I/O 一律走 `BlobStore`。

### 7.2 Worker I/O 建議：parent 代理，worker 維持純檔案系統

ProcessPool worker 是獨立行程，目前接 path 字串、自己讀寫檔。**MinIO client 不是 fork-safe**（見 §10 地雷）。最低維護的做法：

- **parent** 在 submit 前把 input blob 下載到 scratch temp dir → 把 temp path 丟給 worker；
- worker **照舊只碰本地檔**（程式碼幾乎不動）；
- worker 回傳結果後，**parent 的 done-callback 把 output 上傳 MinIO**。

→ 憑證與網路不進 worker 子行程，blast radius 最小。blob 都很小（§2.1），代理成本可忽略。

### 7.3 物件儲存不支援的假設（要逐一改）

| 現況假設 | 物件儲存的對應 |
|---|---|
| `Path.exists()`（散布很多處） | `HEAD` / `stat_object`（多一次 round-trip） |
| `Path.unlink()`（match 失效、transient 清理） | `delete_object`（404 視為已刪） |
| 原子 `write_bytes()` / `write_text()` | 無原子 rename；用 staging key 或 conditional-put（現設計容忍不完整寫入，job 可重跑） |
| `@lru_cache(_cached_parsed(path, mtime_ns))` | **物件儲存沒有 mtime** → 改用 **ETag / version** 當 cache key |
| `FileResponse(svg_path)`（serve SVG） | presigned URL 轉址，或 stream 下載 |
| `shutil.copyfile`（DRC bundle 組裝） | 下載到 temp 再打包，或 bucket 內 copy |
| `mkdir(parents=True)` | no-op（物件 key 是扁平的） |

### 7.4 transient `primitives.json` 的洩漏風險

Phase 1 寫、Phase 2 成功後刪。若 Phase 2 失敗或跳過（unit override），物件會**永久殘留在 MinIO**。需要明確的刪除 + 一支定期 cleanup（依 prefix 掃孤兒）。

**Phase 1 量級：約 4–6 天**（主要是 blob call-site 的廣度，不是深度；參考 §11 清單，約 45+ 個點）。

---

## 8. 關聯層三方案

### Plan A ★（推薦）：SQLite 保留 + Litestream → MinIO

- 單 replica；SQLite 放 ephemeral disk 或 RWO block PVC。
- **Litestream**：一個小 binary（sidecar / entrypoint wrapper），把 SQLite 連續複製到 **你已經有的 MinIO**；pod 開機從 MinIO restore。
  - 給你 **PITR + 異地備份**，DB 程式碼**完全不動**。
  - 甚至可以**不掛 PVC**：開機 restore 到 ephemeral disk、持續寫回 MinIO，**所有持久資料都在 MinIO**。
  - 代價：**單 writer / 單 replica**；非同步複製 → crash 時可能丟最後幾秒的寫入（對內部工具可接受）；MinIO 開機時必須可達。
- 一次性工：**幾乎只有 §6 + §7**（Litestream 設定 ~0.5–1 天）。**棧最小**：SQLite + Litestream + 一個 S3 client。

### Plan B（次選）：PostgreSQL + MinIO，無狀態 pod

- 需要公司提供 Postgres。pod 變完全無狀態（不用 PVC、不用 Litestream）。
- DB ops（備份 / HA）歸 infra；**這在 K8s 上反而是低維護**。
- 一次性工：**SQLite → Postgres port，約 5–7 天**（見 §12 清單）。方言近（`?`→`%s`、`ON CONFLICT`、JSONB），是三者中 CP 值最好的「換 DB」選項。
- 技術棧建議：**psycopg(v3) 直連 + 手寫 SQL**（維持現風格）；migration 可沿用現有 idempotent DDL 改 PG 語法，**Alembic 可選不強制**。**不要上 SQLAlchemy ORM**（違背小棧目標）。

### Plan C（最後手段）：Oracle + MinIO

只有在「政策強制進公司 DB 且只有 Oracle」時才走。Oracle 是這個 codebase **最重**的目標，跟「低維護」衝突最大：

- **綁定參數**：SQLite 全用 `?`，Oracle 要 `:1 / :name` → **每一條 SQL 都要改**（`files.py`/`library.py`/`products.py` 幾十條）。
  - *減痛*：可寫一層薄 adapter 把 `?` 自動轉 `:n`，但仍是 shim。
- **`INSERT OR REPLACE` / `INSERT OR IGNORE` / `UPDATE OR IGNORE`** → Oracle 無，要改 `MERGE`（囉嗦）或接 unique-violation。seed 預設 class、template dedup、register 都中。
- **型別**：`TEXT`→`VARCHAR2`/`CLOB`；`entity_point_sets` 是大 JSON 點雲 → **一定要 CLOB**（VARCHAR2 上限 4000 bytes）。`REAL`→`NUMBER`/`BINARY_DOUBLE`。
- **交易**：現靠 `with self.conn:` 自動 commit，Oracle 要**手動 `commit()`** → 全面改。
- **introspection**：`PRAGMA table_info`→`USER_TAB_COLUMNS`；`executescript` 要拆開逐條跑；無 `IF NOT EXISTS`（現有 `has_col()` 模式可轉）。
- **driver**：`python-oracledb`（thin mode 免裝 Instant Client，是唯一的安慰）。
- 一次性工：**估 8–12 天**，且觀感最不「小棧」。

---

## 9. 要 infra 確認的兩題（決定走哪條）

1. **有沒有 PostgreSQL？**（不要只有 Oracle。）→ 決定 Plan B vs C。
2. **內部工具的資料能不能自管**（SQLite-on-PVC / Litestream），還是**一定要進公司 DB**？→ 決定 Plan A vs B/C。

附帶（之後 sizing 用）：備份/還原期待（volume 快照就好 vs 需要 PITR）、最大 DXF 大小與尖峰併發。

**目前賭注：答案多半會落在 Plan A。**

---

## 10. 地雷清單（不分方案都要記得）

- ❌ **MinIO 不是資料庫** —— 永遠不要把 `library.sqlite` 放物件儲存或 NFS（locking + 無 partial-write = 壞檔）。
- ❌ **psycopg / MinIO client 不是 fork-safe** —— 必須在 ProcessPool worker **函式內 lazy 建立**，絕不從 module-level singleton 繼承（fork 時會帶進壞掉的 handle）。或改用 `spawn` start method。這是整個遷移**最隱晦的正確性風險**。
- ❌ **`_jobs` 記憶體 registry** 讓 app 天生單 replica；多 replica 前**必須先外部化它**（不是換了 DB 就好）。
- ❌ **SQLite on NFS/EFS-backed PVC** 會靜默壞檔 —— 要留 SQLite 就必須是 **block storage（RWO）**。
- ❌ **`lru_cache` 以 `mtime_ns` 為 key** 在物件儲存失效（沒有 mtime）→ 改 **ETag/version**。
- ❌ **transient `primitives.json`** 在物件儲存會洩漏（Phase 2 失敗/跳過時）→ 明確刪除 + cleanup job。
- ❌ **DRC bundle 組裝**（`shutil.copyfile` / `FileResponse`）假設本地檔 → 改下載到 temp 或 presigned URL。

---

## 11. 附錄 A：Blob call-site 盤點（約 45+ 點，code scan）

> 行號為掃描當下近似值，實作時以 codegraph / grep 為準。caller 概數：`upload_path`~12、`parsed_path`~21、`match_path`~29、`rule_check_path`~13、`layer_preview_*`~8+。

| 區塊 | 讀/寫 | 位置（近似） | 怎麼碰 |
|---|---|---|---|
| `uploads/*.dxf` | 寫 | `main.py:489-492` | `dst.write_bytes(content)`（content-hash 去重，`exists()` 守門） |
| `uploads/*.dxf` | 讀 | `jobs.py:588`(discover)、`jobs.py:349`(preprocess)、`drc_bundle.py:146-148`(zip) | 傳 path 字串給 worker |
| `parsed/*.json` | 寫 | `jobs.py:156-168` | `json.dump({primitives,bbox,background,selected_layers})` |
| `parsed/*.json` | 讀 | `main.py:112-113`(`@lru_cache _cached_parsed`)、`jobs.py:793-794`(save-match) | mtime_ns cache key（要改 ETag） |
| `prematch/*.json` | 寫/讀 | `jobs.py:256-261` / `main.py:1290-1293` | 缺檔回 `{by_class:{},total:0}` |
| `match/*.json` | 寫 | `jobs.py:846-849` | `json.dump(out, indent=2)` |
| `match/*.json` | 讀 | `main.py:1331-1334`、`drc_bundle.py:147`、`main.py:1365-1370` | bundle 需 byte-for-byte |
| `match/*.json` | 刪 | `main.py:409, 485, 660` | side-region 編輯後 `unlink()` 失效 |
| `rule_check/*.json` | 寫/讀 | `jobs.py:658-661`、`main.py:1432-1433` / `main.py:1388-1402` | 讀路徑會 `_validate_envelope` |
| `layer_preview/layers.json` | 寫/讀 | `jobs.py:522-547` / `main.py:697-701` | confirm_layers 也讀來驗 |
| `layer_preview/*.svg` | 寫/讀 | `jobs.py:533` / `main.py:777-780` | serve 用 `FileResponse`（要改） |
| `layer_preview/primitives.json` | 寫/讀刪(transient) | `jobs.py:552-559` / `jobs.py:124-129, 266` | Phase 2 成功才刪（洩漏風險） |
| 目錄建立 | — | `storage.py:27-28`、`jobs.py` 多處 | `mkdir` 在物件儲存是 no-op |
| `exists()` 散布 | — | `main.py` ~20+ 處、`jobs.py:124,791` | 改 `HEAD` |
| DRC bundle copy | — | `drc_bundle.py:182-183` | `shutil.copyfile`（要改） |

---

## 12. 附錄 B：SQLite-specific 構造盤點（Postgres port 用，約 30 項）

> 來源：`app/files.py`、`app/library.py`、`app/products.py`。

**乾淨可直接 port（風險低）**
- 主鍵全是顯式 `TEXT`（UUID / content-hash），**無 `AUTOINCREMENT`/rowid 依賴**。
- 讀回前已有顯式 `float(...)`/`int(...)` 轉型 → Postgres 嚴格型別不會踩雷。
- 標準 FK 關係（`templates.library_id→libraries.id`、`products.library_id→libraries.id`）。

**直譯（機械改動）**
| SQLite | Postgres |
|---|---|
| `INSERT OR REPLACE`（`files.py:406`） | `INSERT ... ON CONFLICT (id) DO UPDATE SET ...` |
| `INSERT OR IGNORE`（`library.py:445,577,647`） | `INSERT ... ON CONFLICT DO NOTHING` |
| `UPDATE OR IGNORE`（`library.py:554`） | 無對應 → 改顯式邏輯（交易內 check 或 DELETE+INSERT） |
| `row_factory=sqlite3.Row` | `RealDictCursor`（psycopg）；row 存取語法不變 |
| `PRAGMA foreign_keys=ON` | 移除（Postgres 預設強制 FK） |
| `PRAGMA journal_mode=WAL` | 移除（Postgres MVCC 原生） |
| `PRAGMA table_info` / `foreign_key_list` | `information_schema.columns` / `...table_constraints` |
| `ALTER TABLE RENAME COLUMN`（`files.py:337,343`） | 1:1 對應 |
| `time.time()` REAL 欄位 | 可留 `NUMERIC`，或升 `TIMESTAMPTZ`（建議 UTC） |
| JSON-as-TEXT（`selected_layers`/`*_rect`/`entity_point_sets`/`entity_kinds`/`dxf_recover_notes`） | 可留 `TEXT`，或升 `JSONB` |
| `match_saved` INTEGER 0/1 | 可留 `INTEGER`，或升 `BOOLEAN` |

**主要工程（真正的成本）**
- **imperative `__init__`/`_migrate()` + `PRAGMA table_info` 逐欄補 `ALTER TABLE ADD COLUMN`**（`files.py:304-369`、`library.py:463-541`）：這是 Postgres 化的**主要 blocker**。建議 port 成版本化 migration（Alembic）或至少改用 PG 的 `ADD COLUMN IF NOT EXISTS` 重寫；不要留在 app `__init__`。
- **`executescript()` 批次 DDL**（`files.py:289`、`library.py:437,490,517`）→ 拆成交易內逐條 `execute()`。
- **`with self.lock, self.conn:`（RLock + 自動 commit）** → 連線池取代 RLock；多語句仍要顯式交易邊界。
- **三個獨立 `sqlite3.connect()`（各自 RLock + WAL）** → **合併成一個共享連線池**注入三個 store。架構淨利：handle 更少、跨表 FK 真正被強制（`products→libraries` 目前跨連線無法強制）。

> 註：**Oracle** 的 port 在上述之外，還要加 §8 Plan C 的綁定參數（`?`→`:n`）、`MERGE`、CLOB、手動 commit、`USER_TAB_COLUMNS` 等，工程量明顯大於 Postgres。

---

## 13. 工時彙總

| 階段 | 內容 | 估時 |
|---|---|---|
| Phase 0 | 收 `main.py` 3 處 raw SQL 進 `FileStore` | ~0.5d |
| Phase 1 | BlobStore 抽象 + MinIO 後端 + ~45 call-site + worker 代理 I/O + cache 改 ETag + bundle/SVG 改寫 + transient 清理 | ~4–6d |
| **Plan A 收尾** | Litestream→MinIO + container/k8s（單 replica、Recreate） | ~1.5–2d |
| **Plan A 合計** | | **~6–8d** |
| Plan B 追加 | SQLite→Postgres（方言 + 連線池 + fork-safe + migration） | ~5–7d |
| Plan C 追加 | SQLite→Oracle（綁定 + MERGE + CLOB + commit + introspection） | ~8–12d |
| Phase 3（延後） | `_jobs` 外部化，才能 `replicas>1` | ~2–3d |

---

## 14. 下一步

1. Infra 回 §9 兩題。
2. 依答案收斂成 OpenSpec change（propose-first）：
   - 最可能：`add-minio-blob-backend`（= Phase 0 + Phase 1 + Plan A 收尾）。
   - 若政策強制 DB：再加一支 `port-relational-store-to-postgres`（Plan B）。
3. `_jobs` 外部化（Phase 3）獨立成未來牌，待真的要多 replica 再開。
