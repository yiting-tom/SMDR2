# Add Version Diff

## Why

C6(v1↔v2 差異比較)原定延後;user 於 2026-06-11 改為立即實作。兩版皆為完整快照,差異可即時計算,無 schema 變更。

## What Changes

- 新增 **版本差異 API**:比較同一 product 的兩個 version——範本(以 canonical signature 比對,列出新增/移除)、match 調參變更、檔案綁定變更(角色換檔/增刪/per-version 狀態差異)。
- Dashboard 新增 **「比較版本」**入口:product 卡片上選兩版 → modal 呈現三區差異(範本縮圖、調參表、綁定變更)。
- 純讀取功能:不受畫押凍結影響(已畫押版本可比較)。

## Capabilities

### New Capabilities
（無——歸入既有 capability。）

### Modified Capabilities
- `product-versioning`: 新增版本差異需求(diff API 行為與比對語意)。
- `viewer-ui`: dashboard 新增比較版本 modal。

## Impact

- `app/version_diff.py`(新)+ `app/main.py` 一條 GET 端點;`app/static/dashboard.js` + `style.css` modal;`tests/test_version_diff.py`。
- 無 DB 變更、無遷移。
