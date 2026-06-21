# Product 版本管理規劃（同一 product、多版號）

> 狀態:**已實作完成(2026-06-10 定案、openspec `add-product-versioning`)**。
> 本文保留為決策史(point-in-time);現行設計以根目錄 [`SYSTEM_DESIGN.md`](../SYSTEM_DESIGN.md) 為準。
> 最後更新:2026-06-12(狀態標頭)。
> 待下方 §3 的問題與 user 定案後，才會收斂成 OpenSpec change（propose-first）。

本文回答一個問題：**同一個 product 會有不同版本（圖紙小改），user 要能輸入版號 —— 資料模型怎麼設計、有哪些要先跟 user 確認的決策。**

---

## 0. 現況

- 儲存全在 `data/library.sqlite`，階層 `library → product → {templates, files}`。
- 相關 table：
  - `products (id, name, library_id, created_at)`
  - `templates (id, library_id, class_name, entity_point_sets[JSON], centroid, bbox, product_id, created_at)`
  - `files (id=content-hash, product_id, library_id, role∈{SBT,BD,POD,RING,LID})`
- template 有**兩層 scope**：
  - **library-scoped**（`product_id IS NULL`）— 跨所有 product 共用的標準件（如 BGABall）。
  - **product-scoped**（`product_id = X`）— 綁單一 product（Substrate / Ring / DieArea …），從該 product 圖紙框選而來。
- rule 邏輯不存 DB（外部團隊的 stub）；rule 結果存 `data/rule_check/{product_id}.json`。
- **目前完全沒有 version / snapshot 概念。**

## 1. 需求（來自討論）

- 同一個 product 會有不同版本。
- user 要能在選 product 後**輸入版號**。
- 已知事實（user 確認）：
  - **要檢查的 rules 跨版本不變。**
  - **圖紙每版只改一兩個小東西。**

## 2. 已定案（內部討論，2026-06-09）

1. **版本不是不同 product。** rules 不變 → 規則屬 product 身分層級；變的只是比對基準。做成獨立 product 會把固定 rules 複製 N 份 → drift。
2. **儲存用整組快照（方式 A）**，不做 base+diff。版本數頂多 ~20，快照最穩；要看「改了什麼」就兩版即時 diff，不預建 diff 結構。
3. **rules 掛 product、跨版共用；version 只換比對基準。** 若日後有人要「按版本改規則」→ 擋下,那要走的是開新 product。
4. **模型定案（2026-06-10，路線 1：一 version 一 library）**：
   ```
   product (身分 + 固定 rules)
     └─ version ──1:1── library (templates + match 調參)
   ```
   - product 是 version 的容器；product 之間完全不共用（無任何共用範本，新 product 空白開始——拓樸定案見 `auth-permissions.md` §4）。
   - **建新版 = clone 上一版的 library**（一個動作完成整包快照）。
   - **match 調參跟著快照**：v2 調參不影響 v1 → 舊版結果可重現。
   - schema：`templates` / `classes` **完全不動**（本來就掛 `library_id`）；只新增 `versions(id, product_id, label, library_id, created_at)`；`files` 預計改掛 version（待 C1 末段確認）；rule 結果 `{product_id}.json` → `{version_id}.json`。
   - 編輯鎖維持 **product 級**（鎖住 product = 鎖住其下所有 version）。
5. **畫押（version sign-off，2026-06-10 新需求）**：version 有兩態——**編輯中 → 已畫押**。
   - editor 完成所有動作後對 version 畫押；畫押後該版**唯讀凍結**：範本、檔案、調參、重跑全擋。
   - UI 顯示**誰、何時**畫押；**解畫押僅 admin**；畫押/解畫押皆寫 audit log。
   - schema：`versions.signed_off_by / signed_off_at`（NULL = 編輯中）。
   - 與 C4 疊加：未畫押=可編、已畫押=唯讀、任何版本都不可刪。

---

## 3. 待跟 user 討論的問題

> 以下每題都會影響 schema / UI / 遷移，請逐題拍板。

