## Why

Conform 要從單機單用戶 dev 工具上到公司 k8s:政策**強制同時跑 2 個 pod**,而現行 in-memory job dict、本地 `data/`、process 快取在第二個 pod 出現的瞬間就壞;同時公司要求 Keycloak SSO 登入與自建權限管理(2026-06-11 全部定案,見 `docs/schema-auth-jobs.md`、`docs/auth-permissions.md` 勘誤)。基礎設施(MariaDB、MinIO)由 IT 維運,app 只需把狀態全部外移並收斂設定為 env var。

## What Changes

- **DB 遷移**:`library.sqlite` → IT 維運的 MariaDB(utf8mb4);SQLite 仍是測試/本地路徑。Litestream 方案作廢。
- **Blob 外移**:uploads 與 pipeline artifacts(parsed/prematch/match/rule_check/layer_preview)從本地 `data/` → MinIO,一律走 boto3(S3 API);本地只留 per-request scratch。
- **Job queue 落地 DB**:`app/jobs.py` 的 in-memory `_jobs` dict + 進程內 callback → `jobs` 表 + worker 認領迴圈(樂觀兩步認領、heartbeat、死 job 回收);web 與 worker 拆開部署(web ×2 + worker ×1)。
- **清除 process-level 狀態**:`LIBRARIES` 快取、dev_overrides 的跨 replica 髒讀。
- **登入**:Keycloak OIDC,BFF 模式(後端 Authorization Code + PKCE,前端只拿 HttpOnly session cookie);`preferred_username` = userid,首登自動建帳;server-side session(SHA-256、idle 8h/絕對 24h)+ CSRF。
- **授權(自建)**:admin/editor/viewer × global/customer/product 範圍 × 個人/部門(deptid)對象;**customer = product 上層新分群**;editor 可簽核範圍內版本(含自己建的);`BOOTSTRAP_ADMINS` 種第一個 admin;audit log。**BREAKING**:`SMDR2_AUTH_MODE=oidc` 下未登入一律 401、無 grant 者看不到任何 product(bypass 模式維持現行為)。
- **編輯鎖**:product 級悲觀鎖(顯式開始編輯、heartbeat 30s/TTL 5min、admin 強制解鎖)。
- **建 product/customer 僅 admin**,product 必掛 customer。

已落地(Phase 0,working tree):docker-compose dev 環境(nginx LB + web×2 + MariaDB + MinIO + Keycloak realm 仿公司 JWT)、`app/auth.py`(五表 store + identity 依賴,23 測試)、schema 定稿文件。

## Capabilities

### New Capabilities
- `auth-session`: Keycloak OIDC BFF 登入流程、server-side session 生命週期、CSRF、`SMDR2_AUTH_MODE` bypass/oidc 行為、內部端點豁免
- `authorization`: 角色×範圍×對象的 grant 模型、customer 分群、有效角色判定、endpoint 存取矩陣、bootstrap admin、audit log 必記事件
- `product-edit-lock`: product 級悲觀編輯鎖的取得/續約/逾時/強制解鎖語意
- `job-queue`: DB-backed job 生命週期(認領、heartbeat、回收、去重、父子進度)、跨 replica 輪詢一致性
- `blob-storage`: blob/artifact 的 S3 介面語意、scratch 生命週期、上傳大小限制(≥200MB,連動 SEC-001)

### Modified Capabilities
- `product-files`: 建 product 僅 admin 且必須指定 customer;product 列表/可見性依 viewer 範圍過濾

## Impact

- 受影響 code:`app/storage.py`(路徑→S3 key)、`app/jobs.py`(整個 queue 機制)、`app/library.py`(`LIBRARIES` 快取)、`app/files.py`/`app/versions.py`/`app/products.py`(store 連線層)、`app/main.py`(全部 endpoint 掛權限 dependency、BFF 路由)、`app/dev_overrides.py`
- 新依賴:`sqlalchemy`(或 `pymysql` 直驅)、`boto3`、`authlib`/`python-jose`(OIDC)、`alembic`(migration)
- 部署:Dockerfile/compose 已備;k8s 端 web Deployment ×2 + worker ×1,設定走 env(prod secrets 走 Vault)
- 測試:既有 640 測試在 bypass 模式下不變;新增 auth/queue/storage 合約測試
