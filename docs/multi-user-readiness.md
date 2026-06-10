# 多使用者支援規劃（concurrency / readiness 總覽）

> 狀態：**討論中、尚未實作**。本文是**總覽 / 導讀**，把「網頁要支援多人」拆成各個關注點，細節指向既有的兩份決策文件。
> 最後更新：2026-06-10。分支：`product-versioning`。

本文回答一個問題：**SMDR2 從「單用戶假設」走向「多人同時使用」，到底要顧哪些事、各自的解法在哪、哪些現在做哪些延後。**

---

## 0. 最關鍵的觀念：「多使用者」≠「多 replica」

- SMDR2 的 **web 層很輕**，重的是 ProcessPool 的 **CPU-bound 比對**。
- **單一 pod 配大一點 CPU + 調高 `SMDR2_MAX_WORKERS`，就能撐很多人同時用。**
- 真正逼你開「多個 app replica」的只有 **HA / 零停機需求** 或 **單機 CPU 不夠** —— 那是另一個更大、更晚的題目（見 §2）。

所以「支援多人」要拆成兩層來看：

| 層 | 是什麼 | 現在做嗎 | 工程量 |
|---|---|---|---|
| **A. 單 replica 多人** | 併發**正確性**（多人同時用同一個行程） | **要，先做** | 小 |
| **B. 多 replica** | **擴展 / HA**（多個行程） | 延後（你說「先不會」） | 大 |

> ❗ 別把兩者混在一起談 —— A 是這次的目標，B 是上 production 之後的未來牌。

---

## 1. A 層：單 replica 多人（先做，顧「併發正確性」）

多人同時打同一個行程，要顧三件事：

### 1.1 編輯衝突（最先踩到）
- **現況**：last-write-wins。兩個人同時改同一個 product，後者默默覆蓋前者，**零提示**。
- **解法（已定案）**：**product 級悲觀鎖（編輯鎖）**。進編輯先搶鎖、他人唯讀、UI 顯示「誰從何時起在編」、heartbeat + TTL 防殭屍鎖、admin 可強制解鎖。
- **細節**：見 [`auth-permissions.md` §7](auth-permissions.md)。

### 1.2 權限 / 使用者隔離
- **現況**：**完全沒有 auth**，所有 endpoint 全開、資料零隔離。多人一定要先有身分。
- **解法（規劃中）**：三級 **Admin / Editor / Viewer** + 公司 **Keycloak SSO**。editor 綁特定 product、viewer 只要能登入就能看。
- **細節**：見 [`auth-permissions.md`](auth-permissions.md)（§3 editor 範圍、§4 library 拓樸尚待定案）。

### 1.3 SQLite 寫入併發
- **現況**：SQLite 是**單寫入者**；多人同時寫不設定就會 `database is locked`。
- **解法**：開 **WAL（已開）** + 設 **`busy_timeout`**。小改，但多人上線前**必做**。
- **細節**：見 [`production-storage.md` §2.1](production-storage.md)（三個 store 各自開 WAL 的現況）。

> **A 層總結：你現在要的「多人」= 單 replica 上做「auth + 編輯鎖 + SQLite busy_timeout」三件事。**

---

## 2. B 層：多 replica（延後，顧「擴展 / HA」）

即使 blob 上了 MinIO、關聯換了 Postgres，**只要 `_jobs` 還在記憶體就不能多 replica**：

- `_jobs` 是 module-level 記憶體 dict —— job 建在 replica A 的記憶體裡。
- 狀態 poll 被 LB 導到 replica B → B 沒這個 job → **回 404**。
- → 多 replica 的**前置條件**是把 `_jobs` 外部化（存到 DB `jobs` 表或 Redis）。

**好消息**：因為「先不會」多 replica，這道牆現在**不用拆**，`_jobs` 留記憶體、單 replica 是合理選擇。

- **細節**：見 [`production-storage.md` §5](production-storage.md)（為什麼多 replica 不只是換 DB）。

---

## 3. Bucket（MinIO）對多人的角色 —— 釐清誤會

- **MinIO 解的是 blob 外部化**（DXF / JSON / SVG），**不是多人的門檻**。物件儲存天生支援併發存取。
- 它真正的用途是上 K8s 後 **pod 檔案系統是 ephemeral**，持久 blob 要外部化。
- ❌ **硬規則：`library.sqlite` 絕不能放 MinIO / NFS**（POSIX locking + 無 partial-write → 靜默壞檔）。
- **細節**：見 [`production-storage.md` §3、§10](production-storage.md)。

---

## 4. 一句話總結

| 想做的事 | 實際要動的 | 在哪份 docs |
|---|---|---|
| **多人同時用（現在）** | auth + product 編輯鎖 + SQLite `busy_timeout` | `auth-permissions.md` |
| 上 production（blob 外部化） | MinIO BlobStore（SQLite 不放 MinIO） | `production-storage.md` |
| 多 replica / HA（未來） | 先外部化 `_jobs`，再談換 DB | `production-storage.md` §5 |

**三者是獨立的三件事，按這個順序處理，不要混為一談。**

---

## 5. 下一步

- 先推進 `auth-permissions.md` 的待定案（§3 editor 範圍、§4 library 拓樸）—— 編輯鎖粒度會跟著一起定。
- `busy_timeout` 可隨 auth/鎖那支 OpenSpec change 一起收尾（同屬「多人併發」主題）。
- B 層（多 replica）維持延後，待真有 HA 需求再開。