### Q1. ~~版本到底換掉什麼？~~ **已定案（2026-06-10）**
templates + match 調參隨版本走（= 該版的 library，見 §2.4）。files 的關鍵情境（user 提供）：**新版可能 SBT、BD 沿用前一版，只有 POD 是新文件** → files 必須**可跨版共用**，不能硬複製。

結構推導：
- **role 綁定從 `files` 表抽出，改用 junction：`version_files(version_id, role, file_id, …per-version 狀態)`**。`files` 退化為純內容儲存（content-hash 去重本來就支援多處引用，bytes 零重複）。
- 建新版 = clone 上一版的 library **+ 複製上一版的 role 綁定**；user 只替換有改的角色（如 POD），其餘沿用。
- per-version 狀態（selected_layers、rect、unit override 等目前長在 `files` 列上的東西）要跟著搬進 junction——同一份 bytes 在不同版本可有不同選層。
- ⚠️ **衍生 artifact 必須版本化**：`parsed/match/prematch` 目前以 `{file_id}` 為 key；v1/v2 共用同一 SBT file 但 templates 不同 → match 結果不同。若仍以 file_id 為 key，v2 重跑會**覆蓋 v1 的結果、毀掉舊版可重現性**。→ 改以 `(version_id, file_id)` 為 key（如 `match/{version_id}/{file_id}.json`）；rule 結果同理 `{version_id}.json`。精確 keying 留給 OpenSpec design。

### Q2. ~~library-scoped 標準件（共用件）確定版本無關？~~ **已蒸發（2026-06-10）**
拓樸定案「一 product 一 library、無任何共用範本、新 product 空白開始」（見 `auth-permissions.md` §4）→ 不再有 library-scoped 層 → **版本快照涵蓋整個 product library，無例外**。本題不存在了。

### Q3. ~~版號的格式與輸入規則~~ **已定案（2026-06-10）**
**自由輸入文字**、純人工（不自動遞增）；同一 product 內**不可重複**——重複時**報錯**，不覆蓋。

### Q4. ~~舊版的保留與生命週期~~ **已定案（2026-06-10）**
舊版**可回看**（match / rule 結果都要能切回去看 → 衍生 artifact 以 `(version_id, file_id)` keying，Q1 已鋪好）；舊版**不可刪除**、永久保留。UI 需要版本切換器。

### Q5. ~~建新版的起點~~ **已隨 Q1 定案（2026-06-10）**
建新版 = **clone 上一版**（library + role 綁定），user 只替換有改的角色。「SBT/BD 沿用、只換 POD」情境直接蘊含此流程。

### Q6. ~~跨版本比較需求~~ **已實作(2026-06-11)**
原定延後,user 改為立即實作。`GET /api/products/{pid}/version-diff?from=&to=`:範本以 canonical signature 比對(clone 副本不誤報)、match 調參逐類差異、檔案綁定增刪/狀態變更;dashboard 比較 modal 含範本縮圖。已畫押版本可比較(純讀取)。openspec `add-version-diff`。

### Q7. ~~新 product 的第一版~~ **已定案（2026-06-10）**
建 product 時 **user 必須輸入版號**——不自動取名 `v1`、也不存在「無版本」的空 product；第一版隨 product 建立一起生（空白開始，見拓樸定案）。

### Q8. ~~與權限模型的關係~~ **已定案（2026-06-10）**
editor 綁 **product**（能改其下所有版本）；編輯鎖維持 **product 級**，不下放到版本。

### Q9. ~~既有資料遷移~~ **已定案（2026-06-10）：不用遷移**
現有資料全是開發期產物，不保留。schema 直接上新模型，舊資料砍掉重練（dev DB 本來就被測試殘留灌爆，見 DISCUSSION.md 附註）。

---

## 4. 下一步

- **§3 全部定案（2026-06-10）✅** —— versioning 設計面完整收斂，可開 OpenSpec change（拓樸轉換 + versioning 一起、一次 schema 到位；無資料遷移負擔）。
- change 範圍預估：`versions` 表 + `version_files` junction、刪兩層 scope（`PRODUCT_SCOPED_CLASSES`/雙 scope merge）、衍生 artifact 改 `(version_id, file_id)` keying、product 建立流程加必填版號、版本切換 UI、clone-on-new-version 流程。
