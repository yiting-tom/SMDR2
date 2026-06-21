# Design — add-product-versioning

## Context

設計討論已於 2026-06-10 全部定案(詳見 `docs/product-versioning.md`、`docs/DISCUSSION.md` §A/§C、`docs/system-design.md` §5/§7.3-7.4),本文件只收「怎麼實作」的技術決策,不重開「為什麼」。

現況關鍵事實:
- 階層 `library → product → {templates, files}`;`templates.product_id` 區分 product-scoped(`PRODUCT_SCOPED_CLASSES` 8 類)與 library-scoped(`NULL`,跨 product 共用)。
- 三個 store singleton(`FILE_STORE`/`_STORE`+`LIBRARIES`/`PRODUCT_STORE`)各自開同一個 `library.sqlite`(WAL、各自 RLock)。
- 衍生 artifact 以 `{file_id}` 為 key:`parsed/`、`prematch/`、`match/`、`layer_preview/`;rule 結果 `rule_check/{product_id}.json`。
- `files` 表同時承擔內容儲存(content-hash id)與綁定狀態(product_id、product_role、selected_layers、rects、unit override…)。
- worker invariant:ProcessPool worker 必須 `Store.load_library()` 重讀,不可吃 `LIBRARIES` cache。
- 無 auth(另一支 change);測試目前會寫真實 dev DB(要一併修)。

## Goals / Non-Goals

**Goals:**
- 落地「一 version 一 library」模型:versions 表、version_files junction、clone-on-new-version、畫押凍結守門、衍生 artifact `(version_id, file_id)` keying、版本切換 UI。
- 刪除兩層 scope(`PRODUCT_SCOPED_CLASSES` 及其 merge/migration 邏輯)。
- 測試 DB 隔離(fixture 級,不再寫 `data/library.sqlite`)。

**Non-Goals:**
- auth / 編輯鎖 / audit log(等 A4/infra 的 auth change;sign-off 端點先以占位身分通)。
- blob → MinIO(Plan A 儲存是下一支 change;本次仍走本地檔案系統,但 path helper 先長成版本化形狀)。
- 版本 diff 視圖(C6 延後)、資料遷移(C9 不遷移)、matching 演算法變更。

## Decisions

### D1. Schema(SQLite,啟動時重建)

```sql
CREATE TABLE versions (
    id          TEXT PRIMARY KEY,          -- 12-char uuid
    product_id  TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    label       TEXT NOT NULL,             -- 自由輸入
    library_id  TEXT NOT NULL UNIQUE REFERENCES libraries(id),  -- 1:1
    signed_off_by TEXT,                    -- NULL = 編輯中
    signed_off_at REAL,
    created_at  REAL NOT NULL,
    UNIQUE (product_id, label)             -- 同 product 版號不重複 → 409
);

CREATE TABLE version_files (
    version_id  TEXT NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,             -- SBT|BD|POD|RING|LID
    file_id     TEXT NOT NULL REFERENCES files(id),
    selected_layers TEXT,                  -- 從 files 搬來的 per-version 狀態
    view        TEXT,
    rects       TEXT,                      -- side-region rects 等 JSON
    unit_override TEXT,
    created_at  REAL NOT NULL,
    PRIMARY KEY (version_id, role, file_id)
);
```

- `products` 失去 `library_id`(library 錨到 version);`files` 刪除 `product_id`/`product_role`/selected_layers/rects 等綁定欄位,只留 `id`(content-hash)、`filename`、`size`、`created_at`、dxf recover notes 等內容屬性。
- `templates`/`classes` 結構不動;`templates.product_id` 欄位刪除(兩層 scope 消失)。
- **(version_id, role) 多檔**:既有規格允許同 role 多檔(product-files spec),junction PK 含 file_id 以保留此能力。
- 重建策略:bump schema version → 啟動時偵測舊 schema 直接 `DROP` 重建(C9 不遷移);dev DB 與 `data/` 衍生目錄由 `scripts/reset_dev_data.py`(新增)一鍵清空。

### D2. Clone-on-new-version(單交易)

`POST /api/products/{pid}/versions {label, clone_from?}`(預設 clone 該 product 最新版;第一版走 `POST /api/products` 一起建,空 library):

1. `INSERT versions`(UNIQUE 失敗 → 409)。
2. `INSERT libraries`(新 id)→ bulk `INSERT templates SELECT ...`(換 library_id、保留原 created_at)→ 同法複製 `classes` 調參。
3. `INSERT version_files SELECT ...`(換 version_id)。
4. 全部包在一個 SQLite 交易;~3000 列毫秒級,不需背景 job。

衍生 artifact **不 clone**(設計書 §7.3):新版第一次互動時由 job 重算;parsed 可在讀取時 fallback——`parsed/{vid}/{fid}.json` 不存在且 `(vid,fid)` 選層與某舊版相同時直接重跑 parse(不做跨版 symlink,避免 keying 歧義)。

