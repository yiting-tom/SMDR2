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
- [x] **已定案（2026-06-10）：唯一識別用 `sub`**（穩定不可變）；`email` / `preferred_username` 只做顯示。隨 §2「App DB 存權限」模型一起定。
- [ ] 用**現有 realm** 還是新開一個給這工具？client 註冊要走什麼流程？
- [ ] 有沒有強制 **MFA / session timeout / 登出**規範要遵守？
- [ ] token 過期續期（refresh）由工具自管還是靠 Keycloak session？

### §2 角色歸屬（誰存權限）
- [x] **已定案（2026-06-10）：Keycloak 只提供登入（authentication），不管授權。**
- [ ] **授權層待定：公司有自有 Authorization 系統（A4 系統）**，App DB 自管 vs 接 A4 尚未定。要先釐清三件事：
  1. **政策**：內部工具**強制**走 A4 管權限，還是可自管？
  2. **介接方式**：A4 是 API 即時查？定期同步？還是往 token 裡塞 claim？
  3. **粒度**：A4 能不能表達「**per-product editor**」這種資源級指派，還是只有粗角色（admin/editor/viewer）？
  - 若 A4 只有粗角色 → 建議**混合制**：A4 管粗角色，App DB 留 `product_editors(product_id, user_sub)` 管細指派（這張表本來就最適合放 app 端）。
  - 若可自管 → 維持原建議：兩張小表全放 App DB（`user_roles` + `product_editors`），能登入即 viewer。
  - 不論哪條路：離職/轉組靠 Keycloak 停用帳號自動失效（§6 第一題跟著解）。

### §3 Editor 的編輯範圍（最關鍵，先定）
- [ ] editor 的「edit」**具體包含什麼**？上傳檔、commit 範本、rule-check…是否也含改 class 策略這種 **library 級**設定？
- [ ] 一個 editor 會被指派到**幾個** product？一對一還是一對多？
- [ ] editor 能不能**自己建立新 product**，還是只能編被指派的既有 product？
- [ ] editor 能不能刪 product / 刪別人上傳的檔？

### §4 Library / Product 拓樸（決定整個模型，先定）
- [x] **已定案（2026-06-10）：library 跟著 product 走；同日因 versioning 精煉為「一 version 一 library」（路線 1）。** product 是 version 的容器，每個 version 1:1 擁有自己的 library（templates + match 調參）；product 之間照樣完全不共用。library 級寫入風險自然消失；**編輯鎖維持 product 級**（§7 不變，鎖住 product = 鎖住其下所有 version）。
  - 快照語意：建新版 = **clone 上一版的 library**；match 調參跟著快照 → v2 調參不影響 v1，**舊版結果可重現**。
  - schema：`templates` / `classes` 不動（本來就掛 `library_id`），只加 `versions(id, product_id, label, library_id, created_at)`。
- [x] **已定案（2026-06-10）：不再有任何共用範本——新 product 空白開始（選項 c）。** 標準件（SMD-2T/Fiducial 等）每個 product 自己框選、match 調參自己調，不從 seed 或既有 product 複製。
  - 影響：`PRODUCT_SCOPED_CLASSES` 兩層 scope 區分**整個消失**（所有 class 一律 product 範圍）；`load_library()` 的雙 scope merge 可刪；「共用範本誰能改」一題**蒸發**（沒有共用範本了）。
  - 既有累積的 library-scoped 範本（SMD-2T 907、FiducialCircle 421）多為 dev/測試殘留，遷移時不保留為共用資產（細節留給 OpenSpec change）。
- [ ] 不同 product 之間要**完全隔離**（editor 看不到別人 product），還是「viewer 都能看全部、只是不能編」？editor 的「看」範圍是否也全開？

### §5 Admin 與啟動
- [ ] **第一個 admin** 怎麼產生？（隨 §2 授權層一起定）若權限自管 → **env 白名單**（如 `SMDR2_ADMIN_EMAILS=a@x,b@x`，零 bootstrap、套既有 env 慣例）；若走 A4 → admin 直接由 A4 指派，這題消失。
- [ ] admin 除了指派 editor，要不要能**撤銷** editor、**改別人**的 product 指派、查誰有什麼權限？
- [ ] 需不需要**多個 admin**？admin 能不能指派其他 admin？

