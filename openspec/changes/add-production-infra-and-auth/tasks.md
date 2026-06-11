## 1. Phase 0 — 已完成(回填紀錄)

- [x] 1.1 docker-compose dev 環境(nginx LB + web×2 + MariaDB + MinIO + Keycloak realm 仿公司 JWT)、Dockerfile、deploy/README
- [x] 1.2 schema 定稿 `docs/schema-auth-jobs.md`;`docs/auth-permissions.md` 勘誤
- [x] 1.3 `app/auth.py`:AuthStore(users/customers/role_grants/audit_log/product_edit_locks)+ `get_identity`(bypass 預設)+ `tests/test_auth.py` 23 測試

## 2. Phase 1 — DB 連線層與 MariaDB(卡 DBA 資訊;compose 可先行)

- [x] 2.1 引入 SQLAlchemy Core + Alembic;新增 `app/db.py` 連線模組(`DATABASE_URL`,未設 fallback SQLite 檔案;pool_pre_ping)
- [x] 2.2 第一份 Alembic migration:既有七張表 + auth 五張表(MariaDB 方言對照 schema 文件 §8)
- [x] 2.3 各 store(files/products/versions/library/auth)改走 `app/db.py`;`*_SCHEMA` 常數降級為測試用
- [x] 2.4 測試策略落地:suite 維持 SQLite;新增 compose MariaDB 上的 smoke(CI 可選跑)
- [x] 2.5 suite 全綠 + compose 環境對 MariaDB 手動 smoke(上傳→match→rule-check 全流程)

## 3. Phase 1 — BlobStore 與 MinIO

- [x] 3.1 `app/storage.py` 加 `BlobStore` protocol + local-FS 實作(預設,行為不變)
- [x] 3.2 boto3 S3 實作(`S3_ENDPOINT_URL` 觸發),key = 現行相對路徑;大檔 streaming 到 per-request scratch
- [x] 3.3 jobs worker 與 endpoint 的檔案讀寫全部改走 BlobStore;上傳限制 `SMDR2_MAX_UPLOAD_MB` 預設上修 200(SEC-001)
- [x] 3.4 compose 環境驗證:web-1 上傳 → worker 處理 → web-2 讀結果;150MB 大檔記憶體/scratch 實測(設計 Risk #1)

## 4. Phase 2 — jobs 表與 worker 拆分(依賴 §2 §3)

- [x] 4.1 Alembic migration:`jobs` 表(schema 文件 §7)
- [x] 4.2 `app/jobs.py` submit 五式改 INSERT(簽名不變);`GET /api/jobs/{id}` 與去重改 DB 查詢
- [x] 4.3 `app/worker_loop.py`:兩步樂觀認領、heartbeat 30s、回收(>120s requeue,attempts≥3 → error)、done-callback 副作用逐一搬入、7 天 prune
- [x] 4.4 移除 in-memory `_jobs` dict 與 web 進程 executor;`LIBRARIES` 快取改 fresh read;dev_overrides prod 停用
- [x] 4.5 compose 解開 worker service;雙 web + worker 全流程驗證(job 輪詢跨 replica、kill worker 後 job 復活)
- [x] 4.6 suite 全綠 → **k8s replicas=2 解鎖點**

## 5. Phase 3 — BFF 登入與 session(卡 Keycloak 資訊;compose 替身可先行)

- [ ] 5.1 Alembic migration:`sessions` 表;Authlib OIDC client(雙 URL:ISSUER/INTERNAL_BASE)
- [ ] 5.2 `/auth/login`、`/auth/callback`、`/auth/logout` 路由;login upsert 走 `AuthStore.upsert_user_from_claims`;session cookie(SHA-256 落庫、idle 8h/絕對 24h)
- [ ] 5.3 CSRF(`X-CSRF-Token`)+ 內部端點豁免清單;session prune 掛 worker 迴圈
- [ ] 5.4 `get_identity` 的 oidc 分支接 session 查詢;`BOOTSTRAP_ADMINS` 啟動 seeding
- [ ] 5.5 compose Keycloak 四帳號端到端登入測試(admin1 首登即 admin;viewer1 無 grant 全空)

## 6. Phase 3 — 權限掛載與編輯鎖(依賴 §5)

- [ ] 6.1 Alembic migration:`products.customer_id`(DEFAULT 'uncategorized')
- [ ] 6.2 dependency factories:`require_role(role, scope_from)` + `require_lock()`;順序 身分→角色→鎖→簽核 guard
- [ ] 6.3 按 specs/authorization 存取矩陣掛上全部 endpoint(bypass 模式下 suite 行為不變);product 列表按 viewer 範圍過濾
- [ ] 6.4 編輯鎖 API:取得/heartbeat/釋放/admin 強制(`AuthStore` 已有協定實作);前端「開始編輯」+ 鎖持有者顯示 + 30s heartbeat
- [ ] 6.5 簽核/範本增刪改/策略變更接 audit log;既有 `SMDR2_DEV_USER` 簽核路徑改 identity
- [ ] 6.6 測試:fixture 以 `dependency_overrides` 注入身分;矩陣的 403/409 合約測試;suite 全綠

## 7. Phase 3 — Admin 管理介面

- [ ] 7.1 customer CRUD 頁(admin-only;非空不可刪)
- [ ] 7.2 grants 管理頁:個人/部門指派(dept 下拉 = `known_deptids()` + 手動輸入)、撤銷、現有權限總覽
- [ ] 7.3 audit log 檢視頁(篩 product/actor/action)
- [ ] 7.4 建 product 流程加 customer 選擇(admin-only)

## 8. Phase 4 — 上線掃尾

- [ ] 8.1 launch readiness 殘項:logging ERR-005/009、json-guard ERR-001/004
- [ ] 8.2 k8s manifests(web ×2 + worker ×1、env/Vault 對應、ingress client_max_body_size)
- [ ] 8.3 `SMDR2_AUTH_MODE=oidc` 切換演練(compose):無 grant 登入體驗、admin 預先 grant 流程
- [ ] 8.4 docs 同步:ARCHITECTURE/README/CHANGELOG;archive 本 change
