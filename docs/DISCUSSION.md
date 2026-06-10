# SMDR2 待討論彙總（單一討論議程）

> 狀態：**討論用議程**。本文把目前四份決策文件裡**所有待拍板的問題**整合成一張清單，依「該先定什麼」排序，方便一次跟 user / infra 談完。
> 最後更新：2026-06-10。分支：`product-versioning`。
> 每節只列**決策點**；背景與理由在各自的細節文件裡（連結附上）。定案後再 propose-first 收成 OpenSpec change。

---

## 0. 怎麼用這份文件

四個主題彼此有依賴，**建議的討論順序**就是下面 §A → §F。前面定了，後面大多會跟著收斂：

```
§A 拓樸（一 library 一 product?）──┬─► 決定 §B editor 範圍邊界
                                   ├─► 決定 §D 編輯鎖粒度
                                   └─► 決定 library 級風險還在不在
§C 版本管理 Q1（版本換掉什麼?）─────► 決定 schema 怎麼切
§G 儲存 = infra 兩題，可平行先問
```

各主題對應的細節文件：
- 權限 / SSO / 編輯鎖 → [`auth-permissions.md`](auth-permissions.md)
- 產品版本 → [`product-versioning.md`](product-versioning.md)
- 多人併發總覽 → [`multi-user-readiness.md`](multi-user-readiness.md)
- Production 儲存 / DB → [`production-storage.md`](production-storage.md)

**已定案的大方向（不再討論，僅列出讓大家有共識）：**
- 版本**不是**獨立 product；version = product 底下的 template 快照，rules 跨版共用。
- 多人併發：**product 級悲觀鎖（編輯鎖）**，viewer 不受鎖影響。
- 「多人」≠「多 replica」；先做單 replica 多人，多 replica 延後（卡在 `_jobs` 外部化）。
- blob → MinIO；`library.sqlite` 絕不放 MinIO。關聯層**傾向保留 SQLite + Litestream**（Plan A）。

---

## §A. Library / Product 拓樸（**最先定，牽動最多**）
> 細節：[`auth-permissions.md` §4](auth-permissions.md)

- [x] **A1. 已定案（2026-06-10，同日精煉）：一 version 一 library（路線 1）。** product 是 version 的容器，每個 version 1:1 擁有自己的 library（templates + match 調參）；product 間完全不共用。建新版 = clone 上一版 library；調參跟著快照 → 舊版結果可重現。schema 只加 `versions(id, product_id, label, library_id, created_at)`，`templates`/`classes` 不動。**編輯鎖維持 product 級**（D3 結論不變）。
- [x] **A1b. 已定案（2026-06-10）：不再有任何共用範本——新 product 空白開始（選 c）。** 標準件每 product 自己框、調參自己調。影響：兩層 scope 區分整個消失（`PRODUCT_SCOPED_CLASSES` 與雙 scope merge 可刪）；versioning 的 C2 跟著蒸發（快照 = 整個 product library）。
- [x] **A2. 已隨 A1b 蒸發：** 沒有共用範本，無此題。
- [x] **A3. 已定案（2026-06-10）：全部可看。** 登入即可看所有 product（含版本與結果），只是不能編。無另外的可見性模型。

---

## §B. Editor 的編輯範圍（**次先定**）
> 細節：[`auth-permissions.md` §3](auth-permissions.md)

- [x] **B1. 已定案（2026-06-10）：全部動作。** editor 在被指派的 product 內可：上傳/換檔、範本增刪改、match 調參、跑 rule-check、**建新 version**。（路線 1 後 library 屬於 version，無跨 product 風險。）
- [x] **B2. 預設一對多**（一個 editor 可被指派多個 product；未被明確反對，採常理預設）。
- [x] **B3. 已定案（2026-06-10）：建新 product 只有 admin；editor 只能在被指派的 product 下建新 version。**
- [x] **B4. 已定案（2026-06-10）：刪 product 只有 admin。** editor 在**未畫押**的 version 內可自由刪/換檔案與範本（屬 B1 全部動作；有 audit log 兜底）。
- [x] **B5.（新需求，2026-06-10）畫押（version sign-off）：** editor 完成所有動作後可對 version **畫押**；畫押後該 version **唯讀凍結**（範本、檔案、調參、重跑全擋），UI 顯示**誰、何時**畫押；**解畫押只有 admin 能做**，畫押/解畫押都寫 audit log。schema：`versions` 加 `signed_off_by, signed_off_at`（NULL = 編輯中）。

