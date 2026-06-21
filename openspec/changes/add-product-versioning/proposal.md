# Add Product Versioning

## Why

同一個 product(料號)會改版:每版只改一兩個小東西、部分圖紙沿用前一版,而檢查規則跨版不變。目前系統沒有任何 version 概念,新版只能覆蓋舊版資料,舊版的比對與檢查結果無法回看。2026-06-10 已完成完整設計討論並全部定案(`docs/product-versioning.md`、`docs/DISCUSSION.md` §C、`docs/system-design.md`),本 change 落地該模型。

## What Changes

- 新增 **version** 實體:product 是 version 的容器(≥1 版),每個 version **1:1 擁有自己的 library**(templates + match 調參)——「路線 1」。
- **建新版 = clone 上一版**:單交易複製 library(templates + class config)與 role 綁定;user 只替換有改的角色檔(如只換 POD、SBT/BD 沿用)。
- **role 綁定從 files 表抽出**成 `version_files(version_id, role, file_id, …per-version 狀態)` junction;`files` 退化為純 content-hash 內容儲存,bytes 跨版零重複。**BREAKING**(資料模型重構)。
- **刪除兩層 scope**:`PRODUCT_SCOPED_CLASSES` 與 `load_library()` 雙 scope merge 全部移除;不再有任何共用範本,新 product 空白開始。**BREAKING**。
- **衍生 artifact 改以 `(version_id, file_id)` 為 key**(parsed/prematch/match/layer_preview;rule 結果 `{version_id}.json`):v2 重跑不覆蓋 v1,舊版永久可回看。**BREAKING**(磁碟佈局)。
- **版號規則**:建 product 必填版號;自由輸入、同 product 內 UNIQUE(重複 409);version 不可刪除。
- **畫押(sign-off)schema 與凍結守門**:`versions.signed_off_by/signed_off_at`(NULL = 編輯中);已畫押版本所有寫入(範本、檔案、調參、重跑)在 server 端統一擋下。sign-off / unsign 端點的「身分」依賴 auth change,本次先以 dev 身分占位(無 auth 環境下可操作,介面不變)。
- **版本切換 UI**:product 頁的版本切換器(含畫押狀態徽章「誰/何時」)、建新版入口、建 product 必填版號欄位。
- **不遷移舊資料**(C9 定案):dev DB 砍掉重練,schema 直接上新模型。

## Capabilities

### New Capabilities
- `product-versioning`: version 實體與生命週期——版號規則(必填/唯一/不可刪)、一 version 一 library、clone-on-new-version、畫押凍結、衍生 artifact 的版本化 keying、版本切換 UI。

### Modified Capabilities
- `template-library`: 移除兩層 scope(`PRODUCT_SCOPED_CLASSES`/product-scoped templates/雙 scope merge);library 改為 1:1 隸屬 version;新增 library clone 操作。
- `product-files`: `(product_id, dxf_role)` 綁定模型改為 `(version_id, role)` junction;files 成為純 content-hash 內容儲存可跨版共用;per-file 狀態(選層/rect/unit override)移至 version_files。
- `design-rule-checking`: rule-check 的觸發與結果歸屬從 product 改為 version(`check_rules` 的 bundle 由該版全部角色檔組成;結果 `rule_check/{version_id}.json`)。
- `dxf-pipeline`: parsed/prematch/match/layer_preview 等衍生 artifact 路徑改以 `(version_id, file_id)` 為 key;mtime cache key 對應調整。
- `viewer-ui`: dashboard/product 頁新增版本切換器與畫押徽章;建 product 表單必填版號;建新版(clone)入口;已畫押版本的唯讀狀態呈現。

## Impact

- **DB schema**(`app/library.py`、`app/files.py`、`app/products.py`):新增 `versions`、`version_files` 表;`templates`/`classes` 不動(本來就掛 `library_id`);`files` 移除 product/role 欄位;啟動 migration 重建(無資料保留義務)。
- **API**(`app/main.py`):新增 `POST/GET /api/products/{pid}/versions`、`POST /api/versions/{vid}/files`、`POST/DELETE /api/versions/{vid}/sign-off`;`POST /api/products` 必填 `version_label`;既有 file-centric 端點(layers/match/commit/scan-all)介面不變、後端以 version 上下文解析;rule-check 端點移至 version。
- **Jobs / 儲存**(`app/jobs.py`、`app/storage.py`):path helper 全面加 version 維度;job payload 帶 version_id。
- **前端**(`app/static/`、templates):版本切換器、畫押徽章、建版/建料號表單。
- **測試**:既有 product/file/匹配測試大面積改寫(綁定模型變了);新增 version 生命週期、clone、凍結守門、keying 測試。**順帶修測試寫真實 dev DB 的問題**(隔離 DB fixture,2026-06-10 發現)。
- **不影響**:matching 演算法本體(`pattern-matching` 規格不變)、外部規則 stub 介面(`check_rules(product_id, bundle_dir)` 簽名內容物不變,bundle 組成改由 version 解析)、auth(另一支 change,等 A4/infra)。
