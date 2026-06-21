## Context

單機假設遍佈三層:狀態(SQLite 單寫者 + 本地 `data/`)、併發(in-memory `_jobs` dict + 進程內 ProcessPool callback,`app/jobs.py:63`)、身分(`SMDR2_DEV_USER` 佔位)。公司 k8s 政策強制 2 pod,等於三層同時到期。完整 schema 與協定已定稿於 `docs/schema-auth-jobs.md`(表結構、認領協定、鎖協定、MariaDB 方言對照),權限模型決策史在 `docs/auth-permissions.md`。本文記 schema 文件之外的架構決策。

外部條件:MariaDB(3 replica)與 MinIO 皆 IT 維運,MinIO 限 boto3;Keycloak 由公司提供(realm/client 資訊待取);不搬既有資料,prod 空庫開始;dev 環境 = repo 內 docker-compose(nginx LB + web×2,刻意鏡像 2-pod 形態)。

## Goals / Non-Goals

**Goals:**
- 2 個 web pod 同時服務時,job 輪詢、快取、artifact 讀寫全部正確
- Keycloak 登入 + 自建授權上線,bypass 模式保留(dev/測試行為不變)
- 環境差異 100% 收斂為 env var,k8s 部署只改 config

**Non-Goals:**
- 不做 Keycloak back-channel logout(session 上限兜底)
- 不做即時共編/WebSocket(編輯鎖 + 輪詢已定案足夠)
- 不搬既有 dev 資料;不做 rules 編輯權限(app 無此功能)
- 不優化 scan-all 並行度(獨立 change)

## Decisions

**D1 — DB 層用 SQLAlchemy Core(`text()` + named params),不用 ORM、不用雙驅動 if/else。**
現行 store 是 raw `sqlite3`(`?` paramstyle);PyMySQL 是 `%s`,直接雙驅動會讓每條 SQL 長出兩套。SQLAlchemy Core 給統一 paramstyle、connection pool(`pool_pre_ping`)、Alembic 整合,而 store 的 SQL 本體幾乎原樣搬進 `text()`。ORM 不引入——現行 dataclass + SQL 風格保留。測試與本地 dev 走 `sqlite://` URL,suite 速度不變;`DATABASE_URL` 未設時 fallback SQLite 檔案(向後相容)。

**D2 — Blob 層走 `BlobStore` protocol,兩個實作:S3(boto3)與 local-FS。**
介面沿 `docs/production-storage.md` 既有草案(put/get/exists/delete/open_stream/presigned_url)。S3 key 直接沿用現行相對路徑(`uploads/{file_id}.dxf`、`parsed/{version_id}/{file_id}.json`…),零心智轉換。測試與未設 `S3_ENDPOINT_URL` 的 dev 用 local-FS 實作(= 現行為)。worker 讀大檔(DXF 150MB)用 streaming download 到 per-request scratch,不整包進記憶體。

**D3 — jobs 認領用樂觀兩步(SELECT 候選 → 條件 UPDATE 看 rowcount),不用 `SKIP LOCKED`。**
理由在 schema 文件 §7:MySQL 系禁止 UPDATE 子查詢引用同表、SQLite 沒有 SKIP LOCKED;兩步協定在兩引擎語意一致,≤10 併發無吞吐顧慮。worker 與 web 同 image 不同 command(`python -m app.worker_loop`),ProcessPool 留在 worker 進程內當執行器;web 不再起 executor。

**D4 — 既有 jobs.py 的五種 submit API 簽名不變,改為 INSERT 列 + payload JSON。**
done-callback 的 FILE_STORE 副作用(update_parsed、set_match_saved…)搬進 worker 迴圈的 job 完成處理,語意逐一對照現行 `_on_*_done`。`find_inflight_preprocess_job` 改 `idx_jobs_binding` 查詢——跨 replica 去重從此正確。

