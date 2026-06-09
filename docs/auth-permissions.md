# 使用者權限管理與 SSO 串接規劃（Keycloak / RBAC）

> 狀態：**討論中、尚未實作**。本文是決策文件（design / open questions），不是施工單。
> 最後更新：2026-06-09。
> 待下方 §3、§4 兩組問題定案後，才會收斂成 OpenSpec change（propose-first）。

本文回答一個問題：**SMDR2 要加上「非常簡單」的三級權限（Admin / Editor / Viewer），並串接公司 Keycloak SSO 時，模型怎麼設計、有哪些待釐清的決策。**

---

## 0. 現況

- 目前**完全沒有 auth**，所有 endpoint 全開，資料無使用者隔離。
- 寫入操作橫跨三種 scope：
  - **product 級** — `/api/products/{id}/…`（建立 / 刪除 product、上傳檔、rule-check）
  - **file 級** — `/api/files/{id}/…`（patch、unit-override、layers、layouts、match、commit…），file 綁定某 product 或未綁定
  - **library 級** — `PUT /api/libraries/{id}/classes/{name}/strategy`、`DELETE|PATCH /api/templates/{id}`（library-scoped 範本是跨 product 共用）
- 現在是「**一個共用 library 裝多個 product**」：library-scoped 與 product-scoped 範本並存（見 `app/library.py` `PRODUCT_SCOPED_CLASSES`）。

## 1. 需求（來自討論）

- 三種身分：**Admin / Editor / Viewer**。
- **Admin** 可以 assign editor。
- **Editor** 可以編輯，但**只能編特定 product**。
- **Viewer** 可以查看——**只要能登入就能看**。
- 公司有 **Keycloak**，自己的 SSO 需要串接。
- 範圍刻意「非常簡單」，不要過度設計。

## 2. 初步建議方向（待確認，非定案）

- **切分：Keycloak 認證、App 授權。**
  - Keycloak 只做 authentication（你是誰，OIDC）。
  - 角色 / 權限放 **App 自己的 DB**（你能做什麼）。
  - 原因：「editor 只能編特定 product」是 per-product 粒度，Keycloak realm role 無法乾淨表達。
- **Viewer = 任何登入成功的人**（免 DB row），對上「只要能登入就能查看」。
- **資料模型（極簡，一張表）**
  ```
  user_grants(subject, role, product_id)
    admin  → role='admin',  product_id=NULL          # 全域
    editor → role='editor', product_id=<某 product>  # 一個 product 一列
    viewer → 沒有任何 row（隱含）
  ```
- **認證流程**：本專案是 server-render dashboard + fetch，建議 **OIDC authorization-code flow → server-side session cookie**，不要把 bearer token 放前端 JS。驗證用 Keycloak `.well-known` + JWKS。
- **強制點（FastAPI dependency）**：讀取 → `require_authenticated`；寫入 → `require_editor(product_id)` 或 `require_admin()`；指派 editor → `require_admin()`。
- **⚠️ 最大風險：library 級操作**。class 策略、library-scoped 範本是跨 product 共用的；product X 的 editor 改到它就動到別人。解法見 §4。

---

## 待釐清問題（帶去討論）

### §1 身分與 SSO（Keycloak）
- [ ] Keycloak 的**唯一識別**用哪個？`sub`（穩定不可讀）vs `email` / `preferred_username`（可讀但可能變）。
- [ ] 用**現有 realm** 還是新開一個給這工具？client 註冊要走什麼流程？
- [ ] 有沒有強制 **MFA / session timeout / 登出**規範要遵守？
- [ ] token 過期續期（refresh）由工具自管還是靠 Keycloak session？

### §2 角色歸屬（誰存權限）
- [ ] 角色放 **App DB** 還是 **Keycloak**？（per-product editor 放 Keycloak 會很痛）
- [ ] 公司有沒有「權限必須集中在 Keycloak / IAM 管、不能應用自管」的政策？

### §3 Editor 的編輯範圍（最關鍵，先定）
- [ ] editor 的「edit」**具體包含什麼**？上傳檔、commit 範本、rule-check…是否也含改 class 策略這種 **library 級**設定？
- [ ] 一個 editor 會被指派到**幾個** product？一對一還是一對多？
- [ ] editor 能不能**自己建立新 product**，還是只能編被指派的既有 product？
- [ ] editor 能不能刪 product / 刪別人上傳的檔？