### D3. 衍生 artifact keying

`app/storage.py` path helper 簽名全面改為 `(version_id, file_id)`:

```
uploads/{file_id}.dxf                       ← 不變(content-hash 共用)
parsed/{version_id}/{file_id}.json
prematch/{version_id}/{file_id}.json
match/{version_id}/{file_id}.json
layer_preview/{version_id}/{file_id}/...
rule_check/{version_id}.json
```

- `_cached_parsed` 的 lru key 由 `(path, mtime_ns)` 改 `(version_id, file_id, mtime_ns)`(路徑已含 vid,mtime 保留供本地 dev;MinIO 換 ETag 是下一支 change 的事)。
- job payload(`jobs.py`)一律帶 `version_id`;worker 簽名跟著加參數,維持「worker 只收 path 字串/純值」的 pickle 慣例。

### D4. Version context 解析(file-centric API 不變臉)

既有 `/api/files/{file_id}/...` 端點(layers/match/commit/scan-all/patch…)**介面不動**,但 file_id 跨版共用後不再能單獨定位狀態。解法:這些端點全部加 **`version_id` query/body 參數(必填)**,後端以 `(version_id, file_id)` 讀寫 `version_files` 與 artifact。

- 前端 viewer 本來就在 product 頁上下文內開檔,帶 vid 是一行改動。
- 直接打 API 不帶 vid → 422,訊息提示帶法。
- 替代方案(被否決):URL 改 `/api/versions/{vid}/files/{fid}/...`——REST 上更乾淨但要動每一條前端呼叫與測試路徑,改動面大一倍;本次選參數注入,等未來大改版再正規化。

### D5. 凍結守門(畫押)

- 單一 dependency `require_unsigned(version_id)`:FastAPI dependency 注入所有 mutating endpoint(commit/調參/上傳/換綁定/重跑/match-json 寫入);已畫押 → `409 {"error": "version signed-off", "signed_off_by": …}`。
- 「重跑」類(scan-all、rule-check、reprocess)也擋:凍結語意是「結果不再變」。
- sign-off 端點:`POST /api/versions/{vid}/sign-off`(冪等,已畫押 409)、`DELETE`(解畫押)。**身分占位**:無 auth 環境下取 `SMDR2_DEV_USER` env(預設 `"dev"`)寫入 `signed_off_by`;auth change 上線後換成 OIDC sub,介面不變。unsign 在 auth 前不做 admin 檢查(內網 dev 期可接受,規格已注記)。

### D6. 兩層 scope 刪除

- 刪 `PRODUCT_SCOPED_CLASSES`、`is_product_scoped()`、`load_library()` 的雙 scope merge、boot migration 的洩漏清理(`library.py:613-625` 一帶)、`insert_template` 的 product_id 分支。
- `LibraryRegistry` 介面不變(仍以 library_id 取 Library),呼叫端從「product 的 library + product_id」改為「version 的 library_id」一個值,呼叫面反而變簡單。
- canvas.js 的 class 工具列不再有「product-scoped 才顯示」邏輯分支(若存在);類別全等價。

### D7. 測試 DB 隔離

- 新 fixture:`tmp_path` 下建獨立 sqlite + `data/` 衍生目錄,以 env(`SMDR2_DATA_DIR`,新增)注入 `storage.py`/store 初始化;`app.main` 的 singleton 改為可由 env 重定向(維持 module-level 慣例,僅路徑來源改 env)。
- CI/本機跑測試不再污染 `data/library.sqlite`。

## Risks / Trade-offs

- [file-centric 端點加必填 vid 是隱性 breaking] → 前端同 change 內一併改;422 錯誤訊息明確;openspec specs 寫清楚。
- [clone 後 templates id 重新發號,跨版「同一範本」無法追溯] → 接受:C6(diff)延後;若未來要 diff,用 `template_signature` 內容比對即可,不需要 lineage 欄位。
- [無 auth 期間 sign-off 人人可按、可解] → 接受(全封閉內網 + dev 期);auth change 接手身分與 admin 檢查,規格注記。
- [schema 重建會清掉現有 dev 範本(SMD-2T 907 等)] → C9 定案不保留;真要留著玩,reset script 前手動備份 sqlite 即可。
- [`(version_id, role, file_id)` PK 允許同 role 多檔,clone 時整組複製] → 與既有 product-files 規格一致;UI 呈現沿用現行多檔列表。

## Migration Plan

無資料遷移(C9)。部署步驟:
1. 合併後啟動 → 偵測舊 schema → drop & recreate(或手動跑 `scripts/reset_dev_data.py`)。
2. 回滾 = 切回舊版 code + 重跑 reset(dev 資料無保留義務,雙向都是重練)。

## Open Questions

無——設計問題已全數在 `docs/DISCUSSION.md` 定案;唯一外部依賴(auth 身分)以 D5 占位策略解耦。