### §6 邊界與營運
- [ ] **離職 / 轉組**：權限怎麼收？靠 Keycloak 停用帳號自動失效，還是 App 要另外清？
- [ ] 要不要 **audit log**（誰在何時改了什麼）？合規上是否必要？
- [ ] **未登入**怎麼處理——直接導去 Keycloak，還是保留公開唯讀頁？
- [ ] 內網 vs 外網存取限制？只能公司網段用嗎？
- [ ] 上線權限後，**既有資料**（現在無隔離）要不要回溯指派 owner / product 歸屬？

### §7 多用戶併發與編輯鎖定

現況是**單用戶假設**：兩個人同時改同一個 product，會 last-write-wins、後者默默覆蓋前者，沒有任何提示。多人上線後一定會踩到。對「編輯」走 **pessimistic lock（編輯鎖）**，對「查看」完全不擋（viewer 永遠不被鎖影響）。

理由：編輯是「一個操作員把一個 product 從上傳到 commit 範本」的**長時間連續互動（數十分鐘）**。樂觀鎖（version / ETag）只在**存檔當下**才偵測衝突 → 操作員可能做很久才被擋下重做；悲觀鎖**進場就擋**，第二人一進來就看到「X 正在編輯，唯讀」，不白做工。

#### 已定案
- **鎖定粒度 = `product` 級**：同一 product 同時只有一個 editor 能編。工作的真實單位就是 product；file / template 級太細，擋不到「共用 product 狀態（match JSON、範本集合）」的衝突。
- **策略 = 悲觀鎖（編輯 session）**：進編輯先搶鎖，他人唯讀。
- **取得方式 = 明確「開始編輯」按鈕 + 顯示鎖持有者**：不走隱式上鎖。UI 要清楚顯示「誰、從何時起正在編輯這個 product」，讓佔鎖者有自覺、被擋者知道在等誰。
- **殭屍鎖 = heartbeat + TTL 自動過期**：開著編輯頁時定期 heartbeat；沒人續 → 鎖自動失效（關分頁 / 斷線 / 當機不留死鎖）。(間隔與 TTL 數字見待討論 ④)
- **admin 強制解鎖**：admin 可搶走 / 釋放別人的鎖，留一行 audit。應付「卡死又等不及」。
- **背景 job 涵蓋在 product 鎖內**：`jobs.py` 的 preprocess / scan-all 是該 editor 動作觸發的，天然屬於他的 product 鎖；job 寫的是衍生資料（match JSON、預覽）、冪等，收尾晚一點無妨。鎖只需保證「同一 product 同時只有一個**人類**寫入者」。
- **不需即時共編**：內部工具，「被擋住 + 顯示鎖狀態 + 手動重整（或輕量輪詢更新鎖狀態）」即可，不上 WebSocket 即時協作。

#### 待討論
- ③ **該 product 的 editor、但鎖被別人佔住時**怎麼辦？純唯讀等他放，還是給「**請求接手 / 通知 admin**」？
- ④ **heartbeat 間隔與鎖 TTL** 取多少？取決於你們會不會編到一半離開很久（開會、查資料）。太短 → 正常 idle 被誤踢；太長 → 殭屍鎖卡很久。（初步候選：heartbeat 30s、TTL 2–5 分鐘，待確認你們作業節奏）

#### 相依
- **鎖粒度與 §4 拓樸相依**：editor 只能編自己的 product，鎖也只在該 product 範圍競爭；若改成「一 product 一 library」，鎖邊界會直接對齊 library。
- **SQLite 寫入併發（與鎖無關、但必做）**：DB 是 SQLite（單寫入者），多人一上來就要設 **WAL + `busy_timeout`**，否則併發寫直接 `database is locked`。與 [docs/production-storage.md](production-storage.md) 的 DB 規劃一起看。

---

## 建議

討論時**先定 §3（editor 範圍）+ §4（library 拓樸）**——這兩個一旦定了，其餘大多會跟著收斂。**§7（併發 / 鎖定）的粒度也會跟著 §4 一起定**（鎖通常對齊 product 或 library 邊界）。定案後再 propose-first 收成 OpenSpec change。
