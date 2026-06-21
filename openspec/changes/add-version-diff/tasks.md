# Tasks — add-version-diff

## 1. Backend

- [x] 1.1 `app/version_diff.py`:diff 計算(templates by signature、configs、bindings + state fields)
- [x] 1.2 `GET /api/products/{pid}/version-diff?from=&to=` 端點(400 跨 product、404 未知、簽核版可讀)
- [x] 1.3 `tests/test_version_diff.py`:spec 五個 scenario + 空 diff

## 2. Frontend

- [x] 2.1 product 卡片「比較版本」按鈕 + modal(雙 picker、三區、空狀態)
- [x] 2.2 範本縮圖渲染(沿用 Templates modal 的繪製)

## 3. 收尾

- [x] 3.1 docs:DISCUSSION C6 / product-versioning Q6 標記完成
- [x] 3.2 全套 pytest 綠燈
