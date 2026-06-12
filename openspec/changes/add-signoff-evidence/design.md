# Design: add-signoff-evidence

## D1 — 證明與簽核同一個請求,不做兩段式

證明屬於「這一次簽核」:分開的 staging 端點要處理孤兒清理與「簽核前/後誰能傳」
的狀態機。multipart 選填欄位讓兩者原子化:驗證 → 寫 blob → `sign_off()`;
sign_off 拋 `SignedOff`(409,輸給並發簽核者)時盲刪剛寫的 blob 回滾。
FastAPI `UploadFile | None = File(None)` 對「完全沒有 body 的 POST」回 None —
既有呼叫端(dashboard、測試)零改動,以測試鎖住。

## D2 — 中繼資料進 DB,bytes 進 blob

`versions.evidence_name`(原始檔名)+ `evidence_type`(MIME)。
- 列表/版本 payload 直接帶 `evidence_name` — **不用對 blob 打 HEAD**(避免再添
  §11.5 的 N+1)。
- blob key = `sign_off_evidence/{version_id}`(無副檔名;MIME 由 DB 欄位回填
  Content-Type)— deterministic key,符合 no-list 刪除規則(§5.2)。

## D3 — 驗證

- 白名單:`image/png`、`image/jpeg`、`image/webp`,**以 magic bytes 判定**
  (不信 client 的 Content-Type;PNG `\x89PNG\r\n\x1a\n`、JPEG `\xff\xd8\xff`、
  WebP `RIFF....WEBP`),判定結果作為存檔 MIME。
- 上限 10MB(`SIGNOFF_EVIDENCE_MAX_MB` 不另設 env — 證明圖無大檔需求,寫死)。
- 空檔案(0 bytes)視同未附檔。

## D4 — 生命週期

| 事件 | 行為 |
|---|---|
| sign-off(帶檔) | 寫 blob → 簽核 + 欄位原子寫入;audit detail `evidence: <name>` |
| sign-off(不帶) | 與現行為 byte-identical |
| unsign(admin) | 清欄位 + 盲刪 blob(證明屬於被撤銷的那次簽核);audit 既有 |
| clone | 不複製(`_insert_version_locked` 建新列,欄位天然 NULL) |
| product delete | `_version_artifact_keys` 納入 evidence key |
| 再次 sign-off(unsign 後) | 可附新證明 — 上一份已在 unsign 時清掉 |

## D5 — 讀取端點回應

`GET /api/versions/{vid}/sign-off/evidence`:viewer guard;200 回 bytes +
DB 的 MIME + `Cache-Control: private, max-age=3600`(簽核後不可變;unsign 會換
URL 內容但該版本同時也離開唯讀展示情境);無證明或未簽核 → 404。
