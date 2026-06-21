# Schema 定稿:Auth / Jobs / Edit-Lock(Phase 0 產物)

> 狀態:**定稿(2026-06-11)**。本文是施工單層級的 schema 規格,Phase 1–3 直接照此實作。
> 上游決策:[auth-permissions.md](auth-permissions.md)(權限模型)與已刪的 production-storage.md(MariaDB/MinIO;結論在 SYSTEM_DESIGN §5.2/§10)。
> **2026-06-12 後記**:全部已實作(Alembic 0001–0005);0005 另在 `versions` 表加 `evidence_name`/`evidence_type`(簽核證明圖,見 openspec `add-signoff-evidence`),不在本文七表範圍。
> 本文取代 auth-permissions.md 中與 2026-06-11 定案衝突的舊結論(見該文件頂部勘誤)。

涵蓋七張新表 + 一個既有表變更:

| 表 | 歸屬 Phase | 作用 |
|---|---|---|
| `users` | 3 | OIDC 身分落地,userid = `preferred_username` |
| `customers` | 3 | product 上層分群(客戶) |
| `products.customer_id` | 3 | 既有表加欄 |
| `role_grants` | 3 | 權限指派(個人/部門 × 角色 × 範圍) |
| `sessions` | 3 | server-side session(BFF cookie) |
| `audit_log` | 3 | 簽核/授權/解鎖/範本增刪改 紀錄 |
| `product_edit_locks` | 3 | product 級悲觀編輯鎖 |
| `jobs` | 2 | DB-backed job queue(多 replica 前置) |

慣例沿用現有 schema:TEXT uuid 主鍵、REAL epoch 時間戳、snake_case。
DDL 以 SQLite 方言書寫(現行 store 直接可用),MariaDB 方言差異集中在 §8。

---

## 1. users

```sql
CREATE TABLE IF NOT EXISTS users (
    userid        TEXT PRIMARY KEY,   -- = JWT preferred_username(2026-06-11 硬性決策)
    oidc_sub      TEXT,               -- 留存以防 preferred_username 變動時可追
    email         TEXT,
    name          TEXT,               -- JWT name
    deptid        TEXT,               -- ↓ 四欄每次登入自 JWT 刷新
    deptname      TEXT,
    company       TEXT,
    twsitecode    TEXT,
    created_at    REAL NOT NULL,
    last_login_at REAL
);
CREATE INDEX IF NOT EXISTS idx_users_deptid ON users(deptid);
```

- 首登 upsert 建列(**不**附帶任何 grant);每次登入刷新 `deptid/deptname/email/name/last_login_at` → 換部門者,部門 grant 自動跟動。
- JWT 其餘欄位(supervisorid/location/description)**不存**,要用再加欄。
- 離職:Keycloak 停用帳號 → 無法再登入;既有 session 由 §5 的存活上限兜底。

## 2. customers + products.customer_id

```sql
CREATE TABLE IF NOT EXISTS customers (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    created_at  REAL NOT NULL
);

ALTER TABLE products ADD COLUMN customer_id TEXT NOT NULL DEFAULT 'uncategorized';
```

- customer = product 的上層分群(一 customer 多 product)。**口語的「library」≠ 這層**——code/文件一律 customer,library 留給「版本範本庫」。
- app 啟動時冪等 seed 一列 `('uncategorized', '未分類', …)`;`DEFAULT 'uncategorized'` 讓既有 dev 資料與漏帶參數的建立動作都有落點。建 product 僅 admin,API 要求指定 customer。
- **customer 的建/刪/改名一律僅 admin**(2026-06-11 補定案):customer 是 customer-scope grant 的指向對象,動 customer = 動授權結構,與「assign grants 僅 admin」同層。
- 刪 customer:僅 admin 且**底下無 product 時**才可刪(RESTRICT 語意,API 層擋)。

## 3. role_grants

