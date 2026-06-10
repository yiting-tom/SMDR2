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

- [x] **A1. 已定案（2026-06-10）：一 product 一 library，library 跟著 product 走。** library 邊界 = product 邊界；library 級寫入風險消失；§D3 編輯鎖粒度自動對齊。
- [ ] **A1b.（A1 的衍生題，新增）** 共用標準件（BGABall 等）失去「共用 library」的家後放哪？(a) 特殊 global library 放標準件／(b) 建 product 時 clone 一份進 product library（之後各自獨立）／(c) 不再共用、每 product 自己框選。
- [ ] **A2.** 共用範本誰能改？只有 admin，還是不開放編輯？（取決於 A1b 選 (a)/(b)/(c)）
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

- [ ] **C1.（最關鍵）** 版本到底**換掉什麼**？(a) role-bound DXF files + 對應 product-scoped templates 一起換成一份快照／(b) 只換 files、templates 跨版沿用／(c) 其他。→ 決定 `version_id` 掛哪幾張表。
- [ ] **C2.** library-scoped 共用件確定**版本無關**（切版時完全不動）？
- [ ] **C3.** 版號格式與輸入規則：自由輸入 vs 固定格式？自動遞增 vs 人工？同 product 內**可否重複**？
- [ ] **C4.** 舊版生命週期：永久保留 vs 留最近 N 版？可否刪？要能**回看舊版的 match / rule 結果**嗎？
- [ ] **C5.** 建新版的起點：從**上一版 clone**（再改那一兩個小東西）vs 每版從零上傳全部角色檔？
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

- [ ] **E1.** Keycloak 唯一識別用 `sub`（穩定不可讀）vs `email`／`preferred_username`（可讀但可能變）？
- [ ] **E2.** 用**現有 realm** 還是新開一個給這工具？client 註冊流程？
- [ ] **E3.** 有無強制 **MFA / session timeout / 登出**規範？token 續期由工具自管還是靠 Keycloak session？
- [ ] **E4.** 角色放 **App DB** 還是 **Keycloak**？（per-product editor 放 Keycloak 會很痛。）公司有無「權限必須集中在 IAM、不能應用自管」政策？
- [ ] **E5.** **第一個 admin** 怎麼產生？env 白名單 email vs Keycloak `admin` role bootstrap。
- [ ] **E6.** 要**多個 admin** 嗎？admin 能否指派其他 admin？能否撤銷 editor / 改別人的 product 指派 / 查誰有什麼權限？

---

## §F. 營運 / 邊界
> 細節：[`auth-permissions.md` §6](auth-permissions.md)

- [ ] **F1.** **離職 / 轉組**：權限怎麼收？靠 Keycloak 停用帳號自動失效，還是 App 要另外清？
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
