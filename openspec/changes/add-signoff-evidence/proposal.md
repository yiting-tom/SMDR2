# Proposal: add-signoff-evidence

## Why

簽核(畫押)是版本生命週期的法律/流程節點:editor 確認該版本檢查完成並凍結。
實務上簽核常伴隨一份外部證明(紙本簽名掃描、檢查報告截圖、E-mail 核准畫面)。
目前系統只記「誰/何時」,證明文件散落在信箱與共享資料夾,回看舊版時對不起來。

2026-06-12 需求:**畫押時 editor 可(非強制)上傳一張圖片作為證明**,與版本永久綁定、隨版本回看。

## What Changes

- `POST /api/versions/{vid}/sign-off` 接受**選填** multipart 欄位 `evidence`(圖片);
  不帶檔案時行為與現在完全相同(既有呼叫端零改動)。
- 圖片存 blob `sign_off_evidence/{version_id}`(deterministic key,no-list 規則相容);
  `versions` 表新增 `evidence_name` / `evidence_type` 兩欄(NULL = 無證明)。
- 新讀取端點 `GET /api/versions/{vid}/sign-off/evidence`(viewer guard)回傳圖片。
- 解除畫押(admin)時刪除證明(blob + 欄位)— 證明屬於該次簽核事件。
- product 刪除 cascade 納入 evidence key;clone 不複製(新版尚未簽核)。
- Dashboard 簽核流程改為小 modal:確認文案 + 選填圖片欄位;已簽核 badge 旁
  顯示「📎 證明」連結(有證明時)。
- audit `version.sign_off` detail 增加 `evidence`(檔名或 null)。

## Capabilities

### New Capabilities

(無 — 併入既有 product-versioning 能力)

### Modified Capabilities

- `product-versioning`:sign-off 流程增加選填證明圖片(上傳/讀取/解簽清除/cascade)。

## Impact

- **Code**:`app/versions.py`(欄位+遷移)、`app/main.py`(sign-off 端點、evidence GET、
  cascade)、`app/storage.py`(key helper)、`alembic/versions/0005`、
  `app/static/dashboard.js`(modal + badge link)。
- **DB**:`versions` +2 欄(SQLite boot migration 比照 `products.customer_id` 模式;
  MariaDB 走 Alembic)。
- **安全**:僅 editor(原 guard 鏈)可上傳;類型白名單 png/jpeg/webp + magic bytes
  驗證;大小上限 10MB;viewer 範圍內可讀。
- **風險**:sign-off 端點從純 POST 變 multipart 相容 — 以測試鎖住「無檔案的舊式呼叫
  不變」。