```sql
CREATE TABLE IF NOT EXISTS role_grants (
    id           TEXT PRIMARY KEY,
    grantee_type TEXT NOT NULL CHECK (grantee_type IN ('user','dept')),
    grantee_id   TEXT NOT NULL,      -- userid 或 deptid
    role         TEXT NOT NULL CHECK (role IN ('admin','editor','viewer')),
    scope_type   TEXT NOT NULL CHECK (scope_type IN ('global','customer','product')),
    scope_id     TEXT NOT NULL DEFAULT '',  -- '' = global(哨兵,見下)
    granted_by   TEXT NOT NULL,      -- userid
    granted_at   REAL NOT NULL,
    UNIQUE (grantee_type, grantee_id, role, scope_type, scope_id),
    CHECK ((scope_type = 'global') = (scope_id = ''))
);
CREATE INDEX IF NOT EXISTS idx_grants_grantee ON role_grants(grantee_type, grantee_id);
CREATE INDEX IF NOT EXISTS idx_grants_scope ON role_grants(scope_type, scope_id);
```

- **`scope_id` 用 `''` 表 global、不用 NULL**:SQLite 與 MariaDB 的 UNIQUE 都視 NULL 互不相等,NULL 會讓重複的 global grant 鑽進來;哨兵讓防重複交給 DB。
- **DB 層只擋形狀,語意規則在 API 層擋**(deptgrant 之後若開放 editor 不用動表):
  - `role='admin'` ⇒ 必 `scope_type='global'` 且 `grantee_type='user'`
  - `grantee_type='dept'` ⇒ 目前僅准 `role='viewer'`
  - `scope_id` 必須指向存在的 customer / product(無 FK——指向兩張表擇一,DB 表達不了)
- 部門 grant 的 `grantee_id` 允許填「尚未有人登入過的 deptid」(之後有人登入自然生效);admin UI 的下拉來源 = `SELECT DISTINCT deptid FROM users`,並允許手動輸入。
- 撤銷 = DELETE(寫 audit);不做軟刪除。

### 有效權限判定(唯一一條規則)

```sql
SELECT role FROM role_grants
WHERE (   (grantee_type = 'user' AND grantee_id = :userid)
       OR (grantee_type = 'dept' AND grantee_id = :deptid) )   -- 取自 users 當列,非 JWT 即時值
  AND (   scope_type = 'global'
       OR (scope_type = 'customer' AND scope_id = :customer_id)
       OR (scope_type = 'product'  AND scope_id = :product_id) );
```

取最高(admin > editor > viewer)。editor 即可編輯+**簽核**(含自己建的版,無 separation of duties——2026-06-11 定案)。每請求一次查詢,量級(<100 users)不需快取;真要快取也只准 per-request。

## 4. sessions

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,   -- = SHA-256(cookie token) hex;DB 不落明文 token
    userid       TEXT NOT NULL,
    created_at   REAL NOT NULL,
    last_seen_at REAL NOT NULL,      -- idle timeout 用
    expires_at   REAL NOT NULL,      -- 絕對上限(created_at + 24h)
    csrf_token   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user    ON sessions(userid);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