---

## §C. Product 版本管理
> 細節：[`product-versioning.md` §3](product-versioning.md)

- [x] **C1. 已定案（2026-06-10）：** templates + match 調參 = 該版 library；files **可跨版共用**（實例：新版 SBT/BD 沿用前版、只換 POD）→ role 綁定抽成 junction `version_files(version_id, role, file_id, …per-version 狀態)`，`files` 退化為純內容儲存（content-hash 天然去重）。建新版 = clone library + 複製綁定，只換有改的角色。⚠️ 衍生 artifact（parsed/match/prematch/rule_check）改以 `(version_id, file_id)` 為 key，否則 v2 重跑會覆蓋 v1 結果。細節見 [`product-versioning.md` Q1](product-versioning.md)。
- [x] **C2. 已隨 A1b 蒸發：** 不再有 library-scoped 共用件 → 版本快照涵蓋**整個 version library**，無例外層。
- [x] **C3. 已定案（2026-06-10）：** 版號**自由輸入**、純人工；同 product 內**不可重複**（重複報錯，不覆蓋）。
- [x] **C4. 已定案（2026-06-10）：** 舊版**可回看**（match / rule 結果都要能看 → artifact 以 `(version_id, file_id)` keying 已在 C1 鋪好）；舊版**不可刪除**、永久保留。
- [x] **C5. 已隨 C1 定案：** 建新版 = **clone 上一版**（library + role 綁定），user 只替換有改的角色。「SBT/BD 沿用、只換 POD」的情境直接蘊含此流程。
- [x] **C6. 已實作(2026-06-11):** 版本差異比較完成 —— `GET /api/products/{pid}/version-diff?from=&to=`(範本 signature 比對/調參/綁定三區)+ dashboard「🔍 比較」modal。openspec `add-version-diff`。
- [x] **C7. 已定案（2026-06-10）：** 建 product 時 **user 必須輸入版號** —— 不自動取名 `v1`、也不存在「無版本」的空 product；第一版隨 product 建立一起生。
- [x] **C8. 已定案（2026-06-10）：** editor 綁 **product**（能改其下所有版本）；編輯鎖維持 product 級，不下放。
- [x] **C9. 已定案（2026-06-10）：不用遷移** —— 現有資料全是開發期產物，不保留。schema 直接上新模型，舊資料砍掉重練。

---

## §D. 編輯鎖細節
> 細節：[`auth-permissions.md` §7](auth-permissions.md)（大方向已定，剩兩個參數）

- [x] **D1. 已定案（2026-06-10）：(c)** 被佔鎖就唯讀等待；急用走「找 admin 強制解鎖」（既有定案功能），不做「請求接手」通知機制。
- [x] **D2. 已定案（2026-06-10）：heartbeat 30s、TTL 5 分鐘。** 開會情境分析：分頁開著 → heartbeat 持續 → 鎖不掉（人還在編輯狀態）；筆電休眠/關分頁 → heartbeat 停 → 5 分鐘後鎖自動釋放，回來再搶即可（被人搶走就等或找 admin，符合 D1）。
- [x] **D3. 已隨 A1 定案：** 一 product 一 library → 鎖粒度 product = library，自動對齊，無需再議。

---

## §E. 身分 / SSO / 角色歸屬
> 細節：[`auth-permissions.md` §1、§2、§5](auth-permissions.md)