### §4 Library / Product 拓樸（決定整個模型，先定）
- [ ] 維持「**一個共用 library 裝多 product**」，還是改成「**一 product 一 library**」（讓 library 邊界 = product 邊界，library 級風險自然消失）？
- [ ] library-scoped 共用範本誰能改？只有 admin，還是不開放編輯？
- [ ] 不同 product 之間要**完全隔離**（editor 看不到別人 product），還是「viewer 都能看全部、只是不能編」？editor 的「看」範圍是否也全開？

### §5 Admin 與啟動
- [ ] **第一個 admin** 怎麼產生？env 白名單 email vs Keycloak 一個 `admin` role（bootstrap）。
- [ ] admin 除了指派 editor，要不要能**撤銷** editor、**改別人**的 product 指派、查誰有什麼權限？
- [ ] 需不需要**多個 admin**？admin 能不能指派其他 admin？

### §6 邊界與營運
- [ ] **離職 / 轉組**：權限怎麼收？靠 Keycloak 停用帳號自動失效，還是 App 要另外清？
- [ ] 要不要 **audit log**（誰在何時改了什麼）？合規上是否必要？
- [ ] **未登入**怎麼處理——直接導去 Keycloak，還是保留公開唯讀頁？
- [ ] 內網 vs 外網存取限制？只能公司網段用嗎？
- [ ] 上線權限後，**既有資料**（現在無隔離）要不要回溯指派 owner / product 歸屬？

### §7 多用戶併發與編輯鎖定

現況是**單用戶假設**：兩個人同時改同一個 product / file，會 last-write-wins、後者默默覆蓋前者，沒有任何提示。加上多人上線後一定會踩到。要先想清楚兩件事——**併發策略**與**鎖定粒度**。

初步傾向（待確認）：對「編輯」走 **pessimistic lock（編輯鎖）**，對「查看」完全不擋（viewer 永遠不被鎖影響）。

- [ ] **鎖定粒度**：鎖在 **product** 級（同一 product 同時只有一個 editor 能編）、**file** 級、還是 **template** 級？粒度越粗越簡單但越擋人，越細越自由但越難正確。
- [ ] **策略選型**：
  - **悲觀鎖**（進編輯先搶鎖，他人看到「X 正在編輯，唯讀」）——直覺、衝突前就擋住，但要處理「鎖沒釋放」。
  - **樂觀鎖**（version / ETag，存檔時比版本，衝突就擋下重整）——不擋人、實作輕，但衝突發生在**存檔當下**、使用者已白做。
  - 兩者混用？
- [ ] **鎖的取得 / 釋放**：何時上鎖（開編輯頁 vs 第一次寫入）？何時釋放（離開頁、存檔、登出）？
- [ ] **stale lock（殭屍鎖）**：使用者直接關分頁、斷線、當機 → 鎖怎麼自動失效？靠 **TTL + heartbeat**？多久？
- [ ] **admin 強制解鎖**：admin 能不能搶走 / 釋放別人的鎖？要不要留紀錄？
- [ ] **背景 job 的併發**：現在 `jobs.py` 已有背景處理（preprocess / scan-all）。某 product 正在被一個 job 寫入時，另一個人的編輯 / 另一個 job 要不要互斥？鎖要不要涵蓋「人 + job」兩種寫入者？
- [ ] **即時性**：他人需要**即時看到**變更（WebSocket / SSE 推播）還是「被擋住 + 手動重整」就夠？（越即時越複雜，內部工具通常後者就夠）
- [ ] **SQLite 寫入併發**：目前 DB 是 SQLite（單寫入者）。多人同時寫，DB 層的鎖 / busy-timeout / WAL 設定要不要一起檢視？（這跟 [docs/production-storage.md](production-storage.md) 的 DB 規劃相關）
- [ ] **鎖 vs 權限的交集**：editor 只能編自己的 product，鎖自然也只在那個 product 範圍內競爭；但若改成「一 product 一 library」（§4），鎖粒度可能直接對齊 library——兩個決策會互相影響。

---

## 建議

討論時**先定 §3（editor 範圍）+ §4（library 拓樸）**——這兩個一旦定了，其餘大多會跟著收斂。**§7（併發 / 鎖定）的粒度也會跟著 §4 一起定**（鎖通常對齊 product 或 library 邊界）。定案後再 propose-first 收成 OpenSpec change。
