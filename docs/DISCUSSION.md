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
- [ ] **A3.** 不同 product 要**完全隔離**（editor 看不到別人 product），還是「viewer 看全部、只是不能編」？

---

## §B. Editor 的編輯範圍（**次先定**）
> 細節：[`auth-permissions.md` §3](auth-permissions.md)

- [ ] **B1.** editor 的「edit」**具體含什麼**？上傳檔、commit 範本、rule-check…是否也含改 class 策略這種 **library 級**設定？
- [ ] **B2.** 一個 editor 指派到**幾個** product？一對一還是一對多？
- [ ] **B3.** editor 能不能**自己建新 product**，還是只能編被指派的既有 product？
- [ ] **B4.** editor 能不能刪 product / 刪別人上傳的檔？

---

## §C. Product 版本管理
> 細節：[`product-versioning.md` §3](product-versioning.md)

- [x] **C1. 已定案（2026-06-10）：** templates + match 調參 = 該版 library；files **可跨版共用**（實例：新版 SBT/BD 沿用前版、只換 POD）→ role 綁定抽成 junction `version_files(version_id, role, file_id, …per-version 狀態)`，`files` 退化為純內容儲存（content-hash 天然去重）。建新版 = clone library + 複製綁定，只換有改的角色。⚠️ 衍生 artifact（parsed/match/prematch/rule_check）改以 `(version_id, file_id)` 為 key，否則 v2 重跑會覆蓋 v1 結果。細節見 [`product-versioning.md` Q1](product-versioning.md)。
- [x] **C2. 已隨 A1b 蒸發：** 不再有 library-scoped 共用件 → 版本快照涵蓋**整個 version library**，無例外層。
- [ ] **C3.** 版號格式與輸入規則：自由輸入 vs 固定格式？自動遞增 vs 人工？同 product 內**可否重複**？
- [ ] **C4.** 舊版生命週期：永久保留 vs 留最近 N 版？可否刪？要能**回看舊版的 match / rule 結果**嗎？
- [x] **C5. 已隨 C1 定案：** 建新版 = **clone 上一版**（library + role 綁定），user 只替換有改的角色。「SBT/BD 沿用、只換 POD」的情境直接蘊含此流程。
- [ ] **C6.** 要不要 **v1 ↔ v2 差異比較**？現在就要還是未來再加？
- [ ] **C7.** 新 product 是否**自動建第一版**（如 `v1`），還是可存在「沒有任何版本」的空狀態？
- [ ] **C8.**（連動 §B）editor 綁 **product**（能改所有版本）還是綁**特定版本**？product 編輯鎖要不要下放到版本級？
- [ ] **C9.** 既有資料遷移：全部歸到一個預設版（如 `v1`）？rule 結果 `{product_id}.json` → `{version_id}.json` 由 migration 自動搬？

---

## §D. 編輯鎖細節
> 細節：[`auth-permissions.md` §7](auth-permissions.md)（大方向已定，剩兩個參數）

- [ ] **D1.** 是該 product 的 editor、但**鎖被別人佔住**時怎麼辦？純唯讀等他放，還是給「**請求接手 / 通知 admin**」？
- [ ] **D2.** **heartbeat 間隔與鎖 TTL** 取多少？（初步候選：heartbeat 30s、TTL 2–5 分鐘，取決於你們會不會編到一半離開很久。）
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
- [ ] **F2.** 要不要 **audit log**（誰在何時改了什麼）？合規上是否必要？
- [ ] **F3.** **未登入**怎麼處理——直接導去 Keycloak，還是保留公開唯讀頁？
- [ ] **F4.** 內網 vs 外網存取限制？只能公司網段用嗎？

---

## §G. Production 儲存 / DB（**問 infra，可平行**）
> 細節：[`production-storage.md` §9](production-storage.md)

- [ ] **G1.** 公司有沒有 **PostgreSQL**（不要只有 Oracle）？→ 決定 Plan B vs C。
- [ ] **G2.** 內部工具資料能否**自管**（SQLite-on-PVC / Litestream），還是**一定要進公司 DB**？→ 決定 Plan A vs B/C。
- [ ] **G3.**（sizing 用）備份/還原期待（volume 快照 vs 需要 PITR）、最大 DXF 大小、尖峰併發人數？

---

## 收斂後的 OpenSpec change（定案後才開）

| 主題 | 預計 change | 觸發條件 |
|---|---|---|
| 權限 + 編輯鎖 + `busy_timeout` | `add-auth-and-edit-lock` | §A §B §D §E §F 定案 |
| 產品版本 | `add-product-versioning` | §C 定案（尤其 C1） |
| blob → MinIO | `add-minio-blob-backend` | §G 定案（多半 Plan A） |
| 換 DB（若政策強制） | `port-relational-store-to-postgres` | G2 = 必須進公司 DB |
| `_jobs` 外部化（多 replica） | 未來牌 | 真有 HA 需求才開 |