**D5 — `LIBRARIES` 快取直接移除,讀取一律 `Store.load_library()` fresh read。**
worker 端本就強制 fresh read(docstring 不變式 + regression test);web 端讀頻率低、單查詢 <5ms,快取的跨 pod 失效成本遠大於收益。dev_overrides 在 prod 模式整組停用(env gate),不搬 DB。

**D6 — 認證/授權分層:BFF 換 token、session 落 MariaDB、權限全在 `app/auth.py` 本地判。**
OIDC 用 Authlib(成熟、FastAPI 整合直接)。雙 URL 設計(`OIDC_ISSUER` vs `OIDC_INTERNAL_BASE`)對應 compose 與 k8s 的瀏覽器/網內分流,已在 dev 環境驗證 issuer 一致。`get_identity` 依賴已落地,`SMDR2_AUTH_MODE=bypass` 預設讓掛 dependency 與開 oidc 成為兩個獨立、可分開驗證的步驟。

**D7 — endpoint 存取矩陣集中宣告,不散在各 handler。**
一張「路由 → 所需角色(+是否需編輯鎖)」對照表(specs/authorization 為準),以 FastAPI dependency factory 實現:`require_role('editor', product_from='path')` + `require_lock()`。順序固定:身分 → 角色 → 鎖 → 簽核 guard。內部端點(health/metrics)走豁免清單。

**D8 — migration 用 Alembic,從第一張表開始。**
versioned migration 是進公司 DB 的前提;`*_SCHEMA` 常數降級為測試 in-memory 用,prod schema 一律 migration 建。

## Risks / Trade-offs

- [150MB 單檔記憶體未實測] → Phase 1 完成後在 compose 環境灌大檔實測 parser/worker RSS 與 scratch 用量;上傳限制同步上修 ≥200MB(SEC-001)
- [兩步認領在極端競爭下空轉] → rowcount=0 重試 + 隨機 backoff;規模上限 (≤10 user) 使空轉機率趨近零
- [worker 單 replica 是吞吐瓶頸] → 接受:現行也是單機;jobs 表落地後加 replica 純調 yaml
- [SQLAlchemy 引入造成 store 大面積 churn] → 連線層集中一個模組,各 store 只改 import 與 execute 呼叫;640 測試是安全網
- [bypass→oidc 切換日 UX 斷裂(無 grant 者全空)] → 上線前由 admin 預先 grant;`BOOTSTRAP_ADMINS` 保證第一個 admin 存在
- [Keycloak/DBA 資訊未到] → Phase 1/3 的外部閘門;compose 替身讓開發不被卡

## Migration Plan

Phase 順序(= memory impl order;每 phase 結束 suite 全綠):
1. **Phase 1**:Alembic + SQLAlchemy 連線層 + 既有表遷 MariaDB;`BlobStore` + artifacts 上 MinIO
2. **Phase 2**:jobs 表 + worker_loop;移除 in-memory dict 與 `LIBRARIES` 快取;compose 解開 worker service → **此後才准 replicas=2**
3. **Phase 3**:BFF 登入 + session/CSRF;權限 dependency 按矩陣掛上(bypass 預設,行為不變);admin 管理 UI;k8s 切 `SMDR2_AUTH_MODE=oidc`
4. **Phase 4**:launch readiness 殘項(logging、json-guard、SEC-001)、150MB 實測

Rollback:每 phase 是獨立 PR;DB schema 只加不改(Alembic down 可用但不依賴);`SMDR2_AUTH_MODE` 與 `S3_ENDPOINT_URL`/`DATABASE_URL` 未設即回 SQLite/本地/bypass——任一 phase 可單獨退。

## Open Questions

- Keycloak realm/client/issuer、MariaDB 連線與專用 schema(外部,待要)
- k8s CPU limit 與 `SMDR2_MAX_WORKERS`/ProcessPool 的匹配(部署時與 infra 對)
- session 與 jobs 的定期 prune 掛 worker 迴圈即可,或需要 k8s CronJob(實作時定,傾向前者)