```

- cookie 帶 256-bit 隨機 token(HttpOnly, SameSite=Lax, Secure 由 `COOKIE_SECURE` 控);DB 存其 SHA-256,撞庫拿不到可用 session。
- 存活:**idle 8h**(`last_seen_at` 超過即失效,寫入頻率每分鐘至多一次防熱寫)、**絕對 24h**(`expires_at`)。離職者最壞 24h 內失效。
- 變更類請求驗 `csrf_token`(header `X-CSRF-Token` 對表);GET 不驗。
- 清理:每日 prune `expires_at < now`(掛在 worker 或啟動時)。
- 不做 Keycloak back-channel logout(內部工具,上限兜底即可)。

## 5. audit_log

```sql
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          REAL NOT NULL,
    actor       TEXT NOT NULL,       -- userid;系統動作用 'system'
    action      TEXT NOT NULL,       -- 動詞,見下表
    product_id  TEXT,                -- 便宜過濾用的冗餘 context(可 NULL)
    version_id  TEXT,
    target_type TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    detail      TEXT                 -- JSON(前後值、grant 內容…)
);
CREATE INDEX IF NOT EXISTS idx_audit_at      ON audit_log(at);
CREATE INDEX IF NOT EXISTS idx_audit_target  ON audit_log(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_audit_product ON audit_log(product_id);
```

必記動作(2026-06-10/11 兩日定案的聯集):

| action | 觸發 |
|---|---|
| `version.sign_off` / `version.unsign` | 簽核;解簽核僅 admin |
| `grant.create` / `grant.revoke` | 權限指派/撤銷 |
| `lock.force_release` | admin 強制解鎖 |
| `user.first_login` | 首登自動建帳 |
| `product.create` / `product.delete`、`customer.create` / `customer.delete` | 容器增刪 |
| `template.add` / `template.delete` / `template.modify`、`class.strategy_change` | library 內容增刪改(§6 既有定案) |

rules 異動**不在此**——app 無 rules 編輯功能,rules 走 code/git(2026-06-11 定案)。

## 6. product_edit_locks(2026-06-10 既有定案,落成表)

```sql
CREATE TABLE IF NOT EXISTS product_edit_locks (
    product_id   TEXT PRIMARY KEY,
    held_by      TEXT NOT NULL,      -- userid
    acquired_at  REAL NOT NULL,
    heartbeat_at REAL NOT NULL
);
```

- 參數沿用定案:**heartbeat 30s、TTL 300s**。鎖有效 ⇔ `heartbeat_at > now − 300`。
- 取鎖(顯式「開始編輯」按鈕):
  1. `INSERT` 成功 → 取得;
  2. PK 衝突 → `UPDATE … SET held_by=:me, acquired_at=:now, heartbeat_at=:now WHERE product_id=:p AND heartbeat_at < :now-300`,rowcount=1 → 搶下殭屍鎖,0 → 顯示「{held_by} 編輯中」轉唯讀。
  3. 兩步都是單句原子操作,兩 replica 同搶不會雙贏。
- 釋放:本人 DELETE;admin 強制 DELETE + `lock.force_release` audit。
- 寫入類 API 的 dependency 順序:**先驗 role(editor on P)→ 再驗鎖(held_by = me)**;背景 job 不驗鎖(屬觸發者的鎖內動作,既有定案)。

## 7. jobs(Phase 2;取代 `jobs.py` 的 in-memory `_jobs` dict)

```sql
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,      -- discover|preprocess|save_match|rule_check|reprocess_all
    status       TEXT NOT NULL,      -- queued|running|done|error
    version_id   TEXT,
    file_id      TEXT,
    product_id   TEXT,               -- rule_check 用
    parent_id    TEXT,               -- reprocess_all 子 job → 父 job id
    payload      TEXT NOT NULL,      -- JSON:worker 參數(selected_layers、overrides snapshot、layout…)
    result       TEXT,               -- JSON:今日 job dict 的 result 原樣
    error        TEXT,
    total        INTEGER,            -- 父 job 進度(reprocess_all)
    done         INTEGER,
    attempts     INTEGER NOT NULL DEFAULT 0,
    submitted_by TEXT,               -- userid
    submitted_at REAL NOT NULL,
    started_at   REAL,
    completed_at REAL,
    claimed_by   TEXT,               -- worker 身分:hostname:pid
    heartbeat_at REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_queue    ON jobs(status, submitted_at);
CREATE INDEX IF NOT EXISTS idx_jobs_binding  ON jobs(kind, version_id, file_id, status);
CREATE INDEX IF NOT EXISTS idx_jobs_parent   ON jobs(parent_id);
```

### 認領協定(SQLite / MariaDB 通用的樂觀認領)

```sql
-- 1. 挑一個候選(不上鎖)
SELECT id FROM jobs WHERE status='queued' ORDER BY submitted_at LIMIT 1;
-- 2. 原子搶占;rowcount=0 表示被別人搶走 → 回到 1
UPDATE jobs SET status='running', claimed_by=:w, started_at=:t, heartbeat_at=:t,
                attempts=attempts+1
 WHERE id=:id AND status='queued';
```

- 刻意**不用** `FOR UPDATE SKIP LOCKED`:MySQL/MariaDB 禁止 UPDATE 子查詢引用同表,且 SQLite 根本沒有——上面兩步協定在兩引擎行為一致,≤10 併發遠用不到 SKIP LOCKED 的吞吐。
- worker 跑長任務期間每 30s `UPDATE … SET heartbeat_at=:now`。
- **回收**(worker 迴圈每分鐘掃):`status='running' AND heartbeat_at < now−120` → `attempts < 3` 改回 `queued`(worker 全冪等:重寫 artifact 無害),否則 `error`。pod 被 k8s 滾掉的 job 由此復活。
- 去重(`find_inflight_preprocess_job` 的替代):同 `(kind, version_id, file_id)` 有 `queued|running` 列 → 409,走 `idx_jobs_binding`。
- 進度:`reprocess_all` 子 job 完成時 `UPDATE jobs SET done=done+1 WHERE id=:parent`(原子),前端照舊輪詢父 job。
- 輪詢 API `GET /api/jobs/{id}` 改 SELECT——任一 replica 可答。
- 保留:`done|error` 列 7 天後 prune(同 §4 清理排程)。

## 8. MariaDB 方言對照(Phase 1 遷移用)

| SQLite(本文 DDL) | MariaDB |
|---|---|
| `TEXT` 主鍵 / 一般欄 | `VARCHAR(64)`(uuid/id)、`VARCHAR(255)`(userid/email/name 等)、`TEXT`(detail/payload/result/error) |
| `REAL` epoch 時間戳 | `DOUBLE`(維持 epoch 慣例,不改 DATETIME——避免時區/精度兩套語意) |
| `INTEGER PRIMARY KEY AUTOINCREMENT` | `BIGINT AUTO_INCREMENT PRIMARY KEY` |
| JSON-in-TEXT | `LONGTEXT`(不用 JSON 型別,維持兩引擎同款讀寫路徑) |
| `CHECK (… IN (…))` | 同語法可用(MariaDB 10.2+ 會真的執行) |
| — | 建表一律 `ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci` |
| `BEGIN`(預設 DEFERRED) | 交易內讀寫,connection pool `pool_pre_ping=True` |

UNIQUE 索引上限:MariaDB utf8mb4 下 `VARCHAR(255)` 單欄 1020 bytes,`role_grants` 的四欄複合 UNIQUE 需把 `grantee_id/scope_id` 控在 `VARCHAR(64)`、`role/grantee_type/scope_type` 用 `VARCHAR(16)`,合計遠低於 3072-byte 限制(InnoDB DYNAMIC)。

## 9. 一致性備忘

- 新表建立沿用現行模式:各 store 模組頂層 `*_SCHEMA` 常數 + `CREATE TABLE IF NOT EXISTS`,並掛 `ensure_versioned_schema` 之後。Phase 1 引入 Alembic 後,**新表改由 migration 建**,`*_SCHEMA` 常數僅供測試 in-memory DB 使用。
- 權限 dependency 與簽核 guard 在同一層(現行 sign-off guard 位置),`SMDR2_DEV_USER` 是被 session 身分取代的那個點(`app/main.py:88`)。
- 測試 fixture:注入 `(userid, deptid, grants)` 假身分,繞過 BFF;dev 模式 `AUTH_BYPASS=1` 時以 `SMDR2_DEV_USER` 為 admin。
```