- [x] **E1. 已定案（2026-06-10）：** 唯一識別用 `sub`；email/username 只做顯示。
- [ ] **E2.** 用**現有 realm** 還是新開一個給這工具？client 註冊流程？（問 infra）
- [ ] **E3.** 有無強制 **MFA / session timeout / 登出**規範？token 續期由工具自管還是靠 Keycloak session？（問 infra）
- [x] **E4a. 已定案（2026-06-10）：Keycloak 只提供登入（authentication），不管授權。**
- [ ] **E4b.（新增）授權層：公司有自有 Authorization 系統「A4」**，App DB 自管 vs 接 A4 待定。要問：①政策上強制走 A4 嗎？②介接方式（API 即時查／同步／token claim）？③A4 撐不撐得起 per-product editor 粒度？若 A4 只有粗角色 → 建議混合制：A4 管粗角色、App DB 留 `product_editors` 細指派。
- [ ] **E5.** 第一個 admin 怎麼產生？若權限自管 → env 白名單（`SMDR2_ADMIN_EMAILS`）；若走 A4 → admin 直接由 A4 給，這題消失。（隨 E4b 定）
- [ ] **E6.** 要**多個 admin** 嗎？admin 能否指派其他 admin？能否撤銷 editor / 改別人的 product 指派 / 查誰有什麼權限？

---

## §F. 營運 / 邊界
> 細節：[`auth-permissions.md` §6](auth-permissions.md)

- [x] **F1. 已隨 E4 定案：** 靠 Keycloak 停用帳號自動失效（登不進來 = 權限死），App 不用另外清。
- [x] **F2. 已定案（2026-06-10）：要。** 至少記錄 **library 內容的增刪改**：哪個 editor、在哪個 product（含版本）、對 templates / match 調參做了 add / delete / modify。雛形：`audit_log(id, ts, user_sub, product_id, version_id, action, target_type, target_id, detail)`。是否擴及其他動作（上傳檔、建 product、rule-check 觸發…）實作時再議，表結構先留通用。
- [x] **F3. 已定案（2026-06-10）：** 無論如何**強制先登入**——未登入一律導去 Keycloak SSO login，不留公開頁。
- [x] **F4. 已定案（2026-06-10）：** 部署環境是**全封閉網路**，無外網存取考量（TLS/暴露面不在 app 範圍）。

---

## §G. Production 儲存 / DB（**問 infra，可平行**）
> 細節：[`production-storage.md` §9](production-storage.md)

- [x] **G1. 已確認（2026-06-10）：** 公司 DB 是 **Oracle**（無 Postgres）。
- [x] **G2. 已確認（2026-06-10）：** 內部工具資料**可以自管**。
- [x] **G3. 已確認（2026-06-10）：** 同時最多 **10 人**、總用戶 **<100**、單一 DXF 最大 **150MB**、每年 **<500 個** DXF。
- [x] **→ 路線定案：Plan A**（blob → MinIO、SQLite 保留 + Litestream→MinIO 備份、單 replica）。Oracle port（Plan C）因 G2 可自管而整個避開。⚠️ G3 的 150MB 單檔需要：上修上傳限制（`SMDR2_MAX_UPLOAD_MB` ≥ 200）＋ 驗證 parser/worker 對 150MB DXF 的記憶體行為（現有資料最大才 ~34MB 總量級）。儲存量級：500/年 × 最壞 150MB ≈ 75GB/年上限，MinIO 輕鬆；併發 10 人單 replica + ProcessPool 足夠。

---

## 收斂後的 OpenSpec change（定案後才開）

| 主題 | 預計 change | 觸發條件 |
|---|---|---|
| 權限 + 編輯鎖 + `busy_timeout` | `add-auth-and-edit-lock` | §A §B §D §E §F 定案 |
| 產品版本 | `add-product-versioning` | §C 定案（尤其 C1） |
| blob → MinIO | `add-minio-blob-backend` | §G 定案（多半 Plan A） |
| 換 DB（若政策強制） | `port-relational-store-to-postgres` | G2 = 必須進公司 DB |
| `_jobs` 外部化（多 replica） | 未來牌 | 真有 HA 需求才開 |
