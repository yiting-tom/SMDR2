# Tasks — add-product-versioning

## 1. 測試隔離與基礎設施(先做,後面每步都靠它驗證)

- [x] 1.1 新增 `SMDR2_DATA_DIR` env:`app/storage.py` 與三個 store 的路徑來源改由 env 解析(預設 `data/`,行為不變)
- [x] 1.2 pytest fixture:`tmp_path` 注入 `SMDR2_DATA_DIR`,app/store singleton 可重建;既有測試跑在隔離 DB 上(不再寫 `data/library.sqlite`)
- [x] 1.3 新增 `scripts/reset_dev_data.py`:清空 sqlite + `data/` 衍生目錄

## 2. Schema 與 store 層

- [x] 2.1 schema 重建邏輯:偵測缺 `versions` 表 → drop & recreate 全部表(含新 `versions`、`version_files`;`files` 去綁定欄位;`templates` 去 `product_id`;`products` 去 `library_id`)
- [x] 2.2 `VersionStore`(或併入 `ProductStore`):create(含 UNIQUE(product_id,label) → 409 語意)、get、list_by_product、sign_off/unsign
- [x] 2.3 `version_files` CRUD:bind/unbind/list_by_version、per-version 狀態欄位(selected_layers/view/rects/unit_override)讀寫
- [x] 2.4 clone 交易:複製 library(templates + classes 調參)+ version_files 到新 version(單交易,毫秒級)
- [x] 2.5 刪除兩層 scope:`PRODUCT_SCOPED_CLASSES`、`is_product_scoped()`、`load_library()` 的 product_id 參數與 merge、`insert_template` 分支、boot 洩漏清理
- [x] 2.6 dedup scope 改 `(library_id, class_name)`(`add_template_for_file` 簽名去 product_id)
- [x] 2.7 product 建立改為「product + 第一版 + 空 library」單交易;product 刪除 cascade 驗證(versions/libraries/templates/version_files)

## 3. 儲存路徑與 jobs 版本化

- [x] 3.1 `storage.py` path helper 全面改 `(version_id, file_id)` 簽名:parsed/prematch/match/layer_preview;`rule_check_path(version_id)`
- [x] 3.2 `jobs.py`:所有 worker payload 帶 `version_id`;討論期 invariant 不變(worker 重讀 store、只碰本地路徑)
- [x] 3.3 `_cached_parsed` lru key 改 `(version_id, file_id, mtime_ns)`
- [x] 3.4 移除 startup 一次性 legacy auto-rescale 掃描(REMOVED 規格);`/api/dev/reprocess-all` 保留並改為逐 version 遍歷

## 4. API 層

- [x] 4.1 `POST /api/products` 必填 `version_label`(422);回傳含 versions 列表;`GET /api/products[/{pid}]` 帶版本與 per-version `latest_rule_check_job`
- [x] 4.2 `POST /api/products/{pid}/versions`(label、clone_from 預設最新版;409 重複;400 跨 product clone_from);`GET /api/products/{pid}/versions`
- [x] 4.3 `POST/DELETE /api/versions/{vid}/sign-off`(身分取 `SMDR2_DEV_USER`;冪等 409;無 admin 檢查、註記待 auth)
- [x] 4.4 凍結守門 dependency `require_unsigned(version_id)`:掛上所有 mutating endpoint(commit、templates CRUD、strategy、上傳、side-regions、unit-override、layers、match-json save、rule-check、scan-all 寫入路徑)→ 409 含 signed_off_by/at
- [x] 4.5 檔案綁定endpoints:`POST /api/versions/{vid}/files`(additive、replace_file_id 換綁、skip_layer_pick、dedup-rebind per-version 處理、409 已畫押)、`DELETE /api/versions/{vid}/files/{role}`;移除 `POST /api/products/{pid}/files` 與 `files.library_id` PATCH 分支
- [x] 4.6 file-centric endpoints 加必填 `version_id` 參數(layers/match/commit/scan-all/prematch/match-json/side-regions/unit-override/primitives…)→ 缺參 422;以 `(vid, fid)` 解析 version_files 狀態與 artifact 路徑
- [x] 4.7 rule-check 移至 version:`POST/GET /api/versions/{vid}/rule-check`(202/400/409 語意照 spec);DRC bundle 端點改 version 解析
- [x] 4.8 移除 library 管理 API 對外語意:`/api/libraries*` CRUD 下線(內部函式保留供 version 使用)

## 5. 前端

- [ ] 5.1 Dashboard:扁平 product 卡片(移除 customer sections 與 library bar);New Product 表單加必填版號欄位
- [ ] 5.2 Product 卡片/頁:版本切換器(label + 畫押徽章「誰/何時」);new-version 動作(prompt label → clone 當前選中版)
- [ ] 5.3 已畫押版本:上傳/commit/重跑/刪除控制項全部 disabled + 徽章呈現;sign-off / unsign 按鈕
- [ ] 5.4 viewer:呼叫鏈帶 `version_id`(開檔入口從版本上下文進);header 的 library `<select>` 移除,改顯示 product/version 唯讀標籤
- [ ] 5.5 Library modal 改名 Templates、資料源改當前 version;已畫押時刪/移按鈕停用

## 6. 測試

- [ ] 6.1 version 生命週期:建料號必填版號、409 重複 label、無 delete 路由、product cascade
- [ ] 6.2 clone:templates/調參/綁定複製、clone_from 指舊版、跨 product 400、改 clone 不動 source
- [ ] 6.3 凍結守門:已畫押後 commit/上傳/調參/rule-check/scan-all 全 409;讀取 200;unsign 後恢復
- [ ] 6.4 keying:`(vid, fid)` artifact 隔離(v2 重跑不動 v1)、共用檔案 bytes 單份、per-version 選層/rects 獨立
- [ ] 6.5 既有測試遷移:product-files/dxf-pipeline/template-library 系列改 version 綁定模型;刪除兩層 scope 相關測試
- [ ] 6.6 全量 `pytest` 綠燈 + `ruff` 乾淨

## 7. 收尾

- [ ] 7.1 更新 `ARCHITECTURE.md` / `README.md` 對應段落(拓樸、版本、無共用範本)
- [ ] 7.2 `CHANGELOG.md` 條目
- [ ] 7.3 手動煙測:建料號(v1)→ 上傳 → 框選 commit → scan-all → rule-check → 畫押 → 建 v2(clone)→ 只換 POD → 驗 v1 結果不動
